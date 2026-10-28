"""Portable, file-authored traditional MUD maps."""
from io import BytesIO
import hashlib
import json
import hashlib
from pathlib import Path
import re
import uuid
import zipfile

from flask import current_app

from . import mud_conditions, mud_gathering, mud_work, mud_scope, mud_society, mud_triggers, mud_wayfinding, mud_wilderness, mud_world_dungeons


MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
FILES = {"manifest.json", "rooms.json", "exits.json"}
CONTENT_FILE = "content.json"
ORIGINS_FILE = "origins.json"
SKILLS_FILE = "skills.json"
DUNGEONS_FILE = mud_world_dungeons.DUNGEONS_FILE
EVENTS_FILE = "events.json"
WILDERNESS_FILE = mud_wilderness.WILDERNESS_FILE
OPTIONAL_FILES = {CONTENT_FILE, ORIGINS_FILE, SKILLS_FILE, DUNGEONS_FILE, EVENTS_FILE, WILDERNESS_FILE}
# Order follows foreign-key dependencies. Only these authored columns cross
# the package boundary; player state and generated dungeon data never do.
CONTENT_TABLES = {
    "mud_world_factions": ("id", "name", "description", "default_reputation", "defeat_reputation", "public",
                           "ranks_json", "sort_order"),
    "mud_faction_relations": ("id", "faction_id", "other_faction_id", "share_percent"),
    "mud_item_definitions": ("id", "name", "description", "item_type", "properties_json", "behaviors_json", "equipment_slot"),
    "mud_conversations": ("id", "name"),
    "mud_dialogue_nodes": ("id", "conversation_id", "node_key", "body", "is_start"),
    "mud_quests": ("id", "name", "description", "prerequisite_quest_id", "completion_mode",
                   "turn_in_npc_definition_id", "sequential", "abandonable", "conditions_json"),
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
                             "optional", "consume_on_turn_in", "recipient_npc_definition_id"),
    "mud_quest_rewards": ("id", "quest_id", "reward_type", "target_id", "amount"),
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
}
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
}
# Quests and dialogue responses carry condition/outcome lists that name content.
CONDITION_COLUMNS = {"mud_quests": ("conditions_json",),
                     "mud_room_discoveries": ("conditions_json",),
                     "mud_professions": ("conditions_json",),
                     "mud_work_activities": ("conditions_json",),
                     "mud_dialogue_responses": ("conditions_json", "outcomes_json"),
                     "mud_world_destinations": ("conditions_json",),
                     "mud_shop_stock": ("conditions_json",)}
CONDITION_TABLES = {"item": "mud_item_definitions", "quest": "mud_quests",
                    "objective": "mud_quest_objectives", "object": "mud_item_instances", "room": "rooms",
                    "destination": "mud_world_destinations", "npc": "mud_npc_definitions",
                    "faction": "mud_world_factions"}
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
    pass


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


