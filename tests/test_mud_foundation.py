"""Foundational MUD commands, private events, and durable NPC ticks."""
from datetime import datetime, timedelta, timezone
import json
import shutil
import uuid
from pathlib import Path

import pytest

from stationnet import create_app, character_creation, mud, mud_worlds
from stationnet.db import get_db


@pytest.fixture
def game():
    data_path = Path(__file__).parent / f"_mud_foundation_{uuid.uuid4().hex}"
    data_path.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        room = mud._start_room(db)
        users = []
        for username, callsign, name in (("one", "ONE", "Ada One"), ("two", "TWO", "Bea Two")):
            uid = db.execute("INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
                             (username, callsign, "unused", f"connection-{username}")).lastrowid
            character_creation.complete(db, uid, {
                "name": name, "race": "human", "class": "warrior",
                "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                "skills": ["lore", "guard"]})
            mud._set_player_location(db, uid, "traditional", "world_room", room["id"])
            db.execute("INSERT INTO mud_presence(world_key,user_id,connection_id,client_id,room_id,location_type,location_key,last_seen) "
                       "VALUES(mud_world(),?,?,?,?,?,?,?)", (uid, f"connection-{username}", f"client-{username}-123", room["id"],
                                               "world_room", room["id"], mud._scheduled_now()))
            users.append(uid)
        db.commit()
    yield app, users
    coordinator = app.extensions["broadcast_coordinator"]
    coordinator._closed = True
    coordinator._timer.cancel()
    shutil.rmtree(data_path)


def test_named_containers_and_visible_actions(game):
    app, (uid, other) = game
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        room = mud._current_room(db, uid)
        assert "Aliases: /get, /take" in mud._help_action(db, user, {"arguments": ["get"]})[0].body
        db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type) VALUES('chest','Oak Chest','A chest','container')")
        db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type) VALUES('apple','Red Apple','An apple','misc')")
        for index in (1, 2):
            db.execute("INSERT INTO mud_item_instances(id,definition_id,room_id) VALUES(?, 'chest', ?)",
                       (f"chest-{index}", room["id"]))
        db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) VALUES('apple-1','apple',?)",
                   (f"stationnet:character:{uid}",))
        with pytest.raises(mud.ActionError, match="More than one"):
            mud._open_action(db, user, {"arguments": ["Oak", "Chest"]})
        opened = mud._open_action(db, user, {"arguments": ["Oak", "Chest", "2"]})
        assert "You open" in opened[0].body and other in opened[1].recipient_ids
        assert "You put" in mud._put_action(db, user, {"arguments": ["Red", "Apple", "into", "Oak", "Chest", "2"]})[0].body
        assert "Red Apple" in mud._look_action(db, user, {"arguments": ["Oak", "Chest", "2"]})[0].body
        assert "An apple" in mud._look_action(db, user, {"arguments": ["Red", "Apple"]})[0].body
        assert "You pick up" in mud._get_action(db, user, {"arguments": ["Red", "Apple", "from", "Oak", "Chest", "2"]})[0].body
        assert "You close" in mud._close_action(db, user, {"arguments": ["Oak", "Chest", "2"]})[0].body


def test_private_social_presence_and_examination(game):
    app, (uid, other) = game
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        second = db.execute("SELECT * FROM users WHERE id=?", (other,)).fetchone()
        room = mud._current_room(db, uid)
        db.execute("INSERT INTO mud_presence(world_key,user_id,connection_id,client_id,room_id,location_type,location_key,last_seen) "
                   "VALUES(mud_world(),?,?,?,?,?,?,?)", (uid, "connection-one", "client-one-extra", room["id"],
                                           "world_room", room["id"], mud._scheduled_now()))
        assert mud._occupant_ids(db, room["id"]).count(uid) == 1
        assert "Bea Two" in mud._who_action(db, user, {})[0].body
        mud._description_action(db, second, {"arguments": ["A", "quiet", "explorer"]})
        assert "quiet explorer" in mud._look_action(db, user, {"arguments": ["Bea", "Two"]})[0].body
        drafts = mud._tell_action(db, user, {"arguments": ["Bea Two", "hello"]})
        for draft in drafts:
            mud._insert_event(db, draft, default_user_id=uid)
        assert "hello" in mud._events_for_user(db, other)[-1]["body"]
        assert "Ada One" in mud._reply_player_action(db, second, {"arguments": ["hi"]})[0].body
        mud._block_action(db, second, {"arguments": ["Ada", "One"]})
        with pytest.raises(mud.ActionError, match="not available"):
            mud._tell_action(db, user, {"arguments": ["Bea Two", "blocked"]})
        db.execute("DELETE FROM mud_presence WHERE user_id=?", (other,))
        assert "Bea Two" not in mud._who_action(db, user, {})[0].body
        db.execute("DELETE FROM mud_presence WHERE user_id=? AND client_id='client-one-123'", (uid,))
        assert "Ada One" in mud._who_action(db, user, {})[0].body


def test_private_message_reaches_another_browser_session(game):
    app, (uid, other) = game
    sender = app.test_client()
    recipient = app.test_client()
    for client, user_id in ((sender, uid), (recipient, other)):
        with client.session_transaction() as state:
            state["user_id"] = user_id
            state["csrf_token"] = "test-token"
    sender_id = "browser-sender-1234"
    recipient_id = "browser-recipient-1234"
    assert sender.get(f"/mud/w/stationnet/updates?client_id={sender_id}").status_code == 200
    assert recipient.get(f"/mud/w/stationnet/updates?client_id={recipient_id}").status_code == 200
    result = sender.post("/mud/w/stationnet/actions", data={
        "csrf_token": "test-token", "client_id": sender_id,
        "request_id": uuid.uuid4().hex, "command": '/tell "Bea Two" hello from another browser',
    })
    assert result.status_code == 200
    assert "You tell Bea Two" in result.json["events"][-1]["body"]
    received = recipient.get(f"/mud/w/stationnet/updates?client_id={recipient_id}&after=0")
    assert received.status_code == 200
    assert any("Ada One tells you: hello from another browser" in event["body"]
               for event in received.json["events"])


