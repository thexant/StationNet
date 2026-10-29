"""Shared, turn based MUD effects used by spells and content definitions.

Content uses ``{"key": "poison", "potency": 2, "duration": 3}``.
Applying the same key refreshes duration and keeps the stronger potency.

Built-in effects live here; packages may install more, built only from the
fixed ``KINDS`` below. A custom effect never adds a new mechanic.
"""
import json

# What an effect does each turn (damage, regen) or which stat it shifts.
KINDS = ("damage", "regen", "attack", "defense", "accuracy", "evasion")
STAT_KINDS = ("attack", "defense", "accuracy", "evasion")
# Effects marked outside_combat advance one turn this often while their target is out of combat.
PULSE_SECONDS = 30
# Built-ins are named by their key so long-standing messages read the same.
EFFECTS = {
    "poison": {"kind": "damage"},
    "burning": {"kind": "damage"},
    "bleeding": {"kind": "damage"},
    "might": {"kind": "attack", "sign": 1},
    "weakness": {"kind": "attack", "sign": -1},
    "ward": {"kind": "defense", "sign": 1},
    "vulnerability": {"kind": "defense", "sign": -1},
    "focus": {"kind": "accuracy", "sign": 1},
    "blinded": {"kind": "accuracy", "sign": -1},
    "evasive": {"kind": "evasion", "sign": 1},
    "snared": {"kind": "evasion", "sign": -1},
    "regeneration": {"kind": "regen"},
}
for _key, _definition in EFFECTS.items():
    _definition.update(key=_key, name=_key, description="")


def harmful(definition):
    """Damage and stat penalties hurt the target; regen and stat bonuses help it."""
    return definition["kind"] == "damage" or definition.get("sign") == -1


def normalize_effect(document):
    """Validate one effect definition; only fixed kinds are supported."""
    from . import character_creation
    if not isinstance(document, dict):
        raise ValueError("An effect must be a JSON object.")
    unknown = set(document) - {"key", "name", "description", "kind", "sign", "resist_as", "outside_combat"}
    if unknown:
        raise ValueError(f"Unsupported effect fields: {', '.join(sorted(unknown))}.")
    key = document.get("key")
    if not isinstance(key, str) or not character_creation.KEY_RE.fullmatch(key):
        raise ValueError("Effect key must be 2-40 lowercase letters, digits, or hyphens, starting with a letter.")
    kind = document.get("kind")
    if kind not in KINDS:
        raise ValueError(f"Effect kind must be one of: {', '.join(KINDS)}.")
    definition = {"key": key, "name": character_creation._text(document.get("name"), "Effect name", 40, 2),
                  "description": "", "kind": kind}
    if document.get("description"):
        definition["description"] = character_creation._text(document["description"], "Effect description", 500)
    if kind in STAT_KINDS:
        if document.get("sign") not in (1, -1) or type(document.get("sign")) is not int:
            raise ValueError("Stat effects need sign 1 (bonus) or -1 (penalty).")
        definition["sign"] = document["sign"]
    elif "sign" in document:
        raise ValueError("Only attack, defense, accuracy, and evasion effects take a sign.")
    if "resist_as" in document:
        if document["resist_as"] not in character_creation.RESISTANCES:
            raise ValueError(f"resist_as must be one of: {', '.join(character_creation.RESISTANCES)}.")
        if not harmful(definition):
            raise ValueError("Only harmful effects (damage, or a sign of -1) can be resisted.")
        definition["resist_as"] = document["resist_as"]
    if "outside_combat" in document:
        if type(document["outside_combat"]) is not bool:
            raise ValueError("outside_combat must be true or false.")
        if document["outside_combat"]:
            definition["outside_combat"] = True
    return definition


def catalog(database, *, include_removed=False):
    """Every effect by key: built-ins, then installed definitions."""
    result = {key: {**definition, "imported": False, "installed": True} for key, definition in EFFECTS.items()}
    if database is None:
        return result
    for row in database.execute("SELECT key,definition_json,installed FROM mud_effect_definitions"):
        if row["key"] in result or (not row["installed"] and not include_removed):
            continue
        result[row["key"]] = {**json.loads(row["definition_json"]), "imported": True,
                              "installed": bool(row["installed"])}
    return result


def _lookup(database, key):
    """One effect definition, or None; removed definitions still resolve for active effects."""
    if key in EFFECTS:
        return EFFECTS[key]
    if database is None:
        return None
    row = database.execute("SELECT definition_json FROM mud_effect_definitions WHERE key=?", (key,)).fetchone()
    return json.loads(row["definition_json"]) if row else None


