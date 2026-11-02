"""Timed removal of bulletins and announcements, including after downtime."""
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from flask import Flask

from stationnet import create_app
from stationnet import db as database_module
from stationnet.auto_removal import purge_expired
from stationnet.db import get_db

CSRF = "removal-test-token"
ZONE = "America/Edmonton"


@pytest.fixture
def station(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,guest_access) VALUES('Test','Test','TST-1',1)")
        admin = db.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('owner','OWN-1','unused',1)"
        ).lastrowid
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = admin
        session["csrf_token"] = CSRF
    return app, admin, client


def local(hours, zone=ZONE):
    return (datetime.now(ZoneInfo(zone)) + timedelta(hours=hours)).strftime("%Y-%m-%d|%H:%M").split("|")


def removal(hours=2, zone=ZONE):
    date, clock = local(hours, zone)
    return {"expires_date": date, "expires_time": clock, "expires_timezone": zone}


def bulletin(client, **extra):
    return client.post("/board/new", data={"csrf_token": CSRF, "title": "Sale", "body": "Text", **extra})


def count(app, table):
    with app.app_context():
        return get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def stored(app, table="posts"):
    with app.app_context():
        return get_db().execute(f"SELECT expires_at FROM {table}").fetchone()[0]


def test_local_time_is_stored_as_utc(station):
    app, _, client = station
    assert bulletin(client, **removal()).status_code == 302
    expected = datetime.now(timezone.utc) + timedelta(hours=2)
    moment = datetime.fromisoformat(stored(app).replace("Z", "+00:00"))
    assert abs(moment - expected) < timedelta(minutes=2)
    assert stored(app).endswith(":00Z")


def test_no_removal_by_default(station):
    app, _, client = station
    bulletin(client)
    assert stored(app) is None


@pytest.mark.parametrize("fields", [
    removal(-2),
    {"expires_date": "2099-01-01"},
    {"expires_time": "12:00"},
    {"expires_date": "2099-01-01", "expires_time": "12:00", "expires_timezone": "Not/AZone"},
    {"expires_date": "2099-01-01", "expires_time": "12:00", "expires_timezone": "America"},
    {"expires_date": "2099-03-08", "expires_time": "02:30", "expires_timezone": "America/New_York"},  # DST gap
])
def test_invalid_times_create_nothing(station, fields):
    app, _, client = station
    assert bulletin(client, **fields).status_code == 200
    assert count(app, "posts") == 0


def test_due_thread_goes_with_replies_and_poll(station):
    app, admin, client = station
    bulletin(client, poll_enabled="1", poll_question="Q?", poll_option=["A", "B"], **removal())
    with app.app_context():
        db = get_db()
        post = db.execute("SELECT id FROM posts").fetchone()[0]
        db.execute("INSERT INTO posts(author_id,parent_id,body) VALUES(?,?,'reply')", (admin, post))
        db.execute("UPDATE posts SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (post,))
        db.commit()
    assert client.get(f"/board/{post}").status_code == 404
    assert count(app, "posts") == 0 and count(app, "polls") == 0


def test_future_item_survives_purge(station):
    app, _, client = station
    bulletin(client, **removal())
    with app.app_context():
        purge_expired(get_db())
    assert count(app, "posts") == 1


def test_announcement_edit_shows_changes_and_clears_time(station):
    app, _, client = station
    fields = removal()
    client.post("/announcements/new", data={"csrf_token": CSRF, "title": "T", "body": "B", **fields})
    assert "AUTO-REMOVES" in client.get("/announcements").get_data(as_text=True)
    page = client.get("/announcements/1/edit").get_data(as_text=True)
    assert f'value="{fields["expires_date"]}"' in page and f'value="{ZONE}"' in page
    assert 'name="poll_enabled"' not in page
    later = removal(5, "Asia/Tokyo")
    client.post("/announcements/1/edit", data={"csrf_token": CSRF, "title": "T", "body": "B", **later})
    assert abs(datetime.fromisoformat(stored(app, "announcements").replace("Z", "+00:00"))
               - (datetime.now(timezone.utc) + timedelta(hours=5))) < timedelta(minutes=2)
    client.post("/announcements/1/edit", data={"csrf_token": CSRF, "title": "T", "body": "B"})
    assert stored(app, "announcements") is None


def test_expired_while_offline_is_gone_after_restart_without_a_request(station, tmp_path):
    app, admin, client = station
    bulletin(client, **removal())
    client.post("/announcements/new", data={"csrf_token": CSRF, "title": "T", "body": "B", **removal()})
    with app.app_context():
        db = get_db()
        db.execute("UPDATE posts SET expires_at='2000-01-01T00:00:00Z'")
        db.execute("UPDATE announcements SET expires_at='2000-01-01T00:00:00Z'")
        db.commit()
    restarted = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (count(restarted, "posts") or count(restarted, "announcements")):
        time.sleep(.1)
    assert count(restarted, "posts") == 0 and count(restarted, "announcements") == 0


def test_old_database_is_migrated(tmp_path):
    connection = sqlite3.connect(tmp_path / "station.db")
    connection.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY);
        CREATE TABLE posts(id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL,
          parent_id INTEGER, title TEXT, body TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE announcements(id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL,
          title TEXT NOT NULL, body TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)
    connection.close()
    flask_app = Flask(__name__)
    flask_app.config["DATABASE"] = str(tmp_path / "station.db")
    with flask_app.app_context():
        db = database_module.get_db()
        database_module._migrate_post_options(db)
        database_module._migrate_post_options(db)
        assert "expires_at" in {row["name"] for row in db.execute("PRAGMA table_info(posts)")}
        assert {"expires_at", "expires_timezone"} <= {row["name"] for row in db.execute("PRAGMA table_info(announcements)")}
