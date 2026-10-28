"""Disabled origins are grandfathered, deleted ones are replaced, and each world keeps its own character."""
import json
import shutil
import uuid
from pathlib import Path

import pytest

from mud_world_helpers import retire_origin, set_offered
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
                       "VALUES(?,'traditional','main_hand','test:item')", (cid,))
            db.execute("INSERT INTO mud_character_wallets(character_id,coins) VALUES(?,77)", (cid,))
            # Disabling a class in this world only stops new characters from choosing it.
            set_offered(db, "classes", "mystic", False)
            db.commit()
        client = _client(app, player)
        assert client.get("/mud/w/stationnet").status_code == 200
        with app.app_context():
            db = get_db()
            # Deleting the class from the station asks its characters for a replacement.
            retire_origin(db, cid, "classes")
            db.commit()
        assert client.get("/mud/w/stationnet").location.endswith("/mud/w/stationnet/character/compatibility")
        assert client.get("/mud/w/stationnet/updates").status_code == 409
        assert client.post("/mud/w/stationnet/actions", data={"csrf_token": "test-token", "command": "/look"}).status_code == 409
        invalid = client.post("/mud/w/stationnet/character/compatibility", data={
            "csrf_token": "test-token", "race": "halfling", "class": "mystic"})
        assert b"available class" in invalid.data
        updated = client.post("/mud/w/stationnet/character/compatibility", data={
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


def test_each_world_has_its_own_character(data_path):
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
            mud._set_player_location(db, player, "traditional", "world_room", "stationnet:start")
            db.commit()
            mud_worlds.install(db, mud_worlds.package_bytes({
                "id": "second", "name": "Second World", "author": "Builder", "start_room": "start",
                "rooms": [{"id": "start", "name": "Start", "description": "A second world."}], "exits": []}))
            db.commit()
        admin = _client(app, sysop)
        player_client = _client(app, player)
        assert player_client.get("/mud/w/second").status_code == 404
        enabled = admin.post("/sysop/settings/mud-worlds/enabled", data={
            "csrf_token": "test-token", "world_id": "second", "enabled": "1"}, follow_redirects=True)
        assert b"Second World is now open to players" in enabled.data
        # Both worlds stay open at once; the player's first character is untouched.
        assert player_client.get("/mud/w/stationnet").status_code == 200
        assert player_client.get("/mud/w/second").location.endswith("/mud/w/second/create")
        created = player_client.post("/mud/w/second/create", data={"csrf_token": "test-token", "build": json.dumps({
            "name": "Second Hero", "race": "human", "class": "warrior",
            "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
            "skills": ["lore", "guard"]})})
        assert created.location.endswith("/mud/w/second")
        listing = player_client.get("/mud").data
        assert b"First Hero" in listing and b"Second Hero" in listing
        with app.app_context():
            db = get_db()
            rows = {row["world_key"]: row for row in db.execute(
                "SELECT c.id,c.world_key,c.display_name,p.level,COALESCE(w.coins,-1) AS coins "
                "FROM mud_characters c JOIN mud_character_progression p ON p.character_id=c.id "
                "LEFT JOIN mud_character_wallets w ON w.character_id=c.id WHERE c.user_id=?", (player,))}
            assert rows["stationnet"]["id"] == cid
            assert (rows["stationnet"]["level"], rows["stationnet"]["coins"]) == (5, 91)
            assert rows["second"]["id"] != cid and rows["second"]["level"] == 1 and rows["second"]["coins"] != 91
        disabled = admin.post("/sysop/settings/mud-worlds/enabled", data={
            "csrf_token": "test-token", "world_id": "second", "enabled": "0"})
        assert disabled.status_code == 302
        assert player_client.get("/mud/w/second").status_code == 404
        assert b"Second Hero" not in player_client.get("/mud").data
        with app.app_context():
            # Disabling hides the world but keeps its characters for when it returns.
            assert get_db().execute("SELECT display_name FROM mud_characters WHERE user_id=? AND world_key='second'",
                                    (player,)).fetchone()[0] == "Second Hero"
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
