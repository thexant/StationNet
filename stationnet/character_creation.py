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
MAX_ORIGIN_PACK_BYTES = 1024 * 1024
MAX_ORIGINS_PER_PACK_KIND = 100
KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
# Growth an origin adds on top of the usual rules: (minimum, maximum) per key.
PROGRESSION_BOUNDS = {"health": (-50, 50), "mana": (-50, 50), "health_per_level": (-5, 5),
                      "mana_per_level": (-5, 5), "skill_points": (0, 10), "starting_skill_choices": (0, 3)}
# Damage taken from enemy blows, plus every hostile timed effect, can be resisted.
RESISTANCES = ("physical", "poison", "burning", "bleeding", "weakness", "vulnerability", "blinded", "snared")
MAX_ORIGIN_TAGS = 12
MAX_AFFINITIES = 12


def _built_in_options():
    path = Path(current_app.config.get("MUD_CHARACTER_OPTIONS") or
                Path(__file__).with_name("character_options.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    for group in (*KINDS, "skills"):
        keys = [item["key"] for item in data[group]]
        if len(keys) != len(set(keys)):
            raise ValueError(f"Duplicate {group} keys in character options.")
    return data


AVAILABILITY_KINDS = (*KINDS, "skills")


def default_availability():
    """What a world offers before a SYSOP chooses: every built-in race, class, and skill."""
    built_in = _built_in_options()
    return {kind: {item["key"] for item in built_in[kind]} for kind in AVAILABILITY_KINDS}


def world_availability(database, world):
    """The race, class, and skill keys one world offers; unconfigured worlds offer the built-ins."""
    chosen = {kind: set() for kind in AVAILABILITY_KINDS}
    for row in database.execute(
            "SELECT kind,definition_key FROM mud_world_availability WHERE world_key=?", (world,)):
        chosen[row["kind"]].add(row["definition_key"])
    return chosen if any(chosen.values()) else default_availability()


def availability_configured(database, world):
    return database.execute("SELECT 1 FROM mud_world_availability WHERE world_key=? LIMIT 1",
                            (world,)).fetchone() is not None


def set_world_availability(database, world, chosen):
    """Offer exactly the chosen installed definitions in one world; other worlds are untouched."""
    from . import mud_skills
    chosen = {kind: set(chosen.get(kind) or ()) for kind in AVAILABILITY_KINDS}
    origins = catalog(database, world="")
    for kind in KINDS:
        if chosen[kind] - {entry["key"] for entry in origins[kind]}:
            raise ValueError("Choose only installed races and classes.")
        if not chosen[kind]:
            raise ValueError("Offer at least one race and one class in every world.")
    skills = mud_skills.catalog(database, world="")
    if chosen["skills"] - {entry["key"] for entry in skills}:
        raise ValueError("Choose only installed skills.")
    mud_skills.require_available(database, chosen["skills"])
    database.execute("DELETE FROM mud_world_availability WHERE world_key=?", (world,))
    database.executemany(
        "INSERT INTO mud_world_availability(world_key,kind,definition_key) VALUES(?,?,?)",
        ((world, kind, key) for kind in AVAILABILITY_KINDS for key in sorted(chosen[kind])))
    return chosen


def playable_world_keys(database):
    """Worlds players can enter now: enabled authored worlds, plus the dungeon world when it is on."""
    from . import mud_scope
    keys = [row["id"] for row in database.execute("SELECT id FROM mud_world_packages WHERE enabled=1 ORDER BY id")]
    station = database.execute("SELECT mud_procedural_enabled FROM station WHERE id=1").fetchone()
    if station is not None and station["mud_procedural_enabled"]:
        keys.append(mud_scope.PROCEDURAL)
    return keys


def offer(database, kind, keys, worlds=None):
    """Add newly installed definitions to some worlds' selections (by default, every playable world)."""
    worlds = playable_world_keys(database) if worlds is None else worlds
    for world in worlds:
        if not availability_configured(database, world):
            # Make the built-in default explicit before adding to it.
            database.executemany(
                "INSERT OR IGNORE INTO mud_world_availability(world_key,kind,definition_key) VALUES(?,?,?)",
                ((world, group, key) for group, defaults in default_availability().items() for key in defaults))
        database.executemany(
            "INSERT OR IGNORE INTO mud_world_availability(world_key,kind,definition_key) VALUES(?,?,?)",
            ((world, kind, key) for key in keys))


def forget_definition(database, kind, key):
    """A deleted definition leaves every world's selection; emptied worlds fall back to defaults."""
    database.execute("DELETE FROM mud_world_availability WHERE kind=? AND definition_key=?", (kind, key))


def restore_world_defaults(database):
    """Refill any configured world whose race, class, or starting skill choices ran out."""
    from . import mud_skills
    built_in = default_availability()
    restored = []
    for row in database.execute("SELECT DISTINCT world_key FROM mud_world_availability").fetchall():
        world = row["world_key"]
        chosen = world_availability(database, world)
        installed = {kind: {entry["key"] for entry in catalog(database, world="")[kind]} for kind in KINDS}
        installed["skills"] = {entry["key"] for entry in mud_skills.catalog(database, world="")}
        chosen = {kind: chosen[kind] & installed[kind] for kind in AVAILABILITY_KINDS}
        changed = False
        for kind in KINDS:
            if not chosen[kind]:
                chosen[kind] = set(built_in[kind])
                changed = True
        try:
            mud_skills.require_available(database, chosen["skills"])
        except ValueError:
            chosen["skills"] |= built_in["skills"]
            changed = True
        if changed:
            set_world_availability(database, world, chosen)
            restored.append(world)
    return restored


def catalog(database=None, *, include_removed=False, world=None):
    """Return server-side metadata and definitions for every managed origin.

    ``enabled`` says whether a definition is offered in ``world`` (the current
    MUD world when omitted). Outside any world it means only "installed".
    """
    from . import mud_scope
    if database is None:
        from .db import get_db
        database = get_db()
    world = mud_scope.get() if world is None else world
    available = world_availability(database, world) if world else None
    built_in = _built_in_options()
    rows = {(row["kind"], row["key"]): row for row in database.execute(
        "SELECT kind,key,definition_json,imported,installed FROM mud_origin_definitions"
    )}
    entries = {kind: [] for kind in KINDS}
    for kind in KINDS:
        for definition in built_in[kind]:
            rows.pop((kind, definition["key"]), None)
            entries[kind].append({"kind": kind, "key": definition["key"],
                                  "definition": definition, "imported": False,
                                  "installed": True,
                                  "enabled": available is None or definition["key"] in available[kind]})
    for (kind, key), row in rows.items():
        if not row["imported"] or (not row["installed"] and not include_removed):
            continue
        entries[kind].append({"kind": kind, "key": key,
                              "definition": json.loads(row["definition_json"]),
                              "imported": True, "installed": bool(row["installed"]),
                              "enabled": bool(row["installed"]) and (available is None or key in available[kind])})
    for kind in KINDS:
        entries[kind].sort(key=lambda entry: entry["definition"]["name"].casefold())
    return entries


def options(database=None, *, include_inactive=False, include_removed=False):
    """Origins plus the enabled skills offered at creation."""
    from . import mud_skills
    if database is None:
        from .db import get_db
        database = get_db()
    data = _built_in_options()
    entries = catalog(database, include_removed=include_removed)
    for kind in KINDS:
        data[kind] = [entry["definition"] for entry in entries[kind]
                      if include_inactive or entry["enabled"]]
    data["skills"] = [definition for definition in mud_skills.definitions(database).values()
                      if definition["starting"]]
    from . import mud_abilities
    data["ability_names"] = mud_abilities.names(database)
    return data


def _text(value, label, maximum, minimum=1):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{label} must be {minimum}-{maximum} characters.")
    return value.strip()


def validate_origin(kind, document, database, *, check_key=True, extra_abilities=()):
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
    from . import mud_skills
    for field, allowed, bound in (("attributes", ATTRIBUTES, 10),
                                   ("effects", tuple(mud_skills.EFFECT_TYPES), 100)):
        values = document.get(field, {})
        if not isinstance(values, dict) or any(
            name not in allowed or type(value) is not int or abs(value) > bound
            for name, value in values.items()
        ):
            raise ValueError(f"{field} must contain supported keys ({', '.join(allowed)}) "
                             f"with integer values within {bound}.")
        definition[field] = values
    skills = {entry["key"] for entry in mud_skills.catalog(database)}
    # Optional fields are stored only when present so older definitions compare unchanged.
    progression = document.get("progression", {})
    if not isinstance(progression, dict) or any(
        name not in PROGRESSION_BOUNDS or type(value) is not int
        or not PROGRESSION_BOUNDS[name][0] <= value <= PROGRESSION_BOUNDS[name][1]
        for name, value in progression.items()
    ):
        raise ValueError("progression may contain " + ", ".join(
            f"{name} ({low} to {high})" for name, (low, high) in PROGRESSION_BOUNDS.items()) + ".")
    if progression:
        definition["progression"] = progression
    proficiencies = document.get("proficiencies", {})
    if not isinstance(proficiencies, dict) or set(proficiencies) - {"weapons", "armor"}:
        raise ValueError("proficiencies may contain weapons and armor category lists.")
    proficiencies = {kind: mud_skills._key_list(proficiencies.get(kind), f"{kind} proficiencies")
                     for kind in ("weapons", "armor")}
    if any(proficiencies.values()):
        definition["proficiencies"] = proficiencies
    affinities = document.get("skill_affinities", {})
    if not isinstance(affinities, dict) or len(affinities) > MAX_AFFINITIES or any(
        (key not in skills and key not in mud_skills.GROUPS) or type(value) is not int
        or not -50 <= value <= 200 for key, value in affinities.items()
    ):
        raise ValueError(f"skill_affinities must map up to {MAX_AFFINITIES} installed skill keys or skill "
                         f"groups ({', '.join(mud_skills.GROUPS)}) to percentages from -50 to 200.")
    if affinities:
        definition["skill_affinities"] = affinities
    resistances = document.get("resistances", {})
    if not isinstance(resistances, dict) or any(
        name not in RESISTANCES or type(value) is not int or abs(value) > 100
        for name, value in resistances.items()
    ):
        raise ValueError(f"resistances must map {', '.join(RESISTANCES)} to percentages from -100 to 100.")
    if resistances:
        definition["resistances"] = resistances
    tags = mud_skills._key_list(document.get("tags"), "tags", MAX_ORIGIN_TAGS)
    if tags:
        definition["tags"] = tags
    grants = document.get("skills", {})
    if not isinstance(grants, dict) or len(grants) > 10 or any(
        key not in skills or type(rank) is not int or not 1 <= rank <= 5 for key, rank in grants.items()
    ):
        raise ValueError("Granted skills must map installed skill keys to ranks from 1 to 5.")
    if grants:
        definition["skills"] = grants
    from . import mud_abilities
    from .mud import EQUIPMENT_SLOTS
    if kind == "classes":
        skill = document.get("skill")
        if not isinstance(skill, str) or skill not in skills:
            raise ValueError("Class favored skill must be a supported skill.")
        definition["skill"] = skill
    # Races may carry innate abilities and heirloom gear too; classes always list both.
    spells = document.get("spells", [])
    abilities = set(mud_abilities.catalog(database)) | set(extra_abilities)
    if (not isinstance(spells, list) or len(spells) > 12
            or any(not isinstance(spell, str) for spell in spells) or len(set(spells)) != len(spells)):
        raise ValueError("Spells must be a list of up to 12 distinct ability keys.")
    missing = [spell for spell in spells if spell not in abilities]
    if missing:
        raise ValueError(f"Spells must be installed or bundled abilities; unknown: {', '.join(missing)}. "
                         f"Built-in abilities are {', '.join(mud_abilities.BUILT_IN)}.")
    if spells or kind == "classes":
        definition["spells"] = spells
    equipment = document.get("equipment", [])
    if not isinstance(equipment, list) or len(equipment) > len(EQUIPMENT_SLOTS):
        raise ValueError("Equipment must be a list of up to eight items.")
    parsed = []
    for item in equipment:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not KEY_RE.fullmatch(item["key"]):
            raise ValueError("Each equipment item needs a valid key.")
        if item.get("slot") not in EQUIPMENT_SLOTS:
            raise ValueError("Equipment must use a supported slot.")
        parsed_item = {"key": item["key"], "name": _text(item.get("name"), "Equipment name", 80, 2),
                       "description": _text(item.get("description"), "Equipment description", 1000),
                       "slot": item["slot"]}
        if "category" in item:
            if not isinstance(item["category"], str) or not KEY_RE.fullmatch(item["category"]):
                raise ValueError("Equipment category must be a lowercase key such as blade.")
            parsed_item["category"] = item["category"]
        for stat in ("attack", "defense"):
            value = item.get(stat, 0)
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError("Equipment attack and defense must be integers from 0 to 100.")
            parsed_item[stat] = value
        for stat in ("accuracy", "evasion"):
            if stat in item:
                if type(item[stat]) is not int or abs(item[stat]) > 20:
                    raise ValueError("Equipment accuracy and evasion must be integers from -20 to 20.")
                parsed_item[stat] = item[stat]
        parsed.append(parsed_item)
    if len({item["key"] for item in parsed}) != len(parsed):
        raise ValueError(f"Equipment keys must be distinct within a {KIND_NAMES[kind]}.")
    if parsed or kind == "classes":
        definition["equipment"] = parsed
    if check_key and any(entry["key"] == key for entry in catalog(database, include_removed=True)[kind]):
        raise ValueError(f"A {KIND_NAMES[kind]} with that key already exists or was used by a character.")
    return definition


def character_origins(database, character_id):
    """The race and class definitions a character uses, including disabled or removed ones."""
    row = database.execute(
        "SELECT race_key,class_key FROM mud_characters WHERE id=?", (character_id,)
    ).fetchone()
    if row is None:
        return None, None
    config = options(database, include_inactive=True, include_removed=True)
    return tuple(next((item for item in config[group] if item["key"] == row[key]), None)
                 for group, key in (("races", "race_key"), ("classes", "class_key")))


def origin_profile(*origins):
    """Merge race and class features; missing optional fields count as empty."""
    profile = {"effects": {}, "progression": {}, "resistances": {}, "skill_affinities": {},
               "proficiencies": {"weapons": [], "armor": []}, "tags": [], "spells": []}
    # Class spells come first so the familiar spell order is kept.
    for origin in sorted((origin for origin in origins if origin), key=lambda origin: "skill" not in origin):
        for field in ("effects", "progression", "resistances", "skill_affinities"):
            for key, value in origin.get(field, {}).items():
                profile[field][key] = profile[field].get(key, 0) + value
        for kind in ("weapons", "armor"):
            for category in origin.get("proficiencies", {}).get(kind, []):
                if category not in profile["proficiencies"][kind]:
                    profile["proficiencies"][kind].append(category)
        for field in ("tags", "spells"):
            for key in origin.get(field, []):
                if key not in profile[field]:
                    profile[field].append(key)
    profile["resistances"] = {key: max(-100, min(100, value)) for key, value in profile["resistances"].items()}
    return profile


def origin_facts(database, character_id):
    """What item requirements and tag conditions compare against: keys and tags as sets."""
    row = database.execute(
        "SELECT race_key,class_key FROM mud_characters WHERE id=?", (character_id,)
    ).fetchone()
    tags = origin_profile(*character_origins(database, character_id))["tags"]
    return {"races": {row["race_key"]} - {None} if row else set(),
            "classes": {row["class_key"]} - {None} if row else set(), "tags": set(tags)}


def origin_resources(profile, level):
    """Maximum health and mana an origin adds at a level."""
    growth = profile["progression"]
    return (growth.get("health", 0) + (level - 1) * growth.get("health_per_level", 0),
            growth.get("mana", 0) + (level - 1) * growth.get("mana_per_level", 0))


def sync_origin_resources(database, character_id):
    """Bring each track's origin health and mana in line with the current definitions.

    Returns True when a row changed. Progression rows remember the amount already applied, so this is idempotent and
    also absorbs origin swaps, level gains, and rebalanced built-in definitions.
    """
    profile = origin_profile(*character_origins(database, character_id))
    changed = False
    for row in database.execute(
        "SELECT track_key,level,current_health,maximum_health,current_mana,maximum_mana,"
        "origin_health,origin_mana FROM mud_character_progression WHERE character_id=?", (character_id,)
    ).fetchall():
        health, mana = origin_resources(profile, row["level"])
        if (health, mana) == (row["origin_health"], row["origin_mana"]):
            continue
        maximum = max(1, row["maximum_health"] + health - row["origin_health"])
        # A fallen character stays at zero; anyone standing keeps at least one point.
        current = (min(maximum, max(1, row["current_health"] + health - row["origin_health"]))
                   if row["current_health"] > 0 else 0)
        maximum_mana = max(1, row["maximum_mana"] + mana - row["origin_mana"])
        current_mana = min(maximum_mana, max(0, row["current_mana"] + mana - row["origin_mana"]))
        database.execute(
            "UPDATE mud_character_progression SET maximum_health=?,current_health=?,maximum_mana=?,"
            "current_mana=?,origin_health=?,origin_mana=? WHERE character_id=? AND track_key=?",
            (maximum, current, maximum_mana, current_mana, health, mana, character_id, row["track_key"]),
        )
        changed = True
    return changed


def starting_skill_count(config, race, archetype):
    """Starting skill picks: the configured number plus any origin bonus."""
    bonus = origin_profile(race, archetype)["progression"].get("starting_skill_choices", 0)
    return config["starting_skill_choices"] + bonus


def import_origin(database, kind, payload, worlds=None):
    if not payload or len(payload) > MAX_ORIGIN_BYTES:
        raise ValueError("Choose a JSON file no larger than 32 KB.")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("The origin file must contain valid UTF-8 JSON.") from error
    definition = validate_origin(kind, document, database)
    if any(entry["installed"] and
           entry["definition"]["name"].casefold() == definition["name"].casefold()
           for entry in catalog(database, include_removed=True)[kind]):
        raise ValueError(f"A {KIND_NAMES[kind]} with that name is already installed.")
    database.execute(
        "INSERT INTO mud_origin_definitions(kind,key,definition_json,imported,installed,enabled) "
        "VALUES(?,?,?,1,1,1)",
        (kind, definition["key"], json.dumps(definition, separators=(",", ":"))),
    )
    offer(database, kind, [definition["key"]], worlds)
    return definition


def import_pack(database, payload, worlds=None):
    """Validate and install a races/classes document as one atomic unit."""
    if not payload or len(payload) > MAX_ORIGIN_PACK_BYTES:
        raise ValueError("Choose a JSON pack no larger than 1 MB.")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("The origin pack must contain valid UTF-8 JSON.") from error
    if not isinstance(document, dict) or not document or set(document) - set(KINDS):
        raise ValueError("An origin pack must contain only races and/or classes arrays.")

    existing = catalog(database, include_removed=True)
    existing_names = {
        kind: {entry["definition"]["name"].casefold() for entry in existing[kind]
               if entry["installed"]}
        for kind in KINDS
    }
    definitions = {kind: [] for kind in KINDS}
    for kind in KINDS:
        sources = document.get(kind, [])
        if not isinstance(sources, list) or len(sources) > MAX_ORIGINS_PER_PACK_KIND:
            raise ValueError(
                f"Pack {kind} must be an array of at most {MAX_ORIGINS_PER_PACK_KIND} objects."
            )
        seen_keys = set()
        seen_names = set()
        for index, source in enumerate(sources, 1):
            try:
                definition = validate_origin(kind, source, database)
            except ValueError as error:
                raise ValueError(f"Invalid {KIND_NAMES[kind]} at {kind}[{index}]: {error}") from error
            folded_name = definition["name"].casefold()
            if definition["key"] in seen_keys:
                raise ValueError(
                    f"Duplicate {KIND_NAMES[kind]} key in pack: {definition['key']}."
                )
            if folded_name in seen_names:
                raise ValueError(
                    f"Duplicate {KIND_NAMES[kind]} name in pack: {definition['name']}."
                )
            if folded_name in existing_names[kind]:
                raise ValueError(
                    f"A {KIND_NAMES[kind]} named {definition['name']} is already installed."
                )
            seen_keys.add(definition["key"])
            seen_names.add(folded_name)
            definitions[kind].append(definition)
    if not any(definitions.values()):
        raise ValueError("The origin pack does not contain any races or classes.")

    for kind in KINDS:
        for definition in definitions[kind]:
            database.execute(
                "INSERT INTO mud_origin_definitions(kind,key,definition_json,imported,installed,enabled) "
                "VALUES(?,?,?,1,1,1)",
                (kind, definition["key"], json.dumps(definition, separators=(",", ":"))),
            )
        offer(database, kind, [definition["key"] for definition in definitions[kind]], worlds)
    return definitions


def remove_origins(database, selected):
    """Remove installed custom origins from the station and every world's selection.

    Definitions a character (live or archived) still uses are retained as
    removed rather than deleted, so saved characters keep working.
    """
    entries = catalog(database, world="")
    removable = {
        (kind, entry["key"]): entry
        for kind in KINDS for entry in entries[kind] if entry["imported"]
    }
    requested = {(kind, key) for kind in KINDS for key in selected.get(kind, set())}
    if not requested:
        raise ValueError("Select at least one imported race or class to remove.")
    unknown = requested - set(removable)
    if unknown:
        raise ValueError("Select only installed imported races and classes to remove.")

    removed = []
    for kind in KINDS:
        for key in sorted(selected.get(kind, set())):
            entry = removable[(kind, key)]
            column = "race_key" if kind == "races" else "class_key"
            used = database.execute(
                f"SELECT 1 FROM mud_characters WHERE {column}=? LIMIT 1", (key,)
            ).fetchone()
            archived = any(
                json.loads(row["snapshot_json"]).get("character", {}).get(column) == key
                for row in database.execute("SELECT snapshot_json FROM mud_campaign_characters")
            )
            if used or archived:
                database.execute(
                    "UPDATE mud_origin_definitions SET installed=0,enabled=0 WHERE kind=? AND key=?",
                    (kind, key),
                )
            else:
                database.execute(
                    "DELETE FROM mud_origin_definitions WHERE kind=? AND key=?", (kind, key)
                )
            database.execute(
                "DELETE FROM mud_world_origins WHERE kind=? AND origin_key=?", (kind, key)
            )
            database.execute(
                "DELETE FROM mud_dungeon_origins WHERE kind=? AND origin_key=?", (kind, key)
            )
            forget_definition(database, kind, key)
            removed.append(entry)
    # Callers then run restore_world_defaults: a world may have offered only these choices.
    return removed


def removable_associations(database, linked):
    """Find installed origins linked only to the content being removed."""
    entries = catalog(database, world="")
    result = {kind: [] for kind in KINDS}
    for kind in KINDS:
        lookup = {entry["key"]: entry for entry in entries[kind] if entry["imported"]}
        for key in sorted(linked[kind]):
            entry = lookup.get(key)
            if entry is None:
                continue
            world_ref = database.execute(
                "SELECT 1 FROM mud_world_origins WHERE kind=? AND origin_key=? AND world_id!=? LIMIT 1",
                (kind, key, linked.get("world_id", "")),
            ).fetchone()
            dungeon_ref = database.execute(
                "SELECT 1 FROM mud_dungeon_origins WHERE kind=? AND origin_key=? AND theme_id!=? LIMIT 1",
                (kind, key, linked.get("theme_id", "")),
            ).fetchone()
            if not world_ref and not dungeon_ref:
                result[kind].append(entry)
    return result


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
    final = {key: allocations[key] + race.get("attributes", {}).get(key, 0)
             + archetype.get("attributes", {}).get(key, 0) for key in ATTRIBUTES}
    if any(value < 1 or value > 30 for value in final.values()):
        raise ValueError("An attribute modifier is outside the supported range.")
    skills = payload.get("skills")
    eligible = eligible_skills(config, race, archetype, final)
    required = min(starting_skill_count(config, race, archetype), len(eligible))
    if (not isinstance(skills, list) or any(not isinstance(skill, str) for skill in skills)
            or len(skills) != required
            or len(set(skills)) != len(skills) or not set(skills) <= {item["key"] for item in eligible}):
        raise ValueError("Choose the required number of distinct starting skills.")
    return name, race, archetype, allocations, final, skills


def eligible_skills(config, race, archetype, attributes):
    """Starting skills this race, class, and attribute spread may choose."""
    from . import mud_skills
    grants = mud_skills.origin_grants(race, archetype)
    return [skill for skill in config["skills"] if skill["key"] not in grants and
            not mud_skills.requirement_problems(skill, level=1, attributes=attributes,
                                                race_key=race["key"], class_key=archetype["key"],
                                                ranks=grants)]


def ensure_character(database, user_id, world=None):
    """The account's character in a world, created pending on first visit.

    Each world has its own character. An account's first character keeps the
    original ID format; later ones append the world key.
    """
    from . import mud_scope
    world = mud_scope.current() if world is None else world
    row = database.execute("SELECT id FROM mud_characters WHERE user_id=? AND world_key=?",
                           (user_id, world)).fetchone()
    if row is not None:
        return row["id"]
    user = database.execute("SELECT callsign FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        raise ValueError("Unknown StationNet account.")
    character_id = f"stationnet:character:{user_id}"
    if database.execute("SELECT 1 FROM mud_characters WHERE id=?", (character_id,)).fetchone():
        character_id = f"stationnet:character:{user_id}:{world}"
    database.execute(
        "INSERT INTO mud_characters(id,user_id,world_key,display_name) VALUES(?,?,?,?)",
        (character_id, user_id, world, user["callsign"]),
    )
    database.execute("INSERT OR IGNORE INTO mud_character_progression(character_id,track_key) VALUES(?,?)",
                     (character_id, mud_scope.mode(world)))
    return character_id


def find_character(database, user_id, world=None):
    """The account's character row in a world, or None before its first visit."""
    from . import mud_scope
    return database.execute("SELECT * FROM mud_characters WHERE user_id=? AND world_key=?",
                            (user_id, mud_scope.current() if world is None else world)).fetchone()


def complete(database, user_id, payload):
    """Apply a pending build atomically for the account's character in the current world."""
    from . import mud_scope
    world = mud_scope.current()
    possession_mode = mud_scope.mode(world)
    config = options(database)
    name, race, archetype, allocated, attributes, skills = validate(payload, config)
    ensure_character(database, user_id, world)
    row = find_character(database, user_id, world)
    if row["creation_state"] not in ("pending", "legacy"):
        raise ValueError("This character has already been created.")
    # Names only need to be distinct within a world.
    collision = database.execute(
        "SELECT 1 FROM mud_characters WHERE display_name=? COLLATE NOCASE AND world_key=? AND user_id<>?",
        (name, world, user_id),
    ).fetchone()
    if collision:
        raise ValueError("That character name is already in use.")
    health_bonus = (attributes["vitality"] - 10) * 5
    mana_bonus = (attributes["intellect"] - 10) * 3
    for track in (possession_mode,):
        progression = database.execute(
            "SELECT current_health,maximum_health,current_mana,maximum_mana,attribute_points,"
            "trained_attributes_json FROM mud_character_progression "
            "WHERE character_id=? AND track_key=?", (row["id"], track),
        ).fetchone()
        if progression is None:
            continue
        # Base attributes are rewritten, so refund any training rather than lose it.
        try:
            trained = json.loads(progression["trained_attributes_json"] or "{}")
        except json.JSONDecodeError:
            trained = {}
        refund = sum(value for value in trained.values() if type(value) is int and value > 0)             if isinstance(trained, dict) else 0
        maximum = max(1, progression["maximum_health"] + health_bonus)
        current = min(maximum, max(0, progression["current_health"] + health_bonus))
        maximum_mana = max(1, progression["maximum_mana"] + mana_bonus)
        current_mana = min(maximum_mana, max(0, progression["current_mana"] + mana_bonus))
        database.execute(
            "UPDATE mud_character_progression SET strength=?,agility=?,intellect=?,vitality=?,"
            "maximum_health=?,current_health=?,maximum_mana=?,current_mana=?,"
            "attribute_points=?,trained_attributes_json='{}',updated_at=CURRENT_TIMESTAMP "
            "WHERE character_id=? AND track_key=?",
            (*[attributes[key] for key in ATTRIBUTES], maximum, current,
             maximum_mana, current_mana, progression["attribute_points"] + refund, row["id"], track),
        )
    from . import mud_skills
    for skill in skills:
        database.execute(
            "INSERT INTO mud_character_skills(character_id,skill_key,rank) VALUES(?,?,?)",
            (row["id"], skill, 2 if archetype.get("skill") == skill else 1),
        )
    mud_skills.grant(database, row["id"], mud_skills.origin_grants(race, archetype))
    # One grant during creation; legacy possessions and equipment are untouched.
    for origin in (race, archetype):
        for item in origin.get("equipment", []):
            item_hash = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:12]
            owner = origin["key"] if origin is archetype else f"race:{origin['key']}"
            definition_id = f"origin:{config['id']}:{owner}:{item['key']}:{item_hash}"
            properties = {"attack": item.get("attack", 0), "defense": item.get("defense", 0),
                          **{key: item[key] for key in ("category", "accuracy", "evasion") if key in item}}
            database.execute(
                "INSERT OR IGNORE INTO mud_item_definitions"
                "(id,name,description,item_type,properties_json,equipment_slot) "
                "VALUES(?,?,?,'equipment',?,?)",
                (definition_id, item["name"], item["description"], json.dumps(properties), item["slot"]),
            )
            database.execute(
                "INSERT INTO mud_item_instances(id,definition_id,owner_character_id,possession_mode) "
                "VALUES(?,?,?,?)",
                (f"origin:item:{uuid.uuid4().hex}", definition_id, row["id"], possession_mode),
            )
    database.execute(
        "UPDATE mud_characters SET display_name=?,race_key=?,class_key=?,base_attributes_json=?,"
        "creation_state='complete',"
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND creation_state IN ('pending','legacy')",
        (name, race["key"], archetype["key"], json.dumps(allocated), row["id"]),
    )
    sync_origin_resources(database, row["id"])
    return name


def skill_ranks(database, character_id):
    return {row["skill_key"]: row["rank"] for row in database.execute(
        "SELECT skill_key,rank FROM mud_character_skills WHERE character_id=?", (character_id,)
    )}


def practice(database, character_id, skill_key, amount=1):
    """Practice one known skill as if its first trigger fired; returns notices."""
    from . import mud_skills
    definition = mud_skills.definitions(database).get(skill_key)
    triggers = (definition or {}).get("practice", {}).get("triggers", [])
    if not triggers:
        return []
    return mud_skills.practice(database, character_id, triggers[0], amount, skill_key=skill_key)
