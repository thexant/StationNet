"""Optional quest authoring: player-facing details, failure rules, outcomes, and retries.

Every field here is optional. A quest with none of them behaves exactly like
the original quests: it has no deadline, it cannot fail, and it completes once
its required objectives are done.

Details are short notes shown to the player as they become relevant::

    [{"kind": "background", "text": "Wolves have taken three sheep."},
     {"kind": "location", "text": "The den lies north.", "room": "woods", "reveal": "offer"},
     {"kind": "consequence", "text": "The elder will not forgive cowardice.",
      "reveal": [{"type": "objective", "objective": "speak"}]}]

Failure rules fail the quest (or one objective) when all their conditions hold::

    [{"when": [{"type": "flag", "scope": "world", "key": "elder.dead"}],
      "message": "The elder is dead."}]

This module validates and rewrites that JSON without touching the database.
"""
import json
import re

from . import mud_conditions

MAX_DETAILS = 12
MAX_DETAIL_TEXT = 1000
MAX_FAIL_RULES = 10
MAX_MESSAGE = 300
MAX_SUMMARY = 300
MIN_TIME_LIMIT = 60
MAX_TIME_LIMIT = 30 * 86_400
MAX_RETRY_COOLDOWN = 30 * 86_400
DETAIL_KINDS = ("background", "location", "consequence", "note")
DETAIL_LABELS = {"background": "Background", "location": "Where", "consequence": "Known consequences",
                 "note": "Note"}
# offer: shown before accepting; accepted: once on the quest; finished: after it ends.
REVEAL_STAGES = ("offer", "accepted", "finished")
RETRY_MODES = ("retry", "once")
FAILED_OUTCOME = "failed"
OUTCOME_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")


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


def _text(value, label, maximum, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError(f"{label} must be text of {1 if required else 0} to {maximum} characters.")
    return value.strip()


def validate_summary(value):
    return _text(value or "", "The quest goal", MAX_SUMMARY, required=False)


def validate_details(value):
    """Return a normalized detail list or raise ValueError."""
    items = _load(value, "Quest details")
    if len(items) > MAX_DETAILS:
        raise ValueError(f"Use at most {MAX_DETAILS} quest details.")
    result = []
    for index, raw in enumerate(items, 1):
        where = f"Quest detail {index}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object.")
        unknown = set(raw) - {"kind", "text", "room", "reveal"}
        if unknown:
            raise ValueError(f"{where} does not accept {', '.join(sorted(unknown))}.")
        kind = raw.get("kind", "note")
        if kind not in DETAIL_KINDS:
            raise ValueError(f"{where} kind must be one of {', '.join(DETAIL_KINDS)}.")
        entry = {"kind": kind, "text": _text(raw.get("text"), f"{where} text", MAX_DETAIL_TEXT)}
        if "room" in raw:
            if kind != "location":
                raise ValueError(f"{where}: only location details name a room.")
            if not isinstance(raw["room"], str) or not 1 <= len(raw["room"].strip()) <= mud_conditions.MAX_ID_LENGTH:
                raise ValueError(f"{where} room must name a content ID.")
            entry["room"] = raw["room"].strip()
        reveal = raw.get("reveal", "accepted")
        if isinstance(reveal, str):
            if reveal not in REVEAL_STAGES:
                raise ValueError(f"{where} reveal must be {', '.join(REVEAL_STAGES)}, or a conditions list.")
            entry["reveal"] = reveal
        else:
            try:
                entry["reveal"] = mud_conditions.validate_conditions(reveal)
            except ValueError as problem:
                raise ValueError(f"{where} reveal: {problem}") from problem
            if not entry["reveal"]:
                raise ValueError(f"{where} reveal conditions cannot be empty.")
        result.append(entry)
    return result


