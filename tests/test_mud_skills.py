"""Data-driven skills change real outcomes, grow through use, and can be learned later."""
import json
import re
import shutil
import uuid
from pathlib import Path

import pytest

from stationnet import character_creation, create_app, mud, mud_compat, mud_skills
from stationnet.db import get_db

BUILD = {
    "name": "Skilled Scholar", "race": "human", "class": "scholar",
    "attributes": {"strength": 12, "agility": 10, "intellect": 12, "vitality": 10},
    "skills": ["lore", "guard"],
}


def _close(app):
    coordinator = app.extensions.get("broadcast_coordinator") if app else None
    if coordinator:
        coordinator._closed = True
        coordinator._timer.cancel()


@pytest.fixture
def data_path():
    path = Path(__file__).parent / f"_mud_skills_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


@pytest.fixture
def game(data_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled,"
                       "mud_procedural_enabled) VALUES('Test','Test','TST',1,1,1)")
            user_id = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                 "VALUES('player','PLY','unused')").lastrowid
            character_creation.complete(db, user_id, BUILD)
            mud._set_player_location(db, user_id, "traditional", "world_room", mud._start_room(db)["id"])
            db.commit()
        with app.app_context():
            yield get_db(), user_id
    finally:
        _close(app)


def _user(db, user_id):
    return db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def _cid(user_id):
    return f"stationnet:character:{user_id}"


def _rank(db, user_id, key, rank, experience=0):
    db.execute("INSERT INTO mud_character_skills(character_id,skill_key,rank,experience) VALUES(?,?,?,?) "
               "ON CONFLICT(character_id,skill_key) DO UPDATE SET rank=excluded.rank,experience=excluded.experience",
               (_cid(user_id), key, rank, experience))


def _skill(db, user_id, key):
    return db.execute("SELECT * FROM mud_character_skills WHERE character_id=? AND skill_key=?",
                      (_cid(user_id), key)).fetchone()


def _combat(db, user_id):
    return mud.character_state(db, user_id)["combat"]


def _equip(db, user_id, name, slot, properties):
    definition_id = f"test:item:{uuid.uuid4().hex}"
    db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,properties_json,equipment_slot) "
               "VALUES(?,?,'Test gear','equipment',?,?)", (definition_id, name, json.dumps(properties), slot))
    mud._grant_item(db, user_id, definition_id, "test:item")
    return mud._equip_action(db, _user(db, user_id), {"arguments": [name]})


def _npc(db, name, *, trainer=None, shop_item=None):
    npc_id = f"test:npc:{uuid.uuid4().hex}"
    db.execute("INSERT INTO mud_npc_definitions(id,name,description) VALUES(?,?,'A test NPC')", (npc_id, name))
    db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES(?,?,?)",
               (f"{npc_id}:here", npc_id, mud._start_room(db)["id"]))
    if trainer:
        db.execute("INSERT INTO mud_npc_trainers(id,npc_definition_id,skill_key,price,max_rank) VALUES(?,?,?,?,?)",
                   (f"{npc_id}:lesson", npc_id, *trainer))
    if shop_item:
        item_id = f"test:item:{uuid.uuid4().hex}"
        db.execute("INSERT INTO mud_item_definitions(id,name,description) VALUES(?,?,'Stock')", (item_id, shop_item[0]))
        db.execute("INSERT INTO mud_npc_shops(npc_definition_id) VALUES(?)", (npc_id,))
        db.execute("INSERT INTO mud_shop_stock(id,npc_definition_id,item_definition_id,price) VALUES(?,?,?,?)",
                   (f"{npc_id}:stock", npc_id, item_id, shop_item[1]))
    return npc_id


def _coins(db, user_id, amount=None):
    mud._wallet(db, _cid(user_id))
    if amount is not None:
        db.execute("UPDATE mud_character_wallets SET coins=? WHERE character_id=?", (amount, _cid(user_id)))
    return mud._wallet(db, _cid(user_id))


def _import(db, document):
    return mud_skills.import_skill(db, json.dumps(document).encode())


