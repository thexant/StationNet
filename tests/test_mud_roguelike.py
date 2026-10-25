"""The portal economy and expedition item boundary must survive persisted runs."""
from pathlib import Path
import json
import shutil
import uuid

import pytest

from stationnet import create_app
from stationnet.db import get_db
from stationnet import mud


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


def test_shop_storage_extraction_death_and_completion(game):
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
        mud._retreat_action(db, user, {})
        assert mud._current_room(db, user_id)["id"] == mud.PORTAL_ROOM_ID
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
        assert mud._current_room(db, user_id)["id"] == mud.PORTAL_ROOM_ID
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (doomed,)).fetchone() is None
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (doomed_gear,)).fetchone() is None
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (owned,)).fetchone()
        assert len(mud.hub_state(db, user_id)["storage"]) == 1

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
        mud._go_action(db, user, {"exit_id": exit_row["id"]})
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
        mud._flee_action(db, user, {})
        assert mud._current_room(db, user_id)["id"] == entrance["id"]
        db.execute("UPDATE mud_dungeon_content SET room_key=? WHERE id=?",
                   (entrance["id"], enemy["id"]))
        loot = mud._grant_item(db, user_id, "stationnet:treasure:relic")
        mud._entry_attack(db, user)
        mud._flee_action(db, user, {})
        assert mud._current_room(db, user_id)["id"] == mud.PORTAL_ROOM_ID
        assert db.execute("SELECT acquired_run_id FROM mud_item_instances WHERE id=?",
                          (loot,)).fetchone()[0] is None


def test_hub_http_controls_and_idempotent_purchase(game):
    app, user_id = game
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    page = client.get("/mud")
    assert page.status_code == 200
    assert b"Trader &amp; Storage" in page.data
    payload = {
        "csrf_token": "test-token", "request_id": "buy-once-1234",
        "action": "shop", "parameters": json.dumps({
            "operation": "buy", "item_id": "stationnet:shop:tonic"
        }),
    }
    first = client.post("/mud/actions", data=payload)
    second = client.post("/mud/actions", data=payload)
    assert first.status_code == second.status_code == 200
    assert first.json["hub"]["coins"] == second.json["hub"]["coins"] == 6
    assert len(first.json["inventory"]["inventory"]) == 1
    assert len(second.json["inventory"]["inventory"]) == 1
    denied = client.post("/mud/actions", data={
        **payload, "request_id": "buy-twice-1234",
    })
    assert denied.status_code == 400
    assert denied.json["hub"]["coins"] == 6


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
        mud._abandon_expedition_action(db, user, {})
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?",
                          (acquired["id"],)).fetchone() is None
