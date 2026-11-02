"""Validation and rules shared by authored vehicles, fuel stations, transit lines, the builder, and world packages.

A vehicle is something a character boards: a mount or cart carries only its
operator and goes wherever they walk; a vehicle with an interior room carries
passengers inside while its operator steers it from room to room. Vehicles
declare movement modes (author-chosen words such as water or rail) and may be
limited to some wilderness terrains; exits and wilderness presets may require
a mode. A transit line is a service between stops: riders pay a fare and
arrive after the line's travel time. mud.py owns the commands and movement.
"""

from . import mud_conditions
from .mud_wilderness import KEY_RE, TERRAINS

MAX_MODES = 8
# Authored defaults; exports leave out fields that still have them.
VEHICLE_DEFAULTS = {"description": "", "modes": "", "terrains": "", "mode_only": 0, "speed": 100,
                    "interior_room_id": None, "fuel_capacity": 0, "fuel_per_move": 1, "fuel_item_id": None,
                    "fuel_per_item": 10, "shareable": 0, "return_seconds": 0, "conditions_json": [],
                    "conditions_message": ""}
LINE_DEFAULTS = {"description": "", "fare": 0, "fare_per_stop": 0, "cabin_room_id": None, "conditions_json": [],
                 "conditions_message": ""}
STOP_DEFAULTS = {"sort_order": 0, "travel_seconds": 60}
STATION_DEFAULTS = {"price": 1, "modes": ""}
VEHICLE_FIELDS = ("name", *VEHICLE_DEFAULTS)
LINE_FIELDS = ("name", *LINE_DEFAULTS)
# The scheduler ticks every two seconds, so no journey step can be shorter.
MIN_STEP_SECONDS = 2


def _text(value, label, low, high):
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise ValueError(f"{label} must be {low} to {high} characters.")
    return " ".join(value.split()) if label.endswith("name") else value.strip()


def _number(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}.")
    return value


def _flag(value, label):
    if type(value) is bool:
        value = int(value)
    if value not in (0, 1):
        raise ValueError(f"{label} must be true or false.")
    return value


def _words(value, label, allowed=None):
    """A comma list of keys as stored text: blank for none, else sorted and unique."""
    if value in (None, "", []):
        return ""
    parts = value.split(",") if isinstance(value, str) else value
    if not isinstance(parts, list) or any(not isinstance(part, str) for part in parts):
        raise ValueError(f"{label} must be a list of words.")
    chosen = sorted({part.strip().lower() for part in parts if part.strip()})
    if len(chosen) > MAX_MODES:
        raise ValueError(f"Use at most {MAX_MODES} {label.lower()}.")
    if allowed is not None and set(chosen) - set(allowed):
        raise ValueError(f"{label} must be from {', '.join(allowed)}.")
    if allowed is None and any(not KEY_RE.match(part) for part in chosen):
        raise ValueError(f"{label} must be short lowercase words such as water or rail.")
    return ",".join(chosen)


def modes(value):
    return _words(value, "Modes")


def terrains(value):
    return _words(value, "Terrains", TERRAINS)


