"""Validation for authored, replenishing room gathering nodes."""

import json

ACTIVITIES = ("harvest", "mine", "scavenge", "collect")


def rewards(value):
    """Return a bounded list of item awards; every successful attempt grants all of them."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Gathering rewards must be valid JSON.") from error
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ValueError("Gathering rewards must list 1 to 10 items.")
    clean = []
    seen = set()
    for reward in value:
        if not isinstance(reward, dict) or set(reward) != {"item_id", "amount"}:
            raise ValueError("Each gathering reward needs item_id and amount.")
        item_id, amount = reward["item_id"], reward["amount"]
        if not isinstance(item_id, str) or not item_id or type(amount) is not int or not 1 <= amount <= 20:
            raise ValueError("Gathering rewards need an item ID and quantity from 1 to 20.")
        if item_id in seen:
            raise ValueError("A gathering reward item may appear only once per node.")
        seen.add(item_id)
        clean.append({"item_id": item_id, "amount": amount})
    return clean


def validate(row):
    if not isinstance(row.get("name"), str) or not 2 <= len(row["name"].strip()) <= 80:
        raise ValueError("Gathering name must contain 2 to 80 characters.")
    if row.get("activity") not in ACTIVITIES:
        raise ValueError("Gathering activity must be harvest, mine, scavenge, or collect.")
    if type(row.get("success_percent", 100)) is not int or not 1 <= row.get("success_percent", 100) <= 100:
        raise ValueError("Gathering success chance must be 1 to 100 percent.")
    if type(row.get("respawn_seconds")) is not int or not 5 <= row["respawn_seconds"] <= 86400:
        raise ValueError("Gathering replenishment must be 5 to 86,400 seconds.")
    rank = row.get("min_rank", 0)
    if type(rank) is not int or not 0 <= rank <= 20:
        raise ValueError("Gathering skill rank must be 0 to 20.")
    if rank and not row.get("skill_key"):
        raise ValueError("A gathering rank requirement needs a skill.")
    for key in ("skill_key", "tool_definition_id"):
        if row.get(key) is not None and (not isinstance(row[key], str) or not row[key]):
            raise ValueError(f"Gathering {key} must be a nonempty ID.")
    return rewards(row.get("rewards_json"))
