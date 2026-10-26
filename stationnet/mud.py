"""Native MUD service and server-authoritative action dispatch."""
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
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for

from .auth import admin_required, login_required
from .db import get_db
from . import dungeons, character_creation
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
    input_method: str = "context"
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    client_id: str = ""


class ActionRegistry:
    """Maps every UI affordance and command alias to one action definition."""

    def __init__(self):
        self._actions = {}
        self._commands = {}
        self._context_providers = []

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

    def register_context_provider(self, provider):
        """Add a future room, inventory, or encounter action contributor."""
        self._context_providers.append(provider)

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

    def contextual(self, user, database=None):
        options = [
            {
                "key": item.key,
                "label": item.label,
                "description": item.description,
                "group": item.group,
                "parameters": {},
            }
            for item in self._actions.values()
            if item.show_context and _authorized(item, user)
        ]
        for provider in self._context_providers:
            for option in provider(database, user):
                definition = self.get(option.get("key"))
                if definition is not None and _authorized(definition, user):
                    options.append(option)
        return options


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
        "p.current_health,p.maximum_health,p.strength,p.agility,p.intellect,p.vitality "
        "FROM mud_characters AS c JOIN mud_character_progression AS p "
        "ON p.character_id=c.id WHERE c.user_id=? AND p.track_key=?",
        (user_id, track_key),
    ).fetchone()


def character_state(database, user_id, track_key=None):
    """Return the authoritative identity and selected progression track."""
    row = _character_row(database, user_id, track_key)
    origin = database.execute(
        "SELECT creation_state,race_key,class_key FROM mud_characters WHERE id=?", (row["id"],)
    ).fetchone()
    config = character_creation.options()
    race = next((item for item in config["races"] if item["key"] == origin["race_key"]), None)
    archetype = next((item for item in config["classes"] if item["key"] == origin["class_key"]), None)
    skills = [dict(skill) for skill in database.execute(
        "SELECT skill_key,rank,experience FROM mud_character_skills WHERE character_id=? ORDER BY skill_key",
        (row["id"],),
    )]
    level_floor = experience_for_level(row["level"])
    next_floor = experience_for_level(row["level"] + 1)
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": row["display_name"],
        "creation_state": origin["creation_state"],
        "race": race["name"] if race else None,
        "class": archetype["name"] if archetype else None,
        "skills": [{**skill, "name": next((item["name"] for item in config["skills"]
                                             if item["key"] == skill["skill_key"]), skill["skill_key"])}
                   for skill in skills],
        "mode": _current_mode(database, user_id),
        "progression_track": row["track_key"],
        "progression_label": {
            "shared": "Shared progression",
            "traditional": "Traditional progression",
            "procedural": "Procedural progression",
        }[row["track_key"]],
        "level": row["level"],
        "experience": row["experience"],
        "experience_into_level": row["experience"] - level_floor,
        "experience_for_next_level": next_floor - level_floor,
        "experience_to_next_level": next_floor - row["experience"],
        "health": row["current_health"],
        "maximum_health": row["maximum_health"],
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
    if before["creation_state"] == "complete":
        bonus = max(0, before["attributes"]["intellect"] - 8)
        bonus += _character_effects(database, before["id"])["experience_percent"]
        amount += amount * bonus // 100
        _practice_effect(database, before["id"], "experience_percent")
    experience = before["experience"] + amount
    if experience > MAX_EXPERIENCE:
        raise ValueError("Experience total exceeds the supported range.")
    level = level_for_experience(experience)
    levels_gained = level - before["level"]
    maximum_health = before["maximum_health"] + levels_gained * HEALTH_PER_LEVEL
    current_health = min(
        maximum_health, before["health"] + levels_gained * HEALTH_PER_LEVEL
    )
    database.execute(
        "UPDATE mud_character_progression SET experience=?,level=?,current_health=?,"
        "maximum_health=?,updated_at=CURRENT_TIMESTAMP "
        "WHERE character_id=? AND track_key=?",
        (experience, level, current_health, maximum_health, before["id"], resolved_track),
    )
    return {
        "amount": amount,
        "levels_gained": levels_gained,
        "previous_level": before["level"],
        "character": character_state(database, user_id, resolved_track),
    }


def _origin_options(database, character_id):
    row = database.execute(
        "SELECT race_key,class_key FROM mud_characters WHERE id=?", (character_id,)
    ).fetchone()
    config = character_creation.options()
    return tuple(next((item for item in config[group] if item["key"] == row[key]), None)
                 for group, key in (("races", "race_key"), ("classes", "class_key")))


def _character_effects(database, character_id):
    race, archetype = _origin_options(database, character_id)
    totals = {key: 0 for key in ("attack", "defense", "experience_percent")}
    for origin in (race, archetype):
        for key, value in (origin or {}).get("effects", {}).items():
            if key in totals:
                totals[key] += value
    ranks = character_creation.skill_ranks(database, character_id)
    for skill in character_creation.options()["skills"]:
        effect = skill.get("effect")
        if effect in totals:
            totals[effect] += ranks.get(skill["key"], 0) * skill.get("per_rank", 1)
    return totals


def _practice_effect(database, character_id, effect):
    for skill in character_creation.options()["skills"]:
        if skill.get("effect") == effect:
            character_creation.practice(database, character_id, skill["key"])


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
    skills = ", ".join(f"{skill['name']} {skill['rank']}" for skill in character["skills"])
    return [EventDraft(
        f"{character['name']} — Level {character['level']}\n"
        f"Origin: {character['race'] or 'Unchosen'} / {character['class'] or 'Unchosen'}\n"
        f"Health: {character['health']}/{character['maximum_health']}\n"
        f"Experience: {character['experience_into_level']}/"
        f"{character['experience_for_next_level']} "
        f"({character['experience_to_next_level']} to next level)\n"
        f"Strength {attributes['strength']} · Agility {attributes['agility']} · "
        f"Intellect {attributes['intellect']} · Vitality {attributes['vitality']}\n"
        f"Skills: {skills or 'none'}\n"
        f"Track: {character['progression_label']}",
        event_type="character",
    )]


def _help_action(_database, _user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The help action does not accept arguments.")
    return [EventDraft(
        "Available commands:\n"
        "/look (or /l, /examine, /x) — examine your surroundings\n"
        "/exits — list routes from this room\n"
        "/go <exit> (or /move, /walk) — travel through an exit\n"
        "/get <item> [from <container>], /drop <item>, /open <container>\n"
        "/put <item> in <container>, /equip <item>, /unequip <item>, /use <item>\n"
        "/combat, /attack <enemy>, /defend, /flee - turn-based combat\n"
        "/talk <npc>, /respond <number or response> — converse with an NPC\n"
        "/quests, /quest accept <name> — view or accept available quests\n"
        "/north, /south, /east, /west, /up, /down, /in, /out — direction shortcuts\n"
        "/status — check the MUD service\n"
        "/character (or /stats) — view your character and progression\n"
        "/portal enters the portal hub; /world returns when available\n"
        "/expedition <theme> [difficulty] [size], /join <code>, /run\n"
        "/daily, /daily enter, /daily join <code> — today's station challenge\n"
        "/shop buy <name>, /shop sell <item>, /storage deposit|withdraw <item> at the hub\n"
        "/complete at the exit; /retreat at the entrance keeps loot; /abandon loses it\n"
        "Text without a leading slash is spoken to everyone in your room."
    )]


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


def _set_player_location(database, user_id, mode, location_type, location_key,
                         instance_id=None):
    """Move shared identity state without making system locations world rooms."""
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
    valid = False
    if location is not None:
        if location["mode"] == "traditional" and modes["traditional"]:
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
        "SELECT room_key AS id,name,description,role,depth,difficulty,instance_id "
        "FROM mud_dungeon_rooms WHERE instance_id=? AND room_key=?",
        (instance_id, room_key),
    ).fetchone()
    if row is None:
        return None
    room = dict(row)
    room.update(location_type="dungeon_instance", mode="procedural", system_managed=True)
    return room


def _current_room(database, user_id):
    location = _reconcile_player_location(database, user_id)
    if location is None:
        raise ActionError("The MUD is currently disabled.", 404)
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
        _set_player_location(database, user_id, "procedural", "portal", PORTAL_ROOM_ID)
        return _portal_room(database)
    room = database.execute(
        "SELECT id,name,description FROM mud_world_rooms WHERE id=?",
        (location["location_key"],),
    ).fetchone()
    if room is not None:
        return dict(room)
    fallback = _start_room(database)
    _set_player_location(
        database, user_id, "traditional", "world_room", fallback["id"]
    )
    return dict(fallback)


def _room_exits(database, room_id, instance_id=None):
    if instance_id:
        return [dict(row) for row in database.execute(
            "SELECT (e.instance_id || ':exit:' || from_room_key || ':' || direction) AS id,"
            "e.name,e.direction,e.to_room_key AS to_room_id,r.name AS destination_name "
            "FROM mud_dungeon_exits AS e JOIN mud_dungeon_rooms AS r "
            "ON r.instance_id=e.instance_id AND r.room_key=e.to_room_key "
            "WHERE e.instance_id=? AND e.from_room_key=? ORDER BY e.direction,e.to_room_key",
            (instance_id, room_id),
        ).fetchall()]
    return database.execute(
        "SELECT e.id,e.name,e.direction,e.to_room_id,r.name AS destination_name "
        "FROM mud_world_exits AS e JOIN mud_world_rooms AS r ON r.id=e.to_room_id "
        "WHERE e.from_room_id=? ORDER BY "
        "CASE WHEN e.direction IS NULL THEN 1 ELSE 0 END,e.name COLLATE NOCASE,e.id",
        (room_id,),
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
    return [dict(row) for row in database.execute(
        "SELECT DISTINCT u.id,c.id AS character_id,"
        "COALESCE(c.display_name,u.callsign) AS character_name FROM mud_presence AS p "
        "JOIN users AS u ON u.id=p.user_id "
        "LEFT JOIN mud_characters AS c ON c.user_id=u.id "
        "WHERE p.location_type=? AND p.location_key=? AND p.user_id<>? "
        "AND (? IS NULL OR p.instance_id=?) "
        "ORDER BY character_name COLLATE NOCASE,u.id",
        (location_type, room_id, viewer_id, instance_id, instance_id),
    ).fetchall()]


def _exit_label(exit_row):
    route = exit_row["direction"] or exit_row["name"]
    return f"{route.upper()} — {exit_row['destination_name']}"


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
                "experience_reward": snapshot.get("experience_reward", 0),
                "respawn_seconds": snapshot.get("respawn_seconds", 86400),
            })
        return rows
    return database.execute(
        "SELECT i.id,i.definition_id,i.room_id,i.state_json,d.name,d.description,"
        "d.conversation_id,d.properties_json,d.behaviors_json,"
        "p.maximum_health AS combat_maximum_health,p.attack AS combat_attack,"
        "p.defense AS combat_defense,p.experience_reward,p.respawn_seconds "
        "FROM mud_npc_instances AS i JOIN mud_npc_definitions AS d "
        "ON d.id=i.definition_id LEFT JOIN mud_npc_combat_profiles AS p "
        "ON p.npc_definition_id=d.id WHERE i.room_id=? "
        "ORDER BY d.name COLLATE NOCASE,i.id", (room_id,),
    ).fetchall()


