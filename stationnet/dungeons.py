"""Deterministic, template-driven procedural dungeon generation.

The generator builds an isolated graph and immutable content snapshot.  It does
not start a run or move a character; Chunks 11 and 12 own those concerns.
"""
from collections import deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import uuid

import click
from flask import current_app
from flask.cli import with_appcontext

from .db import get_db


GENERATOR_VERSION = "stationnet-dungeon-1"
SCHEMA_VERSION = 1
MAX_THEME_BYTES = 1024 * 1024
SUPPORTED_LAYOUTS = frozenset({"linear", "branching", "maze", "hub"})
EQUIPMENT_SLOTS = frozenset({
    "head", "body", "hands", "legs", "feet", "main_hand", "off_hand", "accessory",
})
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
DELTAS = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}


class TemplateError(ValueError):
    """A theme could not be parsed, validated, or installed."""

    def __init__(self, errors):
        self.errors = [str(error) for error in errors]
        super().__init__("\n".join(self.errors))


class GenerationError(ValueError):
    """A valid request could not produce a dungeon."""


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def template_hash(template):
    return hashlib.sha256(canonical_json(template).encode("utf-8")).hexdigest()


def themes_directory():
    return Path(current_app.config["DUNGEON_THEMES_DIR"])


def custom_themes_directory():
    return Path(current_app.config["DUNGEONS_DIR"])


def read_template(path):
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as problem:
        raise TemplateError([f"{path}: cannot read template: {problem}"]) from problem
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as problem:
        raise TemplateError([
            f"{path}:{problem.lineno}:{problem.colno}: invalid JSON: {problem.msg}"
        ]) from problem
    if not isinstance(value, dict):
        raise TemplateError(["$: template root must be a JSON object"])
    return value


def discover_templates(directory=None):
    """Return installed themes keyed by ID, rejecting duplicates explicitly."""
    discovered = {}
    custom_directory = custom_themes_directory() if directory is None else None
    directories = [Path(directory)] if directory is not None else [themes_directory(), custom_directory]
    for source in directories:
        if not source.exists():
            continue
        for path in sorted(source.glob("*.json"), key=lambda item: item.name.casefold()):
            if source == custom_directory and path.name in {"_template.json", "theme-schema.json"}:
                continue
            template = read_template(path)
            errors = validate_template(template)
            if source == custom_directory and isinstance(template, dict) and "author" not in template:
                errors.append("$.author: must be a non-empty string")
            if errors:
                raise TemplateError([f"{path.name}: {error}" for error in errors])
            theme_id = template["id"]
            if theme_id in discovered:
                raise TemplateError([
                    f"duplicate theme id {theme_id!r}: {discovered[theme_id][0]} and {path}"
                ])
            discovered[theme_id] = (path, template)
    return discovered


def _is_int(value, minimum=None, maximum=None):
    return (isinstance(value, int) and not isinstance(value, bool)
            and (minimum is None or value >= minimum)
            and (maximum is None or value <= maximum))


def _validate_weighted_text(errors, value, path):
    if not isinstance(value, list) or not value:
        errors.append(f"{path}: must be a non-empty array")
        return
    for index, choice in enumerate(value):
        here = f"{path}[{index}]"
        if not isinstance(choice, dict):
            errors.append(f"{here}: must be an object")
            continue
        if not isinstance(choice.get("text"), str) or not choice["text"].strip():
            errors.append(f"{here}.text: must be a non-empty string")
        if not _is_int(choice.get("weight", 1), 1, 1_000_000):
            errors.append(f"{here}.weight: must be an integer from 1 to 1000000")


def _validate_conditions(errors, value, path):
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{path}: must be an object")
        return
    allowed = {
        "layouts", "roles", "min_difficulty", "max_difficulty", "min_depth_ratio",
        "max_depth_ratio", "min_degree", "max_degree", "dead_end",
    }
    for key in value:
        if key not in allowed:
            errors.append(f"{path}.{key}: unknown condition")
    for key in ("layouts", "roles"):
        if key in value and (not isinstance(value[key], list) or not value[key]
                             or not all(isinstance(item, str) and item for item in value[key])):
            errors.append(f"{path}.{key}: must be a non-empty string array")
    for key in ("min_difficulty", "max_difficulty", "min_degree", "max_degree"):
        if key in value and not _is_int(value[key], 0):
            errors.append(f"{path}.{key}: must be a non-negative integer")
    for key in ("min_depth_ratio", "max_depth_ratio"):
        if key in value and (not isinstance(value[key], (int, float))
                             or isinstance(value[key], bool) or not 0 <= value[key] <= 1):
            errors.append(f"{path}.{key}: must be a number from 0 to 1")
    if "dead_end" in value and not isinstance(value["dead_end"], bool):
        errors.append(f"{path}.dead_end: must be true or false")


def _validate_pool_base(errors, pool, path, ids):
    if not isinstance(pool, dict):
        errors.append(f"{path}: must be an object")
        return False
    pool_id = pool.get("id")
    if not isinstance(pool_id, str) or not ID_RE.fullmatch(pool_id):
        errors.append(f"{path}.id: must match {ID_RE.pattern}")
    elif pool_id in ids:
        errors.append(f"{path}.id: duplicate id {pool_id!r}")
    else:
        ids.add(pool_id)
    if not _is_int(pool.get("weight", 1), 1, 1_000_000):
        errors.append(f"{path}.weight: must be an integer from 1 to 1000000")
    _validate_conditions(errors, pool.get("conditions"), f"{path}.conditions")
    return True


def local_definition_id(template, kind, local_id):
    """Return the stable, collision-resistant engine ID for embedded content."""
    singular = "npc" if kind in {"npc", "enemy", "npcs", "enemies"} else "item"
    return f"dungeon:{template['id']}@{template['version']}:{singular}:{local_id}"


def _validate_json_object(errors, value, path):
    if value is not None and not isinstance(value, dict):
        errors.append(f"{path}: must be an object")


