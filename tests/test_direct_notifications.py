"""Connection-scoped direct notification behavior."""
from datetime import datetime, timedelta, timezone

import pytest

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        database = get_db()
        database.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TST-001')")
        database.executemany(
            "INSERT INTO users(id,username,callsign,password_hash) VALUES(?,?,?,'x')",
            [(1, "alice", "ALC-001"), (2, "bob", "BOB-002"), (3, "carl", "CRL-003")],
        )
        database.commit()
    return app


def signed_in(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=user_id, csrf_token="token")
    client.get("/users/presence")
    return client


def send(client, recipient, body="Hello"):
    return client.post(f"/chat/direct/{recipient}/messages",
                       data={"csrf_token": "token", "body": body})


def notifications(client, claim=False):
    return client.get(f"/chat/direct/notifications?claim={int(claim)}").json


def test_site_wide_unread_claim_and_read(app):
    alice, bob, carl = (signed_in(app, uid) for uid in (1, 2, 3))
    assert send(bob, 1).status_code == 201
    assert send(bob, 1, "Again").status_code == 201
    assert send(carl, 1).status_code == 201
    board = alice.get("/board")
    assert board.status_code == 200
    assert b'data-direct-notifications-url="/chat/direct/notifications"' in board.data
    assert b'data-direct-count' in board.data
    direct_page = alice.get("/chat/direct/2")
    assert b'data-direct-peer="2"' in direct_page.data
    pending = notifications(alice)
    assert {(item["username"], item["count"]) for item in pending["unread"]} == {
        ("bob", 2), ("carl", 1)
    }
    assert pending["alerts"] == []
    claimed = notifications(alice, claim=True)
    assert sum(item["count"] for item in claimed["alerts"]) == 3
    assert notifications(alice, claim=True)["alerts"] == []
    # Reading one channel clears only that sender. Polls by another tab share state.
    assert alice.get("/chat/direct/2/messages?read=1").json["active"] is True
    assert [item["username"] for item in notifications(alice)["unread"]] == ["carl"]
    assert send(bob, 1, "New").status_code == 201
    assert [item["username"] for item in notifications(alice, claim=True)["alerts"]] == ["bob"]


def test_disconnect_and_stale_presence_remove_notifications(app):
    alice, bob = signed_in(app, 1), signed_in(app, 2)
    assert send(bob, 1).status_code == 201
    assert notifications(alice)["unread"]
    assert bob.post("/logout", data={"csrf_token": "token"}).status_code == 302
    assert notifications(alice)["unread"] == []
    assert alice.get("/chat/direct/2").status_code == 302
    assert send(alice, 2).status_code == 409
    bob = signed_in(app, 2)
    assert notifications(alice)["unread"] == []
    assert send(bob, 1).status_code == 201
    with app.app_context():
        database = get_db()
        database.execute("UPDATE users SET last_seen=? WHERE id=2", (
            (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
        ))
        database.commit()
    assert notifications(alice)["unread"] == []
    assert alice.get("/chat/direct/2").status_code == 302


def test_notifications_require_authentication(app):
    assert app.test_client().get("/chat/direct/notifications").status_code == 302


def test_mail_unread_count_tracks_viewed_messages(app):
    alice, bob = signed_in(app, 1), signed_in(app, 2)
    sent = bob.post("/mail/compose", data={"csrf_token": "token", "recipient_id": 1,
                                           "subject": "Hello", "body": "Mail body"})
    assert sent.status_code == 302
    notification = alice.get("/mail/notifications?after=0").json
    assert notification["new_count"] == notification["unread_count"] == 1
    assert b'data-mail-count' in alice.get("/board").data
    assert alice.get(f'/mail/{notification["latest_id"]}').status_code == 200
    assert alice.get("/mail/notifications?after=0").json["unread_count"] == 0
