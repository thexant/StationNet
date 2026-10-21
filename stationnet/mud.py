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
            alias = command.lower()
            if alias in self._commands:
                raise RuntimeError(f"Duplicate MUD command: {alias}")
            self._commands[alias] = definition

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
        definition = self._commands.get(parts[0].lower())
        if definition is None:
            raise ActionError("Unknown command. Use /help to list available commands.")
        return definition, {"arguments": parts[1:]}

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
    return [EventDraft("MUD service online. World and character systems are not yet initialized.")]


def _help_action(_database, _user, parameters):
    if parameters.get("arguments"):
        raise ActionError("The help action does not accept arguments.")
    return [EventDraft("Available commands: /help and /status. Gameplay commands arrive in later chunks.")]


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
    return render_template(
        "mud/index.html", events=_recent_events(g.user["id"]),
        mud_actions=actions.contextual(g.user, get_db()),
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
        return jsonify(error=str(problem)), problem.status
    return jsonify(
        action=action_key, events=events,
        actions=actions.contextual(g.user, get_db()),
    )