def _validate_embedded_definitions(errors, template, database=None):
    definitions = template.get("definitions", {})
    if not isinstance(definitions, dict):
        errors.append("$.definitions: must be an object")
        return {"items": set(), "npcs": set(), "enemies": set()}
    allowed = {"items", "npcs", "enemies"}
    for key in definitions:
        if key not in allowed:
            errors.append(f"$.definitions.{key}: unsupported definition type")
    ids = {kind: set() for kind in allowed}
    item_slots = {}
    for kind in ("items", "npcs", "enemies"):
        values = definitions.get(kind, [])
        path = f"$.definitions.{kind}"
        if not isinstance(values, list):
            errors.append(f"{path}: must be an array")
            continue
        for index, definition in enumerate(values):
            here = f"{path}[{index}]"
            if not isinstance(definition, dict):
                errors.append(f"{here}: must be an object")
                continue
            local_id = definition.get("id")
            if not isinstance(local_id, str) or not ID_RE.fullmatch(local_id):
                errors.append(f"{here}.id: must match {ID_RE.pattern}")
            elif local_id in ids[kind]:
                errors.append(f"{here}.id: duplicate id {local_id!r}")
            else:
                ids[kind].add(local_id)
            for field in ("name", "description"):
                if not isinstance(definition.get(field), str) or not definition[field].strip():
                    errors.append(f"{here}.{field}: must be a non-empty string")
            _validate_json_object(errors, definition.get("properties"), f"{here}.properties")
            _validate_json_object(errors, definition.get("behaviors"), f"{here}.behaviors")
            if kind == "items":
                item_type = definition.get("item_type", "misc")
                if item_type not in {"misc", "equipment", "container", "consumable", "key"}:
                    errors.append(f"{here}.item_type: unsupported item type")
                slot = definition.get("equipment_slot")
                if slot is not None and slot not in EQUIPMENT_SLOTS:
                    errors.append(f"{here}.equipment_slot: unsupported equipment slot")
                if slot is not None and item_type != "equipment":
                    errors.append(f"{here}.equipment_slot: requires item_type 'equipment'")
                if isinstance(local_id, str):
                    item_slots[local_id] = slot
                continue
            combat = definition.get("combat")
            if kind == "enemies" and not isinstance(combat, dict):
                errors.append(f"{here}.combat: enemies require a combat object")
            if combat is not None:
                if not isinstance(combat, dict):
                    errors.append(f"{here}.combat: must be an object")
                else:
                    limits = {"maximum_health": (1, 1_000_000), "attack": (1, 100_000),
                              "defense": (0, 100_000), "experience_reward": (0, 100_000),
                              "respawn_seconds": (5, 86_400)}
                    defaults = {"maximum_health": 30, "attack": 6, "defense": 2,
                                "experience_reward": 10, "respawn_seconds": 60}
                    for field, (low, high) in limits.items():
                        if not _is_int(combat.get(field, defaults[field]), low, high):
                            errors.append(f"{here}.combat.{field}: must be an integer from {low} to {high}")
            conversation = definition.get("conversation")
            if conversation is not None:
                _validate_conversation(errors, conversation, f"{here}.conversation")
            equipment = definition.get("equipment", [])
            if not isinstance(equipment, list):
                errors.append(f"{here}.equipment: must be an array")
            else:
                used_slots = set()
                for equipment_index, entry in enumerate(equipment):
                    equip_path = f"{here}.equipment[{equipment_index}]"
                    if not isinstance(entry, dict):
                        errors.append(f"{equip_path}: must be an object"); continue
                    slot = entry.get("slot")
                    if slot not in EQUIPMENT_SLOTS or slot in used_slots:
                        errors.append(f"{equip_path}.slot: must be a unique supported slot")
                    else:
                        used_slots.add(slot)
                    _validate_item_reference(errors, entry, equip_path, ids["items"], item_slots,
                                             database, expected_slot=slot)
            loot = definition.get("loot", [])
            if not isinstance(loot, list):
                errors.append(f"{here}.loot: must be an array")
            else:
                for loot_index, reward in enumerate(loot):
                    reward_path = f"{here}.loot[{loot_index}]"
                    if not isinstance(reward, dict):
                        errors.append(f"{reward_path}: must be an object"); continue
                    reward_type = reward.get("type")
                    if reward_type not in {"experience", "item"}:
                        errors.append(f"{reward_path}.type: choose 'experience' or 'item'")
                    if not _is_int(reward.get("amount", 1), 1, 100_000):
                        errors.append(f"{reward_path}.amount: must be an integer from 1 to 100000")
                    if reward_type == "item":
                        _validate_item_reference(errors, reward, reward_path, ids["items"], item_slots,
                                                 database)
    overlap = ids["npcs"] & ids["enemies"]
    for local_id in sorted(overlap):
        errors.append(f"$.definitions: NPC/enemy id {local_id!r} is duplicated")
    return ids


def _validate_item_reference(errors, value, path, local_items, item_slots, database=None,
                             expected_slot=None):
    local_id, shared_id = value.get("item_local_id"), value.get("item_definition_id")
    if bool(local_id) == bool(shared_id):
        errors.append(f"{path}: requires exactly one of item_local_id or item_definition_id")
    elif local_id and local_id not in local_items:
        errors.append(f"{path}.item_local_id: unknown embedded item {local_id!r}")
    elif local_id and expected_slot and item_slots.get(local_id) != expected_slot:
        errors.append(f"{path}.item_local_id: item must use equipment slot {expected_slot!r}")
    elif shared_id and database is not None:
        row = database.execute(
            "SELECT item_type,equipment_slot FROM mud_item_definitions WHERE id=?", (shared_id,)
        ).fetchone()
        if row is None:
            errors.append(f"{path}.item_definition_id: {shared_id!r} does not exist")
        elif expected_slot and (row["item_type"] != "equipment"
                                or row["equipment_slot"] != expected_slot):
            errors.append(f"{path}.item_definition_id: item must use equipment slot {expected_slot!r}")


def _validate_conversation(errors, value, path):
    if not isinstance(value, dict):
        errors.append(f"{path}: must be an object"); return
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append(f"{path}.nodes: must be a non-empty array"); return
    node_ids = set()
    starts = 0
    for index, node in enumerate(nodes):
        here = f"{path}.nodes[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{here}: must be an object"); continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not ID_RE.fullmatch(node_id) or node_id in node_ids:
            errors.append(f"{here}.id: must be a unique valid id")
        else:
            node_ids.add(node_id)
        if not isinstance(node.get("body"), str) or not node["body"].strip():
            errors.append(f"{here}.body: must be a non-empty string")
        starts += node.get("start") is True
        responses = node.get("responses", [])
        if not isinstance(responses, list):
            errors.append(f"{here}.responses: must be an array")
        else:
            response_ids = set()
            for response_index, response in enumerate(responses):
                response_path = f"{here}.responses[{response_index}]"
                if not isinstance(response, dict):
                    errors.append(f"{response_path}: must be an object"); continue
                response_id = response.get("id")
                if not isinstance(response_id, str) or not ID_RE.fullmatch(response_id) or response_id in response_ids:
                    errors.append(f"{response_path}.id: must be a unique valid id")
                else:
                    response_ids.add(response_id)
                if not isinstance(response.get("text"), str) or not response["text"].strip():
                    errors.append(f"{response_path}.text: must be a non-empty string")
    if starts != 1:
        errors.append(f"{path}.nodes: exactly one node must have start true")
    for index, node in enumerate(nodes):
        if not isinstance(node, dict): continue
        for response_index, response in enumerate(node.get("responses", [])):
            if isinstance(response, dict) and response.get("next_node_id") not in (None, "") \
                    and response["next_node_id"] not in node_ids:
                errors.append(f"{path}.nodes[{index}].responses[{response_index}].next_node_id: unknown node")


