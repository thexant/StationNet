"""Shared world and helpers for the world-life tests (clock, schedules, relationships, factions, crime)."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import shutil
import uuid

from stationnet import create_app, character_creation, mud, mud_wayfinding, mud_worlds
from stationnet.db import get_db

WORLD = "lifeworld"


def document(world_id=WORLD, **manifest):
    """A small town: home - square - forge in a row, with the tavern off the square."""
    return {
        "id": world_id, "name": "Life World", "author": "Test", "start_room": "square", **manifest,
        "rooms": [{"id": "home", "name": "Smith's Home", "description": "A cottage."},
                  {"id": "square", "name": "Town Square", "description": "A square."},
                  {"id": "forge", "name": "Forge", "description": "Hot."},
                  {"id": "tavern", "name": "Tavern", "description": "Loud."}],
        "exits": [{"from": "home", "to": "square", "name": "lane", "direction": "east"},
                  {"from": "square", "to": "home", "name": "lane", "direction": "west"},
                  {"from": "square", "to": "forge", "name": "forge door", "direction": "east"},
                  {"from": "forge", "to": "square", "name": "forge door", "direction": "west"},
                  {"from": "square", "to": "tavern", "name": "tavern door", "direction": "north"},
                  {"from": "tavern", "to": "square", "name": "tavern door", "direction": "south"}],
        "content": {
            "mud_item_definitions": [
                {"id": "nail", "name": "Iron Nail", "description": "A nail.", "item_type": "misc",
                 "properties_json": {"value": 10}},
                {"id": "lantern", "name": "Brass Lantern", "description": "A lantern.", "item_type": "misc",
                 "properties_json": {"value": 40}}],
            "mud_npc_definitions": [
                {"id": "smith", "name": "Smith", "description": "A broad smith.",
                 "behaviors_json": {"schedule": {"interval_seconds": 30, "entries": [
                     {"from": 8, "to": 18, "room": "forge", "open": True, "lines": ["The smith hammers."]},
                     {"from": 18, "to": 22, "room": "tavern"}]}}},
                {"id": "baker", "name": "Baker", "description": "Floury."}],
            "mud_npc_instances": [{"id": "smith-1", "definition_id": "smith", "room_id": "home"},
                                  {"id": "baker-1", "definition_id": "baker", "room_id": "square"}],
            "mud_npc_shops": [{"npc_definition_id": "smith", "buys_items": 1, "sell_percent": 50},
                              {"npc_definition_id": "baker", "buys_items": 1, "sell_percent": 50}],
            "mud_shop_stock": [{"id": "nails", "npc_definition_id": "smith", "item_definition_id": "nail", "price": 10},
                               {"id": "baker-nails", "npc_definition_id": "baker", "item_definition_id": "nail",
                                "price": 20}],
        },
    }


@contextmanager
def running(doc=None, users=("one", "two")):
    """An app with doc installed and active, and created characters placed in the square."""
    data = Path(__file__).parent / f"_society_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            ids = []
            for index, name in enumerate(users):
                uid = db.execute("INSERT INTO users(username,callsign,password_hash,connection_id,is_admin) "
                                 "VALUES(?,?,?,?,?)", (name, name.upper(), "unused", f"connection-{name}",
                                                       int(index == 0))).lastrowid
                character_creation.complete(db, uid, {
                    "name": name.title(), "race": "human", "class": "warrior",
                    "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                    "skills": ["lore", "guard"]})
                ids.append(uid)
            db.commit()
            installed = doc or document()
            mud_worlds.install(db, mud_worlds.package_bytes(installed))
            db.execute("UPDATE mud_world_packages SET active=0")
            db.execute("UPDATE mud_world_packages SET active=1 WHERE id=?", (installed["id"],))
            for uid in ids:
                place(db, uid, room(db, "square", installed["id"]))
            db.commit()
        yield app, ids
    finally:
        app.extensions["broadcast_coordinator"]._closed = True
        app.extensions["broadcast_coordinator"]._timer.cancel()
        mud_wayfinding._ROUTES.clear()
        mud_wayfinding._GRAPHS.clear()
        shutil.rmtree(data)


def room(db, local, world=WORLD):
    return db.execute("SELECT id FROM mud_world_rooms WHERE world_id=? AND id LIKE ?",
                      (world, f"%:room:{local}")).fetchone()["id"]


def installed(db, table, local, world=WORLD):
    return db.execute(f"SELECT id FROM {table} WHERE id LIKE ?", (f"world:{world}:%:{table}:{local}",)).fetchone()["id"]


def place(db, uid, room_id):
    mud._set_player_location(db, uid, "traditional", "world_room", room_id)
    db.execute("DELETE FROM mud_presence WHERE user_id=?", (uid,))
    db.execute("INSERT INTO mud_presence(user_id,connection_id,client_id,room_id,location_type,location_key,last_seen) "
               "VALUES(?,?,?,?,?,?,?)", (uid, f"connection-{uid}", f"client-{uid}-1", room_id, "world_room", room_id,
                                         mud._scheduled_now()))


def user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def act(db, uid, text):
    definition, parameters = mud.actions.from_command(text)
    return "\n".join(draft.body for draft in definition.handler(db, user(db, uid), parameters))


def set_society(db, **sections):
    settings = mud.society_settings(db)
    for key, value in sections.items():
        settings[key] = {**settings[key], **value}
    from stationnet import mud_society
    import json
    db.execute("UPDATE mud_world_packages SET society_json=? WHERE active=1",
               (json.dumps(mud_society.changed_settings(mud_society.validate_settings(settings))),))


def at_hour(hour, day_minutes=1440, start_hour=0):
    """A real moment at which a clock with these settings reads hour:00 (on 1440, plain UTC)."""
    assert day_minutes == 1440 and start_hour == 0
    return datetime(2026, 10, 28, hour, 0, 5, tzinfo=timezone.utc)


def npc_room(db, local, world=WORLD):
    return db.execute("SELECT room_id FROM mud_npc_instances WHERE id=?",
                      (installed(db, "mud_npc_instances", local, world),)).fetchone()["room_id"]


def client(app, uid):
    test_client = app.test_client()
    with test_client.session_transaction() as state:
        state["user_id"] = uid
        state["csrf_token"] = "test-token"
    return test_client
