"""World origin changes and campaign archives preserve character data."""
import json
import re
import shutil
import uuid
from pathlib import Path

import pytest

from stationnet import create_app, character_creation, mud, mud_worlds
from stationnet.db import get_db


@pytest.fixture
def data_path():
    path = Path(__file__).parent / f"_mud_compat_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    return client


def _switch(client, world_id, campaign="migrate", mode="preserve", **choices):
    form = {"csrf_token": "test-token", "world_id": world_id, "origin_mode": mode, **choices}
    preview = client.post("/sysop/settings/mud-worlds/activation-preview", data=form)
    assert preview.status_code == 200
    fingerprint = re.search(rb'name="fingerprint" value="([0-9a-f]+)"', preview.data).group(1).decode()
    archive = re.search(rb'name="archive_id" value="([0-9]+)"', preview.data)
    return client.post("/sysop/settings/mud-worlds/activate", data={
        **form, "fingerprint": fingerprint, "confirm": "yes", "campaign": campaign,
        "archive_id": archive.group(1).decode() if archive else ""}, follow_redirects=True)


def test_replacement_preserves_progress_and_unequips_unusable_gear(data_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled) VALUES('Test','Test','TST',1)")
            player = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                "VALUES('player','PLY','unused')").lastrowid
            cid = f"stationnet:character:{player}"
            character_creation.complete(db, player, {
                "name": "Old Hero", "race": "halfling", "class": "mystic",
                "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                "skills": ["lore", "guard"]})
            db.execute("UPDATE mud_character_progression SET level=7,experience=9000 WHERE character_id=?", (cid,))
            db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,equipment_slot,properties_json) "
                       "VALUES('test:gear','Strong gear','Gear','equipment','main_hand',?)",
                       (json.dumps({"requirements": {"intellect": 12}}),))
            db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) "
                       "VALUES('test:item','test:gear',?)", (cid,))
            db.execute("INSERT INTO mud_character_equipment(character_id,loadout_key,slot,item_instance_id) "
                       "VALUES(?,'shared','main_hand','test:item')", (cid,))
            db.execute("INSERT INTO mud_character_wallets(character_id,coins) VALUES(?,77)", (cid,))
            db.execute("INSERT INTO mud_origin_definitions(kind,key,enabled) VALUES('classes','mystic',0)")
            db.commit()
        client = _client(app, player)
        assert client.get("/mud").location.endswith("/mud/character/compatibility")
        assert client.get("/mud/updates").status_code == 409
        assert client.post("/mud/actions", data={"csrf_token": "test-token", "command": "/look"}).status_code == 409
        invalid = client.post("/mud/character/compatibility", data={
            "csrf_token": "test-token", "race": "halfling", "class": "mystic"})
        assert b"available class" in invalid.data
        updated = client.post("/mud/character/compatibility", data={
            "csrf_token": "test-token", "race": "halfling", "class": "warrior"})
        assert updated.status_code == 302
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT display_name,race_key,class_key FROM mud_characters WHERE id=?",
                              (cid,)).fetchone()[:] == ("Old Hero", "halfling", "warrior")
            assert {tuple(row) for row in db.execute(
                "SELECT level,experience FROM mud_character_progression WHERE character_id=?", (cid,))} == {(7, 9000)}
            assert db.execute("SELECT coins FROM mud_character_wallets WHERE character_id=?", (cid,)).fetchone()[0] == 77
            assert db.execute("SELECT id FROM mud_item_instances WHERE id='test:item'").fetchone()
            assert db.execute("SELECT 1 FROM mud_character_equipment WHERE item_instance_id='test:item'").fetchone() is None
            assert db.execute("SELECT rank FROM mud_character_skills WHERE character_id=? AND skill_key='lore'",
                              (cid,)).fetchone()[0] == 2
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()


def test_new_campaign_archives_and_restores_character(data_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled) VALUES('Test','Test','TST',1)")
            sysop = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                               "VALUES('sysop','SYS','unused',1)").lastrowid
            player = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                "VALUES('player','PLY','unused')").lastrowid
            cid = f"stationnet:character:{player}"
            character_creation.complete(db, player, {
                "name": "First Hero", "race": "human", "class": "warrior",
                "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                "skills": ["lore", "guard"]})
            db.execute("UPDATE mud_character_progression SET level=5,experience=5000 WHERE character_id=?", (cid,))
            db.execute("INSERT INTO mud_character_wallets(character_id,coins) VALUES(?,91)", (cid,))
            db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type) "
                       "VALUES('test:bag','Bag','A bag','container')")
            db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type) "
                       "VALUES('test:gem','Gem','A gem','misc')")
            db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) "
                       "VALUES('test:bag-item','test:bag',?)", (cid,))
            db.execute("INSERT INTO mud_item_instances(id,definition_id,container_id) "
                       "VALUES('test:gem-item','test:gem','test:bag-item')")
            db.execute("INSERT INTO mud_character_storage(item_instance_id,character_id) "
                       "VALUES('test:bag-item',?)", (cid,))
            mud._set_player_location(db, player, "traditional", "world_room", "stationnet:start")
            db.commit()
            mud_worlds.install(db, mud_worlds.package_bytes({
                "id": "second", "name": "Second World", "author": "Builder", "start_room": "start",
                "rooms": [{"id": "start", "name": "Start", "description": "A second world."}], "exits": []}))
            db.commit()
        admin = _client(app, sysop)
        denied = admin.post("/sysop/settings/mud-worlds/activate", data={
            "csrf_token": "test-token", "world_id": "second"}, follow_redirects=True)
        assert b"Preview the world impact" in denied.data
        activated = _switch(admin, "second", "new")
        assert b"Active MUD world changed" in activated.data
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT creation_state FROM mud_characters WHERE id=?", (cid,)).fetchone()[0] == "pending"
            assert db.execute("SELECT username,callsign FROM users WHERE id=?", (player,)).fetchone()[:] == ("player", "PLY")
            assert db.execute("SELECT COUNT(*) FROM mud_campaign_characters WHERE user_id=?", (player,)).fetchone()[0] == 1
            assert db.execute("SELECT 1 FROM mud_item_instances WHERE id='test:bag-item'").fetchone() is None
        assert _client(app, player).get("/mud").location.endswith("/mud/create")
        restored = _switch(admin, "stationnet", "restore")
        assert b"Active MUD world changed" in restored.data
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT display_name,creation_state FROM mud_characters WHERE id=?",
                              (cid,)).fetchone()[:] == ("First Hero", "complete")
            assert {tuple(row) for row in db.execute(
                "SELECT level,experience FROM mud_character_progression WHERE character_id=?", (cid,))} == {(5, 5000)}
            assert db.execute("SELECT coins FROM mud_character_wallets WHERE character_id=?", (cid,)).fetchone()[0] == 91
            assert db.execute("SELECT container_id FROM mud_item_instances WHERE id='test:gem-item'").fetchone()[0] == "test:bag-item"
            assert db.execute("SELECT item_instance_id FROM mud_character_storage WHERE character_id=?",
                              (cid,)).fetchone()[0] == "test:bag-item"
            assert db.execute("SELECT location_key FROM mud_player_locations WHERE user_id=?",
                              (player,)).fetchone()[0] == "stationnet:start"
            assert db.execute("SELECT COUNT(*) FROM mud_campaign_archives").fetchone()[0] == 2
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
