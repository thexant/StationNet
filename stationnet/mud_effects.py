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
    unknown = set(document) - {"key", "name", "description", "kind", "sign", "resist_as"}
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


def tick_detail(database, target_type, target_id):
    """Consume one turn; return per-turn damage and regen as (name, amount) pairs."""
    detail = {"damage": [], "regen": []}
    for row, definition in _active(database, target_type, target_id):
        if definition["kind"] in detail:
            detail[definition["kind"]].append((definition["name"], row["potency"]))
    database.execute(
        "DELETE FROM mud_active_effects WHERE target_type=? AND target_id=? AND remaining_turns=1",
        (target_type, target_id),
    )
    database.execute(
        "UPDATE mud_active_effects SET remaining_turns=remaining_turns-1 "
        "WHERE target_type=? AND target_id=?", (target_type, target_id),
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


def _mentions(value, key):
    """Whether parsed item or NPC properties apply an effect key."""
    return isinstance(value, dict) and any(
        isinstance(spec, dict) and spec.get("key") == key
        for field in ("on_use_effects", "on_hit_effects") for spec in (value.get(field) or [])
        if isinstance(value.get(field), list))


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
