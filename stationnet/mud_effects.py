"""Shared, turn based MUD effects used by spells and content definitions.

Content uses ``{"key": "poison", "potency": 2, "duration": 3}``.
Applying the same key refreshes duration and keeps the stronger potency.
"""

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
}


def validate(spec):
    if not isinstance(spec, dict) or spec.get("key") not in EFFECTS:
        raise ValueError("Unknown effect key.")
    potency, duration = spec.get("potency", 1), spec.get("duration", 3)
    if type(potency) is not int or not 1 <= potency <= 1000:
        raise ValueError("Effect potency must be from 1 to 1000.")
    if type(duration) is not int or not 1 <= duration <= 100:
        raise ValueError("Effect duration must be from 1 to 100 turns.")
    return spec["key"], potency, duration


def apply(database, target_type, target_id, spec, source=""):
    key, potency, duration = validate(spec)
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
    return [dict(row) for row in database.execute(
        "SELECT effect_key,potency,remaining_turns,source FROM mud_active_effects "
        "WHERE target_type=? AND target_id=? ORDER BY effect_key", (target_type, target_id),
    )]


def modifier(database, target_type, target_id, kind):
    return sum(EFFECTS[row["effect_key"]].get("sign", 0) * row["potency"]
               for row in list_effects(database, target_type, target_id)
               if EFFECTS[row["effect_key"]]["kind"] == kind)


def tick(database, target_type, target_id):
    """Consume one turn and return damage and names of damaging effects."""
    active = list_effects(database, target_type, target_id)
    damage = [(row["effect_key"], row["potency"]) for row in active
              if EFFECTS[row["effect_key"]]["kind"] == "damage"]
    database.execute(
        "DELETE FROM mud_active_effects WHERE target_type=? AND target_id=? AND remaining_turns=1",
        (target_type, target_id),
    )
    database.execute(
        "UPDATE mud_active_effects SET remaining_turns=remaining_turns-1 "
        "WHERE target_type=? AND target_id=?", (target_type, target_id),
    )
    return damage


def clear(database, target_type, target_id):
    database.execute("DELETE FROM mud_active_effects WHERE target_type=? AND target_id=?",
                     (target_type, target_id))
