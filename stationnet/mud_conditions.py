"""Authored dialogue/quest conditions and dialogue outcomes.

Conditions and outcomes are small JSON lists stored on quests and dialogue
responses. This module validates their shape, rewrites the content IDs they
reference (for world import/export), and evaluates conditions against a
context object supplied by the game. It never touches the database itself.

A context must provide these methods:
    level() race() class_key() tags() coins() item_count(item_id) attribute(name)
    skill_rank(skill_key) quest_status(quest_id) objective_complete(objective_id)
    state(key) claimed() flag(scope, key) object_state(object_id) room_id()
    knows_destination(destination_id)

Contexts may also provide game_hour(), the current world's hour or None, and
disposition(npc_definition_id), an NPC's (regard, tier) toward the character or
None while relationships are off; a missing NPC means the one being spoken to;
reputation(faction_id), the character's (standing, rank key) with a faction; and
fines(faction_id), unpaid fines in coins (to one faction, or to all for None),
or None while crime is off; and member(faction_id), whether the character
belongs to a faction, which is false while faction membership is off.

It also validates and checks equipment requirements, which gate items on
attributes, level, and the wearer's race, class, or origin tags.
"""
import json
import re

from .character_creation import ATTRIBUTES, KEY_RE

MAX_CONDITIONS = 20
MAX_OUTCOMES = 10
MAX_ID_LENGTH = 200
QUEST_STATUSES = ("not_started", "active", "ready", "completed", "failed", "abandoned")
OUTCOME_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
CONDITION_TYPES = ("level", "race", "class", "tag", "has_item", "coins", "attribute", "skill",
                   "quest", "objective", "state", "claimed", "flag", "object_state", "in_room",
                   "knows_destination", "time", "disposition", "reputation", "wanted", "member", "owns_property")
OUTCOME_TYPES = ("give_item", "take_item", "experience", "coins", "take_coins", "learn_destination", "disposition",
                 "reputation", "join_faction", "leave_faction", "grant_property", "revoke_property")
# Condition types that only make sense in traditional worlds, where quests and
# placed objects, rooms and wayfinding destinations exist.
QUEST_CONDITION_TYPES = ("quest", "objective", "object_state", "in_room", "knows_destination", "time",
                         "disposition", "reputation", "wanted", "member", "owns_property")
# All a character being created can be tested on; nothing else exists yet.
START_CONDITION_TYPES = ("level", "race", "class", "tag", "attribute", "skill")
# What a start rule may test: the finished origins and the faction chosen with them.
START_RULE_CONDITION_TYPES = ("race", "class", "member")
# Outcomes that teach world knowledge; procedural dungeons have no destinations.
WORLD_OUTCOME_TYPES = ("learn_destination", "disposition", "reputation", "join_faction", "leave_faction",
                       "grant_property", "revoke_property")
# owns_property role: any active owner, or only the primary owner who manages it.
PROPERTY_ROLES = ("owner", "primary")
REPUTATION_LIMIT = 1000
DISPOSITION_TIERS = ("hostile", "unfriendly", "neutral", "friendly", "trusted")
FLAG_SCOPES = ("player", "world")
# Flag keys are short lowercase names such as gate.open or wardens_slain.
FLAG_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")
MAX_FLAG_VALUE = 200
# Which field of each condition/outcome names another piece of content.
_REFERENCE_FIELDS = {
    "has_item": ("item", "item"), "skill": ("skill", "skill"),
    "quest": ("quest", "quest"), "objective": ("objective", "objective"),
    "give_item": ("item", "item"), "take_item": ("item", "item"),
    "object_state": ("object", "object"), "in_room": ("room", "room"),
    "knows_destination": ("destination", "destination"), "learn_destination": ("destination", "destination"),
    # Optional: without an NPC these mean the one being spoken to.
    "disposition": ("npc", "npc"),
    "reputation": ("faction", "faction"),
    "wanted": ("faction", "faction"),
    "member": ("faction", "faction"),
    "join_faction": ("faction", "faction"), "leave_faction": ("faction", "faction"),
    "owns_property": ("property", "property"),
    "grant_property": ("property", "property"), "revoke_property": ("property", "property"),
}


