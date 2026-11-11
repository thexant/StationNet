"""Sysop Settings control center: structure, anchors, and save routing."""
from collections import Counter
from html.parser import HTMLParser

import pytest

from stationnet import create_app
from stationnet.db import get_db
from stationnet.modules import ALL_MODULES

TOKEN = "settings-test"

# Every fragment a save route redirects to must exist on the page, or the
# sysop lands on the wrong category after saving.
REDIRECT_ANCHORS = (
    "module-availability", "station-parameters", "station-identity", "landing-message", "login-sequence",
    "station-icon", "station-themes", "masthead-banner", "header-taglines", "theme-library",
    "station-midi", "access-policy", "invitations", "events-service", "user-feeds",
    "office-suite", "file-archive", "videos-service", "library-service", "arcade-service",
    "tv-service", "imageboard-service", "kiwix-service", "mud-service", "mud-worlds",
    "mud-origins", "mud-skills", "mud-abilities", "dungeon-themes", "mud-daily",
    "radio-storage", "radio-spectrum", "broadcast-frequencies", "radio-permissions",
    "radio-users",
)
# Elements settings-mud.js and station.js look up by id.
SCRIPT_IDS = (
    "world-import-form", "world-import-file", "world-import-problems", "world-import-upgrade",
    "world-import-changes", "world-import-mode", "world-import-manual",
    "world-import-bundled-races", "world-import-bundled-classes", "world-import-bundled-skills",
    "world-export-id", "world-removal-status",
)


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = Counter()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids[attrs["id"]] += 1


