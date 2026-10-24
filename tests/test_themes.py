"""Palette inheritance, saved preferences, and legacy banner compatibility."""

import sqlite3

from stationnet import create_app
from stationnet.db import _migrate_themes, get_db
from stationnet.themes import THEMES, palette


def test_themes_are_independent_and_persist(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(id,name,description,callsign,header_color) "
                   "VALUES(1,'Test Station','','TST-001','#123456')")
        db.execute("INSERT INTO users(id,username,callsign,password_hash,is_admin) "
                   "VALUES(1,'sysop','SYS-001','test',1)")
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["csrf_token"] = "theme-test"

    css = client.get("/masthead.css").text
    assert "--green:#00ed45" in css
    assert "--masthead-color:#123456" in css

    response = client.post("/sysop/settings", data={
        "csrf_token": "theme-test", "name": "Test Station", "description": "",
        "brand_tagline": "Test station", "header_font": "small",
        "header_font_size": "100", "header_color": "#123456",
        "site_theme": "electric-blue", "masthead_theme": "crimson",
        "upload_permission": "members", "file_storage_limit_mb": "100",
        "file_size_limit_mb": "10",
    })
    assert response.status_code == 302
    css = client.get("/masthead.css").text
    assert "--green:#54a8ff" in css
    assert "--masthead-color:#f05c6a" in css
    with app.app_context():
        station = get_db().execute("SELECT site_theme,masthead_theme,header_color FROM station").fetchone()
        assert tuple(station) == ("electric-blue", "crimson", "")

    response = client.post("/users/1/themes", data={
        "csrf_token": "theme-test", "site_theme": "violet",
        "masthead_theme": "",
    })
    assert response.status_code == 302
    css = client.get("/masthead.css").text
    assert "--green:#b38aff" in css
    assert "--masthead-color:#f05c6a" in css

    client.post("/users/1/themes", data={
        "csrf_token": "theme-test", "site_theme": "",
        "masthead_theme": "monochrome",
    })
    css = client.get("/masthead.css").text
    assert "--green:#54a8ff" in css
    assert "--masthead-color:#e5e5e5" in css

    client.post("/users/1/themes", data={
        "csrf_token": "theme-test", "site_theme": "invalid-css;body{}",
        "masthead_theme": "amber",
    })
    with app.app_context():
        saved = get_db().execute("SELECT site_theme,masthead_theme FROM users WHERE id=1").fetchone()
        assert tuple(saved) == (None, "monochrome")


def test_every_palette_has_safe_css():
    from stationnet.themes import css
    original_surfaces = {key: palette("cyan")[key] for key in ("black", "field", "button")}
    for theme_id in THEMES:
        assert {key: palette(theme_id)[key] for key in original_surfaces} == original_surfaces
        sheet = css(theme_id, theme_id)
        assert f".theme-swatch--{theme_id}" in sheet
        assert "--masthead-color:#" in sheet
        assert f"--masthead-shadow:{palette(theme_id)['masthead-shadow']}" in sheet
        assert "--surface-field:#" in sheet
        assert "color-mix" not in sheet


def test_legacy_rows_inherit_original_palette():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript("CREATE TABLE station(id INTEGER,header_color TEXT);"
                           "CREATE TABLE users(id INTEGER);"
                           "INSERT INTO station VALUES(1,'#123456');"
                           "INSERT INTO users VALUES(1);")
    _migrate_themes(database)
    station = database.execute("SELECT site_theme,masthead_theme,header_color FROM station").fetchone()
    user = database.execute("SELECT site_theme,masthead_theme FROM users").fetchone()
    assert tuple(station) == ("cyan", "cyan", "#123456")
    assert tuple(user) == (None, None)


def test_masthead_shadows_are_distinct_and_visible_on_dark_background():
    def luminance(value):
        channels = [int(value[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
                  for channel in channels]
        return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    assert palette("cyan")["masthead-shadow"] == "#d829be"
    for theme_id in THEMES:
        colors = palette(theme_id)
        assert colors["masthead-shadow"] != colors["masthead"]
        assert (luminance(colors["masthead-shadow"]) + 0.05) / 0.05 >= 3
