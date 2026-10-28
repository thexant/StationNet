"""Data-driven MUD skills: definitions, availability, progression, and learning."""
import json

from . import character_creation


GROUPS = ("combat", "weapon", "armor", "magic", "stealth", "exploration", "social", "knowledge")
# Every effect type is consumed by a specific game rule in mud.py; definitions
# naming anything else are rejected rather than silently doing nothing.
EFFECT_TYPES = {
    "attack": "melee and firearm damage",
    "melee_damage": "melee damage",
    "firearm_damage": "firearm damage",
    "spell_power": "spell damage",
    "healing": "healing spells",
    "defense": "damage mitigation",
    "accuracy": "chance to hit",
    "evasion": "chance to dodge attacks",
    "experience_percent": "% experience earned",
    "stealth": "% hiding and sneaking chance",
    "flee": "% escape chance",
    "search": "search score for hidden discoveries",
    "lockpick": "lockpicking score",
    "gathering": "% gathering success chance",
    "shop_discount": "% merchant discount",
}
WEAPON_FILTERED = {"attack", "melee_damage", "firearm_damage", "accuracy"}
ARMOR_FILTERED = {"defense"}
EFFECT_CAPS = {"shop_discount": 50}
TRIGGERS = {
    "melee_hit": "landing melee attacks",
    "firearm_hit": "landing firearm shots",
    "spell_cast": "casting damage spells",
    "heal_cast": "casting healing spells",
    "damage_taken": "taking damage in combat",
    "defend": "bracing with /defend",
    "dodge": "dodging attacks",
    "hide": "hiding",
    "flee": "fleeing combat",
    "search": "searching rooms",
    "pick_lock": "picking locks",
    "experience_gained": "earning experience",
    "purchase": "buying from merchants",
    "gather": "successfully gathering materials",
}
# Category-bound practice only counts while matching gear is equipped.
CATEGORY_TRIGGERS = {"melee_hit", "firearm_hit", "damage_taken", "defend"}
LEGACY_TRIGGERS = {"attack": ["melee_hit", "firearm_hit"], "defense": ["damage_taken"],
                   "experience_percent": ["experience_gained"]}
MAX_RANK = 20
PRACTICE_BASE = 6
RANK_LEVEL_ALLOWANCE = 4
MAX_SKILL_BYTES = 32 * 1024
MAX_PACK_BYTES = 1024 * 1024
MAX_SKILLS_PER_PACK = 100
SOURCES = ("creation", "granted", "learned", "trainer")


def _whole(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}.")
    return value


def _key(value, label):
    if not isinstance(value, str) or not character_creation.KEY_RE.fullmatch(value):
        raise ValueError(f"{label} must be 2-40 lowercase letters, digits, or hyphens, starting with a letter.")
    return value


def _key_list(value, label, limit=20):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit or len(set(map(str, value))) != len(value):
        raise ValueError(f"{label} must be a list of up to {limit} distinct keys.")
    return [_key(item, label) for item in value]


