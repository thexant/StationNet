"""Factions: standing, ranks, relations between factions, and how standing changes play."""
import json
import sqlite3
import zipfile
from io import BytesIO

import pytest

from stationnet import mud, mud_compat, mud_conditions, mud_society, mud_triggers, mud_worlds
from stationnet.db import get_db

from mud_society_support import WORLD, act, client, document, installed, place, room, running, user


def _world():
    doc = document()
    content = doc["content"]
    content["mud_world_factions"] = [
        {"id": "guild", "name": "Smiths' Guild", "description": "Hammer and tongs."},
        {"id": "bandits", "name": "Red Hand", "defeat_reputation": 0,
         "ranks_json": [{"key": "enemy", "name": "Enemy", "min": -1000},
                        {"key": "outsider", "name": "Outsider", "min": -100},
                        {"key": "brother", "name": "Brother", "min": 200, "attitude": "peaceful",
                         "price_percent": 80}]},
        {"id": "watch", "name": "Town Watch", "public": 0, "default_reputation": 50},
    ]
    content["mud_faction_relations"] = [
        {"id": "rivals", "faction_id": "watch", "other_faction_id": "bandits", "share_percent": -50},
        {"id": "friends", "faction_id": "guild", "other_faction_id": "watch", "share_percent": 50}]
    npcs = content["mud_npc_definitions"]
    npcs[0]["faction_id"] = "guild"
    npcs[0]["behaviors_json"] = {}
    npcs.append({"id": "thug", "name": "Red Thug", "description": "Scarred.", "faction_id": "bandits"})
    npcs.append({"id": "guard", "name": "Watch Guard", "description": "Stern.", "faction_id": "watch",
                 "behaviors_json": {"neutral": True}})
    content["mud_npc_instances"] += [{"id": "thug-1", "definition_id": "thug", "room_id": "tavern"},
                                     {"id": "guard-1", "definition_id": "guard", "room_id": "forge"}]
    content["mud_npc_combat_profiles"] = [
        {"npc_definition_id": "thug", "maximum_health": 5, "attack": 2, "defense": 0,
         "experience_reward": 1, "respawn_seconds": 60},
        {"npc_definition_id": "guard", "maximum_health": 50, "attack": 3, "defense": 0,
         "experience_reward": 1, "respawn_seconds": 60}]
    content["mud_item_definitions"].append({"id": "badge", "name": "Guild Badge", "description": "Shiny.",
                                            "item_type": "misc", "properties_json": {"value": 5}})
    content["mud_shop_stock"].append({"id": "badges", "npc_definition_id": "smith", "item_definition_id": "badge",
                                      "price": 50, "conditions_json": [{"type": "reputation", "faction": "guild",
                                                                        "rank": ["friendly", "honored", "exalted"]}]})
    content["mud_quests"] = [{"id": "apprentice", "name": "Apprentice", "description": "Sweep the forge."}]
    content["mud_quest_objectives"] = [{"id": "sweep", "quest_id": "apprentice", "objective_type": "visit_room",
                                        "target_id": "forge", "description": "Visit the forge"}]
    content["mud_quest_rewards"] = [{"id": "guild-pay", "quest_id": "apprentice", "reward_type": "reputation",
                                     "target_id": "guild", "amount": 150}]
    content["mud_quest_givers"] = [{"quest_id": "apprentice", "npc_definition_id": "smith"}]
    doc["exits"][2]["conditions"] = [{"type": "reputation", "faction": "guild", "min": 0}]
    doc["exits"][2]["conditions_message"] = "Guild members only."
    doc["events"] = [{"id": "insult", "event": "say_phrase", "source": {"room": "square"}, "phrase": "smiths are fools",
                      "actions": [{"type": "reputation", "faction": "guild", "amount": -100}]}]
    return doc


def _standing(db, uid, local):
    row = mud_society.faction(db, installed(db, "mud_world_factions", local))
    return mud_society.standing(db, f"stationnet:character:{uid}", row)


def test_rank_rules_and_validation():
    ranks = mud_society.validate_ranks(None)
    assert [mud_society.rank_for(ranks, value)["key"] for value in (-1000, -500, 0, 150, 999)] == [
        "hated", "hostile", "neutral", "friendly", "exalted"]
    assert mud_society.rank_for(ranks, -2000)["key"] == "hated"
    for bad in ([{"key": "a", "name": "A", "min": 0}],
                [{"key": "a", "name": "A", "min": 5}, {"key": "b", "name": "B", "min": 1}],
                [{"key": "a", "name": "A", "min": 0}, {"key": "a", "name": "B", "min": 1}],
                [{"key": "a", "name": "A", "min": 0}, {"key": "b", "name": "B", "min": 1, "attitude": "angry"}]):
        with pytest.raises(ValueError):
            mud_society.validate_ranks(bad)
    assert mud_conditions.validate_conditions([{"type": "reputation", "faction": "x", "rank": "friendly"}])
    with pytest.raises(ValueError):
        mud_conditions.validate_conditions([{"type": "reputation", "faction": "x"}])
    with pytest.raises(ValueError):
        mud_triggers.validate_actions([{"type": "reputation", "faction": "x", "amount": 5000}])