def _whole(value, low, high):
    return type(value) is int and low <= value <= high


def _identifier(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= MAX_ID_LENGTH:
        raise ValueError(f"{label} must name a content ID.")
    return value.strip()


def flag_key(value, label="Flag key"):
    if not isinstance(value, str) or not FLAG_KEY_RE.fullmatch(value.strip()):
        raise ValueError(f"{label} must be 1 to 80 lowercase letters, digits, or . _ : -, such as gate.open.")
    return value.strip()


def flag_number(value):
    """A stored flag value as a whole number; text and blanks count as 0."""
    try:
        return int(str(value).strip() or 0)
    except ValueError:
        return 0


def _load(value, label):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} must be valid JSON.") from error
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array.")
    return value


def _condition(raw, allowed, nested):
    if not isinstance(raw, dict):
        raise ValueError("Each condition must be an object.")
    negate = raw.get("negate", False)
    if not isinstance(negate, bool):
        raise ValueError("Condition negate must be true or false.")
    if "any" in raw:
        if nested:
            raise ValueError("Condition 'any' groups cannot be nested.")
        if set(raw) - {"any", "negate"}:
            raise ValueError("An 'any' condition group only accepts 'any' and 'negate'.")
        options = raw["any"]
        if not isinstance(options, list) or not 1 <= len(options) <= MAX_CONDITIONS:
            raise ValueError(f"An 'any' group needs 1 to {MAX_CONDITIONS} conditions.")
        result = {"any": [_condition(option, allowed, True) for option in options]}
        return {**result, "negate": True} if negate else result
    kind = raw.get("type")
    if kind not in allowed:
        raise ValueError(f"Unsupported condition type {kind!r}; use one of {', '.join(allowed)}.")
    fields = {"type", "negate"}
    result = {"type": kind}
    if kind == "level":
        fields |= {"min", "max"}
        if "min" not in raw and "max" not in raw:
            raise ValueError("Level conditions need min and/or max.")
        for bound in ("min", "max"):
            if bound in raw:
                if not _whole(raw[bound], 1, 1000):
                    raise ValueError("Level bounds must be whole numbers from 1 to 1000.")
                result[bound] = raw[bound]
        if result.get("min", 1) > result.get("max", 1000):
            raise ValueError("Level min cannot exceed max.")
    elif kind in ("race", "class", "tag"):
        fields |= {"key", "keys"}
        keys = raw.get("keys", [raw["key"]] if "key" in raw else None)
        if (not isinstance(keys, list) or not 1 <= len(keys) <= 50
                or any(not isinstance(key, str) or not KEY_RE.fullmatch(key) for key in keys)):
            raise ValueError(f"{kind.title()} conditions need keys: a list of {kind} keys.")
        result["keys"] = sorted(set(keys))
    elif kind == "has_item":
        fields |= {"item", "count"}
        result["item"] = _identifier(raw.get("item"), "has_item item")
        count = raw.get("count", 1)
        if not _whole(count, 1, 1000):
            raise ValueError("has_item count must be a whole number from 1 to 1000.")
        result["count"] = count
    elif kind == "coins":
        fields |= {"min"}
        if not _whole(raw.get("min"), 0, 1_000_000_000):
            raise ValueError("Coin conditions need a nonnegative whole min.")
        result["min"] = raw["min"]
    elif kind == "attribute":
        fields |= {"attribute", "min"}
        if raw.get("attribute") not in ATTRIBUTES or not _whole(raw.get("min"), 0, 1000):
            raise ValueError(f"Attribute conditions need attribute ({', '.join(ATTRIBUTES)}) and a whole min.")
        result.update(attribute=raw["attribute"], min=raw["min"])
    elif kind == "skill":
        fields |= {"skill", "min_rank"}
        skill = raw.get("skill")
        if not isinstance(skill, str) or not KEY_RE.fullmatch(skill):
            raise ValueError("Skill conditions need a skill key.")
        rank = raw.get("min_rank", 1)
        if not _whole(rank, 1, 100):
            raise ValueError("Skill min_rank must be a whole number from 1 to 100.")
        result.update(skill=skill, min_rank=rank)
    elif kind == "quest":
        fields |= {"quest", "status", "outcome"}
        result["quest"] = _identifier(raw.get("quest"), "Quest condition quest")
        status = raw.get("status", "completed")
        status = [status] if isinstance(status, str) else status
        if (not isinstance(status, list) or not status
                or any(value not in QUEST_STATUSES for value in status)):
            raise ValueError(f"Quest condition status must be one or more of {', '.join(QUEST_STATUSES)}.")
        result["status"] = [value for value in QUEST_STATUSES if value in status]
        if "outcome" in raw:
            # success, a resolution objective's outcome key, or failed.
            outcomes = [raw["outcome"]] if isinstance(raw["outcome"], str) else raw["outcome"]
            if (not isinstance(outcomes, list) or not 1 <= len(outcomes) <= 10
                    or any(not isinstance(value, str) or not OUTCOME_KEY_RE.fullmatch(value) for value in outcomes)):
                raise ValueError("Quest condition outcome must be one or more outcome keys, such as success.")
            result["outcome"] = sorted(set(outcomes))
    elif kind == "objective":
        fields |= {"objective"}
        result["objective"] = _identifier(raw.get("objective"), "Objective condition objective")
    elif kind == "state":
        fields |= {"key", "value"}
        key, value = raw.get("key"), raw.get("value", "")
        if not isinstance(key, str) or not 1 <= len(key.strip()) <= 80:
            raise ValueError("State conditions need a key of 1 to 80 characters.")
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError("State condition values must be text up to 500 characters.")
        result.update(key=" ".join(key.split()), value=value)
    elif kind == "flag":
        fields |= {"scope", "key", "value", "min", "max"}
        scope = raw.get("scope", "player")
        if scope not in FLAG_SCOPES:
            raise ValueError("Flag conditions need scope player or world.")
        result.update(scope=scope, key=flag_key(raw.get("key"), "Flag condition key"))
        if "value" in raw and ("min" in raw or "max" in raw):
            raise ValueError("Flag conditions use value or min/max, not both.")
        if "value" in raw:
            if not isinstance(raw["value"], str) or len(raw["value"]) > MAX_FLAG_VALUE:
                raise ValueError(f"Flag condition values must be text up to {MAX_FLAG_VALUE} characters.")
            result["value"] = raw["value"]
        for bound in ("min", "max"):
            if bound in raw:
                if type(raw[bound]) is not int or abs(raw[bound]) > 1_000_000_000:
                    raise ValueError("Flag min and max must be whole numbers.")
                result[bound] = raw[bound]
        if result.get("min", 0) > result.get("max", result.get("min", 0)):
            raise ValueError("Flag min cannot exceed max.")
    elif kind == "object_state":
        fields |= {"object", "state"}
        state = raw.get("state", "")
        if not isinstance(state, str) or len(state) > 40:
            raise ValueError("object_state conditions need a state of up to 40 characters.")
        result.update(object=_identifier(raw.get("object"), "object_state object"), state=state.strip())
    elif kind == "in_room":
        fields |= {"room"}
        result["room"] = _identifier(raw.get("room"), "in_room room")
    elif kind == "knows_destination":
        fields |= {"destination"}
        result["destination"] = _identifier(raw.get("destination"), "knows_destination destination")
    elif kind == "wanted":
        fields |= {"faction", "min_fine"}
        if "faction" in raw:
            result["faction"] = _identifier(raw["faction"], "wanted faction")
        if not _whole(raw.get("min_fine", 1), 1, 10_000_000):
            raise ValueError("wanted min_fine must be a whole number of coins from 1.")
        result["min_fine"] = raw.get("min_fine", 1)
    elif kind == "member":
        fields |= {"faction"}
        result["faction"] = _identifier(raw.get("faction"), "member faction")
    elif kind == "owns_property":
        fields |= {"property", "role"}
        result["property"] = _identifier(raw.get("property"), "owns_property property")
        if raw.get("role", "owner") not in PROPERTY_ROLES:
            raise ValueError("owns_property role must be owner or primary.")
        if raw.get("role", "owner") != "owner":
            result["role"] = raw["role"]
    elif kind == "reputation":
        fields |= {"faction", "min", "max", "rank"}
        result["faction"] = _identifier(raw.get("faction"), "reputation faction")
        if "rank" in raw:
            if "min" in raw or "max" in raw:
                raise ValueError("Reputation conditions use rank or min/max, not both.")
            ranks = [raw["rank"]] if isinstance(raw["rank"], str) else raw["rank"]
            if (not isinstance(ranks, list) or not 1 <= len(ranks) <= 10
                    or any(not isinstance(rank, str) or not KEY_RE.fullmatch(rank) for rank in ranks)):
                raise ValueError("Reputation rank must be one or more rank keys, such as friendly.")
            result["rank"] = sorted(set(ranks))
        elif "min" not in raw and "max" not in raw:
            raise ValueError("Reputation conditions need rank, or min and/or max.")
        for bound in ("min", "max"):
            if bound in raw:
                if not _whole(raw[bound], -REPUTATION_LIMIT, REPUTATION_LIMIT):
                    raise ValueError(f"Reputation bounds must be whole numbers from -{REPUTATION_LIMIT} to {REPUTATION_LIMIT}.")
                result[bound] = raw[bound]
        if result.get("min", -REPUTATION_LIMIT) > result.get("max", REPUTATION_LIMIT):
            raise ValueError("Reputation min cannot exceed max.")
    elif kind == "disposition":
        fields |= {"npc", "min", "max", "tier"}
        if "npc" in raw:
            result["npc"] = _identifier(raw["npc"], "disposition npc")
        if "tier" in raw:
            if "min" in raw or "max" in raw:
                raise ValueError("Disposition conditions use tier or min/max, not both.")
            tiers = [raw["tier"]] if isinstance(raw["tier"], str) else raw["tier"]
            if not isinstance(tiers, list) or not tiers or any(tier not in DISPOSITION_TIERS for tier in tiers):
                raise ValueError(f"Disposition tier must be one or more of {', '.join(DISPOSITION_TIERS)}.")
            result["tier"] = [tier for tier in DISPOSITION_TIERS if tier in tiers]
        elif "min" not in raw and "max" not in raw:
            raise ValueError("Disposition conditions need tier, or min and/or max.")
        for bound in ("min", "max"):
            if bound in raw:
                if not _whole(raw[bound], -100, 100):
                    raise ValueError("Disposition bounds must be whole numbers from -100 to 100.")
                result[bound] = raw[bound]
        if result.get("min", -100) > result.get("max", 100):
            raise ValueError("Disposition min cannot exceed max.")
    elif kind == "time":
        fields |= {"from", "to"}
        if not _whole(raw.get("from"), 0, 23) or not _whole(raw.get("to"), 0, 23) or raw["from"] == raw["to"]:
            raise ValueError("Time conditions need different from and to hours from 0 to 23.")
        result.update({"from": raw["from"], "to": raw["to"]})
    unknown = set(raw) - fields
    if unknown:
        raise ValueError(f"{kind} conditions do not accept: {', '.join(sorted(unknown))}.")
    if negate:
        result["negate"] = True
    return result