def test_npc_schedule_recovers_once_and_validates_packages(game):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        room = mud._start_room(db)
        config = {"ambient": {"interval_seconds": 5, "lines": ["Good day."]},
                  "reactions": {"enter": "Welcome."}}
        db.execute("INSERT INTO mud_npc_definitions(id,name,description,behaviors_json) VALUES('keeper','Keeper','A keeper',?)",
                   (json.dumps(config),))
        db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES('keeper-1','keeper',?)", (room["id"],))
        db.commit()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    assert mud.run_scheduled_tick(app, now) == 0
    assert mud.run_scheduled_tick(app, now + timedelta(seconds=6)) == 1
    assert mud.run_scheduled_tick(app, now + timedelta(seconds=6)) == 0
    with app.app_context():
        db = get_db()
        assert len(db.execute("SELECT id FROM mud_events WHERE event_type='npc'").fetchall()) == 1
        assert mud._events_for_user(db, uid)[-1]["body"] == "Keeper says: Good day."
        mud.schedule_room_message(db, room["id"], "A bell rings.", now + timedelta(seconds=7))
        db.commit()
    assert mud.run_scheduled_tick(app, now + timedelta(seconds=8)) == 1
    assert mud.run_scheduled_tick(app, now + timedelta(seconds=8)) == 0
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM mud_scheduled_events WHERE event_kind='room_message'").fetchone()[0] == 0
        assert mud._events_for_user(db, uid)[-1]["body"] == "A bell rings."
    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"]})
    assert mud.run_scheduled_tick(restarted, now + timedelta(days=1)) == 1
    assert mud.run_scheduled_tick(restarted, now + timedelta(days=1)) == 0
    restarted_coordinator = restarted.extensions["broadcast_coordinator"]
    restarted_coordinator._closed = True
    restarted_coordinator._timer.cancel()
    with pytest.raises(ValueError):
        mud.validate_npc_behaviors({"ambient": {"interval_seconds": 5, "lines": []}})
    with pytest.raises(mud_worlds.WorldError):
        mud_worlds.read_package(mud_worlds.package_bytes({
            "id": "badworld", "name": "Bad World", "author": "Tester", "start_room": "room",
            "rooms": [{"id": "room", "name": "Room", "description": "Room."}], "exits": [],
            "content": {"mud_npc_definitions": [{"id": "npc", "name": "NPC", "description": "NPC.",
                                                     "behaviors_json": {"mystery": 1}}]},
        }))


def test_npc_patrol_and_room_reaction(game):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        room = mud._start_room(db)
        db.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES('second','Second Room','Another room')")
        db.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) "
                   "VALUES('north-exit',?, 'second','north path','north')", (room["id"],))
        db.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) "
                   "VALUES('south-exit','second',?,'south path','south')", (room["id"],))
        config = {"patrol": {"interval_seconds": 5, "directions": ["north", "south"]},
                  "reactions": {"say": "I heard that."}}
        db.execute("INSERT INTO mud_npc_definitions(id,name,description,behaviors_json) VALUES('guard','Guard','A guard',?)",
                   (json.dumps(config),))
        db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES('guard-1','guard',?)", (room["id"],))
        db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id) VALUES('guard')")
        mud._encounter_row(db, mud._room_npc_rows(db, room["id"])[0])
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        assert "I heard that" in mud._npc_reactions(db, user, "say")[0].body
        db.commit()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    mud.run_scheduled_tick(app, now)
    mud.run_scheduled_tick(app, now + timedelta(seconds=6))
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT room_id FROM mud_npc_instances WHERE id='guard-1'").fetchone()[0] == "second"
        assert db.execute("SELECT scope_key FROM mud_combat_encounters WHERE npc_instance_id='guard-1' AND status='ready'").fetchone()[0] == "second"
        db.execute("UPDATE mud_combat_encounters SET status='active' WHERE npc_instance_id='guard-1'")
        db.commit()
    mud.run_scheduled_tick(app, now + timedelta(seconds=12))
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT room_id FROM mud_npc_instances WHERE id='guard-1'").fetchone()[0] == "second"
        db.execute("UPDATE mud_combat_encounters SET status='ready' WHERE npc_instance_id='guard-1'")
        db.commit()
    mud.run_scheduled_tick(app, now + timedelta(seconds=18))
    with app.app_context():
        assert get_db().execute("SELECT room_id FROM mud_npc_instances WHERE id='guard-1'").fetchone()[0] == mud.START_ROOM_ID


def test_existing_character_migrates_description_without_losing_state(game):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        before = db.execute("SELECT id,display_name FROM mud_characters WHERE user_id=?", (uid,)).fetchone()
        db.execute("ALTER TABLE mud_characters DROP COLUMN description")
        db.commit()
    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"]})
    with restarted.app_context():
        db = get_db()
        after = db.execute("SELECT id,display_name,description FROM mud_characters WHERE user_id=?", (uid,)).fetchone()
        assert after["id"] == before["id"]
        assert after["display_name"] == before["display_name"]
        assert after["description"] == ""
    coordinator = restarted.extensions["broadcast_coordinator"]
    coordinator._closed = True
    coordinator._timer.cancel()
