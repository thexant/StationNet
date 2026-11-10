"""Optional world life: a game clock, NPC schedules, NPC relationships, factions, and crime.

Every system here is off until a world enables it in its manifest (or the
World Forge), so worlds that never mention them play exactly as before.
This module validates settings and authored schedules and holds the pure
rules; mud.py owns the commands, events, and the scheduled tick.
"""
import copy
import json
import re
import time

CLOCK_DEFAULTS = {"enabled": False, "day_minutes": 120, "start_hour": 6}
# How much an NPC's regard changes for a completed quest, an unprovoked attack,
# or a witnessed crime, and the disposition at or below which it turns hostile.
RELATIONSHIP_DEFAULTS = {"enabled": False, "quest_complete": 5, "attack": -40, "crime": -25, "hostile_at": -60}
CRIMES = ("theft", "pickpocket", "assault")
# Witness odds, and the fine and standing loss of each detected crime. A
# fine_expiry_hours of 0 keeps fines until they are paid.
CRIME_DEFAULTS = {"enabled": False, "detection": 70, "fine_expiry_hours": 0,
                  "fines": {"theft": 25, "pickpocket": 40, "assault": 75},
                  "reputation": {"theft": -50, "pickpocket": -75, "assault": -100}}
# Whether players may belong to factions, how many at once (0 for no limit), and
# whether new characters must pick an eligible starting faction.
FACTION_DEFAULTS = {"enabled": False, "max_memberships": 1, "start_required": False}
# Housing is on by default because nothing happens until a world authors
# properties; max_holdings caps how many a character may own or share at once.
HOUSING_DEFAULTS = {"enabled": True, "max_holdings": 3}
# Vehicles and transit lines are likewise inert until a world authors them.
TRANSPORT_DEFAULTS = {"enabled": True}
DEFAULT_SOCIETY = {"clock": CLOCK_DEFAULTS, "relationships": RELATIONSHIP_DEFAULTS, "crime": CRIME_DEFAULTS,
                   "factions": FACTION_DEFAULTS, "housing": HOUSING_DEFAULTS, "transport": TRANSPORT_DEFAULTS}
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


def validate_crime(value):
    if value is None:
        return copy.deepcopy(CRIME_DEFAULTS)
    if not isinstance(value, dict):
        raise SocietyError("crime must be an object.")
    scalars = {key: item for key, item in CRIME_DEFAULTS.items() if not isinstance(item, dict)}
    result = _section({key: item for key, item in value.items() if key in scalars or key not in CRIME_DEFAULTS},
                      "crime", scalars, {"detection": (0, 100), "fine_expiry_hours": (0, 720)})
    for key, (low, high) in (("fines", (0, 100000)), ("reputation", (-1000, 0))):
        section = value.get(key, {})
        if not isinstance(section, dict) or set(section) - set(CRIMES):
            raise SocietyError(f"crime {key} accepts only {', '.join(CRIMES)}.")
        if any(not _whole(amount, low, high) for amount in section.values()):
            raise SocietyError(f"crime {key} must be whole numbers from {low} to {high}.")
        result[key] = {**CRIME_DEFAULTS[key], **section}
    return {key: result[key] for key in CRIME_DEFAULTS}


def validate_factions(value):
    result = _section({key: item for key, item in value.items() if key != "start_required"}
                      if isinstance(value, dict) else value,
                      "factions", FACTION_DEFAULTS, {"max_memberships": (0, 20)})
    if isinstance(value, dict) and "start_required" in value:
        if type(value["start_required"]) is not bool:
            raise SocietyError("factions start_required must be true or false.")
        result["start_required"] = value["start_required"]
    return result


def validate_housing(value):
    return _section(value, "housing", HOUSING_DEFAULTS, {"max_holdings": (1, 20)})


def validate_transport(value):
    return _section(value, "transport", TRANSPORT_DEFAULTS, {})


VALIDATORS = {"clock": validate_clock, "relationships": validate_relationships, "crime": validate_crime,
              "factions": validate_factions, "housing": validate_housing, "transport": validate_transport}