def test_built_in_skills_validate_and_cover_the_effect_vocabulary(game):
    db, _user_id = game
    built_in = mud_skills.definitions(db)
    assert {"arms", "guard", "lore", "marksmanship", "blades", "stealth", "lockpicking"} <= set(built_in)
    used = {effect["type"] for definition in built_in.values() for effect in definition["effects"]}
    # attack stays for race/class effects; every other type has a default skill using it.
    assert used == set(mud_skills.EFFECT_TYPES) - {"attack"}
    assert all(definition["practice"]["triggers"] for definition in built_in.values())
    legacy = mud_skills.normalize({"key": "old-arms", "name": "Old Arms", "description": "Legacy.",
                                   "effect": "attack", "per_rank": 2})
    assert legacy["effects"] == [{"type": "attack", "per_rank": 2, "every": 1}]
    assert legacy["practice"]["triggers"] == ["melee_hit", "firearm_hit"]
    base = {"key": "custom", "name": "Custom", "description": "Custom skill.",
            "effects": [{"type": "search", "per_rank": 2}]}
    for change, message in (
        ({"effects": [{"type": "flight", "per_rank": 1}]}, "supported type"),
        ({"effects": []}, "at least one effect"),
        ({"practice": {"triggers": ["dancing"]}}, "triggers"),
        ({"effects": [{"type": "spell_power", "weapon_category": "blade"}]}, "weapon_category"),
        ({"effects": [{"type": "search", "per_rank": 500}]}, "per_rank"),
        ({"requirements": {"skills": {"missing-skill": 2}}}, "not installed"),
        ({"requirements": {"classes": ["no-such-class"]}}, "installed classes"),
        ({"group": "cooking"}, "group"),
        ({"surprise": True}, "Unsupported"),
    ):
        with pytest.raises(ValueError, match=message):
            mud_skills.validate_skill({**base, **change}, db)
    assert mud_skills.validate_skill({**base, "requirements": {"classes": ["scholar"], "skills": {"lore": 2}}},
                                     db)["requirements"]["classes"] == ["scholar"]


def test_creation_grants_origin_skills_and_filters_choices(game):
    db, user_id = game
    perception = _skill(db, user_id, "perception")
    assert (perception["rank"], perception["source"]) == (1, "granted")
    assert _skill(db, user_id, "lore")["rank"] == 2  # scholar's favored skill
    config = character_creation.options(db)
    scholar = next(item for item in config["classes"] if item["key"] == "scholar")
    human = next(item for item in config["races"] if item["key"] == "human")
    eligible = {skill["key"] for skill in character_creation.eligible_skills(
        config, human, scholar, BUILD["attributes"])}
    assert "perception" not in eligible and {"lore", "guard", "stealth"} <= eligible
    _import(db, {"key": "duelist", "name": "Duelist", "description": "Warriors only.",
                 "effects": [{"type": "melee_damage"}], "requirements": {"classes": ["warrior"]}})
    config = character_creation.options(db)
    eligible = {skill["key"] for skill in character_creation.eligible_skills(
        config, human, scholar, BUILD["attributes"])}
    assert "duelist" not in eligible
    newcomer = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('new','NEW','x')").lastrowid
    with pytest.raises(ValueError, match="starting skills"):
        character_creation.complete(db, newcomer, {**BUILD, "name": "Second Scholar",
                                                   "skills": ["lore", "perception"]})


def test_melee_firearm_magic_and_mobility_effects(game):
    db, user_id = game
    before = _combat(db, user_id)
    _rank(db, user_id, "arms", 5)
    _rank(db, user_id, "marksmanship", 4)
    _rank(db, user_id, "evocation", 3)
    _rank(db, user_id, "restoration", 2)
    _rank(db, user_id, "stealth", 20)
    _rank(db, user_id, "evasion", 20)
    _rank(db, user_id, "haggling", 20)
    after = _combat(db, user_id)
    # Arms is melee only; Marksmanship covers firearms (the old Arms bonus leaked into both).
    assert after["melee_damage"] - before["melee_damage"] == 5
    assert after["firearm_damage"] - before["firearm_damage"] == 4
    assert after["spell_power"] - before["spell_power"] == 3
    assert after["healing_bonus"] - before["healing_bonus"] == 4
    assert after["stealth_chance"] == min(95, before["stealth_chance"] + 30)
    assert after["flee_chance"] == min(95, before["flee_chance"] + 20)
    assert after["shop_discount"] == 30  # capped at the skill's maximum
    assert mud._stealth_chance(10, 5, 50) == 95 and mud._flee_chance(10, 0) == 50


