"""Combat: hostility, enemy packs, guarding, hit rolls, recovery, death, and shared rewards."""
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

from stationnet import create_app, character_creation, db as db_module, mud, mud_combat, mud_effects, mud_worlds
from stationnet.db import get_db


def _close(app):
    coordinator = app.extensions["broadcast_coordinator"]
    coordinator._closed = True
    coordinator._timer.cancel()


@pytest.fixture
def game():
    data_path = Path(__file__).parent / f"_mud_combat_{uuid.uuid4().hex}"
    data_path.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            db.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES('test:den','Den','A den.')")
            users = []
            for username, callsign, name in (("one", "ONE", "Ada One"), ("two", "TWO", "Bea Two")):
                uid = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
                                 (username, callsign, "unused")).lastrowid
                character_creation.complete(db, uid, {
                    "name": name, "race": "human", "class": "warrior",
                    "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                    "skills": ["lore", "guard"]})
                mud._set_player_location(db, uid, "traditional", "world_room", "test:den")
                users.append(uid)
            db.commit()
        yield app, users
    finally:
        _close(app)
        shutil.rmtree(data_path)


def _user(db, user_id):
    return db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def _cid(user_id):
    return f"stationnet:character:{user_id}"


def _state(db, user_id):
    return mud.character_state(db, user_id)


def _set(db, user_id, **values):
    for column, value in values.items():
        db.execute(f"UPDATE mud_character_progression SET {column}=? WHERE character_id=?", (value, _cid(user_id)))


def _npc(db, key, name, behaviors=None, *, health=1000, attack=10, defense=0, accuracy=None, evasion=None,
         experience=100, properties=None, room="test:den"):
    db.execute("INSERT INTO mud_npc_definitions(id,name,description,properties_json,behaviors_json) VALUES(?,?,?,?,?)",
               (key, name, f"A {name.casefold()}.", json.dumps(properties or {}), json.dumps(behaviors or {})))
    db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack,defense,"
               "experience_reward,accuracy,evasion) VALUES(?,?,?,?,?,?,?)",
               (key, health, attack, defense, experience, accuracy, evasion))
    db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES(?,?,?)", (f"{key}:1", key, room))
    return f"{key}:1"


def _encounter_health(db, instance_id):
    return db.execute("SELECT current_health FROM mud_combat_encounters WHERE npc_instance_id=? "
                      "ORDER BY rowid DESC LIMIT 1", (instance_id,)).fetchone()[0]


def _bodies(drafts):
    return [draft.body for draft in drafts]


def test_hit_chance_is_bounded_and_rewards_evasion_with_diminishing_returns():
    assert mud_combat.hit_chance(0, 0) == 85
    assert mud_combat.hit_chance(0, 8) == 77  # Agility 14 at level 1
    assert mud_combat.hit_chance(0, 30) == 60  # Agility 20 plus Evasion rank 10
    assert mud_combat.hit_chance(100, 0) == mud_combat.MAX_HIT == 95
    assert mud_combat.hit_chance(0, 1000) == mud_combat.MIN_HIT == 30
    assert mud_combat.effective_evasion(20) == 20 and mud_combat.effective_evasion(30) == 25
    assert [mud_combat.resolve(0, 10, roll) for roll in (74, 75, 84, 85, 99)] == \
        ["hit", "dodge", "dodge", "miss", "miss"]
    assert mud_combat.scaled_reward(100, 1.0) == 100
    assert mud_combat.scaled_reward(100, 0.5) == 70
    assert mud_combat.reward_threshold(2) == 0.10 and mud_combat.reward_threshold(10) == 0.05
    assert mud_combat.death_experience_loss(1500, 900) == 60


def test_neutral_npc_retaliates_only_against_its_attacker(game):
    app, (uid, other) = game
    with app.app_context():
        db = get_db()
        user, bystander = _user(db, uid), _user(db, other)
        _npc(db, "test:ferryman", "Ferryman", {"neutral": True}, attack=20)
        room = mud._current_room(db, uid)
        assert mud._active_hostiles(db, room, uid) == []
        with pytest.raises(mud.ActionError, match="no hostile"):
            mud._defend_action(db, user, {})
        assert "\nNeutral" in mud._look_action(db, user, {"arguments": ["Ferryman"]})[0].body

        before = _state(db, uid)["health"]
        drafts = mud._attack_action(db, user, {"arguments": ["Ferryman"]})
        assert any(body.startswith("Ferryman strikes for") for body in _bodies(drafts))
        assert _state(db, uid)["health"] < before
        assert "\nHostile" in mud._look_action(db, user, {"arguments": ["Ferryman"]})[0].body
        assert "\nNeutral" in mud._look_action(db, bystander, {"arguments": ["Ferryman"]})[0].body
        assert [npc["hostile"] for npc in mud._room_state(db, uid)["npcs"]] == [True]
        assert [npc["hostile"] for npc in mud._room_state(db, other)["npcs"]] == [False]

        # The fight is between the Ferryman and its attacker; the bystander is unaffected.
        assert mud._active_hostiles(db, room, other) == []
        with pytest.raises(mud.ActionError, match="no hostile"):
            mud._defend_action(db, bystander, {})
        assert "brace" in mud._defend_action(db, user, {})[0].body
        _set(db, uid, current_health=1)
        with pytest.raises(mud.ActionError, match="enemies are nearby"):
            mud._rest_action(db, user, {})


def test_passive_npc_never_strikes_back(game):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        user = _user(db, uid)
        dummy = _npc(db, "test:dummy", "Dummy", {"passive": True}, attack=50)
        before = _state(db, uid)["health"]
        drafts = mud._attack_action(db, user, {"arguments": ["Dummy"]})
        assert _encounter_health(db, dummy) < 1000
        assert not any("Dummy strikes" in body for body in _bodies(drafts))
        assert _state(db, uid)["health"] == before


def test_every_engaged_enemy_strikes_and_lingering_damage_ticks_once(game):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        user = _user(db, uid)
        _npc(db, "test:grey", "Grey Wolf", attack=6)
        black = _npc(db, "test:black", "Black Wolf", attack=6)
        mud_effects.apply(db, "character", _cid(uid), {"key": "poison", "potency": 2, "duration": 3})
        before = _state(db, uid)["health"]
        drafts = mud._enemy_turn(db, user, chosen=black)
        strikes = [body for body in _bodies(drafts) if " strikes for " in body]
        assert len(strikes) == 2 and strikes[0].startswith("Black Wolf")
        assert sum("from poison" in body for body in _bodies(drafts)) == 1
        dealt = sum(int(body.split(" strikes for ")[1].split()[0]) for body in strikes)
        assert before - _state(db, uid)["health"] == dealt + 2

        # A default-strength pack is dangerous but survivable for a fresh character.
        mud_effects.clear(db, "character", _cid(uid))
        _set(db, uid, current_health=_state(db, uid)["maximum_health"])
        mitigation = _state(db, uid)["combat"]["mitigation"]
        for _ in range(3):
            mud._enemy_turn(db, user)
        state = _state(db, uid)
        # Guard practice can only lower later blows, so three exchanges cost at most this much.
        assert 0 < state["maximum_health"] - state["health"] <= 3 * 2 * max(1, 6 - mitigation)
        assert state["health"] > state["maximum_health"] // 2


def test_entry_attack_gives_only_the_first_enemy_a_free_strike(game):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        user = _user(db, uid)
        first = _npc(db, "test:grey", "Grey Wolf")
        second = _npc(db, "test:black", "Black Wolf")
        bodies = _bodies(mud._entry_attack(db, user))
        assert sum(" strikes for " in body for body in bodies) == 1
        assert sum("joins the fight" in body for body in bodies) == 1
        room = mud._current_room(db, uid)
        engaged = {npc["id"] for npc, encounter in mud._active_hostiles(db, room, uid)
                   if mud._engaged(db, encounter["id"], _cid(uid))}
        assert engaged == {first, second}


def test_guard_draws_attacks_until_the_window_expires(game):
    app, (uid, other) = game
    with app.app_context():
        db = get_db()
        user, guard = _user(db, uid), _user(db, other)
        _npc(db, "test:wolf", "Wolf", attack=10)
        guard_health = _state(db, other)["health"]
        mud._attack_action(db, user, {"arguments": ["Wolf"]})
        assert _state(db, other)["health"] == guard_health  # idle players are never hit by others' turns

        mud._defend_action(db, guard, {})
        guard_health = _state(db, other)["health"]
        actor_health = _state(db, uid)["health"]
        drafts = mud._attack_action(db, user, {"arguments": ["Wolf"]})
        assert "Bea Two intercepts Wolf's attack." in _bodies(drafts)
        assert _state(db, uid)["health"] == actor_health
        assert _state(db, other)["health"] < guard_health
        guard_event = next(draft for draft in drafts if " strikes for " in draft.body)
        assert guard_event.recipient_ids == (other,)
        assert db.execute("SELECT damage_taken FROM mud_combat_participants WHERE character_id=?",
                          (_cid(other),)).fetchone()[0] > 0

        db.execute("UPDATE mud_combat_participants SET guarding_until='2000-01-01 00:00:00'")
        mud._attack_action(db, user, {"arguments": ["Wolf"]})
        assert _state(db, uid)["health"] < actor_health


@pytest.mark.combat_rolls
def test_misses_and_dodges_deal_no_damage_effects_or_practice(game, monkeypatch):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        user = _user(db, uid)
        wraith = _npc(db, "test:wraith", "Wraith", evasion=10,
                      properties={"on_hit_effects": [{"key": "poison", "potency": 2, "duration": 3}]})
        monkeypatch.setattr(mud, "_hit_roll", lambda: 99)

        # Ambushes from hiding always land, even on a roll that would miss.
        monkeypatch.setattr(mud.secrets, "randbelow", lambda _limit: 0)
        mud._hide_action(db, user, {})
        surprise = mud._attack_action(db, user, {"arguments": ["Wraith"]})
        assert "surprises Wraith for" in surprise[0].body and _encounter_health(db, wraith) < 1000

        blades = lambda: db.execute("SELECT experience FROM mud_character_skills WHERE character_id=? "
                                    "AND skill_key='blades'", (_cid(uid),)).fetchone()[0]
        health, practice, enemy = _state(db, uid)["health"], blades(), _encounter_health(db, wraith)
        drafts = mud._attack_action(db, user, {"arguments": ["Wraith"]})
        assert drafts[0].body == f"Ada One attacks Wraith but misses ({enemy}/1000 health)."
        assert f"Wraith strikes at you but misses ({health}/{_state(db, uid)['maximum_health']} health)." \
            in _bodies(drafts)
        assert (_encounter_health(db, wraith), _state(db, uid)["health"], blades()) == (enemy, health, practice)
        assert not mud_effects.list_effects(db, "character", _cid(uid))

        # A roll that only the target's evasion turns aside is a dodge, for NPCs and players alike.
        db.execute("INSERT INTO mud_character_skills(character_id,skill_key,rank,experience) "
                   "VALUES(?,'evasion',1,0)", (_cid(uid),))
        accuracy = _state(db, uid)["combat"]["melee_accuracy"]
        rolls = iter([mud_combat.hit_chance(accuracy, 0) - 1, mud_combat.hit_chance(0, 0) - 1])
        monkeypatch.setattr(mud, "_hit_roll", lambda: next(rolls))
        bodies = _bodies(mud._attack_action(db, user, {"arguments": ["Wraith"]}))
        assert bodies[0].startswith("Wraith dodges Ada One's attack")
        assert any(body.startswith("You dodge Wraith's attack") for body in bodies)
        assert db.execute("SELECT experience FROM mud_character_skills WHERE character_id=? "
                          "AND skill_key='evasion'", (_cid(uid),)).fetchone()[0] == 1

        # Damage spells roll and still cost mana on a miss; self spells never roll.
        monkeypatch.setattr(mud, "_hit_roll", lambda: 99)
        mana = _state(db, uid)["mana"]
        assert "casts at Wraith but misses" in mud._cast_action(db, user, {"arguments": ["rend", "Wraith"]})[0].body
        assert _state(db, uid)["mana"] == mana - mud.SPELLS["rend"]["cost"]
        assert _encounter_health(db, wraith) == enemy
        assert "gain might" in mud._cast_action(db, user, {"arguments": ["might"]})[0].body


@pytest.mark.combat_rolls
def test_missed_shot_still_spends_a_round(game, monkeypatch):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        user = _user(db, uid)
        db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,equipment_slot,properties_json) "
                   "VALUES('test:gun','Pistol','Pistol','equipment','main_hand',?)",
                   (json.dumps({"firearm": True, "ammo_type": "9mm", "magazine_size": 2, "attack_bonus": 3}),))
        db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,properties_json) "
                   "VALUES('test:ammo','Rounds','Rounds','misc',?)", (json.dumps({"ammo_type": "9mm", "rounds": 2}),))
        gun = mud._grant_item(db, uid, "test:gun")
        mud._grant_item(db, uid, "test:ammo")
        mud._equip_action(db, user, {"item_id": gun})
        mud._reload_action(db, user, {})
        target = _npc(db, "test:target", "Target", {"passive": True})
        monkeypatch.setattr(mud, "_hit_roll", lambda: 99)
        assert "fires at Target but misses" in mud._fire_action(db, user, {"arguments": ["Target"]})[0].body
        assert mud._loaded_rounds(mud._item_row(db, gun)) == 1
        assert _encounter_health(db, target) == 1000