def validate_fail_rules(value, label="Failure rules"):
    """Return a normalized failure rule list or raise ValueError."""
    items = _load(value, label)
    if len(items) > MAX_FAIL_RULES:
        raise ValueError(f"Use at most {MAX_FAIL_RULES} failure rules.")
    result = []
    for index, raw in enumerate(items, 1):
        where = f"{label[:-1] if label.endswith('s') else label} {index}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object.")
        unknown = set(raw) - {"when", "message"}
        if unknown:
            raise ValueError(f"{where} does not accept {', '.join(sorted(unknown))}.")
        try:
            conditions = mud_conditions.validate_conditions(raw.get("when"))
        except ValueError as problem:
            raise ValueError(f"{where}: {problem}") from problem
        if not conditions:
            raise ValueError(f"{where} needs at least one condition in when.")
        message = raw.get("message", "")
        result.append({"when": conditions, "message": _text(message, f"{where} message", MAX_MESSAGE,
                                                             required=False)})
    return result


def validate_outcome_key(value, label="Outcome key"):
    """A blank key means none; 'failed' is reserved for failure consequences."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str) or not OUTCOME_KEY_RE.fullmatch(value.strip()):
        raise ValueError(f"{label} must be 1 to 40 lowercase letters, digits, or hyphens, such as spared.")
    if value.strip() in (FAILED_OUTCOME, "success"):
        raise ValueError(f"{label} cannot be {value.strip()!r}; that name is reserved.")
    return value.strip()


def validate_reward_outcome(value):
    """'' pays on any success, 'failed' on failure, anything else on that outcome."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if isinstance(value, str) and value.strip() == FAILED_OUTCOME:
        return FAILED_OUTCOME
    return validate_outcome_key(value, "Reward outcome")


def validate_time_limit(value):
    if value is None or value == "" or value == 0:
        return None
    if type(value) is not int or not MIN_TIME_LIMIT <= value <= MAX_TIME_LIMIT:
        raise ValueError(f"Time limits must be {MIN_TIME_LIMIT} seconds to 30 days (or blank for none).")
    return value


def validate_retry(mode, cooldown):
    mode = mode or "retry"
    if mode not in RETRY_MODES:
        raise ValueError(f"Retry mode must be one of {', '.join(RETRY_MODES)}.")
    cooldown = cooldown or 0
    if type(cooldown) is not int or not 0 <= cooldown <= MAX_RETRY_COOLDOWN:
        raise ValueError("Retry cooldowns must be 0 seconds to 30 days.")
    if mode == "once" and cooldown:
        raise ValueError("One-attempt quests cannot be retried, so they take no retry cooldown.")
    return mode, cooldown


def references(details=(), fail_rules=()):
    """Yield (kind, id) for every content reference, as mud_conditions.references does."""
    for entry in details:
        if "room" in entry:
            yield "room", entry["room"]
        if isinstance(entry.get("reveal"), list):
            yield from mud_conditions.references(entry["reveal"])
    for rule in fail_rules:
        yield from mud_conditions.references(rule["when"])


def remap_details(details, mapper):
    result = []
    for entry in details:
        entry = dict(entry)
        if "room" in entry:
            entry["room"] = mapper("room", entry["room"])
        if isinstance(entry.get("reveal"), list):
            entry["reveal"] = mud_conditions.remap(entry["reveal"], mapper)
        result.append(entry)
    return result


def remap_fail_rules(rules, mapper):
    return [{**rule, "when": mud_conditions.remap(rule["when"], mapper)} for rule in rules]


def dumps(value):
    return json.dumps(value, separators=(",", ":"))


def check_structure(objectives, rewards):
    """Problems in how a quest's objectives and rewards fit together, as readable text."""
    problems = []
    outcomes = {objective.get("outcome_key") for objective in objectives if objective.get("outcome_key")}
    for objective in objectives:
        if objective.get("outcome_key") and objective.get("optional"):
            problems.append(f"Objective {objective.get('description') or objective.get('id')!r} resolves the "
                            "quest, so it cannot also be optional.")
    for reward in rewards:
        outcome = reward.get("outcome") or ""
        if outcome and outcome != FAILED_OUTCOME and outcome not in outcomes:
            problems.append(f"A reward pays on outcome {outcome!r}, but no objective resolves the quest that way.")
    return problems


def describe_duration(seconds):
    """A short human reading of a duration, such as '2h 5m' or '45s'."""
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{days}d" if days else "", f"{hours}h" if hours else "", f"{minutes}m" if minutes else ""]
    text = " ".join(part for part in parts if part)
    return text or f"{secs}s"
