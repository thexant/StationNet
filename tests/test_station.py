import re
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


def test_terminal_service_links_match_header_order_and_include_radio(initialized):
    response = initialized.get("/terminal")
    service_list = response.data.split(b'class="service-list">', 1)[1].split(b"</div>", 1)[0]

    assert re.findall(rb"<strong>([^<]+)</strong>", service_list) == [
        b"BULLETIN BOARD",
        b"ANNOUNCEMENTS",
        b"STATION CHAT",
        b"PRIVATE MAIL",
        b"RADIO",
        b"FILE ARCHIVE",
        b"USER DIRECTORY",
        b"GAMES AND APPS",
    ]
    assert b'href="/radio/"' in service_list


def test_terminal_activity_uses_existing_station_data(app, initialized):
    with app.app_context():
        database = get_db()
        user = database.execute("SELECT id,callsign FROM users WHERE username='sysop'").fetchone()
        bulletin_id = database.execute(
            "INSERT INTO posts(author_id,title,body,created_at) VALUES(?,?,?,?)",
            (user["id"], "Power Window", "Primary bulletin", "2026-10-20 12:00:00"),
        ).lastrowid
        reply_id = database.execute(
            "INSERT INTO posts(author_id,parent_id,body,created_at) VALUES(?,?,?,?)",
            (user["id"], bulletin_id, "Generator checks are complete.", "2026-10-20 12:05:00"),
        ).lastrowid
        database.execute(
            "INSERT INTO station_chat_messages(author_id,body,created_at) VALUES(?,?,?)",
            (user["id"], "All clear", "2026-10-20 12:10:00"),
        )
        file_id = database.execute(
            "INSERT INTO archive_files(uploader_id,display_name,stored_name,category,size_bytes,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (user["id"], "relay-map.txt", "test-map.txt", "Documents", 12, "2026-10-20 12:15:00"),
        ).lastrowid
        database.execute(
            "INSERT INTO mail(sender_id,recipient_id,subject,body,is_read) VALUES(?,?,?,?,0)",
            (user["id"], user["id"], "Unread one", "Message"),
        )
        database.execute(
            "INSERT INTO radio_broadcast_assignments(user_id,frequency_khz,broadcast_name,broadcast_message) "
            "VALUES(?,?,?,?)",
            (user["id"], 10100, "Night Signal", "Music through local midnight"),
        )
        database.commit()

    public_broadcasts = {
        user["id"]: {
            "user_id": user["id"], "callsign": user["callsign"], "frequency": "10.10",
            "frequency_khz": 10100, "status": "playing", "track_title": "Aurora",
            "track_artist": "Relay Ensemble",
        }
    }
    with patch.object(app.extensions["hosted_broadcasts"], "all_public", return_value=public_broadcasts):
        response = initialized.get("/terminal")

    assert response.status_code == 200
    for text in (
        b"STATION ACTIVITY", b"Power Window", b"sysop", b"Generator checks are complete.",
        b"relay-map.txt", b"Night Signal", b"Music through local midnight", b"Aurora",
        b"Relay Ensemble",
    ):
        assert text in response.data
    assert f'href="/board/{bulletin_id}#reply-{reply_id}"'.encode() in response.data
    assert f'href="/files#file-{file_id}"'.encode() in response.data
    assert response.data.count(b"data-local-time") == 4
    assert b">1</strong><small>UNREAD MESSAGE</small>" in response.data


def test_terminal_activity_excludes_paused_and_unassigned_broadcasts(app, initialized):
    with app.app_context():
        database = get_db()
        user = database.execute("SELECT id,callsign FROM users WHERE username='sysop'").fetchone()
        database.execute(
            "INSERT INTO radio_broadcast_assignments(user_id,frequency_khz,broadcast_name) VALUES(?,?,?)",
            (user["id"], 10100, "Assigned Signal"),
        )
        database.commit()

    paused = {user["id"]: {"user_id": user["id"], "callsign": user["callsign"],
              "frequency": "10.10", "frequency_khz": 10100, "status": "paused",
              "track_title": "Quiet", "track_artist": ""}}
    with patch.object(app.extensions["hosted_broadcasts"], "all_public", return_value=paused):
        response = initialized.get("/terminal")

    assert b"Assigned Signal" not in response.data
    assert b"NO AUTHORIZED BROADCASTS ON AIR" in response.data


