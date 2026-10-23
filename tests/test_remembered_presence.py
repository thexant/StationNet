from datetime import datetime, timedelta, timezone

import pytest

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path),
        "DATABASE": str(tmp_path / "station.db"),
        "SECRET_KEY": "test-secret",
    })
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(id,name,description,callsign) VALUES(1,?,?,?)",
            ("Test Station", "", "AAA-BBB"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("member", "CCC-DDD", "unused"),
        ).lastrowid
        database.commit()
        app.config["USER_ID"] = user_id
    return app


def test_remembered_session_reconnects_after_presence_expires(app):
    old_connection_id = "expired-connection"
    stale_seen = (
        datetime.now(timezone.utc) - timedelta(minutes=2)
    ).replace(microsecond=0).isoformat()
    with app.app_context():
        database = get_db()
        database.execute(
            "UPDATE users SET connection_id=?,last_seen=? WHERE id=?",
            (old_connection_id, stale_seen, app.config["USER_ID"]),
        )
        database.commit()

    client = app.test_client()
    with client.session_transaction() as session:
        session.permanent = True
        session["user_id"] = app.config["USER_ID"]
        session["connection_id"] = old_connection_id

    response = client.get("/users")

    assert response.status_code == 200
    assert b"ONLINE" in response.data
    with client.session_transaction() as session:
        assert session["connection_id"] != old_connection_id
    with app.app_context():
        user = get_db().execute(
            "SELECT connection_id,last_seen FROM users WHERE id=?",
            (app.config["USER_ID"],),
        ).fetchone()
    assert user["connection_id"] != old_connection_id
    assert user["last_seen"] > stale_seen
