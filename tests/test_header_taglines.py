import json
import sqlite3

from stationnet import create_app
from stationnet.db import _migrate_header_taglines, get_db


def _client(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign) VALUES('Test Station','Test','TST-1')"
        )
        sysop = database.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) "
            "VALUES('sysop','SYS-1','unused',1)"
        ).lastrowid
        database.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = sysop
        session["csrf_token"] = "test-token"
    return app, client


def _save(client, texts, colors=None, effects=None, modes=None):
    return client.post("/sysop/settings", data={
        "csrf_token": "test-token",
        "settings_section": "header_taglines",
        "tagline_text": texts,
        "tagline_color_mode": modes or ["inherit"] * len(texts),
        "tagline_color_value": colors or ["#e6a431"] * len(texts),
        "tagline_effect": effects or ["none"] * len(texts),
    }, follow_redirects=True)


def test_upgrade_keeps_legacy_tagline_and_is_idempotent():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute("CREATE TABLE station(id INTEGER PRIMARY KEY, brand_tagline TEXT NOT NULL)")
    database.execute("INSERT INTO station VALUES(1, '✦ Legacy BBS ✦')")
    _migrate_header_taglines(database)
    _migrate_header_taglines(database)
    row = database.execute("SELECT header_taglines_json FROM station").fetchone()
    assert json.loads(row[0]) == [{"text": "✦ Legacy BBS ✦", "color": "", "effect": "none"}]


def test_save_reorder_delete_and_unicode_preview_controls(tmp_path):
    app, client = _client(tmp_path)
    settings = client.get("/sysop/settings").get_data(as_text=True)
    assert "HEADER TAGLINES" in settings
    assert "data-tagline-preview-button" in settings
    assert "H7/dS! // YOUR INDEPENDENT COMMUNITY STATION //" in settings

    response = _save(client, ["✦ First ✦", "☻ Second ★"],
                     colors=["#e6a431", "#AB12CD"],
                     effects=["none", "typewriter"],
                     modes=["inherit", "custom"])
    assert "Header taglines updated." in response.get_data(as_text=True)
    with app.app_context():
        row = get_db().execute("SELECT brand_tagline,header_taglines_json FROM station").fetchone()
        assert row["brand_tagline"] == "✦ First ✦"
        assert json.loads(row["header_taglines_json"]) == [
            {"text": "✦ First ✦", "color": "", "effect": "none"},
            {"text": "☻ Second ★", "color": "#ab12cd", "effect": "typewriter"},
        ]
    assert "☻ Second ★" in client.get("/sysop/settings").get_data(as_text=True)

    _save(client, ["☻ Second ★"], colors=["#AB12CD"], effects=["typewriter"], modes=["custom"])
    with app.app_context():
        assert json.loads(get_db().execute("SELECT header_taglines_json FROM station").fetchone()[0]) == [
            {"text": "☻ Second ★", "color": "#ab12cd", "effect": "typewriter"}
        ]
    page = client.get("/").get_data(as_text=True)
    assert "tagline-effect-typewriter" in page
    assert ".brand-tag-current { color: #ab12cd; }" in page
    assert "data-typewriter-visible" in page

    _save(client, [])
    with app.app_context():
        assert get_db().execute("SELECT header_taglines_json FROM station").fetchone()[0] == "[]"
    assert 'class="brand-tag' not in client.get("/").get_data(as_text=True)


def test_navigation_avoids_immediate_repeat_and_rejects_bad_entries(tmp_path):
    app, client = _client(tmp_path)
    _save(client, ["ONE", "TWO"], effects=["blink", "rainbow"])
    previous = None
    for _ in range(8):
        page = client.get("/").get_data(as_text=True)
        current = "ONE" if '>ONE</span>' in page else "TWO"
        assert current != previous
        previous = current
    with app.app_context():
        original = get_db().execute("SELECT header_taglines_json FROM station").fetchone()[0]
    invalid = _save(client, ["Okay", ""], effects=["none", "glow"])
    assert "between 1 and 160" in invalid.get_data(as_text=True)
    invalid = _save(client, ["Okay"], colors=["#bad"], modes=["custom"])
    assert "six-digit hexadecimal" in invalid.get_data(as_text=True)
    invalid = _save(client, ["Okay"], effects=["script"])
    assert "valid tagline animation" in invalid.get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT header_taglines_json FROM station").fetchone()[0] == original


def test_css_respects_reduced_motion(tmp_path):
    _, client = _client(tmp_path)
    css = client.get("/static/station.css").get_data(as_text=True)
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".tagline-effect-blink, .tagline-effect-rainbow, .tagline-effect-glow { animation: none; }" in css
