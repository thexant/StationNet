"""Turn-in quests, staged objectives, quest management, and conditional dialogue."""
import json
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

import pytest

from stationnet import create_app, character_creation, dungeons, mud, mud_conditions, mud_worlds
from stationnet import db as db_module
from stationnet.db import get_db


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_quests_{uuid.uuid4().hex}"
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


def _document(world_id="questing"):
    return {
        "id": world_id, "name": "Questing World", "author": "Test", "start_room": "square",
        "rooms": [{"id": "square", "name": "Village Square", "description": "The village square."},
                  {"id": "woods", "name": "Dark Woods", "description": "Tall dark trees."}],
        "exits": [{"from": "square", "to": "woods", "name": "trail", "direction": "north"},
                  {"from": "woods", "to": "square", "name": "trail", "direction": "south"}],
        "content": {
            "mud_item_definitions": [
                {"id": "pelt", "name": "Wolf Pelt", "description": "A pelt.", "item_type": "misc"},
                {"id": "badge", "name": "Hunter Badge", "description": "A badge.", "item_type": "misc"}],
            "mud_conversations": [{"id": "elder-talk", "name": "Elder"}, {"id": "hunter-talk", "name": "Hunter"}],
            "mud_dialogue_nodes": [
                {"id": "elder-start", "conversation_id": "elder-talk", "node_key": "start",
                 "body": "Wolves trouble us.", "is_start": 1},
                {"id": "hunter-start", "conversation_id": "hunter-talk", "node_key": "start",
                 "body": "Got pelts?", "is_start": 1}],
            "mud_npc_definitions": [
                {"id": "elder", "name": "Elder", "description": "An elder.", "conversation_id": "elder-talk"},
                {"id": "hunter", "name": "Hunter", "description": "A hunter.", "conversation_id": "hunter-talk"},
                {"id": "ranger", "name": "Ranger", "description": "A quiet ranger."}],
            "mud_npc_instances": [
                {"id": "elder-here", "definition_id": "elder", "room_id": "square"},
                {"id": "ranger-here", "definition_id": "ranger", "room_id": "square"},
                {"id": "hunter-here", "definition_id": "hunter", "room_id": "woods"}],
            "mud_quests": [
                {"id": "hunt", "name": "Wolf Hunt", "description": "Bring two pelts to the hunter.",
                 "completion_mode": "turn_in", "turn_in_npc_definition_id": "hunter", "sequential": 1},
                {"id": "errand", "name": "Scout the Woods", "description": "Look at the woods."},
                {"id": "veteran", "name": "Veteran Work", "description": "For the experienced.",
                 "conditions_json": [{"type": "level", "min": 5}]},
                {"id": "sequel", "name": "Sequel", "description": "After the hunt.",
                 "prerequisite_quest_id": "hunt",
                 "conditions_json": [{"type": "quest", "quest": "hunt", "status": "completed"}]}],
            "mud_quest_givers": [{"quest_id": quest, "npc_definition_id": "elder"}
                                 for quest in ("hunt", "veteran", "sequel")] +
                                [{"quest_id": "errand", "npc_definition_id": "ranger"}],
            "mud_quest_objectives": [
                {"id": "speak", "quest_id": "hunt", "objective_type": "talk_npc", "target_id": "elder",
                 "description": "Hear the elder out", "sort_order": 1},
                {"id": "pelts", "quest_id": "hunt", "objective_type": "possess_item", "target_id": "pelt",
                 "description": "Collect two pelts", "required_count": 2, "sort_order": 2,
                 "consume_on_turn_in": 1},
                {"id": "explore", "quest_id": "hunt", "objective_type": "visit_room", "target_id": "woods",
                 "description": "Explore the woods", "sort_order": 2, "optional": 1},
                {"id": "scout", "quest_id": "errand", "objective_type": "visit_room", "target_id": "woods",
                 "description": "Visit the woods"}],
            "mud_quest_rewards": [
                {"id": "hunt-coins", "quest_id": "hunt", "reward_type": "coins", "amount": 20},
                {"id": "hunt-badge", "quest_id": "hunt", "reward_type": "item", "target_id": "badge"},
                {"id": "errand-coins", "quest_id": "errand", "reward_type": "coins", "amount": 3}],
            "mud_dialogue_responses": [
                {"id": "take-hunt", "node_id": "elder-start", "response_text": "I will hunt.",
                 "accept_quest_id": "hunt"},
                {"id": "advice", "node_id": "elder-start", "response_text": "Any advice for a warrior?",
                 "conditions_json": [{"type": "class", "keys": ["warrior"]}],
                 "outcomes_json": [{"type": "coins", "amount": 5}, {"type": "experience", "amount": 10}]},
                {"id": "mage-only", "node_id": "elder-start", "response_text": "Arcane matters.",
                 "conditions_json": [{"type": "class", "keys": ["mage"]}]},
                {"id": "no-pelt", "node_id": "elder-start", "response_text": "I have no pelts yet.",
                 "conditions_json": [{"type": "has_item", "item": "pelt", "negate": True}]},
                {"id": "elder-turn-in", "node_id": "elder-start", "response_text": "Here are the pelts.",
                 "turn_in_quest_id": "hunt"},
                {"id": "hunter-turn-in", "node_id": "hunter-start", "response_text": "Here are the pelts.",
                 "turn_in_quest_id": "hunt"},
                {"id": "buy-pelt", "node_id": "hunter-start", "response_text": "Sell me a pelt.",
                 "outcomes_json": [{"type": "take_coins", "amount": 50}, {"type": "give_item", "item": "pelt"}]},
                {"id": "veteran-chat", "node_id": "hunter-start", "response_text": "Tales of the old days.",
                 "conditions_json": [{"any": [{"type": "level", "min": 10}, {"type": "quest", "quest": "hunt",
                                                                             "status": ["ready", "completed"]}]}]}],
        },
    }


