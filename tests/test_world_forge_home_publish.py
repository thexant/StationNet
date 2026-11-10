"""World Forge publishes the home world through the package upgrade path without losing progress."""
import json
from pathlib import Path
import shutil
import uuid

import pytest

from stationnet import create_app, character_creation, mud, mud_scope, mud_worlds
from stationnet.db import get_db


@pytest.fixture
def app():
    data = Path(__file__).parent / f"_home_publish_{uuid.uuid4().hex}"
    data.mkdir()
    application = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with application.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        db.commit()
    yield application
    application.extensions["broadcast_coordinator"]._closed = True
    application.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data, ignore_errors=True)


def _builder_home(db):
    """Home content shaped exactly as the old in-place builder stored it: UUID IDs, no local IDs."""
    start = mud._start_room(db, mud_scope.HOME)["id"]
    hall = f"world:room:{uuid.uuid4().hex}"
    cellar = f"world:room:{uuid.uuid4().hex}"
    for room, name in ((hall, "Great Hall"), (cellar, "Cellar")):
        db.execute("INSERT INTO mud_world_rooms(id,name,description,world_id) VALUES(?,?,?,?)",
                   (room, name, f"The {name.lower()}.", mud_scope.HOME))
    exit_id = f"world:exit:{uuid.uuid4().hex}"
    db.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) VALUES(?,?,?,?,?)",
               (exit_id, start, hall, "hall", "north"))
    db.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) VALUES(?,?,?,?,?)",
               (f"world:exit:{uuid.uuid4().hex}", hall, start, "out", "south"))
    db.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) VALUES(?,?,?,?,?)",
               (f"world:exit:{uuid.uuid4().hex}", hall, cellar, "cellar", "down"))
    quest = f"world:quest:{uuid.uuid4().hex}"
    db.execute("INSERT INTO mud_quests(id,name,description,world_id) VALUES(?,?,?,?)",
               (quest, "Sweep the Hall", "Sweep it.", mud_scope.HOME))
    faction = f"world:faction:{uuid.uuid4().hex}"
    db.execute("INSERT INTO mud_world_factions(id,world_id,name,description) VALUES(?,?,?,?)",
               (faction, mud_scope.HOME, "Wardens", "Keepers of the hall."))
    lamp = f"world:item-definition:{uuid.uuid4().hex}"
    db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,world_id) VALUES(?,?,?,?,?)",
               (lamp, "Brass Lamp", "A lamp.", "misc", mud_scope.HOME))
    db.execute("INSERT INTO mud_item_instances(id,definition_id,room_id) VALUES(?,?,?)",
               (f"world:item:{uuid.uuid4().hex}", lamp, hall))
    return {"start": start, "hall": hall, "cellar": cellar, "quest": quest, "faction": faction, "lamp": lamp}


def _player(db, room):
    uid = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('wren','WRN','x')").lastrowid
    character_creation.complete(db, uid, {
        "name": "Wren", "race": "human", "class": "warrior",
        "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
        "skills": ["lore", "guard"]})
    mud._set_player_location(db, uid, "traditional", "world_room", room)
    return uid


def _publish(db, document):
    payload = mud_worlds.package_bytes(document)
    with mud_worlds.home_publish():
        preview = mud_worlds.upgrade_preview(db, payload)
        return preview, mud_worlds.upgrade(db, payload, preview["expected_hash"])