def _npc_match(rows, target):
    key = " ".join(str(target).casefold().split())
    matches = [row for row in rows if row["id"] == target or row["definition_id"] == target
               or " ".join(row["name"].casefold().split()) == key]
    if not matches:
        raise ActionError(f'You do not see an NPC matching "{target}" here.')
    if len(matches) > 1:
        raise ActionError(f'More than one NPC matches "{target}". Use an NPC control.')
    return matches[0]


def _json_properties(raw):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _integer_property(properties, *names):
    for name in names:
        value = properties.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 0


def _equipment_combat_totals(database, character_id=None, npc_definition_id=None):
    if character_id is not None:
        owner = database.execute(
            "SELECT user_id FROM mud_characters WHERE id=?", (character_id,)
        ).fetchone()
        loadout = _inventory_context(database, owner["user_id"])["loadout"]
        rows = database.execute(
            "SELECT d.properties_json FROM mud_character_equipment AS e "
            "JOIN mud_item_instances AS i ON i.id=e.item_instance_id "
            "JOIN mud_item_definitions AS d ON d.id=i.definition_id "
            "WHERE e.character_id=? AND e.loadout_key=? AND i.owner_character_id=?",
            (character_id, loadout, character_id),
        ).fetchall()
    else:
        rows = database.execute(
            "SELECT d.properties_json FROM mud_npc_equipment AS e "
            "JOIN mud_item_definitions AS d ON d.id=e.item_definition_id "
            "WHERE e.npc_definition_id=?", (npc_definition_id,),
        ).fetchall()
    attack = defense = 0
    for row in rows:
        properties = _json_properties(row["properties_json"])
        attack += max(0, _integer_property(properties, "damage", "attack", "attack_bonus"))
        defense += max(0, _integer_property(properties, "armor", "defense", "defense_bonus"))
    return {"attack": attack, "defense": defense}


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
    if content_id and latest is not None and latest["status"] == "defeated":
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
    for npc in _room_npc_rows(database, room["id"], room.get("instance_id")):
        encounter = _encounter_row(database, npc)
        if encounter is None:
            continue
        enemies.append({
            "id": npc["id"], "definition_id": npc["definition_id"], "name": npc["name"],
            "encounter_id": encounter["id"], "status": encounter["status"],
            "health": encounter["current_health"], "maximum_health": encounter["maximum_health"],
            "attack": npc["combat_attack"], "defense": npc["combat_defense"],
        })
    return {"scope_type": "instance" if room.get("instance_id") else "room",
            "scope_key": room.get("instance_id") or room["id"], "enemies": enemies}


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
    room = _current_room(database, user_id)
    portal = room["id"] == PORTAL_ROOM_ID
    instance_id = room.get("instance_id")
    npcs = []
    for npc in _room_npc_rows(database, room["id"], instance_id):
        entry = {"id": npc["id"], "definition_id": npc["definition_id"],
                 "name": npc["name"], "description": npc["description"],
                 "hostile": _npc_aggressive(npc)}
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
        for exit_row in _room_exits(database, room["id"], instance_id)
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
    return {
        "id": room["id"],
        "name": room["name"],
        "description": room["description"],
        "instructions": (room["instructions"] if portal else
                         _expedition_status_text(database, user_id) if instance_id else ""),
        "system_managed": portal,
        "mode": "procedural" if portal or instance_id else "traditional",
        "exits": exits,
        "occupants": _room_occupants(database, room["id"], user_id, instance_id),
        "items": [{"id": item["id"], "name": item["name"], "item_type": item["item_type"]} for item in _room_item_rows(database, room["id"], instance_id)],
        "npcs": npcs,
    }


def _describe_room(database, room, viewer_id=None):
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    exits = _room_exits(database, room["id"], instance_id)
    routes = ", ".join(_exit_label(item) for item in exits) if exits else "none"
    occupants = _room_occupants(database, room["id"], viewer_id, instance_id) if viewer_id else []
    people = ", ".join(item["character_name"] for item in occupants) if occupants else "none"
    items = ", ".join(item["name"] for item in _room_item_rows(database, room["id"], instance_id)) or "none"
    npcs = ", ".join(item["name"] for item in _room_npc_rows(database, room["id"], instance_id)) or "none"
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
    return (
        f"{room['name']}\n{room['description']}{instructions}\n\n"
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


def _item_matches(rows, target):
    key = " ".join(target.casefold().split())
    matches = [row for row in rows if " ".join(row["name"].casefold().split()) == key or row["id"] == target]
    if not matches:
        raise ActionError(f'You cannot find "{target}".')
    if len(matches) > 1:
        raise ActionError(f'More than one item matches "{target}". Use its item control.')
    return matches[0]


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
    return database.execute(
        "SELECT i.*,d.name,d.description,d.item_type,d.equipment_slot AS definition_slot,d.properties_json "
        "FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id "
        "WHERE i.room_id=? ORDER BY d.name COLLATE NOCASE,i.id", (room_id,)
    ).fetchall()


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


def inventory_state(database, user_id):
    context = _inventory_context(database, user_id)
    inventory = [dict(row) for row in _inventory_rows(database, user_id)]
    equipment = [dict(row) for row in _inventory_rows(database, user_id, equipped=True)]
    return {
        "inventory": inventory, "equipment": equipment, "slots": EQUIPMENT_SLOTS,
        "mode": context["mode"], "sharing": "shared" if context["shared"] else "independent",
    }


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
                  "price": _item_value(row)} for row in catalog],
        "storage": [{"id": row["id"], "name": row["name"]} for row in stored],
    }


def _require_hub(database, user_id):
    if _current_room(database, user_id)["id"] != PORTAL_ROOM_ID:
        raise ActionError("The trader and personal storage are only available in the portal room.")


def _shop_action(database, user, parameters):
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
        price = _item_value(row)
        _wallet(database, character["id"])
        if not database.execute(
            "UPDATE mud_character_wallets SET coins=coins-? WHERE character_id=? AND coins>=?",
            (price, character["id"], price),
        ).rowcount:
            raise ActionError("You do not have enough coins.")
        _grant_item(database, user["id"], row["id"], "shop:item")
        return [EventDraft(f"You buy {row['name']} for {price} coins.", event_type="shop")]
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
        price = max(1, _item_value(item) * 2 // 5)
        database.execute("DELETE FROM mud_item_instances WHERE id=? AND owner_character_id=?",
                         (item["id"], character["id"]))
        _wallet(database, character["id"])
        database.execute("UPDATE mud_character_wallets SET coins=coins+? WHERE character_id=?",
                         (price, character["id"]))
        return [EventDraft(f"You sell {item['name']} for {price} coins.", event_type="shop")]
    raise ActionError("Use /shop, /shop buy <name>, or /shop sell <item>.")


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
    }
    table, fallback = tables[objective["objective_type"]]
    row = database.execute(f"SELECT name FROM {table} WHERE id=?", (objective["target_id"],)).fetchone()
    return row["name"] if row else f"Unknown {fallback}"


def quest_state(database, user_id):
    """Return player-specific active/completed quest state for rendering."""
    character = _character_row(database, user_id)
    quests = []
    rows = database.execute(
        "SELECT q.id,q.name,q.description,p.status,p.accepted_at,p.completed_at,p.rewarded_at "
        "FROM mud_player_quests AS p JOIN mud_quests AS q ON q.id=p.quest_id "
        "WHERE p.character_id=? ORDER BY p.status,q.name COLLATE NOCASE,q.id",
        (character["id"],),
    ).fetchall()
    for row in rows:
        entry = dict(row)
        entry["objectives"] = []
        objectives = database.execute(
            "SELECT o.*,COALESCE(p.progress,0) AS progress,p.completed_at "
            "FROM mud_quest_objectives AS o LEFT JOIN mud_player_objectives AS p "
            "ON p.objective_id=o.id AND p.character_id=? WHERE o.quest_id=? "
            "ORDER BY o.sort_order,o.id", (character["id"], row["id"]),
        ).fetchall()
        for objective in objectives:
            item = dict(objective)
            item["target_name"] = _objective_target_name(database, objective)
            entry["objectives"].append(item)
        quests.append(entry)
    return {"active": [q for q in quests if q["status"] == "active"],
            "completed": [q for q in quests if q["status"] == "completed"]}


def _award_quest_rewards(database, user, quest_id):
    character = _character_row(database, user["id"])
    player_quest = database.execute(
        "SELECT rewarded_at FROM mud_player_quests WHERE character_id=? AND quest_id=?",
        (character["id"], quest_id),
    ).fetchone()
    if player_quest is None or player_quest["rewarded_at"] is not None:
        return []
    messages = []
    for reward in database.execute(
        "SELECT * FROM mud_quest_rewards WHERE quest_id=? ORDER BY id", (quest_id,),
    ):
        if reward["reward_type"] == "experience":
            result = award_experience(database, user["id"], reward["amount"])
            messages.append(f"{reward['amount']} experience")
            if result["levels_gained"]:
                messages.append(f"level {result['character']['level']}")
        elif reward["reward_type"] == "item":
            definition = database.execute(
                "SELECT name FROM mud_item_definitions WHERE id=?", (reward["target_id"],)
            ).fetchone()
            if definition is None:
                continue
            for _ in range(reward["amount"]):
                _grant_item(database, user["id"], reward["target_id"])
            messages.append(f"{reward['amount']} × {definition['name']}")
    database.execute(
        "UPDATE mud_player_quests SET rewarded_at=CURRENT_TIMESTAMP WHERE character_id=? "
        "AND quest_id=? AND rewarded_at IS NULL", (character["id"], quest_id),
    )
    return messages


def _finish_ready_quests(database, user):
    character = _character_row(database, user["id"])
    completed = []
    active = database.execute(
        "SELECT q.id,q.name FROM mud_player_quests AS p JOIN mud_quests AS q ON q.id=p.quest_id "
        "WHERE p.character_id=? AND p.status='active'", (character["id"],),
    ).fetchall()
    for quest in active:
        remaining = database.execute(
            "SELECT 1 FROM mud_quest_objectives AS o LEFT JOIN mud_player_objectives AS p "
            "ON p.objective_id=o.id AND p.character_id=? WHERE o.quest_id=? "
            "AND (p.completed_at IS NULL) LIMIT 1", (character["id"], quest["id"]),
        ).fetchone()
        if remaining is not None:
            continue
        database.execute(
            "UPDATE mud_player_quests SET status='completed',completed_at=CURRENT_TIMESTAMP "
            "WHERE character_id=? AND quest_id=? AND status='active'",
            (character["id"], quest["id"]),
        )
        rewards = _award_quest_rewards(database, user, quest["id"])
        suffix = f" Rewards: {', '.join(rewards)}." if rewards else ""
        completed.append(EventDraft(f"Quest complete: {quest['name']}.{suffix}", event_type="quest"))
    return completed


