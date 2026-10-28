"""Authored world triggers: when something happens, check conditions, run actions.

A trigger is a small JSON rule. It names an event (a player enters a room,
uses an object, defeats an NPC, ...), the content that event must involve, a
list of conditions from ``mud_conditions``, and a short ordered list of
actions. Nothing here is a programming language: there are no expressions,
variables, or loops, and no code ever runs from a world package.

This module validates and rewrites triggers without touching the database.
The game engine (``mud.py``) loads them and applies their actions.

A normalized trigger looks like::

    {"id": "pull-lever", "name": "Pull the lever", "event": "use_object",
     "source": {"object": "lever-1"}, "scope": "world", "once": True,
     "cooldown_seconds": 0, "max_fires": None, "enabled": True,
     "conditions": [...], "fail_message": "", "actions": [...]}
"""
import re

from . import mud_conditions

MAX_TRIGGERS = 2000
MAX_ACTIONS = 10
MAX_TEXT = 500
MAX_DELAY_SECONDS = 3600
MAX_CHAIN_DEPTH = 4
MAX_CHAIN_ACTIONS = 40
MAX_PENDING_DELAYS = 20
TRIGGER_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SCOPES = ("character", "world")
EVENT_TYPES = ("enter_room", "use_object", "take_item", "say_phrase", "defeat_npc",
               "dialogue_choice", "quest_complete", "timer", "manual")
# Which source kinds each event accepts. Exactly one source is required,
# except for manual triggers, which are only run by other triggers.
EVENT_SOURCES = {
    "enter_room": ("room",), "use_object": ("object", "item"), "take_item": ("object", "item"),
    "say_phrase": ("room",), "defeat_npc": ("npc", "npc_instance"),
    "dialogue_choice": ("response",), "quest_complete": ("quest",), "timer": ("room",),
    "manual": (),
}
EVENT_LABELS = {
    "enter_room": "A player enters a room", "use_object": "A player uses or interacts with an object",
    "take_item": "A player picks up an item", "say_phrase": "A player says a phrase in a room",
    "defeat_npc": "A player helps defeat an NPC", "dialogue_choice": "A player picks a dialogue response",
    "quest_complete": "A player completes a quest", "timer": "A repeating timer (no player)",
    "manual": "Only when another trigger runs it",
}
SOURCE_LABELS = {"room": "room", "object": "placed object", "item": "item type", "npc": "NPC type",
                 "npc_instance": "placed NPC", "response": "dialogue response", "quest": "quest"}
ACTION_TYPES = ("message", "set_flag", "clear_flag", "give_item", "take_item", "experience", "coins",
                "take_coins", "heal", "damage", "set_object_state", "set_exit", "show", "hide",
                "spawn_npc", "teleport", "start_quest", "complete_objective", "run_trigger",
                "learn_destination", "forget_destination", "disposition", "reputation")
OUTCOME_ACTIONS = ("give_item", "take_item", "experience", "coins", "take_coins")
MESSAGE_TARGETS = ("actor", "room", "other_room")
EXIT_STATES = ("open", "locked", "hidden", "default")
# Actions that need a player to act on. Timer triggers have no player.
_ACTOR_ACTIONS = {"give_item", "take_item", "experience", "coins", "take_coins", "heal", "damage",
                  "teleport", "start_quest", "complete_objective", "learn_destination", "forget_destination",
                  "disposition", "reputation"}
# Procedural dungeons are generated fresh for each run, so their triggers only
# use actions that make sense inside one run.
DUNGEON_EVENTS = ("enter_room", "use_object", "take_item", "say_phrase", "defeat_npc", "manual")
DUNGEON_SOURCES = {"enter_room": ("room_role", "room_template"), "say_phrase": ("room_role", "room_template"),
                   "use_object": ("item",), "take_item": ("item",), "defeat_npc": ("npc",), "manual": ()}
DUNGEON_ACTIONS = ("message", "set_flag", "clear_flag", "give_item", "take_item", "experience", "coins",
                   "take_coins", "heal", "damage", "run_trigger")
