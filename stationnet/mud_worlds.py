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


MAX_ZIP_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
FILES = {"manifest.json", "rooms.json", "exits.json"}
CONTENT_FILE = "content.json"
ORIGINS_FILE = "origins.json"
# Order follows foreign-key dependencies. Only these authored columns cross
# the package boundary; player state and generated dungeon data never do.
CONTENT_TABLES = {
    "mud_item_definitions": ("id", "name", "description", "item_type", "properties_json", "behaviors_json", "equipment_slot"),
    "mud_conversations": ("id", "name"),
    "mud_dialogue_nodes": ("id", "conversation_id", "node_key", "body", "is_start"),
    "mud_quests": ("id", "name", "description", "prerequisite_quest_id"),
    "mud_npc_definitions": ("id", "name", "description", "conversation_id", "properties_json", "behaviors_json"),
    "mud_npc_combat_profiles": ("npc_definition_id", "maximum_health", "attack", "defense", "experience_reward", "respawn_seconds"),
    "mud_npc_equipment": ("npc_definition_id", "equipment_slot", "item_definition_id"),
    "mud_npc_combat_rewards": ("id", "npc_definition_id", "reward_type", "target_id", "amount", "chance_percent"),
    "mud_npc_shops": ("npc_definition_id", "buys_items", "sell_percent"),
    "mud_shop_stock": ("id", "npc_definition_id", "item_definition_id", "price", "quantity"),
    "mud_quest_objectives": ("id", "quest_id", "objective_type", "target_id", "description", "required_count", "sort_order"),
    "mud_quest_rewards": ("id", "quest_id", "reward_type", "target_id", "amount"),
    "mud_quest_givers": ("quest_id", "npc_definition_id"),
    "mud_dialogue_responses": ("id", "node_id", "response_text", "next_node_id", "accept_quest_id", "deliver_item_id", "required_state_key", "required_state_value", "state_key", "state_value", "sort_order"),
    "mud_item_instances": ("id", "definition_id", "room_id", "container_id", "is_open", "state_json"),
    "mud_item_spawns": ("id", "definition_id", "room_id", "respawn_seconds"),
    "mud_room_discoveries": ("id", "room_id", "clue", "item_definition_id"),
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
}


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
            if not FILES <= set(names) or set(names) - FILES - {CONTENT_FILE, ORIGINS_FILE} or len(names) != len(set(names)):
                raise WorldError("World ZIP must contain manifest.json, rooms.json, and exits.json at its root, with optional content.json and origins.json.")
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
        clean_exits.append({"from": source, "to": target, "name": label, "direction": direction,
                            "key_item_id": key_item_id})
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
            elif table == "mud_quest_objectives":
                if row.get("objective_type") not in {"talk_npc", "visit_room", "possess_item", "defeat_npc", "deliver_item"} or not _whole(row.get("required_count", 1), 1, 100):
                    raise WorldError("Quest objective type or required_count is invalid.")
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
            elif table == "mud_item_spawns":
                if not _whole(row.get("respawn_seconds"), 5, 86400):
                    raise WorldError("Item respawn_seconds must be 5 to 86,400.")
            elif table == "mud_room_discoveries":
                if not isinstance(row.get("clue"), str) or not 1 <= len(row["clue"].strip()) <= 500:
                    raise WorldError("Room discovery clue must be 1 to 500 characters.")
    key_ids = {row.get("id") for row in content.get("mud_item_definitions", []) if row.get("item_type") == "key"}
    if any(item["key_item_id"] and item["key_item_id"] not in key_ids for item in clean_exits):
        raise WorldError("Locked exits must reference a key item definition in content.json.")
    origins = docs.get(ORIGINS_FILE, {})
    if not isinstance(origins, dict) or set(origins) - {"races", "classes"}:
        raise WorldError("origins.json must contain races and/or classes arrays.")
    for kind, definitions in origins.items():
        if not isinstance(definitions, list) or len(definitions) > 100 or any(not isinstance(item, dict) for item in definitions):
            raise WorldError(f"origins.json {kind} must contain at most 100 definition objects.")
    return {"id": world_id, "name": name, "author": author, "start_room": start,
            "rooms": clean_rooms, "exits": clean_exits, "content": content, "origins": origins}


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