def test_station_name_and_callsign_appear_in_descriptive_identity(initialized, app):
    with app.app_context():
        callsign = get_db().execute("SELECT callsign FROM station WHERE id=1").fetchone()[0]
    identity = f"Test Relay - [{callsign}]".encode()

    for path in ("/", "/login", "/terminal", "/sysop/settings"):
        response = initialized.get(path)
        assert identity in response.data

    # The compact footer remains callsign-only.
    response = initialized.get("/")
    assert f"STATION: {callsign}".encode() in response.data
    assert f"STATION: Test Relay - [{callsign}]".encode() not in response.data


def test_sysop_settings_use_independent_accessible_collapsible_sections(initialized):
    page = initialized.get("/sysop/settings")

    assert page.data.count(b"data-collapsible-panel") == 8
    for content_id in (
        "station-parameters-content", "access-policy-content", "radio-storage-content",
        "mud-service-content",
        "radio-spectrum-content", "broadcast-frequencies-content",
        "radio-permissions-content", "radio-users-content",
    ):
        assert f'aria-controls="{content_id}"'.encode() in page.data
        assert f'id="{content_id}" data-panel-content'.encode() in page.data
    assert page.data.count(b'aria-expanded="true"') >= 8
    assert b"COMMIT PARAMETERS" in page.data
    assert b"SAVE ACCESS POLICY" in page.data
    assert b"ADD / UPDATE RULE" in page.data

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
            "landing_heading": "A note from the operator",
            "landing_message": "Welcome to **every service** aboard this relay.\n\n- Be kind\n- Have fun",
            "landing_message_control": "1",
            "landing_message_enabled": "on",
            "brand_tagline": "SIGNAL HOUSE // COMMUNITY ONLINE //",
            "registration_open": "on",
            "guest_access": "on",
        },
        follow_redirects=True,
    )
    assert b"Station parameters updated" in response.data

    response = initialized.get("/")
    assert b"A note from the operator" in response.data
    assert b"Welcome to <strong>every service</strong> aboard this relay." in response.data
    assert b"<li>Be kind</li>" in response.data
    assert b"SIGNAL HOUSE // COMMUNITY ONLINE //" in response.data
    assert b"Explore the station as a visitor" not in response.data


def test_sysop_can_disable_and_reenable_landing_message(initialized):
    form = {
        "csrf_token": csrf(initialized, "/sysop/settings"),
        "name": "Test Relay",
        "description": "Local test station",
        "landing_heading": "Operator message",
        "landing_message": "This message can be hidden.",
        "landing_message_control": "1",
    }
    initialized.post("/sysop/settings", data=form)
    landing = initialized.get("/")
    assert b"Operator message" not in landing.data
    assert b"This message can be hidden." not in landing.data
    assert b"landing-copy" not in landing.data

    form["csrf_token"] = csrf(initialized, "/sysop/settings")
    form["landing_message_enabled"] = "on"
    initialized.post("/sysop/settings", data=form)
    assert b"This message can be hidden." in initialized.get("/").data


def test_landing_markdown_escapes_html_and_unsafe_links(initialized):
    initialized.post(
        "/sysop/settings",
        data={
            "csrf_token": csrf(initialized, "/sysop/settings"),
            "name": "Test Relay",
            "description": "Local test station",
            "landing_message": "<script>alert(1)</script> [bad](javascript:alert(2))",
            "landing_message_control": "1",
            "landing_message_enabled": "on",
        },
    )
    landing = initialized.get("/")
    assert b"<script>" not in landing.data
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in landing.data
    assert b'href="javascript:' not in landing.data

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

def test_login_remember_me_controls_cookie_persistence(app, initialized):
    initialized.post("/logout", data={"csrf_token": csrf(initialized, "/terminal")})

    login_page = initialized.get("/login")
    assert b'name="remember_me"' in login_page.data
    response = initialized.post(
        "/login",
        data={
            "csrf_token": csrf(initialized, "/login"),
            "username": "sysop",
            "password": "correct horse battery staple",
        },
    )
    assert "Expires=" not in response.headers["Set-Cookie"]

    initialized.post("/logout", data={"csrf_token": csrf(initialized, "/terminal")})
    response = initialized.post(
        "/login",
        data={
            "csrf_token": csrf(initialized, "/login"),
            "username": "sysop",
            "password": "correct horse battery staple",
            "remember_me": "on",
        },
    )
    assert "Expires=" in response.headers["Set-Cookie"]

    remembered_cookie = initialized.get_cookie(app.config["SESSION_COOKIE_NAME"])
    restarted_app = create_app({
        "TESTING": True,
        "DATA_DIR": app.config["DATA_DIR"],
        "SECRET_KEY": app.config["SECRET_KEY"],
    })
    restarted = restarted_app.test_client()
    restarted.set_cookie(app.config["SESSION_COOKIE_NAME"], remembered_cookie.value)
    assert b"AUTHENTICATED" in restarted.get("/terminal").data

    restarted.post("/logout", data={"csrf_token": csrf(restarted, "/terminal")})
    terminal = restarted.get("/terminal")
    assert b"AUTHENTICATED" not in terminal.data
    assert b"AUTHENTICATE" in terminal.data

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


