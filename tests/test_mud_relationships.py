"""NPC relationships: per-player regard, how it changes, and what it changes."""
import json
import zipfile
from io import BytesIO

import pytest

from stationnet import mud, mud_conditions, mud_society, mud_triggers, mud_worlds
from stationnet.db import get_db

from mud_society_support import WORLD, act, client, document, installed, place, room, running, set_society, user


def _world(enabled=True):
    doc = document(society={"relationships": {"enabled": enabled}})
    content = doc["content"]
    content["mud_item_definitions"].append(
        {"id": "pie", "name": "Apple Pie", "description": "Warm.", "item_type": "misc"})
    content["mud_npc_definitions"][1]["behaviors_json"] = {"relationship": {
        "gifts": {"pie": 30}, "greetings": {"friendly": "Ah, my favourite customer!"}}}
    content["mud_npc_definitions"][1]["conversation_id"] = "bakery"
    content["mud_npc_definitions"].append(
        {"id": "dog", "name": "Guard Dog", "description": "Wary.",
         "behaviors_json": {"neutral": True, "relationship": {"default": -55}}})
    content["mud_npc_instances"].append({"id": "dog-1", "definition_id": "dog", "room_id": "tavern"})
    content["mud_npc_combat_profiles"] = [{"npc_definition_id": "dog", "maximum_health": 40, "attack": 2,
                                           "defense": 0, "experience_reward": 1, "respawn_seconds": 60}]
    content["mud_conversations"] = [{"id": "bakery", "name": "Bakery"}]
    content["mud_dialogue_nodes"] = [{"id": "bake-start", "conversation_id": "bakery", "node_key": "start",
                                      "body": "Fresh bread!", "is_start": 1}]
    content["mud_dialogue_responses"] = [
        {"id": "compliment", "node_id": "bake-start", "response_text": "Your bread is the best.",
         "outcomes_json": [{"type": "disposition", "amount": 25}]},
        {"id": "secret", "node_id": "bake-start", "response_text": "Any secret recipes?",
         "conditions_json": [{"type": "disposition", "tier": ["friendly", "trusted"]}]}]
    content["mud_quests"] = [{"id": "errand", "name": "Errand", "description": "Visit the forge."}]
    content["mud_quest_objectives"] = [{"id": "go-forge", "quest_id": "errand", "objective_type": "visit_room",
                                        "target_id": "forge", "description": "Visit the forge"}]
    content["mud_quest_givers"] = [{"quest_id": "errand", "npc_definition_id": "baker"}]
    return doc


def _regard(db, uid, local):
    return mud_society.disposition(db, f"stationnet:character:{uid}",
                                   installed(db, "mud_npc_definitions", local), 0)


def test_tiers_and_validation():
    assert [mud_society.disposition_tier(value) for value in (-60, -21, 0, 20, 60)] == [
        "hostile", "unfriendly", "neutral", "friendly", "trusted"]
    assert mud_society.disposition_tier(-50, hostile_at=-40) == "hostile"
    assert mud_society.disposition_price_percent(100) == 90 and mud_society.disposition_price_percent(-100) == 110
    with pytest.raises(ValueError):
        mud.validate_npc_behaviors({"relationship": {"gifts": {"pie": 500}}})
    with pytest.raises(ValueError):
        mud.validate_npc_behaviors({"relationship": {"greetings": {"besties": "Hi"}}})
    assert mud_conditions.validate_conditions([{"type": "disposition", "min": 10}])
    with pytest.raises(ValueError):
        mud_conditions.validate_conditions([{"type": "disposition", "tier": "friendly", "min": 1}])
    with pytest.raises(ValueError):
        mud_conditions.validate_outcomes([{"type": "disposition", "amount": 0}])
    with pytest.raises(ValueError):
        mud_triggers.validate_actions([{"type": "disposition", "amount": 5}])  # Triggers must name the NPC.
    with pytest.raises(mud_society.SocietyError):
        mud_society.validate_relationships({"hostile_at": 10})


def test_off_by_default_changes_nothing():
    with running(_world(enabled=False)) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            assert "Fresh bread!" in act(db, one, "/talk Baker")
            act(db, one, "/respond 1")
            assert db.execute("SELECT COUNT(*) FROM mud_npc_relationships").fetchone()[0] == 0
            assert "secret" not in act(db, one, "/talk Baker").casefold()
            assert {"relations", "give"} <= mud._hidden_actions(db, one)
            place(db, one, room(db, "tavern"))
            assert not mud._active_hostiles(db, mud._current_room(db, one), one)


def test_dialogue_outcome_raises_regard_and_unlocks_responses():
    with running(_world()) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            act(db, one, "/talk Baker")
            assert "now regards you as friendly" in act(db, one, "/respond 1")
            assert _regard(db, one, "baker") == 25 and _regard(db, two, "baker") == 0
            spoken = act(db, one, "/talk Baker")
            assert spoken.startswith("Baker says: Ah, my favourite customer!")
            assert "Any secret recipes?" in spoken
            assert "Any secret recipes?" not in act(db, two, "/talk Baker")
            assert "Baker: friendly (+25)" in act(db, one, "/relations")


