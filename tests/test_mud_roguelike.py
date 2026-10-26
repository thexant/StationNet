"""The portal economy and expedition item boundary must survive persisted runs."""
from pathlib import Path
import json
import shutil
import uuid

import pytest
from flask import session

from stationnet import create_app
from stationnet.db import get_db
from stationnet import mud


def test_expeditions_alias_uses_expedition_handler():
    definition, parameters = mud.actions.from_command("/expeditions")
    assert definition.key == "expedition"
    assert parameters == {"arguments": []}


@pytest.fixture
def game():
    data_dir = Path(__file__).parent / f"_mud_test_{uuid.uuid4().hex}"
    data_dir.mkdir()
    try:
        app = create_app({"TESTING": True, "DATA_DIR": str(data_dir)})
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO station(name,description,callsign,mud_enabled,"
                "mud_procedural_enabled) VALUES('Test','Test','TST',1,1)"
            )
            user_id = db.execute(
                "INSERT INTO users(username,callsign,password_hash) VALUES('delver','DLV','unused')"
            ).lastrowid
            db.execute("UPDATE mud_characters SET creation_state='complete' WHERE user_id=?",
                       (user_id,))
            mud._set_player_location(db, user_id, "procedural", "portal", mud.PORTAL_ROOM_ID)
            db.commit()
        yield app, user_id
    finally:
        coordinator = app.extensions["broadcast_coordinator"] if "app" in locals() else None
        if coordinator is not None:
            coordinator._closed = True
            coordinator._timer.cancel()
        shutil.rmtree(data_dir)


def _launch(db, user):
    mud._expedition_action(db, user, {"theme_id": "generic-fantasy-dungeon",
                                      "difficulty": 1, "size": 8})
    run = mud._active_expedition(db, user["id"])
    assert run is not None
    return run["id"]


def _owned(db, user_id):
    return {row["definition_id"]: row for row in db.execute(
        "SELECT definition_id,acquired_run_id FROM mud_item_instances "
        "WHERE owner_character_id=?", (f"stationnet:character:{user_id}",)
    ).fetchall()}


