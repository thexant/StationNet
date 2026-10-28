"""Leveling grants attribute points, level-scaled combat, and dungeon-aware experience."""
import json
import re
import shutil
import uuid
from pathlib import Path

import pytest

from mud_world_helpers import retire_origin
from stationnet import mud_scope
from stationnet import character_creation, create_app, dungeons, mud, mud_compat, mud_effects
from stationnet.db import get_db

BUILD = {
    "name": "Rising Scholar", "race": "human", "class": "scholar",
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
    path = Path(__file__).parent / f"_mud_progression_{uuid.uuid4().hex}"
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
            # The start room in an authored world, or the Dungeon Portals in the dungeon world.
            mud._reconcile_player_location(db, user_id)
            db.commit()
        yield app, user_id
    finally:
        _close(app)


def _user(db, user_id):
    return db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def _track(db, user_id, track="traditional"):
    return db.execute("SELECT * FROM mud_character_progression WHERE character_id=? AND track_key=?",
                      (f"stationnet:character:{user_id}", track)).fetchone()


def _set_level(db, user_id, level):
    db.execute("UPDATE mud_character_progression SET level=? WHERE character_id=?",
               (level, mud._character_id(db, user_id)))


def test_attribute_points_per_level():
    assert [mud.attribute_points_for_level(level) for level in range(2, 11)] == [1, 1, 1, 2, 1, 1, 1, 1, 2]
    assert mud.level_bonuses(1) == {"attack": 0, "spell_power": 0, "healing": 0, "defense": 0,
                                    "accuracy": 0, "evasion": 0}
    assert mud.level_bonuses(10) == {"attack": 3, "spell_power": 3, "healing": 4, "defense": 1,
                                     "accuracy": 4, "evasion": 2}


def test_leveling_grants_points_and_announces_them(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        assert mud.character_state(db, user_id)["attribute_points"] == 0
        first = mud.award_experience(db, user_id, 100)
        assert (first["levels_gained"], first["attribute_points_gained"]) == (1, 1)
        assert first["skill_points_gained"] == 1
        assert mud.level_up_text(first).startswith(
            "You reached level 2! +1 attribute point — use /train. +1 skill point — use /skills.")
        # Levels 3, 4, and 5 arrive together; the fifth level grants a bonus point.
        jump = mud.award_experience(db, user_id, 1500)
        assert jump["character"]["level"] == 5
        assert jump["attribute_points_gained"] == 4
        assert jump["character"]["attribute_points"] == 5
        assert "+4 attribute points" in mud.level_up_text(jump)
        # Levels 3-5 add one point at level 4 and a fifth-level bonus.
        assert jump["skill_points_gained"] == 2 and "+2 skill points" in mud.level_up_text(jump)
        assert "Your Lore skill rises to rank" in mud.level_up_text(jump)
        quiet = mud.award_experience(db, user_id, 1)
        assert quiet["levels_gained"] == 0 and quiet["skill_points_gained"] == 0


def test_level_cap_and_no_demotion(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        capped = mud.award_experience(db, user_id, mud.experience_for_level(60))
        assert capped["character"]["level"] == mud.MAX_LEVEL
        assert capped["attribute_points_gained"] == 49 + 10
        state = capped["character"]
        assert state["at_max_level"] and state["experience_to_next_level"] == 0
        _set_level(db, user_id, 55)
        later = mud.award_experience(db, user_id, 100)
        assert later["character"]["level"] == 55
        assert later["levels_gained"] == 0 and later["attribute_points_gained"] == 0


def test_train_raises_attributes_and_resources(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = _user(db, user_id)
        mud.award_experience(db, user_id, 1600)
        before = mud.character_state(db, user_id)
        assert before["level"] == 5 and before["attribute_points"] == 5
        listing = mud._train_action(db, user, {"arguments": []})[0].body
        assert "Attribute points available: 5" in listing and "Vitality 10/30" in listing

        result = mud._train_action(db, user, {"arguments": ["vit", "2"]})[0].body
        assert "Vitality 10 → 12" in result and "3 attribute points remaining" in result
        after = mud.character_state(db, user_id)
        # Two vitality points: 2*5 at creation plus one extra health for each of four levels.
        assert after["maximum_health"] == before["maximum_health"] + 14
        assert after["health"] == before["health"] + 14
        assert after["trained_attributes"] == {"vitality": 2}

        mud._train_action(db, user, {"arguments": ["intellect"]})
        trained = mud.character_state(db, user_id)
        assert trained["attributes"]["intellect"] == 14
        assert trained["maximum_mana"] == after["maximum_mana"] + 3 + 4 * 1
        assert trained["attribute_points"] == 2
        assert json.loads(_track(db, user_id)["trained_attributes_json"]) == {"intellect": 1, "vitality": 2}

        with pytest.raises(mud.ActionError, match="2 attribute points available"):
            mud._train_action(db, user, {"arguments": ["strength", "3"]})
        with pytest.raises(mud.ActionError, match="Choose strength"):
            mud._train_action(db, user, {"arguments": ["luck"]})
        with pytest.raises(mud.ActionError, match="at least one"):
            mud._train_action(db, user, {"arguments": ["str", "0"]})
        db.execute("UPDATE mud_character_progression SET strength=30 WHERE character_id=?",
                   (trained["id"],))
        with pytest.raises(mud.ActionError, match="cannot exceed 30"):
            mud._train_action(db, user, {"arguments": ["str"]})


def test_train_requires_completed_character(game):
    app, _user_id = game
    with app.app_context():
        db = get_db()
        newcomer = db.execute("INSERT INTO users(username,callsign,password_hash) "
                              "VALUES('newbie','NEW','unused')").lastrowid
        with pytest.raises(mud.ActionError, match="Create your character"):
            mud._train_action(db, _user(db, newcomer), {"arguments": []})


def test_each_world_trains_its_own_character(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        assert mud._progression_track(db, user_id) == "traditional"
        mud.award_experience(db, user_id, 100)
        mud._train_action(db, _user(db, user_id), {"arguments": ["str"]})
        assert _track(db, user_id)["strength"] == 13
        with mud_scope.use(mud_scope.PROCEDURAL):
            # The dungeon world's character is separate and starts over.
            character_creation.complete(db, user_id, BUILD)
            dungeon = mud._character_row(db, user_id)
            assert dungeon["track_key"] == "procedural"
            assert (dungeon["level"], dungeon["strength"], dungeon["attribute_points"]) == (1, 12, 0)
        assert _track(db, user_id)["strength"] == 13


def test_migration_starts_existing_characters_fresh(data_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled) VALUES('Test','Test','TST',1)")
            user_id = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                 "VALUES('veteran','VET','unused')").lastrowid
            character_creation.complete(db, user_id, BUILD)
            db.execute("UPDATE mud_character_progression SET level=9,experience=6500,maximum_health=190,"
                       "current_health=150 WHERE character_id=?", (f"stationnet:character:{user_id}",))
            db.execute("ALTER TABLE mud_character_progression DROP COLUMN attribute_points")
            db.execute("ALTER TABLE mud_character_progression DROP COLUMN trained_attributes_json")
            db.commit()
    finally:
        _close(app)
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            row = _track(db, user_id)
            assert (row["level"], row["experience"], row["maximum_health"], row["current_health"]) == \
                (9, 6500, 190, 150)
            assert (row["attribute_points"], row["trained_attributes_json"]) == (0, "{}")
            # Level-derived combat bonuses still reward the veteran's earned levels.
            assert mud.character_state(db, user_id)["combat"]["level_bonuses"]["attack"] == 2
    finally:
        _close(app)


def _dummy(db, room_id, attack=0):
    db.execute("INSERT INTO mud_npc_definitions(id,name,description) VALUES('test:dummy','Dummy','A dummy')")
    db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES('test:dummy-here','test:dummy',?)",
               (room_id,))
    db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack,defense,"
               "experience_reward,respawn_seconds) VALUES('test:dummy',5000,?,0,100,60)", (attack,))


def _damage(events):
    return int(re.search(r"for (\d+) damage", events[0].body).group(1))


def test_level_scales_melee_spells_healing_and_mitigation(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = _user(db, user_id)
        _dummy(db, mud._start_room(db)["id"], attack=30)
        cid = f"stationnet:character:{user_id}"

        def clear_effects():
            encounter = db.execute("SELECT id FROM mud_combat_encounters WHERE npc_definition_id='test:dummy'"
                                   ).fetchone()
            if encounter:
                mud_effects.clear(db, "encounter", encounter["id"])

        def strike():
            clear_effects()
            return _damage(mud._attack_action(db, user, {"arguments": ["Dummy"]}))

        def struck():
            # Hold Guard steady; faster skill practice would otherwise blur the level bonus.
            db.execute("UPDATE mud_character_skills SET rank=1,experience=0 WHERE character_id=?", (cid,))
            before = _track(db, user_id)["current_health"]
            mud._enemy_turn(db, user)
            return before - _track(db, user_id)["current_health"]

        def ember():
            clear_effects()
            db.execute("UPDATE mud_character_progression SET current_mana=maximum_mana WHERE character_id=?", (cid,))
            return _damage(mud._cast_action(db, user, {"arguments": ["ember", "Dummy"]}))

        def mend():
            db.execute("UPDATE mud_character_progression SET current_health=10,current_mana=maximum_mana "
                       "WHERE character_id=?", (cid,))
            body = mud._cast_action(db, user, {"arguments": ["mend"]})[0].body
            return int(re.search(r"restore (\d+) health", body).group(1))

        def full_health():
            db.execute("UPDATE mud_character_progression SET current_health=maximum_health WHERE character_id=?",
                       (cid,))

        low = (strike(), ember(), mend())
        full_health()
        low_taken = struck()
        _set_level(db, user_id, 11)
        full_health()
        high = (strike(), ember(), mend())
        full_health()
        high_taken = struck()
        assert high[0] - low[0] == mud.level_bonuses(11)["attack"] == 3
        assert high[1] - low[1] == mud.level_bonuses(11)["spell_power"] == 3
        assert high[2] - low[2] == mud.level_bonuses(11)["healing"] == 5
        assert low_taken - high_taken == mud.level_bonuses(11)["defense"] == 2


@pytest.mark.parametrize("level,difficulty,percent", [
    (1, 1, 100), (4, 1, 100), (5, 1, 85), (30, 1, 25), (7, 3, 100), (6, 3, 105), (1, 3, 125), (1, 8, 125),
])
def test_dungeon_experience_percent(level, difficulty, percent):
    assert dungeons.dungeon_xp_percent(level, difficulty) == percent


def test_recommended_levels():
    assert [dungeons.recommended_level(difficulty) for difficulty in (1, 2, 8)] == [1, 4, 22]


def _reward_body(db, user_id, scope_type, scope_key, room_id):
    db.execute("INSERT OR IGNORE INTO mud_npc_definitions(id,name,description) VALUES('test:rat','Rat','A rat')")
    encounter_id = f"test:encounter:{uuid.uuid4().hex}"
    instance_id = None
    if scope_type == "room":
        instance_id = f"test:rat:{uuid.uuid4().hex}"
        db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES(?,'test:rat',?)",
                   (instance_id, room_id))
    db.execute("INSERT INTO mud_combat_encounters(id,scope_type,scope_key,npc_instance_id,npc_definition_id,status,"
               "current_health,maximum_health,ended_at) VALUES(?,?,?,?,'test:rat','defeated',0,1,CURRENT_TIMESTAMP)",
               (encounter_id, scope_type, scope_key, instance_id))
    db.execute("INSERT INTO mud_combat_participants(encounter_id,character_id,damage_dealt) VALUES(?,?,1)",
               (encounter_id, mud._character_id(db, user_id)))
    encounter = db.execute("SELECT * FROM mud_combat_encounters WHERE id=?", (encounter_id,)).fetchone()
    npc = {"definition_id": "test:rat", "name": "Rat", "experience_reward": 100, "room_id": room_id}
    return mud._encounter_reward_drafts(db, encounter, npc)[0].body


def test_dungeon_kills_taper_for_overleveled_characters(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        user = _user(db, user_id)
        _set_level(db, user_id, 20)
        room_id = mud._start_room(db)["id"]

        def boosted(amount):
            # Rewards report what was earned after Intellect and Lore bonuses.
            bonus = mud.character_state(db, user_id)["combat"]["experience_bonus"]
            return f"{amount + amount * bonus // 100} experience"

        # Traditional worlds are hand-tuned, so their rewards are never tapered.
        expected = boosted(100)
        assert expected in _reward_body(db, user_id, "room", room_id, room_id)

        with mud_scope.use(mud_scope.PROCEDURAL):
            character_creation.complete(db, user_id, BUILD)
            _set_level(db, user_id, 20)
            mud._set_player_location(db, user_id, "procedural", "portal", mud.PORTAL_ROOM_ID)
            launched = mud._expedition_action(db, user, {"theme_id": "generic-fantasy-dungeon",
                                                         "difficulty": 1, "size": 8})
            assert "beneath you" in launched[0].body and "25%" in launched[0].body
            run = mud._active_expedition(db, user_id)
            entrance = db.execute("SELECT current_room_key FROM mud_expedition_participants WHERE instance_id=?",
                                  (run["id"],)).fetchone()[0]
            expected = boosted(25)
            assert expected in _reward_body(db, user_id, "instance", run["id"], entrance)
            _set_level(db, user_id, 1)
            expected = boosted(100)
            assert expected in _reward_body(db, user_id, "instance", run["id"], entrance)


@pytest.mark.mud_world(mud_scope.PROCEDURAL)
def test_expedition_listing_shows_recommended_levels(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        mud._set_player_location(db, user_id, "procedural", "portal", mud.PORTAL_ROOM_ID)
        body = mud._expedition_action(db, _user(db, user_id), {"arguments": []})[0].body
        assert "recommended level 1-" in body and "you are level 1" in body


def test_origin_replacement_keeps_training(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        mud.award_experience(db, user_id, 400)
        mud._train_action(db, _user(db, user_id), {"arguments": ["str", "2"]})
        assert _track(db, user_id)["strength"] == 14
        retire_origin(db, f"stationnet:character:{user_id}", "classes")
        mud_compat.replace_origins(db, user_id, "human", "warrior")
        row = _track(db, user_id)
        assert row["strength"] == 14
        assert json.loads(row["trained_attributes_json"]) == {"strength": 2}


def test_legacy_customization_refunds_training(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        legacy = db.execute("INSERT INTO users(username,callsign,password_hash) "
                            "VALUES('oldtimer','OLD','unused')").lastrowid
        cid = mud._character_id(db, legacy)
        db.execute("UPDATE mud_characters SET creation_state='legacy' WHERE id=?", (cid,))
        db.execute("UPDATE mud_character_progression SET attribute_points=1,"
                   "trained_attributes_json='{\"strength\": 2}' WHERE character_id=?", (cid,))
        character_creation.complete(db, legacy, {**BUILD, "name": "Old Timer"})
        row = _track(db, legacy)
        assert (row["attribute_points"], row["trained_attributes_json"]) == (3, "{}")


def test_character_sheet_shows_progression(game):
    app, user_id = game
    with app.app_context():
        db = get_db()
        mud.award_experience(db, user_id, 150)
        body = mud._character_action(db, _user(db, user_id), {})[0].body
        assert "Level 2/50" in body
        assert "Attribute points: 1 unspent" in body
        assert "Combat: melee" in body and "Defense: mitigation" in body
        assert re.search(r"Experience: \[#*\.*\] \d+/300", body)
        definition, _parameters = mud.actions.from_command("/train str")
        assert definition.key == "train"