def test_accuracy_and_evasion_follow_attributes_level_gear_and_effects(game):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        user = _user(db, uid)
        base = _state(db, uid)["combat"]
        assert base["evasion"] == 2 and base["chance_to_be_hit"] == 83

        _set(db, uid, agility=14)
        nimble = _state(db, uid)["combat"]
        assert nimble["evasion"] == base["evasion"] + 6
        assert nimble["chance_to_be_hit"] < base["chance_to_be_hit"]
        assert nimble["mitigation"] == base["mitigation"]  # evasion and damage reduction stay distinct

        _set(db, uid, level=10)
        veteran = _state(db, uid)["combat"]
        assert veteran["melee_accuracy"] == nimble["melee_accuracy"] + 4
        assert veteran["evasion"] == nimble["evasion"] + 2

        db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,equipment_slot,properties_json) "
                   "VALUES('test:charm','Heavy Charm','A charm','equipment','accessory',?)",
                   (json.dumps({"accuracy": 3, "evasion": -5}),))
        mud._equip_action(db, user, {"item_id": mud._grant_item(db, uid, "test:charm")})
        geared = _state(db, uid)["combat"]
        assert (geared["melee_accuracy"], geared["evasion"]) == \
            (veteran["melee_accuracy"] + 3, veteran["evasion"] - 5)

        mud_effects.apply(db, "character", _cid(uid), {"key": "snared", "potency": 4, "duration": 3})
        mud_effects.apply(db, "character", _cid(uid), {"key": "focus", "potency": 2, "duration": 3})
        affected = _state(db, uid)["combat"]
        assert (affected["melee_accuracy"], affected["evasion"]) == \
            (geared["melee_accuracy"] + 2, geared["evasion"] - 4)