def test_shop_storage_completion_death_and_completion(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        assert mud.hub_state(db, user_id)["coins"] == 30
        mud._shop_action(db, user, {"operation": "buy", "item_id": "stationnet:shop:tonic"})
        tonic = next(row for row in mud._inventory_rows(db, user_id)
                     if row["definition_id"] == "stationnet:shop:tonic")
        mud._storage_action(db, user, {"operation": "deposit", "item_id": tonic["id"]})
        assert not mud._inventory_rows(db, user_id)
        assert len(mud.hub_state(db, user_id)["storage"]) == 1

        owned = mud._grant_item(db, user_id, "stationnet:shop:blade")
        mud._equip_action(db, user, {"item_id": owned})
        run_id = _launch(db, user)
        chart = mud._grant_item(db, user_id, "stationnet:shop:chart")
        guidance = mud._use_action(db, user, {"item_id": chart})
        assert any("toward the exit" in event.body for event in guidance)
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (chart,)).fetchone() is None
        acquired = mud._grant_item(db, user_id, "stationnet:treasure:relic")
        assert db.execute("SELECT acquired_run_id FROM mud_item_instances WHERE id=?",
                          (acquired,)).fetchone()[0] == run_id
        db.commit()

    # A new database connection sees the same active run and provisional loot.
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        assert mud._active_expedition(db, user_id)["id"] == run_id
        assert _owned(db, user_id)["stationnet:treasure:relic"]["acquired_run_id"] == run_id
        exit_room = db.execute(
            "SELECT room_key FROM mud_dungeon_rooms WHERE instance_id=? AND role='exit'",
            (run_id,),
        ).fetchone()["room_key"]
        db.execute(
            "INSERT INTO mud_dungeon_content_state(content_id,status) "
            "SELECT id,'defeated' FROM mud_dungeon_content WHERE instance_id=? "
            "AND room_key=? AND content_type='enemy'", (run_id, exit_room),
        )
        mud._set_player_location(db, user_id, "procedural", "dungeon_instance", exit_room, run_id)
        mud._record_expedition_visit(db, run_id, user_id, exit_room)
        completion = mud._complete_expedition_action(db, user, {})
        assert mud._current_room(db, user_id)["id"] == mud.PORTAL_ROOM_ID
        assert next(event.body for event in completion if event.event_type == "room") == \
            mud._look_action(db, user, {})[0].body
        assert _owned(db, user_id)["stationnet:treasure:relic"]["acquired_run_id"] is None
        mud._shop_action(db, user, {"operation": "sell", "item_id": acquired})
        assert mud.hub_state(db, user_id)["coins"] > 6

        second = _launch(db, user)
        doomed = mud._grant_item(db, user_id, "stationnet:treasure:gem")
        doomed_gear = mud._grant_item(db, user_id, "stationnet:shop:sabre")
        mud._equip_action(db, user, {"item_id": doomed_gear})
        enemy = db.execute(
            "SELECT c.room_key,c.id FROM mud_dungeon_content AS c "
            "WHERE c.instance_id=? AND c.content_type='enemy' LIMIT 1", (second,),
        ).fetchone()
        assert enemy is not None
        mud._set_player_location(db, user_id, "procedural", "dungeon_instance",
                                 enemy["room_key"], second)
        mud._record_expedition_visit(db, second, user_id, enemy["room_key"])
        character = mud._character_row(db, user_id)
        db.execute("UPDATE mud_character_progression SET current_health=1 "
                   "WHERE character_id=? AND track_key=?",
                   (character["id"], character["track_key"]))
        mud._entry_attack(db, user)
        assert mud._current_room(db, user_id)["id"] == "stationnet:death-summary"
        summary = mud._room_state(db, user_id)
        assert "DEATH SUMMARY" in summary["description"]
        assert "Rooms visited:" in summary["description"]
        assert "Run loot lost: 2 items" in summary["description"]
        assert db.execute("SELECT location_type FROM mud_player_locations WHERE user_id=?",
                          (user_id,)).fetchone()[0] == "dungeon_instance"
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (doomed,)).fetchone() is None
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (doomed_gear,)).fetchone() is None
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (owned,)).fetchone()

        mud._continue_action(db, user, {})
        assert mud._current_room(db, user_id)["id"] == mud.PORTAL_ROOM_ID
        assert len(mud.hub_state(db, user_id)["storage"]) == 1
        with pytest.raises(mud.ActionError, match="only available"):
            mud._continue_action(db, user, {})

        third = _launch(db, user)
        reward = mud._grant_item(db, user_id, "stationnet:treasure:gem")
        exit_room = db.execute(
            "SELECT room_key FROM mud_dungeon_rooms WHERE instance_id=? AND role='exit'",
            (third,),
        ).fetchone()["room_key"]
        db.execute(
            "INSERT INTO mud_dungeon_content_state(content_id,status) "
            "SELECT id,'defeated' FROM mud_dungeon_content WHERE instance_id=? "
            "AND room_key=? AND content_type='enemy'",
            (third, exit_room),
        )
        mud._set_player_location(db, user_id, "procedural", "dungeon_instance", exit_room, third)
        mud._record_expedition_visit(db, third, user_id, exit_room)
        before = mud._wallet(db, character["id"])
        mud._complete_expedition_action(db, user, {})
        assert mud._current_room(db, user_id)["id"] == mud.PORTAL_ROOM_ID
        assert db.execute("SELECT acquired_run_id FROM mud_item_instances WHERE id=?",
                          (reward,)).fetchone()[0] is None
        assert mud._wallet(db, character["id"]) > before


def test_combat_waits_for_actions_and_consumables_are_spent(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        run_id = _launch(db, user)
        enemy = db.execute(
            "SELECT room_key FROM mud_dungeon_content WHERE instance_id=? "
            "AND content_type='enemy' LIMIT 1", (run_id,),
        ).fetchone()
        assert enemy is not None
        mud._set_player_location(db, user_id, "procedural", "dungeon_instance",
                                 enemy["room_key"], run_id)
        mud._record_expedition_visit(db, run_id, user_id, enemy["room_key"])
        mud._entry_attack(db, user)
        health = mud.character_state(db, user_id)["health"]
        for _ in range(3):
            mud.combat_state(db, user_id)
        assert mud.character_state(db, user_id)["health"] == health
        character = mud._character_row(db, user_id)
        db.execute("UPDATE mud_character_progression SET current_health=40 "
                   "WHERE character_id=? AND track_key=?",
                   (character["id"], character["track_key"]))
        tonic = mud._grant_item(db, user_id, "stationnet:shop:tonic")
        mud._use_action(db, user, {"item_id": tonic})
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (tonic,)).fetchone() is None
        assert mud.character_state(db, user_id)["health"] > 40
        mud._defend_action(db, user, {})
        assert mud._active_expedition(db, user_id) is not None