def _set_objective_progress(database, user, objective_type, target_id):
    character = _character_row(database, user["id"])
    objectives = database.execute(
        "SELECT o.id,o.required_count FROM mud_quest_objectives AS o "
        "JOIN mud_player_quests AS q ON q.quest_id=o.quest_id "
        "WHERE q.character_id=? AND q.status='active' AND o.objective_type=? AND o.target_id=?",
        (character["id"], objective_type, target_id),
    ).fetchall()
    for objective in objectives:
        if objective_type == "possess_item":
            visible = [*list(_inventory_rows(database, user["id"])),
                       *list(_inventory_rows(database, user["id"], True))]
            count = sum(item["definition_id"] == target_id for item in visible)
            progress = min(count, objective["required_count"])
        else:
            progress = objective["required_count"]
        database.execute(
            "INSERT INTO mud_player_objectives(character_id,objective_id,progress,completed_at) "
            "VALUES(?,?,?,CASE WHEN ? THEN CURRENT_TIMESTAMP END) "
            "ON CONFLICT(character_id,objective_id) DO UPDATE SET progress=MAX(progress,excluded.progress),"
            "completed_at=CASE WHEN completed_at IS NOT NULL THEN completed_at "
            "WHEN excluded.progress>=(SELECT required_count FROM mud_quest_objectives WHERE id=excluded.objective_id) "
            "THEN CURRENT_TIMESTAMP END,updated_at=CURRENT_TIMESTAMP",
            (character["id"], objective["id"], progress,
             1 if progress >= objective["required_count"] else 0),
        )
    return _finish_ready_quests(database, user)


def _accept_quest(database, user, quest_id):
    character = _character_row(database, user["id"])
    quest = database.execute("SELECT * FROM mud_quests WHERE id=?", (quest_id,)).fetchone()
    if quest is None:
        raise ActionError("That quest no longer exists.")
    existing = database.execute(
        "SELECT status FROM mud_player_quests WHERE character_id=? AND quest_id=?",
        (character["id"], quest_id),
    ).fetchone()
    if existing:
        raise ActionError("You have already accepted that quest.")
    if quest["prerequisite_quest_id"] and database.execute(
        "SELECT 1 FROM mud_player_quests WHERE character_id=? AND quest_id=? "
        "AND status='completed' AND rewarded_at IS NOT NULL",
        (character["id"], quest["prerequisite_quest_id"]),
    ).fetchone() is None:
        raise ActionError("You have not completed this quest's prerequisite.")
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
    events += _set_objective_progress(database, user, "visit_room", room["id"])
    definitions = {row["definition_id"] for row in (
        *list(_inventory_rows(database, user["id"])),
        *list(_inventory_rows(database, user["id"], True)),
    )}
    for definition_id in definitions:
        events += _set_objective_progress(database, user, "possess_item", definition_id)
    return events


def _describe_item(database, row, user_id=None):
    body = f"{row['name']}\n{row['description']}\nType: {row['item_type']}"
    if row["definition_slot"]:
        body += f"\nEquipment slot: {row['definition_slot'].replace('_', ' ')}"
    if _is_container(row):
        contents = _container_rows(database, row["id"], user_id)
        if row["is_open"]:
            body += "\nContents: " + (", ".join(child["name"] for child in contents) or "empty")
        else:
            body += "\nIt is closed."
    return body


def _examine_item(database, user, room, target):
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    candidates = list(_room_item_rows(database, room["id"], instance_id)) + list(_inventory_rows(database, user["id"])) + list(_inventory_rows(database, user["id"], True))
    for container in _room_item_rows(database, room["id"], instance_id):
        if _is_container(container) and container["is_open"]:
            candidates.extend(_container_rows(database, container["id"], user["id"]))
    key = " ".join(target.casefold().split())
    matching = [item for item in candidates if item["id"] == target or " ".join(item["name"].casefold().split()) == key]
    if not matching:
        return None
    if len(matching) > 1:
        raise ActionError(f'More than one item matches "{target}".')
    return _describe_item(database, matching[0], user["id"])


examinations.register(_examine_item)


def _examine_npc(_database, _user, room, target):
    key = " ".join(target.casefold().split())
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    matches = [npc for npc in _room_npc_rows(_database, room["id"], instance_id)
               if npc["id"] == target or " ".join(npc["name"].casefold().split()) == key]
    if not matches:
        return None
    if len(matches) > 1:
        raise ActionError(f'More than one NPC matches "{target}".')
    npc = matches[0]
    body = f"{npc['name']}\n{npc['description']}"
    if npc["combat_maximum_health"] is not None:
        encounter = _encounter_row(_database, npc)
        body += (f"\nHostile · {encounter['status']} · Health "
                 f"{encounter['current_health']}/{encounter['maximum_health']}"
                 f" · Attack {npc['combat_attack']} · Defense {npc['combat_defense']}")
    return body


examinations.register(_examine_npc)


def _combat_target(parameters):
    return str(parameters.get("npc_id") or " ".join(
        str(item) for item in parameters.get("arguments", [])
    )).strip()


def _encounter_reward_drafts(database, encounter, npc):
    drafts = []
    if encounter["scope_type"] == "instance":
        participants = database.execute(
            "SELECT p.character_id,c.user_id,c.display_name FROM mud_combat_participants AS p "
            "JOIN mud_characters AS c ON c.id=p.character_id "
            "JOIN mud_player_locations AS l ON l.user_id=c.user_id "
            "JOIN mud_expedition_participants AS ep ON ep.instance_id=l.instance_id "
            "AND ep.user_id=c.user_id "
            "WHERE p.encounter_id=? AND p.damage_dealt>0 AND p.left_at IS NULL "
            "AND l.instance_id=? AND ep.status='active' ORDER BY p.joined_at,p.character_id",
            (encounter["id"], encounter["scope_key"]),
        ).fetchall()
    else:
        participants = database.execute(
            "SELECT p.character_id,c.user_id,c.display_name FROM mud_combat_participants AS p "
            "JOIN mud_characters AS c ON c.id=p.character_id "
            "JOIN mud_world_locations AS l ON l.user_id=c.user_id "
            "WHERE p.encounter_id=? AND p.damage_dealt>0 AND p.left_at IS NULL "
            "AND l.room_id=? ORDER BY p.joined_at,p.character_id",
            (encounter["id"], encounter["scope_key"]),
        ).fetchall()
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
        rewards = []
        experience = npc["experience_reward"]
        for reward in configured:
            if reward["reward_type"] == "experience":
                experience += reward["amount"]
            else:
                for _ in range(reward["amount"]):
                    _grant_item(
                        database, participant["user_id"], reward["target_id"],
                        "combat:reward",
                    )
                rewards.append(f"{reward['amount']} × {reward['item_name']}")
        if experience:
            result = award_experience(database, participant["user_id"], experience)
            rewards.insert(0, f"{experience} experience")
            if result["levels_gained"]:
                rewards.append(f"level {result['character']['level']}")
        summary = ", ".join(rewards) if rewards else "no configured rewards"
        drafts.append(EventDraft(
            f"Victory rewards for defeating {npc['name']}: {summary}.",
            event_type="reward", audience_key=str(participant["user_id"]),
        ))
    return drafts


def _npc_aggressive(npc):
    if npc["combat_maximum_health"] is None:
        return False
    behavior = _json_properties(npc["behaviors_json"])
    return bool(behavior.get("aggressive", not any(
        behavior.get(key) for key in ("friendly", "neutral", "passive")
    )))


def _active_hostiles(database, room):
    enemies = []
    for npc in _room_npc_rows(database, room["id"], room.get("instance_id")):
        if not _npc_aggressive(npc):
            continue
        encounter = _encounter_row(database, npc)
        if encounter["status"] in {"ready", "active"}:
            enemies.append((npc, encounter))
    return enemies