def normalize(document, *, known_skills=None, known_origins=None):
    """Validate one skill document; reference checks run when known keys are given."""
    if not isinstance(document, dict):
        raise ValueError("A skill must be a JSON object.")
    document = dict(document)
    if "effect" in document and "effects" not in document:
        # Older character option files used one effect with an implicit trigger.
        legacy = document.pop("effect")
        document["effects"] = [{"type": legacy, "per_rank": document.pop("per_rank", 1)}]
        document.setdefault("practice", {"triggers": LEGACY_TRIGGERS.get(legacy, [])})
    allowed = {"key", "name", "description", "group", "starting", "max_rank", "effects",
               "proficiencies", "practice", "learn", "requirements"}
    unknown = set(document) - allowed
    if unknown:
        raise ValueError(f"Unsupported skill fields: {', '.join(sorted(unknown))}.")
    definition = {
        "key": _key(document.get("key"), "Skill key"),
        "name": character_creation._text(document.get("name"), "Name", 80, 2),
        "description": character_creation._text(document.get("description"), "Description", 1000),
    }
    group = document.get("group", "combat")
    if group not in GROUPS:
        raise ValueError(f"Skill group must be one of: {', '.join(GROUPS)}.")
    definition["group"] = group
    starting = document.get("starting", True)
    if not isinstance(starting, bool):
        raise ValueError("starting must be true or false.")
    definition["starting"] = starting
    definition["max_rank"] = _whole(document.get("max_rank", MAX_RANK), 1, MAX_RANK, "max_rank")

    effects = document.get("effects", [])
    if not isinstance(effects, list) or len(effects) > 8:
        raise ValueError("effects must be a list of up to eight effects.")
    parsed_effects = []
    for effect in effects:
        if not isinstance(effect, dict) or effect.get("type") not in EFFECT_TYPES:
            raise ValueError(f"Each effect needs a supported type: {', '.join(EFFECT_TYPES)}.")
        extra = set(effect) - {"type", "per_rank", "every", "max", "weapon_category", "armor_category"}
        if extra:
            raise ValueError(f"Unsupported effect fields: {', '.join(sorted(extra))}.")
        parsed = {"type": effect["type"],
                  "per_rank": _whole(effect.get("per_rank", 1), 1, 100, "Effect per_rank"),
                  "every": _whole(effect.get("every", 1), 1, MAX_RANK, "Effect every")}
        if "max" in effect:
            parsed["max"] = _whole(effect["max"], 1, 1000, "Effect max")
        if "weapon_category" in effect:
            if effect["type"] not in WEAPON_FILTERED:
                raise ValueError("weapon_category only applies to attack, melee_damage, firearm_damage, or accuracy.")
            parsed["weapon_category"] = _key(effect["weapon_category"], "weapon_category")
        if "armor_category" in effect:
            if effect["type"] not in ARMOR_FILTERED:
                raise ValueError("armor_category only applies to defense.")
            parsed["armor_category"] = _key(effect["armor_category"], "armor_category")
        parsed_effects.append(parsed)
    definition["effects"] = parsed_effects

    proficiencies = document.get("proficiencies", {})
    if not isinstance(proficiencies, dict) or set(proficiencies) - {"weapons", "armor"}:
        raise ValueError("proficiencies may contain weapons and armor category lists.")
    definition["proficiencies"] = {kind: _key_list(proficiencies.get(kind), f"{kind} proficiencies")
                                   for kind in ("weapons", "armor")}

    practice = document.get("practice", {})
    if not isinstance(practice, dict) or set(practice) - {"triggers", "rate"}:
        raise ValueError("practice may contain triggers and rate.")
    triggers = practice.get("triggers", [])
    if (not isinstance(triggers, list) or len(set(map(str, triggers))) != len(triggers)
            or any(trigger not in TRIGGERS for trigger in triggers)):
        raise ValueError(f"Practice triggers must be distinct values from: {', '.join(TRIGGERS)}.")
    definition["practice"] = {"triggers": list(triggers),
                              "rate": _whole(practice.get("rate", 1), 1, 10, "Practice rate")}
    if not parsed_effects and not any(definition["proficiencies"].values()):
        raise ValueError("A skill needs at least one effect or proficiency to affect play.")

    learn = document.get("learn", {})
    if not isinstance(learn, dict) or set(learn) - {"cost", "trainer_required"}:
        raise ValueError("learn may contain cost and trainer_required.")
    if not isinstance(learn.get("trainer_required", False), bool):
        raise ValueError("trainer_required must be true or false.")
    definition["learn"] = {"cost": _whole(learn.get("cost", 1), 0, 10, "Learn cost"),
                           "trainer_required": learn.get("trainer_required", False)}

    requirements = document.get("requirements", {})
    if not isinstance(requirements, dict) or set(requirements) - {"level", "attributes", "races", "classes", "skills"}:
        raise ValueError("requirements may contain level, attributes, races, classes, and skills.")
    attributes = requirements.get("attributes", {})
    if not isinstance(attributes, dict) or any(
        name not in character_creation.ATTRIBUTES or type(value) is not int or not 1 <= value <= 30
        for name, value in attributes.items()
    ):
        raise ValueError("Attribute requirements must map attributes to values from 1 to 30.")
    required_skills = requirements.get("skills", {})
    if not isinstance(required_skills, dict) or len(required_skills) > 10:
        raise ValueError("Skill requirements must map up to ten skill keys to ranks.")
    for skill_key, rank in required_skills.items():
        _key(skill_key, "Required skill")
        _whole(rank, 1, MAX_RANK, "Required skill rank")
        if skill_key == definition["key"]:
            raise ValueError("A skill cannot require itself.")
        if known_skills is not None and skill_key not in known_skills:
            raise ValueError(f"Required skill {skill_key} is not installed.")
    origins = {}
    for kind in character_creation.KINDS:
        keys = _key_list(requirements.get(kind), f"Required {kind}", 100)
        if known_origins is not None and set(keys) - known_origins.get(kind, set()):
            raise ValueError(f"Required {kind} must name installed {kind}.")
        origins[kind] = keys
    definition["requirements"] = {
        "level": _whole(requirements.get("level", 1), 1, 50, "Required level"),
        "attributes": dict(attributes), **origins, "skills": dict(required_skills),
    }
    return definition


