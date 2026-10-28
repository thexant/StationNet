"""End-to-end authored world features and package persistence."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

import pytest
from flask import session

from stationnet import create_app, character_creation, mud, mud_worlds
from stationnet.db import get_db
from stationnet import db as db_module


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_authored_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        users = []
        for name in ("one", "two"):
            uid = db.execute("INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
                             (name, name.upper(), "unused", f"connection-{name}")).lastrowid
            character_creation.complete(db, uid, {
                "name": name.title(), "race": "human", "class": "warrior",
                "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                "skills": ["lore", "guard"]})
            users.append(uid)
        db.commit()
    yield app, users
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data)


def _document():
    return {
        "id": "authored", "name": "Authored World", "author": "Test", "start_room": "square",
        "rooms": [{"id": "square", "name": "Town Square", "description": "A busy square."},
                  {"id": "vault", "name": "Old Vault", "description": "A locked vault."}],
        "exits": [{"from": "square", "to": "vault", "name": "vault door", "direction": "north",
                   "key_item_id": "brass-key"}],
        "content": {
            "mud_item_definitions": [
                {"id": "brass-key", "name": "Brass Key", "description": "A key.", "item_type": "key"},
                {"id": "herb", "name": "Silver Herb", "description": "A useful herb.", "item_type": "misc",
                 "properties_json": {"value": 10}},
                {"id": "tonic", "name": "Plain Tonic", "description": "Restores health.",
                 "item_type": "consumable", "properties_json": {"heal": 7, "value": 19}}],
            "mud_conversations": [{"id": "offer", "name": "Offer an errand"}],
            "mud_dialogue_nodes": [{"id": "offer-start", "conversation_id": "offer", "node_key": "start",
                                    "body": "Can you bring herbs?", "is_start": 1}],
            "mud_npc_definitions": [{"id": "trader", "name": "Trader", "description": "A shopkeeper.",
                                     "conversation_id": "offer"},
                                    {"id": "mole", "name": "Mole", "description": "A quiet mole.",
                                     "behaviors_json": {"friendly": True}}],
            "mud_npc_instances": [{"id": "trader-here", "definition_id": "trader", "room_id": "square"},
                                  {"id": "mole-here", "definition_id": "mole", "room_id": "vault"}],
            "mud_npc_combat_profiles": [{"npc_definition_id": "mole", "maximum_health": 5,
                                         "attack": 1, "defense": 0, "experience_reward": 0,
                                         "respawn_seconds": 5}],
            "mud_npc_combat_rewards": [{"id": "mole-coins", "npc_definition_id": "mole",
                                        "reward_type": "coins", "amount": 3, "chance_percent": 25}],
            "mud_npc_shops": [{"npc_definition_id": "trader", "buys_items": 1, "sell_percent": 50}],
            "mud_shop_stock": [{"id": "tonic-stock", "npc_definition_id": "trader",
                                "item_definition_id": "tonic", "price": 6, "quantity": 1}],
            "mud_item_spawns": [{"id": "herb-spawn", "definition_id": "herb", "room_id": "square",
                                 "respawn_seconds": 5}],
            "mud_room_discoveries": [{"id": "key-clue", "room_id": "square", "clue": "A key lies beneath a stone.",
                                      "item_definition_id": "brass-key"}],
            "mud_quests": [{"id": "errand", "name": "Herb Errand", "description": "Bring herbs."}],
            "mud_quest_givers": [{"quest_id": "errand", "npc_definition_id": "trader"}],
            "mud_quest_objectives": [{"id": "delivery", "quest_id": "errand", "objective_type": "deliver_item",
                                      "target_id": "herb", "description": "Deliver two herbs", "required_count": 2}],
            "mud_quest_rewards": [{"id": "pay", "quest_id": "errand", "reward_type": "coins", "amount": 9}],
            "mud_dialogue_responses": [{"id": "agree", "node_id": "offer-start", "response_text": "I will help.",
                                        "accept_quest_id": "errand"},
                                       {"id": "handover", "node_id": "offer-start", "response_text": "Here is an herb.",
                                        "deliver_item_id": "herb"}],
        },
    }


def test_search_reveals_objects_and_exits_per_character_after_conditions(game):
    app, users = game
    document = _document()
    document["exits"][0].update(id="secret-vault", hidden=True)
    document["content"]["mud_item_definitions"].append({
        "id": "marker", "name": "Hidden Marker", "description": "A small marker.",
        "item_type": "misc", "properties_json": {"fixed": True}})
    document["content"]["mud_item_instances"] = [
        {"id": "marker-here", "definition_id": "marker", "room_id": "square"}]
    document["content"]["mud_room_discoveries"] = [
        {"id": "a-object", "room_id": "square", "clue": "A marker lies in a niche.",
         "item_instance_id": "marker-here", "conditions_json": [
             {"type": "attribute", "attribute": "intellect", "min": 11},
             {"type": "skill", "skill": "lore", "min_rank": 1},
             {"type": "has_item", "item": "brass-key"}]},
        {"id": "b-exit", "room_id": "square", "clue": "A concealed vault door opens north.",
         "exit_id": "secret-vault", "conditions_json": [
             {"type": "flag", "key": "explore.ready", "value": "yes"}]},
    ]
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.package_bytes(document))
        db.execute("UPDATE mud_world_packages SET active=0")
        db.execute("UPDATE mud_world_packages SET active=1 WHERE id='authored'")
        room_id = db.execute("SELECT start_room_id FROM mud_world_packages WHERE id='authored'").fetchone()[0]
        for uid in users:
            mud._set_player_location(db, uid, "traditional", "world_room", room_id)
        first, second = [db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() for uid in users]
        hidden_object_id = db.execute("SELECT id FROM mud_item_instances WHERE room_id=? AND definition_id IN "
                                      "(SELECT id FROM mud_item_definitions WHERE name='Hidden Marker')",
                                      (room_id,)).fetchone()[0]
        assert "Hidden Marker" not in mud._look_action(db, first, {})[0].body
        assert "vault door" not in mud._look_action(db, first, {})[0].body.lower()
        assert "nothing new" in mud._search_action(db, first, {})[0].body
        with pytest.raises(mud.ActionError):
            mud._interact_action(db, first, {"arguments": ["Hidden Marker"]})
        with pytest.raises(mud.ActionError):
            mud._look_action(db, first, {"arguments": [hidden_object_id]})
        with pytest.raises(mud.ActionError):
            mud._get_action(db, first, {"item_id": hidden_object_id})
        with pytest.raises(mud.ActionError):
            mud._go_action(db, first, {"arguments": ["north"]})
        key_id = db.execute("SELECT id FROM mud_item_definitions WHERE name='Brass Key'").fetchone()[0]
        mud._grant_item(db, users[0], key_id)
        assert "marker lies" in mud._search_action(db, first, {})[0].body
        assert "Hidden Marker" in mud._look_action(db, first, {})[0].body
        assert "Hidden Marker" not in mud._look_action(db, second, {})[0].body
        character_id = mud._character_row(db, users[0])["id"]
        mud._store_script_flag(db, "authored", character_id, "explore.ready", "yes")
        assert "vault door opens" in mud._search_action(db, first, {})[0].body
        assert "north" in mud._look_action(db, first, {})[0].body.lower()
        assert "north" not in mud._look_action(db, second, {})[0].body.lower()
        with pytest.raises(mud.ActionError, match="locked"):
            mud._go_action(db, first, {"arguments": ["north"]})
        marker_id = db.execute("SELECT id FROM mud_item_instances WHERE room_id=? AND definition_id IN "
                               "(SELECT id FROM mud_item_definitions WHERE name='Hidden Marker')",
                               (room_id,)).fetchone()[0]
        exit_id = db.execute("SELECT id FROM mud_world_exits WHERE from_room_id=?",
                             (room_id,)).fetchone()[0]
        mud._store_entity_state(db, "authored", "item", marker_id, "shown")
        assert "Hidden Marker" in mud._look_action(db, second, {})[0].body
        mud._store_entity_state(db, "authored", "item", marker_id, "hidden")
        assert "Hidden Marker" not in mud._look_action(db, first, {})[0].body
        mud._store_entity_state(db, "authored", "item", marker_id, "")
        mud._store_entity_state(db, "authored", "exit", exit_id, "hidden")
        assert "north" not in mud._look_action(db, first, {})[0].body.lower()
        mud._store_entity_state(db, "authored", "exit", exit_id, "open")
        assert "north" in mud._look_action(db, second, {})[0].body.lower()
        mud._store_entity_state(db, "authored", "exit", exit_id, "")
        exported = mud_worlds.export(db, "authored")
        restored = mud_worlds.read_package(exported)
        results = restored["content"]["mud_room_discoveries"]
        assert {row.get("item_instance_id") for row in results if row.get("item_instance_id")}
        assert {row.get("exit_id") for row in results if row.get("exit_id")}
        db.commit()
    with app.app_context():
        db = get_db()
        first = db.execute("SELECT * FROM users WHERE id=?", (users[0],)).fetchone()
        second = db.execute("SELECT * FROM users WHERE id=?", (users[1],)).fetchone()
        assert "Hidden Marker" in mud._look_action(db, first, {})[0].body
        assert "Hidden Marker" not in mud._look_action(db, second, {})[0].body
        assert "nothing new" in mud._search_action(db, first, {})[0].body
    imported_data = Path(__file__).parent / f"_discovery_roundtrip_{uuid.uuid4().hex}"
    imported_data.mkdir()
    imported_app = create_app({"TESTING": True, "DATA_DIR": str(imported_data)})
    try:
        with imported_app.app_context():
            imported_db = get_db()
            mud_worlds.install(imported_db, exported)
            rows = imported_db.execute("SELECT clue,item_instance_id,exit_id,conditions_json "
                                       "FROM mud_room_discoveries ORDER BY clue").fetchall()
            assert len(rows) == 2
            assert any(row["item_instance_id"] for row in rows)
            assert any(row["exit_id"] for row in rows)
            assert any('"attribute"' in row["conditions_json"] for row in rows)
    finally:
        imported_app.extensions["broadcast_coordinator"]._closed = True
        imported_app.extensions["broadcast_coordinator"]._timer.cancel()
        shutil.rmtree(imported_data)


def test_builder_edits_discovery_without_erasing_history_and_rejects_foreign_targets(game):
    app, users = game
    document = _document()
    document["exits"][0]["id"] = "vault-route"
    document["content"]["mud_item_instances"] = [
        {"id": "placed-key", "definition_id": "brass-key", "room_id": "square"}]
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.package_bytes(document))
        db.execute("UPDATE mud_world_packages SET active=0")
        db.execute("UPDATE mud_world_packages SET active=1 WHERE id='authored'")
        db.execute("INSERT INTO mud_builder_permissions(user_id) VALUES(?)", (users[0],))
        room = db.execute("SELECT start_room_id FROM mud_world_packages WHERE id='authored'").fetchone()[0]
        vault = db.execute("SELECT id FROM mud_world_rooms WHERE world_id='authored' AND id<>?",
                           (room,)).fetchone()[0]
        object_id = db.execute("SELECT id FROM mud_item_instances WHERE room_id=? AND definition_id IN "
                               "(SELECT id FROM mud_item_definitions WHERE name='Brass Key')",
                               (room,)).fetchone()[0]
        exit_id = db.execute("SELECT id FROM mud_world_exits WHERE from_room_id=?", (room,)).fetchone()[0]
        db.commit()
    builder = app.test_client()
    with builder.session_transaction() as session:
        session["user_id"] = users[0]
        session["csrf_token"] = "test-token"
    player = app.test_client()
    with player.session_transaction() as session:
        session["user_id"] = users[1]
        session["csrf_token"] = "test-token"
    path = f"/mud/builder/rooms/{room}/discoveries"
    assert player.post(path, data={"csrf_token": "test-token", "clue": "No."}).status_code == 403
    invalid = builder.post(f"/mud/builder/rooms/{vault}/discoveries", data={
        "csrf_token": "test-token", "clue": "Wrong room.", "result": f"exit|{exit_id}"},
        follow_redirects=True)
    assert b"Choose a result in this world and room" in invalid.data
    assert builder.post(path, data={"csrf_token": "test-token", "clue": "A hidden key.",
                                    "result": f"object|{object_id}", "difficulty": "0",
                                    "conditions_json": '[{"type":"attribute","attribute":"intellect","min":11}]'}).status_code == 302
    with app.app_context():
        db = get_db()
        found = db.execute("SELECT * FROM mud_room_discoveries WHERE clue='A hidden key.'").fetchone()
        discovery_id = found["id"]
        assert found["item_instance_id"] == object_id
        assert '"attribute"' in found["conditions_json"]
        character_id = mud._character_row(db, users[0])["id"]
        db.execute("INSERT INTO mud_player_discoveries(character_id,discovery_id) VALUES(?,?)",
                   (character_id, discovery_id))
        db.commit()
    update_path = f"/mud/builder/discoveries/{discovery_id}"
    assert builder.post(update_path, data={"csrf_token": "test-token", "clue": "A hidden route.",
                                           "result": f"exit|{exit_id}", "difficulty": "12"}).status_code == 302
    with app.app_context():
        db = get_db()
        found = db.execute("SELECT * FROM mud_room_discoveries WHERE id=?", (discovery_id,)).fetchone()
        assert found["exit_id"] == exit_id and found["item_instance_id"] is None
        assert db.execute("SELECT 1 FROM mud_player_discoveries WHERE discovery_id=?",
                          (discovery_id,)).fetchone()
        # Deleting the target keeps the clue and the character's history.
        db.execute("DELETE FROM mud_world_exits WHERE id=?", (exit_id,))
        db.commit()
        assert db.execute("SELECT exit_id FROM mud_room_discoveries WHERE id=?",
                          (discovery_id,)).fetchone()[0] is None
        assert db.execute("SELECT 1 FROM mud_player_discoveries WHERE discovery_id=?",
                          (discovery_id,)).fetchone()


def test_discovery_migration_keeps_legacy_rows():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute("CREATE TABLE mud_room_discoveries (id TEXT PRIMARY KEY,room_id TEXT,clue TEXT,"
                     "item_definition_id TEXT,difficulty INTEGER NOT NULL DEFAULT 0)")
    database.execute("INSERT INTO mud_room_discoveries(id,room_id,clue) VALUES('old','hall','Old clue')")
    db_module._migrate_mud_discoveries(database)
    db_module._migrate_mud_discoveries(database)
    row = database.execute("SELECT * FROM mud_room_discoveries WHERE id='old'").fetchone()
    assert row["clue"] == "Old clue" and row["conditions_json"] == "[]"
    assert row["item_instance_id"] is None and row["exit_id"] is None
    database.close()


@pytest.mark.parametrize("changes", [
    {"item_instance_id": "missing"},
    {"exit_id": "missing"},
    {"item_definition_id": "brass-key", "exit_id": "missing"},
    {"conditions_json": [{"type": "attribute", "attribute": "unknown", "min": 1}]},
])
def test_world_package_rejects_invalid_discovery_references_and_conditions(changes):
    document = _document()
    document["content"]["mud_room_discoveries"] = [
        {"id": "bad", "room_id": "square", "clue": "A clue.", **changes}]
    with pytest.raises(mud_worlds.WorldError):
        mud_worlds.read_package(mud_worlds.package_bytes(document))


def test_package_quest_shop_respawn_search_and_lock(game):
    app, users = game
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.package_bytes(_document()))
        world = db.execute("SELECT start_room_id FROM mud_world_packages WHERE id='authored'").fetchone()
        for uid in users:
            mud._set_player_location(db, uid, "traditional", "world_room", world["start_room_id"])
        db.execute("UPDATE mud_world_packages SET active=0")
        db.execute("UPDATE mud_world_packages SET active=1 WHERE id='authored'")
        db.commit()
        user = db.execute("SELECT * FROM users WHERE id=?", (users[0],)).fetchone()
        other = db.execute("SELECT * FROM users WHERE id=?", (users[1],)).fetchone()
        definition, arguments = mud.actions.from_command("/quest accept Herb Errand")
        assert "Quest accepted" in definition.handler(db, user, arguments)[0].body
        assert "Can you bring herbs?" in mud._talk_action(db, other, {"arguments": ["Trader"]})[0].body
        assert any("Quest accepted" in event.body for event in mud._respond_action(db, other, {"arguments": ["1"]}))
        assert mud.quest_state(db, users[1])["active"]
        herb_definition = db.execute("SELECT id FROM mud_item_definitions WHERE name='Silver Herb'").fetchone()[0]
        mud._grant_item(db, users[1], herb_definition)
        # The accept response is hidden once the quest is taken, so delivery is now first.
        mud._talk_action(db, other, {"arguments": ["Trader"]})
        assert "You deliver" in mud._respond_action(db, other, {"arguments": ["1"]})[1].body
        assert mud.quest_state(db, users[1])["active"][0]["objectives"][0]["progress"] == 1
        assert "locked" in mud._look_action(db, user, {"arguments": ["vault door"]})[0].body.lower()
        with pytest.raises(mud.ActionError, match="locked"):
            mud._go_action(db, user, {"arguments": ["north"]})
        assert "beneath a stone" in mud._search_action(db, user, {})[0].body
        assert "nothing new" in mud._search_action(db, user, {})[0].body
        with pytest.raises(mud.ActionError, match="key"):
            mud._unlock_action(db, other, {"arguments": ["north"]})
        mud._unlock_action(db, user, {"arguments": ["north"]})
        assert "Unlocked" in mud._look_action(db, user, {"arguments": ["vault door"]})[0].body
        stock = db.execute("SELECT * FROM mud_shop_stock").fetchone()
        assert "6 coins" in mud._shop_action(db, user, {})[0].body
        for uid in users:
            mud._wallet(db, f"stationnet:character:{uid}")
            db.execute("UPDATE mud_character_wallets SET coins=6 WHERE character_id=?",
                       (f"stationnet:character:{uid}",))
        assert "buy" in mud._shop_action(db, user, {"arguments": ["buy", "Plain", "Tonic"]})[0].body
        character = mud._character_row(db, users[0])
        db.execute("UPDATE mud_character_progression SET current_health=maximum_health-10 "
                   "WHERE character_id=? AND track_key=?", (character["id"], character["track_key"]))
        assert "restore 7 health" in mud._use_action(db, user, {"arguments": ["Plain", "Tonic"]})[0].body
        with pytest.raises(mud.ActionError, match="out of stock"):
            mud._shop_action(db, other, {"arguments": ["buy", "Plain", "Tonic"]})
        assert db.execute("SELECT quantity FROM mud_shop_stock WHERE id=?", (stock["id"],)).fetchone()[0] == 0
        herb = db.execute("SELECT i.id FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id WHERE d.name='Silver Herb' AND i.room_id=?", (world["start_room_id"],)).fetchone()
        mud._get_action(db, user, {"arguments": [herb["id"]]})
        mud._drop_action(db, user, {"arguments": ["Silver", "Herb"]})
        while_dropped = mud_worlds.read_package(mud_worlds.export(db, "authored"))
        assert not any(item["definition_id"] == next(
            definition["id"] for definition in while_dropped["content"]["mud_item_definitions"]
            if definition["name"] == "Silver Herb")
            for item in while_dropped["content"].get("mud_item_instances", []))
        mud._get_action(db, user, {"arguments": ["Silver", "Herb"]})
        mud._deliver_action(db, user, {"arguments": ["Silver", "Herb"]})
        assert mud.quest_state(db, users[0])["active"][0]["objectives"][0]["progress"] == 1
        due = datetime.now(timezone.utc) + timedelta(seconds=6)
        mud.process_scheduled_events(db, due)
        respawned = db.execute("SELECT live_instance_id FROM mud_item_spawns").fetchone()[0]
        assert respawned and respawned != herb["id"]
        mud._get_action(db, user, {"arguments": [respawned]})
        assert "Quest complete" in mud._deliver_action(db, user, {"arguments": ["Silver", "Herb"]})[-1].body
        assert mud._wallet(db, f"stationnet:character:{users[0]}") == 9
        herb_id = db.execute("SELECT id FROM mud_item_definitions WHERE name='Silver Herb'").fetchone()[0]
        mud._grant_item(db, users[0], herb_id)
        assert "5 coins" in mud._shop_action(db, user, {"arguments": ["sell", "Silver", "Herb"]})[0].body
        assert mud._wallet(db, f"stationnet:character:{users[0]}") == 14
        exported = mud_worlds.read_package(mud_worlds.export(db, "authored"))
        assert exported["exits"][0]["key_item_id"]
        for table in ("mud_npc_shops", "mud_shop_stock", "mud_item_spawns", "mud_room_discoveries"):
            assert exported["content"][table]
        assert exported["content"]["mud_npc_combat_rewards"][0]["chance_percent"] == 25
        db.execute("UPDATE users SET is_admin=1 WHERE id=?", (users[0],))
        db.commit()
    with app.app_context():
        db = get_db()
        assert mud.quest_state(db, users[0])["completed"]
        assert db.execute("SELECT COUNT(*) FROM mud_exit_unlocks").fetchone()[0] == 1
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = users[0]
        state["csrf_token"] = "test-token"
    assert client.get("/mud/builder").status_code == 200


def test_legacy_quest_progress_survives_schema_upgrade():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    for table in ("mud_world_exits", "mud_item_instances", "mud_dialogue_responses", "mud_quest_objectives", "mud_quest_rewards", "mud_npc_combat_rewards",
                  "mud_player_objectives"):
        sql = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\);", db_module.SCHEMA, re.S).group()
        sql = sql.replace("'possess_item','defeat_npc','deliver_item'", "'possess_item'")
        sql = sql.replace("'experience','item','coins'", "'experience','item'")
        sql = re.sub(r"\s*chance_percent INTEGER NOT NULL DEFAULT 100 CHECK\(chance_percent BETWEEN 1 AND 100\),", "", sql)
        sql = sql.replace(" spawn_source_id TEXT,", "")
        sql = sql.replace(" deliver_item_id TEXT REFERENCES mud_item_definitions(id) ON DELETE SET NULL,", "")
        database.execute(sql)
    database.execute("INSERT INTO mud_quest_objectives(id,quest_id,objective_type,target_id,description,required_count) "
                     "VALUES('old-objective','old-quest','talk_npc','old-npc','Talk twice',2)")
    database.execute("INSERT INTO mud_player_objectives(character_id,objective_id,progress) "
                     "VALUES('old-character','old-objective',1)")
    database.commit()
    db_module._migrate_mud_authored_features(database)
    assert database.execute("SELECT progress FROM mud_player_objectives").fetchone()[0] == 1
    database.commit()
    database.execute("PRAGMA foreign_keys=OFF")
    database.execute("INSERT INTO mud_quest_objectives(id,quest_id,objective_type,target_id,description) "
                     "VALUES('new-objective','old-quest','defeat_npc','old-npc','Defeat')")
    database.execute("INSERT INTO mud_quest_rewards(id,quest_id,reward_type,amount) "
                     "VALUES('new-reward','old-quest','coins',3)")
    database.execute("INSERT INTO mud_npc_combat_rewards(id,npc_definition_id,reward_type,amount,chance_percent) "
                     "VALUES('new-drop','old-npc','coins',3,50)")
    assert database.execute("SELECT COUNT(*) FROM mud_player_objectives").fetchone()[0] == 1
    database.close()


def test_two_players_cannot_buy_one_stock_item(game):
    app, users = game
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.package_bytes(_document()))
        room = db.execute("SELECT start_room_id FROM mud_world_packages WHERE id='authored'").fetchone()[0]
        db.execute("UPDATE mud_world_packages SET active=0")
        db.execute("UPDATE mud_world_packages SET active=1 WHERE id='authored'")
        for uid in users:
            mud._set_player_location(db, uid, "traditional", "world_room", room)
            character = mud._character_row(db, uid)
            mud._wallet(db, character["id"])
            db.execute("UPDATE mud_character_wallets SET coins=6 WHERE character_id=?", (character["id"],))
        db.commit()

    def buy(uid):
        with app.test_request_context("/mud/actions", method="POST"):
            session["connection_id"] = f"connection-{'one' if uid == users[0] else 'two'}"
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            request = mud.ActionRequest(mud.actions.get("shop"), {"arguments": ["buy", "Plain", "Tonic"]})
            try:
                mud.service.execute(request, user)
                return True
            except mud.ActionError as error:
                return str(error)

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(buy, users))
    assert sum(outcome is True for outcome in outcomes) == 1, outcomes
    assert sum(isinstance(outcome, str) for outcome in outcomes) == 1, outcomes
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT quantity FROM mud_shop_stock").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM mud_item_instances i JOIN mud_item_definitions d "
                          "ON d.id=i.definition_id WHERE d.name='Plain Tonic' AND i.owner_character_id IS NOT NULL").fetchone()[0] == 1


def test_counted_defeats_coin_loot_and_no_double_rewards(game, monkeypatch):
    app, users = game
    uid = users[0]
    with app.app_context():
        db = get_db()
        room_id = mud._start_room(db)["id"]
        mud._set_player_location(db, uid, "traditional", "world_room", room_id)
        db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type) VALUES('test-herb','Test Herb','Herb','misc')")
        db.execute("INSERT INTO mud_npc_definitions(id,name,description) VALUES('test-mole','Mole','A mole')")
        db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES('test-mole-here','test-mole',?)", (room_id,))
        db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack,defense,experience_reward,respawn_seconds) "
                   "VALUES('test-mole',1,1,0,0,5)")
        db.execute("INSERT INTO mud_npc_combat_rewards(id,npc_definition_id,reward_type,amount,chance_percent) "
                   "VALUES('test-coins','test-mole','coins',4,100)")
        db.execute("INSERT INTO mud_npc_combat_rewards(id,npc_definition_id,reward_type,target_id,amount,chance_percent) "
                   "VALUES('test-chance','test-mole','item','test-herb',1,1)")
        db.execute("INSERT INTO mud_quests(id,name,description) VALUES('mole-quest','Mole Hunt','Defeat two moles')")
        db.execute("INSERT INTO mud_quest_givers(quest_id,npc_definition_id) VALUES('mole-quest','test-mole')")
        db.execute("INSERT INTO mud_quest_objectives(id,quest_id,objective_type,target_id,description,required_count) "
                   "VALUES('two-moles','mole-quest','defeat_npc','test-mole','Defeat two moles',2)")
        db.execute("INSERT INTO mud_quest_rewards(id,quest_id,reward_type,amount) VALUES('hunt-pay','mole-quest','coins',5)")
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        mud._quests_action(db, user, {"arguments": ["accept", "Mole Hunt"]})
        starting_coins = mud._wallet(db, f"stationnet:character:{uid}")
        npc = mud._room_npc_rows(db, room_id)[0]
        monkeypatch.setattr(mud.random, "randint", lambda low, high: 100)
        for index in range(2):
            encounter_id = f"test-encounter-{index}"
            db.execute("INSERT INTO mud_combat_encounters(id,scope_type,scope_key,npc_instance_id,npc_definition_id,status,current_health,maximum_health,ended_at) "
                       "VALUES(?,'room',?,'test-mole-here','test-mole','defeated',0,1,CURRENT_TIMESTAMP)",
                       (encounter_id, room_id))
            db.execute("INSERT INTO mud_combat_participants(encounter_id,character_id,damage_dealt) VALUES(?,?,1)",
                       (encounter_id, f"stationnet:character:{uid}"))
            encounter = db.execute("SELECT * FROM mud_combat_encounters WHERE id=?", (encounter_id,)).fetchone()
            mud._encounter_reward_drafts(db, encounter, npc)
            assert mud.quest_state(db, uid)["active" if index == 0 else "completed"][0]["objectives"][0]["progress"] == index + 1
            mud._encounter_reward_drafts(db, encounter, npc)
        assert mud._wallet(db, f"stationnet:character:{uid}") == starting_coins + 13
        assert db.execute("SELECT COUNT(*) FROM mud_item_instances WHERE definition_id='test-herb' AND owner_character_id IS NOT NULL").fetchone()[0] == 0


@pytest.mark.parametrize("table,field,value", [
    ("mud_shop_stock", "price", 0),
    ("mud_npc_combat_rewards", "chance_percent", 101),
    ("mud_item_spawns", "respawn_seconds", 0),
])
def test_world_package_rejects_invalid_economy_and_respawn_settings(table, field, value):
    document = _document()
    document["content"][table][0][field] = value
    with pytest.raises(mud_worlds.WorldError):
        mud_worlds.read_package(mud_worlds.package_bytes(document))
