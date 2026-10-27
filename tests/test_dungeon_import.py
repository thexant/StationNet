"""SYSOP dungeon imports become available to new runs without a restart."""
import json
import shutil
import uuid
from io import BytesIO
from pathlib import Path

import pytest

from stationnet import create_app, character_creation, dungeons, mud
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


def test_dungeon_bundled_origins_optional_cleanup_and_fallback(data_dir):
    tmp_path, apps = data_dir
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    apps.append(app)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_procedural_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        sysop = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('sysop','SYS','unused',1)").lastrowid
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = sysop
        session["csrf_token"] = "test-token"
    templates = Path(character_creation.__file__).with_name("mud_origin_templates")
    theme = _theme()
    theme["origins"] = {
        "races": [json.loads((templates / "race.json").read_text())],
        "classes": [json.loads((templates / "class.json").read_text())],
    }
    invalid = json.loads(json.dumps(theme))
    invalid["origins"]["classes"][0]["skill"] = "unknown"
    assert b"Dungeon theme import failed" in _upload(client, invalid).data
    assert not (tmp_path / "dungeons" / "custom-cavern.json").exists()
    assert b"Imported dungeon theme" in _upload(client, theme).data
    settings = client.get("/sysop/settings").data
    assert b"dungeon-remove-origins" in settings
    assert b"Riverfolk" in settings and b"Pathfinder" in settings
    race_key = dungeons.bundled_origin_key(theme["id"], "races", "riverfolk")
    class_key = dungeons.bundled_origin_key(theme["id"], "classes", "pathfinder")
    with app.app_context():
        db = get_db()
        assert dungeons.theme_origin_keys(db, theme["id"]) == {
            "races": {race_key}, "classes": {class_key}}
    preserved = client.post("/sysop/settings/dungeons/remove", data={
        "csrf_token": "test-token", "theme_id": theme["id"],
        "confirm_remove": "on"}, follow_redirects=True)
    assert b"preserved: 1 races, 1 classes" in preserved.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM mud_origin_definitions "
                                "WHERE key IN (?,?)", (race_key, class_key)).fetchone()[0] == 2
    assert b"Imported dungeon theme" in _upload(client, theme).data
    with app.app_context():
        db = get_db()
        for kind in character_creation.KINDS:
            db.execute("UPDATE mud_origin_definitions SET enabled=0 WHERE kind=?", (kind,))
            for entry in character_creation.catalog(db)[kind]:
                if not entry["imported"]:
                    db.execute("INSERT INTO mud_origin_definitions(kind,key,enabled) VALUES(?,?,0) "
                               "ON CONFLICT(kind,key) DO UPDATE SET enabled=0",
                               (kind, entry["key"]))
        db.execute("UPDATE mud_origin_definitions SET enabled=1 WHERE key IN (?,?)", (race_key, class_key))
        db.commit()
    response = client.post("/sysop/settings/dungeons/remove", data={
        "csrf_token": "test-token", "theme_id": theme["id"],
        "confirm_remove": "on", "remove_origins": "on"}, follow_redirects=True)
    assert b"Built-in races and classes restored" in response.data
    with app.app_context():
        db = get_db()
        assert character_creation.active_counts(db)["races"] > 0
        assert character_creation.active_counts(db)["classes"] > 0
        assert db.execute("SELECT 1 FROM mud_origin_definitions WHERE key IN (?,?)",
                          (race_key, class_key)).fetchone() is None


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
    assert (tmp_path / "dungeons" / "README_DUNGEON_THEMES.md").is_file()
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


