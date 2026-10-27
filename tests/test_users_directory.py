"""Directory ordering, pagination, and shared profile activity display."""
import re
import threading
from datetime import datetime, timezone

import pytest
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


def _setup(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES('Test','Test','TST-1')")
        owner = db.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin,created_at) "
            "VALUES('owner','OWN-1','unused',1,'2020-01-01 00:00:00')"
        ).lastrowid
        for number in range(23):
            db.execute(
                "INSERT INTO users(username,callsign,password_hash,created_at,last_seen) "
                "VALUES(?,?,?,?,?)",
                (f"user{number:02d}", f"U-{number:02d}", "unused",
                 f"2024-01-{number + 1:02d} 00:00:00",
                 f"2024-02-{23 - number:02d}T00:00:00+00:00"),
            )
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = owner
    return app, client


def _names(response):
    assert response.status_code == 200
    return re.findall(r'<h2><a href="/users/\d+">([^<]+)</a></h2>', response.get_data(as_text=True))


def test_directory_sorts_before_paginating_and_keeps_sort_in_links(tmp_path):
    _, client = _setup(tmp_path)
    expected = {
        "active": ["owner"] + [f"user{n:02d}" for n in range(23)],
        "newest": [f"user{n:02d}" for n in reversed(range(23))] + ["owner"],
        "oldest": ["owner"] + [f"user{n:02d}" for n in range(23)],
        "alphabetical": ["owner"] + [f"user{n:02d}" for n in range(23)],
    }
    for sort, names in expected.items():
        pages = [client.get(f"/users?sort={sort}&page={page}") for page in (1, 2, 3)]
        assert [_names(page) for page in pages] == [names[:10], names[10:20], names[20:]]
        assert f"/users?sort={sort}&amp;page=2" in pages[0].get_data(as_text=True)
        assert 'aria-current="page"' in pages[1].get_data(as_text=True)
        assert "SHOWING 11–20 OF 24 USERS" in pages[1].get_data(as_text=True)

    assert _names(client.get("/users")) == expected["active"][:10]
    assert _names(client.get("/users?sort=invalid")) == expected["active"][:10]
    assert _names(client.get("/users?sort=newest&page=999")) == expected["newest"][20:]
    assert _names(client.get("/users?sort=newest&page=bad")) == expected["newest"][:10]


def test_directory_ties_are_stable_and_profiles_share_last_active(tmp_path):
    app, client = _setup(tmp_path)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE users SET last_seen='2024-03-01T00:00:00+00:00' "
                   "WHERE username IN ('user00','user01')")
        db.execute("UPDATE users SET last_seen=NULL WHERE username='user22'")
        db.commit()
        user00 = db.execute("SELECT id FROM users WHERE username='user00'").fetchone()[0]
        user22 = db.execute("SELECT id FROM users WHERE username='user22'").fetchone()[0]
    first = _names(client.get("/users"))
    assert first[:3] == ["owner", "user01", "user00"]
    assert _names(client.get("/users?page=3"))[-1] == "user22"
    assert 'LAST ACTIVE: NEVER' in client.get("/users?page=3").get_data(as_text=True)
    assert '<dt>LAST ACTIVE</dt><dd>NEVER</dd>' in client.get(f"/users/{user22}").get_data(as_text=True)
    assert 'datetime="2024-03-01T00:00:00+00:00" data-local-time' in client.get(
        f"/users/{user00}").get_data(as_text=True)

    recent = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with app.app_context():
        db = get_db()
        db.execute("UPDATE users SET last_seen=?,connection_id='active' WHERE id=?", (recent, user00))
        db.commit()
    assert '<dt>LAST ACTIVE</dt><dd>NOW</dd>' in client.get(f"/users/{user00}").get_data(as_text=True)


def test_directory_controls_fit_desktop_and_mobile(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    app, client = _setup(tmp_path)
    with client.session_transaction() as session:
        owner = session["user_id"]
    cookie = app.session_interface.get_signing_serializer(app).dumps({"user_id": owner})
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as driver:
            try:
                browser = driver.chromium.launch(headless=True)
            except Exception as error:
                pytest.skip(f"Chromium is unavailable: {error}")
            try:
                for width in (390, 1280):
                    context = browser.new_context(viewport={"width": width, "height": 800})
                    context.add_cookies([{"name": "session", "value": cookie,
                                          "url": f"http://127.0.0.1:{server.server_port}/"}])
                    page = context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/users?sort=newest&page=2")
                    assert page.locator(".user-card").count() == 10
                    for selector in (".directory-toolbar", ".directory-sort select",
                                     ".directory-sort button", ".directory-pagination"):
                        bounds = page.locator(selector).bounding_box()
                        assert bounds["x"] >= -1
                        assert bounds["x"] + bounds["width"] <= width + 1
                    context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
