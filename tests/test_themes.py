import json
from io import BytesIO

import pytest

from stationnet import create_app, themes
from stationnet.db import get_db


def _client(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES('Test Station','Test','TST-1')")
        sysop = db.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('sysop','SYS-1','unused',1)"
        ).lastrowid
        member = db.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('member','MEM-1','unused',0)"
        ).lastrowid
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = sysop
        session["csrf_token"] = "test-token"
    return app, client, sysop, member


def _theme(**changes):
    document = {
        "id": "ocean-night", "name": "Ocean Night", "author": "Theme Maker",
        "site": {key: "#123456" for key in themes.SITE_KEYS},
        "masthead": {key: "#abcdef" for key in themes.MASTHEAD_KEYS},
    }
    document.update(changes)
    return json.dumps(document).encode("utf-8")


def _import(client, payload):
    return client.post("/sysop/settings/theme", data={
        "csrf_token": "test-token", "theme_file": (BytesIO(payload), "theme.json"),
    }, follow_redirects=True)


def test_custom_theme_import_and_member_selection(tmp_path):
    app, client, _, member = _client(tmp_path)
    assert (tmp_path / "themes" / "template-theme.json").is_file()
    response = _import(client, _theme())
    assert b"Imported theme Ocean Night" in response.data
    assert (tmp_path / "themes" / "ocean-night.json").is_file()
    assert b"[CUSTOM] Ocean Night" in response.data
    saved = client.post("/sysop/settings", data={
        "csrf_token": "test-token", "name": "Test Station",
        "site_theme": "custom-ocean-night", "masthead_theme": "custom-ocean-night",
    }, follow_redirects=True)
    assert b"Station parameters updated" in saved.data
    stylesheet = client.get("/masthead.css").get_data(as_text=True)
    assert "--green:#123456" in stylesheet
    assert "--masthead-color:#abcdef" in stylesheet
    with client.session_transaction() as session:
        session["user_id"] = member
    edit = client.get(f"/users/{member}/edit")
    assert b"[CUSTOM] Ocean Night" in edit.data
    save = client.post(f"/users/{member}/themes", data={
        "csrf_token": "test-token", "site_theme": "custom-ocean-night",
        "masthead_theme": "custom-ocean-night",
    })
    assert save.status_code == 302
    with app.app_context():
        row = get_db().execute("SELECT site_theme,masthead_theme FROM users WHERE id=?", (member,)).fetchone()
        assert tuple(row) == ("custom-ocean-night", "custom-ocean-night")


def test_station_theme_policy_preserves_and_restores_custom_preferences(tmp_path):
    app, client, sysop, member = _client(tmp_path)
    assert b"Imported theme" in _import(client, _theme()).data
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET site_theme='amber',masthead_theme='violet',header_color='#123456'")
        db.commit()
    with client.session_transaction() as session:
        session["user_id"] = member
    assert client.post(f"/users/{member}/themes", data={
        "csrf_token": "test-token", "site_theme": "custom-ocean-night",
        "masthead_theme": "custom-ocean-night",
    }).status_code == 302
    css = client.get("/masthead.css").get_data(as_text=True)
    assert "--green:#123456" in css
    assert "--masthead-color:#abcdef" in css

    with client.session_transaction() as session:
        session["user_id"] = sysop
    response = client.post("/sysop/settings", data={
        "csrf_token": "test-token", "name": "Test Station",
        "personal_themes_control": "1", "site_theme": "amber",
        "masthead_theme": "violet", "header_color": "#123456",
    }, follow_redirects=True)
    assert b"Station parameters updated" in response.data
    with client.session_transaction() as session:
        session["user_id"] = member
    edit = client.get(f"/users/{member}/edit")
    assert b"Theme preferences" not in edit.data
    assert b'name="site_theme"' not in edit.data
    css = client.get("/masthead.css").get_data(as_text=True)
    with app.app_context():
        assert "--green:" + themes.palette("amber")["green"] in css
    assert "--masthead-color:#123456" in css
    assert client.post(f"/users/{member}/themes", data={
        "csrf_token": "test-token", "site_theme": "cyan", "masthead_theme": "cyan",
    }).status_code == 403
    with app.app_context():
        row = get_db().execute("SELECT site_theme,masthead_theme FROM users WHERE id=?", (member,)).fetchone()
        assert tuple(row) == ("custom-ocean-night", "custom-ocean-night")

    with client.session_transaction() as session:
        session["user_id"] = sysop
    response = client.post("/sysop/settings", data={
        "csrf_token": "test-token", "name": "Test Station",
        "personal_themes_control": "1", "personal_themes_enabled": "on",
        "site_theme": "amber", "masthead_theme": "violet",
        "header_color": "#123456",
    }, follow_redirects=True)
    assert b"Station parameters updated" in response.data
    with client.session_transaction() as session:
        session["user_id"] = member
    edit = client.get(f"/users/{member}/edit")
    assert b"Theme preferences" in edit.data
    css = client.get("/masthead.css").get_data(as_text=True)
    assert "--green:#123456" in css
    assert "--masthead-color:#abcdef" in css


def test_partial_themes_and_invalid_uploads(tmp_path):
    app, client, _, _ = _client(tmp_path)
    site = json.loads(_theme(id="site-only"))
    del site["masthead"]
    assert b"Imported theme" in _import(client, json.dumps(site).encode()).data
    masthead = json.loads(_theme(id="masthead-only"))
    del masthead["site"]
    assert b"Imported theme" in _import(client, json.dumps(masthead).encode()).data
    with app.app_context():
        assert themes.valid("custom-site-only", "site")
        assert not themes.valid("custom-site-only", "masthead")
        assert themes.valid("custom-masthead-only", "masthead")
        assert not themes.valid("custom-masthead-only", "site")
        assert themes.effective(
            {"site_theme": "amber", "masthead_theme": "violet"},
            {"site_theme": "custom-masthead-only", "masthead_theme": "custom-site-only"},
        ) == ("amber", "violet")
    invalid = json.loads(_theme(id="bad-css"))
    invalid["site"]["green"] = "red;body{display:none}"
    assert b"six-digit hex" in _import(client, json.dumps(invalid).encode()).data
    assert not (tmp_path / "themes" / "bad-css.json").exists()
    assert b"already installed" in _import(client, json.dumps(site).encode()).data