def validate(spec, database=None, extra_keys=()):
    """Check one applied effect; installed keys are known only when a database is given."""
    key = spec.get("key") if isinstance(spec, dict) else None
    if not isinstance(key, str) or not (key in extra_keys or key in catalog(database)):
        raise ValueError(f"Unknown effect key: {key}." if isinstance(key, str) else "Unknown effect key.")
    potency, duration = spec.get("potency", 1), spec.get("duration", 3)
    if type(potency) is not int or not 1 <= potency <= 1000:
        raise ValueError("Effect potency must be from 1 to 1000.")
    if type(duration) is not int or not 1 <= duration <= 100:
        raise ValueError("Effect duration must be from 1 to 100 turns.")
    return key, potency, duration


def display_name(database, key):
    definition = _lookup(database, key)
    return definition["name"] if definition else key


def resist_key(database, key):
    """The resistance that weakens an effect: its resist_as, or its own built-in key."""
    definition = _lookup(database, key)
    if definition is None:
        return None
    return definition.get("resist_as") or (key if key in EFFECTS else None)


def resistance_for(database, key, resistances):
    """A character's total resistance to one effect: its own key plus its resistance category.

    Gear may resist a world's effect by key, so a mask can stop one gas without
    stopping every poison.
    """
    category = resist_key(database, key)
    total = resistances.get(key, 0) + (resistances.get(category, 0) if category and category != key else 0)
    return max(-100, min(100, total))


def validate_resistances(value, database=None, extra_keys=(), label="resistances"):
    """Item resistances map a resistance category or an effect key to -100..100 percent."""
    from . import character_creation
    known = set(character_creation.RESISTANCES) | set(extra_keys) | set(catalog(database))
    if not isinstance(value, dict) or len(value) > 20 or any(
            not isinstance(key, str) or key not in known or type(percent) is not int or abs(percent) > 100
            for key, percent in value.items()):
        raise ValueError(f"{label} must map up to 20 resistance categories ({', '.join(character_creation.RESISTANCES)}) "
                         "or effect keys to percentages from -100 to 100.")
    return value


def validate_cure(spec, database=None, extra_keys=(), label="Cure"):
    """One cure: an effect or a resistance category, optionally only weakened or shortened.

    Without potency or turns the matching effects are removed outright.
    """
    from . import character_creation
    if not isinstance(spec, dict) or set(spec) - {"effect", "category", "potency", "turns"}:
        raise ValueError(f"{label} must be an object with effect or category, and optional potency and turns.")
    if ("effect" in spec) == ("category" in spec):
        raise ValueError(f"{label} needs exactly one of effect or category.")
    if "effect" in spec and not (spec["effect"] in extra_keys or spec["effect"] in catalog(database)):
        raise ValueError(f"{label} names unknown effect {spec['effect']!r}.")
    if "category" in spec and spec["category"] not in character_creation.RESISTANCES:
        raise ValueError(f"{label} category must be one of: {', '.join(character_creation.RESISTANCES)}.")
    for field, high in (("potency", 1000), ("turns", 100)):
        if field in spec and (type(spec[field]) is not int or not 1 <= spec[field] <= high):
            raise ValueError(f"{label} {field} must be from 1 to {high}.")
    return spec


def validate_cures(value, database=None, extra_keys=(), label="cures"):
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ValueError(f"{label} must be an array of 1 to 10 cures.")
    return [validate_cure(spec, database, extra_keys, f"{label}[{index}]") for index, spec in enumerate(value)]


def reduce(database, target_type, target_id, cure):
    """Remove, weaken, or shorten matching active effects; return (name, "cured"|"eased") pairs."""
    result = []
    for row, definition in _active(database, target_type, target_id):
        key = row["effect_key"]
        if "effect" in cure and key != cure["effect"]:
            continue
        if "category" in cure and resist_key(database, key) != cure["category"]:
            continue
        potency = row["potency"] - cure.get("potency", 0)
        turns = row["remaining_turns"] - cure.get("turns", 0)
        whole = "potency" not in cure and "turns" not in cure
        if whole or potency <= 0 or turns <= 0:
            database.execute("DELETE FROM mud_active_effects WHERE target_type=? AND target_id=? AND effect_key=?",
                             (target_type, target_id, key))
            result.append((definition["name"], "cured"))
        else:
            database.execute("UPDATE mud_active_effects SET potency=?,remaining_turns=? "
                             "WHERE target_type=? AND target_id=? AND effect_key=?",
                             (potency, turns, target_type, target_id, key))
            result.append((definition["name"], "eased"))
    return result


