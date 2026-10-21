from stationnet import create_app
from stationnet.db import get_db
from stationnet.mud import (
    award_experience, character_state, experience_for_level, level_for_experience,
)

from .conftest import csrf


def enable_mud(client):
    return client.post(
        "/sysop/settings/mud",
        data={"csrf_token": csrf(client, "/sysop/settings"), "mud_enabled": "on"},
        follow_redirects=True,
    )


def install_test_rooms(app):
    """Create the smallest useful directional/named navigation fixture."""
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO mud_world_rooms(id,name,description) VALUES(?,?,?)",
            ("test:hall", "Test Hall", "A temporary hall used to verify movement."),
        )
        database.executemany(
            "INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) "
            "VALUES(?,?,?,?,?)",
            (
                ("test:north", "stationnet:start", "test:hall", "forward hatch", "north"),
                ("test:return", "test:hall", "stationnet:start", "return hatch", None),
            ),
        )
        database.commit()


def register_member(app, username):
    client = app.test_client()
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf(client, "/register"),
            "username": username,
            "password": "member password",
            "confirm_password": "member password",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    return client


def mud_update(client, client_id, after=0):
    return client.get(f"/mud/updates?client_id={client_id}&after={after}")


def test_mud_is_disabled_by_default_and_hidden(initialized, app):
    terminal = initialized.get("/terminal")
    assert b"STATION MUD" not in terminal.data
    assert b">MUD</a>" not in terminal.data
    assert initialized.get("/mud").status_code == 404

    with app.app_context():
        assert get_db().execute("SELECT mud_enabled FROM station").fetchone()[0] == 0
        tables = {
            row[0] for row in get_db().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"mud_action_log", "mud_events"} <= tables


def test_sysop_can_enable_and_disable_mud_without_deleting_data(initialized, app):
    response = enable_mud(initialized)
    assert b"MUD service enabled" in response.data
    assert b"STATION MUD" in initialized.get("/terminal").data
    mud_page = initialized.get("/mud")
    assert b">MUD</a>" in mud_page.data
    for marker in (
        b"data-mud-events", b"data-mud-actions", b"data-mud-command",
        b'id="mud-character-panel"', b'id="mud-inventory-panel"',
    ):
        assert marker in mud_page.data

    initialized.post(
        "/mud/actions",
        data={
            "csrf_token": csrf(initialized, "/mud"),
            "action": "status",
            "parameters": "{}",
            "request_id": "persist-001",
        },
    )
    response = initialized.post(
        "/sysop/settings/mud",
        data={"csrf_token": csrf(initialized, "/sysop/settings")},
        follow_redirects=True,
    )
    assert b"MUD service disabled" in response.data
    assert initialized.get("/mud").status_code == 404
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM mud_events").fetchone()[0] == 1


def test_mud_requires_authentication(initialized):
    enable_mud(initialized)
    initialized.post("/logout", data={"csrf_token": csrf(initialized, "/terminal")})
    response = initialized.get("/mud")
    assert response.status_code == 302
    assert "/login?next=/mud" in response.headers["Location"]


def test_context_button_and_command_share_action_pipeline(initialized, app):
    enable_mud(initialized)
    token = csrf(initialized, "/mud")
    contextual = initialized.post(
        "/mud/actions",
        data={
            "csrf_token": token,
            "action": "status",
            "parameters": "{}",
            "request_id": "context-001",
        },
    )
    command = initialized.post(
        "/mud/actions",
        data={
            "csrf_token": token,
            "command": "/status",
            "request_id": "command-001",
        },
    )

    assert contextual.status_code == command.status_code == 200
    assert contextual.json["action"] == command.json["action"] == "status"
    assert contextual.json["events"][0]["body"] == command.json["events"][0]["body"]
    with app.app_context():
        rows = get_db().execute(
            "SELECT action_key,input_method FROM mud_action_log ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("status", "context"), ("status", "command")
        ]


