"""Re-importing a newer package for an installed world upgrades it in place and keeps its players."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import uuid
import zipfile

import pytest

from mud_world_helpers import enter_world
from stationnet import create_app, character_creation, mud, mud_abilities, mud_world_dungeons, mud_worlds
from stationnet.db import get_db


WORLD = "lantern-isles"


@pytest.fixture
def app():
    data = Path(__file__).parent / f"_upgrade_{uuid.uuid4().hex}"
    data.mkdir()
    application = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with application.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        db.commit()
        mud_worlds.install(db, mud_worlds.example_bytes(), enable=True)
        db.commit()
    yield application
    application.extensions["broadcast_coordinator"]._closed = True
    application.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data)


def _version_two():
    """The example with the market gone, a stronger flare, and a new lighthouse."""
    with zipfile.ZipFile(BytesIO(mud_worlds.example_bytes())) as archive:
        files = {name: json.loads(archive.read(name)) for name in archive.namelist()}
    files["rooms.json"] = [room for room in files["rooms.json"] if room["id"] != "market"] + [
        {"id": "lighthouse", "name": "Lighthouse", "description": "The great lamp turns overhead."}]
    files["exits.json"] = [exit_ for exit_ in files["exits.json"] if "market" not in (exit_["from"], exit_["to"])]
    content = files["content.json"]
    content["mud_npc_instances"] = [row for row in content["mud_npc_instances"] if row["room_id"] != "market"]
    files["abilities.json"]["abilities"][0]["damage"] = 12
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, json.dumps(data))
    return stream.getvalue()


def _room(db, name):
    return db.execute("SELECT id FROM mud_world_rooms WHERE world_id=? AND name=?", (WORLD, name)).fetchone()["id"]


def _player(db):
    enter_world(db, WORLD)
    uid = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('wren','WRN','x')").lastrowid
    character_creation.complete(db, uid, {
        "name": "Wren", "race": mud_worlds.bundled_key(WORLD, "races", "islander"),
        "class": mud_worlds.bundled_key(WORLD, "classes", "lamplighter"),
        "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
        "skills": ["lore", "guard"]})
    mud._set_player_location(db, uid, "traditional", "world_room", _room(db, "Lantern Harbor"))
    return uid, db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def test_upgrade_keeps_characters_progress_and_settings(app):
    with app.app_context():
        db = get_db()
        uid, user = _player(db)
        character = mud._character_row(db, uid)
        quest = db.execute("SELECT id FROM mud_quests WHERE world_id=?", (WORLD,)).fetchone()["id"]
        mud._accept_quest(db, user, quest)
        faction = db.execute("SELECT id FROM mud_world_factions WHERE world_id=?", (WORLD,)).fetchone()["id"]
        db.execute("INSERT INTO mud_character_reputation(character_id,faction_id,reputation) VALUES(?,?,40)",
                   (character["id"], faction))
        oil = db.execute("SELECT id FROM mud_item_definitions WHERE world_id=? AND name='Flare Oil'",
                         (WORLD,)).fetchone()["id"]
        held = mud._grant_item(db, uid, oil)
        mud.award_experience(db, uid, 500)
        level = mud.character_state(db, uid)["level"]
        mud._set_player_location(db, uid, "traditional", "world_room", _room(db, "Night Market"))
        db.commit()

        payload = _version_two()
        preview = mud_worlds.upgrade_preview(db, payload)
        assert preview["changes"]["rooms"] == {"added": ["lighthouse"], "removed": ["market"], "changed": []}
        assert preview["changes"]["abilities"]["changed"] == ["lantern-flare"]
        assert preview["moved_to_start"] == 1 and preview["characters"] == 1
        with pytest.raises(mud_worlds.WorldError, match="differs from the one that was previewed"):
            mud_worlds.upgrade(db, payload, "0" * 64)

        _document, summary = mud_worlds.upgrade(db, payload, preview["expected_hash"])
        assert summary["moved_to_start"] == 1
        location = db.execute("SELECT location_key FROM mud_player_locations WHERE user_id=?", (uid,)).fetchone()
        assert location["location_key"] == _room(db, "Lantern Harbor")
        new_quest = db.execute("SELECT id FROM mud_quests WHERE world_id=?", (WORLD,)).fetchone()["id"]
        assert new_quest != quest
        assert db.execute("SELECT status FROM mud_player_quests WHERE character_id=? AND quest_id=?",
                          (character["id"], new_quest)).fetchone()["status"] == "active"
        new_faction = db.execute("SELECT id FROM mud_world_factions WHERE world_id=?", (WORLD,)).fetchone()["id"]
        assert db.execute("SELECT reputation FROM mud_character_reputation WHERE character_id=? AND faction_id=?",
                          (character["id"], new_faction)).fetchone()["reputation"] == 40
        new_oil = db.execute("SELECT definition_id FROM mud_item_instances WHERE id=?", (held,)).fetchone()[0]
        assert new_oil != oil and new_oil.endswith(":mud_item_definitions:flare-oil")
        assert mud.character_state(db, uid)["level"] == level
        assert mud_abilities.catalog(db)[mud_worlds.bundled_key(WORLD, "abilities", "lantern-flare")]["damage"] == 12
        world = db.execute("SELECT enabled,package_hash FROM mud_world_packages WHERE id=?", (WORLD,)).fetchone()
        assert world["enabled"] == 1 and world["package_hash"] == preview["expected_hash"]
        assert db.execute("SELECT COUNT(*) FROM mud_world_upgrades WHERE world_id=?", (WORLD,)).fetchone()[0] == 1
        assert mud_worlds.directory().joinpath(f"{WORLD}.zip").read_bytes() == payload
        assert len(mud_world_dungeons.world_templates(db, WORLD)) == 1


def test_failed_upgrade_leaves_the_world_untouched(app, monkeypatch):
    with app.app_context():
        db = get_db()
        rooms = {row["id"] for row in db.execute("SELECT id FROM mud_world_rooms WHERE world_id=?", (WORLD,))}
        payload = _version_two()
        expected = mud_worlds.package_hash(payload)

        def broken(*args, **kwargs):
            raise mud_world_dungeons.WorldDungeonError("simulated failure")

        monkeypatch.setattr(mud_world_dungeons, "install_document", broken)
        with pytest.raises(mud_worlds.WorldError, match="simulated failure"):
            mud_worlds.upgrade(db, payload, expected)
        assert {row["id"] for row in db.execute("SELECT id FROM mud_world_rooms WHERE world_id=?", (WORLD,))} == rooms
        assert db.execute("SELECT enabled FROM mud_world_packages WHERE id=?", (WORLD,)).fetchone()[0] == 1
        assert mud_worlds.directory().joinpath(f"{WORLD}.zip").read_bytes() == mud_worlds.example_bytes()


def test_import_of_an_installed_id_points_to_upgrade(app):
    with app.app_context():
        with pytest.raises(mud_worlds.WorldError, match="upgrade"):
            mud_worlds.install(get_db(), mud_worlds.example_bytes())
