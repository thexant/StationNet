"""Polls on bulletins and announcements: creation, voting, visibility, closing."""
import pytest

from stationnet import create_app
from stationnet.db import get_db

CSRF = "polls-test-token"


@pytest.fixture
def station(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,guest_access) VALUES('Test','Test','TST-1',1)")
        ids = {}
        for name, admin in (("owner", 1), ("author", 0), ("voter", 0), ("other", 0)):
            ids[name] = db.execute(
                "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,'unused',?)",
                (name, name.upper(), admin),
            ).lastrowid
        db.commit()
    return app, ids


def client_for(app, user_id=None):
    client = app.test_client()
    with client.session_transaction() as session:
        if user_id:
            session["user_id"] = user_id
        session["csrf_token"] = CSRF
    return client


def post_bulletin(client, options=("Red", "Blue"), multiple=False):
    data = {"csrf_token": CSRF, "title": "Colours", "body": "Pick one", "poll_enabled": "1",
            "poll_question": "Best colour?", "poll_option": list(options)}
    if multiple:
        data["poll_multiple"] = "1"
    return client.post("/board/new", data=data)


def poll_ids(app):
    with app.app_context():
        db = get_db()
        poll = db.execute("SELECT id,post_id FROM polls ORDER BY id DESC").fetchone()
        options = [row["id"] for row in db.execute(
            "SELECT id FROM poll_options WHERE poll_id=? ORDER BY position", (poll["id"],))]
    return poll["id"], poll["post_id"], options


def vote(client, poll_id, *option_ids, **extra):
    return client.post(f"/polls/{poll_id}/vote", data={"csrf_token": CSRF, "option_id": list(option_ids), **extra})


def votes(app):
    with app.app_context():
        return sorted(tuple(row) for row in get_db().execute("SELECT option_id,user_id FROM poll_votes"))


@pytest.mark.parametrize("options", [("Only",), [str(n) for n in range(11)], ("Same", "same"), ("Solo", "  ")])
def test_invalid_responses_create_nothing(station, options):
    app, ids = station
    response = post_bulletin(client_for(app, ids["author"]), options)
    assert response.status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
        assert get_db().execute("SELECT COUNT(*) FROM polls").fetchone()[0] == 0


def test_ten_responses_ignoring_blanks(station):
    app, ids = station
    assert post_bulletin(client_for(app, ids["author"]), [str(n) for n in range(10)] + [""]).status_code == 302
    assert len(poll_ids(app)[2]) == 10


def test_single_choice_vote_can_change_and_clear(station):
    app, ids = station
    post_bulletin(client_for(app, ids["author"]))
    poll, _, (red, blue) = poll_ids(app)
    voter = client_for(app, ids["voter"])
    vote(voter, poll, red)
    assert votes(app) == [(red, ids["voter"])]
    vote(voter, poll, blue)
    assert votes(app) == [(blue, ids["voter"])]
    vote(voter, poll, red, blue)
    assert votes(app) == [(blue, ids["voter"])]
    vote(voter, poll, action="clear")
    assert votes(app) == []


def test_multiple_choice(station):
    app, ids = station
    post_bulletin(client_for(app, ids["author"]), multiple=True)
    poll, _, (red, blue) = poll_ids(app)
    vote(client_for(app, ids["voter"]), poll, red, blue)
    assert votes(app) == [(red, ids["voter"]), (blue, ids["voter"])]


def test_option_from_another_poll_is_rejected(station):
    app, ids = station
    client = client_for(app, ids["author"])
    post_bulletin(client)
    first, _, (red, _blue) = poll_ids(app)
    post_bulletin(client)
    second, _, _ = poll_ids(app)
    vote(client_for(app, ids["voter"]), second, red)
    assert votes(app) == []


def test_guest_cannot_vote_but_sees_sign_in(station):
    app, ids = station
    post_bulletin(client_for(app, ids["author"]))
    poll, post, (red, _blue) = poll_ids(app)
    guest = client_for(app)
    assert vote(guest, poll, red).status_code == 302
    assert votes(app) == []
    page = guest.get(f"/board/{post}").get_data(as_text=True)
    assert "Best colour?" in page and "Sign in to vote" in page and 'name="option_id"' not in page