def test_worlds_without_factions_are_unchanged():
    with running() as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            assert mud.reputation_state(db, one) == []
            assert "reputation" in mud._hidden_actions(db, one)
            with pytest.raises(mud.ActionError):
                act(db, one, "/reputation")


def test_standing_relations_and_reports():
    with running(_world()) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            assert "reputation" not in mud._hidden_actions(db, one)
            # The hidden watch never shows; its default standing is 50.
            assert [entry["name"] for entry in mud.reputation_state(db, one)] == ["Red Hand", "Smiths' Guild"]
            drafts = mud._change_reputation(db, user(db, one), installed(db, "mud_world_factions", "guild"), 120)
            assert drafts[0].body == "Your standing changes: Smiths' Guild +120 (now Friendly)."
            # Half passes to the watch, and none further to its rival (one hop only).
            assert _standing(db, one, "watch")[0] == 110
            assert _standing(db, one, "bandits")[0] == 0
            mud._change_reputation(db, user(db, one), installed(db, "mud_world_factions", "watch"), 100)
            assert _standing(db, one, "bandits")[0] == -50
            assert _standing(db, two, "guild")[0] == 0
            assert "Smiths' Guild: Friendly (+120)" in act(db, one, "/reputation")


def test_conditions_gate_exits_and_stock_and_prices():
    with running(_world()) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            mud._change_reputation(db, user(db, two), installed(db, "mud_world_factions", "guild"), -100)
            with pytest.raises(mud.ActionError, match="Guild members only"):
                act(db, two, "/go east")
            place(db, one, room(db, "home"))
            assert "Guild Badge" not in act(db, one, "/shop Smith")
            with pytest.raises(mud.ActionError, match="not sold here"):
                act(db, one, '/shop buy "Guild Badge"')
            mud._change_reputation(db, user(db, one), installed(db, "mud_world_factions", "guild"), 120)
            listing = act(db, one, "/shop Smith")
            # Friendly members charge 95%.
            assert "Guild Badge: 47 coins" in listing and "Iron Nail: 9 coins" in listing
            db.execute("UPDATE mud_character_wallets SET coins=100 WHERE character_id=?", (f"stationnet:character:{one}",))
            assert "for 47 coins" in act(db, one, '/shop buy "Guild Badge"')


def test_ranks_set_attitude_talk_and_trade():
    with running(_world()) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            place(db, one, room(db, "tavern"))
            place(db, two, room(db, "tavern"))
            tavern = mud._current_room(db, one)
            # The thug is aggressive by default...
            assert [npc["name"] for npc, _ in mud._active_hostiles(db, tavern, one)] == ["Red Thug"]
            mud._change_reputation(db, user(db, one), installed(db, "mud_world_factions", "bandits"), 250)
            # ...but leaves a Brother alone, and still attacks everyone else.
            assert not mud._active_hostiles(db, tavern, one)
            assert mud._active_hostiles(db, tavern, two)
            place(db, two, room(db, "home"))
            mud._change_reputation(db, user(db, two), installed(db, "mud_world_factions", "guild"), -700)
            with pytest.raises(mud.ActionError, match="refuses to speak"):
                act(db, two, "/talk Smith")
            with pytest.raises(mud.ActionError, match="will not trade"):
                act(db, two, "/shop Smith")
            # A neutral guard turns on players its faction hates.
            place(db, two, room(db, "forge"))
            mud._change_reputation(db, user(db, two), installed(db, "mud_world_factions", "watch"), -700)
            assert [npc["name"] for npc, _ in mud._active_hostiles(db, mud._current_room(db, two), two)] == ["Watch Guard"]


def test_quest_rewards_triggers_and_defeats():
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            mud._accept_quest(db, user(db, one), installed(db, "mud_quests", "apprentice"))
            place(db, one, room(db, "forge"))
            done = "\n".join(draft.body for draft in mud._set_objective_progress(
                db, user(db, one), "visit_room", room(db, "forge")))
            assert "standing: Smiths' Guild +150 (now Friendly)" in done
            place(db, one, room(db, "square"))
            mud.fire_triggers(db, user(db, one), "say_phrase", [room(db, "square")], phrase="Smiths are fools!")
            assert _standing(db, one, "guild")[0] == 50
            db.execute("UPDATE mud_world_factions SET defeat_reputation=-30 WHERE id=?",
                       (installed(db, "mud_world_factions", "bandits"),))
            place(db, one, room(db, "tavern"))
            for _ in range(3):
                if "is defeated" in act(db, one, '/attack "Red Thug"'):
                    break
            assert _standing(db, one, "bandits")[0] == -30


