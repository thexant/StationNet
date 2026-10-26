"""Portable, file-authored traditional MUD maps."""
from io import BytesIO
import json
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
    "mud_npc_combat_rewards": ("id", "npc_definition_id", "reward_type", "target_id", "amount"),
    "mud_quest_objectives": ("id", "quest_id", "objective_type", "target_id", "description", "required_count", "sort_order"),
    "mud_quest_rewards": ("id", "quest_id", "reward_type", "target_id", "amount"),
    "mud_quest_givers": ("quest_id", "npc_definition_id"),
    "mud_dialogue_responses": ("id", "node_id", "response_text", "next_node_id", "accept_quest_id", "required_state_key", "required_state_value", "state_key", "state_value", "sort_order"),
    "mud_item_instances": ("id", "definition_id", "room_id", "container_id", "is_open", "state_json"),
    "mud_npc_instances": ("id", "definition_id", "room_id", "state_json"),
}
ID_TABLES = {table for table, columns in CONTENT_TABLES.items() if "id" in columns}
REFERENCE_TABLE = {
    "conversation_id": "mud_conversations", "node_id": "mud_dialogue_nodes",
    "next_node_id": "mud_dialogue_nodes", "accept_quest_id": "mud_quests",
    "prerequisite_quest_id": "mud_quests", "npc_definition_id": "mud_npc_definitions",
    "item_definition_id": "mud_item_definitions", "quest_id": "mud_quests",
    "definition_id": None, "room_id": "rooms", "container_id": "mud_item_instances",
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
    guide = target / "README.md"
    if not guide.exists():
        guide.write_bytes((Path(__file__).with_name("mud_world_templates") / "README.md").read_bytes())


def _object(value, label):
    if not isinstance(value, dict):
        raise WorldError(f"{label} must be an object.")
    return value


def _label(value, label, minimum, maximum):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise WorldError(f"{label} must contain {minimum} to {maximum} characters.")
    return value.strip()


def read_package(payload):
    """Read a bounded ZIP without extracting paths or accepting hidden extras."""
    if not payload or len(payload) > MAX_ZIP_BYTES:
        raise WorldError("World ZIP must be between 1 byte and 16 MB.")
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            if set(names) not in (FILES, FILES | {CONTENT_FILE}) or len(names) != len(set(names)):
                raise WorldError("World ZIP must contain manifest.json, rooms.json, and exits.json at its root, with optional content.json.")
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
        clean_exits.append({"from": source, "to": target, "name": label, "direction": direction})
    content = docs.get(CONTENT_FILE, {})
    if not isinstance(content, dict) or set(content) - set(CONTENT_TABLES):
        raise WorldError("content.json must be an object of supported content tables.")
    for table, rows in content.items():
        if not isinstance(rows, list) or len(rows) > 5000 or any(not isinstance(row, dict) for row in rows):
            raise WorldError(f"{table} must be an array of at most 5000 objects.")
    return {"id": world_id, "name": name, "author": author, "start_room": start,
            "rooms": clean_rooms, "exits": clean_exits, "content": content}


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
                        ref_table = ("mud_item_definitions" if table == "mud_item_instances"
                                     else "mud_npc_definitions")
                    if table == "mud_quests" and column == "prerequisite_quest_id":
                        continue
                    value = _mapped(maps, ref_table, value, column)
                elif column == "target_id":
                    if table == "mud_quest_objectives":
                        if not isinstance(row.get("objective_type"), str):
                            raise WorldError("Quest objective has an unsupported type.")
                        ref_table = {"talk_npc": "mud_npc_definitions", "visit_room": "rooms",
                                     "possess_item": "mud_item_definitions"}.get(row.get("objective_type"))
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
    for row in document["content"].get("mud_quests", []):
        if row.get("prerequisite_quest_id"):
            database.execute("UPDATE mud_quests SET prerequisite_quest_id=? WHERE id=?",
                             (_mapped(maps, "mud_quests", row["prerequisite_quest_id"], "prerequisite_quest_id"),
                              _mapped(maps, "mud_quests", row["id"], "id")))


def install(database, payload):
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
        _insert_content(database, document, installation_key)
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
        "SELECT id,definition_id,room_id,container_id FROM mud_item_instances"
    )]
    placed_item_ids = {row["id"] for row in all_placed_items if row["room_id"] in room_map}
    while True:
        expanded = placed_item_ids | {row["id"] for row in all_placed_items
                                      if row["container_id"] in placed_item_ids}
        if expanded == placed_item_ids:
            break
        placed_item_ids = expanded
    needed_items = {row["definition_id"] for row in all_placed_items if row["id"] in placed_item_ids}
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
        elif table in {"mud_npc_combat_profiles", "mud_npc_equipment", "mud_npc_combat_rewards"}:
            rows = [row for row in rows if row["npc_definition_id"] in selected["mud_npc_definitions"]]
        elif table in {"mud_quest_objectives", "mud_quest_rewards", "mud_quest_givers"}:
            rows = [row for row in rows if row["quest_id"] in selected["mud_quests"]]
        elif table == "mud_dialogue_responses":
            rows = [row for row in rows if row["node_id"] in selected["mud_dialogue_nodes"]]
        elif table == "mud_item_instances":
            rows = [row for row in rows if row["id"] in placed_item_ids]
        elif table == "mud_npc_instances":
            rows = [row for row in rows if row["room_id"] in room_map]
        selected[table] = {row["id"]: row for row in rows} if table in ID_TABLES else rows

    prefixes = {"mud_item_definitions": "item", "mud_conversations": "conversation",
                "mud_dialogue_nodes": "node", "mud_quests": "quest",
                "mud_npc_definitions": "npc", "mud_npc_combat_rewards": "combat-reward",
                "mud_quest_objectives": "objective", "mud_quest_rewards": "quest-reward",
                "mud_dialogue_responses": "response", "mud_item_instances": "placed-item",
                "mud_npc_instances": "placed-npc"}
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
                        ref_table = ("mud_item_definitions" if table == "mud_item_instances"
                                     else "mud_npc_definitions")
                    value = _mapped(maps, ref_table, value, column)
                elif column == "target_id":
                    ref_table = ({"talk_npc": "mud_npc_definitions", "visit_room": "rooms",
                                  "possess_item": "mud_item_definitions"}.get(row["objective_type"])
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
    return content


def export(database, world_id):
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
        "SELECT e.from_room_id,e.to_room_id,e.name,e.direction FROM mud_world_exits e "
        "JOIN mud_world_rooms r ON r.id=e.from_room_id WHERE r.world_id=? ORDER BY e.id", (world_id,)
    ).fetchall()
    document = {"id": world_id if world_id != "stationnet" else "my-world",
                "name": world["name"], "author": world["author"],
                "start_room": ids[world["start_room_id"]],
                "rooms": [{"id": ids[room["id"]], "name": room["name"],
                           "description": room["description"]} for room in rooms],
                "exits": [{"from": ids[row["from_room_id"]], "to": ids[row["to_room_id"]],
                           "name": row["name"], "direction": row["direction"]}
                          for row in exits if row["to_room_id"] in ids],
                "content": _export_content(database, world_id, ids)}
    return package_bytes(document)


def activate(database, world_id):
    from . import mud
    with database:
        database.execute("BEGIN IMMEDIATE")
        world = database.execute("SELECT * FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
        if world is None:
            raise WorldError("World was not found.")
        if world["active"]:
            return False
        start = database.execute("SELECT id,name FROM mud_world_rooms WHERE id=? AND world_id=?",
                                 (world["start_room_id"], world_id)).fetchone()
        if start is None:
            raise WorldError("World starting room is missing.")
        affected = database.execute(
            "SELECT user_id FROM mud_player_locations WHERE mode='traditional' AND location_type='world_room'"
        ).fetchall()
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