def test_gifts_quests_and_prices():
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            assert "20 coins" in act(db, one, "/shop Baker")
            mud._grant_item(db, one, installed(db, "mud_item_definitions", "pie"), "test")
            mud._grant_item(db, one, installed(db, "mud_item_definitions", "nail"), "test")
            with pytest.raises(mud.ActionError, match="does not want"):
                act(db, one, '/give "Iron Nail" to Baker')
            assert "gladly accepts" in act(db, one, '/give "Apple Pie" to Baker')
            assert _regard(db, one, "baker") == 30
            assert not mud._inventory_rows(db, one) or all(row["name"] != "Apple Pie" for row in mud._inventory_rows(db, one))
            # Friendly regard takes 3% off; the stored price is unchanged.
            assert "19 coins" in act(db, one, "/shop Baker")
            mud._accept_quest(db, user(db, one), installed(db, "mud_quests", "errand"))
            place(db, one, room(db, "forge"))
            completed = "\n".join(draft.body for draft in mud._set_objective_progress(
                db, user(db, one), "visit_room", room(db, "forge")))
            assert "Baker's goodwill" in completed
            assert _regard(db, one, "baker") == 35


def test_hostile_regard_refuses_talk_and_turns_combat_npcs():
    with running(_world()) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            place(db, one, room(db, "tavern"))
            place(db, two, room(db, "tavern"))
            tavern = mud._current_room(db, one)
            # The dog starts unfriendly (-55) and neutral: no one is attacked yet.
            assert not mud._active_hostiles(db, tavern, one)
            mud._change_disposition(db, user(db, one), installed(db, "mud_npc_definitions", "dog"), -10)
            assert [npc["name"] for npc, _ in mud._active_hostiles(db, tavern, one)] == ["Guard Dog"]
            assert not mud._active_hostiles(db, tavern, two)
            assert mud._npc_stance(db, mud._room_npc_rows(db, tavern["id"])[0], two) != "Hostile"
            place(db, two, room(db, "square"))
            mud._change_disposition(db, user(db, two), installed(db, "mud_npc_definitions", "baker"), -70)
            with pytest.raises(mud.ActionError, match="refuses to speak"):
                act(db, two, "/talk Baker")


def test_attacking_a_peaceful_npc_is_remembered():
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            place(db, one, room(db, "tavern"))
            mud._change_disposition(db, user(db, one), installed(db, "mud_npc_definitions", "dog"), 50)
            act(db, one, '/attack "Guard Dog"')
            assert _regard(db, one, "dog") == -45
            act(db, one, '/attack "Guard Dog"')  # Already fighting: no second penalty.
            assert _regard(db, one, "dog") == -45


def test_trigger_action_and_package_round_trip():
    doc = _world()
    doc["events"] = [{"id": "praise", "event": "enter_room", "source": {"room": "forge"},
                      "actions": [{"type": "disposition", "npc": "smith", "amount": 7}]}]
    with running(doc) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            mud.fire_triggers(db, user(db, one), "enter_room", [room(db, "forge")])
            assert _regard(db, one, "smith") == 7
            payload = mud_worlds.export(db, WORLD)
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                content = json.loads(archive.read("content.json"))
                events = json.loads(archive.read("events.json"))
            assert manifest["relationships"]["enabled"] is True
            baker = next(row for row in content["mud_npc_definitions"] if row["name"] == "Baker")
            pie = next(row["id"] for row in content["mud_item_definitions"] if row["name"] == "Apple Pie")
            assert baker["behaviors_json"]["relationship"]["gifts"] == {pie: 30}
            smith = next(row["id"] for row in content["mud_npc_definitions"] if row["name"] == "Smith")
            assert events["triggers"][0]["actions"][0]["npc"] == smith
            mud_worlds.read_package(payload)
    broken = _world()
    broken["content"]["mud_npc_definitions"][1]["behaviors_json"]["relationship"]["gifts"] = {"cake": 5}
    with pytest.raises(mud_worlds.WorldError, match="unknown item"):
        mud_worlds.read_package(mud_worlds.package_bytes(broken))
    broken = _world()
    broken["content"]["mud_dialogue_responses"][0]["outcomes_json"] = [
        {"type": "disposition", "npc": "ghost", "amount": 5}]
    with pytest.raises(mud_worlds.WorldError):
        with running(broken):
            pass


def test_builder_saves_relationship_settings():
    with running() as (app, (one, _two)):
        response = client(app, one).post(f"/mud/w/{WORLD}/builder/society/relationships", data={
            "csrf_token": "test-token", "enabled": "1", "quest_complete": "8", "attack": "-30",
            "crime": "-20", "hostile_at": "-50"})
        assert response.status_code == 302
        with app.app_context():
            assert mud.society_settings(get_db())["relationships"] == {
                "enabled": True, "quest_complete": 8, "attack": -30, "crime": -20, "hostile_at": -50}
        response = client(app, one).post(f"/mud/w/{WORLD}/builder/society/relationships", data={
            "csrf_token": "test-token", "hostile_at": "5"}, follow_redirects=True)
        assert b"hostile_at must be" in response.data