def test_death_summary_persists_and_continue_is_scoped(game):
    app, user_id = game
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    assert client.get("/mud").status_code == 200

    def command(value):
        return client.post("/mud/actions", data={
            "csrf_token": "test-token", "command": value,
        })

    assert command("/continue").status_code == 400
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        run_id = _launch(db, user)
        enemy = db.execute(
            "SELECT room_key FROM mud_dungeon_content WHERE instance_id=? "
            "AND content_type='enemy' LIMIT 1", (run_id,),
        ).fetchone()
        mud._set_player_location(db, user_id, "procedural", "dungeon_instance",
                                 enemy["room_key"], run_id)
        mud._record_expedition_visit(db, run_id, user_id, enemy["room_key"])
        character = mud._character_row(db, user_id)
        db.execute("UPDATE mud_character_progression SET current_health=1 "
                   "WHERE character_id=? AND track_key=?",
                   (character["id"], character["track_key"]))
        mud._entry_attack(db, user)
        db.commit()

    page = client.get("/mud")
    assert b"DEATH SUMMARY" in page.data
    assert b"/continue" in page.data
    blocked = command("/portal")
    assert blocked.status_code == 400
    assert "death summary" in blocked.json["error"]
    assert blocked.json["room"]["id"] == "stationnet:death-summary"
    continued = command("/continue")
    assert continued.status_code == 200
    assert continued.json["room"]["id"] == mud.PORTAL_ROOM_ID
    assert command("/continue").status_code == 400


def test_room_entry_aggression_and_risky_flee(game, monkeypatch):
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        run_id = _launch(db, user)
        entrance = mud._current_room(db, user_id)
        exit_row = mud._room_exits(db, entrance["id"], run_id)[0]
        enemy = db.execute(
            "SELECT id FROM mud_dungeon_content WHERE instance_id=? "
            "AND content_type='enemy' LIMIT 1", (run_id,),
        ).fetchone()
        assert enemy is not None
        db.execute("UPDATE mud_dungeon_content SET room_key=? WHERE id=?",
                   (exit_row["to_room_id"], enemy["id"]))
        health = mud.character_state(db, user_id)["health"]
        movement = mud._go_action(db, user, {"exit_id": exit_row["id"]})
        assert next(event.body for event in movement if event.event_type == "room") == \
            mud._look_action(db, user, {})[0].body
        assert mud.character_state(db, user_id)["health"] < health
        with pytest.raises(mud.ActionError):
            mud._go_action(db, user, {"arguments": ["out"]})
        current = mud.character_state(db, user_id)["health"]
        db.execute("UPDATE mud_combat_encounters SET updated_at='2000-01-01' "
                   "WHERE scope_key=? AND status='active'", (run_id,))
        mud.combat_state(db, user_id)
        assert mud.character_state(db, user_id)["health"] == current
        monkeypatch.setattr(mud.secrets, "randbelow", lambda _limit: 99)
        mud._flee_action(db, user, {})
        assert mud._current_room(db, user_id)["id"] == exit_row["to_room_id"]
        monkeypatch.setattr(mud.secrets, "randbelow", lambda _limit: 0)
        escape = mud._flee_action(db, user, {})
        assert mud._current_room(db, user_id)["id"] == entrance["id"]
        assert next(event.body for event in escape if event.event_type == "room") == \
            mud._look_action(db, user, {})[0].body
        db.execute("UPDATE mud_dungeon_content SET room_key=? WHERE id=?",
                   (entrance["id"], enemy["id"]))
        loot = mud._grant_item(db, user_id, "stationnet:treasure:relic")
        mud._entry_attack(db, user)
        mud._flee_action(db, user, {})
        assert mud._current_room(db, user_id)["id"] == entrance["id"]
        assert db.execute("SELECT acquired_run_id FROM mud_item_instances WHERE id=?",
                          (loot,)).fetchone()[0] == run_id