def validate_conditions(value, *, allow_quests=True):
    """Return a normalized condition list or raise ValueError."""
    items = _load(value, "Conditions")
    if len(items) > MAX_CONDITIONS:
        raise ValueError(f"Use at most {MAX_CONDITIONS} conditions.")
    allowed = tuple(kind for kind in CONDITION_TYPES
                    if allow_quests or kind not in QUEST_CONDITION_TYPES)
    return [_condition(item, allowed, False) for item in items]


def validate_start_conditions(value):
    """Conditions for a starting faction, limited to what a new character has."""
    items = _load(value, "Starting conditions")
    if len(items) > MAX_CONDITIONS:
        raise ValueError(f"Use at most {MAX_CONDITIONS} conditions.")
    return [_condition(item, START_CONDITION_TYPES, False) for item in items]


def validate_start_rule_conditions(value):
    """Conditions for a start rule: one race, class, and faction test at most.

    Rules rank by how many of these they test, so each is positive and appears
    once; several factions are alternatives in one 'any' group of member tests.
    """
    items = _load(value, "Start rule conditions")
    if not items:
        raise ValueError("A start rule needs a race, class, or faction; "
                         "the world's starting room already covers everyone else.")
    if len(items) > len(START_RULE_CONDITION_TYPES):
        raise ValueError("A start rule tests race, class, and faction once each.")
    result = [_condition(item, START_RULE_CONDITION_TYPES, False) for item in items]
    seen = set()
    for entry in result:
        options = entry.get("any", [entry])
        kinds = {option["type"] for option in options}
        if entry.get("negate") or any(option.get("negate") for option in options):
            raise ValueError("Start rules cannot use negate.")
        if "any" in entry and kinds != {"member"}:
            raise ValueError("Start rule 'any' groups list factions; give races and classes as keys.")
        if kinds & seen:
            raise ValueError("A start rule tests race, class, and faction once each.")
        seen |= kinds
    return result


