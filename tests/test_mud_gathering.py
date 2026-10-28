"""Authored gathering nodes, shared depletion, progression, and packages."""
import json
import shutil
import uuid
from pathlib import Path

import pytest

from stationnet import character_creation, create_app, mud, mud_gathering, mud_skills, mud_worlds
from stationnet.db import get_db


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_gathering_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        room = mud._start_room(db)
        users = []
        for number in (1, 2):
            uid = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,?)",
                             (f"gatherer{number}", f"GTH{number}", "unused", int(number == 1))).lastrowid
            character_creation.complete(db, uid, {
                "name": "Ada Gatherer" if number == 1 else "Bea Gatherer", "race": "human", "class": "warrior",
                "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                "skills": ["lore", "guard"]})
            mud._set_player_location(db, uid, "traditional", "world_room", room["id"])
            users.append(uid)
        db.commit()
    yield app, users
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data)


def _skill(db):
    definition = mud_skills.normalize({
        "key": "orecraft", "name": "Orecraft", "description": "Finding usable ore.",
        "group": "exploration", "effects": [{"type": "gathering", "per_rank": 5}],
        "practice": {"triggers": ["gather"]}})
    mud_skills._store(db, definition)


def _node(db, room_id, *, chance=100):
    db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type) "
               "VALUES('ore','Copper Ore','Ore.','misc'),('rock','Stone','Rock.','misc'),"
               "('pick','Pickaxe','A pick.','misc')")
    db.execute("INSERT INTO mud_gathering_nodes(id,room_id,name,activity,rewards_json,skill_key,min_rank,"
               "tool_definition_id,success_percent,respawn_seconds) VALUES(?,?,?,?,?,?,?,?,?,?)",
               ("vein", room_id, "Copper Vein", "mine", json.dumps([
                   {"item_id": "ore", "amount": 2}, {"item_id": "rock", "amount": 1}]),
                "orecraft", 1, "pick", chance, 300))


def _user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _count(db, uid):
    return db.execute("SELECT COUNT(*) FROM mud_item_instances WHERE owner_character_id=? "
                      "AND definition_id IN ('ore','rock')", (f"stationnet:character:{uid}",)).fetchone()[0]


def test_gathering_requires_skill_and_tool_and_depletes_for_everyone(game):
    app, (first, second) = game
    definition, parameters = mud.actions.from_command('/mine "Copper Vein"')
    assert definition.key == "gather" and parameters["arguments"] == [":mine", "Copper Vein"]
    with app.app_context():
        db = get_db(); room = mud._start_room(db)
        _skill(db); _node(db, room["id"])
        with pytest.raises(mud.ActionError, match="Orecraft rank 1"):
            mud._gather_action(db, _user(db, first), {"arguments": ["Copper Vein"]})
        db.execute("INSERT INTO mud_character_skills(character_id,skill_key,rank,experience) "
                   "VALUES(?,?,1,0)", (f"stationnet:character:{first}", "orecraft"))
        with pytest.raises(mud.ActionError, match="Pickaxe"):
            mud._gather_action(db, _user(db, first), {"arguments": ["Copper Vein"]})
        db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) VALUES('pick-1','pick',?)",
                   (f"stationnet:character:{first}",))
        result = mud._gather_action(db, _user(db, first), {"arguments": ["Copper Vein"]})
        assert "2 × Copper Ore, 1 × Stone" in result[0].body
        assert _count(db, first) == 3
        assert db.execute("SELECT experience FROM mud_character_skills WHERE character_id=? AND skill_key='orecraft'",
                          (f"stationnet:character:{first}",)).fetchone()[0] > 0
        assert "Gathering: Copper Vein" in mud._look_action(db, _user(db, first), {})[0].body
        assert not mud._room_state(db, second)["gathering"][0]["available"]
        # Give the second player the requirements; the shared cooldown still blocks them.
        db.execute("INSERT INTO mud_character_skills(character_id,skill_key,rank,experience) VALUES(?,?,1,0)",
                   (f"stationnet:character:{second}", "orecraft"))
        db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) VALUES('pick-2','pick',?)",
                   (f"stationnet:character:{second}",))
        assert "depleted" in mud._gather_action(db, _user(db, second),
                                                 {"arguments": ["Copper Vein"]})[0].body.lower()
        assert _count(db, second) == 0
        db.execute("UPDATE mud_gathering_nodes SET available_at='2000-01-01T00:00:00+00:00' WHERE id='vein'")
        assert "collect" in mud._gather_action(db, _user(db, second),
                                                {"arguments": [":mine", "Copper Vein"]})[0].body
        assert _count(db, second) == 3


def test_failed_attempt_depletes_without_reward_or_practice(game, monkeypatch):
    app, (first, _) = game
    with app.app_context():
        db = get_db(); _skill(db); _node(db, mud._start_room(db)["id"], chance=1)
        db.execute("INSERT INTO mud_character_skills(character_id,skill_key,rank,experience) VALUES(?,?,1,0)",
                   (f"stationnet:character:{first}", "orecraft"))
        db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) VALUES('pick-1','pick',?)",
                   (f"stationnet:character:{first}",))
        monkeypatch.setattr(mud.secrets, "randbelow", lambda _: 99)
        assert "nothing usable" in mud._gather_action(db, _user(db, first),
                                                      {"arguments": ["Copper Vein"]})[0].body
        assert _count(db, first) == 0
        assert db.execute("SELECT experience FROM mud_character_skills WHERE character_id=? AND skill_key='orecraft'",
                          (f"stationnet:character:{first}",)).fetchone()[0] == 0
        assert "depleted" in mud._gather_action(db, _user(db, first),
                                                 {"arguments": ["Copper Vein"]})[0].body


