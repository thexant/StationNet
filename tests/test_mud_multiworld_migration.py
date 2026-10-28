"""Single-world MUD databases upgrade to one character per world without losing progress.

tests/fixtures/mud_legacy.sqlite3 was built by scripts/make_mud_legacy_fixture.py with
the single-world code: Alice has a Station World character, shared progression, a
procedural item, and a live Dungeon Portals run; Bob only played Station World; and
a campaign archive holds Alice's level 4 Harbor character.
"""
from pathlib import Path
import shutil
import sqlite3
import uuid

import pytest

from stationnet import create_app, mud, mud_scope
from stationnet.db import get_db, init_db

FIXTURE = Path(__file__).parent / "fixtures" / "mud_legacy.sqlite3"
PROCEDURAL = mud_scope.PROCEDURAL


@pytest.fixture
def legacy():
    data = Path(__file__).parent / f"_legacy_migration_{uuid.uuid4().hex}"
    data.mkdir()
    shutil.copy(FIXTURE, data / "station.db")
    apps = []

    def start(progression_shared=True, inventory_shared=True):
        connection = sqlite3.connect(data / "station.db")
        connection.execute("UPDATE mud_crossover_settings SET progression_shared=?,inventory_shared=?",
                           (int(progression_shared), int(inventory_shared)))
        connection.commit()
        connection.close()
        app = create_app({"TESTING": True, "DATA_DIR": str(data)})
        apps.append(app)
        return app

    try:
        yield start, data
    finally:
        for app in apps:
            app.extensions["broadcast_coordinator"]._closed = True
            app.extensions["broadcast_coordinator"]._timer.cancel()
        shutil.rmtree(data)


def _characters(db, user_id):
    return {row["world_key"]: row for row in db.execute(
        "SELECT c.*,p.track_key,p.level,p.experience FROM mud_characters c "
        "JOIN mud_character_progression p ON p.character_id=c.id WHERE c.user_id=?", (user_id,))}


def _items(db, character_id):
    return {row["definition_id"] for row in db.execute(
        "SELECT definition_id FROM mud_item_instances WHERE owner_character_id=?", (character_id,))}


def test_shared_progression_splits_into_world_characters(legacy):
    start, data = legacy
    app = start()
    with app.app_context():
        db = get_db()
        alice = _characters(db, 1)
        assert set(alice) == {"stationnet", PROCEDURAL, "harbor"}
        home, dungeon, harbor = alice["stationnet"], alice[PROCEDURAL], alice["harbor"]
        # The existing character stays in the formerly active world, keeping its ID.
        assert home["id"] == "stationnet:character:1" and home["display_name"] == "Alice"
        assert (home["track_key"], home["level"], home["experience"]) == ("traditional", 3, 400)
        # Shared progression and skills were used in the dungeons too, so the dungeon character copies them.
        assert dungeon["display_name"] == "Alice" and dungeon["race_key"] == home["race_key"]
        assert (dungeon["track_key"], dungeon["level"], dungeon["experience"]) == ("procedural", 3, 400)
        assert {row[0] for row in db.execute("SELECT skill_key FROM mud_character_skills WHERE character_id=?",
                                             (dungeon["id"],))} >= {"lore", "guard"}
        # The live portal run and its explorer move to the dungeon world.
        assert db.execute("SELECT character_id FROM mud_expedition_participants").fetchone()[0] == dungeon["id"]
        location = db.execute("SELECT * FROM mud_player_locations WHERE user_id=1 AND world_key=?",
                              (PROCEDURAL,)).fetchone()
        assert location["location_type"] == "dungeon_instance"
        # A shared inventory stays with the original character; coins and bank stay too.
        assert {"legacy:lamp", "stationnet:shop:tonic"} <= _items(db, home["id"])
        assert db.execute("SELECT coins FROM mud_character_wallets WHERE character_id=?", (home["id"],)).fetchone()[0] == 123
        assert db.execute("SELECT coins FROM mud_character_banks WHERE character_id=?", (home["id"],)).fetchone()[0] == 45
        # The archived Harbor campaign became Alice's Harbor character with fresh item IDs.
        assert (harbor["display_name"], harbor["level"], harbor["experience"]) == ("Harbor Alice", 4, 900)
        assert "legacy:anchor" in _items(db, harbor["id"])
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id='legacy:anchor-1'").fetchone() is None
        assert db.execute("SELECT converted_at FROM mud_campaign_archives").fetchone()[0] is not None
        assert db.execute("SELECT COUNT(*) FROM mud_campaign_characters").fetchone()[0] == 1
        # Bob never entered the dungeons, so he keeps exactly one character.
        assert set(_characters(db, 2)) == {"stationnet"}
        # Worlds: the formerly active one stays enabled; the other stays installed and off.
        assert {row["id"]: row["enabled"] for row in db.execute("SELECT id,enabled FROM mud_world_packages")} == {
            "stationnet": 1, "harbor": 0}
        # Every world starts with the race, class, and skill selection the station offered.
        offered = {row[0] for row in db.execute("SELECT DISTINCT world_key FROM mud_world_availability")}
        assert offered == {"stationnet", "harbor", PROCEDURAL}
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='mud_crossover_settings'").fetchone() is None
        assert db.execute("SELECT COUNT(*) FROM mud_character_progression WHERE track_key='shared'").fetchone()[0] == 0
        with mud_scope.use(PROCEDURAL):
            assert mud._current_room(db, 1)["instance_id"] is not None
        with mud_scope.use("stationnet"):
            assert mud._current_room(db, 2)["id"] == "stationnet:start"
    backups = list((data / "backups").glob("mud-pre-multiworld-*.sqlite3"))
    assert len(backups) == 1
    original = sqlite3.connect(backups[0])
    try:
        assert original.execute("SELECT COUNT(*) FROM mud_characters").fetchone()[0] == 2
        assert original.execute("SELECT progression_shared FROM mud_crossover_settings").fetchone()[0] == 1
    finally:
        original.close()