SECTION_LABELS = {"clock": "clock", "relationships": "relationship", "crime": "crime", "factions": "faction",
                  "housing": "housing", "transport": "transport"}


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
    if isinstance(behaviors.get("steal"), dict):
        for entry in behaviors["steal"].get("items", []):
            yield "item", entry["item"]


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
    if isinstance(behaviors.get("steal"), dict) and "items" in behaviors["steal"]:
        result["steal"] = {**behaviors["steal"], "items": [
            {**entry, "item": mapper("item", entry["item"])} for entry in behaviors["steal"]["items"]
            if mapper("item", entry["item"]) is not None]}
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


# --- Factions and reputation -----------------------------------------------

REPUTATION_LIMIT = 1000
RANK_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ATTITUDES = ("hostile", "peaceful")
# Members attack at hated and hostile; prices ease as standing grows.
DEFAULT_RANKS = (
    {"key": "hated", "name": "Hated", "min": -1000, "attitude": "hostile", "talk": False, "trade": False},
    {"key": "hostile", "name": "Hostile", "min": -600, "attitude": "hostile", "talk": False, "trade": False},
    {"key": "unfriendly", "name": "Unfriendly", "min": -200, "price_percent": 115},
    {"key": "neutral", "name": "Neutral", "min": -50},
    {"key": "friendly", "name": "Friendly", "min": 100, "price_percent": 95},
    {"key": "honored", "name": "Honored", "min": 400, "price_percent": 90},
    {"key": "exalted", "name": "Exalted", "min": 800, "price_percent": 85},
)
RANK_DEFAULTS = {"price_percent": 100, "attitude": None, "talk": True, "trade": True}


def validate_ranks(value):
    """A faction's ranks, lowest first; an empty value means the default ranks."""
    if value in (None, "", []):
        return [{**RANK_DEFAULTS, **rank} for rank in DEFAULT_RANKS]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Faction ranks must be valid JSON.") from error
    if not isinstance(value, list) or not 2 <= len(value) <= 10:
        raise ValueError("A faction needs 2 to 10 ranks.")
    ranks, keys = [], set()
    for rank in value:
        if not isinstance(rank, dict) or set(rank) - {"key", "name", "min", *RANK_DEFAULTS}:
            raise ValueError("Each rank accepts key, name, min, price_percent, attitude, talk, and trade.")
        if not isinstance(rank.get("key"), str) or not RANK_KEY_RE.fullmatch(rank["key"]) or rank["key"] in keys:
            raise ValueError("Rank keys must be unique lowercase keys such as friendly.")
        keys.add(rank["key"])
        if not isinstance(rank.get("name"), str) or not 1 <= len(rank["name"].strip()) <= 40:
            raise ValueError("Rank names must be 1 to 40 characters.")
        if not _whole(rank.get("min"), -REPUTATION_LIMIT, REPUTATION_LIMIT):
            raise ValueError(f"Rank min must be a whole number from -{REPUTATION_LIMIT} to {REPUTATION_LIMIT}.")
        if not _whole(rank.get("price_percent", 100), 50, 200):
            raise ValueError("Rank price_percent must be 50 to 200.")
        if rank.get("attitude") not in (None, *ATTITUDES):
            raise ValueError("Rank attitude must be hostile or peaceful, or left out.")
        if any(type(rank.get(key, True)) is not bool for key in ("talk", "trade")):
            raise ValueError("Rank talk and trade must be true or false.")
        ranks.append({**RANK_DEFAULTS, **rank, "name": rank["name"].strip()})
    if [rank["min"] for rank in ranks] != sorted({rank["min"] for rank in ranks}):
        raise ValueError("List ranks from lowest to highest, each with a higher min than the last.")
    return ranks


def stored_ranks(text):
    try:
        return validate_ranks(text)
    except ValueError:
        return validate_ranks(None)


def rank_for(ranks, value):
    """The highest rank whose min the value reaches; below every rank means the lowest."""
    return next((rank for rank in reversed(ranks) if value >= rank["min"]), ranks[0])


