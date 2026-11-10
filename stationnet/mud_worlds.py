"""Portable, file-authored traditional MUD maps."""
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import uuid
import zipfile

from flask import current_app

from . import mud_conditions, mud_gathering, mud_housing, mud_quests, mud_work, mud_scope, mud_society, mud_transport, mud_triggers, mud_wayfinding, mud_wilderness, mud_world_dungeons
from . import mud_abilities, mud_effects, mud_progression, mud_shared_definitions
from .mud_package_report import PackageReport


MAX_ZIP_BYTES = 16 * 1024 * 1024
# Set only while World Forge publishes the home world: the one path allowed to
# read a package named 'stationnet' and to upgrade a world that was never imported.
_HOME_PUBLISH = ContextVar("mud_world_home_publish", default=False)


@contextmanager
def home_publish():
    """Let read_package and upgrade accept the home world for the duration of a World Forge publish."""
    token = _HOME_PUBLISH.set(True)
    try:
        yield
    finally:
        _HOME_PUBLISH.reset(token)


@contextmanager
def dry_run(database, name):
    """A transaction whose writes are always rolled back.

    Outside a transaction it starts with BEGIN IMMEDIATE: a deferred one that reads
    first and writes later fails at once with "database is locked" if another
    connection committed in between (WAL snapshot conflict).
    """
    own = not database.in_transaction
    database.execute("BEGIN IMMEDIATE" if own else f"SAVEPOINT {name}")
    try:
        yield
    finally:
        if own:
            database.rollback()
        else:
            database.execute(f"ROLLBACK TO {name}")
            database.execute(f"RELEASE {name}")


def _upgradable(world_id, world):
    return world is not None and (world["imported"] or (world_id == mud_scope.HOME and _HOME_PUBLISH.get()))
MAX_FILE_BYTES = 8 * 1024 * 1024
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
FILES = {"manifest.json", "rooms.json", "exits.json"}
CONTENT_FILE = "content.json"
ORIGINS_FILE = "origins.json"
SKILLS_FILE = "skills.json"
DUNGEONS_FILE = mud_world_dungeons.DUNGEONS_FILE
EVENTS_FILE = "events.json"
WILDERNESS_FILE = mud_wilderness.WILDERNESS_FILE
ABILITIES_FILE = "abilities.json"
EFFECTS_FILE = "effects.json"
OPTIONAL_FILES = {CONTENT_FILE, ORIGINS_FILE, SKILLS_FILE, DUNGEONS_FILE, EVENTS_FILE, WILDERNESS_FILE,
                  ABILITIES_FILE, EFFECTS_FILE}
# Format 1 packages carry no format_version; format 2 adds abilities, effects, progression, and
# availability, and rejects manifest keys it does not know instead of ignoring them.
PACKAGE_FORMAT = 2
MANIFEST_KEYS = {"id", "name", "author", "start_room", "format_version", "resource", "progression",
                 "availability", "layers", *mud_society.SOCIETY_KEYS}
# Manifest keys authors might try that name engine rules; packages may not redefine them.
ENGINE_RESERVED = {"max_level", "level_cap", "experience_curve", "xp_curve", "attributes", "attribute_cap",
                   "effect_kinds", "resistances", "equipment_slots", "directions", "skill_effect_types",
                   "combat"}
# Order follows foreign-key dependencies. Only these authored columns cross
# the package boundary; player state and generated dungeon data never do.
CONTENT_TABLES = {
    "mud_world_factions": ("id", "name", "description", "default_reputation", "defeat_reputation", "public",
                           "ranks_json", "sort_order", "joinable", "start_joinable", "recruiter_npc_definition_id",
                           "join_conditions_json", "join_conditions_message", "start_conditions_json", "join_cost",
                           "join_reputation", "leavable", "leave_reputation", "rejoin_hours", "expel_below"),
    "mud_faction_relations": ("id", "faction_id", "other_faction_id", "share_percent"),
    "mud_item_definitions": ("id", "name", "description", "item_type", "properties_json", "behaviors_json", "equipment_slot"),
    "mud_conversations": ("id", "name"),
    "mud_dialogue_nodes": ("id", "conversation_id", "node_key", "body", "is_start"),
    "mud_quests": ("id", "name", "description", "prerequisite_quest_id", "completion_mode",
                   "turn_in_npc_definition_id", "sequential", "abandonable", "conditions_json",
                   "summary", "details_json", "time_limit_seconds", "fail_conditions_json", "retry_mode",
                   "retry_cooldown_seconds"),
    "mud_npc_definitions": ("id", "name", "description", "conversation_id", "properties_json", "behaviors_json",
                            "faction_id"),
    "mud_professions": ("id", "name", "description", "recruiter_npc_definition_id", "conditions_json"),
    "mud_work_activities": ("id", "profession_id", "name", "description", "kind", "room_id",
                            "provider_npc_definition_id", "conditions_json", "tool_definition_id",
                            "practice_skill_key", "cooldown_seconds", "objective_type", "objective_target_id",
                            "required_count"),
    "mud_work_rewards": ("id", "activity_id", "reward_type", "target_id", "amount"),
    "mud_npc_combat_profiles": ("npc_definition_id", "maximum_health", "attack", "defense", "experience_reward", "respawn_seconds", "accuracy", "evasion"),
    "mud_npc_equipment": ("npc_definition_id", "equipment_slot", "item_definition_id"),
    "mud_npc_combat_rewards": ("id", "npc_definition_id", "reward_type", "target_id", "amount", "chance_percent"),
    "mud_npc_shops": ("npc_definition_id", "buys_items", "sell_percent"),
    "mud_npc_banks": ("npc_definition_id",),
    "mud_shop_stock": ("id", "npc_definition_id", "item_definition_id", "price", "quantity", "conditions_json"),
    "mud_npc_trainers": ("id", "npc_definition_id", "skill_key", "price", "max_rank"),
    "mud_quest_objectives": ("id", "quest_id", "objective_type", "target_id", "description", "required_count", "sort_order",
                             "optional", "consume_on_turn_in", "recipient_npc_definition_id",
                             "hidden", "outcome_key", "fail_conditions_json"),
    "mud_quest_rewards": ("id", "quest_id", "reward_type", "target_id", "amount", "outcome"),
    "mud_quest_givers": ("quest_id", "npc_definition_id"),
    "mud_dialogue_responses": ("id", "node_id", "response_text", "next_node_id", "accept_quest_id", "deliver_item_id", "required_state_key", "required_state_value", "state_key", "state_value", "sort_order",
                               "conditions_json", "outcomes_json", "turn_in_quest_id"),
    "mud_item_instances": ("id", "definition_id", "room_id", "container_id", "is_open", "state_json"),
    "mud_item_spawns": ("id", "definition_id", "room_id", "respawn_seconds"),
    "mud_gathering_nodes": ("id", "room_id", "name", "activity", "rewards_json", "skill_key",
                            "min_rank", "tool_definition_id", "success_percent", "respawn_seconds"),
    "mud_room_discoveries": ("id", "room_id", "clue", "item_definition_id", "difficulty",
                             "conditions_json", "item_instance_id", "exit_id"),
    "mud_npc_instances": ("id", "definition_id", "room_id", "state_json"),
    "mud_world_destinations": ("id", "room_id", "name", "aliases", "category", "description", "public",
                               "discover_on_visit", "map_items_json", "conditions_json", "sort_order"),
    "mud_world_start_rules": ("id", "room_id", "spawn_conditions_json", "sort_order"),
    "mud_properties": ("id", "name", "description", "kind", "entrance_room_id", "acquisition", "price",
                       "refund_percent", "rent", "rent_period_seconds", "grace_seconds", "prepay_periods",
                       "max_owners", "agent_npc_definition_id", "conditions_json", "available"),
    "mud_property_rooms": ("room_id", "property_id"),
    "mud_storage_points": ("id", "room_id", "name", "description", "scope", "capacity", "item_types",
                           "conditions_json"),
    "mud_vehicle_definitions": ("id", *mud_transport.VEHICLE_FIELDS),
    "mud_vehicle_placements": ("id", "vehicle_definition_id", "room_id"),
    "mud_fuel_stations": ("id", "room_id", *mud_transport.STATION_DEFAULTS),
    "mud_transit_lines": ("id", *mud_transport.LINE_FIELDS),
    "mud_transit_stops": ("id", "line_id", "room_id", *mud_transport.STOP_DEFAULTS),
}
FACTION_MEMBERSHIP_DEFAULTS = {"joinable": 0, "start_joinable": 0, "recruiter_npc_definition_id": None,
                               "join_conditions_json": [], "join_conditions_message": "",
                               "start_conditions_json": [], "join_cost": 0, "join_reputation": 0, "leavable": 1,
                               "leave_reputation": 0, "rejoin_hours": 0, "expel_below": None}
# Optional quest fields; export leaves them out at these values so plain quests stay readable by older stations.
QUEST_FIELD_DEFAULTS = {
    "mud_quests": {"summary": "", "details_json": [], "time_limit_seconds": None, "fail_conditions_json": [],
                   "retry_mode": "retry", "retry_cooldown_seconds": 0},
    "mud_quest_objectives": {"hidden": 0, "outcome_key": None, "fail_conditions_json": []},
    "mud_quest_rewards": {"outcome": ""},
}
# Quest JSON that holds conditions inside a larger shape: detail reveal rules and failure rules.
QUEST_RULE_COLUMNS = {"mud_quests": {"details_json": "details", "fail_conditions_json": "fail"},
                      "mud_quest_objectives": {"fail_conditions_json": "fail"}}
ID_TABLES = {table for table, columns in CONTENT_TABLES.items() if "id" in columns}
REFERENCE_TABLE = {
    "conversation_id": "mud_conversations", "node_id": "mud_dialogue_nodes",
    "next_node_id": "mud_dialogue_nodes", "accept_quest_id": "mud_quests",
    "deliver_item_id": "mud_item_definitions",
    "prerequisite_quest_id": "mud_quests", "npc_definition_id": "mud_npc_definitions",
    "item_definition_id": "mud_item_definitions", "quest_id": "mud_quests",
    "definition_id": None, "room_id": "rooms", "container_id": "mud_item_instances",
    "key_item_id": "mud_item_definitions",
    "tool_definition_id": "mud_item_definitions",
    "turn_in_npc_definition_id": "mud_npc_definitions",
    "recipient_npc_definition_id": "mud_npc_definitions", "turn_in_quest_id": "mud_quests",
    "faction_id": "mud_world_factions", "other_faction_id": "mud_world_factions",
    "recruiter_npc_definition_id": "mud_npc_definitions",
    "provider_npc_definition_id": "mud_npc_definitions",
    "profession_id": "mud_professions", "activity_id": "mud_work_activities",
    "item_instance_id": "mud_item_instances", "exit_id": "exits",
    "entrance_room_id": "rooms", "agent_npc_definition_id": "mud_npc_definitions", "property_id": "mud_properties",
    "vehicle_definition_id": "mud_vehicle_definitions", "interior_room_id": "rooms", "cabin_room_id": "rooms",
    "fuel_item_id": "mud_item_definitions", "line_id": "mud_transit_lines",
}
# Quests and dialogue responses carry condition/outcome lists that name content.
CONDITION_COLUMNS = {"mud_world_factions": ("join_conditions_json", "start_conditions_json"),
                     "mud_quests": ("conditions_json",),
                     "mud_room_discoveries": ("conditions_json",),
                     "mud_professions": ("conditions_json",),
                     "mud_work_activities": ("conditions_json",),
                     "mud_dialogue_responses": ("conditions_json", "outcomes_json"),
                     "mud_world_destinations": ("conditions_json",),
                     "mud_world_start_rules": ("spawn_conditions_json",),
                     "mud_shop_stock": ("conditions_json",),
                     "mud_properties": ("conditions_json",),
                     "mud_storage_points": ("conditions_json",),
                     "mud_vehicle_definitions": ("conditions_json",),
                     "mud_transit_lines": ("conditions_json",)}
CONDITION_TABLES = {"item": "mud_item_definitions", "quest": "mud_quests",
                    "objective": "mud_quest_objectives", "object": "mud_item_instances", "room": "rooms",
                    "destination": "mud_world_destinations", "npc": "mud_npc_definitions",
                    "faction": "mud_world_factions", "property": "mud_properties",
                    "vehicle": "mud_vehicle_definitions"}
# What each kind of trigger reference names; exits and triggers are mapped separately.
TRIGGER_TABLES = {**CONDITION_TABLES, "npc": "mud_npc_definitions", "npc_instance": "mud_npc_instances",
                  "response": "mud_dialogue_responses"}
OBJECTIVE_TYPES = ("talk_npc", "visit_room", "possess_item", "defeat_npc", "deliver_item", "script")
# Script objectives are completed by triggers; their target is a free label.
OBJECTIVE_TARGETS = {"talk_npc": "mud_npc_definitions", "defeat_npc": "mud_npc_definitions",
                     "visit_room": "rooms", "possess_item": "mud_item_definitions",
                     "deliver_item": "mud_item_definitions"}
ORIGIN_KINDS = {"race": "races", "class": "classes"}
# Content that NPC behaviors (schedules and the like) may name, besides rooms.
BEHAVIOR_TABLES = {"item": "mud_item_definitions"}
# Who owns a placed item, so taking it is theft: state_json key -> table.
OWNER_TABLES = {"owner_faction": "mud_world_factions", "owner_npc": "mud_npc_instances"}
DEFAULT_RESOURCE = {"enabled": True, "name": "Mana"}
RESOURCE_NAME_RE = re.compile(r"^[^\W\d_][^\W\d_ '-]*(?:[ '-][^\W\d_]+)*$")


