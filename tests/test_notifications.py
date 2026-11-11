"""Notification centre: sources, coalescing, mutes, the combined poll, and legacy migration."""
from datetime import datetime, timedelta, timezone

import pytest

from stationnet import create_app, notifications
from stationnet.db import get_db, init_db
from stationnet.modules import ALL_MODULES

TOKEN = "alerts-test"


@pytest.fixture
def setup(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    columns = ",".join(f"{name}_enabled" for name, _label in ALL_MODULES)
    with app.app_context():
        db = get_db()
        db.execute(f"INSERT INTO station(name,description,callsign,{columns}) "
                   f"VALUES('Test Station','About us','TST-1',{','.join('1' for _ in ALL_MODULES)})")
        ids = {}
        for name, admin in (("admin", 1), ("alice", 0), ("bob", 0), ("carol", 0)):
            ids[name] = db.execute(
                "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,?)",
                (name, f"{name[:3].upper()}-1", "unused", admin),
            ).lastrowid
        db.commit()
    return app, ids


def client_for(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = TOKEN
    return client


def alerts(app, user_id, kind=None):
    with app.app_context():
        query = "SELECT * FROM notifications WHERE user_id=?"
        params = [user_id]
        if kind:
            query += " AND kind=?"
            params.append(kind)
        return get_db().execute(query + " ORDER BY id", params).fetchall()


def new_thread(app, client, title="Hello world", body="First post"):
    response = client.post("/board/new", data={"csrf_token": TOKEN, "title": title, "body": body})
    assert response.status_code == 302
    return int(response.headers["Location"].rsplit("/", 1)[1])


def test_board_reply_notifies_author_and_participants_not_replier(setup):
    app, ids = setup
    alice, bob, carol = (client_for(app, ids[name]) for name in ("alice", "bob", "carol"))
    thread = new_thread(app, alice)
    bob.post(f"/board/{thread}", data={"csrf_token": TOKEN, "body": "Reply from bob"})
    assert [row["kind"] for row in alerts(app, ids["alice"])] == ["reply"]
    assert alerts(app, ids["bob"]) == []

    carol.post(f"/board/{thread}", data={"csrf_token": TOKEN, "body": "Carol joins"})
    # Alice's thread alert coalesces into one unread line; Bob hears as a participant.
    alice_rows = alerts(app, ids["alice"])
    assert len(alice_rows) == 1 and "carol" in alice_rows[0]["text"]
    assert [row["kind"] for row in alerts(app, ids["bob"])] == ["reply"]
    assert alerts(app, ids["carol"]) == []


def test_mentions_notify_named_users_but_not_self(setup):
    app, ids = setup
    alice = client_for(app, ids["alice"])
    new_thread(app, alice, body="Ping @bob and @Carol and @alice and @nobody")
    assert [row["kind"] for row in alerts(app, ids["bob"])] == ["mention"]
    assert [row["kind"] for row in alerts(app, ids["carol"])] == ["mention"]
    assert alerts(app, ids["alice"]) == []


def test_mentioned_users_ignores_emails_and_caps_count(setup):
    app, _ids = setup
    with app.app_context():
        database = get_db()
        assert notifications.mentioned_users(database, "mail bob@alice.example") == []
        many = " ".join(f"@user{i:02d}" for i in range(20))
        for i in range(20):
            database.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
                             (f"user{i:02d}", f"U{i:02d}-9", "x"))
        assert len(notifications.mentioned_users(database, many)) == notifications.MAX_MENTIONS


def test_feed_comment_goes_to_alerts_not_mail(setup):
    app, ids = setup
    alice, bob = client_for(app, ids["alice"]), client_for(app, ids["bob"])
    alice.post(f"/feeds/users/{ids['alice']}/posts", data={"csrf_token": TOKEN, "body": "Status"})
    with app.app_context():
        post_id = get_db().execute("SELECT id FROM user_feed_posts").fetchone()[0]
    bob.post(f"/feeds/posts/{post_id}/comments", data={"csrf_token": TOKEN, "body": "Nice"})
    assert [row["kind"] for row in alerts(app, ids["alice"])] == ["comment"]
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM mail").fetchone()[0] == 0


def test_reactions_coalesce_into_one_alert(setup):
    app, ids = setup
    alice = client_for(app, ids["alice"])
    alice.post(f"/feeds/users/{ids['alice']}/posts", data={"csrf_token": TOKEN, "body": "Status"})
    with app.app_context():
        post_id = get_db().execute("SELECT id FROM user_feed_posts").fetchone()[0]
    for name in ("bob", "carol"):
        client_for(app, ids[name]).post(f"/feeds/posts/{post_id}/react",
                                        data={"csrf_token": TOKEN, "kind": "like"})
    rows = alerts(app, ids["alice"], "reaction")
    assert len(rows) == 1
    assert "1 other" in rows[0]["text"]


def test_muted_kind_is_suppressed_but_sysop_notices_are_not(setup):
    app, ids = setup
    bob = client_for(app, ids["bob"])
    kinds = [kind for kind in notifications.MUTABLE_KINDS if kind != "mention"]
    bob.post("/notifications/mutes", data={"csrf_token": TOKEN, "kind": kinds})
    new_thread(app, client_for(app, ids["alice"]), body="hey @bob")
    assert alerts(app, ids["bob"]) == []
    admin = client_for(app, ids["admin"])
    admin.post(f"/sysop/moderation/users/{ids['bob']}/action",
               data={"csrf_token": TOKEN, "action": "warn", "reason": "Be kind"})
    assert [row["kind"] for row in alerts(app, ids["bob"])] == ["moderation"]


def test_event_reminder_fires_once(setup):
    app, ids = setup
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(microsecond=0)
    with app.app_context():
        database = get_db()
        event = database.execute(
            "INSERT INTO events(creator_id,name,description,start_at,timezone) VALUES(?,?,?,?,?)",
            (ids["admin"], "Net night", "Weekly net", start.isoformat().replace("+00:00", "Z"), "UTC"),
        ).lastrowid
        database.execute("INSERT INTO event_rsvps(event_id,user_id,response) VALUES(?,?,'going')",
                         (event, ids["alice"]))
        database.commit()
    alice = client_for(app, ids["alice"])
    for _ in range(3):
        assert alice.get("/notifications/poll").status_code == 200
    rows = alerts(app, ids["alice"], "event")
    assert len(rows) == 1 and "Net night" in rows[0]["text"]
    alice.post("/notifications/read-all", data={"csrf_token": TOKEN})
    alice.get("/notifications/poll")
    assert alerts(app, ids["alice"], "event")[0]["read_at"] is not None


def test_poll_returns_every_section(setup):
    app, ids = setup
    payload = client_for(app, ids["alice"]).get("/notifications/poll").get_json()
    assert set(payload) == {"mail", "direct", "notifications"}
    assert payload["notifications"] == {"latest_id": 0, "unread_count": 0, "new_count": 0}


def test_open_marks_read_and_redirects_only_locally(setup):
    app, ids = setup
    with app.app_context():
        database = get_db()
        notifications.notify(database, ids["alice"], "system", "Local", url="/board")
        notifications.notify(database, ids["alice"], "system", "Remote", url="//evil.example/")
        database.commit()
    local, remote = alerts(app, ids["alice"])
    alice = client_for(app, ids["alice"])
    response = alice.post(f"/notifications/{local['id']}/open", data={"csrf_token": TOKEN})
    assert response.headers["Location"].endswith("/board")
    response = alice.post(f"/notifications/{remote['id']}/open", data={"csrf_token": TOKEN})
    assert response.headers["Location"].endswith("/notifications")
    assert all(row["read_at"] for row in alerts(app, ids["alice"]))
    # Another member cannot open someone else's alert.
    assert client_for(app, ids["bob"]).post(
        f"/notifications/{local['id']}/open", data={"csrf_token": TOKEN}).status_code == 404


def test_report_resolution_notifies_reporter(setup):
    app, ids = setup
    with app.app_context():
        database = get_db()
        report = database.execute(
            "INSERT INTO moderation_reports(reporter_id,target_type,target_id,subject_user_id,reason,created_at) "
            "VALUES(?,?,?,?,?,?)", (ids["bob"], "user", ids["carol"], ids["carol"], "spam", "2026-01-01"),
        ).lastrowid
        database.commit()
    client_for(app, ids["admin"]).post(f"/sysop/moderation/reports/{report}/resolve",
                                       data={"csrf_token": TOKEN, "status": "dismissed", "note": "Fine"})
    rows = alerts(app, ids["bob"], "report")
    assert len(rows) == 1 and rows[0]["body"] == "Fine"


def test_password_reset_reaches_sysop_alerts(setup):
    app, ids = setup
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf_token"] = TOKEN
    client.post("/login", data={"csrf_token": TOKEN, "username": "alice", "action": "forgot_password"})
    rows = alerts(app, ids["admin"], "system")
    assert len(rows) == 1 and "Temporary password:" in rows[0]["body"]
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM mail").fetchone()[0] == 0


def test_legacy_feed_mail_migrates_to_alerts(setup):
    app, ids = setup
    with app.app_context():
        database = get_db()
        legacy = database.execute(
            "INSERT INTO mail(sender_id,recipient_id,subject,body,sender_deleted,is_read) "
            "VALUES(?,?,?,?,1,1)",
            (ids["bob"], ids["alice"], "FEED: New comment on your post",
             "bob commented.\n\nNice\n\nOpen: /users/2#user-feed"),
        ).lastrowid
        database.execute("UPDATE mail SET thread_id=id WHERE id=?", (legacy,))
        reply = database.execute(
            "INSERT INTO mail(sender_id,recipient_id,thread_id,reply_to_id,subject,body) "
            "VALUES(?,?,?,?,?,?)", (ids["alice"], ids["bob"], legacy, legacy, "Re: FEED", "thanks"),
        ).lastrowid
        database.commit()
        init_db()
        assert [tuple(row) for row in database.execute("SELECT id,thread_id FROM mail")] == [(reply, reply)]
    rows = alerts(app, ids["alice"], "comment")
    assert len(rows) == 1
    assert rows[0]["url"] == "/users/2#user-feed" and rows[0]["read_at"] is not None