def _install(db, users, document=None):
    document = document or _document()
    mud_worlds.install(db, mud_worlds.package_bytes(document))
    db.execute("UPDATE mud_world_packages SET active=0")
    db.execute("UPDATE mud_world_packages SET active=1 WHERE id=?", (document["id"],))
    rooms = {row["name"]: row["id"] for row in db.execute(
        "SELECT id,name FROM mud_world_rooms WHERE world_id=?", (document["id"],))}
    for uid in users:
        mud._set_player_location(db, uid, "traditional", "world_room", rooms["Village Square"])
    db.commit()
    return rooms


def _user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _definition(db, name, table="mud_item_definitions"):
    return db.execute(f"SELECT id FROM {table} WHERE name=?", (name,)).fetchone()[0]


def _quest(db, uid, name, status="active"):
    return next(quest for quest in mud.quest_state(db, uid)[status] if quest["name"] == name)


def _objective(quest, description):
    return next(objective for objective in quest["objectives"] if objective["description"] == description)


def _coins(db, uid):
    return mud._wallet(db, f"stationnet:character:{uid}")


def _held(db, uid, name):
    return sum(item["name"] == name for item in mud._inventory_rows(db, uid))


def test_turn_in_quest_with_sequential_stages_and_single_reward(game):
    app, users = game
    uid = users[0]
    with app.app_context():
        db = get_db()
        rooms = _install(db, users)
        user = _user(db, uid)
        pelt = _definition(db, "Wolf Pelt")
        greeting = mud._talk_action(db, user, {"arguments": ["Elder"]})[0].body
        assert "I will hunt." in greeting and "I have no pelts yet." in greeting
        assert "Arcane matters." not in greeting and "Here are the pelts." not in greeting
        accepted = mud._respond_action(db, user, {"arguments": ["I", "will", "hunt."]})
        assert any("Quest accepted: Wolf Hunt" in event.body for event in accepted)

        # Accepting through dialogue is not the same as speaking to the elder again.
        quest = _quest(db, uid, "Wolf Hunt")
        speak, pelts, explore = (_objective(quest, text) for text in
                                 ("Hear the elder out", "Collect two pelts", "Explore the woods"))
        assert not speak["completed_at"] and not speak["locked"]
        assert pelts["locked"] and explore["locked"] and explore["optional"]

        # Pelts held while their stage is locked count as soon as /talk unlocks it.
        mud._grant_item(db, uid, pelt)
        mud._grant_item(db, uid, pelt)
        mud._sync_held_items(db, user)
        assert _objective(_quest(db, uid, "Wolf Hunt"), "Collect two pelts")["progress"] == 0
        events = mud._talk_action(db, user, {"arguments": ["Elder"]})
        # The accept response disappears once the quest is taken.
        assert "I will hunt." not in events[0].body
        quest = _quest(db, uid, "Wolf Hunt")
        assert quest["ready"] and quest["rewarded_at"] is None
        assert _objective(quest, "Collect two pelts")["completed_at"]
        assert not _objective(quest, "Explore the woods")["completed_at"]
        assert quest["turn_in_npc_name"] == "Hunter"
        assert any("Return to Hunter" in event.body for event in events)
        # The elder is not the turn-in NPC, so neither dialogue nor the command works here.
        assert "Here are the pelts." not in mud._talk_action(db, user, {"arguments": ["Elder"]})[0].body
        with pytest.raises(mud.ActionError, match="Return to Hunter"):
            mud._quests_action(db, user, {"arguments": ["turnin", "Wolf", "Hunt"]})
        starting = _coins(db, uid)

        mud._set_player_location(db, uid, "traditional", "world_room", rooms["Dark Woods"])
        talk = mud._talk_action(db, user, {"arguments": ["Hunter"]})[0].body
        assert "Here are the pelts." in talk and "Tales of the old days." in talk
        done = mud._respond_action(db, user, {"arguments": ["Here", "are", "the", "pelts."]})
        assert any("Quest complete: Wolf Hunt" in event.body and "20 coins" in event.body for event in done)
        assert _coins(db, uid) == starting + 20
        assert _held(db, uid, "Wolf Pelt") == 0 and _held(db, uid, "Hunter Badge") == 1
        assert _quest(db, uid, "Wolf Hunt", "completed")["rewarded_at"]

        with pytest.raises(mud.ActionError, match="already turned in"):
            mud._turn_in_quest(db, user, _definition(db, "Wolf Hunt", "mud_quests"))
        with pytest.raises(mud.ActionError, match="no active quest"):
            mud._quests_action(db, user, {"arguments": ["turnin", "Wolf", "Hunt"]})
        assert "Here are the pelts." not in mud._talk_action(db, user, {"arguments": ["Hunter"]})[0].body
        assert _coins(db, uid) == starting + 20


