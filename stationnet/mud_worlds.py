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

from . import mud_conditions


MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
FILES = {"manifest.json", "rooms.json", "exits.json"}
CONTENT_FILE = "content.json"
ORIGINS_FILE = "origins.json"
SKILLS_FILE = "skills.json"
# Order follows foreign-key dependencies. Only these authored columns cross
# the package boundary; player state and generated dungeon data never do.
CONTENT_TABLES = {
    "mud_item_definitions": ("id", "name", "description", "item_type", "properties_json", "behaviors_json", "equipment_slot"),
    "mud_conversations": ("id", "name"),
    "mud_dialogue_nodes": ("id", "conversation_id", "node_key", "body", "is_start"),
    "mud_quests": ("id", "name", "description", "prerequisite_quest_id", "completion_mode",
                   "turn_in_npc_definition_id", "sequential", "abandonable", "conditions_json"),
    "mud_npc_definitions": ("id", "name", "description", "conversation_id", "properties_json", "behaviors_json"),
    "mud_npc_combat_profiles": ("npc_definition_id", "maximum_health", "attack", "defense", "experience_reward", "respawn_seconds", "accuracy", "evasion"),
    "mud_npc_equipment": ("npc_definition_id", "equipment_slot", "item_definition_id"),
    "mud_npc_combat_rewards": ("id", "npc_definition_id", "reward_type", "target_id", "amount", "chance_percent"),
    "mud_npc_shops": ("npc_definition_id", "buys_items", "sell_percent"),
    "mud_shop_stock": ("id", "npc_definition_id", "item_definition_id", "price", "quantity"),
    "mud_npc_trainers": ("id", "npc_definition_id", "skill_key", "price", "max_rank"),
    "mud_quest_objectives": ("id", "quest_id", "objective_type", "target_id", "description", "required_count", "sort_order",
                             "optional", "consume_on_turn_in", "recipient_npc_definition_id"),
    "mud_quest_rewards": ("id", "quest_id", "reward_type", "target_id", "amount"),
    "mud_quest_givers": ("quest_id", "npc_definition_id"),
    "mud_dialogue_responses": ("id", "node_id", "response_text", "next_node_id", "accept_quest_id", "deliver_item_id", "required_state_key", "required_state_value", "state_key", "state_value", "sort_order",
                               "conditions_json", "outcomes_json", "turn_in_quest_id"),
    "mud_item_instances": ("id", "definition_id", "room_id", "container_id", "is_open", "state_json"),
    "mud_item_spawns": ("id", "definition_id", "room_id", "respawn_seconds"),
    "mud_room_discoveries": ("id", "room_id", "clue", "item_definition_id", "difficulty"),
    "mud_npc_instances": ("id", "definition_id", "room_id", "state_json"),
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
    "turn_in_npc_definition_id": "mud_npc_definitions",
    "recipient_npc_definition_id": "mud_npc_definitions", "turn_in_quest_id": "mud_quests",
}
# Quests and dialogue responses carry condition/outcome lists that name content.
CONDITION_COLUMNS = {"mud_quests": ("conditions_json",),
                     "mud_dialogue_responses": ("conditions_json", "outcomes_json")}
CONDITION_TABLES = {"item": "mud_item_definitions", "quest": "mud_quests",
                    "objective": "mud_quest_objectives"}
ORIGIN_KINDS = {"race": "races", "class": "classes"}


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


