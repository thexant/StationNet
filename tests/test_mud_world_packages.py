"""Portable traditional worlds can be installed, selected, exported, and removed."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import time
import uuid
import zipfile

import pytest

from stationnet import mud_scope
from stationnet import create_app, character_creation, mud, mud_worlds
from stationnet.db import get_db


def _archive(world_id="harbor", *, bad_exit=False):
    document = {
        "id": world_id, "name": "Harbor World", "author": "Builder", "start_room": "dock",
        "rooms": [{"id": "dock", "name": "Old Dock", "description": "A tide rolls in."},
                  {"id": "street", "name": "Main Street", "description": "A market opens."}],
        "exits": [{"from": "dock", "to": "missing" if bad_exit else "street",
                   "name": "street", "direction": "north"}],
        "content": {
            "mud_item_definitions": [{"id": "bell", "name": "Harbor bell", "description": "A bronze bell.",
                                      "item_type": "misc", "properties_json": {"value": 2}},
                                     {"id": "crate", "name": "Wooden crate", "description": "A small crate.",
                                      "item_type": "container"}],
            "mud_item_instances": [{"id": "bell-on-dock", "definition_id": "bell",
                                    "container_id": "crate-on-dock"},
                                   {"id": "crate-on-dock", "definition_id": "crate", "room_id": "dock"}],
            "mud_conversations": [{"id": "greeting", "name": "Greeting"}],
            "mud_dialogue_nodes": [{"id": "hello", "conversation_id": "greeting", "node_key": "start",
                                    "body": "Welcome!", "is_start": 1}],
            "mud_npc_definitions": [{"id": "keeper", "name": "Keeper", "description": "A dock keeper.",
                                     "conversation_id": "greeting"}],
            "mud_npc_instances": [{"id": "keeper-on-dock", "definition_id": "keeper", "room_id": "dock"}],
            "mud_quests": [{"id": "visit", "name": "Visit the street", "description": "Explore the harbor."}],
            "mud_quest_objectives": [{"id": "street-visit", "quest_id": "visit", "objective_type": "visit_room",
                                      "target_id": "street", "description": "Visit the street"}],
            "mud_quest_givers": [{"quest_id": "visit", "npc_definition_id": "keeper"}],
            "mud_dialogue_responses": [{"id": "accept", "node_id": "hello", "response_text": "I'll go.",
                                        "accept_quest_id": "visit"}],
        },
    }
    return mud_worlds.package_bytes(document)


def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    return client


def _enable(client, world_id, enabled=True):
    return client.post("/sysop/settings/mud-worlds/enabled", data={
        "csrf_token": "test-token", "world_id": world_id, "enabled": "1" if enabled else "0"},
        follow_redirects=True)


def _offer(client, world_id, **choices):
    with client.application.app_context():
        skills = sorted(character_creation.default_availability()["skills"])
    return client.post(f"/sysop/settings/mud-worlds/{world_id}/availability", data={
        "csrf_token": "test-token", "skills": skills, **choices}, follow_redirects=True)


def _offered(db, world_id, kind):
    with mud_scope.use(world_id):
        return {item["key"] for item in character_creation.options(db)[kind]}


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / f"_world_package_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_worlds_run_side_by_side_and_disable_without_loss(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            sysop = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                               "VALUES('sysop','SYS','unused',1)").lastrowid
            player = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                "VALUES('player','PLY','unused')").lastrowid
            mud._set_player_location(db, player, "traditional", "world_room", "stationnet:start")
            db.commit()
        client = _client(app, sysop)
        assert (tmp_path / "mud_worlds" / "README_CUSTOM_WORLDS.md").exists()
        template = client.get("/sysop/settings/mud-worlds/template")
        assert template.status_code == 200
        assert mud_worlds.read_package(template.data)["start_room"] == "start"
        invalid = client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (BytesIO(_archive(bad_exit=True)), "bad.zip")},
            follow_redirects=True)
        assert b"Every exit must connect rooms" in invalid.data
        assert not (tmp_path / "mud_worlds" / "harbor.zip").exists()
        bad_content = mud_worlds.read_package(_archive())
        bad_content["content"]["mud_npc_instances"][0]["definition_id"] = "unknown"
        rejected = client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (
                BytesIO(mud_worlds.package_bytes(bad_content)), "bad-content.zip")},
            follow_redirects=True)
        assert b"references unknown mud_npc_definitions" in rejected.data
        with app.app_context():
            assert get_db().execute("SELECT 1 FROM mud_world_packages WHERE id='harbor'").fetchone() is None
        imported = client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (BytesIO(_archive()), "harbor.zip")},
            follow_redirects=True)
        assert b"Imported world Harbor World" in imported.data and b"disabled until you enable it" in imported.data
        assert (tmp_path / "mud_worlds" / "harbor.zip").exists()
        assert b"already installed" in client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (BytesIO(_archive()), "harbor.zip")},
            follow_redirects=True).data
        player_client = _client(app, player)
        assert player_client.get("/mud/w/harbor").status_code == 404
        assert b"Harbor World" not in player_client.get("/mud").data
        assert b"Harbor World is now open to players" in _enable(client, "harbor").data
        page = player_client.get("/mud").data
        assert b"Harbor World" in page and b"Station World" in page
        with app.app_context():
            db = get_db()
            # Enabling another world moves nobody: worlds are separate games.
            assert db.execute("SELECT location_key FROM mud_player_locations WHERE user_id=? "
                              "AND world_key='stationnet'", (player,)).fetchone()[0] == "stationnet:start"
            start_id = db.execute("SELECT start_room_id FROM mud_world_packages WHERE id='harbor'").fetchone()[0]
            assert db.execute("SELECT COUNT(*) FROM mud_npc_instances WHERE room_id=?", (start_id,)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM mud_item_instances WHERE room_id=?", (start_id,)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM mud_item_instances child "
                              "JOIN mud_item_instances container ON child.container_id=container.id "
                              "WHERE container.room_id=?", (start_id,)).fetchone()[0] == 1
        exported = client.get("/mud/forge/api/w/harbor/published/export")
        assert exported.status_code == 200
        with zipfile.ZipFile(BytesIO(exported.data)) as archive:
            assert set(archive.namelist()) == {"manifest.json", "rooms.json", "exits.json", "content.json"}
            assert json.loads(archive.read("rooms.json"))[0]["name"] in {"Old Dock", "Main Street"}
            content = json.loads(archive.read("content.json"))
            assert content["mud_npc_instances"][0]["definition_id"] == content["mud_npc_definitions"][0]["id"]
        with app.app_context():
            db = get_db()
            exported_document = mud_worlds.read_package(exported.data)
            exported_document["id"] = "harbor-copy"
            copied = mud_worlds.install(db, mud_worlds.package_bytes(exported_document))
            assert copied["id"] == "harbor-copy"
            street_id = db.execute("SELECT id FROM mud_world_rooms WHERE world_id='harbor-copy' "
                                   "AND name='Main Street'").fetchone()[0]
            assert db.execute("SELECT COUNT(*) FROM mud_quest_objectives WHERE "
                              "target_id=?", (street_id,)).fetchone()[0] == 1
        assert b"Disable this world before removing it" in client.post("/sysop/settings/mud-worlds/remove", data={
            "csrf_token": "test-token", "world_id": "harbor", "confirm_remove": "on"},
            follow_redirects=True).data
        _enable(client, "harbor", False)
        assert player_client.get("/mud/w/harbor").status_code == 404
        # Builders still reach a hidden world in World Forge.
        assert client.get("/mud/forge/?world=harbor").status_code == 200
        removed = client.post("/sysop/settings/mud-worlds/remove", data={
            "csrf_token": "test-token", "world_id": "harbor", "confirm_remove": "on"},
            follow_redirects=True)
        assert b"World removal started" in removed.data
        finished = []
        for _ in range(200):
            status = client.get("/sysop/settings/mud-worlds/remove-status").get_json()
            finished = status["finished"]
            if finished:
                break
            time.sleep(0.05)
        assert finished and "Custom world removed" in finished[0]
        with app.app_context():
            assert get_db().execute("SELECT 1 FROM mud_world_packages WHERE id='harbor'").fetchone() is None
        assert not (tmp_path / "mud_worlds" / "harbor.zip").exists()
        reinstalled = client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (BytesIO(_archive()), "harbor.zip")},
            follow_redirects=True)
        assert b"Imported world Harbor World" in reinstalled.data
        with app.app_context():
            assert get_db().execute("SELECT start_room_id FROM mud_world_packages WHERE id='harbor'").fetchone()[0] != start_id
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()

def test_world_origin_cleanup_restores_only_missing_races(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    try:
        with app.app_context():
            db = get_db()
            templates = Path(character_creation.__file__).with_name("mud_origin_templates")
            document = mud_worlds.read_package(_archive("cleanup"))
            document["origins"] = {"races": [json.loads((templates / "race.json").read_text())]}
            mud_worlds.install(db, mud_worlds.package_bytes(document))
            key = mud_worlds.bundled_key("cleanup", "races", "riverfolk")
            # The home world borrows the cleanup world's race as its only race.
            home = character_creation.world_availability(db, "stationnet")
            character_creation.set_world_availability(db, "stationnet", {**home, "races": {key}})
            db.commit()
            result = mud_worlds.remove(db, "cleanup", remove_origins=True)
            assert key in result["removed"]["races"]
            assert result["restored"] == ["stationnet"]
            after = character_creation.world_availability(db, "stationnet")
            assert after["races"] == character_creation.default_availability()["races"]
            assert after["classes"] == home["classes"]
            assert db.execute("SELECT 1 FROM mud_origin_definitions WHERE key=?", (key,)).fetchone() is None
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()

def test_world_cleanup_preserves_shared_origin(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    try:
        with app.app_context():
            db = get_db()
            templates = Path(character_creation.__file__).with_name("mud_origin_templates")
            first = mud_worlds.read_package(_archive("first-world"))
            first["origins"] = {"races": [json.loads((templates / "race.json").read_text())]}
            mud_worlds.install(db, mud_worlds.package_bytes(first))
            mud_worlds.install(db, _archive("second-world"))
            key = mud_worlds.bundled_key("first-world", "races", "riverfolk")
            db.execute("INSERT INTO mud_world_origins(world_id,kind,origin_key,local_key) "
                       "VALUES('second-world','races',?,'riverfolk')", (key,))
            db.commit()
            result = mud_worlds.remove(db, "first-world", remove_origins=True)
            assert result["removed"]["races"] == set()
            assert result["preserved"]["races"] == 1
            assert db.execute("SELECT installed FROM mud_origin_definitions WHERE key=?", (key,)).fetchone()[0] == 1
            user_id = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                 "VALUES('archived','ARC','unused')").lastrowid
            archive_id = db.execute("INSERT INTO mud_campaign_archives(world_id,world_name) "
                                    "VALUES('second-world','Second World')").lastrowid
            db.execute("INSERT INTO mud_campaign_characters(archive_id,user_id,snapshot_json) "
                       "VALUES(?,?,?)", (archive_id, user_id,
                       json.dumps({"character": {"race_key": key}})))
            db.commit()
            result = mud_worlds.remove(db, "second-world", remove_origins=True)
            assert result["removed"]["races"] == {key}
            assert db.execute("SELECT installed,enabled FROM mud_origin_definitions WHERE key=?",
                              (key,)).fetchone()[:] == (0, 0)
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()


def test_non_sysop_cannot_install_or_enable(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign) VALUES('Test','Test','TST')")
            member = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                "VALUES('member','MEM','unused')").lastrowid
            db.commit()
        client = _client(app, member)
        response = client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (BytesIO(_archive()), "harbor.zip")})
        assert response.status_code == 302
        assert not (tmp_path / "mud_worlds" / "harbor.zip").exists()
        assert client.post("/sysop/settings/mud-worlds/enabled", data={
            "csrf_token": "test-token", "world_id": "stationnet", "enabled": "0"}).status_code == 302
        assert client.get("/sysop/settings/mud-worlds/stationnet/availability").status_code == 302
        with app.app_context():
            assert get_db().execute("SELECT enabled FROM mud_world_packages WHERE id='stationnet'").fetchone()[0] == 1
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()

def test_bundled_origins_have_per_world_availability_export_and_reinstall(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            sysop = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                               "VALUES('sysop','SYS','unused',1)").lastrowid
            db.commit()
        client = _client(app, sysop)
        document = mud_worlds.read_package(_archive())
        templates = Path(character_creation.__file__).with_name("mud_origin_templates")
        document["origins"] = {
            "races": [json.loads((templates / "race.json").read_text(encoding="utf-8"))],
            "classes": [json.loads((templates / "class.json").read_text(encoding="utf-8"))],
        }
        with app.app_context():
            db = get_db()
            character_creation.import_origin(db, "races", json.dumps(
                document["origins"]["races"][0]).encode())
            db.commit()
        payload = mud_worlds.package_bytes(document)
        race_key = mud_worlds.bundled_key("harbor", "races", "riverfolk")
        class_key = mud_worlds.bundled_key("harbor", "classes", "pathfinder")
        preview = client.post("/sysop/settings/mud-worlds/preview", data={
            "csrf_token": "test-token", "world_file": (BytesIO(payload), "harbor.zip")})
        assert preview.json["origins"]["races"][0]["key"] == race_key
        invalid = json.loads(json.dumps(document))
        invalid["origins"]["classes"][0]["skill"] = "unknown"
        rejected = client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (
                BytesIO(mud_worlds.package_bytes(invalid)), "invalid.zip")},
            follow_redirects=True)
        assert b"Invalid bundled class" in rejected.data
        with app.app_context():
            assert get_db().execute("SELECT 1 FROM mud_world_packages WHERE id='harbor'").fetchone() is None
            assert not (tmp_path / "mud_worlds" / "harbor.zip").exists()
        imported = client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (BytesIO(payload), "harbor.zip"), "enable": "on"},
            follow_redirects=True)
        assert b"Imported world Harbor World" in imported.data
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT installed FROM mud_origin_definitions "
                              "WHERE kind='races' AND key=?", (race_key,)).fetchone()[0] == 1
            # A world that bundles a race and class offers only those by default...
            assert _offered(db, "harbor", "races") == {race_key}
            assert _offered(db, "harbor", "classes") == {class_key}
            # ...and other worlds never see them unless a sysop adds them.
            assert race_key not in _offered(db, "stationnet", "races")
            assert "riverfolk" in _offered(db, "stationnet", "races")
            exported = mud_worlds.read_package(mud_worlds.export(db, "harbor"))
            assert exported["origins"]["races"][0]["key"] == "riverfolk"
            assert exported["origins"]["classes"][0]["key"] == "pathfinder"
            assert mud_worlds.export(db, "harbor", origin_keys=set())
        page = client.get("/sysop/settings/mud-worlds/harbor/availability").data
        assert b"Bundled with Harbor World".upper() in page and b"SELECT THIS WORLD'S BUNDLED" in page
        _offer(client, "stationnet", races=["human", race_key], classes=["warrior"])
        _offer(client, "harbor", races=["human"], classes=[class_key])
        with app.app_context():
            db = get_db()
            # Each world keeps its own selection from the shared registry.
            assert _offered(db, "harbor", "races") == {"human"}
            assert _offered(db, "stationnet", "races") == {"human", race_key}
            assert db.execute("SELECT installed FROM mud_origin_definitions WHERE key=?", (race_key,)).fetchone()[0] == 1
        copied_document = json.loads(json.dumps(document))
        copied_document["id"] = "harbor-copy"
        copy_race = mud_worlds.bundled_key("harbor-copy", "races", "riverfolk")
        copy_class = mud_worlds.bundled_key("harbor-copy", "classes", "pathfinder")
        imported_copy = client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "origin_mode": "manual",
            "races": ["human", copy_race], "classes": ["warrior", copy_class],
            "skills": ["lore", "guard"],
            "world_file": (BytesIO(mud_worlds.package_bytes(copied_document)), "copy.zip")},
            follow_redirects=True)
        assert b"Imported world Harbor World" in imported_copy.data
        with app.app_context():
            db = get_db()
            assert _offered(db, "harbor-copy", "races") == {"human", copy_race}
            assert _offered(db, "harbor", "races") == {"human"}
        _enable(client, "harbor", False)
        client.post("/sysop/settings/mud-worlds/remove", data={
            "csrf_token": "test-token", "world_id": "harbor", "confirm_remove": "on"})
        for _ in range(200):
            if client.get("/sysop/settings/mud-worlds/remove-status").get_json()["finished"]:
                break
            time.sleep(0.05)
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT installed FROM mud_origin_definitions WHERE key=?", (race_key,)).fetchone()[0] == 1
            assert not character_creation.availability_configured(db, "harbor")
            # Removing a world does not touch another world's selection.
            assert race_key in character_creation.world_availability(db, "stationnet")["races"]
        assert client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (BytesIO(payload), "harbor.zip")}).status_code == 302
        with app.app_context():
            assert get_db().execute("SELECT COUNT(*) FROM mud_world_origins WHERE world_id='harbor'").fetchone()[0] == 2
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()


def test_interrupted_world_removal_resumes_and_other_writers_stay_unblocked(tmp_path, monkeypatch):
    import sqlite3
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    try:
        with app.app_context():
            db = get_db()
            mud_worlds.install(db, _archive("slowpoke"))
            db.commit()
            rooms = db.execute("SELECT COUNT(*) FROM mud_world_rooms WHERE world_id='slowpoke'").fetchone()[0]
            assert rooms
            mud_worlds.begin_removal(db, "slowpoke")
            assert db.execute("SELECT removing FROM mud_world_packages WHERE id='slowpoke'").fetchone()[0] == 1
            with pytest.raises(mud_worlds.WorldError):
                mud_worlds.set_enabled(db, "slowpoke", True)
            # Another connection can write between the removal's short transactions.
            other = sqlite3.connect(str(tmp_path / "station.db"), timeout=0.2)
            real = mud_worlds._delete_batches

            def failing(*args, **kwargs):
                other.execute("UPDATE station SET mud_procedural_enabled=mud_procedural_enabled WHERE id=1")
                other.commit()
                raise RuntimeError("interrupted")
            monkeypatch.setattr(mud_worlds, "_delete_batches", failing)
            with pytest.raises(RuntimeError):
                mud_worlds.finish_removal(db, "slowpoke")
            other.close()
            assert db.execute("SELECT removing FROM mud_world_packages WHERE id='slowpoke'").fetchone()[0] == 1
            monkeypatch.setattr(mud_worlds, "_delete_batches", real)
            mud_worlds.finish_removal(db, "slowpoke")
            assert db.execute("SELECT 1 FROM mud_world_packages WHERE id='slowpoke'").fetchone() is None
            assert db.execute("SELECT COUNT(*) FROM mud_world_rooms WHERE world_id='slowpoke'").fetchone()[0] == 0
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