def test_world_and_portal_entry_persist_look_in_events_log(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET mud_traditional_enabled=1")
        db.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES(?,?,?)",
                   ("test:library", "Library", "Rows of old books line the walls."))
        db.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) "
                   "VALUES(?,?,?,?,?)", ("test:library-exit", mud.START_ROOM_ID,
                                       "test:library", "Library door", "east"))
        mud._set_player_location(db, user_id, "traditional", "world_room", mud.START_ROOM_ID)
        db.execute("UPDATE users SET connection_id=? WHERE id=?", ("test-connection", user_id))
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        db.commit()
        request = mud.ActionRequest(mud.actions.get("go"), {"exit_id": "test:library-exit"})
        with app.test_request_context():
            session["connection_id"] = "test-connection"
            _, events = mud.service.execute(request, user)
        room_event = next(event for event in events if event["event_type"] == "room")
        assert room_event["body"] == mud._look_action(db, user, {})[0].body
        assert "Rows of old books line the walls." in room_event["body"]
        assert "Exits:" in room_event["body"]
        assert any(event["id"] == room_event["id"] for event in mud._events_for_user(db, user_id))

        mud._set_player_location(db, user_id, "traditional", "world_room", mud.START_ROOM_ID)
        portal_events = mud._portal_action(db, user, {})
        assert next(event.body for event in portal_events if event.event_type == "room") == \
            mud._look_action(db, user, {})[0].body
        world_events = mud._world_action(db, user, {})
        assert next(event.body for event in world_events if event.event_type == "room") == \
            mud._look_action(db, user, {})[0].body


