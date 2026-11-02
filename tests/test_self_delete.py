"""Authors can delete their own bulletin and imageboard content; moderation stays restricted."""
from pathlib import Path

import pytest

from stationnet import create_app
from stationnet.db import get_db

CSRF = "self-delete-test-token"


@pytest.fixture
def station(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,imageboard_enabled) VALUES('Test','Test','TST-1',1)")
        admin = db.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('owner','OWN-1','unused',1)"
        ).lastrowid
        author = db.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('author','AUT-1','unused')"
        ).lastrowid
        other = db.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('other','OTH-1','unused')"
        ).lastrowid
        db.commit()
    return app, admin, author, other


def client_for(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = CSRF
    return client


def seed_bulletin(app, author, replier):
    with app.app_context():
        db = get_db()
        thread = db.execute("INSERT INTO posts(author_id,title,body) VALUES(?,?,?)",
                            (author, "bulletin", "body")).lastrowid
        reply = db.execute("INSERT INTO posts(author_id,parent_id,body) VALUES(?,?,?)",
                           (replier, thread, "reply")).lastrowid
        db.commit()
    return thread, reply


def seed_imageboard(app, author, replier):
    folder = Path(app.config["DATA_DIR"]) / "imageboard"
    folder.mkdir(exist_ok=True)
    for name in ("thread.jpg", "thread-thumb.jpg", "reply.jpg", "reply-thumb.jpg"):
        (folder / name).write_bytes(b"image")
    with app.app_context():
        db = get_db()
        thread = db.execute(
            "INSERT INTO imageboard_threads(author_id,subject,image_name,image_type,thumb_name,created_at,bumped_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (author, "thread", "thread.jpg", "image/jpeg", "thread-thumb.jpg",
             "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
        ).lastrowid
        reply = db.execute(
            "INSERT INTO imageboard_replies(thread_id,author_id,body,image_name,image_type,thumb_name,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (thread, replier, "reply", "reply.jpg", "image/jpeg", "reply-thumb.jpg",
             "2026-01-02T00:00:00+00:00"),
        ).lastrowid
        db.commit()
    return thread, reply


def count(app, table, row_id):
    with app.app_context():
        return get_db().execute(f"SELECT COUNT(*) FROM {table} WHERE id=?", (row_id,)).fetchone()[0]


def audit_rows(app):
    with app.app_context():
        return get_db().execute("SELECT COUNT(*) FROM moderation_audit").fetchone()[0]


def test_bulletin_author_deletes_thread_and_replies_cascade(station):
    app, _, author, other = station
    thread, reply = seed_bulletin(app, author, other)
    response = client_for(app, author).post(f"/board/{thread}/delete", data={"csrf_token": CSRF})
    assert response.status_code == 302
    assert count(app, "posts", thread) == 0
    assert count(app, "posts", reply) == 0
    assert audit_rows(app) == 0


def test_bulletin_author_deletes_own_reply_only(station):
    app, _, author, other = station
    thread, reply = seed_bulletin(app, author, other)
    response = client_for(app, other).post(f"/board/{reply}/delete", data={"csrf_token": CSRF})
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/board/{thread}")
    assert count(app, "posts", thread) == 1
    assert count(app, "posts", reply) == 0
    assert audit_rows(app) == 0


def test_bulletin_other_user_cannot_delete(station):
    app, _, author, other = station
    thread, reply = seed_bulletin(app, author, other)
    client = client_for(app, other)
    assert client.post(f"/board/{thread}/delete", data={"csrf_token": CSRF}).status_code == 403
    assert count(app, "posts", thread) == 1
    assert count(app, "posts", reply) == 1


def test_bulletin_admin_removal_is_audited(station):
    app, admin, author, other = station
    thread, _ = seed_bulletin(app, author, other)
    assert client_for(app, admin).post(f"/board/{thread}/delete", data={"csrf_token": CSRF}).status_code == 302
    assert count(app, "posts", thread) == 0
    assert audit_rows(app) == 1


def test_bulletin_delete_requires_csrf_and_login(station):
    app, _, author, other = station
    thread, _ = seed_bulletin(app, author, other)
    assert client_for(app, author).post(f"/board/{thread}/delete").status_code == 400
    assert app.test_client().post(f"/board/{thread}/delete", data={"csrf_token": CSRF}).status_code == 302
    assert count(app, "posts", thread) == 1


