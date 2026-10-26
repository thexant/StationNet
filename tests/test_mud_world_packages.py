"""Portable traditional worlds can be installed, selected, exported, and removed."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import uuid
import zipfile

import pytest

from stationnet import create_app, mud, mud_worlds
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


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / f"_world_package_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_world_lifecycle_and_player_relocation(tmp_path):
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
            explorer = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                  "VALUES('explorer','EXP','unused')").lastrowid
            mud._set_player_location(db, player, "traditional", "world_room", "stationnet:start")
            mud._set_player_location(db, explorer, "procedural", "portal", mud.PORTAL_ROOM_ID)
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
        assert b"Imported world Harbor World" in imported.data
        assert (tmp_path / "mud_worlds" / "harbor.zip").exists()
        assert b"already installed" in client.post("/sysop/settings/mud-worlds/import", data={
            "csrf_token": "test-token", "world_file": (BytesIO(_archive()), "harbor.zip")},
            follow_redirects=True).data
        switched = client.post("/sysop/settings/mud-worlds/activate", data={
            "csrf_token": "test-token", "world_id": "harbor"}, follow_redirects=True)
        assert b"Active MUD world changed" in switched.data
        with app.app_context():
            db = get_db()
            start_id = db.execute("SELECT start_room_id FROM mud_world_packages WHERE id='harbor'").fetchone()[0]
            assert db.execute("SELECT location_key FROM mud_player_locations WHERE user_id=?",
                              (player,)).fetchone()[0] == start_id
            assert db.execute("SELECT location_key FROM mud_player_locations WHERE user_id=?",
                              (explorer,)).fetchone()[0] == mud.PORTAL_ROOM_ID
            assert db.execute("SELECT 1 FROM mud_events WHERE body LIKE '%active MUD world changed%' ").fetchone()
            assert db.execute("SELECT COUNT(*) FROM mud_npc_instances WHERE room_id=?", (start_id,)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM mud_item_instances WHERE room_id=?", (start_id,)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM mud_item_instances child "
                              "JOIN mud_item_instances container ON child.container_id=container.id "
                              "WHERE container.room_id=?", (start_id,)).fetchone()[0] == 1
        exported = client.get("/mud/builder/export")
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
        assert b"Select another active world" in client.post("/sysop/settings/mud-worlds/remove", data={
            "csrf_token": "test-token", "world_id": "harbor", "confirm_remove": "on"},
            follow_redirects=True).data
        client.post("/sysop/settings/mud-worlds/activate", data={
            "csrf_token": "test-token", "world_id": "stationnet"})
        removed = client.post("/sysop/settings/mud-worlds/remove", data={
            "csrf_token": "test-token", "world_id": "harbor", "confirm_remove": "on"},
            follow_redirects=True)
        assert b"Custom world removed" in removed.data
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


def test_non_sysop_cannot_install_or_switch(tmp_path):
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
        assert client.post("/sysop/settings/mud-worlds/activate", data={
            "csrf_token": "test-token", "world_id": "stationnet"}).status_code == 302
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
