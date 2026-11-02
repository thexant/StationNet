"""Start rules: conditional starting rooms for new characters.

A world's start_room is its default. Rules are optional exceptions, each naming
a room and the race, class, and/or faction it is for. Within one of those keys
are alternatives; across them all must match. When several rules match, the one
testing the most of them wins, then the lowest sort order, then room name and ID
so the outcome never depends on storage order.
"""
import json

from . import mud_conditions

LABELS = {"race": "Race", "class": "Class", "member": "Faction"}


def conditions_from(races, classes, factions):
    """The stored conditions for a rule's chosen keys; blanks are left unconstrained."""
    conditions = []
    if races:
        conditions.append({"type": "race", "keys": list(races)})
    if classes:
        conditions.append({"type": "class", "keys": list(classes)})
    members = [{"type": "member", "faction": faction} for faction in factions]
    if len(members) == 1:
        conditions.extend(members)
    elif members:
        conditions.append({"any": members})
    return conditions


def choices(conditions):
    """{race|class|member: set of keys} for a rule's validated conditions."""
    result = {}
    for condition in conditions:
        for option in condition.get("any", [condition]):
            kind = option["type"]
            result.setdefault(kind, set()).update(option["keys"] if "keys" in option else [option["faction"]])
    return result


def _read(row):
    try:
        return mud_conditions.validate_start_rule_conditions(row["spawn_conditions_json"])
    except ValueError:
        return None


def ordered(database, world):
    """The world's rules in evaluation order; conditions or room_name are None when unusable."""
    rows = database.execute(
        "SELECT s.id,s.room_id,s.spawn_conditions_json,s.sort_order,r.name AS room_name "
        "FROM mud_world_start_rules s LEFT JOIN mud_world_rooms r ON r.id=s.room_id AND r.world_id=s.world_id "
        "WHERE s.world_id=?", (world,)).fetchall()
    rules = [{**dict(row), "conditions": _read(row)} for row in rows]
    return sorted(rules, key=lambda rule: (-len(choices(rule["conditions"] or [])), rule["sort_order"],
                                           (rule["room_name"] or "").casefold(), rule["id"]))


def choose(database, world, context):
    """The room of the best rule context matches, or None for the world's default."""
    for rule in ordered(database, world):
        if (rule["conditions"] is not None and rule["room_name"] is not None
                and mud_conditions.evaluate(rule["conditions"], context)):
            return rule["room_id"]
    return None


def summary(conditions, names):
    """Readable text such as 'Race: Elf or Dwarf AND Class: Ranger'."""
    picked = choices(conditions)
    return " AND ".join(
        f"{label}: " + " or ".join(sorted(names[kind].get(key, key) for key in picked[kind]))
        for kind, label in LABELS.items() if kind in picked)


def _overlap(first, second):
    """Whether one character could match both rules' choices."""
    return all(first[kind] & second[kind] for kind in first.keys() & second.keys())


def review(database, world, names, offered_factions):
    """Each rule in evaluation order with what it tests and what an author should fix.

    names maps race, class, and member to {key: display name}.
    """
    rules = ordered(database, world)
    for index, rule in enumerate(rules):
        picked = choices(rule["conditions"] or [])
        rule.update(summary=summary(rule["conditions"], names) if rule["conditions"] else "Unreadable conditions",
                    races=picked.get("race", set()), classes=picked.get("class", set()),
                    factions=picked.get("member", set()), warnings=[])
        warnings = rule["warnings"]
        if rule["conditions"] is None:
            warnings.append("Its conditions cannot be read, so it is skipped. Remove it and add it again.")
        if rule["room_name"] is None:
            warnings.append("Its room is not in this world, so it is skipped.")
        for kind, label in (("race", "Race"), ("class", "Class"), ("member", "Faction")):
            for key in sorted(picked.get(kind, ())):
                if key not in names[kind]:
                    warnings.append(f"{label} {key} is not available in this world.")
        for key in sorted(picked.get("member", ())):
            if key in names["member"] and key not in offered_factions:
                warnings.append(f"{names['member'][key]} is not offered at character creation, "
                                "so no new character can match it.")
        for earlier in rules[:index]:
            if (earlier["conditions"] is None or rule["conditions"] is None
                    or earlier["room_id"] == rule["room_id"] or earlier["sort_order"] != rule["sort_order"]
                    or len(choices(earlier["conditions"])) != len(picked)):
                continue
            if _overlap(choices(earlier["conditions"]), picked):
                warnings.append(f"Ties with the rule for {earlier['room_name']}: a character matching both starts "
                                f"there, by room name. Give one a lower order to choose deliberately.")
    return rules


def stored(conditions):
    return json.dumps(conditions, ensure_ascii=False, separators=(",", ":"))
