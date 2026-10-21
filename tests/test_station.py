from unittest.mock import patch

from stationnet import create_app
from stationnet.ansi import station_banner
from stationnet.db import get_db
from stationnet.security import generate_station_callsign, valid_callsign
from .conftest import csrf

def test_uninitialized_redirects(client):
    response=client.get("/"); assert response.status_code==302; assert response.headers["Location"].endswith("/setup")

def test_setup_persists_and_hashes(app, initialized):
    with app.app_context():
        station=get_db().execute("SELECT * FROM station").fetchone(); admin=get_db().execute("SELECT * FROM users").fetchone()
        assert valid_callsign(station["callsign"]); assert valid_callsign(admin["callsign"])
        assert station["callsign"] != admin["callsign"]
        assert admin["is_admin"]==1; assert admin["password_hash"].startswith("scrypt:"); assert "correct horse" not in admin["password_hash"]

def test_generated_callsigns_have_six_alphanumeric_characters():
    with patch("stationnet.security.secrets.choice", side_effect=list("GPT123")):
        value = generate_station_callsign()
    assert value == "GPT-123"
    assert valid_callsign(value)

def test_station_banner_normalizes_text_and_bounds_line_width():
    assert station_banner("Dög City") == station_banner("DOG CITY")
    assert station_banner("\N{SATELLITE ANTENNA}") == station_banner("STATIONNET")
    assert max(map(len, station_banner("A" * 80).splitlines())) <= 72

def test_station_name_drives_ansi_masthead(initialized):
    initialized.post(
        "/sysop/settings",
        data={
            "csrf_token": csrf(initialized, "/sysop/settings"),
            "name": "Dog City",
            "description": "Local test station",
        },
    )

    response = initialized.get("/terminal")
    assert station_banner("Dog City").encode() in response.data
    assert b'aria-label="Dog City home"' in response.data

def test_landing_message_reflects_guest_access(initialized):
    response = initialized.get("/")
    assert b"Explore the station as a visitor" in response.data
    assert b"Browse the public board" not in response.data

    initialized.post(
        "/sysop/settings/access",
        data={"csrf_token": csrf(initialized, "/sysop/settings")},
    )
    response = initialized.get("/")
    assert b"Identify yourself to access station services" in response.data
    assert b"Explore the station as a visitor" not in response.data

def test_sysop_can_customize_landing_message_and_tagline(initialized):
    response = initialized.post(
        "/sysop/settings",
        data={
            "csrf_token": csrf(initialized, "/sysop/settings"),
            "name": "Test Relay",
            "description": "Local test station",
            "landing_message": "Welcome to every service aboard this relay.",
            "brand_tagline": "SIGNAL HOUSE // COMMUNITY ONLINE //",
            "registration_open": "on",
            "guest_access": "on",
        },
        follow_redirects=True,
    )
    assert b"Station parameters updated" in response.data

    response = initialized.get("/")
    assert b"Welcome to every service aboard this relay." in response.data
    assert b"SIGNAL HOUSE // COMMUNITY ONLINE //" in response.data
    assert b"Explore the station as a visitor" not in response.data

def test_startup_replaces_legacy_callsigns(app, initialized):
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET callsign='GPT50'")
        database.execute("UPDATE users SET callsign='GPT5-7973'")
        database.commit()

    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "new-process"})
    with restarted.app_context():
        station_callsign = get_db().execute("SELECT callsign FROM station").fetchone()[0]
        user_callsign = get_db().execute("SELECT callsign FROM users").fetchone()[0]
        assert station_callsign != user_callsign
        assert all(valid_callsign(value) for value in (station_callsign, user_callsign))

def test_register_login_and_unique_callsign(app, initialized):
    response=initialized.post("/register",data={"csrf_token":csrf(initialized,"/register"),"username":"visitor","password":"a secure visitor pass","confirm_password":"a secure visitor pass"},follow_redirects=True)
    assert b"Account linked" in response.data
    with app.app_context():
        values=[r["callsign"] for r in get_db().execute("SELECT callsign FROM users")]
        assert len(values)==len(set(values))==2
        assert all(valid_callsign(value) for value in values)
    initialized.post("/logout",data={"csrf_token":csrf(initialized,"/terminal")})
    response=initialized.post("/login",data={"csrf_token":csrf(initialized,"/login"),"username":"visitor","password":"a secure visitor pass"},follow_redirects=True)
    assert b"Link established" in response.data

def test_access_policy_can_enable_registration_independently(app, initialized):
    response = initialized.post(
        "/sysop/settings/access",
        data={
            "csrf_token": csrf(initialized, "/sysop/settings"),
            "registration_open": "on",
            "guest_access": "on",
        },
        follow_redirects=True,
    )
    assert b"Access policy updated" in response.data
    with app.app_context():
        station = get_db().execute(
            "SELECT registration_open,guest_access FROM station WHERE id=1"
        ).fetchone()
        assert tuple(station) == (1, 1)

    initialized.post(
        "/logout", data={"csrf_token": csrf(initialized, "/terminal")}
    )
    landing = initialized.get("/")
    assert b'href="/register"' in landing.data
    assert b"CREATE ACCOUNT" in landing.data

    registration = initialized.get("/register")
    assert registration.status_code == 200
    assert b"Create your account" in registration.data

def test_board_reply_guest_read_and_restart(app, initialized):
    response=initialized.post("/board/new",data={"csrf_token":csrf(initialized,"/board/new"),"title":"First transmission","body":"Hello local network."},follow_redirects=True)
    assert b"First transmission" in response.data
    initialized.post("/board/1",data={"csrf_token":csrf(initialized,"/board/1"),"body":"Signal received."})
    initialized.post("/logout",data={"csrf_token":csrf(initialized,"/terminal")})
    assert b"First transmission" in initialized.get("/board").data; assert b"Signal received" in initialized.get("/board/1").data
    restarted=create_app({"TESTING":True,"DATA_DIR":app.config["DATA_DIR"],"SECRET_KEY":"new-process"}).test_client()
    assert b"First transmission" in restarted.get("/board").data

def test_admin_delete_and_csrf(app, initialized):
    initialized.post("/board/new",data={"csrf_token":csrf(initialized,"/board/new"),"title":"Moderate me","body":"Content"})
    response=initialized.post("/board/1/delete",data={"csrf_token":csrf(initialized,"/board/1")},follow_redirects=True)
    assert b"removed by SYSOP" in response.data
    with app.app_context(): assert get_db().execute("SELECT COUNT(*) FROM posts").fetchone()[0]==0
    assert initialized.post("/board/new",data={"title":"No token","body":"Rejected"}).status_code==400