def validate_outcomes(value, *, allow_world=True):
    """Return a normalized outcome list or raise ValueError."""
    items = _load(value, "Outcomes")
    if len(items) > MAX_OUTCOMES:
        raise ValueError(f"Use at most {MAX_OUTCOMES} outcomes.")
    allowed = tuple(kind for kind in OUTCOME_TYPES if allow_world or kind not in WORLD_OUTCOME_TYPES)
    result = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("Each outcome must be an object.")
        kind = raw.get("type")
        if kind not in allowed:
            raise ValueError(f"Unsupported outcome type {kind!r}; use one of {', '.join(allowed)}.")
        amount = raw.get("amount", 1)
        if kind == "reputation":
            if set(raw) - {"type", "faction", "amount"}:
                raise ValueError("reputation outcomes accept only faction and amount.")
            if not _whole(amount, -REPUTATION_LIMIT, REPUTATION_LIMIT) or amount == 0:
                raise ValueError(f"Reputation amounts must be nonzero whole numbers from -{REPUTATION_LIMIT} to {REPUTATION_LIMIT}.")
            result.append({"type": kind, "faction": _identifier(raw.get("faction"), "reputation faction"),
                           "amount": amount})
        elif kind in ("grant_property", "revoke_property"):
            if set(raw) - {"type", "property"}:
                raise ValueError(f"{kind} outcomes accept only property.")
            result.append({"type": kind, "property": _identifier(raw.get("property"), f"{kind} property")})
        elif kind in ("join_faction", "leave_faction"):
            if set(raw) - {"type", "faction"}:
                raise ValueError(f"{kind} outcomes accept only faction.")
            result.append({"type": kind, "faction": _identifier(raw.get("faction"), f"{kind} faction")})
        elif kind == "disposition":
            if set(raw) - {"type", "npc", "amount"}:
                raise ValueError("disposition outcomes accept only npc and amount.")
            if not _whole(amount, -100, 100) or amount == 0:
                raise ValueError("Disposition amounts must be nonzero whole numbers from -100 to 100.")
            result.append({"type": kind, "amount": amount,
                           **({"npc": _identifier(raw["npc"], "disposition npc")} if "npc" in raw else {})})
        elif kind == "learn_destination":
            if set(raw) - {"type", "destination"}:
                raise ValueError("learn_destination outcomes accept only destination.")
            result.append({"type": kind, "destination": _identifier(raw.get("destination"),
                                                                    "learn_destination destination")})
        elif kind in ("give_item", "take_item"):
            if set(raw) - {"type", "item", "amount"}:
                raise ValueError(f"{kind} outcomes accept only item and amount.")
            if not _whole(amount, 1, 100):
                raise ValueError("Item outcome amounts must be whole numbers from 1 to 100.")
            result.append({"type": kind, "item": _identifier(raw.get("item"), f"{kind} item"),
                           "amount": amount})
        else:
            if set(raw) - {"type", "amount"}:
                raise ValueError(f"{kind} outcomes accept only amount.")
            if not _whole(amount, 1, 100_000):
                raise ValueError("Experience and coin outcome amounts must be 1 to 100,000.")
            result.append({"type": kind, "amount": amount})
    return result


