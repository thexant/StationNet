"""Character creation choices and confirmed deletion of a MUD save."""
import json
from pathlib import Path
import shutil
import uuid

import pytest

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture
def data_path():
    path = Path(__file__).parent / f"_mud_character_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_expanded_choices_and_confirmed_character_deletion(data_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled) "
                       "VALUES('Test','Test','TST',1)")
            user_id = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                 "VALUES('player','PLY','unused')").lastrowid
            db.commit()
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "test-token"
        creation = client.get("/mud/create")
        assert b"Halfling" in creation.data and b"Mystic" in creation.data
        assert b"data-adjust" in creation.data and b"data-step-jump" in creation.data
        build = {"name": "New Adventurer", "race": "halfling", "class": "mystic",
                 "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                 "skills": ["lore", "guard"]}
        created = client.post("/mud/create", data={"csrf_token": "test-token",
                                                   "build": json.dumps(build)})
        assert created.status_code == 302
        with app.app_context():
            db = get_db()
            character_id = f"stationnet:character:{user_id}"
            assert db.execute("SELECT creation_state FROM mud_characters WHERE id=?",
                              (character_id,)).fetchone()[0] == "complete"
            db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type) "
                       "VALUES('test:bag','Bag','A bag','container')")
            db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type) "
                       "VALUES('test:coin','Coin','A coin','misc')")
            db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) "
                       "VALUES('test:owned-bag','test:bag',?)", (character_id,))
            db.execute("INSERT INTO mud_item_instances(id,definition_id,container_id) "
                       "VALUES('test:contained-coin','test:coin','test:owned-bag')")
            db.execute("UPDATE mud_character_progression SET level=7 WHERE character_id=?",
                       (character_id,))
            db.commit()
        assert b"New Adventurer" in client.get("/mud/character/delete").data
        rejected = client.post("/mud/character/delete", data={
            "csrf_token": "test-token", "confirm_name": "wrong"})
        assert rejected.status_code == 200
        with app.app_context():
            assert get_db().execute("SELECT creation_state FROM mud_characters WHERE user_id=?",
                                    (user_id,)).fetchone()[0] == "complete"
        deleted = client.post("/mud/character/delete", data={
            "csrf_token": "test-token", "confirm_name": "New Adventurer"})
        assert deleted.status_code == 302 and deleted.location.endswith("/mud/create")
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT creation_state,race_key FROM mud_characters WHERE user_id=?",
                              (user_id,)).fetchone()[:] == ("pending", None)
            assert [row[0] for row in db.execute(
                "SELECT level FROM mud_character_progression WHERE character_id=?",
                (character_id,))] == [1, 1, 1]
            assert db.execute("SELECT 1 FROM mud_item_instances WHERE id LIKE 'test:%'").fetchone() is None
            assert db.execute("SELECT 1 FROM mud_character_skills WHERE character_id=?",
                              (character_id,)).fetchone() is None
    finally:
        coordinator = app.extensions.get("broadcast_coordinator")
        if coordinator is not None:
            coordinator._closed = True
            coordinator._timer.cancel()
