"""Wilderness: coordinate-placed world rooms that share reusable room presets.

A wild room is a slim row in mud_world_rooms holding only its coordinates,
preset and region; its name and text are chosen from the preset by a hash of
its position, so a room reads the same on every visit while neighbours vary.
Exits between grid neighbours are computed rather than stored, and a stored
exit in the same direction always wins, so roads, gates and caves keep their
authored behaviour. Every cell is still a real room with its own occupants,
contents and persistent changes.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import uuid

from .dungeons import _weighted


KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
REFERENCE_RE = re.compile(r"^@(-?\d{1,6}),(-?\d{1,6}),(-?\d{1,6})$")
WILDERNESS_FILE = "wilderness.json"
TERRAINS = ("grassland", "forest", "dense-forest", "hills", "mountains", "desert", "swamp",
            "tundra", "water", "road", "custom")
LIGHTS = ("bright", "dim", "dark")
EXPOSURES = ("open", "sheltered")
MAX_PRESETS = 200
MAX_REGIONS = 200
MAX_DESCRIPTIONS = 40
MAX_FILL_CELLS = 250_000
MAX_WORLD_CELLS = 1_000_000
MAX_RUNS = 200_000
COORDINATE_LIMIT = 100_000
MAP_LIMIT = 101
CHUNK = 5000
DELTAS = {"north": (0, -1, 0), "northeast": (1, -1, 0), "east": (1, 0, 0), "southeast": (1, 1, 0),
          "south": (0, 1, 0), "southwest": (-1, 1, 0), "west": (-1, 0, 0), "northwest": (-1, -1, 0),
          "up": (0, 0, 1), "down": (0, 0, -1)}
DIAGONALS = {"northeast", "southeast", "southwest", "northwest"}
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east",
            "northeast": "southwest", "southwest": "northeast", "northwest": "southeast",
            "southeast": "northwest", "up": "down", "down": "up"}
ROOM_COLUMNS = "id,name,description,world_id,kind,x,y,z,preset_id,region_id,grid_link,blocked_dirs,resources_at"
TEMPLATE_DIRECTORY = Path(__file__).with_name("room_preset_templates")
# Tests replace this with a seeded generator; play uses unpredictable rolls.
_RNG = random.SystemRandom()
_PRESETS = {}
_REGIONS = {}


class WildernessError(ValueError):
    pass


# --- Validation -------------------------------------------------------------

def _text(value, label, low, high):
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not low <= len(text) <= high:
        raise WildernessError(f"{label} must contain {low} to {high} characters.")
    return text


def _whole(value, label, low, high, default=None):
    if value is None and default is not None:
        return default
    if type(value) is not int or not low <= value <= high:
        raise WildernessError(f"{label} must be a whole number from {low} to {high}.")
    return value


def _number(value, label, low, high, default):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise WildernessError(f"{label} must be a number from {low} to {high}.")
    return value


def _object(value, label, allowed):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WildernessError(f"{label} must be an object.")
    unknown = set(value) - set(allowed)
    if unknown:
        raise WildernessError(f"{label} has unsupported fields: {', '.join(sorted(unknown))}.")
    return value


def _weighted_texts(value, label, minimum, maximum, length):
    """[{text, weight}] lists; plain strings are shorthand for weight 1."""
    if value is None:
        value = []
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise WildernessError(f"{label} must be a list of {minimum} to {maximum} entries.")
    result = []
    for entry in value:
        if isinstance(entry, str):
            entry = {"text": entry}
        entry = _object(entry, f"{label} entry", ("text", "weight"))
        text = entry.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= length:
            raise WildernessError(f"{label} text must contain 1 to {length} characters.")
        result.append({"text": text.strip(), "weight": _whole(entry.get("weight"), f"{label} weight", 1, 1000, 1)})
    return result


def _reference(value, label):
    if not isinstance(value, str) or not 1 <= len(value) <= 200:
        raise WildernessError(f"{label} must name a definition ID.")
    return value


def _creatures(value, label):
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > 50:
        raise WildernessError(f"{label} must be a list of at most 50 creatures.")
    result = []
    for entry in value:
        entry = _object(entry, f"{label} entry", ("npc", "weight", "min_danger", "max_danger"))
        low = _whole(entry.get("min_danger"), "Creature min_danger", 1, 10, 1)
        high = _whole(entry.get("max_danger"), "Creature max_danger", 1, 10, 10)
        if low > high:
            raise WildernessError("Creature min_danger cannot exceed max_danger.")
        result.append({"npc": _reference(entry.get("npc"), "Creature npc"),
                       "weight": _whole(entry.get("weight"), "Creature weight", 1, 1000, 1),
                       "min_danger": low, "max_danger": high})
    return result


def validate_preset(value):
    """Return a preset in canonical form; unknown fields and out-of-range values are refused."""
    preset = _object(value, "A room preset", ("key", "name", "terrain", "glyph", "links", "names",
                                               "descriptions", "environment", "encounters", "resources"))
    if not preset:
        raise WildernessError("A room preset must be an object.")
    key = preset.get("key")
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        raise WildernessError("Preset keys are 1-32 lowercase letters, digits and hyphens, starting with a letter.")
    terrain = preset.get("terrain", "custom")
    if terrain not in TERRAINS:
        raise WildernessError(f"Preset terrain must be one of: {', '.join(TERRAINS)}.")
    glyph = preset.get("glyph", ".")
    if not isinstance(glyph, str) or len(glyph) != 1 or not glyph.isprintable() or glyph in " @*P":
        raise WildernessError("Preset glyph must be one printable character other than a space, @, * or P.")
    links = str(preset.get("links", "4"))
    if links not in ("4", "8"):
        raise WildernessError('Preset links must be "4" (north, east, south, west) or "8" (adds diagonals).')
    environment = _object(preset.get("environment"), "Preset environment", ("light", "exposure", "lines", "tags"))
    light = environment.get("light", "bright")
    exposure = environment.get("exposure", "open")
    if light not in LIGHTS or exposure not in EXPOSURES:
        raise WildernessError("Environment light must be bright, dim or dark; exposure must be open or sheltered.")
    tags = environment.get("tags", [])
    if (not isinstance(tags, list) or len(tags) > 20
            or any(not isinstance(tag, str) or not KEY_RE.fullmatch(tag) for tag in tags)):
        raise WildernessError("Environment tags must be at most 20 lowercase keys.")
    encounters = _object(preset.get("encounters"), "Preset encounters",
                         ("chance", "max_live", "despawn_seconds", "creatures"))
    resources = _object(preset.get("resources"), "Preset resources", ("respawn_seconds", "max_per_room", "items"))
    items = resources.get("items", [])
    if not isinstance(items, list) or len(items) > 50:
        raise WildernessError("Preset resource items must be a list of at most 50 entries.")
    clean_items = []
    for entry in items:
        entry = _object(entry, "Resource item", ("item", "chance", "count"))
        count = _object(entry.get("count"), "Resource count", ("minimum", "maximum"))
        low = _whole(count.get("minimum"), "Resource minimum count", 1, 5, 1)
        high = _whole(count.get("maximum"), "Resource maximum count", 1, 5, low)
        if low > high:
            raise WildernessError("Resource minimum count cannot exceed the maximum.")
        clean_items.append({"item": _reference(entry.get("item"), "Resource item"),
                            "chance": _number(entry.get("chance"), "Resource chance", 0, 100, 100),
                            "count": {"minimum": low, "maximum": high}})
    return {
        "key": key,
        "name": _text(preset.get("name"), "Preset name", 2, 80),
        "terrain": terrain, "glyph": glyph, "links": links,
        "names": _weighted_texts(preset.get("names"), "Preset room names", 0, 20, 80),
        "descriptions": _weighted_texts(preset.get("descriptions"), "Preset descriptions", 1, MAX_DESCRIPTIONS, 4000),
        "environment": {"light": light, "exposure": exposure, "tags": tags,
                        "lines": _weighted_texts(environment.get("lines"), "Environment lines", 0, 20, 400)},
        "encounters": {"chance": _number(encounters.get("chance"), "Encounter chance", 0, 100, 0),
                       "max_live": _whole(encounters.get("max_live"), "Encounter max_live", 0, 5, 1),
                       "despawn_seconds": _whole(encounters.get("despawn_seconds"), "Encounter despawn_seconds",
                                                 10, 86400, 600),
                       "creatures": _creatures(encounters.get("creatures"), "Encounter creatures")},
        "resources": {"respawn_seconds": _whole(resources.get("respawn_seconds"), "Resource respawn_seconds",
                                                60, 86400, 3600),
                      "max_per_room": _whole(resources.get("max_per_room"), "Resource max_per_room", 0, 5, 2),
                      "items": clean_items},
    }


def validate_region_overrides(value):
    """Local adjustments a region applies to every preset used inside it."""
    overrides = _object(value, "Region overrides", ("danger", "encounter_chance", "encounter_multiplier",
                                                     "creatures", "resource_multiplier", "name_prefix"))
    result = {"danger": _whole(overrides.get("danger"), "Region danger", 1, 10, 1)}
    if overrides.get("encounter_chance") is not None:
        result["encounter_chance"] = _number(overrides["encounter_chance"], "Region encounter_chance", 0, 100, 0)
    result["encounter_multiplier"] = _number(overrides.get("encounter_multiplier"), "Region encounter_multiplier",
                                             0, 5, 1)
    result["resource_multiplier"] = _number(overrides.get("resource_multiplier"), "Region resource_multiplier",
                                            0, 5, 1)
    if overrides.get("creatures") is not None:
        creatures = _object(overrides["creatures"], "Region creatures", ("mode", "list"))
        mode = creatures.get("mode", "add")
        if mode not in ("add", "replace"):
            raise WildernessError("Region creatures mode must be add or replace.")
        result["creatures"] = {"mode": mode, "list": _creatures(creatures.get("list"), "Region creatures list")}
    if overrides.get("name_prefix"):
        result["name_prefix"] = _text(overrides["name_prefix"], "Region name prefix", 1, 30)
    return result


def preset_references(preset):
    """(npc IDs, item IDs) a preset names."""
    return ({entry["npc"] for entry in preset["encounters"]["creatures"]},
            {entry["item"] for entry in preset["resources"]["items"]})


def region_references(overrides):
    return {entry["npc"] for entry in overrides.get("creatures", {}).get("list", [])}


def remap_preset(preset, npc_mapper, item_mapper):
    preset = json.loads(json.dumps(preset))
    for entry in preset["encounters"]["creatures"]:
        entry["npc"] = npc_mapper(entry["npc"])
    for entry in preset["resources"]["items"]:
        entry["item"] = item_mapper(entry["item"])
    return preset


def remap_overrides(overrides, npc_mapper):
    overrides = json.loads(json.dumps(overrides))
    for entry in overrides.get("creatures", {}).get("list", []):
        entry["npc"] = npc_mapper(entry["npc"])
    return overrides


def missing_references(database, npcs, items):
    """Human-readable problems for definitions that do not exist."""
    problems = []
    for table, label, values in (("mud_npc_definitions", "NPC", npcs), ("mud_item_definitions", "item", items)):
        for value in sorted(values):
            if database.execute(f"SELECT 1 FROM {table} WHERE id=?", (value,)).fetchone() is None:
                problems.append(f"unknown {label} {value}")
    return problems


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# --- Storage ----------------------------------------------------------------

def bundled_presets():
    """Starter presets shipped with the station, by key."""
    result = {}
    for path in sorted(TEMPLATE_DIRECTORY.glob("*.json")):
        try:
            preset = validate_preset(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        result[preset["key"]] = preset
    return result


def world_presets(database, world_id):
    return database.execute(
        "SELECT p.*,(SELECT COUNT(*) FROM mud_world_rooms r WHERE r.preset_id=p.id) AS cell_count "
        "FROM mud_world_room_presets p WHERE p.world_id=? ORDER BY p.name COLLATE NOCASE,p.id", (world_id,),
    ).fetchall()


def world_regions(database, world_id):
    return database.execute(
        "SELECT g.*,(SELECT COUNT(*) FROM mud_world_rooms r WHERE r.region_id=g.id) AS cell_count "
        "FROM mud_world_regions g WHERE g.world_id=? ORDER BY g.name COLLATE NOCASE,g.id", (world_id,),
    ).fetchall()


def save_preset(database, world_id, preset, preset_id=None, new_id=None):
    """Create or revise a world preset; the caller owns the transaction."""
    preset = validate_preset(preset)
    duplicate = database.execute(
        "SELECT id FROM mud_world_room_presets WHERE world_id=? AND local_key=? AND id<>COALESCE(?,'')",
        (world_id, preset["key"], preset_id),
    ).fetchone()
    if duplicate:
        raise WildernessError(f"This world already has a room preset with the key {preset['key']}.")
    stored = canonical(preset)
    if preset_id is None:
        if database.execute("SELECT COUNT(*) FROM mud_world_room_presets WHERE world_id=?",
                            (world_id,)).fetchone()[0] >= MAX_PRESETS:
            raise WildernessError(f"A world can hold at most {MAX_PRESETS} room presets.")
        preset_id = new_id or f"world:room-preset:{uuid.uuid4().hex}"
        database.execute("INSERT INTO mud_world_room_presets(id,world_id,local_key,name,preset_json) "
                         "VALUES(?,?,?,?,?)", (preset_id, world_id, preset["key"], preset["name"], stored))
        return preset_id
    existing = database.execute("SELECT preset_json FROM mud_world_room_presets WHERE id=? AND world_id=?",
                                (preset_id, world_id)).fetchone()
    if existing is None:
        raise WildernessError("That room preset was not found.")
    database.execute(
        "UPDATE mud_world_room_presets SET local_key=?,name=?,preset_json=?,revision=revision+?,"
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (preset["key"], preset["name"], stored, int(existing["preset_json"] != stored), preset_id),
    )
    return preset_id


def save_region(database, world_id, key, name, overrides, region_id=None, new_id=None):
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        raise WildernessError("Region keys are 1-32 lowercase letters, digits and hyphens, starting with a letter.")
    name = _text(name, "Region name", 2, 80)
    stored = canonical(validate_region_overrides(overrides))
    if database.execute("SELECT 1 FROM mud_world_regions WHERE world_id=? AND local_key=? AND id<>COALESCE(?,'')",
                        (world_id, key, region_id)).fetchone():
        raise WildernessError(f"This world already has a region with the key {key}.")
    if region_id is None:
        if database.execute("SELECT COUNT(*) FROM mud_world_regions WHERE world_id=?",
                            (world_id,)).fetchone()[0] >= MAX_REGIONS:
            raise WildernessError(f"A world can hold at most {MAX_REGIONS} regions.")
        region_id = new_id or f"world:region:{uuid.uuid4().hex}"
        database.execute("INSERT INTO mud_world_regions(id,world_id,local_key,name,overrides_json) VALUES(?,?,?,?,?)",
                         (region_id, world_id, key, name, stored))
        return region_id
    if database.execute("UPDATE mud_world_regions SET local_key=?,name=?,overrides_json=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND world_id=?", (key, name, stored, region_id, world_id)).rowcount == 0:
        raise WildernessError("That region was not found.")
    return region_id


def delete_preset(database, world_id, preset_id, reassign_to=None):
    """Remove a preset; cells using it must first be moved to another preset."""
    used = database.execute("SELECT COUNT(*) FROM mud_world_rooms WHERE preset_id=?", (preset_id,)).fetchone()[0]
    if used and not reassign_to:
        raise WildernessError(f"{used} wilderness rooms use this preset. Reassign them to another preset first.")
    if reassign_to:
        if reassign_to == preset_id or database.execute(
                "SELECT 1 FROM mud_world_room_presets WHERE id=? AND world_id=?", (reassign_to, world_id)).fetchone() is None:
            raise WildernessError("Choose a different preset of this world to take over its rooms.")
        database.execute("UPDATE mud_world_rooms SET preset_id=?,updated_at=CURRENT_TIMESTAMP WHERE preset_id=?",
                         (reassign_to, preset_id))
    if database.execute("DELETE FROM mud_world_room_presets WHERE id=? AND world_id=?",
                        (preset_id, world_id)).rowcount == 0:
        raise WildernessError("That room preset was not found.")
    _PRESETS.pop(preset_id, None)
    return used


def delete_region(database, world_id, region_id):
    database.execute("UPDATE mud_world_rooms SET region_id=NULL WHERE region_id=? AND world_id=?", (region_id, world_id))
    if database.execute("DELETE FROM mud_world_regions WHERE id=? AND world_id=?", (region_id, world_id)).rowcount == 0:
        raise WildernessError("That region was not found.")
    _REGIONS.pop(region_id, None)


def preset_for(database, preset_id):
    """A stored preset, parsed once per revision."""
    if not preset_id:
        return None
    row = database.execute("SELECT revision,preset_json FROM mud_world_room_presets WHERE id=?",
                           (preset_id,)).fetchone()
    if row is None:
        return None
    cached = _PRESETS.get(preset_id)
    if cached and cached[0] == (row["revision"], row["preset_json"]):
        return cached[1]
    try:
        preset = validate_preset(json.loads(row["preset_json"]))
    except (ValueError, TypeError):
        return None
    _PRESETS[preset_id] = ((row["revision"], row["preset_json"]), preset)
    return preset


def region_for(database, region_id):
    if not region_id:
        return {}
    row = database.execute("SELECT overrides_json FROM mud_world_regions WHERE id=?", (region_id,)).fetchone()
    if row is None:
        return {}
    cached = _REGIONS.get(region_id)
    if cached and cached[0] == row["overrides_json"]:
        return cached[1]
    try:
        overrides = validate_region_overrides(json.loads(row["overrides_json"]))
    except (ValueError, TypeError):
        overrides = {}
    _REGIONS[region_id] = (row["overrides_json"], overrides)
    return overrides


def effective_preset(preset, overrides):
    """A preset as a region sees it: adjusted encounter odds, creatures and resource odds."""
    danger = overrides.get("danger", 1)
    encounters = dict(preset["encounters"])
    chance = overrides.get("encounter_chance", encounters["chance"])
    encounters["chance"] = min(100, chance * overrides.get("encounter_multiplier", 1))
    creatures = list(encounters["creatures"])
    if "creatures" in overrides:
        extra = overrides["creatures"]["list"]
        creatures = extra if overrides["creatures"]["mode"] == "replace" else creatures + extra
    encounters["creatures"] = [entry for entry in creatures if entry["min_danger"] <= danger <= entry["max_danger"]]
    multiplier = overrides.get("resource_multiplier", 1)
    resources = {**preset["resources"],
                 "items": [{**entry, "chance": min(100, entry["chance"] * multiplier)}
                           for entry in preset["resources"]["items"]]}
    return {**preset, "encounters": encounters, "resources": resources, "danger": danger}


# --- Cells ------------------------------------------------------------------

def cell_id(world_id, x, y, z):
    return f"world:{world_id}:cell:{x}:{y}:{z}"


def cell_reference(x, y, z):
    """The package-local name of a wilderness cell."""
    return f"@{x},{y},{z}"


def parse_reference(value):
    match = REFERENCE_RE.fullmatch(value) if isinstance(value, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def _seeded(*parts):
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def resolve_room(database, row):
    """A room as players see it; wild rooms take their name and text from their preset."""
    room = dict(row)
    if room.get("kind") != "wild":
        return room
    preset = preset_for(database, room.get("preset_id"))
    if preset is None:
        room.update(name="Uncharted Wilds", description="This land has not been described yet.",
                    terrain="custom", glyph="?")
        return room
    overrides = region_for(database, room.get("region_id"))
    position = (room["world_id"], room["x"], room["y"], room["z"], preset["key"])
    name = (_weighted(_seeded(*position, "name"), preset["names"])["text"]
            if preset["names"] else preset["name"])
    if overrides.get("name_prefix"):
        name = f"{overrides['name_prefix']} {name}"
    description = _weighted(_seeded(*position, "description"), preset["descriptions"])["text"]
    if preset["environment"]["lines"]:
        description += " " + _weighted(_seeded(*position, "environment"), preset["environment"]["lines"])["text"]
    room.update(name=name, description=description, terrain=preset["terrain"], glyph=preset["glyph"])
    return room


def room_row(database, room_id):
    return database.execute(f"SELECT {ROOM_COLUMNS} FROM mud_world_rooms WHERE id=?", (room_id,)).fetchone()


def _blocked(row):
    return {part for part in (row["blocked_dirs"] or "").split(",") if part}


def _links(database, row):
    if row["kind"] != "wild":
        return "8"
    preset = preset_for(database, row["preset_id"])
    return preset["links"] if preset else "4"


def touch_navigation(database):
    """Tell cached route graphs that rooms appeared or vanished; see mud_wayfinding."""
    database.execute("UPDATE mud_navigation_revision SET revision=revision+1 WHERE id=1")


def grid_linked(direction, own_blocked, own_links, other_grid_link, other_blocked, other_links):
    """Whether a grid-linked room reaches its neighbour in direction; shared with route finding."""
    if direction in own_blocked or not other_grid_link or OPPOSITE[direction] in other_blocked:
        return False
    return direction not in DIAGONALS or (own_links == "8" and other_links == "8")


def grid_exits(database, room_id, taken_directions=()):
    """Computed exits to grid neighbours, shaped like stored exit rows.

    A link exists only when both rooms are grid-linked, neither walls off the
    shared edge, and, for diagonals, both rooms allow eight-way links. A stored
    exit in the same direction replaces the computed one.
    """
    room = room_row(database, room_id)
    if room is None or room["x"] is None or not room["grid_link"]:
        return []
    x, y, z = room["x"], room["y"], room["z"]
    neighbours = {(row["x"] - x, row["y"] - y, row["z"] - z): row for row in database.execute(
        f"SELECT {ROOM_COLUMNS} FROM mud_world_rooms WHERE world_id=? AND z BETWEEN ? AND ? "
        "AND y BETWEEN ? AND ? AND x BETWEEN ? AND ? AND id<>?",
        (room["world_id"], z - 1, z + 1, y - 1, y + 1, x - 1, x + 1, room_id),
    )}
    own_blocked = _blocked(room)
    own_links = _links(database, room)
    taken = {str(direction).casefold() for direction in taken_directions if direction}
    result = []
    for direction, delta in DELTAS.items():
        other = neighbours.get(delta)
        if other is None or direction in taken or not grid_linked(
                direction, own_blocked, own_links, other["grid_link"], _blocked(other),
                _links(database, other) if direction in DIAGONALS else ""):
            continue
        result.append({"id": f"grid:{room_id}:{direction}", "name": direction, "direction": direction,
                       "to_room_id": other["id"], "key_item_id": None, "pick_difficulty": None,
                       "conditions_json": "[]", "conditions_message": "",
                       "destination_name": resolve_room(database, other)["name"], "script_state": ""})
    return result


def parse_shape(values):
    """A fill area from form or JSON values: rect, ellipse (inside the corners) or a cell list."""
    kind = str(values.get("shape") or "rect")
    if kind not in ("rect", "ellipse", "cells"):
        raise WildernessError("Shape must be rect, ellipse or cells.")

    def coordinate(name):
        try:
            number = int(str(values.get(name, "")).strip())
        except ValueError:
            raise WildernessError(f"{name} must be a whole number.") from None
        if not -COORDINATE_LIMIT <= number <= COORDINATE_LIMIT:
            raise WildernessError(f"Coordinates must be between -{COORDINATE_LIMIT} and {COORDINATE_LIMIT}.")
        return number

    z = coordinate("z")
    if kind == "cells":
        raw = values.get("cells")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "[]")
            except json.JSONDecodeError:
                raise WildernessError("cells must be a JSON list of [x, y] pairs.") from None
        if (not isinstance(raw, list) or not raw or len(raw) > MAX_FILL_CELLS
                or any(not isinstance(cell, list) or len(cell) != 2 or any(type(part) is not int
                       or abs(part) > COORDINATE_LIMIT for part in cell) for cell in raw)):
            raise WildernessError("cells must be a non-empty JSON list of [x, y] whole-number pairs.")
        cells = sorted({(cell[0], cell[1]) for cell in raw})
        xs, ys = [cell[0] for cell in cells], [cell[1] for cell in cells]
        return {"shape": kind, "z": z, "cells": cells,
                "x1": min(xs), "y1": min(ys), "x2": max(xs), "y2": max(ys)}
    x1, y1, x2, y2 = (coordinate(name) for name in ("x1", "y1", "x2", "y2"))
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if (x2 - x1 + 1) * (y2 - y1 + 1) > MAX_FILL_CELLS:
        raise WildernessError(f"One operation can cover at most {MAX_FILL_CELLS:,} rooms.")
    return {"shape": kind, "z": z, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def shape_cells(shape):
    """Every (x, y) inside a parsed shape."""
    if shape["shape"] == "cells":
        yield from shape["cells"]
        return
    x1, y1, x2, y2 = shape["x1"], shape["y1"], shape["x2"], shape["y2"]
    if shape["shape"] == "rect":
        for y in range(y1, y2 + 1):
            for x in range(x1, x2 + 1):
                yield x, y
        return
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    rx, ry = (x2 - x1) / 2 + 0.5, (y2 - y1) / 2 + 0.5
    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1:
                yield x, y


def _existing(database, world_id, shape):
    return {(row["x"], row["y"]): row for row in database.execute(
        "SELECT id,kind,x,y FROM mud_world_rooms WHERE world_id=? AND z=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
        (world_id, shape["z"], shape["x1"], shape["x2"], shape["y1"], shape["y2"]),
    )}


def fill(database, world_id, shape, preset_id, region_id=None, mode="empty"):
    """Fill an area with a preset; authored rooms are never overwritten.

    mode 'empty' only creates rooms where none exist; 'replace' also moves
    existing wild rooms to the new preset and region in place, so their IDs,
    items and occupants survive. The caller owns the transaction.
    """
    if mode not in ("empty", "replace"):
        raise WildernessError("Fill mode must be empty or replace.")
    if database.execute("SELECT 1 FROM mud_world_room_presets WHERE id=? AND world_id=?",
                        (preset_id, world_id)).fetchone() is None:
        raise WildernessError("Choose a room preset of this world.")
    if region_id and database.execute("SELECT 1 FROM mud_world_regions WHERE id=? AND world_id=?",
                                      (region_id, world_id)).fetchone() is None:
        raise WildernessError("Choose a region of this world, or none.")
    existing = _existing(database, world_id, shape)
    inserts, updates, skipped = [], [], 0
    z = shape["z"]
    for x, y in shape_cells(shape):
        row = existing.get((x, y))
        if row is None:
            inserts.append((cell_id(world_id, x, y, z), world_id, x, y, z, preset_id, region_id or None))
        elif row["kind"] != "wild":
            skipped += 1
        elif mode == "replace":
            updates.append((preset_id, region_id or None, row["id"]))
    total = database.execute("SELECT COUNT(*) FROM mud_world_rooms WHERE world_id=? AND kind='wild'",
                             (world_id,)).fetchone()[0]
    if total + len(inserts) > MAX_WORLD_CELLS:
        raise WildernessError(f"A world can hold at most {MAX_WORLD_CELLS:,} wilderness rooms.")
    before = database.total_changes
    for start in range(0, len(inserts), CHUNK):
        database.executemany(
            "INSERT OR IGNORE INTO mud_world_rooms(id,name,description,world_id,kind,x,y,z,preset_id,region_id) "
            "VALUES(?,'','',?,'wild',?,?,?,?,?)", inserts[start:start + CHUNK])
    created = database.total_changes - before
    if created:
        touch_navigation(database)
    for start in range(0, len(updates), CHUNK):
        database.executemany("UPDATE mud_world_rooms SET preset_id=?,region_id=?,updated_at=CURRENT_TIMESTAMP "
                             "WHERE id=? AND kind='wild'", updates[start:start + CHUNK])
    return {"created": created, "updated": len(updates), "skipped_authored": skipped}


def cells_in(database, world_id, shape):
    """IDs of wild rooms inside a shape."""
    existing = _existing(database, world_id, shape)
    return [existing[cell]["id"] for cell in shape_cells(shape)
            if cell in existing and existing[cell]["kind"] == "wild"]


def entrance_count(database, room_ids):
    total = 0
    for start in range(0, len(room_ids), 500):
        chunk = room_ids[start:start + 500]
        total += database.execute(
            f"SELECT COUNT(*) FROM mud_world_dungeon_entrances WHERE room_id IN ({','.join('?' * len(chunk))})",
            chunk).fetchone()[0]
    return total


def delete_cells(database, room_ids):
    """Delete wild rooms and what lies in them; occupants must already have been moved."""
    for start in range(0, len(room_ids), 500):
        chunk = room_ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        database.execute(f"DELETE FROM mud_item_instances WHERE room_id IN ({marks})", chunk)
        database.execute(f"DELETE FROM mud_world_rooms WHERE kind='wild' AND id IN ({marks})", chunk)
    touch_navigation(database)


def convert_to_authored(database, room_id):
    """Turn a wild room into an editable authored room that keeps its current text and position."""
    row = room_row(database, room_id)
    if row is None or row["kind"] != "wild":
        raise WildernessError("Only wilderness rooms can be converted.")
    room = resolve_room(database, row)
    database.execute("UPDATE mud_world_rooms SET kind='authored',name=?,description=?,preset_id=NULL,"
                     "updated_at=CURRENT_TIMESTAMP WHERE id=?", (room["name"][:80], room["description"][:4000], room_id))
    return room


def parse_position(x, y, z):
    """Optional grid coordinates from form text; all blank removes a room from the grid."""
    values = [str(value or "").strip() for value in (x, y, z)]
    if not any(values):
        return None
    try:
        position = tuple(int(value) for value in values)
    except ValueError:
        raise WildernessError("Grid coordinates must be three whole numbers, or all blank.") from None
    if any(abs(value) > COORDINATE_LIMIT for value in position):
        raise WildernessError(f"Coordinates must be between -{COORDINATE_LIMIT} and {COORDINATE_LIMIT}.")
    return position


def place_authored(database, room_id, position, grid_link=True, blocked_dirs=""):
    """Put an authored room on the grid, or take it off with position None.

    A wild room already at the spot is absorbed: its NPCs, items, spawns,
    discoveries, entrances and exits move to the authored room, then the wild
    room is removed. Players standing there must already have been moved.
    Returns the absorbed wild room's ID, if any.
    """
    room = room_row(database, room_id)
    if room is None or room["kind"] != "authored":
        raise WildernessError("Only authored rooms can be placed on the grid by hand.")
    blocked = validate_blocked(blocked_dirs)
    absorbed = None
    if position is not None:
        other = database.execute("SELECT id,kind,name FROM mud_world_rooms WHERE world_id=? AND x=? AND y=? AND z=? AND id<>?",
                                 (room["world_id"], *position, room_id)).fetchone()
        if other is not None and other["kind"] != "wild":
            raise WildernessError(f"{other['name']} already stands at {position[0]}, {position[1]}, {position[2]}.")
        if other is not None:
            absorbed = other["id"]
            for table, column in (("mud_npc_instances", "room_id"), ("mud_item_instances", "room_id"),
                                  ("mud_item_spawns", "room_id"), ("mud_room_discoveries", "room_id"),
                                  ("mud_world_destinations", "room_id"),
                                  ("mud_world_dungeon_entrances", "room_id"),
                                  ("mud_world_dungeon_entrances", "return_room_id"),
                                  ("mud_world_exits", "from_room_id"), ("mud_world_exits", "to_room_id")):
                database.execute(f"UPDATE OR IGNORE {table} SET {column}=? WHERE {column}=?", (room_id, absorbed))
            database.execute("DELETE FROM mud_world_rooms WHERE id=?", (absorbed,))
    x, y, z = position if position is not None else (None, None, None)
    database.execute("UPDATE mud_world_rooms SET x=?,y=?,z=?,grid_link=?,blocked_dirs=?,updated_at=CURRENT_TIMESTAMP "
                     "WHERE id=?", (x, y, z, int(bool(grid_link)), blocked, room_id))
    return absorbed


def map_window(database, world_id, x, y, z, width, height, *, hide_users=()):
    """A rectangle of one layer: palette indices per cell plus landmark and player markers.

    Index 0 is empty and 1 is an authored room; presets follow from 2.
    """
    width, height = max(1, min(MAP_LIMIT, width)), max(1, min(MAP_LIMIT, height))
    x1, y1 = x - width // 2, y - height // 2
    x2, y2 = x1 + width - 1, y1 + height - 1
    bounds = (world_id, z, x1, x2, y1, y2)
    palette = [{"key": "", "glyph": " ", "name": "Nothing", "terrain": ""},
               {"key": "*", "glyph": "*", "name": "Landmark", "terrain": ""}]
    indices = {}
    grid = [[0] * width for _ in range(height)]
    authored = []
    for row in database.execute(
        "SELECT id,name,kind,x,y,preset_id FROM mud_world_rooms WHERE world_id=? AND z=? "
        "AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?", bounds,
    ):
        if row["kind"] != "wild":
            grid[row["y"] - y1][row["x"] - x1] = 1
            authored.append({"x": row["x"], "y": row["y"], "id": row["id"], "name": row["name"]})
            continue
        if row["preset_id"] not in indices:
            preset = preset_for(database, row["preset_id"])
            indices[row["preset_id"]] = len(palette)
            palette.append({"key": preset["key"] if preset else "?", "glyph": preset["glyph"] if preset else "?",
                            "name": preset["name"] if preset else "Unknown preset",
                            "terrain": preset["terrain"] if preset else "custom"})
        grid[row["y"] - y1][row["x"] - x1] = indices[row["preset_id"]]
    hidden = tuple(hide_users)
    players = [[row["x"], row["y"]] for row in database.execute(
        "SELECT DISTINCT r.x,r.y,p.user_id FROM mud_presence p JOIN mud_world_rooms r ON r.id=p.location_key "
        "WHERE p.location_type='world_room' AND r.world_id=? AND r.z=? AND r.x BETWEEN ? AND ? "
        "AND r.y BETWEEN ? AND ? AND p.user_id NOT IN (SELECT user_id FROM mud_player_stealth)", bounds,
    ) if row["user_id"] not in hidden]
    npcs = [[row["x"], row["y"]] for row in database.execute(
        "SELECT DISTINCT r.x,r.y FROM mud_npc_instances n JOIN mud_world_rooms r ON r.id=n.room_id "
        "WHERE r.world_id=? AND r.z=? AND r.x BETWEEN ? AND ? AND r.y BETWEEN ? AND ?", bounds)]
    return {"origin": [x1, y1], "z": z, "width": width, "height": height, "palette": palette,
            "grid": grid, "authored": authored, "players": players, "npcs": npcs}


# --- Runtime ----------------------------------------------------------------

def effective_for_room(database, room):
    if not room or room.get("kind") != "wild":
        return None
    preset = preset_for(database, room.get("preset_id"))
    return effective_preset(preset, region_for(database, room.get("region_id"))) if preset else None


def roll_encounter(database, room):
    """A creature to spawn as a player arrives, or None.

    The chance is per arrival; a region's danger level filters which creatures
    can appear. Spawns are capped per room and despawn on a timer.
    """
    preset = effective_for_room(database, room)
    if preset is None:
        return None
    encounters = preset["encounters"]
    if encounters["chance"] <= 0 or not encounters["creatures"] or encounters["max_live"] <= 0:
        return None
    source = f"wild:{room['id']}"
    if database.execute("SELECT COUNT(*) FROM mud_npc_instances WHERE spawned_by_trigger=?",
                        (source,)).fetchone()[0] >= encounters["max_live"]:
        return None
    if _RNG.uniform(0, 100) >= encounters["chance"]:
        return None
    choice = _weighted(_RNG, encounters["creatures"])
    definition = database.execute("SELECT id,name FROM mud_npc_definitions WHERE id=?", (choice["npc"],)).fetchone()
    if definition is None:
        return None
    return {"npc": definition["id"], "name": definition["name"], "source": source,
            "despawn_seconds": encounters["despawn_seconds"]}


def roll_resources(database, room, now=None):
    """Lazily gather a wild room's resources; untouched rooms hold nothing until visited.

    Returns the names of items that appeared.
    """
    preset = effective_for_room(database, room)
    if preset is None:
        return []
    resources = preset["resources"]
    if not resources["items"] or resources["max_per_room"] <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    last = database.execute("SELECT resources_at FROM mud_world_rooms WHERE id=?", (room["id"],)).fetchone()
    if last and last["resources_at"]:
        try:
            elapsed = (now - datetime.fromisoformat(last["resources_at"])).total_seconds()
        except (TypeError, ValueError):
            elapsed = resources["respawn_seconds"]
        if elapsed < resources["respawn_seconds"]:
            return []
    slots = resources["max_per_room"] - database.execute(
        "SELECT COUNT(*) FROM mud_item_instances WHERE room_id=? AND id LIKE 'world:wild-item:%'",
        (room["id"],)).fetchone()[0]
    names = []
    for entry in resources["items"]:
        if slots <= 0:
            break
        if _RNG.uniform(0, 100) >= entry["chance"]:
            continue
        definition = database.execute("SELECT id,name FROM mud_item_definitions WHERE id=?", (entry["item"],)).fetchone()
        if definition is None:
            continue
        for _ in range(min(slots, _RNG.randint(entry["count"]["minimum"], entry["count"]["maximum"]))):
            database.execute("INSERT INTO mud_item_instances(id,definition_id,room_id) VALUES(?,?,?)",
                             (f"world:wild-item:{uuid.uuid4().hex}", definition["id"], room["id"]))
            names.append(definition["name"])
            slots -= 1
    database.execute("UPDATE mud_world_rooms SET resources_at=? WHERE id=?",
                     (now.replace(microsecond=0).isoformat(), room["id"]))
    return names


# --- World packages ---------------------------------------------------------

class RoomReferences(dict):
    """Room IDs by reference, where '@x,y,z' cell references resolve on demand.

    Lets every existing reference check and mapping accept wilderness cells
    without turning a million cells into a million dictionary entries.
    """

    def __init__(self, rooms, cell_lookup):
        super().__init__(rooms)
        self._cell = cell_lookup

    def __contains__(self, key):
        return super().__contains__(key) or self._cell(key) is not None

    def __getitem__(self, key):
        if super().__contains__(key):
            return super().__getitem__(key)
        value = self._cell(key)
        if value is None:
            raise KeyError(key)
        return value

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def _encode_row(cells, origin):
    """Run-length encode one row's sorted (x, palette index) pairs: '12*1,3*0,2'.

    Gaps become empty runs and nothing follows the last filled square, so sparse
    rows stay short however wide the layer is.
    """
    runs, position = [], origin
    for x, value in cells:
        if x > position:
            runs.append([0, x - position])
        if runs and runs[-1][0] == value and x == position:
            runs[-1][1] += 1
        else:
            runs.append([value, 1])
        position = x + 1
    return ",".join(str(value) if length == 1 else f"{length}*{value}" for value, length in runs)


def _decode_row(text, width, palette_size):
    runs, position = [], 0
    if text == "":
        return runs, 0
    if not isinstance(text, str):
        raise WildernessError("Wilderness layer rows must be text.")
    for part in text.split(","):
        length, _, value = part.rpartition("*")
        try:
            length, value = (int(length) if length else 1), int(value)
        except ValueError:
            raise WildernessError(f"Wilderness row run {part!r} is not count*index.") from None
        if length < 1 or not 0 <= value < palette_size:
            raise WildernessError(f"Wilderness row run {part!r} is out of range.")
        if value:
            runs.append((position, length, value))
        position += length
    if position > width:
        raise WildernessError("A wilderness row is wider than its layer.")
    return runs, len(text.split(","))


def read_document(value, npc_ids, item_ids):
    """Validate wilderness.json; returns presets, regions and run-length cell runs.

    Shared definitions are named by package-local ID or as built-in stationnet: IDs.
    """
    if value is None:
        return {"presets": [], "regions": [], "layers": [], "cells": [], "index": {}, "count": 0}
    doc = _object(value, "wilderness.json", ("presets", "regions", "layers", "cells"))
    presets = doc.get("presets", [])
    regions = doc.get("regions", [])
    if not isinstance(presets, list) or len(presets) > MAX_PRESETS:
        raise WildernessError(f"wilderness.json presets must be a list of at most {MAX_PRESETS}.")
    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        raise WildernessError(f"wilderness.json regions must be a list of at most {MAX_REGIONS}.")

    def known(value, ids):
        return value in ids or value.startswith("stationnet:")

    clean_presets = {}
    for entry in presets:
        preset = validate_preset(entry)
        if preset["key"] in clean_presets:
            raise WildernessError(f"Room preset key {preset['key']} is used twice.")
        npcs, items = preset_references(preset)
        unknown = [ref for ref in npcs if not known(ref, npc_ids)] + [ref for ref in items if not known(ref, item_ids)]
        if unknown:
            raise WildernessError(f"Room preset {preset['key']} names unknown definitions: {', '.join(sorted(unknown))}.")
        clean_presets[preset["key"]] = preset
    clean_regions = {}
    for entry in regions:
        entry = _object(entry, "Region", ("key", "name", "overrides"))
        key = entry.get("key")
        if not isinstance(key, str) or not KEY_RE.fullmatch(key) or key in clean_regions:
            raise WildernessError("Region keys must be unique lowercase keys.")
        overrides = validate_region_overrides(entry.get("overrides"))
        unknown = [ref for ref in region_references(overrides) if not known(ref, npc_ids)]
        if unknown:
            raise WildernessError(f"Region {key} names unknown NPCs: {', '.join(sorted(unknown))}.")
        clean_regions[key] = {"key": key, "name": _text(entry.get("name"), "Region name", 2, 80), "overrides": overrides}
    layers = doc.get("layers", [])
    if not isinstance(layers, list) or len(layers) > 64:
        raise WildernessError("wilderness.json layers must be a list of at most 64 layers.")
    index, clean_layers, total_runs, count = {}, [], 0, 0
    for layer in layers:
        layer = _object(layer, "Wilderness layer", ("z", "origin", "width", "height", "palette", "rows"))
        z = _whole(layer.get("z"), "Layer z", -COORDINATE_LIMIT, COORDINATE_LIMIT)
        if z in {entry["z"] for entry in clean_layers}:
            raise WildernessError(f"wilderness.json has more than one layer for z={z}.")
        origin = layer.get("origin")
        if (not isinstance(origin, list) or len(origin) != 2
                or any(type(part) is not int or abs(part) > COORDINATE_LIMIT for part in origin)):
            raise WildernessError("Layer origin must be [x, y].")
        width = _whole(layer.get("width"), "Layer width", 1, 2 * COORDINATE_LIMIT + 1)
        height = _whole(layer.get("height"), "Layer height", 1, 2 * COORDINATE_LIMIT + 1)
        if origin[0] + width - 1 > COORDINATE_LIMIT or origin[1] + height - 1 > COORDINATE_LIMIT:
            raise WildernessError("A layer extends beyond the coordinate limit.")
        palette = layer.get("palette")
        if not isinstance(palette, list) or not 1 <= len(palette) <= 1000:
            raise WildernessError("Layer palette must list 1 to 1000 entries.")
        clean_palette = []
        for entry in palette:
            if entry == "":
                clean_palette.append(None)
                continue
            preset_key, _, region_key = str(entry).partition("@") if isinstance(entry, str) else ("", "", "")
            if preset_key not in clean_presets or (region_key and region_key not in clean_regions):
                raise WildernessError(f"Palette entry {entry!r} must be preset or preset@region from this file.")
            clean_palette.append((preset_key, region_key or None))
        rows = layer.get("rows")
        if not isinstance(rows, list) or len(rows) > height:
            raise WildernessError("Layer rows must be a list no longer than the layer height.")
        for offset, text in enumerate(rows):
            runs, parts = _decode_row(text, width, len(clean_palette))
            total_runs += parts
            if total_runs > MAX_RUNS:
                raise WildernessError(f"wilderness.json can hold at most {MAX_RUNS:,} runs.")
            kept = []
            for start, length, value in runs:
                if clean_palette[value] is None:
                    continue
                kept.append((origin[0] + start, length, *clean_palette[value]))
                count += length
            if kept:
                index[(z, origin[1] + offset)] = kept
        if count > MAX_WORLD_CELLS:
            raise WildernessError(f"A world can hold at most {MAX_WORLD_CELLS:,} wilderness rooms.")
        clean_layers.append({"z": z})
    document = {"presets": list(clean_presets.values()), "regions": list(clean_regions.values()),
                "layers": clean_layers, "index": index, "count": count, "cells": []}
    cells = doc.get("cells", [])
    if not isinstance(cells, list) or len(cells) > 50_000:
        raise WildernessError("wilderness.json cells must be a list of at most 50,000 entries.")
    for entry in cells:
        entry = _object(entry, "Wilderness cell", ("x", "y", "z", "grid_link", "blocked_dirs"))
        position = tuple(_whole(entry.get(axis), f"Cell {axis}", -COORDINATE_LIMIT, COORDINATE_LIMIT)
                         for axis in ("x", "y", "z"))
        if lookup(document, cell_reference(*position)) is None:
            raise WildernessError(f"Cell settings name {cell_reference(*position)}, which no layer fills.")
        grid_link = entry.get("grid_link", True)
        if type(grid_link) is not bool:
            raise WildernessError("Cell grid_link must be true or false.")
        document["cells"].append({"position": position, "grid_link": grid_link,
                                  "blocked_dirs": validate_blocked(entry.get("blocked_dirs", ""))})
    return document


def validate_blocked(value):
    """Comma-separated or listed directions a room walls off from its grid neighbours."""
    parts = value if isinstance(value, list) else str(value or "").replace(" ", "").split(",")
    parts = [str(part).casefold() for part in parts if part]
    if any(part not in DELTAS for part in parts):
        raise WildernessError(f"Blocked directions must be among: {', '.join(DELTAS)}.")
    return ",".join(direction for direction in DELTAS if direction in parts)


def lookup(document, reference):
    """(x, y, z, preset key, region key) for a filled '@x,y,z' reference, else None."""
    position = parse_reference(reference)
    if position is None:
        return None
    for start, length, preset_key, region_key in document["index"].get((position[2], position[1]), ()):
        if start <= position[0] < start + length:
            return (*position, preset_key, region_key)
    return None


def install_cells(database, world_id, installation_key, document):
    """Insert presets (without shared references), regions and every filled cell.

    Runs before exits and placed content, which may stand in cells. Shared
    definition references are filled in later by install_references.
    """
    prefix = f"world:{world_id}:{installation_key}"
    preset_ids = {preset["key"]: f"{prefix}:room-preset:{preset['key']}" for preset in document["presets"]}
    region_ids = {region["key"]: f"{prefix}:region:{region['key']}" for region in document["regions"]}
    for preset in document["presets"]:
        save_preset(database, world_id, remap_preset(preset, lambda _: "pending", lambda _: "pending"),
                    new_id=preset_ids[preset["key"]])
    for region in document["regions"]:
        save_region(database, world_id, region["key"], region["name"], remap_overrides(region["overrides"], lambda _: "pending"),
                    new_id=region_ids[region["key"]])
    batch = []
    for (z, y), runs in document["index"].items():
        for start, length, preset_key, region_key in runs:
            for x in range(start, start + length):
                batch.append((cell_id(world_id, x, y, z), world_id, x, y, z, preset_ids[preset_key],
                              region_ids.get(region_key)))
                if len(batch) >= CHUNK:
                    _insert_batch(database, batch)
                    batch = []
    _insert_batch(database, batch)
    for cell in document["cells"]:
        database.execute("UPDATE mud_world_rooms SET grid_link=?,blocked_dirs=? WHERE id=?",
                         (int(cell["grid_link"]), cell["blocked_dirs"], cell_id(world_id, *cell["position"])))
    touch_navigation(database)
    return preset_ids, region_ids


def _insert_batch(database, batch):
    if batch:
        database.executemany(
            "INSERT OR IGNORE INTO mud_world_rooms(id,name,description,world_id,kind,x,y,z,preset_id,region_id) "
            "VALUES(?,'','',?,'wild',?,?,?,?,?)", batch)


def install_references(database, world_id, document, preset_ids, region_ids, npc_mapper, item_mapper):
    """Store presets and regions with shared definitions remapped to installed IDs."""
    for preset in document["presets"]:
        save_preset(database, world_id, remap_preset(preset, npc_mapper, item_mapper), preset_ids[preset["key"]])
    for region in document["regions"]:
        save_region(database, world_id, region["key"], region["name"], remap_overrides(region["overrides"], npc_mapper),
                    region_ids[region["key"]])


def shared_references(database, world_id):
    """Shared NPC and item definitions the world's presets and regions name."""
    npcs, items = set(), set()
    for row in world_presets(database, world_id):
        try:
            found_npcs, found_items = preset_references(validate_preset(json.loads(row["preset_json"])))
        except (ValueError, TypeError):
            continue
        npcs |= found_npcs
        items |= found_items
    for row in world_regions(database, world_id):
        try:
            npcs |= region_references(validate_region_overrides(json.loads(row["overrides_json"])))
        except (ValueError, TypeError):
            continue
    return ({value for value in npcs if not value.startswith("stationnet:")},
            {value for value in items if not value.startswith("stationnet:")})