def test_sysop_can_disable_and_reenable_built_in_and_custom_themes(data_dir):
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
        member = db.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM','unused')"
        ).lastrowid
        db.execute("UPDATE mud_daily_dungeon_settings SET eligible_theme_ids_json=? WHERE id=1",
                   (json.dumps(["desert"]),))
        db.commit()

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = sysop
        session["csrf_token"] = "test-token"
    assert b"Imported dungeon theme" in _upload(client, _theme()).data
    custom_path = tmp_path / "dungeons" / "custom-cavern.json"
    original_custom = custom_path.read_bytes()
    route = "/sysop/settings/dungeons/availability"

    with client.session_transaction() as session:
        session["user_id"] = member
    denied = client.post(route, data={"csrf_token": "test-token", "theme_id": "desert",
                                      "enabled": "0"}, follow_redirects=True)
    assert b"SYSOP authorization required" in denied.data
    with app.app_context():
        assert "desert" in dungeons.playable_templates(get_db())

    with client.session_transaction() as session:
        session["user_id"] = sysop
    for theme_id in ("desert", "custom-cavern"):
        response = client.post(route, data={"csrf_token": "test-token", "theme_id": theme_id,
                                            "enabled": "0"}, follow_redirects=True)
        assert b"DISABLED" in response.data
    with app.app_context():
        db = get_db()
        assert {"desert", "custom-cavern"}.issubset(dungeons.discover_templates())
        assert "desert" not in dungeons.playable_templates(db)
        assert "custom-cavern" not in dungeons.playable_templates(db)
        assert json.loads(db.execute(
            "SELECT eligible_theme_ids_json FROM mud_daily_dungeon_settings WHERE id=1"
        ).fetchone()["eligible_theme_ids_json"]) == ["desert"]
    assert custom_path.read_bytes() == original_custom

    # A second app instance exercises the same database initialization used on restart.
    restarted = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    apps.append(restarted)
    with restarted.app_context():
        assert not dungeons.theme_is_enabled("desert", get_db())
        assert "desert" in dungeons.discover_templates()

    response = client.post(route, data={"csrf_token": "test-token", "theme_id": "desert",
                                        "enabled": "1"}, follow_redirects=True)
    assert b"Dungeon theme Desert Temples enabled" in response.data
    with app.app_context():
        assert "desert" in dungeons.playable_templates(get_db())


def test_no_enabled_dungeon_themes_is_handled_and_existing_run_survives(data_dir):
    tmp_path, apps = data_dir
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    apps.append(app)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,"
                   "mud_procedural_enabled) VALUES('Test','Test','TST',1,1)")
        player = db.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('player','PLY','unused')"
        ).lastrowid
        db.execute("UPDATE mud_characters SET creation_state='complete' WHERE user_id=?", (player,))
        mud._set_player_location(db, player, "procedural", "portal", mud.PORTAL_ROOM_ID)
        user = db.execute("SELECT * FROM users WHERE id=?", (player,)).fetchone()
        mud._expedition_action(db, user, {"theme_id": "desert", "difficulty": 1, "size": 8})
        existing_id = mud._active_expedition(db, player)["id"]
        for theme_id in dungeons.discover_templates():
            dungeons.set_theme_enabled(db, theme_id, False)
        db.commit()

        assert dungeons.playable_templates(db) == {}
        assert mud._active_expedition(db, player)["id"] == existing_id
        assert db.execute(
            "SELECT COUNT(*) AS amount FROM mud_dungeon_rooms WHERE instance_id=?", (existing_id,)
        ).fetchone()["amount"] > 0
        daily = mud.ensure_daily_dungeon(db)
        assert daily["status"] == "failed"
        assert "No eligible playable dungeon themes" in daily["error_message"]

        second = db.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('second','SEC','unused')"
        ).lastrowid
        db.execute("UPDATE mud_characters SET creation_state='complete' WHERE user_id=?", (second,))
        mud._set_player_location(db, second, "procedural", "portal", mud.PORTAL_ROOM_ID)
        second_user = db.execute("SELECT * FROM users WHERE id=?", (second,)).fetchone()
        with pytest.raises(mud.ActionError, match="No playable dungeon themes"):
            mud._expedition_action(db, second_user, {})

        dungeons.set_theme_enabled(db, "desert", True)
        db.commit()
        mud._expedition_action(db, second_user,
                               {"theme_id": "desert", "difficulty": 1, "size": 8})
        assert mud._active_expedition(db, second)["theme_id"] == "desert"