def read_package(payload):
    """Read a bounded ZIP without extracting paths or accepting hidden extras."""
    if not payload or len(payload) > MAX_ZIP_BYTES:
        raise WorldError("World ZIP must be between 1 byte and 16 MB.")
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            if not FILES <= set(names) or set(names) - FILES - {CONTENT_FILE, ORIGINS_FILE, SKILLS_FILE} or len(names) != len(set(names)):
                raise WorldError("World ZIP must contain manifest.json, rooms.json, and exits.json at its root, with optional content.json, origins.json, and skills.json.")
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
    start = manifest.get("start_room")
    if not isinstance(start, str) or not ID_RE.fullmatch(start):
        raise WorldError("start_room must name a room ID in rooms.json.")
    rooms = docs["rooms.json"]
    exits = docs["exits.json"]
    if not isinstance(rooms, list) or not 1 <= len(rooms) <= 1000:
        raise WorldError("rooms.json must contain 1 to 1000 rooms.")
    if not isinstance(exits, list) or len(exits) > 5000:
        raise WorldError("exits.json must contain at most 5000 exits.")
    room_ids = set()
    clean_rooms = []
    for room in rooms:
        room = _object(room, "Room")
        local_id = room.get("id")
        if not isinstance(local_id, str) or not ID_RE.fullmatch(local_id) or local_id in room_ids:
            raise WorldError("Room IDs must be unique lowercase IDs.")
        room_ids.add(local_id)
        clean_rooms.append({"id": local_id, "name": _label(room.get("name"), "Room name", 2, 80),
                            "description": _label(room.get("description"), "Room description", 1, 4000)})
    if start not in room_ids:
        raise WorldError("start_room does not match a room in rooms.json.")
    clean_exits = []
    exit_names = set()
    exit_directions = set()
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
        clean_exits.append({"from": source, "to": target, "name": label, "direction": direction,
                            "key_item_id": key_item_id, "pick_difficulty": pick_difficulty,
                            "conditions": conditions, "conditions_message": " ".join(message.split())})
    content = docs.get(CONTENT_FILE, {})
    if not isinstance(content, dict) or set(content) - set(CONTENT_TABLES):
        raise WorldError("content.json must be an object of supported content tables.")
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
            elif table == "mud_npc_combat_profiles":
                if any(row.get(key) is not None and not _whole(row[key], 0, 100)
                       for key in ("accuracy", "evasion")):
                    raise WorldError("NPC combat accuracy and evasion must be whole numbers from 0 to 100.")
            elif table == "mud_quest_objectives":
                if row.get("objective_type") not in {"talk_npc", "visit_room", "possess_item", "defeat_npc", "deliver_item"} or not _whole(row.get("required_count", 1), 1, 100):
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
            elif table == "mud_room_discoveries":
                if not isinstance(row.get("clue"), str) or not 1 <= len(row["clue"].strip()) <= 500:
                    raise WorldError("Room discovery clue must be 1 to 500 characters.")
                if not _whole(row.get("difficulty", 0), 0, 200):
                    raise WorldError("Room discovery difficulty must be 0 to 200.")
            for column in CONDITION_COLUMNS.get(table, ()):
                try:
                    _condition_validator(column)(row.get(column))
                except ValueError as problem:
                    raise WorldError(f"Invalid {table} {column}: {problem}") from problem
    key_ids = {row.get("id") for row in content.get("mud_item_definitions", []) if row.get("item_type") == "key"}
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
    return {"id": world_id, "name": name, "author": author, "start_room": start,
            "rooms": clean_rooms, "exits": clean_exits, "content": content, "origins": origins,
            "skills": skills["skills"]}


def package_bytes(document):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = {key: document[key] for key in ("id", "name", "author", "start_room")}
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
    })