def _insert_content(database, document, installation_key):
    maps = _content_maps(document, installation_key)
    world_id = document["id"]
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
                    if table == "mud_quests" and column == "prerequisite_quest_id":
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
    return maps


def bundled_key(world_id, kind, local_key):
    """A repeatable global key independent of the import order or station."""
    digest = hashlib.sha256(f"{world_id}:{kind}:{local_key}".encode("utf-8")).hexdigest()[:32]
    return f"w-{digest}"


def _install_origins(database, document):
    from . import character_creation

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
                    kind, {**source, "key": key}, database, check_key=False)
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
    return {kind: {row["origin_key"] for row in database.execute(
        "SELECT origin_key FROM mud_world_origins WHERE world_id=? AND kind=?", (world_id, kind))}
        for kind in ("races", "classes")}


def set_availability(database, world_id, mode="preserve", selected=None):
    from . import character_creation
    chosen = availability_choice(database, world_id, mode, selected)
    if mode == "preserve":
        return
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


def availability_choice(database, world_id, mode="preserve", selected=None):
    from . import character_creation
    if mode not in {"only", "combine", "manual"}:
        if mode != "preserve":
            raise WorldError("Choose a valid race and class availability mode.")
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
    return chosen


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
        for room in document["rooms"]:
            database.execute("INSERT INTO mud_world_rooms(id,name,description,world_id) VALUES(?,?,?,?)",
                             (prefix + room["id"], room["name"], room["description"], world_id))
        for ordinal, item in enumerate(document["exits"]):
            database.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) VALUES(?,?,?,?,?)",
                             (f"world:{world_id}:{installation_key}:exit:{ordinal}", prefix + item["from"],
                              prefix + item["to"], item["name"], item["direction"]))
        maps = _insert_content(database, document, installation_key)
        for ordinal, item in enumerate(document["exits"]):
            if item["key_item_id"]:
                database.execute("UPDATE mud_world_exits SET key_item_id=? WHERE id=?",
                                 (_mapped(maps, "mud_item_definitions", item["key_item_id"], "key_item_id"),
                                  f"world:{world_id}:{installation_key}:exit:{ordinal}"))
        _install_origins(database, document)
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
        elif table in {"mud_npc_combat_profiles", "mud_npc_equipment", "mud_npc_combat_rewards", "mud_npc_shops", "mud_shop_stock"}:
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
                "mud_npc_instances": "placed-npc", "mud_shop_stock": "stock",
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


def export(database, world_id, *, origin_keys=None):
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
        "SELECT e.from_room_id,e.to_room_id,e.name,e.direction,e.key_item_id FROM mud_world_exits e "
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
                              if row["key_item_id"] else {})}
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
    origins = {kind: [] for kind in character_creation.KINDS}
    for kind, key in sorted(chosen):
        definition = dict(installed[(kind, key)]["definition"])
        definition["key"] = associations.get((kind, key), key)
        origins[kind].append(definition)
    if any(origins.values()):
        document["origins"] = origins
    return package_bytes(document)


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
              "chosen": {kind: sorted(chosen[kind]) for kind in character_creation.KINDS}}
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


def remove(database, world_id):
    path = directory() / f"{world_id}.zip"
    with database:
        database.execute("BEGIN IMMEDIATE")
        world = database.execute("SELECT imported,active FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
        if world is None or not world["imported"]:
            raise WorldError("Only installed custom worlds can be removed.")
        if world["active"]:
            raise WorldError("Select another active world before removing this one.")
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
    path.unlink(missing_ok=True)