def _generate_invitation(client):
    response = client.post(
        "/sysop/invitations/generate",
        data={"csrf_token": csrf(client, "/sysop/settings")},
    )
    assert response.status_code == 200
    match = re.search(rb'value="(SN-[A-Za-z0-9_-]+)"', response.data)
    assert match
    return match.group(1).decode(), response


def _set_registration_mode(client, mode):
    response = client.post(
        "/sysop/settings/access",
        data={
            "csrf_token": csrf(client, "/sysop/settings"),
            "registration_mode": mode,
            "guest_access": "on",
        },
    )
    assert response.status_code == 302


def test_private_registration_requires_and_atomically_consumes_invitation(app, initialized):
    code, created = _generate_invitation(initialized)
    assert b"COPY CODE" in created.data
    assert code.encode() in created.data
    assert (b"/register#invite=" + code.encode()) in created.data

    with app.app_context():
        invite = get_db().execute("SELECT * FROM registration_invites").fetchone()
        assert invite["token_hash"] != code
        assert code not in tuple(str(value) for value in invite)

    _set_registration_mode(initialized, "private")
    initialized.post("/logout", data={"csrf_token": csrf(initialized, "/terminal")})

    landing = initialized.get("/")
    assert b"INVITATION ONLY" in landing.data
    assert b"USE INVITATION" in landing.data
    assert b"gateway-splash mode-private" in landing.data
    assert b"RESTRICTED ACCESS NODE" in landing.data
    linked = initialized.get("/register?invite=" + code)
    assert code.encode() in linked.data
    assert b"Invitation code" in linked.data
    assert linked.headers["Cache-Control"] == "no-store"
    assert linked.headers["Referrer-Policy"] == "no-referrer"

    invalid = initialized.post(
        "/register",
        data={
            "csrf_token": csrf(initialized, "/register"),
            "invite": "SN-not-valid",
            "username": "intruder",
            "password": "a secure visitor pass",
            "confirm_password": "a secure visitor pass",
        },
    )
    assert b"invalid or no longer available" in invalid.data

    registered = initialized.post(
        "/register",
        data={
            "csrf_token": csrf(initialized, "/register"),
            "invite": code,
            "username": "invited",
            "password": "a secure visitor pass",
            "confirm_password": "a secure visitor pass",
        },
        follow_redirects=True,
    )
    assert b"Account linked" in registered.data

    initialized.post("/logout", data={"csrf_token": csrf(initialized, "/terminal")})
    reused = initialized.post(
        "/register",
        data={
            "csrf_token": csrf(initialized, "/register"),
            "invite": code,
            "username": "second-user",
            "password": "a secure visitor pass",
            "confirm_password": "a secure visitor pass",
        },
    )
    assert b"invalid or no longer available" in reused.data
    with app.app_context():
        database = get_db()
        assert database.execute("SELECT COUNT(*) FROM users WHERE username='second-user'").fetchone()[0] == 0
        redemption = database.execute("SELECT used_by,used_at FROM registration_invites").fetchone()
        assert redemption["used_by"] is not None
        assert redemption["used_at"] is not None


