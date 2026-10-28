"""Crime: theft, pickpocketing, and assault; witnesses, fines, guards, and the law's reach."""
import json
import zipfile
from io import BytesIO

import pytest

from stationnet import mud, mud_conditions, mud_society, mud_triggers, mud_worlds
from stationnet.db import get_db

from mud_society_support import WORLD, act, client, document, installed, place, room, running, user


def _world(crime=True):
    doc = document(society={"crime": {"enabled": crime, "detection": 70},
                            "relationships": {"enabled": True}})
    content = doc["content"]
    content["mud_world_factions"] = [{"id": "watch", "name": "Town Watch"},
                                     {"id": "guild", "name": "Merchants' Guild"}]
    content["mud_faction_relations"] = [{"id": "allies", "faction_id": "guild", "other_faction_id": "watch",
                                         "share_percent": 50}]
    npcs = content["mud_npc_definitions"]
    npcs[0]["behaviors_json"] = {}
    npcs[1].update(faction_id="guild", behaviors_json={"neutral": True, "steal": {
        "difficulty": 10, "coins": {"minimum": 5, "maximum": 5}, "items": [{"item": "nail", "chance": 100}]}})
    npcs.append({"id": "guard", "name": "Watch Guard", "description": "Stern.", "faction_id": "watch",
                 "behaviors_json": {"guard": True, "neutral": True}})
    npcs.append({"id": "drunk", "name": "Drunk", "description": "Snoring.", "behaviors_json": {"witness": False}})
    content["mud_npc_instances"] += [{"id": "guard-1", "definition_id": "guard", "room_id": "forge"},
                                     {"id": "drunk-1", "definition_id": "drunk", "room_id": "square"}]
    content["mud_npc_combat_profiles"] = [
        {"npc_definition_id": "guard", "maximum_health": 60, "attack": 1, "defense": 0,
         "experience_reward": 1, "respawn_seconds": 60},
        {"npc_definition_id": "baker", "maximum_health": 60, "attack": 1, "defense": 0,
         "experience_reward": 1, "respawn_seconds": 60}]
    content["mud_item_instances"] = [
        {"id": "stall-lantern", "definition_id": "lantern", "room_id": "square",
         "state_json": {"owner_npc": "baker-1"}},
        {"id": "free-nail", "definition_id": "nail", "room_id": "square"}]
    doc["exits"][2]["conditions"] = [{"type": "wanted", "negate": True}]
    doc["exits"][2]["conditions_message"] = "The gate guard turns you away."
    doc["events"] = [{"id": "alarm", "event": "crime", "source": {"faction": "guild"},
                      "actions": [{"type": "set_flag", "key": "crimes", "add": 1}]}]
    return doc


@pytest.fixture
def rolls(monkeypatch):
    """Set the next witness/pickpocket rolls; once used up, every roll is 99 (unnoticed, failed)."""
    queue = []
    monkeypatch.setattr(mud, "_crime_roll", lambda: queue.pop(0) if queue else 99)
    return queue


def _fines(db, uid):
    return mud_society.open_fines(db, f"stationnet:character:{uid}", WORLD)


def _standing(db, uid, local):
    return mud_society.standing(db, f"stationnet:character:{uid}",
                                mud_society.faction(db, installed(db, "mud_world_factions", local)))[0]


def test_settings_and_validation():
    crime = mud_society.validate_crime({"enabled": True, "fines": {"theft": 5}})
    assert crime["fines"] == {"theft": 5, "pickpocket": 40, "assault": 75}
    for bad in ({"fines": {"arson": 5}}, {"reputation": {"theft": 5}}, {"detection": 101}, {"bounty": 1}):
        with pytest.raises(mud_society.SocietyError):
            mud_society.validate_crime(bad)
    assert mud_society.detection_chance(70, 0, 10, False) == 70
    assert mud_society.detection_chance(70, 30, 20, True) == 5
    with pytest.raises(ValueError):
        mud.validate_npc_behaviors({"steal": {"coins": {"minimum": 0, "maximum": 0}}})
    with pytest.raises(ValueError):
        mud.validate_npc_behaviors({"guard": "yes"})
    assert mud_conditions.validate_conditions([{"type": "wanted"}]) == [{"type": "wanted", "min_fine": 1}]
    assert mud_triggers.validate_trigger({"id": "a", "event": "crime", "source": {"faction": "x"},
                                          "actions": [{"type": "clear_fines"}]})


