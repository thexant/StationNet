"""Optional world life: a game clock and NPC schedules.

Every system here is off until a world enables it in its manifest (or the
World Builder), so worlds that never mention them play exactly as before.
This module validates settings and authored schedules and holds the pure
rules; mud.py owns the commands, events, and the scheduled tick.
"""
import copy
import json
import time

CLOCK_DEFAULTS = {"enabled": False, "day_minutes": 120, "start_hour": 6}
DEFAULT_SOCIETY = {"clock": CLOCK_DEFAULTS}
SOCIETY_KEYS = tuple(DEFAULT_SOCIETY)
# Hours at which each part of the day begins, in order.
PERIODS = ((0, "night"), (5, "dawn"), (7, "morning"), (12, "afternoon"), (17, "evening"), (21, "night"))
MAX_SCHEDULE_ENTRIES = 12
MAX_REFERENCE = 200


class SocietyError(ValueError):
    pass


def _whole(value, low, high):
    return type(value) is int and low <= value <= high


def _section(value, name, defaults, rules):
    """One settings object: only known keys, each checked by its rule; missing keys keep defaults."""
    if value is None:
        return copy.deepcopy(defaults)
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise SocietyError(f"{name} accepts only {', '.join(defaults)}.")
    result = copy.deepcopy(defaults)
    if "enabled" in value:
        if type(value["enabled"]) is not bool:
            raise SocietyError(f"{name} enabled must be true or false.")
        result["enabled"] = value["enabled"]
    for key, (low, high) in rules.items():
        if key in value:
            if not _whole(value[key], low, high):
                raise SocietyError(f"{name} {key} must be a whole number from {low} to {high}.")
            result[key] = value[key]
    return result


def validate_clock(value):
    return _section(value, "clock", CLOCK_DEFAULTS, {"day_minutes": (5, 1440), "start_hour": (0, 23)})


VALIDATORS = {"clock": validate_clock}
SECTION_LABELS = {"clock": "clock"}


def validate_settings(value):
    """A world's full, normalized life settings from a manifest-shaped object."""
    if value is None:
        value = {}
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except json.JSONDecodeError as error:
            raise SocietyError("World life settings must be valid JSON.") from error
    if not isinstance(value, dict) or set(value) - set(SOCIETY_KEYS):
        raise SocietyError(f"World life settings accept only {', '.join(SOCIETY_KEYS)}.")
    return {key: VALIDATORS[key](value.get(key)) for key in SOCIETY_KEYS}


def stored_settings(text):
    """Settings read back from the database; unreadable values fall back to the defaults."""
    try:
        return validate_settings(text)
    except SocietyError:
        return copy.deepcopy(DEFAULT_SOCIETY)


def changed_settings(settings):
    """Only the sections that differ from the defaults, for manifests and storage."""
    return {key: settings[key] for key in SOCIETY_KEYS if settings[key] != DEFAULT_SOCIETY[key]}


# --- Clock -----------------------------------------------------------------

def _now():
    """Real time in seconds; tests replace it to choose the hour."""
    return time.time()


def game_time(clock, now=None):
    """The world's hour and minute, derived from real time so nothing is stored.

    One game day lasts day_minutes real minutes; 1440 follows real UTC time
    shifted by start_hour.
    """
    if not clock or not clock.get("enabled"):
        return None
    seconds = _now() if now is None else now.timestamp()
    minute = (int(seconds * 24 / clock["day_minutes"]) + clock["start_hour"] * 60) % 1440
    hour = minute // 60
    period = next(name for start, name in reversed(PERIODS) if hour >= start)
    return {"hour": hour, "minute": minute % 60, "period": period}


def describe_time(moment):
    return f"It is {moment['period']} ({moment['hour']:02d}:{moment['minute']:02d})."


def hour_between(hour, start, end):
    """True when hour falls in [start, end), wrapping past midnight."""
    return start <= hour < end if start < end else hour >= start or hour < end


# --- Schedules -------------------------------------------------------------

def _reference(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= MAX_REFERENCE:
        raise ValueError(f"{label} must name a room.")
    return value.strip()


def validate_schedule(spec):
    """An NPC's daily routine. Room references are checked by the caller."""
    allowed = {"interval_seconds", "home", "travel", "entries"}
    if not isinstance(spec, dict) or set(spec) - allowed or "entries" not in spec:
        raise ValueError("schedule needs entries, with optional interval_seconds, home, and travel.")
    if not _whole(spec.get("interval_seconds", 60), 5, 86400):
        raise ValueError("schedule interval_seconds must be 5 to 86400.")
    if spec.get("travel", "walk") not in ("walk", "teleport"):
        raise ValueError("schedule travel must be walk or teleport.")
    if "home" in spec:
        _reference(spec["home"], "schedule home")
    entries = spec["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_SCHEDULE_ENTRIES:
        raise ValueError(f"schedule entries must list 1 to {MAX_SCHEDULE_ENTRIES} periods.")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"from", "to", "room", "open", "lines", "label"}:
            raise ValueError("Each schedule entry accepts from, to, room, open, lines, and label.")
        if not _whole(entry.get("from"), 0, 23) or not _whole(entry.get("to"), 0, 23) or entry["from"] == entry["to"]:
            raise ValueError("Schedule entry from and to must be different hours from 0 to 23.")
        _reference(entry.get("room"), "Schedule entry room")
        if type(entry.get("open", False)) is not bool:
            raise ValueError("Schedule entry open must be true or false.")
        lines = entry.get("lines", [])
        if not isinstance(lines, list) or len(lines) > 20 or any(
                not isinstance(line, str) or not 1 <= len(line.strip()) <= 300 for line in lines):
            raise ValueError("Schedule entry lines must contain up to 20 short strings.")
        label = entry.get("label", "")
        if not isinstance(label, str) or len(label) > 80:
            raise ValueError("Schedule entry label must be up to 80 characters.")
    return spec


def schedule_rooms(spec):
    """Every room a schedule names, for reference checks and remapping."""
    rooms = [entry["room"] for entry in spec.get("entries", [])]
    return rooms + ([spec["home"]] if spec.get("home") else [])


def remap_schedule(spec, mapper):
    """Rewrite schedule rooms with mapper(room); entries whose room maps to None are dropped."""
    result = {key: value for key, value in spec.items() if key != "home"}
    if spec.get("home") and mapper(spec["home"]) is not None:
        result["home"] = mapper(spec["home"])
    result["entries"] = [{**entry, "room": mapper(entry["room"])} for entry in spec["entries"]
                         if mapper(entry["room"]) is not None]
    return result if result["entries"] else None


def behavior_references(behaviors):
    """(kind, id) for every content reference inside NPC behaviors."""
    if isinstance(behaviors.get("schedule"), dict):
        for room in schedule_rooms(behaviors["schedule"]):
            yield "room", room


def remap_behaviors(behaviors, mapper):
    """NPC behaviors with references rewritten by mapper(kind, id); None drops a reference."""
    result = dict(behaviors)
    if isinstance(behaviors.get("schedule"), dict):
        schedule = remap_schedule(behaviors["schedule"], lambda room: mapper("room", room))
        if schedule is None:
            del result["schedule"]
        else:
            result["schedule"] = schedule
    return result


def active_entry(spec, hour):
    """The first entry covering this hour, or None when the NPC should be home."""
    if hour is None:
        return None
    return next((entry for entry in spec["entries"] if hour_between(hour, entry["from"], entry["to"])), None)
