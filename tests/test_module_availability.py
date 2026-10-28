"""Module switches protect navigation and routes without changing service state."""

from stationnet import create_app
from stationnet.db import get_db
from stationnet.modules import ALL_MODULES


def test_module_switches_hide_navigation_and_block_routes(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign,mud_enabled,"
            "mud_traditional_enabled) VALUES('Test','Kept description','TST-1',1,1)"
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) "
            "VALUES('sysop','SYS-1','unused',1)"
        ).lastrowid
        database.commit()
        initial = {name: bool(database.execute(
            f"SELECT {name}_enabled FROM station WHERE id=1"
        ).fetchone()[0]) for name, _ in ALL_MODULES}

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"

    assert client.get("/sysop/settings").status_code == 200
    for module in ("board", "chat", "mail", "radio", "applications", "mud"):
        initial[module] = False
    response = client.post("/sysop/settings", data={
        "csrf_token": "test-token", "settings_section": "modules",
        **{f"{module}_enabled": "on" for module, active in initial.items() if active},
    })
    assert response.status_code == 302

    settings = client.get("/sysop/settings")
    assert settings.status_code == 200
    assert b"Module availability" in settings.data
    assert b"SYSOP CONTROL" in settings.data
    assert settings.data.index(b'settings-jump-nav') < settings.data.index(b'id="module-availability"')
    assert b'name="station_enabled"' not in settings.data
    assert b'name="moderation_enabled"' not in settings.data
    assert b'href="/board"' not in settings.data
    assert b'href="/mud/w/stationnet/builder"' not in settings.data
    for path in ("/board", "/chat/messages", "/mail/notifications", "/radio/state",
                 "/games-apps", "/sysop/applications", "/mud/w/stationnet", "/mud/w/stationnet/builder"):
        assert client.get(path).status_code == 404, path

    with app.app_context():
        row = get_db().execute(
            "SELECT description,mud_traditional_enabled FROM station WHERE id=1"
        ).fetchone()
        assert tuple(row) == ("Kept description", 1)

    initial.update({name: True for name in ("board", "chat", "mail", "radio", "applications", "mud")})
    client.post("/sysop/settings", data={
        "csrf_token": "test-token", "settings_section": "modules",
        **{f"{module}_enabled": "on" for module, active in initial.items() if active},
    })
    assert client.get("/board").status_code == 200
    assert client.get("/games-apps").status_code == 200
    assert client.get("/mud/w/stationnet/builder").status_code == 200

    # Required Station and Moderation routes remain available when all optional services are off.
    client.post("/sysop/settings", data={
        "csrf_token": "test-token", "settings_section": "modules",
    })
    settings = client.get("/sysop/settings")
    assert settings.status_code == 200
    assert b"SYSOP CONTROL" in settings.data
    assert client.get("/terminal").status_code == 200
    assert client.get("/sysop/moderation").status_code == 200
    for path in ("/board", "/announcements", "/events", "/wiki/",
                 "/videos", "/library", "/arcade/", "/chat/messages",
                 "/mail/notifications", "/radio/state", "/files", "/users/presence",
                 "/office", "/games-apps", "/mud/w/stationnet/builder"):
        assert client.get(path).status_code == 404, path
    assert client.post("/feeds/users/1/posts").status_code == 404
