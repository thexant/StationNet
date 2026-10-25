"""SYSOP dungeon imports become available to new runs without a restart."""
import json
import shutil
import uuid
from io import BytesIO
from pathlib import Path

import pytest

from stationnet import create_app, dungeons, mud
from stationnet.db import get_db


def _theme():
    source = Path(dungeons.__file__).with_name("dungeon_templates") / "starter-theme.json"
    theme = json.loads(source.read_text(encoding="utf-8"))
    theme.update(id="custom-cavern", name="Custom Cavern", author="Test Author")
    return theme


def _upload(client, theme):
    return client.post("/sysop/settings/dungeons/import", data={
        "csrf_token": "test-token",
        "dungeon_file": (BytesIO(json.dumps(theme).encode("utf-8")), "cavern.json"),
    }, follow_redirects=True)


@pytest.fixture
def data_dir():
    path = Path(__file__).parent / f"_dungeon_import_{uuid.uuid4().hex}"
    path.mkdir()
    apps = []
    try:
        yield path, apps
    finally:
        for app in apps:
            coordinator = app.extensions["broadcast_coordinator"]
            coordinator._closed = True
            coordinator._timer.cancel()
        shutil.rmtree(path)


def test_sysop_import_is_immediately_playable(data_dir):
    tmp_path, apps = data_dir
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    apps.append(app)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,"
                   "mud_procedural_enabled) VALUES('Test','Test','TST',1,1)")
        sysop = db.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) "
            "VALUES('sysop','SYS','unused',1)"
        ).lastrowid
        player = db.execute(
            "INSERT INTO users(username,callsign,password_hash) "
            "VALUES('player','PLY','unused')"
        ).lastrowid
        db.execute("UPDATE mud_characters SET creation_state='complete' WHERE user_id=?", (player,))
        mud._set_player_location(db, player, "procedural", "portal", mud.PORTAL_ROOM_ID)
        db.commit()
        assert "custom-cavern" not in dungeons.discover_templates()
    assert (tmp_path / "dungeons" / "README.md").is_file()
    assert (tmp_path / "dungeons" / "_template.json").is_file()

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = sysop
        session["csrf_token"] = "test-token"
    template_response = client.get("/sysop/settings/dungeons/template")
    assert template_response.status_code == 200
    assert json.loads(template_response.data)["author"] == "[Your name]"
    response = _upload(client, _theme())
    assert b"Imported dungeon theme Custom Cavern by Test Author" in response.data
    assert (tmp_path / "dungeons" / "custom-cavern.json").is_file()
    assert b"Custom Cavern (custom-cavern@1.0.0)" in response.data
    with app.app_context():
        db = get_db()
        assert "custom-cavern" in dungeons.discover_templates()
        user = db.execute("SELECT * FROM users WHERE id=?", (player,)).fetchone()
        mud._expedition_action(db, user, {"theme_id": "custom-cavern", "difficulty": 1, "size": 8})
        run = mud._active_expedition(db, player)
        assert run is not None
        instance = db.execute("SELECT theme_id FROM mud_procedural_instances WHERE id=?", (run["id"],)).fetchone()
        assert instance["theme_id"] == "custom-cavern"


def test_import_rejects_invalid_duplicate_and_non_sysop(data_dir):
    tmp_path, apps = data_dir
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    apps.append(app)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES('Test','Test','TST')")
        sysop = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('sysop','SYS','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) "
                            "VALUES('member','MEM','unused')").lastrowid
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = member
        session["csrf_token"] = "test-token"
    assert b"SYSOP authorization required" in _upload(client, _theme()).data
    assert not (tmp_path / "dungeons" / "custom-cavern.json").exists()
    with client.session_transaction() as session:
        session["user_id"] = sysop
    missing_author = _theme()
    del missing_author["author"]
    assert b"$.author" in _upload(client, missing_author).data
    assert not (tmp_path / "dungeons" / "custom-cavern.json").exists()
    duplicate = _theme()
    duplicate["id"] = "desert"
    assert b"already installed" in _upload(client, duplicate).data
    assert b"Imported dungeon theme" in _upload(client, _theme()).data
    assert b"already installed" in _upload(client, _theme()).data


def test_remove_custom_dungeon_requires_confirmation_and_preserves_bundled(data_dir):
    tmp_path, apps = data_dir
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    apps.append(app)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES('Test','Test','TST')")
        sysop = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('sysop','SYS','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) "
                            "VALUES('member','MEM','unused')").lastrowid
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = sysop
        session["csrf_token"] = "test-token"
    assert b"Imported dungeon theme" in _upload(client, _theme()).data
    path = tmp_path / "dungeons" / "custom-cavern.json"
    bundled = tmp_path / "dungeon_themes" / "desert.json"
    with app.app_context():
        db = get_db()
        db.execute("UPDATE mud_daily_dungeon_settings SET eligible_theme_ids_json=? WHERE id=1",
                   (json.dumps(["custom-cavern", "desert"]),))
        db.commit()
    route = "/sysop/settings/dungeons/remove"
    response = client.post(route, data={"csrf_token": "test-token", "theme_id": "custom-cavern"},
                           follow_redirects=True)
    assert b"Check the confirmation box" in response.data
    assert path.is_file()
    response = client.post(route, data={"csrf_token": "test-token", "theme_id": "desert",
                                        "confirm_remove": "on"}, follow_redirects=True)
    assert b"Select an installed custom dungeon theme" in response.data
    assert bundled.is_file() and path.is_file()
    with client.session_transaction() as session:
        session["user_id"] = member
    assert b"SYSOP authorization required" in client.post(
        route, data={"csrf_token": "test-token", "theme_id": "custom-cavern",
                     "confirm_remove": "on"}, follow_redirects=True).data
    assert path.is_file()
    with client.session_transaction() as session:
        session["user_id"] = sysop
    response = client.post(route, data={"csrf_token": "test-token", "theme_id": "custom-cavern",
                                        "confirm_remove": "on"}, follow_redirects=True)
    assert b"Removed dungeon theme Custom Cavern" in response.data
    assert not path.exists()
    assert bundled.is_file()
    with app.app_context():
        assert "custom-cavern" not in dungeons.discover_templates()
        row = get_db().execute(
            "SELECT eligible_theme_ids_json FROM mud_daily_dungeon_settings WHERE id=1"
        ).fetchone()
        assert json.loads(row["eligible_theme_ids_json"]) == ["desert"]
