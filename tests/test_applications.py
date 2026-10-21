import json

from stationnet.applications import APPS, GAMES, catalog, find
from stationnet.db import get_db


def test_catalog_is_centralized_and_contains_both_categories():
    applications = catalog()

    assert applications
    assert {application.category for application in applications} == {GAMES, APPS}
    assert all(
        all(
            (
                application.slug,
                application.name,
                application.description,
                application.author,
                application.version,
            )
        )
        for application in applications
    )
    assert all(find(application.slug) is application for application in applications)


def test_directory_requires_login_and_is_linked_from_navigation(initialized):
    initialized.post("/logout", data={"csrf_token": _csrf(initialized)})
    response = initialized.get("/games-apps")
    assert response.status_code == 302
    assert "/login?next=/games-apps" in response.headers["Location"]

    response = initialized.post(
        "/login",
        data={
            "csrf_token": _csrf(initialized, "/login"),
            "username": "sysop",
            "password": "correct horse battery staple",
        },
        follow_redirects=True,
    )
    assert b'href="/games-apps"' in response.data
    assert b"GAMES AND APPS" in response.data


def test_directory_lists_metadata_and_display_uses_station_shell(initialized):
    response = initialized.get("/games-apps")
    assert response.status_code == 200
    assert b"Word Chain [Placeholder]" in response.data
    assert b"Event Board [Placeholder]" in response.data
    assert b"StationNet Core" in response.data
    assert b"0.0.1" in response.data

    display = initialized.get("/games-apps/word-chain")
    assert display.status_code == 200
    assert b"station-shell" in display.data
    assert b"STATION APPLICATION DISPLAY" in display.data
    assert b"FUNCTIONALITY HAS NOT BEEN IMPLEMENTED YET" in display.data
    assert b"RETURN TO GAMES AND APPS" in display.data
    assert b'aria-current="page"' in display.data


def test_unknown_application_returns_not_found(initialized):
    assert initialized.get("/games-apps/not-registered").status_code == 404


def test_number_guess_uses_isolated_runtime_and_public_api(initialized):
    directory = initialized.get("/games-apps")
    assert b"Number Guess" in directory.data
    assert b"Signal Runner" not in directory.data

    display = initialized.get("/games-apps/number-guess")
    assert display.status_code == 200
    assert b'sandbox="allow-scripts"' in display.data
    assert b"application_host.js" in display.data

    runtime = initialized.get("/games-apps/number-guess/runtime")
    assert runtime.status_code == 200
    assert b"stationnet-application-source" in runtime.data
    assert "connect-src 'none'" in runtime.headers["Content-Security-Policy"]
    assert "worker-src blob:" in runtime.headers["Content-Security-Policy"]
    assert runtime.headers["X-Frame-Options"] == "SAMEORIGIN"

    source = initialized.get("/games-apps/number-guess/source.js")
    assert source.status_code == 200
    assert source.mimetype == "application/javascript"
    assert b"ui.numberInput" in source.data


def test_placeholder_has_no_runtime(initialized):
    assert initialized.get("/games-apps/word-chain/runtime").status_code == 404
    assert initialized.get("/games-apps/word-chain/source.js").status_code == 404


def test_application_storage_persists_and_supports_json(app, initialized):
    headers = {"X-CSRF-Token": _csrf(initialized), "Content-Type": "application/json"}
    endpoint = "/games-apps/number-guess/storage"
    value = {"best": 4, "history": [8, 4], "sound": True}

    saved = initialized.post(endpoint, data=json.dumps({
        "operation": "set", "key": "profile", "value": value,
    }), headers=headers)
    assert saved.status_code == 200
    assert saved.get_json() == {"ok": True, "value": True}

    # A later request represents a reopened runtime; storage is SQLite-backed.
    loaded = initialized.post(endpoint, data=json.dumps({
        "operation": "get", "key": "profile",
    }), headers=headers)
    assert loaded.get_json() == {"ok": True, "found": True, "value": value}
    keys = initialized.post(endpoint, data=json.dumps({"operation": "keys"}), headers=headers)
    assert keys.get_json()["value"] == ["profile"]
    deleted = initialized.post(endpoint, data=json.dumps({
        "operation": "delete", "key": "profile",
    }), headers=headers)
    assert deleted.get_json()["value"] is True
    missing = initialized.post(endpoint, data=json.dumps({
        "operation": "get", "key": "profile",
    }), headers=headers)
    assert missing.get_json() == {"ok": True, "found": False, "value": None}


def test_application_storage_isolates_users_apps_and_exposes_shared_scope(app, initialized):
    endpoint = "/games-apps/number-guess/storage"
    token = _csrf(initialized)
    headers = {"X-CSRF-Token": token, "Content-Type": "application/json"}
    initialized.post(endpoint, json={"operation": "set", "key": "private", "value": "sysop"}, headers=headers)
    initialized.post(endpoint, json={
        "operation": "set", "key": "scoreboard", "value": ["SYSOP"], "scope": "shared",
    }, headers=headers)
    with app.app_context():
        database = get_db()
        second_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('second','TST-002','unused')"
        ).lastrowid
        database.execute(
            "INSERT INTO application_storage(application_slug,storage_scope,owner_key,storage_key,value_json,size_bytes) "
            "VALUES('another-app','user',?,'private','\"other app\"',11)", (f"user:{second_id}",),
        )
        database.commit()
    with initialized.session_transaction() as browser_session:
        browser_session["user_id"] = second_id
        browser_session.pop("connection_id", None)

    private = initialized.post(endpoint, json={"operation": "get", "key": "private"}, headers=headers)
    assert private.get_json() == {"ok": True, "found": False, "value": None}
    shared = initialized.post(endpoint, json={
        "operation": "get", "key": "scoreboard", "scope": "shared",
    }, headers=headers)
    assert shared.get_json()["value"] == ["SYSOP"]
    keys = initialized.post(endpoint, json={"operation": "keys"}, headers=headers)
    assert "private" not in keys.get_json()["value"]


def test_application_storage_enforces_tokens_keys_and_limits(initialized):
    endpoint = "/games-apps/number-guess/storage"
    assert initialized.post(endpoint, json={"operation": "keys"}).status_code == 400
    headers = {"X-CSRF-Token": _csrf(initialized)}
    invalid = initialized.post(endpoint, json={
        "operation": "set", "key": "../escape", "value": 1,
    }, headers=headers)
    assert invalid.status_code == 400
    oversized = initialized.post(endpoint, json={
        "operation": "set", "key": "large", "value": "x" * (64 * 1024),
    }, headers=headers)
    assert oversized.status_code == 413
    assert initialized.get("/terminal").status_code == 200


def test_expanded_runtime_and_demo_exercise_updates_timers_and_storage(initialized):
    source = initialized.get("/games-apps/number-guess/source.js")
    assert b'ui.storage.get("best-score")' in source.data
    assert b'ui.update("message"' in source.data
    assert b"ui.interval" in source.data

    runtime = initialized.get("/static/application_runtime.js")
    assert b"textarea" in runtime.data
    assert b"storage-result" in runtime.data
    host = initialized.get("/static/application_host.js")
    assert b"PROGRAM STATUS / ERROR" in host.data
    assert b"data-app-component" in host.data


def _csrf(client, path="/terminal"):
    client.get(path)
    with client.session_transaction() as session:
        return session["csrf_token"]