def validate_template(template, database=None):
    """Return actionable JSON-path errors; an empty list means valid."""
    errors = []
    if not isinstance(template, dict):
        return ["$: template root must be an object"]
    if template.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"$.schema_version: must be {SCHEMA_VERSION}")
    for key in ("id", "version"):
        value = template.get(key)
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            errors.append(f"$.{key}: must match {ID_RE.pattern}")
    if not isinstance(template.get("name"), str) or not template.get("name", "").strip():
        errors.append("$.name: must be a non-empty string")
    if "author" in template and (not isinstance(template["author"], str) or not template["author"].strip()):
        errors.append("$.author: must be a non-empty string")
    embedded_ids = _validate_embedded_definitions(errors, template, database)

    generation = template.get("generation")
    if not isinstance(generation, dict):
        errors.append("$.generation: must be an object")
        generation = {}
    sizes = generation.get("size", {})
    if not isinstance(sizes, dict):
        errors.append("$.generation.size: must be an object")
        sizes = {}
    for key in ("minimum", "default", "maximum"):
        if not _is_int(sizes.get(key), 2, 500):
            errors.append(f"$.generation.size.{key}: must be an integer from 2 to 500")
    if all(_is_int(sizes.get(key), 2, 500) for key in ("minimum", "default", "maximum")):
        if not sizes["minimum"] <= sizes["default"] <= sizes["maximum"]:
            errors.append("$.generation.size: require minimum <= default <= maximum")
    difficulty = generation.get("difficulty", {})
    if not isinstance(difficulty, dict):
        errors.append("$.generation.difficulty: must be an object")
        difficulty = {}
    for key in ("minimum", "default", "maximum"):
        if not _is_int(difficulty.get(key), 1, 1000):
            errors.append(f"$.generation.difficulty.{key}: must be an integer from 1 to 1000")
    if all(_is_int(difficulty.get(key), 1, 1000) for key in ("minimum", "default", "maximum")):
        if not difficulty["minimum"] <= difficulty["default"] <= difficulty["maximum"]:
            errors.append("$.generation.difficulty: require minimum <= default <= maximum")
    layouts = generation.get("layouts")
    if not isinstance(layouts, list) or not layouts:
        errors.append("$.generation.layouts: must be a non-empty array")
    else:
        seen_layouts = set()
        for index, layout in enumerate(layouts):
            path = f"$.generation.layouts[{index}]"
            if not isinstance(layout, dict):
                errors.append(f"{path}: must be an object")
                continue
            if layout.get("type") not in SUPPORTED_LAYOUTS:
                errors.append(f"{path}.type: choose from {', '.join(sorted(SUPPORTED_LAYOUTS))}")
            elif layout["type"] in seen_layouts:
                errors.append(f"{path}.type: duplicate layout {layout['type']!r}")
            else:
                seen_layouts.add(layout["type"])
            if not _is_int(layout.get("weight", 1), 1, 1_000_000):
                errors.append(f"{path}.weight: must be an integer from 1 to 1000000")

    roles = generation.get("special_roles", [])
    if not isinstance(roles, list):
        errors.append("$.generation.special_roles: must be an array")
    else:
        seen_roles = {"entrance", "exit", "normal"}
        for index, rule in enumerate(roles):
            path = f"$.generation.special_roles[{index}]"
            if not isinstance(rule, dict):
                errors.append(f"{path}: must be an object")
                continue
            role = rule.get("role")
            if not isinstance(role, str) or not ID_RE.fullmatch(role):
                errors.append(f"{path}.role: must be a valid id")
            elif role in seen_roles:
                errors.append(f"{path}.role: reserved or duplicate role {role!r}")
            else:
                seen_roles.add(role)
            if not _is_int(rule.get("count"), 1, 100):
                errors.append(f"{path}.count: must be an integer from 1 to 100")
            for key in ("min_depth_ratio", "max_depth_ratio"):
                value = rule.get(key, 0 if key.startswith("min") else 1)
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
                    errors.append(f"{path}.{key}: must be a number from 0 to 1")
            if "prefer_dead_ends" in rule and not isinstance(rule["prefer_dead_ends"], bool):
                errors.append(f"{path}.prefer_dead_ends: must be true or false")

    pools = template.get("pools")
    if not isinstance(pools, dict):
        errors.append("$.pools: must be an object")
        pools = {}
    ids_by_kind = {}
    for kind in ("rooms", "environments", "npcs", "enemies", "items", "encounters"):
        values = pools.get(kind, [])
        if not isinstance(values, list):
            errors.append(f"$.pools.{kind}: must be an array")
            continue
        if kind == "rooms" and not values:
            errors.append("$.pools.rooms: must contain at least one room template")
        ids = set()
        ids_by_kind[kind] = ids
        for index, pool in enumerate(values):
            path = f"$.pools.{kind}[{index}]"
            if not _validate_pool_base(errors, pool, path, ids):
                continue
            if kind == "rooms":
                _validate_weighted_text(errors, pool.get("names"), f"{path}.names")
                _validate_weighted_text(errors, pool.get("descriptions"), f"{path}.descriptions")
            elif kind == "environments":
                _validate_weighted_text(errors, pool.get("descriptions"), f"{path}.descriptions")
            elif kind in {"npcs", "enemies", "items"}:
                chance = pool.get("chance", 1)
                if not isinstance(chance, (int, float)) or isinstance(chance, bool) or not 0 <= chance <= 1:
                    errors.append(f"{path}.chance: must be a number from 0 to 1")
                count = pool.get("count", {})
                if not isinstance(count, dict) or not _is_int(count.get("minimum", 0), 0, 100) or not _is_int(count.get("maximum", 1), 0, 100):
                    errors.append(f"{path}.count: requires integer minimum/maximum from 0 to 100")
                elif count["minimum"] > count["maximum"]:
                    errors.append(f"{path}.count: minimum cannot exceed maximum")
                choices = pool.get("choices")
                if not isinstance(choices, list) or not choices:
                    errors.append(f"{path}.choices: must be a non-empty array")
                else:
                    table = "mud_npc_definitions" if kind in {"npcs", "enemies"} else "mud_item_definitions"
                    local_kind = kind
                    for choice_index, choice in enumerate(choices):
                        choice_path = f"{path}.choices[{choice_index}]"
                        if not isinstance(choice, dict):
                            errors.append(f"{choice_path}: must be an object")
                            continue
                        shared_id, local_id = choice.get("definition_id"), choice.get("local_id")
                        if bool(shared_id) == bool(local_id):
                            errors.append(f"{choice_path}: requires exactly one of definition_id or local_id")
                            continue
                        if shared_id is not None and (not isinstance(shared_id, str) or not shared_id):
                            errors.append(f"{choice_path}.definition_id: must be a non-empty string")
                            continue
                        if local_id is not None and (not isinstance(local_id, str)
                                                     or local_id not in embedded_ids.get(local_kind, set())):
                            errors.append(f"{choice_path}.local_id: unknown embedded {local_kind[:-1]} {local_id!r}")
                            continue
                        if not _is_int(choice.get("weight", 1), 1, 1_000_000):
                            errors.append(f"{choice_path}.weight: must be an integer from 1 to 1000000")
                        _validate_conditions(errors, choice.get("conditions"), f"{choice_path}.conditions")
                        if database is not None and shared_id and database.execute(
                                f"SELECT 1 FROM {table} WHERE id=?", (shared_id,)
                        ).fetchone() is None:
                            errors.append(f"{choice_path}.definition_id: {shared_id!r} does not exist")
            else:
                chance = pool.get("chance", 1)
                if not isinstance(chance, (int, float)) or isinstance(chance, bool) or not 0 <= chance <= 1:
                    errors.append(f"{path}.chance: must be a number from 0 to 1")
                for key, target in (("npc_pool", "npcs"), ("enemy_pool", "enemies"),
                                    ("item_pool", "items")):
                    if key in pool and not isinstance(pool[key], str):
                        errors.append(f"{path}.{key}: must be a pool id string")
                    elif key in pool and pool[key] not in ids_by_kind.get(target, set()):
                        # Target pools are conventionally declared before encounters.
                        errors.append(f"{path}.{key}: unknown {target} pool {pool[key]!r}")
                if "config" in pool and not isinstance(pool["config"], dict):
                    errors.append(f"{path}.config: must be an object")
    return errors


