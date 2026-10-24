"""Event module gates, permissions, timezone validation, and RSVP lifecycle."""
import pytest

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TST-001')")
        db.executemany("INSERT INTO users(id,username,callsign,password_hash,is_admin) VALUES(?,?,?,?,?)", [
            (1, "sysop", "SYS-001", "x", 1), (2, "member", "USR-002", "x", 0),
            (3, "other", "USR-003", "x", 0),
        ])
        db.commit()
    return app


def client(app, user_id=None):
    result = app.test_client()
    if user_id:
        with result.session_transaction() as session:
            session["user_id"] = user_id
    result.get("/")
    return result


def post(client, path, data):
    with client.session_transaction() as session:
        token = session["csrf_token"]
    return client.post(path, data={**data, "csrf_token": token})


def event_data(**changes):
    return {**dict(name="Game Night", description="Board games", start_date="2030-06-10",
                   start_time="19:00", end_date="2030-06-10", end_time="21:00",
                   timezone="America/Edmonton", location="Lounge", url="https://example.com"), **changes}


def test_disabled_and_creation_permissions(app):
    guest, member, sysop = client(app), client(app, 2), client(app, 1)
    assert guest.get("/events").status_code == 404
    assert b'href="/events/"' not in guest.get("/").data
    assert post(sysop, "/sysop/settings", {"settings_section": "events", "events_enabled": "on",
                                            "events_create_permission": "sysops"}).status_code == 302
    assert guest.get("/events").status_code == 200
    assert b'href="/events/"' in guest.get("/").data
    assert member.get("/events/new").status_code == 403
    assert post(member, "/events/new", event_data()).status_code == 403
    assert post(sysop, "/events/new", event_data()).status_code == 302
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO radio_permission_groups(id,name) VALUES(90,'EVENT HOSTS')")
        db.execute("INSERT INTO radio_group_memberships(user_id,group_id) VALUES(2,90)")
        db.commit()
    post(sysop, "/sysop/settings", {"settings_section": "events", "events_enabled": "on",
                                    "events_create_permission": "groups", "event_group_id": "90"})
    assert member.get("/events/new").status_code == 200
    assert client(app, 3).get("/events/new").status_code == 403
    post(sysop, "/sysop/settings", {"settings_section": "events", "events_create_permission": "groups",
                                    "event_group_id": "90"})
    assert guest.get("/events").status_code == 404
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_terminal_directory_orders_enabled_events(app):
    visitor = client(app)
    directory = visitor.get("/terminal").data
    assert b"UPCOMING STATION EVENTS AND RSVPS" not in directory
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET events_enabled=1,kiwix_enabled=1 WHERE id=1")
        db.commit()
    directory = visitor.get("/terminal").data
    assert directory.index(b"OFFICIAL STATION NOTICES") < directory.index(b"UPCOMING STATION EVENTS AND RSVPS")
    assert directory.index(b"UPCOMING STATION EVENTS AND RSVPS") < directory.index(b"READ THE STATION&#39;S OFFLINE KNOWLEDGE LIBRARY")


def test_rsvp_lifecycle_and_ownership(app):
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET events_enabled=1,events_create_permission='members'")
        db.commit()
    creator, attendee, sysop = client(app, 2), client(app, 3), client(app, 1)
    assert post(creator, "/events/new", event_data()).status_code == 302
    assert attendee.get("/events/1/edit").status_code == 403
    assert post(attendee, "/events/1/cancel", {}).status_code == 403
    assert creator.get("/events/1/edit").status_code == 200
    assert post(creator, "/events/1/edit", event_data(name="Updated Game Night")).status_code == 302
    assert b"Updated Game Night" in creator.get("/events/1").data
    assert post(attendee, "/events/1/rsvp", {"response": "going"}).status_code == 302
    assert post(attendee, "/events/1/rsvp", {"response": "interested"}).status_code == 302
    with app.app_context():
        row = get_db().execute("SELECT response FROM event_rsvps WHERE event_id=1 AND user_id=3").fetchone()
        assert row["response"] == "interested"
        assert get_db().execute("SELECT COUNT(*) FROM event_rsvps").fetchone()[0] == 1
    assert b"1 Interested" in attendee.get("/events/1").data
    assert post(attendee, "/events/1/rsvp", {"response": "withdraw"}).status_code == 302
    assert post(creator, "/events/1/cancel", {}).status_code == 302
    assert post(attendee, "/events/1/rsvp", {"response": "going"}).status_code == 409
    assert post(creator, "/events/1/delete", {}).status_code == 403
    assert post(sysop, "/events/1/delete", {}).status_code == 302


def test_invalid_dates_and_urls(app):
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET events_enabled=1")
        db.commit()
    sysop = client(app, 1)
    for bad in (dict(start_date="not-a-date"), dict(end_time=""),
                dict(end_date="2030-06-09"), dict(timezone="Invalid/Zone"),
                dict(url="javascript:alert(1)"),
                dict(start_date="2024-03-10", start_time="02:30", timezone="America/Edmonton")):
        assert post(sysop, "/events/new", event_data(**bad)).status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_deleted_organizer_keeps_event(app):
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET events_enabled=1,events_create_permission='members'")
        db.commit()
    creator = client(app, 2)
    assert post(creator, "/events/new", event_data()).status_code == 302
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM users WHERE id=2")
        db.commit()
    assert b"Deleted member" in client(app).get("/events/1").data


def test_sysop_moderation_panel_on_event_views(app):
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET events_enabled=1,events_create_permission='members'")
        db.commit()
    creator, sysop = client(app, 2), client(app, 1)
    assert post(creator, "/events/new", event_data()).status_code == 302
    for path in ("/events/", "/events/1"):
        assert b"Event moderation tools for Game Night" in sysop.get(path).data
        assert b"SYSOP REMOVE EVENT" in sysop.get(path).data
        assert b"Event moderation tools" not in creator.get(path).data
    assert b"CANCEL EVENT" in creator.get("/events/1").data
    assert post(sysop, "/events/1/delete", {}).status_code == 302
    assert sysop.get("/events/1").status_code == 404