def test_weapon_and_armor_proficiency_bonus_and_untrained_penalty(game):
    db, user_id = game
    base = _combat(db, user_id)
    events = _equip(db, user_id, "Steel Sword", "main_hand", {"attack_bonus": 8, "category": "blade"})
    assert "untrained with blade" in events[-1].body
    untrained = _combat(db, user_id)
    assert untrained["proficiency_warnings"] == ["Steel Sword"]
    melee_from_sword = untrained["melee_damage"] - base["melee_damage"]
    _rank(db, user_id, "blades", 4)
    trained = _combat(db, user_id)
    assert trained["proficiency_warnings"] == []
    # Full gear bonus (+2 over 75%) plus +1 per two Blades ranks.
    assert trained["melee_damage"] - base["melee_damage"] == melee_from_sword + 2 + 2
    _equip(db, user_id, "Oak Club", "main_hand", {"attack_bonus": 8})
    plain = _combat(db, user_id)
    assert plain["proficiency_warnings"] == [] and plain["melee_damage"] - base["melee_damage"] == melee_from_sword + 2
    _equip(db, user_id, "Plate Coat", "body", {"defense_bonus": 4, "category": "heavy"})
    unarmored = _combat(db, user_id)["mitigation"]
    _rank(db, user_id, "heavy-armor", 4)
    assert _combat(db, user_id)["mitigation"] - unarmored == 1 + 2


def test_practice_follows_triggers_ranks_up_and_respects_the_soft_cap(game):
    db, user_id = game
    cid = _cid(user_id)
    _rank(db, user_id, "arms", 1)
    assert mud_skills.practice(db, cid, "firearm_hit") == []
    assert _skill(db, user_id, "arms")["experience"] == 0
    notices = mud_skills.practice(db, cid, "melee_hit", 6)
    assert notices == ["Your Arms skill rises to rank 2."]
    # Rank r costs 6r practice, so 12 more uses reach rank 3.
    assert mud_skills.practice(db, cid, "melee_hit", 12) == ["Your Arms skill rises to rank 3."]
    # Level 1 characters cap at rank 5 until they level.
    capped = mud_skills.practice(db, cid, "melee_hit", 500)
    assert capped[0] == "Your Arms skill rises to rank 5."
    assert "advance no further until you reach level 2" in capped[1]
    held = _skill(db, user_id, "arms")
    assert (held["rank"], held["experience"]) == (5, mud_skills.next_rank_cost(5) - 1)
    assert mud_skills.practice(db, cid, "melee_hit", 5) == []  # the hint is not repeated
    db.execute("UPDATE mud_character_progression SET level=3 WHERE character_id=?", (cid,))
    assert mud_skills.practice(db, cid, "melee_hit") == ["Your Arms skill rises to rank 6."]
    # Category skills only practice with matching gear equipped.
    _rank(db, user_id, "blades", 1)
    mud_skills.practice(db, cid, "melee_hit", 3, categories={"blunt"})
    assert _skill(db, user_id, "blades")["experience"] == 0
    mud_skills.practice(db, cid, "melee_hit", 3, categories={"blade"})
    assert _skill(db, user_id, "blades")["experience"] == 3


def test_guard_learns_only_from_real_blows_and_lore_scales(game, monkeypatch):
    db, user_id = game
    user = _user(db, user_id)
    db.execute("INSERT INTO mud_npc_definitions(id,name,description) VALUES('test:gnat','Gnat','A gnat')")
    db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES('test:gnat:here','test:gnat',?)",
               (mud._start_room(db)["id"],))
    db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack,defense,"
               "experience_reward,respawn_seconds) VALUES('test:gnat',50,1,0,1,60)")
    calls = []
    real_practice = mud_skills.practice
    monkeypatch.setattr(mud_skills, "practice",
                        lambda database, cid, trigger, *args, **kwargs:
                        calls.append(trigger) or real_practice(database, cid, trigger, *args, **kwargs))
    mud._defend_action(db, user, {})  # a 1-attack gnat is fully blocked while defending
    assert "damage_taken" not in calls and "defend" in calls
    calls.clear()
    mud._enemy_turn(db, user)
    assert "damage_taken" in calls
    _rank(db, user_id, "lore", 2, 0)
    result = mud.award_experience(db, user_id, 400)
    # Lore practice scales with the boosted award instead of counting once per award.
    after = _skill(db, user_id, "lore")
    progress = sum(mud_skills.next_rank_cost(rank) for rank in range(2, after["rank"])) + after["experience"]
    assert result["amount"] > 400 and progress == result["amount"] // 25
    assert result["skill_notices"] == ["Your Lore skill rises to rank 3."]