def mode(value):
    """One required mode for an exit or preset: None when blank."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not KEY_RE.match(value.strip().lower()):
        raise ValueError("A required mode must be a short lowercase word such as water or rail.")
    return value.strip().lower()


def word_set(text):
    return frozenset(part for part in (text or "").split(",") if part)


def _message(value, label):
    if not isinstance(value, str) or len(value.strip()) > 300:
        raise ValueError(f"{label} must be at most 300 characters.")
    return value.strip()


def vehicle(row):
    """A normalized vehicle definition row (without its ID) or ValueError."""
    get = lambda key: row.get(key, VEHICLE_DEFAULTS.get(key))  # noqa: E731 - one lookup rule for every field
    description = get("description")
    if not isinstance(description, str) or len(description.strip()) > 2000:
        raise ValueError("Vehicle description must be at most 2000 characters.")
    result = {
        "name": _text(row.get("name"), "Vehicle name", 2, 80),
        "description": description.strip(),
        "modes": modes(get("modes")),
        "terrains": terrains(get("terrains")),
        "mode_only": _flag(get("mode_only"), "Vehicle mode only"),
        "speed": _number(get("speed"), "Vehicle speed", 25, 400),
        "interior_room_id": get("interior_room_id") or None,
        "fuel_capacity": _number(get("fuel_capacity"), "Fuel capacity", 0, 100_000),
        "fuel_per_move": _number(get("fuel_per_move"), "Fuel per move", 1, 1000),
        "fuel_item_id": get("fuel_item_id") or None,
        "fuel_per_item": _number(get("fuel_per_item"), "Fuel per item", 1, 100_000),
        "shareable": _flag(get("shareable"), "Vehicle shareable"),
        "return_seconds": _number(get("return_seconds"), "Return time", 0, 604_800),
        "conditions_json": mud_conditions.validate_conditions(get("conditions_json")),
        "conditions_message": _message(get("conditions_message"), "Vehicle refusal message"),
    }
    if result["fuel_item_id"] and not result["fuel_capacity"]:
        raise ValueError("A fuel item needs a fuel capacity.")
    if result["mode_only"] and not result["modes"] and not result["terrains"]:
        raise ValueError("A mode-only vehicle needs modes or terrains.")
    if result["shareable"] and not result["interior_room_id"]:
        raise ValueError("Only a vehicle with an interior can carry passengers.")
    return result


def line(row):
    """A normalized transit line row (without its ID or stops) or ValueError."""
    get = lambda key: row.get(key, LINE_DEFAULTS.get(key))  # noqa: E731
    description = get("description")
    if not isinstance(description, str) or len(description.strip()) > 2000:
        raise ValueError("Line description must be at most 2000 characters.")
    return {
        "name": _text(row.get("name"), "Line name", 2, 80),
        "description": description.strip(),
        "fare": _number(get("fare"), "Fare", 0, 100_000),
        "fare_per_stop": _number(get("fare_per_stop"), "Fare per stop", 0, 100_000),
        "cabin_room_id": get("cabin_room_id") or None,
        "conditions_json": mud_conditions.validate_conditions(get("conditions_json")),
        "conditions_message": _message(get("conditions_message"), "Line refusal message"),
    }


def stop(row):
    get = lambda key: row.get(key, STOP_DEFAULTS.get(key))  # noqa: E731
    return {"sort_order": _number(get("sort_order"), "Stop order", 0, 10_000),
            "travel_seconds": _number(get("travel_seconds"), "Stop travel time", 5, 86_400)}


def station(row):
    get = lambda key: row.get(key, STATION_DEFAULTS.get(key))  # noqa: E731
    return {"price": _number(get("price"), "Fuel price", 0, 100_000), "modes": modes(get("modes"))}


def passable(vehicle, *, exit_mode=None, exit_allows=True, target_kind=None, target_terrain=None,
             target_mode=None, source=None):
    """Why this move is refused, or None.

    vehicle is None on foot, else a mapping with name, modes, terrains, and
    mode_only. exit_mode and exit_allows come from a stored exit (None and
    True for a wilderness step); the target values describe the room entered,
    and source is (kind, terrain, required mode) of the room left, if known.
    """
    wild = target_kind == "wild"
    needed = exit_mode or (target_mode if wild else None)
    if vehicle is None:
        if needed:
            return f"You need something that travels by {needed} to go that way."
        return None
    name = vehicle["name"]
    if not exit_allows:
        return f"The way is too narrow for the {name}; /disembark first."
    owned = word_set(vehicle["modes"])
    if exit_mode and exit_mode not in owned:
        return f"The {name} cannot travel by {exit_mode}."
    if wild and target_mode and target_mode not in owned:
        return f"The {name} cannot travel by {target_mode}."
    allowed = word_set(vehicle["terrains"])
    if wild and allowed and target_terrain not in allowed:
        return f"The {name} cannot cross {target_terrain or 'that ground'}."
    if (vehicle["mode_only"] and not (needed and needed in owned) and not (wild and target_terrain in allowed)
            and not (not wild and _native(source, owned, allowed))):
        return f"The {name} cannot go that way."
    return None


def _native(source, owned, allowed):
    """Whether a room is wild ground a mode-only vehicle is made for; from there it may come
    ashore into the authored room beside it, such as a dock."""
    if not source or source[0] != "wild":
        return False
    _kind, terrain, mode = source
    return bool((allowed and terrain in allowed) or (mode and mode in owned))


def travel_interval(base, speed):
    """Seconds between journey steps at a vehicle's speed percentage."""
    return max(MIN_STEP_SECONDS, round(base * 100 / (speed or 100)))


def trip(stops, from_index, to_index):
    """(seconds, segments) between two stops of an ordered stop list, either direction.

    Each stop's travel_seconds is the time from the stop before it.
    """
    low, high = sorted((from_index, to_index))
    return sum(stops[index]["travel_seconds"] for index in range(low + 1, high + 1)), high - low


def fare(line_row, segments):
    return line_row["fare"] + line_row["fare_per_stop"] * segments