# Which field of each action names other content, and what kind it is.
_ACTION_REFERENCES = {
    "give_item": (("item", "item"),), "take_item": (("item", "item"),),
    "set_object_state": (("object", "object"),), "set_exit": (("exit", "exit"),),
    "show": (("npc_instance", "npc_instance"), ("object", "object")),
    "hide": (("npc_instance", "npc_instance"), ("object", "object")),
    "spawn_npc": (("npc", "npc"), ("room", "room")), "teleport": (("room", "room"),),
    "start_quest": (("quest", "quest"),), "complete_objective": (("objective", "objective"),),
    "run_trigger": (("trigger", "trigger"),), "message": (("room", "room"),),
    "learn_destination": (("destination", "destination"),),
    "forget_destination": (("destination", "destination"),),
    "disposition": (("npc", "npc"),),
    "reputation": (("faction", "faction"),),
}


def _whole(value, low, high):
    return type(value) is int and low <= value <= high


def _text(value, label, maximum=MAX_TEXT, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError(f"{label} must be text of {1 if required else 0} to {maximum} characters.")
    return value.strip()


def _identifier(value, label):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= mud_conditions.MAX_ID_LENGTH:
        raise ValueError(f"{label} must name a content ID.")
    return value.strip()


def phrase_key(text):
    """Compare spoken phrases without case, punctuation, or extra spaces."""
    return " ".join(re.sub(r"[^\w\s]", " ", str(text).casefold()).split())


def _action(raw, index, dungeon):
    where = f"Action {index + 1}"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object.")
    kind = raw.get("type")
    allowed = DUNGEON_ACTIONS if dungeon else ACTION_TYPES
    if kind not in allowed:
        raise ValueError(f"{where} has unsupported type {kind!r}; use one of {', '.join(allowed)}.")
    fields = {"type", "delay_seconds"}
    result = {"type": kind}
    if kind == "message":
        fields |= {"text", "to", "room"}
        target = raw.get("to", "actor")
        if target not in MESSAGE_TARGETS or (dungeon and target == "other_room"):
            raise ValueError(f"{where}: message to must be actor or room" + ("" if dungeon else " or other_room") + ".")
        result.update(text=_text(raw.get("text"), f"{where} text"), to=target)
        if target == "other_room":
            result["room"] = _identifier(raw.get("room"), f"{where} room")
        elif "room" in raw:
            raise ValueError(f"{where}: only other_room messages name a room.")
    elif kind in ("set_flag", "clear_flag"):
        fields |= {"scope", "key", "value", "add"}
        scope = raw.get("scope", "player")
        if scope not in mud_conditions.FLAG_SCOPES:
            raise ValueError(f"{where}: flag scope must be player or world.")
        result.update(scope=scope, key=mud_conditions.flag_key(raw.get("key"), f"{where} key"))
        if kind == "set_flag":
            if ("value" in raw) == ("add" in raw):
                raise ValueError(f"{where}: set_flag needs exactly one of value or add.")
            if "value" in raw:
                if not isinstance(raw["value"], str) or len(raw["value"]) > mud_conditions.MAX_FLAG_VALUE:
                    raise ValueError(f"{where}: flag values must be text up to "
                                     f"{mud_conditions.MAX_FLAG_VALUE} characters.")
                result["value"] = raw["value"]
            else:
                if not _whole(raw["add"], -1_000_000, 1_000_000) or raw["add"] == 0:
                    raise ValueError(f"{where}: add must be a nonzero whole number up to a million.")
                result["add"] = raw["add"]
        elif "value" in raw or "add" in raw:
            raise ValueError(f"{where}: clear_flag accepts only scope and key.")
    elif kind in OUTCOME_ACTIONS:
        fields |= {"item", "amount"}
        try:
            outcome = mud_conditions.validate_outcomes([{key: value for key, value in raw.items()
                                                         if key != "delay_seconds"}])[0]
        except ValueError as problem:
            raise ValueError(f"{where}: {problem}") from problem
        result.update({key: value for key, value in outcome.items() if key != "type"})
    elif kind in ("heal", "damage"):
        fields |= {"amount"}
        if not _whole(raw.get("amount"), 1, 10_000):
            raise ValueError(f"{where}: {kind} amount must be a whole number from 1 to 10,000.")
        result["amount"] = raw["amount"]
    elif kind == "set_object_state":
        fields |= {"object", "state"}
        state = raw.get("state", "")
        if not isinstance(state, str) or len(state.strip()) > 40:
            raise ValueError(f"{where}: object state must be text up to 40 characters (blank resets it).")
        result.update(object=_identifier(raw.get("object"), f"{where} object"), state=state.strip())
    elif kind == "set_exit":
        fields |= {"exit", "state", "scope"}
        if raw.get("state") not in EXIT_STATES:
            raise ValueError(f"{where}: exit state must be one of {', '.join(EXIT_STATES)}.")
        scope = raw.get("scope", "world")
        if scope not in SCOPES:
            raise ValueError(f"{where}: exit scope must be character or world.")
        result.update(exit=_identifier(raw.get("exit"), f"{where} exit"), state=raw["state"], scope=scope)
    elif kind in ("show", "hide"):
        fields |= {"npc_instance", "object"}
        named = [key for key in ("npc_instance", "object") if key in raw]
        if len(named) != 1:
            raise ValueError(f"{where}: {kind} needs exactly one of npc_instance or object.")
        result[named[0]] = _identifier(raw[named[0]], f"{where} {named[0]}")
    elif kind == "spawn_npc":
        fields |= {"npc", "room", "despawn_seconds", "max_live"}
        despawn = raw.get("despawn_seconds", 600)
        live = raw.get("max_live", 1)
        if not _whole(despawn, 10, 86_400):
            raise ValueError(f"{where}: despawn_seconds must be 10 to 86,400.")
        if not _whole(live, 1, 5):
            raise ValueError(f"{where}: max_live must be 1 to 5.")
        result.update(npc=_identifier(raw.get("npc"), f"{where} npc"),
                      room=_identifier(raw.get("room"), f"{where} room"),
                      despawn_seconds=despawn, max_live=live)
    elif kind == "teleport":
        fields |= {"room"}
        result["room"] = _identifier(raw.get("room"), f"{where} room")
    elif kind == "start_quest":
        fields |= {"quest"}
        result["quest"] = _identifier(raw.get("quest"), f"{where} quest")
    elif kind == "complete_objective":
        fields |= {"objective"}
        result["objective"] = _identifier(raw.get("objective"), f"{where} objective")
    elif kind == "run_trigger":
        fields |= {"trigger"}
        result["trigger"] = _identifier(raw.get("trigger"), f"{where} trigger")
    elif kind in ("learn_destination", "forget_destination"):
        fields |= {"destination"}
        result["destination"] = _identifier(raw.get("destination"), f"{where} destination")
    elif kind == "reputation":
        fields |= {"faction", "amount"}
        try:
            outcome = mud_conditions.validate_outcomes([{"type": kind, "faction": raw.get("faction"),
                                                         "amount": raw.get("amount")}])[0]
        except ValueError as problem:
            raise ValueError(f"{where}: {problem}") from problem
        result.update(faction=outcome["faction"], amount=outcome["amount"])
    elif kind == "disposition":
        fields |= {"npc", "amount"}
        if not _whole(raw.get("amount"), -100, 100) or raw["amount"] == 0:
            raise ValueError(f"{where}: disposition amount must be a nonzero whole number from -100 to 100.")
        result.update(npc=_identifier(raw.get("npc"), f"{where} npc"), amount=raw["amount"])
    unknown = set(raw) - fields
    if unknown:
        raise ValueError(f"{where}: {kind} does not accept {', '.join(sorted(unknown))}.")
    delay = raw.get("delay_seconds", 0)
    if not _whole(delay, 0, MAX_DELAY_SECONDS):
        raise ValueError(f"{where}: delay_seconds must be 0 to {MAX_DELAY_SECONDS}.")
    if delay:
        result["delay_seconds"] = delay
    return result


def actor_required(action):
    """True when an action changes or addresses the player who caused the event."""
    return (action["type"] in _ACTOR_ACTIONS
            or action["type"] in ("set_flag", "clear_flag") and action["scope"] == "player"
            or action["type"] == "set_exit" and action["scope"] == "character"
            or action["type"] == "message" and action["to"] == "actor")


def validate_actions(value, *, dungeon=False):
    items = mud_conditions._load(value, "Actions")
    if not 1 <= len(items) <= MAX_ACTIONS:
        raise ValueError(f"A trigger needs 1 to {MAX_ACTIONS} actions.")
    return [_action(raw, index, dungeon) for index, raw in enumerate(items)]


def validate_trigger(raw, *, dungeon=False):
    """Return a normalized trigger or raise ValueError with a readable reason."""
    if not isinstance(raw, dict):
        raise ValueError("Each trigger must be an object.")
    fields = {"id", "name", "event", "source", "phrase", "interval_seconds", "scope", "once",
              "cooldown_seconds", "max_fires", "enabled", "conditions", "fail_message", "actions"}
    unknown = set(raw) - fields
    if unknown:
        raise ValueError(f"Triggers do not accept: {', '.join(sorted(unknown))}.")
    trigger_id = raw.get("id")
    if not isinstance(trigger_id, str) or not TRIGGER_ID_RE.fullmatch(trigger_id):
        raise ValueError("Trigger id must be lowercase letters, digits, and hyphens, starting with a letter.")
    label = f"Trigger {trigger_id}"
    events = DUNGEON_EVENTS if dungeon else EVENT_TYPES
    event = raw.get("event")
    if event not in events:
        raise ValueError(f"{label}: event must be one of {', '.join(events)}.")
    result = {"id": trigger_id, "name": _text(raw.get("name", trigger_id), f"{label} name", 80),
              "event": event}
    sources = (DUNGEON_SOURCES if dungeon else EVENT_SOURCES)[event]
    source = raw.get("source") or {}
    if not isinstance(source, dict):
        raise ValueError(f"{label}: source must be an object such as {{\"room\": \"hall\"}}.")
    if sources:
        if len(source) != 1 or next(iter(source)) not in sources:
            raise ValueError(f"{label}: {event} needs a source naming one of: {', '.join(sources)}.")
        kind, ref = next(iter(source.items()))
        result["source"] = {kind: _identifier(ref, f"{label} source {kind}")}
    elif source:
        raise ValueError(f"{label}: manual triggers have no source.")
    else:
        result["source"] = {}
    if event == "say_phrase":
        phrase = phrase_key(_text(raw.get("phrase"), f"{label} phrase", 200))
        if not phrase:
            raise ValueError(f"{label} phrase must contain letters or digits.")
        result["phrase"] = phrase
    elif "phrase" in raw:
        raise ValueError(f"{label}: only say_phrase triggers have a phrase.")
    if event == "timer":
        if not _whole(raw.get("interval_seconds"), 30, 86_400):
            raise ValueError(f"{label}: timer interval_seconds must be 30 to 86,400.")
        result["interval_seconds"] = raw["interval_seconds"]
    elif "interval_seconds" in raw:
        raise ValueError(f"{label}: only timer triggers have interval_seconds.")
    scope = raw.get("scope", "world" if event == "timer" else "character")
    if scope not in SCOPES or (event == "timer" and scope != "world"):
        raise ValueError(f"{label}: scope must be character or world" + (" (timers are world)" if event == "timer" else "") + ".")
    result["scope"] = scope
    for key in ("once", "enabled"):
        value = raw.get(key, key == "enabled")
        if type(value) is not bool:
            raise ValueError(f"{label}: {key} must be true or false.")
        result[key] = value
    cooldown = raw.get("cooldown_seconds", 0)
    if not _whole(cooldown, 0, 604_800):
        raise ValueError(f"{label}: cooldown_seconds must be 0 to 604,800 (one week).")
    result["cooldown_seconds"] = cooldown
    max_fires = raw.get("max_fires")
    if max_fires is not None and not _whole(max_fires, 1, 1_000_000):
        raise ValueError(f"{label}: max_fires must be blank or a whole number from 1.")
    result["max_fires"] = max_fires
    try:
        result["conditions"] = mud_conditions.validate_conditions(raw.get("conditions"), allow_quests=not dungeon)
        result["actions"] = validate_actions(raw.get("actions"), dungeon=dungeon)
    except ValueError as problem:
        raise ValueError(f"{label}: {problem}") from problem
    result["fail_message"] = _text(raw.get("fail_message"), f"{label} fail_message", 200, required=False)
    if event == "timer":
        if any(actor_required(action) for action in result["actions"]):
            raise ValueError(f"{label}: timers have no player, so they cannot give, take, heal, damage, "
                             "teleport, teach destinations, message the actor, or change player flags.")
        if any(condition.get("type") not in ("flag", "object_state", "time") or condition.get("scope") == "player"
               for condition in _flatten(result["conditions"])):
            raise ValueError(f"{label}: timer conditions may only check world flags, object states, and the time.")
        if result["fail_message"]:
            raise ValueError(f"{label}: timers have no player to show a fail_message to.")
    return result


def _flatten(conditions):
    for condition in conditions:
        yield from condition.get("any", [condition])


def check_chains(triggers):
    """Reject run_trigger links to unknown or non-manual triggers, and loops."""
    by_id = {trigger["id"]: trigger for trigger in triggers}
    links = {}
    for trigger in triggers:
        targets = [action["trigger"] for action in trigger["actions"] if action["type"] == "run_trigger"]
        for target in targets:
            if target not in by_id:
                raise ValueError(f"Trigger {trigger['id']}: run_trigger names unknown trigger {target!r}.")
            if by_id[target]["event"] != "manual":
                raise ValueError(f"Trigger {trigger['id']}: run_trigger may only run manual triggers.")
        links[trigger["id"]] = targets
    visiting, done = set(), set()

    def visit(node):
        if node in done:
            return
        if node in visiting:
            raise ValueError(f"Triggers run each other in a loop through {node!r}.")
        visiting.add(node)
        for target in links.get(node, ()):
            visit(target)
        visiting.discard(node)
        done.add(node)

    for node in links:
        visit(node)


def validate_list(value, *, dungeon=False):
    """Normalize a list of triggers with unique IDs and loop-free chains."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_TRIGGERS:
        raise ValueError(f"triggers must be an array of at most {MAX_TRIGGERS} triggers.")
    triggers = [validate_trigger(raw, dungeon=dungeon) for raw in value]
    ids = [trigger["id"] for trigger in triggers]
    if len(set(ids)) != len(ids):
        raise ValueError("Trigger IDs must be unique.")
    check_chains(triggers)
    return triggers


def validate_document(value):
    """Validate a world package's events.json: {"triggers": [...]}."""
    if value is None:
        return []
    if not isinstance(value, dict) or set(value) != {"triggers"}:
        raise ValueError('events.json must be an object with a "triggers" array.')
    return validate_list(value["triggers"])


def references(trigger):
    """Yield (kind, id) for every piece of content a trigger names.

    Kinds are those of ``mud_conditions.references`` plus exit, npc,
    npc_instance, response, and trigger.
    """
    for kind, ref in trigger["source"].items():
        yield kind, ref
    yield from mud_conditions.references(trigger["conditions"])
    for action in trigger["actions"]:
        for field, kind in _ACTION_REFERENCES.get(action["type"], ()):
            if field in action:
                yield kind, action[field]


def remap(trigger, mapper):
    """Rewrite content references with mapper(kind, id)."""
    result = dict(trigger)
    result["source"] = {kind: mapper(kind, ref) for kind, ref in trigger["source"].items()}
    result["conditions"] = mud_conditions.remap(trigger["conditions"], mapper)
    actions = []
    for action in trigger["actions"]:
        action = dict(action)
        for field, kind in _ACTION_REFERENCES.get(action["type"], ()):
            if field in action:
                action[field] = mapper(kind, action[field])
        actions.append(action)
    result["actions"] = actions
    return result


def describe(trigger):
    """Short builder summary such as 'use_object → set_exit, message'."""
    limits = []
    if trigger.get("once"):
        limits.append(f"once per {trigger['scope']}")
    if trigger.get("cooldown_seconds"):
        limits.append(f"{trigger['cooldown_seconds']}s cooldown")
    if trigger.get("max_fires"):
        limits.append(f"max {trigger['max_fires']}")
    summary = f"{trigger['event']} → " + ", ".join(action["type"] for action in trigger["actions"])
    return summary + (f" ({'; '.join(limits)})" if limits else "")