def test_action_requests_are_idempotent_and_persist_across_restart(initialized, app):
    enable_mud(initialized)
    token = csrf(initialized, "/mud")
    payload = {
        "csrf_token": token,
        "command": "/help",
        "request_id": "idempotent-001",
    }
    first = initialized.post("/mud/actions", data=payload)
    second = initialized.post("/mud/actions", data=payload)
    assert first.status_code == second.status_code == 200
    assert first.json["events"] == second.json["events"]

    restarted = create_app({
        "TESTING": True,
        "DATA_DIR": app.config["DATA_DIR"],
        "SECRET_KEY": "new-process",
    })
    with restarted.app_context():
        database = get_db()
        assert database.execute("SELECT COUNT(*) FROM mud_action_log").fetchone()[0] == 1
        assert database.execute("SELECT COUNT(*) FROM mud_events").fetchone()[0] == 1


def test_mud_action_validation_and_csrf(initialized):
    enable_mud(initialized)
    assert initialized.post(
        "/mud/actions", data={"command": "/help", "request_id": "missing-csrf"}
    ).status_code == 400
    token = csrf(initialized, "/mud")
    unknown = initialized.post(
        "/mud/actions",
        data={"csrf_token": token, "command": "/dance", "request_id": "unknown-001"},
    )
    assert unknown.status_code == 400
    assert "Unknown command" in unknown.json["error"]