def export_document(database, world_id, npc_mapper, item_mapper):
    """Presets, regions and run-length layers for wilderness.json; None when the world has none."""
    preset_rows = world_presets(database, world_id)
    region_rows = world_regions(database, world_id)
    presets, preset_keys = [], {}
    for row in preset_rows:
        preset = preset_for(database, row["id"])
        if preset is not None:
            presets.append(remap_preset(preset, npc_mapper, item_mapper))
            preset_keys[row["id"]] = preset["key"]
    regions, region_keys = [], {}
    for row in region_rows:
        regions.append({"key": row["local_key"], "name": row["name"],
                        "overrides": remap_overrides(region_for(database, row["id"]), npc_mapper)})
        region_keys[row["id"]] = row["local_key"]
    layers, cells = [], []
    for bounds in database.execute(
        "SELECT z,MIN(x) AS x1,MAX(x) AS x2,MIN(y) AS y1,MAX(y) AS y2 FROM mud_world_rooms "
        "WHERE world_id=? AND kind='wild' GROUP BY z ORDER BY z", (world_id,),
    ).fetchall():
        palette, indices, rows = [""], {}, []
        width = bounds["x2"] - bounds["x1"] + 1
        current_y, cells_in_row = bounds["y1"], []
        for row in database.execute(
            "SELECT x,y,preset_id,region_id,grid_link,blocked_dirs FROM mud_world_rooms "
            "WHERE world_id=? AND kind='wild' AND z=? ORDER BY y,x", (world_id, bounds["z"]),
        ):
            while row["y"] > current_y:
                rows.append(_encode_row(cells_in_row, bounds["x1"]))
                current_y, cells_in_row = current_y + 1, []
            preset_key = preset_keys.get(row["preset_id"])
            if preset_key is None:
                continue
            entry = preset_key + (f"@{region_keys[row['region_id']]}" if row["region_id"] in region_keys else "")
            if entry not in indices:
                indices[entry] = len(palette)
                palette.append(entry)
            cells_in_row.append((row["x"], indices[entry]))
            if not row["grid_link"] or row["blocked_dirs"]:
                cell = {"x": row["x"], "y": row["y"], "z": bounds["z"]}
                if not row["grid_link"]:
                    cell["grid_link"] = False
                if row["blocked_dirs"]:
                    cell["blocked_dirs"] = row["blocked_dirs"]
                cells.append(cell)
        rows.append(_encode_row(cells_in_row, bounds["x1"]))
        while rows and rows[-1] == "":
            rows.pop()
        layers.append({"z": bounds["z"], "origin": [bounds["x1"], bounds["y1"]], "width": width,
                       "height": bounds["y2"] - bounds["y1"] + 1, "palette": palette, "rows": rows})
    if not presets and not regions and not layers:
        return None
    document = {"presets": presets, "regions": regions, "layers": layers}
    if cells:
        document["cells"] = cells
    return document


def export_references(world_id, authored):
    """Export room names: authored rooms by their package ID, wild cells as '@x,y,z'."""
    pattern = re.compile(rf"^{re.escape(f'world:{world_id}:cell:')}(-?\d+):(-?\d+):(-?\d+)$")

    def cell(key):
        match = pattern.fullmatch(key) if isinstance(key, str) else None
        return cell_reference(*(int(part) for part in match.groups())) if match else None

    return RoomReferences(authored, cell)