def test_turn_in_requires_items_still_held_and_command_flow(game):
    app, users = game
    uid = users[0]
    with app.app_context():
        db = get_db()
        rooms = _install(db, users)
        user = _user(db, uid)
        pelt = _definition(db, "Wolf Pelt")
        mud._quests_action(db, user, {"arguments": ["accept", "Wolf", "Hunt"]})
        mud._talk_action(db, user, {"arguments": ["Elder"]})
        mud._grant_item(db, uid, pelt)
        mud._grant_item(db, uid, pelt)
        mud._set_objective_progress(db, user, "possess_item", pelt)
        assert _quest(db, uid, "Wolf Hunt")["ready"]
        listing = mud._quests_action(db, user, {})[0].body
        assert "READY — return to Hunter" in listing and "(optional)" in listing
        # Progress never regresses, but the items must be handed over at turn-in.
        mud._drop_action(db, user, {"arguments": ["Wolf", "Pelt", "1"]})
        mud._set_player_location(db, uid, "traditional", "world_room", rooms["Dark Woods"])
        with pytest.raises(mud.ActionError, match="You need 2 × Wolf Pelt"):
            mud._quests_action(db, user, {"arguments": ["turnin", "Wolf", "Hunt"]})
        mud._grant_item(db, uid, pelt)
        events = mud._quests_action(db, user, {"arguments": ["turnin", "Wolf", "Hunt"]})
        assert "You hand over Wolf Pelt, Wolf Pelt." == events[0].body
        assert "Quest complete" in events[1].body
        assert "✓ Wolf Hunt" in mud._quests_action(db, user, {"arguments": ["completed"]})[0].body