def test_independent_inventories_follow_their_mode(legacy):
    start, _data = legacy
    app = start(progression_shared=False, inventory_shared=False)
    with app.app_context():
        db = get_db()
        alice = _characters(db, 1)
        home, dungeon = alice["stationnet"], alice[PROCEDURAL]
        # Separate tracks keep their own progress: the dungeon track was level 2.
        assert (home["level"], dungeon["level"]) == (1, 2)
        assert "stationnet:shop:tonic" in _items(db, dungeon["id"])
        assert "stationnet:shop:tonic" not in _items(db, home["id"])
        assert "legacy:lamp" in _items(db, home["id"])
        assert {row[0] for row in db.execute("SELECT possession_mode FROM mud_item_instances "
                                             "WHERE owner_character_id=?", (dungeon["id"],))} == {"procedural"}


def test_migration_runs_once(legacy):
    start, data = legacy
    app = start()
    with app.app_context():
        db = get_db()
        before = [tuple(row) for row in db.execute("SELECT id,world_key FROM mud_characters ORDER BY id")]
        init_db()
        after = [tuple(row) for row in db.execute("SELECT id,world_key FROM mud_characters ORDER BY id")]
        assert before == after
        assert db.execute("SELECT COUNT(*) FROM mud_characters WHERE user_id=1").fetchone()[0] == 3
    assert len(list((data / "backups").glob("*.sqlite3"))) == 1


def test_fresh_station_needs_no_backup():
    data = Path(__file__).parent / f"_fresh_multiworld_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    try:
        with app.app_context():
            db = get_db()
            assert "world_key" in {row["name"] for row in db.execute("PRAGMA table_info(mud_characters)")}
            assert db.execute("SELECT enabled FROM mud_world_packages WHERE id='stationnet'").fetchone()[0] == 1
            # New accounts no longer get a character until they visit a world.
            db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('new','NEW','unused')")
            assert db.execute("SELECT COUNT(*) FROM mud_characters").fetchone()[0] == 0
        assert not (data / "backups").exists()
    finally:
        app.extensions["broadcast_coordinator"]._closed = True
        app.extensions["broadcast_coordinator"]._timer.cancel()
        shutil.rmtree(data)