def read_package(payload):
    """Read a bounded ZIP without extracting paths or accepting hidden extras."""
    if not payload or len(payload) > MAX_ZIP_BYTES:
        raise WorldError("World ZIP must be between 1 byte and 16 MB.")
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            if not FILES <= set(names) or set(names) - FILES - OPTIONAL_FILES or len(names) != len(set(names)):
                raise WorldError("World ZIP must contain manifest.json, rooms.json, and exits.json at its root, with optional content.json, origins.json, skills.json, dungeons.json, events.json, and wilderness.json.")
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
    world_id = manifest.get("id")
    if not isinstance(world_id, str) or not ID_RE.fullmatch(world_id) or world_id == "stationnet":
        raise WorldError("World ID must be lowercase letters, digits, and hyphens, starting with a letter; 'stationnet' is reserved.")
    name = _label(manifest.get("name"), "World name", 2, 80)
    author = _label(manifest.get("author"), "World author", 1, 80)
    resource = validate_resource(manifest.get("resource"))
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
    for room in rooms:
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
    if start not in authored_ids:
        raise WorldError("start_room does not match a room in rooms.json.")
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
    clean_exits = []
    exit_names = set()
    exit_directions = set()
    exit_ids = set()
    for item in exits:
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
        clean_exits.append({"from": source, "to": target, "name": label, "direction": direction,
                            "key_item_id": key_item_id, "pick_difficulty": pick_difficulty,
                            "conditions": conditions, "conditions_message": " ".join(message.split()),
                            "id": exit_id, "hidden": hidden})
    content = docs.get(CONTENT_FILE, {})
    if not isinstance(content, dict) or set(content) - set(CONTENT_TABLES):
        raise WorldError("content.json must be an object of supported content tables.")
    destination_names = set()
    for table, rows in content.items():
        if not isinstance(rows, list) or len(rows) > 5000 or any(not isinstance(row, dict) for row in rows):
            raise WorldError(f"{table} must be an array of at most 5000 objects.")
        if table == "mud_npc_definitions":
            from .mud import validate_npc_behaviors
            for row in rows:
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
        for row in rows:
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
            elif table == "mud_quests":
                if row.get("completion_mode", "auto") not in {"auto", "turn_in"}:
                    raise WorldError("Quest completion_mode must be auto or turn_in.")
                if not _whole(row.get("sequential", 0), 0, 1) or not _whole(row.get("abandonable", 1), 0, 1):
                    raise WorldError("Quest sequential and abandonable must be 0 or 1.")
            elif table == "mud_world_factions":
                try:
                    row.update(mud_society.validate_faction(row))
                except ValueError as problem:
                    raise WorldError(f"Invalid faction {row.get('id')}: {problem}") from problem
            elif table == "mud_faction_relations":
                try:
                    mud_society.validate_share(row.get("share_percent"))
                except ValueError as problem:
                    raise WorldError(str(problem)) from problem
                if row.get("faction_id") == row.get("other_faction_id"):
                    raise WorldError("A faction cannot be related to itself.")
            elif table == "mud_quest_rewards" and row.get("reward_type") == "reputation":
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
    skills = docs.get(SKILLS_FILE, {"skills": []})
    if (not isinstance(skills, dict) or set(skills) != {"skills"} or not isinstance(skills["skills"], list)
            or len(skills["skills"]) > 100 or any(not isinstance(item, dict) for item in skills["skills"])):
        raise WorldError("skills.json must contain a skills array of at most 100 definition objects.")
    try:
        world_dungeons = mud_world_dungeons.read_document(docs.get(DUNGEONS_FILE), room_ids)
    except mud_world_dungeons.WorldDungeonError as problem:
        raise WorldError(str(problem)) from problem
    try:
        triggers = mud_triggers.validate_document(docs.get(EVENTS_FILE))
    except ValueError as problem:
        raise WorldError(f"Invalid events.json: {problem}") from problem
    known = {kind: {row.get("id") for row in content.get(table, [])}
             for kind, table in TRIGGER_TABLES.items() if table != "rooms"}
    known.update(room=room_ids, exit=exit_ids, trigger={trigger["id"] for trigger in triggers})
    for trigger in triggers:
        for kind, ref in mud_triggers.references(trigger):
            if kind in known and ref not in known[kind]:
                raise WorldError(f"Trigger {trigger['id']} names an unknown {kind}: {ref}.")
    return {"id": world_id, "name": name, "author": author, "start_room": start, "resource": resource,
            "society": society,
            "rooms": clean_rooms, "exits": clean_exits, "content": content, "origins": origins,
            "skills": skills["skills"], "dungeons": world_dungeons, "events": triggers,
            # wilderness is the file as written, so a parsed document repackages unchanged;
            # wild is its validated run index, used to install and resolve '@x,y,z'.
            "wilderness": docs.get(WILDERNESS_FILE), "wild": wilderness}


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
        if document.get("resource", DEFAULT_RESOURCE) != DEFAULT_RESOURCE:
            manifest["resource"] = document["resource"]
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
        "id": "my-world", "name": "My World", "author": "Your name", "start_room": "start",
        "rooms": [{"id": "start", "name": "Starting Room",
                   "description": "The beginning of a world you can build from files."}],
        "exits": [],
        "events": [{"id": "welcome", "name": "Welcome message", "event": "enter_room",
                    "source": {"room": "start"}, "once": True,
                    "actions": [{"type": "message", "text": "Welcome. Edit events.json to make this world react."}]}],
    })


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
    if not isinstance(properties, (dict, list)):
        raise WorldError("properties_json must be JSON text or an object.")
    return json.dumps(properties, ensure_ascii=False, separators=(",", ":"))