def _built_in():
    return [normalize(item) for item in character_creation._built_in_options()["skills"]]


def starting_choices():
    return character_creation._built_in_options().get("starting_skill_choices", 2)


def catalog(database, *, include_removed=False):
    """Every managed skill with its source and availability, sorted by name."""
    rows = {row["key"]: row for row in database.execute(
        "SELECT key,definition_json,imported,installed,enabled FROM mud_skill_definitions")}
    entries = []
    for definition in _built_in():
        row = rows.pop(definition["key"], None)
        entries.append({"key": definition["key"], "definition": definition, "imported": False,
                        "installed": True, "enabled": bool(row["enabled"]) if row else True})
    for key, row in rows.items():
        if not row["imported"] or (not row["installed"] and not include_removed):
            continue
        entries.append({"key": key, "definition": json.loads(row["definition_json"]),
                        "imported": True, "installed": bool(row["installed"]),
                        "enabled": bool(row["enabled"]) and bool(row["installed"])})
    entries.sort(key=lambda entry: entry["definition"]["name"].casefold())
    return entries


def definitions(database, *, include_inactive=False, include_removed=False):
    return {entry["key"]: entry["definition"]
            for entry in catalog(database, include_removed=include_removed)
            if include_inactive or entry["enabled"]}


def known_origin_keys(database):
    entries = character_creation.catalog(database, include_removed=True)
    return {kind: {entry["key"] for entry in entries[kind]} for kind in character_creation.KINDS}


def validate_skill(document, database, *, check_key=True, extra_skills=(), extra_origins=None):
    known_skills = {entry["key"] for entry in catalog(database, include_removed=True)} | set(extra_skills)
    origins = known_origin_keys(database)
    for kind, keys in (extra_origins or {}).items():
        origins[kind] = origins.get(kind, set()) | set(keys)
    definition = normalize(document, known_skills=known_skills, known_origins=origins)
    if check_key and any(entry["key"] == definition["key"] for entry in catalog(database, include_removed=True)):
        raise ValueError("A skill with that key already exists or was used by a character.")
    return definition


def _store(database, definition, enabled=1):
    database.execute(
        "INSERT INTO mud_skill_definitions(key,definition_json,imported,installed,enabled) VALUES(?,?,1,1,?)",
        (definition["key"], json.dumps(definition, separators=(",", ":")), enabled),
    )


def _decode(payload, limit, label):
    if not payload or len(payload) > limit:
        raise ValueError(f"Choose a JSON file no larger than {limit // 1024} KB.")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"The {label} must contain valid UTF-8 JSON.") from error


def _installed_names(database):
    return {entry["definition"]["name"].casefold() for entry in catalog(database, include_removed=True)
            if entry["installed"]}


def import_skill(database, payload):
    definition = validate_skill(_decode(payload, MAX_SKILL_BYTES, "skill file"), database)
    if definition["name"].casefold() in _installed_names(database):
        raise ValueError("A skill with that name is already installed.")
    _store(database, definition)
    return definition