def validate_faction(values):
    """Normalize builder or package faction fields; raises ValueError."""
    name = " ".join(str(values.get("name", "")).split())
    if not 2 <= len(name) <= 80:
        raise ValueError("Faction names must be 2 to 80 characters.")
    description = str(values.get("description") or "").strip()
    if len(description) > 1000:
        raise ValueError("Faction descriptions must be up to 1000 characters.")
    result = {"name": name, "description": description}
    for key in ("default_reputation", "defeat_reputation"):
        value = values.get(key, 0)
        if not _whole(value, -REPUTATION_LIMIT, REPUTATION_LIMIT):
            raise ValueError(f"Faction {key} must be a whole number from -{REPUTATION_LIMIT} to {REPUTATION_LIMIT}.")
        result[key] = value
    if values.get("public", 1) not in (0, 1) or not _whole(values.get("sort_order", 0), -100000, 100000):
        raise ValueError("Faction public must be 0 or 1, and sort_order a whole number.")
    result.update(public=values.get("public", 1), sort_order=values.get("sort_order", 0))
    ranks = values.get("ranks_json")
    result["ranks_json"] = "" if ranks in (None, "", []) else json.dumps(validate_ranks(ranks), separators=(",", ":"))
    result.update(_validate_membership(values, result))
    return result


def _flag(values, key, default):
    value = values.get(key, default)
    if value not in (0, 1) or type(value) is not int:
        raise ValueError(f"Faction {key} must be 0 or 1.")
    return value


def _validate_membership(values, base):
    """The joining rules of a faction; a faction that omits them cannot be joined."""
    from . import mud_conditions
    result = {key: _flag(values, key, default)
              for key, default in (("joinable", 0), ("start_joinable", 0), ("leavable", 1))}
    if (result["joinable"] or result["start_joinable"]) and not base["public"]:
        raise ValueError("A joinable faction must be public; hidden factions are joined by triggers or dialogue.")
    result["join_conditions_json"] = json.dumps(mud_conditions.validate_conditions(values.get("join_conditions_json")),
                                                ensure_ascii=False, separators=(",", ":"))
    result["start_conditions_json"] = json.dumps(
        mud_conditions.validate_start_conditions(values.get("start_conditions_json")),
        ensure_ascii=False, separators=(",", ":"))
    message = str(values.get("join_conditions_message") or "").strip()
    if len(message) > 300:
        raise ValueError("Faction join_conditions_message must be up to 300 characters.")
    result["join_conditions_message"] = message
    if not _whole(values.get("join_cost", 0), 0, 10_000_000):
        raise ValueError("Faction join_cost must be a whole number of coins from 0.")
    result["join_cost"] = values.get("join_cost", 0)
    for key in ("join_reputation", "leave_reputation"):
        if not _whole(values.get(key, 0), -REPUTATION_LIMIT, REPUTATION_LIMIT):
            raise ValueError(f"Faction {key} must be a whole number from -{REPUTATION_LIMIT} to {REPUTATION_LIMIT}.")
        result[key] = values.get(key, 0)
    if not _whole(values.get("rejoin_hours", 0), 0, 8760):
        raise ValueError("Faction rejoin_hours must be a whole number from 0 to 8760.")
    result["rejoin_hours"] = values.get("rejoin_hours", 0)
    floor = values.get("expel_below")
    if floor is not None and not _whole(floor, -REPUTATION_LIMIT, REPUTATION_LIMIT):
        raise ValueError(f"Faction expel_below must be blank or a whole number from -{REPUTATION_LIMIT} to {REPUTATION_LIMIT}.")
    result["expel_below"] = floor
    if result["start_joinable"]:
        start = max(-REPUTATION_LIMIT, min(REPUTATION_LIMIT, base["default_reputation"] + result["join_reputation"]))
        if standing_forbids_membership({**base, **result}, start):
            raise ValueError("A starting faction cannot begin its members at a hostile rank or below expel_below.")
    return result


def validate_share(value):
    if not _whole(value, -100, 100) or value == 0:
        raise ValueError("A faction relation's share_percent must be a nonzero whole number from -100 to 100.")
    return value


def faction(database, faction_id):
    return database.execute("SELECT * FROM mud_world_factions WHERE id=?", (faction_id,)).fetchone()