def _insert_content(database, document, installation_key, skill_map=None):
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
                elif table == "mud_item_definitions" and column == "properties_json":
                    value = _remap_item_properties(value, condition_mapper)
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
                         "mud_world_destinations", "mud_world_factions", "mud_professions"}:
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


def _install_skills(database, document):
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
        existing = database.execute("SELECT definition_json,installed FROM mud_skill_definitions WHERE key=?",
                                    (key,)).fetchone()
        if existing is not None:
            if not existing["installed"] or json.loads(existing["definition_json"]) != definition:
                raise WorldError(f"Bundled skill {local_key} conflicts with an installed definition.")
        else:
            database.execute(
                "INSERT INTO mud_skill_definitions(key,definition_json,imported,installed,enabled) VALUES(?,?,1,1,0)",
                (key, json.dumps(definition, separators=(",", ":"))))
        database.execute("INSERT INTO mud_world_skills(world_id,skill_key,local_key) VALUES(?,?,?)",
                         (document["id"], key, local_key))
    return mapping


def _install_origins(database, document, skill_map=None):
    from . import character_creation, mud_skills

    for kind in character_creation.KINDS:
        seen = set()
        for source in document["origins"].get(kind, []):
            local_key = source.get("key")
            if not isinstance(local_key, str) or not character_creation.KEY_RE.fullmatch(local_key) or local_key in seen:
                raise WorldError(f"Bundled {kind} need distinct valid local keys.")
            seen.add(local_key)
            key = bundled_key(document["id"], kind, local_key)
            try:
                definition = character_creation.validate_origin(
                    kind, mud_skills.remap({**source, "key": key}, skill_map or {}), database, check_key=False)
            except ValueError as error:
                raise WorldError(f"Invalid bundled {kind[:-1]} {local_key}: {error}") from error
            existing = database.execute(
                "SELECT definition_json,installed FROM mud_origin_definitions WHERE kind=? AND key=?",
                (kind, key),
            ).fetchone()
            if existing is not None:
                if not existing["installed"] or json.loads(existing["definition_json"]) != definition:
                    raise WorldError(f"Bundled {kind[:-1]} {local_key} conflicts with an installed definition.")
            else:
                database.execute(
                    "INSERT INTO mud_origin_definitions(kind,key,definition_json,imported,installed,enabled) "
                    "VALUES(?,?,?,1,1,0)",
                    (kind, key, json.dumps(definition, separators=(",", ":"))),
                )
            database.execute(
                "INSERT INTO mud_world_origins(world_id,kind,origin_key,local_key) VALUES(?,?,?,?)",
                (document["id"], kind, key, local_key),
            )


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
        "SELECT p.id,p.name,p.author,p.imported,p.enabled,"
        "(SELECT COUNT(*) FROM mud_characters AS c WHERE c.world_key=p.id AND c.creation_state<>'pending') "
        "AS characters FROM mud_world_packages AS p ORDER BY p.id<>?,p.name COLLATE NOCASE,p.id",
        (mud_scope.HOME,)).fetchall()
    result = [{"key": row["id"], "name": row["name"], "author": row["author"], "kind": "authored",
               "imported": bool(row["imported"]), "enabled": bool(row["enabled"]),
               "characters": row["characters"]} for row in rows]
    procedural = bool(station and station["mud_procedural_enabled"])
    result.append({"key": mud_scope.PROCEDURAL, "name": world_name(database, mud_scope.PROCEDURAL),
                   "author": "StationNet", "kind": "procedural", "imported": False, "enabled": procedural,
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
        if database.execute("SELECT 1 FROM mud_world_rooms WHERE id=? AND world_id=?",
                            (world["start_room_id"], world_id)).fetchone() is None:
            raise WorldError("World starting room is missing.")
        chosen = character_creation.world_availability(database, world_id)
        if not chosen["races"] or not chosen["classes"]:
            raise WorldError("Offer at least one race and one class in this world first.")
    database.execute("UPDATE mud_world_packages SET enabled=? WHERE id=?", (int(bool(enabled)), world_id))


def _exit_condition_mapper(document, maps, skill_map):
    """Exit conditions resolve like content conditions, once content IDs exist."""
    origin_map = _bundled_origin_map(document)

    def mapper(kind, value):
        if kind in ORIGIN_KINDS:
            return origin_map.get((ORIGIN_KINDS[kind], value), value)
        if kind == "skill":
            return (skill_map or {}).get(value, value)
        return _mapped(maps, CONDITION_TABLES[kind], value, f"exit {kind} condition")
    return mapper


def install(database, payload, *, origin_mode=None, selected=None, enable=False):
    document = read_package(payload)
    world_id = document["id"]
    if database.execute("SELECT 1 FROM mud_world_packages WHERE id=?", (world_id,)).fetchone():
        raise WorldError("A world with that ID is already installed.")
    destination = directory() / f"{world_id}.zip"
    if destination.exists():
        raise WorldError("A world ZIP with that ID already exists.")
    installation_key = uuid.uuid4().hex
    prefix = f"world:{world_id}:{installation_key}:room:"
    created = False
    try:
        database.execute("BEGIN IMMEDIATE")
        database.execute("INSERT INTO mud_world_packages(id,name,author,start_room_id,imported,"
                         "resource_enabled,resource_name,society_json) VALUES(?,?,?,?,1,?,?,?)",
                         (world_id, document["name"], document["author"], prefix + document["start_room"],
                          int(document["resource"]["enabled"]), document["resource"]["name"],
                          json.dumps(mud_society.changed_settings(document["society"]), separators=(",", ":"))))
        skill_map = _install_skills(database, document)
        for room in document["rooms"]:
            x, y, z = room.get("x"), room.get("y"), room.get("z")
            database.execute("INSERT INTO mud_world_rooms(id,name,description,world_id,x,y,z,grid_link,blocked_dirs,banking_enabled) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (prefix + room["id"], room["name"], room["description"], world_id, x, y, z,
                              int(room.get("grid_link", True)), room.get("blocked_dirs", ""), int(room["bank"])))
        try:
            preset_ids, region_ids = mud_wilderness.install_cells(database, world_id, installation_key,
                                                                  document["wild"])
        except mud_wilderness.WildernessError as problem:
            raise WorldError(f"Invalid wilderness.json: {problem}") from problem
        room_refs = _room_references(document, installation_key)
        for ordinal, item in enumerate(document["exits"]):
            database.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction,pick_difficulty,"
                             "conditions_json,conditions_message,local_id,hidden) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (f"world:{world_id}:{installation_key}:exit:{ordinal}", room_refs[item["from"]],
                              room_refs[item["to"]], item["name"], item["direction"], item["pick_difficulty"],
                              "[]", item["conditions_message"], item.get("id"), int(item.get("hidden", False))))
        maps = _insert_content(database, document, installation_key, skill_map)

        def shared(table):
            return lambda value: value if value.startswith("stationnet:") else _mapped(maps, table, value, "preset reference")

        mud_wilderness.install_references(database, world_id, document["wild"], preset_ids, region_ids,
                                          shared("mud_npc_definitions"), shared("mud_item_definitions"))
        exit_mapper = _exit_condition_mapper(document, maps, skill_map)
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
                    database, world_id, document["dungeons"], room_refs, maps, exit_mapper)
            except mud_world_dungeons.WorldDungeonError as problem:
                raise WorldError(str(problem)) from problem
        _install_triggers(database, document, installation_key, maps, exit_mapper)
        _install_origins(database, document, skill_map)
        set_availability(database, world_id, origin_mode, selected)
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