def test_crime_off_leaves_everything_alone(rolls):
    with running(_world(crime=False)) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            rolls.extend([0] * 10)
            assert act(db, one, '/get "Brass Lantern"') == "You pick up Brass Lantern.\nOne picks up Brass Lantern."
            assert db.execute("SELECT COUNT(*) FROM mud_character_crimes").fetchone()[0] == 0
            assert {"steal", "fines", "pay"} <= mud._hidden_actions(db, one)
            with pytest.raises(mud.ActionError):
                act(db, one, "/steal from Baker")


def test_unseen_theft_marks_goods_stolen(rolls):
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            assert "sees" not in act(db, one, '/get "Brass Lantern"')
            assert _fines(db, one) == {}
            item = installed(db, "mud_item_instances", "stall-lantern")
            state = json.loads(db.execute("SELECT state_json FROM mud_item_instances WHERE id=?", (item,)).fetchone()[0])
            baker = installed(db, "mud_npc_instances", "baker-1")
            assert state == {"stolen_from": installed(db, "mud_world_factions", "guild")} or state == {"stolen_from": baker}
            # The guild's merchant recognises its own goods.
            with pytest.raises(mud.ActionError, match="stolen"):
                act(db, one, '/shop sell "Brass Lantern"')
            # Dropping and taking it again is no longer theft.
            act(db, one, '/drop "Brass Lantern"')
            rolls.extend([0] * 5)
            assert "sees" not in act(db, one, '/get "Brass Lantern"')
            # Ordinary items never are.
            assert "sees" not in act(db, one, '/get "Iron Nail"')


def test_seen_theft_fines_costs_standing_and_fires_triggers(rolls):
    with running(_world()) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            rolls.extend([0, 0])  # The baker notices (the drunk never looks).
            text = act(db, one, '/get "Brass Lantern"')
            assert "Baker sees One taking Brass Lantern!" in text
            assert "fined 25 coins by Merchants' Guild" in text
            guild = installed(db, "mud_world_factions", "guild")
            assert _fines(db, one) == {guild: 25} and _fines(db, two) == {}
            assert _standing(db, one, "guild") == -50 and _standing(db, one, "watch") == -25
            assert mud_society.disposition(db, f"stationnet:character:{one}",
                                           installed(db, "mud_npc_definitions", "baker")) == -25
            assert mud._script_flag(db, WORLD, f"stationnet:character:{one}", "crimes") == "1"
            assert "Merchants' Guild: 25 coins" in act(db, one, "/fines")
            # A wanted character is turned away at the forge door.
            with pytest.raises(mud.ActionError, match="turns you away"):
                act(db, one, "/go east")
            assert mud_conditions.evaluate([{"type": "wanted", "faction": guild}], mud._ConditionContext(db, one))


def test_pickpocketing(rolls):
    with running(_world()) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            rolls.extend([0, 0])  # The lift succeeds, and the nail comes with it; nobody notices.
            text = act(db, one, "/steal from Baker")
            assert text.startswith("You lift 5 coins, Iron Nail from Baker.")
            assert _fines(db, one) == {}
            with pytest.raises(mud.ActionError, match="close watch"):
                act(db, one, "/steal Baker")
            # A failed lift is always caught by the victim.
            text = act(db, two, "/steal from Baker")
            assert "Baker catches your hand!" in text and "fined 40 coins" in text
            with pytest.raises(mud.ActionError, match="nothing worth stealing"):
                act(db, one, "/steal from Drunk")


def test_guards_pursue_until_fines_are_paid(rolls):
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            watch = installed(db, "mud_world_factions", "watch")
            mud.mud_society.record_crime(db, f"stationnet:character:{one}", WORLD, watch, "other", 30, -40)
            place(db, one, room(db, "forge"))
            forge = mud._current_room(db, one)
            assert [npc["name"] for npc, _ in mud._active_hostiles(db, forge, one)] == ["Watch Guard"]
            shouts = "\n".join(draft.body for draft in mud._entry_attack(db, user(db, one)))
            assert "Halt! You owe 30 coins" in shouts
            mud._wallet(db, f"stationnet:character:{one}")
            db.execute("UPDATE mud_character_wallets SET coins=10 WHERE character_id=?", (f"stationnet:character:{one}",))
            with pytest.raises(mud.ActionError, match="need 30 coins"):
                act(db, one, "/pay")
            db.execute("UPDATE mud_character_wallets SET coins=100 WHERE character_id=?", (f"stationnet:character:{one}",))
            text = act(db, one, "/pay")
            assert "You pay 30 coins in fines." in text and "Town Watch +20" in text
            assert _fines(db, one) == {}
            assert not mud._active_hostiles(db, forge, one)
            place(db, one, room(db, "square"))
            mud.mud_society.record_crime(db, f"stationnet:character:{one}", WORLD, watch, "other", 5, 0)
            with pytest.raises(mud.ActionError, match="Find a member of Town Watch"):
                act(db, one, "/pay")


