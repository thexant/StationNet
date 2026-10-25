import json
from io import BytesIO

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