def test_auto_quests_still_complete_immediately_and_abandon_rules(game):
    app, users = game
    uid = users[0]
    with app.app_context():
        db = get_db()
        rooms = _install(db, users)
        user = _user(db, uid)
        available = mud._quests_action(db, user, {"arguments": ["available"]})[0].body
        assert "Scout the Woods (from Ranger)" in available and "Wolf Hunt (from Elder)" in available
        assert "Veteran Work" not in available and "Sequel" not in available
        mud._quests_action(db, user, {"arguments": ["accept", "Scout", "the", "Woods"]})
        abandoned = mud._quests_action(db, user, {"arguments": ["abandon", "Scout", "the", "Woods"]})
        assert "Quest abandoned" in abandoned[0].body
        assert not mud.quest_state(db, uid)["active"]
        assert db.execute("SELECT COUNT(*) FROM mud_player_objectives").fetchone()[0] == 0
        mud._quests_action(db, user, {"arguments": ["accept", "Scout", "the", "Woods"]})
        with pytest.raises(mud.ActionError, match="completes on its own"):
            mud._turn_in_quest(db, user, _definition(db, "Scout the Woods", "mud_quests"))
        starting = _coins(db, uid)
        events = mud._set_objective_progress(db, user, "visit_room", rooms["Dark Woods"])
        assert any("Quest complete: Scout the Woods. Rewards: 3 coins." == event.body for event in events)
        assert _coins(db, uid) == starting + 3
        with pytest.raises(mud.ActionError, match="no active quest"):
            mud._quests_action(db, user, {"arguments": ["abandon", "Scout", "the", "Woods"]})
        db.execute("UPDATE mud_quests SET abandonable=0 WHERE name='Wolf Hunt'")
        mud._quests_action(db, user, {"arguments": ["accept", "Wolf", "Hunt"]})
        with pytest.raises(mud.ActionError, match="cannot be abandoned"):
            mud._quests_action(db, user, {"arguments": ["abandon", "Wolf", "Hunt"]})
        info = mud._quests_action(db, user, {"arguments": ["info", "Wolf", "Hunt"]})[0].body
        assert info.startswith("Wolf Hunt —") and "🔒 Collect two pelts" in info
        with pytest.raises(mud.ActionError, match="Use /quests"):
            mud._quests_action(db, user, {"arguments": ["dance"]})


def test_quest_conditions_and_prerequisites_gate_acceptance(game):
    app, users = game
    uid = users[0]
    with app.app_context():
        db = get_db()
        _install(db, users)
        user = _user(db, uid)
        with pytest.raises(mud.ActionError, match="not currently available"):
            mud._quests_action(db, user, {"arguments": ["accept", "Veteran", "Work"]})
        with pytest.raises(mud.ActionError, match="requirements"):
            mud._accept_quest(db, user, _definition(db, "Veteran Work", "mud_quests"))
        character = mud._character_row(db, uid)
        db.execute("UPDATE mud_character_progression SET level=5 WHERE character_id=? AND track_key=?",
                   (character["id"], character["track_key"]))
        assert "Veteran Work" in mud._quests_action(db, user, {"arguments": ["available"]})[0].body
        with pytest.raises(mud.ActionError, match="prerequisite"):
            mud._accept_quest(db, user, _definition(db, "Sequel", "mud_quests"))