def _condition_validator(column):
    return (mud_conditions.validate_outcomes if column == "outcomes_json"
            else mud_conditions.validate_conditions)


def _stored_conditions(value, column):
    """Stored condition/outcome JSON as a normalized list; unreadable values export as []."""
    try:
        return _condition_validator(column)(value)
    except ValueError:
        return []


def _export_content(database, world_id, room_map, exit_map=None):
    roots = {"mud_item_definitions", "mud_conversations", "mud_npc_definitions", "mud_quests", "mud_world_factions", "mud_professions"}
    all_placed_items = [dict(row) for row in database.execute(
        "SELECT id,definition_id,room_id,container_id FROM mud_item_instances "
        "WHERE spawn_source_id IS NULL AND id NOT LIKE 'world:wild-item:%'"
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
        "JOIN mud_world_rooms r ON r.id=d.room_id WHERE r.world_id=?",
        (world_id, world_id, world_id, world_id, world_id, world_id),
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
            ).fetchone()[0] == world_id and not row["id"].startswith("stationnet:") and
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
        elif table in {"mud_item_spawns", "mud_gathering_nodes", "mud_room_discoveries", "mud_world_destinations"}:
            rows = [row for row in rows if row["room_id"] in room_map]
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
                "mud_faction_relations": "faction-relation"}
    prefixes.update({"mud_professions": "profession", "mud_work_activities": "work",
                     "mud_work_rewards": "work-reward"})
    maps = {"rooms": room_map, "exits": exit_map or {}}
    for table in ID_TABLES:
        maps[table] = {key: f"{prefixes[table]}-{index}"
                       for index, key in enumerate(selected[table], 1)}
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
                elif table == "mud_world_destinations" and column == "map_items_json":
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
                                    del value[key]
                                else:
                                    value[key] = local
                    if table == "mud_npc_definitions" and column == "behaviors_json" and isinstance(value, dict):
                        # References outside the exported world are dropped rather than left dangling.
                        value = mud_society.remap_behaviors(value, lambda kind, ref: (
                            room_map.get(ref) if kind == "room"
                            else maps.get(BEHAVIOR_TABLES[kind], {}).get(ref)))
                    if (table == "mud_item_definitions" and column == "properties_json" and isinstance(value, dict)
                            and "teaches_destinations" in value):
                        # Destinations outside the exported rooms are dropped rather than left dangling.
                        local = maps["mud_world_destinations"]
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