def import_pack(database, payload):
    """Validate and install a skills document as one atomic unit."""
    document = _decode(payload, MAX_PACK_BYTES, "skill pack")
    if not isinstance(document, dict) or set(document) != {"skills"}:
        raise ValueError("A skill pack must contain only a skills array.")
    sources = document["skills"]
    if not isinstance(sources, list) or not sources or len(sources) > MAX_SKILLS_PER_PACK:
        raise ValueError(f"Pack skills must be an array of 1 to {MAX_SKILLS_PER_PACK} objects.")
    pack_keys = {source.get("key") for source in sources if isinstance(source, dict)}
    existing_names = _installed_names(database)
    seen_keys, seen_names, parsed = set(), set(), []
    for index, source in enumerate(sources, 1):
        try:
            definition = validate_skill(source, database, extra_skills=pack_keys)
        except ValueError as error:
            raise ValueError(f"Invalid skill at skills[{index}]: {error}") from error
        folded = definition["name"].casefold()
        if definition["key"] in seen_keys:
            raise ValueError(f"Duplicate skill key in pack: {definition['key']}.")
        if folded in seen_names:
            raise ValueError(f"Duplicate skill name in pack: {definition['name']}.")
        if folded in existing_names:
            raise ValueError(f"A skill named {definition['name']} is already installed.")
        seen_keys.add(definition["key"])
        seen_names.add(folded)
        parsed.append(definition)
    for definition in parsed:
        _store(database, definition)
    return parsed


def set_enabled(database, keys):
    """Enable exactly the chosen installed skills."""
    entries = catalog(database)
    unknown = set(keys) - {entry["key"] for entry in entries}
    if unknown:
        raise ValueError("Choose only installed skills.")
    for entry in entries:
        enabled = int(entry["key"] in keys)
        if entry["imported"]:
            database.execute("UPDATE mud_skill_definitions SET enabled=? WHERE key=?", (enabled, entry["key"]))
        else:
            database.execute(
                "INSERT INTO mud_skill_definitions(key,enabled) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled", (entry["key"], enabled))


def starting_count(entries, chosen=None):
    return sum(1 for entry in entries if entry["definition"]["starting"]
               and (entry["enabled"] if chosen is None else entry["key"] in chosen))


def require_available(database, chosen=None):
    """Character creation needs enough enabled starting skills while the MUD runs."""
    needed = starting_choices()
    if starting_count(catalog(database), chosen) < needed:
        raise ValueError(f"Enable at least {needed} starting skills while a MUD mode is enabled.")


def restore_missing_defaults(database):
    if starting_count(catalog(database)) >= starting_choices():
        return False
    for definition in _built_in():
        if definition["starting"]:
            database.execute("INSERT INTO mud_skill_definitions(key,enabled) VALUES(?,1) "
                             "ON CONFLICT(key) DO UPDATE SET enabled=1", (definition["key"],))
    return True


def dependents(database, key, ignored=()):
    """Installed races, classes, and skills that reference a skill."""
    names = []
    entries = character_creation.catalog(database)
    for kind in character_creation.KINDS:
        for entry in entries[kind]:
            definition = entry["definition"]
            if (definition.get("skill") == key or key in definition.get("skills", {})
                    or key in definition.get("skill_affinities", {})):
                names.append(f"{character_creation.KIND_NAMES[kind]} {definition['name']}")
    for entry in catalog(database):
        if entry["key"] not in ignored and key in entry["definition"]["requirements"]["skills"]:
            names.append(f"skill {entry['definition']['name']}")
    return names


def remove_skills(database, keys):
    """Remove imported skills, keeping definitions that characters still hold."""
    entries = {entry["key"]: entry for entry in catalog(database) if entry["imported"]}
    keys = set(keys)
    if not keys:
        raise ValueError("Select at least one imported skill to remove.")
    if keys - set(entries):
        raise ValueError("Select only installed imported skills to remove.")
    for key in sorted(keys):
        blocking = dependents(database, key, ignored=keys)
        if blocking:
            raise ValueError(f"{entries[key]['definition']['name']} is still used by "
                             f"{', '.join(blocking)}. Remove or update those first.")
    archived = [json.loads(row["snapshot_json"]) for row in
                database.execute("SELECT snapshot_json FROM mud_campaign_characters")]
    removed = []
    for key in sorted(keys):
        used = database.execute("SELECT 1 FROM mud_character_skills WHERE skill_key=? LIMIT 1", (key,)).fetchone()
        used = used or any(row.get("skill_key") == key for snapshot in archived
                           for row in snapshot.get("tables", {}).get("mud_character_skills", []))
        if used:
            database.execute("UPDATE mud_skill_definitions SET installed=0,enabled=0 WHERE key=?", (key,))
        else:
            database.execute("DELETE FROM mud_skill_definitions WHERE key=?", (key,))
        for table in ("mud_npc_trainers", "mud_world_skills", "mud_dungeon_skills"):
            database.execute(f"DELETE FROM {table} WHERE skill_key=?", (key,))
        removed.append(entries[key])
    return removed