def test_result_visibility(station):
    app, ids = station
    author = client_for(app, ids["author"])
    post_bulletin(author)
    poll, post, (red, _blue) = poll_ids(app)
    voter, other, sysop, guest = (client_for(app, ids["voter"]), client_for(app, ids["other"]),
                                  client_for(app, ids["owner"]), client_for(app))
    vote(voter, poll, red)
    def shown(client):
        return "1 USER HAS VOTED" in client.get(f"/board/{post}").get_data(as_text=True)
    assert shown(author) and shown(sysop) and shown(voter)
    assert not shown(other) and not shown(guest)
    assert "<progress" not in other.get(f"/board/{post}").get_data(as_text=True)
    assert author.post(f"/polls/{poll}/close", data={"csrf_token": CSRF}).status_code == 302
    assert shown(other) and shown(guest)


def test_closed_poll_refuses_votes(station):
    app, ids = station
    author = client_for(app, ids["author"])
    post_bulletin(author)
    poll, post, (red, _blue) = poll_ids(app)
    voter = client_for(app, ids["voter"])
    vote(voter, poll, red)
    author.post(f"/polls/{poll}/close", data={"csrf_token": CSRF})
    vote(voter, poll, action="clear")
    vote(client_for(app, ids["other"]), poll, red)
    assert votes(app) == [(red, ids["voter"])]
    page = voter.get(f"/board/{post}").get_data(as_text=True)
    assert "CLOSE POLL" not in page and 'name="option_id"' not in page


def test_close_permissions_and_audit(station):
    app, ids = station
    post_bulletin(client_for(app, ids["author"]))
    poll, _, _ = poll_ids(app)
    assert client_for(app, ids["other"]).post(f"/polls/{poll}/close", data={"csrf_token": CSRF}).status_code == 403
    assert client_for(app, ids["owner"]).post(f"/polls/{poll}/close", data={"csrf_token": CSRF}).status_code == 302
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT closed_at FROM polls").fetchone()[0]
        row = db.execute("SELECT actor_id,user_id FROM moderation_audit WHERE action='close_poll'").fetchone()
        assert tuple(row) == (ids["owner"], ids["author"])


def test_announcement_poll_is_sysop_only_and_votable(station):
    app, ids = station
    data = {"csrf_token": CSRF, "title": "Notice", "body": "Text", "poll_enabled": "1",
            "poll_question": "Agree?", "poll_option": ["Yes", "No"]}
    assert client_for(app, ids["author"]).post("/announcements/new", data=data).status_code in (302, 403)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 0
    sysop = client_for(app, ids["owner"])
    assert sysop.post("/announcements/new", data=data).status_code == 302
    with app.app_context():
        poll = get_db().execute("SELECT id FROM polls").fetchone()[0]
        yes = get_db().execute("SELECT id FROM poll_options ORDER BY position").fetchone()[0]
    voter = client_for(app, ids["voter"])
    assert "#poll-" in vote(voter, poll, yes).headers["Location"]
    assert "1 USER HAS VOTED" in voter.get("/announcements").get_data(as_text=True)


def test_disabled_module_hides_poll_routes(station):
    app, ids = station
    post_bulletin(client_for(app, ids["author"]))
    poll, _, (red, _blue) = poll_ids(app)
    with app.app_context():
        get_db().execute("UPDATE station SET board_enabled=0")
        get_db().commit()
    assert vote(client_for(app, ids["voter"]), poll, red).status_code == 404


def test_cascades(station):
    app, ids = station
    post_bulletin(client_for(app, ids["author"]))
    poll, post, (red, _blue) = poll_ids(app)
    vote(client_for(app, ids["voter"]), poll, red)
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM users WHERE id=?", (ids["voter"],))
        assert db.execute("SELECT COUNT(*) FROM poll_votes").fetchone()[0] == 0
        db.execute("DELETE FROM posts WHERE id=?", (post,))
        assert db.execute("SELECT COUNT(*) FROM polls").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM poll_options").fetchone()[0] == 0
