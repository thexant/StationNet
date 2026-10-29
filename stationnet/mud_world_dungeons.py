"""Procedural dungeon templates owned by an authored world, and the entrances that launch them.

Templates reuse the dungeon engine's schema and generator unchanged. A world
template is stored without its own ID or version: each row is given a unique
engine theme ID, and every edit bumps the revision so runs already generated
keep the immutable snapshot they were built from.
"""
import json
import re
import secrets
import uuid

from . import dungeons, mud_conditions, mud_effects


KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
DUNGEONS_FILE = "dungeons.json"
DEFAULT_PARTY = 4
MAX_PARTY = 20
MAX_TEMPLATES = 50
MAX_ENTRANCES = 200


class WorldDungeonError(ValueError):
    pass


def new_theme_id(local_key):
    """A station-unique engine theme ID; reinstalling a world never reuses one."""
    return f"w-{secrets.token_hex(4)}-{local_key}"


def effective_template(row):
    template = json.loads(row["template_json"])
    template["id"] = row["theme_id"]
    template["version"] = str(row["revision"])
    return template


def parse_template(text):
    if isinstance(text, dict):
        return text
    if not isinstance(text, str) or not text.strip():
        raise WorldDungeonError("Paste the dungeon template JSON.")
    if len(text.encode("utf-8")) > dungeons.MAX_THEME_BYTES:
        raise WorldDungeonError("Dungeon templates cannot exceed 1 MB.")
    try:
        template = json.loads(text)
    except json.JSONDecodeError as problem:
        raise WorldDungeonError(f"Template JSON is invalid: {problem.msg} (line {problem.lineno}).") from problem
    if not isinstance(template, dict):
        raise WorldDungeonError("The template root must be a JSON object.")
    return template


def template_errors(template, database=None, extra_effects=()):
    """Engine validation, ignoring the author's ID and version, which the world assigns.

    World templates apply the world's own effects (``extra_effects``) rather than bundling any.
    """
    errors = [f"$.{key}: world dungeons use the world's {key}; "
              f"bundle them in the world's {key}.json instead"
              for key in ("origins", "skills", "abilities", "effects") if template.get(key)]
    probe = {**template, "id": "w-probe", "version": "1"}
    return errors + dungeons.validate_template(probe, database, extra_effects)