def test_search_difficulty_lockpicking_and_haggling(game, monkeypatch):
    db, user_id = game
    user = _user(db, user_id)
    room_id = mud._start_room(db)["id"]
    db.execute("INSERT INTO mud_room_discoveries(id,room_id,clue,difficulty) VALUES('test:clue',?,"
               "'A loose stone hides a niche.',60)", (room_id,))
    monkeypatch.setattr(mud.secrets, "randbelow", lambda _limit: 99)
    missed = mud._search_action(db, user, {})
    assert "eludes you" in missed[0].body
    assert _skill(db, user_id, "perception")["experience"] == 1  # granted by the scholar class
    monkeypatch.setattr(mud.secrets, "randbelow", lambda _limit: 0)
    assert mud._search_action(db, user, {})[0].body == "A loose stone hides a niche."
    assert mud._search_action(db, user, {})[0].body == "You search carefully but find nothing new."
    assert _skill(db, user_id, "perception")["experience"] == 2  # empty searches teach nothing

    target = db.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES('test:vault','Vault','A vault')")
    db.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction,pick_difficulty) "
               "VALUES('test:door',?,'test:vault','iron door','north',40)", (room_id,))
    with pytest.raises(mud.ActionError, match="/pick"):
        mud._go_action(db, user, {"arguments": ["north"]})
    with pytest.raises(mud.ActionError, match="no keyhole"):
        mud._unlock_action(db, user, {"arguments": ["north"]})
    _rank(db, user_id, "lockpicking", 1)
    monkeypatch.setattr(mud.secrets, "randbelow", lambda _limit: 99)
    assert "resists" in mud._pick_action(db, user, {"arguments": ["north"]})[0].body
    monkeypatch.setattr(mud.secrets, "randbelow", lambda _limit: 0)
    assert "You pick the lock" in mud._pick_action(db, user, {"arguments": ["north"]})[0].body
    assert _skill(db, user_id, "lockpicking")["experience"] == 2
    assert "[unlocked]" in mud._player_exit_label(db, user_id, mud._room_exits(db, room_id)[0])
    assert mud._skill_check_chance(10, 40) == 20 and mud._skill_check_chance(500, 0) == 95
    mud._go_action(db, user, {"arguments": ["north"]})
    mud._set_player_location(db, user_id, "traditional", "world_room", room_id)
    assert target is not None

    _npc(db, "Trader Ann", shop_item=("Lantern", 100))
    _coins(db, user_id, 500)
    _rank(db, user_id, "haggling", 5)
    bought = mud._shop_action(db, user, {"arguments": ["buy", "Lantern"]})
    assert "for 90 coins" in bought[0].body and _coins(db, user_id) == 410
    assert _skill(db, user_id, "haggling")["experience"] == 1


def test_skill_points_learning_practice_and_refunds(game):
    db, user_id = game
    user = _user(db, user_id)
    cid = _cid(user_id)
    assert [mud_skills.earned_points(level) for level in (1, 2, 4, 5, 10)] == [0, 1, 2, 3, 7]
    db.execute("UPDATE mud_character_progression SET level=4 WHERE character_id=? AND track_key='shared'", (cid,))
    db.execute("UPDATE mud_character_progression SET level=3 WHERE character_id=? AND track_key='traditional'",
               (cid,))
    assert mud.character_state(db, user_id)["skill_points"] == 2  # highest track only, never summed
    learned = mud._learn_action(db, user, {"arguments": ["stealth"]})
    assert "You learn Stealth at rank 1 (1 skill point)" in learned[0].body
    assert _skill(db, user_id, "stealth")["source"] == "learned"
    trained = mud._practice_skill_action(db, user, {"arguments": ["stealth"]})
    assert "rises to rank 2" in trained[0].body
    assert mud.character_state(db, user_id)["skill_points"] == 0
    with pytest.raises(mud.ActionError, match="no skill points"):
        mud._practice_skill_action(db, user, {"arguments": ["stealth"]})
    with pytest.raises(mud.ActionError, match="already know"):
        mud._learn_action(db, user, {"arguments": ["stealth"]})
    # Disabling a skill makes it dormant and returns the points spent on it.
    mud_skills.set_enabled(db, set(mud_skills.definitions(db)) - {"stealth"})
    state = mud.character_state(db, user_id)
    assert state["skill_points"] == 2
    assert next(skill for skill in state["skills"] if skill["skill_key"] == "stealth")["active"] is False
    assert mud._character_effects(db, cid)["stealth"] == 0
    assert _skill(db, user_id, "stealth")["rank"] == 2
    _import(db, {"key": "veteran", "name": "Veteran", "description": "Hard-won.",
                 "effects": [{"type": "defense"}], "requirements": {"level": 10}})
    with pytest.raises(mud.ActionError, match="requires level 10"):
        mud._learn_action(db, user, {"arguments": ["veteran"]})