def test_sysop_can_revoke_invitation_and_full_code_is_not_listed(app, initialized):
    code, _ = _generate_invitation(initialized)
    settings = initialized.get("/sysop/settings")
    assert code.encode() not in settings.data
    assert code[-8:].encode() in settings.data

    with app.app_context():
        invitation_id = get_db().execute("SELECT id FROM registration_invites").fetchone()[0]
    response = initialized.post(
        f"/sysop/invitations/{invitation_id}/revoke",
        data={"csrf_token": csrf(initialized, "/sysop/settings")},
        follow_redirects=True,
    )
    assert b"Invitation revoked" in response.data
    assert b"REVOKED" in response.data

    _set_registration_mode(initialized, "private")
    initialized.post("/logout", data={"csrf_token": csrf(initialized, "/terminal")})
    rejected = initialized.post(
        "/register",
        data={
            "csrf_token": csrf(initialized, "/register"),
            "invite": code,
            "username": "revoked-user",
            "password": "a secure visitor pass",
            "confirm_password": "a secure visitor pass",
        },
    )
    assert b"invalid or no longer available" in rejected.data


def test_disabled_registration_blocks_direct_requests_but_not_login(initialized):
    _set_registration_mode(initialized, "disabled")
    initialized.post("/logout", data={"csrf_token": csrf(initialized, "/terminal")})

    landing = initialized.get("/")
    assert b"CLOSED TO NEW REGISTRATIONS" in landing.data
    assert b"CREATE ACCOUNT" not in landing.data
    assert b"gateway-splash mode-disabled" in landing.data
    assert b"CLOSED ACCESS NODE" in landing.data
    assert initialized.get("/register").status_code == 302
    blocked = initialized.post(
        "/register",
        data={
            "csrf_token": csrf(initialized, "/login"),
            "username": "bypass",
            "password": "a secure visitor pass",
            "confirm_password": "a secure visitor pass",
        },
    )
    assert blocked.status_code == 302

    login = initialized.post(
        "/login",
        data={
            "csrf_token": csrf(initialized, "/login"),
            "username": "sysop",
            "password": "correct horse battery staple",
        },
        follow_redirects=True,
    )
    assert b"Link established" in login.data


def test_legacy_closed_policy_migrates_without_changing_accounts(app, initialized):
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET registration_open=0")
        original_accounts = database.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        database.execute(
            "CREATE TABLE legacy_station (id INTEGER PRIMARY KEY CHECK(id=1),"
            "name TEXT NOT NULL,description TEXT NOT NULL,callsign TEXT NOT NULL UNIQUE,"
            "registration_open INTEGER NOT NULL DEFAULT 1,"
            "guest_access INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        database.execute(
            "INSERT INTO legacy_station(id,name,description,callsign,registration_open,guest_access,created_at) "
            "SELECT id,name,description,callsign,registration_open,guest_access,created_at FROM station"
        )
        database.execute("DROP TABLE station")
        database.execute("ALTER TABLE legacy_station RENAME TO station")
        database.commit()

    restarted = create_app({
        "TESTING": True,
        "DATA_DIR": app.config["DATA_DIR"],
        "SECRET_KEY": app.config["SECRET_KEY"],
    })
    with restarted.app_context():
        database = get_db()
        station = database.execute(
            "SELECT registration_open,registration_mode FROM station WHERE id=1"
        ).fetchone()
        assert tuple(station) == (0, "disabled")
        assert database.execute("SELECT COUNT(*) FROM users").fetchone()[0] == original_accounts

def test_board_reply_guest_read_and_restart(app, initialized):
    response=initialized.post("/board/new",data={"csrf_token":csrf(initialized,"/board/new"),"title":"First transmission","body":"Hello local network."},follow_redirects=True)
    assert b"First transmission" in response.data
    initialized.post("/board/1",data={"csrf_token":csrf(initialized,"/board/1"),"body":"Signal received."})
    initialized.post("/logout",data={"csrf_token":csrf(initialized,"/terminal")})
    board = initialized.get("/board")
    assert b"First transmission" in board.data
    assert b"sysop - [" in board.data
    assert b"Signal received" in initialized.get("/board/1").data
    restarted=create_app({"TESTING":True,"DATA_DIR":app.config["DATA_DIR"],"SECRET_KEY":"new-process"}).test_client()
    assert b"First transmission" in restarted.get("/board").data

def test_admin_delete_and_csrf(app, initialized):
    initialized.post("/board/new",data={"csrf_token":csrf(initialized,"/board/new"),"title":"Moderate me","body":"Content"})
    response=initialized.post("/board/1/delete",data={"csrf_token":csrf(initialized,"/board/1")},follow_redirects=True)
    assert b"removed by SYSOP" in response.data
    with app.app_context(): assert get_db().execute("SELECT COUNT(*) FROM posts").fetchone()[0]==0
    assert initialized.post("/board/new",data={"title":"No token","body":"Rejected"}).status_code==400