def test_bulletin_controls_follow_ownership(station):
    app, admin, author, other = station
    thread, _ = seed_bulletin(app, author, other)
    author_page = client_for(app, author).get(f"/board/{thread}").get_data(as_text=True)
    assert "DELETE THREAD" in author_page
    assert "DELETE REPLY" not in author_page
    assert "SYSOP REMOVE" not in author_page
    replier_page = client_for(app, other).get(f"/board/{thread}").get_data(as_text=True)
    assert "DELETE THREAD" not in replier_page
    assert "DELETE REPLY" in replier_page
    admin_page = client_for(app, admin).get(f"/board/{thread}").get_data(as_text=True)
    assert "SYSOP REMOVE THREAD" in admin_page
    assert "SYSOP REMOVE REPLY" in admin_page


def test_imageboard_author_deletes_thread_and_files(station):
    app, _, author, other = station
    thread, reply = seed_imageboard(app, author, other)
    response = client_for(app, author).post(f"/imageboard/{thread}/moderate",
                                            data={"csrf_token": CSRF, "action": "delete"})
    assert response.status_code == 302
    assert count(app, "imageboard_threads", thread) == 0
    assert count(app, "imageboard_replies", reply) == 0
    assert audit_rows(app) == 0
    assert list((Path(app.config["DATA_DIR"]) / "imageboard").iterdir()) == []


def test_imageboard_author_deletes_reply_and_bump_recomputes(station):
    app, _, author, other = station
    thread, reply = seed_imageboard(app, author, other)
    response = client_for(app, other).post(f"/imageboard/replies/{reply}/delete", data={"csrf_token": CSRF})
    assert response.status_code == 302
    assert count(app, "imageboard_replies", reply) == 0
    assert audit_rows(app) == 0
    with app.app_context():
        row = get_db().execute("SELECT created_at,bumped_at FROM imageboard_threads WHERE id=?", (thread,)).fetchone()
    assert row["bumped_at"] == row["created_at"]
    folder = Path(app.config["DATA_DIR"]) / "imageboard"
    assert not (folder / "reply.jpg").exists()
    assert (folder / "thread.jpg").exists()


def test_imageboard_other_user_cannot_delete(station):
    app, _, author, other = station
    thread, reply = seed_imageboard(app, author, other)
    client = client_for(app, other)
    assert client.post(f"/imageboard/{thread}/moderate",
                       data={"csrf_token": CSRF, "action": "delete"}).status_code == 403
    client = client_for(app, author)
    assert client.post(f"/imageboard/replies/{reply}/delete", data={"csrf_token": CSRF}).status_code == 403
    assert count(app, "imageboard_threads", thread) == 1
    assert count(app, "imageboard_replies", reply) == 1


def test_imageboard_admin_removal_is_audited(station):
    app, admin, author, other = station
    thread, reply = seed_imageboard(app, author, other)
    client = client_for(app, admin)
    assert client.post(f"/imageboard/replies/{reply}/delete", data={"csrf_token": CSRF}).status_code == 302
    assert audit_rows(app) == 1
    assert client.post(f"/imageboard/{thread}/moderate",
                       data={"csrf_token": CSRF, "action": "delete"}).status_code == 302
    assert audit_rows(app) == 2


def test_imageboard_author_cannot_pin_or_lock(station):
    app, _, author, other = station
    thread, _ = seed_imageboard(app, author, other)
    client = client_for(app, author)
    for action in ("pin", "lock"):
        assert client.post(f"/imageboard/{thread}/moderate",
                           data={"csrf_token": CSRF, "action": action}).status_code == 403
    with app.app_context():
        row = get_db().execute("SELECT pinned,locked FROM imageboard_threads WHERE id=?", (thread,)).fetchone()
    assert (row["pinned"], row["locked"]) == (0, 0)


def test_imageboard_delete_requires_csrf(station):
    app, _, author, other = station
    thread, _ = seed_imageboard(app, author, other)
    assert client_for(app, author).post(f"/imageboard/{thread}/moderate",
                                        data={"action": "delete"}).status_code == 400
    assert count(app, "imageboard_threads", thread) == 1


def test_imageboard_controls_follow_ownership(station):
    app, admin, author, other = station
    thread, _ = seed_imageboard(app, author, other)
    author_page = client_for(app, author).get(f"/imageboard/{thread}").get_data(as_text=True)
    assert "DELETE THREAD" in author_page
    assert "DELETE REPLY" not in author_page
    assert ">PIN<" not in author_page
    replier_page = client_for(app, other).get(f"/imageboard/{thread}").get_data(as_text=True)
    assert "DELETE THREAD" not in replier_page
    assert "DELETE REPLY" in replier_page
    admin_page = client_for(app, admin).get(f"/imageboard/{thread}").get_data(as_text=True)
    assert ">PIN<" in admin_page and "DELETE THREAD" in admin_page and "DELETE REPLY" in admin_page