def _flatten(conditions):
    for condition in conditions:
        yield from condition.get("any", [condition])


def references(conditions=(), outcomes=()):
    """Yield (kind, id) for every content reference; kinds are item, quest,
    objective, skill, race, class, object (a placed item), room, and destination."""
    for entry in [*_flatten(conditions), *outcomes]:
        kind = entry.get("type")
        if kind in _REFERENCE_FIELDS:
            field, ref_kind = _REFERENCE_FIELDS[kind]
            if field in entry:
                yield ref_kind, entry[field]
        elif kind in ("race", "class"):
            for key in entry["keys"]:
                yield kind, key


def _remap_entry(entry, mapper):
    kind = entry.get("type")
    if "any" in entry:
        return {**entry, "any": [_remap_entry(option, mapper) for option in entry["any"]]}
    if kind in _REFERENCE_FIELDS:
        field, ref_kind = _REFERENCE_FIELDS[kind]
        return {**entry, field: mapper(ref_kind, entry[field])} if field in entry else dict(entry)
    if kind in ("race", "class"):
        return {**entry, "keys": sorted({mapper(kind, key) for key in entry["keys"]})}
    return dict(entry)


def remap(entries, mapper):
    """Rewrite references in normalized conditions or outcomes with mapper(kind, id)."""
    return [_remap_entry(entry, mapper) for entry in entries]


