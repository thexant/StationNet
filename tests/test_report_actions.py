"""Report buttons live in each item's actions dropdown and target that item."""
import pytest

from stationnet import create_app
from stationnet.db import get_db
from stationnet.modules import ALL_MODULES

TOKEN = "report-test"


@pytest.fixture
def setup(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    columns = ",".join(f"{name}_enabled" for name, _label in ALL_MODULES)
    with app.app_context():
        db = get_db()
        db.execute(f"INSERT INTO station(name,description,callsign,{columns}) "
                   f"VALUES('Test Station','About us','TST-1',{','.join('1' for _ in ALL_MODULES)})")
        author = db.execute("INSERT INTO users(username,callsign,password_hash) "
                            "VALUES('author','AUT-1','unused')").lastrowid
        viewer = db.execute("INSERT INTO users(username,callsign,password_hash) "
                            "VALUES('viewer','VIE-1','unused')").lastrowid
        post = db.execute("INSERT INTO posts(author_id,title,body) VALUES(?,?,?)",
                          (author, "Hello", "Body")).lastrowid
        reply = db.execute("INSERT INTO posts(author_id,parent_id,body) VALUES(?,?,?)",
                           (author, post, "Reply")).lastrowid
        thread = db.execute("INSERT INTO imageboard_threads(author_id,subject,image_name,image_type,thumb_name,"
                            "created_at,bumped_at) VALUES(?,?,?,?,?,?,?)",
                            (author, "Pics", "a.png", "image/png", "a_t.png", "2026-01-01", "2026-01-01")).lastrowid
        ib_reply = db.execute("INSERT INTO imageboard_replies(thread_id,author_id,body,created_at) VALUES(?,?,?,?)",
                              (thread, author, "Nice", "2026-01-01")).lastrowid
        feed = db.execute("INSERT INTO user_feed_posts(owner_id,body) VALUES(?,?)", (author, "Thought")).lastrowid
        comment = db.execute("INSERT INTO user_feed_comments(post_id,author_id,body) VALUES(?,?,?)",
                             (feed, author, "Comment")).lastrowid
        db.commit()
    ids = dict(author=author, viewer=viewer, post=post, reply=reply, thread=thread,
               ib_reply=ib_reply, feed=feed, comment=comment)
    return app, app.test_client(), ids


def login(client, user):
    with client.session_transaction() as session:
        session["user_id"] = user
        session["csrf_token"] = TOKEN


def report_marker(target_type, target_id):
    return f'name="target_type" value="{target_type}"><input type="hidden" name="target_id" value="{target_id}"'


def test_viewer_sees_per_item_report_buttons(setup):
    _app, client, ids = setup
    login(client, ids["viewer"])
    pages = {
        f"/board/{ids['post']}": [("bulletin", ids["post"]), ("bulletin", ids["reply"])],
        f"/imageboard/{ids['thread']}": [("imageboard_thread", ids["thread"]),
                                         ("imageboard_reply", ids["ib_reply"])],
        f"/users/{ids['author']}": [("user", ids["author"]), ("feed_post", ids["feed"]),
                                    ("feed_comment", ids["comment"])],
    }
    for url, targets in pages.items():
        html = client.get(url).get_data(as_text=True)
        assert "report-panel" not in html, url
        for target_type, target_id in targets:
            assert report_marker(target_type, target_id) in html, (url, target_type)
        assert "data-report-prompt" in html


def test_author_sees_no_report_on_own_items(setup):
    _app, client, ids = setup
    login(client, ids["author"])
    for url in (f"/board/{ids['post']}", f"/imageboard/{ids['thread']}", f"/users/{ids['author']}"):
        html = client.get(url).get_data(as_text=True)
        assert "data-report-prompt" not in html, url
        assert "moderation-actions" in html, url


def test_report_form_submits_for_its_item(setup):
    app, client, ids = setup
    login(client, ids["viewer"])
    response = client.post("/reports", data={"csrf_token": TOKEN, "target_type": "bulletin",
                                             "target_id": ids["reply"], "reason": "This reply is spam."})
    assert response.status_code == 302
    with app.app_context():
        row = get_db().execute("SELECT target_type,target_id FROM moderation_reports").fetchone()
    assert (row["target_type"], row["target_id"]) == ("bulletin", ids["reply"])
