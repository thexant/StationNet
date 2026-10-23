"""Moderation authorization, expiry, reporting, and chat propagation."""
from datetime import datetime, timedelta, timezone

import pytest

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TST-001')")
        db.executemany("INSERT INTO users(id,username,callsign,password_hash,is_admin) VALUES(?,?,?,?,?)", [
            (1, "sysop", "SYS-001", "x", 1), (2, "member", "MEM-002", "x", 0),
            (3, "other", "OTH-003", "x", 0), (4, "secondop", "SYS-004", "x", 1),
        ])
        db.commit()
    return app


def client_as(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "token"
    return client


def action(client, user_id, name, duration="24"):
    return client.post(f"/sysop/moderation/users/{user_id}/action", data={
        "csrf_token": "token", "action": name, "duration": duration, "reason": "Repeated harassment",
    })


def test_restrictions_apply_to_public_direct_and_other_routes_then_expire(app):
    sysop, member = client_as(app, 1), client_as(app, 2)
    assert action(member, 3, "ban").status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM moderation_restrictions").fetchone()[0] == 0
    assert action(sysop, 2, "mute").status_code == 302
    payload = {"csrf_token": "token", "body": "Hello"}
    assert member.post("/chat/messages", data=payload).status_code == 403
    assert member.post("/chat/direct/3/messages", data=payload).status_code == 403
    assert member.get("/chat/messages").json["muted"] is True
    with app.app_context():
        get_db().execute("UPDATE moderation_restrictions SET expires_at=? WHERE user_id=2",
                         ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
        get_db().commit()
    assert member.post("/chat/messages", data=payload).status_code == 201
    assert member.get("/chat/messages").json["muted"] is False
    assert action(sysop, 2, "suspend").status_code == 302
    assert member.post("/chat/messages", data=payload, headers={"Accept": "application/json"}).status_code == 403
    assert member.get("/board").status_code == 403
    assert action(sysop, 2, "reinstate").status_code == 302
    assert member.get("/board").status_code == 200


def test_admin_protection_warning_report_and_deletion_feed(app):
    sysop, member, other = client_as(app, 1), client_as(app, 2), client_as(app, 3)
    assert action(sysop, 1, "ban", "indefinite").status_code == 403
    assert action(sysop, 4, "ban", "indefinite").status_code == 403
    assert action(sysop, 2, "warn").status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM mail WHERE recipient_id=2 AND subject='Formal SYSOP warning'").fetchone()[0] == 1
    sent = member.post("/chat/messages", data={"csrf_token": "token", "body": "Message"})
    message_id = sent.json["id"]
    report = other.post("/reports", data={"csrf_token": "token", "target_type": "chat",
                                           "target_id": message_id, "reason": "This is inappropriate"})
    assert report.status_code == 302
    assert other.post("/reports", data={"csrf_token": "token", "target_type": "chat",
                                               "target_id": message_id, "reason": "This is inappropriate"}).status_code == 409
    assert member.post(f"/sysop/moderation/chat/{message_id}/delete", data={"csrf_token": "token", "reason": "bad"}).status_code == 302
    assert sysop.post(f"/sysop/moderation/chat/{message_id}/delete", data={"csrf_token": "token", "reason": "bad"}).status_code == 200
    feed = other.get(f"/chat/messages?after={message_id}").json
    assert message_id in feed["deleted_ids"]
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM moderation_audit WHERE action='delete_chat'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM moderation_reports WHERE status='open'").fetchone()[0] == 1
    assert sysop.post("/sysop/moderation/reports/1/resolve", data={"csrf_token": "token", "status": "resolved", "note": "Message removed"}).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT status FROM moderation_reports WHERE id=1").fetchone()[0] == "resolved"
