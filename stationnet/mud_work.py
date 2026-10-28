"""Validation shared by authored professions, the builder, and world packages."""

from . import mud_conditions

GOALS = {"visit_room", "talk_npc", "defeat_npc", "possess_item", "deliver_item"}
COOLDOWN_MIN = 60
COOLDOWN_MAX = 604800


def _text(value, label, low, high):
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise ValueError(f"{label} must be {low} to {high} characters.")
    return " ".join(value.split()) if label == "Name" else value.strip()


def _number(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}.")
    return value


def profession(row):
    name = _text(row.get("name"), "Name", 2, 120)
    description = _text(row.get("description"), "Description", 1, 2000)
    if not row.get("recruiter_npc_definition_id"):
        raise ValueError("Choose a recruiter NPC.")
    conditions = mud_conditions.validate_conditions(row.get("conditions_json"))
    return {"name": name, "description": description, "conditions_json": conditions}


def activity(row):
    name = _text(row.get("name"), "Name", 2, 120)
    description = _text(row.get("description"), "Description", 1, 2000)
    kind = row.get("kind")
    room, provider = row.get("room_id"), row.get("provider_npc_definition_id")
    if kind not in {"immediate", "assignment"}:
        raise ValueError("Work kind must be immediate or assignment.")
    if bool(room) == bool(provider) or (kind == "assignment" and not provider):
        raise ValueError("Choose one room or NPC provider; accepted jobs need an NPC.")
    goal, target = row.get("objective_type"), row.get("objective_target_id")
    if kind == "immediate" and (goal or target):
        raise ValueError("Immediate work cannot have an objective.")
    if kind == "assignment" and (goal not in GOALS or not target):
        raise ValueError("Accepted jobs need one supported objective and target.")
    conditions = mud_conditions.validate_conditions(row.get("conditions_json"))
    return {
        "name": name, "description": description, "kind": kind,
        "room_id": room or None, "provider_npc_definition_id": provider or None,
        "conditions_json": conditions,
        "tool_definition_id": row.get("tool_definition_id") or None,
        "practice_skill_key": row.get("practice_skill_key") or None,
        "cooldown_seconds": _number(row.get("cooldown_seconds", 3600), "Cooldown", COOLDOWN_MIN, COOLDOWN_MAX),
        "objective_type": goal or None, "objective_target_id": target or None,
        "required_count": _number(row.get("required_count", 1), "Required count", 1, 100),
    }


def reward(row):
    kind = row.get("reward_type")
    target = row.get("target_id") or None
    if kind not in {"coins", "experience", "item"}:
        raise ValueError("Work reward must be coins, experience, or item.")
    amount = _number(row.get("amount"), "Reward amount", 1, 20 if kind == "item" else 10000)
    if (kind == "item") != bool(target):
        raise ValueError("Only item rewards name an item target.")
    return {"reward_type": kind, "target_id": target, "amount": amount}