def resist(spec, percent):
    """Scale an incoming effect by a resistance percentage; None means it is shrugged off.

    Negative resistance is a vulnerability and strengthens the effect instead.
    """
    if not percent:
        return spec
    key, potency, duration = spec["key"], spec.get("potency", 1), spec.get("duration", 3)
    potency = potency * (100 - percent) // 100
    if potency <= 0:
        return None
    return {"key": key, "potency": min(1000, potency),
            "duration": max(1, min(100, duration * (100 - percent) // 100))}


def apply(database, target_type, target_id, spec, source=""):
    key, potency, duration = validate(spec, database)
    if target_type not in ("character", "encounter"):
        raise ValueError("Invalid effect target.")
    database.execute(
        "INSERT INTO mud_active_effects(target_type,target_id,effect_key,potency,remaining_turns,source) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(target_type,target_id,effect_key) DO UPDATE SET "
        "potency=MAX(potency,excluded.potency),"
        "remaining_turns=MAX(remaining_turns,excluded.remaining_turns),source=excluded.source",
        (target_type, target_id, key, potency, duration, str(source)[:100]),
    )
    return key


def list_effects(database, target_type, target_id):
    return [{**dict(row), "name": display_name(database, row["effect_key"])} for row in database.execute(
        "SELECT effect_key,potency,remaining_turns,source FROM mud_active_effects "
        "WHERE target_type=? AND target_id=? ORDER BY effect_key", (target_type, target_id),
    )]


def _active(database, target_type, target_id):
    """Active effects with their definitions; rows whose definition vanished are dropped."""
    result = []
    for row in list_effects(database, target_type, target_id):
        definition = _lookup(database, row["effect_key"])
        if definition is None:
            database.execute("DELETE FROM mud_active_effects WHERE target_type=? AND target_id=? AND effect_key=?",
                             (target_type, target_id, row["effect_key"]))
            continue
        result.append((row, definition))
    return result


def modifier(database, target_type, target_id, kind):
    return sum(definition.get("sign", 0) * row["potency"]
               for row, definition in _active(database, target_type, target_id) if definition["kind"] == kind)


def tick_detail(database, target_type, target_id, outside_combat_only=False):
    """Consume one turn; return per-turn damage and regen as (name, amount) pairs.

    Outside combat only effects marked ``outside_combat`` advance.
    """
    detail = {"damage": [], "regen": []}
    keys = []
    for row, definition in _active(database, target_type, target_id):
        if outside_combat_only and not definition.get("outside_combat"):
            continue
        keys.append(row["effect_key"])
        if definition["kind"] in detail:
            detail[definition["kind"]].append((definition["name"], row["potency"]))
    if not keys:
        return detail
    marks = ",".join("?" * len(keys))
    database.execute(
        f"DELETE FROM mud_active_effects WHERE target_type=? AND target_id=? AND remaining_turns=1 "
        f"AND effect_key IN ({marks})", (target_type, target_id, *keys),
    )
    database.execute(
        f"UPDATE mud_active_effects SET remaining_turns=remaining_turns-1 "
        f"WHERE target_type=? AND target_id=? AND effect_key IN ({marks})", (target_type, target_id, *keys),
    )
    return detail


def tick(database, target_type, target_id):
    """Consume one turn and return damage and names of damaging effects."""
    return tick_detail(database, target_type, target_id)["damage"]


def clear(database, target_type, target_id):
    database.execute("DELETE FROM mud_active_effects WHERE target_type=? AND target_id=?",
                     (target_type, target_id))


# Managed definitions ---------------------------------------------------------

def store(database, definition):
    database.execute("INSERT INTO mud_effect_definitions(key,definition_json,imported,installed) VALUES(?,?,1,1)",
                     (definition["key"], json.dumps(definition, separators=(",", ":"))))


# Fields an effect definition may carry; exports copy exactly these.
DEFINITION_FIELDS = ("key", "name", "description", "kind", "sign", "resist_as", "outside_combat")
# Item and NPC properties that apply effects; ``cures`` and ``resistances`` name them too.
APPLY_FIELDS = ("on_use_effects", "on_hit_effects")


def property_effect_keys(properties):
    """Every effect key that item or NPC properties apply, cure, or resist by key."""
    from . import character_creation
    if not isinstance(properties, dict):
        return set()
    keys = {spec["key"] for field in APPLY_FIELDS for spec in properties.get(field) or []
            if isinstance(properties.get(field), list) and isinstance(spec, dict) and isinstance(spec.get("key"), str)}
    if isinstance(properties.get("cures"), list):
        keys |= {spec["effect"] for spec in properties["cures"]
                 if isinstance(spec, dict) and isinstance(spec.get("effect"), str)}
    if isinstance(properties.get("resistances"), dict):
        keys |= {key for key in properties["resistances"]
                 if isinstance(key, str) and key not in character_creation.RESISTANCES}
    return keys


def remap_properties(properties, mapper):
    """Item or NPC properties with every named effect key renamed by mapper(key)."""
    from . import character_creation
    if not isinstance(properties, dict):
        return properties
    result = dict(properties)
    for field in APPLY_FIELDS:
        if isinstance(result.get(field), list):
            result[field] = [{**spec, "key": mapper(spec["key"])}
                             if isinstance(spec, dict) and isinstance(spec.get("key"), str) else spec
                             for spec in result[field]]
    if isinstance(result.get("cures"), list):
        result["cures"] = [{**spec, "effect": mapper(spec["effect"])}
                           if isinstance(spec, dict) and isinstance(spec.get("effect"), str) else spec
                           for spec in result["cures"]]
    if isinstance(result.get("resistances"), dict):
        result["resistances"] = {key if key in character_creation.RESISTANCES else mapper(key): value
                                 for key, value in result["resistances"].items()}
    return result


def validate_properties(properties, database=None, extra_keys=()):
    """Check the effect fields of item or NPC properties; return (field path, problem) pairs."""
    problems = []
    if not isinstance(properties, dict):
        return problems
    for field in APPLY_FIELDS:
        specs = properties.get(field)
        if specs is None:
            continue
        if not isinstance(specs, list) or len(specs) > 10:
            problems.append((field, f"{field} must be an array of up to 10 effects."))
            continue
        for index, spec in enumerate(specs):
            try:
                validate(spec, database, extra_keys)
            except ValueError as problem:
                problems.append((f"{field}[{index}]", str(problem)))
    for field, check in (("cures", validate_cures), ("resistances", validate_resistances)):
        if field in properties:
            try:
                check(properties[field], database, extra_keys)
            except ValueError as problem:
                problems.append((field, str(problem)))
    return problems


def _mentions(value, key):
    """Whether parsed item or NPC properties apply, cure, or resist an effect key."""
    return key in property_effect_keys(value)


def dependents(database, key, ignored_abilities=(), *, retired=False):
    """Installed abilities and content that apply an effect.

    Content of removed worlds is kept for players who still hold it; it counts
    only when ``retired`` is true.
    """
    from . import mud_abilities
    names = [f"ability {entry['name']}" for entry in mud_abilities.catalog(database).values()
             if entry["key"] not in ignored_abilities
             and any(spec["key"] == key for spec in entry.get("effects", []))]
    pattern = f'%"{key}"%'
    for table, label in (("mud_item_definitions", "item"), ("mud_npc_definitions", "NPC")):
        for row in database.execute(f"SELECT name,properties_json FROM {table} WHERE properties_json LIKE ? "
                                    "AND (? OR world_id NOT LIKE 'retired:%')", (pattern, int(retired))):
            try:
                if _mentions(json.loads(row["properties_json"] or "{}"), key):
                    names.append(f"{label} {row['name']}")
            except json.JSONDecodeError:
                continue
    if database.execute("SELECT 1 FROM mud_world_triggers WHERE definition_json LIKE ? LIMIT 1",
                        (pattern,)).fetchone():
        names.append("a world trigger")
    return names


def remove_effects(database, keys, *, force=False):
    """Remove imported effects; ones still active on someone are kept as removed."""
    entries = {key: entry for key, entry in catalog(database).items() if entry["imported"]}
    keys = set(keys)
    if not keys or keys - set(entries):
        raise ValueError("Select only installed imported effects to remove.")
    if not force:
        for key in sorted(keys):
            blocking = dependents(database, key)
            if blocking:
                raise ValueError(f"{entries[key]['name']} is still used by {', '.join(blocking)}. "
                                 "Remove or update those first.")
    for key in sorted(keys):
        # Kept, hidden, while it is still active on someone or on items a player may still hold.
        if (database.execute("SELECT 1 FROM mud_active_effects WHERE effect_key=? LIMIT 1", (key,)).fetchone()
                or dependents(database, key, retired=True)):
            database.execute("UPDATE mud_effect_definitions SET installed=0 WHERE key=?", (key,))
        else:
            database.execute("DELETE FROM mud_effect_definitions WHERE key=?", (key,))
        for table in ("mud_world_effects", "mud_dungeon_effects"):
            database.execute(f"DELETE FROM {table} WHERE effect_key=?", (key,))
    return [entries[key] for key in sorted(keys)]