def test_builder_and_package_round_trip(game):
    app, (first, _) = game
    with app.app_context():
        db = get_db(); room = mud._start_room(db)
        db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,world_id) "
                   "VALUES('herb','Wild Herb','A herb.','misc','stationnet')")
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = first; session["csrf_token"] = "test-token"
    result = client.post("/mud/builder/gathering-nodes", data={
        "csrf_token": "test-token", "room_id": room["id"], "name": "Herb patch",
        "activity": "harvest", "item_id": "herb", "amount": "2", "success_percent": "75",
        "respawn_seconds": "180", "min_rank": "0"})
    assert result.status_code == 302
    builder = client.get("/mud/builder")
    assert builder.status_code == 200 and b"Herb patch" in builder.data
    with app.app_context():
        db = get_db()
        node = db.execute("SELECT * FROM mud_gathering_nodes WHERE name='Herb patch'").fetchone()
        assert node and mud_gathering.rewards(node["rewards_json"])[0]["amount"] == 2
        node_id = node["id"]
    changed = client.post(f"/mud/builder/gathering-nodes/{node_id}/rewards/update", data={
        "csrf_token": "test-token", "item_id": "herb", "amount": "3"})
    assert changed.status_code == 302
    with app.app_context():
        db = get_db()
        assert mud_gathering.rewards(db.execute(
            "SELECT rewards_json FROM mud_gathering_nodes WHERE id=?", (node_id,)).fetchone()[0])[0]["amount"] == 3
        exported = mud_worlds.read_package(mud_worlds.export(db, "stationnet"))
        packaged = exported["content"]["mud_gathering_nodes"][0]
        assert packaged["activity"] == "harvest"
        assert packaged["rewards_json"][0]["item_id"] in {
            item["id"] for item in exported["content"]["mud_item_definitions"]}
        exported["id"] = "gather-copy"; exported["name"] = "Gather Copy"
        mud_worlds.install(db, mud_worlds.package_bytes(exported))
        copied = db.execute("SELECT n.* FROM mud_gathering_nodes n JOIN mud_world_rooms r ON r.id=n.room_id "
                            "WHERE r.world_id='gather-copy'").fetchone()
        assert copied and copied["available_at"] is None
        assert mud_gathering.rewards(copied["rewards_json"])[0]["amount"] == 3


def test_bundled_gathering_skill_round_trip(game):
    app, _ = game
    document = {
        "id": "ore-world", "name": "Ore World", "author": "Test", "start_room": "cave",
        "rooms": [{"id": "cave", "name": "Cave", "description": "A cave."}], "exits": [],
        "skills": [{"key": "orecraft", "name": "Orecraft", "description": "Finding ore.",
                    "group": "exploration", "effects": [{"type": "gathering", "per_rank": 5}],
                    "practice": {"triggers": ["gather"]}}],
        "content": {"mud_item_definitions": [
            {"id": "ore", "name": "Copper Ore", "description": "Ore.", "item_type": "misc"},
            {"id": "pick", "name": "Pickaxe", "description": "A pick.", "item_type": "misc"}],
            "mud_gathering_nodes": [{"id": "vein", "room_id": "cave", "name": "Copper Vein",
                                     "activity": "mine", "rewards_json": [{"item_id": "ore", "amount": 2}],
                                     "skill_key": "orecraft", "min_rank": 1,
                                     "tool_definition_id": "pick", "success_percent": 70,
                                     "respawn_seconds": 600}]}}
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.package_bytes(document))
        node = db.execute("SELECT n.* FROM mud_gathering_nodes n JOIN mud_world_rooms r ON r.id=n.room_id "
                          "WHERE r.world_id='ore-world'").fetchone()
        assert node["skill_key"] != "orecraft"
        assert node["skill_key"] in mud_skills.definitions(db, include_inactive=True)
        exported = mud_worlds.read_package(mud_worlds.export(db, "ore-world"))
        assert exported["content"]["mud_gathering_nodes"][0]["skill_key"] == "orecraft"
        assert exported["skills"][0]["key"] == "orecraft"


@pytest.mark.parametrize("rewards", [[{"item_id": "missing", "amount": 21}],
                                    [{"item_id": "missing", "amount": 1}]])
def test_package_rejects_unbounded_or_missing_rewards(rewards):
    document = {"id": "gather-check", "name": "Gather Check", "author": "Test", "start_room": "field",
                "rooms": [{"id": "field", "name": "Field", "description": "A field."}], "exits": [],
                "content": {"mud_gathering_nodes": [{"id": "patch", "room_id": "field", "name": "Patch",
                                                    "activity": "harvest", "rewards_json": rewards,
                                                    "respawn_seconds": 60}]}}
    with pytest.raises(mud_worlds.WorldError):
        mud_worlds.read_package(mud_worlds.package_bytes(document))