class WorldError(ValueError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


def directory():
    path = Path(current_app.config["DATA_DIR"]) / "mud_worlds"
    path.mkdir(parents=True, exist_ok=True)
    return path


def init_app(app):
    target = Path(app.config["DATA_DIR"]) / "mud_worlds"
    target.mkdir(parents=True, exist_ok=True)
    guide = target / "README_CUSTOM_WORLDS.md"
    if not guide.exists():
        guide.write_bytes((Path(__file__).with_name("mud_world_templates") / "README_CUSTOM_WORLDS.md").read_bytes())


def _object(value, label):
    if not isinstance(value, dict):
        raise WorldError(f"{label} must be an object.")
    return value


def _label(value, label, minimum, maximum):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise WorldError(f"{label} must contain {minimum} to {maximum} characters.")
    return value.strip()


def _whole(value, low, high):
    return type(value) is int and low <= value <= high


def validate_resource(value):
    """Return a world's mana settings; a missing value keeps Mana."""
    if value is None:
        return dict(DEFAULT_RESOURCE)
    if not isinstance(value, dict) or set(value) - {"enabled", "name"}:
        raise WorldError("resource must be an object with optional enabled and name.")
    enabled = value.get("enabled", True)
    if type(enabled) is not bool:
        raise WorldError("resource enabled must be true or false.")
    name = value.get("name", DEFAULT_RESOURCE["name"])
    name = " ".join(name.split()) if isinstance(name, str) else None
    if not name or len(name) > 20 or not RESOURCE_NAME_RE.fullmatch(name):
        raise WorldError("Resource name must be 1-20 letters, with single spaces, hyphens, or apostrophes between words.")
    return {"enabled": enabled, "name": name}


def read_package(payload, database=None, report=None):
    """Read a bounded ZIP without extracting paths or accepting hidden extras.

    Problems in individual rows are collected and reported together, each with
    its file and JSON path. With a database, references to installed races,
    classes, skills, abilities, and effects are checked as well. Pass ``report``
    to receive warnings from a package that is still valid.
    """
    report = PackageReport() if report is None else report
    if not payload or len(payload) > MAX_ZIP_BYTES:
        raise WorldError("World ZIP must be between 1 byte and 16 MB.")
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            if not FILES <= set(names) or set(names) - FILES - OPTIONAL_FILES or len(names) != len(set(names)):
                raise WorldError("World ZIP must contain manifest.json, rooms.json, and exits.json at its root, with optional content.json, origins.json, skills.json, abilities.json, effects.json, dungeons.json, events.json, and wilderness.json.")
            docs = {}
            for name in names:
                info = archive.getinfo(name)
                if info.is_dir() or info.file_size > MAX_FILE_BYTES:
                    raise WorldError(f"{name} is too large or is not a file.")
                raw = archive.read(info, MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    raise WorldError(f"{name} is too large.")
                docs[name] = json.loads(raw.decode("utf-8"))
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, RuntimeError, EOFError) as error:
        raise WorldError("World ZIP must contain valid UTF-8 JSON files.") from error
    manifest = _object(docs["manifest.json"], "manifest.json")
    format_version = manifest.get("format_version", 1)
    if type(format_version) is int and format_version > PACKAGE_FORMAT:
        raise WorldError(f"This world needs a newer StationNet: it uses package format {format_version}, "
                         f"and this station reads formats 1 to {PACKAGE_FORMAT}.")
    if type(format_version) is not int or format_version < 1:
        raise WorldError("manifest.json format_version must be 1 or 2.")
    # Format 1 packages predate the stricter checks, so those findings are warnings for them.
    strict = "error" if format_version >= 2 else "warning"
    for key in sorted(set(manifest) - MANIFEST_KEYS):
        if key in ENGINE_RESERVED:
            report.add(strict, "manifest.json", f"$.{key}", f"{key} is an engine rule a world package cannot "
                       "change; tune progression within its bounds instead.", "engine_reserved")
        else:
            report.add(strict, "manifest.json", f"$.{key}", f"Unsupported manifest field {key}.", "unsupported")
    try:
        progression = mud_progression.validate(manifest.get("progression"))
    except mud_progression.ProgressionError as problem:
        report.error("manifest.json", "$.progression", str(problem),
                     "engine_reserved" if "engine-owned" in str(problem) else None)
        progression = dict(mud_progression.DEFAULTS)
    availability = manifest.get("availability")
    if availability is not None and (
            not isinstance(availability, dict) or set(availability) - {"races", "classes", "skills"}
            or any(not isinstance(keys, list) or len(keys) > 200 or any(not isinstance(key, str) for key in keys)
                   for keys in availability.values())):
        report.error("manifest.json", "$.availability", "availability must map races, classes, and/or skills "
                     "to lists of keys.")
        availability = None
    world_id = manifest.get("id")
    if (not isinstance(world_id, str) or not ID_RE.fullmatch(world_id)
            or (world_id == mud_scope.HOME and not _HOME_PUBLISH.get())):
        raise WorldError("World ID must be lowercase letters, digits, and hyphens, starting with a letter; 'stationnet' is reserved.")
    name = _label(manifest.get("name"), "World name", 2, 80)
    author = _label(manifest.get("author"), "World author", 1, 80)
    resource = validate_resource(manifest.get("resource"))
    try:
        layers = mud_wilderness.validate_layer_names(manifest.get("layers"))
    except mud_wilderness.WildernessError as problem:
        report.error("manifest.json", "$.layers", str(problem))
        layers = {}
    try:
        society = mud_society.validate_settings({key: manifest[key] for key in mud_society.SOCIETY_KEYS
                                                 if key in manifest})
    except mud_society.SocietyError as problem:
        raise WorldError(str(problem)) from problem
    start = manifest.get("start_room")
    if not isinstance(start, str) or not ID_RE.fullmatch(start):
        raise WorldError("start_room must name a room ID in rooms.json.")
    rooms = docs["rooms.json"]
    exits = docs["exits.json"]
    if not isinstance(rooms, list) or not 1 <= len(rooms) <= 1000:
        raise WorldError("rooms.json must contain 1 to 1000 rooms.")
    if not isinstance(exits, list) or len(exits) > 5000:
        raise WorldError("exits.json must contain at most 5000 exits.")
    authored_ids = set()
    clean_rooms = []
    positions = {}
    for index, room in enumerate(rooms):
        try:
            room = _object(room, "Room")
            local_id = room.get("id")
            if not isinstance(local_id, str) or not ID_RE.fullmatch(local_id) or local_id in authored_ids:
                raise WorldError("Room IDs must be unique lowercase IDs.")
            authored_ids.add(local_id)
            if type(room.get("bank", False)) is not bool:
                raise WorldError(f"Room {local_id} bank must be true or false.")
            clean_rooms.append({"id": local_id, "name": _label(room.get("name"), "Room name", 2, 80),
                                "description": _label(room.get("description"), "Room description", 1, 4000),
                                "bank": room.get("bank", False),
                                **_room_grid(room, local_id, positions)})
        except WorldError as problem:
            report.error("rooms.json", f"$[{index}]", str(problem))
    if start not in authored_ids:
        report.error("manifest.json", "$.start_room", "start_room does not match a room in rooms.json.")
    raw_content = docs.get(CONTENT_FILE, {}) if isinstance(docs.get(CONTENT_FILE, {}), dict) else {}

    def raw_ids(table):
        rows = raw_content.get(table, [])
        return {row.get("id") for row in rows if isinstance(row, dict)} if isinstance(rows, list) else set()

    try:
        wilderness = mud_wilderness.read_document(docs.get(WILDERNESS_FILE), raw_ids("mud_npc_definitions"),
                                                  raw_ids("mud_item_definitions"))
    except mud_wilderness.WildernessError as problem:
        raise WorldError(f"Invalid wilderness.json: {problem}") from problem
    for position, local_id in positions.items():
        if mud_wilderness.lookup(wilderness, mud_wilderness.cell_reference(*position)):
            raise WorldError(f"Room {local_id} stands on a square wilderness.json also fills; leave that square empty.")
    # Exits, content, entrances and triggers may name wilderness squares as '@x,y,z'.
    room_ids = mud_wilderness.RoomReferences(
        {local_id: local_id for local_id in authored_ids},
        lambda key: key if mud_wilderness.lookup(wilderness, key) else None)
    # Each placed room's layer; layers meet only through up and down exits.
    room_layers = {local_id: position[2] for position, local_id in positions.items()}

    def layer_of(reference):
        position = mud_wilderness.parse_reference(reference)
        return position[2] if position else room_layers.get(reference)

    clean_exits = []
    exit_names = set()
    exit_directions = set()
    exit_ids = set()
    for exit_index, item in enumerate(exits):
        try:
            item = _object(item, "Exit")
            source, target = item.get("from"), item.get("to")
            if not isinstance(source, str) or not isinstance(target, str) or source not in room_ids or target not in room_ids:
                raise WorldError("Every exit must connect rooms in rooms.json.")
            label = _label(item.get("name"), "Exit name", 1, 80)
            direction = item.get("direction") or None
            if direction is not None:
                from .mud import DIRECTIONS
                if direction not in DIRECTIONS:
                    raise WorldError("Exit direction is not supported by the MUD.")
            crossing = mud_wilderness.layer_problem(direction, layer_of(source), layer_of(target))
            if crossing:
                raise WorldError(f"Exit {label} from {source} to {target}: {crossing}")
            name_key = (source, label.casefold())
            direction_key = (source, direction)
            if name_key in exit_names or (direction and direction_key in exit_directions):
                raise WorldError("Exit names and directions must be unique within each room.")
            exit_names.add(name_key)
            if direction:
                exit_directions.add(direction_key)
            key_item_id = item.get("key_item_id") or None
            if key_item_id is not None and (not isinstance(key_item_id, str) or not ID_RE.fullmatch(key_item_id)):
                raise WorldError("Exit key_item_id must be an item ID.")
            pick_difficulty = item.get("pick_difficulty")
            if pick_difficulty is not None and not _whole(pick_difficulty, 1, 200):
                raise WorldError("Exit pick_difficulty must be a whole number from 1 to 200.")
            try:
                conditions = mud_conditions.validate_conditions(item.get("conditions"))
            except ValueError as problem:
                raise WorldError(f"Invalid exit conditions: {problem}") from problem
            message = item.get("conditions_message", "")
            if not isinstance(message, str) or len(message.strip()) > 200:
                raise WorldError("Exit conditions_message must be text up to 200 characters.")
            exit_id = item.get("id")
            if exit_id is not None and (not isinstance(exit_id, str) or not ID_RE.fullmatch(exit_id) or exit_id in exit_ids):
                raise WorldError("Exit IDs must be unique lowercase IDs.")
            if exit_id:
                exit_ids.add(exit_id)
            hidden = item.get("hidden", False)
            if type(hidden) is not bool:
                raise WorldError("Exit hidden must be true or false.")
            try:
                requires_mode = mud_transport.mode(item.get("requires_mode"))
            except ValueError as problem:
                raise WorldError(f"Exit {label}: {problem}") from problem
            allow_vehicles = item.get("allow_vehicles", True)
            if type(allow_vehicles) is not bool:
                raise WorldError("Exit allow_vehicles must be true or false.")
            if requires_mode and not allow_vehicles:
                raise WorldError(f"Exit {label} cannot both require a vehicle mode and refuse vehicles.")
            clean_exits.append({"from": source, "to": target, "name": label, "direction": direction,
                                "key_item_id": key_item_id, "pick_difficulty": pick_difficulty,
                                "conditions": conditions, "conditions_message": " ".join(message.split()),
                                "id": exit_id, "hidden": hidden, "requires_mode": requires_mode,
                                "allow_vehicles": allow_vehicles})
        except WorldError as problem:
            report.error("exits.json", f"$[{exit_index}]", str(problem))
    local_effects, local_abilities = _read_magic(docs, report)
    known_effects = {**mud_effects.catalog(database), **local_effects}
    known_abilities = set(mud_abilities.catalog(database)) | set(local_abilities)
    content = docs.get(CONTENT_FILE, {})
    if not isinstance(content, dict) or set(content) - set(CONTENT_TABLES):
        raise WorldError("content.json must be an object of supported content tables.")
    destination_names = set()
    for table, rows in content.items():
        if not isinstance(rows, list) or len(rows) > 5000 or any(not isinstance(row, dict) for row in rows):
            raise WorldError(f"{table} must be an array of at most 5000 objects.")
        if table == "mud_npc_definitions":
            from .mud import validate_npc_behaviors
            for index, row in enumerate(rows):
                try:
                    value = row.get("behaviors_json", {})
                    try:
                        value = json.loads(value) if isinstance(value, str) else value
                        validate_npc_behaviors(value)
                    except (ValueError, json.JSONDecodeError) as problem:
                        raise WorldError(f"Invalid NPC behaviors: {problem}") from problem
                    for kind, ref in mud_society.behavior_references(value):
                        known_refs = room_ids if kind == "room" else raw_ids(BEHAVIOR_TABLES[kind])
                        if ref not in known_refs:
                            raise WorldError(f"NPC {row.get('id')} behaviors name an unknown {kind}: {ref}.")
                except WorldError as problem:
                    report.error(CONTENT_FILE, f"$.{table}[{index}].behaviors_json", str(problem))
        for index, row in enumerate(rows):
            try:
                unknown = set(row) - set(CONTENT_TABLES[table])
                if unknown:
                    raise WorldError(f"Unsupported fields: {', '.join(sorted(unknown))}.")
                if table in ("mud_item_definitions", "mud_npc_definitions"):
                    _check_effect_lists(row.get("properties_json"), known_effects, report, strict,
                                        f"$.{table}[{index}].properties_json")
                if table == "mud_item_definitions":
                    properties = row.get("properties_json", {})
                    if isinstance(properties, str):
                        try: properties = json.loads(properties)
                        except json.JSONDecodeError as problem: raise WorldError("Item properties must be valid JSON.") from problem
                    if not isinstance(properties, dict) or any(
                        key in properties and not _whole(properties[key], 0, 100000)
                        for key in ("heal", "value")
                    ):
                        raise WorldError("Item heal and value must be nonnegative integers.")
                    if "category" in properties and (not isinstance(properties["category"], str)
                                                     or not ID_RE.fullmatch(properties["category"])):
                        raise WorldError("Item category must be a lowercase key such as blade.")
                    try:
                        mud_conditions.validate_item_requirements(properties.get("requirements"))
                    except ValueError as problem:
                        raise WorldError(f"Invalid item requirements: {problem}") from problem
                    validate_object_properties(properties)
                    try:
                        taught = mud_wayfinding.validate_teaches(properties.get("teaches_destinations", []))
                    except mud_wayfinding.WayfindingError as problem:
                        raise WorldError(str(problem)) from problem
                    destination_ids = {entry.get("id") for entry in content.get("mud_world_destinations", [])}
                    if set(taught) - destination_ids:
                        raise WorldError("Item teaches_destinations must name destinations in content.json.")
                elif table == "mud_item_instances":
                    state = row.get("state_json", {})
                    try:
                        state = json.loads(state) if isinstance(state, str) else state
                    except json.JSONDecodeError as problem:
                        raise WorldError("Placed item state_json must be valid JSON.") from problem
                    for key, owner_table in OWNER_TABLES.items():
                        if isinstance(state, dict) and key in state and state[key] not in raw_ids(owner_table):
                            raise WorldError(f"Placed item {row.get('id')} names an unknown {key}: {state[key]}.")
                elif table == "mud_world_destinations":
                    if not isinstance(row.get("room_id"), str):
                        raise WorldError("Every destination needs a room_id.")
                    try:
                        clean = mud_wayfinding.validate_destination(row)
                    except mud_wayfinding.WayfindingError as problem:
                        raise WorldError(str(problem)) from problem
                    item_ids = {entry.get("id") for entry in content.get("mud_item_definitions", [])}
                    if set(clean["map_items"]) - item_ids:
                        raise WorldError("Destination map_items_json must name items in content.json.")
                    if clean["name"].casefold() in destination_names:
                        raise WorldError(f"Two destinations are named {clean['name']!r}.")
                    destination_names.add(clean["name"].casefold())
                    # Store the normalized form; old fields keep their meaning.
                    row.update({key: clean[key] for key in ("name", "aliases", "category", "description",
                                                           "public", "discover_on_visit", "sort_order")},
                               map_items_json=clean["map_items"])
                elif table == "mud_world_start_rules":
                    if row.get("room_id") not in room_ids:
                        raise WorldError("Start rule room_id must name a room in the world.")
                    if not _whole(row.get("sort_order", 0), -10000, 10000):
                        raise WorldError("Start rule sort_order must be a whole number from -10000 to 10000.")
                    try:
                        conditions = mud_conditions.validate_start_rule_conditions(row.get("spawn_conditions_json"))
                    except ValueError as problem:
                        raise WorldError(f"Invalid start rule: {problem}") from problem
                    if any(kind == "faction" and ref not in raw_ids("mud_world_factions")
                           for kind, ref in mud_conditions.references(conditions)):
                        raise WorldError("Start rule factions must name factions in content.json.")
                elif table == "mud_npc_combat_profiles":
                    if any(row.get(key) is not None and not _whole(row[key], 0, 100)
                           for key in ("accuracy", "evasion")):
                        raise WorldError("NPC combat accuracy and evasion must be whole numbers from 0 to 100.")
                elif table == "mud_professions":
                    try:
                        mud_work.profession(row)
                    except ValueError as problem:
                        raise WorldError(f"Invalid profession: {problem}") from problem
                    if row["recruiter_npc_definition_id"] not in raw_ids("mud_npc_definitions"):
                        raise WorldError("Profession recruiter must name an NPC in content.json.")
                elif table == "mud_work_activities":
                    try:
                        work = mud_work.activity(row)
                    except ValueError as problem:
                        raise WorldError(f"Invalid work activity: {problem}") from problem
                    if row.get("profession_id") not in raw_ids("mud_professions"):
                        raise WorldError("Work activity must name a profession in content.json.")
                    if work["provider_npc_definition_id"] and work["provider_npc_definition_id"] not in raw_ids("mud_npc_definitions"):
                        raise WorldError("Work provider must name an NPC in content.json.")
                    if work["room_id"] and work["room_id"] not in room_ids:
                        raise WorldError("Independent work must name a room in the world.")
                    if work["tool_definition_id"] and work["tool_definition_id"] not in raw_ids("mud_item_definitions"):
                        raise WorldError("Work tool must name an item in content.json.")
                    target_table = ("rooms" if work["objective_type"] == "visit_room" else
                                    "mud_npc_definitions" if work["objective_type"] in {"talk_npc", "defeat_npc"} else
                                    "mud_item_definitions")
                    if work["objective_target_id"] and work["objective_target_id"] not in (
                            room_ids if target_table == "rooms" else raw_ids(target_table)):
                        raise WorldError("Work objective must name content in this world.")
                    configured_rewards = content.get("mud_work_rewards", [])
                    if (isinstance(configured_rewards, list) and not any(
                            isinstance(reward, dict) and reward.get("activity_id") == row.get("id")
                            for reward in configured_rewards) and not work["practice_skill_key"]):
                        raise WorldError("A work activity needs a reward or practice skill.")
                elif table == "mud_work_rewards":
                    try:
                        clean = mud_work.reward(row)
                    except ValueError as problem:
                        raise WorldError(f"Invalid work reward: {problem}") from problem
                    if row.get("activity_id") not in raw_ids("mud_work_activities"):
                        raise WorldError("Work reward must name an activity in content.json.")
                    if clean["target_id"] and clean["target_id"] not in raw_ids("mud_item_definitions"):
                        raise WorldError("Work reward item must exist in content.json.")
                elif table == "mud_quest_objectives":
                    if row.get("objective_type") not in OBJECTIVE_TYPES or not _whole(row.get("required_count", 1), 1, 100):
                        raise WorldError("Quest objective type or required_count is invalid.")
                    if not _whole(row.get("optional", 0), 0, 1) or not _whole(row.get("consume_on_turn_in", 0), 0, 1):
                        raise WorldError("Quest objective optional and consume_on_turn_in must be 0 or 1.")
                    if row.get("consume_on_turn_in") and row["objective_type"] != "possess_item":
                        raise WorldError("Only possess_item objectives can consume items on turn-in.")
                    if row.get("recipient_npc_definition_id") and row["objective_type"] != "deliver_item":
                        raise WorldError("Only deliver_item objectives can name a recipient NPC.")
                    if not _whole(row.get("hidden", 0), 0, 1):
                        raise WorldError("Quest objective hidden must be 0 or 1.")
                    try:
                        outcome_key = mud_quests.validate_outcome_key(row.get("outcome_key"))
                    except ValueError as problem:
                        raise WorldError(str(problem)) from problem
                    if outcome_key and row.get("optional"):
                        raise WorldError("An objective that resolves the quest (outcome_key) cannot be optional.")
                elif table == "mud_quests":
                    if row.get("completion_mode", "auto") not in {"auto", "turn_in"}:
                        raise WorldError("Quest completion_mode must be auto or turn_in.")
                    if not _whole(row.get("sequential", 0), 0, 1) or not _whole(row.get("abandonable", 1), 0, 1):
                        raise WorldError("Quest sequential and abandonable must be 0 or 1.")
                    try:
                        mud_quests.validate_summary(row.get("summary", ""))
                        mud_quests.validate_time_limit(row.get("time_limit_seconds"))
                        mud_quests.validate_retry(row.get("retry_mode"), row.get("retry_cooldown_seconds"))
                    except ValueError as problem:
                        raise WorldError(f"Invalid quest {row.get('id')}: {problem}") from problem
                elif table == "mud_world_factions":
                    try:
                        row.update(mud_society.validate_faction(row))
                    except ValueError as problem:
                        raise WorldError(f"Invalid faction {row.get('id')}: {problem}") from problem
                    if row.get("recruiter_npc_definition_id") is not None and (
                            row["recruiter_npc_definition_id"] not in raw_ids("mud_npc_definitions")):
                        raise WorldError("Faction recruiter must name an NPC in content.json.")
                elif table == "mud_properties":
                    try:
                        clean = mud_housing.property(row)
                    except ValueError as problem:
                        raise WorldError(f"Invalid property {row.get('id')}: {problem}") from problem
                    if row.get("entrance_room_id") not in room_ids:
                        raise WorldError("Property entrance_room_id must name a room in the world.")
                    if clean["agent_npc_definition_id"] and (
                            clean["agent_npc_definition_id"] not in raw_ids("mud_npc_definitions")):
                        raise WorldError("Property agent must name an NPC in content.json.")
                    if "available" in row:
                        row["available"] = clean["available"]
                elif table == "mud_property_rooms":
                    if row.get("room_id") not in room_ids:
                        raise WorldError("Property room_id must name a room in the world.")
                    if row.get("property_id") not in raw_ids("mud_properties"):
                        raise WorldError("Property room must name a property in content.json.")
                elif table == "mud_storage_points":
                    try:
                        clean = mud_housing.storage_point(row)
                    except ValueError as problem:
                        raise WorldError(f"Invalid storage point {row.get('id')}: {problem}") from problem
                    if row.get("room_id") not in room_ids:
                        raise WorldError("Storage room_id must name a room in the world.")
                    row["item_types"] = clean["item_types"]
                elif table in TRANSPORT_VALIDATORS:
                    _check_transport_row(table, row, room_ids, raw_ids)
                elif table == "mud_faction_relations":
                    try:
                        mud_society.validate_share(row.get("share_percent"))
                    except ValueError as problem:
                        raise WorldError(str(problem)) from problem
                    if row.get("faction_id") == row.get("other_faction_id"):
                        raise WorldError("A faction cannot be related to itself.")
                if table == "mud_quest_rewards":
                    try:
                        mud_quests.validate_reward_outcome(row.get("outcome"))
                    except ValueError as problem:
                        raise WorldError(str(problem)) from problem
                if table == "mud_quest_rewards" and row.get("reward_type") == "reputation":
                    if not row.get("target_id") or not _whole(row.get("amount", 1), 1, mud_society.REPUTATION_LIMIT):
                        raise WorldError("Reputation rewards need a target faction and an amount from 1 to 1000.")
                elif table in {"mud_quest_rewards", "mud_npc_combat_rewards"}:
                    if row.get("reward_type") not in {"experience", "item", "coins"} or not _whole(row.get("amount", 1), 1, 100000):
                        raise WorldError("Reward type or amount is invalid.")
                    if row["reward_type"] == "item" and (not row.get("target_id") or row.get("amount", 1) > 100):
                        raise WorldError("Item rewards require a target and quantity up to 100.")
                    if row["reward_type"] != "item" and row.get("target_id"):
                        raise WorldError("Coin and experience rewards cannot target an item.")
                    if table == "mud_npc_combat_rewards" and not _whole(row.get("chance_percent", 100), 1, 100):
                        raise WorldError("Combat reward chance_percent must be 1 to 100.")
                elif table == "mud_npc_shops":
                    if not _whole(row.get("buys_items", 1), 0, 1) or not _whole(row.get("sell_percent", 40), 1, 100):
                        raise WorldError("Merchant settings are invalid.")
                elif table == "mud_npc_banks":
                    npc_id = row.get("npc_definition_id")
                    if not isinstance(npc_id, str) or npc_id not in raw_ids("mud_npc_definitions"):
                        raise WorldError("Banker settings must name an NPC definition in content.json.")
                elif table == "mud_shop_stock":
                    if not _whole(row.get("price"), 1, 100000) or (row.get("quantity") is not None and not _whole(row["quantity"], 0, 100000)):
                        raise WorldError("Merchant stock price or quantity is invalid.")
                elif table == "mud_npc_trainers":
                    if (not isinstance(row.get("skill_key"), str) or not ID_RE.fullmatch(row["skill_key"])
                            or not _whole(row.get("price", 0), 0, 100000) or not _whole(row.get("max_rank", 5), 1, 20)):
                        raise WorldError("Trainer skill_key, price, or max_rank is invalid.")
                elif table == "mud_item_spawns":
                    if not _whole(row.get("respawn_seconds"), 5, 86400):
                        raise WorldError("Item respawn_seconds must be 5 to 86,400.")
                elif table == "mud_gathering_nodes":
                    try:
                        rewards = mud_gathering.validate(row)
                    except ValueError as problem:
                        raise WorldError(str(problem)) from problem
                    if any(reward["item_id"] not in raw_ids("mud_item_definitions") for reward in rewards):
                        raise WorldError("Gathering rewards must name items in content.json.")
                elif table == "mud_room_discoveries":
                    if not isinstance(row.get("clue"), str) or not 1 <= len(row["clue"].strip()) <= 500:
                        raise WorldError("Room discovery clue must be 1 to 500 characters.")
                    if not _whole(row.get("difficulty", 0), 0, 200):
                        raise WorldError("Room discovery difficulty must be 0 to 200.")
                    if sum(bool(row.get(key)) for key in ("item_definition_id", "item_instance_id", "exit_id")) > 1:
                        raise WorldError("A room discovery may have only one result.")
                    room_id = row.get("room_id")
                    object_id, exit_id = row.get("item_instance_id"), row.get("exit_id")
                    if object_id and not any(item.get("id") == object_id and item.get("room_id") == room_id
                                             for item in content.get("mud_item_instances", [])):
                        raise WorldError("Discovery object must be placed in its room.")
                    if exit_id and not any(item.get("id") == exit_id and item["from"] == room_id
                                           for item in clean_exits):
                        raise WorldError("Discovery exit must leave its room and have an ID.")
                for column in CONDITION_COLUMNS.get(table, ()):
                    try:
                        _condition_validator(column)(row.get(column))
                    except ValueError as problem:
                        raise WorldError(f"Invalid {table} {column}: {problem}") from problem
                for column, kind in QUEST_RULE_COLUMNS.get(table, {}).items():
                    try:
                        _quest_rules(kind, row.get(column))
                    except ValueError as problem:
                        raise WorldError(f"Invalid {table} {column}: {problem}") from problem
            except WorldError as problem:
                report.error(CONTENT_FILE, f"$.{table}[{index}]", str(problem))
    for index, row in enumerate(content.get("mud_quest_rewards", [])):
        outcome = row.get("outcome") or ""
        if outcome and outcome != mud_quests.FAILED_OUTCOME and not any(
                objective.get("quest_id") == row.get("quest_id") and objective.get("outcome_key") == outcome
                for objective in content.get("mud_quest_objectives", [])):
            report.error(CONTENT_FILE, f"$.mud_quest_rewards[{index}]",
                         f"Reward outcome {outcome!r} is not the outcome_key of any objective of its quest.")
    for problem in _housing_problems(content, start):
        report.error(CONTENT_FILE, "$.mud_properties", problem)
    transport_errors, transport_warnings = _transport_problems(content, clean_exits, start,
                                                               docs.get(WILDERNESS_FILE))
    for problem in transport_errors:
        report.error(CONTENT_FILE, "$.mud_vehicle_definitions", problem)
    for problem in transport_warnings:
        report.warning(CONTENT_FILE, "$.mud_vehicle_definitions", problem)
    reward_counts = {}
    banker_ids = [row["npc_definition_id"] for row in content.get("mud_npc_banks", [])]
    if len(banker_ids) != len(set(banker_ids)):
        raise WorldError("An NPC can be marked as a banker only once.")
    for row in content.get("mud_work_rewards", []):
        activity_id = row["activity_id"]
        reward_counts[activity_id] = reward_counts.get(activity_id, 0) + 1
        if reward_counts[activity_id] > 10:
            raise WorldError("A work activity may have at most ten rewards.")
    key_ids = {row.get("id") for row in content.get("mud_item_definitions", []) if row.get("item_type") == "key"}
    for column in ("item_instance_id", "exit_id"):
        targets = [row[column] for row in content.get("mud_room_discoveries", []) if row.get(column)]
        if len(targets) != len(set(targets)):
            raise WorldError("An object or exit may belong to only one discovery.")
    if any(item["key_item_id"] and item["key_item_id"] not in key_ids for item in clean_exits):
        raise WorldError("Locked exits must reference a key item definition in content.json.")
    origins = docs.get(ORIGINS_FILE, {})
    if not isinstance(origins, dict) or set(origins) - {"races", "classes"}:
        raise WorldError("origins.json must contain races and/or classes arrays.")
    for kind, definitions in origins.items():
        if not isinstance(definitions, list) or len(definitions) > 100 or any(not isinstance(item, dict) for item in definitions):
            raise WorldError(f"origins.json {kind} must contain at most 100 definition objects.")
        for index, definition in enumerate(definitions):
            spells = definition.get("spells", [])
            for spell_index, spell in enumerate(spells if isinstance(spells, list) else []):
                if spell not in known_abilities:
                    report.error(ORIGINS_FILE, f"$.{kind}[{index}].spells[{spell_index}]",
                                 f"Ability {spell!r} is not built in, bundled in abilities.json, or installed.",
                                 "missing_reference")
    skills = docs.get(SKILLS_FILE, {"skills": []})
    if (not isinstance(skills, dict) or set(skills) != {"skills"} or not isinstance(skills["skills"], list)
            or len(skills["skills"]) > 100 or any(not isinstance(item, dict) for item in skills["skills"])):
        raise WorldError("skills.json must contain a skills array of at most 100 definition objects.")
    try:
        world_dungeons = mud_world_dungeons.read_document(docs.get(DUNGEONS_FILE), room_ids,
                                                          extra_effects=set(known_effects))
    except mud_world_dungeons.WorldDungeonError as problem:
        report.error(DUNGEONS_FILE, "", str(problem))
        world_dungeons = {"templates": [], "entrances": []}
    try:
        triggers = mud_triggers.validate_document(docs.get(EVENTS_FILE))
    except ValueError as problem:
        report.error(EVENTS_FILE, "$.triggers", str(problem))
        triggers = []
    known = {kind: {row.get("id") for row in content.get(table, [])}
             for kind, table in TRIGGER_TABLES.items() if table != "rooms"}
    known.update(room=room_ids, exit=exit_ids, trigger={trigger["id"] for trigger in triggers},
                 effect=set(known_effects))
    for trigger in triggers:
        for kind, ref in mud_triggers.references(trigger):
            if kind in known and ref not in known[kind]:
                report.error(EVENTS_FILE, f"$.triggers[{trigger['id']}]",
                             f"Trigger {trigger['id']} names an unknown {kind}: {ref}.", "missing_reference")
    if report.errors:
        raise WorldError(report.summary(), report)
    return {"id": world_id, "name": name, "author": author, "start_room": start, "resource": resource,
            "society": society, "format_version": format_version, "progression": progression,
            "availability": availability, "effects": list(local_effects.values()),
            "layers": [{"z": z, "name": layer_name} for z, layer_name in sorted(layers.items())],
            "abilities": list(local_abilities.values()),
            "rooms": clean_rooms, "exits": clean_exits, "content": content, "origins": origins,
            "skills": skills["skills"], "dungeons": world_dungeons, "events": triggers,
            # wilderness is the file as written, so a parsed document repackages unchanged;
            # wild is its validated run index, used to install and resolve '@x,y,z'.
            "wilderness": docs.get(WILDERNESS_FILE), "wild": wilderness}


def _housing_problems(content, start):
    """Package-wide housing rules: rooms belong to one property, and entrances, starts and storage fit."""
    problems = []
    properties = {row.get("id"): row for row in content.get("mud_properties", [])}
    names = [" ".join(str(row.get("name", "")).casefold().split()) for row in properties.values()]
    if len(names) != len(set(names)):
        problems.append("Two properties have the same name.")
    owner = {}
    for row in content.get("mud_property_rooms", []):
        if row.get("room_id") in owner:
            problems.append(f"Room {row.get('room_id')} belongs to more than one property.")
        owner[row.get("room_id")] = row.get("property_id")
    for property_id, row in properties.items():
        if property_id not in owner.values():
            problems.append(f"Property {property_id} needs at least one room in mud_property_rooms.")
        if row.get("entrance_room_id") in owner:
            problems.append(f"Property {property_id} entrance cannot itself be a property room.")
    if start in owner:
        problems.append("The start room cannot be a property room.")
    if any(row.get("room_id") in owner for row in content.get("mud_world_start_rules", [])):
        problems.append("Start rules cannot place characters in a property room.")
    for row in content.get("mud_storage_points", []):
        if row.get("scope") == "property" and row.get("room_id") not in owner:
            problems.append(f"Storage point {row.get('id')} keeps owners' items, so it must be in a property room.")
    return problems


TRANSPORT_VALIDATORS = {"mud_vehicle_definitions": ("vehicle", mud_transport.vehicle),
                        "mud_transit_lines": ("transit line", mud_transport.line),
                        "mud_transit_stops": ("transit stop", mud_transport.stop),
                        "mud_fuel_stations": ("fuel station", mud_transport.station)}


def _check_transport_row(table, row, room_ids, raw_ids):
    """One vehicle, line, stop, or station row; normalized list fields are written back."""
    label, validator = TRANSPORT_VALIDATORS[table]
    try:
        clean = validator(row)
    except ValueError as problem:
        raise WorldError(f"Invalid {label} {row.get('id')}: {problem}") from problem
    for column in ("modes", "terrains"):
        if column in row:
            row[column] = clean[column]
    for column in ("room_id", "interior_room_id", "cabin_room_id"):
        required = column == "room_id" and column in CONTENT_TABLES[table]
        if (row.get(column) is not None or required) and row.get(column) not in room_ids:
            raise WorldError(f"{label.capitalize()} {row.get('id')} {column} must name a room in the world.")
    if clean.get("fuel_item_id") and clean["fuel_item_id"] not in raw_ids("mud_item_definitions"):
        raise WorldError(f"Vehicle {row.get('id')} fuel item must name an item in content.json.")
    if table == "mud_transit_stops" and row.get("line_id") not in raw_ids("mud_transit_lines"):
        raise WorldError("Transit stop must name a line in content.json.")


def _transport_problems(content, clean_exits, start, wilderness):
    """Package-wide vehicle and transit rules: (errors, warnings).

    Vehicle interiors and line cabins are rooms reached only by boarding or
    riding, so nothing else may lead into or use them.
    """
    errors, warnings = [], []
    vehicles = content.get("mud_vehicle_definitions", [])
    lines = content.get("mud_transit_lines", [])
    for label, rows in (("vehicles", vehicles), ("transit lines", lines)):
        names = [" ".join(str(row.get("name", "")).casefold().split()) for row in rows]
        if len(names) != len(set(names)):
            errors.append(f"Two {label} have the same name.")
    private = [(row.get("interior_room_id"), f"Vehicle {row.get('id')} interior") for row in vehicles
               if row.get("interior_room_id")]
    private += [(row.get("cabin_room_id"), f"Line {row.get('id')} cabin") for row in lines if row.get("cabin_room_id")]
    if len({room for room, _label in private}) != len(private):
        errors.append("Each vehicle interior and line cabin needs a room of its own.")
    property_rooms = {row.get("room_id") for row in content.get("mud_property_rooms", [])}
    exit_rooms = {item["from"] for item in clean_exits} | {item["to"] for item in clean_exits}
    used = {row.get("room_id") for table in ("mud_vehicle_placements", "mud_fuel_stations", "mud_transit_stops",
                                              "mud_world_start_rules") for row in content.get(table, [])}
    for room, label in private:
        if room == start:
            errors.append(f"{label} cannot be the start room.")
        if room in exit_rooms:
            errors.append(f"{label} ({room}) cannot have exits; riders reach it only by boarding.")
        if room in property_rooms:
            errors.append(f"{label} cannot be a property room.")
        if room in used:
            errors.append(f"{label} cannot also be a stop, placement, fuel station, or start rule room.")
    for row in content.get("mud_vehicle_placements", []):
        if row.get("room_id") in property_rooms:
            errors.append(f"Vehicle placement {row.get('id')} cannot be in a property room.")
    for line_row in lines:
        stops = [row for row in content.get("mud_transit_stops", []) if row.get("line_id") == line_row.get("id")]
        if len(stops) < 2:
            errors.append(f"Line {line_row.get('id')} needs at least two stops.")
    declared = set()
    for row in vehicles:
        declared |= mud_transport.word_set(row.get("modes") if isinstance(row.get("modes"), str) else "")
    required = {item["requires_mode"] for item in clean_exits if item.get("requires_mode")}
    presets = (wilderness or {}).get("presets", []) if isinstance(wilderness, dict) else []
    required |= {preset.get("requires_mode") for preset in (presets if isinstance(presets, list) else [])
                 if isinstance(preset, dict) and preset.get("requires_mode")}
    for mode in sorted(required - declared):
        warnings.append(f"Some ways require the {mode} mode, but no vehicle in this world travels by {mode}.")
    placed = {row.get("vehicle_definition_id") for row in content.get("mud_vehicle_placements", [])}
    for row in vehicles:
        if row.get("return_seconds") and row.get("id") not in placed:
            warnings.append(f"Vehicle {row.get('id')} has a return time but is never placed, so it never returns.")
    return errors, warnings


def _read_magic(docs, report):
    """A package's own effects and abilities by local key; built-in keys are reserved."""
    effects, abilities = {}, {}
    for filename, field in ((EFFECTS_FILE, "effects"), (ABILITIES_FILE, "abilities")):
        document = docs.get(filename, {field: []})
        if (not isinstance(document, dict) or set(document) != {field} or not isinstance(document[field], list)
                or len(document[field]) > 100):
            raise WorldError(f"{filename} must contain a {field} array of at most 100 definitions.")
    known = mud_effects.catalog(None)
    for index, source in enumerate(docs.get(EFFECTS_FILE, {"effects": []})["effects"]):
        try:
            definition = mud_effects.normalize_effect(source)
        except ValueError as problem:
            report.error(EFFECTS_FILE, f"$.effects[{index}]", str(problem))
            continue
        if definition["key"] in mud_effects.EFFECTS:
            report.error(EFFECTS_FILE, f"$.effects[{index}].key", f"{definition['key']} is a built-in effect; "
                         "a package may apply it but not redefine it.", "engine_reserved")
        elif definition["key"] in effects:
            report.error(EFFECTS_FILE, f"$.effects[{index}].key", f"Duplicate effect key {definition['key']}.")
        else:
            effects[definition["key"]] = definition
    known.update(effects)
    for index, source in enumerate(docs.get(ABILITIES_FILE, {"abilities": []})["abilities"]):
        try:
            definition = mud_abilities.normalize_ability(source, known)
        except ValueError as problem:
            report.error(ABILITIES_FILE, f"$.abilities[{index}]", str(problem))
            continue
        if definition["key"] in mud_abilities.BUILT_IN:
            report.error(ABILITIES_FILE, f"$.abilities[{index}].key", f"{definition['key']} is a built-in "
                         "ability; a package may grant it but not redefine it.", "engine_reserved")
        elif definition["key"] in abilities:
            report.error(ABILITIES_FILE, f"$.abilities[{index}].key", f"Duplicate ability key {definition['key']}.")
        else:
            abilities[definition["key"]] = definition
    return effects, abilities


def _check_effect_lists(properties, known_effects, report, severity, path):
    """Item and NPC properties may apply, cure, or resist effects; each must exist."""
    if isinstance(properties, str):
        try:
            properties = json.loads(properties)
        except json.JSONDecodeError:
            return
    for field, problem in mud_effects.validate_properties(properties, extra_keys=known_effects):
        report.add(severity, CONTENT_FILE, f"{path}.{field}", problem)


def _room_grid(room, local_id, positions):
    """Optional grid placement for an authored room: x, y, z together, plus link settings."""
    axes = [room.get(axis) for axis in ("x", "y", "z")]
    if all(value is None for value in axes):
        if "grid_link" in room or "blocked_dirs" in room:
            raise WorldError(f"Room {local_id} sets grid links without x, y and z.")
        return {}
    limit = mud_wilderness.COORDINATE_LIMIT
    if not all(_whole(value, -limit, limit) for value in axes):
        raise WorldError(f"Room {local_id} needs whole-number x, y and z between -{limit} and {limit}.")
    position = tuple(axes)
    if position in positions:
        raise WorldError(f"Rooms {positions[position]} and {local_id} share the position {position}.")
    positions[position] = local_id
    grid_link = room.get("grid_link", True)
    if type(grid_link) is not bool:
        raise WorldError(f"Room {local_id} grid_link must be true or false.")
    try:
        blocked = mud_wilderness.validate_blocked(room.get("blocked_dirs", ""))
    except mud_wilderness.WildernessError as problem:
        raise WorldError(f"Room {local_id}: {problem}") from problem
    return {"x": position[0], "y": position[1], "z": position[2], "grid_link": grid_link, "blocked_dirs": blocked}


def validate_object_properties(properties):
    """Trigger-facing item properties: fixed objects and per-state descriptions."""
    if "fixed" in properties and type(properties["fixed"]) is not bool:
        raise WorldError("Item fixed must be true or false.")
    descriptions = properties.get("state_descriptions")
    if descriptions is not None and (
            not isinstance(descriptions, dict) or len(descriptions) > 20
            or any(not isinstance(key, str) or len(key) > 40 or not isinstance(text, str) or len(text) > 500
                   for key, text in descriptions.items())):
        raise WorldError("Item state_descriptions must map up to 20 states to text of up to 500 characters.")


def package_bytes(document):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = {key: document[key] for key in ("id", "name", "author", "start_room")}
        if document.get("format_version"):
            manifest = {"format_version": document["format_version"], **manifest}
        if document.get("resource", DEFAULT_RESOURCE) != DEFAULT_RESOURCE:
            manifest["resource"] = document["resource"]
        progression = mud_progression.changed(mud_progression.validate(document.get("progression")))
        if progression:
            manifest["progression"] = progression
        if document.get("availability"):
            manifest["availability"] = document["availability"]
        if document.get("layers"):
            manifest["layers"] = document["layers"]
        try:
            manifest.update(mud_society.changed_settings(mud_society.validate_settings(document.get("society"))))
        except mud_society.SocietyError as problem:
            raise WorldError(str(problem)) from problem
        for filename, data in (("manifest.json", manifest), ("rooms.json", document["rooms"]),
                               ("exits.json", document["exits"])):
            encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise WorldError(f"{filename} is too large to export.")
            archive.writestr(filename, encoded)
        if document.get("content"):
            encoded = (json.dumps(document["content"], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise WorldError("content.json is too large to export.")
            archive.writestr(CONTENT_FILE, encoded)
        if document.get("origins"):
            encoded = (json.dumps(document["origins"], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise WorldError("origins.json is too large to export.")
            archive.writestr(ORIGINS_FILE, encoded)
        if document.get("skills"):
            encoded = (json.dumps({"skills": document["skills"]}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise WorldError("skills.json is too large to export.")
            archive.writestr(SKILLS_FILE, encoded)
        for filename, field in ((EFFECTS_FILE, "effects"), (ABILITIES_FILE, "abilities")):
            if document.get(field):
                encoded = (json.dumps({field: document[field]}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                if len(encoded) > MAX_FILE_BYTES:
                    raise WorldError(f"{filename} is too large to export.")
                archive.writestr(filename, encoded)
        if document.get("dungeons"):
            encoded = (json.dumps(document["dungeons"], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise WorldError("dungeons.json is too large to export.")
            archive.writestr(DUNGEONS_FILE, encoded)
        if document.get("events"):
            encoded = (json.dumps({"triggers": document["events"]}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise WorldError("events.json is too large to export.")
            archive.writestr(EVENTS_FILE, encoded)
        if document.get("wilderness"):
            encoded = (json.dumps(document["wilderness"], ensure_ascii=False, indent=1) + "\n").encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise WorldError("wilderness.json is too large to export.")
            archive.writestr(WILDERNESS_FILE, encoded)
    payload = stream.getvalue()
    if len(payload) > MAX_ZIP_BYTES:
        raise WorldError("World ZIP is too large to export.")
    return payload


def template_bytes():
    return package_bytes({
        "format_version": PACKAGE_FORMAT,
        "id": "my-world", "name": "My World", "author": "Your name", "start_room": "start",
        "rooms": [{"id": "start", "name": "Starting Room",
                   "description": "The beginning of a world you can build from files."}],
        "exits": [],
        "events": [{"id": "welcome", "name": "Welcome message", "event": "enter_room",
                    "source": {"room": "start"}, "once": True,
                    "actions": [{"type": "message", "text": "Welcome. Edit events.json to make this world react."}]}],
    })


def example_bytes():
    """The shipped example world: every package file, ready to import into a clean station."""
    source = Path(__file__).with_name("mud_world_templates") / "example-world"
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.glob("*.json")):
            # A fixed timestamp keeps the archive, and so its package hash, the same every time.
            archive.writestr(zipfile.ZipInfo(path.name, date_time=(2026, 1, 1, 0, 0, 0)), path.read_bytes(),
                             compress_type=zipfile.ZIP_DEFLATED)
    return stream.getvalue()


def _room_references(document, installation_key):
    """Installed room IDs by package reference, wilderness squares included."""
    world_id = document["id"]

    def cell(key):
        found = mud_wilderness.lookup(document["wild"], key)
        return mud_wilderness.cell_id(world_id, *found[:3]) if found else None

    return mud_wilderness.RoomReferences(
        {room["id"]: f"world:{world_id}:{installation_key}:room:{room['id']}" for room in document["rooms"]}, cell)


def _content_maps(document, installation_key):
    maps = {"rooms": _room_references(document, installation_key)}
    maps["exits"] = {item["id"]: f"world:{document['id']}:{installation_key}:exit:{ordinal}"
                     for ordinal, item in enumerate(document["exits"]) if item.get("id")}
    for table in ID_TABLES:
        mapping = {}
        for row in document["content"].get(table, []):
            local_id = row.get("id")
            if not isinstance(local_id, str) or not ID_RE.fullmatch(local_id) or local_id in mapping:
                raise WorldError(f"{table} IDs must be unique lowercase IDs.")
            mapping[local_id] = f"world:{document['id']}:{installation_key}:{table}:{local_id}"
        maps[table] = mapping
    return maps


def _mapped(maps, table, value, column):
    if value is None or value == "":
        return None
    try:
        return maps[table][value]
    except (KeyError, TypeError):
        raise WorldError(f"{column} references unknown {table} ID {value!r}.") from None


def _remap_item_properties(value, mapper):
    """Point item race/class requirements at bundled origin keys (or back to local keys), and taught destinations."""
    properties = json.loads(value) if isinstance(value, str) else value
    if isinstance(properties, dict) and properties.get("requirements"):
        properties = {**properties, "requirements": mud_conditions.remap_item_requirements(
            mud_conditions.validate_item_requirements(properties["requirements"]), mapper)}
    if isinstance(properties, dict) and properties.get("teaches_destinations"):
        properties = {**properties, "teaches_destinations": [
            mapper("destination", ref) for ref in mud_wayfinding.validate_teaches(properties["teaches_destinations"])]}
    properties = _remap_effect_lists(properties, mapper)
    if not isinstance(properties, (dict, list)):
        raise WorldError("properties_json must be JSON text or an object.")
    return json.dumps(properties, ensure_ascii=False, separators=(",", ":"))


def _remap_effect_lists(properties, mapper):
    """Rename effect keys applied, cured, or resisted with mapper("effect", key)."""
    return mud_effects.remap_properties(properties, lambda key: mapper("effect", key))


def _insert_content(database, document, installation_key, skill_map=None, effect_map=None):
    from . import mud_skills
    maps = _content_maps(document, installation_key)
    installed_skills = {entry["key"] for entry in mud_skills.catalog(database)}
    world_id = document["id"]
    origin_map = _bundled_origin_map(document)

    def condition_mapper(kind, value):
        if kind in ORIGIN_KINDS:
            return origin_map.get((ORIGIN_KINDS[kind], value), value)
        if kind == "skill":
            return (skill_map or {}).get(value, value)
        if kind == "effect":
            return (effect_map or {}).get(value, value)
        return _mapped(maps, CONDITION_TABLES[kind], value, f"{kind} condition")

    quest_prerequisites = {row["id"]: row.get("prerequisite_quest_id")
                           for row in document["content"].get("mud_quests", [])}
    if any(value is not None and not isinstance(value, str)
           for value in quest_prerequisites.values()):
        raise WorldError("Quest prerequisite IDs must be strings.")
    for quest_id in quest_prerequisites:
        seen = set()
        cursor = quest_id
        while cursor:
            if cursor in seen:
                raise WorldError("Quest prerequisites cannot form a cycle.")
            seen.add(cursor)
            cursor = quest_prerequisites.get(cursor)
    for table, columns in CONTENT_TABLES.items():
        rows = document["content"].get(table, [])
        if table == "mud_item_instances":
            # Containers must exist before their children.
            remaining = list(rows)
            ordered = []
            placed = set()
            while remaining:
                ready = [row for row in remaining if not row.get("container_id") or row["container_id"] in placed]
                if not ready:
                    raise WorldError("Item containers form a cycle or reference a missing item.")
                ordered.extend(ready)
                placed.update(row["id"] for row in ready)
                remaining = [row for row in remaining if row not in ready]
            rows = ordered
        for row in rows:
            unknown = set(row) - set(columns)
            if unknown:
                raise WorldError(f"{table} has unsupported fields: {', '.join(sorted(unknown))}.")
            fields = {}
            for column in columns:
                if column not in row:
                    continue
                value = row[column]
                if column == "id":
                    value = _mapped(maps, table, value, column)
                elif column in REFERENCE_TABLE:
                    ref_table = REFERENCE_TABLE[column]
                    if ref_table is None:
                        ref_table = ("mud_item_definitions" if table in {"mud_item_instances", "mud_item_spawns"}
                                     else "mud_npc_definitions")
                    if table == "mud_quests" and column in ("prerequisite_quest_id", "turn_in_npc_definition_id"):
                        # Linked after every quest and NPC definition exists.
                        continue
                    value = _mapped(maps, ref_table, value, column)
                elif column == "target_id":
                    if table == "mud_quest_objectives":
                        if not isinstance(row.get("objective_type"), str):
                            raise WorldError("Quest objective has an unsupported type.")
                        if row["objective_type"] == "script":
                            fields[column] = _label(value, "Script objective target", 1, 80)
                            continue
                        ref_table = OBJECTIVE_TARGETS.get(row.get("objective_type"))
                    elif row.get("reward_type") == "reputation":
                        ref_table = "mud_world_factions"
                    else:
                        ref_table = "mud_item_definitions"
                    if ref_table is None:
                        raise WorldError("Quest objective has an unsupported type.")
                    value = _mapped(maps, ref_table, value, column)
                elif column in {"skill_key", "practice_skill_key"}:
                    # Trainers name a bundled skill by its local key or an installed skill.
                    value = (skill_map or {}).get(value, value)
                    if value is not None and value not in installed_skills:
                        raise WorldError(f"Skill {row[column]!r} is not bundled or installed.")
                elif table == "mud_work_activities" and column == "objective_target_id":
                    if value:
                        goal = row.get("objective_type")
                        ref_table = ("rooms" if goal == "visit_room" else
                                     "mud_npc_definitions" if goal in {"talk_npc", "defeat_npc"} else
                                     "mud_item_definitions")
                        value = _mapped(maps, ref_table, value, column)
                elif table == "mud_gathering_nodes" and column == "rewards_json":
                    value = json.dumps([{"item_id": _mapped(maps, "mud_item_definitions", reward["item_id"], column),
                                         "amount": reward["amount"]} for reward in mud_gathering.rewards(value)])
                elif column in CONDITION_COLUMNS.get(table, ()):
                    value = json.dumps(mud_conditions.remap(_condition_validator(column)(value), condition_mapper),
                                       ensure_ascii=False, separators=(",", ":"))
                elif column in QUEST_RULE_COLUMNS.get(table, {}):
                    kind = QUEST_RULE_COLUMNS[table][column]
                    value = json.dumps(_remap_quest_rules(kind, _quest_rules(kind, value), condition_mapper),
                                       ensure_ascii=False, separators=(",", ":"))
                elif table == "mud_item_definitions" and column == "properties_json":
                    value = _remap_item_properties(value, condition_mapper)
                elif table == "mud_npc_definitions" and column == "properties_json":
                    properties = json.loads(value) if isinstance(value, str) else value
                    value = json.dumps(_remap_effect_lists(properties, condition_mapper),
                                       ensure_ascii=False, separators=(",", ":"))
                elif table == "mud_item_instances" and column == "state_json":
                    state = json.loads(value) if isinstance(value, str) else value
                    if isinstance(state, dict):
                        state = {key: (_mapped(maps, OWNER_TABLES[key], item, key) if key in OWNER_TABLES else item)
                                 for key, item in state.items()}
                    value = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                elif table == "mud_npc_definitions" and column == "behaviors_json":
                    behaviors = json.loads(value) if isinstance(value, str) else value
                    value = json.dumps(mud_society.remap_behaviors(behaviors, lambda kind, ref: (
                        maps["rooms"][ref] if kind == "room"
                        else _mapped(maps, BEHAVIOR_TABLES[kind], ref, f"behavior {kind}"))),
                        ensure_ascii=False, separators=(",", ":"))
                elif table == "mud_world_destinations" and column == "map_items_json":
                    value = json.dumps([_mapped(maps, "mud_item_definitions", ref, column) for ref in value])
                elif column.endswith("_json"):
                    if isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    elif not isinstance(value, str):
                        raise WorldError(f"{column} must be JSON text or an object.")
                elif not isinstance(value, (str, int, float, type(None))):
                    raise WorldError(f"{table}.{column} must be a scalar value.")
                fields[column] = value
            if table in {"mud_item_definitions", "mud_npc_definitions", "mud_conversations", "mud_quests",
                         "mud_world_destinations", "mud_world_factions", "mud_professions",
                         "mud_world_start_rules", "mud_properties", "mud_vehicle_definitions",
                         "mud_transit_lines"}:
                fields["world_id"] = world_id
            names = list(fields)
            database.execute(
                f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})",
                tuple(fields[name] for name in names),
            )
            if table == "mud_item_spawns":
                instance_id = f"{fields['id']}:initial"
                database.execute("INSERT INTO mud_item_instances(id,definition_id,room_id,spawn_source_id) VALUES(?,?,?,?)",
                                 (instance_id, fields["definition_id"], fields["room_id"], fields["id"]))
                database.execute("UPDATE mud_item_spawns SET live_instance_id=? WHERE id=?",
                                 (instance_id, fields["id"]))
    for row in document["content"].get("mud_quests", []):
        if row.get("prerequisite_quest_id"):
            database.execute("UPDATE mud_quests SET prerequisite_quest_id=? WHERE id=?",
                             (_mapped(maps, "mud_quests", row["prerequisite_quest_id"], "prerequisite_quest_id"),
                              _mapped(maps, "mud_quests", row["id"], "id")))
        if row.get("turn_in_npc_definition_id"):
            database.execute("UPDATE mud_quests SET turn_in_npc_definition_id=? WHERE id=?",
                             (_mapped(maps, "mud_npc_definitions", row["turn_in_npc_definition_id"],
                                      "turn_in_npc_definition_id"),
                              _mapped(maps, "mud_quests", row["id"], "id")))
    return maps


def bundled_key(world_id, kind, local_key):
    """A repeatable global key independent of the import order or station."""
    digest = hashlib.sha256(f"{world_id}:{kind}:{local_key}".encode("utf-8")).hexdigest()[:32]
    return f"w-{digest}"


def _bundled_origin_map(document):
    from . import character_creation
    return {(kind, source.get("key")): bundled_key(document["id"], kind, source.get("key"))
            for kind in character_creation.KINDS for source in document["origins"].get(kind, [])
            if isinstance(source.get("key"), str)}


def _bundle_conflict(problem):
    raise WorldError(f"{problem} If this is a newer version of the same world, upgrade it instead.") from problem


def _install_magic(database, document, notes=None):
    """Install bundled effects, then abilities; return local -> installed key maps."""
    effect_map = {definition["key"]: bundled_key(document["id"], "effects", definition["key"])
                  for definition in document.get("effects", [])}
    ability_map = {definition["key"]: bundled_key(document["id"], "abilities", definition["key"])
                   for definition in document.get("abilities", [])}
    owner = ("world", document["id"])
    try:
        for definition in document.get("effects", []):
            mud_shared_definitions.install_bundled(database, "effects", effect_map[definition["key"]],
                                                   {**definition, "key": effect_map[definition["key"]]},
                                                   owner, definition["key"], notes)
        for definition in document.get("abilities", []):
            installed = mud_abilities.remap_effects({**definition, "key": ability_map[definition["key"]]},
                                                    effect_map)
            mud_shared_definitions.install_bundled(database, "abilities", installed["key"], installed,
                                                   owner, definition["key"], notes)
    except mud_shared_definitions.DefinitionConflict as problem:
        _bundle_conflict(problem)
    return effect_map, ability_map


def _install_skills(database, document, notes=None):
    """Install bundled skills before origins and content that reference them."""
    from . import character_creation, mud_skills

    sources = document.get("skills", [])
    local_keys = [source.get("key") for source in sources]
    if any(not isinstance(key, str) or not character_creation.KEY_RE.fullmatch(key) for key in local_keys) \
            or len(set(local_keys)) != len(local_keys):
        raise WorldError("Bundled skills need distinct valid local keys.")
    mapping = {key: bundled_key(document["id"], "skills", key) for key in local_keys}
    origin_map = _bundled_origin_map(document)
    extra_origins = {}
    for (kind, _local_key), key in origin_map.items():
        extra_origins.setdefault(kind, set()).add(key)
    for source, local_key in zip(sources, local_keys):
        key = mapping[local_key]
        try:
            definition = mud_skills.validate_skill(
                mud_skills.remap_origins(mud_skills.remap({**source, "key": key}, mapping), origin_map),
                database, check_key=False, extra_skills=mapping.values(), extra_origins=extra_origins)
        except ValueError as error:
            raise WorldError(f"Invalid bundled skill {local_key}: {error}") from error
        try:
            mud_shared_definitions.install_bundled(database, "skills", key, definition, ("world", document["id"]),
                                                   local_key, notes)
        except mud_shared_definitions.DefinitionConflict as problem:
            _bundle_conflict(problem)
    return mapping


def _install_origins(database, document, skill_map=None, ability_map=None, notes=None):
    from . import character_creation, mud_skills

    for kind in character_creation.KINDS:
        seen = set()
        for source in document["origins"].get(kind, []):
            local_key = source.get("key")
            if not isinstance(local_key, str) or not character_creation.KEY_RE.fullmatch(local_key) or local_key in seen:
                raise WorldError(f"Bundled {kind} need distinct valid local keys.")
            seen.add(local_key)
            key = bundled_key(document["id"], kind, local_key)
            source = {**source, "key": key}
            if isinstance(source.get("spells"), list):
                source["spells"] = [(ability_map or {}).get(spell, spell) for spell in source["spells"]]
            try:
                definition = character_creation.validate_origin(
                    kind, mud_skills.remap(source, skill_map or {}), database, check_key=False,
                    extra_abilities=(ability_map or {}).values())
            except ValueError as error:
                raise WorldError(f"Invalid bundled {kind[:-1]} {local_key}: {error}") from error
            try:
                mud_shared_definitions.install_bundled(database, kind, key, definition, ("world", document["id"]),
                                                       local_key, notes)
            except mud_shared_definitions.DefinitionConflict as problem:
                _bundle_conflict(problem)


def _package_availability(database, document, skill_map, notes=None):
    """The manifest's suggested race, class, and skill selection, as installed keys; None to use defaults."""
    from . import character_creation
    wanted = document.get("availability")
    if not wanted:
        return None
    origin_map = _bundled_origin_map(document)
    selected = {"races": set(), "classes": set(), "skills": set()}
    for kind in selected:
        for key in wanted.get(kind, []):
            selected[kind].add(skill_map.get(key, key) if kind == "skills" else origin_map.get((kind, key), key))
    for kind in character_creation.KINDS:
        if not selected[kind]:
            selected[kind] = set(availability_choice(database, document["id"])[kind])
    if not wanted.get("skills"):
        selected["skills"] = set(availability_choice(database, document["id"])["skills"])
    try:
        availability_choice(database, document["id"], "manual", selected)
        from . import mud_skills
        mud_skills.require_available(database, selected["skills"])
    except (WorldError, ValueError) as problem:
        if notes is not None:
            notes.append(f"The package's suggested availability could not be used ({problem}); "
                         "the default selection was applied.")
        return None
    return selected


def world_definitions(database, world_id):
    """The races, classes, skills, abilities, and effects a world owns, by kind, for World Forge."""
    from . import character_creation, mud_skills
    owner = ("world", world_id)
    sources = {"effects": mud_effects.catalog(database), "abilities": mud_abilities.catalog(database),
               "skills": {entry["key"]: entry["definition"] for entry in mud_skills.catalog(database, world="")}}
    origins = character_creation.catalog(database, world="")
    for kind in character_creation.KINDS:
        sources[kind] = {entry["key"]: entry["definition"] for entry in origins[kind]}
    local = {kind: {key: local_key for key, local_key in
                    mud_shared_definitions.owned_keys(database, owner, kind).items()}
             for kind in mud_shared_definitions.KINDS}
    result = {}
    for kind in mud_shared_definitions.KINDS:
        reverse = {installed: local_key for kinds in local.values() for installed, local_key in kinds.items()}
        entries = []
        for key, local_key in sorted(local[kind].items(), key=lambda pair: pair[1]):
            definition = sources[kind].get(key)
            if definition is None:
                continue
            shown = {field: value for field, value in definition.items() if field not in ("imported", "installed")}
            shown["key"] = local_key
            # Show the world's own definitions by their local keys, as authors write them.
            if kind == "abilities":
                shown = mud_abilities.remap_effects(shown, reverse)
            elif kind in character_creation.KINDS and shown.get("spells"):
                shown["spells"] = [reverse.get(spell, spell) for spell in shown["spells"]]
            if kind in ("skills", *character_creation.KINDS):
                shown = mud_skills.remap(shown, {key: value for key, value in reverse.items()
                                                 if key in local["skills"]})
            entries.append({"key": key, "local_key": local_key, "name": definition.get("name", local_key),
                            "json": json.dumps(shown, ensure_ascii=False, indent=2)})
        result[kind] = entries
    return result


def save_world_definition(database, world_id, kind, document):
    """Create or replace one definition a world owns; its references use the world's local keys."""
    from . import character_creation, mud_skills
    if kind not in mud_shared_definitions.KINDS:
        raise WorldError("Choose races, classes, skills, abilities, or effects.")
    if not isinstance(document, dict) or not isinstance(document.get("key"), str)             or not character_creation.KEY_RE.fullmatch(document["key"]):
        raise WorldError("The definition needs a key of 2-40 lowercase letters, digits, or hyphens.")
    local_key = document["key"]
    if (kind == "effects" and local_key in mud_effects.EFFECTS
            or kind == "abilities" and local_key in mud_abilities.BUILT_IN):
        raise WorldError(f"{local_key} is built in; choose another key.")
    owner = ("world", world_id)
    local = {other: {local_name: key for key, local_name in
                     mud_shared_definitions.owned_keys(database, owner, other).items()}
             for other in mud_shared_definitions.KINDS}
    key = bundled_key(world_id, kind, local_key)
    local[kind][local_key] = key
    try:
        if kind == "effects":
            definition = {**mud_effects.normalize_effect(document), "key": key}
        elif kind == "abilities":
            definition = mud_abilities.normalize_ability(
                mud_abilities.remap_effects({**document, "key": key}, local["effects"]),
                mud_effects.catalog(database))
        elif kind == "skills":
            source = mud_skills.remap_origins(mud_skills.remap({**document, "key": key}, local["skills"]),
                                              {(origin_kind, name): value for origin_kind in character_creation.KINDS
                                               for name, value in local[origin_kind].items()})
            definition = mud_skills.validate_skill(source, database, check_key=False, extra_skills=[key])
        else:
            source = {**document, "key": key}
            if isinstance(source.get("spells"), list):
                source["spells"] = [local["abilities"].get(spell, spell) for spell in source["spells"]]
            definition = character_creation.validate_origin(
                kind, mud_skills.remap(source, local["skills"]), database, check_key=False)
        notes = []
        mud_shared_definitions.install_bundled(database, kind, key, definition, owner, local_key, notes)
    except mud_shared_definitions.DefinitionConflict as problem:
        raise WorldError(str(problem)) from problem
    except ValueError as problem:
        raise WorldError(str(problem)) from problem
    if kind in ("races", "classes", "skills"):
        character_creation.offer(database, kind, [key], [world_id])
    return definition


def world_origin_keys(database, world_id):
    keys = {kind: {row["origin_key"] for row in database.execute(
        "SELECT origin_key FROM mud_world_origins WHERE world_id=? AND kind=?", (world_id, kind))}
        for kind in ("races", "classes")}
    keys["skills"] = {row["skill_key"] for row in database.execute(
        "SELECT skill_key FROM mud_world_skills WHERE world_id=?", (world_id,))}
    return keys


AVAILABILITY_MODES = ("only", "combine", "manual")


def default_availability_mode(database, world_id):
    """Worlds that bundle a race and a class offer only those; others add theirs to the built-ins."""
    bundled = world_origin_keys(database, world_id)
    return "only" if bundled["races"] and bundled["classes"] else "combine"


def availability_choice(database, world_id, mode=None, selected=None):
    """The race, class, and skill keys a world would offer under an availability mode.

    only: the world's bundled definitions (plus built-in starting skills if it bundles too few);
    combine: the world's bundled definitions with the built-ins; manual: exactly ``selected``.
    """
    from . import character_creation, mud_skills
    mode = mode or default_availability_mode(database, world_id)
    if mode not in AVAILABILITY_MODES:
        raise WorldError("Choose a valid race, class, and skill availability mode.")
    installed = {kind: {entry["key"] for entry in character_creation.catalog(database, world="")[kind]}
                 for kind in character_creation.KINDS}
    installed["skills"] = {entry["key"] for entry in mud_skills.catalog(database, world="")}
    bundled = {kind: keys & installed[kind] for kind, keys in world_origin_keys(database, world_id).items()}
    defaults = character_creation.default_availability()
    if mode == "manual":
        chosen = {kind: set((selected or {}).get(kind) or ()) for kind in character_creation.AVAILABILITY_KINDS}
        if any(chosen[kind] - installed[kind] for kind in chosen):
            raise WorldError("Choose only installed races, classes, and skills.")
        return chosen
    chosen = {kind: set(bundled[kind]) if mode == "only" else bundled[kind] | defaults[kind]
              for kind in character_creation.AVAILABILITY_KINDS}
    for kind in character_creation.KINDS:
        if not chosen[kind]:
            chosen[kind] = set(defaults[kind])
    try:
        mud_skills.require_available(database, chosen["skills"])
    except ValueError:
        chosen["skills"] |= defaults["skills"]
    return chosen


def set_availability(database, world_id, mode=None, selected=None):
    """Store one world's selection; other worlds' selections are unaffected."""
    from . import character_creation
    chosen = availability_choice(database, world_id, mode, selected)
    try:
        return character_creation.set_world_availability(database, world_id, chosen)
    except ValueError as error:
        raise WorldError(str(error)) from error


def world_name(database, world_id):
    if world_id == mud_scope.PROCEDURAL:
        row = database.execute("SELECT name FROM mud_portal_settings WHERE id=1").fetchone()
        return row["name"] if row else "Dungeon Portals"
    row = database.execute("SELECT name FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
    return row["name"] if row else world_id


def worlds(database, *, include_disabled=False):
    """Every installed authored world plus the procedural dungeon world, home world first."""
    station = database.execute("SELECT mud_procedural_enabled FROM station WHERE id=1").fetchone()
    rows = database.execute(
        "SELECT p.id,p.name,p.author,p.imported,p.enabled,p.removing,"
        "(SELECT COUNT(*) FROM mud_characters AS c WHERE c.world_key=p.id AND c.creation_state<>'pending') "
        "AS characters FROM mud_world_packages AS p ORDER BY p.id<>?,p.name COLLATE NOCASE,p.id",
        (mud_scope.HOME,)).fetchall()
    result = [{"key": row["id"], "name": row["name"], "author": row["author"], "kind": "authored",
               "imported": bool(row["imported"]), "enabled": bool(row["enabled"]),
               "removing": bool(row["removing"]), "characters": row["characters"]} for row in rows]
    procedural = bool(station and station["mud_procedural_enabled"])
    result.append({"key": mud_scope.PROCEDURAL, "name": world_name(database, mud_scope.PROCEDURAL),
                   "author": "StationNet", "kind": "procedural", "imported": False, "enabled": procedural,
                   "removing": False,
                   "characters": database.execute(
                       "SELECT COUNT(*) FROM mud_characters WHERE world_key=? AND creation_state<>'pending'",
                       (mud_scope.PROCEDURAL,)).fetchone()[0]})
    return result if include_disabled else [world for world in result if world["enabled"]]


def set_enabled(database, world_id, enabled):
    """Offer or hide a world on the player world list; its content and characters are untouched."""
    from . import character_creation
    if world_id == mud_scope.PROCEDURAL:
        database.execute("UPDATE station SET mud_procedural_enabled=? WHERE id=1", (int(bool(enabled)),))
        return
    world = database.execute("SELECT start_room_id FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
    if world is None:
        raise WorldError("World was not found.")
    if enabled:
        if database.execute("SELECT removing FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()[0]:
            raise WorldError("This world is being removed.")
        if database.execute("SELECT 1 FROM mud_world_rooms WHERE id=? AND world_id=?",
                            (world["start_room_id"], world_id)).fetchone() is None:
            raise WorldError("World starting room is missing.")
        chosen = character_creation.world_availability(database, world_id)
        if not chosen["races"] or not chosen["classes"]:
            raise WorldError("Offer at least one race and one class in this world first.")
    database.execute("UPDATE mud_world_packages SET enabled=? WHERE id=?", (int(bool(enabled)), world_id))


def _exit_condition_mapper(document, maps, skill_map, effect_map=None):
    """Exit conditions resolve like content conditions, once content IDs exist."""
    origin_map = _bundled_origin_map(document)

    def mapper(kind, value):
        if kind in ORIGIN_KINDS:
            return origin_map.get((ORIGIN_KINDS[kind], value), value)
        if kind == "skill":
            return (skill_map or {}).get(value, value)
        if kind == "effect":
            return (effect_map or {}).get(value, value)
        return _mapped(maps, CONDITION_TABLES[kind], value, f"exit {kind} condition")
    return mapper


def package_hash(payload):
    return hashlib.sha256(payload).hexdigest()


def install(database, payload, *, origin_mode=None, selected=None, enable=False):
    report = PackageReport()
    document = read_package(payload, database, report)
    world_id = document["id"]
    if database.execute("SELECT 1 FROM mud_world_packages WHERE id=?", (world_id,)).fetchone():
        report.error("manifest.json", "$.id", "A world with that ID is already installed; upgrade it instead.",
                     "conflict")
        raise WorldError("A world with that ID is already installed. Import it as an upgrade to replace it.",
                         report)
    destination = directory() / f"{world_id}.zip"
    if destination.exists():
        raise WorldError("A world ZIP with that ID already exists.")
    created = False
    try:
        database.execute("BEGIN IMMEDIATE")
        _install_rows(database, document, payload, origin_mode=origin_mode, selected=selected)
        if enable:
            set_enabled(database, world_id, True)
        with destination.open("xb") as target:
            created = True
            target.write(payload)
        database.commit()
    except Exception:
        database.rollback()
        if created:
            destination.unlink(missing_ok=True)
        raise
    return document


def validate_install(database, payload):
    """Install inside a savepoint and roll back: every check a real import makes, nothing kept.

    Returns the read document and its report; a failed check is an error in the report.
    """
    report = PackageReport()
    with dry_run(database, "world_dry_run"):
        document = read_package(payload, database, report)
        notes = []
        if database.execute("SELECT 1 FROM mud_world_packages WHERE id=?", (document["id"],)).fetchone() is None:
            try:
                _install_rows(database, document, payload, notes=notes)
            except (WorldError, ValueError) as problem:
                report.error("", "", str(problem))
        for note in notes:
            report.info("", "", note)
        return document, report


def _install_rows(database, document, payload, *, origin_mode=None, selected=None, notes=None, upgrade=False):
    """Insert every row of a read package; the caller owns the transaction."""
    notes = [] if notes is None else notes
    world_id = document["id"]
    installation_key = uuid.uuid4().hex
    prefix = f"world:{world_id}:{installation_key}:room:"
    values = (document["name"], document["author"], prefix + document["start_room"],
              int(document["resource"]["enabled"]), document["resource"]["name"],
              json.dumps(mud_society.changed_settings(document["society"]), separators=(",", ":")),
              json.dumps(mud_progression.changed(document["progression"]), separators=(",", ":")),
              document["format_version"], package_hash(payload))
    if upgrade:
        database.execute("UPDATE mud_world_packages SET name=?,author=?,start_room_id=?,resource_enabled=?,"
                         "resource_name=?,society_json=?,progression_json=?,format_version=?,package_hash=? "
                         "WHERE id=?", (*values, world_id))
    else:
        database.execute("INSERT INTO mud_world_packages(name,author,start_room_id,resource_enabled,resource_name,"
                         "society_json,progression_json,format_version,package_hash,id,imported) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,1)", (*values, world_id))
    effect_map, ability_map = _install_magic(database, document, notes)
    skill_map = _install_skills(database, document, notes)
    for room in document["rooms"]:
        x, y, z = room.get("x"), room.get("y"), room.get("z")
        database.execute("INSERT INTO mud_world_rooms(id,name,description,world_id,x,y,z,grid_link,blocked_dirs,banking_enabled) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (prefix + room["id"], room["name"], room["description"], world_id, x, y, z,
                          int(room.get("grid_link", True)), room.get("blocked_dirs", ""), int(room["bank"])))
    mud_wilderness.install_layer_names(database, world_id,
                                       {entry["z"]: entry["name"] for entry in document.get("layers", [])})
    try:
        preset_ids, region_ids = mud_wilderness.install_cells(database, world_id, installation_key,
                                                              document["wild"])
    except mud_wilderness.WildernessError as problem:
        raise WorldError(f"Invalid wilderness.json: {problem}") from problem
    room_refs = _room_references(document, installation_key)
    for ordinal, item in enumerate(document["exits"]):
        database.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction,pick_difficulty,"
                         "conditions_json,conditions_message,local_id,hidden,requires_mode,allow_vehicles) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"world:{world_id}:{installation_key}:exit:{ordinal}", room_refs[item["from"]],
                          room_refs[item["to"]], item["name"], item["direction"], item["pick_difficulty"],
                          "[]", item["conditions_message"], item.get("id"), int(item.get("hidden", False)),
                          item.get("requires_mode"), int(item.get("allow_vehicles", True))))
    maps = _insert_content(database, document, installation_key, skill_map, effect_map)

    def shared(table):
        return lambda value: value if value.startswith("stationnet:") else _mapped(maps, table, value, "preset reference")

    mud_wilderness.install_references(database, world_id, document["wild"], preset_ids, region_ids,
                                      shared("mud_npc_definitions"), shared("mud_item_definitions"))
    exit_mapper = _exit_condition_mapper(document, maps, skill_map, effect_map)
    for ordinal, item in enumerate(document["exits"]):
        if item["conditions"]:
            database.execute("UPDATE mud_world_exits SET conditions_json=? WHERE id=?",
                             (json.dumps(mud_conditions.remap(item["conditions"], exit_mapper),
                                         ensure_ascii=False, separators=(",", ":")),
                              f"world:{world_id}:{installation_key}:exit:{ordinal}"))
        if item["key_item_id"]:
            database.execute("UPDATE mud_world_exits SET key_item_id=? WHERE id=?",
                             (_mapped(maps, "mud_item_definitions", item["key_item_id"], "key_item_id"),
                              f"world:{world_id}:{installation_key}:exit:{ordinal}"))
    if document["dungeons"]["templates"]:
        try:
            mud_world_dungeons.install_document(
                database, world_id, document["dungeons"], room_refs, maps, exit_mapper, effect_map)
        except mud_world_dungeons.WorldDungeonError as problem:
            raise WorldError(str(problem)) from problem
        except ValueError as problem:
            raise WorldError(f"Invalid dungeons.json: {problem}") from problem
    _install_triggers(database, document, installation_key, maps, exit_mapper)
    _install_origins(database, document, skill_map, ability_map, notes)
    from . import mud
    for placement in maps.get("mud_vehicle_placements", {}).values():
        mud.spawn_placed_vehicle(database, placement)
    if origin_mode is None and selected is None:
        package_choice = _package_availability(database, document, skill_map, notes)
        if package_choice is not None:
            origin_mode, selected = "manual", package_choice
    set_availability(database, world_id, origin_mode, selected)
    return {"maps": maps, "installation_key": installation_key, "notes": notes}


GLOBAL_ID_RE = re.compile(r"^world:(?P<world>[a-z][a-z0-9-]{0,63}):[0-9a-f]{32}:(?P<table>[a-z_]+):(?P<local>[a-z0-9-]+)$")
# Package section -> how to list its local IDs, for an upgrade's summary of changes.
DIFF_SECTIONS = ("rooms", "exits", "effects", "abilities", "skills", "events")


def _keyed(rows, key="id"):
    return {row[key]: row for row in rows or [] if isinstance(row, dict) and row.get(key)}


def document_changes(current, incoming):
    """What an upgrade adds, removes, and changes, by package-local ID."""
    sections = {name: (_keyed(current.get(name)), _keyed(incoming.get(name)))
                for name in ("rooms", "exits")}
    for name in ("effects", "abilities", "skills"):
        sections[name] = (_keyed(current.get(name), "key"), _keyed(incoming.get(name), "key"))
    sections["events"] = (_keyed(current.get("events")), _keyed(incoming.get("events")))
    for kind in ("races", "classes"):
        sections[kind] = (_keyed((current.get("origins") or {}).get(kind), "key"),
                          _keyed((incoming.get("origins") or {}).get(kind), "key"))
    for table in CONTENT_TABLES:
        if table in ID_TABLES:
            sections[table] = (_keyed((current.get("content") or {}).get(table)),
                               _keyed((incoming.get("content") or {}).get(table)))
    templates = lambda document: _keyed(((document.get("dungeons") or {}).get("templates")), "key")
    sections["dungeons"] = (templates(current), templates(incoming))
    result = {}
    for name, (before, after) in sections.items():
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(key for key in set(before) & set(after) if before[key] != after[key])
        if added or removed or changed:
            result[name] = {"added": added, "removed": removed, "changed": changed}
    return result


def upgrade_preview(database, payload):
    """Validate a newer package for an installed world and describe what replacing it would do."""
    world_id = read_package(payload, database)["id"]
    current, _current_report = export_document(database, world_id)
    document, report, incoming = validate_upgrade(database, payload)
    rooms = {room["id"]: room for room in document["rooms"]}
    local_rooms = local_ids([row["id"] for row in database.execute(
        "SELECT id FROM mud_world_rooms WHERE world_id=? AND kind='authored' ORDER BY id", (world_id,))],
        world_id, "room", "room")
    displaced = database.execute(
        "SELECT COUNT(*) FROM mud_player_locations l JOIN mud_world_rooms r ON r.id=l.location_key "
        "WHERE r.world_id=? AND l.location_type='world_room'", (world_id,)).fetchone()[0]
    staying = sum(1 for row in database.execute(
        "SELECT l.location_key FROM mud_player_locations l JOIN mud_world_rooms r ON r.id=l.location_key "
        "WHERE r.world_id=? AND l.location_type='world_room'", (world_id,))
        if local_rooms.get(row["location_key"]) in rooms)
    world = database.execute("SELECT name,enabled,package_hash FROM mud_world_packages WHERE id=?",
                             (world_id,)).fetchone()
    return {"world": world["name"], "id": world_id, "enabled": bool(world["enabled"]),
            "expected_hash": package_hash(payload), "installed_hash": world["package_hash"],
            "changes": document_changes(current, incoming or {}),
            "characters": database.execute(
                "SELECT COUNT(*) FROM mud_characters WHERE world_key=? AND creation_state<>'pending'",
                (world_id,)).fetchone()[0],
            "moved_to_start": displaced - staying, "problems": report.as_json()}


def validate_upgrade(database, payload):
    """Every check an upgrade makes, inside a savepoint that is rolled back.

    Also returns the upgraded world as export_document would write it, so it
    compares cleanly with the installed world's own export.
    """
    report = PackageReport()
    document = read_package(payload, database, report)
    world = database.execute("SELECT imported FROM mud_world_packages WHERE id=?", (document["id"],)).fetchone()
    if world is None:
        raise WorldError("No installed world has that ID; import it as a new world instead.", report)
    if not _upgradable(document["id"], world):
        raise WorldError("The home world cannot be replaced by an import; edit it in World Forge.", report)
    incoming = None
    with dry_run(database, "world_upgrade_check"):
        try:
            _upgrade_rows(database, document, payload)
            incoming, _incoming_report = export_document(database, document["id"])
        except (WorldError, ValueError) as problem:
            report.error("", "", str(problem))
    if report.errors:
        raise WorldError(report.summary(), report)
    return document, report, incoming


def upgrade(database, payload, expected_hash, *, reset_world_state=False):
    """Replace an installed world with a newer package, keeping its characters and their progress.

    Content is matched by package-local ID: quests, objectives, reputation, NPC
    regard, learned destinations, held items, and trigger state carry over to
    the new rows with the same IDs. Characters standing in a room the new
    version removes are moved to its start room.
    """
    if package_hash(payload) != expected_hash:
        raise WorldError("This file differs from the one that was previewed; preview it again.")
    document = read_package(payload, database)
    world = database.execute("SELECT imported FROM mud_world_packages WHERE id=?", (document["id"],)).fetchone()
    if not _upgradable(document["id"], world):
        raise WorldError("Only installed custom worlds can be upgraded.")
    path = directory() / f"{document['id']}.zip"
    staged = path.with_name(f"{path.name}.upgrading-{uuid.uuid4().hex}")
    staged.write_bytes(payload)
    try:
        database.execute("BEGIN IMMEDIATE")
        summary = _upgrade_rows(database, document, payload, reset_world_state=reset_world_state)
        database.execute("INSERT INTO mud_world_upgrades(world_id,from_hash,to_hash,summary_json) VALUES(?,?,?,?)",
                         (document["id"], summary["from_hash"], expected_hash,
                          json.dumps(summary, separators=(",", ":"), default=sorted)))
        database.commit()
    except Exception:
        database.rollback()
        staged.unlink(missing_ok=True)
        raise
    try:
        staged.replace(path)
    except OSError:
        summary["file_warning"] = True
    return document, summary


# Field-only edits World Forge can publish in place, like the old builder's row edits: no rows
# appear or vanish and nothing that names other content changes, so live state (items players
# dropped, NPC positions, dungeon runs) stays put. Anything else goes through a full upgrade.
LIGHT_ROOM_FIELDS = {"name", "description", "bank"}
LIGHT_EXIT_FIELDS = {"hidden", "pick_difficulty", "conditions_message"}
LIGHT_CONTENT_FIELDS = {
    "mud_item_definitions": {"name", "description"},
    "mud_npc_definitions": {"name", "description"},
    "mud_conversations": {"name"},
    "mud_dialogue_nodes": {"body"},
    "mud_dialogue_responses": {"response_text"},
    "mud_quests": {"name", "description", "summary"},
    "mud_quest_objectives": {"description"},
    "mud_world_factions": {"name", "description"},
    "mud_world_destinations": {"name", "description"},
    "mud_shop_stock": {"price", "quantity"},
    "mud_npc_trainers": {"price"},
    "mud_professions": {"name", "description"},
    "mud_work_activities": {"name", "description"},
    "mud_properties": {"name", "description"},
    "mud_storage_points": {"name", "description"},
    "mud_vehicle_definitions": {"name", "description"},
    "mud_npc_combat_profiles": {"maximum_health", "attack", "defense", "experience_reward",
                                "respawn_seconds", "accuracy", "evasion"},
}
LIGHT_KEYS = {"mud_npc_combat_profiles": "npc_definition_id"}


def _exit_key(row):
    return row.get("id") or ("", row.get("from"), row.get("name"))


def _row_edits(before, after, allowed):
    """Changed fields between two rows, or None when a field outside ``allowed`` changed."""
    edits = {}
    for key in set(before) | set(after):
        if before.get(key) != after.get(key):
            if key not in allowed:
                return None
            edits[key] = after.get(key)
    return edits


def light_changes(current, incoming):
    """Per-row field edits that turn ``current`` into ``incoming``, or None if any change is structural."""
    plain = lambda document: {key: value for key, value in document.items()
                              if key not in ("id", "name", "author", "rooms", "exits", "content")}
    if plain(current) != plain(incoming):
        return None
    edits = []
    if current.get("name") != incoming.get("name") or current.get("author") != incoming.get("author"):
        edits.append(("manifest", None, {"name": incoming.get("name"), "author": incoming.get("author")}))
    sections = [("rooms", lambda row: row.get("id"), LIGHT_ROOM_FIELDS, current.get("rooms"), incoming.get("rooms")),
                ("exits", _exit_key, LIGHT_EXIT_FIELDS, current.get("exits"), incoming.get("exits"))]
    before_content, after_content = current.get("content") or {}, incoming.get("content") or {}
    for table in set(before_content) | set(after_content):
        key = LIGHT_KEYS.get(table, "id")
        if table not in LIGHT_CONTENT_FIELDS or key not in CONTENT_TABLES[table]:
            if before_content.get(table) != after_content.get(table):
                return None
            continue
        sections.append((table, lambda row, key=key: row.get(key), LIGHT_CONTENT_FIELDS[table],
                         before_content.get(table), after_content.get(table)))
    for section, key, allowed, before_rows, after_rows in sections:
        before_rows = {key(row): row for row in before_rows or []}
        after_rows = {key(row): row for row in after_rows or []}
        if set(before_rows) != set(after_rows):
            return None
        for local, row in after_rows.items():
            changed = _row_edits(before_rows[local], row, allowed)
            if changed is None:
                return None
            if changed:
                edits.append((section, local, changed))
    return edits


def light_upgrade_plan(database, payload):
    """The field edits a package makes to its installed world, or None when it needs a full upgrade."""
    document, _report, incoming = validate_upgrade(database, payload)
    maps = {}
    current, _current_report = export_document(database, document["id"], keep_id=True, maps_out=maps)
    if incoming is None:
        return None
    incoming = {**incoming, "id": current["id"]}
    edits = light_changes(current, incoming)
    return None if edits is None else {"document": document, "edits": edits, "maps": maps}


def light_upgrade(database, payload, expected_hash):
    """Apply a field-only package in place; returns (document, summary) or None when it is not field-only."""
    if package_hash(payload) != expected_hash:
        raise WorldError("This file differs from the one that was previewed; preview it again.")
    plan = light_upgrade_plan(database, payload)
    if plan is None:
        return None
    document, maps = plan["document"], plan["maps"]
    world_id = document["id"]
    to_global = {section: {local: global_id for global_id, local in mapping.items()}
                 for section, mapping in maps.items() if isinstance(mapping, dict)}
    exits = {}
    for row in database.execute("SELECT e.id,e.from_room_id,e.name FROM mud_world_exits e "
                                "JOIN mud_world_rooms r ON r.id=e.from_room_id WHERE r.world_id=?", (world_id,)):
        exits[("", maps["rooms"].get(row["from_room_id"]), row["name"])] = row["id"]
    exits.update(to_global.get("exits", {}))
    before = database.execute("SELECT package_hash FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
    touched_rooms = []
    try:
        database.execute("BEGIN IMMEDIATE")
        for section, local, fields in plan["edits"]:
            if section == "manifest":
                database.execute("UPDATE mud_world_packages SET name=?,author=? WHERE id=?",
                                 (fields["name"], fields["author"], world_id))
                continue
            if section == "rooms":
                target, table, key = to_global["rooms"].get(local), "mud_world_rooms", "id"
                fields = {("banking_enabled" if name == "bank" else name): (int(bool(value)) if name == "bank" else value)
                          for name, value in fields.items()}
                touched_rooms.append(target)
            elif section == "exits":
                target, table, key = exits.get(local), "mud_world_exits", "id"
                fields = {name: (int(bool(value)) if name == "hidden" else value) for name, value in fields.items()}
            else:
                key = LIGHT_KEYS.get(section, "id")
                table = section
                target = (to_global.get("mud_npc_definitions", {}).get(local) if key == "npc_definition_id"
                          else to_global.get(section, {}).get(local))
            if target is None:
                raise WorldError("The installed world changed while this edit was prepared; preview it again.")
            database.execute(f"UPDATE {table} SET {','.join(f'{name}=?' for name in fields)} WHERE {key}=?",
                             (*fields.values(), target))
        database.execute("UPDATE mud_world_packages SET package_hash=? WHERE id=?", (expected_hash, world_id))
        summary = {"from_hash": before["package_hash"] if before else None, "light": True,
                   "edits": len(plan["edits"]), "moved_to_start": 0, "carried": 0, "notes": [],
                   "moved_users": [], "touched_rooms": [room for room in touched_rooms if room],
                   "ended_holding_owners": []}
        database.execute("INSERT INTO mud_world_upgrades(world_id,from_hash,to_hash,summary_json) VALUES(?,?,?,?)",
                         (world_id, summary["from_hash"], expected_hash,
                          json.dumps(summary, separators=(",", ":"), default=sorted)))
        database.commit()
    except Exception:
        database.rollback()
        raise
    try:
        (directory() / f"{world_id}.zip").write_bytes(payload)
    except OSError:
        summary["file_warning"] = True
    return document, summary


def _upgrade_rows(database, document, payload, *, reset_world_state=False):
    """Swap an installed world's content for a package's; the caller owns the transaction."""
    from . import character_creation
    world_id = document["id"]
    before = database.execute("SELECT enabled,package_hash FROM mud_world_packages WHERE id=?",
                              (world_id,)).fetchone()
    offered = character_creation.world_availability(database, world_id)
    configured = character_creation.availability_configured(database, world_id)
    old_links = world_origin_keys(database, world_id)
    # Rows that cascade away with their rooms are saved first and restored by local ID.
    learned = [dict(row) for row in database.execute(
        "SELECT d.character_id,d.destination_id,d.source,d.learned_at FROM mud_character_destinations d "
        "JOIN mud_world_destinations w ON w.id=d.destination_id WHERE w.world_id=?", (world_id,))]
    locations = [dict(row) for row in database.execute(
        "SELECT l.user_id,l.location_key FROM mud_player_locations l JOIN mud_world_rooms r ON r.id=l.location_key "
        "WHERE r.world_id=? AND l.location_type='world_room'", (world_id,))]
    old_exits = {row["id"]: row["local_id"] for row in database.execute(
        "SELECT e.id,e.local_id FROM mud_world_exits e JOIN mud_world_rooms r ON r.id=e.from_room_id "
        "WHERE r.world_id=?", (world_id,))}
    old_rooms = local_ids([row["id"] for row in database.execute(
        "SELECT id FROM mud_world_rooms WHERE world_id=? AND kind='authored' ORDER BY id", (world_id,))],
        world_id, "room", "room")
    # Rows a builder created were never imported, so their IDs carry no local ID; the
    # installed world's own export names them, exactly as a World Forge draft saw them.
    old_local = {}
    try:
        export_document(database, world_id, keep_id=True, maps_out=old_local)
    except (WorldError, ValueError):
        old_local = {}
    for global_id, local in (old_local.get("rooms") or {}).items():
        if not local.startswith("@"):
            old_rooms.setdefault(global_id, local)
    for global_id, local in (old_local.get("exits") or {}).items():
        if global_id in old_exits and not old_exits[global_id]:
            old_exits[global_id] = local
    present_users = [row["user_id"] for row in locations]
    housing = _housing_snapshot(database, world_id)
    transport = _transport_snapshot(database, world_id)
    database.execute("UPDATE mud_world_packages SET enabled=0 WHERE id=?", (world_id,))
    retired_id = _retire_rows(database, world_id, "This world was upgraded; start a new run.", keep_state=True)
    for table in ("mud_world_triggers", "mud_world_dungeon_templates", "mud_world_room_presets", "mud_world_regions",
                  "mud_world_destinations", "mud_world_start_rules"):
        database.execute(f"DELETE FROM {table} WHERE world_id=?", (world_id,))
    for kind in ("effects", "abilities", "skills", "races"):
        table, owner_column, _key = mud_shared_definitions.LINKS["world"][kind]
        database.execute(f"DELETE FROM {table} WHERE {owner_column}=?", (world_id,))
    notes = []
    installed = _install_rows(database, document, payload, notes=notes, upgrade=True)
    if configured:
        try:
            set_availability(database, world_id, "manual",
                             _carried_availability(database, world_id, offered, old_links))
        except WorldError as problem:
            notes.append(f"The world's race, class, and skill selection was reset: {problem}")
    maps = installed["maps"]
    new_rooms = maps["rooms"]
    start = database.execute("SELECT start_room_id FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()[0]

    def carried(old_id, table):
        match = GLOBAL_ID_RE.fullmatch(old_id or "")
        local = (match.group("local") if match and match.group("world") == world_id
                 else (old_local.get(table) or {}).get(old_id))
        return maps.get(table, {}).get(local) if local else None

    kept_copies = _restore_housing(database, housing, carried, old_rooms, new_rooms)
    riders = _restore_transport(database, transport, carried, old_rooms, new_rooms, start)
    moved, moved_users = 0, []
    for row in locations:
        if row["location_key"] in kept_copies:
            continue
        local = old_rooms.get(row["location_key"])
        target = riders.get(row["user_id"]) or (new_rooms.get(local) if local else None)
        if target is None:
            target, moved = start, moved + 1
            moved_users.append(row["user_id"])
        database.execute("UPDATE mud_player_locations SET location_key=? WHERE user_id=?", (target, row["user_id"]))
    # A character's start room keeps following its local ID; wilderness cells keep theirs.
    for row in database.execute("SELECT id,start_room_id FROM mud_characters WHERE world_key=? "
                                "AND start_room_id IS NOT NULL", (world_id,)).fetchall():
        local = old_rooms.get(row["start_room_id"])
        if local:
            database.execute("UPDATE mud_characters SET start_room_id=? WHERE id=?",
                             (new_rooms.get(local), row["id"]))
    for row in learned:
        target = carried(row["destination_id"], "mud_world_destinations")
        if target:
            database.execute("INSERT OR IGNORE INTO mud_character_destinations(character_id,destination_id,source,"
                             "learned_at) VALUES(?,?,?,?)",
                             (row["character_id"], target, row["source"], row["learned_at"]))
    # Player progress follows its content to the new rows with the same local IDs.
    carry = (("mud_player_quests", "quest_id", "mud_quests", ("character_id",)),
             ("mud_player_objectives", "objective_id", "mud_quest_objectives", ("character_id",)),
             ("mud_character_reputation", "faction_id", "mud_world_factions", ("character_id",)),
             ("mud_character_factions", "faction_id", "mud_world_factions", ("character_id",)),
             ("mud_npc_relationships", "npc_definition_id", "mud_npc_definitions", ("character_id",)))
    carried_rows = 0
    for table, column, target_table, keys in carry:
        for row in database.execute(f"SELECT rowid,{column},{','.join(keys)} FROM {table}").fetchall():
            target = carried(row[column], target_table)
            if not target or target == row[column]:
                continue
            clash = database.execute(f"SELECT 1 FROM {table} WHERE {column}=? AND "
                                     + " AND ".join(f"{key}=?" for key in keys),
                                     (target, *(row[key] for key in keys))).fetchone()
            if clash:
                continue
            database.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (target, row["rowid"]))
            carried_rows += 1
    database.execute("UPDATE mud_character_crimes SET faction_id=NULL WHERE world_id=? AND faction_id IN "
                     "(SELECT id FROM mud_world_factions WHERE world_id=?)", (world_id, retired_id))
    for row in database.execute("SELECT id,definition_id FROM mud_item_instances WHERE owner_character_id IS NOT NULL "
                                "AND definition_id IN (SELECT id FROM mud_item_definitions WHERE world_id=?)",
                                (retired_id,)).fetchall():
        target = carried(row["definition_id"], "mud_item_definitions")
        if target:
            database.execute("UPDATE mud_item_instances SET definition_id=? WHERE id=?", (target, row["id"]))
    if not reset_world_state:
        exits = {local: global_id for global_id, local in (
            (row["id"], row["local_id"]) for row in database.execute(
                "SELECT e.id,e.local_id FROM mud_world_exits e JOIN mud_world_rooms r ON r.id=e.from_room_id "
                "WHERE r.world_id=? AND e.local_id IS NOT NULL", (world_id,)))}
        for row in database.execute("SELECT rowid,trigger_id FROM mud_trigger_fires WHERE realm=?",
                                    (world_id,)).fetchall():
            match = GLOBAL_ID_RE.fullmatch(row["trigger_id"])
            local = match.group("local") if match else (old_local.get("triggers") or {}).get(row["trigger_id"])
            target = f"world:{world_id}:{installed['installation_key']}:trigger:{local}" if local else None
            if target and database.execute("SELECT 1 FROM mud_world_triggers WHERE id=?", (target,)).fetchone():
                database.execute("UPDATE OR IGNORE mud_trigger_fires SET trigger_id=? WHERE rowid=?",
                                 (target, row["rowid"]))
        for row in database.execute("SELECT rowid,entity_type,entity_id FROM mud_script_entity_states WHERE realm=?",
                                    (world_id,)).fetchall():
            if row["entity_type"] == "exit":
                target = exits.get(old_exits.get(row["entity_id"]))
            else:
                table = {"object": "mud_item_instances", "item": "mud_item_definitions",
                         "npc": "mud_npc_instances"}.get(row["entity_type"])
                target = carried(row["entity_id"], table)
            if target:
                database.execute("UPDATE OR IGNORE mud_script_entity_states SET entity_id=? WHERE rowid=?",
                                 (target, row["rowid"]))
    else:
        for table in ("mud_script_flags", "mud_trigger_fires", "mud_script_entity_states"):
            database.execute(f"DELETE FROM {table} WHERE realm=?", (world_id,))
    if before["enabled"]:
        try:
            set_enabled(database, world_id, True)
        except WorldError as problem:
            notes.append(f"The world was left disabled: {problem}")
    ended_holdings = [holding for holding in housing["holdings"]
                      if not database.execute("SELECT 1 FROM mud_property_holdings WHERE id=?",
                                              (holding["id"],)).fetchone()]
    ended_owners = sorted({owner["character_id"] for owner in housing["owners"]
                           if owner["holding_id"] in {holding["id"] for holding in ended_holdings}})
    return {"from_hash": before["package_hash"], "moved_to_start": moved, "carried": carried_rows,
            "notes": notes, "moved_users": moved_users, "present_users": present_users,
            "ended_holding_owners": ended_owners}


def _housing_snapshot(database, world_id):
    """Holdings, their owners, private rooms, and stored items, saved before an upgrade replaces the content."""
    holdings = [dict(row) for row in database.execute(
        "SELECT h.*,p.kind FROM mud_property_holdings h JOIN mud_properties p ON p.id=h.property_id "
        "WHERE h.world_id=? ORDER BY h.created_at,h.id", (world_id,))]
    ids = [row["id"] for row in holdings]
    marks = ",".join("?" * len(ids))
    return {
        "holdings": holdings,
        "owners": [dict(row) for row in database.execute(
            f"SELECT * FROM mud_property_owners WHERE holding_id IN ({marks})", ids)] if ids else [],
        "copies": [dict(row) for row in database.execute(
            "SELECT id,template_room_id,holding_id FROM mud_world_rooms WHERE world_id=? AND kind='housing'",
            (world_id,))],
        "storage": [dict(row) for row in database.execute(
            "SELECT s.* FROM mud_character_storage s JOIN mud_storage_points p ON p.id=s.storage_point_id "
            "JOIN mud_world_rooms r ON r.id=p.room_id WHERE r.world_id=?", (world_id,))],
    }


def _restore_housing(database, housing, carried, old_rooms, new_rooms):
    """Put holdings and stored items back on the upgraded content by local ID; returns the kept private rooms.

    A holding whose property is gone ends, and items stored where nothing
    matches any more stay with their owners as ordinary inventory.
    """
    kept = set()
    for holding in housing["holdings"]:
        target = carried(holding["property_id"], "mud_properties")
        found = database.execute("SELECT kind FROM mud_properties WHERE id=?", (target,)).fetchone() if target else None
        if found is None:
            continue
        if database.execute("INSERT OR IGNORE INTO mud_property_holdings(id,property_id,world_id,unique_key,created_at) "
                            "VALUES(?,?,?,?,?)", (holding["id"], target, holding["world_id"],
                                                  target if found["kind"] == "unique" else None,
                                                  holding["created_at"])).rowcount:
            kept.add(holding["id"])
    for owner in housing["owners"]:
        if owner["holding_id"] in kept:
            database.execute(f"INSERT INTO mud_property_owners({','.join(owner)}) VALUES({','.join('?' * len(owner))})",
                             tuple(owner.values()))
    copies = set()
    for room in housing["copies"]:
        template = new_rooms.get(old_rooms.get(room["template_room_id"]))
        instanced = database.execute("SELECT 1 FROM mud_property_holdings h JOIN mud_properties p ON p.id=h.property_id "
                                     "JOIN mud_property_rooms r ON r.property_id=p.id WHERE h.id=? AND p.kind='instanced' "
                                     "AND r.room_id=?", (room["holding_id"], template)).fetchone() if template else None
        if room["holding_id"] in kept and instanced:
            row = database.execute("SELECT name,description,world_id FROM mud_world_rooms WHERE id=?",
                                   (template,)).fetchone()
            database.execute("INSERT INTO mud_world_rooms(id,name,description,world_id,kind,template_room_id,holding_id) "
                             "VALUES(?,?,?,?,'housing',?,?)", (room["id"], row["name"], row["description"],
                                                               row["world_id"], template, room["holding_id"]))
            copies.add(room["id"])
    for row in housing["storage"]:
        point = carried(row["storage_point_id"], "mud_storage_points")
        scope = database.execute("SELECT scope FROM mud_storage_points WHERE id=?", (point,)).fetchone() if point else None
        if scope is None or (scope["scope"] == "property" and row["holding_id"] not in kept):
            continue
        holding_id = row["holding_id"] if scope["scope"] == "property" else None
        if scope["scope"] == "property" and holding_id is None:
            continue
        database.execute("INSERT OR IGNORE INTO mud_character_storage(item_instance_id,character_id,stored_at,"
                         "storage_point_id,holding_id) SELECT ?,?,?,?,? WHERE EXISTS "
                         "(SELECT 1 FROM mud_item_instances WHERE id=? AND owner_character_id=?)",
                         (row["item_instance_id"], row["character_id"], row["stored_at"], point, holding_id,
                          row["item_instance_id"], row["character_id"]))
    return copies


def _transport_snapshot(database, world_id):
    """Owned vehicles, and where riders and transit passengers should end up, saved before an upgrade."""
    return {
        "vehicles": [dict(row) for row in database.execute(
            "SELECT * FROM mud_vehicles WHERE world_id=? AND owner_character_id IS NOT NULL", (world_id,))],
        # Riders step off; they land where their vehicle stood.
        "riders": [dict(row) for row in database.execute(
            "SELECT c.user_id,v.room_id FROM mud_vehicle_riders r JOIN mud_vehicles v ON v.id=r.vehicle_id "
            "JOIN mud_characters c ON c.id=r.character_id WHERE v.world_id=?", (world_id,))],
        # Rides in progress finish at once at their destinations.
        "trips": [dict(row) for row in database.execute(
            "SELECT c.user_id,t.to_room_id FROM mud_transit_trips t JOIN mud_characters c ON c.id=t.character_id "
            "WHERE t.world_id=?", (world_id,))],
    }


def _restore_transport(database, transport, carried, old_rooms, new_rooms, start):
    """Put owned vehicles back on the upgraded content by local ID; returns user -> room for riders.

    A vehicle whose definition is gone is lost with it; one whose room is gone
    waits at the start room. Public vehicles come back from the new placements.
    """
    def room(old_id):
        if old_id is None:
            return None
        if database.execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (old_id,)).fetchone():
            return old_id
        return new_rooms.get(old_rooms.get(old_id))

    for vehicle in transport["vehicles"]:
        definition = carried(vehicle["definition_id"], "mud_vehicle_definitions")
        capacity = database.execute("SELECT fuel_capacity FROM mud_vehicle_definitions WHERE id=?",
                                    (definition,)).fetchone() if definition else None
        if capacity is None:
            continue
        home = room(vehicle["home_room_id"]) or start
        database.execute("INSERT OR IGNORE INTO mud_vehicles(id,world_id,definition_id,owner_character_id,room_id,"
                         "home_room_id,fuel,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (vehicle["id"], vehicle["world_id"], definition, vehicle["owner_character_id"],
                          room(vehicle["room_id"]) or home, home, min(vehicle["fuel"], capacity[0]),
                          vehicle["created_at"]))
    landing = {}
    for row in transport["riders"]:
        landing[row["user_id"]] = room(row["room_id"]) or start
    for row in transport["trips"]:
        landing[row["user_id"]] = room(row["to_room_id"]) or start
    return landing


def _carried_availability(database, world_id, offered, old_links):
    """Keep a sysop's selection across an upgrade, minus definitions that are gone, plus new bundles."""
    from . import character_creation, mud_skills
    installed = {kind: {entry["key"] for entry in character_creation.catalog(database, world="")[kind]}
                 for kind in character_creation.KINDS}
    installed["skills"] = {entry["key"] for entry in mud_skills.catalog(database, world="")}
    new_links = world_origin_keys(database, world_id)
    chosen = {kind: (set(offered[kind]) & installed[kind]) | (new_links[kind] - old_links[kind])
              for kind in installed}
    defaults = character_creation.default_availability()
    for kind in character_creation.KINDS:
        if not chosen[kind]:
            chosen[kind] = set(new_links[kind]) or set(defaults[kind])
    return chosen


def _install_triggers(database, document, installation_key, maps, condition_mapper):
    world_id = document["id"]
    exits = {item["id"]: f"world:{world_id}:{installation_key}:exit:{ordinal}"
             for ordinal, item in enumerate(document["exits"]) if item.get("id")}
    triggers = {trigger["id"]: f"world:{world_id}:{installation_key}:trigger:{trigger['id']}"
                for trigger in document.get("events", [])}

    def mapper(kind, value):
        if kind == "exit":
            return exits[value]
        if kind == "trigger":
            return triggers[value]
        if kind in TRIGGER_TABLES and kind not in CONDITION_TABLES:
            return _mapped(maps, TRIGGER_TABLES[kind], value, f"trigger {kind}")
        return condition_mapper(kind, value)

    for trigger in document.get("events", []):
        save_trigger(database, world_id, triggers[trigger["id"]], mud_triggers.remap(trigger, mapper))


def save_trigger(database, world_id, trigger_id, trigger):
    """Store a normalized trigger whose references already use installed IDs."""
    database.execute(
        "INSERT INTO mud_world_triggers(id,world_id,local_id,name,event,source_id,definition_json,enabled) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET local_id=excluded.local_id,name=excluded.name,"
        "event=excluded.event,source_id=excluded.source_id,definition_json=excluded.definition_json,"
        "enabled=excluded.enabled,updated_at=CURRENT_TIMESTAMP",
        (trigger_id, world_id, trigger["id"], trigger["name"], trigger["event"],
         next(iter(trigger["source"].values()), None),
         json.dumps(trigger, ensure_ascii=False, separators=(",", ":"), sort_keys=True), int(trigger["enabled"])))


def world_triggers(database, world_id):
    """A world's stored triggers as normalized dictionaries; unreadable rows are skipped."""
    result = []
    for row in database.execute("SELECT * FROM mud_world_triggers WHERE world_id=? ORDER BY name COLLATE NOCASE,id",
                                (world_id,)):
        try:
            trigger = mud_triggers.validate_trigger(json.loads(row["definition_json"]))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        result.append({**trigger, "global_id": row["id"]})
    return result


def _quest_rules(kind, value):
    return mud_quests.validate_details(value) if kind == "details" else mud_quests.validate_fail_rules(value)


def _remap_quest_rules(kind, rules, mapper):
    return (mud_quests.remap_details(rules, mapper) if kind == "details"
            else mud_quests.remap_fail_rules(rules, mapper))


def _condition_validator(column):
    return (mud_conditions.validate_outcomes if column == "outcomes_json"
            else mud_conditions.validate_start_conditions if column == "start_conditions_json"
            else mud_conditions.validate_start_rule_conditions if column == "spawn_conditions_json"
            else mud_conditions.validate_conditions)


def _stored_conditions(value, column):
    """Stored condition/outcome JSON as a normalized list; unreadable values export as []."""
    try:
        return _condition_validator(column)(value)
    except ValueError:
        return []


def local_ids(global_ids, world_id, table, prefix, taken=None):
    """Package-local IDs for installed rows: the ID they were imported with, else prefix-N.

    Keeping imported IDs makes repeated exports match and lets an upgrade tell
    which rows are the same thing.
    """
    taken = set() if taken is None else taken
    pattern = re.compile(rf"^world:{re.escape(world_id)}:[0-9a-f]{{32}}:{re.escape(table)}:([a-z][a-z0-9-]{{0,63}})$")
    result = {}
    for global_id in global_ids:
        match = pattern.fullmatch(global_id) if isinstance(global_id, str) else None
        if match and match.group(1) not in taken:
            result[global_id] = match.group(1)
            taken.add(match.group(1))
    index = 0
    for global_id in global_ids:
        if global_id in result:
            continue
        index += 1
        while f"{prefix}-{index}" in taken:
            index += 1
        result[global_id] = f"{prefix}-{index}"
        taken.add(result[global_id])
    return result


def _export_content(database, world_id, room_map, exit_map=None, report=None):
    report = PackageReport() if report is None else report
    roots = {"mud_item_definitions", "mud_conversations", "mud_npc_definitions", "mud_quests", "mud_world_factions",
             "mud_professions", "mud_properties", "mud_vehicle_definitions", "mud_transit_lines"}
    all_placed_items = [dict(row) for row in database.execute(
        "SELECT id,definition_id,room_id,container_id FROM mud_item_instances "
        "WHERE spawn_source_id IS NULL AND id NOT LIKE 'world:wild-item:%' "
        # Only placements a builder or package made; items players dropped are not world content.
        "AND id LIKE 'world:%' AND id NOT LIKE 'world:spawn:%' AND id NOT LIKE 'world:respawn:%'"
    )]
    placed_item_ids = {row["id"] for row in all_placed_items if row["room_id"] in room_map}
    while True:
        expanded = placed_item_ids | {row["id"] for row in all_placed_items
                                      if row["container_id"] in placed_item_ids}
        if expanded == placed_item_ids:
            break
        placed_item_ids = expanded
    needed_items = {row["definition_id"] for row in all_placed_items if row["id"] in placed_item_ids}
    needed_items |= {row["definition_id"] for row in database.execute(
        "SELECT definition_id FROM mud_item_spawns WHERE room_id IN (SELECT id FROM mud_world_rooms WHERE world_id=?)", (world_id,))}
    for row in database.execute("SELECT rewards_json,tool_definition_id FROM mud_gathering_nodes WHERE room_id IN "
                                "(SELECT id FROM mud_world_rooms WHERE world_id=?)", (world_id,)):
        needed_items |= {reward["item_id"] for reward in mud_gathering.rewards(row["rewards_json"])}
        if row["tool_definition_id"]:
            needed_items.add(row["tool_definition_id"])
    needed_items |= {row["item_definition_id"] for row in database.execute(
        "SELECT item_definition_id FROM mud_room_discoveries WHERE room_id IN (SELECT id FROM mud_world_rooms WHERE world_id=?) AND item_definition_id IS NOT NULL", (world_id,))}
    needed_items |= {row["key_item_id"] for row in database.execute(
        "SELECT e.key_item_id FROM mud_world_exits e JOIN mud_world_rooms r ON r.id=e.from_room_id WHERE r.world_id=? AND e.key_item_id IS NOT NULL", (world_id,))}
    needed_items |= {row["item_definition_id"] for row in database.execute(
        "SELECT s.item_definition_id FROM mud_shop_stock s JOIN mud_npc_definitions n ON n.id=s.npc_definition_id WHERE n.world_id=?", (world_id,))}
    needed_items |= {row["fuel_item_id"] for row in database.execute(
        "SELECT fuel_item_id FROM mud_vehicle_definitions WHERE world_id=? AND fuel_item_id IS NOT NULL", (world_id,))}
    for row in database.execute("SELECT map_items_json FROM mud_world_destinations WHERE world_id=?", (world_id,)):
        needed_items |= set(mud_wayfinding.map_items(row))
    for row in database.execute("SELECT a.tool_definition_id,a.objective_type,a.objective_target_id "
                                "FROM mud_work_activities a JOIN mud_professions p ON p.id=a.profession_id "
                                "WHERE p.world_id=?", (world_id,)):
        if row["tool_definition_id"]:
            needed_items.add(row["tool_definition_id"])
        if row["objective_type"] in {"possess_item", "deliver_item"}:
            needed_items.add(row["objective_target_id"])
    needed_items |= {row["target_id"] for row in database.execute(
        "SELECT r.target_id FROM mud_work_rewards r JOIN mud_work_activities a ON a.id=r.activity_id "
        "JOIN mud_professions p ON p.id=a.profession_id WHERE p.world_id=? AND r.target_id IS NOT NULL",
        (world_id,))}
    needed_npcs = {row["definition_id"] for row in database.execute(
        "SELECT definition_id FROM mud_npc_instances WHERE spawned_by_trigger IS NULL AND room_id IN "
        "(SELECT id FROM mud_world_rooms WHERE world_id=?)", (world_id,)
    )}
    needed_npcs |= {row["recruiter_npc_definition_id"] for row in database.execute(
        "SELECT recruiter_npc_definition_id FROM mud_professions WHERE world_id=?", (world_id,))}
    needed_npcs |= {row["agent_npc_definition_id"] for row in database.execute(
        "SELECT agent_npc_definition_id FROM mud_properties WHERE world_id=? AND agent_npc_definition_id IS NOT NULL",
        (world_id,))}
    needed_npcs |= {row["recruiter_npc_definition_id"] for row in database.execute(
        "SELECT recruiter_npc_definition_id FROM mud_world_factions WHERE world_id=? AND "
        "recruiter_npc_definition_id IN (SELECT id FROM mud_npc_definitions)", (world_id,))}
    for row in database.execute("SELECT a.provider_npc_definition_id,a.objective_type,a.objective_target_id "
                                "FROM mud_work_activities a JOIN mud_professions p ON p.id=a.profession_id "
                                "WHERE p.world_id=?", (world_id,)):
        if row["provider_npc_definition_id"]:
            needed_npcs.add(row["provider_npc_definition_id"])
        if row["objective_type"] in {"talk_npc", "defeat_npc"}:
            needed_npcs.add(row["objective_target_id"])
    for trigger in world_triggers(database, world_id):
        for kind, ref in mud_triggers.references(trigger):
            if kind == "item":
                needed_items.add(ref)
            elif kind == "npc":
                needed_npcs.add(ref)
    template_items, template_npcs = mud_world_dungeons.shared_references(database, world_id)
    needed_items |= template_items
    needed_npcs |= template_npcs
    preset_npcs, preset_items = mud_wilderness.shared_references(database, world_id)
    needed_items |= preset_items
    needed_npcs |= preset_npcs
    needed_conversations = {row["conversation_id"] for row in database.execute(
        "SELECT conversation_id FROM mud_npc_definitions WHERE id IN "
        "(SELECT definition_id FROM mud_npc_instances WHERE room_id IN "
        "(SELECT id FROM mud_world_rooms WHERE world_id=?))", (world_id,)
    ) if row["conversation_id"]}
    needed_conversations |= {row["conversation_id"] for row in database.execute(
        f"SELECT conversation_id FROM mud_npc_definitions WHERE id IN ({','.join('?' for _ in template_npcs)})",
        tuple(template_npcs),
    ) if row["conversation_id"]} if template_npcs else set()
    needed_conversations |= {row["conversation_id"] for row in database.execute(
        "SELECT id,conversation_id FROM mud_npc_definitions WHERE world_id=?", (world_id,))
        if row["id"] in needed_npcs and row["conversation_id"]}
    for row in database.execute(
        "SELECT r.conditions_json,r.outcomes_json FROM mud_dialogue_responses r "
        "JOIN mud_dialogue_nodes n ON n.id=r.node_id JOIN mud_conversations c ON c.id=n.conversation_id "
        "WHERE c.world_id=? UNION ALL SELECT conditions_json,'[]' FROM mud_quests WHERE world_id=? "
        "UNION ALL SELECT e.conditions_json,'[]' FROM mud_world_exits e "
        "JOIN mud_world_rooms r ON r.id=e.from_room_id WHERE r.world_id=? "
        "UNION ALL SELECT conditions_json,'[]' FROM mud_professions WHERE world_id=? "
        "UNION ALL SELECT a.conditions_json,'[]' FROM mud_work_activities a "
        "JOIN mud_professions p ON p.id=a.profession_id WHERE p.world_id=? "
        "UNION ALL SELECT d.conditions_json,'[]' FROM mud_room_discoveries d "
        "JOIN mud_world_rooms r ON r.id=d.room_id WHERE r.world_id=? "
        "UNION ALL SELECT conditions_json,'[]' FROM mud_properties WHERE world_id=? "
        "UNION ALL SELECT s.conditions_json,'[]' FROM mud_storage_points s "
        "JOIN mud_world_rooms r ON r.id=s.room_id WHERE r.world_id=? "
        "UNION ALL SELECT conditions_json,'[]' FROM mud_vehicle_definitions WHERE world_id=? "
        "UNION ALL SELECT conditions_json,'[]' FROM mud_transit_lines WHERE world_id=?",
        (world_id, world_id, world_id, world_id, world_id, world_id, world_id, world_id, world_id, world_id),
    ):
        named = list(mud_conditions.references(_stored_conditions(row["conditions_json"], "conditions_json"),
                                               _stored_conditions(row["outcomes_json"], "outcomes_json")))
        needed_items |= {ref for kind, ref in named if kind == "item"}
        needed_npcs |= {ref for kind, ref in named if kind == "npc"}
    selected = {}
    for table, columns in CONTENT_TABLES.items():
        rows = [dict(row) for row in database.execute(
            f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid"
        )]
        if table in roots:
            rows = [row for row in rows if database.execute(
                f"SELECT world_id FROM {table} WHERE id=?", (row["id"],)
            ).fetchone()[0] == world_id and not row["id"].startswith(("stationnet:", "origin:")) and
                database.execute("SELECT 1 FROM mud_dungeon_managed_definitions WHERE definition_id=?",
                                 (row["id"],)).fetchone() is None or
                table == "mud_item_definitions" and row["id"] in needed_items or
                table == "mud_npc_definitions" and row["id"] in needed_npcs or
                table == "mud_conversations" and row["id"] in needed_conversations]
        elif table == "mud_dialogue_nodes":
            rows = [row for row in rows if row["conversation_id"] in selected["mud_conversations"]]
        elif table == "mud_faction_relations":
            rows = [row for row in rows if row["faction_id"] in selected["mud_world_factions"]
                    and row["other_faction_id"] in selected["mud_world_factions"]]
        elif table in {"mud_npc_combat_profiles", "mud_npc_equipment", "mud_npc_combat_rewards", "mud_npc_shops", "mud_npc_banks",
                       "mud_shop_stock", "mud_npc_trainers"}:
            rows = [row for row in rows if row["npc_definition_id"] in selected["mud_npc_definitions"]]
        elif table in {"mud_quest_objectives", "mud_quest_rewards", "mud_quest_givers"}:
            rows = [row for row in rows if row["quest_id"] in selected["mud_quests"]]
        elif table == "mud_work_activities":
            rows = [row for row in rows if row["profession_id"] in selected["mud_professions"]]
        elif table == "mud_work_rewards":
            rows = [row for row in rows if row["activity_id"] in selected["mud_work_activities"]]
        elif table == "mud_dialogue_responses":
            rows = [row for row in rows if row["node_id"] in selected["mud_dialogue_nodes"]]
        elif table == "mud_item_instances":
            rows = [row for row in rows if row["id"] in placed_item_ids]
        elif table in {"mud_item_spawns", "mud_gathering_nodes", "mud_room_discoveries", "mud_world_destinations",
                       "mud_storage_points"}:
            rows = [row for row in rows if row["room_id"] in room_map]
        elif table == "mud_property_rooms":
            rows = [row for row in rows if row["room_id"] in room_map and row["property_id"] in selected["mud_properties"]]
        elif table in {"mud_vehicle_placements", "mud_fuel_stations"}:
            rows = [row for row in rows if row["room_id"] in room_map and (
                table == "mud_fuel_stations" or row["vehicle_definition_id"] in selected["mud_vehicle_definitions"])]
        elif table == "mud_transit_stops":
            rows = [row for row in rows if row["room_id"] in room_map and row["line_id"] in selected["mud_transit_lines"]]
        elif table == "mud_world_start_rules":
            rows = [row for row in rows if row["room_id"] in room_map]
            readable = [row for row in rows if _stored_conditions(row["spawn_conditions_json"],
                                                                  "spawn_conditions_json")]
            if len(readable) != len(rows):
                report.warning(CONTENT_FILE, f"$.{table}", "A start rule with unreadable conditions was left out.")
            rows = readable
        elif table == "mud_npc_instances":
            spawned = {row["id"] for row in database.execute(
                "SELECT id FROM mud_npc_instances WHERE spawned_by_trigger IS NOT NULL")}
            rows = [row for row in rows if row["room_id"] in room_map and row["id"] not in spawned]
        selected[table] = {row["id"]: row for row in rows} if table in ID_TABLES else rows

    prefixes = {"mud_item_definitions": "item", "mud_conversations": "conversation",
                "mud_dialogue_nodes": "node", "mud_quests": "quest",
                "mud_npc_definitions": "npc", "mud_npc_combat_rewards": "combat-reward",
                "mud_quest_objectives": "objective", "mud_quest_rewards": "quest-reward",
                "mud_dialogue_responses": "response", "mud_item_instances": "placed-item",
                "mud_npc_instances": "placed-npc", "mud_shop_stock": "stock", "mud_npc_trainers": "trainer",
                "mud_item_spawns": "spawn", "mud_room_discoveries": "discovery",
                "mud_gathering_nodes": "gathering-node",
                "mud_world_destinations": "destination", "mud_world_factions": "faction",
                "mud_faction_relations": "faction-relation", "mud_world_start_rules": "start-rule"}
    prefixes.update({"mud_professions": "profession", "mud_work_activities": "work",
                     "mud_work_rewards": "work-reward", "mud_properties": "property", "mud_storage_points": "storage",
                     "mud_vehicle_definitions": "vehicle", "mud_vehicle_placements": "vehicle-placement",
                     "mud_fuel_stations": "fuel-station", "mud_transit_lines": "line", "mud_transit_stops": "stop"})
    maps = {"rooms": room_map, "exits": exit_map or {}}
    for table in ID_TABLES:
        maps[table] = local_ids(list(selected[table]), world_id, table, prefixes[table])
    content = {}
    for table, columns in CONTENT_TABLES.items():
        source_rows = selected[table].values() if table in ID_TABLES else selected[table]
        output = []
        for row in source_rows:
            result = {}
            for column in columns:
                value = row[column]
                if column == "id":
                    value = maps[table][value]
                elif table == "mud_room_discoveries" and column == "item_instance_id" and value not in maps["mud_item_instances"]:
                    if value:
                        report.warning(CONTENT_FILE, f"$.{table}", f"A discovery reveals an object outside this "
                                       "world's rooms; the object link was left out.")
                    value = None
                elif column in {"interior_room_id", "cabin_room_id"} and value and value not in room_map:
                    report.warning(CONTENT_FILE, f"$.{table}", f"{row['name']} uses a room outside this world "
                                   "as its interior; the interior was left out.")
                    value = None
                elif (table == "mud_world_factions" and column == "recruiter_npc_definition_id"
                      and value and value not in maps["mud_npc_definitions"]):
                    report.warning(CONTENT_FILE, f"$.{table}", f"Faction {row['name']} names a recruiter that "
                                   "no longer exists; the recruiter was left out.")
                    value = None
                elif column in REFERENCE_TABLE:
                    ref_table = REFERENCE_TABLE[column]
                    if ref_table is None:
                        ref_table = ("mud_item_definitions" if table in {"mud_item_instances", "mud_item_spawns"}
                                     else "mud_npc_definitions")
                    value = _mapped(maps, ref_table, value, column)
                elif column == "target_id":
                    if table == "mud_quest_objectives" and row["objective_type"] == "script":
                        result[column] = value
                        continue
                    ref_table = (OBJECTIVE_TARGETS.get(row["objective_type"]) if table == "mud_quest_objectives"
                                 else "mud_world_factions" if row.get("reward_type") == "reputation"
                                 else "mud_item_definitions")
                    value = _mapped(maps, ref_table, value, column)
                elif table == "mud_work_activities" and column == "objective_target_id":
                    if value:
                        goal = row["objective_type"]
                        ref_table = ("rooms" if goal == "visit_room" else
                                     "mud_npc_definitions" if goal in {"talk_npc", "defeat_npc"} else
                                     "mud_item_definitions")
                        value = _mapped(maps, ref_table, value, column)
                elif table == "mud_gathering_nodes" and column == "rewards_json":
                    value = [{"item_id": _mapped(maps, "mud_item_definitions", reward["item_id"], column),
                              "amount": reward["amount"]} for reward in mud_gathering.rewards(value)]
                elif column in CONDITION_COLUMNS.get(table, ()):
                    # Origins and skills are renamed in export() once their local keys are known.
                    value = mud_conditions.remap(
                        _stored_conditions(value, column),
                        lambda kind, ref: ref if kind not in CONDITION_TABLES
                        else _mapped(maps, CONDITION_TABLES[kind], ref, f"{kind} condition"))
                elif column in QUEST_RULE_COLUMNS.get(table, {}):
                    kind = QUEST_RULE_COLUMNS[table][column]
                    try:
                        stored = _quest_rules(kind, value)
                    except ValueError:
                        stored = []
                    value = _remap_quest_rules(
                        kind, stored, lambda ref_kind, ref: ref if ref_kind not in CONDITION_TABLES
                        else _mapped(maps, CONDITION_TABLES[ref_kind], ref, f"{ref_kind} condition"))
                elif table == "mud_world_destinations" and column == "map_items_json":
                    dropped = [ref for ref in mud_wayfinding.map_items(row) if ref not in maps["mud_item_definitions"]]
                    if dropped:
                        report.warning(CONTENT_FILE, f"$.{table}", f"Destination {row['name']} names map items "
                                       f"outside this world ({', '.join(dropped)}); they were left out.")
                    value = [maps["mud_item_definitions"][ref] for ref in mud_wayfinding.map_items(row)
                             if ref in maps["mud_item_definitions"]]
                elif column.endswith("_json") and isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        pass
                    if table == "mud_item_instances" and column == "state_json" and isinstance(value, dict):
                        for key, owner_table in OWNER_TABLES.items():
                            if key in value:
                                local = maps.get(owner_table, {}).get(value[key])
                                if local is None:
                                    report.warning(CONTENT_FILE, f"$.{table}", f"A placed item's {key} is "
                                                   "outside this world; ownership was left out.")
                                    del value[key]
                                else:
                                    value[key] = local
                    if table == "mud_npc_definitions" and column == "behaviors_json" and isinstance(value, dict):
                        # References outside the exported world are dropped rather than left dangling.
                        def behavior_reference(kind, ref, name=row["name"]):
                            local = (room_map.get(ref) if kind == "room"
                                     else maps.get(BEHAVIOR_TABLES[kind], {}).get(ref))
                            if local is None:
                                report.warning(CONTENT_FILE, "$.mud_npc_definitions", f"NPC {name} behaviors "
                                               f"name a {kind} outside this world ({ref}); it was left out.")
                            return local
                        value = mud_society.remap_behaviors(value, behavior_reference)
                    if (table == "mud_item_definitions" and column == "properties_json" and isinstance(value, dict)
                            and "teaches_destinations" in value):
                        # Destinations outside the exported rooms are dropped rather than left dangling.
                        local = maps["mud_world_destinations"]
                        if any(ref not in local for ref in mud_wayfinding.teaches(value)):
                            report.warning(CONTENT_FILE, "$.mud_item_definitions", f"Item {row['name']} teaches "
                                           "destinations outside this world; they were left out.")
                        value["teaches_destinations"] = [local[ref] for ref in mud_wayfinding.teaches(value)
                                                         if ref in local]
                        if not value["teaches_destinations"]:
                            del value["teaches_destinations"]
                result[column] = value
            output.append(result)
        if output:
            content[table] = output
    return content, maps


def _skill_closure(skill_entries, origin_definitions, chosen):
    """Imported skills the exported origins and skills need, plus the chosen ones."""
    chosen = set(chosen)
    pending = list(origin_definitions) + [skill_entries[key] for key in chosen if key in skill_entries]
    while pending:
        definition = pending.pop()
        references = set(definition.get("skills", {}))
        references |= set(definition.get("requirements", {}).get("skills", {}))
        if definition.get("skill"):
            references.add(definition["skill"])
        for key in references:
            if key in skill_entries and key not in chosen:
                chosen.add(key)
                pending.append(skill_entries[key])
    return chosen


def export(database, world_id, *, origin_keys=None, skill_keys=None, strict=False):
    document, report = export_document(database, world_id, origin_keys=origin_keys, skill_keys=skill_keys)
    if strict and report.warnings:
        raise WorldError("Export left out references: " + " ".join(
            problem["message"] for problem in report.warnings[:5]), report)
    return package_bytes(document)


def export_document(database, world_id, *, origin_keys=None, skill_keys=None, keep_id=False, maps_out=None):
    """A self-contained package document for a world, and the problems found building it.

    ``keep_id`` keeps the home world's own ID (World Forge drafts); public exports
    rename it. ``maps_out`` receives installed ID -> package-local ID for every
    section, so an upgrade can carry progress for rows that were never imported.
    """
    report = PackageReport()
    world = database.execute("SELECT * FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
    if world is None:
        raise WorldError("World was not found.")
    rooms = database.execute("SELECT id,name,description,x,y,z,grid_link,blocked_dirs,banking_enabled FROM mud_world_rooms "
                             "WHERE world_id=? AND kind='authored' ORDER BY id", (world_id,)).fetchall()
    if not rooms:
        raise WorldError("World has no rooms to export.")
    # Wilderness rooms travel as run-length layers in wilderness.json and are named '@x,y,z'.
    ids = mud_wilderness.export_references(world_id, local_ids([room["id"] for room in rooms], world_id, "room", "room"))
    if world["start_room_id"] not in ids:
        raise WorldError("World starting room is missing.")
    exits = database.execute(
        "SELECT e.id,e.from_room_id,e.to_room_id,e.name,e.direction,e.key_item_id,e.pick_difficulty,"
        "e.conditions_json,e.conditions_message,e.local_id,e.hidden,e.requires_mode,e.allow_vehicles "
        "FROM mud_world_exits e "
        "JOIN mud_world_rooms r ON r.id=e.from_room_id WHERE r.world_id=? ORDER BY e.id", (world_id,)
    ).fetchall()
    triggers = world_triggers(database, world_id)
    referenced_exits = {ref for trigger in triggers for kind, ref in mud_triggers.references(trigger) if kind == "exit"}
    referenced_exits.update(row["exit_id"] for row in database.execute(
        "SELECT d.exit_id FROM mud_room_discoveries d JOIN mud_world_rooms r ON r.id=d.room_id "
        "WHERE r.world_id=? AND d.exit_id IS NOT NULL", (world_id,)))
    exit_ids, taken = {}, set()
    for index, row in enumerate(exits, 1):
        if row["to_room_id"] in ids and (row["local_id"] or row["id"] in referenced_exits):
            local = row["local_id"] if row["local_id"] and row["local_id"] not in taken else f"exit-{index}"
            exit_ids[row["id"]] = local
            taken.add(local)
    left_out = [row for row in exits if row["to_room_id"] not in ids]
    for row in left_out:
        report.warning("exits.json", "", f"Exit {row['name']} leads outside this world; it was left out.")
    content, maps = _export_content(database, world_id, ids, exit_ids, report)
    document = {"format_version": PACKAGE_FORMAT,
                "id": world_id if world_id != mud_scope.HOME or keep_id else "my-world",
                "name": world["name"], "author": world["author"],
                "start_room": ids[world["start_room_id"]],
                "resource": {"enabled": bool(world["resource_enabled"]), "name": world["resource_name"]},
                "society": mud_society.stored_settings(world["society_json"]),
                "rooms": [{"id": ids[room["id"]], "name": room["name"], "description": room["description"],
                           **({"bank": True} if room["banking_enabled"] else {}),
                           **({"x": room["x"], "y": room["y"], "z": room["z"]} if room["x"] is not None else {}),
                           **({"grid_link": False} if room["x"] is not None and not room["grid_link"] else {}),
                           **({"blocked_dirs": mud_wilderness.export_blocked(room["blocked_dirs"])}
                              if room["x"] is not None and mud_wilderness.export_blocked(room["blocked_dirs"]) else {})}
                          for room in rooms],
                "exits": [{**({"id": exit_ids[row["id"]]} if row["id"] in exit_ids else {}),
                           "from": ids[row["from_room_id"]], "to": ids[row["to_room_id"]],
                           "name": row["name"], "direction": row["direction"],
                           **({"hidden": True} if row["hidden"] else {}),
                           **({"key_item_id": _mapped(maps, "mud_item_definitions", row["key_item_id"], "key_item_id")}
                              if row["key_item_id"] else {}),
                           **({"pick_difficulty": row["pick_difficulty"]} if row["pick_difficulty"] else {}),
                           **({"conditions": mud_conditions.validate_conditions(row["conditions_json"])}
                              if row["conditions_json"] not in (None, "", "[]") else {}),
                           **({"conditions_message": row["conditions_message"]} if row["conditions_message"] else {}),
                           **({"requires_mode": row["requires_mode"]} if row["requires_mode"] else {}),
                           **({"allow_vehicles": False} if not row["allow_vehicles"] else {})}
                          for row in exits if row["to_room_id"] in ids],
                "content": content,
                "layers": mud_wilderness.export_layer_names(database, world_id),
                "progression": mud_progression.stored(world["progression_json"])}
    from . import character_creation
    entries = character_creation.catalog(database, world="")
    installed = {(kind, entry["key"]): entry for kind in character_creation.KINDS
                 for entry in entries[kind] if entry["imported"]}
    associations = {(row["kind"], row["origin_key"]): row["local_key"] for row in database.execute(
        "SELECT kind,origin_key,local_key FROM mud_world_origins WHERE world_id=?", (world_id,))}
    # By default a world travels with its own definitions and the custom ones it offers players.
    offered = character_creation.world_availability(database, world_id)
    default_origins = set(associations) | {pair for pair in installed if pair[1] in offered[pair[0]]}
    chosen = default_origins if origin_keys is None else set(origin_keys)
    if chosen - set(installed):
        raise WorldError("Choose only installed custom races and classes to export.")
    from . import mud_skills
    skill_entries = {entry["key"]: entry["definition"] for entry in mud_skills.catalog(database, world="")
                     if entry["imported"]}
    skill_links = {row["skill_key"]: row["local_key"] for row in database.execute(
        "SELECT skill_key,local_key FROM mud_world_skills WHERE world_id=?", (world_id,))}
    default_skills = (set(skill_links) | (set(skill_entries) & offered["skills"])) & set(skill_entries)
    wanted = default_skills if skill_keys is None else set(skill_keys)
    if wanted - set(skill_entries):
        raise WorldError("Choose only installed custom skills to export.")
    content_skills = {row["skill_key"] for table in ("mud_npc_trainers", "mud_gathering_nodes")
                      for row in content.get(table, []) if row.get("skill_key")}
    content_skills |= {row["practice_skill_key"] for row in content.get("mud_work_activities", [])
                       if row.get("practice_skill_key")}
    for table, columns in CONDITION_COLUMNS.items():
        for row in content.get(table, []):
            for column in columns:
                content_skills |= {ref for kind, ref in mud_conditions.references(row.get(column, []))
                                   if kind == "skill"}
    wanted |= content_skills & set(skill_entries)
    wanted = _skill_closure(skill_entries, [installed[pair]["definition"] for pair in chosen], wanted)
    local_skill = {key: skill_links.get(key, key) for key in wanted}
    if len(set(local_skill.values())) != len(local_skill):
        raise WorldError("Two exported skills share a local key; rename one before exporting.")
    origin_local = {(kind, key): associations.get((kind, key), key) for kind, key in chosen}
    effect_local, ability_local = _magic_closure(database, world_id, content, triggers,
                                                 [installed[pair]["definition"] for pair in chosen], document,
                                                 report)
    origins = {kind: [] for kind in character_creation.KINDS}
    for kind, key in sorted(chosen):
        definition = mud_skills.remap(dict(installed[(kind, key)]["definition"]), local_skill)
        definition["key"] = origin_local[(kind, key)]
        if definition.get("spells"):
            definition["spells"] = [ability_local.get(spell, spell) for spell in definition["spells"]]
        origins[kind].append(definition)
    if any(origins.values()):
        document["origins"] = origins
    skills = []
    for key in sorted(wanted, key=lambda item: local_skill[item]):
        definition = mud_skills.remap_origins(mud_skills.remap(dict(skill_entries[key]), local_skill), origin_local)
        definition["key"] = local_skill[key]
        skills.append(definition)
    if skills:
        document["skills"] = skills
    for table in ("mud_npc_trainers", "mud_gathering_nodes"):
        for row in content.get(table, []):
            if row.get("skill_key"):
                row["skill_key"] = local_skill.get(row["skill_key"], row["skill_key"])
    for row in content.get("mud_work_activities", []):
        if row.get("practice_skill_key"):
            row["practice_skill_key"] = local_skill.get(row["practice_skill_key"], row["practice_skill_key"])

    def local_reference(kind, ref):
        if kind in ORIGIN_KINDS:
            return origin_local.get((ORIGIN_KINDS[kind], ref), ref)
        if kind == "effect":
            return effect_local.get(ref, ref)
        return local_skill.get(ref, ref) if kind == "skill" else ref

    for table, columns in CONDITION_COLUMNS.items():
        for row in content.get(table, []):
            for column in columns:
                row[column] = mud_conditions.remap(row[column], local_reference)
    for table, columns in QUEST_RULE_COLUMNS.items():
        for row in content.get(table, []):
            for column, kind in columns.items():
                if column in row:
                    row[column] = _remap_quest_rules(kind, row[column], local_reference)
    for table, defaults in QUEST_FIELD_DEFAULTS.items():
        for row in content.get(table, []):
            for column, default in defaults.items():
                if row.get(column) in (default, None):
                    row.pop(column, None)
    # Leave out untouched joining rules so plain worlds stay readable by older stations.
    for row in content.get("mud_world_factions", []):
        for column, default in FACTION_MEMBERSHIP_DEFAULTS.items():
            if row.get(column) in (default, None):
                row.pop(column, None)
    for table, defaults in (("mud_properties", mud_housing.PROPERTY_DEFAULTS),
                            ("mud_storage_points", mud_housing.STORAGE_DEFAULTS),
                            ("mud_vehicle_definitions", mud_transport.VEHICLE_DEFAULTS),
                            ("mud_fuel_stations", mud_transport.STATION_DEFAULTS),
                            ("mud_transit_lines", mud_transport.LINE_DEFAULTS),
                            ("mud_transit_stops", mud_transport.STOP_DEFAULTS)):
        for row in content.get(table, []):
            for column, default in defaults.items():
                if row.get(column) in (default, None):
                    row.pop(column, None)
    for row in content.get("mud_item_definitions", []):
        if isinstance(row.get("properties_json"), dict) and row["properties_json"].get("requirements"):
            row["properties_json"] = json.loads(_remap_item_properties(row["properties_json"], local_reference))
    for table in ("mud_item_definitions", "mud_npc_definitions"):
        for row in content.get(table, []):
            row["properties_json"] = _remap_effect_lists(row.get("properties_json"), local_reference)
    for item in document["exits"]:
        if "conditions" in item:
            item["conditions"] = mud_conditions.remap(item["conditions"], _export_exit_reference(maps, local_reference))
    world_dungeons = mud_world_dungeons.export_document(
        database, world_id, ids, maps, _export_exit_reference(maps, local_reference), effect_local)
    if world_dungeons:
        document["dungeons"] = world_dungeons
    events = _export_triggers(triggers, maps, local_reference)
    if events:
        document["events"] = events
    if maps_out is not None:
        maps_out.update(maps)
        maps_out["rooms"] = dict(ids)
        maps_out["triggers"] = {trigger["global_id"]: trigger["id"] for trigger in triggers}

    def shared(table):
        return lambda value: maps.get(table, {}).get(value, value)

    wilderness = mud_wilderness.export_document(database, world_id, shared("mud_npc_definitions"),
                                                shared("mud_item_definitions"))
    if wilderness:
        document["wilderness"] = wilderness
    availability = character_creation.world_availability(database, world_id)
    if character_creation.availability_configured(database, world_id):
        # Selections travel by package-local key; station-specific definitions stay behind.
        portable = {kind: sorted(origin_local.get((kind, key), key) for key in keys
                                 if (kind, key) in origin_local or key in character_creation.default_availability()[kind])
                    for kind, keys in availability.items() if kind in character_creation.KINDS}
        portable["skills"] = sorted(local_skill.get(key, key) for key in availability["skills"]
                                    if key in local_skill or key in character_creation.default_availability()["skills"])
        document["availability"] = {kind: keys for kind, keys in portable.items() if keys}
    return document, report


def _magic_closure(database, world_id, content, triggers, origin_definitions, document, report):
    """Bundle every custom ability and effect the exported world uses; return installed -> local keys."""
    effects = mud_effects.catalog(database, include_removed=True)
    abilities = mud_abilities.catalog(database, include_removed=True)
    owner = ("world", world_id)
    linked_effects = mud_shared_definitions.owned_keys(database, owner, "effects")
    linked_abilities = mud_shared_definitions.owned_keys(database, owner, "abilities")
    wanted_effects, wanted_abilities = set(linked_effects), set(linked_abilities)
    for table in ("mud_item_definitions", "mud_npc_definitions"):
        for row in content.get(table, []):
            wanted_effects |= mud_effects.property_effect_keys(row.get("properties_json"))
    for trigger in triggers:
        wanted_effects.update(ref for kind, ref in mud_triggers.references(trigger) if kind == "effect")
    mud_world_dungeons.shared_references(database, world_id, wanted_effects)
    for definition in origin_definitions:
        wanted_abilities.update(definition.get("spells", []))
    wanted_abilities -= set(mud_abilities.BUILT_IN)
    for key in sorted(wanted_abilities):
        if key not in abilities:
            report.warning(ABILITIES_FILE, "", f"Ability {key} is no longer installed and was left out.",
                           "missing_reference")
            continue
        wanted_effects.update(spec["key"] for spec in abilities[key].get("effects", []))
    wanted_effects -= set(mud_effects.EFFECTS)
    effect_local, ability_local = {}, {}
    taken = set(mud_effects.EFFECTS)
    for key in sorted(wanted_effects):
        if key not in effects:
            report.warning(EFFECTS_FILE, "", f"Effect {key} is no longer installed and was left out.",
                           "missing_reference")
            continue
        local = linked_effects.get(key, key)
        while local in taken:
            local = f"{local[:36]}-x"
        taken.add(local)
        effect_local[key] = local
    taken = set(mud_abilities.BUILT_IN)
    for key in sorted(wanted_abilities):
        if key not in abilities:
            continue
        local = linked_abilities.get(key, key)
        while local in taken:
            local = f"{local[:36]}-x"
        taken.add(local)
        ability_local[key] = local
    fields = mud_effects.DEFINITION_FIELDS
    exported_effects = [{**{field: effects[key][field] for field in fields if field in effects[key]},
                         "key": local} for key, local in effect_local.items()]
    exported_abilities = []
    for key, local in ability_local.items():
        definition = {field: value for field, value in abilities[key].items() if field not in ("imported", "installed")}
        definition = mud_abilities.remap_effects({**definition, "key": local}, effect_local)
        exported_abilities.append(definition)
    if exported_effects:
        document["effects"] = sorted(exported_effects, key=lambda item: item["key"])
    if exported_abilities:
        document["abilities"] = sorted(exported_abilities, key=lambda item: item["key"])
    return effect_local, ability_local


def _export_triggers(triggers, maps, local_reference):
    """Package-local triggers; effects and other definitions are renamed by local_reference."""
    local_ids = {trigger["global_id"]: trigger["id"] for trigger in triggers}

    def mapper(kind, value):
        if kind == "trigger":
            if value not in local_ids:
                raise WorldError(f"A trigger runs a trigger that no longer exists: {value}.")
            return local_ids[value]
        if kind == "exit":
            if value not in maps["exits"]:
                raise WorldError(f"A trigger names an exit outside this world: {value}.")
            return maps["exits"][value]
        if kind in ORIGIN_KINDS or kind in ("skill", "effect"):
            return local_reference(kind, value)
        return _mapped(maps, TRIGGER_TABLES[kind], value, f"trigger {kind}")

    events = []
    for trigger in triggers:
        trigger = mud_triggers.remap({key: value for key, value in trigger.items() if key != "global_id"}, mapper)
        events.append({key: value for key, value in trigger.items()
                       if value not in (None, [], {}, "") or key in ("source", "actions")})
    return events


def _export_exit_reference(maps, local_reference):
    """Exit conditions name content by installed ID; export them by package-local ID."""
    def mapper(kind, value):
        if kind in ORIGIN_KINDS or kind == "skill":
            return local_reference(kind, value)
        return _mapped(maps, CONDITION_TABLES[kind], value, f"exit {kind} condition")
    return mapper


# Station-owned rows (portal shop, treasure, origin kit, dungeon-managed definitions) carry
# world_id='stationnet' only as the column default; retiring the home world must leave them.
SYSTEM_ROW_GUARD = (" AND id NOT LIKE 'stationnet:%' AND id NOT LIKE 'origin:%' AND id NOT IN "
                    "(SELECT definition_id FROM mud_dungeon_managed_definitions)")


def _retire_rows(database, world_id, reason, *, keep_state=False):
    """Take a world's content out of play; the caller owns the transaction.

    Definitions a player's possessions or quest history name are kept under a
    retired world ID, separate from any later installation with the same ID.
    """
    from . import mud
    # Runs end before their rooms go, so explorers keep nothing from a vanished world.
    mud.end_world_runs(database, world_id, reason)
    if not keep_state:
        for table in ("mud_script_flags", "mud_trigger_fires", "mud_script_entity_states"):
            database.execute(f"DELETE FROM {table} WHERE realm=?", (world_id,))
    retired_id = f"retired:{world_id}:{uuid.uuid4().hex}"
    for table in ("mud_item_definitions", "mud_npc_definitions", "mud_conversations", "mud_quests",
                  "mud_world_factions"):
        database.execute(f"UPDATE {table} SET world_id=? WHERE world_id=?{SYSTEM_ROW_GUARD}", (retired_id, world_id))
    # Room items may contain other items; remove descendants before their
    # containers so the item-location constraint remains satisfied.
    items = database.execute(
        "WITH RECURSIVE placed(id,depth) AS ("
        "SELECT id,0 FROM mud_item_instances WHERE room_id IN "
        "(SELECT id FROM mud_world_rooms WHERE world_id=?) UNION ALL "
        "SELECT child.id,placed.depth+1 FROM mud_item_instances child "
        "JOIN placed ON child.container_id=placed.id) "
        "SELECT id FROM placed ORDER BY depth DESC", (world_id,)
    ).fetchall()
    for item in items:
        database.execute("DELETE FROM mud_item_instances WHERE id=?", (item["id"],))
    database.execute("DELETE FROM mud_professions WHERE world_id=?", (world_id,))
    # Vehicles go before their rooms, taking riders and interior copies with them.
    for table in ("mud_vehicles", "mud_transit_trips", "mud_vehicle_definitions", "mud_transit_lines"):
        database.execute(f"DELETE FROM {table} WHERE world_id=?", (world_id,))
    # Ending holdings hands stored items back to their owners before the rooms go.
    database.execute("DELETE FROM mud_property_holdings WHERE world_id=?", (world_id,))
    database.execute("DELETE FROM mud_properties WHERE world_id=?", (world_id,))
    database.execute("DELETE FROM mud_world_rooms WHERE world_id=?", (world_id,))
    return retired_id


def removal_origins(database, world_id):
    from . import character_creation, mud_skills
    linked = world_origin_keys(database, world_id)
    result = character_creation.removable_associations(database, {**linked, "world_id": world_id})
    result["skills"] = mud_skills.removable_associations(database, linked["skills"], world_id=world_id)
    return result


REMOVAL_BATCH = 200


def _transaction(database):
    """One short write transaction; commits (or rolls back) on exit."""
    class _Txn:
        def __enter__(self):
            database.execute("BEGIN IMMEDIATE")
            return database

        def __exit__(self, kind, error, trace):
            if kind is None:
                database.commit()
            else:
                database.rollback()
            return False
    return _Txn()


def _staged_files(world_id):
    path = directory() / f"{world_id}.zip"
    return sorted(path.parent.glob(path.name + ".removing-*"))


def begin_removal(database, world_id, *, remove_origins=False):
    """Validate a removal and mark the world as being removed; the heavy work is finish_removal's.

    The world is disabled, so players are not in it. Marking it ``removing`` keeps it from being
    enabled again and lets an interrupted removal resume after a restart.
    """
    world = database.execute("SELECT imported,enabled,removing FROM mud_world_packages WHERE id=?",
                             (world_id,)).fetchone()
    if world is None or not world["imported"]:
        raise WorldError("Only installed custom worlds can be removed.")
    if world["enabled"]:
        raise WorldError("Disable this world before removing it.")
    if world["removing"]:
        raise WorldError("This world is already being removed.")
    path = directory() / f"{world_id}.zip"
    staged = path.with_name(path.name + ".removing-" + uuid.uuid4().hex)
    if path.exists():
        path.rename(staged)
    try:
        with _transaction(database):
            marked = database.execute(
                "UPDATE mud_world_packages SET removing=1,removing_origins=? "
                "WHERE id=? AND imported=1 AND enabled=0 AND removing=0", (int(bool(remove_origins)), world_id))
            if marked.rowcount != 1:
                raise WorldError("Disable this world before removing it.")
    except Exception:
        if staged.exists():
            staged.rename(path)
        raise


def _delete_batches(database, select_ids, delete_sql, parameters=()):
    """Delete in small transactions so other writers get the database between them."""
    import time
    while True:
        ids = [row[0] for row in database.execute(select_ids, parameters)]
        if not ids:
            return
        with _transaction(database):
            database.executemany(delete_sql, [(value,) for value in ids])
        time.sleep(0.01)


def _retire_rows_batched(database, world_id, reason):
    """_retire_rows in short transactions. Every step is safe to repeat if removal is resumed."""
    import time
    from . import mud
    with _transaction(database):
        mud.end_world_runs(database, world_id, reason)
        for table in ("mud_script_flags", "mud_trigger_fires", "mud_script_entity_states"):
            database.execute(f"DELETE FROM {table} WHERE realm=?", (world_id,))
        retired_id = f"retired:{world_id}:{uuid.uuid4().hex}"
        for table in ("mud_item_definitions", "mud_npc_definitions", "mud_conversations", "mud_quests",
                      "mud_world_factions"):
            database.execute(f"UPDATE {table} SET world_id=? WHERE world_id=?{SYSTEM_ROW_GUARD}", (retired_id, world_id))
    # Descendants go before their containers so the item-location constraint holds in every batch.
    items = [row[0] for row in database.execute(
        "WITH RECURSIVE placed(id,depth) AS ("
        "SELECT id,0 FROM mud_item_instances WHERE room_id IN "
        "(SELECT id FROM mud_world_rooms WHERE world_id=?) UNION ALL "
        "SELECT child.id,placed.depth+1 FROM mud_item_instances child "
        "JOIN placed ON child.container_id=placed.id) "
        "SELECT id FROM placed ORDER BY depth DESC", (world_id,))]
    for start in range(0, len(items), REMOVAL_BATCH * 2):
        with _transaction(database):
            database.executemany("DELETE FROM mud_item_instances WHERE id=?",
                                 [(value,) for value in items[start:start + REMOVAL_BATCH * 2]])
        time.sleep(0.01)
    with _transaction(database):
        database.execute("DELETE FROM mud_professions WHERE world_id=?", (world_id,))
        for table in ("mud_vehicles", "mud_transit_trips", "mud_vehicle_definitions", "mud_transit_lines"):
            database.execute(f"DELETE FROM {table} WHERE world_id=?", (world_id,))
        database.execute("DELETE FROM mud_property_holdings WHERE world_id=?", (world_id,))
        database.execute("DELETE FROM mud_properties WHERE world_id=?", (world_id,))
    # Rooms cascade into exits, NPC instances and more, so remove them a slice at a time.
    _delete_batches(database, f"SELECT id FROM mud_world_rooms WHERE world_id=? LIMIT {REMOVAL_BATCH}",
                    "DELETE FROM mud_world_rooms WHERE id=?", (world_id,))


def finish_removal(database, world_id):
    """Do the removal begin_removal started: clear the world in slices, then drop its package."""
    from . import character_creation, mud_skills
    world = database.execute("SELECT removing,removing_origins FROM mud_world_packages WHERE id=?",
                             (world_id,)).fetchone()
    if world is None or not world["removing"]:
        raise WorldError("This world is not being removed.")
    remove_origins = bool(world["removing_origins"])
    # Origin links come from tables the steps below leave alone, so it is safe to read them first.
    associated = removal_origins(database, world_id)
    linked = world_origin_keys(database, world_id)
    selected = {kind: {entry["key"] for entry in associated[kind]}
                for kind in (*character_creation.KINDS, "skills")}
    _retire_rows_batched(database, world_id, "This world was removed from the station.")
    with _transaction(database):
        if remove_origins and any(selected[kind] for kind in character_creation.KINDS):
            character_creation.remove_origins(database, selected)
        # Skills go after the origins removed above, which no longer depend on them.
        if remove_origins and selected["skills"]:
            selected["skills"] = {entry["key"] for entry in
                                  mud_skills.remove_linked(database, associated["skills"])}
        # Abilities and effects go last, once nothing removed above still grants or applies them.
        magic = (mud_shared_definitions.remove_unused_abilities_and_effects(database, ("world", world_id))
                 if remove_origins else {"abilities": [], "effects": [], "kept": []})
        database.execute("DELETE FROM mud_world_packages WHERE id=?", (world_id,))
        database.execute("DELETE FROM mud_world_availability WHERE world_key=?", (world_id,))
        preserved = {kind: len(linked[kind]) - (len(selected[kind]) if remove_origins else 0)
                     for kind in selected}
        # Worlds left without a race, class, or starting skills fall back to the built-ins.
        restored = character_creation.restore_world_defaults(database)
    file_warning = False
    for staged in _staged_files(world_id):
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            file_warning = True
    return {"removed": selected if remove_origins else {kind: set() for kind in selected},
            "preserved": preserved, "magic": magic,
            "restored": restored, "file_warning": file_warning}


def remove(database, world_id, *, remove_origins=False):
    """Uninstall a disabled imported world. Its characters stay, hidden, and return if it is reinstalled."""
    begin_removal(database, world_id, remove_origins=remove_origins)
    return finish_removal(database, world_id)


def removing_worlds(database):
    return [row["id"] for row in database.execute("SELECT id FROM mud_world_packages WHERE removing=1")]


def start_removal(app, world_id):
    """Run finish_removal on a worker thread; False if one is already running for this world."""
    from . import background_jobs
    return background_jobs.start(app, f"mud-world-remove:{world_id}",
                                 lambda database: finish_removal(database, world_id))


def resume_removals(app):
    """Restart removals a shutdown interrupted."""
    from .db import close_db, get_db
    with app.app_context():
        try:
            pending = removing_worlds(get_db())
        finally:
            close_db()
    for world_id in pending:
        start_removal(app, world_id)
