from stationnet import create_app
from stationnet.db import get_db

from .conftest import csrf


def enable_mud(client):
    return client.post(
        "/sysop/settings/mud",
        data={"csrf_token": csrf(client, "/sysop/settings"), "mud_enabled": "on"},
        follow_redirects=True,
    )


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
