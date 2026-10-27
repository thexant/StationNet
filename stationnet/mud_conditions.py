"""Authored dialogue/quest conditions and dialogue outcomes.

Conditions and outcomes are small JSON lists stored on quests and dialogue
responses. This module validates their shape, rewrites the content IDs they
reference (for world import/export), and evaluates conditions against a
context object supplied by the game. It never touches the database itself.

A context must provide these methods:
    level() race() class_key() tags() coins() item_count(item_id) attribute(name)
    skill_rank(skill_key) quest_status(quest_id) objective_complete(objective_id)
    state(key) claimed()

It also validates and checks equipment requirements, which gate items on
attributes, level, and the wearer's race, class, or origin tags.
"""
import json

from .character_creation import ATTRIBUTES, KEY_RE

MAX_CONDITIONS = 20
MAX_OUTCOMES = 10
MAX_ID_LENGTH = 200
QUEST_STATUSES = ("not_started", "active", "ready", "completed")
CONDITION_TYPES = ("level", "race", "class", "tag", "has_item", "coins", "attribute", "skill",
                   "quest", "objective", "state", "claimed")
OUTCOME_TYPES = ("give_item", "take_item", "experience", "coins", "take_coins")
# Condition types that only make sense where traditional-world quests exist.
QUEST_CONDITION_TYPES = ("quest", "objective")
# Which field of each condition/outcome names another piece of content.
_REFERENCE_FIELDS = {
    "has_item": ("item", "item"), "skill": ("skill", "skill"),
    "quest": ("quest", "quest"), "objective": ("objective", "objective"),
    "give_item": ("item", "item"), "take_item": ("item", "item"),
}


def _whole(value, low, high):
    return type(value) is int and low <= value <= high


def _identifier(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= MAX_ID_LENGTH:
        raise ValueError(f"{label} must name a content ID.")
    return value.strip()


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
        fields |= {"quest", "status"}
        result["quest"] = _identifier(raw.get("quest"), "Quest condition quest")
        status = raw.get("status", "completed")
        status = [status] if isinstance(status, str) else status
        if (not isinstance(status, list) or not status
                or any(value not in QUEST_STATUSES for value in status)):
            raise ValueError(f"Quest condition status must be one or more of {', '.join(QUEST_STATUSES)}.")
        result["status"] = [value for value in QUEST_STATUSES if value in status]
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


def validate_outcomes(value):
    """Return a normalized outcome list or raise ValueError."""
    items = _load(value, "Outcomes")
    if len(items) > MAX_OUTCOMES:
        raise ValueError(f"Use at most {MAX_OUTCOMES} outcomes.")
    result = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("Each outcome must be an object.")
        kind = raw.get("type")
        if kind not in OUTCOME_TYPES:
            raise ValueError(f"Unsupported outcome type {kind!r}; use one of {', '.join(OUTCOME_TYPES)}.")
        amount = raw.get("amount", 1)
        if kind in ("give_item", "take_item"):
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
    objective, skill, race, and class."""
    for entry in [*_flatten(conditions), *outcomes]:
        kind = entry.get("type")
        if kind in _REFERENCE_FIELDS:
            field, ref_kind = _REFERENCE_FIELDS[kind]
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
        return {**entry, field: mapper(ref_kind, entry[field])}
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
        return context.quest_status(condition["quest"]) in condition["status"]
    if kind == "objective":
        return context.objective_complete(condition["objective"])
    if kind == "state":
        return str(context.state(condition["key"])) == condition["value"]
    if kind == "claimed":
        return context.claimed()
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
