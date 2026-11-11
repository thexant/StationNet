"""Login door: call log, redirect after login, oneliners, panels, and SYSOP settings."""
import json
from datetime import date

import pytest

from stationnet import create_app, door
from stationnet.db import get_db
from stationnet.modules import ALL_MODULES
from stationnet.security import hash_password

TOKEN = "door-test"
PASSWORD = "correct horse battery"


@pytest.fixture
def setup(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    columns = ",".join(f"{name}_enabled" for name, _label in ALL_MODULES)
    with app.app_context():
        db = get_db()
        db.execute(f"INSERT INTO station(name,description,callsign,{columns}) "
                   f"VALUES('Test Station','About us','TST-1',{','.join('1' for _ in ALL_MODULES)})")
        ids = {}
        for name, admin in (("admin", 1), ("alice", 0), ("bob", 0)):
            ids[name] = db.execute(
                "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,?)",
                (name, f"{name[:3].upper()}-1", hash_password(PASSWORD), admin),
            ).lastrowid
        db.commit()
    return app, ids


def client_for(app, user_id=None):
    client = app.test_client()
    with client.session_transaction() as session:
        if user_id is not None:
            session["user_id"] = user_id
        session["csrf_token"] = TOKEN
    return client


def login(client, username, next_path=None):
    url = "/login" + (f"?next={next_path}" if next_path else "")
    return client.post(url, data={"csrf_token": TOKEN, "username": username, "password": PASSWORD})


def query(app, sql, *params):
    with app.app_context():
        return get_db().execute(sql, params).fetchall()


def test_login_records_call_and_lands_on_door(setup):
    app, ids = setup
    response = login(client_for(app), "alice")
    assert response.headers["Location"].endswith("/door")
    assert [row["user_id"] for row in query(app, "SELECT user_id FROM station_calls")] == [ids["alice"]]


def test_login_honours_next_and_disabled_door(setup):
    app, _ids = setup
    assert login(client_for(app), "alice", "/board").headers["Location"].endswith("/board")
    with app.app_context():
        get_db().execute("UPDATE station SET door_enabled=0")
        get_db().commit()
    assert login(client_for(app), "bob").headers["Location"].endswith("/terminal")
    assert client_for(app, 2).get("/door").headers["Location"].endswith("/terminal")


def test_door_renders_callers_and_previous_call(setup):
    app, ids = setup
    for _ in range(2):
        login(client_for(app), "alice")
    login(client_for(app), "bob")
    with app.app_context():
        assert door.previous_call(get_db(), ids["alice"]) is not None
        assert door.previous_call(get_db(), ids["bob"]) is None
    page = client_for(app, ids["alice"]).get("/door").get_data(as_text=True)
    assert "LAST CALLERS" in page and "WAITING FOR YOU" in page
    assert "bob" in page and "YOUR LAST CALL" in page


def test_oneliner_length_rate_limit_and_delete_rules(setup):
    app, ids = setup
    alice, bob, admin = (client_for(app, ids[name]) for name in ("alice", "bob", "admin"))
    alice.post("/door/oneliners", data={"csrf_token": TOKEN, "body": "x" * 81})
    assert query(app, "SELECT * FROM oneliners") == []
    alice.post("/door/oneliners", data={"csrf_token": TOKEN, "body": "  hello   world  "})
    alice.post("/door/oneliners", data={"csrf_token": TOKEN, "body": "too soon"})
    rows = query(app, "SELECT id,body FROM oneliners")
    assert [row["body"] for row in rows] == ["hello world"]
    line = rows[0]["id"]
    assert bob.post(f"/door/oneliners/{line}/delete", data={"csrf_token": TOKEN}).status_code == 403
    admin.post(f"/door/oneliners/{line}/delete", data={"csrf_token": TOKEN})
    assert query(app, "SELECT * FROM oneliners") == []
    assert query(app, "SELECT action FROM moderation_audit")[0]["action"] == "delete_oneliner"


def test_muted_member_cannot_post_oneliner(setup):
    app, ids = setup
    with app.app_context():
        get_db().execute("INSERT INTO moderation_restrictions(user_id,kind,starts_at,reason) "
                         "VALUES(?,'mute','2020-01-01T00:00:00+00:00','quiet')", (ids["bob"],))
        get_db().commit()
    bob = client_for(app, ids["bob"])
    assert bob.post("/door/oneliners", data={"csrf_token": TOKEN, "body": "hi"}).status_code == 403
    assert 'name="body"' not in bob.get("/door").get_data(as_text=True)


def test_sysop_settings_save_order_and_validate_quotes(setup):
    app, ids = setup
    admin = client_for(app, ids["admin"])
    response = admin.post("/sysop/settings/door", data={
        "csrf_token": TOKEN, "door_enabled": "on", "panel": ["quote", "callers", "banner"],
        "position_banner": "3", "position_quote": "1", "position_callers": "2",
        "door_quotes": "First quote\n\n  Second quote  ",
    })
    assert response.headers["Location"].endswith("#login-sequence")
    row = query(app, "SELECT door_enabled,door_panels_json,door_quotes FROM station")[0]
    assert row["door_enabled"] == 1
    assert json.loads(row["door_panels_json"]) == ["quote", "callers", "banner"]
    assert row["door_quotes"] == "First quote\nSecond quote"

    admin.post("/sysop/settings/door", data={"csrf_token": TOKEN, "door_quotes": "x" * 201})
    assert query(app, "SELECT door_quotes FROM station")[0]["door_quotes"] == "First quote\nSecond quote"

    member = client_for(app, ids["alice"])
    member.post("/sysop/settings/door", data={"csrf_token": TOKEN})
    assert query(app, "SELECT door_enabled FROM station")[0]["door_enabled"] == 1


def test_quote_of_the_day_is_stable_and_falls_back_to_taglines(setup):
    app, _ids = setup
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET door_quotes='Alpha\nBeta\nGamma'")
        station = database.execute("SELECT * FROM station").fetchone()
        today = date(2026, 11, 10)
        assert door.quote_of_the_day(station, today) == door.quote_of_the_day(station, today)
        assert door.quote_of_the_day(station, today) in {"Alpha", "Beta", "Gamma"}
        database.execute("UPDATE station SET door_quotes=''")
        station = database.execute("SELECT * FROM station").fetchone()
        assert door.quote_of_the_day(station, today) == station["brand_tagline"]


def test_welcome_page_shows_waiting_summary(setup):
    app, ids = setup
    with app.app_context():
        database = get_db()
        database.execute("INSERT INTO mail(sender_id,recipient_id,subject,body) VALUES(?,?,?,?)",
                         (ids["bob"], ids["alice"], "Hi", "Hello"))
        database.commit()
    page = client_for(app, ids["alice"]).get("/").get_data(as_text=True)
    assert "WAITING FOR YOU" in page and "1 NEW MAIL" in page



def test_landing_enter_button_leads_to_door_and_door_continues_to_terminal(setup):
    app, ids = setup
    client = client_for(app, ids["alice"])
    assert 'href="/door"' in client.get("/").get_data(as_text=True)
    assert 'href="/terminal" data-door-continue' in client.get("/door").get_data(as_text=True)
    with app.app_context():
        get_db().execute("UPDATE station SET door_enabled=0")
        get_db().commit()
    page = client.get("/").get_data(as_text=True)
    assert 'href="/door"' not in page and 'href="/terminal"' in page


def test_door_visit_counts_as_a_call_once(setup):
    app, ids = setup
    client = client_for(app, ids["alice"])  # a remembered session: no sign-in
    client.get("/door")
    client.get("/door")
    assert len(query(app, "SELECT * FROM station_calls")) == 1
    with app.app_context():
        get_db().execute("UPDATE station_calls SET called_at='2020-01-01T00:00:00+00:00'")
        get_db().commit()
    client.get("/door")
    assert len(query(app, "SELECT * FROM station_calls")) == 2


def test_sign_in_then_door_is_one_call(setup):
    app, _ids = setup
    client = client_for(app)
    assert login(client, "alice").headers["Location"].endswith("/door")
    client.get("/door")
    assert len(query(app, "SELECT * FROM station_calls")) == 1


def test_last_callers_lists_each_member_once_at_latest_call(setup):
    app, ids = setup
    with app.app_context():
        database = get_db()
        for name in ("alice", "bob", "alice", "alice"):
            door.record_call(database, ids[name])
        database.commit()
        callers = door.last_callers(database)
        latest_alice = database.execute(
            "SELECT MAX(id) FROM station_calls WHERE user_id=?", (ids["alice"],)).fetchone()[0]
    assert [row["username"] for row in callers] == ["alice", "bob"]
    assert callers[0]["id"] == latest_alice
