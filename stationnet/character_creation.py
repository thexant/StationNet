"""Configurable MUD character origins and authoritative one-time creation."""
import hashlib
import json
from pathlib import Path
import re
import uuid

from flask import current_app


ATTRIBUTES = ("strength", "agility", "intellect", "vitality")
KINDS = ("races", "classes")
KIND_NAMES = {"races": "race", "classes": "class"}
MAX_ORIGIN_BYTES = 32 * 1024
KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


def _built_in_options():
    path = Path(current_app.config.get("MUD_CHARACTER_OPTIONS") or
                Path(__file__).with_name("character_options.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    for group in (*KINDS, "skills"):
        keys = [item["key"] for item in data[group]]
        if len(keys) != len(set(keys)):
            raise ValueError(f"Duplicate {group} keys in character options.")
    return data


def catalog(database=None, *, include_removed=False):
    """Return server-side metadata and definitions for every managed origin."""
    if database is None:
        from .db import get_db
        database = get_db()
    built_in = _built_in_options()
    rows = {(row["kind"], row["key"]): row for row in database.execute(
        "SELECT kind,key,definition_json,imported,installed,enabled FROM mud_origin_definitions"
    )}
    entries = {kind: [] for kind in KINDS}
    for kind in KINDS:
        for definition in built_in[kind]:
            row = rows.pop((kind, definition["key"]), None)
            entries[kind].append({"kind": kind, "key": definition["key"],
                                  "definition": definition, "imported": False,
                                  "installed": True,
                                  "enabled": bool(row["enabled"]) if row else True})
    for (kind, key), row in rows.items():
        if not row["imported"] or (not row["installed"] and not include_removed):
            continue
        entries[kind].append({"kind": kind, "key": key,
                              "definition": json.loads(row["definition_json"]),
                              "imported": True, "installed": bool(row["installed"]),
                              "enabled": bool(row["enabled"]) and bool(row["installed"])})
    for kind in KINDS:
        entries[kind].sort(key=lambda entry: entry["definition"]["name"].casefold())
    return entries


def options(database=None, *, include_inactive=False, include_removed=False):
    data = _built_in_options()
    entries = catalog(database, include_removed=include_removed)
    for kind in KINDS:
        data[kind] = [entry["definition"] for entry in entries[kind]
                      if include_inactive or entry["enabled"]]
    return data


def _text(value, label, maximum, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{label} must be {minimum}-{maximum} characters.")
    return value.strip()


def validate_origin(kind, document, database):
    """Validate an imported JSON definition against supported MUD mechanics."""
    if kind not in KINDS or not isinstance(document, dict):
        raise ValueError("Choose a race or class JSON object.")
    key = document.get("key")
    if not isinstance(key, str) or not KEY_RE.fullmatch(key):
        raise ValueError("Origin key must be 2-40 lowercase letters, digits, or hyphens, starting with a letter.")
    definition = {
        "key": key,
        "name": _text(document.get("name"), "Name", 80, 2),
        "description": _text(document.get("description"), "Description", 1000),
        "traits": _text(document.get("traits"), "Traits", 160),
    }
    for field, allowed, bound in (("attributes", ATTRIBUTES, 10),
                                   ("effects", ("attack", "defense", "experience_percent"), 100)):
        values = document.get(field, {})
        if not isinstance(values, dict) or any(
            name not in allowed or type(value) is not int or abs(value) > bound
            for name, value in values.items()
        ):
            raise ValueError(f"{field} must contain supported keys with integer values within {bound}.")
        definition[field] = values
    if kind == "classes":
        from .mud import EQUIPMENT_SLOTS, SPELLS
        built_in = _built_in_options()
        skills = {item["key"] for item in built_in["skills"]}
        skill = document.get("skill")
        if not isinstance(skill, str) or skill not in skills:
            raise ValueError("Class favored skill must be a supported skill.")
        definition["skill"] = skill
        spells = document.get("spells", [])
        if (not isinstance(spells, list) or len(spells) > len(SPELLS)
                or any(not isinstance(spell, str) or spell not in SPELLS for spell in spells)
                or len(set(spells)) != len(spells)):
            raise ValueError("Class spells must be distinct supported spell keys.")
        definition["spells"] = spells
        equipment = document.get("equipment", [])
        if not isinstance(equipment, list) or len(equipment) > len(EQUIPMENT_SLOTS):
            raise ValueError("Class equipment must be a list of up to eight items.")
        parsed = []
        for item in equipment:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not KEY_RE.fullmatch(item["key"]):
                raise ValueError("Each equipment item needs a valid key.")
            if item.get("slot") not in EQUIPMENT_SLOTS:
                raise ValueError("Equipment must use a supported slot.")
            parsed_item = {"key": item["key"], "name": _text(item.get("name"), "Equipment name", 80, 2),
                           "description": _text(item.get("description"), "Equipment description", 1000),
                           "slot": item["slot"]}
            for stat in ("attack", "defense"):
                value = item.get(stat, 0)
                if type(value) is not int or not 0 <= value <= 100:
                    raise ValueError("Equipment attack and defense must be integers from 0 to 100.")
                parsed_item[stat] = value
            parsed.append(parsed_item)
        if len({item["key"] for item in parsed}) != len(parsed):
            raise ValueError("Equipment keys must be distinct within a class.")
        definition["equipment"] = parsed
    if any(entry["key"] == key for entry in catalog(database, include_removed=True)[kind]):
        raise ValueError(f"A {KIND_NAMES[kind]} with that key already exists or was used by a character.")
    return definition


def import_origin(database, kind, payload):
    if not payload or len(payload) > MAX_ORIGIN_BYTES:
        raise ValueError("Choose a JSON file no larger than 32 KB.")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("The origin file must contain valid UTF-8 JSON.") from error
    definition = validate_origin(kind, document, database)
    database.execute(
        "INSERT INTO mud_origin_definitions(kind,key,definition_json,imported,installed,enabled) "
        "VALUES(?,?,?,1,1,1)",
        (kind, definition["key"], json.dumps(definition, separators=(",", ":"))),
    )
    return definition


def active_counts(database):
    entries = catalog(database)
    return {kind: sum(entry["enabled"] for entry in entries[kind]) for kind in KINDS}


def require_available(database):
    counts = active_counts(database)
    if not counts["races"] or not counts["classes"]:
        raise ValueError("Enable at least one race and one class while a MUD mode is enabled.")


def _choice(items, key, label):
    return next((item for item in items if item["key"] == key), None) or _invalid(label)


def _invalid(label):
    raise ValueError(f"Choose a valid {label}.")


def validate(payload, config):
    name = " ".join(str(payload.get("name", "")).split())
    if not (2 <= len(name) <= 32 and name[0].isalpha()
            and all(char.isalpha() or char in " .'-" for char in name)):
        raise ValueError("Character name must be 2–32 letters and may contain spaces, apostrophes, periods, or hyphens.")
    race = _choice(config["races"], payload.get("race"), "race")
    archetype = _choice(config["classes"], payload.get("class"), "class")
    point_buy = config["point_buy"]
    allocations = payload.get("attributes")
    if not isinstance(allocations, dict) or set(allocations) != set(ATTRIBUTES):
        raise ValueError("Allocate every attribute.")
    if any(type(allocations[key]) is not int or not point_buy["minimum"] <= allocations[key] <= point_buy["maximum"]
           for key in ATTRIBUTES):
        raise ValueError("Attribute values are outside the allowed range.")
    if sum(allocations.values()) != point_buy["base"] * len(ATTRIBUTES) + point_buy["points"]:
        raise ValueError("Spend exactly the available attribute points.")
    skills = payload.get("skills")
    valid_skills = {item["key"] for item in config["skills"]}
    if (not isinstance(skills, list) or any(not isinstance(skill, str) for skill in skills)
            or len(skills) != config["starting_skill_choices"]
            or len(set(skills)) != len(skills) or not set(skills) <= valid_skills):
        raise ValueError("Choose the required number of distinct starting skills.")
    final = {key: allocations[key] + race.get("attributes", {}).get(key, 0)
             + archetype.get("attributes", {}).get(key, 0) for key in ATTRIBUTES}
    if any(value < 1 or value > 30 for value in final.values()):
        raise ValueError("An attribute modifier is outside the supported range.")
    return name, race, archetype, allocations, final, skills


def complete(database, user_id, payload, possession_mode="traditional"):
    """Apply a pending build atomically; legacy progression and possessions remain."""
    config = options(database)
    if possession_mode not in ("traditional", "procedural"):
        raise ValueError("Invalid starting mode.")
    name, race, archetype, _allocated, attributes, skills = validate(payload, config)
    row = database.execute(
        "SELECT id,creation_state FROM mud_characters WHERE user_id=?", (user_id,)
    ).fetchone()
    if row is None or row["creation_state"] not in ("pending", "legacy"):
        raise ValueError("This character has already been created.")
    collision = database.execute(
        "SELECT 1 FROM mud_characters WHERE display_name=? COLLATE NOCASE AND user_id<>?",
        (name, user_id),
    ).fetchone()
    if collision:
        raise ValueError("That character name is already in use.")
    health_bonus = (attributes["vitality"] - 10) * 5
    mana_bonus = (attributes["intellect"] - 10) * 3
    for track in ("shared", "traditional", "procedural"):
        progression = database.execute(
            "SELECT current_health,maximum_health,current_mana,maximum_mana FROM mud_character_progression "
            "WHERE character_id=? AND track_key=?", (row["id"], track),
        ).fetchone()
        if progression is None:
            continue
        maximum = max(1, progression["maximum_health"] + health_bonus)
        current = min(maximum, max(0, progression["current_health"] + health_bonus))
        maximum_mana = max(1, progression["maximum_mana"] + mana_bonus)
        current_mana = min(maximum_mana, max(0, progression["current_mana"] + mana_bonus))
        database.execute(
            "UPDATE mud_character_progression SET strength=?,agility=?,intellect=?,vitality=?,"
            "maximum_health=?,current_health=?,maximum_mana=?,current_mana=?,"
            "updated_at=CURRENT_TIMESTAMP "
            "WHERE character_id=? AND track_key=?",
            (*[attributes[key] for key in ATTRIBUTES], maximum, current,
             maximum_mana, current_mana, row["id"], track),
        )
    for skill in skills:
        database.execute(
            "INSERT INTO mud_character_skills(character_id,skill_key,rank) VALUES(?,?,?)",
            (row["id"], skill, 2 if archetype.get("skill") == skill else 1),
        )
    # One grant during creation; legacy possessions and equipment are untouched.
    for item in archetype.get("equipment", []):
        item_hash = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:12]
        definition_id = f"origin:{config['id']}:{archetype['key']}:{item['key']}:{item_hash}"
        database.execute(
            "INSERT OR IGNORE INTO mud_item_definitions"
            "(id,name,description,item_type,properties_json,equipment_slot) "
            "VALUES(?,?,?,'equipment',?,?)",
            (definition_id, item["name"], item["description"],
             json.dumps({"attack": item.get("attack", 0), "defense": item.get("defense", 0)}),
             item["slot"]),
        )
        database.execute(
            "INSERT INTO mud_item_instances(id,definition_id,owner_character_id,possession_mode) "
            "VALUES(?,?,?,?)",
            (f"origin:item:{uuid.uuid4().hex}", definition_id, row["id"], possession_mode),
        )
    database.execute(
        "UPDATE mud_characters SET display_name=?,race_key=?,class_key=?,creation_state='complete',"
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND creation_state IN ('pending','legacy')",
        (name, race["key"], archetype["key"], row["id"]),
    )
    return name


def skill_ranks(database, character_id):
    return {row["skill_key"]: row["rank"] for row in database.execute(
        "SELECT skill_key,rank FROM mud_character_skills WHERE character_id=?", (character_id,)
    )}


def practice(database, character_id, skill_key, amount=1):
    row = database.execute(
        "SELECT rank,experience FROM mud_character_skills WHERE character_id=? AND skill_key=?",
        (character_id, skill_key),
    ).fetchone()
    if row is None or row["rank"] >= 20:
        return
    experience = row["experience"] + amount
    rank = row["rank"]
    while rank < 20 and experience >= rank * 20:
        experience -= rank * 20
        rank += 1
    database.execute(
        "UPDATE mud_character_skills SET rank=?,experience=? WHERE character_id=? AND skill_key=?",
        (rank, experience, character_id, skill_key),
    )
