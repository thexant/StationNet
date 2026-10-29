"""World triggers: events, conditions, actions, persistence, packages, and shared play."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import uuid

import pytest

from mud_world_helpers import activate_world, url
from stationnet import create_app, character_creation, dungeons, mud, mud_triggers, mud_world_dungeons, mud_worlds
from stationnet.db import get_db


def _trigger(**fields):
    return {"id": "t", "event": "enter_room", "source": {"room": "hall"},
            "actions": [{"type": "message", "text": "Hi."}], **fields}


def _document(world_id="trigworld"):
    return {
        "id": world_id, "name": "Trigger World", "author": "Test", "start_room": "hall",
        "rooms": [{"id": room, "name": room.replace("-", " ").title(), "description": f"The {room}."}
                  for room in ("hall", "vault", "crypt", "tower", "garden", "mirror-a", "mirror-b")],
        "exits": [
            {"id": "vault-door", "from": "hall", "to": "vault", "name": "vault door", "direction": "north",
             "hidden": True},
            {"from": "vault", "to": "hall", "name": "hall", "direction": "south"},
            {"id": "crypt-gate", "from": "hall", "to": "crypt", "name": "crypt gate", "direction": "east",
             "hidden": True},
            {"from": "crypt", "to": "hall", "name": "hall", "direction": "west"},
            {"from": "hall", "to": "tower", "name": "stairs", "direction": "up",
             "conditions": [{"type": "flag", "scope": "world", "key": "warden.slain", "value": "yes"}],
             "conditions_message": "A ward blocks the stairs."},
            {"from": "tower", "to": "hall", "name": "stairs", "direction": "down"},
            {"from": "hall", "to": "garden", "name": "garden", "direction": "west"},
            {"from": "garden", "to": "hall", "name": "hall", "direction": "east"},
            {"from": "hall", "to": "mirror-a", "name": "mirror", "direction": "in"},
        ],
        "content": {
            "mud_item_definitions": [
                {"id": "lever", "name": "Rusty Lever", "description": "A lever.", "item_type": "misc",
                 "properties_json": {"fixed": True, "state_descriptions": {
                     "": "A rusty lever points up.", "down": "The rusty lever is pulled down."}}},
                {"id": "bell", "name": "Bronze Bell", "description": "A bell.", "item_type": "misc",
                 "properties_json": {"fixed": True}},
                {"id": "gem", "name": "Red Gem", "description": "A gem.", "item_type": "misc"},
                {"id": "ring", "name": "Gold Ring", "description": "A ring.", "item_type": "misc"}],
            "mud_item_instances": [
                {"id": "lever-1", "definition_id": "lever", "room_id": "hall"},
                {"id": "bell-1", "definition_id": "bell", "room_id": "vault"},
                {"id": "gem-1", "definition_id": "gem", "room_id": "garden"}],
            "mud_conversations": [{"id": "sage-talk", "name": "Sage"}],
            "mud_dialogue_nodes": [{"id": "sage-start", "conversation_id": "sage-talk", "node_key": "start",
                                    "body": "Speak.", "is_start": 1}],
            "mud_dialogue_responses": [{"id": "summon", "node_id": "sage-start", "response_text": "Summon a ghost."}],
            "mud_npc_definitions": [
                {"id": "sage", "name": "Sage", "description": "A sage.", "conversation_id": "sage-talk"},
                {"id": "ghost", "name": "Ghost", "description": "A ghost."},
                {"id": "guard", "name": "Hidden Guard", "description": "A guard."},
                {"id": "warden", "name": "Warden", "description": "A warden.",
                 "behaviors_json": {"passive": True}}],
            "mud_npc_combat_profiles": [{"npc_definition_id": "warden", "maximum_health": 3, "attack": 1,
                                         "defense": 0, "experience_reward": 1, "respawn_seconds": 60}],
            "mud_npc_instances": [
                {"id": "sage-1", "definition_id": "sage", "room_id": "hall"},
                {"id": "guard-1", "definition_id": "guard", "room_id": "hall", "state_json": {"hidden": True}},
                {"id": "warden-1", "definition_id": "warden", "room_id": "garden"}],
            "mud_quests": [{"id": "pilgrim", "name": "Pilgrimage", "description": "Pray in the crypt."}],
            "mud_quest_objectives": [{"id": "pray", "quest_id": "pilgrim", "objective_type": "script",
                                      "target_id": "script", "description": "Pray at the altar"}],
            "mud_quest_rewards": [{"id": "pilgrim-coins", "quest_id": "pilgrim", "reward_type": "coins",
                                   "amount": 7}],
        },
        "events": [
            {"id": "welcome", "event": "enter_room", "source": {"room": "garden"}, "once": True,
             "actions": [{"type": "message", "text": "Welcome to the garden."},
                         {"type": "experience", "amount": 5}]},
            {"id": "pull-lever", "name": "Pull the lever", "event": "use_object", "source": {"object": "lever-1"},
             "scope": "world", "once": True,
             "conditions": [{"type": "attribute", "attribute": "strength", "min": 11}],
             "fail_message": "The lever will not budge.",
             "actions": [{"type": "set_object_state", "object": "lever-1", "state": "down"},
                         {"type": "set_exit", "exit": "vault-door", "state": "open"},
                         {"type": "message", "to": "room", "text": "Gears grind somewhere below."},
                         {"type": "show", "npc_instance": "guard-1"},
                         {"type": "give_item", "item": "ring"}]},
            {"id": "password", "event": "say_phrase", "source": {"room": "hall"}, "phrase": "Open, Sesame!",
             "actions": [{"type": "set_exit", "exit": "crypt-gate", "state": "open", "scope": "character"},
                         {"type": "message", "text": "The crypt gate swings open for you."}]},
            {"id": "warden-slain", "event": "defeat_npc", "source": {"npc": "warden"}, "scope": "world", "once": True,
             "actions": [{"type": "set_flag", "scope": "world", "key": "warden.slain", "value": "yes"},
                         {"type": "message", "to": "other_room", "room": "hall", "text": "The ward on the stairs fades."}]},
            {"id": "summon", "event": "dialogue_choice", "source": {"response": "summon"},
             "actions": [{"type": "spawn_npc", "npc": "ghost", "room": "hall", "despawn_seconds": 60}]},
            {"id": "gem-trap", "event": "take_item", "source": {"item": "gem"},
             "actions": [{"type": "damage", "amount": 500},
                         {"type": "set_flag", "key": "gems.taken", "add": 1}]},
            {"id": "crypt-rite", "event": "enter_room", "source": {"room": "crypt"}, "once": True,
             "actions": [{"type": "start_quest", "quest": "pilgrim"},
                         {"type": "complete_objective", "objective": "pray"},
                         {"type": "run_trigger", "trigger": "bless"}]},
            {"id": "bless", "event": "manual",
             "actions": [{"type": "message", "text": "You feel blessed."}]},
            {"id": "pilgrim-done", "event": "quest_complete", "source": {"quest": "pilgrim"},
             "actions": [{"type": "set_flag", "key": "pilgrim", "value": "done"}]},
            {"id": "echo", "event": "enter_room", "source": {"room": "vault"},
             "actions": [{"type": "message", "text": "An echo answers.", "delay_seconds": 5}]},
            {"id": "chime", "event": "timer", "source": {"room": "hall"}, "interval_seconds": 60,
             "conditions": [{"type": "object_state", "object": "lever-1", "state": "down"}],
             "actions": [{"type": "message", "to": "room", "text": "A distant chime."}]},
            {"id": "mirror-a", "event": "enter_room", "source": {"room": "mirror-a"},
             "actions": [{"type": "teleport", "room": "mirror-b"}]},
            {"id": "mirror-b", "event": "enter_room", "source": {"room": "mirror-b"},
             "actions": [{"type": "teleport", "room": "mirror-a"}]},
            {"id": "ring-bell", "event": "use_object", "source": {"object": "bell-1"}, "cooldown_seconds": 300,
             "actions": [{"type": "set_flag", "scope": "world", "key": "rings", "add": 1},
                         {"type": "run_trigger", "trigger": "chain-1"}]},
            *({"id": f"chain-{n}", "event": "manual",
               "actions": [{"type": "message", "text": f"Chain {n}."}]
               + ([{"type": "run_trigger", "trigger": f"chain-{n + 1}"}] if n < 5 else [])}
              for n in range(1, 6)),
        ],
    }


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_triggers_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        users = []
        for name, admin in (("one", 1), ("two", 0), ("three", 0)):
            uid = db.execute("INSERT INTO users(username,callsign,password_hash,connection_id,is_admin) "
                             "VALUES(?,?,?,?,?)", (name, name.upper(), "unused", f"connection-{name}", admin)).lastrowid
            character_creation.complete(db, uid, {
                "name": name.title(), "race": "human", "class": "warrior",
                "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                "skills": ["lore", "guard"]})
            users.append(uid)
        db.commit()
        mud_worlds.install(db, mud_worlds.package_bytes(_document()))
        activate_world(db, 'trigworld')
        for uid in users:
            _move(db, uid, "hall")
        db.commit()
    yield app, users, data
    _close(app)
    shutil.rmtree(data)


def _close(app):
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()


def _room(db, local):
    return db.execute("SELECT id FROM mud_world_rooms WHERE world_id='trigworld' AND id LIKE ?",
                      (f"%:room:{local}",)).fetchone()["id"]


def _move(db, uid, local):
    """Place a player and mark them connected there, so room messages reach them."""
    room = _room(db, local)
    mud._set_player_location(db, uid, "traditional", "world_room", room)
    db.execute("DELETE FROM mud_presence WHERE user_id=?", (uid,))
    db.execute("INSERT INTO mud_presence(world_key,user_id,connection_id,client_id,room_id,location_type,location_key,last_seen) "
               "VALUES(mud_world(),?,?,?,?,?,?,?)", (uid, f"connection-{uid}", f"client-{uid}-1", room, "world_room", room,
                                         mud._scheduled_now()))


def _user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _bodies(drafts, uid=None):
    """Bodies a given player would receive, or every body when uid is None."""
    return [draft.body for draft in drafts
            if uid is None or draft.recipient_ids is None or uid in draft.recipient_ids]


def _flag(db, key, uid=None, realm="trigworld"):
    scope = f"stationnet:character:{uid}" if uid else ""
    return mud._script_flag(db, realm, scope, key)


def _exits(db, uid):
    return {row["name"] for row in mud._room_exits(db, mud._current_room(db, uid)["id"], viewer_id=uid)}


def _ring_count(db, uid):
    return db.execute("SELECT COUNT(*) FROM mud_item_instances i JOIN mud_item_definitions d ON d.id=i.definition_id "
                      "WHERE d.name='Gold Ring' AND i.owner_character_id=?", (f"stationnet:character:{uid}",)).fetchone()[0]


# Validation -----------------------------------------------------------------

@pytest.mark.parametrize("trigger, problem", [
    (_trigger(event="explode"), "event must be one of"),
    (_trigger(source={"object": "x"}), "needs a source naming one of: room"),
    (_trigger(actions=[{"type": "summon_dragon"}]), "unsupported type"),
    (_trigger(actions=[]), "1 to 10 actions"),
    (_trigger(event="timer", interval_seconds=60, actions=[{"type": "give_item", "item": "x"}]), "timers have no player"),
    (_trigger(event="timer", interval_seconds=10), "interval_seconds must be 30"),
    (_trigger(event="timer", interval_seconds=60, conditions=[{"type": "level", "min": 2}],
              actions=[{"type": "message", "to": "room", "text": "Tick."}]), "timer conditions"),
    (_trigger(actions=[{"type": "set_flag", "key": "Bad Key!", "value": "x"}]), "lowercase letters"),
    (_trigger(actions=[{"type": "set_flag", "key": "a", "value": "x", "add": 1}]), "exactly one of value or add"),
    (_trigger(actions=[{"type": "message", "text": "x", "delay_seconds": 9999}]), "delay_seconds"),
    (_trigger(phrase="hello"), "only say_phrase"),
    (_trigger(event="manual"), "manual triggers have no source"),
    (_trigger(conditions=[{"type": "flag", "scope": "team", "key": "a"}]), "scope player or world"),
])
def test_invalid_triggers_explain_the_problem(trigger, problem):
    with pytest.raises(ValueError, match=problem):
        mud_triggers.validate_trigger(trigger)


def test_trigger_chains_must_name_manual_triggers_without_loops():
    loop = [{"id": "a", "event": "manual", "actions": [{"type": "run_trigger", "trigger": "b"}]},
            {"id": "b", "event": "manual", "actions": [{"type": "run_trigger", "trigger": "a"}]}]
    with pytest.raises(ValueError, match="loop"):
        mud_triggers.validate_list(loop)
    with pytest.raises(ValueError, match="manual"):
        mud_triggers.validate_list([_trigger(id="a", actions=[{"type": "run_trigger", "trigger": "b"}]), _trigger(id="b")])
    with pytest.raises(ValueError, match="unique"):
        mud_triggers.validate_list([_trigger(), _trigger()])
    assert mud_triggers.phrase_key("  Open,   SESAME! ") == "open sesame"


def test_packages_reject_events_naming_missing_content(game):
    app, _users, _data = game
    with app.app_context():
        db = get_db()
        for broken in (_trigger(source={"room": "nowhere"}),
                       _trigger(actions=[{"type": "set_exit", "exit": "missing-exit", "state": "open"}]),
                       _trigger(actions=[{"type": "give_item", "item": "no-such-item"}])):
            document = {**_document("broken"), "events": [broken]}
            with pytest.raises(mud_worlds.WorldError, match="unknown"):
                mud_worlds.install(db, mud_worlds.package_bytes(document))
        bad_properties = _document("props")
        bad_properties["content"]["mud_item_definitions"][0]["properties_json"] = {"fixed": "yes"}
        with pytest.raises(mud_worlds.WorldError, match="fixed"):
            mud_worlds.install(db, mud_worlds.package_bytes(bad_properties))


# Interactions -----------------------------------------------------------------

def test_lever_changes_shared_world_state_and_rewards_only_the_first_player(game):
    app, (one, two, three), _data = game
    with app.app_context():
        db = get_db()
        assert "vault door" not in _exits(db, one)
        assert "A rusty lever points up." in mud._look_action(db, _user(db, one), {})[0].body
        assert "Hidden Guard" not in mud._look_action(db, _user(db, one), {})[0].body
        with pytest.raises(mud.ActionError, match="fixed in place"):
            mud._get_action(db, _user(db, one), {"arguments": ["Rusty Lever"]})

        drafts = mud._interact_action(db, _user(db, one), {"arguments": [":pull", "Rusty Lever"]})
        assert "One pulls Rusty Lever." in _bodies(drafts, two)
        assert "Gears grind somewhere below." in _bodies(drafts, one)
        assert "Gears grind somewhere below." in _bodies(drafts, two)
        assert "You receive 1 × Gold Ring." in _bodies(drafts, one)
        assert "You receive 1 × Gold Ring." not in _bodies(drafts, two)
        # Every player now sees the shared result.
        look = mud._look_action(db, _user(db, two), {})[0].body
        assert "The rusty lever is pulled down." in look and "Hidden Guard" in look
        assert "vault door" in _exits(db, two)
        # A world-once trigger never rewards a second player or a repeat pull.
        second = mud._interact_action(db, _user(db, two), {"arguments": [":pull", "Rusty Lever"]})
        assert _bodies(second, two)[-1] == "Nothing happens."
        mud._use_action(db, _user(db, one), {"arguments": ["Rusty Lever"]})
        assert _ring_count(db, one) == 1 and _ring_count(db, two) == 0


def test_conditions_gate_triggers_and_show_the_fail_message(game):
    app, (one, _two, _three), _data = game
    with app.app_context():
        db = get_db()
        db.execute("UPDATE mud_character_progression SET strength=5 WHERE character_id=?",
                   (f"stationnet:character:{one}",))
        drafts = mud._interact_action(db, _user(db, one), {"arguments": [":pull", "Rusty Lever"]})
        assert "The lever will not budge." in _bodies(drafts, one)
        assert mud._object_state(db, db.execute("SELECT id FROM mud_item_instances WHERE id LIKE '%lever-1'"
                                                ).fetchone()["id"]) == ""


def test_password_opens_a_door_for_the_speaker_only(game):
    app, (one, two, _three), _data = game
    with app.app_context():
        db = get_db()
        drafts = mud._say_action(db, _user(db, one), {"message": "open sesame"})
        assert "The crypt gate swings open for you." in _bodies(drafts, one)
        assert "The crypt gate swings open for you." not in _bodies(drafts, two)
        assert "crypt gate" in _exits(db, one)
        assert "crypt gate" not in _exits(db, two)
        with pytest.raises(mud.ActionError, match="no exit"):
            mud._go_action(db, _user(db, two), {"arguments": ["east"]})
        mud._say_action(db, _user(db, two), {"message": "open the door please"})
        assert "crypt gate" not in _exits(db, two)


def test_entering_a_room_runs_quest_actions_chains_and_completion_triggers(game):
    app, (one, _two, _three), _data = game
    with app.app_context():
        db = get_db()
        mud._say_action(db, _user(db, one), {"message": "Open sesame"})
        drafts = mud._go_action(db, _user(db, one), {"arguments": ["east"]})
        bodies = _bodies(drafts, one)
        assert any(body.startswith("Quest accepted: Pilgrimage") for body in bodies)
        assert any(body.startswith("Quest complete: Pilgrimage") and "7 coins" in body for body in bodies)
        assert "You feel blessed." in bodies
        assert _flag(db, "pilgrim", one) == "done"
        # The rite is once per character: walking back in does nothing new.
        mud._go_action(db, _user(db, one), {"arguments": ["west"]})
        again = _bodies(mud._go_action(db, _user(db, one), {"arguments": ["east"]}), one)
        assert "You feel blessed." not in again


def test_defeating_an_npc_sets_a_world_flag_that_opens_an_exit_elsewhere(game):
    app, (one, two, _three), _data = game
    with app.app_context():
        db = get_db()
        with pytest.raises(mud.ActionError, match="ward blocks"):
            mud._go_action(db, _user(db, two), {"arguments": ["up"]})
        _move(db, one, "garden")
        bodies = []
        for _ in range(20):
            drafts = mud._attack_action(db, _user(db, one), {"arguments": ["Warden"]})
            bodies += _bodies(drafts)
            if "Warden is defeated." in bodies:
                break
        assert "Warden is defeated." in bodies
        assert _flag(db, "warden.slain") == "yes"
        assert any("The ward on the stairs fades." in draft.body and two in draft.recipient_ids
                   for draft in drafts if draft.recipient_ids)
        assert any("You go up" in body for body in _bodies(mud._go_action(db, _user(db, two), {"arguments": ["up"]})))


def test_dialogue_choice_spawns_a_temporary_npc_that_despawns(game):
    app, (one, two, _three), _data = game
    with app.app_context():
        db = get_db()
        mud._talk_action(db, _user(db, one), {"arguments": ["Sage"]})
        drafts = mud._respond_action(db, _user(db, one), {"arguments": ["1"]})
        assert "Ghost appears." in _bodies(drafts, two)
        assert any(row["name"] == "Ghost" for row in mud._room_npc_rows(db, _room(db, "hall")))
        # max_live keeps a repeat choice from flooding the room.
        mud._talk_action(db, _user(db, two), {"arguments": ["Sage"]})
        mud._respond_action(db, _user(db, two), {"arguments": ["1"]})
        assert sum(row["name"] == "Ghost" for row in mud._room_npc_rows(db, _room(db, "hall"))) == 1
        later = datetime.now(timezone.utc) + timedelta(seconds=120)
        mud.process_scheduled_events(db, later)
        assert not any(row["name"] == "Ghost" for row in mud._room_npc_rows(db, _room(db, "hall")))


def test_traps_cannot_kill_and_counters_accumulate(game):
    app, (one, _two, _three), _data = game
    with app.app_context():
        db = get_db()
        _move(db, one, "garden")
        drafts = mud._get_action(db, _user(db, one), {"arguments": ["Red Gem"]})
        assert any(body.startswith("You take") for body in _bodies(drafts, one))
        assert mud._character_row(db, one)["current_health"] == 1
        assert _flag(db, "gems.taken", one) == "1"
        mud._drop_action(db, _user(db, one), {"arguments": ["Red Gem"]})
        mud._get_action(db, _user(db, one), {"arguments": ["Red Gem"]})
        assert _flag(db, "gems.taken", one) == "2"
        assert mud._character_row(db, one)["current_health"] == 1


# Safety ------------------------------------------------------------------------

def test_character_once_rewards_cannot_repeat(game):
    app, (one, two, _three), _data = game
    with app.app_context():
        db = get_db()
        experience = lambda uid: mud._character_row(db, uid)["experience"]
        before = experience(one)
        assert "Welcome to the garden." in _bodies(mud._go_action(db, _user(db, one), {"arguments": ["west"]}), one)
        gained = experience(one) - before
        assert gained > 0
        mud._go_action(db, _user(db, one), {"arguments": ["east"]})
        assert "Welcome to the garden." not in _bodies(mud._go_action(db, _user(db, one), {"arguments": ["west"]}), one)
        assert experience(one) - before == gained
        # Another character still gets their own first visit.
        assert "Welcome to the garden." in _bodies(mud._go_action(db, _user(db, two), {"arguments": ["west"]}), two)


def test_cooldowns_and_chain_limits_stop_runaway_triggers(game):
    app, (one, _two, _three), _data = game
    with app.app_context():
        db = get_db()
        mud._interact_action(db, _user(db, one), {"arguments": [":pull", "Rusty Lever"]})
        mud._go_action(db, _user(db, one), {"arguments": ["north"]})
        drafts = mud._interact_action(db, _user(db, one), {"arguments": [":push", "Bronze Bell"]})
        chain = [body for body in _bodies(drafts, one) if body.startswith("Chain")]
        # The bell is depth 1, so three chained manual triggers run before the depth limit.
        assert chain == ["Chain 1.", "Chain 2.", "Chain 3."]
        assert _flag(db, "rings") == "1"
        assert _bodies(mud._interact_action(db, _user(db, one), {"arguments": [":push", "Bronze Bell"]}), one)[-1] \
            == "Nothing happens."
        assert _flag(db, "rings") == "1"
        db.execute("UPDATE mud_trigger_fires SET last_fired_at=? WHERE trigger_id LIKE '%:trigger:ring-bell'",
                   ((datetime.now(timezone.utc) - timedelta(seconds=301)).replace(microsecond=0).isoformat(),))
        mud._interact_action(db, _user(db, one), {"arguments": [":push", "Bronze Bell"]})
        assert _flag(db, "rings") == "2"


def test_teleport_loops_end_instead_of_running_forever(game):
    app, (one, _two, _three), _data = game
    with app.app_context():
        db = get_db()
        drafts = mud._go_action(db, _user(db, one), {"arguments": ["in"]})
        # Each mirror teleports to the other; the chain stops when the first would run again.
        assert mud._current_room(db, one)["id"] == _room(db, "mirror-a")
        assert len(drafts) < 40


def test_max_fires_limits_how_many_players_a_shared_trigger_serves(game):
    app, users, _data = game
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT id,definition_json FROM mud_world_triggers WHERE local_id='password'").fetchone()
        trigger = {**json.loads(row["definition_json"]), "scope": "world", "max_fires": 2}
        mud_worlds.save_trigger(db, "trigworld", row["id"], mud_triggers.validate_trigger(trigger))
        assert "max 2" in mud_triggers.describe(trigger)
        for uid in users:
            mud._say_action(db, _user(db, uid), {"message": "open sesame"})
        assert ["crypt gate" in _exits(db, uid) for uid in users] == [True, True, False]


# Timers and persistence -------------------------------------------------------------

def test_delayed_actions_and_timers_run_from_the_scheduler(game):
    app, (one, two, _three), _data = game
    with app.app_context():
        db = get_db()
        mud._interact_action(db, _user(db, one), {"arguments": [":pull", "Rusty Lever"]})
        drafts = mud._go_action(db, _user(db, one), {"arguments": ["north"]})
        assert "An echo answers." not in _bodies(drafts, one)
        mud.sync_trigger_schedule(db)
        mud.process_scheduled_events(db, datetime.now(timezone.utc) + timedelta(seconds=90))
        echoes = [row["body"] for row in mud._events_for_user(db, one)]
        assert "An echo answers." in echoes
        assert "An echo answers." not in [row["body"] for row in mud._events_for_user(db, two)]
        # The chime reaches whoever stands in the hall, now the lever is down.
        assert "A distant chime." in [row["body"] for row in mud._events_for_user(db, two)]
        assert "A distant chime." not in echoes


def test_delayed_actions_skip_players_who_left_the_world(game):
    app, (one, _two, _three), _data = game
    with app.app_context():
        db = get_db()
        mud._interact_action(db, _user(db, one), {"arguments": [":pull", "Rusty Lever"]})
        mud._go_action(db, _user(db, one), {"arguments": ["north"]})
        activate_world(db, 'stationnet')
        mud.process_scheduled_events(db, datetime.now(timezone.utc) + timedelta(seconds=90))
        assert "An echo answers." not in [row["body"] for row in mud._events_for_user(db, one)]


def test_script_state_persists_and_can_be_reset(game):
    app, (one, _two, _three), data = game
    with app.app_context():
        db = get_db()
        mud._say_action(db, _user(db, one), {"message": "open sesame"})
        mud._interact_action(db, _user(db, one), {"arguments": [":pull", "Rusty Lever"]})
        db.commit()
    reopened = create_app({"TESTING": True, "DATA_DIR": str(data)})
    try:
        with reopened.app_context():
            db = get_db()
            assert "crypt gate" in _exits(db, one) and "vault door" in _exits(db, one)
            assert "pulled down" in mud._look_action(db, _user(db, one), {})[0].body
            mud.reset_script_state(db, "trigworld")
            assert "crypt gate" not in _exits(db, one) and "vault door" not in _exits(db, one)
            assert "points up" in mud._look_action(db, _user(db, one), {})[0].body
    finally:
        _close(reopened)


# Packages --------------------------------------------------------------------------

def test_export_and_reimport_keep_triggers_with_local_ids(game):
    app, _users, _data = game
    with app.app_context():
        db = get_db()
        exported = mud_worlds.read_package(mud_worlds.export(db, "trigworld"))
        events = {trigger["id"]: trigger for trigger in exported["events"]}
        assert len(events) == len(_document()["events"])
        lever = events["pull-lever"]
        # Exports keep the IDs a world was imported with, so repeated exports and upgrades line up.
        assert lever["source"]["object"] == "lever-1"
        exit_ids = {item["id"] for item in exported["exits"] if "id" in item}
        assert next(action["exit"] for action in lever["actions"] if action["type"] == "set_exit") in exit_ids
        assert any(item.get("hidden") for item in exported["exits"])
        assert events["crypt-rite"]["actions"][2] == {"type": "run_trigger", "trigger": "bless"}
        exported["id"] = "trigcopy"
        mud_worlds.install(db, mud_worlds.package_bytes(exported))
        copies = mud_worlds.world_triggers(db, "trigcopy")
        assert len(copies) == len(events)
        copy = next(trigger for trigger in copies if trigger["id"] == "pull-lever")
        assert copy["source"]["object"].startswith("world:trigcopy:")
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (copy["source"]["object"],)).fetchone()


def test_worlds_without_events_are_unchanged(game):
    app, (one, _two, _three), _data = game
    with app.app_context():
        db = get_db()
        document = _document("plain")
        document.pop("events")
        payload = mud_worlds.package_bytes(document)
        assert "events.json" not in __import__("zipfile").ZipFile(__import__("io").BytesIO(payload)).namelist()
        mud_worlds.install(db, payload)
        assert mud_worlds.world_triggers(db, "plain") == []
        template = mud_worlds.read_package(mud_worlds.template_bytes())
        assert template["events"][0]["event"] == "enter_room"


def test_removing_a_world_removes_its_triggers_and_state(game):
    app, (one, _two, _three), _data = game
    with app.app_context():
        db = get_db()
        mud._interact_action(db, _user(db, one), {"arguments": [":pull", "Rusty Lever"]})
        # Only a disabled world can be removed; its runs and state go with it.
        mud_worlds.set_enabled(db, "trigworld", False)
        db.commit()
        mud_worlds.remove(db, "trigworld")
        for table in ("mud_world_triggers",):
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE world_id='trigworld'").fetchone()[0] == 0
        for table in ("mud_script_flags", "mud_trigger_fires", "mud_script_entity_states"):
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE realm='trigworld'").fetchone()[0] == 0


# World Builder ---------------------------------------------------------------------

def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    return client


def test_builder_creates_edits_and_removes_triggers(game):
    app, (sysop, player, _three), _data = game
    client = _client(app, sysop)
    page = client.get(url("/builder"))
    assert page.status_code == 200 and b"events-heading" in page.data and b"Pull the lever" in page.data
    with app.app_context():
        hall = _room(get_db(), "hall")
    form = {"csrf_token": "test-token", "name": "Ring the hall", "event": "enter_room", "source": f"room|{hall}",
            "scope": "character", "enabled": "1", "cooldown_seconds": "0",
            "actions": json.dumps([{"type": "message", "text": "Ding."}])}
    response = client.post(url("/builder/triggers"), data=form, follow_redirects=True)
    assert b"Trigger Ring the hall created." in response.data
    with app.app_context():
        row = get_db().execute("SELECT * FROM mud_world_triggers WHERE local_id='ring-the-hall'").fetchone()
        assert row["world_id"] == "trigworld" and row["source_id"] == hall
    bad = client.post(url("/builder/triggers"), data={**form, "name": "Broken", "actions": "[not json"},
                      follow_redirects=True)
    assert b"Actions must be valid JSON" in bad.data
    missing = client.post(url("/builder/triggers"), data={
        **form, "name": "Missing", "actions": json.dumps([{"type": "teleport", "room": "world:nowhere"}])},
        follow_redirects=True)
    assert b"unknown room" in missing.data
    updated = client.post(url(f"/builder/triggers/{row['id']}"), data={**form, "once": "1"}, follow_redirects=True)
    assert b"saved" in updated.data
    with app.app_context():
        assert json.loads(get_db().execute("SELECT definition_json FROM mud_world_triggers WHERE id=?",
                                           (row["id"],)).fetchone()[0])["once"] is True
    with app.app_context():
        bless = get_db().execute("SELECT id FROM mud_world_triggers WHERE local_id='bless'").fetchone()["id"]
    blocked = client.post(url(f"/builder/triggers/{bless}/delete"), data={"csrf_token": "test-token",
                                                                         "confirmation": "delete"},
                          follow_redirects=True)
    assert b"Remove the run_trigger actions" in blocked.data
    removed = client.post(url(f"/builder/triggers/{row['id']}/delete"),
                          data={"csrf_token": "test-token", "confirmation": "delete"}, follow_redirects=True)
    assert b"removed" in removed.data
    reset = client.post(url("/builder/triggers/reset-state"), data={"csrf_token": "test-token",
                                                                   "confirmation": "reset"}, follow_redirects=True)
    assert b"reset" in reset.data
    assert _client(app, player).post(url("/builder/triggers"), data=form).status_code == 403


def test_builder_saves_hidden_exits_and_script_objectives(game):
    app, (sysop, _player, _three), _data = game
    client = _client(app, sysop)
    with app.app_context():
        db = get_db()
        hall, garden = _room(db, "hall"), _room(db, "garden")
        quest = db.execute("SELECT id FROM mud_quests WHERE name='Pilgrimage'").fetchone()["id"]
    client.post(url("/builder/exits"), data={"csrf_token": "test-token", "from_room_id": hall, "to_room_id": garden,
                                            "name": "secret path", "hidden": "1"})
    client.post(url(f"/builder/quests/{quest}/objectives"), data={
        "csrf_token": "test-token", "objective_type": "script", "target_id": hall,
        "description": "Ring the bell", "required_count": "1"})
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT hidden FROM mud_world_exits WHERE name='secret path'").fetchone()["hidden"] == 1
        assert db.execute("SELECT target_id FROM mud_quest_objectives WHERE description='Ring the bell'"
                          ).fetchone()["target_id"] == "script"


# Procedural dungeons ------------------------------------------------------------------

def _dungeon_template(triggers):
    path = Path(dungeons.__file__).with_name("dungeon_templates") / "generic-fantasy-dungeon.json"
    template = json.loads(path.read_text(encoding="utf-8"))
    return {**{key: value for key, value in template.items() if key not in ("id", "version")}, "triggers": triggers}


def test_dungeon_templates_validate_their_triggers():
    good = [{"id": "arrive", "event": "enter_room", "source": {"room_role": "entrance"},
             "actions": [{"type": "message", "text": "The air is stale."}]}]
    assert mud_world_dungeons.template_errors(_dungeon_template(good)) == []
    for broken, problem in (
            ({**good[0], "source": {"room_role": "throne"}}, "unknown room role"),
            ({**good[0], "actions": [{"type": "teleport", "room": "x"}]}, "unsupported type"),
            ({**good[0], "event": "quest_complete"}, "event must be one of")):
        assert any(problem in error for error in mud_world_dungeons.template_errors(_dungeon_template([broken])))


def test_dungeon_triggers_fire_inside_each_run_separately(game):
    app, (one, two, _three), _data = game
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET mud_procedural_enabled=1")
        triggers = [{"id": "arrive", "event": "enter_room", "source": {"room_role": "entrance"},
                     "actions": [{"type": "message", "text": "The air is stale."},
                                 {"type": "set_flag", "scope": "world", "key": "arrivals", "add": 1},
                                 {"type": "set_flag", "key": "visited", "value": "yes"}]}]
        template_id = mud_world_dungeons.save_template(db, "trigworld", "crypt-run", "Crypt Run",
                                                       _dungeon_template(triggers))
        hall = _room(db, "hall")
        db.execute("INSERT INTO mud_world_dungeon_entrances(id,world_id,room_id,return_room_id,template_id,name,"
                   "difficulty,size,max_party) VALUES('world:dungeon-entrance:crypt','trigworld',?,?,?,"
                   "'deep crypt',1,8,1)", (hall, hall, template_id))
        runs = []
        for uid in (one, two):
            drafts = mud._enter_dungeon_action(db, _user(db, uid), {"arguments": ["deep crypt"]})
            assert "The air is stale." in _bodies(drafts, uid)
            runs.append(mud._active_expedition(db, uid)["id"])
        assert runs[0] != runs[1]
        for uid, run in zip((one, two), runs):
            assert _flag(db, "arrivals", realm=run) == "1"
            assert _flag(db, "visited", uid, realm=run) == "yes"
        assert _flag(db, "arrivals") == ""