def _weighted(rng, choices):
    if not choices:
        raise GenerationError("no eligible weighted choices")
    total = sum(choice.get("weight", 1) for choice in choices)
    roll = rng.randrange(total)
    for choice in choices:
        roll -= choice.get("weight", 1)
        if roll < 0:
            return choice
    return choices[-1]


def _conditions_match(conditions, context):
    conditions = conditions or {}
    if "layouts" in conditions and context["layout"] not in conditions["layouts"]:
        return False
    if "roles" in conditions and context["role"] not in conditions["roles"]:
        return False
    pairs = (
        ("min_difficulty", "difficulty", lambda a, b: b >= a),
        ("max_difficulty", "difficulty", lambda a, b: b <= a),
        ("min_depth_ratio", "depth_ratio", lambda a, b: b >= a),
        ("max_depth_ratio", "depth_ratio", lambda a, b: b <= a),
        ("min_degree", "degree", lambda a, b: b >= a),
        ("max_degree", "degree", lambda a, b: b <= a),
    )
    if any(key in conditions and not compare(conditions[key], context[field])
           for key, field, compare in pairs):
        return False
    return "dead_end" not in conditions or conditions["dead_end"] == context["dead_end"]


def _free_directions(position, occupied):
    result = []
    for direction, delta in DELTAS.items():
        destination = (position[0] + delta[0], position[1] + delta[1])
        if destination not in occupied:
            result.append((direction, destination))
    return result


def _add_edge(edges, first, second):
    edge = tuple(sorted((first, second)))
    if first != second and edge not in edges:
        edges.append(edge)


def _linear_layout(size, _rng):
    positions = [(index, 0) for index in range(size)]
    return positions, [(index - 1, index) for index in range(1, size)]