def _holds(condition, context):
    if "any" in condition:
        return any(_check(option, context) for option in condition["any"])
    kind = condition["type"]
    if kind == "level":
        level = context.level()
        return condition.get("min", 1) <= level <= condition.get("max", level)
    if kind == "race":
        return context.race() in condition["keys"]
    if kind == "class":
        return context.class_key() in condition["keys"]
    if kind == "tag":
        return bool(set(context.tags()) & set(condition["keys"]))
    if kind == "has_item":
        return context.item_count(condition["item"]) >= condition["count"]
    if kind == "coins":
        return context.coins() >= condition["min"]
    if kind == "attribute":
        return context.attribute(condition["attribute"]) >= condition["min"]
    if kind == "skill":
        return context.skill_rank(condition["skill"]) >= condition["min_rank"]
    if kind == "quest":
        status = context.quest_status(condition["quest"])
        # Abandoning once erased the record, so not_started still covers abandoned quests.
        if status not in condition["status"] and not (status == "abandoned" and "not_started" in condition["status"]):
            return False
        if "outcome" in condition:
            outcome = getattr(context, "quest_outcome", lambda quest: None)(condition["quest"])
            return outcome in condition["outcome"]
        return True
    if kind == "objective":
        return context.objective_complete(condition["objective"])
    if kind == "state":
        return str(context.state(condition["key"])) == condition["value"]
    if kind == "claimed":
        return context.claimed()
    if kind == "flag":
        value = context.flag(condition["scope"], condition["key"])
        if "min" in condition or "max" in condition:
            number = flag_number(value)
            return condition.get("min", number) <= number <= condition.get("max", number)
        if "value" in condition:
            return value == condition["value"]
        return value != ""
    if kind == "object_state":
        return context.object_state(condition["object"]) == condition["state"]
    if kind == "in_room":
        return context.room_id() == condition["room"]
    if kind == "knows_destination":
        knows = getattr(context, "knows_destination", None)
        return bool(knows and knows(condition["destination"]))
    if kind == "wanted":
        owed = getattr(context, "fines", lambda faction: None)(condition.get("faction"))
        return owed is not None and owed >= condition["min_fine"]
    if kind == "reputation":
        found = getattr(context, "reputation", lambda faction: None)(condition["faction"])
        if found is None:
            return False
        value, rank = found
        if "rank" in condition:
            return rank in condition["rank"]
        return condition.get("min", -REPUTATION_LIMIT) <= value <= condition.get("max", REPUTATION_LIMIT)
    if kind == "member":
        return bool(getattr(context, "member", lambda faction: False)(condition["faction"]))
    if kind == "owns_property":
        owns = getattr(context, "owns_property", lambda property_id, role: False)
        return bool(owns(condition["property"], condition.get("role", "owner")))
    if kind == "disposition":
        found = getattr(context, "disposition", lambda npc: None)(condition.get("npc"))
        if found is None:
            return False
        value, tier = found
        if "tier" in condition:
            return tier in condition["tier"]
        return condition.get("min", -100) <= value <= condition.get("max", 100)
    if kind == "time":
        hour = getattr(context, "game_hour", lambda: None)()
        if hour is None:
            return False
        start, end = condition["from"], condition["to"]
        return start <= hour < end if start < end else hour >= start or hour < end
    return False