def removable_associations(database, linked, *, world_id="", theme_id=""):
    """Installed skills linked only to the world or theme being removed."""
    lookup = {entry["key"]: entry for entry in catalog(database) if entry["imported"]}
    result = []
    for key in sorted(linked):
        if key not in lookup:
            continue
        if database.execute("SELECT 1 FROM mud_world_skills WHERE skill_key=? AND world_id!=? LIMIT 1",
                            (key, world_id)).fetchone():
            continue
        if database.execute("SELECT 1 FROM mud_dungeon_skills WHERE skill_key=? AND theme_id!=? LIMIT 1",
                            (key, theme_id)).fetchone():
            continue
        result.append(lookup[key])
    return result


def remove_linked(database, linked):
    """Remove bundled skills, skipping any another installed definition still needs."""
    keys = {entry["key"] for entry in linked}
    usable = {key for key in keys if not dependents(database, key, ignored=keys)}
    return remove_skills(database, usable) if usable else []


def remap(definition, mapping):
    """Rewrite skill references inside a skill or origin definition."""
    result = dict(definition)
    if result.get("skill") in mapping:
        result["skill"] = mapping[result["skill"]]
    for field in ("skills", "skill_affinities"):
        if isinstance(result.get(field), dict):
            result[field] = {mapping.get(key, key): value for key, value in result[field].items()}
    if isinstance(result.get("requirements"), dict):
        requirements = dict(result["requirements"])
        if isinstance(requirements.get("skills"), dict):
            requirements["skills"] = {mapping.get(key, key): value
                                      for key, value in requirements["skills"].items()}
        result["requirements"] = requirements
    return result


def remap_origins(definition, mapping):
    """Rewrite race/class keys named by a skill's requirements."""
    result = dict(definition)
    requirements = dict(result.get("requirements") or {})
    for kind in character_creation.KINDS:
        if isinstance(requirements.get(kind), list):
            requirements[kind] = [mapping.get((kind, key), key) for key in requirements[kind]]
    if requirements:
        result["requirements"] = requirements
    return result


# Character progression -----------------------------------------------------