def stored_template(template):
    """The canonical form kept in the database and in packages."""
    stored = {key: value for key, value in template.items() if key not in ("id", "version")}
    return json.dumps(stored, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def remap_references(template, item_mapper, npc_mapper, effect_mapper=None):
    """Rename shared item/NPC definition IDs and applied effect keys; embedded local_id content is untouched."""
    template = json.loads(json.dumps(template))
    if effect_mapper is not None:
        definitions = template.get("definitions") if isinstance(template.get("definitions"), dict) else {}
        for kind in ("items", "npcs", "enemies"):
            for entry in definitions.get(kind, []) if isinstance(definitions.get(kind), list) else []:
                if isinstance(entry, dict) and isinstance(entry.get("properties"), dict):
                    entry["properties"] = mud_effects.remap_properties(entry["properties"], effect_mapper)
        for trigger in template.get("triggers", []) if isinstance(template.get("triggers"), list) else []:
            for action in trigger.get("actions", []) if isinstance(trigger, dict) else []:
                if (isinstance(action, dict) and action.get("type") in ("apply_effect", "cure_effect")
                        and "effect" in action):
                    action["effect"] = effect_mapper(action.get("effect"))
    pools = template.get("pools") if isinstance(template.get("pools"), dict) else {}
    for kind, mapper in (("items", item_mapper), ("npcs", npc_mapper), ("enemies", npc_mapper)):
        for pool in pools.get(kind, []) if isinstance(pools.get(kind), list) else []:
            for choice in pool.get("choices", []) if isinstance(pool, dict) else []:
                if isinstance(choice, dict) and isinstance(choice.get("definition_id"), str):
                    choice["definition_id"] = mapper(choice["definition_id"])
    definitions = template.get("definitions") if isinstance(template.get("definitions"), dict) else {}
    for kind in ("npcs", "enemies"):
        for npc in definitions.get(kind, []) if isinstance(definitions.get(kind), list) else []:
            for key in ("equipment", "loot"):
                for entry in npc.get(key, []) if isinstance(npc, dict) else []:
                    if isinstance(entry, dict) and isinstance(entry.get("item_definition_id"), str):
                        entry["item_definition_id"] = item_mapper(entry["item_definition_id"])
    # Triggers name embedded content by local ID and shared content by definition ID.
    local = {kind: {entry.get("id") for entry in definitions.get(kind, []) if isinstance(entry, dict)}
             for kind in ("items", "npcs", "enemies")} if definitions else {"items": set(), "npcs": set(), "enemies": set()}

    def shared(kind, value):
        if kind == "item" and isinstance(value, str) and value not in local["items"]:
            return item_mapper(value)
        if kind == "npc" and isinstance(value, str) and value not in local["npcs"] | local["enemies"]:
            return npc_mapper(value)
        return value

    for trigger in template.get("triggers", []) if isinstance(template.get("triggers"), list) else []:
        if not isinstance(trigger, dict):
            continue
        if isinstance(trigger.get("source"), dict):
            trigger["source"] = {kind: shared(kind, value) for kind, value in trigger["source"].items()}
        for action in trigger.get("actions", []) if isinstance(trigger.get("actions"), list) else []:
            if isinstance(action, dict) and action.get("type") in ("give_item", "take_item"):
                action["item"] = shared("item", action.get("item"))
        if isinstance(trigger.get("conditions"), list):
            for condition in trigger["conditions"]:
                for entry in condition.get("any", [condition]) if isinstance(condition, dict) else []:
                    if isinstance(entry, dict) and entry.get("type") == "has_item":
                        entry["item"] = shared("item", entry.get("item"))
    return template


def shared_references(database, world_id, effects=None):
    """Shared item and NPC definitions the world's templates name, for the export closure.

    Effect keys the templates apply are added to ``effects`` when it is given.
    """
    items, npcs = set(), set()
    for row in world_templates(database, world_id):
        remap_references(json.loads(row["template_json"]),
                         lambda value: items.add(value) or value,
                         lambda value: npcs.add(value) or value,
                         None if effects is None else (lambda value: effects.add(value) or value))
    # Built-in station content exists everywhere, so it stays a reference.
    return ({value for value in items if not value.startswith("stationnet:")},
            {value for value in npcs if not value.startswith("stationnet:")})


def world_templates(database, world_id):
    return database.execute(
        "SELECT * FROM mud_world_dungeon_templates WHERE world_id=? ORDER BY name COLLATE NOCASE,id",
        (world_id,),
    ).fetchall()


def world_entrances(database, world_id):
    return database.execute(
        "SELECT e.*,t.name AS template_name,r.name AS room_name,rr.name AS return_room_name "
        "FROM mud_world_dungeon_entrances AS e "
        "JOIN mud_world_dungeon_templates AS t ON t.id=e.template_id "
        "JOIN mud_world_rooms AS r ON r.id=e.room_id "
        "LEFT JOIN mud_world_rooms AS rr ON rr.id=e.return_room_id "
        "WHERE e.world_id=? ORDER BY r.name COLLATE NOCASE,e.name COLLATE NOCASE,e.id",
        (world_id,),
    ).fetchall()


def save_template(database, world_id, local_key, name, template, template_id=None):
    """Create or revise a world template; the caller owns the transaction."""
    if not isinstance(local_key, str) or not KEY_RE.fullmatch(local_key):
        raise WorldDungeonError("Template keys are 1-32 lowercase letters, digits and hyphens, starting with a letter.")
    name = " ".join(str(name or template.get("name") or "").split())
    if not 2 <= len(name) <= 80:
        raise WorldDungeonError("Template names must be between 2 and 80 characters.")
    template = {**template, "name": name}
    errors = template_errors(template, database)
    if errors:
        raise dungeons.TemplateError(errors)
    stored = stored_template(template)
    duplicate = database.execute(
        "SELECT id FROM mud_world_dungeon_templates WHERE world_id=? AND local_key=? AND id<>COALESCE(?,'')",
        (world_id, local_key, template_id),
    ).fetchone()
    if duplicate:
        raise WorldDungeonError("This world already has a dungeon template with that key.")
    if template_id is None:
        if database.execute("SELECT COUNT(*) FROM mud_world_dungeon_templates WHERE world_id=?",
                            (world_id,)).fetchone()[0] >= MAX_TEMPLATES:
            raise WorldDungeonError(f"A world can hold at most {MAX_TEMPLATES} dungeon templates.")
        template_id = f"world:dungeon-template:{uuid.uuid4().hex}"
        database.execute(
            "INSERT INTO mud_world_dungeon_templates(id,world_id,local_key,theme_id,name,template_json) "
            "VALUES(?,?,?,?,?,?)",
            (template_id, world_id, local_key, new_theme_id(local_key), name, stored),
        )
        return template_id
    existing = database.execute(
        "SELECT template_json,local_key FROM mud_world_dungeon_templates WHERE id=? AND world_id=?",
        (template_id, world_id),
    ).fetchone()
    if existing is None:
        raise WorldDungeonError("That dungeon template was not found.")
    # Unchanged content keeps its revision, so repeated saves do not churn engine versions.
    changed = existing["template_json"] != stored
    database.execute(
        "UPDATE mud_world_dungeon_templates SET local_key=?,name=?,template_json=?,"
        "revision=revision+?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (local_key, name, stored, int(changed), template_id),
    )
    return template_id


def _optional_whole(value, label, low, high):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = None
    if number is None or isinstance(value, bool) or not low <= number <= high:
        raise WorldDungeonError(f"{label} must be blank or a whole number from {low} to {high}.")
    return number


def entrance_fields(template, values):
    """Validate entrance settings against the template's own generation bounds."""
    name = " ".join(str(values.get("name") or "").split())
    if not 1 <= len(name) <= 80:
        raise WorldDungeonError("Entrance names must be between 1 and 80 characters.")
    description = str(values.get("description") or "").strip()
    if len(description) > 2000:
        raise WorldDungeonError("Entrance descriptions cannot exceed 2000 characters.")
    generation = template["generation"]
    difficulty = _optional_whole(values.get("difficulty"), "Difficulty",
                                 generation["difficulty"]["minimum"], generation["difficulty"]["maximum"])
    size = _optional_whole(values.get("size"), "Room count",
                           generation["size"]["minimum"], generation["size"]["maximum"])
    layout = str(values.get("layout") or "").strip() or None
    allowed = {entry["type"] for entry in generation["layouts"]}
    if layout is not None and layout not in allowed:
        raise WorldDungeonError(f"Layout must be blank or one of the template's layouts: {', '.join(sorted(allowed))}.")
    max_party = _optional_whole(values.get("max_party"), "Party size", 1, MAX_PARTY) or DEFAULT_PARTY
    try:
        conditions = mud_conditions.validate_conditions(values.get("conditions") or [])
    except ValueError as problem:
        raise WorldDungeonError(f"Entrance conditions: {problem}") from problem
    message = " ".join(str(values.get("conditions_message") or "").split())
    if len(message) > 200:
        raise WorldDungeonError("The barred-entrance message cannot exceed 200 characters.")
    enabled = values.get("enabled", True)
    enabled = enabled if isinstance(enabled, bool) else str(enabled).casefold() in {"1", "true", "on", "yes"}
    return {"name": name, "description": description, "difficulty": difficulty, "size": size,
            "layout": layout, "max_party": max_party, "conditions": conditions,
            "conditions_message": message, "enabled": enabled}


# --- World packages -------------------------------------------------------

def read_document(value, room_ids, extra_effects=()):
    """Validate dungeons.json shape and references before anything is installed."""
    if value is None:
        return {"templates": [], "entrances": []}
    if not isinstance(value, dict) or set(value) - {"templates", "entrances"}:
        raise WorldDungeonError("dungeons.json must be an object with templates and entrances arrays.")
    templates = value.get("templates", [])
    entrances = value.get("entrances", [])
    if not isinstance(templates, list) or len(templates) > MAX_TEMPLATES:
        raise WorldDungeonError(f"dungeons.json templates must be an array of at most {MAX_TEMPLATES}.")
    if not isinstance(entrances, list) or len(entrances) > MAX_ENTRANCES:
        raise WorldDungeonError(f"dungeons.json entrances must be an array of at most {MAX_ENTRANCES}.")
    clean_templates = {}
    for entry in templates:
        if not isinstance(entry, dict) or set(entry) - {"key", "name", "template"}:
            raise WorldDungeonError("Each dungeon template needs key, optional name, and template.")
        key = entry.get("key")
        if not isinstance(key, str) or not KEY_RE.fullmatch(key) or key in clean_templates:
            raise WorldDungeonError("Dungeon template keys must be unique lowercase keys.")
        template = entry.get("template")
        if not isinstance(template, dict):
            raise WorldDungeonError(f"Dungeon template {key!r} must be an object.")
        # Shared definition references are checked at install time, once IDs are remapped.
        errors = template_errors(template, extra_effects=extra_effects)
        if errors:
            raise WorldDungeonError(f"Dungeon template {key!r}: " + "; ".join(errors[:5]))
        clean_templates[key] = {"key": key, "name": entry.get("name") or template["name"], "template": template}
    clean_entrances = []
    names = set()
    for entry in entrances:
        if not isinstance(entry, dict):
            raise WorldDungeonError("Each dungeon entrance must be an object.")
        unknown = set(entry) - {"room", "return_room", "template", "name", "description", "difficulty",
                                "size", "layout", "max_party", "conditions", "conditions_message", "enabled"}
        if unknown:
            raise WorldDungeonError(f"Dungeon entrance has unsupported fields: {', '.join(sorted(unknown))}.")
        room, return_room = entry.get("room"), entry.get("return_room") or entry.get("room")
        if room not in room_ids or return_room not in room_ids:
            raise WorldDungeonError("Dungeon entrances must name rooms in rooms.json.")
        if entry.get("template") not in clean_templates:
            raise WorldDungeonError("Dungeon entrances must name a template in dungeons.json.")
        fields = entrance_fields(clean_templates[entry["template"]]["template"], entry)
        if (room, fields["name"].casefold()) in names:
            raise WorldDungeonError("Dungeon entrance names must be unique within each room.")
        names.add((room, fields["name"].casefold()))
        clean_entrances.append({**fields, "room": room, "return_room": return_room,
                                "template": entry["template"]})
    return {"templates": list(clean_templates.values()), "entrances": clean_entrances}


def install_document(database, world_id, document, room_map, maps, condition_mapper, effect_map=None):
    """Insert a package's templates and entrances; content IDs must already exist."""
    def mapper(table):
        return lambda value: maps.get(table, {}).get(value, value)

    template_ids = {}
    for entry in document["templates"]:
        template = remap_references(entry["template"], mapper("mud_item_definitions"),
                                    mapper("mud_npc_definitions"),
                                    lambda key: (effect_map or {}).get(key, key))
        try:
            template_ids[entry["key"]] = save_template(database, world_id, entry["key"],
                                                       entry["name"], template)
        except dungeons.TemplateError as problem:
            raise WorldDungeonError(f"Dungeon template {entry['key']!r}: " + "; ".join(problem.errors[:5])) from problem
    for entry in document["entrances"]:
        database.execute(
            "INSERT INTO mud_world_dungeon_entrances(id,world_id,room_id,return_room_id,template_id,name,"
            "description,difficulty,size,layout,max_party,conditions_json,conditions_message,enabled) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"world:dungeon-entrance:{uuid.uuid4().hex}", world_id, room_map[entry["room"]],
             room_map[entry["return_room"]], template_ids[entry["template"]], entry["name"],
             entry["description"], entry["difficulty"], entry["size"], entry["layout"],
             entry["max_party"],
             json.dumps(mud_conditions.remap(entry["conditions"], condition_mapper),
                        ensure_ascii=False, separators=(",", ":")),
             entry["conditions_message"], int(entry["enabled"])),
        )


