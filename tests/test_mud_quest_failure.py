"""Quest goals and details, failure rules, deadlines, retries, abandonment, and alternative endings."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

import pytest

from mud_world_helpers import activate_world, url
from stationnet import create_app, character_creation, mud, mud_conditions, mud_quests, mud_triggers, mud_worlds
from stationnet import db as db_module
from stationnet.db import get_db


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_questfail_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        users = []
        for name in ("one", "two"):
            uid = db.execute("INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
                             (name, name.upper(), "unused", f"connection-{name}")).lastrowid
            character_creation.complete(db, uid, {
                "name": name.title(), "race": "human", "class": "warrior",
                "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                "skills": ["lore", "guard"]})
            users.append(uid)
        db.commit()
    yield app, users
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data)


def _document(world_id="failing"):
    return {
        "id": world_id, "name": "Failing World", "author": "Test", "start_room": "square",
        "rooms": [{"id": "square", "name": "Village Square", "description": "The village square."},
                  {"id": "woods", "name": "Dark Woods", "description": "Tall dark trees."},
                  {"id": "den", "name": "Wolf Den", "description": "Bones everywhere."}],
        "exits": [{"from": "square", "to": "woods", "name": "trail", "direction": "north"},
                  {"from": "woods", "to": "square", "name": "trail", "direction": "south"}],
        "content": {
            "mud_item_definitions": [
                {"id": "pelt", "name": "Wolf Pelt", "description": "A pelt.", "item_type": "misc"},
                {"id": "torch", "name": "Torch", "description": "A torch.", "item_type": "misc"}],
            "mud_conversations": [{"id": "elder-talk", "name": "Elder"}],
            "mud_dialogue_nodes": [{"id": "elder-start", "conversation_id": "elder-talk", "node_key": "start",
                                    "body": "Wolves trouble us.", "is_start": 1}],
            "mud_npc_definitions": [
                {"id": "elder", "name": "Elder", "description": "An elder.", "conversation_id": "elder-talk"},
                {"id": "wolf", "name": "Wolf", "description": "A wolf."}],
            "mud_npc_instances": [{"id": "elder-here", "definition_id": "elder", "room_id": "square"}],
            "mud_quests": [
                {"id": "plain", "name": "Plain Errand", "description": "Visit the woods."},
                {"id": "timed", "name": "Race the Dusk", "description": "Reach the woods before dark.",
                 "summary": "Get to the woods in time", "time_limit_seconds": 600,
                 "details_json": [
                     {"kind": "background", "text": "Night falls fast here.", "reveal": "offer"},
                     {"kind": "location", "text": "The woods lie north.", "room": "woods"},
                     {"kind": "consequence", "text": "The elder will remember.", "reveal": "finished"},
                     {"kind": "note", "text": "You found the trail.",
                      "reveal": [{"type": "objective", "objective": "trail"}]}]},
                {"id": "guarded", "name": "Keep the Elder Safe", "description": "Guard the elder.",
                 "retry_mode": "retry", "retry_cooldown_seconds": 3600,
                 "fail_conditions_json": [{"when": [{"type": "flag", "scope": "world", "key": "elder.dead"}],
                                           "message": "The elder is dead"}]},
                {"id": "single", "name": "One Chance", "description": "Only once.", "retry_mode": "once"},
                {"id": "wolves", "name": "The Wolf Problem", "description": "Deal with the wolves.",
                 "completion_mode": "turn_in", "turn_in_npc_definition_id": "elder"},
                {"id": "staged", "name": "Torchlight", "description": "Light the way.", "sequential": 1}],
            "mud_quest_givers": [{"quest_id": quest, "npc_definition_id": "elder"}
                                 for quest in ("plain", "timed", "guarded", "single", "wolves", "staged")],
            "mud_quest_objectives": [
                {"id": "visit", "quest_id": "plain", "objective_type": "visit_room", "target_id": "woods",
                 "description": "Visit the woods"},
                {"id": "trail", "quest_id": "timed", "objective_type": "script", "target_id": "trail",
                 "description": "Find the trail"},
                {"id": "arrive", "quest_id": "timed", "objective_type": "visit_room", "target_id": "woods",
                 "description": "Reach the woods"},
                {"id": "secret", "quest_id": "timed", "objective_type": "visit_room", "target_id": "den",
                 "description": "Find the hidden den", "optional": 1, "hidden": 1},
                {"id": "watch", "quest_id": "guarded", "objective_type": "script", "target_id": "watch",
                 "description": "Keep watch"},
                {"id": "bonus", "quest_id": "guarded", "objective_type": "script", "target_id": "bonus",
                 "description": "Keep the torch lit", "optional": 1,
                 "fail_conditions_json": [{"when": [{"type": "flag", "scope": "world", "key": "torch.out"}]}]},
                {"id": "single-task", "quest_id": "single", "objective_type": "script", "target_id": "x",
                 "description": "Do it right"},
                {"id": "slay", "quest_id": "wolves", "objective_type": "script", "target_id": "slay",
                 "description": "Slay the pack leader", "outcome_key": "slain"},
                {"id": "spare", "quest_id": "wolves", "objective_type": "script", "target_id": "spare",
                 "description": "Drive the pack away", "outcome_key": "spared"},
                {"id": "light", "quest_id": "staged", "objective_type": "possess_item", "target_id": "torch",
                 "description": "Carry a torch", "sort_order": 1},
                {"id": "walk", "quest_id": "staged", "objective_type": "visit_room", "target_id": "woods",
                 "description": "Walk the trail", "sort_order": 2}],
            "mud_quest_rewards": [
                {"id": "slain-coins", "quest_id": "wolves", "reward_type": "coins", "amount": 30, "outcome": "slain"},
                {"id": "spared-xp", "quest_id": "wolves", "reward_type": "experience", "amount": 40,
                 "outcome": "spared"},
                {"id": "any-coins", "quest_id": "wolves", "reward_type": "coins", "amount": 5},
                {"id": "guard-fail", "quest_id": "guarded", "reward_type": "coins", "amount": 2, "outcome": "failed"}],
            "mud_dialogue_responses": [
                {"id": "insult", "node_id": "elder-start", "response_text": "You are a fool."}],
        },
        "events": [
            {"id": "insulted", "event": "dialogue_choice", "source": {"response": "insult"},
             "actions": [{"type": "fail_quest", "quest": "single", "message": "The elder will not forgive you."}]},
            {"id": "guard-lost", "event": "quest_failed", "source": {"quest": "guarded"},
             "actions": [{"type": "set_flag", "scope": "player", "key": "guard.failed", "add": 1}]},
        ],
    }


def _install(db, users, document=None):
    document = document or _document()
    mud_worlds.install(db, mud_worlds.package_bytes(document))
    activate_world(db, document["id"])
    rooms = {row["name"]: row["id"] for row in db.execute(
        "SELECT id,name FROM mud_world_rooms WHERE world_id=?", (document["id"],))}
    for uid in users:
        mud._set_player_location(db, uid, "traditional", "world_room", rooms["Village Square"])
    db.commit()
    return rooms


def _user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _id(db, name, table="mud_quests"):
    return db.execute(f"SELECT id FROM {table} WHERE name=?", (name,)).fetchone()[0]


def _objective_id(db, description):
    return db.execute("SELECT id FROM mud_quest_objectives WHERE description=?", (description,)).fetchone()[0]


def _quest(db, uid, name, status="active"):
    return next(quest for quest in mud.quest_state(db, uid)[status] if quest["name"] == name)


def _row(db, uid, name):
    return db.execute("SELECT * FROM mud_player_quests WHERE character_id=? AND quest_id=?",
                      (mud._character_row(db, uid)["id"], _id(db, name))).fetchone()


def _complete(db, uid, description):
    objective = db.execute("SELECT * FROM mud_quest_objectives WHERE description=?", (description,)).fetchone()
    return mud._set_objective_progress(db, _user(db, uid), objective["objective_type"], objective["target_id"],
                                       objective["id"])


def _world_flag(db, key, value="yes"):
    mud._store_script_flag(db, "failing", "", key, value)


def _bodies(events):
    return "\n".join(event.body for event in events)


def test_plain_quests_keep_their_old_behavior(game):
    app, users = game
    with app.app_context():
        db = get_db()
        rooms = _install(db, users)
        user = _user(db, users[0])
        mud._accept_quest(db, user, _id(db, "Plain Errand"))
        row = _row(db, users[0], "Plain Errand")
        assert (row["deadline_at"], row["attempts"], row["status"]) == (None, 1, "active")
        assert mud._check_quest_failures(db, user) == []
        events = mud._set_objective_progress(db, user, "visit_room", rooms["Dark Woods"])
        assert "Quest complete: Plain Errand" in _bodies(events)
        assert _row(db, users[0], "Plain Errand")["outcome"] == "success"
        exported = mud_worlds.read_package(mud_worlds.export(db, "failing"))
        plain = next(row for row in exported["content"]["mud_quests"] if row["name"] == "Plain Errand")
        # Untouched optional fields stay out of the package, so older stations can read it.
        assert not {"summary", "time_limit_seconds", "retry_mode", "details_json", "fail_conditions_json"} & set(plain)


def test_details_and_hidden_objectives_are_revealed_in_stages(game):
    app, users = game
    with app.app_context():
        db = get_db()
        rooms = _install(db, users)
        user = _user(db, users[0])
        offer = "\n".join(mud._offer_lines(db, user, db.execute(
            "SELECT * FROM mud_quests WHERE id=?", (_id(db, "Race the Dusk"),)).fetchone()))
        assert "Goal: Get to the woods in time" in offer and "Time limit: 10m" in offer
        assert "Night falls fast" in offer and "woods lie north" not in offer
        accepted = _bodies(mud._accept_quest(db, user, _id(db, "Race the Dusk")))
        assert "Goal: Get to the woods in time" in accepted and "You have 10m" in accepted
        quest = _quest(db, users[0], "Race the Dusk")
        assert [detail["label"] for detail in quest["details"]] == ["Background", "Where"]
        assert quest["details"][1]["room_name"] == "Dark Woods"
        assert {objective["description"] for objective in quest["objectives"]} == {"Find the trail", "Reach the woods"}
        assert 0 < quest["seconds_left"] <= 600 and quest["time_left"]
        assert "fail_conditions_json" not in json.dumps(mud.quest_state(db, users[0]))
        _complete(db, users[0], "Find the trail")
        quest = _quest(db, users[0], "Race the Dusk")
        assert "You found the trail." in [detail["text"] for detail in quest["details"]]
        assert [(objective["description"], objective["state"]) for objective in quest["objectives"]] == [
            ("Reach the woods", "current"), ("Find the trail", "done")]
        info = _bodies(mud._quests_action(db, user, {"arguments": ["info", "Race", "the", "Dusk"]}))
        assert "Current tasks:" in info and "Finished tasks:" in info and "From: Elder" in info
        assert "hidden den" not in info
        mud._set_objective_progress(db, user, "visit_room", rooms["Wolf Den"])
        assert "Find the hidden den" in [o["description"] for o in _quest(db, users[0], "Race the Dusk")["objectives"]]
        mud._set_objective_progress(db, user, "visit_room", rooms["Dark Woods"])
        done = _quest(db, users[0], "Race the Dusk", "completed")
        assert "The elder will remember." in [detail["text"] for detail in done["details"]]


def test_time_limit_fails_overdue_quests_and_blocks_late_progress(game):
    app, users = game
    with app.app_context():
        db = get_db()
        rooms = _install(db, users)
        user = _user(db, users[0])
        mud._accept_quest(db, user, _id(db, "Race the Dusk"))
        assert db.execute("SELECT 1 FROM mud_scheduled_events WHERE event_kind='quest_deadline'").fetchone()
        db.execute("UPDATE mud_player_quests SET deadline_at=? WHERE quest_id=?",
                   ((datetime.now(timezone.utc) - timedelta(seconds=5)).replace(microsecond=0).isoformat(),
                    _id(db, "Race the Dusk")))
        events = mud._check_quest_failures(db, user, deadlines_only=True)
        assert "Quest failed: Race the Dusk. Time ran out. You may accept it again." in _bodies(events)
        assert db.execute("SELECT 1 FROM mud_scheduled_events WHERE event_kind='quest_deadline'").fetchone() is None
        # Work done after failing never completes the quest.
        mud._set_objective_progress(db, user, "visit_room", rooms["Dark Woods"])
        assert _row(db, users[0], "Race the Dusk")["status"] == "failed"
        assert mud._check_quest_failures(db, user) == []
        assert _quest(db, users[0], "Race the Dusk", "failed")["fail_reason"] == "Time ran out."


def test_scheduled_deadline_fails_the_quest_for_an_absent_player(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        mud._accept_quest(db, _user(db, users[0]), _id(db, "Race the Dusk"))
        db.execute("UPDATE mud_player_quests SET deadline_at='2000-01-01T00:00:00+00:00'")
        db.execute("UPDATE mud_scheduled_events SET due_at='2000-01-01T00:00:00+00:00' WHERE event_kind='quest_deadline'")
        mud.process_scheduled_events(db, datetime.now(timezone.utc))
        assert _row(db, users[0], "Race the Dusk")["status"] == "failed"
        body = db.execute("SELECT body FROM mud_events WHERE body LIKE 'Quest failed:%'").fetchone()["body"]
        assert "Time ran out" in body


def test_overdue_quest_fails_before_the_next_action_runs(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        mud._accept_quest(db, _user(db, users[0]), _id(db, "Race the Dusk"))
        db.execute("UPDATE mud_player_quests SET deadline_at='2000-01-01T00:00:00+00:00'")
        db.commit()
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = users[0]
        state["csrf_token"] = "test-token"
    payload = client.post(url("/actions"), data={"csrf_token": "test-token", "command": "/look"}).get_json()
    assert any("Quest failed: Race the Dusk" in event["body"] for event in payload["events"])
    assert [quest["name"] for quest in payload["quests"]["failed"]] == ["Race the Dusk"]


def test_failure_rules_consequences_cooldown_and_retry(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        user = _user(db, users[0])
        quest_id = _id(db, "Keep the Elder Safe")
        mud._accept_quest(db, user, quest_id)
        _world_flag(db, "torch.out")
        events = _bodies(mud._check_quest_failures(db, user))
        assert "Task failed (Keep the Elder Safe): Keep the torch lit." in events
        assert _row(db, users[0], "Keep the Elder Safe")["status"] == "active"
        _world_flag(db, "elder.dead")
        coins = mud._wallet(db, mud._character_row(db, users[0])["id"])
        events = _bodies(mud._check_quest_failures(db, user))
        assert "Quest failed: Keep the Elder Safe. The elder is dead. You may try again in" in events
        assert "You receive 2 coins." in events
        assert mud._wallet(db, mud._character_row(db, users[0])["id"]) == coins + 2
        assert mud._script_flag(db, "failing", mud._character_row(db, users[0])["id"], "guard.failed") == "1"
        context = mud._ConditionContext(db, users[0])
        assert mud_conditions.evaluate([{"type": "quest", "quest": quest_id, "status": "failed"}], context)
        assert mud_conditions.evaluate([{"type": "quest", "quest": quest_id, "status": "failed",
                                         "outcome": "failed"}], context)
        # A failed quest cannot be progressed or completed by a trigger.
        _complete(db, users[0], "Keep watch")
        assert _row(db, users[0], "Keep the Elder Safe")["status"] == "failed"
        with pytest.raises(mud.ActionError, match="try Keep the Elder Safe again in 1h"):
            mud._accept_quest(db, user, quest_id)
        db.execute("UPDATE mud_player_quests SET failed_at=datetime('now','-2 hours') WHERE quest_id=?", (quest_id,))
        _world_flag(db, "elder.dead", "")
        _world_flag(db, "torch.out", "")
        assert "Attempt 2." in _bodies(mud._accept_quest(db, user, quest_id))
        row = _row(db, users[0], "Keep the Elder Safe")
        assert (row["status"], row["attempts"], row["failed_at"], row["fail_reason"]) == ("active", 2, None, None)
        assert all(objective["state"] == "current" for objective in _quest(db, users[0], "Keep the Elder Safe")["objectives"])
        # Failure consequences paid once, however often the quest fails.
        _world_flag(db, "elder.dead")
        events = _bodies(mud._check_quest_failures(db, user))
        assert "Quest failed" in events and "You receive" not in events


def test_trigger_fails_one_attempt_quest_for_good(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        user = _user(db, users[0])
        mud._accept_quest(db, user, _id(db, "One Chance"))
        mud._talk_action(db, user, {"arguments": ["Elder"]})
        events = _bodies(mud._respond_action(db, user, {"arguments": ["You", "are", "a", "fool."]}))
        assert ("Quest failed: One Chance. The elder will not forgive you. It cannot be attempted again."
                in events)
        with pytest.raises(mud.ActionError, match="cannot be attempted again"):
            mud._accept_quest(db, user, _id(db, "One Chance"))
        assert "One Chance" not in [quest["name"] for quest in mud._available_quests(
            db, user, mud._current_room(db, users[0])["id"])]
        history = _bodies(mud._quests_action(db, user, {"arguments": ["history"]}))
        assert "✗ One Chance (failed)" in history
        assert "One Chance" in _bodies(mud._quests_action(db, user, {"arguments": ["failed"]}))


def test_abandoning_keeps_a_record_and_still_reads_as_not_started(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        user = _user(db, users[0])
        plain = _id(db, "Plain Errand")
        mud._accept_quest(db, user, plain)
        events = _bodies(mud._quests_action(db, user, {"arguments": ["abandon", "Plain", "Errand"]}))
        assert "Quest abandoned: Plain Errand" in events
        assert _row(db, users[0], "Plain Errand")["status"] == "abandoned"
        context = mud._ConditionContext(db, users[0])
        assert mud_conditions.evaluate([{"type": "quest", "quest": plain, "status": "not_started"}], context)
        assert mud_conditions.evaluate([{"type": "quest", "quest": plain, "status": "abandoned"}], context)
        mud._accept_quest(db, user, plain)
        assert _row(db, users[0], "Plain Errand")["attempts"] == 2
        mud._accept_quest(db, user, _id(db, "One Chance"))
        closing = _bodies(mud._abandon_quest(db, user, db.execute(
            "SELECT * FROM mud_quests WHERE id=?", (_id(db, "One Chance"),)).fetchone()))
        assert "It cannot be attempted again." in closing
        with pytest.raises(mud.ActionError, match="cannot be attempted again"):
            mud._accept_quest(db, user, _id(db, "One Chance"))


def test_alternative_endings_pay_their_own_rewards_at_turn_in(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        for uid, path in ((users[0], "Slay the pack leader"), (users[1], "Drive the pack away")):
            user = _user(db, uid)
            mud._accept_quest(db, user, _id(db, "The Wolf Problem"))
            # Neither ending alone is a default path, so the quest waits for one.
            assert not _quest(db, uid, "The Wolf Problem")["ready"]
            assert "Objectives complete: The Wolf Problem" in _bodies(_complete(db, uid, path))
            coins = mud._wallet(db, mud._character_row(db, uid)["id"])
            done = _bodies(mud._turn_in_quest(db, user, _id(db, "The Wolf Problem")))
            assert "Quest complete: The Wolf Problem" in done
            gained = mud._wallet(db, mud._character_row(db, uid)["id"]) - coins
            if uid == users[0]:
                assert gained == 35 and "experience" not in done
                assert _row(db, uid, "The Wolf Problem")["outcome"] == "slain"
            else:
                assert gained == 5 and "experience" in done
        context = mud._ConditionContext(db, users[1])
        quest = _id(db, "The Wolf Problem")
        assert mud_conditions.evaluate([{"type": "quest", "quest": quest, "outcome": "spared"}], context)
        assert not mud_conditions.evaluate([{"type": "quest", "quest": quest, "outcome": "slain"}], context)
        info = _bodies(mud._quests_action(db, _user(db, users[0]), {"arguments": ["info", "The", "Wolf", "Problem"]}))
        assert "Completed (slain)." in info


def test_closing_every_ending_fails_the_quest(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        user = _user(db, users[0])
        mud._accept_quest(db, user, _id(db, "The Wolf Problem"))
        assert "Task failed" in _bodies(mud._fail_objective(db, user, _objective_id(db, "Slay the pack leader")))
        events = _bodies(mud._fail_objective(db, user, _objective_id(db, "Drive the pack away"), "The pack fled"))
        assert "Quest failed: The Wolf Problem. The pack fled." in events
        with pytest.raises(mud.ActionError, match="failed The Wolf Problem"):
            mud._turn_in_quest(db, user, _id(db, "The Wolf Problem"))


def test_quest_fails_when_deleted_content_makes_it_impossible(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        user = _user(db, users[0])
        mud._accept_quest(db, user, _id(db, "Torchlight"))
        db.execute("DELETE FROM mud_item_definitions WHERE name='Torch'")
        events = _bodies(mud._check_quest_failures(db, user))
        assert "this quest can no longer be completed" in events
        assert _row(db, users[0], "Torchlight")["status"] == "failed"


def test_trigger_and_package_validation_for_new_fields(game):
    assert mud_triggers.validate_actions([{"type": "fail_quest", "quest": "q", "message": "Gone."}]) == [
        {"type": "fail_quest", "quest": "q", "message": "Gone."}]
    assert mud_triggers.actor_required({"type": "fail_objective", "objective": "o"})
    with pytest.raises(ValueError, match="reserved"):
        mud_quests.validate_outcome_key("failed")
    with pytest.raises(ValueError, match="needs at least one condition"):
        mud_quests.validate_fail_rules([{"when": []}])
    with pytest.raises(ValueError, match="only location details name a room"):
        mud_quests.validate_details([{"kind": "note", "text": "x", "room": "r"}])
    with pytest.raises(ValueError, match="no retry cooldown"):
        mud_quests.validate_retry("once", 60)
    with pytest.raises(ValueError, match="60 seconds"):
        mud_quests.validate_time_limit(5)
    app, _users = game
    for change, message in (
            (lambda doc: doc["content"]["mud_quest_rewards"].append(
                {"id": "ghost", "quest_id": "wolves", "reward_type": "coins", "amount": 1, "outcome": "fled"}),
             "not the outcome_key"),
            (lambda doc: doc["content"]["mud_quests"][1].update(retry_mode="sometimes"), "Retry mode"),
            (lambda doc: doc["content"]["mud_quest_objectives"][7].update(optional=1), "cannot be optional")):
        document = _document()
        change(document)
        with pytest.raises(mud_worlds.WorldError, match=message):
            mud_worlds.read_package(mud_worlds.package_bytes(document))
    document = _document()
    document["content"]["mud_quests"][2]["fail_conditions_json"][0]["when"] = [{"type": "has_item", "item": "ghost"}]
    with app.app_context():
        with pytest.raises(mud_worlds.WorldError, match="ghost"):
            mud_worlds.install(get_db(), mud_worlds.package_bytes(document))


def test_export_round_trip_keeps_failure_details_and_outcomes(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        exported = mud_worlds.read_package(mud_worlds.export(db, "failing"))
        content = exported["content"]
        local = {row["name"]: row["id"] for table in ("mud_quests", "mud_quest_objectives")
                 for row in content[table] if "name" in row}
        timed = next(row for row in content["mud_quests"] if row["name"] == "Race the Dusk")
        trail = next(row for row in content["mud_quest_objectives"] if row["description"] == "Find the trail")
        assert timed["time_limit_seconds"] == 600 and timed["summary"] == "Get to the woods in time"
        assert timed["details_json"][3]["reveal"] == [{"type": "objective", "objective": trail["id"]}]
        room = next(item["id"] for item in exported["rooms"] if item["name"] == "Dark Woods")
        assert timed["details_json"][1]["room"] == room
        guarded = next(row for row in content["mud_quests"] if row["name"] == "Keep the Elder Safe")
        assert (guarded["retry_cooldown_seconds"], guarded["fail_conditions_json"][0]["message"]) == (3600, "The elder is dead")
        single = next(row for row in content["mud_quests"] if row["name"] == "One Chance")
        assert single["retry_mode"] == "once"
        slay = next(row for row in content["mud_quest_objectives"] if row["description"] == "Slay the pack leader")
        assert slay["outcome_key"] == "slain"
        assert {row.get("outcome", "") for row in content["mud_quest_rewards"]} == {"", "slain", "spared", "failed"}
        assert any(action["type"] == "fail_quest" and action["quest"] == local["One Chance"]
                   for trigger in exported["events"] for action in trigger["actions"])
        exported["id"] = "failing-copy"
        mud_worlds.install(db, mud_worlds.package_bytes(exported))
        copied = db.execute("SELECT * FROM mud_quests WHERE world_id='failing-copy' AND name='Race the Dusk'").fetchone()
        trail_copy = db.execute("SELECT o.id FROM mud_quest_objectives o WHERE o.quest_id=? AND description=?",
                                (copied["id"], "Find the trail")).fetchone()[0]
        assert json.loads(copied["details_json"])[3]["reveal"][0]["objective"] == trail_copy


def test_builder_saves_and_validates_failure_settings(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        db.execute("UPDATE users SET is_admin=1 WHERE id=?", (users[0],))
        db.commit()
        elder = _id(db, "Elder", "mud_npc_definitions")
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = users[0]
        state["csrf_token"] = "test-token"
    quest = {"csrf_token": "test-token", "name": "Vigil", "description": "Hold the vigil.",
             "completion_mode": "auto", "abandonable": "1", "conditions_json": "[]",
             "summary": "Watch until dawn", "time_limit_minutes": "0.5", "retry_mode": "retry",
             "retry_cooldown_minutes": "", "fail_conditions_json": "[]", "details_json": "[]"}
    client.post(url("/builder/quests"), data=quest)  # under a minute
    quest.update(time_limit_minutes="30", retry_mode="once", retry_cooldown_minutes="5")
    client.post(url("/builder/quests"), data=quest)  # once quests take no cooldown
    quest.update(retry_cooldown_minutes="", fail_conditions_json='[{"when":[{"type":"has_item","item":"nowhere"}]}]')
    client.post(url("/builder/quests"), data=quest)  # unknown item
    quest.update(fail_conditions_json='[{"when":[{"type":"flag","scope":"world","key":"dawn.missed"}],"message":"Too late."}]',
                 details_json='[{"kind":"background","text":"The old vigil.","reveal":"offer"}]')
    client.post(url("/builder/quests"), data=quest)
    with app.app_context():
        db = get_db()
        rows = db.execute("SELECT * FROM mud_quests WHERE name='Vigil'").fetchall()
        assert len(rows) == 1
        vigil = rows[0]
        assert (vigil["summary"], vigil["time_limit_seconds"], vigil["retry_mode"]) == ("Watch until dawn", 1800, "once")
        assert json.loads(vigil["fail_conditions_json"])[0]["message"] == "Too late."
        assert json.loads(vigil["details_json"])[0]["reveal"] == "offer"
    objective = {"csrf_token": "test-token", "objective_type": "talk_npc", "target_id": elder,
                 "description": "Report", "required_count": "1", "sort_order": "0", "outcome_key": "Bad Key"}
    client.post(url(f"/builder/quests/{vigil['id']}/objectives"), data=objective)
    objective.update(outcome_key="reported", hidden="1")
    client.post(url(f"/builder/quests/{vigil['id']}/objectives"), data=objective)
    reward = {"csrf_token": "test-token", "reward_type": "coins", "amount": "3", "outcome": "fled"}
    client.post(url(f"/builder/quests/{vigil['id']}/rewards"), data=reward)
    reward["outcome"] = "reported"
    client.post(url(f"/builder/quests/{vigil['id']}/rewards"), data=reward)
    reward["outcome"] = "failed"
    client.post(url(f"/builder/quests/{vigil['id']}/rewards"), data=reward)
    with app.app_context():
        db = get_db()
        objectives = db.execute("SELECT * FROM mud_quest_objectives WHERE quest_id=?", (vigil["id"],)).fetchall()
        assert [(row["outcome_key"], row["hidden"]) for row in objectives] == [("reported", 1)]
        assert sorted(row["outcome"] for row in db.execute(
            "SELECT outcome FROM mud_quest_rewards WHERE quest_id=?", (vigil["id"],))) == ["failed", "reported"]
    page = client.get(url("/builder"))
    assert page.status_code == 200 and b"one attempt" in page.data and b"branching" in page.data


def test_migration_adds_failure_columns_and_keeps_old_progress():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    for table in ("mud_quests", "mud_quest_objectives", "mud_quest_rewards", "mud_player_quests",
                  "mud_player_objectives"):
        sql = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\);", db_module.SCHEMA, re.S).group()
        database.execute(sql)
    database.execute("CREATE INDEX idx_mud_player_quests_status ON mud_player_quests(character_id,status,accepted_at)")
    database.execute("INSERT INTO mud_quests(id,name,description) VALUES('old','Old','Old quest')")
    database.execute("INSERT INTO mud_player_quests(character_id,quest_id,status,rewarded_at) "
                     "VALUES('hero','old','completed','2020-01-01 00:00:00')")
    database.commit()
    db_module._migrate_mud_quest_failure(database)
    db_module._migrate_mud_quest_failure(database)
    quest = database.execute("SELECT * FROM mud_quests").fetchone()
    assert (quest["summary"], quest["retry_mode"], quest["time_limit_seconds"], quest["fail_conditions_json"]) == \
        ("", "retry", None, "[]")
    progress = database.execute("SELECT * FROM mud_player_quests").fetchone()
    assert (progress["status"], progress["rewarded_at"], progress["attempts"]) == ("completed", "2020-01-01 00:00:00", 1)
    database.execute("UPDATE mud_player_quests SET status='failed'")
    assert database.execute("SELECT 1 FROM sqlite_master WHERE name='idx_mud_player_quests_status'").fetchone()
    for table, column in (("mud_quest_objectives", "outcome_key"), ("mud_quest_rewards", "outcome"),
                          ("mud_player_objectives", "failed_at")):
        assert column in {row["name"] for row in database.execute(f"PRAGMA table_info({table})")}
    database.close()