def test_annotated_template_can_be_imported(tmp_path):
    _, client, _, _ = _client(tmp_path)
    template = tmp_path / "themes" / "template-theme.json"
    document = json.loads(template.read_text(encoding="utf-8"))
    document.update(id="annotated-theme", name="Annotated Theme", author="Theme Maker")
    response = _import(client, json.dumps(document).encode("utf-8"))
    assert b"Imported theme Annotated Theme" in response.data
    saved = json.loads((tmp_path / "themes" / "annotated-theme.json").read_text(encoding="utf-8"))
    assert set(saved["site"]) == set(themes.SITE_KEYS)
    assert set(saved["masthead"]) == set(themes.MASTHEAD_KEYS)

    document["site"]["unexpected"] = "#123456"
    with pytest.raises(themes.ThemeError):
        themes.parse(json.dumps(document))


def test_remove_custom_theme_requires_confirmation_and_resets_selections(tmp_path):
    app, client, sysop, member = _client(tmp_path)
    assert b"Imported theme" in _import(client, _theme()).data
    path = tmp_path / "themes" / "ocean-night.json"
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET site_theme=?,masthead_theme=?",
                   ("custom-ocean-night", "custom-ocean-night"))
        db.execute("UPDATE users SET site_theme=?,masthead_theme=? WHERE id=?",
                   ("custom-ocean-night", "custom-ocean-night", member))
        db.commit()
    route = "/sysop/settings/theme/remove"
    response = client.post(route, data={"csrf_token": "test-token", "theme_id": "ocean-night"},
                           follow_redirects=True)
    assert b"Check the confirmation box" in response.data
    assert path.is_file()
    response = client.post(route, data={"csrf_token": "test-token", "theme_id": "cyan",
                                        "confirm_remove": "on"}, follow_redirects=True)
    assert b"Select an installed custom theme" in response.data
    assert path.is_file()
    with client.session_transaction() as session:
        session["user_id"] = member
    assert b"SYSOP authorization required" in client.post(
        route, data={"csrf_token": "test-token", "theme_id": "ocean-night",
                     "confirm_remove": "on"}, follow_redirects=True).data
    assert path.is_file()
    with client.session_transaction() as session:
        session["user_id"] = sysop
    response = client.post(route, data={"csrf_token": "test-token", "theme_id": "ocean-night",
                                        "confirm_remove": "on"}, follow_redirects=True)
    assert b"Removed theme Ocean Night" in response.data
    assert not path.exists()
    assert b"REMOVE CUSTOM COLOUR THEME" not in response.data
    with app.app_context():
        db = get_db()
        station = db.execute("SELECT site_theme,masthead_theme FROM station").fetchone()
        user = db.execute("SELECT site_theme,masthead_theme FROM users WHERE id=?", (member,)).fetchone()
        assert tuple(station) == ("cyan", "cyan")
        assert tuple(user) == (None, None)


def _variables(stylesheet):
    root = stylesheet.split("}", 1)[0].split("{", 1)[1]
    return dict(item.split(":", 1) for item in root.split(";") if item)


LEGACY_SURFACES = ("#030703", "#001b08", "#071107", "#020702", "#010701", "#092010", "#10351c",
                   "#10241a", "#001506", "#001505", "#001005", "#064d1d", "#00330d")


def test_default_theme_keeps_legacy_surfaces(tmp_path):
    app, *_ = _client(tmp_path)
    with app.app_context():
        variables = _variables(themes.css("cyan", "cyan"))
    assert variables["--surface-field"] == "#030703"
    assert variables["--surface-button"] == "#001b08"
    assert variables["--surface-selected"] == "#10351c"
    assert variables["--border-soft"] == "#064d1d"
    assert variables["--theme-ink"] == "#00330d"
    assert variables["--surface-tint-rgb"] == "0,12,3"
    assert variables["--surface-accent-rgb"] == "0,10,8"


def test_non_green_theme_surfaces_follow_its_palette(tmp_path):
    app, *_ = _client(tmp_path)
    with app.app_context():
        stylesheet = themes.css("crimson", "crimson")
    root = stylesheet.split("}", 1)[0]
    assert not any(color in root for color in LEGACY_SURFACES)
    variables = _variables(stylesheet)
    for name in ("--surface-button", "--surface-selected", "--surface-soft-hover", "--border-soft"):
        red, green, _ = themes._rgb(variables[name])
        assert red > green, name


def test_custom_field_and_button_are_kept_unless_template_defaults(tmp_path):
    app, client, *_ = _client(tmp_path)
    _import(client, _theme())
    site = {key: "#f05c6a" for key in themes.SITE_KEYS}
    site.update(field="#030703", button="#001b08")
    _import(client, _theme(id="red-template", name="Red Template", site=site))
    with app.app_context():
        custom = _variables(themes.css("custom-ocean-night", "cyan"))
        template = _variables(themes.css("custom-red-template", "cyan"))
    assert custom["--surface-field"] == "#123456"
    assert custom["--surface-button"] == "#123456"
    assert template["--surface-field"] != "#030703"
    assert template["--surface-button"] != "#001b08"
    assert themes._rgb(template["--surface-button"])[0] > themes._rgb(template["--surface-button"])[1]
