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
        "/get <item> [from <container>], /drop <item>, /open <container>\n"
        "/put <item> in <container>, /equip <item>, /unequip <item>, /use <item>\n"
        "/talk <npc>, /respond <number or response> — converse with an NPC\n"
        "/quests, /quest accept <name> — view or accept available quests\n"
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


def _room_npc_rows(database, room_id):
    return database.execute(
        "SELECT i.id,i.definition_id,i.room_id,i.state_json,d.name,d.description,"
        "d.conversation_id,d.properties_json,d.behaviors_json "
        "FROM mud_npc_instances AS i JOIN mud_npc_definitions AS d "
        "ON d.id=i.definition_id WHERE i.room_id=? "
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
        "items": [{"id": item["id"], "name": item["name"], "item_type": item["item_type"]} for item in _room_item_rows(database, room["id"])],
        "npcs": [{"id": npc["id"], "definition_id": npc["definition_id"],
                  "name": npc["name"], "description": npc["description"]}
                 for npc in _room_npc_rows(database, room["id"])],
    }


def _describe_room(database, room, viewer_id=None):
    exits = _room_exits(database, room["id"])
    routes = ", ".join(_exit_label(item) for item in exits) if exits else "none"
    occupants = _room_occupants(database, room["id"], viewer_id) if viewer_id else []
    people = ", ".join(item["character_name"] for item in occupants) if occupants else "none"
    items = ", ".join(item["name"] for item in _room_item_rows(database, room["id"])) or "none"
    npcs = ", ".join(item["name"] for item in _room_npc_rows(database, room["id"])) or "none"
    return (
        f"{room['name']}\n{room['description']}\n\n"
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
    suffix = "IS NOT NULL" if equipped else "IS NULL"
    return database.execute(
        "SELECT i.*,d.name,d.description,d.item_type,d.equipment_slot AS definition_slot,d.properties_json "
        "FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id "
        f"WHERE i.owner_character_id=? AND i.equipped_slot {suffix} ORDER BY d.name COLLATE NOCASE,i.id",
        (character["id"],),
    ).fetchall()


def _room_item_rows(database, room_id):
    return database.execute(
        "SELECT i.*,d.name,d.description,d.item_type,d.equipment_slot AS definition_slot,d.properties_json "
        "FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id "
        "WHERE i.room_id=? ORDER BY d.name COLLATE NOCASE,i.id", (room_id,)
    ).fetchall()


def _container_rows(database, container_id):
    return database.execute(
        "SELECT i.*,d.name,d.description,d.item_type,d.equipment_slot AS definition_slot,d.properties_json "
        "FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id "
        "WHERE i.container_id=? ORDER BY d.name COLLATE NOCASE,i.id", (container_id,)
    ).fetchall()


def _is_container(row):
    return row["item_type"] == "container"


def _visible_container(database, user_id, room, item_id):
    row = _item_row(database, item_id)
    if row is None or not _is_container(row):
        raise ActionError("That is not an available container.")
    character = _character_row(database, user_id)
    if row["room_id"] != room["id"] and row["owner_character_id"] != character["id"]:
        raise ActionError("That container is not available to you.")
    return row


def inventory_state(database, user_id):
    inventory = [dict(row) for row in _inventory_rows(database, user_id)]
    equipment = [dict(row) for row in _inventory_rows(database, user_id, equipped=True)]
    return {"inventory": inventory, "equipment": equipment, "slots": EQUIPMENT_SLOTS}


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
                database.execute(
                    "INSERT INTO mud_item_instances(id,definition_id,owner_character_id) VALUES(?,?,?)",
                    (f"reward:item:{uuid.uuid4().hex}", reward["target_id"], character["id"]),
                )
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
            count = database.execute(
                "SELECT COUNT(*) AS amount FROM mud_item_instances WHERE definition_id=? "
                "AND owner_character_id=?", (target_id, character["id"]),
            ).fetchone()["amount"]
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
    definitions = {row["definition_id"] for row in database.execute(
        "SELECT definition_id FROM mud_item_instances WHERE owner_character_id=?", (character["id"],)
    )}
    for definition_id in definitions:
        events += _set_objective_progress(database, user, "possess_item", definition_id)
    return events


def _describe_item(database, row):
    body = f"{row['name']}\n{row['description']}\nType: {row['item_type']}"
    if row["definition_slot"]:
        body += f"\nEquipment slot: {row['definition_slot'].replace('_', ' ')}"
    if _is_container(row):
        contents = _container_rows(database, row["id"])
        if row["is_open"]:
            body += "\nContents: " + (", ".join(child["name"] for child in contents) or "empty")
        else:
            body += "\nIt is closed."
    return body


def _examine_item(database, user, room, target):
    candidates = list(_room_item_rows(database, room["id"])) + list(_inventory_rows(database, user["id"])) + list(_inventory_rows(database, user["id"], True))
    for container in _room_item_rows(database, room["id"]):
        if _is_container(container) and container["is_open"]:
            candidates.extend(_container_rows(database, container["id"]))
    key = " ".join(target.casefold().split())
    matching = [item for item in candidates if item["id"] == target or " ".join(item["name"].casefold().split()) == key]
    if not matching:
        return None
    if len(matching) > 1:
        raise ActionError(f'More than one item matches "{target}".')
    return _describe_item(database, matching[0])


examinations.register(_examine_item)


def _examine_npc(_database, _user, room, target):
    key = " ".join(target.casefold().split())
    matches = [npc for npc in _room_npc_rows(_database, room["id"])
               if npc["id"] == target or " ".join(npc["name"].casefold().split()) == key]
    if not matches:
        return None
    if len(matches) > 1:
        raise ActionError(f'More than one NPC matches "{target}".')
    return f"{matches[0]['name']}\n{matches[0]['description']}"


examinations.register(_examine_npc)


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
    container_id = str(parameters.get("container_id", "")).strip()
    if not container_id and " from " in target.casefold():
        target, container_name = re.split(r"\s+from\s+", target, maxsplit=1, flags=re.I)
        container_id = _item_matches(_room_item_rows(database, room["id"]), container_name)["id"]
    container = _visible_container(database, user["id"], room, container_id) if container_id else None
    source = _container_rows(database, container["id"]) if container else _room_item_rows(database, room["id"])
    if container and not container["is_open"]:
        raise ActionError("Open that container first.")
    item = _item_matches(source, target)
    character = _character_row(database, user["id"])
    database.execute("UPDATE mud_item_instances SET room_id=NULL,container_id=NULL,owner_character_id=?,equipped_slot=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (character["id"], item["id"]))
    return [EventDraft(f"You pick up {item['name']}.", event_type="item")] + \
        _set_objective_progress(database, user, "possess_item", item["definition_id"])


def _drop_action(database, user, parameters):
    target = _item_target(parameters)
    if not target:
        raise ActionError("Drop what?")
    item = _item_matches(_inventory_rows(database, user["id"]), target)
    room = _current_room(database, user["id"])
    database.execute("UPDATE mud_item_instances SET room_id=?,owner_character_id=NULL,container_id=NULL,equipped_slot=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (room["id"], item["id"]))
    return [EventDraft(f"You drop {item['name']}.", event_type="item")]


def _open_action(database, user, parameters):
    target = _item_target(parameters)
    room = _current_room(database, user["id"])
    item = _visible_container(database, user["id"], room, target)
    if item["is_open"]:
        return [EventDraft(f"{item['name']} is already open.\n" + _describe_item(database, item), event_type="item")]
    database.execute("UPDATE mud_item_instances SET is_open=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item["id"],))
    refreshed = _item_row(database, item["id"])
    return [EventDraft(f"You open {item['name']}.\n" + _describe_item(database, refreshed), event_type="item")]


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
    if item["id"] == container["id"]:
        raise ActionError("An item cannot contain itself.")
    database.execute("UPDATE mud_item_instances SET room_id=NULL,owner_character_id=NULL,container_id=?,equipped_slot=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (container["id"], item["id"]))
    return [EventDraft(f"You put {item['name']} in {container['name']}.", event_type="item")]


def _equip_action(database, user, parameters):
    target = _item_target(parameters)
    item = _item_matches(_inventory_rows(database, user["id"]), target)
    if item["item_type"] != "equipment" or not item["definition_slot"]:
        raise ActionError("That item cannot be equipped.")
    character = _character_row(database, user["id"])
    slot = item["definition_slot"]
    existing = database.execute("SELECT id FROM mud_item_instances WHERE owner_character_id=? AND equipped_slot=?", (character["id"], slot)).fetchone()
    if existing:
        database.execute("UPDATE mud_item_instances SET equipped_slot=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (existing["id"],))
    database.execute("UPDATE mud_item_instances SET equipped_slot=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (slot, item["id"]))
    return [EventDraft(f"You equip {item['name']} ({slot.replace('_', ' ')}).", event_type="item")]


def _unequip_action(database, user, parameters):
    target = _item_target(parameters)
    item = _item_matches(_inventory_rows(database, user["id"], True), target)
    database.execute("UPDATE mud_item_instances SET equipped_slot=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (item["id"],))
    return [EventDraft(f"You unequip {item['name']}.", event_type="item")]


def _use_action(database, user, parameters):
    target = _item_target(parameters)
    item = _item_matches(list(_inventory_rows(database, user["id"])) + list(_inventory_rows(database, user["id"], True)), target)
    try:
        properties = json.loads(item["properties_json"])
    except json.JSONDecodeError:
        properties = {}
    message = properties.get("use_message")
    if not isinstance(message, str) or not message.strip():
        raise ActionError(f"{item['name']} has no usable effect yet.")
    return [EventDraft(message.strip(), event_type="item")]


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


def _available_dialogue_responses(database, character_id, npc_id, node_id):
    node, responses = _dialogue_node(database, node_id)
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
    npc = _npc_match(_room_npc_rows(database, room["id"]), target)
    if not npc["conversation_id"]:
        raise ActionError(f"{npc['name']} has nothing to say right now.")
    node = database.execute(
        "SELECT id FROM mud_dialogue_nodes WHERE conversation_id=? AND is_start=1",
        (npc["conversation_id"],),
    ).fetchone()
    if node is None:
        raise ActionError(f"{npc['name']} has nothing to say right now.")
    character = _character_row(database, user["id"])
    database.execute(
        "INSERT INTO mud_conversation_sessions(character_id,npc_instance_id,node_id) VALUES(?,?,?) "
        "ON CONFLICT(character_id) DO UPDATE SET npc_instance_id=excluded.npc_instance_id,"
        "node_id=excluded.node_id,updated_at=CURRENT_TIMESTAMP",
        (character["id"], npc["id"], node["id"]),
    )
    node, responses = _available_dialogue_responses(database, character["id"], npc["id"], node["id"])
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
    if session_row is None or session_row["room_id"] != room["id"]:
        raise ActionError("Start a nearby conversation with /talk first.")
    node, responses = _available_dialogue_responses(
        database, character["id"], session_row["npc_id"], session_row["node_id"]
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
        state_row = database.execute(
            "SELECT state_json FROM mud_npc_player_states WHERE character_id=? AND npc_instance_id=?",
            (character["id"], session_row["npc_id"]),
        ).fetchone()
        try:
            state = json.loads(state_row["state_json"]) if state_row else {}
        except json.JSONDecodeError:
            state = {}
        state[chosen["state_key"]] = chosen["state_value"]
        database.execute(
            "INSERT INTO mud_npc_player_states(character_id,npc_instance_id,state_json) VALUES(?,?,?) "
            "ON CONFLICT(character_id,npc_instance_id) DO UPDATE SET state_json=excluded.state_json,"
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
        database.execute(
            "UPDATE mud_conversation_sessions SET node_id=?,updated_at=CURRENT_TIMESTAMP "
            "WHERE character_id=?", (chosen["next_node_id"], character["id"]),
        )
        next_node, next_responses = _available_dialogue_responses(
            database, character["id"], session_row["npc_id"], chosen["next_node_id"]
        )
        events.append(EventDraft(_dialogue_text(
            session_row["name"], next_node, next_responses
        ), event_type="dialogue"))
    else:
        database.execute("DELETE FROM mud_conversation_sessions WHERE character_id=?", (character["id"],))
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
    events = [
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
    events += _set_objective_progress(database, user, "visit_room", destination_room["id"])
    return events


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
actions.register(ActionDefinition(key="get", label="PICK UP", description="Pick up an item in this room or an open container.", commands=("get", "take"), handler=_get_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="drop", label="DROP", description="Drop a carried item in this room.", commands=("drop",), handler=_drop_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="open", label="OPEN", description="Open an available container.", commands=("open",), handler=_open_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="put", label="PUT AWAY", description="Put a carried item in an open container.", commands=("put",), handler=_put_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="equip", label="EQUIP", description="Equip a carried item.", commands=("equip", "wear"), handler=_equip_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="unequip", label="UNEQUIP", description="Remove equipped gear.", commands=("unequip", "remove"), handler=_unequip_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="use", label="USE", description="Use a carried item.", commands=("use",), handler=_use_action, group="ITEMS", show_context=False))
actions.register(ActionDefinition(key="talk", label="TALK", description="Talk to a nearby NPC.", commands=("talk", "speak"), handler=_talk_action, group="NPCS", show_context=False))
actions.register(ActionDefinition(key="respond", label="RESPOND", description="Choose a response in your current NPC conversation.", commands=("respond", "reply"), handler=_respond_action, group="DIALOGUE", show_context=False))
actions.register(ActionDefinition(key="quests", label="VIEW QUESTS", description="View your active quest objectives and progress.", commands=("quests", "quest", "objectives"), handler=_quests_action, group="QUESTS"))
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


def _item_actions(database, user):
    room = _current_room(database, user["id"])
    options = []
    for item in _room_item_rows(database, room["id"]):
        base = {"item_id": item["id"]}
        options.extend((
            {"key": "look", "label": f"EXAMINE {item['name'].upper()}", "description": item["description"], "group": "ITEMS", "parameters": {"target": item["id"]}},
            {"key": "get", "label": f"PICK UP {item['name'].upper()}", "description": "Pick it up.", "group": "ITEMS", "parameters": base},
        ))
        if _is_container(item): options.append({"key": "open", "label": f"OPEN {item['name'].upper()}", "description": "Open and inspect it.", "group": "ITEMS", "parameters": base})
        if _is_container(item) and item["is_open"]:
            for child in _container_rows(database, item["id"]):
                options.append({"key": "get", "label": f"TAKE {child['name'].upper()} FROM {item['name'].upper()}", "description": "Take from the open container.", "group": "ITEMS", "parameters": {"item_id": child["id"], "container_id": item["id"]}})
    for item in _inventory_rows(database, user["id"]):
        base = {"item_id": item["id"]}
        options.extend((
            {"key": "look", "label": f"EXAMINE {item['name'].upper()}", "description": item["description"], "group": "INVENTORY", "parameters": {"target": item["id"]}},
            {"key": "drop", "label": f"DROP {item['name'].upper()}", "description": "Leave it here.", "group": "INVENTORY", "parameters": base},
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
    for npc in _room_npc_rows(database, room["id"]):
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
    if session_row and session_row["room_id"] == room["id"]:
        _node, responses = _available_dialogue_responses(
            database, character["id"], session_row["npc_id"], session_row["node_id"],
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
    inventory = inventory_state(database, g.user["id"])
    quests = quest_state(database, g.user["id"])
    mud_actions = actions.contextual(g.user, database)
    events = _recent_events(database, g.user["id"])
    database.commit()
    return render_template(
        "mud/index.html", events=events,
        mud_actions=mud_actions, room=room, character=character, inventory=inventory,
        quests=quests,
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
        inventory = inventory_state(database, g.user["id"])
        quests = quest_state(database, g.user["id"])
        available = actions.contextual(g.user, database)
        database.commit()
    except (ActionError, sqlite3.Error) as problem:
        database.rollback()
        if isinstance(problem, ActionError):
            return jsonify(error=str(problem)), problem.status
        raise
    return jsonify(events=events, room=room, character=character, inventory=inventory,
                   quests=quests, actions=available)


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
        inventory = inventory_state(database, g.user["id"])
        quests = quest_state(database, g.user["id"])
        available = actions.contextual(g.user, database)
        database.commit()
        return jsonify(
            error=str(problem), room=room, character=character, inventory=inventory,
            quests=quests, actions=available
        ), problem.status
    database = get_db()
    room = _room_state(database, g.user["id"])
    character = character_state(database, g.user["id"])
    inventory = inventory_state(database, g.user["id"])
    quests = quest_state(database, g.user["id"])
    available = actions.contextual(g.user, database)
    database.commit()
    return jsonify(
        action=action_key, events=events,
        actions=available, room=room, character=character, inventory=inventory,
        quests=quests,
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
    definitions = database.execute("SELECT * FROM mud_item_definitions ORDER BY name COLLATE NOCASE,id").fetchall()
    items_by_room = {room["id"]: [] for room in rooms}
    for row in database.execute("SELECT i.id,i.room_id,d.name,d.item_type FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id WHERE i.room_id IS NOT NULL ORDER BY d.name COLLATE NOCASE"):
        items_by_room.setdefault(row["room_id"], []).append(dict(row))
    conversations = [dict(row) for row in database.execute(
        "SELECT * FROM mud_conversations ORDER BY name COLLATE NOCASE,id"
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
        "SELECT * FROM mud_npc_definitions ORDER BY name COLLATE NOCASE,id"
    )]
    npcs_by_room = {room["id"]: [] for room in rooms}
    for row in database.execute(
        "SELECT i.id,i.room_id,d.name FROM mud_npc_instances AS i JOIN mud_npc_definitions AS d "
        "ON d.id=i.definition_id ORDER BY d.name COLLATE NOCASE,i.id"
    ):
        npcs_by_room.setdefault(row["room_id"], []).append(dict(row))
    quests = [dict(row) for row in database.execute(
        "SELECT * FROM mud_quests ORDER BY name COLLATE NOCASE,id"
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
    )


def _item_definition_fields():
    name = " ".join(request.form.get("name", "").split())
    description = request.form.get("description", "").strip()
    item_type = request.form.get("item_type", "misc")
    slot = request.form.get("equipment_slot", "").strip() or None
    use_message = request.form.get("use_message", "").strip()
    if not 2 <= len(name) <= 80 or not 1 <= len(description) <= 4000:
        return None, "Item names must be 2–80 characters and descriptions 1–4000 characters."
    if item_type not in {"misc", "equipment", "container", "consumable", "key"}:
        return None, "Choose a valid item type."
    if slot not in EQUIPMENT_SLOTS:
        if slot is not None: return None, "Choose a valid equipment slot."
    if item_type != "equipment": slot = None
    if len(use_message) > 1000: return None, "Use messages cannot exceed 1000 characters."
    return (name, description, item_type, json.dumps({"use_message": use_message} if use_message else {}), slot), None


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


@bp.post("/builder/conversations")
@builder_required
def builder_create_conversation():
    validate_csrf(); name = " ".join(request.form.get("name", "").split())
    if not 2 <= len(name) <= 80:
        flash("Conversation names must be between 2 and 80 characters.", "error"); return _builder_redirect()
    database = get_db(); conversation_id = f"world:conversation:{uuid.uuid4().hex}"
    database.execute("INSERT INTO mud_conversations(id,name,created_by) VALUES(?,?,?)", (conversation_id, name, g.user["id"]))
    database.commit(); flash(f"Conversation {name} created.", "success"); return _builder_redirect()


@bp.post("/builder/conversations/<path:conversation_id>")
@builder_required
def builder_update_conversation(conversation_id):
    validate_csrf(); database = get_db(); name = " ".join(request.form.get("name", "").split())
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


@bp.post("/builder/npcs")
@builder_required
def builder_create_npc_definition():
    validate_csrf(); database = get_db(); fields, error = _npc_definition_fields()
    if error: flash(error, "error"); return _builder_redirect()
    if fields[2] and database.execute("SELECT 1 FROM mud_conversations WHERE id=?", (fields[2],)).fetchone() is None: flash("Choose an existing conversation.", "error"); return _builder_redirect()
    npc_id = f"world:npc-definition:{uuid.uuid4().hex}"; database.execute("INSERT INTO mud_npc_definitions(id,name,description,conversation_id,properties_json,behaviors_json,created_by) VALUES(?,?,?,?,?,?,?)", (npc_id, *fields, g.user["id"])); database.commit()
    flash(f"NPC definition {fields[0]} created.", "success"); return _builder_redirect()


@bp.post("/builder/npcs/<path:npc_id>")
@builder_required
def builder_update_npc_definition(npc_id):
    validate_csrf(); database = get_db(); fields, error = _npc_definition_fields()
    if database.execute("SELECT 1 FROM mud_npc_definitions WHERE id=?", (npc_id,)).fetchone() is None: abort(404)
    if error: flash(error, "error"); return _builder_redirect()
    if fields[2] and database.execute("SELECT 1 FROM mud_conversations WHERE id=?", (fields[2],)).fetchone() is None: flash("Choose an existing conversation.", "error"); return _builder_redirect()
    database.execute("UPDATE mud_npc_definitions SET name=?,description=?,conversation_id=?,properties_json=?,behaviors_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (*fields, npc_id)); database.commit(); flash("NPC definition updated.", "success"); return _builder_redirect()


@bp.post("/builder/npcs/place")
@builder_required
def builder_place_npc():
    validate_csrf(); database = get_db(); definition_id = request.form.get("definition_id", "").strip(); room_id = request.form.get("room_id", "").strip()
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
    quest_id = f"world:quest:{uuid.uuid4().hex}"; database.execute("INSERT INTO mud_quests(id,name,description,prerequisite_quest_id,created_by) VALUES(?,?,?,?,?)", (quest_id, *fields, g.user["id"])); database.commit(); flash(f"Quest {fields[0]} created.", "success"); return _builder_redirect()


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
    get_db().execute("INSERT INTO mud_item_definitions(id,name,description,item_type,properties_json,equipment_slot,created_by) VALUES(?,?,?,?,?,?,?)", (item_id, *fields, g.user["id"])); get_db().commit()
    flash(f"Item definition {fields[0]} created.", "success"); return _builder_redirect()


@bp.post("/builder/items/<path:item_id>")
@builder_required
def builder_update_item_definition(item_id):
    validate_csrf(); fields, error = _item_definition_fields(); database = get_db()
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