def test_assault_is_always_seen(rolls):
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            text = act(db, one, "/attack Baker")
            assert "Baker sees One attacking Baker!" in text and "fined 75 coins" in text
            assert _standing(db, one, "guild") == -100
            act(db, one, "/attack Baker")  # The same fight is not a second assault.
            assert db.execute("SELECT COUNT(*) FROM mud_character_crimes").fetchone()[0] == 1


def test_fine_expiry_and_trigger_actions():
    doc = _world()
    doc["society"]["crime"]["fine_expiry_hours"] = 1
    doc["events"].append({"id": "pardon", "event": "say_phrase", "source": {"room": "square"}, "phrase": "mercy",
                          "actions": [{"type": "clear_fines", "faction": "watch"}]})
    doc["events"].append({"id": "tax", "event": "say_phrase", "source": {"room": "square"}, "phrase": "tax me",
                          "actions": [{"type": "fine", "faction": "watch", "amount": 12}]})
    with running(doc) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            watch = installed(db, "mud_world_factions", "watch")
            square = room(db, "square")
            mud.fire_triggers(db, user(db, one), "say_phrase", [square], phrase="tax me")
            assert _fines(db, one) == {watch: 12}
            mud.fire_triggers(db, user(db, one), "say_phrase", [square], phrase="mercy")
            assert _fines(db, one) == {}
            mud.fire_triggers(db, user(db, one), "say_phrase", [square], phrase="tax me")
            db.execute("UPDATE mud_character_crimes SET created_at=datetime('now','-2 hours')")
            assert mud._fines_owed(db, one) == 0


def test_package_round_trip():
    with running(_world()) as (app, _users):
        with app.app_context():
            db = get_db()
            payload = mud_worlds.export(db, WORLD)
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                content = json.loads(archive.read("content.json"))
                events = json.loads(archive.read("events.json"))
            assert manifest["crime"]["enabled"] is True
            npc_ids = {row["id"] for row in content["mud_npc_instances"]}
            lantern = next(row for row in content["mud_item_instances"] if row["state_json"].get("owner_npc"))
            assert lantern["state_json"]["owner_npc"] in npc_ids
            baker = next(row for row in content["mud_npc_definitions"] if row["name"] == "Baker")
            nail = next(row["id"] for row in content["mud_item_definitions"] if row["name"] == "Iron Nail")
            assert baker["behaviors_json"]["steal"]["items"] == [{"item": nail, "chance": 100}]
            factions = {row["id"] for row in content["mud_world_factions"]}
            assert events["triggers"][0]["source"]["faction"] in factions
            mud_worlds.read_package(payload)
    broken = _world()
    broken["content"]["mud_item_instances"][0]["state_json"] = {"owner_npc": "ghost"}
    with pytest.raises(mud_worlds.WorldError, match="unknown owner_npc"):
        mud_worlds.read_package(mud_worlds.package_bytes(broken))


def test_builder_crime_settings_and_item_owner():
    with running(_world()) as (app, (one, _two)):
        web = client(app, one)
        response = web.post(f"/mud/w/{WORLD}/builder/society/crime", data={
            "csrf_token": "test-token", "enabled": "1", "detection": "40", "fine_expiry_hours": "0",
            "fines.theft": "9", "fines.pickpocket": "8", "fines.assault": "7",
            "reputation.theft": "-1", "reputation.pickpocket": "-2", "reputation.assault": "-3"})
        assert response.status_code == 302
        with app.app_context():
            db = get_db()
            crime = mud.society_settings(db)["crime"]
            assert crime["detection"] == 40 and crime["fines"]["theft"] == 9 and crime["reputation"]["assault"] == -3
            nail = installed(db, "mud_item_instances", "free-nail")
            watch = installed(db, "mud_world_factions", "watch")
        web.post(f"/mud/w/{WORLD}/builder/item-instances/{nail}/owner", data={"csrf_token": "test-token",
                                                                    "owner": f"faction|{watch}"})
        with app.app_context():
            assert mud._item_owner(get_db(), nail) == (watch, None)
        assert b"Owner of Iron Nail" in web.get(f"/mud/w/{WORLD}/builder").data