def test_home_world_round_trips_through_publish_keeping_progress(app):
    with app.app_context():
        db = get_db()
        home = _builder_home(db)
        uid = _player(db, home["hall"])
        character = mud._character_row(db, uid)
        db.execute("INSERT INTO mud_player_quests(character_id,quest_id,status) VALUES(?,?,'active')",
                   (character["id"], home["quest"]))
        db.execute("INSERT INTO mud_character_reputation(character_id,faction_id,reputation) VALUES(?,?,25)",
                   (character["id"], home["faction"]))
        held = mud._grant_item(db, uid, home["lamp"])
        dropped = mud._grant_item(db, uid, home["lamp"], "shop:item")
        db.execute("UPDATE mud_item_instances SET owner_character_id=NULL,room_id=? WHERE id=?",
                   (home["hall"], dropped))
        db.commit()

        document, _report = mud_worlds.export_document(db, mud_scope.HOME, keep_id=True)
        assert document["id"] == mud_scope.HOME
        # Dropped player items are not world content; the authored lamp placement is.
        assert len(document["content"]["mud_item_instances"]) == 1
        hall_local = next(room["id"] for room in document["rooms"] if room["name"] == "Great Hall")
        for room in document["rooms"]:
            if room["id"] == hall_local:
                room["name"] = "Grand Hall"
        document["rooms"].append({"id": "attic", "name": "Attic", "description": "Dusty rafters."})
        document["exits"].append({"from": hall_local, "to": "attic", "name": "attic", "direction": "up"})

        preview, (_document, summary) = _publish(db, document)
        assert preview["changes"]["rooms"]["added"] == ["attic"]
        assert summary["moved_to_start"] == 0

        new_hall = db.execute("SELECT id FROM mud_world_rooms WHERE world_id=? AND name='Grand Hall'",
                              (mud_scope.HOME,)).fetchone()["id"]
        assert db.execute("SELECT location_key FROM mud_player_locations WHERE user_id=?",
                          (uid,)).fetchone()[0] == new_hall
        new_quest = db.execute("SELECT id FROM mud_quests WHERE world_id=?", (mud_scope.HOME,)).fetchone()["id"]
        assert new_quest != home["quest"]
        assert db.execute("SELECT status FROM mud_player_quests WHERE character_id=? AND quest_id=?",
                          (character["id"], new_quest)).fetchone()["status"] == "active"
        new_faction = db.execute("SELECT id FROM mud_world_factions WHERE world_id=?",
                                 (mud_scope.HOME,)).fetchone()["id"]
        assert db.execute("SELECT reputation FROM mud_character_reputation WHERE character_id=? AND faction_id=?",
                          (character["id"], new_faction)).fetchone()["reputation"] == 25
        new_lamp = db.execute("SELECT definition_id FROM mud_item_instances WHERE id=?", (held,)).fetchone()[0]
        assert new_lamp != home["lamp"] and new_lamp.startswith("world:stationnet:")
        # Station-owned rows sharing the home world's ID are left alone.
        assert db.execute("SELECT world_id FROM mud_item_definitions WHERE id='stationnet:shop:blade'"
                          ).fetchone()[0] == mud_scope.HOME
        package = db.execute("SELECT imported,package_hash FROM mud_world_packages WHERE id=?",
                             (mud_scope.HOME,)).fetchone()
        assert package["imported"] == 0 and package["package_hash"] == preview["expected_hash"]

        # A second export of the published world is stable.
        again, _ = mud_worlds.export_document(db, mud_scope.HOME, keep_id=True)
        assert {room["id"] for room in again["rooms"]} == {room["id"] for room in document["rooms"]}


def test_zip_imports_still_cannot_replace_the_home_world(app):
    with app.app_context():
        db = get_db()
        _builder_home(db)
        db.commit()
        document, _report = mud_worlds.export_document(db, mud_scope.HOME, keep_id=True)
        payload = mud_worlds.package_bytes(document)
        with pytest.raises(mud_worlds.WorldError, match="reserved"):
            mud_worlds.upgrade_preview(db, payload)
        with pytest.raises(mud_worlds.WorldError):
            mud_worlds.upgrade(db, payload, mud_worlds.package_hash(payload))
        public, _report = mud_worlds.export_document(db, mud_scope.HOME)
        assert public["id"] == "my-world"


def test_field_only_edits_publish_in_place_and_keep_live_state(app):
    with app.app_context():
        db = get_db()
        home = _builder_home(db)
        uid = _player(db, home["hall"])
        dropped = mud._grant_item(db, uid, home["lamp"], "shop:item")
        db.execute("UPDATE mud_item_instances SET owner_character_id=NULL,room_id=? WHERE id=?",
                   (home["hall"], dropped))
        db.commit()
        document, _report = mud_worlds.export_document(db, mud_scope.HOME, keep_id=True)
        for room in document["rooms"]:
            if room["name"] == "Great Hall":
                room["description"] = "Freshly swept."
        for item in document["content"]["mud_item_definitions"]:
            item["name"] = "Copper Lamp"
        payload = mud_worlds.package_bytes(document)
        with mud_worlds.home_publish():
            plan = mud_worlds.light_upgrade_plan(db, payload)
            assert plan is not None and len(plan["edits"]) == 2
            _document, summary = mud_worlds.light_upgrade(db, payload, mud_worlds.package_hash(payload))
        assert summary["light"] and summary["touched_rooms"] == [home["hall"]]
        # Same rows, same IDs: the player, the dropped item and the lamp definition were edited in place.
        assert db.execute("SELECT description FROM mud_world_rooms WHERE id=?",
                          (home["hall"],)).fetchone()[0] == "Freshly swept."
        assert db.execute("SELECT name FROM mud_item_definitions WHERE id=?", (home["lamp"],)).fetchone()[0] == "Copper Lamp"
        assert db.execute("SELECT room_id FROM mud_item_instances WHERE id=?", (dropped,)).fetchone()[0] == home["hall"]

        document["rooms"].append({"id": "attic", "name": "Attic", "description": "Dusty."})
        with mud_worlds.home_publish():
            assert mud_worlds.light_upgrade_plan(db, mud_worlds.package_bytes(document)) is None
