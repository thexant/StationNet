"""Build tests/fixtures/mud_legacy.sqlite3 from the single-world MUD schema.

Run once with the code *before* the multi-world migration landed; the
resulting database is committed so migration tests can exercise real legacy
data. The fixture contains:

* the Station World active alongside procedural mode, with crossover
  settings on their defaults (shared progression and inventory; tests may
  flip them before migrating);
* "Alice", who has a world character with quests, coins, a bank balance
  and world items, plus an active Dungeon Portals expedition and a
  procedural item;
* "Bob", who only ever played the authored world;
* a second installed world, "harbor", whose campaign archive holds Alice's
  Harbor character (level 4, one item).
"""
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stationnet import character_creation, create_app, mud, mud_compat, mud_worlds  # noqa: E402
from stationnet.db import get_db  # noqa: E402

BUILD = {"attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
         "skills": ["lore", "guard"]}


def main():
    target = ROOT / "tests" / "fixtures" / "mud_legacy.sqlite3"
    work = ROOT / "tests" / f"_legacy_build_{uuid.uuid4().hex}"
    work.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(work)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled,"
                       "mud_procedural_enabled) VALUES('Legacy','Legacy','LGC',1,1,1)")
            alice = db.execute("INSERT INTO users(username,callsign,password_hash) "
                               "VALUES('alice','ALC','unused')").lastrowid
            bob = db.execute("INSERT INTO users(username,callsign,password_hash) "
                             "VALUES('bob','BOB','unused')").lastrowid
            db.commit()
            mud_worlds.install(db, mud_worlds.package_bytes({
                "id": "harbor", "name": "Harbor", "author": "Builder", "start_room": "dock",
                "rooms": [{"id": "dock", "name": "Dock", "description": "Salt and rope."}], "exits": []}))
            db.commit()

            # Alice's Harbor campaign, archived when the station switched back.
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE mud_world_rooms SET is_start=0 WHERE is_start=1")
            harbor_start = db.execute("SELECT start_room_id FROM mud_world_packages WHERE id='harbor'").fetchone()[0]
            db.execute("UPDATE mud_world_rooms SET is_start=1 WHERE id=?", (harbor_start,))
            db.execute("UPDATE mud_world_packages SET active=0")
            db.execute("UPDATE mud_world_packages SET active=1 WHERE id='harbor'")
            character_creation.complete(db, alice, {**BUILD, "name": "Harbor Alice",
                                                    "race": "human", "class": "warrior"})
            cid = f"stationnet:character:{alice}"
            db.execute("UPDATE mud_character_progression SET level=4,experience=900 WHERE character_id=?", (cid,))
            db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,world_id) "
                       "VALUES('legacy:anchor','Anchor charm','A charm','misc','harbor')")
            db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) "
                       "VALUES('legacy:anchor-1','legacy:anchor',?)", (cid,))
            mud._set_player_location(db, alice, "traditional", "world_room", harbor_start)
            mud_compat.archive_campaign(db, "harbor", "Harbor")
            db.execute("UPDATE mud_world_rooms SET is_start=0 WHERE is_start=1")
            db.execute("UPDATE mud_world_rooms SET is_start=1 WHERE id='stationnet:start'")
            db.execute("UPDATE mud_world_packages SET active=0")
            db.execute("UPDATE mud_world_packages SET active=1 WHERE id='stationnet'")
            db.commit()

            # Station World characters.
            character_creation.complete(db, alice, {**BUILD, "name": "Alice", "race": "human", "class": "warrior"})
            character_creation.complete(db, bob, {**BUILD, "name": "Bob", "race": "human", "class": "warrior"})
            db.execute("UPDATE mud_character_progression SET level=3,experience=400 "
                       "WHERE character_id=? AND track_key='shared'", (cid,))
            db.execute("UPDATE mud_character_progression SET level=2,experience=100 "
                       "WHERE character_id=? AND track_key='procedural'", (cid,))
            db.execute("INSERT OR REPLACE INTO mud_character_wallets(character_id,coins) VALUES(?,123)", (cid,))
            db.execute("INSERT OR REPLACE INTO mud_character_banks(character_id,coins) VALUES(?,45)", (cid,))
            db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type) "
                       "VALUES('legacy:lamp','Lamp','A lamp','misc')")
            db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id,possession_mode) "
                       "VALUES('legacy:lamp-1','legacy:lamp',?,'traditional')", (cid,))
            mud._set_player_location(db, bob, "traditional", "world_room", "stationnet:start")
            mud._set_player_location(db, alice, "procedural", "portal", mud.PORTAL_ROOM_ID)
            db.commit()
            user = db.execute("SELECT * FROM users WHERE id=?", (alice,)).fetchone()
            mud._expedition_action(db, user, {"theme_id": "generic-fantasy-dungeon", "difficulty": 1, "size": 8})
            db.commit()
            db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id,possession_mode) "
                       "VALUES('legacy:tonic-1','stationnet:shop:tonic',?,'procedural')", (cid,))
            db.commit()
            assert mud._active_expedition(db, alice) is not None
        with app.app_context():
            source = get_db()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            import sqlite3
            destination = sqlite3.connect(target)
            source.backup(destination)
            destination.execute("VACUUM")
            destination.close()
    finally:
        coordinator = app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
        shutil.rmtree(work, ignore_errors=True)
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