def test_trainers_teach_for_coins_and_limit_ranks(game):
    db, user_id = game
    user = _user(db, user_id)
    _import(db, {"key": "fencing", "name": "Fencing", "description": "Taught only by masters.",
                 "effects": [{"type": "melee_damage"}], "practice": {"triggers": ["melee_hit"]},
                 "learn": {"cost": 0, "trainer_required": True}})
    with pytest.raises(mud.ActionError, match="from a trainer"):
        mud._learn_action(db, user, {"arguments": ["fencing"]})
    _npc(db, "Master Vey", trainer=("fencing", 30, 2))
    _coins(db, user_id, 100)
    listing = mud._skills_action(db, user, {})[0].body
    assert "Fencing (0 pt; trainer only; taught by Master Vey)" in listing
    taught = mud._learn_action(db, user, {"arguments": ["fencing"]})
    assert "Master Vey teaches you for 30 coins" in taught[0].body and _coins(db, user_id) == 70
    assert _skill(db, user_id, "fencing")["source"] == "trainer"
    lesson = mud._practice_skill_action(db, user, {"arguments": ["fencing", "from", "Master", "Vey"]})
    assert "Master Vey trains you for 30 coins" in lesson[0].body and _coins(db, user_id) == 40
    with pytest.raises(mud.ActionError, match="beyond rank 2"):
        mud._practice_skill_action(db, user, {"arguments": ["fencing", "from", "Master", "Vey"]})


def test_skills_command_and_character_state_feedback(game):
    db, user_id = game
    user = _user(db, user_id)
    body = mud._skills_action(db, user, {})[0].body
    assert body.startswith("Skill points: 0")
    assert "Knowledge:\n- Lore 2/20 [0/12]: +4 % experience earned" in body
    assert "Learnable:" in body and "- Stealth (1 pt)" in body
    detail = mud._skills_action(db, user, {"arguments": ["stealth"]})[0].body
    assert "Per rank: +3 % hiding and sneaking chance per rank, max 30" in detail
    assert "Improves by: hiding" in detail
    with pytest.raises(mud.ActionError, match="No single skill"):
        mud._skills_action(db, user, {"arguments": ["zzz"]})
    state = mud.character_state(db, user_id)
    lore = next(skill for skill in state["skills"] if skill["skill_key"] == "lore")
    assert lore["next_rank_at"] == 12 and lore["group"] == "knowledge" and lore["active"]
    assert "Skills:" in mud._character_action(db, user, {})[0].body
    assert mud.actions.from_command("/skills")[0].key == "skills"
    assert mud.actions.from_command("/guard")[0].key == "defend"


def test_origin_replacement_adds_new_grants_without_lowering_ranks(game):
    db, user_id = game
    _rank(db, user_id, "perception", 4)
    character_creation.import_origin(db, "classes", json.dumps({
        "key": "seer", "name": "Seer", "description": "Watches closely.", "traits": "Keen eyes",
        "skill": "lore", "skills": {"perception": 2, "evasion": 2}}).encode())
    db.execute("INSERT INTO mud_origin_definitions(kind,key,enabled) VALUES('classes','scholar',0)")
    assert mud_compat.incompatible(db, user_id) == ("classes",)
    mud_compat.replace_origins(db, user_id, "human", "seer")
    assert _skill(db, user_id, "perception")["rank"] == 4
    assert (_skill(db, user_id, "evasion")["rank"], _skill(db, user_id, "evasion")["source"]) == (2, "granted")
    assert re.search(r"Evasion 2", mud._character_action(db, _user(db, user_id), {})[0].body)
