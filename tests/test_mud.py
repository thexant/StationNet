from stationnet import create_app
from stationnet.db import get_db

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
