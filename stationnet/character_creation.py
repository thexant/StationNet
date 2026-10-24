"""Configurable MUD character origins and authoritative one-time creation."""
import json
from pathlib import Path
import uuid

from flask import current_app


ATTRIBUTES = ("strength", "agility", "intellect", "vitality")
def options():
    path = Path(current_app.config.get("MUD_CHARACTER_OPTIONS") or
                Path(__file__).with_name("character_options.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    for group in ("races", "classes", "skills"):
        keys = [item["key"] for item in data[group]]
        if len(keys) != len(set(keys)):
            raise ValueError(f"Duplicate {group} keys in character options.")
    return data


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
    config = options()
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
    for track in ("shared", "traditional", "procedural"):
        progression = database.execute(
            "SELECT current_health,maximum_health FROM mud_character_progression "
            "WHERE character_id=? AND track_key=?", (row["id"], track),
        ).fetchone()
        if progression is None:
            continue
        maximum = max(1, progression["maximum_health"] + health_bonus)
        current = min(maximum, max(0, progression["current_health"] + health_bonus))
        database.execute(
            "UPDATE mud_character_progression SET strength=?,agility=?,intellect=?,vitality=?,"
            "maximum_health=?,current_health=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE character_id=? AND track_key=?",
            (*[attributes[key] for key in ATTRIBUTES], maximum, current, row["id"], track),
        )
    for skill in skills:
        database.execute(
            "INSERT INTO mud_character_skills(character_id,skill_key,rank) VALUES(?,?,?)",
            (row["id"], skill, 2 if archetype.get("skill") == skill else 1),
        )
    # One grant during creation; legacy possessions and equipment are untouched.
    for item in archetype.get("equipment", []):
        definition_id = f"origin:{config['id']}:{item['key']}"
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