def _check(condition, context):
    return _holds(condition, context) != condition.get("negate", False)


def evaluate(conditions, context):
    """True when every condition holds; an unreadable list never holds."""
    try:
        conditions = validate_conditions(conditions)
    except ValueError:
        return False
    return all(_check(condition, context) for condition in conditions)


# Equipment requirements ----------------------------------------------------

ORIGIN_REQUIREMENTS = {"races": "race", "classes": "class", "tags": "tag"}


def validate_item_requirements(value):
    """Normalize item requirements: attribute minimums, level, and origin gates.

    ``races``/``classes``/``tags`` pass when the wearer matches any listed key;
    ``not_races``/``not_classes``/``not_tags`` fail when the wearer matches any.
    Keys are not checked against installed origins so content can name origins
    installed later or not at all.
    """
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise ValueError("requirements must be an object")
    result = {}
    for key, entry in value.items():
        if key in ATTRIBUTES:
            if not _whole(entry, 1, 30):
                raise ValueError(f"{key} requirements must be whole numbers from 1 to 30")
        elif key == "level":
            if not _whole(entry, 1, 1000):
                raise ValueError("level requirements must be whole numbers from 1 to 1000")
        elif key.removeprefix("not_") in ORIGIN_REQUIREMENTS:
            if (not isinstance(entry, list) or not 1 <= len(entry) <= 50
                    or any(not isinstance(item, str) or not KEY_RE.fullmatch(item) for item in entry)):
                raise ValueError(f"{key} requirements must be a list of lowercase keys")
            entry = sorted(set(entry))
        else:
            raise ValueError(f"unsupported requirement {key!r}; use {', '.join(ATTRIBUTES)}, level, "
                             "races, classes, tags, not_races, not_classes, or not_tags")
        result[key] = entry
    return result


def requirement_problems(requirements, character, facts):
    """Readable unmet requirements for a character mapping (attributes and level)
    and origin facts ({"races": set, "classes": set, "tags": set})."""
    problems = []
    for key, entry in requirements.items():
        if key in ATTRIBUTES and character[key] < entry:
            problems.append(f"{key.title()} {entry}")
        elif key == "level" and character["level"] < entry:
            problems.append(f"level {entry}")
    for field, label in ORIGIN_REQUIREMENTS.items():
        held = facts.get(field, set())
        wanted, barred = requirements.get(field), requirements.get(f"not_{field}")
        if wanted and not held & set(wanted):
            problems.append(f"the {' or '.join(wanted)} trait" if field == "tags" else f"a specific {label}")
        if barred and held & set(barred):
            problems.append(f"lacking the {' or '.join(sorted(held & set(barred)))} trait"
                            if field == "tags" else f"a different {label}")
    return problems


def describe_item_requirements(requirements):
    """Short text such as 'Strength 12, level 5, tag: darkvision'."""
    parts = []
    for key, entry in requirements.items():
        if key in ATTRIBUTES:
            parts.append(f"{key.title()} {entry}")
        elif key == "level":
            parts.append(f"level {entry}")
        elif key == "tags":
            parts.append("trait: " + " or ".join(entry))
        elif key == "not_tags":
            parts.append("not for: " + ", ".join(entry))
        elif key.startswith("not_"):
            parts.append(f"some {key[4:]} excluded")
        else:
            parts.append(f"specific {key}")
    return ", ".join(parts)


def remap_item_requirements(requirements, mapper):
    """Rewrite race/class keys in item requirements with mapper(kind, key)."""
    result = dict(requirements)
    for field in ("races", "classes"):
        for key in (field, f"not_{field}"):
            if key in result:
                result[key] = sorted({mapper(ORIGIN_REQUIREMENTS[field], item) for item in result[key]})
    return result


def describe(entries):
    """Compact builder summary such as 'level, has_item'."""
    return ", ".join(entry.get("type", "any") for entry in entries)