def test_dialogue_outcomes_are_granted_once_and_costs_roll_back(game):
    app, users = game
    uid = users[0]
    with app.app_context():
        db = get_db()
        rooms = _install(db, users)
        user = _user(db, uid)
        starting = _coins(db, uid)
        mud._talk_action(db, user, {"arguments": ["Elder"]})
        first = mud._respond_action(db, user, {"arguments": ["Any", "advice", "for", "a", "warrior?"]})
        assert any(event.body.startswith("You receive 5 coins") for event in first)
        mud._talk_action(db, user, {"arguments": ["Elder"]})
        second = mud._respond_action(db, user, {"arguments": ["Any", "advice", "for", "a", "warrior?"]})
        assert not any("receive" in event.body for event in second)
        assert _coins(db, uid) == starting + 5
        assert db.execute("SELECT COUNT(*) FROM mud_player_dialogue_claims").fetchone()[0] == 1

        mud._set_player_location(db, uid, "traditional", "world_room", rooms["Dark Woods"])
        mud._talk_action(db, user, {"arguments": ["Hunter"]})
        with pytest.raises(mud.ActionError, match="50 coins"):
            mud._respond_action(db, user, {"arguments": ["Sell", "me", "a", "pelt."]})
        assert _held(db, uid, "Wolf Pelt") == 0
        assert db.execute("SELECT COUNT(*) FROM mud_player_dialogue_claims").fetchone()[0] == 1
        character = mud._character_row(db, uid)
        db.execute("UPDATE mud_character_wallets SET coins=60 WHERE character_id=?", (character["id"],))
        mud._respond_action(db, user, {"arguments": ["Sell", "me", "a", "pelt."]})
        assert _coins(db, uid) == 10 and _held(db, uid, "Wolf Pelt") == 1
        mud._talk_action(db, user, {"arguments": ["Hunter"]})
        mud._respond_action(db, user, {"arguments": ["Sell", "me", "a", "pelt."]})
        assert _coins(db, uid) == 10 and _held(db, uid, "Wolf Pelt") == 1