def export(database, world_id, *, origin_keys=None, skill_keys=None):
    world = database.execute("SELECT * FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
    if world is None:
        raise WorldError("World was not found.")
    rooms = database.execute("SELECT id,name,description,x,y,z,grid_link,blocked_dirs,banking_enabled FROM mud_world_rooms "
                             "WHERE world_id=? AND kind='authored' ORDER BY id", (world_id,)).fetchall()
    if not rooms:
        raise WorldError("World has no rooms to export.")
    # Wilderness rooms travel as run-length layers in wilderness.json and are named '@x,y,z'.
    ids = mud_wilderness.export_references(world_id, {room["id"]: f"room-{index}" for index, room in enumerate(rooms, 1)})
    if world["start_room_id"] not in ids:
        raise WorldError("World starting room is missing.")
    exits = database.execute(
        "SELECT e.id,e.from_room_id,e.to_room_id,e.name,e.direction,e.key_item_id,e.pick_difficulty,"
        "e.conditions_json,e.conditions_message,e.local_id,e.hidden FROM mud_world_exits e "
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
    content, maps = _export_content(database, world_id, ids, exit_ids)
    document = {"id": world_id if world_id != "stationnet" else "my-world",
                "name": world["name"], "author": world["author"],
                "start_room": ids[world["start_room_id"]],
                "resource": {"enabled": bool(world["resource_enabled"]), "name": world["resource_name"]},
                "society": mud_society.stored_settings(world["society_json"]),
                "rooms": [{"id": ids[room["id"]], "name": room["name"], "description": room["description"],
                           **({"bank": True} if room["banking_enabled"] else {}),
                           **({"x": room["x"], "y": room["y"], "z": room["z"]} if room["x"] is not None else {}),
                           **({"grid_link": False} if room["x"] is not None and not room["grid_link"] else {}),
                           **({"blocked_dirs": room["blocked_dirs"]} if room["x"] is not None and room["blocked_dirs"] else {})}
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
                           **({"conditions_message": row["conditions_message"]} if row["conditions_message"] else {})}
                          for row in exits if row["to_room_id"] in ids],
                "content": content}
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
    origins = {kind: [] for kind in character_creation.KINDS}
    for kind, key in sorted(chosen):
        definition = mud_skills.remap(dict(installed[(kind, key)]["definition"]), local_skill)
        definition["key"] = origin_local[(kind, key)]
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
        return local_skill.get(ref, ref) if kind == "skill" else ref

    for table, columns in CONDITION_COLUMNS.items():
        for row in content.get(table, []):
            for column in columns:
                row[column] = mud_conditions.remap(row[column], local_reference)
    for row in content.get("mud_item_definitions", []):
        if isinstance(row.get("properties_json"), dict) and row["properties_json"].get("requirements"):
            row["properties_json"] = json.loads(_remap_item_properties(row["properties_json"], local_reference))
    for item in document["exits"]:
        if "conditions" in item:
            item["conditions"] = mud_conditions.remap(item["conditions"], _export_exit_reference(maps, local_reference))
    world_dungeons = mud_world_dungeons.export_document(
        database, world_id, ids, maps, _export_exit_reference(maps, local_reference))
    if world_dungeons:
        document["dungeons"] = world_dungeons
    events = _export_triggers(triggers, maps, local_reference)
    if events:
        document["events"] = events

    def shared(table):
        return lambda value: maps.get(table, {}).get(value, value)

    wilderness = mud_wilderness.export_document(database, world_id, shared("mud_npc_definitions"),
                                                shared("mud_item_definitions"))
    if wilderness:
        document["wilderness"] = wilderness
    return package_bytes(document)


def _export_triggers(triggers, maps, local_reference):
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
        if kind in ORIGIN_KINDS or kind == "skill":
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


def removal_origins(database, world_id):
    from . import character_creation, mud_skills
    linked = world_origin_keys(database, world_id)
    result = character_creation.removable_associations(database, {**linked, "world_id": world_id})
    result["skills"] = mud_skills.removable_associations(database, linked["skills"], world_id=world_id)
    return result


def remove(database, world_id, *, remove_origins=False):
    """Uninstall a disabled imported world. Its characters stay, hidden, and return if it is reinstalled."""
    from . import character_creation, mud, mud_skills
    world = database.execute("SELECT imported,enabled FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
    if world is None or not world["imported"]:
        raise WorldError("Only installed custom worlds can be removed.")
    if world["enabled"]:
        raise WorldError("Disable this world before removing it.")
    path = directory() / f"{world_id}.zip"
    staged = path.with_name(path.name + ".removing-" + uuid.uuid4().hex)
    if path.exists():
        path.rename(staged)
    try:
        with database:
            database.execute("BEGIN IMMEDIATE")
            world = database.execute("SELECT imported,enabled FROM mud_world_packages WHERE id=?",
                                     (world_id,)).fetchone()
            if world is None or not world["imported"]:
                raise WorldError("Only installed custom worlds can be removed.")
            if world["enabled"]:
                raise WorldError("Disable this world before removing it.")
            # Runs end before their rooms go, so explorers keep nothing from a vanished world.
            mud.end_world_runs(database, world_id, "This world was removed from the station.")
            associated = removal_origins(database, world_id)
            linked = world_origin_keys(database, world_id)
            for table in ("mud_script_flags", "mud_trigger_fires", "mud_script_entity_states"):
                database.execute(f"DELETE FROM {table} WHERE realm=?", (world_id,))
            selected = {kind: {entry["key"] for entry in associated[kind]}
                        for kind in (*character_creation.KINDS, "skills")}
            # Keep definitions referenced by a player's possessions or quest history,
            # while separating them from a later installation with the same world ID.
            retired_id = f"retired:{world_id}:{uuid.uuid4().hex}"
            for table in ("mud_item_definitions", "mud_npc_definitions", "mud_conversations", "mud_quests",
                          "mud_world_factions"):
                database.execute(f"UPDATE {table} SET world_id=? WHERE world_id=?", (retired_id, world_id))
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
            database.execute("DELETE FROM mud_world_rooms WHERE world_id=?", (world_id,))
            database.execute("DELETE FROM mud_world_packages WHERE id=?", (world_id,))
            database.execute("DELETE FROM mud_world_availability WHERE world_key=?", (world_id,))
            if remove_origins and any(selected[kind] for kind in character_creation.KINDS):
                character_creation.remove_origins(database, selected)
            # Skills go last so the origins removed above no longer depend on them.
            if remove_origins and selected["skills"]:
                selected["skills"] = {entry["key"] for entry in
                                      mud_skills.remove_linked(database, associated["skills"])}
            preserved = {kind: len(linked[kind]) - (len(selected[kind]) if remove_origins else 0)
                         for kind in selected}
            # Worlds left without a race, class, or starting skills fall back to the built-ins.
            restored = character_creation.restore_world_defaults(database)
    except Exception:
        if staged.exists():
            staged.rename(path)
        raise
    try:
        staged.unlink(missing_ok=True)
        file_warning = False
    except OSError:
        file_warning = True
    return {"removed": selected if remove_origins else {kind: set() for kind in selected},
            "preserved": preserved,
            "restored": restored, "file_warning": file_warning}