def test_only_sysop_can_change_mud_setting(initialized):
    initialized.post(
        "/register",
        data={
            "csrf_token": csrf(initialized, "/register"),
            "username": "member",
            "password": "member password",
            "confirm_password": "member password",
        },
    )
    response = initialized.post(
        "/sysop/settings/mud",
        data={"csrf_token": csrf(initialized, "/terminal"), "mud_enabled": "on"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/terminal")


def test_fresh_install_has_only_a_generic_starting_room(initialized, app):
    enable_mud(initialized)
    page = initialized.get("/mud")
    assert b"Starting Room" in page.data
    assert b"quiet, unadorned room" in page.data
    assert b"data-mud-room" in page.data
    with app.app_context():
        database = get_db()
        rooms = database.execute(
            "SELECT id,name,is_start FROM mud_world_rooms"
        ).fetchall()
        assert [tuple(room) for room in rooms] == [
            ("stationnet:start", "Starting Room", 1)
        ]
        assert database.execute("SELECT COUNT(*) FROM mud_world_exits").fetchone()[0] == 0
        location = database.execute(
            "SELECT room_id FROM mud_world_locations"
        ).fetchone()
        assert location["room_id"] == "stationnet:start"


def test_directional_button_and_named_command_use_same_movement_action(initialized, app):
    install_test_rooms(app)
    enable_mud(initialized)
    token = csrf(initialized, "/mud")

    forward = initialized.post(
        "/mud/actions",
        data={
            "csrf_token": token,
            "action": "go",
            "parameters": '{"exit_id":"test:north"}',
            "request_id": "move-button-001",
        },
    )
    assert forward.status_code == 200
    assert forward.json["action"] == "go"
    assert forward.json["room"]["name"] == "Test Hall"
    assert "You go north" in forward.json["events"][0]["body"]
    assert any(
        action["parameters"] == {"exit_id": "test:return"}
        and "Starting Room" in action["label"]
        for action in forward.json["actions"]
    )

    back = initialized.post(
        "/mud/actions",
        data={
            "csrf_token": token,
            "command": "/go return hatch",
            "request_id": "move-command-001",
        },
    )
    assert back.status_code == 200
    assert back.json["action"] == "go"
    assert back.json["room"]["id"] == "stationnet:start"
    with app.app_context():
        rows = get_db().execute(
            "SELECT action_key,input_method FROM mud_action_log "
            "WHERE request_id LIKE 'move-%' ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("go", "context"), ("go", "command")
        ]


def test_direction_shortcut_and_location_persist_across_return(initialized, app):
    install_test_rooms(app)
    enable_mud(initialized)
    token = csrf(initialized, "/mud")
    moved = initialized.post(
        "/mud/actions",
        data={"csrf_token": token, "command": "/n", "request_id": "shortcut-001"},
    )
    assert moved.status_code == 200
    assert moved.json["room"]["id"] == "test:hall"

    restarted = create_app({
        "TESTING": True,
        "DATA_DIR": app.config["DATA_DIR"],
        "SECRET_KEY": "new-process",
    })
    with restarted.app_context():
        location = get_db().execute(
            "SELECT room_id FROM mud_world_locations"
        ).fetchone()
        assert location["room_id"] == "test:hall"


def test_look_exits_and_examination_errors(initialized, app):
    install_test_rooms(app)
    enable_mud(initialized)
    token = csrf(initialized, "/mud")
    looked = initialized.post(
        "/mud/actions",
        data={"csrf_token": token, "command": "/l", "request_id": "look-001"},
    )
    assert looked.status_code == 200
    assert "Starting Room" in looked.json["events"][0]["body"]
    assert "NORTH" in looked.json["events"][0]["body"]
    exits = initialized.post(
        "/mud/actions",
        data={"csrf_token": token, "command": "/exits", "request_id": "exits-001"},
    )
    assert "Test Hall" in exits.json["events"][0]["body"]
    missing = initialized.post(
        "/mud/actions",
        data={
            "csrf_token": token,
            "command": "/examine brass statue",
            "request_id": "examine-missing-001",
        },
    )
    assert missing.status_code == 400
    assert 'do not see "brass statue"' in missing.json["error"]


def test_invalid_movement_does_not_change_location_or_log_action(initialized, app):
    install_test_rooms(app)
    enable_mud(initialized)
    token = csrf(initialized, "/mud")
    rejected = initialized.post(
        "/mud/actions",
        data={
            "csrf_token": token,
            "command": "/go return hatch",
            "request_id": "invalid-move-001",
        },
    )
    assert rejected.status_code == 400
    assert "no exit matching" in rejected.json["error"]
    assert rejected.json["room"]["id"] == "stationnet:start"
    with app.app_context():
        database = get_db()
        assert database.execute(
            "SELECT room_id FROM mud_world_locations"
        ).fetchone()[0] == "stationnet:start"
        assert database.execute(
            "SELECT COUNT(*) FROM mud_action_log WHERE request_id='invalid-move-001'"
        ).fetchone()[0] == 0


def test_deleted_or_invalid_location_returns_player_to_start(initialized, app):
    install_test_rooms(app)
    enable_mud(initialized)
    token = csrf(initialized, "/mud")
    initialized.post(
        "/mud/actions",
        data={"csrf_token": token, "command": "/north", "request_id": "delete-move-001"},
    )
    with app.app_context():
        database = get_db()
        database.execute("DELETE FROM mud_world_rooms WHERE id='test:hall'")
        database.commit()
        assert database.execute(
            "SELECT room_id FROM mud_world_locations"
        ).fetchone()[0] is None

    recovered = initialized.post(
        "/mud/actions",
        data={"csrf_token": token, "command": "/look", "request_id": "recover-001"},
    )
    assert recovered.status_code == 200
    assert recovered.json["room"]["id"] == "stationnet:start"
    with app.app_context():
        assert get_db().execute(
            "SELECT room_id FROM mud_world_locations"
        ).fetchone()[0] == "stationnet:start"


def test_room_presence_chat_and_movement_are_realtime_and_room_scoped(initialized, app):
    install_test_rooms(app)
    enable_mud(initialized)
    alice = register_member(app, "alice")
    bob = register_member(app, "bob")
    alice.get("/mud")
    bob.get("/mud")

    alice_join = mud_update(alice, "alice-client-001")
    assert alice_join.status_code == 200
    assert alice_join.json["room"]["occupants"] == []
    bob_join = mud_update(bob, "bob-client-0001")
    assert bob_join.status_code == 200
    assert [item["username"] for item in bob_join.json["room"]["occupants"]] == ["alice"]

    alice_presence = mud_update(alice, "alice-client-001")
    assert [item["username"] for item in alice_presence.json["room"]["occupants"]] == ["bob"]
    assert any(event["event_type"] == "arrival" for event in alice_presence.json["events"])

    said = bob.post(
        "/mud/actions",
        data={
            "csrf_token": csrf(bob, "/mud"),
            "message": "Anyone hear me?",
            "request_id": "room-chat-001",
        },
    )
    assert said.status_code == 200
    assert said.json["action"] == "say"
    assert said.json["events"][0]["event_type"] == "chat"
    chat_id = said.json["events"][0]["id"]
    heard = mud_update(alice, "alice-client-001", after=chat_id - 1)
    assert [event["id"] for event in heard.json["events"]] == [chat_id]
    assert "Anyone hear me?" in heard.json["events"][0]["body"]

    moved = alice.post(
        "/mud/actions",
        data={
            "csrf_token": csrf(alice, "/mud"),
            "command": "/north",
            "request_id": "multiplayer-move-001",
        },
    )
    assert moved.status_code == 200
    assert moved.json["room"]["id"] == "test:hall"
    bob_after_move = mud_update(bob, "bob-client-0001", after=chat_id)
    assert bob_after_move.json["room"]["occupants"] == []
    assert any(event["event_type"] == "departure" for event in bob_after_move.json["events"])

    private_chat = alice.post(
        "/mud/actions",
        data={
            "csrf_token": csrf(alice, "/mud"),
            "message": "This stays in the hall.",
            "request_id": "room-chat-002",
        },
    )
    hall_chat_id = private_chat.json["events"][0]["id"]
    isolated = mud_update(bob, "bob-client-0001", after=hall_chat_id - 1)
    assert all(event["id"] != hall_chat_id for event in isolated.json["events"])


def test_late_room_occupants_cannot_read_earlier_room_chat(initialized, app):
    enable_mud(initialized)
    alice = register_member(app, "alice")
    bob = register_member(app, "bob")
    alice.get("/mud")
    mud_update(alice, "alice-client-001")
    message = alice.post(
        "/mud/actions",
        data={
            "csrf_token": csrf(alice, "/mud"),
            "message": "Before Bob arrived.",
            "request_id": "private-history-001",
        },
    )
    message_id = message.json["events"][0]["id"]

    bob.get("/mud")
    joined = mud_update(bob, "bob-client-0001", after=0)
    assert all(event["id"] != message_id for event in joined.json["events"])


def test_multiple_tabs_deduplicate_presence_and_depart_only_after_last_tab(initialized, app):
    enable_mud(initialized)
    alice = register_member(app, "alice")
    bob = register_member(app, "bob")
    alice.get("/mud")
    bob.get("/mud")
    mud_update(bob, "bob-client-0001")
    first = mud_update(alice, "alice-tab-client-01")
    first_event_max = max((event["id"] for event in first.json["events"]), default=0)
    second = mud_update(alice, "alice-tab-client-02")
    assert not any(event["event_type"] == "arrival" for event in second.json["events"])
    bob_view = mud_update(bob, "bob-client-0001", after=first_event_max)
    assert len([item for item in bob_view.json["room"]["occupants"] if item["username"] == "alice"]) == 1

    token = csrf(alice, "/mud")
    alice.post(
        "/mud/disconnect",
        data={"csrf_token": token, "client_id": "alice-tab-client-01"},
    )
    with app.app_context():
        database = get_db()
        database.execute(
            "UPDATE mud_presence SET disconnected_at='2000-01-01T00:00:00+00:00' "
            "WHERE client_id='alice-tab-client-01'"
        )
        database.commit()
    still_there = mud_update(bob, "bob-client-0001")
    assert [item["username"] for item in still_there.json["room"]["occupants"]] == ["alice"]
    assert not any(event["event_type"] == "departure" for event in still_there.json["events"])

    alice.post(
        "/mud/disconnect",
        data={"csrf_token": token, "client_id": "alice-tab-client-02"},
    )
    with app.app_context():
        database = get_db()
        database.execute(
            "UPDATE mud_presence SET disconnected_at='2000-01-01T00:00:00+00:00' "
            "WHERE client_id='alice-tab-client-02'"
        )
        database.commit()
    gone = mud_update(bob, "bob-client-0001")
    assert gone.json["room"]["occupants"] == []
    assert len([event for event in gone.json["events"] if event["event_type"] == "departure"]) == 1


def test_new_login_transfers_presence_without_duplicate_player_or_event(initialized, app):
    enable_mud(initialized)
    alice = register_member(app, "alice")
    bob = register_member(app, "bob")
    alice.get("/mud")
    bob.get("/mud")
    mud_update(alice, "alice-old-client")
    initial = mud_update(bob, "bob-client-0001")
    after = max((event["id"] for event in initial.json["events"]), default=0)

    reconnected = app.test_client()
    login = reconnected.post(
        "/login",
        data={
            "csrf_token": csrf(reconnected, "/login"),
            "username": "alice",
            "password": "member password",
        },
        follow_redirects=True,
    )
    assert login.status_code == 200
    reconnected.get("/mud")
    assert mud_update(reconnected, "alice-new-client").status_code == 200
    observed = mud_update(bob, "bob-client-0001", after=after)
    assert [item["username"] for item in observed.json["room"]["occupants"]] == ["alice"]
    assert not any(event["event_type"] in {"arrival", "departure"} for event in observed.json["events"])
    assert mud_update(alice, "alice-old-client").status_code == 409


def test_accounts_receive_one_persistent_character_with_mode_ready_tracks(initialized, app):
    enable_mud(initialized)
    member = register_member(app, "character_user")
    with app.app_context():
        database = get_db()
        user = database.execute(
            "SELECT id,callsign FROM users WHERE username='character_user'"
        ).fetchone()
        characters = database.execute(
            "SELECT id,display_name FROM mud_characters WHERE user_id=?", (user["id"],)
        ).fetchall()
        assert [tuple(row) for row in characters] == [
            (f"stationnet:character:{user['id']}", user["callsign"])
        ]
        tracks = database.execute(
            "SELECT track_key,level,experience,current_health,maximum_health,"
            "strength,agility,intellect,vitality FROM mud_character_progression "
            "WHERE character_id=? ORDER BY track_key", (characters[0]["id"],)
        ).fetchall()
        assert [tuple(row) for row in tracks] == [
            ("procedural", 1, 0, 100, 100, 10, 10, 10, 10),
            ("shared", 1, 0, 100, 100, 10, 10, 10, 10),
            ("traditional", 1, 0, 100, 100, 10, 10, 10, 10),
        ]
    page = member.get("/mud")
    assert page.status_code == 200
    assert b"data-character-name" in page.data
    assert b"data-character-health" in page.data
    assert b"data-character-attribute=\"strength\"" in page.data


def test_sysop_can_grant_and_revoke_builder_without_admin_access(initialized, app):
    member = register_member(app, "worldsmith")
    with app.app_context():
        user_id = get_db().execute(
            "SELECT id FROM users WHERE username='worldsmith'"
        ).fetchone()["id"]

    assert member.get("/mud/builder").status_code == 403
    denied = member.post(
        "/mud/builder/rooms",
        data={
            "csrf_token": csrf(member, "/terminal"),
            "name": "Forbidden Room",
            "description": "This should never be saved.",
        },
    )
    assert denied.status_code == 403

    granted = initialized.post(
        f"/mud/builder/permissions/{user_id}",
        data={"csrf_token": csrf(initialized, "/mud/builder"), "enabled": "1"},
        follow_redirects=True,
    )
    assert b"World-builder access granted" in granted.data
    builder_page = member.get("/mud/builder")
    assert builder_page.status_code == 200
    assert b"MUD World Builder" in builder_page.data
    assert b"Builder permissions" not in builder_page.data

    created = member.post(
        "/mud/builder/rooms",
        data={
            "csrf_token": csrf(member, "/mud/builder"),
            "name": "Builder Workshop",
            "description": "A room made without station administration rights.",
        },
        follow_redirects=True,
    )
    assert b"Builder Workshop" in created.data

    initialized.post(
        f"/mud/builder/permissions/{user_id}",
        data={"csrf_token": csrf(initialized, "/mud/builder"), "enabled": "0"},
    )
    assert member.get("/mud/builder").status_code == 403
    with app.app_context():
        database = get_db()
        assert database.execute(
            "SELECT is_admin FROM users WHERE id=?", (user_id,)
        ).fetchone()["is_admin"] == 0
        assert database.execute(
            "SELECT COUNT(*) FROM mud_world_rooms WHERE name='Forbidden Room'"
        ).fetchone()[0] == 0


def test_builder_room_and_exit_changes_are_persistent_and_immediately_playable(initialized, app):
    enable_mud(initialized)
    token = csrf(initialized, "/mud/builder")
    initialized.post(
        "/mud/builder/rooms",
        data={
            "csrf_token": token,
            "name": "Observation Deck",
            "description": "A broad viewport looks over the station.",
        },
    )
    with app.app_context():
        room_id = get_db().execute(
            "SELECT id FROM mud_world_rooms WHERE name='Observation Deck'"
        ).fetchone()["id"]

    initialized.post(
        "/mud/builder/exits",
        data={
            "csrf_token": token,
            "from_room_id": "stationnet:start",
            "to_room_id": room_id,
            "name": "observation hatch",
            "direction": "up",
        },
    )
    page = initialized.get("/mud")
    assert b"Observation Deck" in page.data
    moved = initialized.post(
        "/mud/actions",
        data={"csrf_token": csrf(initialized, "/mud"), "command": "/up", "request_id": "built-move-001"},
    )
    assert moved.status_code == 200
    assert moved.json["room"]["name"] == "Observation Deck"

    initialized.post(
        f"/mud/builder/rooms/{room_id}",
        data={
            "csrf_token": token,
            "name": "Panorama Deck",
            "description": "Freshly polished windows frame a field of stars.",
        },
    )
    refreshed = mud_update(initialized, "builder-live-client")
    assert refreshed.json["room"]["name"] == "Panorama Deck"
    assert "polished windows" in refreshed.json["room"]["description"]

    restarted = create_app({
        "TESTING": True,
        "DATA_DIR": app.config["DATA_DIR"],
        "SECRET_KEY": "new-process",
    })
    with restarted.app_context():
        database = get_db()
        assert database.execute(
            "SELECT name,description FROM mud_world_rooms WHERE id=?", (room_id,)
        ).fetchone()["name"] == "Panorama Deck"
        assert database.execute(
            "SELECT to_room_id FROM mud_world_exits WHERE from_room_id='stationnet:start'"
        ).fetchone()["to_room_id"] == room_id


def test_exit_editor_rejects_broken_and_duplicate_connections(initialized, app):
    token = csrf(initialized, "/mud/builder")
    for name in ("North Gallery", "South Gallery"):
        initialized.post(
            "/mud/builder/rooms",
            data={"csrf_token": token, "name": name, "description": f"The {name.lower()}."},
        )
    with app.app_context():
        ids = {row["name"]: row["id"] for row in get_db().execute(
            "SELECT id,name FROM mud_world_rooms WHERE name LIKE '%Gallery'"
        )}

    first = initialized.post(
        "/mud/builder/exits",
        data={
            "csrf_token": token, "from_room_id": "stationnet:start",
            "to_room_id": ids["North Gallery"], "name": "north arch", "direction": "north",
        },
        follow_redirects=True,
    )
    assert b"Exit north arch created" in first.data
    duplicate = initialized.post(
        "/mud/builder/exits",
        data={
            "csrf_token": token, "from_room_id": "stationnet:start",
            "to_room_id": ids["South Gallery"], "name": "other arch", "direction": "north",
        },
        follow_redirects=True,
    )
    assert b"already has an exit" in duplicate.data
    broken = initialized.post(
        "/mud/builder/exits",
        data={
            "csrf_token": token, "from_room_id": "stationnet:start",
            "to_room_id": "missing-room", "name": "void", "direction": "out",
        },
        follow_redirects=True,
    )
    assert b"Choose existing source and destination rooms" in broken.data
    with app.app_context():
        database = get_db()
        assert database.execute("SELECT COUNT(*) FROM mud_world_exits").fetchone()[0] == 1
        exit_id = database.execute("SELECT id FROM mud_world_exits").fetchone()["id"]
    changed = initialized.post(
        f"/mud/builder/exits/{exit_id}",
        data={
            "csrf_token": token, "from_room_id": ids["North Gallery"],
            "to_room_id": ids["South Gallery"], "name": "south passage", "direction": "south",
        },
        follow_redirects=True,
    )
    assert b"Exit south passage updated" in changed.data
    with app.app_context():
        row = get_db().execute(
            "SELECT from_room_id,to_room_id,name,direction FROM mud_world_exits WHERE id=?",
            (exit_id,),
        ).fetchone()
        assert tuple(row) == (
            ids["North Gallery"], ids["South Gallery"], "south passage", "south"
        )
    removed = initialized.post(
        f"/mud/builder/exits/{exit_id}/delete",
        data={"csrf_token": token, "confirmation": "delete"},
        follow_redirects=True,
    )
    assert b"Exit south passage removed" in removed.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM mud_world_exits").fetchone()[0] == 0


def test_deleting_occupied_room_rehomes_players_and_removes_connected_exits(initialized, app):
    enable_mud(initialized)
    token = csrf(initialized, "/mud/builder")
    initialized.post(
        "/mud/builder/rooms",
        data={"csrf_token": token, "name": "Temporary Annex", "description": "Built to be removed."},
    )
    with app.app_context():
        room_id = get_db().execute(
            "SELECT id FROM mud_world_rooms WHERE name='Temporary Annex'"
        ).fetchone()["id"]
        database = get_db()
        database.executemany(
            "INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) VALUES(?,?,?,?,?)",
            (
                ("builder:annex-in", "stationnet:start", room_id, "annex", "east"),
                ("builder:annex-out", room_id, "stationnet:start", "return", "west"),
            ),
        )
        database.commit()

    explorer = register_member(app, "explorer")
    explorer.get("/mud")
    mud_update(explorer, "explorer-client")
    moved = explorer.post(
        "/mud/actions",
        data={"csrf_token": csrf(explorer, "/mud"), "command": "/east", "request_id": "annex-move-001"},
    )
    assert moved.json["room"]["id"] == room_id

    rejected = initialized.post(
        "/mud/builder/rooms/stationnet:start/delete",
        data={"csrf_token": token, "confirmation": "Starting Room"},
        follow_redirects=True,
    )
    assert b"current starting room cannot be deleted" in rejected.data
    deleted = initialized.post(
        f"/mud/builder/rooms/{room_id}/delete",
        data={"csrf_token": token, "confirmation": "Temporary Annex"},
        follow_redirects=True,
    )
    assert b"connected exits were deleted" in deleted.data

    recovered = mud_update(explorer, "explorer-client")
    assert recovered.status_code == 200
    assert recovered.json["room"]["id"] == "stationnet:start"
    assert any("removed by a world builder" in event["body"] for event in recovered.json["events"])
    with app.app_context():
        database = get_db()
        assert database.execute(
            "SELECT COUNT(*) FROM mud_world_rooms WHERE id=?", (room_id,)
        ).fetchone()[0] == 0
        assert database.execute(
            "SELECT COUNT(*) FROM mud_world_exits WHERE from_room_id=? OR to_room_id=?",
            (room_id, room_id),
        ).fetchone()[0] == 0
        assert database.execute(
            "SELECT room_id FROM mud_world_locations JOIN users ON users.id=user_id "
            "WHERE username='explorer'"
        ).fetchone()["room_id"] == "stationnet:start"


def test_sysop_can_replace_start_only_with_an_existing_room(initialized, app):
    token = csrf(initialized, "/mud/builder")
    initialized.post(
        "/mud/builder/rooms",
        data={"csrf_token": token, "name": "Arrival Hall", "description": "A safer point of entry."},
    )
    with app.app_context():
        room_id = get_db().execute(
            "SELECT id FROM mud_world_rooms WHERE name='Arrival Hall'"
        ).fetchone()["id"]
    invalid = initialized.post(
        "/mud/builder/start",
        data={"csrf_token": token, "room_id": "missing"},
        follow_redirects=True,
    )
    assert b"Choose an existing room" in invalid.data
    initialized.post(
        "/mud/builder/start",
        data={"csrf_token": token, "room_id": room_id},
    )
    with app.app_context():
        starts = get_db().execute(
            "SELECT id FROM mud_world_rooms WHERE is_start=1"
        ).fetchall()
        assert [row["id"] for row in starts] == [room_id]


def test_character_and_stats_commands_share_authoritative_character_action(initialized):
    enable_mud(initialized)
    token = csrf(initialized, "/mud")
    character = initialized.post(
        "/mud/actions",
        data={"csrf_token": token, "command": "/character", "request_id": "character-001"},
    )
    stats = initialized.post(
        "/mud/actions",
        data={"csrf_token": token, "command": "/stats", "request_id": "character-002"},
    )
    assert character.status_code == stats.status_code == 200
    assert character.json["action"] == stats.json["action"] == "character"
    assert character.json["events"][0]["body"] == stats.json["events"][0]["body"]
    assert character.json["character"]["level"] == 1
    assert character.json["character"]["attributes"] == {
        "agility": 10, "intellect": 10, "strength": 10, "vitality": 10,
    }


def test_experience_calculations_awards_and_tracks_are_persistent(initialized, app):
    assert [experience_for_level(level) for level in range(1, 5)] == [0, 100, 400, 900]
    assert [level_for_experience(total) for total in (0, 99, 100, 399, 400, 900)] == [
        1, 1, 2, 2, 3, 4,
    ]
    with app.app_context():
        database = get_db()
        user_id = database.execute("SELECT id FROM users WHERE username='sysop'").fetchone()[0]
        award = award_experience(database, user_id, 450)
        separate = award_experience(database, user_id, 100, "procedural")
        database.commit()
        assert award["levels_gained"] == 2
        assert award["character"]["level"] == 3
        assert award["character"]["experience"] == 450
        assert award["character"]["maximum_health"] == 120
        assert separate["character"]["level"] == 2
        assert character_state(database, user_id, "traditional")["level"] == 1

    restarted = create_app({
        "TESTING": True,
        "DATA_DIR": app.config["DATA_DIR"],
        "SECRET_KEY": "new-process",
    })
    with restarted.app_context():
        database = get_db()
        user_id = database.execute("SELECT id FROM users WHERE username='sysop'").fetchone()[0]
        shared = character_state(database, user_id)
        assert (shared["level"], shared["experience"], shared["maximum_health"]) == (3, 450, 120)
        assert character_state(database, user_id, "procedural")["experience"] == 100