def reputation(database, character_id, faction_row):
    row = database.execute("SELECT reputation FROM mud_character_reputation WHERE character_id=? AND faction_id=?",
                           (character_id, faction_row["id"])).fetchone()
    return row["reputation"] if row else faction_row["default_reputation"]


def standing(database, character_id, faction_row):
    """(reputation, rank) of a character with a faction."""
    value = reputation(database, character_id, faction_row)
    return value, rank_for(stored_ranks(faction_row["ranks_json"]), value)


def adjust_reputation(database, character_id, faction_id, amount):
    """Change standing with a faction and, by their shares, with factions related to it.

    Returns (faction row, old, new) for each faction whose standing changed.
    Relations reach one step only, so allies of allies are unaffected.
    """
    changed = []
    target = faction(database, faction_id)
    if target is None or not amount:
        return changed
    shares = [(target, amount)] + [
        (faction(database, row["other_faction_id"]), amount * row["share_percent"] // 100)
        for row in database.execute("SELECT other_faction_id,share_percent FROM mud_faction_relations "
                                    "WHERE faction_id=? ORDER BY other_faction_id", (faction_id,))]
    for row, delta in shares:
        if row is None or not delta:
            continue
        old = reputation(database, character_id, row)
        new = max(-REPUTATION_LIMIT, min(REPUTATION_LIMIT, old + delta))
        if new == old:
            continue
        database.execute(
            "INSERT INTO mud_character_reputation(character_id,faction_id,reputation) VALUES(?,?,?) "
            "ON CONFLICT(character_id,faction_id) DO UPDATE SET reputation=excluded.reputation,"
            "updated_at=CURRENT_TIMESTAMP", (character_id, row["id"], new))
        changed.append((row, old, new))
    return changed


# --- Membership ------------------------------------------------------------

def standing_forbids_membership(faction_row, value):
    """True at a rank the faction attacks on sight, or below its expel_below."""
    floor = faction_row["expel_below"]
    return (rank_for(stored_ranks(faction_row["ranks_json"]), value)["attitude"] == "hostile"
            or floor is not None and value < floor)


def is_member(database, character_id, faction_id):
    return database.execute("SELECT 1 FROM mud_character_factions WHERE character_id=? AND faction_id=? "
                            "AND left_at IS NULL", (character_id, faction_id)).fetchone() is not None


def memberships(database, character_id):
    """Rows of the factions a character currently belongs to in this world."""
    return database.execute(
        "SELECT f.* FROM mud_character_factions m JOIN mud_world_factions f ON f.id=m.faction_id "
        "WHERE m.character_id=? AND m.left_at IS NULL ORDER BY f.sort_order,f.name COLLATE NOCASE",
        (character_id,)).fetchall()


def join_problem(database, character_id, faction_row, settings):
    """Why the world's membership rules refuse this join, or None.

    These hold for every way of joining; recruiters, conditions, and fees apply
    only to players joining on their own.
    """
    if not settings["enabled"]:
        return "Faction membership is not available here."
    if is_member(database, character_id, faction_row["id"]):
        return f"You already belong to {faction_row['name']}."
    limit = settings["max_memberships"]
    if limit and len(memberships(database, character_id)) >= limit:
        return "You already belong to as many factions as this world allows."
    if faction_row["rejoin_hours"] and database.execute(
            "SELECT 1 FROM mud_character_factions WHERE character_id=? AND faction_id=? AND left_at IS NOT NULL "
            "AND left_at>datetime('now',?)",
            (character_id, faction_row["id"], f"-{faction_row['rejoin_hours']} hours")).fetchone():
        return f"{faction_row['name']} will not take you back yet."
    value = max(-REPUTATION_LIMIT, min(REPUTATION_LIMIT, reputation(database, character_id, faction_row)
                                       + faction_row["join_reputation"]))
    if standing_forbids_membership(faction_row, value):
        return f"Your standing with {faction_row['name']} is too low."
    return None


def enroll(database, character_id, faction_row):
    """Make a character a member; returns the resulting standing changes."""
    database.execute(
        "INSERT INTO mud_character_factions(character_id,faction_id) VALUES(?,?) "
        "ON CONFLICT(character_id,faction_id) DO UPDATE SET joined_at=CURRENT_TIMESTAMP,left_at=NULL",
        (character_id, faction_row["id"]))
    return adjust_reputation(database, character_id, faction_row["id"], faction_row["join_reputation"])


def withdraw(database, character_id, faction_row):
    """End a membership; returns the resulting standing changes."""
    database.execute("UPDATE mud_character_factions SET left_at=CURRENT_TIMESTAMP "
                     "WHERE character_id=? AND faction_id=? AND left_at IS NULL", (character_id, faction_row["id"]))
    return adjust_reputation(database, character_id, faction_row["id"], faction_row["leave_reputation"])


def lapsed(database, character_id):
    """Factions the character belongs to whose standing rules they now break."""
    return [row for row in memberships(database, character_id)
            if standing_forbids_membership(row, reputation(database, character_id, row))]


# --- Crime -----------------------------------------------------------------

CRIME_FLAGS = ("guard", "lawful", "witness")


def validate_steal(spec):
    """What a pickpocket can take from an NPC; item IDs are checked by the caller."""
    if not isinstance(spec, dict) or set(spec) - {"difficulty", "coins", "items", "cooldown_seconds"}:
        raise ValueError("steal accepts difficulty, coins, items, and cooldown_seconds.")
    if not _whole(spec.get("difficulty", 20), 0, 200) or not _whole(spec.get("cooldown_seconds", 3600), 60, 604800):
        raise ValueError("steal difficulty must be 0 to 200, and cooldown_seconds 60 to 604800.")
    coins = spec.get("coins", {"minimum": 0, "maximum": 0})
    if (not isinstance(coins, dict) or set(coins) != {"minimum", "maximum"}
            or not _whole(coins["minimum"], 0, 100000) or not _whole(coins["maximum"], coins["minimum"], 100000)):
        raise ValueError("steal coins needs minimum and maximum from 0 to 100000.")
    items = spec.get("items", [])
    if not isinstance(items, list) or len(items) > 10 or any(
            not isinstance(entry, dict) or set(entry) - {"item", "chance"} or not isinstance(entry.get("item"), str)
            or not _whole(entry.get("chance", 100), 1, 100) for entry in items):
        raise ValueError("steal items lists up to 10 entries of item and chance (1 to 100).")
    if not coins["maximum"] and not items:
        raise ValueError("steal needs coins or items to take.")
    return spec


def detection_chance(detection, stealth, agility, hidden):
    """A witness's percent chance to notice a crime: stealth, Agility and hiding all help."""
    return max(5, min(95, detection - stealth - 2 * (agility - 10) - (20 if hidden else 0)))


def expire_fines(database, crime):
    if crime["fine_expiry_hours"]:
        database.execute("UPDATE mud_character_crimes SET status='expired',settled_at=CURRENT_TIMESTAMP "
                         "WHERE status='open' AND created_at<=datetime('now', ?)",
                         (f"-{crime['fine_expiry_hours']} hours",))


def open_fines(database, character_id, world_id, faction_id=None):
    """Unpaid fines as {faction_id: total}; pass faction_id to read one faction."""
    query = ("SELECT faction_id,SUM(fine) AS total FROM mud_character_crimes WHERE character_id=? AND world_id=? "
             "AND status='open' AND faction_id IS NOT NULL")
    parameters = [character_id, world_id]
    if faction_id is not None:
        query += " AND faction_id=?"
        parameters.append(faction_id)
    return {row["faction_id"]: row["total"] for row in database.execute(query + " GROUP BY faction_id", parameters)
            if row["total"]}


def record_crime(database, character_id, world_id, faction_id, kind, fine, reputation_loss):
    database.execute("INSERT INTO mud_character_crimes(character_id,world_id,faction_id,kind,fine,reputation_loss) "
                     "VALUES(?,?,?,?,?,?)", (character_id, world_id, faction_id, kind, fine, reputation_loss))


def active_entry(spec, hour):
    """The first entry covering this hour, or None when the NPC should be home."""
    if hour is None:
        return None
    return next((entry for entry in spec["entries"] if hour_between(hour, entry["from"], entry["to"])), None)