@pytest.fixture
def setup(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    columns = ",".join(f"{name}_enabled" for name, _label in ALL_MODULES)
    with app.app_context():
        db = get_db()
        db.execute(f"INSERT INTO station(name,description,callsign,{columns}) "
                   f"VALUES('Test Station','About us','TST-1',{','.join('1' for _ in ALL_MODULES)})")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('admin','ADM-1','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) "
                            "VALUES('member','MEM-1','unused')").lastrowid
        db.commit()
    return app, app.test_client(), admin, member


def signin(client, user):
    with client.session_transaction() as session:
        session["user_id"] = user
        session["csrf_token"] = TOKEN


def station(app, *columns):
    with app.app_context():
        return get_db().execute(f"SELECT {','.join(columns)} FROM station").fetchone()


def post(client, **data):
    return client.post("/sysop/settings", data={"csrf_token": TOKEN, **data})


def test_page_renders_every_anchor_once(setup):
    app, client, admin, _member = setup
    signin(client, admin)
    response = client.get("/sysop/settings")
    assert response.status_code == 200
    parser = _Ids()
    parser.feed(response.get_data(as_text=True))
    duplicates = [key for key, count in parser.ids.items() if count > 1]
    assert duplicates == []
    for anchor in REDIRECT_ANCHORS + SCRIPT_IDS:
        assert parser.ids[anchor] == 1, anchor
    with app.app_context():
        groups = [row["id"] for row in get_db().execute("SELECT id FROM radio_permission_groups")]
    for group in groups:
        assert parser.ids[f"radio-group-{group}"] == 1
    assert parser.ids[f"radio-user-{admin}"] == 1


def test_page_keeps_key_controls(setup):
    _app, client, admin, _member = setup
    signin(client, admin)
    html = client.get("/sysop/settings").get_data(as_text=True)
    for marker in ("SAVE MODULE AVAILABILITY", 'name="tagline_text"', "data-banner-editor",
                   'data-theme-selector="masthead"', 'data-theme-selector="site"',
                   "COMMIT RADIO SPECTRUM", "SAVE DAILY CONFIGURATION", "data-settings-shell",
                   'name="upload_permission"', "OPEN TV MANAGEMENT"):
        assert marker in html, marker
    # Service on/off lives only on the Modules switchboard.
    assert html.count('name="videos_enabled"') == 1
    assert html.count('name="tv_enabled"') == 1


def test_non_sysop_is_refused(setup):
    _app, client, _admin, member = setup
    signin(client, member)
    assert client.get("/sysop/settings").status_code in (302, 403, 404)


def test_card_form_keeps_other_parameters(setup):
    app, client, admin, _member = setup
    signin(client, admin)
    response = post(client, return_to="station-themes", site_theme="cyan",
                    personal_themes_control="1")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/sysop/settings#station-themes")
    row = station(app, "name", "description", "personal_themes_enabled", "file_size_limit_mb")
    assert (row["name"], row["description"], row["personal_themes_enabled"]) == ("Test Station", "About us", 0)
    response = post(client, return_to="file-archive", upload_permission="admins",
                    file_storage_limit_mb="100", file_size_limit_mb="10")
    assert response.headers["Location"].endswith("#file-archive")
    row = station(app, "name", "upload_permission", "file_storage_limit_mb", "file_size_limit_mb")
    assert tuple(row) == ("Test Station", "admins", 100, 10)


def test_card_form_flashes_and_returns_on_error(setup):
    app, client, admin, _member = setup
    signin(client, admin)
    response = post(client, return_to="file-archive", upload_permission="admins",
                    file_storage_limit_mb="5", file_size_limit_mb="10")
    assert response.headers["Location"].endswith("#file-archive")
    page = client.get("/sysop/settings").get_data(as_text=True)
    assert "Per-file limit cannot exceed total archive storage." in page
    assert station(app, "upload_permission")[0] == "members"
    response = post(client, return_to="landing-message", landing_message_control="1",
                    landing_message_enabled="on", landing_heading="Hello", landing_message="Hi")
    assert response.headers["Location"].endswith("#landing-message")
    assert "Landing page message saved." in client.get("/sysop/settings").get_data(as_text=True)
    assert tuple(station(app, "landing_message_enabled", "landing_heading", "name")) == (1, "Hello", "Test Station")


def test_unknown_return_to_is_ignored(setup):
    _app, client, admin, _member = setup
    signin(client, admin)
    response = post(client, return_to="https://example.com", name="Renamed Station")
    assert response.headers["Location"].endswith("/sysop/settings")


def test_identity_card_requires_valid_name(setup):
    app, client, admin, _member = setup
    signin(client, admin)
    post(client, return_to="station-identity", name="X", description="")
    assert station(app, "name")[0] == "Test Station"
    post(client, return_to="station-identity", name="New Name", description="Fresh")
    assert tuple(station(app, "name", "description")) == ("New Name", "Fresh")


@pytest.mark.parametrize("section,fields,column", [
    ("videos", {"videos_mode": "curated", "videos_default_quota_mb": "5", "videos_max_upload_mb": "50"}, "videos_enabled"),
    ("library", {"library_mode": "curated", "library_default_quota_mb": "5", "library_max_upload_mb": "50"}, "library_enabled"),
    ("arcade", {"arcade_mode": "curated", "arcade_default_quota_mb": "5"}, "arcade_enabled"),
    ("tv", {"tv_max_upload_mb": "50", "tv_timezone": "Europe/London"}, "tv_enabled"),
    ("imageboard", {"imageboard_max_threads": "10", "imageboard_max_image_replies": "5"}, "imageboard_enabled"),
    ("events", {"events_create_permission": "members"}, "events_enabled"),
    ("office", {"office_nav_setting_present": "1"}, "office_enabled"),
])
def test_service_forms_preserve_module_switch(setup, section, fields, column):
    app, client, admin, _member = setup
    signin(client, admin)
    post(client, settings_section=section, preserve_enabled="1", **fields)
    assert station(app, column)[0] == 1
    # Older forms that post the checkbox still control the switch.
    post(client, settings_section=section, **fields)
    assert station(app, column)[0] == 0


def test_module_switchboard_saves(setup):
    app, client, admin, _member = setup
    signin(client, admin)
    response = post(client, settings_section="modules", videos_enabled="on", radio_enabled="on")
    assert response.headers["Location"].endswith("#module-availability")
    row = station(app, "videos_enabled", "radio_enabled", "tv_enabled", "mud_enabled")
    assert tuple(row) == (1, 1, 0, 0)


def test_membership_save_returns_to_member(setup):
    _app, client, admin, member = setup
    signin(client, admin)
    response = client.post(f"/sysop/radio/users/{member}", data={"csrf_token": TOKEN})
    assert response.headers["Location"].endswith(f"#radio-user-{member}")