def _branching_layout(size, rng):
    positions = [(0, 0)]
    occupied = {(0, 0)}
    edges = []
    degrees = [0]
    while len(positions) < size:
        candidates = [index for index, position in enumerate(positions)
                      if degrees[index] < 3 and _free_directions(position, occupied)]
        if not candidates:
            raise GenerationError("branching layout exhausted its coordinate space")
        # Prefer deeper recent nodes, while retaining seeded variation.
        parent = rng.choice(candidates[-max(1, len(candidates) // 2):])
        direction, position = rng.choice(_free_directions(positions[parent], occupied))
        del direction
        child = len(positions)
        positions.append(position)
        occupied.add(position)
        degrees.append(1)
        degrees[parent] += 1
        _add_edge(edges, parent, child)
    return positions, edges


def _maze_layout(size, rng):
    positions = [(0, 0)]
    occupied = {(0, 0): 0}
    edges = []
    stack = [0]
    while len(positions) < size:
        parent = stack[-1]
        choices = _free_directions(positions[parent], occupied)
        if not choices:
            stack.pop()
            if not stack:
                raise GenerationError("maze layout exhausted its coordinate space")
            continue
        _direction, position = rng.choice(choices)
        child = len(positions)
        positions.append(position)
        occupied[position] = child
        _add_edge(edges, parent, child)
        stack.append(child)
    possible = []
    for index, (x, y) in enumerate(positions):
        for dx, dy in ((1, 0), (0, 1)):
            other = occupied.get((x + dx, y + dy))
            if other is not None and tuple(sorted((index, other))) not in edges:
                possible.append((index, other))
    rng.shuffle(possible)
    for edge in possible[:max(1, size // 6)]:
        _add_edge(edges, *edge)
    return positions, edges


def _hub_layout(size, rng):
    positions = [(0, 0)]
    occupied = {(0, 0)}
    edges = []
    arms = [((1, 0), []), ((0, 1), []), ((-1, 0), []), ((0, -1), [])]
    rng.shuffle(arms)
    for index in range(1, size):
        delta, arm = arms[(index - 1) % len(arms)]
        step = len(arm) + 1
        position = (delta[0] * step, delta[1] * step)
        parent = arm[-1] if arm else 0
        positions.append(position)
        occupied.add(position)
        arm.append(index)
        _add_edge(edges, parent, index)
    return positions, edges


LAYOUT_BUILDERS = {
    "linear": _linear_layout,
    "branching": _branching_layout,
    "maze": _maze_layout,
    "hub": _hub_layout,
}


def _distances(size, edges, start=0):
    adjacency = [[] for _ in range(size)]
    for first, second in edges:
        adjacency[first].append(second)
        adjacency[second].append(first)
    distances = [None] * size
    distances[start] = 0
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if distances[neighbor] is None:
                distances[neighbor] = distances[current] + 1
                queue.append(neighbor)
    return adjacency, distances


def _assign_roles(template, adjacency, depths, rng):
    roles = ["normal"] * len(adjacency)
    roles[0] = "entrance"
    exit_index = max(range(1, len(adjacency)), key=lambda index: (depths[index], index))
    roles[exit_index] = "exit"
    maximum_depth = max(depths) or 1
    available = set(range(1, len(adjacency))) - {exit_index}
    for rule in template["generation"].get("special_roles", []):
        low = rule.get("min_depth_ratio", 0)
        high = rule.get("max_depth_ratio", 1)
        candidates = [index for index in sorted(available)
                      if low <= depths[index] / maximum_depth <= high]
        if rule.get("prefer_dead_ends"):
            dead_ends = [index for index in candidates if len(adjacency[index]) == 1]
            if dead_ends:
                candidates = dead_ends
        count = rule["count"]
        if len(candidates) < count:
            # Small branching/hub layouts may not contain a room in every requested
            # depth band. Keep the run playable by using the closest free rooms.
            preferred = list(candidates)
            midpoint = (low + high) / 2
            candidates = preferred + sorted(
                available - set(preferred),
                key=lambda index: (abs(depths[index] / maximum_depth - midpoint), -depths[index], index),
            )
            if len(candidates) < count:
                raise GenerationError(f"role {rule['role']!r} needs {count} free rooms")
        rng.shuffle(candidates)
        for index in candidates[:count]:
            roles[index] = rule["role"]
            available.remove(index)
    return roles


def _direction_between(first, second):
    delta = (second[0] - first[0], second[1] - first[1])
    for direction, candidate in DELTAS.items():
        if delta == candidate:
            return direction
    raise GenerationError(f"non-adjacent generated rooms at {first} and {second}")


def _definition_snapshot(database, kind, definition_id):
    if database is None:
        return {"definition_id": definition_id}
    if kind in {"npc", "enemy"}:
        row = database.execute(
            "SELECT d.id,d.name,d.description,d.conversation_id,d.properties_json,d.behaviors_json,"
            "p.maximum_health,p.attack,p.defense,p.experience_reward,p.respawn_seconds "
            "FROM mud_npc_definitions AS d LEFT JOIN mud_npc_combat_profiles AS p "
            "ON p.npc_definition_id=d.id WHERE d.id=?", (definition_id,),
        ).fetchone()
    else:
        row = database.execute(
            "SELECT id,name,description,item_type,properties_json,behaviors_json,equipment_slot "
            "FROM mud_item_definitions WHERE id=?", (definition_id,),
        ).fetchone()
    if row is None:
        raise GenerationError(f"referenced {kind} definition {definition_id!r} no longer exists")
    return dict(row)


def _embedded_definition(template, kind, local_id):
    collection = "items" if kind == "item" else "npcs" if kind == "npc" else "enemies"
    return next((entry for entry in template.get("definitions", {}).get(collection, [])
                 if entry["id"] == local_id), None)


def _embedded_snapshot(template, kind, definition):
    definition_id = local_definition_id(template, kind, definition["id"])
    snapshot = {
        "definition_id": definition_id, "local_id": definition["id"],
        "source": "embedded", "name": definition["name"],
        "description": definition["description"],
        "properties_json": canonical_json(definition.get("properties", {})),
        "behaviors_json": canonical_json(definition.get("behaviors", {})),
    }
    if kind == "item":
        snapshot.update(item_type=definition.get("item_type", "misc"),
                        equipment_slot=definition.get("equipment_slot"))
    else:
        conversation = definition.get("conversation")
        snapshot["conversation_id"] = (
            f"{definition_id}:conversation" if conversation else None
        )
        snapshot["conversation"] = deepcopy(conversation)
        combat = definition.get("combat")
        if combat:
            snapshot.update({
                "maximum_health": combat.get("maximum_health", 30),
                "attack": combat.get("attack", 6), "defense": combat.get("defense", 2),
                "experience_reward": combat.get("experience_reward", 10),
                "respawn_seconds": combat.get("respawn_seconds", 60),
            })
        snapshot["equipment"] = [
            {"slot": entry["slot"], "item_definition_id": _item_reference_id(template, entry)}
            for entry in definition.get("equipment", [])
        ]
        snapshot["loot"] = [
            ({"type": "item", "item_definition_id": _item_reference_id(template, reward),
              "amount": reward.get("amount", 1)} if reward["type"] == "item" else
             {"type": "experience", "amount": reward.get("amount", 1)})
            for reward in definition.get("loot", [])
        ]
    return snapshot


def _select_content(template, context, rng, database):
    pools = template["pools"]
    result = []
    encounters = [pool for pool in pools.get("encounters", [])
                  if _conditions_match(pool.get("conditions"), context)]
    if not encounters:
        return result
    encounter = _weighted(rng, encounters)
    if rng.random() > encounter.get("chance", 1):
        return result
    result.append({
        "type": "encounter", "pool_id": encounter["id"], "definition_id": None,
        "quantity": 1, "snapshot": {}, "config": deepcopy(encounter.get("config", {})),
    })
    for key, kind, collection in (
        ("npc_pool", "npc", "npcs"), ("enemy_pool", "enemy", "enemies"),
        ("item_pool", "item", "items")
    ):
        pool_id = encounter.get(key)
        if not pool_id:
            continue
        candidates = [pool for pool in pools[collection] if pool["id"] == pool_id]
        if not candidates:
            raise GenerationError(f"encounter {encounter['id']!r} references missing pool {pool_id!r}")
        pool = candidates[0]
        if not _conditions_match(pool.get("conditions"), context) or rng.random() > pool.get("chance", 1):
            continue
        count = rng.randint(pool.get("count", {}).get("minimum", 0),
                            pool.get("count", {}).get("maximum", 1))
        choices = [choice for choice in pool["choices"]
                   if _conditions_match(choice.get("conditions"), context)]
        for _ in range(count):
            choice = _weighted(rng, choices)
            local_id = choice.get("local_id")
            if local_id:
                definition = _embedded_definition(template, kind, local_id)
                definition_id = local_definition_id(template, kind, local_id)
                snapshot = _embedded_snapshot(template, kind, definition)
            else:
                definition_id = choice["definition_id"]
                snapshot = _definition_snapshot(database, kind, definition_id)
            result.append({
                "type": kind, "pool_id": pool_id, "definition_id": definition_id,
                "quantity": 1,
                "snapshot": snapshot,
                "config": {},
            })
    return result


def _risk_loot(context, rng, database):
    """Add a small station supply or treasure cache without requiring theme authors to place it."""
    if database is None or context["role"] == "entrance":
        return []
    depth = context["depth_ratio"]
    if context["role"] != "exit" and rng.random() > min(.8, .22 + depth * .38):
        return []
    tier = 1
    if context["difficulty"] >= 4 and rng.random() < min(.8, .25 + depth * .4):
        tier = 2
    if context["difficulty"] >= 8 and rng.random() < min(.65, .15 + depth * .35):
        tier = 3
    choices = {
        1: ("shop:tonic", "treasure:relic", "shop:blade", "shop:coat"),
        2: ("shop:elixir", "treasure:gem", "shop:sabre", "shop:bulwark"),
        3: ("treasure:idol", "shop:mail", "shop:elixir"),
    }[tier]
    definition_id = "stationnet:" + rng.choice(choices)
    snapshot = _definition_snapshot(database, "item", definition_id)
    return [{"type": "item", "pool_id": "stationnet-risk-loot",
             "definition_id": definition_id, "quantity": 1,
             "snapshot": snapshot, "config": {"rarity": tier}}]


def generate_manifest(template, seed, size=None, layout=None, difficulty=None, database=None):
    """Generate a deterministic, connected dungeon without persisting it."""
    errors = validate_template(template, database)
    if errors:
        raise TemplateError(errors)
    generation = template["generation"]
    size_rule = generation["size"]
    difficulty_rule = generation["difficulty"]
    size = size_rule["default"] if size is None else size
    difficulty = difficulty_rule["default"] if difficulty is None else difficulty
    if not _is_int(size, size_rule["minimum"], size_rule["maximum"]):
        raise GenerationError(
            f"size must be from {size_rule['minimum']} to {size_rule['maximum']}"
        )
    if not _is_int(difficulty, difficulty_rule["minimum"], difficulty_rule["maximum"]):
        raise GenerationError(
            f"difficulty must be from {difficulty_rule['minimum']} to {difficulty_rule['maximum']}"
        )
    seed = str(seed)
    digest_input = "|".join((GENERATOR_VERSION, template["id"], template["version"],
                             template_hash(template), seed, str(size), str(difficulty), layout or ""))
    rng = random.Random(int.from_bytes(hashlib.sha256(digest_input.encode()).digest(), "big"))
    layout_options = generation["layouts"]
    if layout is None:
        layout = _weighted(rng, layout_options)["type"]
    elif layout not in {option["type"] for option in layout_options}:
        raise GenerationError(f"layout {layout!r} is not enabled by this theme")
    positions, edges = LAYOUT_BUILDERS[layout](size, rng)
    adjacency, depths = _distances(size, edges)
    if any(depth is None for depth in depths):
        raise GenerationError("layout is disconnected")
    roles = _assign_roles(template, adjacency, depths, rng)
    maximum_depth = max(depths) or 1
    rooms = []
    for index, position in enumerate(positions):
        room_difficulty = max(1, difficulty + round(depths[index] / maximum_depth * 2))
        context = {
            "layout": layout, "role": roles[index], "difficulty": room_difficulty,
            "depth_ratio": depths[index] / maximum_depth,
            "degree": len(adjacency[index]), "dead_end": len(adjacency[index]) == 1,
        }
        room_choices = [pool for pool in template["pools"]["rooms"]
                        if _conditions_match(pool.get("conditions"), context)]
        if not room_choices:
            raise GenerationError(
                f"room {index} ({roles[index]}) has no eligible room template"
            )
        room_template = _weighted(rng, room_choices)
        name = _weighted(rng, room_template["names"])["text"]
        description = _weighted(rng, room_template["descriptions"])["text"]
        environment_id = None
        environments = [pool for pool in template["pools"].get("environments", [])
                        if _conditions_match(pool.get("conditions"), context)]
        if environments:
            environment = _weighted(rng, environments)
            environment_id = environment["id"]
            addition = _weighted(rng, environment["descriptions"])["text"]
            description = f"{description}\n\n{addition}"
        content = _select_content(template, context, rng, database)
        content.extend(_risk_loot(context, rng, database))
        for entry in content:
            if entry["type"] == "enemy":
                scale = max(0, room_difficulty - 2)
                snapshot = entry["snapshot"]
                if snapshot.get("maximum_health") is not None:
                    snapshot["maximum_health"] += scale * 8
                    snapshot["attack"] += scale * 2
                    snapshot["defense"] += scale // 2
                    snapshot["experience_reward"] += scale * 5
        rooms.append({
            "key": f"room-{index:04d}", "ordinal": index, "x": position[0], "y": position[1],
            "role": roles[index], "depth": depths[index], "difficulty": room_difficulty,
            "name": name, "description": description,
            "room_template_id": room_template["id"], "environment_id": environment_id,
            "content": content,
        })
    exits = []
    for first, second in sorted(edges):
        direction = _direction_between(positions[first], positions[second])
        for source, destination, heading in (
            (first, second, direction), (second, first, OPPOSITE[direction])
        ):
            exits.append({
                "from": f"room-{source:04d}", "to": f"room-{destination:04d}",
                "direction": heading, "name": heading,
            })
    exits.sort(key=lambda item: (item["from"], item["direction"], item["to"]))
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "theme": {"id": template["id"], "version": template["version"],
                  "hash": template_hash(template), "name": template["name"]},
        "seed": seed, "layout": layout, "size": size, "difficulty": difficulty,
        "entrance": "room-0000",
        "exit": next(room["key"] for room in rooms if room["role"] == "exit"),
        "rooms": rooms, "exits": exits,
    }
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest):
    """Assert core topology invariants shared by previews and persisted instances."""
    rooms = manifest["rooms"]
    keys = {room["key"] for room in rooms}
    if len(keys) != manifest["size"]:
        raise GenerationError("generated room count or room keys are invalid")
    if manifest["entrance"] not in keys or manifest["exit"] not in keys:
        raise GenerationError("generated entrance or exit is missing")
    adjacency = {key: set() for key in keys}
    for exit_row in manifest["exits"]:
        if exit_row["from"] not in keys or exit_row["to"] not in keys:
            raise GenerationError("generated exit references an unknown room")
        adjacency[exit_row["from"]].add(exit_row["to"])
    visited = {manifest["entrance"]}
    queue = deque(visited)
    while queue:
        queue.extend(neighbor for neighbor in adjacency[queue.popleft()]
                     if neighbor not in visited and not visited.add(neighbor))
    if visited != keys or manifest["exit"] not in visited:
        raise GenerationError("generated dungeon is not fully navigable")
    for source, destinations in adjacency.items():
        for destination in destinations:
            if source not in adjacency[destination]:
                raise GenerationError("generated exit is not bidirectional")


def _record_log(database, status, message, request_data, instance_id=None):
    database.execute(
        "INSERT INTO mud_dungeon_generation_log(instance_id,theme_id,theme_version,seed,"
        "request_json,status,message) VALUES(?,?,?,?,?,?,?)",
        (instance_id, request_data.get("theme_id"), request_data.get("theme_version"),
         str(request_data.get("seed", "")), canonical_json(request_data), status, message),
    )


def _claim_managed_definition(database, template, definition_id, kind, local_id, value):
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    existing = database.execute(
        "SELECT definition_hash FROM mud_dungeon_managed_definitions WHERE definition_id=?",
        (definition_id,),
    ).fetchone()
    if existing is not None:
        if existing["definition_hash"] != digest:
            raise TemplateError([f"embedded definition collision for {definition_id!r}"])
        return False
    table = ("mud_item_definitions" if kind == "item" else
             "mud_conversations" if kind == "conversation" else "mud_npc_definitions")
    if database.execute(f"SELECT 1 FROM {table} WHERE id=?", (definition_id,)).fetchone():
        raise TemplateError([f"embedded definition ID {definition_id!r} conflicts with station content"])
    database.execute(
        "INSERT INTO mud_dungeon_managed_definitions"
        "(definition_id,theme_id,theme_version,definition_kind,local_id,definition_hash) "
        "VALUES(?,?,?,?,?,?)",
        (definition_id, template["id"], template["version"], kind, local_id, digest),
    )
    return True


def _item_reference_id(template, reference):
    return (local_definition_id(template, "item", reference["item_local_id"])
            if reference.get("item_local_id") else reference["item_definition_id"])


def _install_embedded_definitions(database, template):
    """Materialize immutable namespaced definitions for existing gameplay systems."""
    definitions = template.get("definitions", {})
    for item in definitions.get("items", []):
        definition_id = local_definition_id(template, "item", item["id"])
        if _claim_managed_definition(database, template, definition_id, "item", item["id"], item):
            database.execute(
                "INSERT INTO mud_item_definitions"
                "(id,name,description,item_type,properties_json,behaviors_json,equipment_slot) "
                "VALUES(?,?,?,?,?,?,?)",
                (definition_id, item["name"], item["description"], item.get("item_type", "misc"),
                 canonical_json(item.get("properties", {})), canonical_json(item.get("behaviors", {})),
                 item.get("equipment_slot")),
            )
    for kind in ("npcs", "enemies"):
        for npc in definitions.get(kind, []):
            definition_id = local_definition_id(template, kind, npc["id"])
            conversation = npc.get("conversation")
            conversation_id = f"{definition_id}:conversation" if conversation else None
            if conversation and _claim_managed_definition(
                    database, template, conversation_id, "conversation", npc["id"], conversation):
                database.execute(
                    "INSERT INTO mud_conversations(id,name) VALUES(?,?)",
                    (conversation_id, conversation.get("name", f"{npc['name']} conversation")),
                )
                node_ids = {node["id"]: f"{conversation_id}:node:{node['id']}"
                            for node in conversation["nodes"]}
                for node in conversation["nodes"]:
                    database.execute(
                        "INSERT INTO mud_dialogue_nodes(id,conversation_id,node_key,body,is_start) "
                        "VALUES(?,?,?,?,?)",
                        (node_ids[node["id"]], conversation_id, node["id"], node["body"],
                         int(node.get("start", False))),
                    )
                for node in conversation["nodes"]:
                    for order, response in enumerate(node.get("responses", [])):
                        database.execute(
                            "INSERT INTO mud_dialogue_responses"
                            "(id,node_id,response_text,next_node_id,required_state_key,"
                            "required_state_value,state_key,state_value,sort_order) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (f"{node_ids[node['id']]}:response:{response['id']}", node_ids[node["id"]],
                             response["text"], node_ids.get(response.get("next_node_id")),
                             response.get("required_state_key"), response.get("required_state_value"),
                             response.get("state_key"), response.get("state_value"),
                             response.get("sort_order", order)),
                        )
            registry_kind = "enemy" if kind == "enemies" else "npc"
            if not _claim_managed_definition(
                    database, template, definition_id, registry_kind, npc["id"], npc):
                continue
            database.execute(
                "INSERT INTO mud_npc_definitions"
                "(id,name,description,conversation_id,properties_json,behaviors_json) "
                "VALUES(?,?,?,?,?,?)",
                (definition_id, npc["name"], npc["description"], conversation_id,
                 canonical_json(npc.get("properties", {})), canonical_json(npc.get("behaviors", {}))),
            )
            combat = npc.get("combat")
            if combat:
                database.execute(
                    "INSERT INTO mud_npc_combat_profiles"
                    "(npc_definition_id,maximum_health,attack,defense,experience_reward,respawn_seconds) "
                    "VALUES(?,?,?,?,?,?)",
                    (definition_id, combat.get("maximum_health", 30), combat.get("attack", 6),
                     combat.get("defense", 2), combat.get("experience_reward", 10),
                     combat.get("respawn_seconds", 60)),
                )
            for equipment in npc.get("equipment", []):
                database.execute(
                    "INSERT INTO mud_npc_equipment(npc_definition_id,equipment_slot,item_definition_id) "
                    "VALUES(?,?,?)",
                    (definition_id, equipment["slot"], _item_reference_id(template, equipment)),
                )
            for order, reward in enumerate(npc.get("loot", [])):
                target_id = (_item_reference_id(template, reward)
                             if reward["type"] == "item" else None)
                database.execute(
                    "INSERT INTO mud_npc_combat_rewards"
                    "(id,npc_definition_id,reward_type,target_id,amount) VALUES(?,?,?,?,?)",
                    (f"{definition_id}:reward:{order}", definition_id, reward["type"], target_id,
                     reward.get("amount", 1)),
                )


def persist_instance(database, template, manifest, instance_type="random", source_name="external"):
    """Persist an immutable generated design in procedural-only tables."""
    if instance_type not in {"random", "daily"}:
        raise GenerationError("instance type must be 'random' or 'daily'")
    existing = database.execute(
        "SELECT template_hash FROM mud_dungeon_template_versions WHERE theme_id=? AND version=?",
        (template["id"], template["version"]),
    ).fetchone()
    digest = template_hash(template)
    if existing is not None and existing["template_hash"] != digest:
        raise TemplateError([
            f"theme {template['id']!r} version {template['version']!r} was already installed "
            "with different content; change the template version"
        ])
    database.execute(
        "INSERT OR IGNORE INTO mud_dungeon_template_versions"
        "(theme_id,version,template_hash,template_json,source_name) VALUES(?,?,?,?,?)",
        (template["id"], template["version"], digest, canonical_json(template), source_name),
    )
    _install_embedded_definitions(database, template)
    instance_id = f"dungeon:{uuid.uuid4().hex}"
    database.execute(
        "INSERT INTO mud_procedural_instances"
        "(id,instance_type,status,seed,theme_id,theme_version,template_hash,generator_version,"
        "layout,room_count,difficulty,generation_manifest_json) "
        "VALUES(?,?,'pending',?,?,?,?,?,?,?,?,?)",
        (instance_id, instance_type, manifest["seed"], template["id"], template["version"],
         digest, GENERATOR_VERSION, manifest["layout"], manifest["size"],
         manifest["difficulty"], canonical_json(manifest)),
    )
    for room in manifest["rooms"]:
        database.execute(
            "INSERT INTO mud_dungeon_rooms(instance_id,room_key,ordinal,x,y,role,depth,"
            "difficulty,name,description,room_template_id,environment_id,metadata_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (instance_id, room["key"], room["ordinal"], room["x"], room["y"], room["role"],
             room["depth"], room["difficulty"], room["name"], room["description"],
             room["room_template_id"], room["environment_id"], "{}"),
        )
        for order, content in enumerate(room["content"]):
            database.execute(
                "INSERT INTO mud_dungeon_content(id,instance_id,room_key,content_type,pool_id,"
                "definition_id,quantity,snapshot_json,config_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (f"{instance_id}:content:{room['ordinal']}:{order}", instance_id, room["key"],
                 content["type"], content["pool_id"], content["definition_id"],
                 content["quantity"], canonical_json(content["snapshot"]),
                 canonical_json(content["config"])),
            )
    for exit_row in manifest["exits"]:
        database.execute(
            "INSERT INTO mud_dungeon_exits(instance_id,from_room_key,to_room_key,direction,name) "
            "VALUES(?,?,?,?,?)",
            (instance_id, exit_row["from"], exit_row["to"], exit_row["direction"], exit_row["name"]),
        )
    return instance_id


