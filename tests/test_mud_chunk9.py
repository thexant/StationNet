import json
import uuid

import pytest

from stationnet import create_app, mud
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "chunk-9-tests",
        "DATABASE": str(tmp_path / "stationnet.sqlite"),
    })


def add_station(database, traditional=False, procedural=False):
    database.execute(
        "INSERT INTO station(id,name,description,callsign,mud_enabled,"
        "mud_traditional_enabled,mud_procedural_enabled) VALUES(1,?,?,?,?,?,?)",
        ("Test Station", "Testing", "TEST", int(traditional or procedural),
         int(traditional), int(procedural)),
    )


def add_user(database, name, admin=False):
    connection_id = uuid.uuid4().hex
    cursor = database.execute(
        "INSERT INTO users(username,callsign,password_hash,connection_id,is_admin) "
        "VALUES(?,?,?,?,?)", (name, name.upper(), "unused", connection_id, int(admin)),
    )
    database.commit()
    return database.execute(
        "SELECT * FROM users WHERE id=?", (cursor.lastrowid,),
    ).fetchone()


def set_modes(database, traditional, procedural):
    database.execute(
        "UPDATE station SET mud_enabled=?,mud_traditional_enabled=?,"
        "mud_procedural_enabled=? WHERE id=1",
        (int(traditional or procedural), int(traditional), int(procedural)),
    )
    mud.reconcile_mode_locations(database)
    database.commit()


def test_all_mode_combinations_route_and_reconcile_without_stranding(app):
    with app.app_context():
        database = get_db()
        add_station(database, traditional=True, procedural=True)
        user = add_user(database, "traveler")

        assert mud._current_room(database, user["id"])["id"] == mud.START_ROOM_ID
        mud._portal_action(database, user, {})
        assert mud._current_room(database, user["id"])["id"] == mud.PORTAL_ROOM_ID
        database.execute(
            "INSERT INTO mud_presence(user_id,connection_id,client_id,room_id,"
            "location_type,location_key,last_seen) VALUES(?,?,?,NULL,'portal',?,CURRENT_TIMESTAMP)",
            (user["id"], user["connection_id"], "activeclient01", mud.PORTAL_ROOM_ID),
        )

        set_modes(database, traditional=True, procedural=False)
        assert mud._current_room(database, user["id"])["id"] == mud.START_ROOM_ID
        presence = database.execute(
            "SELECT room_id,location_type,location_key FROM mud_presence WHERE user_id=?",
            (user["id"],),
        ).fetchone()
        assert (presence["room_id"], presence["location_type"], presence["location_key"]) == (
            mud.START_ROOM_ID, "world_room", mud.START_ROOM_ID,
        )

        set_modes(database, traditional=False, procedural=True)
        portal = mud._current_room(database, user["id"])
        assert portal["id"] == mud.PORTAL_ROOM_ID
        assert portal["system_managed"] is True

        set_modes(database, traditional=False, procedural=False)
        assert database.execute("SELECT 1 FROM mud_presence").fetchone() is None
        with pytest.raises(mud.ActionError):
            mud._current_room(database, user["id"])


def test_portal_navigation_preserves_last_traditional_location_and_identity(app):
    with app.app_context():
        database = get_db()
        add_station(database, traditional=True, procedural=True)
        user = add_user(database, "identity")
        database.execute(
            "INSERT INTO mud_world_rooms(id,name,description) VALUES('test:home','Home','Safe.')"
        )
        database.execute("UPDATE mud_world_rooms SET is_start=0 WHERE is_start=1")
        database.execute("UPDATE mud_world_rooms SET is_start=1 WHERE id='test:home'")
        mud._set_player_location(
            database, user["id"], "traditional", "world_room", "test:home"
        )
        character_id = mud.character_state(database, user["id"])["id"]

        mud._portal_action(database, user, {})
        assert mud._current_room(database, user["id"])["id"] == mud.PORTAL_ROOM_ID
        mud._world_action(database, user, {})
        assert mud._current_room(database, user["id"])["id"] == "test:home"
        assert mud.character_state(database, user["id"])["id"] == character_id


def test_portal_controls_share_actions_and_report_future_gameplay(app):
    with app.app_context():
        database = get_db()
        add_station(database, procedural=True)
        user = add_user(database, "explorer")
        assert mud._current_room(database, user["id"])["id"] == mud.PORTAL_ROOM_ID
        options = mud.actions.contextual(user, database)
        portal_options = [item for item in options if item["key"] == "dungeon_entrance"]
        assert {item["parameters"]["kind"] for item in portal_options} == {"daily", "random"}
        definition, parameters = mud.actions.from_command("/daily")
        assert definition is mud.actions.get("dungeon_entrance")
        event = definition.handler(database, user, parameters)[0]
        assert "not yet available" in event.body
        assert database.execute("SELECT 1 FROM mud_procedural_instances").fetchone() is None


def test_sysop_customization_is_protected_from_non_admin_and_builder_data(app):
    with app.app_context():
        database = get_db()
        add_station(database, traditional=True)
        admin = add_user(database, "sysop", admin=True)
        outsider = add_user(database, "member")

    form = {
        "csrf_token": "chunk9-token",
        "mud_traditional_enabled": "on",
        "mud_procedural_enabled": "on",
        "mud_portal_name": "The Threshold",
        "mud_portal_description": "Silver arches wait in silence.",
    }
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = outsider["id"]
        state["csrf_token"] = "chunk9-token"
    denied = client.post("/sysop/settings/mud", data=form)
    assert denied.status_code == 302
    assert denied.headers["Location"].endswith("/terminal")

    with client.session_transaction() as state:
        state["user_id"] = admin["id"]
        state["csrf_token"] = "chunk9-token"
    response = client.post("/sysop/settings/mud", data=form)
    assert response.status_code == 302
    with app.app_context():
        database = get_db()
        portal = mud._portal_room(database)
        assert portal["name"] == "The Threshold"
        assert portal["description"] == "Silver arches wait in silence."
        assert "not yet available" in portal["instructions"]
        assert database.execute(
            "SELECT 1 FROM mud_world_rooms WHERE id=?", (mud.PORTAL_ROOM_ID,),
        ).fetchone() is None


def test_action_api_enters_portal_and_reconnect_keeps_procedural_location(app):
    with app.app_context():
        database = get_db()
        add_station(database, traditional=True, procedural=True)
        user = add_user(database, "online")
        user_id, connection_id = user["id"], user["connection_id"]

    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = user_id
        state["connection_id"] = connection_id
        state["csrf_token"] = "chunk9-token"
    common = {"csrf_token": "chunk9-token", "client_id": "chunk9client01"}
    entered = client.post("/mud/actions", data={
        **common, "request_id": "chunk9portal01", "action": "portal",
        "parameters": json.dumps({}),
    })
    assert entered.status_code == 200
    assert entered.get_json()["room"]["id"] == mud.PORTAL_ROOM_ID
    update = client.get("/mud/updates?after=0&client_id=chunk9client01")
    assert update.status_code == 200
    assert update.get_json()["room"]["id"] == mud.PORTAL_ROOM_ID
    with app.app_context():
        presence = get_db().execute(
            "SELECT room_id,location_type,location_key FROM mud_presence WHERE user_id=?",
            (user_id,),
        ).fetchone()
        assert presence["room_id"] is None
        assert (presence["location_type"], presence["location_key"]) == (
            "portal", mud.PORTAL_ROOM_ID,
        )