def _content_maps(document, installation_key):
    maps = {"rooms": {room["id"]: f"world:{document['id']}:{installation_key}:room:{room['id']}"
                      for room in document["rooms"]}}
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
    """Point item race/class requirements at bundled origin keys (or back to local keys)."""
    properties = json.loads(value) if isinstance(value, str) else value
    if isinstance(properties, dict) and properties.get("requirements"):
        properties = {**properties, "requirements": mud_conditions.remap_item_requirements(
            mud_conditions.validate_item_requirements(properties["requirements"]), mapper)}
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
                        ref_table = {"talk_npc": "mud_npc_definitions", "defeat_npc": "mud_npc_definitions",
                                     "visit_room": "rooms", "possess_item": "mud_item_definitions",
                                     "deliver_item": "mud_item_definitions"}.get(row.get("objective_type"))
                    else:
                        ref_table = "mud_item_definitions"
                    if ref_table is None:
                        raise WorldError("Quest objective has an unsupported type.")
                    value = _mapped(maps, ref_table, value, column)
                elif column == "skill_key":
                    # Trainers name a bundled skill by its local key or an installed skill.
                    value = (skill_map or {}).get(value, value)
                    if value not in installed_skills:
                        raise WorldError(f"Trainer skill {row[column]!r} is not bundled or installed.")
                elif column in CONDITION_COLUMNS.get(table, ()):
                    value = json.dumps(mud_conditions.remap(_condition_validator(column)(value), condition_mapper),
                                       ensure_ascii=False, separators=(",", ":"))
                elif table == "mud_item_definitions" and column == "properties_json":
                    value = _remap_item_properties(value, condition_mapper)
                elif column.endswith("_json"):
                    if isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    elif not isinstance(value, str):
                        raise WorldError(f"{column} must be JSON text or an object.")
                elif not isinstance(value, (str, int, float, type(None))):
                    raise WorldError(f"{table}.{column} must be a scalar value.")
                fields[column] = value
            if table in {"mud_item_definitions", "mud_npc_definitions", "mud_conversations", "mud_quests"}:
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


def set_availability(database, world_id, mode="preserve", selected=None):
    from . import character_creation, mud_skills
    chosen = availability_choice(database, world_id, mode, selected)
    if mode == "preserve":
        return
    mud_skills.set_enabled(database, chosen["skills"])
    entries = character_creation.catalog(database)
    for kind in character_creation.KINDS:
        for entry in entries[kind]:
            enabled = int(entry["key"] in chosen[kind])
            if entry["imported"]:
                database.execute("UPDATE mud_origin_definitions SET enabled=? WHERE kind=? AND key=?",
                                 (enabled, kind, entry["key"]))
            else:
                database.execute(
                    "INSERT INTO mud_origin_definitions(kind,key,enabled) VALUES(?,?,?) "
                    "ON CONFLICT(kind,key) DO UPDATE SET enabled=excluded.enabled",
                    (kind, entry["key"], enabled))


def _skill_choice(database, world_id, mode, selected):
    """Skills follow the race/class mode, but worlds without bundled skills keep the current set."""
    from . import mud_skills
    entries = mud_skills.catalog(database)
    available = {entry["key"] for entry in entries}
    current = {entry["key"] for entry in entries if entry["enabled"]}
    bundled = world_origin_keys(database, world_id)["skills"] & available
    if mode == "manual":
        # Forms that predate skills post no skill choices; keep the current set then.
        chosen = set((selected or {}).get("skills") or current)
        if chosen - available:
            raise WorldError("Choose only installed skills.")
    elif mode == "only" and bundled:
        chosen = bundled
    elif mode in {"only", "combine"}:
        chosen = current | bundled
    else:
        chosen = current
    station = database.execute(
        "SELECT mud_enabled,mud_traditional_enabled,mud_procedural_enabled FROM station LIMIT 1").fetchone()
    if station and any(station):
        try:
            mud_skills.require_available(database, chosen)
        except ValueError as error:
            raise WorldError(str(error)) from error
    return chosen