def create_instance(database, template, seed, size=None, layout=None, difficulty=None,
                    instance_type="random", source_name="external"):
    """Generate and persist atomically; retain a diagnostic log on failure."""
    request_data = {
        "theme_id": template.get("id"), "theme_version": template.get("version"),
        "seed": str(seed), "size": size, "layout": layout, "difficulty": difficulty,
        "instance_type": instance_type,
    }
    try:
        manifest = generate_manifest(template, seed, size, layout, difficulty, database)
        instance_id = persist_instance(database, template, manifest, instance_type, source_name)
        _record_log(database, "success", "generation completed", request_data, instance_id)
        return instance_id, manifest
    except (TemplateError, GenerationError) as problem:
        # Caller controls the transaction. Generated rows are written only after a full manifest exists.
        _record_log(database, "failure", str(problem), request_data)
        raise


def clone_instance(database, source_instance_id, instance_type="daily"):
    """Clone an immutable design into a fresh run with isolated mutable state."""
    source = database.execute(
        "SELECT * FROM mud_procedural_instances WHERE id=?",
        (source_instance_id,),
    ).fetchone()
    if source is None or not source["generation_manifest_json"]:
        raise GenerationError("daily dungeon design is unavailable")
    if instance_type not in {"random", "daily"}:
        raise GenerationError("instance type must be 'random' or 'daily'")
    instance_id = f"dungeon:{uuid.uuid4().hex}"
    database.execute(
        "INSERT INTO mud_procedural_instances"
        "(id,instance_type,status,seed,theme_id,theme_version,template_hash,generator_version,"
        "layout,room_count,difficulty,generation_manifest_json) "
        "VALUES(?,?,'pending',?,?,?,?,?,?,?,?,?)",
        (instance_id, instance_type, source["seed"], source["theme_id"],
         source["theme_version"], source["template_hash"], source["generator_version"],
         source["layout"], source["room_count"], source["difficulty"],
         source["generation_manifest_json"]),
    )
    database.execute(
        "INSERT INTO mud_dungeon_rooms(instance_id,room_key,ordinal,x,y,role,depth,difficulty,"
        "name,description,room_template_id,environment_id,metadata_json) "
        "SELECT ?,room_key,ordinal,x,y,role,depth,difficulty,name,description,room_template_id,"
        "environment_id,metadata_json FROM mud_dungeon_rooms WHERE instance_id=?",
        (instance_id, source_instance_id),
    )
    database.execute(
        "INSERT INTO mud_dungeon_exits(instance_id,from_room_key,to_room_key,direction,name) "
        "SELECT ?,from_room_key,to_room_key,direction,name FROM mud_dungeon_exits "
        "WHERE instance_id=?",
        (instance_id, source_instance_id),
    )
    for content in database.execute(
        "SELECT id,room_key,content_type,pool_id,definition_id,quantity,snapshot_json,config_json "
        "FROM mud_dungeon_content WHERE instance_id=? ORDER BY id",
        (source_instance_id,),
    ).fetchall():
        suffix = content["id"].split(":content:", 1)[-1]
        database.execute(
            "INSERT INTO mud_dungeon_content(id,instance_id,room_key,content_type,pool_id,"
            "definition_id,quantity,snapshot_json,config_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (f"{instance_id}:content:{suffix}", instance_id, content["room_key"],
             content["content_type"], content["pool_id"], content["definition_id"],
             content["quantity"], content["snapshot_json"], content["config_json"]),
        )
    return instance_id, json.loads(source["generation_manifest_json"])


