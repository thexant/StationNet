"""Live-world actions: things the old builder did to the running world, not to its content.

They act immediately and never touch the draft: releasing a property holding,
sending a vehicle home or removing it, resetting script state, and (for SYSOPs)
granting builder access.
"""
from __future__ import annotations

from .. import mud, mud_scope, mud_worlds
from .world import WorldError


def _deliver(database, drafts):
    for draft in drafts or []:
        mud._insert_event(database, draft)


def summary(database, world_id):
    """Who is where, the world clock, and the live holdings and vehicles, by installed and local ID."""
    maps = {}
    try:
        mud_worlds.export_document(database, world_id, keep_id=True, maps_out=maps)
    except mud_worlds.WorldError:
        maps = {}
    rooms = maps.get("rooms", {})
    with mud_scope.use(world_id):
        occupancy = {}
        for row in database.execute(
                "SELECT l.location_key,COUNT(*) AS present FROM mud_player_locations l JOIN mud_world_rooms r "
                "ON r.id=l.location_key WHERE r.world_id=? AND l.location_type='world_room' GROUP BY l.location_key",
                (world_id,)):
            local = rooms.get(row["location_key"], row["location_key"])
            occupancy[local] = occupancy.get(local, 0) + row["present"]
        clock = mud._game_time(database)
        holdings = []
        for holding in database.execute(
                "SELECT h.id,h.created_at,p.name,p.kind FROM mud_property_holdings h JOIN mud_properties p "
                "ON p.id=h.property_id WHERE h.world_id=? ORDER BY p.name COLLATE NOCASE,h.created_at", (world_id,)):
            holdings.append({**dict(holding),
                             "owners": [row["display_name"] for row in mud._holding_owners(database, holding["id"])],
                             "stored": database.execute("SELECT COUNT(*) FROM mud_character_storage WHERE holding_id=?",
                                                        (holding["id"],)).fetchone()[0]})
        vehicles = []
        for row in database.execute(
                "SELECT v.id,v.room_id,v.home_room_id,v.fuel,d.name,c.display_name AS owner_name "
                "FROM mud_vehicles v JOIN mud_vehicle_definitions d ON d.id=v.definition_id "
                "LEFT JOIN mud_characters c ON c.id=v.owner_character_id WHERE v.world_id=? "
                "ORDER BY d.name COLLATE NOCASE,v.id", (world_id,)):
            vehicles.append({**dict(row), "room_name": (mud._world_room(database, row["room_id"]) or {}).get("name", "nowhere"),
                             "riders": len(mud._vehicle_riders(database, row["id"]))})
        characters = database.execute("SELECT COUNT(*) FROM mud_characters WHERE world_key=? AND "
                                      "creation_state<>'pending'", (world_id,)).fetchone()[0]
    return {"occupancy": occupancy, "clock": clock, "holdings": holdings, "vehicles": vehicles,
            "characters": characters}


def release_holding(database, world_id, holding_id):
    with mud_scope.use(world_id):
        holding = database.execute("SELECT p.name FROM mud_property_holdings h JOIN mud_properties p "
                                   "ON p.id=h.property_id WHERE h.id=? AND h.world_id=?",
                                   (holding_id, world_id)).fetchone()
        if holding is None:
            raise WorldError("That holding no longer exists.")
        _deliver(database, mud._release_holding(database, holding_id,
                                                f"A world builder ended your claim on {holding['name']}."))
        database.commit()
        return f"{holding['name']} released; stored items went back to their owners."


def _vehicle(database, world_id, vehicle_id):
    found = database.execute("SELECT id FROM mud_vehicles WHERE id=? AND world_id=?", (vehicle_id, world_id)).fetchone()
    if found is None:
        raise WorldError("That vehicle no longer exists.")
    return mud._vehicle(database, vehicle_id)


def vehicle_home(database, world_id, vehicle_id):
    with mud_scope.use(world_id):
        vehicle = _vehicle(database, world_id, vehicle_id)
        mud._unload(database, vehicle, f"The {vehicle['name']} is called home; you step off at {{name}}.")
        database.execute("UPDATE mud_vehicles SET room_id=home_room_id,return_due=NULL WHERE id=?", (vehicle_id,))
        database.commit()
        return f"The {vehicle['name']} was sent home."


def vehicle_remove(database, world_id, vehicle_id):
    with mud_scope.use(world_id):
        vehicle = _vehicle(database, world_id, vehicle_id)
        mud._remove_vehicle(database, vehicle_id, f"The {vehicle['name']} is taken away; you step off at {{name}}.")
        database.commit()
        return f"The {vehicle['name']} was removed."


def reset_scripts(database, world_id):
    with mud_scope.use(world_id):
        mud.reset_script_state(database, world_id)
        database.commit()
    return "World flags, trigger counts, object and exit states, and spawned NPCs were reset."


def builders(database):
    rows = database.execute(
        "SELECT u.id,u.username,u.callsign,u.is_admin,(p.user_id IS NOT NULL) AS granted FROM users u "
        "LEFT JOIN mud_builder_permissions p ON p.user_id=u.id ORDER BY u.username COLLATE NOCASE").fetchall()
    return [{"id": row["id"], "username": row["username"], "callsign": row["callsign"],
             "admin": bool(row["is_admin"]), "builder": bool(row["is_admin"] or row["granted"])} for row in rows]


def set_builder(database, user_id, enabled, granted_by):
    user = database.execute("SELECT id,username,callsign,is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        raise WorldError("That user does not exist.")
    if user["is_admin"]:
        return "Station operators already have world-builder access."
    if enabled:
        database.execute("INSERT INTO mud_builder_permissions(user_id,granted_by) VALUES(?,?) "
                         "ON CONFLICT(user_id) DO UPDATE SET granted_by=excluded.granted_by,"
                         "granted_at=CURRENT_TIMESTAMP", (user_id, granted_by))
    else:
        database.execute("DELETE FROM mud_builder_permissions WHERE user_id=?", (user_id,))
    database.commit()
    return (f"World-builder access {'granted to' if enabled else 'revoked from'} "
            f"{user['username']} - [{user['callsign']}].")