def test_npc_without_dialogue_lists_quest_business(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        user = _user(db, users[0])
        body = mud._talk_action(db, user, {"arguments": ["Ranger"]})[0].body
        assert "Quests offered: Scout the Woods" in body
        mud._quests_action(db, user, {"arguments": ["accept", "Scout", "the", "Woods"]})
        with pytest.raises(mud.ActionError, match="nothing to say"):
            mud._talk_action(db, user, {"arguments": ["Ranger"]})


class _Context:
    def __init__(self, **values):
        self.values = values

    def level(self): return self.values.get("level", 1)
    def race(self): return self.values.get("race", "human")
    def class_key(self): return self.values.get("class", "warrior")
    def coins(self): return self.values.get("coins", 0)
    def item_count(self, item): return self.values.get("items", {}).get(item, 0)
    def attribute(self, name): return self.values.get("attributes", {}).get(name, 10)
    def skill_rank(self, skill): return self.values.get("skills", {}).get(skill, 0)
    def quest_status(self, quest): return self.values.get("quests", {}).get(quest, "not_started")
    def objective_complete(self, objective): return objective in self.values.get("objectives", ())
    def state(self, key): return self.values.get("state", {}).get(key, "")
    def claimed(self): return self.values.get("claimed", False)


@pytest.mark.parametrize("condition,context,expected", [
    ({"type": "level", "min": 3, "max": 5}, {"level": 4}, True),
    ({"type": "level", "min": 3}, {"level": 2}, False),
    ({"type": "race", "keys": ["elf"]}, {"race": "human"}, False),
    ({"type": "class", "key": "warrior"}, {}, True),
    ({"type": "has_item", "item": "gem", "count": 2}, {"items": {"gem": 2}}, True),
    ({"type": "has_item", "item": "gem", "negate": True}, {"items": {"gem": 1}}, False),
    ({"type": "coins", "min": 10}, {"coins": 9}, False),
    ({"type": "attribute", "attribute": "strength", "min": 12}, {"attributes": {"strength": 12}}, True),
    ({"type": "skill", "skill": "lore", "min_rank": 2}, {"skills": {"lore": 1}}, False),
    ({"type": "quest", "quest": "q", "status": ["active", "ready"]}, {"quests": {"q": "ready"}}, True),
    ({"type": "quest", "quest": "q", "status": "not_started"}, {}, True),
    ({"type": "objective", "objective": "o"}, {"objectives": {"o"}}, True),
    ({"type": "state", "key": "met", "value": "yes"}, {"state": {"met": "yes"}}, True),
    ({"type": "claimed", "negate": True}, {"claimed": True}, False),
    ({"any": [{"type": "level", "min": 9}, {"type": "coins", "min": 1}]}, {"coins": 1}, True),
    ({"any": [{"type": "level", "min": 9}], "negate": True}, {}, True),
])
def test_condition_types(condition, context, expected):
    assert mud_conditions.evaluate([condition], _Context(**context)) is expected


@pytest.mark.parametrize("value,message", [
    ([{"type": "weather"}], "Unsupported condition"),
    ([{"type": "level"}], "min and/or max"),
    ([{"type": "level", "min": 6, "max": 2}], "cannot exceed"),
    ([{"type": "has_item", "item": "gem", "count": 0}], "count"),
    ([{"any": [{"any": [{"type": "claimed"}]}]}], "nested"),
    ([{"type": "coins", "min": 1, "extra": True}], "do not accept"),
    ("{not json", "valid JSON"),
    ({"type": "level"}, "JSON array"),
])
def test_invalid_conditions_are_rejected(value, message):
    with pytest.raises(ValueError, match=message):
        mud_conditions.validate_conditions(value)


def test_invalid_outcomes_and_dungeon_quest_conditions_are_rejected():
    with pytest.raises(ValueError, match="1 to 100"):
        mud_conditions.validate_outcomes([{"type": "give_item", "item": "gem", "amount": 101}])
    with pytest.raises(ValueError, match="Unsupported outcome"):
        mud_conditions.validate_outcomes([{"type": "teleport"}])
    with pytest.raises(ValueError, match="Unsupported condition"):
        mud_conditions.validate_conditions([{"type": "quest", "quest": "q"}], allow_quests=False)
    errors = []
    dungeons._validate_conversation(errors, {"nodes": [{"id": "start", "body": "Hi", "start": True, "responses": [
        {"id": "a", "text": "Quest?", "conditions": [{"type": "quest", "quest": "q"}]},
        {"id": "b", "text": "Gift?", "outcomes": [{"type": "give_item", "item": "unknown"}]},
        {"id": "c", "text": "Gem?", "outcomes": [{"type": "give_item", "item": "gem"}]}]}]},
        "$.conversation", {"gem"})
    assert len(errors) == 2
    assert "conditions" in errors[0] and "'unknown'" in errors[1]


def test_dungeon_theme_responses_accept_conditions_and_outcomes():
    path = Path(dungeons.__file__).with_name("dungeon_templates") / "starter-theme.json"
    template = json.loads(path.read_text(encoding="utf-8"))
    npc = next(npc for npc in template["definitions"]["npcs"] if npc.get("conversation"))
    response = {"id": "gift", "text": "A gift?",
                "conditions": [{"type": "level", "min": 1}, {"type": "claimed", "negate": True}],
                "outcomes": [{"type": "give_item", "item": "healing-placeholder"}]}
    npc["conversation"]["nodes"][0].setdefault("responses", []).append(response)
    assert dungeons.validate_template(template) == []
    conditions_json, outcomes_json = dungeons._response_rules_json(template, response)
    assert json.loads(outcomes_json)[0]["item"] == dungeons.local_definition_id(
        template, "item", "healing-placeholder")
    assert json.loads(conditions_json)[1] == {"type": "claimed", "negate": True}
    response["conditions"] = [{"type": "quest", "quest": "anything"}]
    assert any("conditions" in error for error in dungeons.validate_template(template))


@pytest.mark.parametrize("table,index,field,value,message", [
    ("mud_quests", 0, "completion_mode", "eventually", "completion_mode"),
    ("mud_quests", 0, "sequential", 2, "sequential"),
    ("mud_quest_objectives", 0, "consume_on_turn_in", 1, "possess_item"),
    ("mud_quest_objectives", 0, "recipient_npc_definition_id", "hunter", "deliver_item"),
    ("mud_dialogue_responses", 0, "conditions_json", [{"type": "weather"}], "conditions_json"),
    ("mud_dialogue_responses", 0, "outcomes_json", [{"type": "coins", "amount": 0}], "outcomes_json"),
])
def test_world_package_rejects_invalid_quest_and_dialogue_settings(table, index, field, value, message):
    document = _document()
    document["content"][table][index][field] = value
    with pytest.raises(mud_worlds.WorldError, match=message):
        mud_worlds.read_package(mud_worlds.package_bytes(document))


def test_world_package_rejects_unknown_condition_reference(game):
    app, _users = game
    document = _document()
    document["content"]["mud_dialogue_responses"][3]["conditions_json"] = [{"type": "has_item", "item": "ghost"}]
    with app.app_context():
        with pytest.raises(mud_worlds.WorldError, match="ghost"):
            mud_worlds.install(get_db(), mud_worlds.package_bytes(document))


def test_export_round_trip_keeps_quest_and_dialogue_rules(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        stored = db.execute("SELECT conditions_json FROM mud_dialogue_responses WHERE response_text=?",
                            ("I have no pelts yet.",)).fetchone()[0]
        assert json.loads(stored)[0]["item"] == _definition(db, "Wolf Pelt")
        exported = mud_worlds.read_package(mud_worlds.export(db, "questing"))
        content = exported["content"]
        local = {row["name"]: row["id"] for table in ("mud_item_definitions", "mud_quests", "mud_npc_definitions")
                 for row in content[table]}
        hunt = next(row for row in content["mud_quests"] if row["name"] == "Wolf Hunt")
        assert (hunt["completion_mode"], hunt["sequential"], hunt["abandonable"]) == ("turn_in", 1, 1)
        assert hunt["turn_in_npc_definition_id"] == local["Hunter"]
        sequel = next(row for row in content["mud_quests"] if row["name"] == "Sequel")
        assert sequel["conditions_json"] == [{"type": "quest", "quest": local["Wolf Hunt"], "status": ["completed"]}]
        pelts = next(row for row in content["mud_quest_objectives"] if row["description"] == "Collect two pelts")
        assert pelts["consume_on_turn_in"] == 1 and pelts["sort_order"] == 2
        responses = {row["response_text"]: row for row in content["mud_dialogue_responses"]}
        assert responses["I have no pelts yet."]["conditions_json"] == [
            {"type": "has_item", "item": local["Wolf Pelt"], "count": 1, "negate": True}]
        assert responses["Any advice for a warrior?"]["conditions_json"] == [{"type": "class", "keys": ["warrior"]}]
        assert responses["Sell me a pelt."]["outcomes_json"][1] == {
            "type": "give_item", "item": local["Wolf Pelt"], "amount": 1}
        assert responses["Here are the pelts."]["turn_in_quest_id"] == local["Wolf Hunt"]

        exported["id"] = "questing-copy"
        mud_worlds.install(db, mud_worlds.package_bytes(exported))
        copied_pelt = db.execute("SELECT id FROM mud_item_definitions WHERE name='Wolf Pelt' AND world_id='questing-copy'").fetchone()[0]
        copied = db.execute(
            "SELECT r.conditions_json FROM mud_dialogue_responses r JOIN mud_dialogue_nodes n ON n.id=r.node_id "
            "JOIN mud_conversations c ON c.id=n.conversation_id WHERE c.world_id='questing-copy' "
            "AND r.response_text='I have no pelts yet.'").fetchone()[0]
        assert json.loads(copied)[0]["item"] == copied_pelt


def test_builder_validates_quest_and_dialogue_rules(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _install(db, users)
        db.execute("UPDATE users SET is_admin=1 WHERE id=?", (users[0],))
        db.commit()
        hunter = _definition(db, "Hunter", "mud_npc_definitions")
        pelt = _definition(db, "Wolf Pelt")
        node = db.execute("SELECT id FROM mud_dialogue_nodes WHERE node_key='start' AND body='Got pelts?'").fetchone()[0]
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = users[0]
        state["csrf_token"] = "test-token"
    quest = {"csrf_token": "test-token", "name": "Trophy", "description": "Bring a trophy.",
             "completion_mode": "turn_in", "turn_in_npc_definition_id": hunter, "sequential": "1",
             "abandonable": "1", "conditions_json": '[{"type":"weather"}]'}
    client.post("/mud/builder/quests", data=quest)
    quest["conditions_json"] = '[{"type":"has_item","item":"nowhere"}]'
    client.post("/mud/builder/quests", data=quest)
    quest["conditions_json"] = '[{"type":"level","min":2}]'
    client.post("/mud/builder/quests", data=quest)
    with app.app_context():
        db = get_db()
        rows = db.execute("SELECT * FROM mud_quests WHERE name='Trophy'").fetchall()
        assert len(rows) == 1
        trophy = rows[0]
        assert (trophy["completion_mode"], trophy["turn_in_npc_definition_id"], trophy["sequential"]) == \
            ("turn_in", hunter, 1)
        assert json.loads(trophy["conditions_json"]) == [{"type": "level", "min": 2}]
    objective = {"csrf_token": "test-token", "objective_type": "talk_npc", "target_id": hunter,
                 "description": "Talk", "required_count": "1", "sort_order": "3", "consume_on_turn_in": "1"}
    client.post(f"/mud/builder/quests/{trophy['id']}/objectives", data=objective)
    objective.update(objective_type="possess_item", target_id=pelt, optional="1")
    client.post(f"/mud/builder/quests/{trophy['id']}/objectives", data=objective)
    response = {"csrf_token": "test-token", "response_text": "Trophy please", "turn_in_quest_id": trophy["id"],
                "conditions_json": "[]", "outcomes_json": '[{"type":"coins","amount":4}]', "sort_order": "0"}
    client.post(f"/mud/builder/dialogue-nodes/{node}/responses", data=response)
    with app.app_context():
        db = get_db()
        objectives = db.execute("SELECT * FROM mud_quest_objectives WHERE quest_id=?", (trophy["id"],)).fetchall()
        assert [(row["objective_type"], row["sort_order"], row["optional"], row["consume_on_turn_in"])
                for row in objectives] == [("possess_item", 3, 1, 1)]
        saved = db.execute("SELECT * FROM mud_dialogue_responses WHERE response_text='Trophy please'").fetchone()
        assert saved["turn_in_quest_id"] == trophy["id"]
        assert json.loads(saved["outcomes_json"]) == [{"type": "coins", "amount": 4}]
    response.update(response_text="Trophy now", outcomes_json='[{"type":"coins","amount":0}]')
    client.post(f"/mud/builder/dialogue-responses/{saved['id']}", data=response)
    response["outcomes_json"] = "[]"
    client.post(f"/mud/builder/dialogue-responses/{saved['id']}", data=response)
    objective.update(sort_order="1", optional="")
    client.post(f"/mud/builder/quest-objectives/{objectives[0]['id']}", data=objective)
    with app.app_context():
        db = get_db()
        saved = db.execute("SELECT * FROM mud_dialogue_responses WHERE id=?", (saved["id"],)).fetchone()
        assert (saved["response_text"], saved["outcomes_json"]) == ("Trophy now", "[]")
        updated = db.execute("SELECT * FROM mud_quest_objectives WHERE id=?", (objectives[0]["id"],)).fetchone()
        assert (updated["sort_order"], updated["optional"]) == (1, 0)
    assert client.get("/mud/builder").status_code == 200


def test_migration_adds_quest_and_dialogue_columns_to_old_tables():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    removed = re.compile(r"\n (completion_mode|turn_in_npc_definition_id|sequential|abandonable|conditions_json|"
                         r"optional|consume_on_turn_in|recipient_npc_definition_id|ready_at|outcomes_json|"
                         r"turn_in_quest_id) [^\n]*")
    for table in ("mud_quests", "mud_quest_objectives", "mud_player_quests", "mud_dialogue_responses"):
        sql = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\);", db_module.SCHEMA, re.S).group()
        database.execute(removed.sub("", sql))
    database.execute("INSERT INTO mud_quests(id,name,description) VALUES('old','Old','Old quest')")
    db_module._migrate_mud_quest_dialogue(database)
    quest = database.execute("SELECT * FROM mud_quests").fetchone()
    assert (quest["completion_mode"], quest["sequential"], quest["abandonable"], quest["conditions_json"]) == \
        ("auto", 0, 1, "[]")
    for table, column in (("mud_quest_objectives", "recipient_npc_definition_id"),
                          ("mud_player_quests", "ready_at"), ("mud_dialogue_responses", "outcomes_json")):
        assert column in {row["name"] for row in database.execute(f"PRAGMA table_info({table})")}
    db_module._migrate_mud_quest_dialogue(database)
    database.close()