def export_document(database, world_id, room_map, maps, condition_mapper, effect_map=None):
    """Package-local templates and entrances; None when the world has no dungeons."""
    rows = world_templates(database, world_id)
    if not rows:
        return None

    def mapper(table):
        return lambda value: maps.get(table, {}).get(value, value)

    keys = {row["id"]: row["local_key"] for row in rows}
    templates = [{"key": row["local_key"], "name": row["name"],
                  "template": remap_references(json.loads(row["template_json"]),
                                               mapper("mud_item_definitions"), mapper("mud_npc_definitions"),
                                               lambda key: (effect_map or {}).get(key, key))}
                 for row in rows]
    entrances = []
    for row in world_entrances(database, world_id):
        if row["room_id"] not in room_map:
            continue
        entry = {"room": room_map[row["room_id"]],
                 "return_room": room_map.get(row["return_room_id"], room_map[row["room_id"]]),
                 "template": keys[row["template_id"]], "name": row["name"],
                 "max_party": row["max_party"], "enabled": bool(row["enabled"])}
        for column in ("description", "difficulty", "size", "layout", "conditions_message"):
            if row[column] not in (None, ""):
                entry[column] = row[column]
        conditions = mud_conditions.validate_conditions(row["conditions_json"] or "[]")
        if conditions:
            entry["conditions"] = mud_conditions.remap(conditions, condition_mapper)
        entrances.append(entry)
    return {"templates": templates, "entrances": entrances}
