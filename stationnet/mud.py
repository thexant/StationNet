"""Native MUD service and server-authoritative action dispatch."""
from dataclasses import dataclass, field
import json
import re
import shlex
import sqlite3
import uuid

from flask import Blueprint, abort, g, jsonify, render_template, request

from .auth import login_required
from .db import get_db
from .security import validate_csrf

bp = Blueprint("mud", __name__, url_prefix="/mud")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


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


def _status_action(_database, _user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The status action does not accept arguments.")
    return [EventDraft("MUD service online. Persistent world navigation is available.")]


def _help_action(_database, _user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The help action does not accept arguments.")
    return [EventDraft(
        "Available commands:\n"
        "/look (or /l, /examine, /x) — examine your surroundings\n"
        "/exits — list routes from this room\n"
        "/go <exit> (or /move, /walk) — travel through an exit\n"
        "/north, /south, /east, /west, /up, /down, /in, /out — direction shortcuts\n"
        "/status — check the MUD service"
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
    }


def _describe_room(database, room):
    exits = _room_exits(database, room["id"])
    routes = ", ".join(_exit_label(item) for item in exits) if exits else "none"
    return f"{room['name']}\n{room['description']}\n\nExits: {routes}"


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
        return _describe_room(database, room)
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
    database.execute(
        "UPDATE mud_world_locations SET room_id=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
        (chosen["to_room_id"], user["id"]),
    )
    destination_room = database.execute(
        "SELECT id,name,description FROM mud_world_rooms WHERE id=?",
        (chosen["to_room_id"],),
    ).fetchone()
    route = chosen["direction"] or chosen["name"]
    return [EventDraft(
        f"You go {route}.\n\n{_describe_room(database, destination_room)}",
        event_type="movement",
    )]


actions = ActionRegistry()
actions.register(ActionDefinition(
    key="status", label="SYSTEM STATUS",
    description="Check the current MUD service state.",
    commands=("status",), handler=_status_action,
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


class MudService:
    """Normalizes, authorizes, executes, and records MUD actions atomically."""

    def __init__(self, registry):
        self.registry = registry

    def normalize(self, form):
        command = form.get("command", "").strip()
        action_key = form.get("action", "").strip()
        if bool(command) == bool(action_key):
            raise ActionError("Submit exactly one command or contextual action.")
        request_id = form.get("request_id", "").strip() or uuid.uuid4().hex
        if not REQUEST_ID_RE.fullmatch(request_id):
            raise ActionError("Invalid action request identifier.")
        if command:
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
        return ActionRequest(definition, parameters, method, request_id)

    def execute(self, action_request, user):
        if not _authorized(action_request.definition, user):
            raise ActionError("You do not have permission to use that action.", 403)
        database = get_db()
        try:
            database.execute("BEGIN IMMEDIATE")
            prior = database.execute(
                "SELECT id,action_key FROM mud_action_log WHERE actor_id=? AND request_id=?",
                (user["id"], action_request.request_id),
            ).fetchone()
            if prior is not None:
                if prior["action_key"] != action_request.definition.key:
                    raise ActionError("That action request identifier was already used.", 409)
                events = database.execute(
                    "SELECT id,event_type,body,created_at FROM mud_events "
                    "WHERE action_id=? ORDER BY id", (prior["id"],),
                ).fetchall()
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
                audience_key = draft.audience_key or str(user["id"])
                event_cursor = database.execute(
                    "INSERT INTO mud_events(action_id,audience_type,audience_key,event_type,body) "
                    "VALUES(?,?,?,?,?)",
                    (cursor.lastrowid, draft.audience_type, audience_key,
                     draft.event_type, draft.body),
                )
                event_rows.append(database.execute(
                    "SELECT id,event_type,body,created_at FROM mud_events WHERE id=?",
                    (event_cursor.lastrowid,),
                ).fetchone())
            database.commit()
            return action_request.definition.key, [dict(row) for row in event_rows]
        except (ActionError, sqlite3.Error):
            database.rollback()
            raise


service = MudService(actions)


def _require_enabled():
    if not g.station["mud_enabled"]:
        abort(404)


def _recent_events(user_id):
    rows = get_db().execute(
        "SELECT * FROM (SELECT id,event_type,body,created_at FROM mud_events "
        "WHERE audience_type='private' AND audience_key=? ORDER BY id DESC LIMIT 100) "
        "ORDER BY id", (str(user_id),),
    ).fetchall()
    return [dict(row) for row in rows]


@bp.get("")
@login_required
def index():
    _require_enabled()
    database = get_db()
    room = _room_state(database, g.user["id"])
    mud_actions = actions.contextual(g.user, database)
    database.commit()
    return render_template(
        "mud/index.html", events=_recent_events(g.user["id"]),
        mud_actions=mud_actions, room=room,
    )


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
        available = actions.contextual(g.user, database)
        database.commit()
        return jsonify(error=str(problem), room=room, actions=available), problem.status
    database = get_db()
    room = _room_state(database, g.user["id"])
    available = actions.contextual(g.user, database)
    database.commit()
    return jsonify(
        action=action_key, events=events,
        actions=available, room=room,
    )
