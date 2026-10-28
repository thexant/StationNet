"""Optional world life: a game clock, NPC schedules, and NPC relationships.

Every system here is off until a world enables it in its manifest (or the
World Builder), so worlds that never mention them play exactly as before.
This module validates settings and authored schedules and holds the pure
rules; mud.py owns the commands, events, and the scheduled tick.
"""
import copy
import json
import time

CLOCK_DEFAULTS = {"enabled": False, "day_minutes": 120, "start_hour": 6}
# How much an NPC's regard changes for a completed quest, an unprovoked attack,
# or a witnessed crime, and the disposition at or below which it turns hostile.
RELATIONSHIP_DEFAULTS = {"enabled": False, "quest_complete": 5, "attack": -40, "crime": -25, "hostile_at": -60}
DEFAULT_SOCIETY = {"clock": CLOCK_DEFAULTS, "relationships": RELATIONSHIP_DEFAULTS}
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


def validate_relationships(value):
    return _section(value, "relationships", RELATIONSHIP_DEFAULTS, {
        "quest_complete": (-100, 100), "attack": (-100, 100), "crime": (-100, 100), "hostile_at": (-100, -21)})


VALIDATORS = {"clock": validate_clock, "relationships": validate_relationships}
SECTION_LABELS = {"clock": "clock", "relationships": "relationship"}


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
    if isinstance(behaviors.get("relationship"), dict):
        for item in behaviors["relationship"].get("gifts", {}):
            yield "item", item


def remap_behaviors(behaviors, mapper):
    """NPC behaviors with references rewritten by mapper(kind, id); None drops a reference."""
    result = dict(behaviors)
    if isinstance(behaviors.get("schedule"), dict):
        schedule = remap_schedule(behaviors["schedule"], lambda room: mapper("room", room))
        if schedule is None:
            del result["schedule"]
        else:
            result["schedule"] = schedule
    if isinstance(behaviors.get("relationship"), dict) and "gifts" in behaviors["relationship"]:
        gifts = {mapper("item", item): amount for item, amount in behaviors["relationship"]["gifts"].items()
                 if mapper("item", item) is not None}
        relationship = {key: value for key, value in behaviors["relationship"].items() if key != "gifts"}
        result["relationship"] = {**relationship, **({"gifts": gifts} if gifts else {})}
    return result


# --- Relationships ---------------------------------------------------------

TIERS = ("hostile", "unfriendly", "neutral", "friendly", "trusted")
# Shop prices move by up to this many percent with an NPC's regard.
DISPOSITION_PRICE_PERCENT = 10


def validate_relationship(spec):
    """An NPC's own relationship settings inside behaviors_json; item IDs are checked by the caller."""
    allowed = {"default", "gifts", "greetings", "refuse_talk_below", "quest_complete", "attack", "crime",
               "attacks_when_hostile"}
    if not isinstance(spec, dict) or set(spec) - allowed:
        raise ValueError(f"relationship accepts only {', '.join(sorted(allowed))}.")
    for key in ("default", "refuse_talk_below", "quest_complete", "attack", "crime"):
        if key in spec and not _whole(spec[key], -100, 100):
            raise ValueError(f"relationship {key} must be a whole number from -100 to 100.")
    if type(spec.get("attacks_when_hostile", True)) is not bool:
        raise ValueError("relationship attacks_when_hostile must be true or false.")
    gifts = spec.get("gifts", {})
    if not isinstance(gifts, dict) or len(gifts) > 20 or any(
            not isinstance(item, str) or not 1 <= len(item) <= MAX_REFERENCE or not _whole(amount, -100, 100)
            for item, amount in gifts.items()):
        raise ValueError("relationship gifts must map up to 20 item IDs to changes from -100 to 100.")
    greetings = spec.get("greetings", {})
    if not isinstance(greetings, dict) or set(greetings) - set(TIERS) or any(
            not isinstance(text, str) or not 1 <= len(text.strip()) <= 300 for text in greetings.values()):
        raise ValueError(f"relationship greetings map {', '.join(TIERS)} to text of up to 300 characters.")
    return spec


def disposition_tier(value, hostile_at=RELATIONSHIP_DEFAULTS["hostile_at"]):
    if value <= hostile_at:
        return "hostile"
    if value < -20:
        return "unfriendly"
    if value < 20:
        return "neutral"
    return "friendly" if value < 60 else "trusted"


def disposition(database, character_id, npc_definition_id, default=0):
    row = database.execute("SELECT disposition FROM mud_npc_relationships WHERE character_id=? "
                           "AND npc_definition_id=?", (character_id, npc_definition_id)).fetchone()
    return row["disposition"] if row else default


def adjust_disposition(database, character_id, npc_definition_id, amount, default=0):
    """Change and store an NPC's regard for a character, kept within -100..100; returns (old, new)."""
    old = disposition(database, character_id, npc_definition_id, default)
    new = max(-100, min(100, old + amount))
    database.execute(
        "INSERT INTO mud_npc_relationships(character_id,npc_definition_id,disposition) VALUES(?,?,?) "
        "ON CONFLICT(character_id,npc_definition_id) DO UPDATE SET disposition=excluded.disposition,"
        "updated_at=CURRENT_TIMESTAMP", (character_id, npc_definition_id, new))
    return old, new


def disposition_price_percent(value):
    """100 for neutral regard; trusted friends pay up to 10% less, enemies up to 10% more."""
    return 100 - value * DISPOSITION_PRICE_PERCENT // 100


def active_entry(spec, hour):
    """The first entry covering this hour, or None when the NPC should be home."""
    if hour is None:
        return None
    return next((entry for entry in spec["entries"] if hour_between(hour, entry["from"], entry["to"])), None)