def effect_value(effect, rank):
    value = (rank // effect.get("every", 1)) * effect["per_rank"]
    return min(value, effect["max"]) if "max" in effect else value


def highest_level(database, character_id):
    row = database.execute("SELECT MAX(level) FROM mud_character_progression WHERE character_id=?",
                           (character_id,)).fetchone()
    return row[0] or 1


def earned_points(level):
    """One point every even level plus a bonus at every fifth level."""
    return level // 2 + level // 5


def rank_cap(definition, level):
    return min(definition["max_rank"], level + RANK_LEVEL_ALLOWANCE)


def next_rank_cost(rank):
    return PRACTICE_BASE * rank


def character_rows(database, character_id):
    return [dict(row) for row in database.execute(
        "SELECT skill_key,rank,experience,source,points_spent FROM mud_character_skills "
        "WHERE character_id=? ORDER BY skill_key", (character_id,))]


def active_ranks(database, character_id, active=None):
    active = definitions(database) if active is None else active
    return {row["skill_key"]: (active[row["skill_key"]], row["rank"])
            for row in character_rows(database, character_id) if row["skill_key"] in active}


def origin_profile(database, character_id):
    return character_creation.origin_profile(*character_creation.character_origins(database, character_id))


def available_points(database, character_id):
    active = definitions(database)
    spent = sum(row["points_spent"] for row in character_rows(database, character_id)
                if row["skill_key"] in active)
    bonus = origin_profile(database, character_id)["progression"].get("skill_points", 0)
    return max(0, earned_points(highest_level(database, character_id)) + bonus - spent)


def affinity(profile, definition):
    """Practice percentage from race and class: a skill's own entry beats its group's."""
    affinities = profile["skill_affinities"]
    return affinities.get(definition["key"], affinities.get(definition["group"], 0))


def practice_gain(amount, rate, percent):
    """Practice earned for an action; affinities scale it but never erase it."""
    base = amount * rate
    return max(1, base * (100 + percent) // 100) if base > 0 else base


def proficiencies(database, character_id):
    known = set()
    for definition, _rank in active_ranks(database, character_id).values():
        known.update(definition["proficiencies"]["weapons"])
        known.update(definition["proficiencies"]["armor"])
    return known


def bonuses(database, character_id, weapon_categories=(), armor_categories=()):
    """Sum every active skill effect, honoring gear-category filters."""
    totals = {key: 0 for key in EFFECT_TYPES}
    weapons, armor = set(weapon_categories), set(armor_categories)
    for definition, rank in active_ranks(database, character_id).values():
        for effect in definition["effects"]:
            if "weapon_category" in effect and effect["weapon_category"] not in weapons:
                continue
            if "armor_category" in effect and effect["armor_category"] not in armor:
                continue
            totals[effect["type"]] += effect_value(effect, rank)
    for key, cap in EFFECT_CAPS.items():
        totals[key] = min(totals[key], cap)
    return totals


def _skill_categories(definition):
    return set(definition["proficiencies"]["weapons"]) | set(definition["proficiencies"]["armor"])


def practice(database, character_id, trigger, amount=1, *, categories=None, skill_key=None):
    """Advance skills that respond to a trigger; returns player-facing notices."""
    if trigger not in TRIGGERS:
        raise ValueError("Unknown practice trigger.")
    level = highest_level(database, character_id)
    profile = origin_profile(database, character_id)
    notices = []
    for key, (definition, rank) in active_ranks(database, character_id).items():
        if skill_key is not None and key != skill_key:
            continue
        if trigger not in definition["practice"]["triggers"]:
            continue
        if (categories is not None and trigger in CATEGORY_TRIGGERS and _skill_categories(definition)
                and not _skill_categories(definition) & set(categories)):
            continue
        row = database.execute("SELECT rank,experience FROM mud_character_skills "
                               "WHERE character_id=? AND skill_key=?", (character_id, key)).fetchone()
        rank, experience = row["rank"], row["experience"]
        cap = rank_cap(definition, level)
        if rank >= definition["max_rank"]:
            continue
        start, held = rank, experience
        experience += practice_gain(amount, definition["practice"]["rate"], affinity(profile, definition))
        while rank < cap and experience >= next_rank_cost(rank):
            experience -= next_rank_cost(rank)
            rank += 1
        if rank > start:
            notices.append(f"Your {definition['name']} skill rises to rank {rank}.")
        if rank >= cap:
            # Practice stalls one point short of the next rank until the character levels.
            ceiling = next_rank_cost(rank) - 1
            if rank < definition["max_rank"] and experience >= ceiling and (rank > start or held < ceiling):
                notices.append(f"Your {definition['name']} skill can advance no further until you "
                               f"reach level {rank - RANK_LEVEL_ALLOWANCE + 1}.")
            experience = min(experience, ceiling)
        database.execute("UPDATE mud_character_skills SET rank=?,experience=? WHERE character_id=? AND skill_key=?",
                         (rank, experience, character_id, key))
    return notices


def requirement_problems(definition, *, level, attributes, race_key, class_key, ranks):
    """Describe unmet requirements; an empty list means the skill may be learned."""
    requirements = definition["requirements"]
    problems = []
    if level < requirements["level"]:
        problems.append(f"level {requirements['level']}")
    for name, value in requirements["attributes"].items():
        if attributes.get(name, 0) < value:
            problems.append(f"{name.title()} {value}")
    if requirements["races"] and race_key not in requirements["races"]:
        problems.append("a specific race")
    if requirements["classes"] and class_key not in requirements["classes"]:
        problems.append("a specific class")
    for key, value in requirements["skills"].items():
        if ranks.get(key, 0) < value:
            problems.append(f"{key} rank {value}")
    return problems


def grant(database, character_id, grants, source="granted"):
    """Raise or insert granted ranks for enabled skills, never lowering earned ranks."""
    active = definitions(database)
    granted = []
    for key, rank in grants.items():
        if key not in active:
            continue
        rank = min(rank, active[key]["max_rank"])
        current = database.execute("SELECT rank FROM mud_character_skills WHERE character_id=? AND skill_key=?",
                                   (character_id, key)).fetchone()
        if current is None:
            database.execute("INSERT INTO mud_character_skills(character_id,skill_key,rank,source) VALUES(?,?,?,?)",
                             (character_id, key, rank, source))
            granted.append(key)
        elif current["rank"] < rank:
            database.execute("UPDATE mud_character_skills SET rank=? WHERE character_id=? AND skill_key=?",
                             (rank, character_id, key))
            granted.append(key)
    return granted


def origin_grants(*origins):
    grants = {}
    for origin in origins:
        for key, rank in (origin or {}).get("skills", {}).items():
            grants[key] = max(grants.get(key, 0), rank)
    return grants


def learn(database, character_id, key, *, context, trainer=False):
    """Learn a new skill at rank 1, spending skill points."""
    active = definitions(database)
    definition = active.get(key)
    if definition is None:
        raise ValueError("That skill is not available.")
    if database.execute("SELECT 1 FROM mud_character_skills WHERE character_id=? AND skill_key=?",
                        (character_id, key)).fetchone():
        raise ValueError(f"You already know {definition['name']}.")
    if definition["learn"]["trainer_required"] and not trainer:
        raise ValueError(f"{definition['name']} must be learned from a trainer.")
    problems = requirement_problems(definition, **context)
    if problems:
        raise ValueError(f"{definition['name']} requires {', '.join(problems)}.")
    cost = definition["learn"]["cost"]
    points = available_points(database, character_id)
    if cost > points:
        raise ValueError(f"Learning {definition['name']} costs {cost} skill point"
                         f"{'s' if cost != 1 else ''}; you have {points}.")
    database.execute("INSERT INTO mud_character_skills(character_id,skill_key,rank,source,points_spent) "
                     "VALUES(?,?,1,?,?)", (character_id, key, "trainer" if trainer else "learned", cost))
    return definition, cost


def raise_rank(database, character_id, key, *, use_points=True, limit=None):
    """Raise a known skill one rank with a skill point or a trainer's lesson."""
    active = definitions(database)
    definition = active.get(key)
    row = database.execute("SELECT rank FROM mud_character_skills WHERE character_id=? AND skill_key=?",
                           (character_id, key)).fetchone()
    if definition is None or row is None:
        raise ValueError("You do not know that skill.")
    cap = rank_cap(definition, highest_level(database, character_id))
    if limit is not None:
        cap = min(cap, limit)
    if row["rank"] >= cap:
        if limit is not None and row["rank"] >= limit:
            raise ValueError(f"This trainer cannot teach {definition['name']} beyond rank {limit}.")
        if row["rank"] >= definition["max_rank"]:
            raise ValueError(f"{definition['name']} is already at its maximum rank.")
        raise ValueError(f"Reach level {row['rank'] - RANK_LEVEL_ALLOWANCE + 1} to advance "
                         f"{definition['name']} further.")
    if use_points and available_points(database, character_id) < 1:
        raise ValueError("You have no skill points available.")
    database.execute("UPDATE mud_character_skills SET rank=rank+1,experience=0,points_spent=points_spent+? "
                     "WHERE character_id=? AND skill_key=?", (1 if use_points else 0, character_id, key))
    return definition, row["rank"] + 1


def describe_effects(definition, rank=None):
    """Readable per-rank (or current) effect summary."""
    parts = []
    for effect in definition["effects"]:
        label = EFFECT_TYPES[effect["type"]]
        if effect.get("weapon_category"):
            label += f" with {effect['weapon_category']} weapons"
        if effect.get("armor_category"):
            label += f" in {effect['armor_category']} armor"
        if rank is None:
            step = "rank" if effect["every"] == 1 else f"{effect['every']} ranks"
            cap = f", max {effect['max']}" if "max" in effect else ""
            parts.append(f"+{effect['per_rank']} {label} per {step}{cap}")
        else:
            parts.append(f"+{effect_value(effect, rank)} {label}")
    categories = sorted(_skill_categories(definition))
    if categories:
        parts.append("proficiency: " + ", ".join(categories))
    return "; ".join(parts)