def test_hiding_detection_and_surprise_attack(game, monkeypatch):
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        run_id = _launch(db, user)
        entrance = mud._current_room(db, user_id)
        exit_row = mud._room_exits(db, entrance["id"], run_id)[0]
        enemy = db.execute(
            "SELECT id FROM mud_dungeon_content WHERE instance_id=? "
            "AND content_type='enemy' LIMIT 1", (run_id,),
        ).fetchone()
        db.execute("UPDATE mud_dungeon_content SET room_key=? WHERE id=?",
                   (exit_row["to_room_id"], enemy["id"]))

        monkeypatch.setattr(mud.secrets, "randbelow", lambda _limit: 0)
        assert "hiding" in mud._hide_action(db, user, {})[0].body
        assert mud.combat_state(db, user_id)["hidden"]
        db.commit()
        assert mud._is_hidden(db, user_id)
        health = mud.character_state(db, user_id)["health"]
        movement = mud._go_action(db, user, {"exit_id": exit_row["id"]})
        assert any("unnoticed" in event.body for event in movement)
        assert mud.character_state(db, user_id)["health"] == health
        assert mud._is_hidden(db, user_id)

        npc = mud._room_npc_rows(db, exit_row["to_room_id"], run_id)[0]
        encounter = mud._encounter_row(db, npc)
        character = mud._character_row(db, user_id)
        player_gear = mud._equipment_combat_totals(db, character_id=character["id"])
        enemy_gear = mud._equipment_combat_totals(db, npc_definition_id=npc["definition_id"])
        effects = mud._character_effects(db, character["id"])
        base = max(1, 4 + character["strength"] // 2 + player_gear["attack"]
                   + effects["attack"] - (npc["combat_defense"] + enemy_gear["defense"]) // 2)
        result = mud._attack_action(db, user, {"arguments": [npc["id"]]})
        assert "surprises" in result[0].body
        assert encounter["current_health"] - db.execute(
            "SELECT current_health FROM mud_combat_encounters WHERE id=?",
            (encounter["id"],),
        ).fetchone()[0] == min(encounter["current_health"],
                              base + max(2, character["agility"] // 2))
        assert not mud._is_hidden(db, user_id)
        if db.execute("SELECT status FROM mud_combat_encounters WHERE id=?",
                      (encounter["id"],)).fetchone()[0] != "defeated":
            followup = mud._attack_action(db, user, {"arguments": [npc["id"]]})
            assert "surprises" not in followup[0].body


def test_failed_hiding_and_enemy_detection(game, monkeypatch):
    assert mud._stealth_chance(14, 5) > mud._stealth_chance(8, 5)
    assert mud._stealth_chance(10, 9) < mud._stealth_chance(10, 3)
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        run_id = _launch(db, user)
        entrance = mud._current_room(db, user_id)
        exit_row = mud._room_exits(db, entrance["id"], run_id)[0]
        enemy = db.execute(
            "SELECT id FROM mud_dungeon_content WHERE instance_id=? "
            "AND content_type='enemy' LIMIT 1", (run_id,),
        ).fetchone()
        db.execute("UPDATE mud_dungeon_content SET room_key=? WHERE id=?",
                   (exit_row["to_room_id"], enemy["id"]))
        monkeypatch.setattr(mud.secrets, "randbelow", lambda _limit: 99)
        assert "spotted" in mud._hide_action(db, user, {})[0].body
        assert not mud._is_hidden(db, user_id)
        mud._set_hidden(db, user_id, True)
        result = mud._go_action(db, user, {"exit_id": exit_row["id"]})
        assert any("spots you" in event.body for event in result)
        assert not mud._is_hidden(db, user_id)
        with pytest.raises(mud.ActionError, match="while fighting"):
            mud._hide_action(db, user, {})


def test_hub_console_commands_and_idempotent_purchase(game):
    app, user_id = game
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    page = client.get("/mud")
    assert page.status_code == 200
    assert b'data-mud-suggestions' in page.data
    assert b'data-mud-initial-state' in page.data
    assert b'"name": "expeditions"' in page.data
    assert b"QUICK ACTIONS" not in page.data
    assert b"data-mud-action=" not in page.data
    payload = {
        "csrf_token": "test-token", "request_id": "buy-once-1234",
        "command": "/shop buy Healing Tonic",
    }
    first = client.post("/mud/actions", data=payload)
    second = client.post("/mud/actions", data=payload)
    assert first.status_code == second.status_code == 200
    assert first.json["hub"]["coins"] == second.json["hub"]["coins"] == 6
    assert len(first.json["inventory"]["inventory"]) == 1
    assert len(second.json["inventory"]["inventory"]) == 1
    listed = client.post("/mud/actions", data={
        "csrf_token": "test-token", "command": "/inventory",
    })
    assert listed.status_code == 200
    assert "Healing Tonic" in listed.json["events"][0]["body"]
    denied = client.post("/mud/actions", data={
        **payload, "request_id": "buy-twice-1234",
    })
    assert denied.status_code == 400
    assert denied.json["hub"]["coins"] == 6
    assert client.post("/mud/actions", data={
        "csrf_token": "test-token", "action": "shop",
        "parameters": json.dumps({"operation": "buy", "item_id": "stationnet:shop:tonic"}),
    }).status_code == 400


def test_numbered_enemy_commands_and_inventory(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        run_id = _launch(db, user)
        entrance = mud._current_room(db, user_id)
        owned = mud._grant_item(db, user_id, "stationnet:shop:blade")
        mud._equip_action(db, user, {"item_id": owned})
        enemies = db.execute(
            "SELECT id,snapshot_json FROM mud_dungeon_content WHERE instance_id=? "
            "AND content_type='enemy' ORDER BY id LIMIT 2", (run_id,),
        ).fetchall()
        assert len(enemies) == 2
        for enemy in enemies:
            snapshot = json.loads(enemy["snapshot_json"])
            snapshot["name"] = "Rat"
            db.execute("UPDATE mud_dungeon_content SET room_key=?,snapshot_json=? WHERE id=?",
                       (entrance["id"], json.dumps(snapshot), enemy["id"]))
        room = mud._room_state(db, user_id)
        assert [npc["display_name"] for npc in room["npcs"]] == ["Rat (1)", "Rat (2)"]
        assert [enemy["display_name"] for enemy in mud.combat_state(db, user_id)["enemies"]] == ["Rat (1)", "Rat (2)"]
        assert "Rat" in mud._look_action(db, user, {"arguments": ["Rat", "2"]})[0].body
        with pytest.raises(mud.ActionError, match="Use a number"):
            mud._attack_action(db, user, {"arguments": ["Rat"]})
        with pytest.raises(mud.ActionError, match="number 3"):
            mud._attack_action(db, user, {"arguments": ["Rat", "3"]})
        first = mud._encounter_row(db, mud._room_npc_rows(db, entrance["id"], run_id)[0])
        second = mud._encounter_row(db, mud._room_npc_rows(db, entrance["id"], run_id)[1])
        definition, parameters = mud.actions.from_command("/attack Rat 2")
        definition.handler(db, user, parameters)
        assert db.execute("SELECT current_health FROM mud_combat_encounters WHERE id=?",
                          (second["id"],)).fetchone()[0] < second["current_health"]
        assert db.execute("SELECT current_health FROM mud_combat_encounters WHERE id=?",
                          (first["id"],)).fetchone()[0] == first["current_health"]
        inventory = mud._inventory_action(db, user, {})[0].body
        assert "Carried:" in inventory and "Equipped:" in inventory
        assert "Blade" in inventory
        first_tonic = mud._grant_item(db, user_id, "stationnet:shop:tonic")
        second_tonic = mud._grant_item(db, user_id, "stationnet:shop:tonic")
        inventory = mud._inventory_action(db, user, {})[0].body
        assert "Healing Tonic (1)" in inventory and "Healing Tonic (2)" in inventory
        ordered_tonics = [row["id"] for row in mud._inventory_rows(db, user_id)
                          if row["id"] in {first_tonic, second_tonic}]
        assert mud._item_matches(mud._inventory_rows(db, user_id), "Healing Tonic 2")["id"] == ordered_tonics[1]
        for command in ("/retreat", "/extract"):
            with pytest.raises(mud.ActionError, match="Unknown command"):
                mud.actions.from_command(command)


def test_clear_events_keeps_new_activity_and_survives_reload(game):
    app, user_id = game
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    with app.app_context():
        db = get_db()
        old_id = db.execute(
            "INSERT INTO mud_events(audience_type,audience_key,event_type,body) "
            "VALUES('private',?,'system','old activity marker')",
            (str(user_id),),
        ).lastrowid
        db.commit()
    assert b"old activity marker" in client.get("/mud").data

    cleared = client.post("/mud/clear-events", data={"csrf_token": "test-token"})
    assert cleared.status_code == 200
    assert cleared.json["cleared_through_id"] >= old_id
    page = client.get("/mud")
    assert b"old activity marker" not in page.data
    assert b"Activity cleared. New events will appear here." in page.data

    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO mud_events(audience_type,audience_key,event_type,body) "
            "VALUES('private',?,'system','new activity marker')",
            (str(user_id),),
        )
        db.commit()
        assert [row["body"] for row in mud._events_for_user(db, user_id, after=0)] == [
            "new activity marker"
        ]
    page = client.get("/mud")
    assert b"old activity marker" not in page.data
    assert b"new activity marker" in page.data


def test_existing_item_table_is_migrated(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        db.execute("DROP INDEX idx_mud_item_instances_run")
        db.execute("ALTER TABLE mud_item_instances DROP COLUMN acquired_run_id")
        db.commit()
    reopened = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"]})
    try:
        with reopened.app_context():
            db = get_db()
            columns = {row["name"] for row in db.execute(
                "PRAGMA table_info(mud_item_instances)"
            )}
            assert "acquired_run_id" in columns
            assert mud.hub_state(db, user_id)["coins"] == 30
    finally:
        coordinator = reopened.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()


def test_generated_pickup_cannot_duplicate_and_abandon_loses_it(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        run_id = _launch(db, user)
        entrance = mud._current_room(db, user_id)
        loot = db.execute(
            "SELECT id,definition_id FROM mud_dungeon_content WHERE instance_id=? "
            "AND pool_id='stationnet-risk-loot' LIMIT 1", (run_id,),
        ).fetchone()
        assert loot is not None
        db.execute("UPDATE mud_dungeon_content SET room_key=? WHERE id=?",
                   (entrance["id"], loot["id"]))
        mud._get_action(db, user, {"item_id": loot["id"]})
        acquired = db.execute(
            "SELECT id,acquired_run_id FROM mud_item_instances WHERE definition_id=? "
            "AND owner_character_id=?", (loot["definition_id"], f"stationnet:character:{user_id}"),
        ).fetchone()
        assert acquired["acquired_run_id"] == run_id
        with pytest.raises(mud.ActionError):
            mud._get_action(db, user, {"item_id": loot["id"]})
        assert db.execute(
            "SELECT COUNT(*) FROM mud_item_instances WHERE acquired_run_id=?", (run_id,)
        ).fetchone()[0] == 1
        abandonment = mud._abandon_expedition_action(db, user, {})
        assert next(event.body for event in abandonment if event.event_type == "room") == \
            mud._look_action(db, user, {})[0].body
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?",
                          (acquired["id"],)).fetchone() is None
