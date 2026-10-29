"""Bounded per-world progression tuning.

The engine owns the experience curve, attributes, and combat formulas. A world
may only turn these few dials, each within fixed bounds; the defaults are the
engine's own numbers, so an untuned world plays exactly as before.
"""
import json

BOUNDS = {"level_cap": (1, 50), "experience_percent": (10, 500), "health_per_level": (1, 30),
          "mana_per_level": (0, 10), "starting_coins": (0, 100000)}
DEFAULTS = {"level_cap": 50, "experience_percent": 100, "health_per_level": 10, "mana_per_level": 2,
            "starting_coins": 30}
LABELS = {"level_cap": "Level cap", "experience_percent": "Experience rate (%)",
          "health_per_level": "Base health per level", "mana_per_level": "Base mana per level",
          "starting_coins": "Starting coins"}
# Names authors might reach for that the engine keeps for itself.
RESERVED = {"max_level": "level_cap (at most 50)", "experience_curve": None, "xp_curve": None,
            "attributes": None, "attribute_cap": None, "effect_kinds": None, "resistances": None}


class ProgressionError(ValueError):
    pass


def validate(value):
    """Return complete settings; unknown or out-of-range dials are errors."""
    if value is None:
        return dict(DEFAULTS)
    if not isinstance(value, dict):
        raise ProgressionError("progression must be an object.")
    for key in value:
        if key in RESERVED:
            hint = RESERVED[key]
            raise ProgressionError(f"progression {key} is engine-owned and cannot be changed"
                                   + (f"; use {hint}." if hint else "."))
        if key not in BOUNDS:
            raise ProgressionError(f"progression {key} is not supported; use "
                                   f"{', '.join(BOUNDS)}.")
    result = dict(DEFAULTS)
    for key, (low, high) in BOUNDS.items():
        if key in value:
            if type(value[key]) is not int or not low <= value[key] <= high:
                raise ProgressionError(f"progression {key} must be a whole number from {low} to {high}.")
            result[key] = value[key]
    return result


def changed(settings):
    """Only the dials that differ from the engine defaults, for storage and export."""
    return {key: value for key, value in settings.items() if DEFAULTS.get(key) != value}


def stored(raw):
    try:
        return validate(json.loads(raw or "{}"))
    except (ValueError, TypeError):
        return dict(DEFAULTS)


def for_world(database, world):
    row = database.execute("SELECT progression_json FROM mud_world_packages WHERE id=?", (world,)).fetchone()
    return stored(row["progression_json"]) if row else dict(DEFAULTS)


def for_character(database, character_id):
    """A character's world settings; the dungeon world and unknown worlds use the defaults."""
    row = database.execute("SELECT world_key FROM mud_characters WHERE id=?", (character_id,)).fetchone()
    return for_world(database, row["world_key"]) if row and row["world_key"] else dict(DEFAULTS)