def _enemy_turn(database, user, defending=False, chosen=None):
    room = _current_room(database, user["id"])
    enemies = _active_hostiles(database, room)
    if not enemies:
        return []
    npc, encounter = next(
        ((npc, encounter) for npc, encounter in enemies if chosen == npc["id"]), enemies[0]
    )
    character = _character_row(database, user["id"])
    database.execute(
        "INSERT INTO mud_combat_participants(encounter_id,character_id) VALUES(?,?) "
        "ON CONFLICT(encounter_id,character_id) DO UPDATE SET left_at=NULL,"
        "defeated_at=NULL,last_action_at=CURRENT_TIMESTAMP",
        (encounter["id"], character["id"]),
    )
    database.execute(
        "UPDATE mud_combat_encounters SET status='active',"
        "started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND status='ready'", (encounter["id"],),
    )
    enemy_gear = _equipment_combat_totals(database, npc_definition_id=npc["definition_id"])
    player_gear = _equipment_combat_totals(database, character_id=character["id"])
    effects = _character_effects(database, character["id"])
    damage = max(1, npc["combat_attack"] + enemy_gear["attack"]
                 - (character["vitality"] // 10 + character["agility"] // 5
                    + player_gear["defense"] + effects["defense"]))
    if defending:
        damage = max(0, damage // 3)
    _practice_effect(database, character["id"], "defense")
    health = max(0, character["current_health"] - damage)
    database.execute(
        "UPDATE mud_character_progression SET current_health=?,updated_at=CURRENT_TIMESTAMP "
        "WHERE character_id=? AND track_key=?", (health, character["id"], character["track_key"]),
    )
    drafts = [EventDraft(
        f"{npc['name']} strikes for {damage} damage ({health}/{character['maximum_health']} health).",
        event_type="combat",
    )]
    if health == 0:
        refreshed = database.execute(
            "SELECT * FROM mud_combat_encounters WHERE id=?", (encounter["id"],)
        ).fetchone()
        drafts += _defeat_player(database, user, refreshed, npc)
    return drafts


def _entry_attack(database, user):
    room = _current_room(database, user["id"])
    enemies = _active_hostiles(database, room)
    if not enemies:
        return []
    return [EventDraft(f"{enemies[0][0]['name']} notices you and attacks!", event_type="combat")] + \
        _enemy_turn(database, user, chosen=enemies[0][0]["id"])


def _defeat_player(database, user, encounter, npc):
    character = _character_row(database, user["id"])
    old_room = _current_room(database, user["id"])
    database.execute(
        "UPDATE mud_character_progression SET current_health=maximum_health,"
        "updated_at=CURRENT_TIMESTAMP WHERE character_id=? AND track_key=?",
        (character["id"], character["track_key"]),
    )
    _leave_combat(database, user["id"], encounter["scope_type"], encounter["scope_key"], True)
    if old_room.get("instance_id"):
        instance_id = old_room["instance_id"]
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
        _set_player_location(database, user["id"], "procedural", "portal", PORTAL_ROOM_ID)
        _finish_empty_expedition(database, instance_id, "failure")
        return [
            EventDraft(
                f"{npc['name']} defeats you. Your expedition ends and you recover in "
                f"{_portal_room(database)['name']}.", event_type="defeat",
            ),
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
            f"{npc['name']} defeats you. You awaken fully recovered in {start['name']}.",
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
    npc = _npc_match(_room_npc_rows(database, room["id"], room.get("instance_id")), target)
    if npc["combat_maximum_health"] is None:
        raise ActionError(f"{npc['name']} is not hostile.")
    encounter = _encounter_row(database, npc)
    if encounter["status"] == "defeated":
        raise ActionError(f"{npc['name']} is defeated and has not yet recovered.", 409)
    character = _character_row(database, user["id"])
    database.execute(
        "INSERT INTO mud_combat_participants(encounter_id,character_id) VALUES(?,?) "
        "ON CONFLICT(encounter_id,character_id) DO UPDATE SET left_at=NULL,"
        "defeated_at=NULL,last_action_at=CURRENT_TIMESTAMP",
        (encounter["id"], character["id"]),
    )
    player_gear = _equipment_combat_totals(database, character_id=character["id"])
    enemy_gear = _equipment_combat_totals(database, npc_definition_id=npc["definition_id"])
    effects = _character_effects(database, character["id"])
    damage = max(1, 4 + character["strength"] // 2 + player_gear["attack"]
                 + effects["attack"] - (npc["combat_defense"] + enemy_gear["defense"]) // 2)
    _practice_effect(database, character["id"], "attack")
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
    drafts = [EventDraft(
        f"{character['display_name']} attacks {npc['name']} for {damage} damage "
        f"({remaining}/{encounter['maximum_health']} health).",
        event_type="combat", audience_type=audience_type, audience_key=audience_key,
        recipient_ids=tuple(sorted(recipients)),
    )]
    if remaining == 0:
        defeated = database.execute(
            "SELECT * FROM mud_combat_encounters WHERE id=?", (encounter["id"],),
        ).fetchone()
        drafts.append(EventDraft(
            f"{npc['name']} is defeated.", event_type="victory", audience_type=audience_type,
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


def _defend_action(database, user, _parameters):
    if not _active_hostiles(database, _current_room(database, user["id"])):
        raise ActionError("There is no hostile enemy to defend against.")
    return [EventDraft("You brace for the next blow.", event_type="combat")] + \
        _enemy_turn(database, user, defending=True)


def _flee_action(database, user, _parameters):
    room = _current_room(database, user["id"])
    if not _active_hostiles(database, room):
        raise ActionError("There is no hostile enemy to flee from.")
    character = _character_row(database, user["id"])
    chance = min(75, max(35, 50 + (character["agility"] - 10) * 2))
    if secrets.randbelow(100) >= chance:
        return [EventDraft("Your escape fails.", event_type="combat")] + _enemy_turn(database, user)
    drafts = [EventDraft("You slip past the enemy, but it lashes out as you flee.", event_type="combat")]
    drafts += _enemy_turn(database, user)
    if _current_room(database, user["id"])["id"] != room["id"]:
        return drafts
    instance_id = room.get("instance_id")
    if instance_id:
        if room["role"] == "entrance":
            return drafts + _retreat_run(database, user, evaded=True)
        exits = _room_exits(database, room["id"], instance_id)
        candidates = [database.execute(
            "SELECT depth FROM mud_dungeon_rooms WHERE instance_id=? AND room_key=?",
            (instance_id, exit_row["to_room_id"]),
        ).fetchone()["depth"] for exit_row in exits]
        chosen = exits[candidates.index(min(candidates))]
        _leave_combat(database, user["id"], "instance", instance_id)
        _set_player_location(database, user["id"], "procedural", "dungeon_instance",
                             chosen["to_room_id"], instance_id)
        _record_expedition_visit(database, instance_id, user["id"], chosen["to_room_id"])
        drafts.append(EventDraft(f"You flee {chosen['direction']}.", event_type="movement"))
        return drafts + _entry_attack(database, user)
    exits = _room_exits(database, room["id"])
    if not exits:
        return drafts
    chosen = exits[0]
    _leave_combat(database, user["id"], "room", room["id"])
    _set_player_location(database, user["id"], "traditional", "world_room", chosen["to_room_id"])
    return drafts + [EventDraft(f"You flee {chosen['direction']}.", event_type="movement")] + \
        _entry_attack(database, user)


def _combat_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The combat command does not accept arguments.")
    state = combat_state(database, user["id"])
    if not state["enemies"]:
        body = "There are no hostile enemies here."
    else:
        body = "Nearby enemies:\n" + "\n".join(
            f"- {enemy['name']}: {enemy['status']} · "
            f"{enemy['health']}/{enemy['maximum_health']} health"
            for enemy in state["enemies"]
        )
    return [EventDraft(body, event_type="combat")]


def _look_action(database, user, parameters):
    arguments = parameters.get("arguments", [])
    target = parameters.get("target", "")
    if arguments:
        target = " ".join(str(item) for item in arguments).strip()
    room = _current_room(database, user["id"])
    description = examinations.resolve(database, user, room, str(target).strip())
    return [EventDraft(description, event_type="room")]


def _item_target(parameters):
    return str(parameters.get("item_id") or " ".join(str(x) for x in parameters.get("arguments", []))).strip()


def _get_action(database, user, parameters):
    target = _item_target(parameters)
    if not target:
        raise ActionError("Get what?")
    room = _current_room(database, user["id"])
    if _active_hostiles(database, room):
        raise ActionError("A hostile enemy blocks the loot. Fight or flee first.", 409)
    container_id = str(parameters.get("container_id", "")).strip()
    if not container_id and " from " in target.casefold():
        target, container_name = re.split(r"\s+from\s+", target, maxsplit=1, flags=re.I)
        container_id = _item_matches(_room_item_rows(database, room["id"]), container_name)["id"]
    container = _visible_container(database, user["id"], room, container_id) if container_id else None
    source = (_container_rows(database, container["id"], user["id"]) if container else
              _room_item_rows(database, room["id"], room.get("instance_id")))
    if container and not container["is_open"]:
        raise ActionError("Open that container first.")
    item = _item_matches(source, target)
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
    return [EventDraft(f"You pick up {item['name']}.", event_type="item")] + \
        _set_objective_progress(database, user, "possess_item", item["definition_id"])


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
    return [EventDraft(f"You drop {item['name']}.", event_type="item")]


def _open_action(database, user, parameters):
    target = _item_target(parameters)
    room = _current_room(database, user["id"])
    item = _visible_container(database, user["id"], room, target)
    if item["is_open"]:
        return [EventDraft(f"{item['name']} is already open.\n" + _describe_item(database, item, user["id"]), event_type="item")]
    database.execute("UPDATE mud_item_instances SET is_open=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item["id"],))
    refreshed = _item_row(database, item["id"])
    return [EventDraft(f"You open {item['name']}.\n" + _describe_item(database, refreshed, user["id"]), event_type="item")]


def _put_action(database, user, parameters):
    args = parameters.get("arguments", [])
    target = str(parameters.get("item_id") or (args[0] if args else "")).strip()
    container_id = str(parameters.get("container_id", "")).strip()
    if not container_id and " in " in target.casefold():
        target, container_name = re.split(r"\s+in(?:to)?\s+", target, maxsplit=1, flags=re.I)
        room = _current_room(database, user["id"])
        container_id = _item_matches(_room_item_rows(database, room["id"]), container_name)["id"]
    if not target or not container_id:
        raise ActionError("Put an inventory item into an available container.")
    room = _current_room(database, user["id"])
    container = _visible_container(database, user["id"], room, container_id)
    if not container["is_open"]:
        raise ActionError("Open that container first.")
    item = _item_matches(_inventory_rows(database, user["id"]), target)
    if item["acquired_run_id"]:
        raise ActionError("Run loot cannot be put in a container before extraction.")
    if item["id"] == container["id"]:
        raise ActionError("An item cannot contain itself.")
    database.execute("DELETE FROM mud_character_equipment WHERE item_instance_id=?", (item["id"],))
    database.execute("UPDATE mud_item_instances SET room_id=NULL,owner_character_id=NULL,container_id=?,equipped_slot=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (container["id"], item["id"]))
    return [EventDraft(f"You put {item['name']} in {container['name']}.", event_type="item")]


def _equip_action(database, user, parameters):
    if _active_hostiles(database, _current_room(database, user["id"])):
        raise ActionError("You cannot change equipment while an enemy is attacking.")
    target = _item_target(parameters)
    item = _item_matches(_inventory_rows(database, user["id"]), target)
    if item["item_type"] != "equipment" or not item["definition_slot"]:
        raise ActionError("That item cannot be equipped.")
    character = _character_row(database, user["id"])
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
    return [EventDraft(f"You equip {item['name']} ({slot.replace('_', ' ')}).", event_type="item")]


def _unequip_action(database, user, parameters):
    if _active_hostiles(database, _current_room(database, user["id"])):
        raise ActionError("You cannot change equipment while an enemy is attacking.")
    target = _item_target(parameters)
    item = _item_matches(_inventory_rows(database, user["id"], True), target)
    character = _character_row(database, user["id"])
    loadout = _inventory_context(database, user["id"])["loadout"]
    database.execute(
        "DELETE FROM mud_character_equipment WHERE character_id=? AND loadout_key=? "
        "AND item_instance_id=?", (character["id"], loadout, item["id"]),
    )
    return [EventDraft(f"You unequip {item['name']}.", event_type="item")]


def _use_action(database, user, parameters):
    target = _item_target(parameters)
    item = _item_matches(list(_inventory_rows(database, user["id"])) + list(_inventory_rows(database, user["id"], True)), target)
    properties = _json_properties(item["properties_json"])
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


def _available_dialogue_responses(database, character_id, npc_id, node_id,
                                  dungeon=False):
    node, responses = _dialogue_node(database, node_id)
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
    visible = [response for response in responses if not response["required_state_key"]
               or str(state.get(response["required_state_key"], "")) == response["required_state_value"]]
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


def _talk_action(database, user, parameters):
    target = str(parameters.get("npc_id") or " ".join(str(x) for x in parameters.get("arguments", []))).strip()
    if not target:
        raise ActionError("Talk to whom?")
    room = _current_room(database, user["id"])
    npc = _npc_match(_room_npc_rows(database, room["id"], room.get("instance_id")), target)
    if not npc["conversation_id"]:
        raise ActionError(f"{npc['name']} has nothing to say right now.")
    node = database.execute(
        "SELECT id FROM mud_dialogue_nodes WHERE conversation_id=? AND is_start=1",
        (npc["conversation_id"],),
    ).fetchone()
    if node is None:
        raise ActionError(f"{npc['name']} has nothing to say right now.")
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
    events = [EventDraft(_dialogue_text(npc["name"], node, responses), event_type="dialogue")]
    events += _set_objective_progress(database, user, "talk_npc", npc["definition_id"])
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
    if chosen["state_key"]:
        state_table = ("mud_dungeon_npc_player_states" if dungeon_session
                       else "mud_npc_player_states")
        identity_column = "content_id" if dungeon_session else "npc_instance_id"
        state_row = database.execute(
            f"SELECT state_json FROM {state_table} WHERE character_id=? AND {identity_column}=?",
            (character["id"], session_row["npc_id"]),
        ).fetchone()
        try:
            state = json.loads(state_row["state_json"]) if state_row else {}
        except json.JSONDecodeError:
            state = {}
        state[chosen["state_key"]] = chosen["state_value"]
        database.execute(
            f"INSERT INTO {state_table}(character_id,{identity_column},state_json) VALUES(?,?,?) "
            f"ON CONFLICT(character_id,{identity_column}) DO UPDATE SET state_json=excluded.state_json,"
            "updated_at=CURRENT_TIMESTAMP",
            (character["id"], session_row["npc_id"], json.dumps(state, separators=(",", ":"), sort_keys=True)),
        )
    if chosen["accept_quest_id"]:
        existing_quest = database.execute(
            "SELECT 1 FROM mud_player_quests WHERE character_id=? AND quest_id=?",
            (character["id"], chosen["accept_quest_id"]),
        ).fetchone()
        if existing_quest is None:
            events += _accept_quest(database, user, chosen["accept_quest_id"])
            events += _set_objective_progress(database, user, "talk_npc", session_row["definition_id"])
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


def _quests_action(database, user, parameters):
    args = [str(value) for value in parameters.get("arguments", [])]
    if args and args[0].casefold() == "accept":
        target = " ".join(args[1:]).strip()
        room = _current_room(database, user["id"])
        available = _available_quests(database, user, room["id"])
        matches = [quest for quest in available if quest["id"] == target or quest["name"].casefold() == target.casefold()]
        if len(matches) != 1:
            raise ActionError("That quest is not currently available from a nearby NPC.")
        return _accept_quest(database, user, matches[0]["id"])
    if args:
        raise ActionError("Use /quests or /quest accept <name>.")
    state = quest_state(database, user["id"])
    if not state["active"]:
        body = "You have no active quests."
    else:
        lines = ["Active quests:"]
        for quest in state["active"]:
            lines.append(f"\n{quest['name']} — {quest['description']}")
            for objective in quest["objectives"]:
                mark = "✓" if objective["completed_at"] else "·"
                lines.append(f"{mark} {objective['description']} ({objective['progress']}/{objective['required_count']})")
        body = "\n".join(lines)
    return [EventDraft(body, event_type="quest")]


def _exits_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The exits command does not accept arguments.")
    room = _current_room(database, user["id"])
    exits = _room_state(database, user["id"])["exits"]
    if not exits:
        body = f"There are no visible exits from {room['name']}."
    else:
        body = "Available exits:\n" + "\n".join(
            f"- {_exit_label(exit_row)}" for exit_row in exits
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
    ]


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


def _world_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The world command does not accept arguments.")
    modes = _mode_settings(database)
    room = _current_room(database, user["id"])
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
        database, user["id"], "procedural", "dungeon_instance", room_key, instance_id
    )
    _record_expedition_visit(database, instance_id, user["id"], room_key)
    return _entry_attack(database, user)


def _installed_expedition_themes(database, strict=False):
    try:
        discovered = dungeons.discover_templates()
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


def _expedition_status_text(database, user_id):
    run = _active_expedition(database, user_id)
    if run is None:
        return "No active expedition."
    total = run["room_count"] or 0
    active = database.execute(
        "SELECT COUNT(*) AS amount FROM mud_expedition_participants "
        "WHERE instance_id=? AND status='active'", (run["id"],),
    ).fetchone()["amount"]
    daily_day = _daily_run_day(database, run["id"])
    label = f"Dungeon of the Day {daily_day}" if daily_day else "Expedition"
    return (f"{label} {run['join_code']} · {run['theme_id']} · difficulty "
            f"{run['difficulty']} · {run['rooms_visited']}/{total} rooms visited · "
            f"{active} active explorer{'s' if active != 1 else ''}. "
            "Reach the exit for the full reward, return to the entrance with /retreat "
            "to bank loot, or /abandon to lose this run's loot.")


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
            lines.append(f"- {key}: {template['name']} (difficulty {rule['minimum']}-{rule['maximum']})")
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
    code = _new_join_code(database)
    database.execute(
        "UPDATE mud_procedural_instances SET status='active',owner_user_id=?,join_code=?,"
        "started_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
        (user["id"], code, instance_id),
    )
    character = _character_row(database, user["id"])
    database.execute(
        "INSERT INTO mud_expedition_participants(instance_id,user_id,character_id,current_room_key) "
        "VALUES(?,?,?,?)", (instance_id, user["id"], character["id"], manifest["entrance"]),
    )
    entry = _enter_expedition(database, user, instance_id, manifest["entrance"])
    return [EventDraft(
        f"Expedition launched. Cooperative join code: {code}.\n\n"
        f"{_describe_room(database, _current_room(database, user['id']), user['id'])}",
        event_type="expedition",
    )] + entry


def _join_expedition_action(database, user, parameters):
    if _current_room(database, user["id"])["id"] != PORTAL_ROOM_ID:
        raise ActionError("Expeditions can only be joined from the Dungeon Portals.")
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
    if run is None:
        raise ActionError("That expedition is not active or the code is invalid.", 404)
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
    if _current_room(database, user["id"])["id"] != PORTAL_ROOM_ID:
        raise ActionError("You are already inside an expedition.")
    run = _active_expedition(database, user["id"])
    if run is None:
        raise ActionError("You have no active expedition to resume.")
    _enter_expedition(database, user, run["id"], run["current_room_key"])
    return [EventDraft(
        f"You resume {'Dungeon of the Day run' if _daily_run_day(database, run['id']) else 'expedition'} {run['join_code']}.\n\n"
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
    _set_player_location(database, user["id"], "procedural", "portal", PORTAL_ROOM_ID)
    _finish_empty_expedition(database, run["id"], "abandoned")
    return [EventDraft(
        f"You abandon {'Dungeon of the Day run' if _daily_run_day(database, run['id']) else 'expedition'} {run['join_code']}. This run's loot is lost; previously owned items and earned experience remain.",
        event_type="expedition",
    )]


def _retreat_run(database, user, evaded=False):
    run = _active_expedition(database, user["id"])
    room = _current_room(database, user["id"])
    if run is None or room.get("instance_id") != run["id"] or room.get("role") != "entrance":
        raise ActionError("Return to the dungeon entrance to extract with your loot.")
    if not evaded and _active_hostiles(database, room):
        raise ActionError("A hostile enemy blocks the entrance. Fight or flee.", 409)
    database.execute(
        "UPDATE mud_expedition_participants SET status='abandoned',finished_at=CURRENT_TIMESTAMP "
        "WHERE instance_id=? AND user_id=? AND status='active'", (run["id"], user["id"]),
    )
    _record_daily_result(database, run["id"], user["id"], "abandoned", run["rooms_visited"])
    _settle_run_loot(database, user["id"], run["id"], keep=True)
    _leave_combat(database, user["id"], "instance", run["id"])
    _set_player_location(database, user["id"], "procedural", "portal", PORTAL_ROOM_ID)
    _finish_empty_expedition(database, run["id"], "extracted")
    return [EventDraft("You return through the entrance with your loot. The completion reward is forfeited.",
                       event_type="expedition")]


def _retreat_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The retreat command does not accept arguments.")
    return _retreat_run(database, user)


def _complete_expedition_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The complete command does not accept arguments.")
    run = _active_expedition(database, user["id"])
    room = _current_room(database, user["id"])
    if run is None or room.get("instance_id") != run["id"] or room.get("role") != "exit":
        raise ActionError("The expedition can only be completed from its exit room.")
    if _active_hostiles(database, room):
        raise ActionError("Defeat or escape the enemy guarding the exit first.", 409)
    active = database.execute(
        "SELECT p.user_id,p.character_id,p.rooms_visited,c.display_name "
        "FROM mud_expedition_participants AS p JOIN mud_characters AS c "
        "ON c.id=p.character_id WHERE p.instance_id=? AND p.status='active' "
        "ORDER BY p.joined_at,p.user_id", (run["id"],),
    ).fetchall()
    recipients = tuple(item["user_id"] for item in active)
    daily_day = _daily_run_day(database, run["id"])
    rewarded = 0
    coin_rewards = []
    for participant in active:
        reward = max(1, run["difficulty"] * 10 + participant["rooms_visited"] * 2)
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
            award_experience(database, participant["user_id"], reward)
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
        _set_player_location(database, participant["user_id"], "procedural", "portal", PORTAL_ROOM_ID)
    database.execute(
        "UPDATE mud_procedural_instances SET status='completed',terminal_reason='success',"
        "ended_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='active'",
        (run["id"],),
    )
    reward_text = (
        "Each active explorer receives a completion experience reward based on difficulty and rooms personally visited."
        if daily_day is None else
        f"First-completion rewards were granted to {rewarded} eligible explorer{'s' if rewarded != 1 else ''}; repeat completions are recorded without another reward."
    )
    return [EventDraft(
        f"{'Dungeon of the Day run' if daily_day else 'Expedition'} {run['join_code']} is complete. "
        f"Collected loot is safe, and each explorer earns coins based on difficulty and rooms visited. {reward_text}",
        event_type="reward", audience_type="instance", audience_key=run["id"],
        recipient_ids=recipients,
    )]


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
    if _active_hostiles(database, room):
        raise ActionError("A hostile enemy blocks your route. Fight or attempt to flee.", 409)
    if exit_id:
        matches = [item for item in _room_exits(database, room["id"], instance_id)
                   if item["id"] == exit_id]
    elif destination:
        key = " ".join(destination.casefold().split())
        matches = [
            item for item in _room_exits(database, room["id"], instance_id)
            if key in {
                " ".join(item["name"].casefold().split()),
                " ".join((item["direction"] or "").casefold().split()),
                " ".join(item["destination_name"].casefold().split()),
            }
        ]
    else:
        raise ActionError("Go where? Use /exits to list available routes.")
    if not matches:
        raise ActionError(f'There is no exit matching "{destination or exit_id}" from here.')
    if len(matches) > 1:
        raise ActionError(f'More than one exit matches "{destination}". Use an exit name or direction.')
    chosen = matches[0]
    old_recipients = _occupant_ids(database, room["id"], user["id"], instance_id)
    _leave_combat(database, user["id"], "instance" if instance_id else "room",
                  instance_id or room["id"])
    if instance_id:
        _set_player_location(database, user["id"], "procedural", "dungeon_instance",
                             chosen["to_room_id"], instance_id)
        _record_expedition_visit(database, instance_id, user["id"], chosen["to_room_id"])
        destination_room = _dungeon_room(database, instance_id, chosen["to_room_id"])
    else:
        _set_player_location(database, user["id"], "traditional", "world_room", chosen["to_room_id"])
        destination_room = database.execute(
            "SELECT id,name,description FROM mud_world_rooms WHERE id=?",
            (chosen["to_room_id"],),
        ).fetchone()
    route = chosen["direction"] or chosen["name"]
    new_recipients = _occupant_ids(database, destination_room["id"], user["id"], instance_id)
    audience_type = "instance" if instance_id else "room"
    audience_key = instance_id or room["id"]
    events = [
        EventDraft(
            f"You go {route}.",
            event_type="movement",
        ),
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
    return events + _entry_attack(database, user)


def _say_action(database, user, parameters):
    body = str(parameters.get("message", "")).strip()
    if not 1 <= len(body) <= 1000:
        raise ActionError("Messages must be between 1 and 1000 characters.")
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
    return [EventDraft(
        f"{character['name']}: {body}", event_type="chat",
        audience_type="instance" if instance_id else "room",
        audience_key=instance_id or room["id"],
        recipient_ids=tuple(sorted(recipients)),
    )]


actions = ActionRegistry()
actions.register(ActionDefinition(
    key="status", label="SYSTEM STATUS",
    description="Check the current MUD service state.",
    commands=("status",), handler=_status_action,
))
actions.register(ActionDefinition(
    key="character", label="VIEW STATS",
    description="View your character identity, health, attributes, and progression.",
    commands=("character", "stats"), handler=_character_action,
    group="CHARACTER",
))
actions.register(ActionDefinition(
    key="say", label="SAY",
    description="Speak to the other players in this room.",
    handler=_say_action, show_context=False,
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
actions.register(ActionDefinition(key="get", label="PICK UP", description="Pick up an item in this room or an open container.", commands=("get", "take"), handler=_get_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="drop", label="DROP", description="Drop a carried item in this room.", commands=("drop",), handler=_drop_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="open", label="OPEN", description="Open an available container.", commands=("open",), handler=_open_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="put", label="PUT AWAY", description="Put a carried item in an open container.", commands=("put",), handler=_put_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="equip", label="EQUIP", description="Equip a carried item.", commands=("equip", "wear"), handler=_equip_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="unequip", label="UNEQUIP", description="Remove equipped gear.", commands=("unequip", "remove"), handler=_unequip_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="use", label="USE", description="Use a carried item.", commands=("use",), handler=_use_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="shop", label="PORTAL TRADER", description="Buy supplies or sell loot in the portal room.", commands=("shop",), handler=_shop_action, group="HUB", show_context=False))
actions.register(ActionDefinition(key="storage", label="PERSONAL STORAGE", description="Deposit or withdraw personal items in the portal room.", commands=("storage", "stash"), handler=_storage_action, group="HUB", show_context=False))
actions.register(ActionDefinition(key="talk", label="TALK", description="Talk to a nearby NPC.", commands=("talk", "speak"), handler=_talk_action, group="NPCS", show_context=False))
actions.register(ActionDefinition(key="respond", label="RESPOND", description="Choose a response in your current NPC conversation.", commands=("respond", "reply"), handler=_respond_action, group="DIALOGUE", show_context=False))
actions.register(ActionDefinition(key="quests", label="VIEW QUESTS", description="View your active quest objectives and progress.", commands=("quests", "quest", "objectives"), handler=_quests_action, group="QUESTS"))
actions.register(ActionDefinition(key="combat", label="COMBAT STATUS", description="View hostile enemies and encounter status.", commands=("combat",), handler=_combat_action, group="COMBAT"))
actions.register(ActionDefinition(key="attack", label="ATTACK", description="Attack a hostile enemy and resolve one combat round.", commands=("attack", "hit"), handler=_attack_action, group="COMBAT", show_context=False))
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
actions.register(ActionDefinition(
    key="portal", label="ENTER DUNGEON PORTALS",
    description="Enter the protected procedural gameplay hub.",
    commands=("portal", "portals"), handler=_portal_action,
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
    commands=("expedition",), handler=_expedition_action,
    group="EXPEDITIONS", show_context=False,
))
actions.register(ActionDefinition(
    key="join_expedition", label="JOIN EXPEDITION",
    description="Join an active cooperative expedition by code.",
    commands=("join",), handler=_join_expedition_action,
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
    key="retreat_expedition", label="EXTRACT AT ENTRANCE",
    description="Return alive with loot from the dungeon entrance, forfeiting completion experience.",
    commands=("retreat", "extract"), handler=_retreat_action,
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


def _navigation_actions(database, user):
    room = _current_room(database, user["id"])
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    options = [
        {
            "key": "go",
            "label": _exit_label(exit_row),
            "description": f"Travel to {exit_row['destination_name']}.",
            "group": "NAVIGATION",
            "parameters": {"exit_id": exit_row["id"]},
        }
        for exit_row in _room_exits(database, room["id"], instance_id)
    ]
    modes = _mode_settings(database)
    if room["id"] == PORTAL_ROOM_ID:
        options.extend((
            {"key": "shop", "label": "PORTAL TRADER", "description": "Buy supplies and sell loot.", "group": "HUB", "parameters": {}},
            {"key": "storage", "label": "PERSONAL STORAGE", "description": "Store equipment and items between runs.", "group": "HUB", "parameters": {}},
        ))
        daily_description = _daily_status_text(database, user["id"])
        options.append(
            {"key": "dungeon_entrance", "label": "DUNGEON OF THE DAY",
             "description": daily_description, "group": "DAILY DUNGEON",
             "parameters": {"kind": "daily"}}
        )
        active = _active_expedition(database, user["id"])
        if active:
            options.append({"key": "resume_expedition", "label": "RESUME EXPEDITION",
                            "description": _expedition_status_text(database, user["id"]),
                            "group": "EXPEDITIONS", "parameters": {}})
        else:
            daily = ensure_daily_dungeon(database)
            if daily["status"] == "ready":
                options.append({
                    "key": "daily", "label": "ENTER TODAY'S DUNGEON",
                    "description": "Start a new isolated daily attempt; party members may join by code.",
                    "group": "DAILY DUNGEON", "parameters": {"operation": "enter"},
                })
            for theme_id, _path, template in _installed_expedition_themes(database):
                difficulty = template["generation"]["difficulty"]["default"]
                options.append({
                    "key": "expedition", "label": f"LAUNCH {template['name'].upper()}",
                    "description": f"Start at default difficulty {difficulty}.",
                    "group": "EXPEDITIONS",
                    "parameters": {"theme_id": theme_id, "difficulty": difficulty},
                })
            options.append({"key": "join_expedition", "label": "JOIN BY CODE",
                            "description": "Use /join <code> to join a cooperative run.",
                            "group": "EXPEDITIONS", "parameters": {}})
        if modes["traditional"]:
            options.append({"key": "world", "label": "OUT — TRADITIONAL WORLD",
                            "description": "Return to your last traditional-world location.",
                            "group": "NAVIGATION", "parameters": {}})
    elif modes["procedural"] and room["id"] == _start_room(database)["id"]:
        options.append({"key": "portal", "label": "ENTER DUNGEON PORTALS",
                        "description": "Enter the protected procedural gameplay hub.",
                        "group": "NAVIGATION", "parameters": {}})
    if instance_id:
        options.append({"key": "expedition_status", "label": "RUN STATUS",
                        "description": _expedition_status_text(database, user["id"]),
                        "group": "EXPEDITIONS", "parameters": {}})
        options.append({"key": "abandon_expedition", "label": "ABANDON RUN",
                        "description": "Leave now and lose all loot acquired during this run.",
                        "group": "EXPEDITIONS", "parameters": {}})
        if room.get("role") == "entrance":
            options.append({"key": "retreat_expedition", "label": "EXTRACT WITH LOOT",
                            "description": "Return safely with loot; forfeit the completion reward.",
                            "group": "EXPEDITIONS", "parameters": {}})
        if room.get("role") == "exit":
            options.append({"key": "complete_expedition", "label": "COMPLETE RUN",
                            "description": "Complete the run for all active explorers.",
                            "group": "EXPEDITIONS", "parameters": {}})
    return options


actions.register_context_provider(_navigation_actions)


def _item_actions(database, user):
    room = _current_room(database, user["id"])
    instance_id = room.get("instance_id") if isinstance(room, dict) else None
    options = []
    for item in _room_item_rows(database, room["id"], instance_id):
        base = {"item_id": item["id"]}
        options.extend((
            {"key": "look", "label": f"EXAMINE {item['name'].upper()}", "description": item["description"], "group": "ITEMS", "parameters": {"target": item["id"]}},
            {"key": "get", "label": f"PICK UP {item['name'].upper()}", "description": "Pick it up.", "group": "ITEMS", "parameters": base},
        ))
        if _is_container(item) and not instance_id: options.append({"key": "open", "label": f"OPEN {item['name'].upper()}", "description": "Open and inspect it.", "group": "ITEMS", "parameters": base})
        if _is_container(item) and not instance_id and item["is_open"]:
            for child in _container_rows(database, item["id"], user["id"]):
                options.append({"key": "get", "label": f"TAKE {child['name'].upper()} FROM {item['name'].upper()}", "description": "Take from the open container.", "group": "ITEMS", "parameters": {"item_id": child["id"], "container_id": item["id"]}})
    for item in _inventory_rows(database, user["id"]):
        base = {"item_id": item["id"]}
        options.extend((
            {"key": "look", "label": f"EXAMINE {item['name'].upper()}", "description": item["description"], "group": "INVENTORY", "parameters": {"target": item["id"]}},
            *([] if instance_id or room["id"] == PORTAL_ROOM_ID else [{"key": "drop", "label": f"DROP {item['name'].upper()}", "description": "Leave it here.", "group": "INVENTORY", "parameters": base}]),
            {"key": "use", "label": f"USE {item['name'].upper()}", "description": "Use it if it has an effect.", "group": "INVENTORY", "parameters": base},
        ))
        if item["item_type"] == "equipment" and item["definition_slot"]: options.append({"key": "equip", "label": f"EQUIP {item['name'].upper()}", "description": "Wear or wield it.", "group": "INVENTORY", "parameters": base})
    for item in _inventory_rows(database, user["id"], True):
        options.append({"key": "unequip", "label": f"UNEQUIP {item['name'].upper()}", "description": "Return it to inventory.", "group": "EQUIPMENT", "parameters": {"item_id": item["id"]}})
    return options


actions.register_context_provider(_item_actions)


def _available_quests(database, user, room_id):
    character = _character_row(database, user["id"])
    return database.execute(
        "SELECT DISTINCT q.id,q.name,q.description FROM mud_quests AS q "
        "JOIN mud_quest_givers AS g ON g.quest_id=q.id "
        "JOIN mud_npc_instances AS i ON i.definition_id=g.npc_definition_id "
        "LEFT JOIN mud_player_quests AS p ON p.quest_id=q.id AND p.character_id=? "
        "LEFT JOIN mud_player_quests AS prerequisite ON prerequisite.quest_id=q.prerequisite_quest_id "
        "AND prerequisite.character_id=? AND prerequisite.status='completed' AND prerequisite.rewarded_at IS NOT NULL "
        "WHERE i.room_id=? AND p.quest_id IS NULL "
        "AND (q.prerequisite_quest_id IS NULL OR prerequisite.quest_id IS NOT NULL) "
        "ORDER BY q.name COLLATE NOCASE,q.id",
        (character["id"], character["id"], room_id),
    ).fetchall()


def _npc_actions(database, user):
    room = _current_room(database, user["id"])
    options = []
    for npc in _room_npc_rows(database, room["id"], room.get("instance_id")):
        options.append({"key": "look", "label": f"EXAMINE {npc['name'].upper()}",
                        "description": npc["description"], "group": "NPCS",
                        "parameters": {"target": npc["id"]}})
        if npc["conversation_id"]:
            options.append({"key": "talk", "label": f"TALK TO {npc['name'].upper()}",
                            "description": f"Start a conversation with {npc['name']}.", "group": "NPCS",
                            "parameters": {"npc_id": npc["id"]}})
    character = _character_row(database, user["id"])
    session_row = database.execute(
        "SELECT s.node_id,i.id AS npc_id,i.room_id,d.name FROM mud_conversation_sessions AS s "
        "JOIN mud_npc_instances AS i ON i.id=s.npc_instance_id "
        "JOIN mud_npc_definitions AS d ON d.id=i.definition_id WHERE s.character_id=?",
        (character["id"],),
    ).fetchone()
    dungeon_session = False
    if room.get("instance_id"):
        raw = database.execute(
            "SELECT s.node_id,s.content_id AS npc_id,c.room_key,c.snapshot_json "
            "FROM mud_dungeon_conversation_sessions AS s JOIN mud_dungeon_content AS c "
            "ON c.id=s.content_id WHERE s.character_id=? AND s.instance_id=?",
            (character["id"], room["instance_id"]),
        ).fetchone()
        if raw is not None:
            snapshot = _json_properties(raw["snapshot_json"])
            session_row = dict(raw)
            session_row["room_id"] = session_row["room_key"]
            session_row["name"] = snapshot.get("name", "NPC")
            dungeon_session = True
    if session_row and session_row["room_id"] == room["id"]:
        _node, responses = _available_dialogue_responses(
            database, character["id"], session_row["npc_id"], session_row["node_id"],
            dungeon_session,
        )
        for response in responses:
            options.append({"key": "respond", "label": response["response_text"],
                            "description": f"Reply to {session_row['name']}.", "group": "DIALOGUE",
                            "parameters": {"response_id": response["id"]}})
    for quest in _available_quests(database, user, room["id"]):
        options.append({"key": "quests", "label": f"ACCEPT: {quest['name'].upper()}",
                        "description": quest["description"], "group": "QUESTS",
                        "parameters": {"arguments": ["accept", quest["id"]]}})
    return options


actions.register_context_provider(_npc_actions)


def _combat_actions(database, user):
    room = _current_room(database, user["id"])
    options = []
    for npc in _room_npc_rows(database, room["id"], room.get("instance_id")):
        if npc["combat_maximum_health"] is None:
            continue
        encounter = _encounter_row(database, npc)
        if encounter["status"] in {"ready", "active"}:
            options.append({
                "key": "attack", "label": f"ATTACK {npc['name'].upper()}",
                "description": (f"Fight one round. Enemy health: "
                                f"{encounter['current_health']}/{encounter['maximum_health']}."),
                "group": "COMBAT", "parameters": {"npc_id": npc["id"]},
            })
    if _active_hostiles(database, room):
        options.extend((
            {"key": "defend", "label": "DEFEND", "description": "Reduce damage this turn.", "group": "COMBAT", "parameters": {}},
            {"key": "flee", "label": "FLEE", "description": "Chance to escape; failure costs a turn.", "group": "COMBAT", "parameters": {}},
        ))
    return options


actions.register_context_provider(_combat_actions)


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


class MudService:
    """Normalizes, authorizes, executes, and records MUD actions atomically."""

    def __init__(self, registry):
        self.registry = registry

    def normalize(self, form):
        command = form.get("command", "").strip()
        action_key = form.get("action", "").strip()
        message = form.get("message", "").strip()
        if sum(bool(value) for value in (command, action_key, message)) != 1:
            raise ActionError("Submit exactly one message, command, or contextual action.")
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
        else:
            definition = self.registry.get(action_key)
            if definition is None:
                raise ActionError("That action is not available.")
            method = "context"
            raw_parameters = form.get("parameters", "{}").strip() or "{}"
            if len(raw_parameters) > 10000:
                raise ActionError("Action parameters are too large.")
            try:
                parameters = json.loads(raw_parameters)
            except json.JSONDecodeError as problem:
                raise ActionError("Action parameters are invalid.") from problem
            if not isinstance(parameters, dict):
                raise ActionError("Action parameters must be an object.")
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

            drafts = action_request.definition.handler(
                database, user, action_request.parameters
            )
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
    database.execute("BEGIN IMMEDIATE")
    _prune_presence(database)
    room = _room_state(database, g.user["id"])
    character = character_state(database, g.user["id"])
    inventory = inventory_state(database, g.user["id"])
    hub = hub_state(database, g.user["id"])
    quests = quest_state(database, g.user["id"])
    combat = combat_state(database, g.user["id"])
    mud_actions = actions.contextual(g.user, database)
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
        mud_actions=mud_actions, room=room, character=character, inventory=inventory,
        quests=quests, combat=combat, hub=hub,
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
        available = actions.contextual(g.user, database)
        database.commit()
    except (ActionError, sqlite3.Error) as problem:
        database.rollback()
        if isinstance(problem, ActionError):
            return jsonify(error=str(problem)), problem.status
        raise
    return jsonify(events=events, room=room, character=character, inventory=inventory,
                   quests=quests, combat=combat, hub=hub, actions=available)


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
        return redirect(url_for("mud.index"))
    config = character_creation.options()
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
    return render_template("mud/create.html", config=config, character=row,
                           selected=selected, error=error)


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
        available = actions.contextual(g.user, database)
        database.commit()
        return jsonify(
            error=str(problem), room=room, character=character, inventory=inventory,
            quests=quests, combat=combat, hub=hub, actions=available
        ), problem.status
    database = get_db()
    room = _room_state(database, g.user["id"])
    character = character_state(database, g.user["id"])
    inventory = inventory_state(database, g.user["id"])
    hub = hub_state(database, g.user["id"])
    quests = quest_state(database, g.user["id"])
    combat = combat_state(database, g.user["id"])
    available = actions.contextual(g.user, database)
    database.commit()
    return jsonify(
        action=action_key, events=events,
        actions=available, room=room, character=character, inventory=inventory,
        quests=quests, combat=combat, hub=hub,
    )


def _builder_redirect(room_id=None):
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
    duplicate = database.execute(
        "SELECT id FROM mud_world_exits WHERE from_room_id=? AND id<>COALESCE(?, '') "
        "AND (name=? COLLATE NOCASE OR (? IS NOT NULL AND direction=? COLLATE NOCASE))",
        (from_room_id, exit_id, name, direction, direction),
    ).fetchone()
    if duplicate:
        return None, "That source room already has an exit with this name or direction."
    return (from_room_id, to_room_id, name, direction), None


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
        "WHERE r.world_id=(SELECT id FROM mud_world_packages WHERE active=1) "
        "GROUP BY r.id ORDER BY r.is_start DESC,r.name COLLATE NOCASE,r.id"
    ).fetchall()
    rooms = [dict(row) for row in room_rows]
    exits_by_room = {room["id"]: [] for room in rooms}
    for row in database.execute(
        "SELECT e.*,destination.name AS destination_name "
        "FROM mud_world_exits AS e "
        "JOIN mud_world_rooms AS destination ON destination.id=e.to_room_id "
        "ORDER BY e.from_room_id,e.direction IS NULL,e.direction,e.name COLLATE NOCASE"
    ):
        exits_by_room.setdefault(row["from_room_id"], []).append(dict(row))
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
        item["use_message"] = properties.get("use_message", "")
        definitions.append(item)
    items_by_room = {room["id"]: [] for room in rooms}
    for row in database.execute("SELECT i.id,i.room_id,d.name,d.item_type FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id WHERE i.room_id IS NOT NULL ORDER BY d.name COLLATE NOCASE"):
        items_by_room.setdefault(row["room_id"], []).append(dict(row))
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
        "p.defense AS combat_defense,p.experience_reward,p.respawn_seconds "
        "FROM mud_npc_definitions AS d LEFT JOIN mud_npc_combat_profiles AS p "
        "ON p.npc_definition_id=d.id WHERE d.world_id=(SELECT id FROM mud_world_packages WHERE active=1) AND NOT EXISTS "
        "(SELECT 1 FROM mud_dungeon_managed_definitions AS m WHERE m.definition_id=d.id) "
        "ORDER BY d.name COLLATE NOCASE,d.id"
    )]
    npc_equipment = {item["id"]: {} for item in npc_definitions}
    npc_combat_rewards = {item["id"]: [] for item in npc_definitions}
    for row in database.execute("SELECT * FROM mud_npc_equipment"):
        npc_equipment.setdefault(row["npc_definition_id"], {})[row["equipment_slot"]] = row["item_definition_id"]
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
    return render_template(
        "mud/builder.html", rooms=rooms, exits_by_room=exits_by_room,
        users=users, directions=DIRECTIONS, definitions=definitions, items_by_room=items_by_room,
        conversations=conversations, nodes_by_conversation=nodes_by_conversation,
        responses_by_node=responses_by_node, npc_definitions=npc_definitions,
        npcs_by_room=npcs_by_room, quests=quests, objectives_by_quest=objectives_by_quest,
        rewards_by_quest=rewards_by_quest, givers_by_quest=givers_by_quest,
        npc_equipment=npc_equipment, npc_combat_rewards=npc_combat_rewards,
    )


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
    properties = {}
    if use_message: properties["use_message"] = use_message
    if attack_bonus: properties["attack_bonus"] = attack_bonus
    if defense_bonus: properties["defense_bonus"] = defense_bonus
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


@bp.post("/builder/dialogue-nodes/<path:node_id>/responses")
@builder_required
def builder_create_dialogue_response(node_id):
    validate_csrf(); database = get_db(); node = database.execute("SELECT conversation_id FROM mud_dialogue_nodes WHERE id=?", (node_id,)).fetchone()
    if node is None: abort(404)
    if _managed_dungeon_conversation(database, node["conversation_id"]): abort(404)
    response_text = " ".join(request.form.get("response_text", "").split()); next_node_id = request.form.get("next_node_id", "").strip() or None; quest_id = request.form.get("accept_quest_id", "").strip() or None
    required_state_key = " ".join(request.form.get("required_state_key", "").split()) or None; required_state_value = request.form.get("required_state_value", "").strip() or None
    state_key = " ".join(request.form.get("state_key", "").split()) or None; state_value = request.form.get("state_value", "").strip() or None
    sort_order = request.form.get("sort_order", 0, type=int)
    if not 1 <= len(response_text) <= 500 or (state_key and len(state_key) > 80) or (state_value and len(state_value) > 500) or (required_state_key and len(required_state_key) > 80) or (required_state_value and len(required_state_value) > 500) or sort_order is None or not -10000 <= sort_order <= 10000: flash("Check response, state, and order fields.", "error"); return _builder_redirect()
    if bool(required_state_key) != bool(required_state_value): flash("A response state requirement needs both a key and value.", "error"); return _builder_redirect()
    if next_node_id and database.execute("SELECT 1 FROM mud_dialogue_nodes WHERE id=? AND conversation_id=?", (next_node_id, node["conversation_id"])).fetchone() is None: flash("The next node must belong to this conversation.", "error"); return _builder_redirect()
    if quest_id and database.execute("SELECT 1 FROM mud_quests WHERE id=?", (quest_id,)).fetchone() is None: flash("Choose an existing quest.", "error"); return _builder_redirect()
    database.execute("INSERT INTO mud_dialogue_responses(id,node_id,response_text,next_node_id,accept_quest_id,required_state_key,required_state_value,state_key,state_value,sort_order) VALUES(?,?,?,?,?,?,?,?,?,?)", (f"world:dialogue-response:{uuid.uuid4().hex}", node_id, response_text, next_node_id, quest_id, required_state_key, required_state_value, state_key, state_value, sort_order)); database.commit()
    flash("Dialogue response added.", "success"); return _builder_redirect()


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
        "experience_reward,respawn_seconds) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(npc_definition_id) DO UPDATE SET maximum_health=excluded.maximum_health,"
        "attack=excluded.attack,defense=excluded.defense,experience_reward=excluded.experience_reward,"
        "respawn_seconds=excluded.respawn_seconds,updated_at=CURRENT_TIMESTAMP",
        (npc_id, profile["maximum_health"], profile["attack"], profile["defense"],
         profile["experience_reward"], profile["respawn_seconds"]),
    )
    database.execute("DELETE FROM mud_npc_equipment WHERE npc_definition_id=?", (npc_id,))
    database.executemany(
        "INSERT INTO mud_npc_equipment(npc_definition_id,equipment_slot,item_definition_id) "
        "VALUES(?,?,?)", ((npc_id, slot, item_id) for slot, item_id in equipment.items()),
    )


@bp.post("/builder/npcs")
@builder_required
def builder_create_npc_definition():
    validate_csrf(); database = get_db(); fields, error = _npc_definition_fields()
    if error: flash(error, "error"); return _builder_redirect()
    profile, equipment, error = _npc_combat_fields(database)
    if error: flash(error, "error"); return _builder_redirect()
    if fields[2] and database.execute("SELECT 1 FROM mud_conversations WHERE id=?", (fields[2],)).fetchone() is None: flash("Choose an existing conversation.", "error"); return _builder_redirect()
    npc_id = f"world:npc-definition:{uuid.uuid4().hex}"; database.execute("INSERT INTO mud_npc_definitions(id,name,description,conversation_id,properties_json,behaviors_json,created_by,world_id) VALUES(?,?,?,?,?,?,?,(SELECT id FROM mud_world_packages WHERE active=1))", (npc_id, *fields, g.user["id"])); _save_npc_combat(database, npc_id, profile, equipment); database.commit()
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
    database.execute("UPDATE mud_npc_definitions SET name=?,description=?,conversation_id=?,properties_json=?,behaviors_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*fields, npc_id)); _save_npc_combat(database, npc_id, profile, equipment); database.commit(); flash("NPC definition updated.", "success"); return _builder_redirect()


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
    if reward_type not in {"experience", "item"} or amount is None or not 1 <= amount <= 100000:
        flash("Choose a valid combat reward.", "error"); return _builder_redirect()
    if reward_type == "item":
        if amount > 100 or database.execute(
            "SELECT 1 FROM mud_item_definitions WHERE id=?", (target_id,),
        ).fetchone() is None:
            flash("Choose an item and a quantity up to 100.", "error"); return _builder_redirect()
    else:
        target_id = None
    database.execute(
        "INSERT INTO mud_npc_combat_rewards(id,npc_definition_id,reward_type,target_id,amount) "
        "VALUES(?,?,?,?,?)", (f"world:combat-reward:{uuid.uuid4().hex}", npc_id,
                              reward_type, target_id, amount),
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
    if prerequisite == quest_id: return None, "A quest cannot require itself."
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
    return (name, description, prerequisite), None


@bp.post("/builder/quests")
@builder_required
def builder_create_quest():
    validate_csrf(); database = get_db(); fields, error = _quest_fields(database)
    if error: flash(error, "error"); return _builder_redirect()
    quest_id = f"world:quest:{uuid.uuid4().hex}"; database.execute("INSERT INTO mud_quests(id,name,description,prerequisite_quest_id,created_by,world_id) VALUES(?,?,?,?,?,(SELECT id FROM mud_world_packages WHERE active=1))", (quest_id, *fields, g.user["id"])); database.commit(); flash(f"Quest {fields[0]} created.", "success"); return _builder_redirect()


@bp.post("/builder/quests/<path:quest_id>")
@builder_required
def builder_update_quest(quest_id):
    validate_csrf(); database = get_db()
    if database.execute("SELECT 1 FROM mud_quests WHERE id=?", (quest_id,)).fetchone() is None: abort(404)
    fields, error = _quest_fields(database, quest_id)
    if error: flash(error, "error"); return _builder_redirect()
    database.execute("UPDATE mud_quests SET name=?,description=?,prerequisite_quest_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*fields, quest_id)); database.commit(); flash("Quest updated.", "success"); return _builder_redirect()


@bp.post("/builder/quests/<path:quest_id>/givers")
@builder_required
def builder_set_quest_givers(quest_id):
    validate_csrf(); database = get_db()
    if database.execute("SELECT 1 FROM mud_quests WHERE id=?", (quest_id,)).fetchone() is None: abort(404)
    giver_ids = set(request.form.getlist("npc_definition_id")); valid = {row["id"] for row in database.execute("SELECT id FROM mud_npc_definitions")}
    if not giver_ids <= valid: flash("Choose existing NPC definitions.", "error"); return _builder_redirect()
    database.execute("DELETE FROM mud_quest_givers WHERE quest_id=?", (quest_id,)); database.executemany("INSERT INTO mud_quest_givers(quest_id,npc_definition_id) VALUES(?,?)", ((quest_id, value) for value in giver_ids)); database.commit(); flash("Quest givers updated.", "success"); return _builder_redirect()


@bp.post("/builder/quests/<path:quest_id>/objectives")
@builder_required
def builder_create_quest_objective(quest_id):
    validate_csrf(); database = get_db(); objective_type = request.form.get("objective_type", ""); target_id = request.form.get("target_id", "").strip(); description = request.form.get("description", "").strip(); required_count = request.form.get("required_count", 1, type=int)
    target_tables = {"talk_npc": "mud_npc_definitions", "visit_room": "mud_world_rooms", "possess_item": "mud_item_definitions"}
    if database.execute("SELECT 1 FROM mud_quests WHERE id=?", (quest_id,)).fetchone() is None: abort(404)
    if objective_type not in target_tables or database.execute(f"SELECT 1 FROM {target_tables.get(objective_type, 'mud_quests')} WHERE id=?", (target_id,)).fetchone() is None: flash("Choose a valid objective type and target.", "error"); return _builder_redirect()
    sort_order = request.form.get("sort_order", 0, type=int)
    if not 1 <= len(description) <= 500 or required_count is None or not 1 <= required_count <= 100 or sort_order is None or not -10000 <= sort_order <= 10000: flash("Objective descriptions must be 1–500 characters, counts 1–100, and order valid.", "error"); return _builder_redirect()
    database.execute("INSERT INTO mud_quest_objectives(id,quest_id,objective_type,target_id,description,required_count,sort_order) VALUES(?,?,?,?,?,?,?)", (f"world:quest-objective:{uuid.uuid4().hex}", quest_id, objective_type, target_id, description, required_count, sort_order)); database.commit(); flash("Quest objective added.", "success"); return _builder_redirect()


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
    if reward_type not in {"experience", "item"} or amount is None or not 1 <= amount <= 100000 or (reward_type == "item" and amount > 100): flash("Choose a valid reward amount (up to 100 items or 100,000 XP).", "error"); return _builder_redirect()
    if reward_type == "item" and database.execute("SELECT 1 FROM mud_item_definitions WHERE id=?", (target_id,)).fetchone() is None: flash("Choose an item definition for an item reward.", "error"); return _builder_redirect()
    if reward_type == "experience": target_id = None
    database.execute("INSERT INTO mud_quest_rewards(id,quest_id,reward_type,target_id,amount) VALUES(?,?,?,?,?)", (f"world:quest-reward:{uuid.uuid4().hex}", quest_id, reward_type, target_id, amount)); database.commit(); flash("Quest reward added.", "success"); return _builder_redirect()


@bp.post("/builder/quest-rewards/<path:reward_id>/delete")
@builder_required
def builder_delete_quest_reward(reward_id):
    validate_csrf(); database = get_db()
    if request.form.get("confirmation") != "delete": flash("Reward removal was not confirmed.", "error"); return _builder_redirect()
    if database.execute("DELETE FROM mud_quest_rewards WHERE id=?", (reward_id,)).rowcount == 0: abort(404)
    database.commit(); flash("Quest reward removed.", "success"); return _builder_redirect()


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
    database.commit()
    flash(f"Placed {definition['name']} in the room.", "success"); return _builder_redirect(room_id)


@bp.post("/builder/item-instances/<path:instance_id>/delete")
@builder_required
def builder_delete_item_instance(instance_id):
    validate_csrf(); database = get_db(); item = database.execute("SELECT i.id,i.room_id,d.name FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id WHERE i.id=?", (instance_id,)).fetchone()
    if item is None: abort(404)
    if request.form.get("confirmation") != "delete": flash("Item removal was not confirmed.", "error"); return _builder_redirect(item["room_id"])
    # Never silently destroy items currently held or containing items.
    if item["room_id"] is None or database.execute("SELECT 1 FROM mud_item_instances WHERE container_id=?", (instance_id,)).fetchone():
        flash("Only empty room items can be removed from the builder.", "error"); return _builder_redirect(item["room_id"])
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
        affected = [row["user_id"] for row in database.execute(
            "SELECT user_id FROM mud_world_locations WHERE room_id=? UNION "
            "SELECT user_id FROM mud_presence WHERE room_id=?", (room_id, room_id)
        )]
        database.executemany(
            "INSERT INTO mud_world_locations(user_id,room_id) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET room_id=excluded.room_id,"
            "updated_at=CURRENT_TIMESTAMP",
            ((user_id, start["id"]) for user_id in affected),
        )
        database.executemany(
            "INSERT INTO mud_player_locations(user_id,mode,location_type,location_key) "
            "VALUES(?,'traditional','world_room',?) ON CONFLICT(user_id) DO UPDATE SET "
            "mode='traditional',location_type='world_room',location_key=excluded.location_key,"
            "instance_id=NULL,updated_at=CURRENT_TIMESTAMP",
            ((user_id, start["id"]) for user_id in affected),
        )
        database.execute(
            "UPDATE mud_presence SET room_id=?,location_type='world_room',location_key=?,"
            "instance_id=NULL WHERE room_id=? OR (location_type='world_room' AND location_key=?)",
            (start["id"], start["id"], room_id, room_id),
        )
        database.execute("DELETE FROM mud_world_rooms WHERE id=?", (room_id,))
        for user_id in affected:
            _insert_event(database, EventDraft(
                f"{room['name']} was removed by a world builder. You have been moved to {start['name']}.",
                event_type="movement", recipient_ids=(user_id,),
            ), default_user_id=user_id)
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
        "INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) "
        "VALUES(?,?,?,?,?)", (exit_id, *fields),
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
        "SELECT e.id,e.from_room_id FROM mud_world_exits e JOIN mud_world_rooms r "
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
        "UPDATE mud_world_exits SET from_room_id=?,to_room_id=?,name=?,direction=? WHERE id=?",
        (*fields, exit_id),
    )
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