def test_package_round_trip_and_rejections():
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            mud._change_reputation(db, user(db, one), installed(db, "mud_world_factions", "guild"), 10)
            payload = mud_worlds.export(db, WORLD)
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                content = json.loads(archive.read("content.json"))
            names = {row["id"]: row["name"] for row in content["mud_world_factions"]}
            assert sorted(names.values()) == ["Red Hand", "Smiths' Guild", "Town Watch"]
            smith = next(row for row in content["mud_npc_definitions"] if row["name"] == "Smith")
            assert names[smith["faction_id"]] == "Smiths' Guild"
            assert {names[row["faction_id"]] for row in content["mud_faction_relations"]} == {"Town Watch", "Smiths' Guild"}
            reward = content["mud_quest_rewards"][0]
            assert reward["reward_type"] == "reputation" and names[reward["target_id"]] == "Smiths' Guild"
            badge = next(row for row in content["mud_shop_stock"] if row["price"] == 50)
            assert names[badge["conditions_json"][0]["faction"]] == "Smiths' Guild"
            # Reimports under another ID install cleanly.
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            manifest = json.loads(files["manifest.json"]); manifest["id"] = "lifeworld-copy"
            files["manifest.json"] = json.dumps(manifest).encode()
            stream = BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            db.commit()
            mud_worlds.install(db, stream.getvalue())
            assert db.execute("SELECT COUNT(*) FROM mud_world_factions WHERE world_id='lifeworld-copy'").fetchone()[0] == 3
            # Removing a world retires its factions, so standing survives. Only disabled worlds can go.
            mud_worlds.set_enabled(db, WORLD, False)
            db.commit()
            mud_worlds.remove(db, WORLD)
            assert db.execute("SELECT COUNT(*) FROM mud_character_reputation").fetchone()[0] >= 1
    for change in (lambda doc: doc["content"]["mud_faction_relations"].append(
                       {"id": "self", "faction_id": "guild", "other_faction_id": "guild", "share_percent": 10}),
                   lambda doc: doc["content"]["mud_world_factions"].append({"id": "x", "name": "X", "default_reputation": 5000}),
                   lambda doc: doc["content"]["mud_quest_rewards"][0].update(amount=5000)):
        doc = _world()
        change(doc)
        with pytest.raises(mud_worlds.WorldError):
            mud_worlds.read_package(mud_worlds.package_bytes(doc))
    doc = _world()
    doc["content"]["mud_npc_definitions"][0]["faction_id"] = "nobody"
    with pytest.raises(mud_worlds.WorldError):
        with running(doc):
            pass


def test_builder_faction_routes_and_npc_membership():
    with running() as (app, (one, two)):
        web = client(app, one)
        assert web.post(f"/mud/w/{WORLD}/builder/factions", data={"csrf_token": "test-token", "name": "Fishers", "public": "1",
                                                       "default_reputation": "10"}).status_code == 302
        web.post(f"/mud/w/{WORLD}/builder/factions", data={"csrf_token": "test-token", "name": "Pirates"})
        with app.app_context():
            db = get_db()
            fishers, pirates = [row["id"] for row in db.execute(
                "SELECT id FROM mud_world_factions ORDER BY name")]
        response = web.post(f"/mud/w/{WORLD}/builder/factions", data={"csrf_token": "test-token", "name": "Bad",
                                                           "ranks_json": "[{}]"}, follow_redirects=True)
        assert b"ranks" in response.data.lower()
        web.post(f"/mud/w/{WORLD}/builder/factions/{fishers}/relations",
                 data={"csrf_token": "test-token", "other_faction_id": pirates, "share_percent": "-40"})
        with app.app_context():
            db = get_db()
            smith = installed(db, "mud_npc_definitions", "smith")
            row = db.execute("SELECT * FROM mud_npc_definitions WHERE id=?", (smith,)).fetchone()
        web.post(f"/mud/w/{WORLD}/builder/npcs/{smith}", data={
            "csrf_token": "test-token", "name": row["name"], "description": row["description"],
            "properties_json": "{}", "behaviors_json": row["behaviors_json"], "faction_id": fishers,
            "is_merchant": "1", "buys_items": "1", "sell_percent": "50"})
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT faction_id FROM mud_npc_definitions WHERE id=?", (smith,)).fetchone()[0] == fishers
            assert db.execute("SELECT share_percent FROM mud_faction_relations").fetchone()[0] == -40
        page = web.get(f"/mud/w/{WORLD}/builder").data
        assert b"Fishers" in page
        assert b"-40% of changes pass to Pirates" in page
        assert client(app, two).post(f"/mud/w/{WORLD}/builder/factions", data={"csrf_token": "test-token",
                                                                    "name": "Nope"}).status_code == 403
        web.post(f"/mud/w/{WORLD}/builder/factions/{fishers}/delete", data={"csrf_token": "test-token", "confirmation": "delete"})
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT faction_id FROM mud_npc_definitions WHERE id=?", (smith,)).fetchone()[0] is None


def test_page_shows_standing_panel():
    with running(_world()) as (app, (one, _two)):
        page = client(app, one).get(f"/mud/w/{WORLD}").data
        assert b"data-mud-standing-panel" in page and b"Smiths&#39; Guild: Neutral (+0)" in page
        assert "standing" in client(app, one).get(f"/mud/w/{WORLD}/updates?client_id=browser-standing-1234").get_json()