def format_preview(manifest):
    lines = [
        f"{manifest['theme']['id']}@{manifest['theme']['version']} seed={manifest['seed']}",
        f"layout={manifest['layout']} rooms={manifest['size']} difficulty={manifest['difficulty']}",
        f"entrance={manifest['entrance']} exit={manifest['exit']}",
    ]
    destinations = {}
    for exit_row in manifest["exits"]:
        destinations.setdefault(exit_row["from"], []).append(
            f"{exit_row['direction']}->{exit_row['to']}"
        )
    for room in manifest["rooms"]:
        content = ", ".join(item["pool_id"] for item in room["content"]) or "none"
        lines.append(
            f"{room['key']} ({room['x']},{room['y']}) role={room['role']} "
            f"depth={room['depth']} template={room['room_template_id']} content={content} "
            f"exits=[{', '.join(destinations.get(room['key'], []))}]"
        )
    return "\n".join(lines)


def _load_installed(theme_id):
    themes = discover_templates()
    if theme_id not in themes:
        available = ", ".join(sorted(themes)) or "none"
        raise click.ClickException(f"unknown theme {theme_id!r}; installed themes: {available}")
    return themes[theme_id]


@click.group("dungeon")
def dungeon_cli():
    """Validate, preview, generate, and diagnose procedural dungeons."""