def test_rest_recovers_partially_with_a_cooldown(game):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        user = _user(db, uid)
        state = _state(db, uid)
        _set(db, uid, current_health=1, current_mana=0)
        assert "You rest and recover" in mud._rest_action(db, user, {})[0].body
        rested = _state(db, uid)
        assert rested["health"] == 1 + state["maximum_health"] * 25 // 100
        assert rested["mana"] == state["maximum_mana"] * 40 // 100
        with pytest.raises(mud.ActionError, match="moment before resting"):
            mud._rest_action(db, user, {})
        db.execute("UPDATE mud_character_progression SET last_rested_at='2000-01-01 00:00:00'")
        mud._rest_action(db, user, {})
        assert _state(db, uid)["health"] == 1 + 2 * (state["maximum_health"] * 25 // 100)
        _set(db, uid, current_health=state["maximum_health"], current_mana=state["maximum_mana"])
        with pytest.raises(mud.ActionError, match="fully rested"):
            mud._rest_action(db, user, {})


def test_world_death_costs_progress_but_never_a_level(game):
    app, (uid, _) = game
    with app.app_context():
        db = get_db()
        user = _user(db, uid)
        _npc(db, "test:bear", "Bear", attack=500)
        floor = mud.experience_for_level(3)
        _set(db, uid, level=3, experience=floor + 300, current_health=1)
        drafts = mud._enemy_turn(db, user)
        state = _state(db, uid)
        assert "You lose 30 experience." in drafts[-2].body
        assert (state["level"], state["experience"]) == (3, floor + 270)
        assert state["health"] == state["maximum_health"] // 2
        assert state["mana"] == state["maximum_mana"] // 2
        assert mud._current_room(db, uid)["id"] == mud._start_room(db)["id"]
        # Dying from 1 health never beats two rests, and it also costs experience.
        assert state["health"] <= 1 + 2 * (state["maximum_health"] * 25 // 100)


def test_shared_kill_rewards_follow_contribution(game, monkeypatch):
    app, (uid, other) = game
    with app.app_context():
        db = get_db()
        awarded = []
        real_award = mud.award_experience
        monkeypatch.setattr(mud, "award_experience", lambda database, user_id, amount, track_key=None: (
            awarded.append((user_id, amount)), real_award(database, user_id, amount, track_key))[1])

        def kill(key, contributions, coins=0):
            instance = _npc(db, key, key.split(":")[1].title(), {"passive": True}, experience=100)
            if coins:
                db.execute("INSERT INTO mud_npc_combat_rewards(id,npc_definition_id,reward_type,target_id,"
                           "amount,chance_percent) VALUES(?,?,'coins',NULL,?,100)", (f"{key}:coins", key, coins))
            npc = next(row for row in mud._room_npc_rows(db, "test:den") if row["id"] == instance)
            encounter = mud._encounter_row(db, npc)
            for user_id, (dealt, taken) in contributions.items():
                db.execute("INSERT INTO mud_combat_participants(encounter_id,character_id,damage_dealt,damage_taken) "
                           "VALUES(?,?,?,?)", (encounter["id"], _cid(user_id), dealt, taken))
            awarded.clear()
            return _bodies(mud._encounter_reward_drafts(db, encounter, npc))

        assert kill("test:solo", {uid: (100, 0)}) and awarded == [(uid, 100)]
        bodies = kill("test:tagged", {uid: (95, 0), other: (5, 0)})
        assert awarded == [(uid, 97)]
        assert any("contributed too little" in body for body in bodies)
        before = {user_id: mud._wallet(db, _cid(user_id)) for user_id in (uid, other)}
        kill("test:shared", {uid: (50, 0), other: (50, 0)}, coins=10)
        assert awarded == [(uid, 70), (other, 70)]
        assert all(mud._wallet(db, _cid(user_id)) - before[user_id] == 7 for user_id in (uid, other))
        # Blows absorbed by a guard count half toward their share.
        kill("test:guarded", {uid: (60, 0), other: (0, 80)})
        assert awarded == [(uid, 76), (other, 64)]


@pytest.fixture
def delve():
    data_path = Path(__file__).parent / f"_mud_combat_delve_{uuid.uuid4().hex}"
    data_path.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_procedural_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            uid = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('delver','DLV','unused')"
                             ).lastrowid
            db.execute("UPDATE mud_characters SET creation_state='complete' WHERE user_id=?", (uid,))
            mud._set_player_location(db, uid, "procedural", "portal", mud.PORTAL_ROOM_ID)
            db.commit()
        yield app, uid
    finally:
        _close(app)
        shutil.rmtree(data_path)


def test_dungeon_scaling_rest_and_death_follow_procedural_rules(delve):
    app, uid = delve
    with app.app_context():
        db = get_db()
        user = _user(db, uid)
        mud._expedition_action(db, user, {"theme_id": "generic-fantasy-dungeon", "difficulty": 5, "size": 8})
        run_id = mud._active_expedition(db, uid)["id"]
        enemies = db.execute(
            "SELECT c.id,c.snapshot_json,r.difficulty FROM mud_dungeon_content AS c JOIN mud_dungeon_rooms AS r "
            "ON r.instance_id=c.instance_id AND r.room_key=c.room_key "
            "WHERE c.instance_id=? AND c.content_type='enemy'", (run_id,)).fetchall()
        assert enemies
        for enemy in enemies:
            snapshot = json.loads(enemy["snapshot_json"])
            scale = max(0, enemy["difficulty"] - 2)
            assert (snapshot["accuracy"], snapshot["evasion"]) == (2 * scale, scale)

        # Clear the entrance so it is safe to rest; a dungeon breather restores less than the world.
        entrance = mud._current_room(db, uid)
        db.execute("INSERT INTO mud_dungeon_content_state(content_id,status) SELECT id,'defeated' "
                   "FROM mud_dungeon_content WHERE instance_id=? AND room_key=? AND content_type='enemy'",
                   (run_id, entrance["id"]))
        maximum = _state(db, uid)["maximum_health"]
        _set(db, uid, current_health=1)
        mud._rest_action(db, user, {})
        assert _state(db, uid)["health"] == 1 + maximum * 15 // 100

        # A dungeon death ends the run and costs its loot, but never experience.
        db.execute("UPDATE mud_dungeon_content SET room_key=? WHERE id=?", (entrance["id"], enemies[0]["id"]))
        db.execute("DELETE FROM mud_dungeon_content_state WHERE content_id=?", (enemies[0]["id"],))
        _set(db, uid, current_health=1, experience=150, level=2)
        mud._entry_attack(db, user)
        assert mud._current_room(db, uid)["id"] == "stationnet:death-summary"
        mud._continue_action(db, user, {})
        state = _state(db, uid)
        assert (state["experience"], state["level"]) == (150, 2)
        assert state["health"] == state["maximum_health"] // 2


def _duel_world(**profile):
    return {
        "id": "duel", "name": "Duel World", "author": "Builder", "start_room": "arena",
        "rooms": [{"id": "arena", "name": "Arena", "description": "Sand and banners."}], "exits": [],
        "content": {
            "mud_npc_definitions": [{"id": "fencer", "name": "Fencer", "description": "A fencer."},
                                    {"id": "brute", "name": "Brute", "description": "A brute."}],
            "mud_npc_combat_profiles": [{"npc_definition_id": "fencer", "maximum_health": 40, "attack": 5,
                                         **profile},
                                        {"npc_definition_id": "brute", "maximum_health": 60, "attack": 8}],
            "mud_npc_instances": [{"id": "fencer-1", "definition_id": "fencer", "room_id": "arena"},
                                  {"id": "brute-1", "definition_id": "brute", "room_id": "arena"}],
        },
    }


def test_world_packages_carry_optional_accuracy_and_evasion(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        with pytest.raises(mud_worlds.WorldError, match="accuracy and evasion"):
            mud_worlds.read_package(mud_worlds.package_bytes(_duel_world(accuracy=500)))
        mud_worlds.install(db, mud_worlds.package_bytes(_duel_world(accuracy=12, evasion=7)))
        profiles = {row["name"]: (row["accuracy"], row["evasion"]) for row in db.execute(
            "SELECT d.name,p.accuracy,p.evasion FROM mud_npc_combat_profiles AS p JOIN mud_npc_definitions AS d "
            "ON d.id=p.npc_definition_id WHERE d.world_id='duel'")}
        assert profiles == {"Fencer": (12, 7), "Brute": (None, None)}
        exported = mud_worlds.read_package(mud_worlds.export(db, "duel"))
        assert {(row["accuracy"], row["evasion"]) for row in
                exported["content"]["mud_npc_combat_profiles"]} == {(12, 7), (None, None)}


def test_combat_balance_migration_keeps_existing_rows():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript("""
        CREATE TABLE mud_character_progression(character_id TEXT, track_key TEXT, current_health INTEGER);
        CREATE TABLE mud_combat_participants(encounter_id TEXT, character_id TEXT, damage_dealt INTEGER);
        CREATE TABLE mud_npc_combat_profiles(npc_definition_id TEXT, attack INTEGER);
        INSERT INTO mud_character_progression VALUES('hero','shared',42);
        INSERT INTO mud_combat_participants VALUES('fight','hero',9);
        INSERT INTO mud_npc_combat_profiles VALUES('wolf',6);
    """)
    db_module._migrate_mud_combat_balance(database)
    db_module._migrate_mud_combat_balance(database)
    assert tuple(database.execute("SELECT current_health,last_rested_at FROM mud_character_progression").fetchone()) \
        == (42, None)
    assert tuple(database.execute("SELECT damage_dealt,damage_taken,guarding_until "
                                  "FROM mud_combat_participants").fetchone()) == (9, 0, None)
    assert tuple(database.execute("SELECT attack,accuracy,evasion FROM mud_npc_combat_profiles").fetchone()) \
        == (6, None, None)
