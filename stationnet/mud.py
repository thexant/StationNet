"""Native MUD service and server-authoritative action dispatch."""
import contextvars
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
from io import BytesIO
import json
import math
import re
import secrets
import shlex
import sqlite3
import random
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for

from .auth import admin_required, login_required
from .db import get_db
from . import dungeons, character_creation, mud_combat, mud_conditions, mud_effects, mud_firearms, mud_compat, mud_skills, mud_triggers, mud_wilderness, moderation
from .security import validate_csrf

bp = Blueprint("mud", __name__, url_prefix="/mud")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12,80}$")
PRESENCE_SECONDS = 45
DISCONNECT_GRACE_SECONDS = 4
PROGRESSION_TRACKS = frozenset({"shared", "traditional", "procedural"})
ACTIVE_PROGRESSION_TRACK = "shared"
HEALTH_PER_LEVEL = 10
MAX_EXPERIENCE = 2**63 - 1
MAX_LEVEL = 50
ATTRIBUTE_CAP = 30
ATTRIBUTE_SUMMARIES = {
    "strength": "melee damage and accuracy",
    "agility": "accuracy, evasion, firearm damage, flee and stealth",
    "intellect": "spell power, healing, experience and mana per level",
    "vitality": "health per level, mitigation and defending",
}
DIRECTIONS = (
    "north", "northeast", "east", "southeast", "south", "southwest",
    "west", "northwest", "up", "down", "in", "out",
)


def can_build(database, user):
    """Return whether a user may edit the handcrafted traditional world."""
    if user is None:
        return False
    return bool(user["is_admin"] or database.execute(
        "SELECT 1 FROM mud_builder_permissions WHERE user_id=?", (user["id"],)
    ).fetchone())


def builder_required(view):
    """Protect builder UI and mutations independently of general administration."""
    @wraps(view)
    @login_required
    def wrapped(**kwargs):
        if not can_build(get_db(), g.user):
            abort(403)
        return view(**kwargs)
    return wrapped


class ActionError(ValueError):
    """An expected, user-visible action rejection."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class EventDraft:
    body: str
    event_type: str = "system"
    audience_type: str = "private"
    audience_key: str | None = None
    recipient_ids: tuple[int, ...] | None = None


@dataclass(frozen=True)
class ActionDefinition:
    key: str
    label: str
    description: str
    handler: object
    commands: tuple[str, ...] = ()
    group: str = "SYSTEM"
    permission: str = "member"
    show_context: bool = True


@dataclass(frozen=True)
class ActionRequest:
    definition: ActionDefinition
    parameters: dict = field(default_factory=dict)
    input_method: str = "command"
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    client_id: str = ""


class ActionRegistry:
    """Maps command aliases to server-authoritative action definitions."""

    def __init__(self):
        self._actions = {}
        self._commands = {}

    def register(self, definition):
        if definition.key in self._actions:
            raise RuntimeError(f"Duplicate MUD action: {definition.key}")
        self._actions[definition.key] = definition
        for command in definition.commands:
            self.register_command(command, definition)

    def register_command(self, command, definition, prefix_arguments=()):
        """Register an alias, optionally supplying arguments for shorthand commands."""
        alias = command.lower()
        if alias in self._commands:
            raise RuntimeError(f"Duplicate MUD command: {alias}")
        self._commands[alias] = (definition, tuple(prefix_arguments))

    def get(self, key):
        return self._actions.get(key)

    def from_command(self, text):
        if len(text) > 500:
            raise ActionError("Commands cannot exceed 500 characters.")
        try:
            parts = shlex.split(text.strip().lstrip("/"))
        except ValueError as problem:
            raise ActionError(f"Command syntax error: {problem}") from problem
        if not parts:
            raise ActionError("Enter a command, such as /help.")
        command = self._commands.get(parts[0].lower())
        if command is None:
            raise ActionError("Unknown command. Use /help to list available commands.")
        definition, prefix_arguments = command
        return definition, {"arguments": [*prefix_arguments, *parts[1:]]}

def _authorized(definition, user):
    return definition.permission == "member" or (
        definition.permission == "admin" and bool(user["is_admin"])
    )


def experience_for_level(level):
    """Return the cumulative experience floor for a level."""
    if not isinstance(level, int) or isinstance(level, bool) or level < 1:
        raise ValueError("Level must be a positive integer.")
    return 100 * (level - 1) ** 2


def level_for_experience(experience):
    """Resolve a level from cumulative experience without trusting a client."""
    if not isinstance(experience, int) or isinstance(experience, bool) or experience < 0:
        raise ValueError("Experience must be a non-negative integer.")
    return math.isqrt(experience // 100) + 1


def attribute_points_for_level(level):
    """Return the spendable attribute points earned on reaching a level."""
    return 1 + (1 if level % 5 == 0 else 0)


def level_bonuses(level):
    """Return the combat bonuses every character earns from level alone."""
    return {
        "attack": (level - 1) // 3,
        "spell_power": (level - 1) // 3,
        "healing": (level - 1) // 2,
        "defense": (level - 1) // 5,
        "accuracy": (level - 1) // 2,
        "evasion": (level - 1) // 4,
    }


def health_per_level(vitality):
    return max(1, HEALTH_PER_LEVEL + (vitality - 10) // 2)


def mana_per_level(intellect):
    return max(1, 2 + (intellect - 10) // 2)


def resource_delta(level, old_attributes, new_attributes):
    """Return the maximum health and mana change caused by an attribute change."""
    health = ((new_attributes["vitality"] - old_attributes["vitality"]) * 5 +
              (level - 1) * (health_per_level(new_attributes["vitality"]) -
                             health_per_level(old_attributes["vitality"])))
    mana = ((new_attributes["intellect"] - old_attributes["intellect"]) * 3 +
            (level - 1) * (mana_per_level(new_attributes["intellect"]) -
                           mana_per_level(old_attributes["intellect"])))
    return health, mana


def level_up_text(result):
    """Describe level and skill gains from an award_experience result, or return ''."""
    parts = []
    if result["levels_gained"]:
        points = result["attribute_points_gained"]
        parts.append(f"You reached level {result['character']['level']}!"
                     + (f" +{points} attribute point{'s' if points != 1 else ''} — use /train." if points else ""))
    skill_points = result.get("skill_points_gained", 0)
    if skill_points:
        parts.append(f"+{skill_points} skill point{'s' if skill_points != 1 else ''} — use /skills.")
    parts += result.get("skill_notices", [])
    return " ".join(parts)


def _trained_attributes(value):
    try:
        trained = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(trained, dict):
        return {}
    return {key: trained[key] for key in character_creation.ATTRIBUTES
            if type(trained.get(key)) is int and trained[key] > 0}


def _crossover_settings(database):
    row = database.execute(
        "SELECT progression_shared,inventory_shared FROM mud_crossover_settings WHERE id=1"
    ).fetchone()
    return {
        "progression_shared": True if row is None else bool(row["progression_shared"]),
        "inventory_shared": True if row is None else bool(row["inventory_shared"]),
    }


def _current_mode(database, user_id):
    row = database.execute(
        "SELECT mode FROM mud_player_locations WHERE user_id=?", (user_id,)
    ).fetchone()
    if row is not None and row["mode"] in {"traditional", "procedural"}:
        return row["mode"]
    modes = _mode_settings(database)
    return "traditional" if modes["traditional"] else "procedural"


# Commands that only exist to spend the resource; worlds without it hide them.
RESOURCE_ACTIONS = frozenset({"spells", "cast"})
RESOURCE_DISABLED_DESCRIPTIONS = {
    "character": "View your character identity, health, active effects, attributes, and progression.",
    "rest": "Recover health outside combat.",
}
_RESOURCE_WORD_RE = re.compile(r"\b(mana|Mana|MANA)\b")


def resource_settings(database, user_id=None):
    """The active world's name for mana, or whether it has one; procedural mode keeps Mana."""
    from .mud_worlds import DEFAULT_RESOURCE
    row = None
    if user_id is None or _current_mode(database, user_id) == "traditional":
        row = database.execute(
            "SELECT resource_enabled,resource_name FROM mud_world_packages WHERE active=1"
        ).fetchone()
    enabled = DEFAULT_RESOURCE["enabled"] if row is None else bool(row["resource_enabled"])
    name = DEFAULT_RESOURCE["name"] if row is None else row["resource_name"]
    # Words with inner capitals (PSI, Ki-Force) read better unchanged mid-sentence.
    noun = " ".join(word if word[1:] != word[1:].lower() else word.lower() for word in name.split())
    return {"enabled": enabled, "name": name, "noun": noun}


def resource_text(text, resource):
    """Rename every mention of mana in player-facing text."""
    forms = {"mana": resource["noun"], "Mana": resource["name"], "MANA": resource["name"].upper()}
    return _RESOURCE_WORD_RE.sub(lambda match: forms[match.group(1)], text)


def _action_description(definition, resource):
    if not resource["enabled"] and definition.key in RESOURCE_DISABLED_DESCRIPTIONS:
        return RESOURCE_DISABLED_DESCRIPTIONS[definition.key]
    return resource_text(definition.description, resource)


def _attribute_summary(key, resource):
    if key == "intellect" and not resource["enabled"]:
        return "spell power, healing and experience"
    return resource_text(ATTRIBUTE_SUMMARIES[key], resource)


def _progression_track(database, user_id):
    settings = _crossover_settings(database)
    return "shared" if settings["progression_shared"] else _current_mode(database, user_id)


def _inventory_context(database, user_id):
    settings = _crossover_settings(database)
    mode = _current_mode(database, user_id)
    return {
        "mode": mode,
        "shared": settings["inventory_shared"],
        "loadout": "shared" if settings["inventory_shared"] else mode,
    }


def _character_row(database, user_id, track_key=None):
    track_key = track_key or _progression_track(database, user_id)
    if track_key not in PROGRESSION_TRACKS:
        raise ValueError("Unknown progression track.")
    character_id = f"stationnet:character:{user_id}"
    user = database.execute(
        "SELECT callsign FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if user is None:
        raise ValueError("Unknown StationNet account.")
    database.execute(
        "INSERT OR IGNORE INTO mud_characters(id,user_id,display_name) VALUES(?,?,?)",
        (character_id, user_id, user["callsign"]),
    )
    database.execute(
        "INSERT OR IGNORE INTO mud_character_progression(character_id,track_key) "
        "VALUES(?,?)", (character_id, track_key),
    )
    return database.execute(
        "SELECT c.id,c.user_id,c.display_name,p.track_key,p.level,p.experience,"
        "p.current_health,p.maximum_health,p.current_mana,p.maximum_mana,"
        "p.strength,p.agility,p.intellect,p.vitality,p.attribute_points,p.trained_attributes_json "
        "FROM mud_characters AS c JOIN mud_character_progression AS p "
        "ON p.character_id=c.id WHERE c.user_id=? AND p.track_key=?",
        (user_id, track_key),
    ).fetchone()


def character_state(database, user_id, track_key=None):
    """Return the authoritative identity and selected progression track."""
    row = _character_row(database, user_id, track_key)
    if character_creation.sync_origin_resources(database, row["id"]):
        row = _character_row(database, user_id, track_key)
    origin = database.execute(
        "SELECT creation_state,race_key,class_key FROM mud_characters WHERE id=?", (row["id"],)
    ).fetchone()
    config = character_creation.options(database, include_inactive=True, include_removed=True)
    race = next((item for item in config["races"] if item["key"] == origin["race_key"]), None)
    archetype = next((item for item in config["classes"] if item["key"] == origin["class_key"]), None)
    known = mud_skills.definitions(database, include_inactive=True, include_removed=True)
    active = mud_skills.definitions(database)
    skills = []
    for skill in mud_skills.character_rows(database, row["id"]):
        definition = known.get(skill["skill_key"])
        skills.append({**skill, "name": definition["name"] if definition else skill["skill_key"],
                       "group": definition["group"] if definition else "knowledge",
                       "active": skill["skill_key"] in active,
                       "max_rank": definition["max_rank"] if definition else mud_skills.MAX_RANK,
                       "next_rank_at": mud_skills.next_rank_cost(skill["rank"]),
                       "effects": mud_skills.describe_effects(definition, skill["rank"]) if definition else ""})
    skills.sort(key=lambda skill: (mud_skills.GROUPS.index(skill["group"]), skill["name"].casefold()))
    at_max_level = row["level"] >= MAX_LEVEL
    level_floor = experience_for_level(row["level"])
    next_floor = experience_for_level(row["level"] + 1)
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": row["display_name"],
        "creation_state": origin["creation_state"],
        "race": race["name"] if race else None,
        "class": archetype["name"] if archetype else None,
        "origin": _origin_summary(race, archetype, known),
        "skills": skills,
        "skill_points": mud_skills.available_points(database, row["id"]),
        "mode": _current_mode(database, user_id),
        "progression_track": row["track_key"],
        "progression_label": {
            "shared": "Shared progression",
            "traditional": "Traditional progression",
            "procedural": "Procedural progression",
        }[row["track_key"]],
        "level": row["level"],
        "max_level": MAX_LEVEL,
        "at_max_level": at_max_level,
        "experience": row["experience"],
        "experience_into_level": 0 if at_max_level else row["experience"] - level_floor,
        "experience_for_next_level": 0 if at_max_level else next_floor - level_floor,
        "experience_to_next_level": 0 if at_max_level else next_floor - row["experience"],
        "attribute_points": row["attribute_points"],
        "trained_attributes": _trained_attributes(row["trained_attributes_json"]),
        "combat": _combat_profile(database, row, origin["creation_state"]),
        "health": row["current_health"],
        "maximum_health": row["maximum_health"],
        "mana": row["current_mana"],
        "maximum_mana": row["maximum_mana"],
        "resource": resource_settings(database, user_id),
        "effects": mud_effects.list_effects(database, "character", row["id"]),
        "attributes": {
            "strength": row["strength"],
            "agility": row["agility"],
            "intellect": row["intellect"],
            "vitality": row["vitality"],
        },
    }


def award_experience(database, user_id, amount, track_key=None):
    """Award XP on a track; the caller owns the surrounding transaction."""
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ValueError("Experience award must be a positive integer.")
    resolved_track = track_key or _progression_track(database, user_id)
    before = character_state(database, user_id, resolved_track)
    skill_notices = []
    points_before = mud_skills.earned_points(mud_skills.highest_level(database, before["id"]))
    if before["creation_state"] == "complete":
        bonus = max(0, before["attributes"]["intellect"] - 8)
        bonus += _character_effects(database, before["id"])["experience_percent"]
        amount += amount * bonus // 100
        # Larger rewards teach more, so Lore no longer advances once per award regardless of size.
        skill_notices = mud_skills.practice(database, before["id"], "experience_gained", max(1, amount // 25))
    experience = before["experience"] + amount
    if experience > MAX_EXPERIENCE:
        raise ValueError("Experience total exceeds the supported range.")
    # Levels are capped, but a character already above the cap is never demoted.
    level = max(before["level"], min(MAX_LEVEL, level_for_experience(experience)))
    levels_gained = level - before["level"]
    points_gained = sum(attribute_points_for_level(reached)
                        for reached in range(before["level"] + 1, level + 1))
    health_gain = levels_gained * health_per_level(before["attributes"]["vitality"])
    mana_gain = levels_gained * mana_per_level(before["attributes"]["intellect"])
    maximum_health = before["maximum_health"] + health_gain
    current_health = min(
        maximum_health, before["health"] + health_gain
    )
    maximum_mana = before["maximum_mana"] + mana_gain
    current_mana = min(maximum_mana, before["mana"] + mana_gain)
    database.execute(
        "UPDATE mud_character_progression SET experience=?,level=?,current_health=?,"
        "maximum_health=?,current_mana=?,maximum_mana=?,attribute_points=attribute_points+?,"
        "updated_at=CURRENT_TIMESTAMP WHERE character_id=? AND track_key=?",
        (experience, level, current_health, maximum_health, current_mana, maximum_mana,
         points_gained, before["id"], resolved_track),
    )
    # Origins may grant extra health or mana per level on top of attribute growth.
    character_creation.sync_origin_resources(database, before["id"])
    points_after = mud_skills.earned_points(mud_skills.highest_level(database, before["id"]))
    return {
        "amount": amount,
        "levels_gained": levels_gained,
        "attribute_points_gained": points_gained,
        "skill_points_gained": points_after - points_before,
        "skill_notices": skill_notices,
        "previous_level": before["level"],
        "character": character_state(database, user_id, resolved_track),
    }


def _origin_summary(race, archetype, skills):
    """Player-facing digest of what a race and class do beyond attributes."""
    profile = character_creation.origin_profile(race, archetype)
    proficiencies = profile["proficiencies"]
    affinities = {(skills[key]["name"] if key in skills else key): value
                  for key, value in profile["skill_affinities"].items()}
    growth = {key: value for key, value in profile["progression"].items() if value}
    trained = proficiencies["weapons"] + [item for item in proficiencies["armor"]
                                          if item not in proficiencies["weapons"]]
    summary = [label + ": " + text for label, text in (
        ("Traits", ", ".join(profile["tags"])),
        ("Abilities", ", ".join(name for name in profile["spells"] if name in SPELLS)),
        ("Trained with", ", ".join(trained)),
        ("Resists", " · ".join(f"{key} {value}%" for key, value in profile["resistances"].items() if value)),
        ("Learns faster", " · ".join(f"{key} {value:+d}%" for key, value in affinities.items() if value)),
        ("Growth", " · ".join(f"{key.replace('_', ' ')} {value:+d}" for key, value in growth.items())),
    ) if text]
    return {
        "summary": summary,
        "traits": [origin["traits"] for origin in (race, archetype) if origin],
        "tags": profile["tags"],
        "abilities": [name for name in profile["spells"] if name in SPELLS],
        "proficiencies": trained,
        "resistances": profile["resistances"],
        "affinities": affinities,
        "effects": {mud_skills.EFFECT_TYPES.get(key, key): value
                    for key, value in profile["effects"].items() if value},
        "growth": growth,
    }


def _origin_options(database, character_id):
    return character_creation.character_origins(database, character_id)


def _origin_profile(database, character_id):
    return character_creation.origin_profile(*_origin_options(database, character_id))


def _proficiencies(database, character_id):
    """Gear categories a character is trained with: from skills and from race and class."""
    proficiencies = _origin_profile(database, character_id)["proficiencies"]
    return (mud_skills.proficiencies(database, character_id)
            | set(proficiencies["weapons"]) | set(proficiencies["armor"]))


def _character_effects(database, character_id, gear=None):
    """Origin, skill, and active effect modifiers; skill filters follow equipped gear."""
    if gear is None:
        gear = _equipment_combat_totals(database, character_id=character_id)
    totals = mud_skills.bonuses(database, character_id, gear["categories"], gear["categories"])
    race, archetype = _origin_options(database, character_id)
    for origin in (race, archetype):
        for key, value in (origin or {}).get("effects", {}).items():
            if key in totals:
                totals[key] += value
    for key, cap in mud_skills.EFFECT_CAPS.items():
        totals[key] = min(totals[key], cap)
    for key in ("attack", "defense", "accuracy", "evasion"):
        totals[key] += mud_effects.modifier(database, "character", character_id, key)
    return totals


def _practice(database, character_id, trigger, amount=1, categories=None):
    """Practice skills for a trigger and turn rank-ups into private events."""
    return [EventDraft(notice, event_type="character")
            for notice in mud_skills.practice(database, character_id, trigger, amount, categories=categories)]


def _flee_chance(agility, bonus=0):
    return min(95, max(35, min(75, 50 + (agility - 10) * 2) + bonus))


def _player_mitigation(character, gear, effects):
    """Flat damage reduction on hits that land; Vitality and armor drive it, not Agility."""
    return (3 + (character["vitality"] - 10) // 3 + gear["defense"]
            + _equipment_stat_bonus(gear, character["vitality"], "defense")
            + effects["defense"] + level_bonuses(character["level"])["defense"])


def _player_accuracy(character, gear, effects, kind):
    """Accuracy for melee, firearm, or spell attacks before the target's evasion."""
    agility, strength = character["agility"], character["strength"]
    base = {"melee": (agility - 10) + (strength - 10) // 2,
            "firearm": (agility - 10) * 3 // 2 + gear["firearm_accuracy"],
            "spell": character["intellect"] - 10 + mud_combat.SPELL_ACCURACY_BONUS}[kind]
    return base + gear["accuracy"] + effects["accuracy"] + level_bonuses(character["level"])["accuracy"]


def _player_evasion(character, gear, effects, guarding=False):
    """Agility, level, skills, gear, and effects help a character dodge; bracing helps more."""
    return (2 * (character["agility"] - 10) + level_bonuses(character["level"])["evasion"]
            + gear["evasion"] + effects["evasion"] + (mud_combat.DEFEND_EVASION if guarding else 0))


def _npc_hit_stat(database, npc, encounter, gear, stat):
    """An NPC's authored accuracy or evasion plus its gear and active effects; missing means 0."""
    authored = npc[f"combat_{stat}"] if f"combat_{stat}" in npc.keys() else None
    return ((authored or 0) + gear[stat]
            + mud_effects.modifier(database, "encounter", encounter["id"], stat))


def _resisted(damage, percent):
    """Scale a landed blow by a resistance percentage; only full immunity reaches 0."""
    if percent >= 100:
        return 0
    return max(1, damage - damage * percent // 100) if damage > 0 else damage


def _hit_roll():
    """A 0-99 roll used only for hit checks, separate from stealth, flee, and loot rolls."""
    return _COMBAT_RNG.randrange(100)


_COMBAT_RNG = random.SystemRandom()


def _combat_profile(database, row, creation_state):
    """Summarize how attributes, level, gear, and skills shape combat before enemy defense."""
    gear = _equipment_combat_totals(database, character_id=row["id"])
    effects = _character_effects(database, row["id"], gear)
    bonuses = level_bonuses(row["level"])
    strength, agility = row["strength"], row["agility"]
    intellect = row["intellect"]
    evasion = _player_evasion(row, gear, effects)
    return {
        "melee_damage": 4 + strength // 2 + gear["attack"] + _equipment_stat_bonus(gear, strength, "attack")
                        + effects["attack"] + effects["melee_damage"] + bonuses["attack"],
        "firearm_damage": 6 + agility // 2 + effects["attack"] + effects["firearm_damage"] + bonuses["attack"],
        "spell_power": intellect // 3 + effects["spell_power"] + bonuses["spell_power"],
        "healing_bonus": intellect // 2 + effects["healing"] + bonuses["healing"],
        "mitigation": _player_mitigation(row, gear, effects),
        "melee_accuracy": _player_accuracy(row, gear, effects, "melee"),
        "firearm_accuracy": _player_accuracy(row, gear, effects, "firearm"),
        "spell_accuracy": _player_accuracy(row, gear, effects, "spell"),
        "evasion": evasion,
        # Chances against an average foe with no accuracy or evasion of its own.
        "melee_hit_chance": mud_combat.hit_chance(_player_accuracy(row, gear, effects, "melee"), 0),
        "chance_to_be_hit": mud_combat.hit_chance(0, evasion),
        "flee_chance": _flee_chance(agility, effects["flee"]),
        "stealth_chance": _stealth_chance(agility, 5, effects["stealth"]),
        "search_bonus": effects["search"],
        "lockpick_bonus": effects["lockpick"],
        "shop_discount": effects["shop_discount"],
        "experience_bonus": (max(0, intellect - 8) + effects["experience_percent"]
                             if creation_state == "complete" else 0),
        "level_bonuses": bonuses,
        "proficiency_warnings": gear["unproficient"],
    }


def _status_action(_database, _user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The status action does not accept arguments.")
    modes = _mode_settings(_database)
    enabled = [name for name in ("traditional", "procedural") if modes[name]]
    return [EventDraft(f"MUD service online. Enabled modes: {', '.join(enabled)}.")]


def _character_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The character action does not accept arguments.")
    character = character_state(database, user["id"])
    attributes = character["attributes"]
    trained = character["trained_attributes"]
    combat = character["combat"]
    skills = ", ".join(f"{skill['name']} {skill['rank']}" + ("" if skill["active"] else " (inactive)")
                       for skill in character["skills"])
    active_effects = ", ".join(
        f"{effect['effect_key']} {effect['potency']} ({effect['remaining_turns']} turns)"
        for effect in character["effects"]
    )
    if character["at_max_level"]:
        experience = f"Experience: {character['experience']} (maximum level reached)"
    else:
        filled = 20 * character["experience_into_level"] // character["experience_for_next_level"]
        experience = (f"Experience: [{'#' * filled}{'.' * (20 - filled)}] "
                      f"{character['experience_into_level']}/{character['experience_for_next_level']} "
                      f"({character['experience_to_next_level']} to level {character['level'] + 1})")
    lines = [
        f"{character['name']} — Level {character['level']}/{character['max_level']}",
        f"Origin: {character['race'] or 'Unchosen'} / {character['class'] or 'Unchosen'}",
        *character["origin"]["summary"],
        f"Health: {character['health']}/{character['maximum_health']}",
        *([f"{character['resource']['name']}: {character['mana']}/{character['maximum_mana']}"]
          if character["resource"]["enabled"] else []),
        experience,
    ]
    if character["attribute_points"]:
        lines.append(f"Attribute points: {character['attribute_points']} unspent — use /train <attribute>")
    lines.append(" · ".join(
        f"{key.title()} {attributes[key]}" + (f" (+{trained[key]} trained)" if trained.get(key) else "")
        for key in character_creation.ATTRIBUTES
    ))
    bonuses = combat["level_bonuses"]
    lines += [
        f"Combat: melee {combat['melee_damage']} · firearm {combat['firearm_damage']} + weapon · "
        f"spell power +{combat['spell_power']} · healing +{combat['healing_bonus']}",
        f"Accuracy: melee {combat['melee_accuracy']:+d} · firearm {combat['firearm_accuracy']:+d} · "
        f"spells {combat['spell_accuracy']:+d} · evasion {combat['evasion']:+d} "
        f"(vs. an average foe: you hit {combat['melee_hit_chance']}% in melee, "
        f"it hits you {combat['chance_to_be_hit']}%)",
        f"Defense: mitigation {combat['mitigation']} · flee {combat['flee_chance']}% · "
        f"stealth {combat['stealth_chance']}% · experience +{combat['experience_bonus']}%",
        *([f"Skills: search +{combat['search_bonus']} · lockpicking +{combat['lockpick_bonus']} · "
           f"merchant discount {combat['shop_discount']}%"]
          if combat["search_bonus"] or combat["lockpick_bonus"] or combat["shop_discount"] else []),
        f"Level bonuses: attack +{bonuses['attack']} · spells +{bonuses['spell_power']} · "
        f"healing +{bonuses['healing']} · mitigation +{bonuses['defense']} · "
        f"accuracy +{bonuses['accuracy']} · evasion +{bonuses['evasion']}",
        f"Skills: {skills or 'none'}" + (f" · {character['skill_points']} skill point"
                                         f"{'s' if character['skill_points'] != 1 else ''} — use /skills"
                                         if character["skill_points"] else ""),
        *([f"Untrained gear (reduced effect): {', '.join(combat['proficiency_warnings'])}"]
          if combat["proficiency_warnings"] else []),
        f"Effects: {active_effects or 'none'}",
        f"Track: {character['progression_label']}",
    ]
    return [EventDraft("\n".join(lines), event_type="character")]


def _resolve_attribute(name):
    key = str(name).casefold()
    matches = [attribute for attribute in character_creation.ATTRIBUTES if attribute.startswith(key)]
    if not key or len(matches) != 1:
        raise ActionError("Choose strength, agility, intellect, or vitality.")
    return matches[0]


def _train_action(database, user, parameters):
    args = [str(value) for value in parameters.get("arguments", [])]
    if len(args) > 2:
        raise ActionError("Use /train <attribute> [points].")
    character = character_state(database, user["id"])
    if character["creation_state"] != "complete":
        raise ActionError("Create your character before training attributes.")
    points = character["attribute_points"]
    if not args:
        lines = [f"Attribute points available: {points}. Each level grants 1 point (2 at every fifth level)."]
        for key in character_creation.ATTRIBUTES:
            lines.append(f"- {key.title()} {character['attributes'][key]}/{ATTRIBUTE_CAP}: "
                         f"{_attribute_summary(key, character['resource'])}")
        lines.append("Spend points with /train <attribute> [points], for example /train str 2.")
        return [EventDraft("\n".join(lines), event_type="character")]
    attribute = _resolve_attribute(args[0])
    try:
        amount = int(args[1]) if len(args) > 1 else 1
    except ValueError as problem:
        raise ActionError("Points must be a whole number.") from problem
    if amount < 1:
        raise ActionError("Train at least one point.")
    if amount > points:
        raise ActionError(f"You have {points} attribute point{'s' if points != 1 else ''} available.")
    old = dict(character["attributes"])
    if old[attribute] + amount > ATTRIBUTE_CAP:
        raise ActionError(f"{attribute.title()} cannot exceed {ATTRIBUTE_CAP}.")
    new = {**old, attribute: old[attribute] + amount}
    health_delta, mana_delta = resource_delta(character["level"], old, new)
    trained = dict(character["trained_attributes"])
    trained[attribute] = trained.get(attribute, 0) + amount
    updated = database.execute(
        f"UPDATE mud_character_progression SET {attribute}=?,attribute_points=attribute_points-?,"
        "trained_attributes_json=?,maximum_health=MAX(1,maximum_health+?),"
        "current_health=MIN(MAX(1,maximum_health+?),MAX(0,current_health+?)),"
        "maximum_mana=MAX(1,maximum_mana+?),current_mana=MIN(MAX(1,maximum_mana+?),MAX(0,current_mana+?)),"
        "updated_at=CURRENT_TIMESTAMP WHERE character_id=? AND track_key=? AND attribute_points>=?",
        (new[attribute], amount, json.dumps(trained, sort_keys=True), health_delta, health_delta, health_delta,
         mana_delta, mana_delta, mana_delta, character["id"], character["progression_track"], amount),
    ).rowcount
    if not updated:
        raise ActionError("Your attribute points changed; try again.", 409)
    changes = [f"{attribute.title()} {old[attribute]} → {new[attribute]}"]
    if health_delta:
        changes.append(f"maximum health {health_delta:+d}")
    if mana_delta and character["resource"]["enabled"]:
        changes.append(f"maximum {character['resource']['noun']} {mana_delta:+d}")
    remaining = points - amount
    return [EventDraft(
        f"You train {attribute}: {', '.join(changes)}. "
        f"{remaining} attribute point{'s' if remaining != 1 else ''} remaining.",
        event_type="character",
    )]


def _resolve_skill(database, name, *, include_inactive=False):
    known = mud_skills.definitions(database, include_inactive=include_inactive)
    key = " ".join(str(name).casefold().split())
    if not key:
        raise ActionError("Name a skill, for example /skills stealth.")
    exact = [definition for definition in known.values()
             if key in {definition["key"], definition["name"].casefold()}]
    matches = exact or [definition for definition in known.values()
                        if definition["name"].casefold().startswith(key) or definition["key"].startswith(key)]
    if len(matches) != 1:
        raise ActionError(f'No single skill matches "{name}". Use /skills to list skills.')
    return matches[0]


def _skill_context(database, character):
    origin = database.execute("SELECT race_key,class_key FROM mud_characters WHERE id=?",
                              (character["id"],)).fetchone()
    return {"level": mud_skills.highest_level(database, character["id"]),
            "attributes": character["attributes"], "race_key": origin["race_key"],
            "class_key": origin["class_key"],
            "ranks": {skill["skill_key"]: skill["rank"] for skill in character["skills"] if skill["active"]}}


def _room_trainers(database, user_id, skill_key=None, trainer_name=""):
    room = _current_room(database, user_id)
    if room.get("instance_id"):
        return []
    offers = []
    for npc in _room_npc_rows(database, room["id"]):
        if trainer_name and npc["name"].casefold() != trainer_name.casefold():
            continue
        if npc["combat_maximum_health"] is not None and _encounter_row(database, npc)["status"] == "defeated":
            continue
        for row in database.execute("SELECT * FROM mud_npc_trainers WHERE npc_definition_id=? ORDER BY skill_key",
                                    (npc["definition_id"],)):
            if skill_key is None or row["skill_key"] == skill_key:
                offers.append((npc, row))
    return offers


def _split_trainer(arguments):
    text = " ".join(str(value) for value in arguments).strip()
    if re.search(r"\s+from\s+", text, re.I):
        skill, trainer = re.split(r"\s+from\s+", text, maxsplit=1, flags=re.I)
        return skill.strip(), trainer.strip()
    return text, ""


def _require_created(character, verb):
    if character["creation_state"] != "complete":
        raise ActionError(f"Create your character before {verb}.")


def _skill_line(skill):
    progress = ("max" if skill["rank"] >= skill["max_rank"] else
                f"{skill['experience']}/{skill['next_rank_at']}")
    return (f"- {skill['name']} {skill['rank']}/{skill['max_rank']} [{progress}]"
            + (f": {skill['effects']}" if skill["active"] and skill["effects"] else "")
            + ("" if skill["active"] else " (inactive)"))


def _skills_action(database, user, parameters):
    args = [str(value) for value in parameters.get("arguments", [])]
    character = character_state(database, user["id"])
    if args:
        definition = _resolve_skill(database, " ".join(args), include_inactive=True)
        known = next((skill for skill in character["skills"] if skill["skill_key"] == definition["key"]), None)
        requirements = definition["requirements"]
        needs = [f"level {requirements['level']}"] if requirements["level"] > 1 else []
        needs += [f"{name.title()} {value}" for name, value in requirements["attributes"].items()]
        needs += [f"{key} rank {value}" for key, value in requirements["skills"].items()]
        if requirements["races"] or requirements["classes"]:
            needs.append("specific races or classes")
        triggers = ", ".join(mud_skills.TRIGGERS[trigger] for trigger in definition["practice"]["triggers"])
        lines = [f"{definition['name']} ({definition['group']})", definition["description"],
                 f"Per rank: {mud_skills.describe_effects(definition)}",
                 f"Improves by: {triggers or 'training only'}",
                 f"Learn: {definition['learn']['cost']} skill point(s)"
                 + (", from a trainer" if definition["learn"]["trainer_required"] else "")
                 + (f" · requires {', '.join(needs)}" if needs else "")]
        if known:
            lines.insert(2, f"Your rank: {known['rank']}/{known['max_rank']}"
                            + (f" — now {known['effects']}" if known["active"] and known["effects"] else ""))
        return [EventDraft("\n".join(lines), event_type="character")]
    lines = [f"Skill points: {character['skill_points']} (earned at even levels and every fifth level)."]
    for group in mud_skills.GROUPS:
        members = [skill for skill in character["skills"] if skill["group"] == group]
        if members:
            lines.append(group.title() + ":")
            lines += [_skill_line(skill) for skill in members]
    if not character["skills"]:
        lines.append("You have no skills yet.")
    lines += _learnable_lines(database, user, character)
    lines.append("Use /skills <name> for details, /learn <skill>, or /practice <skill> to spend a point.")
    return [EventDraft("\n".join(lines), event_type="character")]


def _learnable_lines(database, user, character):
    if character["creation_state"] != "complete":
        return []
    context = _skill_context(database, character)
    known = {skill["skill_key"] for skill in character["skills"]}
    trainers = {}
    for npc, offer in _room_trainers(database, user["id"]):
        trainers.setdefault(offer["skill_key"], []).append(npc["name"])
    lines = []
    for definition in mud_skills.definitions(database).values():
        if definition["key"] in known:
            continue
        problems = mud_skills.requirement_problems(definition, **context)
        notes = [f"{definition['learn']['cost']} pt"]
        if definition["learn"]["trainer_required"]:
            notes.append("trainer only")
        if definition["key"] in trainers:
            notes.append("taught by " + ", ".join(trainers[definition["key"]]))
        if problems:
            notes.append("needs " + ", ".join(problems))
        lines.append(f"- {definition['name']} ({'; '.join(notes)})")
    return (["Learnable:"] + lines) if lines else []


def _learn_action(database, user, parameters):
    character = character_state(database, user["id"])
    _require_created(character, "learning skills")
    name, trainer_name = _split_trainer(parameters.get("arguments", []))
    if not name:
        lines = _learnable_lines(database, user, character) or ["There are no new skills to learn."]
        return [EventDraft("\n".join([f"Skill points: {character['skill_points']}.", *lines]),
                           event_type="character")]
    definition = _resolve_skill(database, name)
    offers = _room_trainers(database, user["id"], definition["key"], trainer_name)
    if trainer_name and not offers:
        raise ActionError(f"{trainer_name} does not teach {definition['name']} here.")
    if not trainer_name and len(offers) > 1:
        raise ActionError("Several trainers teach that here. Add 'from <trainer>'.")
    use_trainer = bool(trainer_name) or (definition["learn"]["trainer_required"] and len(offers) == 1)
    try:
        _definition, cost = mud_skills.learn(database, character["id"], definition["key"],
                                             context=_skill_context(database, character), trainer=use_trainer)
    except ValueError as problem:
        raise ActionError(str(problem)) from problem
    paid = ""
    if use_trainer:
        npc, offer = offers[0]
        _charge(database, character["id"], offer["price"])
        paid = f" {npc['name']} teaches you" + (f" for {offer['price']} coins" if offer["price"] else "") + "."
    spent = f" ({cost} skill point{'s' if cost != 1 else ''})" if cost else ""
    return [EventDraft(f"You learn {definition['name']} at rank 1{spent}.{paid} "
                       f"{mud_skills.describe_effects(definition, 1)}".rstrip(), event_type="character")]


def _charge(database, character_id, price):
    if not price:
        return
    _wallet(database, character_id)
    if not database.execute("UPDATE mud_character_wallets SET coins=coins-? WHERE character_id=? AND coins>=?",
                            (price, character_id, price)).rowcount:
        raise ActionError(f"That lesson costs {price} coins.")


def _practice_skill_action(database, user, parameters):
    character = character_state(database, user["id"])
    _require_created(character, "training skills")
    name, trainer_name = _split_trainer(parameters.get("arguments", []))
    if not name:
        raise ActionError("Use /practice <skill> to spend a skill point, or /practice <skill> from <trainer>.")
    definition = _resolve_skill(database, name)
    offer = None
    if trainer_name:
        offers = _room_trainers(database, user["id"], definition["key"], trainer_name)
        if not offers:
            raise ActionError(f"{trainer_name} does not teach {definition['name']} here.")
        offer = offers[0]
    try:
        _definition, rank = mud_skills.raise_rank(
            database, character["id"], definition["key"], use_points=offer is None,
            limit=offer[1]["max_rank"] if offer else None)
    except ValueError as problem:
        raise ActionError(str(problem)) from problem
    if offer:
        # Lessons grow more expensive with each rank they teach.
        price = offer[1]["price"] * (rank - 1)
        _charge(database, character["id"], price)
        source = f"{offer[0]['name']} trains you" + (f" for {price} coins" if price else "")
    else:
        source = "You spend a skill point"
    return [EventDraft(f"{source}. {definition['name']} rises to rank {rank}: "
                       f"{mud_skills.describe_effects(definition, rank)}.", event_type="character")]


def _help_action(_database, _user, parameters):
    resource = resource_settings(_database, _user["id"])
    args = parameters.get("arguments", [])
    if args:
        name = str(args[0]).lstrip("/").casefold()
        entry = actions._commands.get(name)
        if entry is None:
            raise ActionError(f'No help for "{name}".')
        definition, prefix = entry
        if definition.key in RESOURCE_ACTIONS and not resource["enabled"]:
            raise ActionError(f'No help for "{name}".')
        aliases = [alias for alias, (action, _prefix) in actions._commands.items()
                   if action.key == definition.key and not _prefix]
        examples = {
            "get": '/get "Red Apple" from "Oak Chest"', "put": '/put "Red Apple" into "Oak Chest"',
            "open": '/open "Oak Chest"', "close": '/close "Oak Chest"',
            "look": '/look "Oak Chest"', "tell": '/tell "Player Name" hello',
            "reply_player": '/reply hello', "respond": '/respond 1',
            "description": '/description A traveler in a blue coat',
            "train": '/train strength 2', "skills": '/skills stealth',
            "learn": '/learn lockpicking from "Old Thief"', "practice_skill": '/practice blades',
            "pick": '/pick north', "interact": '/pull "Rusty Lever"',
        }
        body = f"/{name}: {_action_description(definition, resource)}\nAliases: " + ", ".join(f"/{alias}" for alias in aliases)
        if definition.key in examples:
            body += f"\nExample: {examples[definition.key]}"
        if definition.key in {"look", "get", "put", "open", "close"}:
            body += "\nQuote multiword targets. For duplicates use a number: Apple 2 or Apple (2)."
        return [EventDraft(body)]
    lines = ["Available commands:"]
    for definition in actions._actions.values():
        if (definition.commands and _authorized(definition, _user)
                and (resource["enabled"] or definition.key not in RESOURCE_ACTIONS)):
            lines.append(f"/{definition.commands[0]} — {_action_description(definition, resource)}")
    lines.append('Use /help <command> for details. Quote multiword names; use "Name 2" or "Name (2)" for duplicates.')
    lines.append('Use /get <item> from <container>, /put <item> in|into <container>, and /open or /close <container>.')
    lines.append('Direction shortcuts include /n, /s, /e, /w, /u, and /d. Plain text speaks to the room.')
    return [EventDraft("\n".join(lines))]


START_ROOM_ID = "stationnet:start"
START_ROOM_NAME = "Starting Room"
START_ROOM_DESCRIPTION = (
    "A quiet, unadorned room waiting for this station's world to take shape."
)
PORTAL_ROOM_ID = "stationnet:portal"
PORTAL_ROOM_NAME = "Dungeon Portals"
PORTAL_ROOM_DESCRIPTION = (
    "A threshold of dormant gateways hums with unrealized possibility."
)
PORTAL_ROOM_INSTRUCTIONS = (
    "This protected hub connects StationNet's procedural adventures. "
    "Enter the station-wide Dungeon of the Day, launch a Random Expedition, "
    "or join a companion using their run code."
)


def _mode_settings(database):
    row = database.execute(
        "SELECT mud_enabled,mud_traditional_enabled,mud_procedural_enabled "
        "FROM station WHERE id=1"
    ).fetchone()
    if row is None:
        # Domain-level tests and pre-setup maintenance can still operate on the
        # traditional content tables; HTTP access remains guarded by station setup.
        return {"traditional": True, "procedural": False, "enabled": True}
    traditional = bool(row["mud_traditional_enabled"])
    procedural = bool(row["mud_procedural_enabled"])
    # Old integrations and tests may still set only the original master switch.
    if row["mud_enabled"] and not traditional and not procedural:
        traditional = True
    return {
        "traditional": traditional,
        "procedural": procedural,
        "enabled": traditional or procedural,
    }


def _portal_room(database):
    row = database.execute(
        "SELECT name,description FROM mud_portal_settings WHERE id=1"
    ).fetchone()
    return {
        "id": PORTAL_ROOM_ID,
        "name": row["name"] if row else PORTAL_ROOM_NAME,
        "description": row["description"] if row else PORTAL_ROOM_DESCRIPTION,
        "instructions": PORTAL_ROOM_INSTRUCTIONS,
        "location_type": "portal",
        "mode": "procedural",
        "system_managed": True,
    }


def _start_room(database):
    room = database.execute(
        "SELECT id,name,description FROM mud_world_rooms WHERE is_start=1"
    ).fetchone()
    if room is not None:
        return room
    existing = database.execute(
        "SELECT id FROM mud_world_rooms WHERE id=?", (START_ROOM_ID,)
    ).fetchone()
    if existing is None:
        database.execute(
            "INSERT INTO mud_world_rooms(id,name,description,is_start) VALUES(?,?,?,1)",
            (START_ROOM_ID, START_ROOM_NAME, START_ROOM_DESCRIPTION),
        )
    else:
        database.execute(
            "UPDATE mud_world_rooms SET is_start=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (START_ROOM_ID,),
        )
    return database.execute(
        "SELECT id,name,description FROM mud_world_rooms WHERE id=?", (START_ROOM_ID,)
    ).fetchone()


def _world_room(database, room_id):
    """A world room as players see it; wilderness rooms take their text from their preset."""
    row = mud_wilderness.room_row(database, room_id)
    return mud_wilderness.resolve_room(database, row) if row is not None else None


def _set_player_location(database, user_id, mode, location_type, location_key,
                         instance_id=None):
    """Move shared identity state without making system locations world rooms."""
    if location_type == "portal":
        database.execute("DELETE FROM mud_player_stealth WHERE user_id=?", (user_id,))
    room_id = location_key if location_type == "world_room" else None
    if location_type == "world_room":
        database.execute(
            "INSERT INTO mud_world_locations(user_id,room_id) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET room_id=excluded.room_id,"
            "updated_at=CURRENT_TIMESTAMP", (user_id, room_id),
        )
    database.execute(
        "INSERT INTO mud_player_locations(user_id,mode,location_type,location_key,instance_id) "
        "VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode,"
        "location_type=excluded.location_type,location_key=excluded.location_key,"
        "instance_id=excluded.instance_id,updated_at=CURRENT_TIMESTAMP",
        (user_id, mode, location_type, location_key, instance_id),
    )
    database.execute(
        "UPDATE mud_presence SET room_id=?,location_type=?,location_key=?,instance_id=? "
        "WHERE user_id=?",
        (room_id, location_type, location_key, instance_id, user_id),
    )


def _reconcile_player_location(database, user_id):
    modes = _mode_settings(database)
    if not modes["enabled"]:
        return None
    location = database.execute(
        "SELECT mode,location_type,location_key,instance_id "
        "FROM mud_player_locations WHERE user_id=?", (user_id,),
    ).fetchone()
    summary = database.execute(
        "SELECT s.instance_id,i.world_id FROM mud_death_summaries AS s "
        "LEFT JOIN mud_procedural_instances AS i ON i.id=s.instance_id WHERE s.user_id=?",
        (user_id,),
    ).fetchone()
    if location is not None and summary is not None and (
            modes["traditional"] if summary["world_id"] else modes["procedural"]):
        return location
    valid = False
    if location is not None:
        if location["mode"] == "traditional" and modes["traditional"]:
            if location["location_type"] == "dungeon_instance":
                # A world dungeon run stays valid only while its world is the active one.
                valid = database.execute(
                    "SELECT 1 FROM mud_procedural_instances AS i "
                    "JOIN mud_expedition_participants AS p ON p.instance_id=i.id "
                    "WHERE i.id=? AND i.status='active' AND p.user_id=? AND p.status='active' "
                    "AND i.world_id=(SELECT id FROM mud_world_packages WHERE active=1)",
                    (location["instance_id"], user_id),
                ).fetchone() is not None
            else:
                valid = (location["location_type"] == "world_room" and database.execute(
                    "SELECT 1 FROM mud_world_rooms WHERE id=? "
                    "AND world_id=(SELECT id FROM mud_world_packages WHERE active=1)",
                    (location["location_key"],),
                ).fetchone() is not None)
        elif location["mode"] == "procedural" and modes["procedural"]:
            valid = location["location_type"] in {"portal", "dungeon_instance"}
            if location["location_type"] == "dungeon_instance":
                valid = database.execute(
                    "SELECT 1 FROM mud_procedural_instances AS i "
                    "JOIN mud_expedition_participants AS p ON p.instance_id=i.id "
                    "WHERE i.id=? AND i.status='active' AND p.user_id=? AND p.status='active'",
                    (location["instance_id"], user_id),
                ).fetchone() is not None
    if valid:
        return location
    if location is not None and location["location_type"] == "world_room":
        _leave_combat(database, user_id, "room", location["location_key"])
    if modes["traditional"]:
        saved = database.execute(
            "SELECT room_id FROM mud_world_locations WHERE user_id=?", (user_id,),
        ).fetchone()
        room_id = saved["room_id"] if saved else None
        if room_id is None or database.execute(
            "SELECT 1 FROM mud_world_rooms WHERE id=? "
            "AND world_id=(SELECT id FROM mud_world_packages WHERE active=1)",
            (room_id,),
        ).fetchone() is None:
            room_id = _start_room(database)["id"]
        _set_player_location(database, user_id, "traditional", "world_room", room_id)
    else:
        _set_player_location(database, user_id, "procedural", "portal", PORTAL_ROOM_ID)
    return database.execute(
        "SELECT mode,location_type,location_key,instance_id "
        "FROM mud_player_locations WHERE user_id=?", (user_id,),
    ).fetchone()


def reconcile_mode_locations(database):
    """Move active and persisted players out of newly inaccessible modes."""
    for row in database.execute("SELECT id FROM users").fetchall():
        _reconcile_player_location(database, row["id"])
    if not _mode_settings(database)["enabled"]:
        database.execute("DELETE FROM mud_presence")


def _active_expedition(database, user_id):
    return database.execute(
        "SELECT i.*,p.status AS participant_status,p.current_room_key,p.rooms_visited,"
        "p.joined_at,p.finished_at,p.reward_experience,p.rewarded_at "
        "FROM mud_expedition_participants AS p "
        "JOIN mud_procedural_instances AS i ON i.id=p.instance_id "
        "WHERE p.user_id=? AND p.status='active' AND i.status='active' "
        "ORDER BY p.joined_at DESC LIMIT 1", (user_id,),
    ).fetchone()


def _dungeon_room(database, instance_id, room_key):
    row = database.execute(
        "SELECT room_key AS id,name,description,role,depth,difficulty,instance_id,room_template_id "
        "FROM mud_dungeon_rooms WHERE instance_id=? AND room_key=?",
        (instance_id, room_key),
    ).fetchone()
    if row is None:
        return None
    room = dict(row)
    room.update(location_type="dungeon_instance", mode=_run_mode(database, instance_id),
                system_managed=True)
    return room


def _world_run(database, instance_id):
    """The world entrance metadata of a run, or None for a Dungeon Portals run."""
    row = database.execute(
        "SELECT i.world_id,i.world_entrance_id,i.return_room_id,i.party_limit,"
        "e.name AS entrance_name,e.room_id AS entrance_room_id "
        "FROM mud_procedural_instances AS i LEFT JOIN mud_world_dungeon_entrances AS e "
        "ON e.id=i.world_entrance_id WHERE i.id=? AND i.world_entrance_id IS NOT NULL",
        (instance_id,),
    ).fetchone() if instance_id else None
    return row


def _run_mode(database, instance_id):
    """World dungeon runs keep the world's progression track and possessions."""
    return "traditional" if _world_run(database, instance_id) else "procedural"


def _return_destination(database, instance_id):
    """Where a run's explorers go when they leave it: its return room, or the portals."""
    run = _world_run(database, instance_id)
    if run is None:
        return "procedural", "portal", PORTAL_ROOM_ID
    room = database.execute(
        "SELECT id FROM mud_world_rooms WHERE id=? AND world_id=(SELECT id FROM mud_world_packages WHERE active=1)",
        (run["return_room_id"],),
    ).fetchone()
    return "traditional", "world_room", room["id"] if room else _start_room(database)["id"]


def _return_label(database, instance_id):
    _mode, location_type, key = _return_destination(database, instance_id)
    if location_type == "portal":
        return "the Dungeon Portals"
    return _world_room(database, key)["name"]


def _leave_run_location(database, user_id, instance_id):
    _set_player_location(database, user_id, *_return_destination(database, instance_id))


def _current_room(database, user_id):
    location = _reconcile_player_location(database, user_id)
    if location is None:
        raise ActionError("The MUD is currently disabled.", 404)
    if database.execute("SELECT 1 FROM mud_death_summaries WHERE user_id=?",
                        (user_id,)).fetchone() is not None:
        return {"id": "stationnet:death-summary", "name": "Expedition Ended",
                "description": "Your run has ended. Review the results and enter /continue.",
                "mode": "procedural", "system_managed": True}
    if location["location_type"] == "portal":
        return _portal_room(database)
    if location["location_type"] == "dungeon_instance":
        room = _dungeon_room(database, location["instance_id"], location["location_key"])
        participant = database.execute(
            "SELECT 1 FROM mud_expedition_participants WHERE instance_id=? AND user_id=? "
            "AND status='active'", (location["instance_id"], user_id),
        ).fetchone()
        if room is not None and participant is not None:
            return room
        _leave_run_location(database, user_id, location["instance_id"])
        return _current_room(database, user_id)
    room = _world_room(database, location["location_key"])
    if room is not None:
        return room
    fallback = _start_room(database)
    _set_player_location(
        database, user_id, "traditional", "world_room", fallback["id"]
    )
    return dict(fallback)


def _room_exits(database, room_id, instance_id=None, viewer_id=None):
    """Exits a viewer can see; triggers may hide, reveal, open, or seal world exits.

    script_state is the viewer's own trigger state, else the world's, else
    'hidden' for exits authored hidden, else ''.
    """
    if instance_id:
        return [dict(row) for row in database.execute(
            "SELECT (e.instance_id || ':exit:' || from_room_key || ':' || direction) AS id,"
            "e.name,e.direction,e.to_room_key AS to_room_id,r.name AS destination_name "
            "FROM mud_dungeon_exits AS e JOIN mud_dungeon_rooms AS r "
            "ON r.instance_id=e.instance_id AND r.room_key=e.to_room_key "
            "WHERE e.instance_id=? AND e.from_room_key=? ORDER BY e.direction,e.to_room_key",
            (instance_id, room_id),
        ).fetchall()]
    rows = database.execute(
        "SELECT e.id,e.name,e.direction,e.to_room_id,e.key_item_id,e.pick_difficulty,"
        "e.conditions_json,e.conditions_message,r.name AS destination_name,"
        "COALESCE((SELECT s.state FROM mud_script_entity_states AS s WHERE s.entity_type='exit' "
        "AND s.entity_id=e.id AND s.scope_key=(SELECT id FROM mud_characters WHERE user_id=?)),"
        "(SELECT s.state FROM mud_script_entity_states AS s WHERE s.entity_type='exit' "
        "AND s.entity_id=e.id AND s.scope_key=''),"
        "CASE WHEN e.hidden=1 THEN 'hidden' ELSE '' END) AS script_state "
        "FROM mud_world_exits AS e JOIN mud_world_rooms AS r ON r.id=e.to_room_id "
        "WHERE e.from_room_id=? ORDER BY "
        "CASE WHEN e.direction IS NULL THEN 1 ELSE 0 END,e.name COLLATE NOCASE,e.id",
        (viewer_id, room_id),
    ).fetchall()
    exits = []
    for row in rows:
        if row["script_state"] == "hidden":
            continue
        if not row["destination_name"]:
            row = {**dict(row), "destination_name": _world_room(database, row["to_room_id"])["name"]}
        exits.append(row)
    # Stored exits win; hidden or sealed ones still claim their direction.
    return exits + mud_wilderness.grid_exits(database, room_id, {row["direction"] for row in rows})


def _room_entrances(database, room_id):
    """Enabled world dungeon entrances in a room of the active world."""
    return database.execute(
        "SELECT e.*,t.name AS template_name FROM mud_world_dungeon_entrances AS e "
        "JOIN mud_world_dungeon_templates AS t ON t.id=e.template_id "
        "WHERE e.room_id=? AND e.enabled=1 "
        "AND e.world_id=(SELECT id FROM mud_world_packages WHERE active=1) "
        "ORDER BY e.name COLLATE NOCASE,e.id", (room_id,),
    ).fetchall()


def _timestamp_before(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).replace(
        microsecond=0
    ).isoformat()


def _occupant_ids(database, room_id, exclude_user_id=None, instance_id=None):
    location_type = ("dungeon_instance" if instance_id else
                     "portal" if room_id == PORTAL_ROOM_ID else "world_room")
    parameters = [location_type, room_id]
    instance_filter = " AND instance_id=?" if instance_id else ""
    if instance_id:
        parameters.append(instance_id)
    exclusion = ""
    if exclude_user_id is not None:
        exclusion = " AND user_id<>?"
        parameters.append(exclude_user_id)
    rows = database.execute(
        "SELECT DISTINCT user_id FROM mud_presence "
        "WHERE location_type=? AND location_key=?" + instance_filter + exclusion,
        parameters,
    ).fetchall()
    return tuple(row["user_id"] for row in rows)


def _room_occupants(database, room_id, viewer_id, instance_id=None):
    location_type = ("dungeon_instance" if instance_id else
                     "portal" if room_id == PORTAL_ROOM_ID else "world_room")
    rows = [dict(row) for row in database.execute(
        "SELECT DISTINCT u.id,c.id AS character_id,"
        "COALESCE(c.display_name,u.callsign) AS character_name FROM mud_presence AS p "
        "JOIN users AS u ON u.id=p.user_id "
        "LEFT JOIN mud_characters AS c ON c.user_id=u.id "
        "WHERE p.location_type=? AND p.location_key=? AND p.user_id<>? "
        "AND (? IS NULL OR p.instance_id=?) "
        "ORDER BY character_name COLLATE NOCASE,u.id",
        (location_type, room_id, viewer_id, instance_id, instance_id),
    ).fetchall()]
    labels = list(_numbered_items([{"id": row["id"], "name": row["character_name"]} for row in rows]))
    for row, (_, label) in zip(rows, labels):
        row["display_name"] = label
    return rows


def _exit_label(exit_row):
    route = exit_row["direction"] or exit_row["name"]
    return f"{route.upper()} — {exit_row['destination_name']}"


def _exit_sealed(exit_row):
    """A trigger locked this exit; no key, pick, or earlier unlock opens it."""
    return "script_state" in exit_row.keys() and exit_row["script_state"] == "locked"


def _exit_locked(exit_row):
    """Traditional exits lock behind a key, a pickable lock, or both; triggers may open or seal them."""
    keys = exit_row.keys()
    if "script_state" in keys and exit_row["script_state"] in ("open", "locked"):
        return exit_row["script_state"] == "locked"
    return bool(("key_item_id" in keys and exit_row["key_item_id"]) or
                ("pick_difficulty" in keys and exit_row["pick_difficulty"]))


def _exit_open_to(database, user_id, exit_row):
    """World exits may require conditions, such as a race, class, or origin tag."""
    conditions = exit_row["conditions_json"] if "conditions_json" in exit_row.keys() else None
    if conditions in (None, "", "[]"):
        return True
    return mud_conditions.evaluate(conditions, _ConditionContext(database, user_id))


def _exit_barred_message(exit_row):
    return exit_row["conditions_message"] or "Something keeps you from going that way."


def _player_exit_label(database, user_id, exit_row):
    label = _exit_label(exit_row)
    if not _exit_open_to(database, user_id, exit_row):
        return label + " [barred]"
    if not _exit_locked(exit_row):
        return label
    if _exit_sealed(exit_row):
        return label + " [sealed]"
    character = _character_row(database, user_id)
    unlocked = database.execute("SELECT 1 FROM mud_exit_unlocks WHERE character_id=? AND exit_id=?",
                                (character["id"], exit_row["id"])).fetchone()
    return label + (" [unlocked]" if unlocked else " [locked]")


def _room_npc_rows(database, room_id, instance_id=None):
    if instance_id:
        rows = []
        for content in database.execute(
            "SELECT c.*,COALESCE(s.status,'available') AS content_status "
            "FROM mud_dungeon_content AS c LEFT JOIN mud_dungeon_content_state AS s "
            "ON s.content_id=c.id WHERE c.instance_id=? AND c.room_key=? "
            "AND c.content_type IN ('npc','enemy') ORDER BY c.id", (instance_id, room_id),
        ).fetchall():
            snapshot = _json_properties(content["snapshot_json"])
            rows.append({
                "id": content["id"], "definition_id": content["definition_id"],
                "room_id": room_id, "instance_id": instance_id,
                "dungeon_content_id": content["id"], "state_json": "{}",
                "name": snapshot.get("name", content["definition_id"] or "Unknown NPC"),
                "description": snapshot.get("description", ""),
                "conversation_id": snapshot.get("conversation_id"),
                "properties_json": snapshot.get("properties_json", "{}"),
                "behaviors_json": snapshot.get("behaviors_json", "{}"),
                "combat_maximum_health": snapshot.get("maximum_health"),
                "combat_attack": snapshot.get("attack"),
                "combat_defense": snapshot.get("defense"),
                "combat_accuracy": snapshot.get("accuracy"),
                "combat_evasion": snapshot.get("evasion"),
                "experience_reward": snapshot.get("experience_reward", 0),
                "respawn_seconds": snapshot.get("respawn_seconds", 86400),
            })
        return rows
    return _script_visible(database, "npc", database.execute(
        "SELECT i.id,i.definition_id,i.room_id,i.state_json,i.spawned_by_trigger,d.name,d.description,"
        "d.conversation_id,d.properties_json,d.behaviors_json,"
        "p.maximum_health AS combat_maximum_health,p.attack AS combat_attack,"
        "p.defense AS combat_defense,p.accuracy AS combat_accuracy,p.evasion AS combat_evasion,"
        "p.experience_reward,p.respawn_seconds "
        "FROM mud_npc_instances AS i JOIN mud_npc_definitions AS d "
        "ON d.id=i.definition_id LEFT JOIN mud_npc_combat_profiles AS p "
        "ON p.npc_definition_id=d.id WHERE i.room_id=? "
        "ORDER BY d.name COLLATE NOCASE,i.id", (room_id,),
    ).fetchall())


def _npc_match(rows, target):
    key = " ".join(str(target).casefold().split())
    rows = list(rows)
    direct = [row for row in rows if row["id"] == target]
    if direct:
        return direct[0]
    matches = [row for row in rows if " ".join(row["name"].casefold().split()) == key]
    if not matches:
        numbered = re.fullmatch(r"(.+?)\s+(?:\((\d+)\)|(\d+))", key)
        if numbered:
            name, number = numbered.group(1), int(numbered.group(2) or numbered.group(3))
            matches = [row for row in rows if " ".join(row["name"].casefold().split()) == name]
            if 1 <= number <= len(matches):
                return matches[number - 1]
            raise ActionError(f'No NPC named "{name}" has number {number}.')
        matches = [row for row in rows if row["definition_id"] == target]
    if not matches:
        raise ActionError(f'You do not see an NPC matching "{target}" here.')
    if len(matches) > 1:
        raise ActionError(f'More than one NPC matches "{target}". Use a number, such as "{matches[0]["name"]} 1" or "{matches[0]["name"]} 2".')
    return matches[0]


def _numbered_npcs(rows):
    rows = list(rows)
    counts = {}
    for row in rows:
        key = " ".join(row["name"].casefold().split())
        counts[key] = counts.get(key, 0) + 1
    positions = {}
    for row in rows:
        key = " ".join(row["name"].casefold().split())
        positions[key] = positions.get(key, 0) + 1
        yield row, f"{row['name']} ({positions[key]})" if counts[key] > 1 else row["name"]


def _json_properties(raw):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _effect_specs(properties, field):
    value = properties.get(field, [])
    if not isinstance(value, list):
        return []
    valid = []
    for spec in value:
        try:
            mud_effects.validate(spec)
            valid.append(spec)
        except ValueError:
            continue
    return valid


def _integer_property(properties, *names):
    for name in names:
        value = properties.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _item_requirements(properties):
    return mud_conditions.validate_item_requirements(properties.get("requirements", {}))


def _item_requirement_problems(properties, character, facts):
    """Unmet requirements for a character row; invalid requirements are never met."""
    try:
        requirements = _item_requirements(properties)
    except ValueError:
        return ["valid requirements"]
    return mud_conditions.requirement_problems(requirements, character, facts)


def _meets_item_requirements(properties, character, facts):
    return not _item_requirement_problems(properties, character, facts)


def _equipment_combat_totals(database, character_id=None, npc_definition_id=None):
    if character_id is not None:
        owner = database.execute(
            "SELECT user_id FROM mud_characters WHERE id=?", (character_id,)
        ).fetchone()
        loadout = _inventory_context(database, owner["user_id"])["loadout"]
        character = _character_row(database, owner["user_id"])
        facts = character_creation.origin_facts(database, character_id)
        rows = database.execute(
            "SELECT d.name,d.properties_json FROM mud_character_equipment AS e "
            "JOIN mud_item_instances AS i ON i.id=e.item_instance_id "
            "JOIN mud_item_definitions AS d ON d.id=i.definition_id "
            "WHERE e.character_id=? AND e.loadout_key=? AND i.owner_character_id=?",
            (character_id, loadout, character_id),
        ).fetchall()
    else:
        rows = database.execute(
            "SELECT d.name,d.properties_json FROM mud_npc_equipment AS e "
            "JOIN mud_item_definitions AS d ON d.id=e.item_definition_id "
            "WHERE e.npc_definition_id=?", (npc_definition_id,),
        ).fetchall()
    attack = defense = accuracy = firearm_accuracy = evasion = 0
    categories, unproficient = set(), []
    known = _proficiencies(database, character_id) if character_id is not None else set()
    limit = mud_combat.ITEM_EVASION_LIMIT
    for row in rows:
        properties = _json_properties(row["properties_json"])
        if character_id is not None and not _meets_item_requirements(properties, character, facts):
            continue
        item_attack = max(0, _integer_property(properties, "damage", "attack", "attack_bonus"))
        item_defense = max(0, _integer_property(properties, "armor", "defense", "defense_bonus"))
        item_accuracy = max(-limit, min(limit, _integer_property(properties, "accuracy")))
        category = _item_category(properties)
        if character_id is not None and category:
            categories.add(category)
            if category not in known:
                unproficient.append(row["name"])
                item_attack, item_defense = _untrained(item_attack), _untrained(item_defense)
        # Heavy gear may carry a negative evasion; a firearm's accuracy only helps its own shots.
        evasion += max(-limit, min(limit, _integer_property(properties, "evasion")))
        if character_id is None or not mud_firearms.is_firearm(properties):
            attack += item_attack
            accuracy += item_accuracy
        else:
            firearm_accuracy += item_accuracy
        defense += item_defense
    return {"attack": attack, "defense": defense, "accuracy": accuracy,
            "firearm_accuracy": firearm_accuracy, "evasion": evasion,
            "categories": categories, "unproficient": unproficient}


def _item_category(properties):
    category = properties.get("category")
    return category if isinstance(category, str) and character_creation.KEY_RE.fullmatch(category) else None


def _untrained(value):
    """Categorized gear without a matching proficiency gives up a quarter of its bonus."""
    return value - value // 4


def _equipment_stat_bonus(gear, attribute, kind):
    """Training with a stat lets a character draw more from equipped gear."""
    return min(gear[kind], max(0, (attribute - 10) // 3))


def _encounter_row(database, npc, create=True):
    """Return one authoritative live/defeated encounter for a world NPC.

    Scope columns deliberately avoid assuming that future encounters are backed by
    handcrafted rooms. Chunk 9+ can create instance-scoped rows directly.
    """
    if npc["combat_maximum_health"] is None:
        return None
    content_id = npc.get("dungeon_content_id") if isinstance(npc, dict) else None
    identity_column = "dungeon_content_id" if content_id else "npc_instance_id"
    identity = content_id or npc["id"]
    live = database.execute(
        f"SELECT * FROM mud_combat_encounters WHERE {identity_column}=? "
        "AND status IN ('ready','active') ORDER BY updated_at DESC,id DESC LIMIT 1",
        (identity,),
    ).fetchone()
    if live is not None:
        return live
    latest = database.execute(
        f"SELECT * FROM mud_combat_encounters WHERE {identity_column}=? "
        "ORDER BY updated_at DESC,id DESC LIMIT 1", (identity,),
    ).fetchone()
    spawned = not content_id and "spawned_by_trigger" in npc.keys() and npc["spawned_by_trigger"]
    if (content_id or spawned) and latest is not None and latest["status"] == "defeated":
        return latest
    if latest is not None and latest["status"] == "defeated":
        ready = database.execute(
            "SELECT datetime(?, '+' || ? || ' seconds')<=CURRENT_TIMESTAMP AS ready",
            (latest["ended_at"], npc["respawn_seconds"]),
        ).fetchone()["ready"]
        if not ready:
            return latest
    if not create:
        return latest
    encounter_id = f"combat:encounter:{uuid.uuid4().hex}"
    if content_id:
        database.execute(
            "INSERT INTO mud_combat_encounters(id,scope_type,scope_key,npc_instance_id,"
            "npc_definition_id,current_health,maximum_health,dungeon_content_id) "
            "VALUES(?,'instance',?,NULL,?,?,?,?)",
            (encounter_id, npc["instance_id"], npc["definition_id"],
             npc["combat_maximum_health"], npc["combat_maximum_health"], content_id),
        )
    else:
        database.execute(
            "INSERT INTO mud_combat_encounters(id,scope_type,scope_key,npc_instance_id,"
            "npc_definition_id,current_health,maximum_health) VALUES(?,'room',?,?,?,?,?)",
            (encounter_id, npc["room_id"], npc["id"], npc["definition_id"],
             npc["combat_maximum_health"], npc["combat_maximum_health"]),
        )
    return database.execute(
        "SELECT * FROM mud_combat_encounters WHERE id=?", (encounter_id,),
    ).fetchone()


def combat_state(database, user_id):
    room = _current_room(database, user_id)
    enemies = []
    for npc, display_name in _numbered_npcs(_room_npc_rows(database, room["id"], room.get("instance_id"))):
        encounter = _encounter_row(database, npc)
        if encounter is None:
            continue
        enemies.append({
            "id": npc["id"], "definition_id": npc["definition_id"], "name": npc["name"],
            "display_name": display_name,
            "encounter_id": encounter["id"], "status": encounter["status"],
            "health": encounter["current_health"], "maximum_health": encounter["maximum_health"],
            "attack": npc["combat_attack"], "defense": npc["combat_defense"],
            "accuracy": npc["combat_accuracy"] or 0, "evasion": npc["combat_evasion"] or 0,
            "effects": mud_effects.list_effects(database, "encounter", encounter["id"]),
        })
    return {"scope_type": "instance" if room.get("instance_id") else "room",
            "scope_key": room.get("instance_id") or room["id"], "enemies": enemies,
            "hidden": _is_hidden(database, user_id)}


def _leave_combat(database, user_id, scope_type="room", scope_key=None, defeated=False):
    character = _character_row(database, user_id)
    parameters = [character["id"], scope_type]
    scope_filter = ""
    if scope_key is not None:
        scope_filter = " AND e.scope_key=?"
        parameters.append(scope_key)
    database.execute(
        "UPDATE mud_combat_participants SET left_at=CURRENT_TIMESTAMP," +
        ("defeated_at=CURRENT_TIMESTAMP," if defeated else "") +
        "last_action_at=CURRENT_TIMESTAMP WHERE character_id=? AND left_at IS NULL "
        "AND encounter_id IN (SELECT e.id FROM mud_combat_encounters AS e "
        "WHERE e.scope_type=? AND e.status='active'" + scope_filter + ")",
        parameters,
    )
    encounters = database.execute(
        "SELECT e.id FROM mud_combat_encounters AS e WHERE e.scope_type=? "
        "AND e.status='active'" + (" AND e.scope_key=?" if scope_key is not None else ""),
        (scope_type, scope_key) if scope_key is not None else (scope_type,),
    ).fetchall()
    for encounter in encounters:
        if database.execute(
            "SELECT 1 FROM mud_combat_participants WHERE encounter_id=? "
            "AND left_at IS NULL LIMIT 1", (encounter["id"],),
        ).fetchone() is None:
            database.execute(
                "UPDATE mud_combat_encounters SET status='abandoned',ended_at=CURRENT_TIMESTAMP,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'", (encounter["id"],),
            )


def _room_state(database, user_id):
    death = database.execute(
        "SELECT summary,instance_id FROM mud_death_summaries WHERE user_id=?", (user_id,)
    ).fetchone()
    if death is not None:
        return {"id": "stationnet:death-summary", "name": "Expedition Ended",
                "description": death["summary"],
                "instructions": f"Enter /continue to return to {_return_label(database, death['instance_id'])}.",
                "system_managed": True, "mode": "procedural", "exits": [],
                "occupants": [], "items": [], "npcs": []}
    room = _current_room(database, user_id)
    portal = room["id"] == PORTAL_ROOM_ID
    instance_id = room.get("instance_id")
    dialogue_responses = []
    character = _character_row(database, user_id)
    if instance_id:
        session_row = database.execute(
            "SELECT s.node_id,s.content_id AS npc_id FROM mud_dungeon_conversation_sessions s "
            "JOIN mud_dungeon_content c ON c.id=s.content_id "
            "WHERE s.character_id=? AND s.instance_id=? AND c.room_key=?",
            (character["id"], instance_id, room["id"]),
        ).fetchone()
    else:
        session_row = database.execute(
            "SELECT s.node_id,s.npc_instance_id AS npc_id FROM mud_conversation_sessions s "
            "JOIN mud_npc_instances i ON i.id=s.npc_instance_id "
            "WHERE s.character_id=? AND i.room_id=?",
            (character["id"], room["id"]),
        ).fetchone()
    if session_row:
        _, responses = _available_dialogue_responses(
            database, character["id"], session_row["npc_id"], session_row["node_id"], bool(instance_id),
        )
        dialogue_responses = [{"number": index, "text": response["response_text"]}
                              for index, response in enumerate(responses, 1)]
    npcs = []
    for npc, display_name in _numbered_npcs(_room_npc_rows(database, room["id"], instance_id)):
        entry = {"id": npc["id"], "definition_id": npc["definition_id"],
                 "name": npc["name"], "display_name": display_name,
                 "description": npc["description"],
                 "hostile": _npc_stance(database, npc, user_id) == "Hostile"}
        if npc["combat_maximum_health"] is not None:
            encounter = _encounter_row(database, npc)
            entry.update(status=encounter["status"], health=encounter["current_health"],
                         maximum_health=encounter["maximum_health"])
        npcs.append(entry)
    exits = [
        {
            "id": exit_row["id"],
            "name": exit_row["name"],
            "direction": exit_row["direction"],
            "destination_id": exit_row["to_room_id"],
            "destination_name": exit_row["destination_name"],
        }
        for exit_row in _room_exits(database, room["id"], instance_id, user_id)
    ]
    modes = _mode_settings(database)
    if portal and modes["traditional"]:
        exits.append({"id": "system:return-world", "name": "Return to the traditional world",
                      "direction": "out", "destination_id": None,
                      "destination_name": "Traditional World"})
    elif not portal and modes["procedural"] and room["id"] == _start_room(database)["id"]:
        exits.append({"id": "system:enter-portals", "name": "Enter the Dungeon Portals",
                      "direction": "portals", "destination_id": PORTAL_ROOM_ID,
                      "destination_name": _portal_room(database)["name"]})
    if not portal and not instance_id:
        exits += [{"id": entrance["id"], "name": f"enter {entrance['name']}", "direction": None,
                   "destination_id": None, "destination_name": entrance["name"], "dungeon": True}
                  for entrance in _room_entrances(database, room["id"])]
    return {
        "id": room["id"],
        "name": room["name"],
        "description": room["description"],
        "instructions": (room["instructions"] if portal else
                         _expedition_status_text(database, user_id) if instance_id else ""),
        "system_managed": portal,
        "mode": "procedural" if portal else room.get("mode", "traditional"),
        "exits": exits,
        "occupants": _room_occupants(database, room["id"], user_id, instance_id),
        "items": [{"id": item["id"], "name": item["name"], "display_name": label,
                   "item_type": item["item_type"], "is_open": bool(item["is_open"]),
                   "contents": [{"name": child["name"], "display_name": child_label}
                                for child, child_label in _numbered_items(_container_rows(database, item["id"], user_id))]
                   if item["item_type"] == "container" and item["is_open"] and not instance_id else []}
                  for item, label in _numbered_items(
                       _room_item_rows(database, room["id"], instance_id))],
        "npcs": npcs,
        "dialogue_responses": dialogue_responses,
    }


def _describe_room(database, room, viewer_id=None):
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    exits = _room_exits(database, room["id"], instance_id, viewer_id)
    routes = ", ".join(_player_exit_label(database, viewer_id, item) if viewer_id else _exit_label(item)
                       for item in exits) if exits else "none"
    occupants = _room_occupants(database, room["id"], viewer_id, instance_id) if viewer_id else []
    people = ", ".join(item["display_name"] for item in occupants) if occupants else "none"
    items = ", ".join(label for _, label in _numbered_items(
        _room_item_rows(database, room["id"], instance_id))) or "none"
    npcs = ", ".join(label for _, label in _numbered_npcs(
        _room_npc_rows(database, room["id"], instance_id))) or "none"
    instructions = (
        f"\n\n{room['instructions']}"
        if room["id"] == PORTAL_ROOM_ID else ""
    )
    modes = _mode_settings(database)
    if room["id"] == PORTAL_ROOM_ID and modes["traditional"]:
        routes = "OUT — Traditional World"
    elif (room["id"] != PORTAL_ROOM_ID and modes["procedural"]
          and room["id"] == _start_room(database)["id"]):
        routes = (routes + ", " if routes != "none" else "") + "PORTALS — Dungeon Portals"
    if room["id"] != PORTAL_ROOM_ID and not instance_id:
        delves = [f"ENTER {entrance['name']}" + ("" if not viewer_id or _exit_open_to(database, viewer_id, entrance)
                                                else " [barred]")
                  for entrance in _room_entrances(database, room["id"])]
        if delves:
            routes = (routes + ", " if routes != "none" else "") + ", ".join(delves)
    states = " ".join(text for text in (_object_state_text(database, item) for item in
                                        _room_item_rows(database, room["id"], instance_id)) if text)
    return (
        f"{room['name']}\n{room['description']}" + (f"\n\n{states}" if states else "") + f"{instructions}\n\n"
        f"Exits: {routes}\nOther players: {people}\nNPCs: {npcs}\nItems: {items}"
    )


class ExaminationRegistry:
    """Ordered resolvers let later entity systems extend examination in place."""

    def __init__(self):
        self._providers = []

    def register(self, provider):
        self._providers.append(provider)

    def resolve(self, database, user, room, target):
        for provider in self._providers:
            result = provider(database, user, room, target)
            if result is not None:
                return result
        raise ActionError(f'You do not see "{target}" here.')


examinations = ExaminationRegistry()


def _examine_surroundings(database, _user, room, target):
    if target.casefold() in {"", "room", "here", "around", "surroundings", room["name"].casefold()}:
        return _describe_room(database, room, _user["id"])
    return None


examinations.register(_examine_surroundings)

EQUIPMENT_SLOTS = ("head", "body", "hands", "legs", "feet", "main_hand", "off_hand", "accessory")


def _item_row(database, item_id):
    return database.execute(
        "SELECT i.*,d.name,d.description,d.item_type,d.properties_json,d.behaviors_json,d.equipment_slot AS definition_slot "
        "FROM mud_item_instances AS i JOIN mud_item_definitions AS d ON d.id=i.definition_id WHERE i.id=?",
        (item_id,),
    ).fetchone()


def _item_name(row):
    return row["name"]


def _item_state(row):
    try:
        return json.loads(row["state_json"])
    except (TypeError, json.JSONDecodeError):
        return {}


def _loaded_rounds(item):
    capacity = _json_properties(item["properties_json"])["magazine_size"]
    value = _item_state(item).get("loaded", 0)
    return min(capacity, max(0, value)) if type(value) is int else 0


def _remaining_ammo(item):
    full = _json_properties(item["properties_json"]).get("rounds", 0)
    if type(full) is not int or full <= 0:
        return 0
    value = _item_state(item).get("rounds", full)
    return min(full, max(0, value)) if type(value) is int else 0


def _equipped_firearm(database, user_id):
    character = _character_row(database, user_id)
    facts = character_creation.origin_facts(database, character["id"])
    for item in _inventory_rows(database, user_id, equipped=True):
        if item["equipped_slot"] in ("main_hand", "off_hand") and mud_firearms.is_firearm(
            _json_properties(item["properties_json"])
        ) and _meets_item_requirements(_json_properties(item["properties_json"]), character, facts):
            return item
    return None


def _save_item_state(database, item_id, state):
    database.execute(
        "UPDATE mud_item_instances SET state_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (json.dumps(state, separators=(",", ":"), sort_keys=True), item_id),
    )


def _item_matches(rows, target):
    rows = list(rows)
    key = " ".join(str(target).casefold().split())
    direct = [row for row in rows if row["id"] == target]
    if direct:
        return direct[0]
    matches = [row for row in rows if " ".join(row["name"].casefold().split()) == key]
    if not matches:
        numbered = re.fullmatch(r"(.+?)\s+(?:\((\d+)\)|(\d+))", key)
        if numbered:
            name, number = numbered.group(1), int(numbered.group(2) or numbered.group(3))
            matches = [row for row in rows if " ".join(row["name"].casefold().split()) == name]
            if 1 <= number <= len(matches):
                return matches[number - 1]
            raise ActionError(f'No item named "{name}" has number {number}.')
    if not matches:
        raise ActionError(f'You cannot find "{target}".')
    if len(matches) > 1:
        raise ActionError(f'More than one item matches "{target}". Use a number, such as "{matches[0]["name"]} 2".')
    return matches[0]


def _numbered_items(rows):
    rows = list(rows)
    counts = {}
    for row in rows:
        key = " ".join(row["name"].casefold().split())
        counts[key] = counts.get(key, 0) + 1
    positions = {}
    for row in rows:
        key = " ".join(row["name"].casefold().split())
        positions[key] = positions.get(key, 0) + 1
        yield row, f"{row['name']} ({positions[key]})" if counts[key] > 1 else row["name"]


def _inventory_rows(database, user_id, equipped=False):
    character = _character_row(database, user_id)
    context = _inventory_context(database, user_id)
    visibility = "" if context["shared"] else "AND i.possession_mode=? "
    parameters = [context["loadout"], character["id"]]
    if not context["shared"]:
        parameters.append(context["mode"])
    equipment_filter = "e.slot IS NOT NULL" if equipped else "e.slot IS NULL"
    return database.execute(
        "SELECT i.id,i.definition_id,i.room_id,i.owner_character_id,i.container_id,"
        "e.slot AS equipped_slot,i.is_open,i.state_json,i.possession_mode,i.acquired_run_id,i.created_at,i.updated_at,"
        "d.name,d.description,d.item_type,d.equipment_slot AS definition_slot,d.properties_json "
        "FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id "
        "LEFT JOIN mud_character_equipment e ON e.item_instance_id=i.id "
        "AND e.character_id=i.owner_character_id AND e.loadout_key=? "
        f"WHERE i.owner_character_id=? {visibility}AND {equipment_filter} "
        "AND NOT EXISTS (SELECT 1 FROM mud_character_storage AS s WHERE s.item_instance_id=i.id) "
        "ORDER BY d.name COLLATE NOCASE,i.id",
        tuple(parameters),
    ).fetchall()


def _room_item_rows(database, room_id, instance_id=None):
    if instance_id:
        rows = []
        for content in database.execute(
            "SELECT c.* FROM mud_dungeon_content AS c "
            "LEFT JOIN mud_dungeon_content_state AS s ON s.content_id=c.id "
            "WHERE c.instance_id=? AND c.room_key=? AND c.content_type='item' "
            "AND COALESCE(s.status,'available')='available' ORDER BY c.id",
            (instance_id, room_id),
        ).fetchall():
            snapshot = _json_properties(content["snapshot_json"])
            rows.append({
                "id": content["id"], "definition_id": content["definition_id"],
                "room_id": None, "owner_character_id": None, "container_id": None,
                "equipped_slot": None, "is_open": 0, "state_json": "{}",
                "name": snapshot.get("name", content["definition_id"] or "Unknown item"),
                "description": snapshot.get("description", ""),
                "item_type": snapshot.get("item_type", "misc"),
                "definition_slot": snapshot.get("equipment_slot"),
                "properties_json": snapshot.get("properties_json", "{}"),
                "dungeon_content_id": content["id"], "instance_id": instance_id,
            })
        return rows
    return _script_visible(database, "item", database.execute(
        "SELECT i.*,d.name,d.description,d.item_type,d.equipment_slot AS definition_slot,d.properties_json "
        "FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id "
        "WHERE i.room_id=? ORDER BY d.name COLLATE NOCASE,i.id", (room_id,)
    ).fetchall())


def _container_rows(database, container_id, user_id=None):
    visibility = ""
    parameters = [container_id]
    if user_id is not None and not _inventory_context(database, user_id)["shared"]:
        visibility = " AND i.possession_mode=?"
        parameters.append(_current_mode(database, user_id))
    return database.execute(
        "SELECT i.*,d.name,d.description,d.item_type,d.equipment_slot AS definition_slot,d.properties_json "
        "FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id "
        f"WHERE i.container_id=?{visibility} ORDER BY d.name COLLATE NOCASE,i.id",
        tuple(parameters),
    ).fetchall()


def _is_container(row):
    return row["item_type"] == "container"


def _visible_container(database, user_id, room, item_id):
    row = _item_row(database, item_id)
    if row is None or not _is_container(row):
        raise ActionError("That is not an available container.")
    if database.execute(
        "SELECT 1 FROM mud_character_storage WHERE item_instance_id=?", (row["id"],)
    ).fetchone():
        raise ActionError("Withdraw that container from personal storage first.")
    character = _character_row(database, user_id)
    owned_visible = row["owner_character_id"] == character["id"] and (
        _inventory_context(database, user_id)["shared"] or
        row["possession_mode"] == _current_mode(database, user_id)
    )
    if row["room_id"] != room["id"] and not owned_visible:
        raise ActionError("That container is not available to you.")
    return row


def _accessible_containers(database, user_id, room):
    return [item for item in (
        list(_room_item_rows(database, room["id"], room.get("instance_id")))
        + list(_inventory_rows(database, user_id))
        + list(_inventory_rows(database, user_id, True))
    ) if _is_container(item) and not isinstance(item, dict)]


def _container_target(database, user_id, room, target):
    candidates = _accessible_containers(database, user_id, room)
    if not candidates:
        raise ActionError("There is no available container here.")
    return _visible_container(database, user_id, room, _item_matches(candidates, target)["id"])


def _room_item_event(database, user, room, message):
    instance_id = room.get("instance_id")
    return EventDraft(message, event_type="item",
                      audience_type="instance" if instance_id else "room",
                      audience_key=instance_id or room["id"],
                      recipient_ids=_occupant_ids(database, room["id"], user["id"], instance_id))


def inventory_state(database, user_id):
    context = _inventory_context(database, user_id)
    character = _character_row(database, user_id)
    facts = character_creation.origin_facts(database, character["id"])
    inventory = [dict(row) for row in _inventory_rows(database, user_id)]
    equipment = [dict(row) for row in _inventory_rows(database, user_id, equipped=True)]
    for items in (inventory, equipment):
        for item, label in _numbered_items(items):
            item["display_name"] = label
            properties = _json_properties(item["properties_json"])
            if item["equipped_slot"]:
                item["requirements_met"] = _meets_item_requirements(properties, character, facts)
            if mud_firearms.is_firearm(properties):
                item["loaded_rounds"] = _loaded_rounds(item)
                item["magazine_size"] = properties["magazine_size"]
            elif "ammo_type" in properties and "rounds" in properties:
                item["rounds_remaining"] = _remaining_ammo(item)
    return {
        "inventory": inventory, "equipment": equipment, "slots": EQUIPMENT_SLOTS,
        "mode": context["mode"], "sharing": "shared" if context["shared"] else "independent",
    }


def _inventory_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The inventory command does not accept arguments.")
    state = inventory_state(database, user["id"])
    def list_items(items, equipped=False):
        if not items:
            return "  none"
        return "\n".join(
            f"  - {label}" +
            (f" ({item['equipped_slot'].replace('_', ' ')})" if equipped else "") +
            (" [requirements unmet]" if equipped and not item["requirements_met"] else "") +
            (f" [{item['loaded_rounds']}/{item['magazine_size']} loaded]" if "loaded_rounds" in item else
             f" [{item['rounds_remaining']} rounds]" if "rounds_remaining" in item else "") +
            (" [run loot]" if item["acquired_run_id"] else "")
            for item, label in _numbered_items(items)
        )
    scope = "shared across modes" if state["sharing"] == "shared" else f"{state['mode']} mode only"
    return [EventDraft(
        f"Inventory ({scope})\nCarried:\n{list_items(state['inventory'])}\n"
        f"Equipped:\n{list_items(state['equipment'], True)}", event_type="item")]


def _grant_item(database, user_id, definition_id, id_prefix="reward:item"):
    """Create one possession with immutable mode provenance."""
    character = _character_row(database, user_id)
    item_id = f"{id_prefix}:{uuid.uuid4().hex}"
    room = _current_room(database, user_id)
    run_id = room.get("instance_id") if isinstance(room, dict) else None
    database.execute(
        "INSERT INTO mud_item_instances"
        "(id,definition_id,owner_character_id,possession_mode,acquired_run_id) VALUES(?,?,?,?,?)",
        (item_id, definition_id, character["id"], _current_mode(database, user_id), run_id),
    )
    return item_id


def _item_value(row):
    properties = _json_properties(row["properties_json"])
    stated = properties.get("value")
    if "ammo_type" in properties and type(properties.get("rounds")) is int:
        full = properties["rounds"]
        if full > 0:
            remaining = _remaining_ammo(row) if "state_json" in row.keys() else full
            full_value = stated if type(stated) is int and 1 <= stated <= 100000 else 2 * full
            return max(1, full_value * max(0, min(full, remaining)) // full)
    if isinstance(stated, int) and not isinstance(stated, bool) and 1 <= stated <= 100000:
        return stated
    name = row["name"].casefold()
    if any(word in name for word in ("gem", "jewel", "relic", "treasure", "casket", "idol", "gold")):
        return 60
    if any(word in name for word in ("coin", "pouch", "cache", "chest", "amulet",
                                     "signet", "compass", "journal", "beadwork", "token")):
        return 24
    if row["item_type"] == "equipment":
        return 12 + 8 * max(1, _integer_property(properties, "damage", "attack", "armor", "defense"))
    if row["item_type"] == "consumable":
        return 20 if "heal" in name or "potion" in name else 8
    return 8 if row["item_type"] == "misc" else 4


def _resale_price(database, item, percent):
    price = max(1, _item_value(item) * percent // 100)
    floor = database.execute("SELECT MIN(price) FROM mud_shop_stock WHERE item_definition_id=?",
                             (item["definition_id"],)).fetchone()[0]
    return min(price, floor) if floor is not None else price


def _wallet(database, character_id):
    database.execute(
        "INSERT OR IGNORE INTO mud_character_wallets(character_id) VALUES(?)", (character_id,),
    )
    return database.execute(
        "SELECT coins FROM mud_character_wallets WHERE character_id=?", (character_id,),
    ).fetchone()["coins"]


def hub_state(database, user_id):
    if _current_room(database, user_id)["id"] != PORTAL_ROOM_ID:
        return None
    character = _character_row(database, user_id)
    catalog = database.execute(
        "SELECT id,name,description,item_type,equipment_slot,properties_json "
        "FROM mud_item_definitions WHERE id LIKE 'stationnet:shop:%' ORDER BY item_type,name"
    ).fetchall()
    stored = database.execute(
        "SELECT i.id,d.name,d.description,d.item_type,d.properties_json "
        "FROM mud_character_storage AS s JOIN mud_item_instances AS i ON i.id=s.item_instance_id "
        "JOIN mud_item_definitions AS d ON d.id=i.definition_id "
        "WHERE s.character_id=? ORDER BY d.name,i.id", (character["id"],),
    ).fetchall()
    return {
        "coins": _wallet(database, character["id"]),
        "shop": [{"id": row["id"], "name": row["name"], "description": row["description"],
                  "price": _discounted(database, character["id"], _item_value(row))} for row in catalog],
        "storage": [{"id": row["id"], "name": row["name"]} for row in stored],
    }


def _discounted(database, character_id, price):
    """Apply Haggling-style merchant discounts; every purchase still costs a coin."""
    discount = _character_effects(database, character_id)["shop_discount"]
    return max(1, price - price * discount // 100)


def _require_hub(database, user_id):
    if _current_room(database, user_id)["id"] != PORTAL_ROOM_ID:
        raise ActionError("The trader and personal storage are only available in the portal room.")


def _shop_action(database, user, parameters):
    if _current_room(database, user["id"])["id"] != PORTAL_ROOM_ID:
        return _world_shop_action(database, user, parameters)
    _require_hub(database, user["id"])
    args = [str(value) for value in parameters.get("arguments", [])]
    operation = str(parameters.get("operation") or (args[0] if args else "list")).casefold()
    target = str(parameters.get("item_id") or " ".join(args[1:])).strip()
    character = _character_row(database, user["id"])
    if operation in {"list", "stock"}:
        stock = hub_state(database, user["id"])
        return [EventDraft("Trader stock (coins):\n" + "\n".join(
            f"- {item['name']}: {item['price']}" for item in stock["shop"]
        ) + f"\nYou have {stock['coins']} coins. Use /shop buy <name> or /shop sell <item>.",
            event_type="shop")]
    if operation == "buy":
        row = database.execute(
            "SELECT * FROM mud_item_definitions WHERE id LIKE 'stationnet:shop:%' "
            "AND (id=? OR name=? COLLATE NOCASE)", (target, target),
        ).fetchone()
        if row is None:
            raise ActionError("That supply is not sold here.")
        price = _discounted(database, character["id"], _item_value(row))
        _wallet(database, character["id"])
        if not database.execute(
            "UPDATE mud_character_wallets SET coins=coins-? WHERE character_id=? AND coins>=?",
            (price, character["id"], price),
        ).rowcount:
            raise ActionError("You do not have enough coins.")
        _grant_item(database, user["id"], row["id"], "shop:item")
        return [EventDraft(f"You buy {row['name']} for {price} coins.", event_type="shop")] + \
            _practice(database, character["id"], "purchase") + \
            _set_objective_progress(database, user, "possess_item", row["id"])
    if operation == "sell":
        item = _item_matches(
            list(_inventory_rows(database, user["id"])) +
            list(_inventory_rows(database, user["id"], True)), target,
        )
        if item["item_type"] == "key":
            raise ActionError("The trader does not buy keys.")
        if item["acquired_run_id"]:
            raise ActionError("Extract from the active run before selling its loot.")
        if database.execute("SELECT 1 FROM mud_item_instances WHERE container_id=? LIMIT 1",
                            (item["id"],)).fetchone():
            raise ActionError("Empty this container before selling it.")
        price = _resale_price(database, item, 40)
        database.execute("DELETE FROM mud_item_instances WHERE id=? AND owner_character_id=?",
                         (item["id"], character["id"]))
        _wallet(database, character["id"])
        database.execute("UPDATE mud_character_wallets SET coins=coins+? WHERE character_id=?",
                         (price, character["id"]))
        return [EventDraft(f"You sell {item['name']} for {price} coins.", event_type="shop")]
    raise ActionError("Use /shop, /shop buy <name>, or /shop sell <item>.")


def _world_shop_action(database, user, parameters):
    room = _current_room(database, user["id"])
    if room.get("instance_id"):
        raise ActionError("There is no merchant here.")
    args = [str(value) for value in parameters.get("arguments", [])]
    operation = args[0].casefold() if args and args[0].casefold() in {"buy", "sell", "list", "stock"} else "list"
    words = args[1:] if operation != "list" or (args and args[0].casefold() in {"list", "stock"}) else args
    target = " ".join(words).strip()
    merchant_name = ""
    if " from " in target.casefold():
        target, merchant_name = re.split(r"\s+from\s+", target, maxsplit=1, flags=re.I)
    elif operation == "list" and target:
        merchant_name = target
    merchants = []
    for npc in _room_npc_rows(database, room["id"]):
        if npc["combat_maximum_health"] is not None and _encounter_row(database, npc)["status"] == "defeated":
            continue
        shop = database.execute("SELECT * FROM mud_npc_shops WHERE npc_definition_id=?",
                                (npc["definition_id"],)).fetchone()
        if shop and (not merchant_name or npc["name"].casefold() == merchant_name.casefold()):
            merchants.append((npc, shop))
    if not merchants:
        raise ActionError("There is no merchant here" + (f" named {merchant_name}" if merchant_name else "") + ".")
    if len(merchants) != 1:
        raise ActionError("Several merchants are here. Add 'from <merchant>' to your command.")
    npc, shop = merchants[0]
    character = _character_row(database, user["id"])
    if operation == "list":
        stock = database.execute(
            "SELECT s.*,d.name FROM mud_shop_stock s JOIN mud_item_definitions d ON d.id=s.item_definition_id "
            "WHERE s.npc_definition_id=? AND (s.quantity IS NULL OR s.quantity>0) ORDER BY d.name COLLATE NOCASE",
            (npc["definition_id"],)).fetchall()
        lines = [f"{npc['name']} offers:"] + [f"- {row['name']}: {_discounted(database, character['id'], row['price'])} coins" +
                 (f" ({row['quantity']} left)" if row["quantity"] is not None else "") for row in stock]
        if not stock:
            lines.append("No stock available.")
        lines.append(f"You have {_wallet(database, character['id'])} coins. Use /shop buy <item> or /shop sell <item>.")
        return [EventDraft("\n".join(lines), event_type="shop")]
    if operation == "buy":
        stock = database.execute(
            "SELECT s.*,d.name FROM mud_shop_stock s JOIN mud_item_definitions d ON d.id=s.item_definition_id "
            "WHERE s.npc_definition_id=? AND (s.item_definition_id=? OR d.name=? COLLATE NOCASE)",
            (npc["definition_id"], target, target)).fetchall()
        if len(stock) != 1:
            raise ActionError("That item is not sold here." if not stock else "Use a specific item ID.")
        row = stock[0]
        if row["quantity"] is not None and not database.execute(
            "UPDATE mud_shop_stock SET quantity=quantity-1 WHERE id=? AND quantity>0", (row["id"],)).rowcount:
            raise ActionError("That item is out of stock.")
        price = _discounted(database, character["id"], row["price"])
        _wallet(database, character["id"])
        if not database.execute("UPDATE mud_character_wallets SET coins=coins-? WHERE character_id=? AND coins>=?",
                                (price, character["id"], price)).rowcount:
            raise ActionError("You do not have enough coins.")
        _grant_item(database, user["id"], row["item_definition_id"], "shop:item")
        return [EventDraft(f"You buy {row['name']} for {price} coins.", event_type="shop")] + \
            _practice(database, character["id"], "purchase") + \
            _set_objective_progress(database, user, "possess_item", row["item_definition_id"])
    if operation == "sell":
        if not shop["buys_items"]:
            raise ActionError(f"{npc['name']} does not buy items.")
        item = _item_matches(list(_inventory_rows(database, user["id"])) +
                             list(_inventory_rows(database, user["id"], True)), target)
        if item["item_type"] == "key" or item["acquired_run_id"]:
            raise ActionError("That item cannot be sold here.")
        if database.execute("SELECT 1 FROM mud_item_instances WHERE container_id=?", (item["id"],)).fetchone():
            raise ActionError("Empty this container before selling it.")
        price = _resale_price(database, item, shop["sell_percent"])
        if database.execute("DELETE FROM mud_item_instances WHERE id=? AND owner_character_id=?",
                            (item["id"], character["id"])).rowcount != 1:
            raise ActionError("That item is no longer yours.")
        _wallet(database, character["id"])
        database.execute("UPDATE mud_character_wallets SET coins=coins+? WHERE character_id=?",
                         (price, character["id"]))
        return [EventDraft(f"You sell {item['name']} for {price} coins.", event_type="shop")]
    raise ActionError("Use /shop, /shop buy <item>, or /shop sell <item>.")


def _storage_action(database, user, parameters):
    _require_hub(database, user["id"])
    args = [str(value) for value in parameters.get("arguments", [])]
    operation = str(parameters.get("operation") or (args[0] if args else "list")).casefold()
    target = str(parameters.get("item_id") or " ".join(args[1:])).strip()
    character = _character_row(database, user["id"])
    if operation == "list":
        stored = hub_state(database, user["id"])["storage"]
        return [EventDraft("Personal storage: " +
                           (", ".join(item["name"] for item in stored) or "empty") +
                           ". Use /storage deposit <item> or /storage withdraw <item>.",
                           event_type="item")]
    if operation == "deposit":
        item = _item_matches(
            list(_inventory_rows(database, user["id"])) +
            list(_inventory_rows(database, user["id"], True)), target,
        )
        if item["acquired_run_id"]:
            raise ActionError("Extract from the active run before storing its loot.")
        database.execute("DELETE FROM mud_character_equipment WHERE item_instance_id=?", (item["id"],))
        database.execute("INSERT INTO mud_character_storage(item_instance_id,character_id) VALUES(?,?)",
                         (item["id"], character["id"]))
        return [EventDraft(f"You store {item['name']}.", event_type="item")]
    if operation == "withdraw":
        rows = database.execute(
            "SELECT i.id,d.name FROM mud_character_storage AS s "
            "JOIN mud_item_instances AS i ON i.id=s.item_instance_id "
            "JOIN mud_item_definitions AS d ON d.id=i.definition_id "
            "WHERE s.character_id=? AND i.owner_character_id=?",
            (character["id"], character["id"]),
        ).fetchall()
        item = _item_matches(rows, target)
        database.execute("DELETE FROM mud_character_storage WHERE item_instance_id=? AND character_id=?",
                         (item["id"], character["id"]))
        return [EventDraft(f"You withdraw {item['name']}.", event_type="item")]
    raise ActionError("Use /storage, /storage deposit <item>, or /storage withdraw <item>.")


def _settle_run_loot(database, user_id, run_id, keep):
    character = _character_row(database, user_id)
    if keep:
        database.execute(
            "UPDATE mud_item_instances SET acquired_run_id=NULL WHERE owner_character_id=? "
            "AND acquired_run_id=?", (character["id"], run_id),
        )
    else:
        database.execute(
            "DELETE FROM mud_item_instances WHERE owner_character_id=? AND acquired_run_id=?",
            (character["id"], run_id),
        )


def _objective_target_name(database, objective):
    tables = {
        "talk_npc": ("mud_npc_definitions", "NPC"),
        "visit_room": ("mud_world_rooms", "room"),
        "possess_item": ("mud_item_definitions", "item"),
        "deliver_item": ("mud_item_definitions", "item"),
        "defeat_npc": ("mud_npc_definitions", "NPC"),
    }
    table, fallback = tables[objective["objective_type"]]
    row = database.execute(f"SELECT name FROM {table} WHERE id=?", (objective["target_id"],)).fetchone()
    return row["name"] if row else f"Unknown {fallback}"


def _npc_names(database, definition_ids):
    names = sorted({row["name"] for row in database.execute(
        f"SELECT name FROM mud_npc_definitions WHERE id IN ({','.join('?' for _ in definition_ids)})",
        tuple(definition_ids),
    )}, key=str.casefold) if definition_ids else []
    return " or ".join(names) if names else "the quest giver"


def _turn_in_npc_ids(database, quest):
    """The NPC definitions that accept a turn-in: the named one, else any giver."""
    if quest["turn_in_npc_definition_id"]:
        return {quest["turn_in_npc_definition_id"]}
    return {row["npc_definition_id"] for row in database.execute(
        "SELECT npc_definition_id FROM mud_quest_givers WHERE quest_id=?", (quest["id"],))}


def _quest_objectives(database, character_id, quest_id):
    return [dict(row) for row in database.execute(
        "SELECT o.*,COALESCE(p.progress,0) AS progress,p.completed_at "
        "FROM mud_quest_objectives AS o LEFT JOIN mud_player_objectives AS p "
        "ON p.objective_id=o.id AND p.character_id=? WHERE o.quest_id=? "
        "ORDER BY o.sort_order,o.id", (character_id, quest_id),
    )]


def _locked_objective_ids(quest, objectives):
    """Sequential quests unlock stages (equal sort_order) once earlier required work is done."""
    if not quest["sequential"]:
        return set()
    locked, blocked = set(), False
    for stage in sorted({objective["sort_order"] for objective in objectives}):
        members = [objective for objective in objectives if objective["sort_order"] == stage]
        if blocked:
            locked.update(objective["id"] for objective in members)
        elif any(not objective["optional"] and not objective["completed_at"] for objective in members):
            blocked = True
    return locked


def quest_state(database, user_id):
    """Return player-specific active/completed quest state for rendering."""
    character = _character_row(database, user_id)
    quests = []
    rows = database.execute(
        "SELECT q.id,q.name,q.description,q.completion_mode,q.sequential,q.abandonable,"
        "q.turn_in_npc_definition_id,p.status,p.accepted_at,p.completed_at,p.rewarded_at,p.ready_at "
        "FROM mud_player_quests AS p JOIN mud_quests AS q ON q.id=p.quest_id "
        "WHERE p.character_id=? ORDER BY p.status,q.name COLLATE NOCASE,q.id",
        (character["id"],),
    ).fetchall()
    for row in rows:
        entry = dict(row)
        entry["ready"] = row["status"] == "active" and row["ready_at"] is not None
        entry["turn_in_npc_name"] = (_npc_names(database, _turn_in_npc_ids(database, row))
                                     if row["completion_mode"] == "turn_in" else None)
        objectives = _quest_objectives(database, character["id"], row["id"])
        locked = _locked_objective_ids(row, objectives) if row["status"] == "active" else set()
        entry["objectives"] = []
        for item in objectives:
            item["target_name"] = _objective_target_name(database, item)
            item["locked"] = item["id"] in locked
            item["optional"] = bool(item["optional"])
            entry["objectives"].append(item)
        quests.append(entry)
    return {"active": [q for q in quests if q["status"] == "active"],
            "completed": [q for q in quests if q["status"] == "completed"]}


def _grant_rewards(database, user, rewards):
    """Grant quest-style rewards (experience, item, coins) and describe them."""
    character = _character_row(database, user["id"])
    messages = []
    for reward in rewards:
        if reward["reward_type"] == "experience":
            result = award_experience(database, user["id"], reward["amount"])
            # Report what was actually earned after Intellect and Lore bonuses.
            messages.append(f"{result['amount']} experience")
            gains = level_up_text(result)
            if gains:
                messages.append(gains)
        elif reward["reward_type"] == "item":
            definition = database.execute(
                "SELECT name FROM mud_item_definitions WHERE id=?", (reward["target_id"],)
            ).fetchone()
            if definition is None:
                continue
            for _ in range(reward["amount"]):
                _grant_item(database, user["id"], reward["target_id"])
            messages.append(f"{reward['amount']} × {definition['name']}")
        elif reward["reward_type"] == "coins":
            _wallet(database, character["id"])
            database.execute("UPDATE mud_character_wallets SET coins=coins+? WHERE character_id=?",
                             (reward["amount"], character["id"]))
            messages.append(f"{reward['amount']} coins")
    return messages


def _award_quest_rewards(database, user, quest_id):
    character = _character_row(database, user["id"])
    # Claim the payout first so a repeated completion can never pay twice.
    if database.execute(
        "UPDATE mud_player_quests SET rewarded_at=CURRENT_TIMESTAMP WHERE character_id=? "
        "AND quest_id=? AND rewarded_at IS NULL", (character["id"], quest_id),
    ).rowcount == 0:
        return []
    rewards = database.execute(
        "SELECT * FROM mud_quest_rewards WHERE quest_id=? ORDER BY id", (quest_id,),
    ).fetchall()
    messages = _grant_rewards(database, user, rewards)
    for reward in rewards:
        if reward["reward_type"] == "item":
            _record_objective_progress(database, user, "possess_item", reward["target_id"])
    return messages


def _finish_ready_quests(database, user):
    character = _character_row(database, user["id"])
    completed = []
    active = database.execute(
        "SELECT q.*,p.ready_at FROM mud_player_quests AS p JOIN mud_quests AS q ON q.id=p.quest_id "
        "WHERE p.character_id=? AND p.status='active'", (character["id"],),
    ).fetchall()
    for quest in active:
        remaining = database.execute(
            "SELECT 1 FROM mud_quest_objectives AS o LEFT JOIN mud_player_objectives AS p "
            "ON p.objective_id=o.id AND p.character_id=? WHERE o.quest_id=? AND o.optional=0 "
            "AND (p.completed_at IS NULL) LIMIT 1", (character["id"], quest["id"]),
        ).fetchone()
        if remaining is not None:
            continue
        if quest["completion_mode"] == "turn_in":
            if quest["ready_at"] is None and database.execute(
                "UPDATE mud_player_quests SET ready_at=CURRENT_TIMESTAMP WHERE character_id=? "
                "AND quest_id=? AND status='active' AND ready_at IS NULL",
                (character["id"], quest["id"]),
            ).rowcount:
                who = _npc_names(database, _turn_in_npc_ids(database, quest))
                completed.append(EventDraft(
                    f"Objectives complete: {quest['name']}. Return to {who} to turn it in "
                    f"(/quest turnin {quest['name']}).", event_type="quest"))
            continue
        database.execute(
            "UPDATE mud_player_quests SET status='completed',completed_at=CURRENT_TIMESTAMP "
            "WHERE character_id=? AND quest_id=? AND status='active'",
            (character["id"], quest["id"]),
        )
        rewards = _award_quest_rewards(database, user, quest["id"])
        suffix = f" Rewards: {', '.join(rewards)}." if rewards else ""
        completed.append(EventDraft(f"Quest complete: {quest['name']}.{suffix}", event_type="quest"))
        completed += fire_triggers(database, user, "quest_complete", [quest["id"]])
    return completed


def _unlocked_objective(database, character_id, objective_id):
    quest = database.execute(
        "SELECT q.* FROM mud_quests q JOIN mud_quest_objectives o ON o.quest_id=q.id WHERE o.id=?",
        (objective_id,),
    ).fetchone()
    if quest is None or not quest["sequential"]:
        return quest is not None
    return objective_id not in _locked_objective_ids(
        quest, _quest_objectives(database, character_id, quest["id"]))


def _held_counts(database, user_id):
    counts = {}
    for item in (*_inventory_rows(database, user_id), *_inventory_rows(database, user_id, True)):
        counts[item["definition_id"]] = counts.get(item["definition_id"], 0) + 1
    return counts


def _record_objective_progress(database, user, objective_type, target_id, objective_id=None):
    """Advance matching unlocked objectives; return True when one newly completes."""
    character = _character_row(database, user["id"])
    objectives = database.execute(
        "SELECT o.id,o.required_count,p.completed_at FROM mud_quest_objectives AS o "
        "JOIN mud_player_quests AS q ON q.quest_id=o.quest_id "
        "LEFT JOIN mud_player_objectives AS p ON p.objective_id=o.id AND p.character_id=q.character_id "
        "WHERE q.character_id=? AND q.status='active' AND o.objective_type=? AND o.target_id=? "
        "AND (? IS NULL OR o.id=?)",
        (character["id"], objective_type, target_id, objective_id, objective_id),
    ).fetchall()
    newly_completed = False
    for objective in objectives:
        if objective["completed_at"] or not _unlocked_objective(database, character["id"], objective["id"]):
            continue
        if objective_type == "possess_item":
            count = _held_counts(database, user["id"]).get(target_id, 0)
            progress = min(count, objective["required_count"])
        else:
            current = database.execute(
                "SELECT progress FROM mud_player_objectives WHERE character_id=? AND objective_id=?",
                (character["id"], objective["id"])).fetchone()
            progress = min(objective["required_count"], (current["progress"] if current else 0) + 1)
        done = progress >= objective["required_count"]
        newly_completed = newly_completed or done
        database.execute(
            "INSERT INTO mud_player_objectives(character_id,objective_id,progress,completed_at) "
            "VALUES(?,?,?,CASE WHEN ? THEN CURRENT_TIMESTAMP END) "
            "ON CONFLICT(character_id,objective_id) DO UPDATE SET progress=MAX(progress,excluded.progress),"
            "completed_at=CASE WHEN completed_at IS NOT NULL THEN completed_at "
            "WHEN excluded.progress>=(SELECT required_count FROM mud_quest_objectives WHERE id=excluded.objective_id) "
            "THEN CURRENT_TIMESTAMP END,updated_at=CURRENT_TIMESTAMP",
            (character["id"], objective["id"], progress, 1 if done else 0),
        )
    return newly_completed


def _sync_held_items(database, user):
    """Credit possession objectives, repeating as finished stages unlock later ones."""
    for _ in range(50):
        changed = False
        for definition_id in _held_counts(database, user["id"]):
            changed = _record_objective_progress(database, user, "possess_item", definition_id) or changed
        if not changed:
            return


def _set_objective_progress(database, user, objective_type, target_id, objective_id=None):
    if _record_objective_progress(database, user, objective_type, target_id, objective_id):
        _sync_held_items(database, user)
    return _finish_ready_quests(database, user)


def _quest_status(database, character_id, quest_id):
    row = database.execute(
        "SELECT status,ready_at FROM mud_player_quests WHERE character_id=? AND quest_id=?",
        (character_id, quest_id),
    ).fetchone()
    if row is None:
        return "not_started"
    if row["status"] == "active" and row["ready_at"] is not None:
        return "ready"
    return row["status"]


def _quest_blocker(database, user, quest):
    """Why this character cannot accept the quest now, or None."""
    character = _character_row(database, user["id"])
    if _quest_status(database, character["id"], quest["id"]) != "not_started":
        return "You have already accepted that quest."
    if quest["prerequisite_quest_id"] and database.execute(
        "SELECT 1 FROM mud_player_quests WHERE character_id=? AND quest_id=? "
        "AND status='completed' AND rewarded_at IS NOT NULL",
        (character["id"], quest["prerequisite_quest_id"]),
    ).fetchone() is None:
        return "You have not completed this quest's prerequisite."
    if not mud_conditions.evaluate(quest["conditions_json"], _ConditionContext(database, user["id"])):
        return "You do not meet the requirements for that quest."
    return None


def _available_quests(database, user, room_id):
    """Quests offered by NPCs in this room that the character can accept now."""
    return [dict(row) for row in database.execute(
        "SELECT q.* FROM mud_quests q WHERE EXISTS (SELECT 1 FROM mud_quest_givers g "
        "JOIN mud_npc_instances n ON n.definition_id=g.npc_definition_id "
        "WHERE g.quest_id=q.id AND n.room_id=?) ORDER BY q.name COLLATE NOCASE,q.id", (room_id,))
        if _quest_blocker(database, user, row) is None]


def _accept_quest(database, user, quest_id):
    character = _character_row(database, user["id"])
    quest = database.execute("SELECT * FROM mud_quests WHERE id=?", (quest_id,)).fetchone()
    if quest is None:
        raise ActionError("That quest no longer exists.")
    blocker = _quest_blocker(database, user, quest)
    if blocker:
        raise ActionError(blocker)
    database.execute(
        "INSERT INTO mud_player_quests(character_id,quest_id) VALUES(?,?)",
        (character["id"], quest_id),
    )
    database.execute(
        "INSERT INTO mud_player_objectives(character_id,objective_id) "
        "SELECT ?,id FROM mud_quest_objectives WHERE quest_id=?",
        (character["id"], quest_id),
    )
    room = _current_room(database, user["id"])
    events = [EventDraft(f"Quest accepted: {quest['name']} — {quest['description']}", event_type="quest")]
    _record_objective_progress(database, user, "visit_room", room["id"])
    _sync_held_items(database, user)
    return events + _finish_ready_quests(database, user)


def _abandon_quest(database, user, quest):
    character = _character_row(database, user["id"])
    status = _quest_status(database, character["id"], quest["id"])
    if status in ("not_started", "completed"):
        raise ActionError("You can only abandon an active quest.")
    if not quest["abandonable"]:
        raise ActionError(f"{quest['name']} cannot be abandoned.")
    database.execute(
        "DELETE FROM mud_player_objectives WHERE character_id=? AND objective_id IN "
        "(SELECT id FROM mud_quest_objectives WHERE quest_id=?)", (character["id"], quest["id"]),
    )
    database.execute("DELETE FROM mud_player_quests WHERE character_id=? AND quest_id=? AND status='active'",
                     (character["id"], quest["id"]))
    return [EventDraft(f"Quest abandoned: {quest['name']}. Its progress has been cleared.", event_type="quest")]


def _handover_items(database, user, definition_id, amount):
    """Carried, unequipped items that can be given away, or an ActionError."""
    eligible = [item for item in _inventory_rows(database, user["id"])
                if item["definition_id"] == definition_id and not item["acquired_run_id"]
                and database.execute("SELECT 1 FROM mud_item_instances WHERE container_id=?",
                                     (item["id"],)).fetchone() is None]
    if len(eligible) < amount:
        name = database.execute("SELECT name FROM mud_item_definitions WHERE id=?",
                                (definition_id,)).fetchone()
        raise ActionError(f"You need {amount} × {name['name'] if name else 'that item'} "
                          "carried, unequipped, and empty (extract run loot first).")
    return eligible[:amount]


def _turn_in_quest(database, user, quest_id, npc_definition_id=None):
    character = _character_row(database, user["id"])
    room = _current_room(database, user["id"])
    quest = database.execute("SELECT * FROM mud_quests WHERE id=?", (quest_id,)).fetchone()
    if quest is None:
        raise ActionError("That quest no longer exists.")
    if room.get("instance_id"):
        raise ActionError("Quests are turned in within traditional worlds.")
    status = _quest_status(database, character["id"], quest_id)
    if status == "completed":
        raise ActionError(f"You have already turned in {quest['name']}.")
    if status == "not_started":
        raise ActionError(f"You are not on the quest {quest['name']}.")
    if quest["completion_mode"] != "turn_in":
        raise ActionError(f"{quest['name']} completes on its own once its objectives are done.")
    if status != "ready":
        raise ActionError(f"You have not finished the objectives for {quest['name']}.")
    recipients = _turn_in_npc_ids(database, quest)
    present = {row["definition_id"] for row in database.execute(
        "SELECT definition_id FROM mud_npc_instances WHERE room_id=?", (room["id"],))} & recipients
    if npc_definition_id is not None:
        present &= {npc_definition_id}
    if not present:
        raise ActionError(f"Return to {_npc_names(database, recipients)} to turn in {quest['name']}.")
    handed = []
    for objective in database.execute(
        "SELECT o.* FROM mud_quest_objectives o JOIN mud_player_objectives p ON p.objective_id=o.id "
        "WHERE o.quest_id=? AND p.character_id=? AND o.consume_on_turn_in=1 "
        "AND o.objective_type='possess_item' AND p.completed_at IS NOT NULL ORDER BY o.sort_order,o.id",
        (quest_id, character["id"]),
    ).fetchall():
        handed += _handover_items(database, user, objective["target_id"], objective["required_count"])
    if database.execute(
        "UPDATE mud_player_quests SET status='completed',completed_at=CURRENT_TIMESTAMP "
        "WHERE character_id=? AND quest_id=? AND status='active' AND ready_at IS NOT NULL",
        (character["id"], quest_id),
    ).rowcount == 0:
        raise ActionError(f"You have already turned in {quest['name']}.")
    for item in handed:
        database.execute("DELETE FROM mud_item_instances WHERE id=? AND owner_character_id=?",
                         (item["id"], character["id"]))
    events = []
    if handed:
        events.append(EventDraft("You hand over " + ", ".join(item["name"] for item in handed) + ".",
                                 event_type="quest"))
    rewards = _award_quest_rewards(database, user, quest_id)
    suffix = f" Rewards: {', '.join(rewards)}." if rewards else ""
    events.append(EventDraft(f"Quest complete: {quest['name']}.{suffix}", event_type="quest"))
    events += fire_triggers(database, user, "quest_complete", [quest_id])
    return events + _finish_ready_quests(database, user)


class _ConditionContext:
    """Lazy character/world facts that dialogue and quest conditions read."""

    def __init__(self, database, user_id, npc_id=None, dungeon=False, realm=None):
        self.database, self.user_id = database, user_id
        self.npc_id, self.dungeon = npc_id, dungeon
        self.realm = realm
        self.response_id = None
        self._row = self._origin = self._counts = self._skills = self._state = None

    def _character(self):
        if self._row is None:
            self._row = _character_row(self.database, self.user_id)
        return self._row

    def _origins(self):
        if self._origin is None:
            self._origin = self.database.execute(
                "SELECT race_key,class_key FROM mud_characters WHERE id=?", (self._character()["id"],)
            ).fetchone()
        return self._origin

    def level(self):
        return self._character()["level"]

    def race(self):
        return self._origins()["race_key"]

    def class_key(self):
        return self._origins()["class_key"]

    def tags(self):
        return _origin_profile(self.database, self._character()["id"])["tags"]

    def coins(self):
        return _wallet(self.database, self._character()["id"])

    def item_count(self, item_id):
        if self._counts is None:
            self._counts = _held_counts(self.database, self.user_id)
        return self._counts.get(item_id, 0)

    def attribute(self, name):
        return self._character()[name]

    def skill_rank(self, skill_key):
        if self._skills is None:
            self._skills = {row["skill_key"]: row["rank"]
                            for row in mud_skills.character_rows(self.database, self._character()["id"])}
        return self._skills.get(skill_key, 0)

    def quest_status(self, quest_id):
        return _quest_status(self.database, self._character()["id"], quest_id)

    def objective_complete(self, objective_id):
        return self.database.execute(
            "SELECT 1 FROM mud_player_objectives WHERE character_id=? AND objective_id=? "
            "AND completed_at IS NOT NULL", (self._character()["id"], objective_id),
        ).fetchone() is not None

    def state(self, key):
        if self._state is None:
            self._state = (_npc_player_state(self.database, self._character()["id"], self.npc_id, self.dungeon)
                           if self.npc_id else {})
        return self._state.get(key, "")

    def claimed(self):
        return self.response_id is not None and self.database.execute(
            "SELECT 1 FROM mud_player_dialogue_claims WHERE character_id=? AND response_id=?",
            (self._character()["id"], self.response_id),
        ).fetchone() is not None

    def flag(self, scope, key):
        if self.realm is None:
            self.realm = _script_realm(self.database, self.user_id)
        scope_key = "" if scope == "world" else self._character()["id"]
        return _script_flag(self.database, self.realm, scope_key, key)

    def object_state(self, object_id):
        return _object_state(self.database, object_id)

    def room_id(self):
        return _current_room(self.database, self.user_id)["id"]


def _describe_item(database, row, user_id=None):
    body = f"{row['name']}\n{row['description']}\nType: {row['item_type']}"
    state = _object_state_text(database, row)
    if state:
        body += f"\n{state}"
    if row["definition_slot"]:
        body += f"\nEquipment slot: {row['definition_slot'].replace('_', ' ')}"
        try:
            requirements = _item_requirements(_json_properties(row["properties_json"]))
        except ValueError:
            requirements = {}
        if requirements:
            body += "\nRequires: " + mud_conditions.describe_item_requirements(requirements)
    if _is_container(row):
        contents = _container_rows(database, row["id"], user_id)
        if row["is_open"]:
            body += "\nContents: " + (", ".join(label for _, label in _numbered_items(contents)) or "empty")
        else:
            body += "\nIt is closed."
    return body


def _examine_item(database, user, room, target):
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    candidates = list(_room_item_rows(database, room["id"], instance_id)) + list(_inventory_rows(database, user["id"])) + list(_inventory_rows(database, user["id"], True))
    for container in _accessible_containers(database, user["id"], room):
        if _is_container(container) and container["is_open"]:
            candidates.extend(_container_rows(database, container["id"], user["id"]))
    key = " ".join(target.casefold().split())
    numbered = re.fullmatch(r"(.+?)\s+(?:\((\d+)\)|(\d+))", key)
    name_key = numbered.group(1) if numbered else key
    if not any(item["id"] == target or " ".join(item["name"].casefold().split()) in {key, name_key}
               for item in candidates):
        return None
    return _describe_item(database, _item_matches(candidates, target), user["id"])


examinations.register(_examine_item)


def _examine_npc(_database, _user, room, target):
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    rows = _room_npc_rows(_database, room["id"], instance_id)
    key = " ".join(target.casefold().split())
    numbered = re.fullmatch(r"(.+?)\s+(?:\((\d+)\)|(\d+))", key)
    name_key = numbered.group(1) if numbered else key
    if not any(npc["id"] == target or npc["definition_id"] == target or
               " ".join(npc["name"].casefold().split()) in {key, name_key} for npc in rows):
        return None
    npc = _npc_match(rows, target)
    body = f"{npc['name']}\n{npc['description']}"
    if npc["combat_maximum_health"] is not None:
        encounter = _encounter_row(_database, npc)
        body += (f"\n{_npc_stance(_database, npc, _user['id'] if _user else None)} · "
                 f"{encounter['status']} · Health "
                 f"{encounter['current_health']}/{encounter['maximum_health']}"
                 f" · Attack {npc['combat_attack']} · Defense {npc['combat_defense']}"
                 f" · Accuracy {npc['combat_accuracy'] or 0} · Evasion {npc['combat_evasion'] or 0}")
    return body


examinations.register(_examine_npc)


def _examine_player(database, user, room, target):
    candidates = _room_occupants(database, room["id"], user["id"], room.get("instance_id"))
    key = " ".join(target.casefold().split())
    if not any(entry["character_name"].casefold() == key or entry["display_name"].casefold() == key
               or entry["display_name"].removesuffix(")").replace(" (", " ").casefold() == key
               for entry in candidates):
        return None
    try:
        match = _item_matches([{"id": entry["id"], "name": entry["character_name"], "entry": entry}
                               for entry in candidates], target)["entry"]
    except ActionError as problem:
        raise ActionError(str(problem).replace("item", "player")) from problem
    row = database.execute("SELECT description FROM mud_characters WHERE id=?", (match["character_id"],)).fetchone()
    state = character_state(database, match["id"])
    return (f"{match['character_name']} — Level {state['level']}\n"
            f"Origin: {state['race'] or 'Unchosen'} / {state['class'] or 'Unchosen'}\n"
            f"{row['description'] or 'No description provided.'}")


examinations.register(_examine_player)


def _examine_exit(database, _user, room, target):
    modes = _mode_settings(database)
    if room["id"] == PORTAL_ROOM_ID and modes["traditional"] and target.casefold() in {"out", "world", "traditional world"}:
        return "Traditional World\nReturns to your last traditional-world location."
    if room["id"] != PORTAL_ROOM_ID and modes["procedural"] and room["id"] == _start_room(database)["id"] and target.casefold() in {"portals", "dungeon portals"}:
        return "Dungeon Portals\nLeads to the protected procedural hub."
    exits = _room_exits(database, room["id"], room.get("instance_id"), _user["id"])
    matches = [entry for entry in exits if target.casefold() in {
        entry["id"].casefold(), entry["name"].casefold(),
        (entry["direction"] or "").casefold()}]
    if len(matches) > 1:
        raise ActionError("More than one exit matches. Use its full name.")
    if matches:
        item = matches[0]
        if _exit_sealed(item):
            return f"{item['name']}\nLeads to {item['destination_name']}. Sealed; something else must open it."
        if _exit_locked(item):
            character = _character_row(database, _user["id"])
            unlocked = database.execute("SELECT 1 FROM mud_exit_unlocks WHERE character_id=? AND exit_id=?",
                                        (character["id"], item["id"])).fetchone()
            return f"{item['name']}\nLeads to {item['destination_name']}. " + ("Unlocked." if unlocked else _locked_hint(item))
        return f"{item['name']}\nLeads to {item['destination_name']}."
    return None


examinations.register(_examine_exit)


def _examine_dungeon_entrance(database, user, room, target):
    if room["id"] == PORTAL_ROOM_ID or room.get("instance_id"):
        return None
    entrance = _entrance_match(database, room["id"], target, required=False)
    if entrance is None:
        return None
    rule = json.loads(database.execute(
        "SELECT template_json FROM mud_world_dungeon_templates WHERE id=?", (entrance["template_id"],),
    ).fetchone()["template_json"])["generation"]["difficulty"]
    difficulty = entrance["difficulty"] or rule["default"]
    lines = [entrance["name"], entrance["description"] or f"A way down into {entrance['template_name']}.",
             f"Each descent is newly generated. Difficulty {difficulty} · recommended level "
             f"{dungeons.recommended_level(difficulty)} · up to {entrance['max_party']} explorer"
             f"{'s' if entrance['max_party'] != 1 else ''}."]
    if not _exit_open_to(database, user["id"], entrance):
        lines.append(f"Barred: {_exit_barred_message(entrance)}")
    else:
        lines.append(f"Enter with /enter {entrance['name']}.")
    return "\n".join(lines)


examinations.register(_examine_dungeon_entrance)


def _combat_target(parameters):
    return str(parameters.get("npc_id") or " ".join(
        str(item) for item in parameters.get("arguments", [])
    )).strip()


def _dungeon_room_difficulty(database, instance_id, room_key):
    row = database.execute(
        "SELECT r.difficulty AS room_difficulty,i.difficulty AS run_difficulty "
        "FROM mud_procedural_instances AS i LEFT JOIN mud_dungeon_rooms AS r "
        "ON r.instance_id=i.id AND r.room_key=? WHERE i.id=?", (room_key, instance_id),
    ).fetchone()
    if row is None:
        return 1
    return row["room_difficulty"] or row["run_difficulty"] or 1


def _encounter_reward_drafts(database, encounter, npc):
    drafts = []
    if encounter["scope_type"] == "instance":
        participants = database.execute(
            "SELECT p.character_id,c.user_id,c.display_name,p.damage_dealt,p.damage_taken "
            "FROM mud_combat_participants AS p "
            "JOIN mud_characters AS c ON c.id=p.character_id "
            "JOIN mud_player_locations AS l ON l.user_id=c.user_id "
            "JOIN mud_expedition_participants AS ep ON ep.instance_id=l.instance_id "
            "AND ep.user_id=c.user_id "
            "WHERE p.encounter_id=? AND p.damage_dealt+p.damage_taken>0 AND p.left_at IS NULL "
            "AND l.instance_id=? AND ep.status='active' ORDER BY p.joined_at,p.character_id",
            (encounter["id"], encounter["scope_key"]),
        ).fetchall()
    else:
        participants = database.execute(
            "SELECT p.character_id,c.user_id,c.display_name,p.damage_dealt,p.damage_taken "
            "FROM mud_combat_participants AS p "
            "JOIN mud_characters AS c ON c.id=p.character_id "
            "JOIN mud_world_locations AS l ON l.user_id=c.user_id "
            "WHERE p.encounter_id=? AND p.damage_dealt+p.damage_taken>0 AND p.left_at IS NULL "
            "AND l.room_id=? ORDER BY p.joined_at,p.character_id",
            (encounter["id"], encounter["scope_key"]),
        ).fetchall()
    # Blows dealt count fully and blows absorbed count half, so guards share the spoils.
    contributions = {row["character_id"]: row["damage_dealt"] + row["damage_taken"] // 2
                     for row in participants}
    total = sum(contributions.values())
    threshold = mud_combat.reward_threshold(sum(1 for value in contributions.values() if value > 0))
    configured = database.execute(
        "SELECT r.*,d.name AS item_name FROM mud_npc_combat_rewards AS r "
        "LEFT JOIN mud_item_definitions AS d ON d.id=r.target_id "
        "WHERE r.npc_definition_id=? ORDER BY r.id", (npc["definition_id"],),
    ).fetchall()
    for participant in participants:
        claimed = database.execute(
            "UPDATE mud_combat_participants SET rewarded_at=CURRENT_TIMESTAMP,"
            "last_action_at=CURRENT_TIMESTAMP WHERE encounter_id=? AND character_id=? "
            "AND rewarded_at IS NULL",
            (encounter["id"], participant["character_id"]),
        ).rowcount
        if not claimed:
            continue
        share = contributions[participant["character_id"]] / total if total else 0
        if share < threshold:
            drafts.append(EventDraft(
                f"You contributed too little to defeating {npc['name']} to earn rewards.",
                event_type="reward", audience_key=str(participant["user_id"]),
            ))
            continue
        rewards = []
        rewarded_items = set()
        experience = npc["experience_reward"]
        for reward in configured:
            if random.randint(1, 100) > reward["chance_percent"]:
                continue
            if reward["reward_type"] == "experience":
                experience += reward["amount"]
            elif reward["reward_type"] == "coins":
                coins = mud_combat.scaled_reward(reward["amount"], share)
                _wallet(database, participant["character_id"])
                database.execute("UPDATE mud_character_wallets SET coins=coins+? WHERE character_id=?",
                                 (coins, participant["character_id"]))
                rewards.append(f"{coins} coins")
            elif reward["reward_type"] == "item":
                for _ in range(reward["amount"]):
                    _grant_item(
                        database, participant["user_id"], reward["target_id"],
                        "combat:reward",
                    )
                rewarded_items.add(reward["target_id"])
                rewards.append(f"{reward['amount']} × {reward['item_name']}")
        level_up = ""
        experience = mud_combat.scaled_reward(experience, share)
        if experience and encounter["scope_type"] == "instance":
            # Dungeon kills taper for characters far above the room's recommended level.
            experience = max(1, experience * dungeons.dungeon_xp_percent(
                _character_row(database, participant["user_id"])["level"],
                _dungeon_room_difficulty(database, encounter["scope_key"], npc.get("room_id")),
            ) // 100)
        if experience:
            result = award_experience(database, participant["user_id"], experience)
            rewards.insert(0, f"{result['amount']} experience")
            level_up = level_up_text(result)
        summary = ", ".join(rewards) if rewards else "no configured rewards"
        drafts.append(EventDraft(
            f"Victory rewards for defeating {npc['name']}: {summary}." + (f" {level_up}" if level_up else ""),
            event_type="reward", audience_key=str(participant["user_id"]),
        ))
        for item_id in rewarded_items:
            for event in _set_objective_progress(database, {"id": participant["user_id"]},
                                                 "possess_item", item_id):
                drafts.append(EventDraft(event.body, event.event_type, "private",
                                         str(participant["user_id"]), (participant["user_id"],)))
        for event in _set_objective_progress(database, {"id": participant["user_id"]},
                                             "defeat_npc", npc["definition_id"]):
            drafts.append(EventDraft(event.body, event.event_type, "private",
                                     str(participant["user_id"]), (participant["user_id"],)))
        drafts += fire_triggers(
            database, {"id": participant["user_id"]}, "defeat_npc",
            [npc["id"] if "id" in npc.keys() else None, npc["definition_id"]],
            instance_id=encounter["scope_key"] if encounter["scope_type"] == "instance" else None)
    return drafts


def validate_npc_behaviors(value):
    """Validate the small, supported authored behavior schema."""
    if not isinstance(value, dict) or set(value) - {"aggressive", "friendly", "neutral", "passive", "ambient", "wander", "patrol", "reactions"}:
        raise ValueError("NPC behaviors contain an unsupported key.")
    if any(type(value[key]) is not bool for key in ("aggressive", "friendly", "neutral", "passive") if key in value):
        raise ValueError("aggressive, friendly, neutral, and passive must be true or false.")
    for key in ("ambient", "wander", "patrol"):
        if key not in value:
            continue
        spec = value[key]
        expected = {"interval_seconds", "lines"} if key == "ambient" else ({"interval_seconds"} if key == "wander" else {"interval_seconds", "directions"})
        if not isinstance(spec, dict) or set(spec) != expected:
            raise ValueError(f"{key} requires {', '.join(sorted(expected))}.")
        interval = spec["interval_seconds"]
        if type(interval) is not int or not 5 <= interval <= 86400:
            raise ValueError(f"{key} interval_seconds must be 5 to 86400.")
        if key == "ambient" and (not isinstance(spec["lines"], list) or not 1 <= len(spec["lines"]) <= 20 or
                                  any(not isinstance(line, str) or not 1 <= len(line.strip()) <= 300 for line in spec["lines"])):
            raise ValueError("ambient lines must contain 1 to 20 short strings.")
        if key == "patrol" and (not isinstance(spec["directions"], list) or not 1 <= len(spec["directions"]) <= 20 or
                                 any(direction not in DIRECTIONS for direction in spec["directions"])):
            raise ValueError("patrol directions must be supported exit directions.")
    if "wander" in value and "patrol" in value:
        raise ValueError("Choose either wander or patrol.")
    if "reactions" in value:
        reactions = value["reactions"]
        if not isinstance(reactions, dict) or not reactions or set(reactions) - {"enter", "say", "emote", "get", "drop", "open", "close", "put"} or any(
            not isinstance(line, str) or not 1 <= len(line.strip()) <= 300 for line in reactions.values()
        ):
            raise ValueError("reactions accepts short text for enter, say, emote, get, drop, open, close, and put.")
    return value


def _npc_aggressive(npc):
    if npc["combat_maximum_health"] is None:
        return False
    behavior = _json_properties(npc["behaviors_json"])
    return bool(behavior.get("aggressive", not any(
        behavior.get(key) for key in ("friendly", "neutral", "passive")
    )))


def _npc_passive(npc):
    """Passive combat NPCs, such as training dummies, take blows but never strike back."""
    return not _npc_aggressive(npc) and bool(_json_properties(npc["behaviors_json"]).get("passive"))


def _engaged(database, encounter_id, character_id):
    return database.execute(
        "SELECT 1 FROM mud_combat_participants WHERE encounter_id=? AND character_id=? "
        "AND left_at IS NULL", (encounter_id, character_id),
    ).fetchone() is not None


def _npc_stance(database, npc, user_id=None):
    """Hostile, Neutral, or Friendly as this player sees the NPC right now."""
    if _npc_aggressive(npc):
        return "Hostile"
    if user_id is not None and npc["combat_maximum_health"] is not None and not _npc_passive(npc):
        encounter = _encounter_row(database, npc, create=False)
        if (encounter is not None and encounter["status"] == "active"
                and _engaged(database, encounter["id"], f"stationnet:character:{user_id}")):
            return "Hostile"
    return "Friendly" if _json_properties(npc["behaviors_json"]).get("friendly") else "Neutral"


def _active_hostiles(database, room, user_id=None):
    """Aggressive enemies, plus non-aggressive ones this player is already fighting."""
    character_id = f"stationnet:character:{user_id}" if user_id is not None else None
    enemies = []
    for npc in _room_npc_rows(database, room["id"], room.get("instance_id")):
        if npc["combat_maximum_health"] is None:
            continue
        aggressive = _npc_aggressive(npc)
        if not aggressive and (character_id is None or _npc_passive(npc)):
            continue
        encounter = _encounter_row(database, npc, create=aggressive)
        if encounter is None:
            continue
        if aggressive and encounter["status"] in {"ready", "active"}:
            enemies.append((npc, encounter))
        elif (not aggressive and encounter["status"] == "active"
              and _engaged(database, encounter["id"], character_id)):
            enemies.append((npc, encounter))
    return enemies


def _is_hidden(database, user_id):
    row = database.execute(
        "SELECT hidden FROM mud_player_stealth WHERE user_id=?", (user_id,)
    ).fetchone()
    return bool(row and row["hidden"])


def _set_hidden(database, user_id, hidden):
    database.execute(
        "INSERT INTO mud_player_stealth(user_id,hidden) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET hidden=excluded.hidden",
        (user_id, int(hidden)),
    )


def _stealth_chance(agility, detection, bonus=0):
    """Agility and Stealth help hiding; enemy defense doubles as awareness."""
    return max(15, min(95, min(90, 55 + 5 * (agility - 10) - 4 * (detection - 5)) + bonus))


def _hide_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The hide command does not accept arguments.")
    room = _current_room(database, user["id"])
    if room["id"] in {PORTAL_ROOM_ID, "stationnet:death-summary"}:
        raise ActionError("You cannot hide here.")
    if _is_hidden(database, user["id"]):
        raise ActionError("You are already hidden.")
    character = _character_row(database, user["id"])
    enemies = _active_hostiles(database, room, user["id"])
    if any(database.execute(
        "SELECT 1 FROM mud_combat_participants WHERE encounter_id=? "
        "AND character_id=? AND left_at IS NULL LIMIT 1",
        (encounter["id"], character["id"]),
    ).fetchone() for _, encounter in enemies):
        raise ActionError("You cannot hide while fighting. Flee first.")
    detection = max((npc["combat_defense"] for npc, _ in enemies), default=5)
    bonus = _character_effects(database, character["id"])["stealth"]
    practiced = _practice(database, character["id"], "hide")
    if secrets.randbelow(100) >= _stealth_chance(character["agility"], detection, bonus):
        drafts = [EventDraft("You try to hide, but are spotted.", event_type="combat")] + practiced
        return drafts + (_enemy_turn(database, user) if enemies else [])
    _set_hidden(database, user["id"], True)
    return [EventDraft("You slip into hiding.", event_type="combat")] + practiced


def _join_encounter(database, encounter_id, character_id):
    database.execute(
        "INSERT INTO mud_combat_participants(encounter_id,character_id) VALUES(?,?) "
        "ON CONFLICT(encounter_id,character_id) DO UPDATE SET left_at=NULL,"
        "defeated_at=NULL,last_action_at=CURRENT_TIMESTAMP",
        (encounter_id, character_id),
    )
    database.execute(
        "UPDATE mud_combat_encounters SET status='active',"
        "started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND status='ready'", (encounter_id,),
    )


def _addressed(drafts, user_id):
    """Send drafts written for the acting player to another player instead."""
    return [EventDraft(draft.body, draft.event_type, "private", str(user_id), (user_id,))
            if draft.audience_type == "private" and draft.recipient_ids is None else draft
            for draft in drafts]


def _guarding_user(database, encounter, actor_character_id, room):
    """The most recent engaged guard in this room, other than the actor, or None."""
    for row in database.execute(
        "SELECT c.user_id FROM mud_combat_participants AS p JOIN mud_characters AS c "
        "ON c.id=p.character_id WHERE p.encounter_id=? AND p.character_id!=? "
        "AND p.left_at IS NULL AND p.guarding_until>CURRENT_TIMESTAMP "
        "ORDER BY p.guarding_until DESC", (encounter["id"], actor_character_id),
    ).fetchall():
        location = _current_room(database, row["user_id"])
        if location["id"] == room["id"] and location.get("instance_id") == room.get("instance_id"):
            return row["user_id"]
    return None


def _enemy_turn(database, user, defending=False, chosen=None, single=False):
    """Every enemy hostile to the actor acts once; with single, only the first strikes."""
    room = _current_room(database, user["id"])
    enemies = _active_hostiles(database, room, user["id"])
    if not enemies:
        return []
    _set_hidden(database, user["id"], False)
    enemies.sort(key=lambda pair: pair[0]["id"] != chosen)
    character = _character_row(database, user["id"])
    newly_engaged = set()
    for _, encounter in enemies:
        if not _engaged(database, encounter["id"], character["id"]):
            newly_engaged.add(encounter["id"])
        _join_encounter(database, encounter["id"], character["id"])
    drafts = []
    if defending:
        database.execute(
            "UPDATE mud_combat_participants SET guarding_until=datetime('now', ?) "
            "WHERE character_id=? AND left_at IS NULL AND encounter_id IN "
            f"({','.join('?' for _ in enemies)})",
            (f"+{mud_combat.GUARD_SECONDS} seconds", character["id"],
             *[encounter["id"] for _, encounter in enemies]),
        )
        gear = _equipment_combat_totals(database, character_id=character["id"])
        drafts += _practice(database, character["id"], "defend", categories=gear["categories"])
    for index, (npc, encounter) in enumerate(enemies):
        if single and index:
            if encounter["id"] in newly_engaged:
                drafts.append(EventDraft(f"{npc['name']} joins the fight.", event_type="combat"))
            continue
        guard = _guarding_user(database, encounter, character["id"], room)
        if guard is None:
            strike, actor_down = _enemy_strike(database, user, npc, encounter, defending)
            drafts += strike
            if actor_down:
                return drafts
            continue
        guard_name = _character_row(database, guard)["display_name"]
        drafts.append(EventDraft(f"{guard_name} intercepts {npc['name']}'s attack.", event_type="combat"))
        drafts += _addressed(_enemy_strike(database, {"id": guard}, npc, encounter, True)[0], guard)
    # Lingering damage effects tick once per enemy turn, however many enemies act.
    ticks = mud_effects.tick(database, "character", character["id"])
    if ticks:
        character = _character_row(database, user["id"])
        damage = sum(amount for _, amount in ticks)
        health = max(0, character["current_health"] - damage)
        database.execute(
            "UPDATE mud_character_progression SET current_health=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE character_id=? AND track_key=?", (health, character["id"], character["track_key"]),
        )
        drafts.append(EventDraft(
            f"You suffer {damage} damage from {', '.join(key for key, _ in ticks)} "
            f"({health}/{character['maximum_health']} health).", event_type="combat"))
        if health == 0:
            npc, encounter = enemies[0]
            refreshed = database.execute(
                "SELECT * FROM mud_combat_encounters WHERE id=?", (encounter["id"],)
            ).fetchone()
            drafts += _defeat_player(database, user, refreshed, npc)
    return drafts


def _enemy_strike(database, user, npc, encounter, defending=False):
    """One enemy attacks one player; returns the drafts and whether that player fell."""
    character = _character_row(database, user["id"])
    enemy_gear = _equipment_combat_totals(database, npc_definition_id=npc["definition_id"])
    npc_reloading = False
    npc_firearm = next((row for row in database.execute(
        "SELECT d.name,d.properties_json FROM mud_npc_equipment AS e "
        "JOIN mud_item_definitions AS d ON d.id=e.item_definition_id "
        "WHERE e.npc_definition_id=? AND e.equipment_slot IN ('main_hand','off_hand') "
        "ORDER BY CASE e.equipment_slot WHEN 'main_hand' THEN 0 ELSE 1 END",
        (npc["definition_id"],),
    ) if mud_firearms.is_firearm(_json_properties(row["properties_json"]))), None)
    if npc_firearm:
        capacity = _json_properties(npc_firearm["properties_json"])["magazine_size"]
        magazine = database.execute(
            "SELECT loaded_rounds FROM mud_encounter_firearms WHERE encounter_id=?", (encounter["id"],)
        ).fetchone()
        if magazine is None:
            database.execute("INSERT INTO mud_encounter_firearms(encounter_id,loaded_rounds) VALUES(?,?)",
                             (encounter["id"], capacity - 1))
        elif magazine["loaded_rounds"] == 0:
            database.execute("UPDATE mud_encounter_firearms SET loaded_rounds=? WHERE encounter_id=?",
                             (capacity, encounter["id"]))
            npc_reloading = True
        else:
            database.execute("UPDATE mud_encounter_firearms SET loaded_rounds=loaded_rounds-1 WHERE encounter_id=?",
                             (encounter["id"],))
    player_gear = _equipment_combat_totals(database, character_id=character["id"])
    effects = _character_effects(database, character["id"], player_gear)
    outcome = "reload" if npc_reloading else mud_combat.resolve(
        _npc_hit_stat(database, npc, encounter, enemy_gear, "accuracy"),
        _player_evasion(character, player_gear, effects, defending), _hit_roll())
    damage = 0
    if outcome == "hit":
        damage = max(1, npc["combat_attack"] + (2 if npc_firearm else 0) + enemy_gear["attack"]
                     + mud_effects.modifier(database, "encounter", encounter["id"], "attack")
                     - _player_mitigation(character, player_gear, effects))
        damage = _resisted(damage, _origin_profile(database, character["id"])["resistances"].get("physical", 0))
        if defending:
            damage = max(0, (damage - max(0, (character["vitality"] - 10) // 2)) // 3)
    # Only real blows teach defense; dodging teaches evasion. Reloads and misses teach neither.
    practiced = (_practice(database, character["id"], "damage_taken", categories=player_gear["categories"])
                 if damage > 0 else [])
    if outcome == "dodge":
        practiced += _practice(database, character["id"], "dodge")
    health = max(0, character["current_health"] - damage)
    if damage:
        database.execute(
            "UPDATE mud_character_progression SET current_health=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE character_id=? AND track_key=?", (health, character["id"], character["track_key"]),
        )
        database.execute(
            "UPDATE mud_combat_participants SET damage_taken=damage_taken+? "
            "WHERE encounter_id=? AND character_id=?", (damage, encounter["id"], character["id"]),
        )
    status = f"({health}/{character['maximum_health']} health)"
    body = {
        "reload": f"{npc['name']} reloads {npc_firearm['name'] if npc_firearm else ''} {status}.",
        "hit": f"{npc['name']} strikes for {damage} damage {status}.",
        "dodge": f"You dodge {npc['name']}'s attack {status}.",
        "miss": f"{npc['name']} strikes at you but misses {status}.",
    }[outcome]
    drafts = [EventDraft(body, event_type="combat")] + practiced
    if health > 0 and damage > 0:
        resistances = _origin_profile(database, character["id"])["resistances"]
        for spec in _effect_specs(_json_properties(npc["properties_json"]), "on_hit_effects"):
            try:
                resisted = mud_effects.resist(spec, resistances.get(spec.get("key"), 0))
                if resisted is None:
                    drafts.append(EventDraft(f"You shrug off {spec['key']}.", event_type="combat"))
                    continue
                key = mud_effects.apply(database, "character", character["id"], resisted, npc["name"])
                drafts.append(EventDraft(f"{npc['name']} inflicts {key}.", event_type="combat"))
            except (ValueError, TypeError, AttributeError):
                continue
    if health == 0:
        refreshed = database.execute(
            "SELECT * FROM mud_combat_encounters WHERE id=?", (encounter["id"],)
        ).fetchone()
        drafts += _defeat_player(database, user, refreshed, npc)
    return drafts, health == 0


def _entry_attack(database, user):
    room = _current_room(database, user["id"])
    enemies = _active_hostiles(database, room, user["id"])
    if not enemies:
        return []
    if _is_hidden(database, user["id"]):
        character = _character_row(database, user["id"])
        detection = max(npc["combat_defense"] for npc, _ in enemies)
        bonus = _character_effects(database, character["id"])["stealth"]
        if secrets.randbelow(100) < _stealth_chance(character["agility"], detection, bonus):
            return [EventDraft("You pass the enemies unnoticed.", event_type="combat")]
        _set_hidden(database, user["id"], False)
        noticed = [EventDraft("An enemy spots you!", event_type="combat")]
    else:
        noticed = []
    # Only the first enemy gets a free strike; the rest join in and act from the next turn.
    return noticed + [EventDraft(f"{enemies[0][0]['name']} notices you and attacks!", event_type="combat")] + \
        _enemy_turn(database, user, chosen=enemies[0][0]["id"], single=True)


def _defeat_player(database, user, encounter, npc):
    _set_hidden(database, user["id"], False)
    character = _character_row(database, user["id"])
    mud_effects.clear(database, "character", character["id"])
    old_room = _current_room(database, user["id"])
    # Death never heals fully, so it is never a better potion than resting.
    health = mud_combat.recovery_amount(character["maximum_health"], mud_combat.DEATH_RECOVERY_PERCENT)
    mana = mud_combat.recovery_amount(character["maximum_mana"], mud_combat.DEATH_RECOVERY_PERCENT)
    # Procedural deaths already cost the run's loot; world deaths cost some level progress instead.
    lost_experience = 0 if old_room.get("instance_id") else mud_combat.death_experience_loss(
        character["experience"], experience_for_level(character["level"]))
    if not resource_settings(database, user["id"])["enabled"]:
        mana = character["current_mana"]
    database.execute(
        "UPDATE mud_character_progression SET current_health=?,current_mana=?,experience=experience-?,"
        "updated_at=CURRENT_TIMESTAMP WHERE character_id=? AND track_key=?",
        (health, mana, lost_experience, character["id"], character["track_key"]),
    )
    _leave_combat(database, user["id"], encounter["scope_type"], encounter["scope_key"], True)
    if old_room.get("instance_id"):
        instance_id = old_room["instance_id"]
        run = database.execute(
            "SELECT join_code,theme_id,difficulty,room_count FROM mud_procedural_instances "
            "WHERE id=?", (instance_id,),
        ).fetchone()
        participant = database.execute(
            "SELECT rooms_visited FROM mud_expedition_participants "
            "WHERE instance_id=? AND user_id=?", (instance_id, user["id"]),
        ).fetchone()
        lost_items = database.execute(
            "SELECT COUNT(*) FROM mud_item_instances WHERE owner_character_id=? "
            "AND acquired_run_id=?", (character["id"], instance_id),
        ).fetchone()[0]
        summary = (
            f"DEATH SUMMARY\n{npc['name']} defeated you in {old_room['name']}.\n"
            f"{_run_label(database, instance_id)} {run['join_code']} · {_run_theme_name(database, instance_id)} · "
            f"difficulty {run['difficulty']}\n"
            f"Rooms visited: {participant['rooms_visited']}/{run['room_count']}\n"
            f"Run loot lost: {lost_items} item{'s' if lost_items != 1 else ''}. "
            "Previously owned items and earned experience remain.\n"
            f"Enter /continue to return to {_return_label(database, instance_id)}."
        )
        failed = database.execute(
            "UPDATE mud_expedition_participants SET status='failed',finished_at=CURRENT_TIMESTAMP "
            "WHERE instance_id=? AND user_id=? AND status='active'",
            (instance_id, user["id"]),
        ).rowcount
        if failed:
            participant = database.execute(
                "SELECT rooms_visited FROM mud_expedition_participants "
                "WHERE instance_id=? AND user_id=?", (instance_id, user["id"]),
            ).fetchone()
            _record_daily_result(
                database, instance_id, user["id"], "failed", participant["rooms_visited"]
            )
        _settle_run_loot(database, user["id"], instance_id, keep=False)
        database.execute(
            "INSERT INTO mud_death_summaries(user_id,instance_id,summary) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET instance_id=excluded.instance_id,"
            "summary=excluded.summary,created_at=CURRENT_TIMESTAMP",
            (user["id"], instance_id, summary),
        )
        _finish_empty_expedition(database, instance_id, "failure")
        return [
            EventDraft(summary, event_type="defeat"),
            EventDraft(
                f"{character['display_name']} is defeated by {npc['name']} and leaves the run.",
                event_type="defeat", audience_type="instance", audience_key=instance_id,
                recipient_ids=_instance_participant_ids(database, instance_id, user["id"]),
            ),
        ]
    start = _start_room(database)
    _set_player_location(
        database, user["id"], "traditional", "world_room", start["id"]
    )
    return [
        EventDraft(
            f"{npc['name']} defeats you. You awaken in {start['name']}, battered "
            f"({health}/{character['maximum_health']} health)."
            + (f" You lose {lost_experience} experience." if lost_experience else "")
            + " Rest to recover.",
            event_type="defeat",
        ),
        EventDraft(
            f"{character['display_name']} is defeated by {npc['name']} and vanishes to safety.",
            event_type="defeat", audience_type="room", audience_key=old_room["id"],
            recipient_ids=_occupant_ids(database, old_room["id"], user["id"]),
        ),
    ]


def _attack_action(database, user, parameters):
    target = _combat_target(parameters)
    if not target:
        raise ActionError("Attack what?")
    room = _current_room(database, user["id"])
    room_npcs = _room_npc_rows(database, room["id"], room.get("instance_id"))
    npc = _npc_match(room_npcs, target)
    display_name = next(label for row, label in _numbered_npcs(room_npcs) if row["id"] == npc["id"])
    if npc["combat_maximum_health"] is None:
        raise ActionError(f"{npc['name']} is not hostile.")
    encounter = _encounter_row(database, npc)
    if encounter["status"] == "defeated":
        raise ActionError(f"{npc['name']} is defeated and has not yet recovered.", 409)
    spell_damage = parameters.get("_spell_damage")
    firearm = None
    if spell_damage is None and not parameters.get("_melee"):
        equipped_gun = _equipped_firearm(database, user["id"])
        if parameters.get("_firearm") and equipped_gun is None:
            raise ActionError("Equip a firearm in either hand before firing.")
        if equipped_gun is not None and _loaded_rounds(equipped_gun) > 0:
            firearm = equipped_gun
        elif parameters.get("_firearm"):
            raise ActionError(f"{equipped_gun['name']} is empty. Use /reload first.")
    character = _character_row(database, user["id"])
    database.execute(
        "INSERT INTO mud_combat_participants(encounter_id,character_id) VALUES(?,?) "
        "ON CONFLICT(encounter_id,character_id) DO UPDATE SET left_at=NULL,"
        "defeated_at=NULL,last_action_at=CURRENT_TIMESTAMP",
        (encounter["id"], character["id"]),
    )
    player_gear = _equipment_combat_totals(database, character_id=character["id"])
    enemy_gear = _equipment_combat_totals(database, npc_definition_id=npc["definition_id"])
    effects = _character_effects(database, character["id"], player_gear)
    surprise = _is_hidden(database, user["id"])
    _set_hidden(database, user["id"], False)
    firearm_attack = 0
    if firearm:
        firearm_properties = _json_properties(firearm["properties_json"])
        firearm_attack = max(0, _integer_property(firearm_properties, "damage", "attack", "attack_bonus"))
        firearm_category = _item_category(firearm_properties)
        if firearm_category and firearm_category not in _proficiencies(database, character["id"]):
            firearm_attack = _untrained(firearm_attack)
    weapon_attack = firearm_attack if firearm else player_gear["attack"]
    weapon_stat = character["agility"] if firearm else character["strength"]
    style_bonus = effects["firearm_damage"] if firearm else effects["melee_damage"]
    bonuses = level_bonuses(character["level"])
    damage = (max(1, spell_damage + character["intellect"] // 3 + effects["spell_power"] + bonuses["spell_power"]
                  - (npc["combat_defense"] + enemy_gear["defense"]
                     + mud_effects.modifier(database, "encounter", encounter["id"], "defense")) // 2)
              if spell_damage is not None else
               max(1, (6 if firearm else 4) + weapon_stat // 2
                   + weapon_attack + _equipment_stat_bonus({"attack": weapon_attack}, weapon_stat, "attack")
                  + effects["attack"] + style_bonus + bonuses["attack"] - (npc["combat_defense"] + enemy_gear["defense"]
                     + mud_effects.modifier(database, "encounter", encounter["id"], "defense")) // 2))
    if surprise:
        # An unaware target cannot dodge, so ambushes always land.
        damage += max(2, character["agility"] // 2)
        outcome = "hit"
    else:
        kind = "spell" if spell_damage is not None else "firearm" if firearm else "melee"
        evasion = _npc_hit_stat(database, npc, encounter, enemy_gear, "evasion")
        # Magic is harder to sidestep: spells face only half of the target's evasion.
        outcome = mud_combat.resolve(_player_accuracy(character, player_gear, effects, kind),
                                     evasion // 2 if kind == "spell" else evasion, _hit_roll())
    if outcome != "hit":
        damage = 0
    practiced = (_practice(database, character["id"],
                           "spell_cast" if spell_damage is not None else "firearm_hit" if firearm else "melee_hit",
                           categories=player_gear["categories"]) if outcome == "hit" else [])
    if firearm:
        state = _item_state(firearm)
        state["loaded"] = _loaded_rounds(firearm) - 1
        _save_item_state(database, firearm["id"], state)
    lingering = sum(amount for _, amount in mud_effects.tick(database, "encounter", encounter["id"]))
    damage += lingering
    remaining = max(0, encounter["current_health"] - damage)
    database.execute(
        "UPDATE mud_combat_encounters SET status=?,current_health=?,"
        "started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP,"
        "ended_at=CASE WHEN ?=0 THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=? "
        "AND status IN ('ready','active')",
        ("defeated" if remaining == 0 else "active", remaining, remaining, encounter["id"]),
    )
    database.execute(
        "UPDATE mud_combat_participants SET damage_dealt=damage_dealt+?,"
        "last_action_at=CURRENT_TIMESTAMP WHERE encounter_id=? AND character_id=?",
        (damage, encounter["id"], character["id"]),
    )
    recipients = set(_occupant_ids(database, room["id"], instance_id=room.get("instance_id"))); recipients.add(user["id"])
    audience_type = "instance" if room.get("instance_id") else "room"
    audience_key = room.get("instance_id") or room["id"]
    verb = ('casts at' if spell_damage is not None else 'fires at' if firearm else
            'surprises' if surprise else 'attacks')
    status = f"({remaining}/{encounter['maximum_health']} health)"
    if outcome == "hit":
        body = f"{character['display_name']} {verb} {display_name} for {damage} damage {status}."
    else:
        noun = "spell" if spell_damage is not None else "shot" if firearm else "attack"
        body = (f"{display_name} dodges {character['display_name']}'s {noun}" if outcome == "dodge" else
                f"{character['display_name']} {verb} {display_name} but misses")
        body += (f"; lingering effects deal {lingering} damage {status}." if lingering else f" {status}.")
    drafts = [EventDraft(
        body, event_type="combat", audience_type=audience_type, audience_key=audience_key,
        recipient_ids=tuple(sorted(recipients)),
    )] + practiced
    if remaining > 0 and outcome == "hit":
        applications = parameters.get("_spell_effects") if spell_damage is not None else []
        if spell_damage is None:
            facts = character_creation.origin_facts(database, character["id"])
            for equipped in ([firearm] if firearm else _inventory_rows(database, user["id"], True)):
                if not _meets_item_requirements(_json_properties(equipped["properties_json"]), character, facts):
                    continue
                if not firearm and mud_firearms.is_firearm(_json_properties(equipped["properties_json"])):
                    continue
                applications += _effect_specs(_json_properties(equipped["properties_json"]), "on_hit_effects")
        for spec in applications:
            try:
                key = mud_effects.apply(database, "encounter", encounter["id"], spec, character["display_name"])
                drafts.append(EventDraft(f"{display_name} suffers {key}.", event_type="combat"))
            except (ValueError, TypeError):
                continue
    if remaining == 0:
        mud_effects.clear(database, "encounter", encounter["id"])
        defeated = database.execute(
            "SELECT * FROM mud_combat_encounters WHERE id=?", (encounter["id"],),
        ).fetchone()
        drafts.append(EventDraft(
            f"{display_name} is defeated.", event_type="victory", audience_type=audience_type,
            audience_key=audience_key, recipient_ids=tuple(sorted(recipients)),
        ))
        if room.get("instance_id"):
            database.execute(
                "INSERT INTO mud_dungeon_content_state(content_id,status) VALUES(?,'defeated') "
                "ON CONFLICT(content_id) DO UPDATE SET status='defeated',updated_at=CURRENT_TIMESTAMP",
                (npc["dungeon_content_id"],),
            )
        drafts += _encounter_reward_drafts(database, defeated, npc)
        return drafts
    return drafts + _enemy_turn(database, user, chosen=npc["id"])


def _fire_action(database, user, parameters):
    return _attack_action(database, user, {**parameters, "_firearm": True})


def _melee_action(database, user, parameters):
    return _attack_action(database, user, {**parameters, "_melee": True})


def _reload_action(database, user, parameters):
    arguments = parameters.get("arguments") or []
    equipped = list(_inventory_rows(database, user["id"], equipped=True))
    if arguments:
        firearm = _item_matches(equipped, " ".join(arguments))
        if not mud_firearms.is_firearm(_json_properties(firearm["properties_json"])):
            raise ActionError("That item is not a firearm.")
    else:
        firearm = _equipped_firearm(database, user["id"])
    if firearm is None:
        raise ActionError("Equip a firearm before reloading.")
    properties = _json_properties(firearm["properties_json"])
    loaded = _loaded_rounds(firearm)
    needed = properties["magazine_size"] - loaded
    if needed <= 0:
        raise ActionError(f"{firearm['name']} is already fully loaded.")
    ammunition = next((item for item in _inventory_rows(database, user["id"])
                       if _json_properties(item["properties_json"]).get("ammo_type") == properties["ammo_type"]
                       and not mud_firearms.is_firearm(_json_properties(item["properties_json"]))
                       and _remaining_ammo(item) > 0), None)
    if ammunition is None:
        raise ActionError(f"You have no {properties['ammo_type']} ammunition.")
    available = _remaining_ammo(ammunition)
    count = min(needed, available)
    firearm_state = _item_state(firearm)
    firearm_state["loaded"] = loaded + count
    _save_item_state(database, firearm["id"], firearm_state)
    if count == available:
        database.execute("DELETE FROM mud_item_instances WHERE id=?", (ammunition["id"],))
    else:
        ammo_state = _item_state(ammunition)
        ammo_state["rounds"] = available - count
        _save_item_state(database, ammunition["id"], ammo_state)
    return [EventDraft(f"You reload {firearm['name']} with {count} rounds ({loaded + count}/{properties['magazine_size']}).", event_type="item")] + _enemy_turn(database, user)


def _defend_action(database, user, _parameters):
    if not _active_hostiles(database, _current_room(database, user["id"]), user["id"]):
        raise ActionError("There is no hostile enemy to defend against.")
    return [EventDraft("You brace for the next blow.", event_type="combat")] + \
        _enemy_turn(database, user, defending=True)


def _flee_action(database, user, _parameters):
    room = _current_room(database, user["id"])
    if not _active_hostiles(database, room, user["id"]):
        raise ActionError("There is no hostile enemy to flee from.")
    character = _character_row(database, user["id"])
    chance = _flee_chance(character["agility"], _character_effects(database, character["id"])["flee"])
    practiced = _practice(database, character["id"], "flee")
    if secrets.randbelow(100) >= chance:
        return [EventDraft("Your escape fails.", event_type="combat")] + practiced + _enemy_turn(database, user)
    drafts = [EventDraft("You slip past the enemy, but it lashes out as you flee.", event_type="combat")] + practiced
    # A clean escape draws one parting strike, not a blow from every enemy in the room.
    drafts += _enemy_turn(database, user, single=True)
    if _current_room(database, user["id"])["id"] != room["id"]:
        return drafts
    instance_id = room.get("instance_id")
    if instance_id:
        if room["role"] == "entrance":
            drafts.append(EventDraft("You reach the entrance, but there is no route out. Find the dungeon exit to secure your loot.", event_type="combat"))
            return drafts
        exits = _room_exits(database, room["id"], instance_id)
        candidates = [database.execute(
            "SELECT depth FROM mud_dungeon_rooms WHERE instance_id=? AND room_key=?",
            (instance_id, exit_row["to_room_id"]),
        ).fetchone()["depth"] for exit_row in exits]
        chosen = exits[candidates.index(min(candidates))]
        _leave_combat(database, user["id"], "instance", instance_id)
        _set_player_location(database, user["id"], _run_mode(database, instance_id), "dungeon_instance",
                             chosen["to_room_id"], instance_id)
        _record_expedition_visit(database, instance_id, user["id"], chosen["to_room_id"])
        drafts.append(EventDraft(f"You flee {chosen['direction']}.", event_type="movement"))
        return drafts + [_room_entry_event(database, user)] + _entry_triggers(database, user) + \
            _entry_attack(database, user)
    exits = _room_exits(database, room["id"], viewer_id=user["id"])
    if not exits:
        return drafts
    chosen = exits[0]
    _leave_combat(database, user["id"], "room", room["id"])
    _set_player_location(database, user["id"], "traditional", "world_room", chosen["to_room_id"])
    return drafts + [EventDraft(f"You flee {chosen['direction']}.", event_type="movement"),
                     _room_entry_event(database, user)] + _entry_triggers(database, user) + \
        _entry_attack(database, user)


def _combat_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The combat command does not accept arguments.")
    state = combat_state(database, user["id"])
    if not state["enemies"]:
        body = "There are no hostile enemies here."
    else:
        body = "Nearby enemies:\n" + "\n".join(
            f"- {enemy['display_name']}: {enemy['status']} · "
            f"{enemy['health']}/{enemy['maximum_health']} health"
            for enemy in state["enemies"]
        )
    if state["hidden"]:
        body = "You are hidden.\n" + body
    return [EventDraft(body, event_type="combat")]


SPELLS = {
    "ember": {"cost": 8, "damage": 9, "effects": [{"key": "burning", "potency": 2, "duration": 3}]},
    "venom": {"cost": 7, "damage": 5, "effects": [{"key": "poison", "potency": 3, "duration": 3}]},
    "rend": {"cost": 6, "damage": 7, "effects": [{"key": "bleeding", "potency": 2, "duration": 3}]},
    "hex": {"cost": 7, "damage": 2, "effects": [{"key": "weakness", "potency": 2, "duration": 3},
                                                   {"key": "vulnerability", "potency": 2, "duration": 3}]},
    "mend": {"cost": 7, "heal": 20},
    "ward": {"cost": 6, "effects": [{"key": "ward", "potency": 3, "duration": 3}]},
    "might": {"cost": 6, "effects": [{"key": "might", "potency": 3, "duration": 3}]},
}


def _known_spells(database, character_id):
    """Class spells plus any innate race abilities."""
    return tuple(_origin_profile(database, character_id)["spells"])


def _require_resource(database, user):
    resource = resource_settings(database, user["id"])
    if not resource["enabled"]:
        raise ActionError("This world does not use spells.")
    return resource


def _spells_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The spells command does not accept arguments.")
    resource = _require_resource(database, user)
    character = _character_row(database, user["id"])
    known = _known_spells(database, character["id"])
    lines = [f"{name}: {SPELLS[name]['cost']} {resource['noun']}" for name in known if name in SPELLS]
    return [EventDraft("Known spells:\n" + ("\n".join(lines) or "none"), event_type="character")]


def _cast_action(database, user, parameters):
    resource = _require_resource(database, user)
    args = parameters.get("arguments") or []
    if not args:
        raise ActionError("Cast which spell? Use /spells to see known spells.")
    name = args[0].casefold()
    character = _character_row(database, user["id"])
    if name not in _known_spells(database, character["id"]) or name not in SPELLS:
        raise ActionError("You do not know that spell.")
    spell = SPELLS[name]
    if character["current_mana"] < spell["cost"]:
        raise ActionError(f"You do not have enough {resource['noun']}.")
    target = " ".join(args[1:])
    if "damage" in spell:
        if not target:
            raise ActionError(f"Cast {name} at which enemy?")
        room = _current_room(database, user["id"])
        npc = _npc_match(_room_npc_rows(database, room["id"], room.get("instance_id")), target)
        if npc["combat_maximum_health"] is None or _encounter_row(database, npc)["status"] == "defeated":
            raise ActionError("That enemy cannot be targeted.")
    elif target:
        raise ActionError(f"{name} is a self-targeted spell.")
    if "heal" in spell and character["current_health"] >= character["maximum_health"]:
        raise ActionError("Your health is already full.")
    database.execute(
        "UPDATE mud_character_progression SET current_mana=current_mana-?,updated_at=CURRENT_TIMESTAMP "
        "WHERE character_id=? AND track_key=?",
        (spell["cost"], character["id"], character["track_key"]),
    )
    if "damage" in spell:
        return _attack_action(database, user, {"arguments": args[1:], "_spell_damage": spell["damage"],
                                               "_spell_effects": spell.get("effects", [])})
    drafts = []
    if "heal" in spell:
        restored = min(spell["heal"] + character["intellect"] // 2
                       + _character_effects(database, character["id"])["healing"]
                       + level_bonuses(character["level"])["healing"],
                       character["maximum_health"] - character["current_health"])
        database.execute("UPDATE mud_character_progression SET current_health=current_health+? "
                         "WHERE character_id=? AND track_key=?",
                         (restored, character["id"], character["track_key"]))
        drafts.append(EventDraft(f"You cast {name} and restore {restored} health.", event_type="combat"))
        drafts += _practice(database, character["id"], "heal_cast")
    for spec in spell.get("effects", []):
        key = mud_effects.apply(database, "character", character["id"], spec, name)
        drafts.append(EventDraft(f"You cast {name} and gain {key}.", event_type="combat"))
    return drafts + _enemy_turn(database, user)


def _rest_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The rest command does not accept arguments.")
    if _active_hostiles(database, _current_room(database, user["id"]), user["id"]):
        raise ActionError("You cannot rest while enemies are nearby.")
    character = _character_row(database, user["id"])
    resource = resource_settings(database, user["id"])
    if (character["current_health"] >= character["maximum_health"]
            and (not resource["enabled"] or character["current_mana"] >= character["maximum_mana"])):
        raise ActionError("You are already fully rested.")
    wait = database.execute(
        "SELECT CAST(? - (julianday('now') - julianday(last_rested_at)) * 86400 AS INTEGER) "
        "FROM mud_character_progression WHERE character_id=? AND track_key=? AND last_rested_at IS NOT NULL",
        (mud_combat.REST_COOLDOWN_SECONDS, character["id"], character["track_key"]),
    ).fetchone()
    if wait is not None and wait[0] > 0:
        raise ActionError(f"You need a moment before resting again ({wait[0]}s).", 409)
    # Dungeons are hostile ground, so a breather there restores less than a rest in the world.
    mode = "procedural" if _current_room(database, user["id"]).get("instance_id") else "traditional"
    health_percent, mana_percent = mud_combat.REST_PERCENT[mode]
    health = min(character["maximum_health"] - character["current_health"],
                 mud_combat.recovery_amount(character["maximum_health"], health_percent))
    mana = min(character["maximum_mana"] - character["current_mana"],
               mud_combat.recovery_amount(character["maximum_mana"], mana_percent)) if resource["enabled"] else 0
    database.execute(
        "UPDATE mud_character_progression SET current_health=current_health+?,current_mana=current_mana+?,"
        "last_rested_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE character_id=? AND track_key=?",
        (health, mana, character["id"], character["track_key"]),
    )
    # Resting lets one turn of lingering effects wear off, harmful ones included.
    ticks = mud_effects.tick(database, "character", character["id"])
    harm = min(sum(amount for _, amount in ticks), character["current_health"] + health - 1)
    if harm > 0:
        database.execute(
            "UPDATE mud_character_progression SET current_health=current_health-? "
            "WHERE character_id=? AND track_key=?", (harm, character["id"], character["track_key"]),
        )
    rested = _character_row(database, user["id"])
    noun = resource["noun"]
    summary = (f"You rest and recover {health} health and {mana} {noun} "
               f"({rested['current_health']}/{rested['maximum_health']} health, "
               f"{rested['current_mana']}/{rested['maximum_mana']} {noun})." if resource["enabled"] else
               f"You rest and recover {health} health "
               f"({rested['current_health']}/{rested['maximum_health']} health).")
    return [EventDraft(
        summary + (f" Lingering effects cost you {harm} health." if harm else ""),
        event_type="character")]


def _look_action(database, user, parameters):
    arguments = parameters.get("arguments", [])
    target = parameters.get("target", "")
    if arguments:
        target = " ".join(str(item) for item in arguments).strip()
    room = _current_room(database, user["id"])
    description = examinations.resolve(database, user, room, str(target).strip())
    return [EventDraft(description, event_type="room")]


MAP_WIDTH = 15
MAP_HEIGHT = 9


def _map_action(database, user, parameters):
    """An ASCII view of the surrounding grid; it shows the land but never moves anyone."""
    room = _current_room(database, user["id"])
    if room.get("instance_id") or room.get("system_managed") or room.get("x") is None:
        return [EventDraft("No map is available here.", event_type="room")]
    window = mud_wilderness.map_window(database, room["world_id"], room["x"], room["y"], room["z"],
                                       MAP_WIDTH, MAP_HEIGHT, hide_users=(user["id"],))
    x1, y1 = window["origin"]
    lines = [[window["palette"][index]["glyph"] for index in row] for row in window["grid"]]
    for x, y in window["players"]:
        lines[y - y1][x - x1] = "P"
    lines[room["y"] - y1][room["x"] - x1] = "@"
    shown = sorted({index for row in window["grid"] for index in row if index > 1})
    legend = ["@ you", "P another player"] + (["* landmark"] if any(1 in row for row in window["grid"]) else [])
    legend += [f"{window['palette'][index]['glyph']} {window['palette'][index]['name']}" for index in shown]
    layer = f", layer {room['z']}" if room["z"] else ""
    text = (f"Map around {room['name']} ({room['x']}, {room['y']}{layer}); north is up.\n"
            + "\n".join("".join(line).rstrip() for line in lines) + "\n" + " · ".join(legend))
    return [EventDraft(text, event_type="map")]


def _search_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("Use /search to search this room.")
    room = _current_room(database, user["id"])
    if room.get("instance_id"):
        raise ActionError("There is nothing to search here.")
    character = _character_row(database, user["id"])
    hidden = database.execute(
        "SELECT d.* FROM mud_room_discoveries d WHERE d.room_id=? AND NOT EXISTS "
        "(SELECT 1 FROM mud_player_discoveries p WHERE p.character_id=? AND p.discovery_id=d.id) "
        "ORDER BY d.id", (room["id"], character["id"])).fetchall()
    # Only well-hidden discoveries are a real challenge, so only they teach Perception.
    practiced = (_practice(database, character["id"], "search")
                 if any(row["difficulty"] for row in hidden) else [])
    score = _character_effects(database, character["id"])["search"] + 2 * (character["intellect"] - 10)
    found = next((row for row in hidden if not row["difficulty"] or
                  secrets.randbelow(100) < _skill_check_chance(score, row["difficulty"])), None)
    if found is None:
        return [EventDraft("You sense something more here, but it eludes you for now." if hidden else
                           "You search carefully but find nothing new.", event_type="room")] + practiced
    database.execute("INSERT INTO mud_player_discoveries(character_id,discovery_id) VALUES(?,?)",
                     (character["id"], found["id"]))
    drafts = [EventDraft(found["clue"], event_type="room")] + practiced
    if found["item_definition_id"]:
        item = database.execute("SELECT name FROM mud_item_definitions WHERE id=?",
                                (found["item_definition_id"],)).fetchone()
        if item:
            _grant_item(database, user["id"], found["item_definition_id"], "discovery:item")
            drafts.append(EventDraft(f"You discover {item['name']}.", event_type="item"))
            drafts += _set_objective_progress(database, user, "possess_item", found["item_definition_id"])
    return drafts


def _skill_check_chance(score, difficulty):
    """Shared search and lockpicking odds: 50% when score matches difficulty."""
    return max(5, min(95, 50 + score - difficulty))


def _room_entry_event(database, user):
    return _look_action(database, user, {})[0]


def _item_target(parameters):
    return str(parameters.get("item_id") or " ".join(str(x) for x in parameters.get("arguments", []))).strip()


def _get_action(database, user, parameters):
    target = _item_target(parameters)
    if not target:
        raise ActionError("Get what?")
    room = _current_room(database, user["id"])
    if _active_hostiles(database, room, user["id"]):
        raise ActionError("A hostile enemy blocks the loot. Fight or flee first.", 409)
    container_name = str(parameters.get("container_id", "")).strip()
    if not container_name and re.search(r"\s+from\s+", target, re.I):
        target, container_name = re.split(r"\s+from\s+", target, maxsplit=1, flags=re.I)
    container = _container_target(database, user["id"], room, container_name) if container_name else None
    source = (_container_rows(database, container["id"], user["id"]) if container else
              _room_item_rows(database, room["id"], room.get("instance_id")))
    if container and not container["is_open"]:
        raise ActionError("Open that container first.")
    item = _item_matches(source, target)
    if _json_properties(item["properties_json"]).get("fixed") is True:
        raise ActionError(f"{item['name']} is fixed in place.")
    character = _character_row(database, user["id"])
    if isinstance(item, dict) and item.get("dungeon_content_id"):
        claimed = database.execute(
            "INSERT INTO mud_dungeon_content_state(content_id,status,claimed_by) "
            "VALUES(?,'claimed',?) ON CONFLICT(content_id) DO UPDATE SET "
            "status='claimed',claimed_by=excluded.claimed_by,updated_at=CURRENT_TIMESTAMP "
            "WHERE mud_dungeon_content_state.status='available'",
            (item["dungeon_content_id"], user["id"]),
        ).rowcount
        if not claimed:
            raise ActionError("That item was already claimed by another explorer.", 409)
        _grant_item(database, user["id"], item["definition_id"], "expedition:item")
    else:
        database.execute(
            "UPDATE mud_item_instances SET room_id=NULL,container_id=NULL,owner_character_id=?,"
            "equipped_slot=NULL,possession_mode=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (character["id"], _current_mode(database, user["id"]), item["id"]),
        )
        spawn = database.execute("SELECT * FROM mud_item_spawns WHERE live_instance_id=?",
                                 (item["id"],)).fetchone()
        if spawn:
            database.execute("UPDATE mud_item_spawns SET live_instance_id=NULL WHERE id=?", (spawn["id"],))
            due = _scheduled_now(datetime.now(timezone.utc) + timedelta(seconds=spawn["respawn_seconds"]))
            database.execute("INSERT INTO mud_scheduled_events(event_kind,entity_id,due_at) VALUES('item_respawn',?,?) "
                             "ON CONFLICT(event_kind,entity_id) DO UPDATE SET due_at=excluded.due_at",
                             (spawn["id"], due))
    return [EventDraft(f"You pick up {item['name']}.", event_type="item"),
            _room_item_event(database, user, room, f"{_character_row(database, user['id'])['display_name']} picks up {item['name']}.")] + \
        _set_objective_progress(database, user, "possess_item", item["definition_id"]) + \
        fire_triggers(database, user, "take_item", [item["id"], item["definition_id"]],
                      instance_id=room.get("instance_id"))


def _drop_action(database, user, parameters):
    target = _item_target(parameters)
    if not target:
        raise ActionError("Drop what?")
    item = _item_matches(_inventory_rows(database, user["id"]), target)
    room = _current_room(database, user["id"])
    if room["id"] == PORTAL_ROOM_ID or room.get("instance_id"):
        raise ActionError("Items cannot be dropped in protected procedural locations.")
    database.execute("DELETE FROM mud_character_equipment WHERE item_instance_id=?", (item["id"],))
    database.execute("UPDATE mud_item_instances SET room_id=?,owner_character_id=NULL,container_id=NULL,equipped_slot=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (room["id"], item["id"]))
    return [EventDraft(f"You drop {item['name']}.", event_type="item"),
            _room_item_event(database, user, room, f"{_character_row(database, user['id'])['display_name']} drops {item['name']}.")]


def _open_action(database, user, parameters):
    target = _item_target(parameters)
    room = _current_room(database, user["id"])
    if not target:
        raise ActionError("Open what?")
    item = _container_target(database, user["id"], room, target)
    if item["is_open"]:
        return [EventDraft(f"{item['name']} is already open.\n" + _describe_item(database, item, user["id"]), event_type="item")]
    database.execute("UPDATE mud_item_instances SET is_open=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item["id"],))
    refreshed = _item_row(database, item["id"])
    return [EventDraft(f"You open {item['name']}.\n" + _describe_item(database, refreshed, user["id"]), event_type="item"),
            _room_item_event(database, user, room, f"{_character_row(database, user['id'])['display_name']} opens {item['name']}.")]


def _close_action(database, user, parameters):
    target = _item_target(parameters)
    if not target:
        raise ActionError("Close what?")
    room = _current_room(database, user["id"])
    item = _container_target(database, user["id"], room, target)
    if not item["is_open"]:
        return [EventDraft(f"{item['name']} is already closed.", event_type="item")]
    database.execute("UPDATE mud_item_instances SET is_open=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item["id"],))
    return [EventDraft(f"You close {item['name']}.", event_type="item"),
            _room_item_event(database, user, room, f"{_character_row(database, user['id'])['display_name']} closes {item['name']}.")]


def _put_action(database, user, parameters):
    args = parameters.get("arguments", [])
    target = str(parameters.get("item_id") or " ".join(str(arg) for arg in args)).strip()
    container_name = str(parameters.get("container_id", "")).strip()
    if not container_name:
        match = re.fullmatch(r"(.+?)\s+in(?:to)?\s+(.+)", target, re.I)
        if match:
            target, container_name = match.group(1), match.group(2)
    if not target or not container_name:
        raise ActionError("Use /put <item> in <container>.")
    room = _current_room(database, user["id"])
    container = _container_target(database, user["id"], room, container_name)
    if not container["is_open"]:
        raise ActionError("Open that container first.")
    item = _item_matches(_inventory_rows(database, user["id"]), target)
    if item["acquired_run_id"]:
        raise ActionError("Run loot cannot be put in a container before completing the dungeon.")
    if item["id"] == container["id"]:
        raise ActionError("An item cannot contain itself.")
    database.execute("DELETE FROM mud_character_equipment WHERE item_instance_id=?", (item["id"],))
    database.execute("UPDATE mud_item_instances SET room_id=NULL,owner_character_id=NULL,container_id=?,equipped_slot=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (container["id"], item["id"]))
    return [EventDraft(f"You put {item['name']} in {container['name']}.", event_type="item"),
            _room_item_event(database, user, room, f"{_character_row(database, user['id'])['display_name']} puts {item['name']} in {container['name']}.")]


def _equip_action(database, user, parameters):
    if _active_hostiles(database, _current_room(database, user["id"]), user["id"]):
        raise ActionError("You cannot change equipment while an enemy is attacking.")
    target = _item_target(parameters)
    item = _item_matches(_inventory_rows(database, user["id"]), target)
    if item["item_type"] != "equipment" or not item["definition_slot"]:
        raise ActionError("That item cannot be equipped.")
    character = _character_row(database, user["id"])
    try:
        requirements = _item_requirements(_json_properties(item["properties_json"]))
    except ValueError as problem:
        raise ActionError(f"{item['name']} has invalid equipment requirements.") from problem
    missing = mud_conditions.requirement_problems(
        requirements, character, character_creation.origin_facts(database, character["id"]))
    if missing:
        raise ActionError(f"{item['name']} requires {', '.join(missing)}.")
    slot = item["definition_slot"]
    loadout = _inventory_context(database, user["id"])["loadout"]
    database.execute(
        "DELETE FROM mud_character_equipment WHERE character_id=? AND loadout_key=? "
        "AND (slot=? OR item_instance_id=?)",
        (character["id"], loadout, slot, item["id"]),
    )
    database.execute(
        "INSERT INTO mud_character_equipment(character_id,loadout_key,slot,item_instance_id) "
        "VALUES(?,?,?,?)", (character["id"], loadout, slot, item["id"]),
    )
    room = _current_room(database, user["id"])
    drafts = [EventDraft(f"You equip {item['name']} ({slot.replace('_', ' ')}).", event_type="item"),
              _room_item_event(database, user, room, f"{character['display_name']} equips {item['name']}.")]
    category = _item_category(_json_properties(item["properties_json"]))
    if category and category not in _proficiencies(database, character["id"]):
        drafts.append(EventDraft(
            f"You are untrained with {category} gear, so {item['name']} gives only three quarters of its "
            "bonuses. Learn a skill with that proficiency to use it fully (see /skills).", event_type="item"))
    return drafts


def _unequip_action(database, user, parameters):
    if _active_hostiles(database, _current_room(database, user["id"]), user["id"]):
        raise ActionError("You cannot change equipment while an enemy is attacking.")
    target = _item_target(parameters)
    item = _item_matches(_inventory_rows(database, user["id"], True), target)
    character = _character_row(database, user["id"])
    loadout = _inventory_context(database, user["id"])["loadout"]
    database.execute(
        "DELETE FROM mud_character_equipment WHERE character_id=? AND loadout_key=? "
        "AND item_instance_id=?", (character["id"], loadout, item["id"]),
    )
    room = _current_room(database, user["id"])
    return [EventDraft(f"You unequip {item['name']}.", event_type="item"),
            _room_item_event(database, user, room, f"{character['display_name']} unequips {item['name']}.")]


def _use_action(database, user, parameters):
    target = _item_target(parameters)
    carried = list(_inventory_rows(database, user["id"])) + list(_inventory_rows(database, user["id"], True))
    try:
        item = _item_matches(carried, target)
    except ActionError:
        # /use also works on objects in the room, such as a lever or an altar.
        room = _current_room(database, user["id"])
        if target and (room.get("instance_id") or not room.get("system_managed")):
            try:
                _item_matches(_room_item_rows(database, room["id"], room.get("instance_id")), target)
            except ActionError:
                pass
            else:
                return _interact_action(database, user, {**parameters, "arguments": [":use", target]})
        raise
    room = _current_room(database, user["id"])
    script = fire_triggers(database, user, "use_object", [item["id"], item["definition_id"]],
                           instance_id=room.get("instance_id"))
    try:
        return _use_item_effect(database, user, item) + script
    except ActionError:
        if script:
            return script
        raise


def _use_item_effect(database, user, item):
    properties = _json_properties(item["properties_json"])
    use_effects = properties.get("on_use_effects", [])
    if use_effects:
        if not isinstance(use_effects, list):
            raise ActionError("This item's effects are invalid.")
        character = _character_row(database, user["id"])
        try:
            keys = [mud_effects.validate(spec)[0] for spec in use_effects]
        except ValueError as problem:
            raise ActionError(f"This item's effects are invalid: {problem}") from problem
        for spec in use_effects:
            mud_effects.apply(database, "character", character["id"], spec, item["name"])
        restored = 0
        heal_value = properties.get("heal")
        if item["item_type"] == "consumable" and type(heal_value) is int and heal_value > 0:
            restored = min(heal_value, character["maximum_health"] - character["current_health"])
            if restored:
                database.execute(
                    "UPDATE mud_character_progression SET current_health=current_health+? "
                    "WHERE character_id=? AND track_key=?",
                    (restored, character["id"], character["track_key"]),
                )
        if item["item_type"] == "consumable":
            database.execute("DELETE FROM mud_item_instances WHERE id=?", (item["id"],))
        message = f"You use {item['name']} and gain {', '.join(keys)}"
        if restored:
            message += f", restoring {restored} health"
        return [EventDraft(message + ".", event_type="item")] + _enemy_turn(database, user)
    heal = properties.get("heal")
    if not isinstance(heal, int) or isinstance(heal, bool) or heal <= 0:
        if item["item_type"] == "consumable" and any(
            word in item["name"].casefold() for word in ("healing", "potion", "tonic", "elixir")
        ):
            heal = 30
        elif item["item_type"] == "consumable" and "standard recovery rules apply" in str(
            properties.get("use_message", "")
        ).casefold():
            heal = 20
        else:
            heal = 0
    if heal and item["item_type"] == "consumable":
        character = _character_row(database, user["id"])
        restored = min(heal, character["maximum_health"] - character["current_health"])
        if restored <= 0:
            raise ActionError("Your health is already full.")
        database.execute(
            "UPDATE mud_character_progression SET current_health=current_health+?,"
            "updated_at=CURRENT_TIMESTAMP WHERE character_id=? AND track_key=?",
            (restored, character["id"], character["track_key"]),
        )
        database.execute("DELETE FROM mud_item_instances WHERE id=?", (item["id"],))
        drafts = [EventDraft(f"You use {item['name']} and restore {restored} health.", event_type="item")]
        drafts += _enemy_turn(database, user)
        return drafts
    if item["item_type"] == "consumable" and properties.get("reveal_exit") is True:
        room = _current_room(database, user["id"])
        instance_id = room.get("instance_id")
        if not instance_id:
            raise ActionError("Use the survey chart inside a dungeon.")
        exit_room = database.execute(
            "SELECT room_key FROM mud_dungeon_rooms WHERE instance_id=? AND role='exit'",
            (instance_id,),
        ).fetchone()["room_key"]
        queue = [(room["id"], None)]
        visited = {room["id"]}
        direction = None
        for room_key, first_step in queue:
            if room_key == exit_room:
                direction = first_step
                break
            for route in _room_exits(database, room_key, instance_id):
                destination = route["to_room_id"]
                if destination not in visited:
                    visited.add(destination)
                    queue.append((destination, first_step or route["direction"]))
        database.execute("DELETE FROM mud_item_instances WHERE id=?", (item["id"],))
        message = (f"The chart points {direction} toward the exit." if direction else
                   "The chart marks this room as the exit.")
        return [EventDraft(message, event_type="item")] + _enemy_turn(database, user)
    message = properties.get("use_message")
    if not isinstance(message, str) or not message.strip():
        raise ActionError(f"{item['name']} has no usable effect yet.")
    if item["item_type"] == "consumable":
        database.execute("DELETE FROM mud_item_instances WHERE id=?", (item["id"],))
    return [EventDraft(message.strip(), event_type="item")] + _enemy_turn(database, user)


def _dialogue_node(database, node_id):
    node = database.execute(
        "SELECT n.*,c.name AS conversation_name FROM mud_dialogue_nodes AS n "
        "JOIN mud_conversations AS c ON c.id=n.conversation_id WHERE n.id=?", (node_id,),
    ).fetchone()
    if node is None:
        raise ActionError("That conversation is no longer available.")
    responses = database.execute(
        "SELECT * FROM mud_dialogue_responses WHERE node_id=? "
        "ORDER BY sort_order,id", (node_id,),
    ).fetchall()
    return node, responses


def _npc_player_state(database, character_id, npc_id, dungeon=False):
    if dungeon:
        state_row = database.execute(
            "SELECT state_json FROM mud_dungeon_npc_player_states "
            "WHERE character_id=? AND content_id=?", (character_id, npc_id),
        ).fetchone()
    else:
        state_row = database.execute(
            "SELECT state_json FROM mud_npc_player_states WHERE character_id=? AND npc_instance_id=?",
            (character_id, npc_id),
        ).fetchone()
    try:
        state = json.loads(state_row["state_json"]) if state_row else {}
    except json.JSONDecodeError:
        state = {}
    return state if isinstance(state, dict) else {}


def _delivery_objective(database, user, definition_id, npc_definition_id=None):
    """The first open, unlocked delivery of this item whose recipient stands here."""
    room = _current_room(database, user["id"])
    if room.get("instance_id"):
        return None
    character = _character_row(database, user["id"])
    present = {row["definition_id"] for row in database.execute(
        "SELECT definition_id FROM mud_npc_instances WHERE room_id=?", (room["id"],))}
    if npc_definition_id is not None:
        present &= {npc_definition_id}
    for objective in database.execute(
        "SELECT o.id,o.quest_id,o.recipient_npc_definition_id FROM mud_quest_objectives o "
        "JOIN mud_player_quests q ON q.quest_id=o.quest_id "
        "LEFT JOIN mud_player_objectives p ON p.objective_id=o.id AND p.character_id=q.character_id "
        "WHERE q.character_id=? AND q.status='active' AND o.objective_type='deliver_item' "
        "AND o.target_id=? AND p.completed_at IS NULL ORDER BY q.accepted_at,o.sort_order,o.id",
        (character["id"], definition_id),
    ).fetchall():
        recipients = ({objective["recipient_npc_definition_id"]} if objective["recipient_npc_definition_id"]
                      else {row["npc_definition_id"] for row in database.execute(
                          "SELECT npc_definition_id FROM mud_quest_givers WHERE quest_id=?",
                          (objective["quest_id"],))})
        if recipients & present and _unlocked_objective(database, character["id"], objective["id"]):
            return objective
    return None


def _response_visible(database, user, context, response, npc_definition_id, dungeon):
    if response["required_state_key"] and str(
            context.state(response["required_state_key"])) != response["required_state_value"]:
        return False
    context.response_id = response["id"]
    if not mud_conditions.evaluate(response["conditions_json"], context):
        return False
    # Quest actions only appear when they can actually happen, so dialogue can
    # neither re-offer a taken quest nor accept an item nobody is waiting for.
    if response["accept_quest_id"]:
        quest = database.execute("SELECT * FROM mud_quests WHERE id=?",
                                 (response["accept_quest_id"],)).fetchone()
        if dungeon or quest is None or _quest_blocker(database, user, quest):
            return False
    if response["turn_in_quest_id"]:
        quest = database.execute("SELECT * FROM mud_quests WHERE id=?",
                                 (response["turn_in_quest_id"],)).fetchone()
        if (dungeon or quest is None or quest["completion_mode"] != "turn_in"
                or context.quest_status(quest["id"]) != "ready"
                or npc_definition_id not in _turn_in_npc_ids(database, quest)):
            return False
    if response["deliver_item_id"]:
        if dungeon or not context.item_count(response["deliver_item_id"]) or _delivery_objective(
                database, user, response["deliver_item_id"], npc_definition_id) is None:
            return False
    return True


def _available_dialogue_responses(database, character_id, npc_id, node_id,
                                  dungeon=False):
    node, responses = _dialogue_node(database, node_id)
    owner = database.execute("SELECT user_id FROM mud_characters WHERE id=?", (character_id,)).fetchone()
    user = {"id": owner["user_id"]}
    if dungeon:
        npc_definition_id = None
    else:
        npc = database.execute("SELECT definition_id FROM mud_npc_instances WHERE id=?", (npc_id,)).fetchone()
        npc_definition_id = npc["definition_id"] if npc else None
    context = _ConditionContext(database, user["id"], npc_id, dungeon)
    visible = [response for response in responses
               if _response_visible(database, user, context, response, npc_definition_id, dungeon)]
    return node, visible


def _dialogue_text(npc_name, node, responses):
    body = f"{npc_name}: {node['body']}"
    if responses:
        body += "\n\n" + "\n".join(
            f"{index}. {response['response_text']}" for index, response in enumerate(responses, 1)
        )
    else:
        body += "\n\nThe conversation ends."
    return body


def _quest_hints(database, user, room, npc, responses=()):
    """Lines telling the player what quest business this NPC has with them."""
    if room.get("instance_id"):
        return []
    in_dialogue = {response[key] for response in responses
                   for key in ("accept_quest_id", "turn_in_quest_id") if response[key]}
    offered = [quest["name"] for quest in _available_quests(database, user, room["id"])
               if quest["id"] not in in_dialogue and database.execute(
                   "SELECT 1 FROM mud_quest_givers WHERE quest_id=? AND npc_definition_id=?",
                   (quest["id"], npc["definition_id"])).fetchone()]
    character = _character_row(database, user["id"])
    ready = [quest["name"] for quest in database.execute(
        "SELECT q.* FROM mud_player_quests p JOIN mud_quests q ON q.id=p.quest_id "
        "WHERE p.character_id=? AND p.status='active' AND p.ready_at IS NOT NULL "
        "AND q.completion_mode='turn_in' ORDER BY q.name COLLATE NOCASE", (character["id"],))
        if quest["id"] not in in_dialogue and npc["definition_id"] in _turn_in_npc_ids(database, quest)]
    lines = []
    if offered:
        lines.append("Quests offered: " + ", ".join(offered) + " — use /quest accept <name>.")
    if ready:
        lines.append("Ready to turn in: " + ", ".join(ready) + " — use /quest turnin <name>.")
    return lines


def _talk_action(database, user, parameters):
    target = str(parameters.get("npc_id") or " ".join(str(x) for x in parameters.get("arguments", []))).strip()
    if not target:
        raise ActionError("Talk to whom?")
    room = _current_room(database, user["id"])
    npc = _npc_match(_room_npc_rows(database, room["id"], room.get("instance_id")), target)
    node = database.execute(
        "SELECT id FROM mud_dialogue_nodes WHERE conversation_id=? AND is_start=1",
        (npc["conversation_id"],),
    ).fetchone() if npc["conversation_id"] else None
    if node is None:
        hints = _quest_hints(database, user, room, npc)
        if not hints:
            raise ActionError(f"{npc['name']} has nothing to say right now.")
        events = [EventDraft(f"{npc['name']} has business with you.\n" + "\n".join(hints),
                             event_type="dialogue")]
        if not room.get("instance_id"):
            events += _set_objective_progress(database, user, "talk_npc", npc["definition_id"])
        return events
    character = _character_row(database, user["id"])
    if room.get("instance_id"):
        database.execute(
            "INSERT INTO mud_dungeon_conversation_sessions"
            "(character_id,instance_id,content_id,node_id) VALUES(?,?,?,?) "
            "ON CONFLICT(character_id) DO UPDATE SET instance_id=excluded.instance_id,"
            "content_id=excluded.content_id,node_id=excluded.node_id,updated_at=CURRENT_TIMESTAMP",
            (character["id"], room["instance_id"], npc["id"], node["id"]),
        )
        database.execute(
            "DELETE FROM mud_conversation_sessions WHERE character_id=?", (character["id"],)
        )
    else:
        database.execute(
            "INSERT INTO mud_conversation_sessions(character_id,npc_instance_id,node_id) VALUES(?,?,?) "
            "ON CONFLICT(character_id) DO UPDATE SET npc_instance_id=excluded.npc_instance_id,"
            "node_id=excluded.node_id,updated_at=CURRENT_TIMESTAMP",
            (character["id"], npc["id"], node["id"]),
        )
        database.execute(
            "DELETE FROM mud_dungeon_conversation_sessions WHERE character_id=?", (character["id"],)
        )
    node, responses = _available_dialogue_responses(
        database, character["id"], npc["id"], node["id"], bool(room.get("instance_id"))
    )
    body = _dialogue_text(npc["name"], node, responses)
    hints = _quest_hints(database, user, room, npc, responses)
    if hints:
        body += "\n\n" + "\n".join(hints)
    events = [EventDraft(body, event_type="dialogue")]
    events += _set_objective_progress(database, user, "talk_npc", npc["definition_id"])
    return events


def _apply_dialogue_outcomes(database, user, response):
    """Grant a response's outcomes once per character; later choices give nothing."""
    try:
        outcomes = mud_conditions.validate_outcomes(response["outcomes_json"])
    except ValueError:
        return []
    if not outcomes:
        return []
    character = _character_row(database, user["id"])
    if database.execute("SELECT 1 FROM mud_player_dialogue_claims WHERE character_id=? AND response_id=?",
                        (character["id"], response["id"])).fetchone():
        return []
    return _settle_outcomes(database, user, outcomes, lambda: database.execute(
        "INSERT OR IGNORE INTO mud_player_dialogue_claims(character_id,response_id) VALUES(?,?)",
        (character["id"], response["id"])).rowcount > 0)


def _settle_outcomes(database, user, outcomes, claim=None, event_type="dialogue"):
    """Pay an outcome list's costs, then grant its rewards; claim() may veto first."""
    character = _character_row(database, user["id"])
    # Check every cost before writing anything so a failed payment changes nothing.
    handed = []
    for outcome in outcomes:
        if outcome["type"] == "take_item":
            handed += _handover_items(database, user, outcome["item"], outcome["amount"])
    cost = sum(outcome["amount"] for outcome in outcomes if outcome["type"] == "take_coins")
    if cost > _wallet(database, character["id"]):
        raise ActionError(f"You need {cost} coins for that.")
    if claim is not None and not claim():
        return []
    events = []
    if handed:
        for item in handed:
            database.execute("DELETE FROM mud_item_instances WHERE id=? AND owner_character_id=?",
                             (item["id"], character["id"]))
        events.append(EventDraft("You hand over " + ", ".join(item["name"] for item in handed) + ".",
                                 event_type=event_type))
    if cost:
        database.execute("UPDATE mud_character_wallets SET coins=coins-? WHERE character_id=?",
                         (cost, character["id"]))
        events.append(EventDraft(f"You pay {cost} coins.", event_type=event_type))
    kinds = {"give_item": "item", "experience": "experience", "coins": "coins"}
    received = _grant_rewards(database, user, [
        {"reward_type": kinds[outcome["type"]], "target_id": outcome.get("item"), "amount": outcome["amount"]}
        for outcome in outcomes if outcome["type"] in kinds])
    if received:
        events.append(EventDraft(f"You receive {', '.join(received)}.", event_type="reward"))
    for outcome in outcomes:
        if outcome["type"] == "give_item":
            events += _set_objective_progress(database, user, "possess_item", outcome["item"])
    return events


def _respond_action(database, user, parameters):
    character = _character_row(database, user["id"])
    session_row = database.execute(
        "SELECT s.node_id,i.id AS npc_id,i.room_id,i.definition_id,d.name FROM mud_conversation_sessions AS s "
        "JOIN mud_npc_instances AS i ON i.id=s.npc_instance_id "
        "JOIN mud_npc_definitions AS d ON d.id=i.definition_id WHERE s.character_id=?",
        (character["id"],),
    ).fetchone()
    room = _current_room(database, user["id"])
    dungeon_session = None
    if room.get("instance_id"):
        raw = database.execute(
            "SELECT s.node_id,s.content_id AS npc_id,c.room_key,c.definition_id,c.snapshot_json "
            "FROM mud_dungeon_conversation_sessions AS s "
            "JOIN mud_dungeon_content AS c ON c.id=s.content_id "
            "WHERE s.character_id=? AND s.instance_id=?",
            (character["id"], room["instance_id"]),
        ).fetchone()
        if raw is not None:
            snapshot = _json_properties(raw["snapshot_json"])
            dungeon_session = dict(raw)
            dungeon_session["name"] = snapshot.get("name", "NPC")
            session_row = dungeon_session
    if session_row is None or session_row["room_id" if not dungeon_session else "room_key"] != room["id"]:
        raise ActionError("Start a nearby conversation with /talk first.")
    node, responses = _available_dialogue_responses(
        database, character["id"], session_row["npc_id"], session_row["node_id"],
        bool(dungeon_session),
    )
    target = str(parameters.get("response_id") or " ".join(str(x) for x in parameters.get("arguments", []))).strip()
    matches = [response for response in responses if response["id"] == target
               or response["response_text"].casefold() == target.casefold()]
    if target.isdigit() and 1 <= int(target) <= len(responses):
        matches = [responses[int(target) - 1]]
    if len(matches) != 1:
        raise ActionError("Choose one of the current dialogue responses.")
    chosen = matches[0]
    events = [EventDraft(f"You: {chosen['response_text']}", event_type="dialogue")]
    events += _apply_dialogue_outcomes(database, user, chosen)
    if chosen["state_key"]:
        state_table = ("mud_dungeon_npc_player_states" if dungeon_session
                       else "mud_npc_player_states")
        identity_column = "content_id" if dungeon_session else "npc_instance_id"
        state = _npc_player_state(database, character["id"], session_row["npc_id"], bool(dungeon_session))
        state[chosen["state_key"]] = chosen["state_value"]
        database.execute(
            f"INSERT INTO {state_table}(character_id,{identity_column},state_json) VALUES(?,?,?) "
            f"ON CONFLICT(character_id,{identity_column}) DO UPDATE SET state_json=excluded.state_json,"
            "updated_at=CURRENT_TIMESTAMP",
            (character["id"], session_row["npc_id"], json.dumps(state, separators=(",", ":"), sort_keys=True)),
        )
    if chosen["accept_quest_id"] and not dungeon_session:
        # Accepting no longer counts as speaking to the giver; talk objectives
        # advance only when the player starts a conversation with /talk.
        events += _accept_quest(database, user, chosen["accept_quest_id"])
    if chosen["deliver_item_id"] and not dungeon_session:
        matching_items = [item for item in _inventory_rows(database, user["id"])
                          if item["definition_id"] == chosen["deliver_item_id"]]
        if not matching_items:
            raise ActionError("You do not carry the item requested in that response.")
        events += _deliver_quest_item(database, user, matching_items[0], session_row["definition_id"])
    if chosen["turn_in_quest_id"] and not dungeon_session:
        events += _turn_in_quest(database, user, chosen["turn_in_quest_id"], session_row["definition_id"])
    if not dungeon_session:
        events += fire_triggers(database, user, "dialogue_choice", [chosen["id"]])
    if chosen["next_node_id"]:
        session_table = ("mud_dungeon_conversation_sessions" if dungeon_session
                         else "mud_conversation_sessions")
        database.execute(
            f"UPDATE {session_table} SET node_id=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE character_id=?", (chosen["next_node_id"], character["id"]),
        )
        next_node, next_responses = _available_dialogue_responses(
            database, character["id"], session_row["npc_id"], chosen["next_node_id"],
            bool(dungeon_session),
        )
        events.append(EventDraft(_dialogue_text(
            session_row["name"], next_node, next_responses
        ), event_type="dialogue"))
    else:
        database.execute(
            f"DELETE FROM {'mud_dungeon_conversation_sessions' if dungeon_session else 'mud_conversation_sessions'} "
            "WHERE character_id=?", (character["id"],)
        )
        events.append(EventDraft("The conversation ends.", event_type="dialogue"))
    return events


QUEST_USAGE = ("Use /quests, /quests completed, /quest available, /quest info <name>, "
               "/quest accept <name>, /quest turnin <name>, or /quest abandon <name>.")


def _match_quest(quests, target, missing):
    key = " ".join(target.casefold().split())
    matches = [quest for quest in quests if quest["id"] == target
               or " ".join(quest["name"].casefold().split()) == key]
    if not key or len(matches) != 1:
        raise ActionError(missing)
    return matches[0]


def _objective_line(objective):
    mark = "✓" if objective["completed_at"] else ("🔒" if objective["locked"] else "·")
    suffix = " (optional)" if objective["optional"] else ""
    return f"{mark} {objective['description']} ({objective['progress']}/{objective['required_count']}){suffix}"


def _quest_lines(quest):
    lines = [f"\n{quest['name']} — {quest['description']}"]
    if quest["ready"]:
        lines.append(f"READY — return to {quest['turn_in_npc_name']} to turn it in.")
    lines += [_objective_line(objective) for objective in quest["objectives"]]
    return lines


def _quests_action(database, user, parameters):
    args = [str(value) for value in parameters.get("arguments", [])]
    command = args[0].casefold() if args else ""
    target = " ".join(args[1:]).strip()
    room = _current_room(database, user["id"])
    state = quest_state(database, user["id"])
    if command == "accept":
        available = _available_quests(database, user, room["id"])
        quest = _match_quest(available, target, "That quest is not currently available from a nearby NPC.")
        return _accept_quest(database, user, quest["id"])
    if command in ("turnin", "turn-in", "complete"):
        quest = _match_quest(state["active"], target, "You have no active quest by that name.")
        return _turn_in_quest(database, user, quest["id"])
    if command == "abandon":
        quest = _match_quest(state["active"], target, "You have no active quest by that name.")
        row = database.execute("SELECT * FROM mud_quests WHERE id=?", (quest["id"],)).fetchone()
        return _abandon_quest(database, user, row)
    if command == "available" and not target:
        available = _available_quests(database, user, room["id"]) if not room.get("instance_id") else []
        if not available:
            return [EventDraft("No one nearby has a quest for you right now.", event_type="quest")]
        lines = ["Quests available here:"]
        for quest in available:
            givers = {row["npc_definition_id"] for row in database.execute(
                "SELECT npc_definition_id FROM mud_quest_givers WHERE quest_id=?", (quest["id"],))}
            lines.append(f"\n{quest['name']} (from {_npc_names(database, givers)}) — {quest['description']}")
        lines.append("\nUse /quest accept <name>.")
        return [EventDraft("\n".join(lines), event_type="quest")]
    if command == "info":
        known = state["active"] + state["completed"]
        try:
            quest = _match_quest(known, target, "")
        except ActionError:
            offered = _available_quests(database, user, room["id"])
            quest = _match_quest(offered, target, "You know of no quest by that name.")
            return [EventDraft(f"{quest['name']} — {quest['description']}\nAvailable here: /quest accept {quest['name']}.",
                               event_type="quest")]
        lines = _quest_lines(quest)
        lines[0] = lines[0].lstrip()
        if quest["status"] == "completed":
            lines.insert(1, "Completed.")
        elif quest["abandonable"]:
            lines.append(f"Use /quest abandon {quest['name']} to give it up.")
        return [EventDraft("\n".join(lines), event_type="quest")]
    if command == "completed" and not target:
        if not state["completed"]:
            return [EventDraft("You have not completed any quests yet.", event_type="quest")]
        return [EventDraft("Completed quests:\n" + "\n".join(f"✓ {quest['name']}" for quest in state["completed"]),
                           event_type="quest")]
    if args:
        raise ActionError(QUEST_USAGE)
    if not state["active"]:
        body = "You have no active quests."
    else:
        lines = ["Active quests:"]
        for quest in state["active"]:
            lines += _quest_lines(quest)
        body = "\n".join(lines)
    return [EventDraft(body, event_type="quest")]


def _deliver_action(database, user, parameters):
    target = " ".join(str(value) for value in parameters.get("arguments", [])).strip()
    if not target:
        raise ActionError("Use /deliver <item> while a quest giver is nearby.")
    room = _current_room(database, user["id"])
    if room.get("instance_id"):
        raise ActionError("Deliveries are available in traditional worlds.")
    item = _item_matches(list(_inventory_rows(database, user["id"])), target)
    return _deliver_quest_item(database, user, item)


def _deliver_quest_item(database, user, item, npc_definition_id=None):
    character = _character_row(database, user["id"])
    objective = _delivery_objective(database, user, item["definition_id"], npc_definition_id)
    if objective is None:
        raise ActionError("No one nearby is waiting for that item.")
    if item["acquired_run_id"]:
        raise ActionError("Extract from the active run before delivering its loot.")
    if database.execute("SELECT 1 FROM mud_item_instances WHERE container_id=?", (item["id"],)).fetchone():
        raise ActionError("Empty the container before delivering it.")
    database.execute("DELETE FROM mud_item_instances WHERE id=? AND owner_character_id=?",
                     (item["id"], character["id"]))
    return [EventDraft(f"You deliver {item['name']}.", event_type="quest")] + \
        _set_objective_progress(database, user, "deliver_item", item["definition_id"], objective["id"])


def _exits_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The exits command does not accept arguments.")
    room = _current_room(database, user["id"])
    exits = _room_state(database, user["id"])["exits"]
    if not exits:
        body = f"There are no visible exits from {room['name']}."
    else:
        body = "Available exits:\n" + "\n".join(
            f"- {_player_exit_label(database, user['id'], exit_row)}" for exit_row in exits
        )
    return [EventDraft(body, event_type="room")]


def _transition_location(database, user, destination_mode, destination_type,
                         destination_key, route):
    character = character_state(database, user["id"])
    old_room = _current_room(database, user["id"])
    old_recipients = _occupant_ids(database, old_room["id"], user["id"])
    _leave_combat(database, user["id"], "room", old_room["id"])
    _set_player_location(
        database, user["id"], destination_mode, destination_type, destination_key
    )
    new_room = _current_room(database, user["id"])
    new_recipients = _occupant_ids(database, new_room["id"], user["id"])
    return [
        EventDraft(
            f"You go {route}.",
            event_type="movement",
        ),
        _room_entry_event(database, user),
        EventDraft(
            f"{character['name']} leaves via {route}.", event_type="departure",
            audience_type="room", audience_key=old_room["id"],
            recipient_ids=old_recipients,
        ),
        EventDraft(
            f"{character['name']} arrives.", event_type="arrival",
            audience_type="room", audience_key=new_room["id"],
            recipient_ids=new_recipients,
        ),
    ] + _entry_triggers(database, user)


def _portal_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The portal command does not accept arguments.")
    modes = _mode_settings(database)
    room = _current_room(database, user["id"])
    if not modes["procedural"]:
        raise ActionError("Procedural dungeon mode is disabled.")
    if room["id"] == PORTAL_ROOM_ID:
        return [EventDraft(PORTAL_ROOM_INSTRUCTIONS, event_type="portal")]
    if room["id"] != _start_room(database)["id"]:
        raise ActionError("The Dungeon Portals can only be entered from the world's starting room.")
    return _transition_location(
        database, user, "procedural", "portal", PORTAL_ROOM_ID, "through the portal threshold"
    )


def _continue_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The continue command does not accept arguments.")
    pending = database.execute(
        "SELECT instance_id FROM mud_death_summaries WHERE user_id=?", (user["id"],)
    ).fetchone()
    if pending is None:
        raise ActionError("/continue is only available on the death summary screen.")
    database.execute("DELETE FROM mud_death_summaries WHERE user_id=?", (user["id"],))
    _leave_run_location(database, user["id"], pending["instance_id"])
    room = _current_room(database, user["id"])
    return [EventDraft(
        f"You recover in {room['name']}.\n\n"
        f"{_describe_room(database, room, user['id'])}",
        event_type="portal" if room["id"] == PORTAL_ROOM_ID else "room",
    )]


def _world_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The world command does not accept arguments.")
    modes = _mode_settings(database)
    room = _current_room(database, user["id"])
    if room.get("instance_id"):
        raise ActionError("Reach the dungeon exit and /complete the run, or /abandon it, to leave.")
    if room["id"] != PORTAL_ROOM_ID:
        raise ActionError("You are already in the traditional world.")
    if not modes["traditional"]:
        raise ActionError("The traditional world is disabled.")
    saved = database.execute(
        "SELECT room_id FROM mud_world_locations WHERE user_id=?", (user["id"],),
    ).fetchone()
    destination = saved["room_id"] if saved else None
    if destination is None or database.execute(
        "SELECT 1 FROM mud_world_rooms WHERE id=?", (destination,),
    ).fetchone() is None:
        destination = _start_room(database)["id"]
    return _transition_location(
        database, user, "traditional", "world_room", destination, "back to the traditional world"
    )


def _instance_participant_ids(database, instance_id, exclude_user_id=None):
    parameters = [instance_id]
    exclusion = ""
    if exclude_user_id is not None:
        exclusion = " AND user_id<>?"
        parameters.append(exclude_user_id)
    return tuple(row["user_id"] for row in database.execute(
        "SELECT user_id FROM mud_expedition_participants WHERE instance_id=? "
        "AND status='active'" + exclusion + " ORDER BY joined_at,user_id", parameters,
    ).fetchall())


def _finish_empty_expedition(database, instance_id, reason):
    if database.execute(
        "SELECT 1 FROM mud_expedition_participants WHERE instance_id=? AND status='active'",
        (instance_id,),
    ).fetchone() is None:
        database.execute(
            "UPDATE mud_procedural_instances SET status='abandoned',terminal_reason=?,"
            "ended_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status='active'", (reason, instance_id),
        )


def _record_expedition_visit(database, instance_id, user_id, room_key):
    database.execute(
        "INSERT OR IGNORE INTO mud_expedition_room_visits(instance_id,room_key,user_id) "
        "VALUES(?,?,?)", (instance_id, room_key, user_id),
    )
    database.execute(
        "UPDATE mud_expedition_participants SET current_room_key=?,rooms_visited=("
        "SELECT COUNT(*) FROM mud_expedition_room_visits WHERE instance_id=? AND user_id=?"
        ") WHERE instance_id=? AND user_id=? AND status='active'",
        (room_key, instance_id, user_id, instance_id, user_id),
    )


def _enter_expedition(database, user, instance_id, room_key):
    _set_player_location(
        database, user["id"], _run_mode(database, instance_id), "dungeon_instance", room_key, instance_id
    )
    _record_expedition_visit(database, instance_id, user["id"], room_key)
    return _entry_triggers(database, user) + _entry_attack(database, user)


def _installed_expedition_themes(database, strict=False):
    try:
        discovered = dungeons.playable_templates(database)
    except dungeons.TemplateError as problem:
        if strict:
            raise ActionError(f"Installed dungeon themes need attention: {problem}") from problem
        return []
    valid = []
    for theme_id, (path, template) in discovered.items():
        errors = dungeons.validate_template(template, database)
        if not errors:
            valid.append((theme_id, path, template))
    return valid


def _daily_settings(database):
    row = database.execute(
        "SELECT * FROM mud_daily_dungeon_settings WHERE id=1"
    ).fetchone()
    if row is None:
        raise ActionError("Dungeon of the Day has not been configured.", 503)
    return row


def _daily_day_key(settings, now=None):
    try:
        zone = ZoneInfo(settings["timezone"])
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(zone).date().isoformat()


def _eligible_daily_themes(database, settings):
    installed = _installed_expedition_themes(database, strict=True)
    try:
        configured = json.loads(settings["eligible_theme_ids_json"] or "[]")
    except json.JSONDecodeError as problem:
        raise ActionError("Dungeon of the Day theme eligibility is invalid.", 503) from problem
    allowed = set(configured) if configured else None
    return [item for item in installed if allowed is None or item[0] in allowed]


def ensure_daily_dungeon(database, now=None, retry_failed=False):
    """Return today's immutable station design, creating it once when needed."""
    settings = _daily_settings(database)
    day_key = _daily_day_key(settings, now)
    existing = database.execute(
        "SELECT * FROM mud_daily_dungeons WHERE day_key=?", (day_key,)
    ).fetchone()
    if existing is not None and not (retry_failed and existing["status"] == "failed"):
        return existing
    if existing is not None:
        database.execute("DELETE FROM mud_daily_dungeons WHERE day_key=?", (day_key,))
    selection_seed = hashlib.sha256(
        f"{settings['station_seed']}|{day_key}|theme".encode("utf-8")
    ).hexdigest()
    generation_seed = hashlib.sha256(
        f"{settings['station_seed']}|{day_key}|generation".encode("utf-8")
    ).hexdigest()
    try:
        themes = _eligible_daily_themes(database, settings)
        if not themes:
            raise ActionError("No eligible playable dungeon themes are installed.", 503)
        theme_id, path, template = themes[int(selection_seed, 16) % len(themes)]
        instance_id, _manifest = dungeons.create_instance(
            database, template, generation_seed,
            size=settings["room_count"], layout=settings["layout"],
            difficulty=settings["difficulty"], instance_type="daily", source_name=path.name,
        )
        database.execute(
            "INSERT INTO mud_daily_dungeons(day_key,timezone,design_instance_id,status,theme_id,"
            "theme_version,template_hash,seed) VALUES(?,?,?,'ready',?,?,?,?)",
            (day_key, settings["timezone"], instance_id, theme_id, template["version"],
             dungeons.template_hash(template), generation_seed),
        )
    except (ActionError, dungeons.TemplateError, dungeons.GenerationError) as problem:
        database.execute(
            "INSERT INTO mud_daily_dungeons(day_key,timezone,status,seed,error_message) "
            "VALUES(?,?,'failed',?,?)",
            (day_key, settings["timezone"], generation_seed, str(problem)),
        )
    return database.execute(
        "SELECT * FROM mud_daily_dungeons WHERE day_key=?", (day_key,)
    ).fetchone()


def _daily_participation(database, day_key, user_id):
    return database.execute(
        "SELECT * FROM mud_daily_participation WHERE day_key=? AND user_id=?",
        (day_key, user_id),
    ).fetchone()


def _daily_run_day(database, instance_id):
    row = database.execute(
        "SELECT day_key FROM mud_daily_runs WHERE instance_id=?", (instance_id,)
    ).fetchone()
    return row["day_key"] if row else None


def _record_daily_result(database, instance_id, user_id, result, rooms_visited=0):
    day_key = _daily_run_day(database, instance_id)
    if day_key is None:
        return
    column = {"completed": "completions", "failed": "failures", "abandoned": "abandons"}[result]
    database.execute(
        f"UPDATE mud_daily_participation SET {column}={column}+1,last_result=?,"
        "best_rooms_visited=MAX(best_rooms_visited,?),last_instance_id=?,"
        "last_finished_at=CURRENT_TIMESTAMP WHERE day_key=? AND user_id=?",
        (result, rooms_visited, instance_id, day_key, user_id),
    )


def _daily_status_text(database, user_id, now=None):
    daily = ensure_daily_dungeon(database, now)
    if daily["status"] != "ready":
        return f"Dungeon of the Day {daily['day_key']} is unavailable: {daily['error_message']}"
    settings = _daily_settings(database)
    participation = _daily_participation(database, daily["day_key"], user_id)
    attempts = participation["attempts"] if participation else 0
    completions = participation["completions"] if participation else 0
    failures = participation["failures"] if participation else 0
    reward = "claimed" if participation and participation["rewarded_at"] else "available"
    template_row = database.execute(
        "SELECT template_json FROM mud_dungeon_template_versions "
        "WHERE theme_id=? AND version=?",
        (daily["theme_id"], daily["theme_version"]),
    ).fetchone()
    theme_name = daily["theme_id"]
    if template_row:
        try:
            theme_name = json.loads(template_row["template_json"]).get("name", theme_name)
        except json.JSONDecodeError:
            pass
    history = database.execute(
        "SELECT COALESCE(SUM(attempts),0) AS attempts,COALESCE(SUM(completions),0) AS completions "
        "FROM mud_daily_participation WHERE user_id=?", (user_id,),
    ).fetchone()
    return (
        f"Dungeon of the Day {daily['day_key']} · {theme_name} "
        f"({daily['theme_id']}@{daily['theme_version']}) · "
        f"attempts {attempts}/{settings['max_attempts']} · completions {completions} · "
        f"failures {failures} · first-completion reward {reward}. "
        f"Historical totals: {history['attempts']} attempts, {history['completions']} completions."
    )


def _new_join_code(database):
    for _ in range(20):
        code = secrets.token_hex(3).upper()
        if database.execute(
            "SELECT 1 FROM mud_procedural_instances WHERE join_code=?", (code,),
        ).fetchone() is None:
            return code
    raise ActionError("Could not allocate an expedition code. Try again.", 503)


def _run_label(database, instance_id):
    daily_day = _daily_run_day(database, instance_id)
    if daily_day:
        return f"Dungeon of the Day {daily_day}"
    world_run = _world_run(database, instance_id)
    if world_run is None:
        return "Expedition"
    return f"{world_run['entrance_name'] or 'World dungeon'} run"


def _run_theme_name(database, instance_id):
    """Portal runs name their theme ID; world runs name the builder's template."""
    row = database.execute(
        "SELECT i.theme_id,t.name FROM mud_procedural_instances AS i "
        "LEFT JOIN mud_world_dungeon_templates AS t ON t.theme_id=i.theme_id WHERE i.id=?",
        (instance_id,),
    ).fetchone()
    return row["name"] or row["theme_id"]


def _expedition_status_text(database, user_id):
    run = _active_expedition(database, user_id)
    if run is None:
        return "No active expedition."
    total = run["room_count"] or 0
    active = database.execute(
        "SELECT COUNT(*) AS amount FROM mud_expedition_participants "
        "WHERE instance_id=? AND status='active'", (run["id"],),
    ).fetchone()["amount"]
    label = _run_label(database, run["id"])
    return (f"{label} {run['join_code']} · {_run_theme_name(database, run['id'])} · difficulty "
            f"{run['difficulty']} · {run['rooms_visited']}/{total} rooms visited · "
            f"{active} active explorer{'s' if active != 1 else ''}. "
            "Reach the exit to secure this run's loot and earn the completion reward, "
            "or /abandon to leave without this run's loot.")


def _expedition_action(database, user, parameters):
    room = _current_room(database, user["id"])
    if room["id"] != PORTAL_ROOM_ID:
        raise ActionError("New expeditions can only be launched from the Dungeon Portals.")
    if _active_expedition(database, user["id"]):
        raise ActionError("You already have an active expedition. Resume or abandon it first.")
    args = [str(value) for value in parameters.get("arguments", [])]
    theme_id = str(parameters.get("theme_id", "") or (args[0] if args else "")).strip()
    themes = _installed_expedition_themes(database, strict=True)
    if not theme_id:
        if not themes:
            raise ActionError("No playable dungeon themes are installed.")
        lines = ["Available expedition themes:"]
        for key, _path, template in themes:
            rule = template["generation"]["difficulty"]
            lines.append(f"- {key}: {template['name']} (difficulty {rule['minimum']}-{rule['maximum']}, "
                         f"recommended level {dungeons.recommended_level(rule['minimum'])}-"
                         f"{dungeons.recommended_level(rule['maximum'])})")
        level = _character_row(database, user["id"])["level"]
        lines.append(f"Each difficulty step suits about three character levels; you are level {level}.")
        lines.append("Launch with /expedition <theme> [difficulty] [size].")
        return [EventDraft("\n".join(lines), event_type="portal")]
    matches = [(path, template) for key, path, template in themes if key == theme_id]
    if not matches:
        raise ActionError(f'No playable dungeon theme matches "{theme_id}".')
    path, template = matches[0]
    try:
        difficulty = int(parameters.get("difficulty") or (args[1] if len(args) > 1 else
                         template["generation"]["difficulty"]["default"]))
        size = int(parameters.get("size") or (args[2] if len(args) > 2 else
                   template["generation"]["size"]["default"]))
    except (TypeError, ValueError) as problem:
        raise ActionError("Difficulty and size must be whole numbers.") from problem
    seed = secrets.token_hex(16)
    try:
        instance_id, manifest = dungeons.create_instance(
            database, template, seed, size=size, difficulty=difficulty,
            instance_type="random", source_name=path.name,
        )
    except (dungeons.TemplateError, dungeons.GenerationError) as problem:
        raise ActionError(f"Expedition generation failed: {problem}") from problem
    code, character, entry = _launch_run(database, user, instance_id, manifest)
    return [EventDraft(
        f"Expedition launched. Cooperative join code: {code}."
        f"{_difficulty_advice(character['level'], manifest['difficulty'])}\n\n"
        f"{_describe_room(database, _current_room(database, user['id']), user['id'])}",
        event_type="expedition",
    )] + entry


def _launch_run(database, user, instance_id, manifest, entrance=None):
    """Activate a freshly generated run with its owner inside; world runs record their origin."""
    code = _new_join_code(database)
    database.execute(
        "UPDATE mud_procedural_instances SET status='active',owner_user_id=?,join_code=?,"
        "world_id=?,world_entrance_id=?,return_room_id=?,party_limit=?,"
        "started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
        (user["id"], code,
         entrance["world_id"] if entrance else None, entrance["id"] if entrance else None,
         (entrance["return_room_id"] or entrance["room_id"]) if entrance else None,
         entrance["max_party"] if entrance else None, instance_id),
    )
    character = _character_row(database, user["id"])
    database.execute(
        "INSERT INTO mud_expedition_participants(instance_id,user_id,character_id,current_room_key) "
        "VALUES(?,?,?,?)", (instance_id, user["id"], character["id"], manifest["entrance"]),
    )
    return code, character, _enter_expedition(database, user, instance_id, manifest["entrance"])


def _entrance_match(database, room_id, target, required=True):
    entrances = _room_entrances(database, room_id)
    key = " ".join(str(target or "").casefold().split()).removeprefix("enter ").strip()
    if not key:
        if len(entrances) == 1:
            return entrances[0]
        if not required:
            return None
        if not entrances:
            raise ActionError("There is no dungeon entrance here.")
        raise ActionError("Choose an entrance: " + ", ".join(row["name"] for row in entrances) + ".")
    matches = [row for row in entrances if key in {row["id"].casefold(),
                                                   " ".join(row["name"].casefold().split())}]
    if not matches and required:
        raise ActionError(f'There is no dungeon entrance matching "{target}" here.')
    return matches[0] if matches else None


def _enter_dungeon_action(database, user, parameters):
    """Generate a fresh, private run from a world entrance; its code lets a party join."""
    room = _current_room(database, user["id"])
    if room["id"] == PORTAL_ROOM_ID or room.get("instance_id") or room.get("system_managed"):
        raise ActionError("World dungeon entrances are found in the traditional world.")
    if not _mode_settings(database)["traditional"]:
        raise ActionError("The traditional world is disabled.")
    target = " ".join(str(value) for value in parameters.get("arguments", [])).strip()
    entrance = _entrance_match(database, room["id"], target)
    if _active_expedition(database, user["id"]):
        raise ActionError("You already have an active dungeon run. /resume or /abandon it first.")
    if not _exit_open_to(database, user["id"], entrance):
        raise ActionError(_exit_barred_message(entrance))
    if _active_hostiles(database, room, user["id"]) and not _is_hidden(database, user["id"]):
        raise ActionError("A hostile enemy blocks your route. Fight or attempt to flee.", 409)
    from . import mud_world_dungeons
    template_row = database.execute(
        "SELECT * FROM mud_world_dungeon_templates WHERE id=?", (entrance["template_id"],),
    ).fetchone()
    template = mud_world_dungeons.effective_template(template_row)
    try:
        instance_id, manifest = dungeons.create_instance(
            database, template, secrets.token_hex(16), size=entrance["size"],
            layout=entrance["layout"], difficulty=entrance["difficulty"],
            instance_type="random", source_name=f"world:{entrance['world_id']}",
        )
    except (dungeons.TemplateError, dungeons.GenerationError) as problem:
        raise ActionError(f"The way into {entrance['name']} will not open: {problem}") from problem
    character_name = character_state(database, user["id"])["name"]
    old_recipients = _occupant_ids(database, room["id"], user["id"])
    _leave_combat(database, user["id"], "room", room["id"])
    code, character, entry = _launch_run(database, user, instance_id, manifest, entrance)
    party = ("" if entrance["max_party"] == 1 else
             f" Party code: {code} — up to {entrance['max_party'] - 1} more explorer"
             f"{'s' if entrance['max_party'] != 2 else ''} can /join {code} from {room['name']}.")
    return [
        EventDraft(f"You enter {entrance['name']}.{party}"
                   f"{_difficulty_advice(character['level'], manifest['difficulty'])}\n\n"
                   f"{_describe_room(database, _current_room(database, user['id']), user['id'])}",
                   event_type="expedition"),
        EventDraft(f"{character_name} enters {entrance['name']}.", event_type="departure",
                   audience_type="room", audience_key=room["id"], recipient_ids=old_recipients),
    ] + entry


def end_world_runs(database, world_id, message):
    """End a world's live dungeon runs, losing their loot, when the world stops being active."""
    for run in database.execute(
        "SELECT id FROM mud_procedural_instances WHERE world_id=? AND status='active'", (world_id,),
    ).fetchall():
        for participant in database.execute(
            "SELECT user_id FROM mud_expedition_participants WHERE instance_id=? AND status='active'",
            (run["id"],),
        ).fetchall():
            user_id = participant["user_id"]
            _leave_combat(database, user_id, "instance", run["id"])
            database.execute(
                "UPDATE mud_expedition_participants SET status='abandoned',finished_at=CURRENT_TIMESTAMP "
                "WHERE instance_id=? AND user_id=? AND status='active'", (run["id"], user_id),
            )
            _settle_run_loot(database, user_id, run["id"], keep=False)
            location = database.execute(
                "SELECT instance_id FROM mud_player_locations WHERE user_id=?", (user_id,),
            ).fetchone()
            if location is not None and location["instance_id"] == run["id"]:
                _leave_run_location(database, user_id, run["id"])
            _insert_event(database, EventDraft(
                f"{message} Your dungeon run ended and its loot was lost.",
                event_type="expedition", recipient_ids=(user_id,)), default_user_id=user_id)
        database.execute(
            "UPDATE mud_procedural_instances SET status='abandoned',terminal_reason='world_changed',"
            "ended_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'",
            (run["id"],),
        )


def _difficulty_advice(level, difficulty):
    recommended = dungeons.recommended_level(difficulty)
    percent = dungeons.dungeon_xp_percent(level, difficulty)
    if percent < 100:
        return (f" Recommended level {recommended}: at level {level} this run is beneath you, "
                f"so experience is reduced to {percent}%.")
    if percent > 100:
        return (f" Recommended level {recommended}: at level {level} this run is dangerous, "
                f"but experience is increased to {percent}%.")
    return f" Recommended level {recommended}."


def _join_expedition_action(database, user, parameters):
    room = _current_room(database, user["id"])
    if room.get("instance_id"):
        raise ActionError("You are already inside a dungeon run.")
    if _active_expedition(database, user["id"]):
        raise ActionError("You already have an active expedition.")
    code = str(parameters.get("code") or " ".join(
        str(value) for value in parameters.get("arguments", [])
    )).strip().upper()
    if not code:
        raise ActionError("Enter a cooperative run code: /join <code>.")
    run = database.execute(
        "SELECT * FROM mud_procedural_instances WHERE join_code=? AND instance_type='random' "
        "AND status='active'", (code,),
    ).fetchone()
    world_run = _world_run(database, run["id"]) if run is not None else None
    if world_run is None and room["id"] != PORTAL_ROOM_ID:
        if run is not None or not _room_entrances(database, room["id"]):
            raise ActionError("Expeditions can only be joined from the Dungeon Portals.")
    if run is None:
        raise ActionError("That expedition is not active or the code is invalid.", 404)
    if world_run is not None:
        # A party gathers where the run began: at its entrance, or the room it returns to.
        if room["id"] not in {world_run["entrance_room_id"], world_run["return_room_id"]}:
            raise ActionError("Join a party at the dungeon entrance it set out from.")
        if not _exit_open_to(database, user["id"], database.execute(
                "SELECT conditions_json,conditions_message FROM mud_world_dungeon_entrances WHERE id=?",
                (run["world_entrance_id"],)).fetchone() or {"conditions_json": "[]"}):
            raise ActionError("You cannot pass that dungeon entrance.")
        members = database.execute(
            "SELECT COUNT(*) FROM mud_expedition_participants WHERE instance_id=? AND status='active'",
            (run["id"],),
        ).fetchone()[0]
        if world_run["party_limit"] and members >= world_run["party_limit"]:
            raise ActionError("That party is full.", 409)
    entrance = database.execute(
        "SELECT room_key FROM mud_dungeon_rooms WHERE instance_id=? AND role='entrance'",
        (run["id"],),
    ).fetchone()["room_key"]
    character = _character_row(database, user["id"])
    try:
        database.execute(
            "INSERT INTO mud_expedition_participants(instance_id,user_id,character_id,current_room_key) "
            "VALUES(?,?,?,?)", (run["id"], user["id"], character["id"], entrance),
        )
    except sqlite3.IntegrityError as problem:
        raise ActionError("You already participated in that expedition and cannot rejoin it.", 409) from problem
    if world_run is not None:
        _leave_combat(database, user["id"], "room", room["id"])
    entry = _enter_expedition(database, user, run["id"], entrance)
    recipients = _instance_participant_ids(database, run["id"], user["id"])
    return [
        EventDraft(f"You join expedition {code}.\n\n{_describe_room(database, _current_room(database, user['id']), user['id'])}", event_type="expedition"),
        EventDraft(f"{character['display_name']} joins expedition {code}.", event_type="arrival",
                   audience_type="instance", audience_key=run["id"], recipient_ids=recipients),
    ] + entry


def _resume_expedition_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The resume command does not accept arguments.")
    room = _current_room(database, user["id"])
    if room.get("instance_id"):
        raise ActionError("You are already inside an expedition.")
    run = _active_expedition(database, user["id"])
    world_run = _world_run(database, run["id"]) if run else None
    if run is None:
        raise ActionError("You have no active expedition to resume.")
    if world_run is None and room["id"] != PORTAL_ROOM_ID:
        raise ActionError("You are already inside an expedition.")
    if world_run is not None and not _mode_settings(database)["traditional"]:
        raise ActionError("The traditional world is disabled.")
    if world_run is not None:
        _leave_combat(database, user["id"], "room", room["id"])
    _enter_expedition(database, user, run["id"], run["current_room_key"])
    return [EventDraft(
        f"You resume {'Dungeon of the Day run' if _daily_run_day(database, run['id']) else 'expedition' if world_run is None else _run_label(database, run['id'])} {run['join_code']}.\n\n"
        f"{_describe_room(database, _current_room(database, user['id']), user['id'])}",
        event_type="expedition",
    )]


def _abandon_expedition_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The abandon command does not accept arguments.")
    run = _active_expedition(database, user["id"])
    if run is None:
        raise ActionError("You have no active expedition to abandon.")
    _leave_combat(database, user["id"], "instance", run["id"])
    abandoned = database.execute(
        "UPDATE mud_expedition_participants SET status='abandoned',finished_at=CURRENT_TIMESTAMP "
        "WHERE instance_id=? AND user_id=? AND status='active'", (run["id"], user["id"]),
    ).rowcount
    if abandoned:
        _record_daily_result(
            database, run["id"], user["id"], "abandoned", run["rooms_visited"]
        )
    _settle_run_loot(database, user["id"], run["id"], keep=False)
    location = database.execute(
        "SELECT instance_id FROM mud_player_locations WHERE user_id=?", (user["id"],),
    ).fetchone()
    world_run = _world_run(database, run["id"])
    # A world explorer displaced from the run abandons it from where they stand.
    if world_run is None or location is None or location["instance_id"] == run["id"]:
        _leave_run_location(database, user["id"], run["id"])
    _finish_empty_expedition(database, run["id"], "abandoned")
    label = ("Dungeon of the Day run" if _daily_run_day(database, run["id"]) else
             "expedition" if world_run is None else _run_label(database, run["id"]))
    events = [EventDraft(
        f"You abandon {label} {run['join_code']}. This run's loot is lost; previously owned items and earned experience remain.",
        event_type="expedition",
    ), _room_entry_event(database, user)]
    recipients = _instance_participant_ids(database, run["id"])
    if world_run is not None and recipients:
        events.append(EventDraft(
            f"{_character_row(database, user['id'])['display_name']} abandons the run.",
            event_type="departure", audience_type="instance", audience_key=run["id"],
            recipient_ids=recipients,
        ))
    return events


def _complete_expedition_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The complete command does not accept arguments.")
    run = _active_expedition(database, user["id"])
    room = _current_room(database, user["id"])
    if run is None or room.get("instance_id") != run["id"] or room.get("role") != "exit":
        raise ActionError("The expedition can only be completed from its exit room.")
    if _active_hostiles(database, room, user["id"]):
        raise ActionError("Defeat or escape the enemy guarding the exit first.", 409)
    active = database.execute(
        "SELECT p.user_id,p.character_id,p.rooms_visited,c.display_name "
        "FROM mud_expedition_participants AS p JOIN mud_characters AS c "
        "ON c.id=p.character_id WHERE p.instance_id=? AND p.status='active' "
        "ORDER BY p.joined_at,p.user_id", (run["id"],),
    ).fetchall()
    recipients = tuple(item["user_id"] for item in active)
    daily_day = _daily_run_day(database, run["id"])
    # World runs always pay the world's track, even to an explorer displaced from the dungeon.
    track = None
    if _world_run(database, run["id"]) is not None:
        track = "shared" if _crossover_settings(database)["progression_shared"] else "traditional"
    rewarded = 0
    coin_rewards = []
    level_drafts = []
    for participant in active:
        reward = max(1, (run["difficulty"] * 10 + participant["rooms_visited"] * 2) * dungeons.dungeon_xp_percent(
            _character_row(database, participant["user_id"], track)["level"], run["difficulty"]) // 100)
        claimed = database.execute(
            "UPDATE mud_expedition_participants SET status='completed',finished_at=CURRENT_TIMESTAMP,"
            "reward_experience=?,rewarded_at=CURRENT_TIMESTAMP WHERE instance_id=? AND user_id=? "
            "AND status='active' AND rewarded_at IS NULL",
            (reward if daily_day is None else 0, run["id"], participant["user_id"]),
        ).rowcount
        daily_claimed = False
        if claimed and daily_day is not None:
            _record_daily_result(
                database, run["id"], participant["user_id"], "completed",
                participant["rooms_visited"],
            )
            daily_claimed = bool(database.execute(
                "UPDATE mud_daily_participation SET reward_experience=?,rewarded_at=CURRENT_TIMESTAMP "
                "WHERE day_key=? AND user_id=? AND rewarded_at IS NULL",
                (reward, daily_day, participant["user_id"]),
            ).rowcount)
            if daily_claimed:
                database.execute(
                    "UPDATE mud_expedition_participants SET reward_experience=? "
                    "WHERE instance_id=? AND user_id=?",
                    (reward, run["id"], participant["user_id"]),
                )
        if claimed and (daily_day is None or daily_claimed):
            level_up = level_up_text(award_experience(database, participant["user_id"], reward, track))
            if level_up:
                level_drafts.append(EventDraft(level_up, event_type="reward",
                                               audience_key=str(participant["user_id"]),
                                               recipient_ids=(participant["user_id"],)))
            rewarded += 1
        if claimed:
            _settle_run_loot(database, participant["user_id"], run["id"], keep=True)
            wallet_owner = _character_row(database, participant["user_id"])["id"]
            _wallet(database, wallet_owner)
            coins = max(5, run["difficulty"] * 5 + participant["rooms_visited"] * 2)
            database.execute(
                "UPDATE mud_character_wallets SET coins=coins+? WHERE character_id=?",
                (coins, wallet_owner),
            )
            coin_rewards.append(coins)
        _leave_combat(database, participant["user_id"], "instance", run["id"])
        _leave_run_location(database, participant["user_id"], run["id"])
    database.execute(
        "UPDATE mud_procedural_instances SET status='completed',terminal_reason='success',"
        "ended_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'",
        (run["id"],),
    )
    reward_text = (
        "Each active explorer receives a completion experience reward based on difficulty and rooms personally visited, "
        "reduced for explorers far above the recommended level."
        if daily_day is None else
        f"First-completion rewards were granted to {rewarded} eligible explorer{'s' if rewarded != 1 else ''}; repeat completions are recorded without another reward."
    )
    return [EventDraft(
        f"{_run_label(database, run['id'])} {run['join_code']} is complete. "
        f"Collected loot is safe, and each explorer earns coins based on difficulty and rooms visited. {reward_text}",
        event_type="reward", audience_type="instance", audience_key=run["id"],
        recipient_ids=recipients,
    )] + level_drafts + [EventDraft(
        _describe_room(database, _current_room(database, participant["user_id"]), participant["user_id"]),
        event_type="room", audience_key=str(participant["user_id"]),
    ) for participant in active]


def _expedition_status_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The run command does not accept arguments.")
    return [EventDraft(_expedition_status_text(database, user["id"]), event_type="expedition")]


def _assert_daily_attempt_available(database, daily, user_id):
    settings = _daily_settings(database)
    participation = _daily_participation(database, daily["day_key"], user_id)
    if participation and participation["attempts"] >= settings["max_attempts"]:
        raise ActionError(
            f"You have used all {settings['max_attempts']} attempts for {daily['day_key']}.", 409
        )


def _claim_daily_attempt(database, daily, user_id, instance_id):
    _assert_daily_attempt_available(database, daily, user_id)
    database.execute(
        "INSERT INTO mud_daily_participation(day_key,user_id,attempts,last_instance_id,"
        "last_result,first_started_at) VALUES(?,?,1,?,'active',CURRENT_TIMESTAMP) "
        "ON CONFLICT(day_key,user_id) DO UPDATE SET attempts=attempts+1,"
        "last_instance_id=excluded.last_instance_id,last_result='active'",
        (daily["day_key"], user_id, instance_id),
    )


def _start_daily_run(database, user, daily):
    if _active_expedition(database, user["id"]):
        raise ActionError("You already have an active run. Resume or abandon it first.")
    _assert_daily_attempt_available(database, daily, user["id"])
    instance_id, manifest = dungeons.clone_instance(database, daily["design_instance_id"], "daily")
    code = _new_join_code(database)
    database.execute(
        "UPDATE mud_procedural_instances SET status='active',owner_user_id=?,join_code=?,"
        "started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
        (user["id"], code, instance_id),
    )
    database.execute(
        "INSERT INTO mud_daily_runs(instance_id,day_key) VALUES(?,?)",
        (instance_id, daily["day_key"]),
    )
    _claim_daily_attempt(database, daily, user["id"], instance_id)
    character = _character_row(database, user["id"])
    database.execute(
        "INSERT INTO mud_expedition_participants(instance_id,user_id,character_id,current_room_key) "
        "VALUES(?,?,?,?)", (instance_id, user["id"], character["id"], manifest["entrance"]),
    )
    entry = _enter_expedition(database, user, instance_id, manifest["entrance"])
    return [EventDraft(
        f"Dungeon of the Day attempt started. Cooperative join code: {code}.\n\n"
        f"{_describe_room(database, _current_room(database, user['id']), user['id'])}",
        event_type="daily",
    )] + entry


def _join_daily_run(database, user, daily, code):
    if _active_expedition(database, user["id"]):
        raise ActionError("You already have an active run.")
    _assert_daily_attempt_available(database, daily, user["id"])
    run = database.execute(
        "SELECT i.* FROM mud_procedural_instances AS i JOIN mud_daily_runs AS r "
        "ON r.instance_id=i.id WHERE i.join_code=? AND r.day_key=? AND i.status='active'",
        (code, daily["day_key"]),
    ).fetchone()
    if run is None:
        raise ActionError("That current-day cooperative run is not active or the code is invalid.", 404)
    if database.execute(
        "SELECT 1 FROM mud_expedition_participants WHERE instance_id=? AND user_id=?",
        (run["id"], user["id"]),
    ).fetchone():
        raise ActionError("You already participated in that run and cannot rejoin it.", 409)
    entrance = database.execute(
        "SELECT room_key FROM mud_dungeon_rooms WHERE instance_id=? AND role='entrance'",
        (run["id"],),
    ).fetchone()["room_key"]
    _claim_daily_attempt(database, daily, user["id"], run["id"])
    character = _character_row(database, user["id"])
    database.execute(
        "INSERT INTO mud_expedition_participants(instance_id,user_id,character_id,current_room_key) "
        "VALUES(?,?,?,?)", (run["id"], user["id"], character["id"], entrance),
    )
    entry = _enter_expedition(database, user, run["id"], entrance)
    return [
        EventDraft(
            f"You join today's run {code}.\n\n"
            f"{_describe_room(database, _current_room(database, user['id']), user['id'])}",
            event_type="daily",
        ),
        EventDraft(
            f"{character['display_name']} joins today's run {code}.", event_type="arrival",
            audience_type="instance", audience_key=run["id"],
            recipient_ids=_instance_participant_ids(database, run["id"], user["id"]),
        ),
    ] + entry


def _daily_action(database, user, parameters):
    if _current_room(database, user["id"])["id"] != PORTAL_ROOM_ID:
        raise ActionError("Dungeon of the Day is only available from the Dungeon Portals.")
    daily = ensure_daily_dungeon(database)
    if daily["status"] != "ready":
        raise ActionError(f"Today's dungeon is unavailable: {daily['error_message']}", 503)
    arguments = [str(item) for item in parameters.get("arguments", [])]
    operation = str(parameters.get("operation", "") or (arguments[0] if arguments else "status"))
    operation = operation.casefold()
    if operation in {"status", "inspect", "details"}:
        design = database.execute(
            "SELECT layout,room_count,difficulty FROM mud_procedural_instances WHERE id=?",
            (daily["design_instance_id"],),
        ).fetchone()
        return [EventDraft(
            f"{_daily_status_text(database, user['id'])}\n"
            f"Layout {design['layout']} · {design['room_count']} rooms · difficulty {design['difficulty']}.\n"
            "Use /daily enter to begin a separate run, or /daily join <code> to join a party.",
            event_type="daily",
        )]
    if operation in {"enter", "start", "launch"}:
        return _start_daily_run(database, user, daily)
    if operation == "join":
        code = str(parameters.get("code") or (arguments[1] if len(arguments) > 1 else "")).strip().upper()
        if not code:
            raise ActionError("Enter a cooperative run code: /daily join <code>.")
        return _join_daily_run(database, user, daily, code)
    raise ActionError("Use /daily, /daily enter, or /daily join <code>.")


def _dungeon_entrance_action(_database, _user, parameters):
    modes = _mode_settings(_database)
    if (not modes["procedural"] or
            _current_room(_database, _user["id"])["id"] != PORTAL_ROOM_ID):
        raise ActionError("That procedural gateway is not available from here.")
    kind = str(parameters.get("kind", "")).strip()
    arguments = parameters.get("arguments", [])
    if arguments:
        kind = " ".join(str(value) for value in arguments).casefold()
    if kind in {"daily", "dungeon of the day"}:
        return _daily_action(_database, _user, {"operation": "status"})
    elif kind in {"random", "expedition", "random expedition", "random expeditions"}:
        return _expedition_action(_database, _user, {})
    else:
        raise ActionError("Choose Dungeon of the Day or a Random Expedition.")


def _locked_hint(exit_row):
    methods = []
    if exit_row["key_item_id"]:
        methods.append("/unlock <exit> with a key")
    if exit_row["pick_difficulty"]:
        methods.append("/pick <exit>")
    return "Locked. Use " + " or ".join(methods) + "."


def _locked_exit(database, user, parameters, usage):
    target = " ".join(str(value) for value in parameters.get("arguments", [])).strip()
    if not target:
        raise ActionError(usage)
    room = _current_room(database, user["id"])
    if room.get("instance_id"):
        raise ActionError("There are no locked exits here.")
    matches = [row for row in _room_exits(database, room["id"], viewer_id=user["id"]) if target.casefold() in
               {row["id"].casefold(), row["name"].casefold(), (row["direction"] or "").casefold()}]
    if len(matches) != 1:
        raise ActionError("Choose a nearby exit by name or direction.")
    if _exit_sealed(matches[0]):
        raise ActionError(f"{matches[0]['name']} is sealed. No key or pick will open it.")
    return room, matches[0]


def _unlock_action(database, user, parameters):
    _room, chosen = _locked_exit(database, user, parameters, "Use /unlock <exit>.")
    if not _exit_locked(chosen):
        return [EventDraft(f"{chosen['name']} is already open.", event_type="room")]
    if not chosen["key_item_id"]:
        raise ActionError(f"{chosen['name']} has no keyhole you can use. Try /pick {chosen['direction'] or chosen['name']}.")
    character = _character_row(database, user["id"])
    if database.execute("SELECT 1 FROM mud_exit_unlocks WHERE character_id=? AND exit_id=?",
                        (character["id"], chosen["id"])).fetchone():
        return [EventDraft(f"{chosen['name']} is already unlocked.", event_type="room")]
    keys = list(_inventory_rows(database, user["id"])) + list(_inventory_rows(database, user["id"], True))
    if not any(item["definition_id"] == chosen["key_item_id"] for item in keys):
        raise ActionError("You do not have the key for that exit.")
    database.execute("INSERT INTO mud_exit_unlocks(character_id,exit_id) VALUES(?,?)",
                     (character["id"], chosen["id"]))
    return [EventDraft(f"You unlock {chosen['name']}.", event_type="room")]


def _pick_action(database, user, parameters):
    room, chosen = _locked_exit(database, user, parameters, "Use /pick <exit>.")
    if not _exit_locked(chosen):
        return [EventDraft(f"{chosen['name']} is already open.", event_type="room")]
    if not chosen["pick_difficulty"]:
        raise ActionError(f"The lock on {chosen['name']} cannot be picked; find its key.")
    if _active_hostiles(database, room, user["id"]):
        raise ActionError("You cannot work a lock with enemies nearby.", 409)
    character = _character_row(database, user["id"])
    if database.execute("SELECT 1 FROM mud_exit_unlocks WHERE character_id=? AND exit_id=?",
                        (character["id"], chosen["id"])).fetchone():
        return [EventDraft(f"{chosen['name']} is already unlocked.", event_type="room")]
    score = _character_effects(database, character["id"])["lockpick"] + 2 * (character["agility"] - 10)
    practiced = _practice(database, character["id"], "pick_lock")
    if secrets.randbelow(100) >= _skill_check_chance(score, chosen["pick_difficulty"]):
        return [EventDraft(f"The lock on {chosen['name']} resists your efforts.", event_type="room")] + practiced
    database.execute("INSERT INTO mud_exit_unlocks(character_id,exit_id) VALUES(?,?)",
                     (character["id"], chosen["id"]))
    return [EventDraft(f"You pick the lock on {chosen['name']}.", event_type="room")] + practiced


def _go_action(database, user, parameters):
    character = character_state(database, user["id"])
    room = _current_room(database, user["id"])
    exit_id = str(parameters.get("exit_id", "")).strip()
    arguments = parameters.get("arguments", [])
    destination = " ".join(str(item) for item in arguments).strip() if arguments else ""
    modes = _mode_settings(database)
    destination_key = " ".join(destination.casefold().split())
    if room["id"] == PORTAL_ROOM_ID and modes["traditional"] and (
        exit_id == "system:return-world" or destination_key in {"out", "world", "traditional world"}
    ):
        return _world_action(database, user, {})
    if room["id"] != PORTAL_ROOM_ID and modes["procedural"] and (
        exit_id == "system:enter-portals" or destination_key in {
            "portal", "portals", "dungeon portals", _portal_room(database)["name"].casefold()
        }
    ):
        return _portal_action(database, user, {})
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    if _active_hostiles(database, room, user["id"]) and not _is_hidden(database, user["id"]):
        raise ActionError("A hostile enemy blocks your route. Fight or attempt to flee.", 409)
    if exit_id:
        matches = [item for item in _room_exits(database, room["id"], instance_id, user["id"])
                   if item["id"] == exit_id]
    elif destination:
        key = " ".join(destination.casefold().split())
        matches = [
            item for item in _room_exits(database, room["id"], instance_id, user["id"])
            if key in {
                " ".join(item["name"].casefold().split()),
                " ".join((item["direction"] or "").casefold().split()),
                " ".join(item["destination_name"].casefold().split()),
            }
        ]
    else:
        raise ActionError("Go where? Use /exits to list available routes.")
    if not matches:
        if (destination and not instance_id and room["id"] != PORTAL_ROOM_ID
                and _entrance_match(database, room["id"], destination, required=False)):
            return _enter_dungeon_action(database, user, {"arguments": [destination]})
        raise ActionError(f'There is no exit matching "{destination or exit_id}" from here.')
    if len(matches) > 1:
        raise ActionError(f'More than one exit matches "{destination}". Use an exit name or direction.')
    chosen = matches[0]
    if not instance_id and not _exit_open_to(database, user["id"], chosen):
        raise ActionError(_exit_barred_message(chosen))
    if not instance_id and _exit_sealed(chosen):
        raise ActionError(f"{chosen['name']} is sealed. Something else must open it.")
    if not instance_id and _exit_locked(chosen):
        unlocked = database.execute("SELECT 1 FROM mud_exit_unlocks WHERE character_id=? AND exit_id=?",
                                    (_character_row(database, user["id"])["id"], chosen["id"])).fetchone()
        if not unlocked:
            raise ActionError("That exit is locked. " + _locked_hint(chosen).removeprefix("Locked. "))
    old_recipients = _occupant_ids(database, room["id"], user["id"], instance_id)
    _leave_combat(database, user["id"], "instance" if instance_id else "room",
                  instance_id or room["id"])
    if instance_id:
        _set_player_location(database, user["id"], _run_mode(database, instance_id), "dungeon_instance",
                             chosen["to_room_id"], instance_id)
        _record_expedition_visit(database, instance_id, user["id"], chosen["to_room_id"])
        destination_room = _dungeon_room(database, instance_id, chosen["to_room_id"])
    else:
        _set_player_location(database, user["id"], "traditional", "world_room", chosen["to_room_id"])
        destination_room = _world_room(database, chosen["to_room_id"])
        mud_wilderness.roll_resources(database, destination_room)
    route = chosen["direction"] or chosen["name"]
    new_recipients = _occupant_ids(database, destination_room["id"], user["id"], instance_id)
    audience_type = "instance" if instance_id else "room"
    audience_key = instance_id or room["id"]
    events = [
        EventDraft(
            f"You go {route}.",
            event_type="movement",
        ),
        _room_entry_event(database, user),
        EventDraft(
            f"{character['name']} leaves via {route}.",
            event_type="departure", audience_type=audience_type, audience_key=audience_key,
            recipient_ids=old_recipients,
        ),
        EventDraft(
            f"{character['name']} arrives.",
            event_type="arrival", audience_type=audience_type,
            audience_key=instance_id or destination_room["id"], recipient_ids=new_recipients,
        ),
    ]
    if not instance_id:
        events += _set_objective_progress(database, user, "visit_room", destination_room["id"])
        events += _wilderness_encounter(database, destination_room)
    events += _entry_triggers(database, user)
    return events + _entry_attack(database, user)


def _wilderness_encounter(database, room):
    """Wild rooms may spawn a creature as someone arrives; it despawns like a trigger spawn."""
    encounter = mud_wilderness.roll_encounter(database, room)
    if encounter is None:
        return []
    instance_id = f"world:spawn:{uuid.uuid4().hex}"
    database.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id,spawned_by_trigger) VALUES(?,?,?,?)",
                     (instance_id, encounter["npc"], room["id"], encounter["source"]))
    database.execute("INSERT INTO mud_scheduled_events(event_kind,entity_id,due_at) VALUES('npc_despawn',?,?)",
                     (instance_id, _scheduled_now(datetime.now(timezone.utc)
                                                  + timedelta(seconds=encounter["despawn_seconds"]))))
    return [EventDraft(f"{encounter['name']} appears.", "npc", "room", room["id"], _occupant_ids(database, room["id"]))]


def _say_action(database, user, parameters):
    body = str(parameters.get("message") or " ".join(str(arg) for arg in parameters.get("arguments", []))).strip()
    if not 1 <= len(body) <= 1000:
        raise ActionError("Messages must be between 1 and 1000 characters.")
    if moderation.active_restriction(user["id"], ("mute",)):
        raise ActionError("You cannot communicate while muted.", 403)
    room = _current_room(database, user["id"])
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    location_type = ("dungeon_instance" if instance_id else
                     "portal" if room["id"] == PORTAL_ROOM_ID else "world_room")
    if database.execute(
        "SELECT 1 FROM mud_presence WHERE user_id=? AND location_type=? "
        "AND location_key=? AND (? IS NULL OR instance_id=?) LIMIT 1",
        (user["id"], location_type, room["id"], instance_id, instance_id),
    ).fetchone() is None:
        raise ActionError("Reconnect to the MUD before speaking in this room.", 409)
    recipients = set(_occupant_ids(database, room["id"], instance_id=instance_id))
    recipients.add(user["id"])
    character = character_state(database, user["id"])
    _set_hidden(database, user["id"], False)
    source = (f"role:{room['role']}", f"room_template:{room.get('room_template_id')}") if instance_id else (room["id"],)
    return [EventDraft(
        f"{character['name']}: {body}", event_type="chat",
        audience_type="instance" if instance_id else "room",
        audience_key=instance_id or room["id"],
        recipient_ids=tuple(sorted(recipients)),
    )] + (fire_triggers(database, user, "say_phrase", source, instance_id=instance_id, phrase=body)
          if room["id"] != PORTAL_ROOM_ID else [])


def _emote_action(database, user, parameters):
    text = " ".join(str(arg) for arg in parameters.get("arguments", [])).strip()
    if not 1 <= len(text) <= 500:
        raise ActionError("Use /emote <action> (up to 500 characters).")
    if moderation.active_restriction(user["id"], ("mute",)):
        raise ActionError("You cannot communicate while muted.", 403)
    room = _current_room(database, user["id"])
    instance_id = room.get("instance_id")
    recipients = set(_occupant_ids(database, room["id"], instance_id=instance_id))
    recipients.add(user["id"])
    _set_hidden(database, user["id"], False)
    return [EventDraft(f"{_character_row(database, user['id'])['display_name']} {text}",
                       event_type="emote", audience_type="instance" if instance_id else "room",
                       audience_key=instance_id or room["id"], recipient_ids=tuple(sorted(recipients)))]


def _present_players(database):
    return database.execute(
        "SELECT u.id,COALESCE(c.display_name,u.callsign) AS name FROM users u "
        "LEFT JOIN mud_characters c ON c.user_id=u.id "
        "WHERE EXISTS (SELECT 1 FROM mud_presence p WHERE p.user_id=u.id) "
        "ORDER BY name COLLATE NOCASE,u.id"
    ).fetchall()


def _who_action(database, _user, parameters):
    if parameters.get("arguments"):
        raise ActionError("Use /who without arguments.")
    players = _present_players(database)
    return [EventDraft("Players present: " + (", ".join(label for _, label in _numbered_items(players)) or "none"), event_type="social")]


def _tell_to(database, user, target_id, message):
    if moderation.active_restriction(user["id"], ("mute",)):
        raise ActionError("You cannot communicate while muted.", 403)
    if not 1 <= len(message) <= 500:
        raise ActionError("Private messages must be between 1 and 500 characters.")
    target = next((row for row in _present_players(database) if row["id"] == target_id), None)
    if target is None or target_id == user["id"]:
        raise ActionError("That player is not currently available.")
    if database.execute("SELECT 1 FROM mud_user_blocks WHERE (user_id=? AND blocked_user_id=?) OR (user_id=? AND blocked_user_id=?)",
                        (target_id, user["id"], user["id"], target_id)).fetchone():
        raise ActionError("That player is not available for private messages.", 403)
    sender = _character_row(database, user["id"])["display_name"]
    database.execute("INSERT INTO mud_tell_history(user_id,other_user_id) VALUES(?,?) "
                     "ON CONFLICT(user_id) DO UPDATE SET other_user_id=excluded.other_user_id", (target_id, user["id"]))
    return [EventDraft(f"You tell {target['name']}: {message}", event_type="tell"),
            EventDraft(f"{sender} tells you: {message}", event_type="tell", audience_key=str(target_id))]


def _tell_action(database, user, parameters):
    args = parameters.get("arguments", [])
    if len(args) < 2:
        raise ActionError('Use /tell "player name" <message>.')
    name = args[0]
    try:
        player = _item_matches(_present_players(database), name)
    except ActionError as problem:
        raise ActionError(str(problem).replace("item", "player") + " Quote multiword names.") from problem
    return _tell_to(database, user, player["id"], " ".join(args[1:]))


def _reply_player_action(database, user, parameters):
    message = " ".join(parameters.get("arguments", [])).strip()
    has_dialogue = database.execute(
        "SELECT 1 FROM mud_conversation_sessions WHERE character_id=(SELECT id FROM mud_characters WHERE user_id=?) "
        "UNION SELECT 1 FROM mud_dungeon_conversation_sessions WHERE character_id=(SELECT id FROM mud_characters WHERE user_id=?)",
        (user["id"], user["id"]),
    ).fetchone()
    if has_dialogue:
        try:
            return _respond_action(database, user, parameters)
        except ActionError:
            if message.isdigit():
                raise
    row = database.execute("SELECT other_user_id FROM mud_tell_history WHERE user_id=?", (user["id"],)).fetchone()
    if row is None:
        return _respond_action(database, user, parameters)
    return _tell_to(database, user, row["other_user_id"], message)


def _description_action(database, user, parameters):
    args = parameters.get("arguments", [])
    if not args:
        row = database.execute("SELECT description FROM mud_characters WHERE user_id=?", (user["id"],)).fetchone()
        return [EventDraft("Description: " + (row["description"] or "none"), event_type="character")]
    description = " ".join(args).strip()
    if description.casefold() == "clear":
        description = ""
    if len(description) > 500:
        raise ActionError("Descriptions cannot exceed 500 characters.")
    database.execute("UPDATE mud_characters SET description=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (description, user["id"]))
    return [EventDraft("Description updated." if description else "Description cleared.", event_type="character")]


def _block_action(database, user, parameters):
    name = " ".join(parameters.get("arguments", [])).strip()
    if not name:
        raise ActionError("Use /block <player> or /unblock <player>.")
    row = database.execute("SELECT c.user_id FROM mud_characters c WHERE c.display_name=? COLLATE NOCASE", (name,)).fetchone()
    if row is None or row["user_id"] == user["id"]:
        raise ActionError("No such player.")
    database.execute("INSERT OR IGNORE INTO mud_user_blocks(user_id,blocked_user_id) VALUES(?,?)", (user["id"], row["user_id"]))
    return [EventDraft(f"Blocked {name} from private messages.", event_type="social")]


def _unblock_action(database, user, parameters):
    name = " ".join(parameters.get("arguments", [])).strip()
    row = database.execute("SELECT c.user_id FROM mud_characters c WHERE c.display_name=? COLLATE NOCASE", (name,)).fetchone()
    if row is None:
        raise ActionError("No such player.")
    database.execute("DELETE FROM mud_user_blocks WHERE user_id=? AND blocked_user_id=?", (user["id"], row["user_id"]))
    return [EventDraft(f"Unblocked {name}.", event_type="social")]


# Authored triggers ---------------------------------------------------------
# A trigger reacts to one game event (see mud_triggers). Triggers that run
# other triggers, teleport players into rooms with their own triggers, or
# finish quests all share one chain, so a single action stays bounded.

_TRIGGER_CHAIN = contextvars.ContextVar("mud_trigger_chain", default=None)
_DUNGEON_TRIGGERS = {}
INTERACT_VERBS = {"interact": "interacts with", "use": "uses", "push": "pushes", "pull": "pulls",
                  "press": "presses", "touch": "touches", "activate": "activates", "turn": "turns"}


def _active_world(database):
    row = database.execute("SELECT id FROM mud_world_packages WHERE active=1").fetchone()
    return row["id"] if row else ""


def _script_realm(database, user_id=None):
    """A dungeon run keeps its own script state; everywhere else uses the active world's."""
    if user_id is not None:
        location = database.execute(
            "SELECT location_type,instance_id FROM mud_player_locations WHERE user_id=?", (user_id,),
        ).fetchone()
        if location is not None and location["location_type"] == "dungeon_instance" and location["instance_id"]:
            return location["instance_id"]
    return _active_world(database)


def _script_flag(database, realm, scope_key, key):
    row = database.execute("SELECT value FROM mud_script_flags WHERE realm=? AND scope_key=? AND key=?",
                           (realm, scope_key, key)).fetchone()
    return row["value"] if row else ""


def _store_script_flag(database, realm, scope_key, key, value):
    if value == "":
        database.execute("DELETE FROM mud_script_flags WHERE realm=? AND scope_key=? AND key=?",
                         (realm, scope_key, key))
        return
    database.execute(
        "INSERT INTO mud_script_flags(realm,scope_key,key,value) VALUES(?,?,?,?) "
        "ON CONFLICT(realm,scope_key,key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
        (realm, scope_key, key, value))


def _store_entity_state(database, realm, entity_type, entity_id, state, scope_key=""):
    """Override an authored exit, NPC, or object; a blank state restores the authored one."""
    if not state:
        database.execute("DELETE FROM mud_script_entity_states WHERE entity_type=? AND entity_id=? "
                         "AND scope_key=?", (entity_type, entity_id, scope_key))
        return
    database.execute(
        "INSERT INTO mud_script_entity_states(realm,entity_type,entity_id,scope_key,state) VALUES(?,?,?,?,?) "
        "ON CONFLICT(entity_type,entity_id,scope_key) DO UPDATE SET state=excluded.state,"
        "realm=excluded.realm,updated_at=CURRENT_TIMESTAMP",
        (realm, entity_type, entity_id, scope_key, state))


def _script_visible(database, entity_type, rows):
    """Drop placed NPCs or items that are hidden, by authored state_json or by a trigger."""
    rows = list(rows)
    if not rows:
        return rows
    states = {row["entity_id"]: row["state"] for row in database.execute(
        f"SELECT entity_id,state FROM mud_script_entity_states WHERE entity_type=? AND scope_key='' "
        f"AND entity_id IN ({','.join('?' for _ in rows)})", (entity_type, *(row["id"] for row in rows)))}
    return [row for row in rows if states.get(
        row["id"], "hidden" if _json_properties(row["state_json"]).get("hidden") is True else "shown") != "hidden"]


def _object_state(database, object_id):
    row = database.execute("SELECT state FROM mud_script_entity_states WHERE entity_type='object' "
                           "AND entity_id=? AND scope_key=''", (object_id,)).fetchone()
    if row is not None:
        return row["state"]
    item = database.execute("SELECT state_json FROM mud_item_instances WHERE id=?", (object_id,)).fetchone()
    state = _json_properties(item["state_json"]).get("state", "") if item else ""
    return state if isinstance(state, str) else ""


def _object_state_text(database, item):
    """The authored line describing an object's current state, such as 'The lever is down.'"""
    descriptions = _json_properties(item["properties_json"]).get("state_descriptions")
    if not isinstance(descriptions, dict):
        return ""
    text = descriptions.get(_object_state(database, item["id"]))
    return text.strip() if isinstance(text, str) else ""


def _load_triggers(rows):
    triggers = []
    for row in rows:
        try:
            trigger = json.loads(row["definition_json"])
        except (TypeError, json.JSONDecodeError):
            continue  # Malformed stored rules stay inert.
        triggers.append({**trigger, "id": row["id"]})
    return triggers


def _world_triggers(database, event, source_ids=(), trigger_id=None):
    world_id = _active_world(database)
    if trigger_id is not None:
        return _load_triggers(database.execute(
            "SELECT id,definition_json FROM mud_world_triggers WHERE id=? AND world_id=? AND enabled=1 "
            "AND event=?", (trigger_id, world_id, event)))
    source_ids = list(source_ids)
    return _load_triggers(database.execute(
        "SELECT id,definition_json FROM mud_world_triggers WHERE world_id=? AND event=? AND enabled=1 "
        f"AND source_id IN ({','.join('?' for _ in source_ids)}) ORDER BY rowid",
        (world_id, event, *source_ids)))


def _dungeon_trigger_list(database, instance_id):
    """A run's triggers, resolved from its template's local names to engine IDs."""
    row = database.execute(
        "SELECT v.template_json,v.template_hash FROM mud_procedural_instances AS i "
        "JOIN mud_dungeon_template_versions AS v ON v.theme_id=i.theme_id AND v.version=i.theme_version "
        "WHERE i.id=?", (instance_id,)).fetchone()
    if row is None:
        return []
    if row["template_hash"] not in _DUNGEON_TRIGGERS:
        template = json.loads(row["template_json"])
        try:
            raw = mud_triggers.validate_list(template.get("triggers"), dungeon=True)
        except ValueError:
            raw = []
        definitions = template.get("definitions") if isinstance(template.get("definitions"), dict) else {}
        local = {kind: {entry.get("id") for entry in definitions.get(kind, []) if isinstance(entry, dict)}
                 for kind in ("items", "npcs", "enemies")}
        prefix = f"dungeon:{template['id']}@{template['version']}:trigger:"

        def resolve(kind, ref):
            if kind == "item" and ref in local["items"]:
                return dungeons.local_definition_id(template, "items", ref)
            if kind == "npc" and ref in local["npcs"] | local["enemies"]:
                return dungeons.local_definition_id(template, "npcs", ref)
            if kind == "trigger":
                return prefix + ref
            if kind == "room_role":
                return f"role:{ref}"
            if kind == "room_template":
                return f"room_template:{ref}"
            return ref

        resolved = []
        for trigger in raw:
            trigger = mud_triggers.remap(trigger, resolve)
            trigger["id"] = prefix + trigger["id"]
            trigger["source_id"] = next(iter(trigger["source"].values()), None)
            resolved.append(trigger)
        _DUNGEON_TRIGGERS[row["template_hash"]] = resolved
    return _DUNGEON_TRIGGERS[row["template_hash"]]


def _dungeon_triggers(database, instance_id, event, source_ids=(), trigger_id=None):
    return [trigger for trigger in _dungeon_trigger_list(database, instance_id)
            if trigger["event"] == event and trigger.get("enabled", True)
            and (trigger["id"] == trigger_id if trigger_id is not None else trigger["source_id"] in source_ids)]


def fire_triggers(database, user, event, source_ids, *, instance_id=None, phrase=None):
    """Run every enabled trigger for this event and source; return the drafts they produce."""
    source_ids = [value for value in source_ids if value]
    if not source_ids:
        return []
    triggers = (_dungeon_triggers(database, instance_id, event, source_ids) if instance_id
                else _world_triggers(database, event, source_ids))
    if phrase is not None:
        key = mud_triggers.phrase_key(phrase)
        triggers = [trigger for trigger in triggers if trigger.get("phrase") == key]
    drafts = []
    for trigger in triggers:
        drafts += _run_trigger(database, user, trigger, instance_id)
    return drafts


def _entry_triggers(database, user):
    room = _current_room(database, user["id"])
    if room["id"] in (PORTAL_ROOM_ID, "stationnet:death-summary"):
        return []
    instance_id = room.get("instance_id")
    sources = ([f"role:{room['role']}", f"room_template:{room.get('room_template_id')}"] if instance_id
               else [room["id"]])
    return fire_triggers(database, user, "enter_room", sources, instance_id=instance_id)


def _private(user_id, text, event_type="world"):
    return EventDraft(text, event_type, "private", str(user_id), (user_id,))


def _trigger_costs_problem(database, user, trigger):
    """Why the player cannot pay a trigger's immediate costs, or None. Nothing is written."""
    items, coins = {}, 0
    for action in trigger["actions"]:
        if action.get("delay_seconds"):
            continue
        if action["type"] == "take_item":
            items[action["item"]] = items.get(action["item"], 0) + action["amount"]
        elif action["type"] == "take_coins":
            coins += action["amount"]
    try:
        for definition_id, amount in items.items():
            _handover_items(database, user, definition_id, amount)
    except ActionError as problem:
        return str(problem)
    if coins and coins > _wallet(database, _character_row(database, user["id"])["id"]):
        return f"You need {coins} coins for that."
    return None


def _claim_trigger_fire(database, realm, trigger, scope_key):
    """Count one firing unless once, max_fires, or the cooldown forbid it.

    Actions run inside one write transaction, so two players can never both
    claim a world-once trigger.
    """
    row = database.execute("SELECT fires,last_fired_at FROM mud_trigger_fires WHERE realm=? AND trigger_id=? "
                           "AND scope_key=?", (realm, trigger["id"], scope_key)).fetchone()
    fires = row["fires"] if row else 0
    if trigger.get("once") and fires or trigger.get("max_fires") and fires >= trigger["max_fires"]:
        return False
    if row and trigger.get("cooldown_seconds") and row["last_fired_at"] > _timestamp_before(trigger["cooldown_seconds"]):
        return False
    database.execute(
        "INSERT INTO mud_trigger_fires(realm,trigger_id,scope_key,fires,last_fired_at) VALUES(?,?,?,1,?) "
        "ON CONFLICT(realm,trigger_id,scope_key) DO UPDATE SET fires=fires+1,last_fired_at=excluded.last_fired_at",
        (realm, trigger["id"], scope_key, _scheduled_now()))
    return True


def _run_trigger(database, user, trigger, instance_id=None):
    chain = _TRIGGER_CHAIN.get()
    token = None
    if chain is None:
        chain = {"depth": 0, "actions": 0, "fired": set()}
        token = _TRIGGER_CHAIN.set(chain)
    try:
        if trigger["id"] in chain["fired"] or chain["depth"] >= mud_triggers.MAX_CHAIN_DEPTH:
            return []
        user_id = user["id"] if user else None
        realm = instance_id or _active_world(database)
        try:
            passed = mud_conditions.evaluate(trigger["conditions"], _ConditionContext(database, user_id, realm=realm))
        except (ActionError, KeyError, TypeError):
            passed = False
        if not passed:
            return [_private(user_id, trigger["fail_message"])] if user and trigger.get("fail_message") else []
        problem = _trigger_costs_problem(database, user, trigger) if user else None
        if problem:
            return [_private(user_id, trigger.get("fail_message") or problem)]
        scope_key = _character_row(database, user_id)["id"] if user and trigger["scope"] == "character" else ""
        if not _claim_trigger_fire(database, realm, trigger, scope_key):
            return []
        chain["fired"].add(trigger["id"])
        chain["depth"] += 1
        drafts = []
        try:
            for index, action in enumerate(trigger["actions"]):
                if chain["actions"] >= mud_triggers.MAX_CHAIN_ACTIONS:
                    break
                chain["actions"] += 1
                if action.get("delay_seconds"):
                    _schedule_trigger_action(database, trigger, index, user_id, realm, instance_id, chain["depth"])
                else:
                    drafts += _trigger_action(database, user, trigger, index, realm, instance_id)
        finally:
            chain["depth"] -= 1
        return _addressed(drafts, user_id) if user else drafts
    finally:
        if token is not None:
            _TRIGGER_CHAIN.reset(token)


def _schedule_trigger_action(database, trigger, index, user_id, realm, instance_id, depth):
    """Persist a delayed action; at most a few per trigger may wait at once."""
    prefix = f"{trigger['id']}#"
    pending = database.execute(
        "SELECT COUNT(*) FROM mud_scheduled_events WHERE event_kind='trigger_action' AND substr(entity_id,1,?)=?",
        (len(prefix), prefix)).fetchone()[0]
    if pending >= mud_triggers.MAX_PENDING_DELAYS:
        return
    action = trigger["actions"][index]
    due = _scheduled_now(datetime.now(timezone.utc) + timedelta(seconds=action["delay_seconds"]))
    payload = {"trigger": {"id": trigger["id"], "source": trigger["source"], "actions": trigger["actions"]},
               "index": index, "user_id": user_id, "realm": realm, "instance_id": instance_id, "depth": depth}
    database.execute(
        "INSERT INTO mud_scheduled_events(event_kind,entity_id,due_at,payload_json) VALUES('trigger_action',?,?,?)",
        (prefix + uuid.uuid4().hex, due, json.dumps(payload, separators=(",", ":"))))


def _trigger_room(database, user, trigger):
    """The room an event happened in: the player's, or a timer's own room."""
    if user is not None:
        return _current_room(database, user["id"])
    return _world_room(database, trigger["source"].get("room"))


def _trigger_action(database, user, trigger, index, realm, instance_id=None):
    """Apply one action. A failed action tells the player why and the rest still run."""
    action = trigger["actions"][index]
    kind = action["type"]
    user_id = user["id"] if user else None
    if user is None and mud_triggers.actor_required(action):
        return []
    try:
        if kind == "message":
            if action["to"] == "actor":
                return [_private(user_id, action["text"])]
            if action["to"] == "other_room":
                room_id, room_instance = action["room"], None
            else:
                room = _trigger_room(database, user, trigger)
                if room is None:
                    return []
                room_id, room_instance = room["id"], room.get("instance_id")
            recipients = set(_occupant_ids(database, room_id, instance_id=room_instance))
            if user and action["to"] == "room":
                recipients.add(user_id)
            if not recipients:
                return []
            return [EventDraft(action["text"], "world", "instance" if room_instance else "room",
                               room_instance or room_id, tuple(sorted(recipients)))]
        if kind in ("set_flag", "clear_flag"):
            scope_key = "" if action["scope"] == "world" else _character_row(database, user_id)["id"]
            value = action.get("value", "")
            if "add" in action:
                current = mud_conditions.flag_number(_script_flag(database, realm, scope_key, action["key"]))
                value = str(max(-1_000_000_000, min(1_000_000_000, current + action["add"])))
            _store_script_flag(database, realm, scope_key, action["key"], value)
            return []
        if kind in mud_triggers.OUTCOME_ACTIONS:
            outcome = {"type": kind, **{key: action[key] for key in ("item", "amount") if key in action}}
            return _settle_outcomes(database, user, [outcome], event_type="world")
        if kind in ("heal", "damage"):
            character = _character_row(database, user_id)
            amount = (min(action["amount"], character["maximum_health"] - character["current_health"]) if kind == "heal"
                      else min(action["amount"], character["current_health"] - 1))
            if amount <= 0:
                return []
            database.execute(
                "UPDATE mud_character_progression SET current_health=current_health+?,updated_at=CURRENT_TIMESTAMP "
                "WHERE character_id=? AND track_key=?",
                (amount if kind == "heal" else -amount, character["id"], character["track_key"]))
            return [_private(user_id, f"You recover {amount} health." if kind == "heal" else f"You take {amount} damage.",
                             "combat")]
        if kind == "set_object_state":
            _store_entity_state(database, realm, "object", action["object"], action["state"])
            return []
        if kind == "set_exit":
            scope_key = _character_row(database, user_id)["id"] if action["scope"] == "character" else ""
            _store_entity_state(database, realm, "exit", action["exit"],
                                "" if action["state"] == "default" else action["state"], scope_key)
            return []
        if kind in ("show", "hide"):
            entity_type, entity_id = (("npc", action["npc_instance"]) if "npc_instance" in action
                                      else ("item", action["object"]))
            _store_entity_state(database, realm, entity_type, entity_id, "shown" if kind == "show" else "hidden")
            return []
        if kind == "spawn_npc":
            return _trigger_spawn(database, trigger, index, action)
        if kind == "teleport":
            return _trigger_teleport(database, user, action["room"])
        if kind == "start_quest":
            return _accept_quest(database, user, action["quest"])
        if kind == "complete_objective":
            objective = database.execute("SELECT objective_type,target_id FROM mud_quest_objectives WHERE id=?",
                                         (action["objective"],)).fetchone()
            if objective is None:
                return []
            return _set_objective_progress(database, user, objective["objective_type"], objective["target_id"],
                                           action["objective"])
        if kind == "run_trigger":
            targets = (_dungeon_triggers(database, instance_id, "manual", trigger_id=action["trigger"]) if instance_id
                       else _world_triggers(database, "manual", trigger_id=action["trigger"]))
            return [draft for target in targets for draft in _run_trigger(database, user, target, instance_id)]
    except ActionError as problem:
        return [_private(user_id, str(problem))] if user else []
    return []


def _trigger_spawn(database, trigger, index, action):
    """Place a temporary NPC; it despawns on a timer and never respawns once defeated."""
    source = f"{trigger['id']}#{index}"
    definition = database.execute("SELECT name FROM mud_npc_definitions WHERE id=?", (action["npc"],)).fetchone()
    room = database.execute("SELECT id FROM mud_world_rooms WHERE id=? AND world_id=?",
                            (action["room"], _active_world(database))).fetchone()
    if definition is None or room is None or database.execute(
            "SELECT COUNT(*) FROM mud_npc_instances WHERE spawned_by_trigger=?", (source,)
    ).fetchone()[0] >= action["max_live"]:
        return []
    instance_id = f"world:spawn:{uuid.uuid4().hex}"
    database.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id,spawned_by_trigger) VALUES(?,?,?,?)",
                     (instance_id, action["npc"], room["id"], source))
    database.execute("INSERT INTO mud_scheduled_events(event_kind,entity_id,due_at) VALUES('npc_despawn',?,?)",
                     (instance_id, _scheduled_now(datetime.now(timezone.utc)
                                                  + timedelta(seconds=action["despawn_seconds"]))))
    recipients = _occupant_ids(database, room["id"])
    return [EventDraft(f"{definition['name']} appears.", "npc", "room", room["id"], recipients)] if recipients else []


def _trigger_teleport(database, user, room_id):
    """Move a player between rooms of the active world, as if they had walked in."""
    current = _current_room(database, user["id"])
    destination = database.execute("SELECT id FROM mud_world_rooms WHERE id=? AND world_id=?",
                                   (room_id, _active_world(database))).fetchone()
    if destination is None or current.get("system_managed") or current["id"] == room_id:
        return []
    name = character_state(database, user["id"])["name"]
    old_recipients = _occupant_ids(database, current["id"], user["id"])
    _leave_combat(database, user["id"], "room", current["id"])
    _set_player_location(database, user["id"], "traditional", "world_room", room_id)
    drafts = [_room_entry_event(database, user),
              EventDraft(f"{name} vanishes.", "departure", "room", current["id"], old_recipients),
              EventDraft(f"{name} appears.", "arrival", "room", room_id,
                         _occupant_ids(database, room_id, user["id"]))]
    drafts += _set_objective_progress(database, user, "visit_room", room_id)
    return drafts + _entry_triggers(database, user)


def _process_trigger_event(database, event):
    """Run a delayed trigger action or a repeating timer; returns drafts to deliver."""
    if event["event_kind"] == "trigger_timer":
        triggers = _world_triggers(database, "timer", trigger_id=event["entity_id"])
        return [draft for trigger in triggers for draft in _run_trigger(database, None, trigger)]
    database.execute("DELETE FROM mud_scheduled_events WHERE id=?", (event["id"],))
    payload = _json_properties(event["payload_json"])
    trigger, index = payload.get("trigger"), payload.get("index")
    if (not isinstance(trigger, dict) or not isinstance(trigger.get("actions"), list) or type(index) is not int
            or not 0 <= index < len(trigger["actions"]) or not payload.get("realm")):
        return []
    user = None
    if payload.get("user_id") is not None:
        user = database.execute("SELECT * FROM users WHERE id=?", (payload["user_id"],)).fetchone()
        # A player who left the world or the run no longer receives its delayed effects.
        if user is None or _script_realm(database, user["id"]) != payload.get("realm"):
            return []
    elif payload.get("realm") != _active_world(database):
        return []
    token = _TRIGGER_CHAIN.set({"depth": payload.get("depth", 1), "actions": 0, "fired": set()})
    try:
        drafts = _trigger_action(database, user, trigger, index, payload["realm"], payload.get("instance_id"))
    finally:
        _TRIGGER_CHAIN.reset(token)
    return _addressed(drafts, user["id"]) if user else drafts


def sync_trigger_schedule(database, now=None):
    """Reconcile repeating timer triggers with the active world's enabled timers."""
    active = {}
    for trigger in _load_triggers(database.execute(
            "SELECT id,definition_json FROM mud_world_triggers WHERE world_id=? AND event='timer' AND enabled=1",
            (_active_world(database),))):
        interval = trigger.get("interval_seconds")
        if type(interval) is not int or interval < 30:
            continue
        active[trigger["id"]] = interval
        due = _scheduled_now((now or datetime.now(timezone.utc)) + timedelta(seconds=interval))
        database.execute(
            "INSERT INTO mud_scheduled_events(event_kind,entity_id,due_at,interval_seconds) VALUES('trigger_timer',?,?,?) "
            "ON CONFLICT(event_kind,entity_id) DO UPDATE SET "
            "due_at=CASE WHEN interval_seconds<>excluded.interval_seconds THEN excluded.due_at ELSE due_at END,"
            "interval_seconds=excluded.interval_seconds", (trigger["id"], due, interval))
    for row in database.execute("SELECT entity_id FROM mud_scheduled_events WHERE event_kind='trigger_timer'").fetchall():
        if row["entity_id"] not in active:
            database.execute("DELETE FROM mud_scheduled_events WHERE event_kind='trigger_timer' AND entity_id=?",
                             (row["entity_id"],))


def _despawn_npc(database, event, now=None):
    npc = database.execute(
        "SELECT i.id,i.room_id,d.name FROM mud_npc_instances i JOIN mud_npc_definitions d ON d.id=i.definition_id "
        "WHERE i.id=? AND i.spawned_by_trigger IS NOT NULL", (event["entity_id"],)).fetchone()
    if npc is not None and database.execute(
            "SELECT 1 FROM mud_combat_encounters WHERE npc_instance_id=? AND status='active'", (npc["id"],)).fetchone():
        # Never pull an enemy out of a fight; try again shortly.
        database.execute("UPDATE mud_scheduled_events SET due_at=? WHERE id=?",
                         (_scheduled_now((now or datetime.now(timezone.utc)) + timedelta(seconds=60)), event["id"]))
        return
    database.execute("DELETE FROM mud_scheduled_events WHERE id=?", (event["id"],))
    if npc is None:
        return
    recipients = _occupant_ids(database, npc["room_id"])
    database.execute("DELETE FROM mud_npc_instances WHERE id=?", (npc["id"],))
    if recipients:
        _insert_event(database, EventDraft(f"{npc['name']} departs.", "npc", "room", npc["room_id"], recipients))


def reset_script_state(database, realm):
    """Forget a world's flags, trigger counts, overrides, spawns, and pending delays."""
    for table in ("mud_script_flags", "mud_trigger_fires", "mud_script_entity_states"):
        database.execute(f"DELETE FROM {table} WHERE realm=?", (realm,))
    database.execute("DELETE FROM mud_npc_instances WHERE spawned_by_trigger IS NOT NULL AND room_id IN "
                     "(SELECT id FROM mud_world_rooms WHERE world_id=?)", (realm,))
    database.execute("DELETE FROM mud_scheduled_events WHERE event_kind='trigger_action' "
                     "AND json_extract(payload_json,'$.realm')=?", (realm,))


def _interact_action(database, user, parameters):
    arguments = [str(value) for value in parameters.get("arguments", [])]
    verb = "interact"
    if arguments and arguments[0].startswith(":") and arguments[0][1:] in INTERACT_VERBS:
        verb = arguments.pop(0)[1:]
    target = str(parameters.get("item_id") or " ".join(arguments)).strip()
    if not target:
        raise ActionError(f"{verb.title()} what? Name an object in this room.")
    room = _current_room(database, user["id"])
    if room.get("system_managed") and not room.get("instance_id"):
        raise ActionError("There is nothing here to interact with.")
    instance_id = room.get("instance_id")
    item = _item_matches(_room_item_rows(database, room["id"], instance_id), target)
    name = _character_row(database, user["id"])["display_name"]
    drafts = [_room_item_event(database, user, room, f"{name} {INTERACT_VERBS[verb]} {item['name']}.")]
    script = fire_triggers(database, user, "use_object", [item["id"], item["definition_id"]],
                           instance_id=instance_id)
    if not script:
        message = _json_properties(item["properties_json"]).get("use_message")
        script = [EventDraft(message.strip() if isinstance(message, str) and message.strip()
                             else "Nothing happens.", event_type="item")]
    return drafts + script


actions = ActionRegistry()
actions.register(ActionDefinition(
    key="status", label="SYSTEM STATUS",
    description="Check the current MUD service state.",
    commands=("status",), handler=_status_action,
))
actions.register(ActionDefinition(
    key="character", label="VIEW STATS",
    description="View your character identity, health, mana, active effects, attributes, and progression.",
    commands=("character", "stats"), handler=_character_action,
    group="CHARACTER",
))
actions.register(ActionDefinition(
    key="train", label="TRAIN ATTRIBUTES",
    description="Spend attribute points earned by leveling. Use /train alone to see options, "
                "or /train <attribute> [points].",
    commands=("train", "improve"), handler=_train_action,
    group="CHARACTER",
))
actions.register(ActionDefinition(
    key="skills", label="SKILLS",
    description="List your skills, ranks, effects, skill points, and skills you can learn; "
                "/skills <name> shows details.",
    commands=("skills", "proficiencies"), handler=_skills_action,
    group="CHARACTER",
))
actions.register(ActionDefinition(
    key="learn", label="LEARN SKILL",
    description="Learn a new skill with skill points: /learn <skill> [from <trainer>].",
    commands=("learn",), handler=_learn_action,
    group="CHARACTER", show_context=False,
))
actions.register(ActionDefinition(
    key="practice_skill", label="PRACTICE SKILL",
    description="Raise a known skill one rank with a skill point, or pay a trainer: "
                "/practice <skill> [from <trainer>].",
    commands=("practice",), handler=_practice_skill_action,
    group="CHARACTER", show_context=False,
))
actions.register(ActionDefinition(
    key="inventory", label="VIEW INVENTORY",
    description="List carried and equipped items.",
    commands=("inventory", "inv", "i"), handler=_inventory_action,
    group="CHARACTER", show_context=False,
))
actions.register(ActionDefinition(
    key="say", label="SAY",
    description="Speak to the other players in this room.",
    commands=("say",), handler=_say_action, show_context=False,
))
actions.register(ActionDefinition(
    key="help", label="COMMAND HELP",
    description="List the commands currently available.",
    commands=("help", "?"), handler=_help_action,
))
actions.register(ActionDefinition(
    key="look", label="LOOK AROUND",
    description="Examine your current surroundings.",
    commands=("look", "l", "examine", "x"), handler=_look_action,
    group="SURROUNDINGS",
))
actions.register(ActionDefinition(
    key="map", label="MAP",
    description="Show a map of the wilderness around you.",
    commands=("map",), handler=_map_action,
    group="SURROUNDINGS",
))
actions.register(ActionDefinition(key="get", label="PICK UP", description="Pick up an item in this room or an open container.", commands=("get", "take"), handler=_get_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="drop", label="DROP", description="Drop a carried item in this room.", commands=("drop",), handler=_drop_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="open", label="OPEN", description="Open an available container.", commands=("open",), handler=_open_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="close", label="CLOSE", description="Close an available container.", commands=("close",), handler=_close_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="put", label="PUT AWAY", description="Put a carried item in an open container.", commands=("put",), handler=_put_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="equip", label="EQUIP", description="Equip a carried item.", commands=("equip", "wear"), handler=_equip_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="unequip", label="UNEQUIP", description="Remove equipped gear.", commands=("unequip", "remove"), handler=_unequip_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="use", label="USE", description="Use a carried item, or an object in this room.", commands=("use",), handler=_use_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="interact", label="INTERACT", description="Work an object in this room; also /push, /pull, /press, /touch, /turn, or /activate <object>.", commands=("interact",), handler=_interact_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="shop", label="SHOP", description="Browse, buy, or sell with a nearby merchant; the portal trader also serves the hub.", commands=("shop",), handler=_shop_action, group="HUB", show_context=False))
actions.register(ActionDefinition(key="storage", label="PERSONAL STORAGE", description="Deposit or withdraw personal items in the portal room.", commands=("storage", "stash"), handler=_storage_action, group="HUB", show_context=False))
actions.register(ActionDefinition(key="talk", label="TALK", description="Talk to a nearby NPC.", commands=("talk", "speak"), handler=_talk_action, group="NPCS", show_context=False))
actions.register(ActionDefinition(key="respond", label="RESPOND", description="Choose a response in your current NPC conversation.", commands=("respond",), handler=_respond_action, group="DIALOGUE", show_context=False))
actions.register(ActionDefinition(key="reply_player", label="REPLY", description="Reply to your latest private message; numeric replies still work in NPC dialogue.", commands=("reply",), handler=_reply_player_action, group="SOCIAL", show_context=False))
actions.register(ActionDefinition(key="tell", label="TELL", description="Send a private message to a present player.", commands=("tell",), handler=_tell_action, group="SOCIAL", show_context=False))
actions.register(ActionDefinition(key="emote", label="EMOTE", description="Describe an action to players in your room.", commands=("emote",), handler=_emote_action, group="SOCIAL", show_context=False))
actions.register(ActionDefinition(key="who", label="WHO", description="List players currently connected to the MUD.", commands=("who",), handler=_who_action, group="SOCIAL", show_context=False))
actions.register(ActionDefinition(key="description", label="DESCRIPTION", description="View or edit your public character description; use clear to remove it.", commands=("description", "describe"), handler=_description_action, group="CHARACTER", show_context=False))
actions.register(ActionDefinition(key="block", label="BLOCK", description="Block a player from private messages.", commands=("block",), handler=_block_action, group="SOCIAL", show_context=False))
actions.register(ActionDefinition(key="unblock", label="UNBLOCK", description="Allow a blocked player to send private messages.", commands=("unblock",), handler=_unblock_action, group="SOCIAL", show_context=False))
actions.register(ActionDefinition(key="quests", label="QUESTS", description="View quests; /quest available, info, accept, turnin, or abandon <name>.", commands=("quests", "quest", "objectives"), handler=_quests_action, group="QUESTS"))
actions.register(ActionDefinition(key="deliver", label="DELIVER ITEM", description="Give a quest item to a nearby quest giver: /deliver <item>.", commands=("deliver",), handler=_deliver_action, group="QUESTS", show_context=False))
actions.register(ActionDefinition(key="combat", label="COMBAT STATUS", description="View hostile enemies and encounter status.", commands=("combat",), handler=_combat_action, group="COMBAT"))
actions.register(ActionDefinition(key="spells", label="KNOWN SPELLS", description="List your class spells and their mana costs.", commands=("spells",), handler=_spells_action, group="MAGIC"))
actions.register(ActionDefinition(key="cast", label="CAST SPELL", description="Cast a known spell, targeting an enemy when required.", commands=("cast",), handler=_cast_action, group="MAGIC", show_context=False))
actions.register(ActionDefinition(key="rest", label="REST", description="Recover health and mana outside combat.", commands=("rest",), handler=_rest_action, group="MAGIC"))
actions.register(ActionDefinition(key="hide", label="HIDE", description="Hide from enemies using Agility against their awareness; your next attack gains surprise damage.", commands=("hide",), handler=_hide_action, group="COMBAT", show_context=False))
actions.register(ActionDefinition(key="attack", label="ATTACK", description="Attack a hostile enemy; fire an equipped loaded gun, otherwise use melee.", commands=("attack", "hit"), handler=_attack_action, group="COMBAT", show_context=False))
actions.register(ActionDefinition(key="fire", label="FIRE", description="Fire an equipped loaded firearm at a hostile enemy.", commands=("fire", "shoot"), handler=_fire_action, group="COMBAT", show_context=False))
actions.register(ActionDefinition(key="melee", label="MELEE", description="Use a melee attack even when a loaded firearm is equipped.", commands=("melee", "strike"), handler=_melee_action, group="COMBAT", show_context=False))
actions.register(ActionDefinition(key="reload", label="RELOAD", description="Load compatible carried ammunition into an equipped firearm.", commands=("reload",), handler=_reload_action, group="COMBAT", show_context=False))
actions.register(ActionDefinition(key="defend", label="DEFEND", description="Brace to reduce the next enemy attack.", commands=("defend", "guard"), handler=_defend_action, group="COMBAT", show_context=False))
actions.register(ActionDefinition(key="flee", label="FLEE", description="Attempt a risky escape from combat.", commands=("flee",), handler=_flee_action, group="COMBAT", show_context=False))
actions.register(ActionDefinition(
    key="exits", label="LIST EXITS",
    description="List the routes available from this room.",
    commands=("exits",), handler=_exits_action,
    group="NAVIGATION",
))
actions.register(ActionDefinition(
    key="go", label="GO",
    description="Travel through an available exit.",
    commands=("go", "move", "walk"), handler=_go_action,
    group="NAVIGATION", show_context=False,
))
actions.register(ActionDefinition(key="unlock", label="UNLOCK EXIT", description="Unlock a nearby keyed exit with a carried key.", commands=("unlock",), handler=_unlock_action, group="NAVIGATION", show_context=False))
actions.register(ActionDefinition(key="pick", label="PICK LOCK", description="Pick a nearby lock using Lockpicking and Agility.", commands=("pick", "picklock"), handler=_pick_action, group="NAVIGATION", show_context=False))
actions.register(ActionDefinition(key="search", label="SEARCH ROOM", description="Search this room for authored discoveries.", commands=("search",), handler=_search_action, group="NAVIGATION", show_context=False))
actions.register(ActionDefinition(
    key="portal", label="ENTER DUNGEON PORTALS",
    description="Enter the protected procedural gameplay hub.",
    commands=("portal", "portals"), handler=_portal_action,
    group="NAVIGATION", show_context=False,
))
actions.register(ActionDefinition(
    key="continue", label="CONTINUE AFTER DEATH",
    description="Leave the death summary and recover in the portal room.",
    commands=("continue",), handler=_continue_action,
    group="NAVIGATION", show_context=False,
))
actions.register(ActionDefinition(
    key="world", label="RETURN TO TRADITIONAL WORLD",
    description="Return to your last traditional-world location.",
    commands=("world",), handler=_world_action,
    group="NAVIGATION", show_context=False,
))
actions.register(ActionDefinition(
    key="dungeon_entrance", label="INSPECT GATEWAY",
    description="Inspect a protected procedural dungeon gateway.",
    handler=_dungeon_entrance_action,
    group="PORTALS", show_context=False,
))
actions.register(ActionDefinition(
    key="daily", label="ENTER DUNGEON OF THE DAY",
    description="Inspect, enter, or join today's station-wide challenge.",
    commands=("daily",), handler=_daily_action,
    group="DAILY DUNGEON", show_context=False,
))
actions.register(ActionDefinition(
    key="expedition", label="LAUNCH EXPEDITION",
    description="Launch a random expedition from an installed theme.",
    commands=("expedition", "expeditions"), handler=_expedition_action,
    group="EXPEDITIONS", show_context=False,
))
actions.register(ActionDefinition(
    key="join_expedition", label="JOIN EXPEDITION",
    description="Join an active cooperative expedition by code.",
    commands=("join",), handler=_join_expedition_action,
    group="EXPEDITIONS", show_context=False,
))
actions.register(ActionDefinition(
    key="enter_dungeon", label="ENTER DUNGEON",
    description="Descend through a world dungeon entrance into a freshly generated run.",
    commands=("enter", "delve"), handler=_enter_dungeon_action,
    group="EXPEDITIONS", show_context=False,
))
actions.register(ActionDefinition(
    key="resume_expedition", label="RESUME EXPEDITION",
    description="Return to your saved room in an active expedition.",
    commands=("resume",), handler=_resume_expedition_action,
    group="EXPEDITIONS", show_context=False,
))
actions.register(ActionDefinition(
    key="abandon_expedition", label="ABANDON EXPEDITION",
    description="Leave immediately and lose this run's loot.",
    commands=("abandon",), handler=_abandon_expedition_action,
    group="EXPEDITIONS", show_context=False,
))
actions.register(ActionDefinition(
    key="complete_expedition", label="COMPLETE EXPEDITION",
    description="Finish the run from its exit and award every active participant.",
    commands=("complete",), handler=_complete_expedition_action,
    group="EXPEDITIONS", show_context=False,
))
actions.register(ActionDefinition(
    key="expedition_status", label="RUN STATUS",
    description="Show expedition objective, progress, party size, and join code.",
    commands=("run",), handler=_expedition_status_action,
    group="EXPEDITIONS", show_context=False,
))
for alias, direction in {
    "north": "north", "n": "north", "south": "south", "s": "south",
    "east": "east", "e": "east", "west": "west", "w": "west",
    "northeast": "northeast", "ne": "northeast",
    "northwest": "northwest", "nw": "northwest",
    "southeast": "southeast", "se": "southeast",
    "southwest": "southwest", "sw": "southwest",
    "up": "up", "u": "up", "down": "down", "d": "down",
    "in": "in", "out": "out",
}.items():
    actions.register_command(alias, actions.get("go"), (direction,))
for verb in INTERACT_VERBS:
    if verb not in ("interact", "use"):
        actions.register_command(verb, actions.get("interact"), (f":{verb}",))


def _insert_event(database, draft, default_user_id=None, action_id=None):
    audience_key = draft.audience_key or str(default_user_id)
    cursor = database.execute(
        "INSERT INTO mud_events(action_id,audience_type,audience_key,event_type,body) "
        "VALUES(?,?,?,?,?)",
        (action_id, draft.audience_type, audience_key, draft.event_type, draft.body),
    )
    recipients = draft.recipient_ids
    if draft.audience_type == "room" and recipients is None:
        recipients = _occupant_ids(database, audience_key)
    if recipients:
        database.executemany(
            "INSERT OR IGNORE INTO mud_event_recipients(event_id,user_id) VALUES(?,?)",
            ((cursor.lastrowid, user_id) for user_id in recipients),
        )
    return database.execute(
        "SELECT id,event_type,body,created_at FROM mud_events WHERE id=?",
        (cursor.lastrowid,),
    ).fetchone()


def _event_cutoff(database, user_id):
    row = database.execute(
        "SELECT cleared_through_id FROM mud_event_views WHERE user_id=?",
        (user_id,),
    ).fetchone()
    return row["cleared_through_id"] if row else 0


def _events_for_user(database, user_id, after=0, action_id=None, limit=200):
    filters = ["e.id>?"]
    parameters = [max(after, _event_cutoff(database, user_id))]
    if action_id is not None:
        filters.append("e.action_id=?")
        parameters.append(action_id)
    filters.append(
        "((e.audience_type='private' AND e.audience_key=?) OR EXISTS ("
        "SELECT 1 FROM mud_event_recipients AS r "
        "WHERE r.event_id=e.id AND r.user_id=?))"
    )
    parameters.extend((str(user_id), user_id, limit))
    return database.execute(
        "SELECT e.id,e.event_type,e.body,e.created_at FROM mud_events AS e WHERE "
        + " AND ".join(filters) + " ORDER BY e.id LIMIT ?",
        parameters,
    ).fetchall()


def _prune_presence(database):
    expired = database.execute(
        "SELECT p.user_id,p.room_id,p.location_type,p.location_key,p.instance_id,"
        "COALESCE(c.display_name,u.callsign) AS character_name "
        "FROM mud_presence AS p "
        "JOIN users AS u ON u.id=p.user_id "
        "LEFT JOIN mud_characters AS c ON c.user_id=u.id "
        "WHERE p.last_seen<? OR (p.disconnected_at IS NOT NULL AND p.disconnected_at<?)",
        (_timestamp_before(PRESENCE_SECONDS), _timestamp_before(DISCONNECT_GRACE_SECONDS)),
    ).fetchall()
    if not expired:
        return
    affected = {}
    for row in expired:
        affected[row["user_id"]] = (
            row["location_key"] or row["room_id"], row["character_name"], row["instance_id"]
        )
    database.execute(
        "DELETE FROM mud_presence WHERE last_seen<? "
        "OR (disconnected_at IS NOT NULL AND disconnected_at<?)",
        (_timestamp_before(PRESENCE_SECONDS), _timestamp_before(DISCONNECT_GRACE_SECONDS)),
    )
    for user_id, (room_id, character_name, instance_id) in affected.items():
        if room_id is None or database.execute(
            "SELECT 1 FROM mud_presence WHERE user_id=? LIMIT 1", (user_id,)
        ).fetchone() is not None:
            continue
        _insert_event(database, EventDraft(
            f"{character_name} disconnects.", event_type="departure",
            audience_type="instance" if instance_id else "room",
            audience_key=instance_id or room_id,
            recipient_ids=_occupant_ids(database, room_id, user_id, instance_id),
        ))


def _connection_id(database, user_id):
    row = database.execute(
        "SELECT connection_id FROM users WHERE id=?", (user_id,)
    ).fetchone()
    connection_id = session.get("connection_id")
    if not connection_id or row is None or row["connection_id"] != connection_id:
        raise ActionError("This session was replaced by a newer login.", 409)
    return connection_id


def _register_presence(database, user, client_id):
    if not CLIENT_ID_RE.fullmatch(client_id):
        raise ActionError("Invalid MUD client identifier.")
    connection_id = _connection_id(database, user["id"])
    character = character_state(database, user["id"])
    _prune_presence(database)
    room = _current_room(database, user["id"])
    location = database.execute(
        "SELECT location_type,location_key,instance_id FROM mud_player_locations "
        "WHERE user_id=?", (user["id"],),
    ).fetchone()
    already_present = database.execute(
        "SELECT 1 FROM mud_presence WHERE user_id=? LIMIT 1", (user["id"],)
    ).fetchone() is not None
    # A new login supersedes older cookies without producing a duplicate player.
    database.execute(
        "DELETE FROM mud_presence WHERE user_id=? AND connection_id<>?",
        (user["id"], connection_id),
    )
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    database.execute(
        "INSERT INTO mud_presence(user_id,connection_id,client_id,room_id,location_type,"
        "location_key,instance_id,last_seen) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id,connection_id,client_id) DO UPDATE SET "
        "room_id=excluded.room_id,location_type=excluded.location_type,"
        "location_key=excluded.location_key,instance_id=excluded.instance_id,"
        "last_seen=excluded.last_seen,disconnected_at=NULL",
        (user["id"], connection_id, client_id,
         room["id"] if location["location_type"] == "world_room" else None,
         location["location_type"], location["location_key"], location["instance_id"], now),
    )
    if not already_present:
        _insert_event(database, EventDraft(
            f"{character['name']} arrives.", event_type="arrival",
            audience_type="instance" if location["instance_id"] else "room",
            audience_key=location["instance_id"] or room["id"],
            recipient_ids=_occupant_ids(database, room["id"], user["id"],
                                        location["instance_id"]),
        ))


def _scheduled_now(now=None):
    return (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()


def schedule_room_message(database, room_id, message, due_at, interval_seconds=None):
    """Persist a bounded one-shot or recurring room announcement."""
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 500:
        raise ValueError("Scheduled room messages must be 1 to 500 characters.")
    if due_at.tzinfo is None:
        raise ValueError("Scheduled times must include a timezone.")
    if interval_seconds is not None and (type(interval_seconds) is not int or not 5 <= interval_seconds <= 86400):
        raise ValueError("Intervals must be 5 to 86400 seconds.")
    if database.execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (room_id,)).fetchone() is None:
        raise ValueError("Scheduled room does not exist.")
    event_key = uuid.uuid4().hex
    database.execute(
        "INSERT INTO mud_scheduled_events(event_kind,entity_id,due_at,interval_seconds,payload_json) "
        "VALUES('room_message',?,?,?,?)",
        (event_key, _scheduled_now(due_at.astimezone(timezone.utc)), interval_seconds,
         json.dumps({"room_id": room_id, "message": message.strip()})),
    )
    return event_key


def sync_npc_schedule(database, now=None):
    """Reconcile persistent timers with currently active authored NPCs."""
    active = set()
    rows = database.execute(
        "SELECT i.id,d.behaviors_json FROM mud_npc_instances i "
        "JOIN mud_npc_definitions d ON d.id=i.definition_id "
        "JOIN mud_world_rooms r ON r.id=i.room_id "
        "WHERE r.world_id=(SELECT id FROM mud_world_packages WHERE active=1)"
    ).fetchall()
    for row in rows:
        try:
            behavior = validate_npc_behaviors(_json_properties(row["behaviors_json"]))
        except ValueError:
            continue  # Legacy malformed data remains inert, without preventing startup.
        for kind in ("ambient", "wander", "patrol"):
            if kind not in behavior:
                continue
            active.add((kind, row["id"]))
            interval = behavior[kind]["interval_seconds"]
            due = _scheduled_now((now or datetime.now(timezone.utc)) + timedelta(seconds=interval))
            database.execute(
                "INSERT INTO mud_scheduled_events(event_kind,entity_id,due_at,interval_seconds) VALUES(?,?,?,?) "
                "ON CONFLICT(event_kind,entity_id) DO UPDATE SET "
                "due_at=CASE WHEN interval_seconds<>excluded.interval_seconds THEN excluded.due_at ELSE due_at END,"
                "interval_seconds=excluded.interval_seconds",
                (kind, row["id"], due, interval),
            )
    for row in database.execute(
        "SELECT event_kind,entity_id FROM mud_scheduled_events WHERE event_kind IN ('ambient','wander','patrol')"
    ).fetchall():
        if (row["event_kind"], row["entity_id"]) not in active:
            database.execute("DELETE FROM mud_scheduled_events WHERE event_kind=? AND entity_id=?", row)


def process_scheduled_events(database, now=None, limit=50):
    """Call inside a write transaction. Advancing a timer and its effects commit together."""
    stamp = _scheduled_now(now)
    rows = database.execute(
        "SELECT * FROM mud_scheduled_events WHERE due_at<=? "
        "AND event_kind IN ('ambient','wander','patrol','room_message','item_respawn',"
        "'trigger_action','trigger_timer','npc_despawn') ORDER BY due_at,id LIMIT ?",
        (stamp, min(50, max(1, limit))),
    ).fetchall()
    for event in rows:
        if event["event_kind"] == "npc_despawn":
            _despawn_npc(database, event, now)
            continue
        if event["event_kind"] in ("trigger_action", "trigger_timer"):
            if event["event_kind"] == "trigger_timer":
                next_due = _scheduled_now((now or datetime.now(timezone.utc)) + timedelta(seconds=event["interval_seconds"]))
                database.execute("UPDATE mud_scheduled_events SET due_at=? WHERE id=?", (next_due, event["id"]))
            for draft in _process_trigger_event(database, event):
                _insert_event(database, draft)
            continue
        if event["event_kind"] == "item_respawn":
            spawn = database.execute("SELECT * FROM mud_item_spawns WHERE id=?", (event["entity_id"],)).fetchone()
            if spawn and spawn["live_instance_id"] is None and database.execute(
                "SELECT 1 FROM mud_world_rooms WHERE id=?", (spawn["room_id"],)).fetchone():
                instance_id = f"world:respawn:{uuid.uuid4().hex}"
                database.execute("INSERT INTO mud_item_instances(id,definition_id,room_id,spawn_source_id) VALUES(?,?,?,?)",
                                 (instance_id, spawn["definition_id"], spawn["room_id"], spawn["id"]))
                database.execute("UPDATE mud_item_spawns SET live_instance_id=? WHERE id=? AND live_instance_id IS NULL",
                                 (instance_id, spawn["id"]))
            database.execute("DELETE FROM mud_scheduled_events WHERE id=?", (event["id"],))
            continue
        if event["event_kind"] == "room_message":
            payload = _json_properties(event["payload_json"])
            room_id = payload.get("room_id")
            message = payload.get("message")
            if database.execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (room_id,)).fetchone() is None:
                database.execute("DELETE FROM mud_scheduled_events WHERE id=?", (event["id"],))
                continue
            active_room = database.execute(
                "SELECT 1 FROM mud_world_rooms WHERE id=? AND world_id=(SELECT id FROM mud_world_packages WHERE active=1)",
                (room_id,),
            ).fetchone()
            if active_room and isinstance(message, str) and message:
                recipients = _occupant_ids(database, room_id)
                if recipients:
                    _insert_event(database, EventDraft(message, "world", "room", room_id, recipients))
            if event["interval_seconds"] is None:
                database.execute("DELETE FROM mud_scheduled_events WHERE id=?", (event["id"],))
            else:
                next_due = _scheduled_now((now or datetime.now(timezone.utc)) + timedelta(seconds=event["interval_seconds"]))
                database.execute("UPDATE mud_scheduled_events SET due_at=? WHERE id=?", (next_due, event["id"]))
            continue
        next_due = _scheduled_now((now or datetime.now(timezone.utc)) + timedelta(seconds=event["interval_seconds"]))
        database.execute("UPDATE mud_scheduled_events SET due_at=? WHERE id=?", (next_due, event["id"]))
        npc = database.execute(
            "SELECT i.*,d.name,d.behaviors_json FROM mud_npc_instances i "
            "JOIN mud_npc_definitions d ON d.id=i.definition_id WHERE i.id=?",
            (event["entity_id"],),
        ).fetchone()
        if npc is None:
            database.execute("DELETE FROM mud_scheduled_events WHERE id=?", (event["id"],))
            continue
        busy = database.execute(
            "SELECT 1 FROM mud_combat_encounters WHERE npc_instance_id=? AND status='active' LIMIT 1",
            (npc["id"],),
        ).fetchone()
        if busy:
            continue
        live_npc = next((row for row in _room_npc_rows(database, npc["room_id"]) if row["id"] == npc["id"]), None)
        if live_npc and live_npc["combat_maximum_health"] is not None:
            if _encounter_row(database, live_npc)["status"] == "defeated":
                continue
        try:
            behavior = validate_npc_behaviors(_json_properties(npc["behaviors_json"]))
        except ValueError:
            database.execute("DELETE FROM mud_scheduled_events WHERE id=?", (event["id"],))
            continue
        spec = behavior.get(event["event_kind"])
        if not isinstance(spec, dict):
            database.execute("DELETE FROM mud_scheduled_events WHERE id=?", (event["id"],))
            continue
        room_id = npc["room_id"]
        if event["event_kind"] == "ambient":
            line = random.choice(spec["lines"])
            recipients = _occupant_ids(database, room_id)
            if recipients:
                _insert_event(database, EventDraft(f"{npc['name']} says: {line}", "npc", "room", room_id, recipients))
            continue
        state = _json_properties(npc["state_json"])
        exits = _room_exits(database, room_id)
        if event["event_kind"] == "patrol":
            directions = spec["directions"]
            index = state.get("patrol_index", 0)
            if type(index) is not int or index < 0:
                index = 0
            direction = directions[index % len(directions)]
            state["patrol_index"] = index + 1
            choices = [exit_row for exit_row in exits if exit_row["direction"] == direction]
        else:
            choices = exits
        if not choices:
            database.execute("UPDATE mud_npc_instances SET state_json=? WHERE id=?", (json.dumps(state), npc["id"]))
            continue
        route = random.choice(choices)
        old_recipients = _occupant_ids(database, room_id)
        new_recipients = _occupant_ids(database, route["to_room_id"])
        database.execute("UPDATE mud_npc_instances SET room_id=?,state_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (route["to_room_id"], json.dumps(state), npc["id"]))
        database.execute("UPDATE mud_combat_encounters SET scope_key=?,updated_at=CURRENT_TIMESTAMP "
                         "WHERE npc_instance_id=? AND scope_type='room' AND status='ready'",
                         (route["to_room_id"], npc["id"]))
        if old_recipients:
            _insert_event(database, EventDraft(f"{npc['name']} leaves via {_exit_label(route)}.", "npc", "room", room_id, old_recipients))
        if new_recipients:
            _insert_event(database, EventDraft(f"{npc['name']} arrives.", "npc", "room", route["to_room_id"], new_recipients))
    return len(rows)


def run_scheduled_tick(app, now=None):
    with app.app_context():
        database = get_db()
        settings = database.execute("SELECT mud_enabled,mud_traditional_enabled FROM station WHERE id=1").fetchone()
        if settings is None or not settings["mud_enabled"] or not settings["mud_traditional_enabled"]:
            return 0
        try:
            database.execute("BEGIN IMMEDIATE")
            sync_npc_schedule(database, now)
            sync_trigger_schedule(database, now)
            count = process_scheduled_events(database, now)
            database.commit()
            return count
        except Exception:
            database.rollback()
            raise


def _npc_reactions(database, user, action_key):
    trigger = {"go": "enter", "say": "say", "emote": "emote", "get": "get",
               "drop": "drop", "open": "open", "close": "close", "put": "put"}.get(action_key)
    if not trigger:
        return []
    room = _current_room(database, user["id"])
    if room.get("instance_id"):
        return []
    drafts = []
    for npc in _room_npc_rows(database, room["id"]):
        reactions = _json_properties(npc["behaviors_json"]).get("reactions")
        response = reactions.get(trigger) if isinstance(reactions, dict) else None
        if response:
            recipients = set(_occupant_ids(database, room["id"]))
            recipients.add(user["id"])
            drafts.append(EventDraft(f"{npc['name']} says: {response}", "npc", "room", room["id"], tuple(sorted(recipients))))
    return drafts


class MudService:
    """Normalizes, authorizes, executes, and records MUD actions atomically."""

    def __init__(self, registry):
        self.registry = registry

    def normalize(self, form):
        command = form.get("command", "").strip()
        message = form.get("message", "").strip()
        if form.get("action", "").strip() or form.get("parameters", "").strip():
            raise ActionError("Use a slash command in the MUD console.")
        if sum(bool(value) for value in (command, message)) != 1:
            raise ActionError("Submit exactly one message or command.")
        request_id = form.get("request_id", "").strip() or uuid.uuid4().hex
        if not REQUEST_ID_RE.fullmatch(request_id):
            raise ActionError("Invalid action request identifier.")
        if message:
            definition = self.registry.get("say")
            parameters = {"message": message}
            # The original schema uses "command" for all text-entry actions.
            method = "command"
        elif command:
            definition, parameters = self.registry.from_command(command)
            method = "command"
        client_id = form.get("client_id", "").strip()
        if client_id and not CLIENT_ID_RE.fullmatch(client_id):
            raise ActionError("Invalid MUD client identifier.")
        return ActionRequest(definition, parameters, method, request_id, client_id)

    def execute(self, action_request, user):
        if not _authorized(action_request.definition, user):
            raise ActionError("You do not have permission to use that action.", 403)
        database = get_db()
        try:
            database.execute("BEGIN IMMEDIATE")
            creation = database.execute(
                "SELECT creation_state FROM mud_characters WHERE user_id=?", (user["id"],)
            ).fetchone()
            if creation is None or creation["creation_state"] == "pending":
                raise ActionError("Create your character before entering the MUD.", 409)
            if mud_compat.incompatible(database, user["id"]):
                raise ActionError("Choose replacement race or class before entering the MUD.", 409)
            if action_request.client_id:
                _register_presence(database, user, action_request.client_id)
            else:
                _connection_id(database, user["id"])
                _prune_presence(database)
            prior = database.execute(
                "SELECT id,action_key FROM mud_action_log WHERE actor_id=? AND request_id=?",
                (user["id"], action_request.request_id),
            ).fetchone()
            if prior is not None:
                if prior["action_key"] != action_request.definition.key:
                    raise ActionError("That action request identifier was already used.", 409)
                events = _events_for_user(
                    database, user["id"], action_id=prior["id"]
                )
                database.commit()
                return prior["action_key"], [dict(row) for row in events]

            pending_death = database.execute(
                "SELECT 1 FROM mud_death_summaries WHERE user_id=?", (user["id"],)
            ).fetchone() is not None
            if pending_death and action_request.definition.key != "continue":
                raise ActionError("Enter /continue to leave the death summary screen.")

            used_item = None
            use_room = None
            if action_request.definition.key == "use":
                use_room = _current_room(database, user["id"])
                target = _item_target(action_request.parameters)
                try:
                    used_item = _item_matches(
                        list(_inventory_rows(database, user["id"])) + list(_inventory_rows(database, user["id"], True)),
                        target,
                    )["name"]
                except ActionError:
                    used_item = None
            drafts = action_request.definition.handler(
                database, user, action_request.parameters
            )
            if used_item:
                drafts.append(_room_item_event(database, user, use_room,
                                               f"{_character_row(database, user['id'])['display_name']} uses {used_item}."))
            drafts += _npc_reactions(database, user, action_request.definition.key)
            cursor = database.execute(
                "INSERT INTO mud_action_log(request_id,actor_id,action_key,input_method,parameters_json) "
                "VALUES(?,?,?,?,?)",
                (
                    action_request.request_id, user["id"],
                    action_request.definition.key, action_request.input_method,
                    json.dumps(action_request.parameters, separators=(",", ":"), sort_keys=True),
                ),
            )
            event_rows = []
            for draft in drafts:
                event = _insert_event(
                    database, draft, default_user_id=user["id"],
                    action_id=cursor.lastrowid,
                )
                visible = (draft.audience_type == "private" and
                           (draft.audience_key is None or draft.audience_key == str(user["id"]))) or (
                    draft.recipient_ids is not None and user["id"] in draft.recipient_ids
                )
                if visible:
                    event_rows.append(event)
            database.commit()
            return action_request.definition.key, [dict(row) for row in event_rows]
        except (ActionError, sqlite3.Error):
            database.rollback()
            raise


service = MudService(actions)


def _require_enabled():
    if not g.station["mud_enabled"]:
        abort(404)


def _recent_events(database, user_id):
    cutoff = _event_cutoff(database, user_id)
    rows = database.execute(
        "SELECT * FROM (SELECT e.id,e.event_type,e.body,e.created_at "
        "FROM mud_events AS e WHERE e.id>? AND ("
        "(e.audience_type='private' AND e.audience_key=?) OR EXISTS ("
        "SELECT 1 FROM mud_event_recipients AS r "
        "WHERE r.event_id=e.id AND r.user_id=?)) "
        "ORDER BY e.id DESC LIMIT 100) ORDER BY id",
        (cutoff, str(user_id), user_id),
    ).fetchall()
    return [dict(row) for row in rows]


@bp.get("")
@login_required
def index():
    _require_enabled()
    database = get_db()
    creation = database.execute(
        "SELECT creation_state FROM mud_characters WHERE user_id=?", (g.user["id"],)
    ).fetchone()
    if creation is None or creation["creation_state"] == "pending":
        return redirect(url_for("mud.create_character"))
    if mud_compat.incompatible(database, g.user["id"]):
        return redirect(url_for("mud.character_compatibility"))
    database.execute("BEGIN IMMEDIATE")
    _prune_presence(database)
    room = _room_state(database, g.user["id"])
    character = character_state(database, g.user["id"])
    inventory = inventory_state(database, g.user["id"])
    hub = hub_state(database, g.user["id"])
    quests = quest_state(database, g.user["id"])
    combat = combat_state(database, g.user["id"])
    events = _recent_events(database, g.user["id"])
    cleared_view = database.execute(
        "SELECT cleared_through_id FROM mud_event_views WHERE user_id=?",
        (g.user["id"],),
    ).fetchone()
    cleared_through_id = cleared_view["cleared_through_id"] if cleared_view else 0
    database.commit()
    return render_template(
        "mud/index.html", events=events, cleared_through_id=cleared_through_id,
        activity_cleared=cleared_view is not None,
        room=room, character=character, inventory=inventory,
        quests=quests, combat=combat, hub=hub,
        command_catalog=[
            {"name": name, "key": definition.key,
             "description": _action_description(definition, character["resource"])}
            for name, (definition, prefix) in actions._commands.items()
            if not prefix and _authorized(definition, g.user)
            and (character["resource"]["enabled"] or definition.key not in RESOURCE_ACTIONS)
        ],
    )


@bp.get("/updates")
@login_required
def updates():
    _require_enabled()
    creation = get_db().execute(
        "SELECT creation_state FROM mud_characters WHERE user_id=?", (g.user["id"],)
    ).fetchone()
    if creation is None or creation["creation_state"] == "pending":
        return jsonify(error="Create your character before entering the MUD."), 409
    if mud_compat.incompatible(get_db(), g.user["id"]):
        return jsonify(error="Choose replacement race or class before entering the MUD.",
                       compatibility_url=url_for("mud.character_compatibility")), 409
    client_id = request.args.get("client_id", "").strip()
    after = max(0, request.args.get("after", 0, type=int))
    database = get_db()
    try:
        database.execute("BEGIN IMMEDIATE")
        _register_presence(database, g.user, client_id)
        events = [dict(row) for row in _events_for_user(
            database, g.user["id"], after=after
        )]
        room = _room_state(database, g.user["id"])
        character = character_state(database, g.user["id"])
        inventory = inventory_state(database, g.user["id"])
        hub = hub_state(database, g.user["id"])
        quests = quest_state(database, g.user["id"])
        combat = combat_state(database, g.user["id"])
        database.commit()
    except (ActionError, sqlite3.Error) as problem:
        database.rollback()
        if isinstance(problem, ActionError):
            return jsonify(error=str(problem)), problem.status
        raise
    return jsonify(events=events, room=room, character=character, inventory=inventory,
                   quests=quests, combat=combat, hub=hub)


@bp.post("/clear-events")
@login_required
def clear_events():
    _require_enabled()
    validate_csrf()
    database = get_db()
    database.execute("BEGIN IMMEDIATE")
    cutoff = database.execute("SELECT COALESCE(MAX(id),0) FROM mud_events").fetchone()[0]
    database.execute(
        "INSERT INTO mud_event_views(user_id,cleared_through_id) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "cleared_through_id=MAX(cleared_through_id,excluded.cleared_through_id)",
        (g.user["id"], cutoff),
    )
    database.commit()
    return jsonify(cleared_through_id=cutoff)


@bp.route("/create", methods=["GET", "POST"])
@login_required
def create_character():
    _require_enabled()
    database = get_db()
    row = database.execute(
        "SELECT id,display_name,creation_state FROM mud_characters WHERE user_id=?",
        (g.user["id"],),
    ).fetchone()
    if row is None:
        _character_row(database, g.user["id"])
        row = database.execute(
            "SELECT id,display_name,creation_state FROM mud_characters WHERE user_id=?",
            (g.user["id"],),
        ).fetchone()
        database.commit()
    if row["creation_state"] == "complete":
        return redirect(url_for("mud.character_compatibility") if mud_compat.incompatible(database, g.user["id"])
                        else url_for("mud.index"))
    config = character_creation.options()
    config["skill_names"] = {key: definition["name"]
                             for key, definition in mud_skills.definitions(database).items()}
    config["effect_names"] = mud_skills.EFFECT_TYPES
    config["resource"] = resource = resource_settings(database, g.user["id"])
    # Without the resource, the built-in Intellect text would promise mana that does not exist.
    config["attribute_descriptions"] = {
        key: ("Raises healing and experience earned."
              if key == "intellect" and not resource["enabled"] and _RESOURCE_WORD_RE.search(text)
              else resource_text(text, resource))
        for key, text in config["attribute_descriptions"].items()}
    error = None
    selected = {}
    if request.method == "POST":
        validate_csrf()
        try:
            selected = json.loads(request.form.get("build", ""))
            if not isinstance(selected, dict):
                raise ValueError("Invalid character build.")
            database.execute("BEGIN IMMEDIATE")
            character_creation.complete(database, g.user["id"], selected,
                                        _current_mode(database, g.user["id"]))
            database.commit()
            return redirect(url_for("mud.index"))
        except (ValueError, json.JSONDecodeError, sqlite3.IntegrityError) as problem:
            database.rollback()
            error = ("Character choices could not be saved. Please try again."
                     if isinstance(problem, sqlite3.IntegrityError) else str(problem))
            if not isinstance(selected, dict):
                selected = {}
    archive_notice = database.execute(
        "SELECT a.world_name FROM mud_campaign_archives a "
        "JOIN mud_campaign_characters c ON c.archive_id=a.id "
        "WHERE c.user_id=? ORDER BY a.id DESC LIMIT 1", (g.user["id"],)).fetchone()
    return render_template("mud/create.html", config=config, character=row,
                           selected=selected, error=error, archive_notice=archive_notice)


@bp.route("/character/compatibility", methods=["GET", "POST"])
@login_required
def character_compatibility():
    _require_enabled()
    database = get_db()
    character = database.execute("SELECT * FROM mud_characters WHERE user_id=?", (g.user["id"],)).fetchone()
    if character is None or character["creation_state"] == "pending":
        return redirect(url_for("mud.create_character"))
    missing = mud_compat.incompatible(database, g.user["id"])
    if not missing:
        return redirect(url_for("mud.index"))
    error = None
    if request.method == "POST":
        validate_csrf()
        try:
            database.execute("BEGIN IMMEDIATE")
            mud_compat.replace_origins(database, g.user["id"], request.form.get("race", ""),
                                       request.form.get("class", ""))
            _insert_event(database, EventDraft(
                "Your character's origins were updated. Your level, experience, identity, earned skills, "
                "inventory and quest progress were preserved.", event_type="character",
                recipient_ids=(g.user["id"],)), default_user_id=g.user["id"])
            database.commit()
            return redirect(url_for("mud.index"))
        except (ValueError, sqlite3.Error) as problem:
            database.rollback()
            error = str(problem)
    entries = character_creation.catalog(database, include_removed=True)
    current = {"races": character["race_key"], "classes": character["class_key"]}
    return render_template("mud/compatibility.html", character=character, missing=missing,
                           entries=entries, current=current, error=error,
                           resource=resource_settings(database, g.user["id"]))


@bp.route("/character/delete", methods=["GET", "POST"])
@login_required
def delete_character():
    _require_enabled()
    database = get_db()
    character = database.execute(
        "SELECT id,display_name,creation_state FROM mud_characters WHERE user_id=?",
        (g.user["id"],),
    ).fetchone()
    if character is None or character["creation_state"] == "pending":
        return redirect(url_for("mud.create_character"))
    error = None
    if request.method == "POST":
        validate_csrf()
        if request.form.get("confirm_name", "").strip() != character["display_name"]:
            error = "Enter your character name exactly to confirm deletion."
        else:
            user_id = g.user["id"]
            database.execute("BEGIN IMMEDIATE")
            try:
                # These records are keyed by account rather than character and would
                # otherwise carry the old character into a new save.
                for table in (
                    "mud_presence", "mud_player_locations", "mud_world_locations",
                    "mud_player_stealth", "mud_death_summaries", "mud_event_views",
                    "mud_event_recipients",
                ):
                    database.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
                database.execute("DELETE FROM mud_action_log WHERE actor_id=?", (user_id,))
                database.execute(
                    "DELETE FROM mud_events WHERE audience_type='private' AND audience_key=?",
                    (str(user_id),),
                )
                database.execute(
                    "DELETE FROM mud_active_effects WHERE target_type='character' AND target_id=?",
                    (character["id"],),
                )
                # Contained items have no owner_id. Remove them before their
                # containers so the location CHECK remains satisfied.
                owned_items = database.execute(
                    "WITH RECURSIVE owned(id,depth) AS ("
                    "SELECT id,0 FROM mud_item_instances WHERE owner_character_id=? "
                    "UNION ALL SELECT i.id,owned.depth+1 FROM mud_item_instances AS i "
                    "JOIN owned ON i.container_id=owned.id) "
                    "SELECT id FROM owned ORDER BY depth DESC", (character["id"],)
                ).fetchall()
                database.executemany("DELETE FROM mud_item_instances WHERE id=?",
                                     ((item["id"],) for item in owned_items))
                database.execute("DELETE FROM mud_characters WHERE id=?", (character["id"],))
                _character_row(database, user_id)
                database.executemany(
                    "INSERT OR IGNORE INTO mud_character_progression(character_id,track_key) "
                    "VALUES(?,?)", ((character["id"], track) for track in PROGRESSION_TRACKS)
                )
                database.commit()
                flash("Character deleted. You can create a new one.", "success")
                return redirect(url_for("mud.create_character"))
            except sqlite3.Error:
                database.rollback()
                raise
    return render_template("mud/delete.html", character=character, error=error)


@bp.post("/disconnect")
@login_required
def disconnect():
    _require_enabled()
    validate_csrf()
    client_id = request.form.get("client_id", "").strip()
    if not CLIENT_ID_RE.fullmatch(client_id):
        return jsonify(error="Invalid MUD client identifier."), 400
    database = get_db()
    connection_id = session.get("connection_id", "")
    database.execute(
        "UPDATE mud_presence SET disconnected_at=? "
        "WHERE user_id=? AND connection_id=? AND client_id=?",
        (datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
         g.user["id"], connection_id, client_id),
    )
    database.commit()
    return ("", 204)


@bp.post("/actions")
@login_required
def perform_action():
    _require_enabled()
    validate_csrf()
    creation = get_db().execute(
        "SELECT creation_state FROM mud_characters WHERE user_id=?", (g.user["id"],)
    ).fetchone()
    if creation is None or creation["creation_state"] == "pending":
        return jsonify(error="Create your character before entering the MUD."), 409
    if mud_compat.incompatible(get_db(), g.user["id"]):
        return jsonify(error="Choose replacement race or class before entering the MUD.",
                       compatibility_url=url_for("mud.character_compatibility")), 409
    try:
        action_request = service.normalize(request.form)
        action_key, events = service.execute(action_request, g.user)
    except ActionError as problem:
        database = get_db()
        room = _room_state(database, g.user["id"])
        character = character_state(database, g.user["id"])
        inventory = inventory_state(database, g.user["id"])
        hub = hub_state(database, g.user["id"])
        quests = quest_state(database, g.user["id"])
        combat = combat_state(database, g.user["id"])
        database.commit()
        return jsonify(
            error=str(problem), room=room, character=character, inventory=inventory,
            quests=quests, combat=combat, hub=hub
        ), problem.status
    database = get_db()
    room = _room_state(database, g.user["id"])
    character = character_state(database, g.user["id"])
    inventory = inventory_state(database, g.user["id"])
    hub = hub_state(database, g.user["id"])
    quests = quest_state(database, g.user["id"])
    combat = combat_state(database, g.user["id"])
    database.commit()
    return jsonify(
        action=action_key, events=events,
        room=room, character=character, inventory=inventory,
        quests=quests, combat=combat, hub=hub,
    )


def _builder_redirect(room_id=None):
    # Forms in the wilderness cell inspector reuse these routes and return to the inspector.
    if request.method == "POST" and request.form.get("return_to") == "wilderness":
        cell = request.form.get("return_cell", "")
        return redirect(url_for("mud.builder_wilderness", cell=cell or None,
                                _anchor="cell-inspector" if cell else None))
    anchor = f"room-{room_id}" if room_id else None
    return redirect(url_for("mud.builder", _anchor=anchor))


def _valid_room_fields():
    name = " ".join(request.form.get("name", "").split())
    description = request.form.get("description", "").strip()
    if not 2 <= len(name) <= 80:
        return None, None, "Room names must be between 2 and 80 characters."
    if not 1 <= len(description) <= 4000:
        return None, None, "Room descriptions must be between 1 and 4000 characters."
    return name, description, None


def _valid_exit_fields(database, exit_id=None):
    from_room_id = request.form.get("from_room_id", "").strip()
    to_room_id = request.form.get("to_room_id", "").strip()
    name = " ".join(request.form.get("name", "").split())
    direction = request.form.get("direction", "").strip().casefold() or None
    key_item_id = request.form.get("key_item_id", "").strip() or None
    pick_raw = request.form.get("pick_difficulty", "").strip()
    room_ids = {
        row["id"] for row in database.execute(
            "SELECT id FROM mud_world_rooms WHERE id IN (?,?) AND world_id=(SELECT id FROM mud_world_packages WHERE active=1)",
            (from_room_id, to_room_id)
        )
    }
    if from_room_id not in room_ids or to_room_id not in room_ids:
        return None, "Choose existing source and destination rooms."
    if not 1 <= len(name) <= 80:
        return None, "Exit names must be between 1 and 80 characters."
    if direction is not None and direction not in DIRECTIONS:
        return None, "Choose a supported direction or leave direction blank for a named exit."
    if key_item_id and database.execute("SELECT 1 FROM mud_item_definitions WHERE id=? AND item_type='key'",
                                        (key_item_id,)).fetchone() is None:
        return None, "Choose an existing key item."
    try:
        pick_difficulty = int(pick_raw) if pick_raw else None
    except ValueError:
        pick_difficulty = 0
    if pick_difficulty is not None and not 1 <= pick_difficulty <= 200:
        return None, "Lock difficulty must be blank (cannot be picked) or 1 to 200."
    duplicate = database.execute(
        "SELECT id FROM mud_world_exits WHERE from_room_id=? AND id<>COALESCE(?, '') "
        "AND (name=? COLLATE NOCASE OR (? IS NOT NULL AND direction=? COLLATE NOCASE))",
        (from_room_id, exit_id, name, direction, direction),
    ).fetchone()
    if duplicate:
        return None, "That source room already has an exit with this name or direction."
    conditions, error = _builder_rules(database, "conditions_json", mud_conditions.validate_conditions)
    if error:
        return None, f"Exit conditions: {error}"
    message = " ".join(request.form.get("conditions_message", "").split())
    if len(message) > 200:
        return None, "The barred-exit message cannot exceed 200 characters."
    hidden = int(request.form.get("hidden") == "1")
    return (from_room_id, to_room_id, name, direction, key_item_id, pick_difficulty, conditions, message, hidden), None


@bp.get("/builder")
@builder_required
def builder():
    database = get_db()
    room_rows = database.execute(
        "SELECT r.*,COUNT(DISTINCT l.user_id) AS located_players,"
        "COUNT(DISTINCT p.user_id) AS present_players "
        "FROM mud_world_rooms AS r "
        "LEFT JOIN mud_world_locations AS l ON l.room_id=r.id "
        "LEFT JOIN mud_presence AS p ON p.room_id=r.id "
        "WHERE r.world_id=(SELECT id FROM mud_world_packages WHERE active=1) AND r.kind='authored' "
        "GROUP BY r.id ORDER BY r.is_start DESC,r.name COLLATE NOCASE,r.id"
    ).fetchall()
    # Wilderness rooms are edited on the wilderness page; listing them here would not scale.
    rooms = [dict(row) for row in room_rows]
    exits_by_room = {room["id"]: [] for room in rooms}
    for row in database.execute(
        "SELECT e.*,destination.name AS destination_name "
        "FROM mud_world_exits AS e "
        "JOIN mud_world_rooms AS destination ON destination.id=e.to_room_id "
        "ORDER BY e.from_room_id,e.direction IS NULL,e.direction,e.name COLLATE NOCASE"
    ):
        if row["from_room_id"] not in exits_by_room:
            continue
        exit_row = dict(row)
        if not exit_row["destination_name"]:
            exit_row["destination_name"] = _world_room(database, row["to_room_id"])["name"]
        exits_by_room[row["from_room_id"]].append(exit_row)
    users = database.execute(
        "SELECT u.id,u.username,u.callsign,u.is_admin,"
        "CASE WHEN p.user_id IS NULL THEN 0 ELSE 1 END AS is_builder "
        "FROM users AS u LEFT JOIN mud_builder_permissions AS p ON p.user_id=u.id "
        "ORDER BY u.is_admin DESC,u.callsign COLLATE NOCASE"
    ).fetchall() if g.user["is_admin"] else []
    definitions = []
    for row in database.execute(
        "SELECT d.* FROM mud_item_definitions AS d WHERE d.world_id=(SELECT id FROM mud_world_packages WHERE active=1) AND NOT EXISTS "
        "(SELECT 1 FROM mud_dungeon_managed_definitions AS m WHERE m.definition_id=d.id) "
        "ORDER BY d.name COLLATE NOCASE,d.id"
    ):
        item = dict(row); properties = _json_properties(item["properties_json"])
        item["attack_bonus"] = _integer_property(properties, "damage", "attack", "attack_bonus")
        item["defense_bonus"] = _integer_property(properties, "armor", "defense", "defense_bonus")
        item["firearm"] = mud_firearms.is_firearm(properties)
        item["ammo_type"] = properties.get("ammo_type", "")
        item["magazine_size"] = properties.get("magazine_size", 0)
        item["rounds"] = properties.get("rounds", 0)
        item["use_message"] = properties.get("use_message", "")
        item["heal"] = properties.get("heal", 0)
        item["value"] = properties.get("value", 0)
        item["on_use_effects_json"] = json.dumps(properties.get("on_use_effects", []))
        item["on_hit_effects_json"] = json.dumps(properties.get("on_hit_effects", []))
        item["requirements_json"] = json.dumps(properties.get("requirements", {}))
        item["category"] = _item_category(properties) or ""
        definitions.append(item)
    items_by_room = {room["id"]: [] for room in rooms}
    for row in database.execute("SELECT i.id,i.room_id,d.name,d.item_type,s.respawn_seconds FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id LEFT JOIN mud_item_spawns s ON s.live_instance_id=i.id WHERE i.room_id IS NOT NULL ORDER BY d.name COLLATE NOCASE"):
        items_by_room.setdefault(row["room_id"], []).append(dict(row))
    spawns_by_room = {room["id"]: [] for room in rooms}
    for row in database.execute("SELECT s.*,d.name FROM mud_item_spawns s JOIN mud_item_definitions d ON d.id=s.definition_id ORDER BY s.room_id,d.name"):
        spawns_by_room.setdefault(row["room_id"], []).append(dict(row))
    discoveries_by_room = {room["id"]: [] for room in rooms}
    for row in database.execute("SELECT * FROM mud_room_discoveries ORDER BY room_id,id"):
        discoveries_by_room.setdefault(row["room_id"], []).append(dict(row))
    conversations = [dict(row) for row in database.execute(
        "SELECT c.* FROM mud_conversations AS c WHERE c.world_id=(SELECT id FROM mud_world_packages WHERE active=1) AND NOT EXISTS "
        "(SELECT 1 FROM mud_dungeon_managed_definitions AS m WHERE m.definition_id=c.id) "
        "ORDER BY c.name COLLATE NOCASE,c.id"
    )]
    nodes_by_conversation = {item["id"]: [] for item in conversations}
    responses_by_node = {}
    for row in database.execute(
        "SELECT * FROM mud_dialogue_nodes ORDER BY conversation_id,is_start DESC,node_key COLLATE NOCASE,id"
    ):
        node = dict(row); nodes_by_conversation.setdefault(row["conversation_id"], []).append(node)
        responses_by_node[node["id"]] = [dict(response) for response in database.execute(
            "SELECT * FROM mud_dialogue_responses WHERE node_id=? ORDER BY sort_order,id", (node["id"],)
        )]
    npc_definitions = [dict(row) for row in database.execute(
        "SELECT d.*,p.maximum_health AS combat_maximum_health,p.attack AS combat_attack,"
        "p.defense AS combat_defense,p.accuracy AS combat_accuracy,p.evasion AS combat_evasion,"
        "p.experience_reward,p.respawn_seconds "
        "FROM mud_npc_definitions AS d LEFT JOIN mud_npc_combat_profiles AS p "
        "ON p.npc_definition_id=d.id WHERE d.world_id=(SELECT id FROM mud_world_packages WHERE active=1) AND NOT EXISTS "
        "(SELECT 1 FROM mud_dungeon_managed_definitions AS m WHERE m.definition_id=d.id) "
        "ORDER BY d.name COLLATE NOCASE,d.id"
    )]
    npc_equipment = {item["id"]: {} for item in npc_definitions}
    npc_combat_rewards = {item["id"]: [] for item in npc_definitions}
    shop_stock = {item["id"]: [] for item in npc_definitions}
    npc_shops = {row["npc_definition_id"]: dict(row) for row in database.execute("SELECT * FROM mud_npc_shops")}
    for row in database.execute("SELECT s.*,d.name AS item_name FROM mud_shop_stock s JOIN mud_item_definitions d ON d.id=s.item_definition_id ORDER BY d.name COLLATE NOCASE"):
        shop_stock.setdefault(row["npc_definition_id"], []).append(dict(row))
    for row in database.execute("SELECT * FROM mud_npc_equipment"):
        npc_equipment.setdefault(row["npc_definition_id"], {})[row["equipment_slot"]] = row["item_definition_id"]
    skill_entries = mud_skills.catalog(database)
    skill_names = {entry["key"]: entry["definition"]["name"] for entry in skill_entries}
    npc_trainers = {item["id"]: [] for item in npc_definitions}
    for row in database.execute("SELECT * FROM mud_npc_trainers ORDER BY skill_key"):
        npc_trainers.setdefault(row["npc_definition_id"], []).append(
            {**dict(row), "skill_name": skill_names.get(row["skill_key"], row["skill_key"])})
    item_categories = sorted({category for entry in skill_entries
                              for kind in ("weapons", "armor")
                              for category in entry["definition"]["proficiencies"][kind]}
                             | {item["category"] for item in definitions if item["category"]})
    for row in database.execute(
        "SELECT r.*,d.name AS item_name FROM mud_npc_combat_rewards AS r "
        "LEFT JOIN mud_item_definitions AS d ON d.id=r.target_id "
        "ORDER BY r.npc_definition_id,r.id"
    ):
        npc_combat_rewards.setdefault(row["npc_definition_id"], []).append(dict(row))
    npcs_by_room = {room["id"]: [] for room in rooms}
    for row in database.execute(
        "SELECT i.id,i.room_id,d.name FROM mud_npc_instances AS i JOIN mud_npc_definitions AS d "
        "ON d.id=i.definition_id ORDER BY d.name COLLATE NOCASE,i.id"
    ):
        npcs_by_room.setdefault(row["room_id"], []).append(dict(row))
    quests = [dict(row) for row in database.execute(
        "SELECT * FROM mud_quests WHERE world_id=(SELECT id FROM mud_world_packages WHERE active=1) ORDER BY name COLLATE NOCASE,id"
    )]
    objectives_by_quest = {item["id"]: [] for item in quests}
    rewards_by_quest = {item["id"]: [] for item in quests}
    givers_by_quest = {item["id"]: set() for item in quests}
    for row in database.execute("SELECT * FROM mud_quest_objectives ORDER BY quest_id,sort_order,id"):
        objectives_by_quest.setdefault(row["quest_id"], []).append(dict(row))
    for row in database.execute("SELECT * FROM mud_quest_rewards ORDER BY quest_id,id"):
        rewards_by_quest.setdefault(row["quest_id"], []).append(dict(row))
    for row in database.execute("SELECT * FROM mud_quest_givers"):
        givers_by_quest.setdefault(row["quest_id"], set()).add(row["npc_definition_id"])
    dungeon_context = _builder_dungeon_context(database)
    trigger_context = _builder_trigger_context(database, rooms, exits_by_room, items_by_room, npcs_by_room,
                                               nodes_by_conversation, responses_by_node, conversations)
    return render_template(
        "mud/builder.html", rooms=rooms, exits_by_room=exits_by_room,
        users=users, directions=DIRECTIONS, definitions=definitions, items_by_room=items_by_room,
        spawns_by_room=spawns_by_room,
        conversations=conversations, nodes_by_conversation=nodes_by_conversation,
        responses_by_node=responses_by_node, npc_definitions=npc_definitions,
        npcs_by_room=npcs_by_room, quests=quests, objectives_by_quest=objectives_by_quest,
        rewards_by_quest=rewards_by_quest, givers_by_quest=givers_by_quest,
        npc_equipment=npc_equipment, npc_combat_rewards=npc_combat_rewards,
        npc_shops=npc_shops, shop_stock=shop_stock, discoveries_by_room=discoveries_by_room,
        skill_entries=[{**entry, "summary": mud_skills.describe_effects(entry["definition"])}
                       for entry in skill_entries],
        npc_trainers=npc_trainers, item_categories=item_categories,
        resource=resource_settings(database), **dungeon_context, **trigger_context,
    )


def _builder_dungeon_context(database):
    """World dungeon templates, entrances by room, clone sources, and an optional unsaved preview."""
    from . import mud_world_dungeons
    world = database.execute("SELECT id FROM mud_world_packages WHERE active=1").fetchone()
    if world is None:
        return {"dungeon_templates": [], "entrances_by_room": {}, "dungeon_bases": [],
                "dungeon_layouts": sorted(dungeons.SUPPORTED_LAYOUTS), "dungeon_preview": None}
    templates = []
    for row in mud_world_dungeons.world_templates(database, world["id"]):
        template = json.loads(row["template_json"])
        generation = template.get("generation", {})
        templates.append({**dict(row), "json": json.dumps(template, ensure_ascii=False, indent=2),
                          "size": generation.get("size", {}), "difficulty": generation.get("difficulty", {}),
                          "layouts": {entry.get("type"): entry.get("weight", 1)
                                      for entry in generation.get("layouts", [])},
                          "entrance_count": database.execute(
                              "SELECT COUNT(*) FROM mud_world_dungeon_entrances WHERE template_id=?",
                              (row["id"],)).fetchone()[0],
                          "active_runs": database.execute(
                              "SELECT COUNT(*) FROM mud_procedural_instances WHERE theme_id=? AND status='active'",
                              (row["theme_id"],)).fetchone()[0]})
    entrances_by_room = {}
    for row in mud_world_dungeons.world_entrances(database, world["id"]):
        entry = dict(row)
        entry["conditions_text"] = "" if row["conditions_json"] in (None, "", "[]") else row["conditions_json"]
        entrances_by_room.setdefault(row["room_id"], []).append(entry)
    try:
        bases = sorted(((key, template["name"]) for key, (_path, template) in dungeons.discover_templates().items()),
                       key=lambda item: item[1].casefold())
    except dungeons.TemplateError:
        bases = []
    preview = None
    preview_id = request.args.get("preview")
    if preview_id:
        row = database.execute("SELECT * FROM mud_world_dungeon_templates WHERE id=? AND world_id=?",
                               (preview_id, world["id"])).fetchone()
        if row is not None:
            seed = request.args.get("seed", "").strip()[:64] or secrets.token_hex(8)
            try:
                manifest = dungeons.generate_manifest(mud_world_dungeons.effective_template(row), seed,
                                                      database=database)
                text = dungeons.format_preview(manifest)
            except (dungeons.TemplateError, dungeons.GenerationError) as problem:
                text = f"Preview failed: {problem}"
            preview = {"template_id": row["id"], "name": row["name"], "seed": seed, "text": text}
    return {"dungeon_templates": templates, "entrances_by_room": entrances_by_room, "dungeon_bases": bases,
            "dungeon_layouts": sorted(dungeons.SUPPORTED_LAYOUTS), "dungeon_preview": preview}


@bp.get("/builder/export")
@builder_required
def builder_export_world():
    from . import mud_worlds
    database = get_db()
    active = database.execute("SELECT id FROM mud_world_packages WHERE active=1").fetchone()
    if active is None:
        abort(404)
    try:
        payload = mud_worlds.export(database, active["id"])
    except mud_worlds.WorldError as error:
        flash(f"World export failed: {error}", "error")
        return redirect(url_for("mud.builder"))
    return send_file(BytesIO(payload), mimetype="application/zip", as_attachment=True,
                     download_name=f"{active['id']}-world.zip")


def _item_definition_fields():
    name = " ".join(request.form.get("name", "").split())
    description = request.form.get("description", "").strip()
    item_type = request.form.get("item_type", "misc")
    slot = request.form.get("equipment_slot", "").strip() or None
    use_message = request.form.get("use_message", "").strip()
    attack_bonus = request.form.get("attack_bonus", 0, type=int)
    defense_bonus = request.form.get("defense_bonus", 0, type=int)
    firearm = request.form.get("firearm") == "1"
    ammo_type = request.form.get("ammo_type", "").strip()
    magazine_size = request.form.get("magazine_size", 0, type=int)
    rounds = request.form.get("rounds", 0, type=int)
    heal = request.form.get("heal", 0, type=int)
    value = request.form.get("value", 0, type=int)
    category = request.form.get("category", "").strip().casefold()
    if not 2 <= len(name) <= 80 or not 1 <= len(description) <= 4000:
        return None, "Item names must be 2–80 characters and descriptions 1–4000 characters."
    if item_type not in {"misc", "equipment", "container", "consumable", "key"}:
        return None, "Choose a valid item type."
    if slot not in EQUIPMENT_SLOTS:
        if slot is not None: return None, "Choose a valid equipment slot."
    if item_type != "equipment": slot = None
    if len(use_message) > 1000: return None, "Use messages cannot exceed 1000 characters."
    if attack_bonus is None or defense_bonus is None or not 0 <= attack_bonus <= 100000 or not 0 <= defense_bonus <= 100000:
        return None, "Combat bonuses must be between 0 and 100,000."
    if heal is None or value is None or not 0 <= heal <= 100000 or not 0 <= value <= 100000:
        return None, "Healing and value must be between 0 and 100,000."
    if heal and item_type != "consumable":
        return None, "Healing applies only to consumables."
    properties = {}
    try:
        requirements = json.loads(request.form.get("requirements", "{}") or "{}")
        _item_requirements({"requirements": requirements})
    except (ValueError, json.JSONDecodeError) as problem:
        return None, f"Invalid equipment requirements: {problem}"
    if requirements and item_type != "equipment":
        return None, "Requirements apply only to equipment."
    if requirements:
        properties["requirements"] = requirements
    if category:
        if item_type != "equipment" or not character_creation.KEY_RE.fullmatch(category):
            return None, "Categories apply to equipment and must be lowercase keys such as blade or heavy."
        properties["category"] = category
    if use_message: properties["use_message"] = use_message
    if attack_bonus: properties["attack_bonus"] = attack_bonus
    if defense_bonus: properties["defense_bonus"] = defense_bonus
    if heal: properties["heal"] = heal
    if value: properties["value"] = value
    if firearm:
        properties.update(firearm=True, ammo_type=ammo_type, magazine_size=magazine_size)
    elif ammo_type or rounds:
        properties.update(ammo_type=ammo_type, rounds=rounds)
    firearm_error = mud_firearms.validate_item(item_type, slot, properties)
    if firearm_error:
        return None, firearm_error
    for field in ("on_use_effects", "on_hit_effects"):
        try:
            specs = json.loads(request.form.get(field, "[]") or "[]")
            if not isinstance(specs, list) or len(specs) > 10:
                raise ValueError("Use an array of up to 10 effects.")
            for spec in specs:
                mud_effects.validate(spec)
        except (ValueError, json.JSONDecodeError) as problem:
            return None, f"Invalid {field}: {problem}"
        if specs:
            properties[field] = specs
    return (name, description, item_type, json.dumps(properties, separators=(",", ":"), sort_keys=True), slot), None


def _json_object_field(name):
    raw = request.form.get(name, "{}").strip() or "{}"
    if len(raw) > 10000:
        return None, f"{name.replace('_', ' ').title()} cannot exceed 10,000 characters."
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"{name.replace('_', ' ').title()} must be valid JSON."
    if not isinstance(value, dict):
        return None, f"{name.replace('_', ' ').title()} must be a JSON object."
    return json.dumps(value, separators=(",", ":"), sort_keys=True), None


def _managed_dungeon_definition(database, definition_id):
    return database.execute(
        "SELECT 1 FROM mud_dungeon_managed_definitions WHERE definition_id=?",
        (definition_id,),
    ).fetchone() is not None


def _managed_dungeon_conversation(database, conversation_id):
    return _managed_dungeon_definition(database, conversation_id)


@bp.post("/builder/conversations")
@builder_required
def builder_create_conversation():
    validate_csrf(); name = " ".join(request.form.get("name", "").split())
    if not 2 <= len(name) <= 80:
        flash("Conversation names must be between 2 and 80 characters.", "error"); return _builder_redirect()
    database = get_db(); conversation_id = f"world:conversation:{uuid.uuid4().hex}"
    database.execute("INSERT INTO mud_conversations(id,name,created_by,world_id) VALUES(?,?,?,(SELECT id FROM mud_world_packages WHERE active=1))", (conversation_id, name, g.user["id"]))
    database.commit(); flash(f"Conversation {name} created.", "success"); return _builder_redirect()


@bp.post("/builder/conversations/<path:conversation_id>")
@builder_required
def builder_update_conversation(conversation_id):
    validate_csrf(); database = get_db(); name = " ".join(request.form.get("name", "").split())
    if _managed_dungeon_conversation(database, conversation_id): abort(404)
    if database.execute("SELECT 1 FROM mud_conversations WHERE id=?", (conversation_id,)).fetchone() is None: abort(404)
    if not 2 <= len(name) <= 80: flash("Conversation names must be between 2 and 80 characters.", "error"); return _builder_redirect()
    database.execute("UPDATE mud_conversations SET name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (name, conversation_id)); database.commit()
    flash("Conversation updated.", "success"); return _builder_redirect()


def _dialogue_node_fields():
    key = " ".join(request.form.get("node_key", "").split()); body = request.form.get("body", "").strip()
    if not 1 <= len(key) <= 80 or not 1 <= len(body) <= 4000:
        return None, "Dialogue keys must be 1–80 characters and dialogue text 1–4000 characters."
    return (key, body, request.form.get("is_start") == "1"), None


@bp.post("/builder/conversations/<path:conversation_id>/nodes")
@builder_required
def builder_create_dialogue_node(conversation_id):
    validate_csrf(); database = get_db(); fields, error = _dialogue_node_fields()
    if _managed_dungeon_conversation(database, conversation_id): abort(404)
    if database.execute("SELECT 1 FROM mud_conversations WHERE id=?", (conversation_id,)).fetchone() is None: abort(404)
    if error: flash(error, "error"); return _builder_redirect()
    node_id = f"world:dialogue-node:{uuid.uuid4().hex}"
    try:
        if fields[2]: database.execute("UPDATE mud_dialogue_nodes SET is_start=0 WHERE conversation_id=?", (conversation_id,))
        database.execute("INSERT INTO mud_dialogue_nodes(id,conversation_id,node_key,body,is_start) VALUES(?,?,?,?,?)", (node_id, conversation_id, fields[0], fields[1], int(fields[2])))
        database.commit()
    except sqlite3.IntegrityError:
        database.rollback(); flash("Dialogue node keys must be unique within a conversation.", "error"); return _builder_redirect()
    flash("Dialogue node created.", "success"); return _builder_redirect()


@bp.post("/builder/dialogue-nodes/<path:node_id>")
@builder_required
def builder_update_dialogue_node(node_id):
    validate_csrf(); database = get_db(); node = database.execute("SELECT conversation_id FROM mud_dialogue_nodes WHERE id=?", (node_id,)).fetchone()
    if node is None: abort(404)
    if _managed_dungeon_conversation(database, node["conversation_id"]): abort(404)
    fields, error = _dialogue_node_fields()
    if error: flash(error, "error"); return _builder_redirect()
    try:
        if fields[2]: database.execute("UPDATE mud_dialogue_nodes SET is_start=0 WHERE conversation_id=?", (node["conversation_id"],))
        database.execute("UPDATE mud_dialogue_nodes SET node_key=?,body=?,is_start=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (fields[0], fields[1], int(fields[2]), node_id)); database.commit()
    except sqlite3.IntegrityError:
        database.rollback(); flash("Dialogue node keys must be unique within a conversation.", "error"); return _builder_redirect()
    flash("Dialogue node updated.", "success"); return _builder_redirect()


def _builder_rules(database, field, validate):
    """Validate a builder conditions/outcomes JSON field and the content it names."""
    try:
        entries = validate(request.form.get(field, "").strip() or "[]")
    except ValueError as problem:
        return None, str(problem)
    tables = {"item": "mud_item_definitions", "quest": "mud_quests", "objective": "mud_quest_objectives",
              "object": "mud_item_instances", "room": "mud_world_rooms"}
    origins = character_creation.catalog(database)
    known = {"race": {entry["key"] for entry in origins["races"]},
             "class": {entry["key"] for entry in origins["classes"]},
             "skill": {entry["key"] for entry in mud_skills.catalog(database)}}
    for kind, ref in mud_conditions.references(entries):
        missing = (ref not in known[kind] if kind in known else
                   database.execute(f"SELECT 1 FROM {tables[kind]} WHERE id=?", (ref,)).fetchone() is None)
        if missing:
            return None, f"{field.replace('_json', '').title()} name an unknown {kind}: {ref}."
    return json.dumps(entries, separators=(",", ":")), None


def _dialogue_response_fields(database, node):
    response_text = " ".join(request.form.get("response_text", "").split()); next_node_id = request.form.get("next_node_id", "").strip() or None; quest_id = request.form.get("accept_quest_id", "").strip() or None; deliver_item_id = request.form.get("deliver_item_id", "").strip() or None; turn_in_quest_id = request.form.get("turn_in_quest_id", "").strip() or None
    required_state_key = " ".join(request.form.get("required_state_key", "").split()) or None; required_state_value = request.form.get("required_state_value", "").strip() or None
    state_key = " ".join(request.form.get("state_key", "").split()) or None; state_value = request.form.get("state_value", "").strip() or None
    sort_order = request.form.get("sort_order", 0, type=int)
    if not 1 <= len(response_text) <= 500 or (state_key and len(state_key) > 80) or (state_value and len(state_value) > 500) or (required_state_key and len(required_state_key) > 80) or (required_state_value and len(required_state_value) > 500) or sort_order is None or not -10000 <= sort_order <= 10000: return None, "Check response, state, and order fields."
    if bool(required_state_key) != bool(required_state_value): return None, "A response state requirement needs both a key and value."
    if next_node_id and database.execute("SELECT 1 FROM mud_dialogue_nodes WHERE id=? AND conversation_id=?", (next_node_id, node["conversation_id"])).fetchone() is None: return None, "The next node must belong to this conversation."
    if quest_id and database.execute("SELECT 1 FROM mud_quests WHERE id=?", (quest_id,)).fetchone() is None: return None, "Choose an existing quest."
    if turn_in_quest_id and database.execute("SELECT 1 FROM mud_quests WHERE id=? AND completion_mode='turn_in'", (turn_in_quest_id,)).fetchone() is None: return None, "Choose an existing quest that is turned in to an NPC."
    if deliver_item_id and database.execute("SELECT 1 FROM mud_item_definitions WHERE id=?", (deliver_item_id,)).fetchone() is None: return None, "Choose an existing delivery item."
    conditions_json, error = _builder_rules(database, "conditions_json", mud_conditions.validate_conditions)
    if error: return None, error
    outcomes_json, error = _builder_rules(database, "outcomes_json", mud_conditions.validate_outcomes)
    if error: return None, error
    return {"response_text": response_text, "next_node_id": next_node_id, "accept_quest_id": quest_id, "deliver_item_id": deliver_item_id, "turn_in_quest_id": turn_in_quest_id, "required_state_key": required_state_key, "required_state_value": required_state_value, "state_key": state_key, "state_value": state_value, "sort_order": sort_order, "conditions_json": conditions_json, "outcomes_json": outcomes_json}, None


@bp.post("/builder/dialogue-nodes/<path:node_id>/responses")
@builder_required
def builder_create_dialogue_response(node_id):
    validate_csrf(); database = get_db(); node = database.execute("SELECT conversation_id FROM mud_dialogue_nodes WHERE id=?", (node_id,)).fetchone()
    if node is None: abort(404)
    if _managed_dungeon_conversation(database, node["conversation_id"]): abort(404)
    fields, error = _dialogue_response_fields(database, node)
    if error: flash(error, "error"); return _builder_redirect()
    fields.update(id=f"world:dialogue-response:{uuid.uuid4().hex}", node_id=node_id)
    database.execute(f"INSERT INTO mud_dialogue_responses({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", tuple(fields.values())); database.commit()
    flash("Dialogue response added.", "success"); return _builder_redirect()


@bp.post("/builder/dialogue-responses/<path:response_id>")
@builder_required
def builder_update_dialogue_response(response_id):
    validate_csrf(); database = get_db()
    node = database.execute("SELECT n.conversation_id FROM mud_dialogue_responses r JOIN mud_dialogue_nodes n ON n.id=r.node_id WHERE r.id=?", (response_id,)).fetchone()
    if node is None or _managed_dungeon_conversation(database, node["conversation_id"]): abort(404)
    fields, error = _dialogue_response_fields(database, node)
    if error: flash(error, "error"); return _builder_redirect()
    database.execute(f"UPDATE mud_dialogue_responses SET {','.join(f'{name}=?' for name in fields)} WHERE id=?", (*fields.values(), response_id)); database.commit()
    flash("Dialogue response updated.", "success"); return _builder_redirect()


@bp.post("/builder/dialogue-responses/<path:response_id>/delete")
@builder_required
def builder_delete_dialogue_response(response_id):
    validate_csrf(); database = get_db()
    managed = database.execute(
        "SELECT 1 FROM mud_dialogue_responses AS r JOIN mud_dialogue_nodes AS n ON n.id=r.node_id "
        "JOIN mud_dungeon_managed_definitions AS m ON m.definition_id=n.conversation_id "
        "WHERE r.id=?", (response_id,),
    ).fetchone()
    if managed: abort(404)
    if request.form.get("confirmation") != "delete": flash("Response removal was not confirmed.", "error"); return _builder_redirect()
    if database.execute("DELETE FROM mud_dialogue_responses WHERE id=?", (response_id,)).rowcount == 0: abort(404)
    database.commit(); flash("Dialogue response removed.", "success"); return _builder_redirect()


def _npc_definition_fields():
    name = " ".join(request.form.get("name", "").split()); description = request.form.get("description", "").strip(); conversation_id = request.form.get("conversation_id", "").strip() or None
    properties, error = _json_object_field("properties_json")
    if error: return None, error
    behaviors, error = _json_object_field("behaviors_json")
    if error: return None, error
    try:
        validate_npc_behaviors(json.loads(behaviors))
    except ValueError as problem:
        return None, str(problem)
    if not 2 <= len(name) <= 80 or not 1 <= len(description) <= 4000: return None, "NPC names must be 2–80 characters and descriptions 1–4000 characters."
    return (name, description, conversation_id, properties, behaviors), None


def _npc_combat_fields(database):
    if request.form.get("is_hostile") != "1":
        return None, {}, None
    values = {
        "maximum_health": request.form.get("maximum_health", 30, type=int),
        "attack": request.form.get("combat_attack", 6, type=int),
        "defense": request.form.get("combat_defense", 2, type=int),
        "experience_reward": request.form.get("experience_reward", 10, type=int),
        "respawn_seconds": request.form.get("respawn_seconds", 60, type=int),
    }
    limits = {
        "maximum_health": (1, 1000000), "attack": (1, 100000),
        "defense": (0, 100000), "experience_reward": (0, 100000),
        "respawn_seconds": (5, 86400),
    }
    if any(values[key] is None or not low <= values[key] <= high
           for key, (low, high) in limits.items()):
        return None, {}, "Check combat values (respawn must be 5 seconds to 24 hours)."
    # Accuracy and evasion are optional; blank keeps the 0 default of older NPCs.
    for key in ("accuracy", "evasion"):
        raw = request.form.get(f"combat_{key}", "").strip()
        if not raw:
            values[key] = None
            continue
        if not raw.isdigit() or not 0 <= int(raw) <= 100:
            return None, {}, "Accuracy and evasion must be whole numbers from 0 to 100."
        values[key] = int(raw)
    equipment = {}
    for slot in EQUIPMENT_SLOTS:
        item_id = request.form.get(f"npc_equipment_{slot}", "").strip()
        if not item_id:
            continue
        valid = database.execute(
            "SELECT 1 FROM mud_item_definitions WHERE id=? AND item_type='equipment' "
            "AND equipment_slot=?", (item_id, slot),
        ).fetchone()
        if valid is None:
            return None, {}, f"Choose valid {slot.replace('_', ' ')} equipment."
        equipment[slot] = item_id
    return values, equipment, None


def _save_npc_combat(database, npc_id, profile, equipment):
    if profile is None:
        database.execute(
            "UPDATE mud_combat_encounters SET status='abandoned',ended_at=CURRENT_TIMESTAMP,"
            "updated_at=CURRENT_TIMESTAMP WHERE npc_definition_id=? AND status IN ('ready','active')",
            (npc_id,),
        )
        database.execute("DELETE FROM mud_npc_combat_profiles WHERE npc_definition_id=?", (npc_id,))
        return
    database.execute(
        "INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack,defense,"
        "experience_reward,respawn_seconds,accuracy,evasion) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(npc_definition_id) DO UPDATE SET maximum_health=excluded.maximum_health,"
        "attack=excluded.attack,defense=excluded.defense,experience_reward=excluded.experience_reward,"
        "respawn_seconds=excluded.respawn_seconds,accuracy=excluded.accuracy,evasion=excluded.evasion,"
        "updated_at=CURRENT_TIMESTAMP",
        (npc_id, profile["maximum_health"], profile["attack"], profile["defense"],
         profile["experience_reward"], profile["respawn_seconds"],
         profile.get("accuracy"), profile.get("evasion")),
    )
    database.execute("DELETE FROM mud_npc_equipment WHERE npc_definition_id=?", (npc_id,))
    database.executemany(
        "INSERT INTO mud_npc_equipment(npc_definition_id,equipment_slot,item_definition_id) "
        "VALUES(?,?,?)", ((npc_id, slot, item_id) for slot, item_id in equipment.items()),
    )


def _save_npc_shop(database, npc_id):
    if request.form.get("is_merchant") != "1":
        database.execute("DELETE FROM mud_npc_shops WHERE npc_definition_id=?", (npc_id,))
        return None
    sell_percent = request.form.get("sell_percent", 40, type=int)
    if sell_percent is None or not 1 <= sell_percent <= 100:
        return "Merchant buyback percentage must be 1 to 100."
    database.execute(
        "INSERT INTO mud_npc_shops(npc_definition_id,buys_items,sell_percent) VALUES(?,?,?) "
        "ON CONFLICT(npc_definition_id) DO UPDATE SET buys_items=excluded.buys_items,"
        "sell_percent=excluded.sell_percent",
        (npc_id, int(request.form.get("buys_items") == "1"), sell_percent))
    return None


@bp.post("/builder/npcs")
@builder_required
def builder_create_npc_definition():
    validate_csrf(); database = get_db(); fields, error = _npc_definition_fields()
    if error: flash(error, "error"); return _builder_redirect()
    profile, equipment, error = _npc_combat_fields(database)
    if error: flash(error, "error"); return _builder_redirect()
    if fields[2] and database.execute("SELECT 1 FROM mud_conversations WHERE id=?", (fields[2],)).fetchone() is None: flash("Choose an existing conversation.", "error"); return _builder_redirect()
    npc_id = f"world:npc-definition:{uuid.uuid4().hex}"; database.execute("INSERT INTO mud_npc_definitions(id,name,description,conversation_id,properties_json,behaviors_json,created_by,world_id) VALUES(?,?,?,?,?,?,?,(SELECT id FROM mud_world_packages WHERE active=1))", (npc_id, *fields, g.user["id"])); _save_npc_combat(database, npc_id, profile, equipment)
    shop_error = _save_npc_shop(database, npc_id)
    if shop_error: database.rollback(); flash(shop_error, "error"); return _builder_redirect()
    database.commit()
    flash(f"NPC definition {fields[0]} created.", "success"); return _builder_redirect()


@bp.post("/builder/npcs/<path:npc_id>")
@builder_required
def builder_update_npc_definition(npc_id):
    validate_csrf(); database = get_db(); fields, error = _npc_definition_fields()
    if _managed_dungeon_definition(database, npc_id): abort(404)
    if database.execute("SELECT 1 FROM mud_npc_definitions WHERE id=?", (npc_id,)).fetchone() is None: abort(404)
    if error: flash(error, "error"); return _builder_redirect()
    profile, equipment, error = _npc_combat_fields(database)
    if error: flash(error, "error"); return _builder_redirect()
    if fields[2] and database.execute("SELECT 1 FROM mud_conversations WHERE id=?", (fields[2],)).fetchone() is None: flash("Choose an existing conversation.", "error"); return _builder_redirect()
    database.execute("UPDATE mud_npc_definitions SET name=?,description=?,conversation_id=?,properties_json=?,behaviors_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*fields, npc_id)); _save_npc_combat(database, npc_id, profile, equipment)
    shop_error = _save_npc_shop(database, npc_id)
    if shop_error: database.rollback(); flash(shop_error, "error"); return _builder_redirect()
    database.commit(); flash("NPC definition updated.", "success"); return _builder_redirect()


@bp.post("/builder/npcs/<path:npc_id>/stock")
@builder_required
def builder_add_shop_stock(npc_id):
    validate_csrf(); database = get_db()
    if _managed_dungeon_definition(database, npc_id): abort(404)
    if database.execute("SELECT 1 FROM mud_npc_shops WHERE npc_definition_id=?", (npc_id,)).fetchone() is None: abort(404)
    item_id = request.form.get("item_definition_id", "").strip()
    price = request.form.get("price", type=int)
    quantity_raw = request.form.get("quantity", "").strip()
    try: quantity = int(quantity_raw) if quantity_raw else None
    except ValueError: quantity = -1
    if (database.execute("SELECT 1 FROM mud_item_definitions WHERE id=?", (item_id,)).fetchone() is None or
            price is None or not 1 <= price <= 100000 or
            (quantity is not None and not 0 <= quantity <= 100000)):
        flash("Choose an item, valid price, and optional nonnegative stock quantity.", "error"); return _builder_redirect()
    database.execute(
        "INSERT INTO mud_shop_stock(id,npc_definition_id,item_definition_id,price,quantity) VALUES(?,?,?,?,?) "
        "ON CONFLICT(npc_definition_id,item_definition_id) DO UPDATE SET price=excluded.price,quantity=excluded.quantity",
        (f"world:stock:{uuid.uuid4().hex}", npc_id, item_id, price, quantity))
    database.commit(); flash("Merchant stock saved.", "success"); return _builder_redirect()


@bp.post("/builder/npcs/<path:npc_id>/trainers")
@builder_required
def builder_save_trainer(npc_id):
    validate_csrf(); database = get_db()
    if _managed_dungeon_definition(database, npc_id): abort(404)
    if database.execute("SELECT 1 FROM mud_npc_definitions WHERE id=?", (npc_id,)).fetchone() is None: abort(404)
    skill_key = request.form.get("skill_key", "").strip()
    price = request.form.get("price", 0, type=int)
    max_rank = request.form.get("max_rank", 5, type=int)
    if (skill_key not in mud_skills.definitions(database) or price is None or not 0 <= price <= 100000
            or max_rank is None or not 1 <= max_rank <= mud_skills.MAX_RANK):
        flash("Choose an enabled skill, a price from 0 to 100,000 coins, and a maximum rank from 1 to 20.", "error")
        return _builder_redirect()
    database.execute(
        "INSERT INTO mud_npc_trainers(id,npc_definition_id,skill_key,price,max_rank) VALUES(?,?,?,?,?) "
        "ON CONFLICT(npc_definition_id,skill_key) DO UPDATE SET price=excluded.price,max_rank=excluded.max_rank",
        (f"world:trainer:{uuid.uuid4().hex}", npc_id, skill_key, price, max_rank))
    database.commit(); flash("Trainer lesson saved.", "success"); return _builder_redirect()


@bp.post("/builder/trainers/<path:trainer_id>/delete")
@builder_required
def builder_delete_trainer(trainer_id):
    validate_csrf(); database = get_db()
    if request.form.get("confirmation") != "delete": abort(400)
    if database.execute("DELETE FROM mud_npc_trainers WHERE id=?", (trainer_id,)).rowcount != 1: abort(404)
    database.commit(); flash("Trainer lesson removed.", "success"); return _builder_redirect()


@bp.post("/builder/shop-stock/<path:stock_id>/delete")
@builder_required
def builder_delete_shop_stock(stock_id):
    validate_csrf(); database = get_db()
    if request.form.get("confirmation") != "delete": abort(400)
    if database.execute("DELETE FROM mud_shop_stock WHERE id=?", (stock_id,)).rowcount != 1: abort(404)
    database.commit(); flash("Merchant stock removed.", "success"); return _builder_redirect()


@bp.post("/builder/npcs/<path:npc_id>/combat-rewards")
@builder_required
def builder_create_npc_combat_reward(npc_id):
    validate_csrf(); database = get_db()
    if _managed_dungeon_definition(database, npc_id): abort(404)
    if database.execute("SELECT 1 FROM mud_npc_combat_profiles WHERE npc_definition_id=?", (npc_id,)).fetchone() is None:
        flash("Enable hostile combat before adding rewards.", "error"); return _builder_redirect()
    reward_type = request.form.get("reward_type", "item")
    target_id = request.form.get("target_id", "").strip() or None
    amount = request.form.get("amount", 1, type=int)
    chance = request.form.get("chance_percent", 100, type=int)
    if reward_type not in {"experience", "item", "coins"} or amount is None or not 1 <= amount <= 100000 or chance is None or not 1 <= chance <= 100:
        flash("Choose a valid combat reward.", "error"); return _builder_redirect()
    if reward_type == "item":
        if amount > 100 or database.execute(
            "SELECT 1 FROM mud_item_definitions WHERE id=?", (target_id,),
        ).fetchone() is None:
            flash("Choose an item and a quantity up to 100.", "error"); return _builder_redirect()
    else:
        target_id = None
    database.execute(
        "INSERT INTO mud_npc_combat_rewards(id,npc_definition_id,reward_type,target_id,amount,chance_percent) "
        "VALUES(?,?,?,?,?,?)", (f"world:combat-reward:{uuid.uuid4().hex}", npc_id,
                              reward_type, target_id, amount, chance),
    )
    database.commit(); flash("Combat reward added.", "success"); return _builder_redirect()


@bp.post("/builder/npc-combat-rewards/<path:reward_id>/delete")
@builder_required
def builder_delete_npc_combat_reward(reward_id):
    validate_csrf(); database = get_db()
    if request.form.get("confirmation") != "delete":
        flash("Reward removal was not confirmed.", "error"); return _builder_redirect()
    if database.execute("DELETE FROM mud_npc_combat_rewards WHERE id=?", (reward_id,)).rowcount == 0:
        abort(404)
    database.commit(); flash("Combat reward removed.", "success"); return _builder_redirect()


@bp.post("/builder/npcs/place")
@builder_required
def builder_place_npc():
    validate_csrf(); database = get_db(); definition_id = request.form.get("definition_id", "").strip(); room_id = request.form.get("room_id", "").strip()
    if _managed_dungeon_definition(database, definition_id): abort(404)
    definition = database.execute("SELECT name FROM mud_npc_definitions WHERE id=?", (definition_id,)).fetchone()
    if definition is None or database.execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (room_id,)).fetchone() is None: abort(404)
    database.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES(?,?,?)", (f"world:npc:{uuid.uuid4().hex}", definition_id, room_id)); database.commit(); flash(f"Placed {definition['name']} in the room.", "success"); return _builder_redirect(room_id)


@bp.post("/builder/npc-instances/<path:instance_id>/delete")
@builder_required
def builder_delete_npc_instance(instance_id):
    validate_csrf(); database = get_db(); npc = database.execute("SELECT i.room_id,d.name FROM mud_npc_instances i JOIN mud_npc_definitions d ON d.id=i.definition_id WHERE i.id=?", (instance_id,)).fetchone()
    if npc is None: abort(404)
    if request.form.get("confirmation") != "delete": flash("NPC removal was not confirmed.", "error"); return _builder_redirect(npc["room_id"])
    database.execute("DELETE FROM mud_npc_instances WHERE id=?", (instance_id,)); database.commit(); flash(f"Removed {npc['name']} from the room.", "success"); return _builder_redirect(npc["room_id"])


def _quest_fields(database, quest_id=None):
    name = " ".join(request.form.get("name", "").split()); description = request.form.get("description", "").strip(); prerequisite = request.form.get("prerequisite_quest_id", "").strip() or None
    if not 2 <= len(name) <= 120 or not 1 <= len(description) <= 4000: return None, "Quest names must be 2–120 characters and descriptions 1–4000 characters."
    if prerequisite and prerequisite == quest_id: return None, "A quest cannot require itself."
    if prerequisite and database.execute("SELECT 1 FROM mud_quests WHERE id=?", (prerequisite,)).fetchone() is None: return None, "Choose an existing prerequisite quest."
    cursor = prerequisite
    seen = set()
    while cursor and cursor not in seen:
        if cursor == quest_id:
            return None, "Quest prerequisites cannot form a cycle."
        seen.add(cursor)
        row = database.execute(
            "SELECT prerequisite_quest_id FROM mud_quests WHERE id=?", (cursor,)
        ).fetchone()
        cursor = row["prerequisite_quest_id"] if row else None
    completion_mode = request.form.get("completion_mode", "auto")
    turn_in_npc = request.form.get("turn_in_npc_definition_id", "").strip() or None
    if completion_mode not in ("auto", "turn_in"): return None, "Choose how the quest completes."
    if turn_in_npc and database.execute("SELECT 1 FROM mud_npc_definitions WHERE id=?", (turn_in_npc,)).fetchone() is None: return None, "Choose an existing turn-in NPC."
    conditions_json, error = _builder_rules(database, "conditions_json", mud_conditions.validate_conditions)
    if error: return None, error
    return (name, description, prerequisite, completion_mode, turn_in_npc if completion_mode == "turn_in" else None,
            int(request.form.get("sequential") == "1"), int(request.form.get("abandonable") == "1"), conditions_json), None


@bp.post("/builder/quests")
@builder_required
def builder_create_quest():
    validate_csrf(); database = get_db(); fields, error = _quest_fields(database)
    if error: flash(error, "error"); return _builder_redirect()
    quest_id = f"world:quest:{uuid.uuid4().hex}"; database.execute("INSERT INTO mud_quests(id,name,description,prerequisite_quest_id,completion_mode,turn_in_npc_definition_id,sequential,abandonable,conditions_json,created_by,world_id) VALUES(?,?,?,?,?,?,?,?,?,?,(SELECT id FROM mud_world_packages WHERE active=1))", (quest_id, *fields, g.user["id"])); database.commit(); flash(f"Quest {fields[0]} created.", "success"); return _builder_redirect()


@bp.post("/builder/quests/<path:quest_id>")
@builder_required
def builder_update_quest(quest_id):
    validate_csrf(); database = get_db()
    if database.execute("SELECT 1 FROM mud_quests WHERE id=?", (quest_id,)).fetchone() is None: abort(404)
    fields, error = _quest_fields(database, quest_id)
    if error: flash(error, "error"); return _builder_redirect()
    database.execute("UPDATE mud_quests SET name=?,description=?,prerequisite_quest_id=?,completion_mode=?,turn_in_npc_definition_id=?,sequential=?,abandonable=?,conditions_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*fields, quest_id)); database.commit(); flash("Quest updated.", "success"); return _builder_redirect()


@bp.post("/builder/quests/<path:quest_id>/givers")
@builder_required
def builder_set_quest_givers(quest_id):
    validate_csrf(); database = get_db()
    if database.execute("SELECT 1 FROM mud_quests WHERE id=?", (quest_id,)).fetchone() is None: abort(404)
    giver_ids = set(request.form.getlist("npc_definition_id")); valid = {row["id"] for row in database.execute("SELECT id FROM mud_npc_definitions")}
    if not giver_ids <= valid: flash("Choose existing NPC definitions.", "error"); return _builder_redirect()
    database.execute("DELETE FROM mud_quest_givers WHERE quest_id=?", (quest_id,)); database.executemany("INSERT INTO mud_quest_givers(quest_id,npc_definition_id) VALUES(?,?)", ((quest_id, value) for value in giver_ids)); database.commit(); flash("Quest givers updated.", "success"); return _builder_redirect()


def _quest_objective_fields(database):
    objective_type = request.form.get("objective_type", ""); target_id = request.form.get("target_id", "").strip(); description = request.form.get("description", "").strip(); required_count = request.form.get("required_count", 1, type=int)
    target_tables = {"talk_npc": "mud_npc_definitions", "visit_room": "mud_world_rooms", "possess_item": "mud_item_definitions", "defeat_npc": "mud_npc_definitions", "deliver_item": "mud_item_definitions"}
    if objective_type == "script":
        target_id = "script"  # A trigger's complete_objective action names the objective itself.
    elif objective_type not in target_tables or database.execute(f"SELECT 1 FROM {target_tables.get(objective_type, 'mud_quests')} WHERE id=?", (target_id,)).fetchone() is None: return None, "Choose a valid objective type and target."
    sort_order = request.form.get("sort_order", 0, type=int)
    if not 1 <= len(description) <= 500 or required_count is None or not 1 <= required_count <= 100 or sort_order is None or not -10000 <= sort_order <= 10000: return None, "Objective descriptions must be 1–500 characters, counts 1–100, and order valid."
    consume = int(request.form.get("consume_on_turn_in") == "1"); recipient = request.form.get("recipient_npc_definition_id", "").strip() or None
    if consume and objective_type != "possess_item": return None, "Only possess-item objectives can take the items at turn-in."
    if recipient and objective_type != "deliver_item": return None, "Only delivery objectives can name a recipient NPC."
    if recipient and database.execute("SELECT 1 FROM mud_npc_definitions WHERE id=?", (recipient,)).fetchone() is None: return None, "Choose an existing recipient NPC."
    return {"objective_type": objective_type, "target_id": target_id, "description": description, "required_count": required_count, "sort_order": sort_order, "optional": int(request.form.get("optional") == "1"), "consume_on_turn_in": consume, "recipient_npc_definition_id": recipient}, None


@bp.post("/builder/quests/<path:quest_id>/objectives")
@builder_required
def builder_create_quest_objective(quest_id):
    validate_csrf(); database = get_db()
    if database.execute("SELECT 1 FROM mud_quests WHERE id=?", (quest_id,)).fetchone() is None: abort(404)
    fields, error = _quest_objective_fields(database)
    if error: flash(error, "error"); return _builder_redirect()
    fields.update(id=f"world:quest-objective:{uuid.uuid4().hex}", quest_id=quest_id)
    database.execute(f"INSERT INTO mud_quest_objectives({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", tuple(fields.values())); database.commit(); flash("Quest objective added.", "success"); return _builder_redirect()


@bp.post("/builder/quest-objectives/<path:objective_id>")
@builder_required
def builder_update_quest_objective(objective_id):
    validate_csrf(); database = get_db()
    if database.execute("SELECT 1 FROM mud_quest_objectives WHERE id=?", (objective_id,)).fetchone() is None: abort(404)
    fields, error = _quest_objective_fields(database)
    if error: flash(error, "error"); return _builder_redirect()
    database.execute(f"UPDATE mud_quest_objectives SET {','.join(f'{name}=?' for name in fields)} WHERE id=?", (*fields.values(), objective_id)); database.commit(); flash("Quest objective updated.", "success"); return _builder_redirect()


@bp.post("/builder/quest-objectives/<path:objective_id>/delete")
@builder_required
def builder_delete_quest_objective(objective_id):
    validate_csrf(); database = get_db()
    if request.form.get("confirmation") != "delete": flash("Objective removal was not confirmed.", "error"); return _builder_redirect()
    if database.execute("DELETE FROM mud_quest_objectives WHERE id=?", (objective_id,)).rowcount == 0: abort(404)
    database.commit(); flash("Quest objective removed.", "success"); return _builder_redirect()


@bp.post("/builder/quests/<path:quest_id>/rewards")
@builder_required
def builder_create_quest_reward(quest_id):
    validate_csrf(); database = get_db(); reward_type = request.form.get("reward_type", ""); target_id = request.form.get("target_id", "").strip() or None; amount = request.form.get("amount", 1, type=int)
    if database.execute("SELECT 1 FROM mud_quests WHERE id=?", (quest_id,)).fetchone() is None: abort(404)
    if reward_type not in {"experience", "item", "coins"} or amount is None or not 1 <= amount <= 100000 or (reward_type == "item" and amount > 100): flash("Choose a valid reward amount (up to 100 items or 100,000 XP/coins).", "error"); return _builder_redirect()
    if reward_type == "item" and database.execute("SELECT 1 FROM mud_item_definitions WHERE id=?", (target_id,)).fetchone() is None: flash("Choose an item definition for an item reward.", "error"); return _builder_redirect()
    if reward_type in {"experience", "coins"}: target_id = None
    database.execute("INSERT INTO mud_quest_rewards(id,quest_id,reward_type,target_id,amount) VALUES(?,?,?,?,?)", (f"world:quest-reward:{uuid.uuid4().hex}", quest_id, reward_type, target_id, amount)); database.commit(); flash("Quest reward added.", "success"); return _builder_redirect()


@bp.post("/builder/quest-rewards/<path:reward_id>/delete")
@builder_required
def builder_delete_quest_reward(reward_id):
    validate_csrf(); database = get_db()
    if request.form.get("confirmation") != "delete": flash("Reward removal was not confirmed.", "error"); return _builder_redirect()
    if database.execute("DELETE FROM mud_quest_rewards WHERE id=?", (reward_id,)).rowcount == 0: abort(404)
    database.commit(); flash("Quest reward removed.", "success"); return _builder_redirect()


@bp.post("/builder/rooms/<path:room_id>/discoveries")
@builder_required
def builder_add_discovery(room_id):
    validate_csrf(); database = get_db()
    if database.execute("SELECT 1 FROM mud_world_rooms WHERE id=? AND world_id=(SELECT id FROM mud_world_packages WHERE active=1)", (room_id,)).fetchone() is None: abort(404)
    clue = request.form.get("clue", "").strip()
    item_id = request.form.get("item_definition_id", "").strip() or None
    difficulty = request.form.get("difficulty", 0, type=int)
    if not 1 <= len(clue) <= 500 or (item_id and database.execute("SELECT 1 FROM mud_item_definitions WHERE id=?", (item_id,)).fetchone() is None):
        flash("Write a short clue and choose a valid optional item.", "error"); return _builder_redirect(room_id)
    if difficulty is None or not 0 <= difficulty <= 200:
        flash("Search difficulty must be 0 (always found) to 200.", "error"); return _builder_redirect(room_id)
    database.execute("INSERT INTO mud_room_discoveries(id,room_id,clue,item_definition_id,difficulty) VALUES(?,?,?,?,?)",
                     (f"world:discovery:{uuid.uuid4().hex}", room_id, clue, item_id, difficulty))
    database.commit(); flash("Room discovery added.", "success"); return _builder_redirect(room_id)


@bp.post("/builder/discoveries/<path:discovery_id>/delete")
@builder_required
def builder_delete_discovery(discovery_id):
    validate_csrf(); database = get_db()
    if request.form.get("confirmation") != "delete": abort(400)
    row = database.execute("SELECT room_id FROM mud_room_discoveries WHERE id=?", (discovery_id,)).fetchone()
    if row is None: abort(404)
    database.execute("DELETE FROM mud_room_discoveries WHERE id=?", (discovery_id,))
    database.commit(); flash("Room discovery removed.", "success"); return _builder_redirect(row["room_id"])


@bp.post("/builder/items")
@builder_required
def builder_create_item_definition():
    validate_csrf(); fields, error = _item_definition_fields()
    if error: flash(error, "error"); return _builder_redirect()
    item_id = f"world:item-definition:{uuid.uuid4().hex}"
    get_db().execute("INSERT INTO mud_item_definitions(id,name,description,item_type,properties_json,equipment_slot,created_by,world_id) VALUES(?,?,?,?,?,?,?,(SELECT id FROM mud_world_packages WHERE active=1))", (item_id, *fields, g.user["id"])); get_db().commit()
    flash(f"Item definition {fields[0]} created.", "success"); return _builder_redirect()


@bp.post("/builder/items/<path:item_id>")
@builder_required
def builder_update_item_definition(item_id):
    validate_csrf(); fields, error = _item_definition_fields(); database = get_db()
    if _managed_dungeon_definition(database, item_id): abort(404)
    if database.execute("SELECT 1 FROM mud_item_definitions WHERE id=?", (item_id,)).fetchone() is None: abort(404)
    if error: flash(error, "error"); return _builder_redirect()
    database.execute("UPDATE mud_item_definitions SET name=?,description=?,item_type=?,properties_json=?,equipment_slot=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*fields, item_id)); database.commit()
    flash("Item definition updated.", "success"); return _builder_redirect()


@bp.post("/builder/items/<path:item_id>/place")
@builder_required
def builder_place_item(item_id):
    return _builder_place_item(item_id)


@bp.post("/builder/items/place")
@builder_required
def builder_place_selected_item():
    return _builder_place_item(request.form.get("definition_id", "").strip())


def _builder_place_item(item_id):
    validate_csrf(); database = get_db(); room_id = request.form.get("room_id", "").strip(); container_id = request.form.get("container_id", "").strip()
    respawn = request.form.get("respawn_seconds", 0, type=int)
    if respawn is None or respawn < 0 or (respawn and not 5 <= respawn <= 86400):
        flash("Respawn must be 5 seconds to 24 hours, or zero for a one-time placement.", "error"); return _builder_redirect(room_id)
    if respawn and container_id:
        flash("Renewable placements must be on the room floor.", "error"); return _builder_redirect(room_id)
    if _managed_dungeon_definition(database, item_id): abort(404)
    definition = database.execute("SELECT name FROM mud_item_definitions WHERE id=?", (item_id,)).fetchone()
    if definition is None or database.execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (room_id,)).fetchone() is None: abort(404)
    if container_id:
        container = database.execute("SELECT i.id FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id WHERE i.id=? AND i.room_id=? AND d.item_type='container'", (container_id, room_id)).fetchone()
        if container is None:
            flash("Choose a container placed in this room.", "error"); return _builder_redirect(room_id)
    instance_id = f"world:item:{uuid.uuid4().hex}"
    if container_id:
        database.execute("INSERT INTO mud_item_instances(id,definition_id,container_id) VALUES(?,?,?)", (instance_id, item_id, container_id))
    else:
        database.execute("INSERT INTO mud_item_instances(id,definition_id,room_id) VALUES(?,?,?)", (instance_id, item_id, room_id))
    if respawn:
        spawn_id = f"world:spawn:{uuid.uuid4().hex}"
        database.execute("INSERT INTO mud_item_spawns(id,definition_id,room_id,respawn_seconds,live_instance_id) VALUES(?,?,?,?,?)",
                         (spawn_id, item_id, room_id, respawn, instance_id))
        database.execute("UPDATE mud_item_instances SET spawn_source_id=? WHERE id=?", (spawn_id, instance_id))
    database.commit()
    flash(f"Placed {definition['name']} in the room.", "success"); return _builder_redirect(room_id)


@bp.post("/builder/item-spawns/<path:spawn_id>/delete")
@builder_required
def builder_delete_item_spawn(spawn_id):
    validate_csrf(); database = get_db()
    if request.form.get("confirmation") != "delete": abort(400)
    spawn = database.execute("SELECT * FROM mud_item_spawns WHERE id=?", (spawn_id,)).fetchone()
    if spawn is None: abort(404)
    database.execute("DELETE FROM mud_scheduled_events WHERE event_kind='item_respawn' AND entity_id=?", (spawn_id,))
    if spawn["live_instance_id"]:
        database.execute("DELETE FROM mud_item_instances WHERE id=? AND room_id=?",
                         (spawn["live_instance_id"], spawn["room_id"]))
    database.execute("DELETE FROM mud_item_spawns WHERE id=?", (spawn_id,))
    database.commit(); flash("Renewable placement removed.", "success"); return _builder_redirect(spawn["room_id"])


@bp.post("/builder/item-instances/<path:instance_id>/delete")
@builder_required
def builder_delete_item_instance(instance_id):
    validate_csrf(); database = get_db(); item = database.execute("SELECT i.id,i.room_id,d.name FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id WHERE i.id=?", (instance_id,)).fetchone()
    if item is None: abort(404)
    if request.form.get("confirmation") != "delete": flash("Item removal was not confirmed.", "error"); return _builder_redirect(item["room_id"])
    # Never silently destroy items currently held or containing items.
    if item["room_id"] is None or database.execute("SELECT 1 FROM mud_item_instances WHERE container_id=?", (instance_id,)).fetchone():
        flash("Only empty room items can be removed from the builder.", "error"); return _builder_redirect(item["room_id"])
    database.execute("DELETE FROM mud_item_spawns WHERE live_instance_id=?", (instance_id,))
    database.execute("DELETE FROM mud_item_instances WHERE id=?", (instance_id,)); database.commit(); flash(f"Removed {item['name']}.", "success"); return _builder_redirect(item["room_id"])


@bp.post("/builder/rooms")
@builder_required
def builder_create_room():
    validate_csrf()
    name, description, error = _valid_room_fields()
    if error:
        flash(error, "error")
        return _builder_redirect()
    room_id = f"world:room:{uuid.uuid4().hex}"
    database = get_db()
    database.execute(
        "INSERT INTO mud_world_rooms(id,name,description,world_id) "
        "VALUES(?,?,?,(SELECT id FROM mud_world_packages WHERE active=1))",
        (room_id, name, description),
    )
    database.commit()
    flash(f"Room {name} created.", "success")
    return _builder_redirect(room_id)


@bp.post("/builder/rooms/<path:room_id>")
@builder_required
def builder_update_room(room_id):
    validate_csrf()
    name, description, error = _valid_room_fields()
    database = get_db()
    room = database.execute(
        "SELECT id,name FROM mud_world_rooms WHERE id=? AND world_id=(SELECT id FROM mud_world_packages WHERE active=1)", (room_id,)
    ).fetchone()
    if room is None:
        abort(404)
    if error:
        flash(error, "error")
        return _builder_redirect(room_id)
    if request.form.get("grid_form") == "1":
        error = _builder_room_grid(database, room_id, name)
        if error:
            database.rollback()
            flash(error, "error")
            return _builder_redirect(room_id)
    recipients = _occupant_ids(database, room_id)
    database.execute(
        "UPDATE mud_world_rooms SET name=?,description=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (name, description, room_id),
    )
    if recipients:
        _insert_event(database, EventDraft(
            f"Your surroundings have changed. You are now in {name}.",
            event_type="room", audience_type="room", audience_key=room_id,
            recipient_ids=recipients,
        ))
    database.commit()
    flash(f"Room {name} updated.", "success")
    return _builder_redirect(room_id)


def _builder_room_grid(database, room_id, name):
    """Place an authored room on the wilderness grid, absorbing any wild room at that spot."""
    try:
        position = mud_wilderness.parse_position(request.form.get("x"), request.form.get("y"), request.form.get("z"))
        blocked = mud_wilderness.validate_blocked(request.form.getlist("blocked_dirs"))
        if position is not None:
            displaced = database.execute(
                "SELECT id FROM mud_world_rooms WHERE world_id=(SELECT world_id FROM mud_world_rooms WHERE id=?) "
                "AND x=? AND y=? AND z=? AND kind='wild'", (room_id, *position)).fetchone()
            if displaced:
                _move_room_occupants(database, [displaced["id"]], {"id": room_id, "name": name},
                                     "A world builder placed {name} here.")
        mud_wilderness.place_authored(database, room_id, position, request.form.get("grid_link") == "1", blocked)
    except mud_wilderness.WildernessError as problem:
        return str(problem)
    return None


def _move_room_occupants(database, room_ids, destination, message):
    """Move saved and present players out of rooms about to change; message may use {name}."""
    affected = set()
    for start in range(0, len(room_ids), 500):
        chunk = list(room_ids[start:start + 500])
        marks = ",".join("?" * len(chunk))
        affected |= {row["user_id"] for row in database.execute(
            f"SELECT user_id FROM mud_world_locations WHERE room_id IN ({marks}) UNION "
            f"SELECT user_id FROM mud_presence WHERE room_id IN ({marks}) UNION "
            f"SELECT user_id FROM mud_player_locations WHERE location_type='world_room' AND location_key IN ({marks})",
            chunk * 3)}
        database.execute(
            f"UPDATE mud_presence SET room_id=?,location_type='world_room',location_key=?,"
            f"instance_id=NULL WHERE room_id IN ({marks}) OR (location_type='world_room' AND location_key IN ({marks}))",
            (destination["id"], destination["id"], *chunk, *chunk),
        )
    database.executemany(
        "INSERT INTO mud_world_locations(user_id,room_id) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET room_id=excluded.room_id,"
        "updated_at=CURRENT_TIMESTAMP",
        ((user_id, destination["id"]) for user_id in affected),
    )
    database.executemany(
        "INSERT INTO mud_player_locations(user_id,mode,location_type,location_key) "
        "VALUES(?,'traditional','world_room',?) ON CONFLICT(user_id) DO UPDATE SET "
        "mode='traditional',location_type='world_room',location_key=excluded.location_key,"
        "instance_id=NULL,updated_at=CURRENT_TIMESTAMP",
        ((user_id, destination["id"]) for user_id in affected),
    )
    for user_id in affected:
        _insert_event(database, EventDraft(message.replace("{name}", destination["name"]),
                                           event_type="movement", recipient_ids=(user_id,)),
                      default_user_id=user_id)
    return affected


@bp.post("/builder/rooms/<path:room_id>/delete")
@builder_required
def builder_delete_room(room_id):
    validate_csrf()
    database = get_db()
    try:
        database.execute("BEGIN IMMEDIATE")
        room = database.execute(
            "SELECT id,name,is_start FROM mud_world_rooms WHERE id=? "
            "AND world_id=(SELECT id FROM mud_world_packages WHERE active=1)", (room_id,)
        ).fetchone()
        if room is None:
            abort(404)
        if request.form.get("confirmation", "").strip() != room["name"]:
            flash("Type the room name exactly to confirm deletion.", "error")
            database.rollback()
            return _builder_redirect(room_id)
        if room["is_start"]:
            flash("The current starting room cannot be deleted. Designate another valid start first.", "error")
            database.rollback()
            return _builder_redirect(room_id)
        start = _start_room(database)
        _move_room_occupants(database, [room_id], start,
                             f"{room['name']} was removed by a world builder. You have been moved to {{name}}.")
        database.execute("DELETE FROM mud_world_rooms WHERE id=?", (room_id,))
        database.commit()
    except sqlite3.Error:
        database.rollback()
        raise
    flash(f"Room {room['name']} and its connected exits were deleted.", "success")
    return _builder_redirect()


@bp.post("/builder/exits")
@builder_required
def builder_create_exit():
    validate_csrf()
    database = get_db()
    fields, error = _valid_exit_fields(database)
    if error:
        flash(error, "error")
        return _builder_redirect(request.form.get("from_room_id"))
    exit_id = f"world:exit:{uuid.uuid4().hex}"
    database.execute(
        "INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction,key_item_id,pick_difficulty,"
        "conditions_json,conditions_message,hidden) VALUES(?,?,?,?,?,?,?,?,?,?)", (exit_id, *fields),
    )
    database.commit()
    flash(f"Exit {fields[2]} created.", "success")
    return _builder_redirect(fields[0])


@bp.post("/builder/exits/<path:exit_id>")
@builder_required
def builder_update_exit(exit_id):
    validate_csrf()
    database = get_db()
    existing = database.execute(
        "SELECT e.id,e.from_room_id,e.to_room_id,e.key_item_id,e.pick_difficulty FROM mud_world_exits e JOIN mud_world_rooms r "
        "ON r.id=e.from_room_id WHERE e.id=? "
        "AND r.world_id=(SELECT id FROM mud_world_packages WHERE active=1)", (exit_id,)
    ).fetchone()
    if existing is None:
        abort(404)
    fields, error = _valid_exit_fields(database, exit_id)
    if error:
        flash(error, "error")
        return _builder_redirect(existing["from_room_id"])
    database.execute(
        "UPDATE mud_world_exits SET from_room_id=?,to_room_id=?,name=?,direction=?,key_item_id=?,"
        "pick_difficulty=?,conditions_json=?,conditions_message=?,hidden=? WHERE id=?",
        (*fields, exit_id),
    )
    if (existing["key_item_id"] != fields[4] or existing["pick_difficulty"] != fields[5]
            or existing["from_room_id"] != fields[0] or existing["to_room_id"] != fields[1]):
        database.execute("DELETE FROM mud_exit_unlocks WHERE exit_id=?", (exit_id,))
    database.commit()
    flash(f"Exit {fields[2]} updated.", "success")
    return _builder_redirect(fields[0])


@bp.post("/builder/exits/<path:exit_id>/delete")
@builder_required
def builder_delete_exit(exit_id):
    validate_csrf()
    database = get_db()
    exit_row = database.execute(
        "SELECT e.id,e.name,e.from_room_id FROM mud_world_exits e JOIN mud_world_rooms r "
        "ON r.id=e.from_room_id WHERE e.id=? "
        "AND r.world_id=(SELECT id FROM mud_world_packages WHERE active=1)", (exit_id,)
    ).fetchone()
    if exit_row is None:
        abort(404)
    if request.form.get("confirmation") != "delete":
        flash("Exit deletion was not confirmed.", "error")
        return _builder_redirect(exit_row["from_room_id"])
    database.execute("DELETE FROM mud_world_exits WHERE id=?", (exit_id,))
    database.commit()
    flash(f"Exit {exit_row['name']} removed.", "success")
    return _builder_redirect(exit_row["from_room_id"])


def _active_world_id(database):
    row = database.execute("SELECT id FROM mud_world_packages WHERE active=1").fetchone()
    if row is None:
        abort(404)
    return row["id"]


def _builder_dungeon_template(database, world_id, existing=None):
    """Start from pasted JSON, a cloned theme, or the template being edited, then apply guided fields."""
    from . import mud_world_dungeons
    text = request.form.get("template_json", "").strip()
    base = request.form.get("base", "").strip()
    if text:
        template = mud_world_dungeons.parse_template(text)
    elif base.startswith("theme:"):
        discovered = dungeons.discover_templates()
        if base[6:] not in discovered:
            raise mud_world_dungeons.WorldDungeonError("Choose an installed dungeon theme to start from.")
        template = {key: value for key, value in discovered[base[6:]][1].items()
                    if key not in ("origins", "skills", "author")}
    elif base.startswith("template:"):
        row = database.execute("SELECT template_json FROM mud_world_dungeon_templates WHERE id=? AND world_id=?",
                               (base[9:], world_id)).fetchone()
        if row is None:
            raise mud_world_dungeons.WorldDungeonError("Choose one of this world's templates to copy.")
        template = json.loads(row["template_json"])
    elif existing is not None:
        template = json.loads(existing["template_json"])
    else:
        raise mud_world_dungeons.WorldDungeonError("Paste template JSON or choose a theme to start from.")
    generation = template.get("generation") if isinstance(template.get("generation"), dict) else None
    if generation is not None:
        for rule in ("size", "difficulty"):
            values = {key: request.form.get(f"{rule}_{key}", "").strip()
                      for key in ("minimum", "default", "maximum")}
            if all(values.values()):
                try:
                    generation[rule] = {key: int(value) for key, value in values.items()}
                except ValueError as problem:
                    raise mud_world_dungeons.WorldDungeonError(f"Dungeon {rule} values must be whole numbers.") from problem
        weights = {layout: request.form.get(f"layout_{layout}", "").strip()
                   for layout in sorted(dungeons.SUPPORTED_LAYOUTS)}
        if any(weights.values()):
            try:
                chosen = [{"type": layout, "weight": int(weight)} for layout, weight in weights.items()
                          if weight and int(weight) > 0]
            except ValueError as problem:
                raise mud_world_dungeons.WorldDungeonError("Layout weights must be whole numbers.") from problem
            if not chosen:
                raise mud_world_dungeons.WorldDungeonError("Give at least one layout a weight above zero.")
            generation["layouts"] = chosen
    return template


def _dungeon_template_error(problem):
    errors = getattr(problem, "errors", None)
    if errors:
        shown = "; ".join(errors[:6])
        return f"Dungeon template is invalid: {shown}" + (f" (and {len(errors) - 6} more)" if len(errors) > 6 else "")
    return str(problem)


@bp.post("/builder/dungeons")
@builder_required
def builder_create_dungeon_template():
    from . import mud_world_dungeons
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    try:
        template = _builder_dungeon_template(database, world_id)
        mud_world_dungeons.save_template(database, world_id, request.form.get("local_key", "").strip(),
                                         request.form.get("name", ""), template)
    except (mud_world_dungeons.WorldDungeonError, dungeons.TemplateError) as problem:
        database.rollback()
        flash(_dungeon_template_error(problem), "error")
        return redirect(url_for("mud.builder", _anchor="dungeon-heading"))
    database.commit()
    flash("Dungeon template created.", "success")
    return redirect(url_for("mud.builder", _anchor="dungeon-heading"))


@bp.post("/builder/dungeons/<path:template_id>")
@builder_required
def builder_update_dungeon_template(template_id):
    from . import mud_world_dungeons
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    existing = database.execute("SELECT * FROM mud_world_dungeon_templates WHERE id=? AND world_id=?",
                                (template_id, world_id)).fetchone()
    if existing is None:
        abort(404)
    try:
        template = _builder_dungeon_template(database, world_id, existing)
        mud_world_dungeons.save_template(database, world_id, request.form.get("local_key", "").strip(),
                                         request.form.get("name", ""), template, template_id)
    except (mud_world_dungeons.WorldDungeonError, dungeons.TemplateError) as problem:
        database.rollback()
        flash(_dungeon_template_error(problem), "error")
        return redirect(url_for("mud.builder", _anchor=f"dungeon-{template_id}"))
    # New generation bounds can strand an entrance's overrides; those fall back to the template default.
    generation = template["generation"]
    layouts = {entry["type"] for entry in generation["layouts"]}
    for rule in ("difficulty", "size"):
        database.execute(
            f"UPDATE mud_world_dungeon_entrances SET {rule}=NULL WHERE template_id=? "
            f"AND {rule} IS NOT NULL AND ({rule}<? OR {rule}>?)",
            (template_id, generation[rule]["minimum"], generation[rule]["maximum"]),
        )
    database.execute(
        f"UPDATE mud_world_dungeon_entrances SET layout=NULL WHERE template_id=? AND layout IS NOT NULL "
        f"AND layout NOT IN ({','.join('?' for _ in layouts)})", (template_id, *sorted(layouts)),
    )
    database.commit()
    flash(f"Dungeon template {existing['name']} saved. New runs use it; runs in progress keep their layout.", "success")
    return redirect(url_for("mud.builder", _anchor=f"dungeon-{template_id}"))


@bp.post("/builder/dungeons/<path:template_id>/delete")
@builder_required
def builder_delete_dungeon_template(template_id):
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    existing = database.execute("SELECT * FROM mud_world_dungeon_templates WHERE id=? AND world_id=?",
                                (template_id, world_id)).fetchone()
    if existing is None:
        abort(404)
    if request.form.get("confirmation") != "delete":
        flash("Template deletion was not confirmed.", "error")
        return redirect(url_for("mud.builder", _anchor=f"dungeon-{template_id}"))
    if database.execute("SELECT 1 FROM mud_world_dungeon_entrances WHERE template_id=?",
                        (template_id,)).fetchone():
        flash("Remove or reassign the entrances that use this template first.", "error")
        return redirect(url_for("mud.builder", _anchor=f"dungeon-{template_id}"))
    database.execute("DELETE FROM mud_world_dungeon_templates WHERE id=?", (template_id,))
    database.commit()
    flash(f"Dungeon template {existing['name']} removed. Runs already in progress continue.", "success")
    return redirect(url_for("mud.builder", _anchor="dungeon-heading"))


def _builder_entrance_fields(database, world_id):
    from . import mud_world_dungeons
    room_id = request.form.get("room_id", "").strip()
    return_room_id = request.form.get("return_room_id", "").strip() or room_id
    rooms = {row["id"] for row in database.execute(
        "SELECT id FROM mud_world_rooms WHERE id IN (?,?) AND world_id=?", (room_id, return_room_id, world_id))}
    if room_id not in rooms or return_room_id not in rooms:
        raise mud_world_dungeons.WorldDungeonError("Choose existing entrance and return rooms in this world.")
    template_row = database.execute("SELECT * FROM mud_world_dungeon_templates WHERE id=? AND world_id=?",
                                    (request.form.get("template_id", ""), world_id)).fetchone()
    if template_row is None:
        raise mud_world_dungeons.WorldDungeonError("Choose one of this world's dungeon templates.")
    conditions, error = _builder_rules(database, "conditions_json", mud_conditions.validate_conditions)
    if error:
        raise mud_world_dungeons.WorldDungeonError(f"Entrance conditions: {error}")
    fields = mud_world_dungeons.entrance_fields(
        mud_world_dungeons.effective_template(template_row),
        {**request.form.to_dict(), "conditions": conditions, "enabled": request.form.get("enabled") == "1"})
    return room_id, return_room_id, template_row["id"], fields


def _save_entrance(database, world_id, entrance_id=None):
    room_id, return_room_id, template_id, fields = _builder_entrance_fields(database, world_id)
    if database.execute(
        "SELECT 1 FROM mud_world_dungeon_entrances WHERE room_id=? AND name=? COLLATE NOCASE AND id<>COALESCE(?,'')",
        (room_id, fields["name"], entrance_id),
    ).fetchone():
        from . import mud_world_dungeons
        raise mud_world_dungeons.WorldDungeonError("That room already has a dungeon entrance with this name.")
    values = (room_id, return_room_id, template_id, fields["name"], fields["description"],
              fields["difficulty"], fields["size"], fields["layout"], fields["max_party"],
              json.dumps(fields["conditions"], separators=(",", ":")), fields["conditions_message"],
              int(fields["enabled"]))
    if entrance_id is None:
        database.execute(
            "INSERT INTO mud_world_dungeon_entrances(id,world_id,room_id,return_room_id,template_id,name,"
            "description,difficulty,size,layout,max_party,conditions_json,conditions_message,enabled) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"world:dungeon-entrance:{uuid.uuid4().hex}", world_id, *values),
        )
    else:
        database.execute(
            "UPDATE mud_world_dungeon_entrances SET room_id=?,return_room_id=?,template_id=?,name=?,"
            "description=?,difficulty=?,size=?,layout=?,max_party=?,conditions_json=?,"
            "conditions_message=?,enabled=? WHERE id=?", (*values, entrance_id),
        )
    return room_id, fields["name"]


@bp.post("/builder/entrances")
@builder_required
def builder_create_entrance():
    from . import mud_world_dungeons
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    try:
        room_id, name = _save_entrance(database, world_id)
    except mud_world_dungeons.WorldDungeonError as problem:
        database.rollback()
        flash(str(problem), "error")
        return _builder_redirect(request.form.get("room_id"))
    database.commit()
    flash(f"Dungeon entrance {name} created.", "success")
    return _builder_redirect(room_id)


@bp.post("/builder/entrances/<path:entrance_id>")
@builder_required
def builder_update_entrance(entrance_id):
    from . import mud_world_dungeons
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    existing = database.execute("SELECT room_id FROM mud_world_dungeon_entrances WHERE id=? AND world_id=?",
                                (entrance_id, world_id)).fetchone()
    if existing is None:
        abort(404)
    try:
        room_id, name = _save_entrance(database, world_id, entrance_id)
    except mud_world_dungeons.WorldDungeonError as problem:
        database.rollback()
        flash(str(problem), "error")
        return _builder_redirect(existing["room_id"])
    database.commit()
    flash(f"Dungeon entrance {name} saved. Runs already in progress keep their settings.", "success")
    return _builder_redirect(room_id)


@bp.post("/builder/entrances/<path:entrance_id>/delete")
@builder_required
def builder_delete_entrance(entrance_id):
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    existing = database.execute("SELECT room_id,name FROM mud_world_dungeon_entrances WHERE id=? AND world_id=?",
                                (entrance_id, world_id)).fetchone()
    if existing is None:
        abort(404)
    if request.form.get("confirmation") != "delete":
        flash("Entrance deletion was not confirmed.", "error")
        return _builder_redirect(existing["room_id"])
    database.execute("DELETE FROM mud_world_dungeon_entrances WHERE id=?", (entrance_id,))
    database.commit()
    flash(f"Dungeon entrance {existing['name']} removed. Runs already in progress continue.", "success")
    return _builder_redirect(existing["room_id"])


TRIGGER_REFERENCE_TABLES = {
    "room": "mud_world_rooms", "object": "mud_item_instances", "item": "mud_item_definitions",
    "npc": "mud_npc_definitions", "npc_instance": "mud_npc_instances", "response": "mud_dialogue_responses",
    "quest": "mud_quests", "objective": "mud_quest_objectives", "exit": "mud_world_exits",
}


def _builder_trigger_context(database, rooms, exits_by_room, items_by_room, npcs_by_room,
                             nodes_by_conversation, responses_by_node, conversations):
    """Triggers, the content they can name, and a per-room list for the builder page."""
    from . import mud_worlds
    world_id = _active_world(database)
    room_names = {room["id"]: room["name"] for room in rooms}
    objects = [{**item, "room_name": room_names.get(item["room_id"], "")}
               for room_items in items_by_room.values() for item in room_items]
    placed_npcs = [{**npc, "room_name": room_names.get(npc["room_id"], "")}
                   for room_npcs in npcs_by_room.values() for npc in room_npcs]
    exits = [{**exit_row, "room_name": room_names.get(exit_row["from_room_id"], "")}
             for room_exits in exits_by_room.values() for exit_row in room_exits
             if exit_row["from_room_id"] in room_names]
    conversation_names = {item["id"]: item["name"] for item in conversations}
    responses = [{**response, "conversation_name": conversation_names.get(node["conversation_id"], "")}
                 for conversation_id, nodes in nodes_by_conversation.items() if conversation_id in conversation_names
                 for node in nodes for response in responses_by_node.get(node["id"], [])]
    object_rooms = {item["id"]: item["room_id"] for item in objects}
    triggers, triggers_by_room = [], {}
    fires = {row["trigger_id"]: row["fires"] for row in database.execute(
        "SELECT trigger_id,SUM(fires) AS fires FROM mud_trigger_fires WHERE realm=? GROUP BY trigger_id", (world_id,))}
    for trigger in mud_worlds.world_triggers(database, world_id):
        kind, ref = next(iter(trigger["source"].items()), ("", ""))
        entry = {**trigger, "summary": mud_triggers.describe(trigger), "source_key": f"{kind}|{ref}" if kind else "",
                 "conditions_text": json.dumps(trigger["conditions"], indent=1) if trigger["conditions"] else "",
                 "actions_text": json.dumps(trigger["actions"], indent=1), "fires": fires.get(trigger["global_id"], 0)}
        triggers.append(entry)
        room_id = ref if kind == "room" else object_rooms.get(ref) if kind == "object" else None
        if room_id:
            triggers_by_room.setdefault(room_id, []).append(entry)
    return {"triggers": triggers, "triggers_by_room": triggers_by_room, "trigger_objects": objects,
            "trigger_npcs": placed_npcs, "trigger_exits": exits, "trigger_responses": responses,
            "trigger_events": [(event, mud_triggers.EVENT_LABELS[event]) for event in mud_triggers.EVENT_TYPES],
            "trigger_action_types": mud_triggers.ACTION_TYPES}


def _trigger_local_id(database, world_id, name, requested, trigger_id):
    """The form's trigger key, or one made from the name that no other trigger uses."""
    taken = {row["local_id"] for row in database.execute(
        "SELECT local_id FROM mud_world_triggers WHERE world_id=? AND id<>?", (world_id, trigger_id))}
    if requested:
        return requested, ("Another trigger already uses that key." if requested in taken else None)
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:50] or "trigger"
    base = base if base[0].isalpha() else f"t-{base}"
    candidate, number = base, 2
    while candidate in taken:
        candidate, number = f"{base}-{number}", number + 1
    return candidate, None


def _builder_trigger_fields(database, world_id, trigger_id):
    """Validate the trigger form; everything it names must exist in the active world."""
    form = request.form
    name = " ".join(form.get("name", "").split())
    event = form.get("event", "")
    local_id, error = _trigger_local_id(database, world_id, name, form.get("local_id", "").strip(), trigger_id)
    if error:
        return None, error
    raw = {"id": local_id, "name": name or local_id, "event": event,
           "scope": "world" if event == "timer" else form.get("scope", "character"),
           "once": form.get("once") == "1", "enabled": form.get("enabled") == "1",
           "fail_message": form.get("fail_message", "")}
    source = form.get("source", "").strip()
    if source and event != "manual":
        kind, _, ref = source.partition("|")
        raw["source"] = {kind: ref}
    if event == "say_phrase":
        raw["phrase"] = form.get("phrase", "")
    numbers = {"cooldown_seconds": "cooldown_seconds", "max_fires": "max_fires"}
    if event == "timer":
        numbers["interval_seconds"] = "interval_seconds"
    for key in numbers:
        value = form.get(key, "").strip()
        if value:
            try:
                raw[key] = int(value)
            except ValueError:
                return None, f"{key.replace('_', ' ').capitalize()} must be a whole number."
    for key in ("conditions", "actions"):
        text = form.get(key, "").strip()
        try:
            raw[key] = json.loads(text) if text else []
        except json.JSONDecodeError:
            return None, f"{key.capitalize()} must be valid JSON, such as [{{\"type\": \"message\", \"text\": \"Hello\"}}]."
    try:
        trigger = mud_triggers.validate_trigger(raw)
    except ValueError as problem:
        return None, str(problem)
    origins = character_creation.catalog(database)
    known = {"race": {entry["key"] for entry in origins["races"]},
             "class": {entry["key"] for entry in origins["classes"]},
             "skill": {entry["key"] for entry in mud_skills.catalog(database)}}
    for kind, ref in mud_triggers.references(trigger):
        if kind in known:
            missing = ref not in known[kind]
        elif kind == "trigger":
            missing = database.execute("SELECT 1 FROM mud_world_triggers WHERE id=? AND world_id=? AND event='manual'",
                                       (ref, world_id)).fetchone() is None
            if missing:
                return None, f"run_trigger must name a manual trigger in this world: {ref}."
        else:
            missing = database.execute(f"SELECT 1 FROM {TRIGGER_REFERENCE_TABLES[kind]} WHERE id=?",
                                       (ref,)).fetchone() is None
        if missing:
            return None, f"The trigger names an unknown {kind.replace('_', ' ')}: {ref}."
    from . import mud_worlds
    chain = [{**entry, "id": entry["global_id"]} for entry in mud_worlds.world_triggers(database, world_id)
             if entry["global_id"] != trigger_id] + [{**trigger, "id": trigger_id}]
    try:
        mud_triggers.check_chains(chain)
    except ValueError as problem:
        return None, str(problem)
    return trigger, None


def _builder_trigger(database, trigger_id):
    row = database.execute("SELECT * FROM mud_world_triggers WHERE id=? AND world_id=?",
                           (trigger_id, _active_world_id(database))).fetchone()
    if row is None:
        abort(404)
    return row


def _events_redirect(trigger_id=None):
    return redirect(url_for("mud.builder", _anchor=f"trigger-{trigger_id}" if trigger_id else "events-heading"))


@bp.post("/builder/triggers")
@builder_required
def builder_create_trigger():
    from . import mud_worlds
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    trigger_id = f"world:{world_id}:trigger:{uuid.uuid4().hex}"
    trigger, error = _builder_trigger_fields(database, world_id, trigger_id)
    if error:
        flash(error, "error")
        return _events_redirect()
    mud_worlds.save_trigger(database, world_id, trigger_id, trigger)
    database.commit()
    flash(f"Trigger {trigger['name']} created.", "success")
    return _events_redirect(trigger_id)


@bp.post("/builder/triggers/reset-state")
@builder_required
def builder_reset_trigger_state():
    validate_csrf()
    database = get_db()
    if request.form.get("confirmation") != "reset":
        flash("Script state reset was not confirmed.", "error")
        return _events_redirect()
    reset_script_state(database, _active_world_id(database))
    database.commit()
    flash("World flags, trigger counts, object and exit states, and spawned NPCs were reset.", "success")
    return _events_redirect()


@bp.post("/builder/triggers/<path:trigger_id>/delete")
@builder_required
def builder_delete_trigger(trigger_id):
    validate_csrf()
    database = get_db()
    existing = _builder_trigger(database, trigger_id)
    if request.form.get("confirmation") != "delete":
        flash("Trigger deletion was not confirmed.", "error")
        return _events_redirect(trigger_id)
    callers = [row["name"] for row in database.execute(
        "SELECT name,definition_json FROM mud_world_triggers WHERE world_id=? AND id<>?",
        (existing["world_id"], trigger_id)) if trigger_id in row["definition_json"]]
    if callers:
        flash(f"Remove the run_trigger actions that call this trigger first: {', '.join(callers)}.", "error")
        return _events_redirect(trigger_id)
    database.execute("DELETE FROM mud_world_triggers WHERE id=?", (trigger_id,))
    database.execute("DELETE FROM mud_trigger_fires WHERE trigger_id=?", (trigger_id,))
    database.commit()
    flash(f"Trigger {existing['name']} removed.", "success")
    return _events_redirect()


@bp.post("/builder/triggers/<path:trigger_id>")
@builder_required
def builder_update_trigger(trigger_id):
    from . import mud_worlds
    validate_csrf()
    database = get_db()
    existing = _builder_trigger(database, trigger_id)
    trigger, error = _builder_trigger_fields(database, existing["world_id"], trigger_id)
    if error:
        flash(error, "error")
        return _events_redirect(trigger_id)
    mud_worlds.save_trigger(database, existing["world_id"], trigger_id, trigger)
    database.commit()
    flash(f"Trigger {trigger['name']} saved.", "success")
    return _events_redirect(trigger_id)


@bp.post("/builder/resource")
@builder_required
def builder_set_resource():
    from . import mud_worlds
    validate_csrf()
    try:
        resource = mud_worlds.validate_resource({
            "enabled": request.form.get("enabled") == "1",
            "name": request.form.get("name", "").strip() or mud_worlds.DEFAULT_RESOURCE["name"],
        })
    except mud_worlds.WorldError as error:
        flash(str(error), "error")
        return _builder_redirect()
    database = get_db()
    database.execute("UPDATE mud_world_packages SET resource_enabled=?,resource_name=? WHERE active=1",
                     (int(resource["enabled"]), resource["name"]))
    database.commit()
    flash(f"{resource['name']} is now this world's resource." if resource["enabled"]
          else "This world no longer uses a resource; spells are unavailable here.", "success")
    return _builder_redirect()


@bp.post("/builder/start")
@admin_required
def builder_set_start():
    validate_csrf()
    room_id = request.form.get("room_id", "").strip()
    database = get_db()
    try:
        database.execute("BEGIN IMMEDIATE")
        room = database.execute(
            "SELECT id,name FROM mud_world_rooms WHERE id=? "
            "AND world_id=(SELECT id FROM mud_world_packages WHERE active=1)", (room_id,)
        ).fetchone()
        if room is None:
            flash("Choose an existing room as the starting location.", "error")
            database.rollback()
            return _builder_redirect()
        database.execute("UPDATE mud_world_rooms SET is_start=0 WHERE is_start=1")
        database.execute(
            "UPDATE mud_world_rooms SET is_start=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (room_id,),
        )
        database.execute("UPDATE mud_world_packages SET start_room_id=? WHERE active=1", (room_id,))
        database.execute(
            "UPDATE mud_world_locations SET room_id=?,updated_at=CURRENT_TIMESTAMP WHERE room_id IS NULL",
            (room_id,),
        )
        database.commit()
    except sqlite3.Error:
        database.rollback()
        raise
    flash(f"{room['name']} is now the traditional-world starting room.", "success")
    return _builder_redirect(room_id)


@bp.post("/builder/permissions/<int:user_id>")
@admin_required
def builder_permission(user_id):
    validate_csrf()
    database = get_db()
    user = database.execute(
        "SELECT id,username,callsign,is_admin FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if user is None:
        abort(404)
    enabled = request.form.get("enabled") == "1"
    if enabled and not user["is_admin"]:
        database.execute(
            "INSERT INTO mud_builder_permissions(user_id,granted_by) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET granted_by=excluded.granted_by,"
            "granted_at=CURRENT_TIMESTAMP", (user_id, g.user["id"]),
        )
    else:
        database.execute("DELETE FROM mud_builder_permissions WHERE user_id=?", (user_id,))
    database.commit()
    if user["is_admin"]:
        flash("Station operators already have world-builder access.", "success")
    else:
        flash(
            f"World-builder access {'granted to' if enabled else 'revoked from'} "
            f"{user['username']} - [{user['callsign']}].",
            "success",
        )
    return _builder_redirect()


# --- Wilderness: room presets, regions and area fills -------------------------

WILDERNESS_VIEW = (41, 25)
MAX_PRESET_UPLOAD_BYTES = 2 * 1024 * 1024


def _wilderness_redirect(anchor=None, **arguments):
    return redirect(url_for("mud.builder_wilderness", _anchor=anchor,
                            **{key: value for key, value in arguments.items() if value not in (None, "")}))


def _definition_names(database, table, world_id):
    """Definition IDs by ID and by case-folded name, for the line-based preset fields."""
    rows = database.execute(
        f"SELECT d.id,d.name FROM {table} AS d WHERE (d.world_id=? OR d.id LIKE 'stationnet:%') AND NOT EXISTS "
        "(SELECT 1 FROM mud_dungeon_managed_definitions AS m WHERE m.definition_id=d.id) ORDER BY d.name COLLATE NOCASE",
        (world_id,)).fetchall()
    lookup = {row["id"]: row["id"] for row in rows}
    for row in rows:
        lookup.setdefault(row["name"].casefold(), row["id"])
    return rows, lookup


def _parse_entry_lines(text, lookup, label, fields):
    """Lines of 'name or ID, n, n, ...' with numeric defaults; returns dictionaries."""
    entries = []
    for number, line in enumerate(str(text or "").splitlines(), 1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        reference = lookup.get(parts[0]) or lookup.get(parts[0].casefold())
        if reference is None:
            raise mud_wilderness.WildernessError(f"{label} line {number}: no definition is named {parts[0]!r}.")
        entry = {"ref": reference}
        for (key, default), value in zip(fields, parts[1:] + [""] * len(fields)):
            if value == "":
                entry[key] = default
                continue
            try:
                entry[key] = float(value) if "." in value else int(value)
            except ValueError:
                raise mud_wilderness.WildernessError(f"{label} line {number}: {value!r} is not a number.") from None
        entries.append(entry)
    return entries


def _creature_lines(text, lookup):
    return [{"npc": entry["ref"], "weight": entry["weight"], "min_danger": entry["min_danger"],
             "max_danger": entry["max_danger"]}
            for entry in _parse_entry_lines(text, lookup, "Creatures",
                                            (("weight", 1), ("min_danger", 1), ("max_danger", 10)))]


def _resource_lines(text, lookup):
    return [{"item": entry["ref"], "chance": entry["chance"],
             "count": {"minimum": entry["minimum"], "maximum": max(entry["minimum"], entry["maximum"])}}
            for entry in _parse_entry_lines(text, lookup, "Resources",
                                            (("chance", 100), ("minimum", 1), ("maximum", 1)))]


def _paragraphs(text):
    """Descriptions are separated by blank lines; a leading 'N|' sets a weight."""
    entries = []
    for block in re.split(r"\n\s*\n", str(text or "").replace("\r\n", "\n")):
        block = " ".join(block.split())
        if not block:
            continue
        match = re.match(r"^(\d{1,4})\|\s*(.+)$", block)
        entries.append({"text": match.group(2), "weight": int(match.group(1))} if match else {"text": block})
    return entries


def _lines(text):
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _number_field(name, default=None):
    value = request.form.get(name, "").strip()
    if value == "":
        return default
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        raise mud_wilderness.WildernessError(f"{name.replace('_', ' ').capitalize()} must be a number.") from None


def _preset_from_form(database, world_id):
    """A preset from the guided editor; definitions may be named instead of given by ID."""
    _, npcs = _definition_names(database, "mud_npc_definitions", world_id)
    _, items = _definition_names(database, "mud_item_definitions", world_id)
    return {
        "key": request.form.get("key", "").strip(), "name": request.form.get("name", ""),
        "terrain": request.form.get("terrain", "custom"), "glyph": request.form.get("glyph", "."),
        "links": request.form.get("links", "4"),
        "names": _lines(request.form.get("names")),
        "descriptions": _paragraphs(request.form.get("descriptions")),
        "environment": {"light": request.form.get("light", "bright"),
                        "exposure": request.form.get("exposure", "open"),
                        "tags": [tag.strip() for tag in request.form.get("tags", "").split(",") if tag.strip()],
                        "lines": _lines(request.form.get("environment_lines"))},
        "encounters": {"chance": _number_field("encounter_chance", 0),
                       "max_live": _number_field("max_live", 1),
                       "despawn_seconds": _number_field("despawn_seconds", 600),
                       "creatures": _creature_lines(request.form.get("creatures"), npcs)},
        "resources": {"respawn_seconds": _number_field("respawn_seconds", 3600),
                      "max_per_room": _number_field("max_per_room", 2),
                      "items": _resource_lines(request.form.get("resources"), items)},
    }


def _preset_json(text):
    try:
        value = json.loads(text)
    except json.JSONDecodeError as problem:
        raise mud_wilderness.WildernessError(f"Preset JSON is invalid: {problem.msg} (line {problem.lineno}).") from None
    return value


def _check_preset_references(database, preset):
    npcs, items = mud_wilderness.preset_references(mud_wilderness.validate_preset(preset))
    problems = mud_wilderness.missing_references(database, npcs, items)
    if problems:
        raise mud_wilderness.WildernessError("This preset names " + ", ".join(problems) + ".")


def _region_from_form(database, world_id):
    _, npcs = _definition_names(database, "mud_npc_definitions", world_id)
    overrides = {"danger": _number_field("danger", 1),
                 "encounter_multiplier": _number_field("encounter_multiplier", 1),
                 "resource_multiplier": _number_field("resource_multiplier", 1),
                 "name_prefix": request.form.get("name_prefix", "").strip()}
    chance = _number_field("encounter_chance")
    if chance is not None:
        overrides["encounter_chance"] = chance
    creatures = _creature_lines(request.form.get("creatures"), npcs)
    if creatures or request.form.get("creature_mode") == "replace":
        overrides["creatures"] = {"mode": request.form.get("creature_mode", "add"), "list": creatures}
    return overrides


def _definition_label(names, value):
    return names.get(value, value)


def _wilderness_cell(database, world_id, text):
    """The inspector's view of one grid position, or None."""
    position = mud_wilderness.parse_reference("@" + str(text or "").replace(" ", ""))
    if position is None:
        return None
    row = database.execute(f"SELECT {mud_wilderness.ROOM_COLUMNS} FROM mud_world_rooms "
                           "WHERE world_id=? AND x=? AND y=? AND z=?", (world_id, *position)).fetchone()
    cell = {"x": position[0], "y": position[1], "z": position[2], "key": f"{position[0]},{position[1]},{position[2]}",
            "room": None}
    if row is None:
        return cell
    room = mud_wilderness.resolve_room(database, row)
    preset = database.execute("SELECT name FROM mud_world_room_presets WHERE id=?", (row["preset_id"],)).fetchone()
    region = database.execute("SELECT name FROM mud_world_regions WHERE id=?", (row["region_id"],)).fetchone()
    cell.update(
        room=room, preset_name=preset["name"] if preset else None, region_name=region["name"] if region else None,
        walls=set((row["blocked_dirs"] or "").split(",")) - {""},
        players=[dict(item) for item in database.execute(
            "SELECT DISTINCT COALESCE(c.display_name,u.callsign) AS name FROM mud_presence p JOIN users u ON u.id=p.user_id "
            "LEFT JOIN mud_characters c ON c.user_id=u.id WHERE p.location_type='world_room' AND p.location_key=?",
            (row["id"],))],
        npcs=[dict(item) for item in database.execute(
            "SELECT i.id,d.name,i.spawned_by_trigger FROM mud_npc_instances i JOIN mud_npc_definitions d "
            "ON d.id=i.definition_id WHERE i.room_id=? ORDER BY d.name COLLATE NOCASE", (row["id"],))],
        contents=[dict(item) for item in database.execute(
            "SELECT i.id,d.name FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id "
            "WHERE i.room_id=? ORDER BY d.name COLLATE NOCASE", (row["id"],))],
        entrances=[dict(item) for item in database.execute(
            "SELECT id,name FROM mud_world_dungeon_entrances WHERE room_id=? ORDER BY name COLLATE NOCASE", (row["id"],))],
        stored_exits=[dict(item) for item in database.execute(
            "SELECT e.id,e.name,e.direction,e.to_room_id FROM mud_world_exits e WHERE e.from_room_id=? "
            "ORDER BY e.direction,e.name", (row["id"],))],
        grid_exits=mud_wilderness.grid_exits(database, row["id"], {
            item["direction"] for item in database.execute(
                "SELECT direction FROM mud_world_exits WHERE from_room_id=?", (row["id"],))}),
    )
    for exit_row in cell["stored_exits"]:
        destination = _world_room(database, exit_row["to_room_id"])
        exit_row["destination_name"] = destination["name"] if destination else "?"
    return cell


@bp.get("/builder/wilderness")
@builder_required
def builder_wilderness():
    database = get_db()
    world_id = _active_world_id(database)
    world = database.execute("SELECT id,name,start_room_id FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
    npc_rows, _ = _definition_names(database, "mud_npc_definitions", world_id)
    item_rows, _ = _definition_names(database, "mud_item_definitions", world_id)
    npc_names = {row["id"]: row["name"] for row in npc_rows}
    item_names = {row["id"]: row["name"] for row in item_rows}
    presets = []
    for row in mud_wilderness.world_presets(database, world_id):
        preset = mud_wilderness.preset_for(database, row["id"])
        if preset is None:
            continue
        presets.append({
            **dict(row), "preset": preset,
            "json": json.dumps(preset, ensure_ascii=False, indent=2),
            "descriptions_text": "\n\n".join((f"{entry['weight']}| " if entry["weight"] != 1 else "") + entry["text"]
                                             for entry in preset["descriptions"]),
            "names_text": "\n".join(entry["text"] for entry in preset["names"]),
            "environment_text": "\n".join(entry["text"] for entry in preset["environment"]["lines"]),
            "creatures_text": "\n".join(f"{_definition_label(npc_names, entry['npc'])}, {entry['weight']}, "
                                        f"{entry['min_danger']}, {entry['max_danger']}"
                                        for entry in preset["encounters"]["creatures"]),
            "resources_text": "\n".join(f"{_definition_label(item_names, entry['item'])}, {entry['chance']:g}, "
                                        f"{entry['count']['minimum']}, {entry['count']['maximum']}"
                                        for entry in preset["resources"]["items"]),
        })
    regions = []
    for row in mud_wilderness.world_regions(database, world_id):
        overrides = mud_wilderness.region_for(database, row["id"])
        creatures = overrides.get("creatures", {"mode": "add", "list": []})
        regions.append({**dict(row), "overrides": overrides, "creature_mode": creatures["mode"],
                        "creatures_text": "\n".join(f"{_definition_label(npc_names, entry['npc'])}, {entry['weight']}, "
                                                    f"{entry['min_danger']}, {entry['max_danger']}"
                                                    for entry in creatures["list"])})
    cell = _wilderness_cell(database, world_id, request.args.get("cell"))
    start = database.execute("SELECT x,y,z FROM mud_world_rooms WHERE id=?", (world["start_room_id"],)).fetchone()
    center = (cell["x"], cell["y"], cell["z"]) if cell else (
        (start["x"], start["y"], start["z"]) if start and start["x"] is not None else (0, 0, 0))
    for index, axis in enumerate("xyz"):
        try:
            center = (*center[:index], int(request.args[axis]), *center[index + 1:])
        except (KeyError, ValueError):
            pass
    authored = database.execute(
        "SELECT id,name,x,y,z FROM mud_world_rooms WHERE world_id=? AND kind='authored' "
        "ORDER BY name COLLATE NOCASE,id", (world_id,)).fetchall()
    return render_template(
        "mud/wilderness.html", world=world, presets=presets, regions=regions, cell=cell, center=center,
        view=WILDERNESS_VIEW, authored_rooms=authored, npc_definitions=npc_rows, item_definitions=item_rows,
        bundled=sorted(mud_wilderness.bundled_presets().values(), key=lambda preset: preset["name"].casefold()),
        terrains=mud_wilderness.TERRAINS, lights=mud_wilderness.LIGHTS, exposures=mud_wilderness.EXPOSURES,
        directions=tuple(mud_wilderness.DELTAS), all_directions=DIRECTIONS,
        dungeon_templates=database.execute("SELECT id,name FROM mud_world_dungeon_templates WHERE world_id=? "
                                           "ORDER BY name COLLATE NOCASE", (world_id,)).fetchall(),
        cell_count=database.execute("SELECT COUNT(*) FROM mud_world_rooms WHERE world_id=? AND kind='wild'",
                                    (world_id,)).fetchone()[0],
        limits={"fill": mud_wilderness.MAX_FILL_CELLS, "world": mud_wilderness.MAX_WORLD_CELLS,
                "map": mud_wilderness.MAP_LIMIT, "coordinate": mud_wilderness.COORDINATE_LIMIT},
    )


@bp.get("/builder/wilderness/map")
@builder_required
def builder_wilderness_map():
    database = get_db()
    world_id = _active_world_id(database)
    try:
        x, y, z = (int(request.args.get(axis, 0)) for axis in "xyz")
        width = int(request.args.get("w", WILDERNESS_VIEW[0]))
        height = int(request.args.get("h", WILDERNESS_VIEW[1]))
    except ValueError:
        return jsonify(error="Map coordinates must be whole numbers."), 400
    limit = mud_wilderness.COORDINATE_LIMIT
    if any(abs(value) > limit for value in (x, y, z)):
        return jsonify(error="Map coordinates are out of range."), 400
    return jsonify(mud_wilderness.map_window(database, world_id, x, y, z, width, height))


@bp.post("/builder/room-presets")
@builder_required
def builder_create_room_preset():
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    base = request.form.get("base", "")
    key = request.form.get("key", "").strip()
    name = request.form.get("name", "")
    try:
        if request.form.get("preset_json", "").strip():
            preset = _preset_json(request.form["preset_json"])
        elif base.startswith("bundled:"):
            preset = mud_wilderness.bundled_presets().get(base.removeprefix("bundled:"))
        elif base.startswith("preset:"):
            preset = mud_wilderness.preset_for(database, base.removeprefix("preset:"))
        else:
            description = request.form.get("description", "").strip()
            preset = {"descriptions": [description] if description else []}
        if not isinstance(preset, dict):
            raise mud_wilderness.WildernessError("Choose a preset to start from.")
        preset = {**preset, **({"key": key} if key else {}), **({"name": name} if name.strip() else {})}
        _check_preset_references(database, preset)
        preset_id = mud_wilderness.save_preset(database, world_id, preset)
    except mud_wilderness.WildernessError as problem:
        database.rollback()
        flash(str(problem), "error")
        return _wilderness_redirect("presets-heading")
    database.commit()
    flash(f"Room preset {mud_wilderness.preset_for(database, preset_id)['name']} created.", "success")
    return _wilderness_redirect(f"preset-{preset_id}")


@bp.post("/builder/room-presets/<path:preset_id>")
@builder_required
def builder_update_room_preset(preset_id):
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    if database.execute("SELECT 1 FROM mud_world_room_presets WHERE id=? AND world_id=?",
                        (preset_id, world_id)).fetchone() is None:
        abort(404)
    try:
        preset = (_preset_json(request.form["preset_json"]) if request.form.get("preset_json", "").strip()
                  else _preset_from_form(database, world_id))
        _check_preset_references(database, preset)
        mud_wilderness.save_preset(database, world_id, preset, preset_id)
    except mud_wilderness.WildernessError as problem:
        database.rollback()
        flash(str(problem), "error")
        return _wilderness_redirect(f"preset-{preset_id}")
    database.commit()
    flash("Room preset saved. Every room using it now reflects the change.", "success")
    return _wilderness_redirect(f"preset-{preset_id}")


@bp.post("/builder/room-presets/<path:preset_id>/delete")
@builder_required
def builder_delete_room_preset(preset_id):
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    if request.form.get("confirmation") != "delete":
        flash("Preset removal was not confirmed.", "error")
        return _wilderness_redirect(f"preset-{preset_id}")
    try:
        moved = mud_wilderness.delete_preset(database, world_id, preset_id,
                                             request.form.get("reassign_to", "").strip() or None)
    except mud_wilderness.WildernessError as problem:
        database.rollback()
        flash(str(problem), "error")
        return _wilderness_redirect(f"preset-{preset_id}")
    database.commit()
    flash(f"Room preset removed{f'; {moved:,} rooms moved to another preset' if moved else ''}.", "success")
    return _wilderness_redirect("presets-heading")


@bp.post("/builder/room-presets/import")
@builder_required
def builder_import_room_presets():
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    uploaded = request.files.get("preset_file")
    payload = uploaded.read(MAX_PRESET_UPLOAD_BYTES + 1) if uploaded else b""
    overwrite = request.form.get("conflict") == "overwrite"
    drop_unknown = request.form.get("drop_unknown") == "1"
    try:
        if not payload or len(payload) > MAX_PRESET_UPLOAD_BYTES:
            raise mud_wilderness.WildernessError("Upload a preset JSON file of at most 2 MB.")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise mud_wilderness.WildernessError("Preset files must be UTF-8 JSON.") from None
        entries = document.get("presets") if isinstance(document, dict) and "presets" in document else [document]
        if not isinstance(entries, list) or not entries:
            raise mud_wilderness.WildernessError('Upload one preset object or {"presets": [...]}.')
        created = updated = skipped = 0
        for entry in entries:
            preset = mud_wilderness.validate_preset(entry)
            if drop_unknown:
                preset["encounters"]["creatures"] = [
                    item for item in preset["encounters"]["creatures"]
                    if not mud_wilderness.missing_references(database, {item["npc"]}, set())]
                preset["resources"]["items"] = [
                    item for item in preset["resources"]["items"]
                    if not mud_wilderness.missing_references(database, set(), {item["item"]})]
            _check_preset_references(database, preset)
            existing = database.execute("SELECT id FROM mud_world_room_presets WHERE world_id=? AND local_key=?",
                                        (world_id, preset["key"])).fetchone()
            if existing and not overwrite:
                skipped += 1
                continue
            mud_wilderness.save_preset(database, world_id, preset, existing["id"] if existing else None)
            updated, created = (updated + 1, created) if existing else (updated, created + 1)
    except mud_wilderness.WildernessError as problem:
        database.rollback()
        flash(f"Preset import failed: {problem}", "error")
        return _wilderness_redirect("presets-heading")
    database.commit()
    flash(f"Imported room presets: {created} created, {updated} replaced, {skipped} skipped.", "success")
    return _wilderness_redirect("presets-heading")


@bp.get("/builder/room-presets/export")
@builder_required
def builder_export_room_presets():
    database = get_db()
    world_id = _active_world_id(database)
    wanted = request.args.get("id")
    presets = [mud_wilderness.preset_for(database, row["id"]) for row in mud_wilderness.world_presets(database, world_id)
               if not wanted or row["id"] == wanted]
    presets = [preset for preset in presets if preset is not None]
    if wanted and not presets:
        abort(404)
    payload = json.dumps({"presets": presets}, ensure_ascii=False, indent=2).encode("utf-8")
    name = f"{presets[0]['key']}-preset.json" if wanted else f"{world_id}-room-presets.json"
    return send_file(BytesIO(payload), mimetype="application/json", as_attachment=True, download_name=name)


@bp.post("/builder/regions")
@builder_required
def builder_create_region():
    return _builder_save_region(None)


@bp.post("/builder/regions/<path:region_id>")
@builder_required
def builder_update_region(region_id):
    return _builder_save_region(region_id)


def _builder_save_region(region_id):
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    try:
        overrides = _region_from_form(database, world_id)
        problems = mud_wilderness.missing_references(
            database, mud_wilderness.region_references(mud_wilderness.validate_region_overrides(overrides)), set())
        if problems:
            raise mud_wilderness.WildernessError("This region names " + ", ".join(problems) + ".")
        region_id = mud_wilderness.save_region(database, world_id, request.form.get("key", "").strip(),
                                               request.form.get("name", ""), overrides, region_id)
    except mud_wilderness.WildernessError as problem:
        database.rollback()
        flash(str(problem), "error")
        return _wilderness_redirect(f"region-{region_id}" if region_id else "regions-heading")
    database.commit()
    flash("Region saved.", "success")
    return _wilderness_redirect(f"region-{region_id}")


@bp.post("/builder/regions/<path:region_id>/delete")
@builder_required
def builder_delete_region(region_id):
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    if request.form.get("confirmation") != "delete":
        flash("Region removal was not confirmed.", "error")
        return _wilderness_redirect(f"region-{region_id}")
    try:
        mud_wilderness.delete_region(database, world_id, region_id)
    except mud_wilderness.WildernessError as problem:
        database.rollback()
        flash(str(problem), "error")
        return _wilderness_redirect("regions-heading")
    database.commit()
    flash("Region removed; its rooms keep their presets without regional changes.", "success")
    return _wilderness_redirect("regions-heading")


@bp.post("/builder/wilderness/fill")
@builder_required
def builder_wilderness_fill():
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    try:
        shape = mud_wilderness.parse_shape(request.form)
        database.execute("BEGIN IMMEDIATE")
        result = mud_wilderness.fill(database, world_id, shape, request.form.get("preset_id", ""),
                                     request.form.get("region_id", "").strip() or None,
                                     request.form.get("mode", "empty"))
    except mud_wilderness.WildernessError as problem:
        database.rollback()
        flash(str(problem), "error")
        return _wilderness_redirect("fill-heading")
    database.commit()
    flash(f"Filled the area: {result['created']:,} rooms created, {result['updated']:,} rooms changed, "
          f"{result['skipped_authored']:,} authored rooms left untouched.", "success")
    return _wilderness_redirect("fill-heading", x=(shape["x1"] + shape["x2"]) // 2,
                                y=(shape["y1"] + shape["y2"]) // 2, z=shape["z"])


@bp.post("/builder/wilderness/clear")
@builder_required
def builder_wilderness_clear():
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    if request.form.get("confirm_clear") != "1":
        flash("Tick the confirmation box to remove wilderness rooms.", "error")
        return _wilderness_redirect("fill-heading")
    try:
        shape = mud_wilderness.parse_shape(request.form)
        database.execute("BEGIN IMMEDIATE")
        room_ids = mud_wilderness.cells_in(database, world_id, shape)
        entrances = mud_wilderness.entrance_count(database, room_ids)
        if entrances and request.form.get("remove_entrances") != "1":
            raise mud_wilderness.WildernessError(
                f"{entrances} dungeon entrances stand in that area. Tick 'also remove dungeon entrances' to proceed.")
        if room_ids:
            _move_room_occupants(database, room_ids, _start_room(database),
                                 "The land around you was reshaped by a world builder. You have been moved to {name}.")
            mud_wilderness.delete_cells(database, room_ids)
    except mud_wilderness.WildernessError as problem:
        database.rollback()
        flash(str(problem), "error")
        return _wilderness_redirect("fill-heading")
    database.commit()
    flash(f"Removed {len(room_ids):,} wilderness rooms. Authored rooms were left untouched.", "success")
    return _wilderness_redirect("fill-heading", x=(shape["x1"] + shape["x2"]) // 2,
                                y=(shape["y1"] + shape["y2"]) // 2, z=shape["z"])


@bp.post("/builder/wilderness/rooms/<path:room_id>/convert")
@builder_required
def builder_wilderness_convert(room_id):
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    row = database.execute("SELECT x,y,z FROM mud_world_rooms WHERE id=? AND world_id=?", (room_id, world_id)).fetchone()
    if row is None:
        abort(404)
    try:
        room = mud_wilderness.convert_to_authored(database, room_id)
    except mud_wilderness.WildernessError as problem:
        flash(str(problem), "error")
        return _wilderness_redirect("cell-inspector", cell=f"{row['x']},{row['y']},{row['z']}")
    database.commit()
    flash(f"{room['name']} is now an authored room. Edit it in the world builder.", "success")
    return redirect(url_for("mud.builder", _anchor=f"room-{room_id}"))


@bp.post("/builder/wilderness/rooms/<path:room_id>/options")
@builder_required
def builder_wilderness_cell_options(room_id):
    validate_csrf()
    database = get_db()
    world_id = _active_world_id(database)
    row = database.execute("SELECT x,y,z FROM mud_world_rooms WHERE id=? AND world_id=? AND kind='wild'",
                           (room_id, world_id)).fetchone()
    if row is None:
        abort(404)
    try:
        blocked = mud_wilderness.validate_blocked(request.form.getlist("blocked_dirs"))
    except mud_wilderness.WildernessError as problem:
        flash(str(problem), "error")
        return _wilderness_redirect("cell-inspector", cell=f"{row['x']},{row['y']},{row['z']}")
    region_id = request.form.get("region_id", "").strip() or None
    if region_id and database.execute("SELECT 1 FROM mud_world_regions WHERE id=? AND world_id=?",
                                      (region_id, world_id)).fetchone() is None:
        abort(400)
    database.execute("UPDATE mud_world_rooms SET grid_link=?,blocked_dirs=?,region_id=?,updated_at=CURRENT_TIMESTAMP "
                     "WHERE id=?", (int(request.form.get("grid_link") == "1"), blocked, region_id, room_id))
    database.commit()
    flash("Room links saved.", "success")
    return _wilderness_redirect("cell-inspector", cell=f"{row['x']},{row['y']},{row['z']}")