@dungeon_cli.command("themes")
@with_appcontext
def list_themes_command():
    """List external themes installed in the configured data directory."""
    try:
        themes = discover_templates()
    except TemplateError as problem:
        raise click.ClickException(str(problem)) from problem
    if not themes:
        click.echo(f"No themes installed in {themes_directory()}")
    for theme_id, (path, template) in themes.items():
        author = f" by {template['author']}" if template.get("author") else ""
        click.echo(f"{theme_id}@{template['version']}  {template['name']}{author}  ({path.name})")


@dungeon_cli.command("definitions")
@with_appcontext
def list_definitions_command():
    """List stable NPC and item IDs available to external themes."""
    database = get_db()
    click.echo("NPC definitions:")
    npcs = database.execute(
        "SELECT d.id,d.name FROM mud_npc_definitions AS d WHERE NOT EXISTS "
        "(SELECT 1 FROM mud_dungeon_managed_definitions AS m WHERE m.definition_id=d.id) "
        "ORDER BY d.name COLLATE NOCASE,d.id"
    ).fetchall()
    for row in npcs:
        click.echo(f"  {row['id']}  {row['name']}")
    if not npcs:
        click.echo("  (none)")
    click.echo("Item definitions:")
    items = database.execute(
        "SELECT d.id,d.name FROM mud_item_definitions AS d WHERE NOT EXISTS "
        "(SELECT 1 FROM mud_dungeon_managed_definitions AS m WHERE m.definition_id=d.id) "
        "ORDER BY d.name COLLATE NOCASE,d.id"
    ).fetchall()
    for row in items:
        click.echo(f"  {row['id']}  {row['name']}")
    if not items:
        click.echo("  (none)")


@dungeon_cli.command("validate")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--check-definitions", is_flag=True, help="Also verify referenced NPC/item IDs.")
@with_appcontext
def validate_command(path, check_definitions):
    """Validate a JSON theme file and print all actionable errors."""
    try:
        template = read_template(path)
    except TemplateError as problem:
        raise click.ClickException(str(problem)) from problem
    errors = validate_template(template, get_db() if check_definitions else None)
    if errors:
        raise click.ClickException("\n".join(errors))
    click.echo(f"Valid: {template['id']}@{template['version']} ({template_hash(template)})")


def _generation_options(function):
    function = click.option("--difficulty", type=int)(function)
    function = click.option("--layout", type=click.Choice(sorted(SUPPORTED_LAYOUTS)))(function)
    function = click.option("--size", type=int)(function)
    function = click.option("--seed", required=True)(function)
    return function


@dungeon_cli.command("preview")
@click.argument("theme_id")
@click.option("--json-output", is_flag=True, help="Print the canonical JSON manifest.")
@_generation_options
@with_appcontext
def preview_command(theme_id, seed, size, layout, difficulty, json_output):
    """Generate a read-only preview for a theme and seed."""
    try:
        _path, template = _load_installed(theme_id)
        manifest = generate_manifest(template, seed, size, layout, difficulty, get_db())
    except (TemplateError, GenerationError) as problem:
        raise click.ClickException(str(problem)) from problem
    click.echo(canonical_json(manifest) if json_output else format_preview(manifest))


@dungeon_cli.command("generate")
@click.argument("theme_id")
@click.option("--type", "instance_type", type=click.Choice(["random", "daily"]), default="random")
@_generation_options
@with_appcontext
def generate_command(theme_id, seed, size, layout, difficulty, instance_type):
    """Persist a pending generated design without starting a playable run."""
    database = get_db()
    try:
        path, template = _load_installed(theme_id)
        database.execute("BEGIN IMMEDIATE")
        instance_id, manifest = create_instance(
            database, template, seed, size, layout, difficulty, instance_type, path.name
        )
        database.commit()
    except (TemplateError, GenerationError) as problem:
        database.commit()  # retain the failure record; no partial instance rows exist
        raise click.ClickException(str(problem)) from problem
    click.echo(f"Generated {instance_id}: {manifest['layout']}, {manifest['size']} rooms")


@dungeon_cli.command("failures")
@click.option("--limit", type=click.IntRange(1, 200), default=20, show_default=True)
@with_appcontext
def failures_command(limit):
    """Show recent persisted generation failures."""
    rows = get_db().execute(
        "SELECT id,theme_id,theme_version,seed,message,created_at "
        "FROM mud_dungeon_generation_log WHERE status='failure' ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        click.echo("No generation failures recorded.")
    for row in rows:
        click.echo(
            f"#{row['id']} {row['created_at']} {row['theme_id']}@{row['theme_version']} "
            f"seed={row['seed']}: {row['message']}"
        )


def init_app(app):
    app.config.setdefault("DUNGEON_THEMES_DIR", str(Path(app.config["DATA_DIR"]) / "dungeon_themes"))
    app.config.setdefault("DUNGEONS_DIR", str(Path(app.config["DATA_DIR"]) / "dungeons"))
    themes_path = Path(app.config["DUNGEON_THEMES_DIR"])
    themes_path.mkdir(parents=True, exist_ok=True)
    custom_path = Path(app.config["DUNGEONS_DIR"])
    custom_path.mkdir(parents=True, exist_ok=True)
    # Bundled playable themes are available on existing and new stations.
    # Files already installed by a user remain authoritative.
    bundled_dir = Path(__file__).with_name("dungeon_templates")
    for name in ("generic-fantasy-dungeon.json", "desert.json", "jungle-ruins.json", "frozen-frontier.json"):
        bundled_theme = bundled_dir / name
        installed_theme = themes_path / name
        if bundled_theme.is_file() and not installed_theme.exists():
            shutil.copyfile(bundled_theme, installed_theme)
    for source_name, installed_name in (("starter-theme.json", "_template.json"),
                                        ("TEMPLATEREADME.md", "README.md"),
                                        ("theme-schema.json", "theme-schema.json")):
        installed = custom_path / installed_name
        if not installed.exists():
            shutil.copyfile(bundled_dir / source_name, installed)
    app.cli.add_command(dungeon_cli)
