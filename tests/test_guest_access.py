"""Guest access applies to service routes and the shared navigation."""

from urllib.parse import parse_qs, urlsplit

import pytest

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture
def station(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(id,name,description,callsign,guest_access,kiwix_enabled,videos_enabled) "
            "VALUES(1,'Test','','TST-001',0,1,1)"
        )
        database.execute(
            "INSERT INTO users(id,username,callsign,password_hash) "
            "VALUES(1,'member','USR-001','test')"
        )
        database.commit()
    return app


@pytest.mark.parametrize("path", [
    "/terminal", "/board", "/board/1", "/announcements",
    "/wiki/", "/wiki/search?q=test", "/wiki/content/content/example",
    "/videos", "/videos/1", "/videos/1/media",
])
def test_disabled_guest_access_redirects_service_routes_to_login(station, path):
    response = station.test_client().get(path)
    assert response.status_code == 302
    destination = urlsplit(response.location)
    assert destination.path == "/login"
    assert parse_qs(destination.query)["next"] == [urlsplit(path).path]


def test_disabled_guest_access_hides_service_navigation(station):
    response = station.test_client().get("/")
    assert response.status_code == 200
    for path in ("/terminal", "/board", "/announcements", "/wiki/", "/videos"):
        assert f'href="{path}"'.encode() not in response.data
    assert b'href="/login"' in response.data


def test_members_and_enabled_guests_can_use_services(station):
    guest = station.test_client()
    with station.app_context():
        database = get_db()
        database.execute("UPDATE station SET guest_access=1 WHERE id=1")
        database.commit()
    for path in ("/terminal", "/board", "/announcements", "/videos"):
        assert guest.get(path).status_code == 200
    assert guest.get("/wiki/").status_code in (200, 503)

    with station.app_context():
        database = get_db()
        database.execute("UPDATE station SET guest_access=0 WHERE id=1")
        database.commit()
    member = station.test_client()
    with member.session_transaction() as session:
        session["user_id"] = 1
    for path in ("/terminal", "/board", "/announcements", "/videos"):
        assert member.get(path).status_code == 200
    assert member.get("/wiki/").status_code in (200, 503)