def availability_choice(database, world_id, mode="preserve", selected=None):
    from . import character_creation
    if mode not in {"only", "combine", "manual"}:
        if mode != "preserve":
            raise WorldError("Choose a valid race, class, and skill availability mode.")
    entries = character_creation.catalog(database)
    available = {kind: {entry["key"] for entry in entries[kind]} for kind in character_creation.KINDS}
    world_keys = world_origin_keys(database, world_id)
    bundled = {kind: world_keys[kind] & available[kind] for kind in character_creation.KINDS}
    current = {kind: {entry["key"] for entry in entries[kind] if entry["enabled"]}
               for kind in character_creation.KINDS}
    if mode == "preserve":
        chosen = current
    elif mode == "only":
        chosen = bundled
    elif mode == "combine":
        chosen = {kind: current[kind] | bundled[kind] for kind in character_creation.KINDS}
    else:
        chosen = {kind: set((selected or {}).get(kind, ())) for kind in character_creation.KINDS}
        if any(chosen[kind] - available[kind] for kind in character_creation.KINDS):
            raise WorldError("Choose only installed races and classes.")
    station = database.execute(
        "SELECT mud_enabled,mud_traditional_enabled,mud_procedural_enabled FROM station LIMIT 1").fetchone()
    if station and any(station) and any(not chosen[kind] for kind in character_creation.KINDS):
        raise WorldError("Enable at least one race and one class while a MUD mode is enabled.")
    chosen["skills"] = _skill_choice(database, world_id, mode, selected)
    return chosen


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


def install(database, payload, *, origin_mode="preserve", selected=None):
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
        database.execute("INSERT INTO mud_world_packages(id,name,author,start_room_id,imported) VALUES(?,?,?,?,1)",
                         (world_id, document["name"], document["author"], prefix + document["start_room"]))
        skill_map = _install_skills(database, document)
        for room in document["rooms"]:
            database.execute("INSERT INTO mud_world_rooms(id,name,description,world_id) VALUES(?,?,?,?)",
                             (prefix + room["id"], room["name"], room["description"], world_id))
        for ordinal, item in enumerate(document["exits"]):
            database.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction,pick_difficulty,"
                             "conditions_json,conditions_message) VALUES(?,?,?,?,?,?,?,?)",
                             (f"world:{world_id}:{installation_key}:exit:{ordinal}", prefix + item["from"],
                              prefix + item["to"], item["name"], item["direction"], item["pick_difficulty"],
                              "[]", item["conditions_message"]))
        maps = _insert_content(database, document, installation_key, skill_map)
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
        _install_origins(database, document, skill_map)
        set_availability(database, world_id, origin_mode, selected)
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


def _condition_validator(column):
    return (mud_conditions.validate_outcomes if column == "outcomes_json"
            else mud_conditions.validate_conditions)


def _stored_conditions(value, column):
    """Stored condition/outcome JSON as a normalized list; unreadable values export as []."""
    try:
        return _condition_validator(column)(value)
    except ValueError:
        return []


