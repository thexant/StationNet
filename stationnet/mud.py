"""Native MUD service and server-authoritative action dispatch."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import math
import re
import shlex
import sqlite3
import uuid

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from .auth import admin_required, login_required
from .db import get_db
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


def _character_row(database, user_id, track_key=ACTIVE_PROGRESSION_TRACK):
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


def character_state(database, user_id, track_key=ACTIVE_PROGRESSION_TRACK):
    """Return the authoritative identity and selected progression track."""
    row = _character_row(database, user_id, track_key)
    level_floor = experience_for_level(row["level"])
    next_floor = experience_for_level(row["level"] + 1)
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": row["display_name"],
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


def award_experience(database, user_id, amount, track_key=ACTIVE_PROGRESSION_TRACK):
    """Award XP on a track; the caller owns the surrounding transaction."""
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ValueError("Experience award must be a positive integer.")
    before = character_state(database, user_id, track_key)
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
        (experience, level, current_health, maximum_health, before["id"], track_key),
    )
    return {
        "amount": amount,
        "levels_gained": levels_gained,
        "previous_level": before["level"],
        "character": character_state(database, user_id, track_key),
    }


def _status_action(_database, _user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The status action does not accept arguments.")
    return [EventDraft("MUD service online. Persistent world navigation is available.")]


def _character_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The character action does not accept arguments.")
    character = character_state(database, user["id"])
    attributes = character["attributes"]
    return [EventDraft(
        f"{character['name']} — Level {character['level']}\n"
        f"Health: {character['health']}/{character['maximum_health']}\n"
        f"Experience: {character['experience_into_level']}/"
        f"{character['experience_for_next_level']} "
        f"({character['experience_to_next_level']} to next level)\n"
        f"Strength {attributes['strength']} · Agility {attributes['agility']} · "
        f"Intellect {attributes['intellect']} · Vitality {attributes['vitality']}\n"
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
        "/north, /south, /east, /west, /up, /down, /in, /out — direction shortcuts\n"
        "/status — check the MUD service\n"
        "/character (or /stats) — view your character and progression\n"
        "Text without a leading slash is spoken to everyone in your room."
    )]


START_ROOM_ID = "stationnet:start"
START_ROOM_NAME = "Starting Room"
START_ROOM_DESCRIPTION = (
    "A quiet, unadorned room waiting for this station's world to take shape."
)


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


def _current_room(database, user_id):
    room = database.execute(
        "SELECT r.id,r.name,r.description FROM mud_world_locations AS l "
        "JOIN mud_world_rooms AS r ON r.id=l.room_id WHERE l.user_id=?",
        (user_id,),
    ).fetchone()
    if room is not None:
        return room
    room = _start_room(database)
    database.execute(
        "INSERT INTO mud_world_locations(user_id,room_id) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET room_id=excluded.room_id,"
        "updated_at=CURRENT_TIMESTAMP",
        (user_id, room["id"]),
    )
    return room


def _room_exits(database, room_id):
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


def _occupant_ids(database, room_id, exclude_user_id=None):
    parameters = [room_id]
    exclusion = ""
    if exclude_user_id is not None:
        exclusion = " AND user_id<>?"
        parameters.append(exclude_user_id)
    rows = database.execute(
        "SELECT DISTINCT user_id FROM mud_presence WHERE room_id=?" + exclusion,
        parameters,
    ).fetchall()
    return tuple(row["user_id"] for row in rows)


def _room_occupants(database, room_id, viewer_id):
    return [dict(row) for row in database.execute(
        "SELECT DISTINCT u.id,u.username,u.callsign,c.id AS character_id,"
        "COALESCE(c.display_name,u.callsign) AS character_name FROM mud_presence AS p "
        "JOIN users AS u ON u.id=p.user_id "
        "LEFT JOIN mud_characters AS c ON c.user_id=u.id "
        "WHERE p.room_id=? AND p.user_id<>? "
        "ORDER BY character_name COLLATE NOCASE,u.id",
        (room_id, viewer_id),
    ).fetchall()]


def _exit_label(exit_row):
    route = exit_row["direction"] or exit_row["name"]
    return f"{route.upper()} — {exit_row['destination_name']}"


def _room_state(database, user_id):
    room = _current_room(database, user_id)
    return {
        "id": room["id"],
        "name": room["name"],
        "description": room["description"],
        "exits": [
            {
                "id": exit_row["id"],
                "name": exit_row["name"],
                "direction": exit_row["direction"],
                "destination_id": exit_row["to_room_id"],
                "destination_name": exit_row["destination_name"],
            }
            for exit_row in _room_exits(database, room["id"])
        ],
        "occupants": _room_occupants(database, room["id"], user_id),
    }


def _describe_room(database, room, viewer_id=None):
    exits = _room_exits(database, room["id"])
    routes = ", ".join(_exit_label(item) for item in exits) if exits else "none"
    occupants = _room_occupants(database, room["id"], viewer_id) if viewer_id else []
    people = ", ".join(item["character_name"] for item in occupants) if occupants else "none"
    return (
        f"{room['name']}\n{room['description']}\n\n"
        f"Exits: {routes}\nOther players: {people}"
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


def _look_action(database, user, parameters):
    arguments = parameters.get("arguments", [])
    target = parameters.get("target", "")
    if arguments:
        target = " ".join(str(item) for item in arguments).strip()
    room = _current_room(database, user["id"])
    description = examinations.resolve(database, user, room, str(target).strip())
    return [EventDraft(description, event_type="room")]


def _exits_action(database, user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The exits command does not accept arguments.")
    room = _current_room(database, user["id"])
    exits = _room_exits(database, room["id"])
    if not exits:
        body = f"There are no visible exits from {room['name']}."
    else:
        body = "Available exits:\n" + "\n".join(
            f"- {_exit_label(exit_row)}" for exit_row in exits
        )
    return [EventDraft(body, event_type="room")]


def _go_action(database, user, parameters):
    character = character_state(database, user["id"])
    room = _current_room(database, user["id"])
    exit_id = str(parameters.get("exit_id", "")).strip()
    arguments = parameters.get("arguments", [])
    destination = " ".join(str(item) for item in arguments).strip() if arguments else ""
    if exit_id:
        matches = database.execute(
            "SELECT e.id,e.name,e.direction,e.to_room_id,r.name AS destination_name "
            "FROM mud_world_exits AS e JOIN mud_world_rooms AS r ON r.id=e.to_room_id "
            "WHERE e.id=? AND e.from_room_id=?",
            (exit_id, room["id"]),
        ).fetchall()
    elif destination:
        key = " ".join(destination.casefold().split())
        matches = [
            item for item in _room_exits(database, room["id"])
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
    old_recipients = _occupant_ids(database, room["id"], user["id"])
    database.execute(
        "UPDATE mud_world_locations SET room_id=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
        (chosen["to_room_id"], user["id"]),
    )
    database.execute(
        "UPDATE mud_presence SET room_id=? WHERE user_id=?",
        (chosen["to_room_id"], user["id"]),
    )
    destination_room = database.execute(
        "SELECT id,name,description FROM mud_world_rooms WHERE id=?",
        (chosen["to_room_id"],),
    ).fetchone()
    route = chosen["direction"] or chosen["name"]
    new_recipients = _occupant_ids(database, destination_room["id"], user["id"])
    return [
        EventDraft(
            f"You go {route}.\n\n{_describe_room(database, destination_room, user['id'])}",
            event_type="movement",
        ),
        EventDraft(
            f"{character['name']} leaves via {route}.",
            event_type="departure", audience_type="room", audience_key=room["id"],
            recipient_ids=old_recipients,
        ),
        EventDraft(
            f"{character['name']} arrives.",
            event_type="arrival", audience_type="room",
            audience_key=destination_room["id"], recipient_ids=new_recipients,
        ),
    ]


def _say_action(database, user, parameters):
    body = str(parameters.get("message", "")).strip()
    if not 1 <= len(body) <= 1000:
        raise ActionError("Messages must be between 1 and 1000 characters.")
    room = _current_room(database, user["id"])
    if database.execute(
        "SELECT 1 FROM mud_presence WHERE user_id=? AND room_id=? LIMIT 1",
        (user["id"], room["id"]),
    ).fetchone() is None:
        raise ActionError("Reconnect to the MUD before speaking in this room.", 409)
    recipients = set(_occupant_ids(database, room["id"]))
    recipients.add(user["id"])
    character = character_state(database, user["id"])
    return [EventDraft(
        f"{character['name']}: {body}", event_type="chat",
        audience_type="room", audience_key=room["id"],
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
    return [
        {
            "key": "go",
            "label": _exit_label(exit_row),
            "description": f"Travel to {exit_row['destination_name']}.",
            "group": "NAVIGATION",
            "parameters": {"exit_id": exit_row["id"]},
        }
        for exit_row in _room_exits(database, room["id"])
    ]


actions.register_context_provider(_navigation_actions)


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


def _events_for_user(database, user_id, after=0, action_id=None, limit=200):
    filters = ["e.id>?"]
    parameters = [after]
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
        "SELECT p.user_id,p.room_id,COALESCE(c.display_name,u.callsign) AS character_name "
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
        affected[row["user_id"]] = (row["room_id"], row["character_name"])
    database.execute(
        "DELETE FROM mud_presence WHERE last_seen<? "
        "OR (disconnected_at IS NOT NULL AND disconnected_at<?)",
        (_timestamp_before(PRESENCE_SECONDS), _timestamp_before(DISCONNECT_GRACE_SECONDS)),
    )
    for user_id, (room_id, character_name) in affected.items():
        if room_id is None or database.execute(
            "SELECT 1 FROM mud_presence WHERE user_id=? LIMIT 1", (user_id,)
        ).fetchone() is not None:
            continue
        _insert_event(database, EventDraft(
            f"{character_name} disconnects.", event_type="departure",
            audience_type="room", audience_key=room_id,
            recipient_ids=_occupant_ids(database, room_id, user_id),
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
        "INSERT INTO mud_presence(user_id,connection_id,client_id,room_id,last_seen) "
        "VALUES(?,?,?,?,?) ON CONFLICT(user_id,connection_id,client_id) DO UPDATE SET "
        "room_id=excluded.room_id,last_seen=excluded.last_seen,disconnected_at=NULL",
        (user["id"], connection_id, client_id, room["id"], now),
    )
    if not already_present:
        _insert_event(database, EventDraft(
            f"{character['name']} arrives.", event_type="arrival",
            audience_type="room", audience_key=room["id"],
            recipient_ids=_occupant_ids(database, room["id"], user["id"]),
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
                visible = draft.audience_type == "private" or (
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
    rows = database.execute(
        "SELECT * FROM (SELECT e.id,e.event_type,e.body,e.created_at "
        "FROM mud_events AS e WHERE "
        "(e.audience_type='private' AND e.audience_key=?) OR EXISTS ("
        "SELECT 1 FROM mud_event_recipients AS r "
        "WHERE r.event_id=e.id AND r.user_id=?) "
        "ORDER BY e.id DESC LIMIT 100) ORDER BY id",
        (str(user_id), user_id),
    ).fetchall()
    return [dict(row) for row in rows]


@bp.get("")
@login_required
def index():
    _require_enabled()
    database = get_db()
    database.execute("BEGIN IMMEDIATE")
    _prune_presence(database)
    room = _room_state(database, g.user["id"])
    character = character_state(database, g.user["id"])
    mud_actions = actions.contextual(g.user, database)
    events = _recent_events(database, g.user["id"])
    database.commit()
    return render_template(
        "mud/index.html", events=events,
        mud_actions=mud_actions, room=room, character=character,
    )


@bp.get("/updates")
@login_required
def updates():
    _require_enabled()
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
        available = actions.contextual(g.user, database)
        database.commit()
    except (ActionError, sqlite3.Error) as problem:
        database.rollback()
        if isinstance(problem, ActionError):
            return jsonify(error=str(problem)), problem.status
        raise
    return jsonify(events=events, room=room, character=character, actions=available)


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
    try:
        action_request = service.normalize(request.form)
        action_key, events = service.execute(action_request, g.user)
    except ActionError as problem:
        database = get_db()
        room = _room_state(database, g.user["id"])
        character = character_state(database, g.user["id"])
        available = actions.contextual(g.user, database)
        database.commit()
        return jsonify(
            error=str(problem), room=room, character=character, actions=available
        ), problem.status
    database = get_db()
    room = _room_state(database, g.user["id"])
    character = character_state(database, g.user["id"])
    available = actions.contextual(g.user, database)
    database.commit()
    return jsonify(
        action=action_key, events=events,
        actions=available, room=room, character=character,
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
            "SELECT id FROM mud_world_rooms WHERE id IN (?,?)", (from_room_id, to_room_id)
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
    return render_template(
        "mud/builder.html", rooms=rooms, exits_by_room=exits_by_room,
        users=users, directions=DIRECTIONS,
    )


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
        "INSERT INTO mud_world_rooms(id,name,description) VALUES(?,?,?)",
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
        "SELECT id,name FROM mud_world_rooms WHERE id=?", (room_id,)
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
            "SELECT id,name,is_start FROM mud_world_rooms WHERE id=?", (room_id,)
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
        database.execute(
            "UPDATE mud_presence SET room_id=? WHERE room_id=?", (start["id"], room_id)
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
        "SELECT id,from_room_id FROM mud_world_exits WHERE id=?", (exit_id,)
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
        "SELECT id,name,from_room_id FROM mud_world_exits WHERE id=?", (exit_id,)
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
            "SELECT id,name FROM mud_world_rooms WHERE id=?", (room_id,)
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