def _export_content(database, world_id, room_map):
    roots = {"mud_item_definitions", "mud_conversations", "mud_npc_definitions", "mud_quests"}
    all_placed_items = [dict(row) for row in database.execute(
        "SELECT id,definition_id,room_id,container_id FROM mud_item_instances "
        "WHERE spawn_source_id IS NULL"
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
    needed_items |= {row["item_definition_id"] for row in database.execute(
        "SELECT item_definition_id FROM mud_room_discoveries WHERE room_id IN (SELECT id FROM mud_world_rooms WHERE world_id=?) AND item_definition_id IS NOT NULL", (world_id,))}
    needed_items |= {row["key_item_id"] for row in database.execute(
        "SELECT e.key_item_id FROM mud_world_exits e JOIN mud_world_rooms r ON r.id=e.from_room_id WHERE r.world_id=? AND e.key_item_id IS NOT NULL", (world_id,))}
    needed_items |= {row["item_definition_id"] for row in database.execute(
        "SELECT s.item_definition_id FROM mud_shop_stock s JOIN mud_npc_definitions n ON n.id=s.npc_definition_id WHERE n.world_id=?", (world_id,))}
    needed_npcs = {row["definition_id"] for row in database.execute(
        "SELECT definition_id FROM mud_npc_instances WHERE room_id IN "
        "(SELECT id FROM mud_world_rooms WHERE world_id=?)", (world_id,)
    )}
    needed_conversations = {row["conversation_id"] for row in database.execute(
        "SELECT conversation_id FROM mud_npc_definitions WHERE id IN "
        "(SELECT definition_id FROM mud_npc_instances WHERE room_id IN "
        "(SELECT id FROM mud_world_rooms WHERE world_id=?))", (world_id,)
    ) if row["conversation_id"]}
    for row in database.execute(
        "SELECT r.conditions_json,r.outcomes_json FROM mud_dialogue_responses r "
        "JOIN mud_dialogue_nodes n ON n.id=r.node_id JOIN mud_conversations c ON c.id=n.conversation_id "
        "WHERE c.world_id=? UNION ALL SELECT conditions_json,'[]' FROM mud_quests WHERE world_id=? "
        "UNION ALL SELECT e.conditions_json,'[]' FROM mud_world_exits e "
        "JOIN mud_world_rooms r ON r.id=e.from_room_id WHERE r.world_id=?",
        (world_id, world_id, world_id),
    ):
        needed_items |= {ref for kind, ref in mud_conditions.references(
            _stored_conditions(row["conditions_json"], "conditions_json"),
            _stored_conditions(row["outcomes_json"], "outcomes_json")) if kind == "item"}
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
        elif table in {"mud_npc_combat_profiles", "mud_npc_equipment", "mud_npc_combat_rewards", "mud_npc_shops",
                       "mud_shop_stock", "mud_npc_trainers"}:
            rows = [row for row in rows if row["npc_definition_id"] in selected["mud_npc_definitions"]]
        elif table in {"mud_quest_objectives", "mud_quest_rewards", "mud_quest_givers"}:
            rows = [row for row in rows if row["quest_id"] in selected["mud_quests"]]
        elif table == "mud_dialogue_responses":
            rows = [row for row in rows if row["node_id"] in selected["mud_dialogue_nodes"]]
        elif table == "mud_item_instances":
            rows = [row for row in rows if row["id"] in placed_item_ids]
        elif table in {"mud_item_spawns", "mud_room_discoveries"}:
            rows = [row for row in rows if row["room_id"] in room_map]
        elif table == "mud_npc_instances":
            rows = [row for row in rows if row["room_id"] in room_map]
        selected[table] = {row["id"]: row for row in rows} if table in ID_TABLES else rows

    prefixes = {"mud_item_definitions": "item", "mud_conversations": "conversation",
                "mud_dialogue_nodes": "node", "mud_quests": "quest",
                "mud_npc_definitions": "npc", "mud_npc_combat_rewards": "combat-reward",
                "mud_quest_objectives": "objective", "mud_quest_rewards": "quest-reward",
                "mud_dialogue_responses": "response", "mud_item_instances": "placed-item",
                "mud_npc_instances": "placed-npc", "mud_shop_stock": "stock", "mud_npc_trainers": "trainer",
                "mud_item_spawns": "spawn", "mud_room_discoveries": "discovery"}
    maps = {"rooms": room_map}
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
                elif column in REFERENCE_TABLE:
                    ref_table = REFERENCE_TABLE[column]
                    if ref_table is None:
                        ref_table = ("mud_item_definitions" if table in {"mud_item_instances", "mud_item_spawns"}
                                     else "mud_npc_definitions")
                    value = _mapped(maps, ref_table, value, column)
                elif column == "target_id":
                    ref_table = ({"talk_npc": "mud_npc_definitions", "defeat_npc": "mud_npc_definitions",
                                  "visit_room": "rooms", "possess_item": "mud_item_definitions",
                                  "deliver_item": "mud_item_definitions"}.get(row["objective_type"])
                                 if table == "mud_quest_objectives" else "mud_item_definitions")
                    value = _mapped(maps, ref_table, value, column)
                elif column in CONDITION_COLUMNS.get(table, ()):
                    # Origins and skills are renamed in export() once their local keys are known.
                    value = mud_conditions.remap(
                        _stored_conditions(value, column),
                        lambda kind, ref: ref if kind not in CONDITION_TABLES
                        else _mapped(maps, CONDITION_TABLES[kind], ref, f"{kind} condition"))
                elif column.endswith("_json") and isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError:
                        pass
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
    rooms = database.execute("SELECT id,name,description FROM mud_world_rooms WHERE world_id=? ORDER BY id",
                             (world_id,)).fetchall()
    if not rooms:
        raise WorldError("World has no rooms to export.")
    ids = {room["id"]: f"room-{index}" for index, room in enumerate(rooms, 1)}
    if world["start_room_id"] not in ids:
        raise WorldError("World starting room is missing.")
    exits = database.execute(
        "SELECT e.from_room_id,e.to_room_id,e.name,e.direction,e.key_item_id,e.pick_difficulty,"
        "e.conditions_json,e.conditions_message FROM mud_world_exits e "
        "JOIN mud_world_rooms r ON r.id=e.from_room_id WHERE r.world_id=? ORDER BY e.id", (world_id,)
    ).fetchall()
    content, maps = _export_content(database, world_id, ids)
    document = {"id": world_id if world_id != "stationnet" else "my-world",
                "name": world["name"], "author": world["author"],
                "start_room": ids[world["start_room_id"]],
                "rooms": [{"id": ids[room["id"]], "name": room["name"],
                           "description": room["description"]} for room in rooms],
                "exits": [{"from": ids[row["from_room_id"]], "to": ids[row["to_room_id"]],
                           "name": row["name"], "direction": row["direction"],
                           **({"key_item_id": _mapped(maps, "mud_item_definitions", row["key_item_id"], "key_item_id")}
                              if row["key_item_id"] else {}),
                           **({"pick_difficulty": row["pick_difficulty"]} if row["pick_difficulty"] else {}),
                           **({"conditions": mud_conditions.validate_conditions(row["conditions_json"])}
                              if row["conditions_json"] not in (None, "", "[]") else {}),
                           **({"conditions_message": row["conditions_message"]} if row["conditions_message"] else {})}
                          for row in exits if row["to_room_id"] in ids],
                "content": content}
    from . import character_creation
    entries = character_creation.catalog(database)
    installed = {(kind, entry["key"]): entry for kind in character_creation.KINDS
                 for entry in entries[kind] if entry["imported"]}
    associations = {(row["kind"], row["origin_key"]): row["local_key"] for row in database.execute(
        "SELECT kind,origin_key,local_key FROM mud_world_origins WHERE world_id=?", (world_id,))}
    chosen = set(associations) if origin_keys is None else set(origin_keys)
    if chosen - set(installed):
        raise WorldError("Choose only installed custom races and classes to export.")
    from . import mud_skills
    skill_entries = {entry["key"]: entry["definition"] for entry in mud_skills.catalog(database) if entry["imported"]}
    skill_links = {row["skill_key"]: row["local_key"] for row in database.execute(
        "SELECT skill_key,local_key FROM mud_world_skills WHERE world_id=?", (world_id,))}
    wanted = set(skill_links) if skill_keys is None else set(skill_keys)
    if wanted - set(skill_entries):
        raise WorldError("Choose only installed custom skills to export.")
    trainer_skills = {row["skill_key"] for row in content.get("mud_npc_trainers", [])}
    wanted |= trainer_skills & set(skill_entries)
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
    for row in content.get("mud_npc_trainers", []):
        row["skill_key"] = local_skill.get(row["skill_key"], row["skill_key"])

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
    return package_bytes(document)


def _export_exit_reference(maps, local_reference):
    """Exit conditions name content by installed ID; export them by package-local ID."""
    def mapper(kind, value):
        if kind in ORIGIN_KINDS or kind == "skill":
            return local_reference(kind, value)
        return _mapped(maps, CONDITION_TABLES[kind], value, f"exit {kind} condition")
    return mapper


def activation_impact(database, world_id, *, origin_mode="preserve", selected=None):
    """Read-only impact and state fingerprint used by the confirmation form."""
    from . import character_creation
    world = database.execute("SELECT id,name,start_room_id,active FROM mud_world_packages WHERE id=?",
                             (world_id,)).fetchone()
    if world is None:
        raise WorldError("World was not found.")
    if database.execute("SELECT 1 FROM mud_world_rooms WHERE id=? AND world_id=?",
                        (world["start_room_id"], world_id)).fetchone() is None:
        raise WorldError("World starting room is missing.")
    chosen = availability_choice(database, world_id, origin_mode, selected)
    current = database.execute("SELECT id,name FROM mud_world_packages WHERE active=1").fetchone()
    definitions = character_creation.catalog(database, include_removed=True)
    names = {kind: {e["key"]: e["definition"]["name"] for e in definitions[kind]}
             for kind in character_creation.KINDS}
    characters = []
    for row in database.execute("SELECT user_id,display_name,race_key,class_key,creation_state "
                                "FROM mud_characters ORDER BY user_id"):
        if row["creation_state"] == "pending":
            continue
        missing = [kind for kind, column in (("races", "race_key"), ("classes", "class_key"))
                   if row["creation_state"] == "complete" and row[column] is not None
                   and row[column] not in chosen[kind]]
        characters.append({"user_id": row["user_id"], "name": row["display_name"],
                           "missing": missing, "race": names["races"].get(row["race_key"], row["race_key"]),
                           "class": names["classes"].get(row["class_key"], row["class_key"])})
    relocated = database.execute(
        "SELECT COUNT(*) FROM mud_player_locations WHERE mode='traditional' AND location_type='world_room'").fetchone()[0]
    archive = database.execute("SELECT id,created_at FROM mud_campaign_archives WHERE world_id=? "
                               "ORDER BY id DESC LIMIT 1", (world_id,)).fetchone()
    result = {"world_id": world_id, "world_name": world["name"],
              "current_world": current["name"] if current else None,
              "current_world_id": current["id"] if current else None,
              "world_changed": not bool(world["active"]), "characters": characters,
              "relocated": relocated, "archive": dict(archive) if archive else None,
              "chosen": {kind: sorted(chosen[kind]) for kind in (*character_creation.KINDS, "skills")}}
    result["fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def activate(database, world_id, *, origin_mode="preserve", selected=None,
             campaign="migrate", archive_id=None, fingerprint=None):
    from . import mud, mud_compat
    with database:
        database.execute("BEGIN IMMEDIATE")
        impact = activation_impact(database, world_id, origin_mode=origin_mode, selected=selected)
        if fingerprint is not None and fingerprint != impact["fingerprint"]:
            raise WorldError("The world impact changed. Preview and confirm again.")
        if campaign not in ("migrate", "new", "restore"):
            raise WorldError("Choose a valid campaign option.")
        if campaign != "migrate" and not impact["world_changed"]:
            raise WorldError("Choose another world to start or restore a campaign.")
        if campaign == "restore" and (not impact["archive"] or
                                       str(impact["archive"]["id"]) != str(archive_id)):
            raise WorldError("That campaign archive is no longer available.")
        world = database.execute("SELECT * FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
        if world is None:
            raise WorldError("World was not found.")
        set_availability(database, world_id, origin_mode, selected)
        if world["active"]:
            return False
        start = database.execute("SELECT id,name FROM mud_world_rooms WHERE id=? AND world_id=?",
                                 (world["start_room_id"], world_id)).fetchone()
        if start is None:
            raise WorldError("World starting room is missing.")
        affected = (database.execute(
            "SELECT user_id FROM mud_player_locations WHERE mode='traditional' AND location_type='world_room'"
        ).fetchall() if campaign == "migrate" else [])
        if campaign in ("new", "restore") and impact["current_world_id"]:
            mud_compat.archive_campaign(database, impact["current_world_id"], impact["current_world"])
        if campaign == "restore":
            mud_compat.restore_campaign(database, int(archive_id))
        database.execute("UPDATE mud_world_rooms SET is_start=0 WHERE is_start=1")
        database.execute("UPDATE mud_world_rooms SET is_start=1 WHERE id=?", (start["id"],))
        database.execute("UPDATE mud_world_packages SET active=0 WHERE active=1")
        database.execute("UPDATE mud_world_packages SET active=1 WHERE id=?", (world_id,))
        for row in affected:
            user_id = row["user_id"]
            old = database.execute("SELECT location_key FROM mud_player_locations WHERE user_id=?", (user_id,)).fetchone()
            if old:
                mud._leave_combat(database, user_id, "room", old["location_key"])
            mud._set_player_location(database, user_id, "traditional", "world_room", start["id"])
            mud._insert_event(database, mud.EventDraft(
                f"The active MUD world changed to {world['name']}. You have been moved to {start['name']}.",
                event_type="movement", recipient_ids=(user_id,)), default_user_id=user_id)
        if campaign == "migrate" and impact["world_changed"]:
            for character in impact["characters"]:
                mud._insert_event(database, mud.EventDraft(
                    f"The active MUD world changed to {world['name']}. "
                    + ("Choose a replacement race or class before continuing."
                       if character["missing"] else "Your character progress is preserved."),
                    event_type="character", recipient_ids=(character["user_id"],)),
                    default_user_id=character["user_id"])
        if campaign in ("new", "restore"):
            for row in database.execute("SELECT user_id FROM mud_characters"):
                mud._insert_event(database, mud.EventDraft(
                    (f"A new MUD campaign has begun in {world['name']}. Create a new character to enter."
                     if campaign == "new" else
                     f"Your archived MUD character was restored for {world['name']}."),
                    event_type="character", recipient_ids=(row["user_id"],)),
                    default_user_id=row["user_id"])
    return True


def removal_origins(database, world_id):
    from . import character_creation, mud_skills
    linked = world_origin_keys(database, world_id)
    result = character_creation.removable_associations(database, {**linked, "world_id": world_id})
    result["skills"] = mud_skills.removable_associations(database, linked["skills"], world_id=world_id)
    return result


def remove(database, world_id, *, remove_origins=False):
    from . import character_creation, mud_skills
    world = database.execute("SELECT imported,active FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
    if world is None or not world["imported"]:
        raise WorldError("Only installed custom worlds can be removed.")
    if world["active"]:
        raise WorldError("Select another active world before removing this one.")
    path = directory() / f"{world_id}.zip"
    staged = path.with_name(path.name + ".removing-" + uuid.uuid4().hex)
    if path.exists():
        path.rename(staged)
    try:
        with database:
            database.execute("BEGIN IMMEDIATE")
            world = database.execute("SELECT imported,active FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
            if world is None or not world["imported"]:
                raise WorldError("Only installed custom worlds can be removed.")
            if world["active"]:
                raise WorldError("Select another active world before removing this one.")
            associated = removal_origins(database, world_id)
            linked = world_origin_keys(database, world_id)
            selected = {kind: {entry["key"] for entry in associated[kind]}
                        for kind in (*character_creation.KINDS, "skills")}
            # Keep definitions referenced by a player's possessions or quest history,
            # while separating them from a later installation with the same world ID.
            retired_id = f"retired:{world_id}:{uuid.uuid4().hex}"
            for table in ("mud_item_definitions", "mud_npc_definitions", "mud_conversations", "mud_quests"):
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
            database.execute("DELETE FROM mud_world_rooms WHERE world_id=?", (world_id,))
            database.execute("DELETE FROM mud_world_packages WHERE id=?", (world_id,))
            if remove_origins and any(selected[kind] for kind in character_creation.KINDS):
                character_creation.remove_origins(database, selected)
            # Skills go last so the origins removed above no longer depend on them.
            if remove_origins and selected["skills"]:
                selected["skills"] = {entry["key"] for entry in
                                      mud_skills.remove_linked(database, associated["skills"])}
            preserved = {kind: len(linked[kind]) - (len(selected[kind]) if remove_origins else 0)
                         for kind in selected}
            restored = character_creation.restore_missing_defaults(database)
            if mud_skills.restore_missing_defaults(database):
                restored.append("skills")
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
