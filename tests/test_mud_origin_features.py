"""Races and classes shape growth, combat, gear, and how world content responds."""
import json
from pathlib import Path
import shutil
import uuid

import pytest

from stationnet import (create_app, character_creation, dungeons, mud, mud_compat, mud_conditions,
                        mud_effects, mud_skills, mud_worlds)
from stationnet.db import get_db


PLAIN_RACE = {"key": "plainfolk", "name": "Plainfolk", "description": "Ordinary people.", "traits": "None"}
PLAIN_CLASS = {"key": "drifter", "name": "Drifter", "description": "No training.", "traits": "None",
               "skill": "guard"}
DEEP_RACE = {
    "key": "stoneblood", "name": "Stoneblood", "description": "Folk of the deep roads.", "traits": "Hard as rock",
    "effects": {"stealth": 7, "search": 3},
    "tags": ["darkvision", "deep"],
    "proficiencies": {"weapons": ["polearm"]},
    "resistances": {"physical": 50, "poison": 100, "weakness": -50},
    "skill_affinities": {"stealth": 100},
    "progression": {"health": 20, "health_per_level": 2, "mana": -5, "skill_points": 2,
                    "starting_skill_choices": 1},
}
SPELL_CLASS = {"key": "ashcaller", "name": "Ashcaller", "description": "Fire speakers.", "traits": "Embers",
               "skill": "guard", "spells": ["ember"], "tags": ["arcane"]}
ATTRIBUTES = {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11}


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_origin_features_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        db.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES('test:den','Den','A den.')")
        character_creation.import_pack(db, json.dumps(
            {"races": [PLAIN_RACE, DEEP_RACE], "classes": [PLAIN_CLASS, SPELL_CLASS]}).encode())
        users = {}
        for name, race, archetype, skills in (("plain", "plainfolk", "drifter", ["lore", "guard"]),
                                              ("deep", "stoneblood", "ashcaller", ["lore", "guard", "stealth"])):
            uid = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
                             (name, name.upper(), "unused")).lastrowid
            character_creation.complete(db, uid, {"name": name.title(), "race": race, "class": archetype,
                                                  "attributes": ATTRIBUTES, "skills": skills})
            mud._set_player_location(db, uid, "traditional", "world_room", "test:den")
            users[name] = uid
        db.commit()
    yield app, users
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data)


def _user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _cid(uid):
    return f"stationnet:character:{uid}"


def _item(db, key, properties, slot="main_hand"):
    db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,properties_json,equipment_slot) "
               "VALUES(?,?,?,'equipment',?,?)", (key, key.title(), "Test gear.", json.dumps(properties), slot))
    return key


# Validation ------------------------------------------------------------------

def test_new_fields_validate_and_old_definitions_store_unchanged(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        definition = character_creation.validate_origin("races", {**DEEP_RACE, "key": "copy"}, db)
        assert definition["resistances"] == DEEP_RACE["resistances"]
        assert definition["tags"] == ["darkvision", "deep"]
        # Older definitions gain no new keys, so re-importing a bundled world never "conflicts".
        old = character_creation.validate_origin("races", {**PLAIN_RACE, "key": "older",
                                                           "effects": {"attack": 1}}, db)
        assert set(old) == {"key", "name", "description", "traits", "attributes", "effects"}
        # Races may now carry innate spells and gear; classes keep listing both.
        race = character_creation.validate_origin("races", {**PLAIN_RACE, "key": "sparkborn", "spells": ["ward"],
            "equipment": [{"key": "charm", "name": "Charm", "description": "Lucky.", "slot": "accessory",
                           "evasion": 2}]}, db)
        assert race["spells"] == ["ward"] and race["equipment"][0]["evasion"] == 2


@pytest.mark.parametrize("field,value,message", [
    ("effects", {"teleport": 1}, "effects must contain"),
    ("progression", {"health": 51}, "progression may contain"),
    ("progression", {"skill_points": -1}, "progression may contain"),
    ("resistances", {"fire": 10}, "resistances must map"),
    ("resistances", {"poison": 101}, "resistances must map"),
    ("skill_affinities", {"juggling": 10}, "skill_affinities must map"),
    ("skill_affinities", {"magic": 500}, "skill_affinities must map"),
    ("tags", ["Not A Key"], "tags must be"),
    ("proficiencies", {"shields": ["x1"]}, "proficiencies may contain"),
    ("spells", ["fireball"], "Spells must be"),
])
def test_invalid_origin_fields_are_rejected(game, field, value, message):
    app, _ = game
    with app.app_context():
        with pytest.raises(ValueError, match=message):
            character_creation.validate_origin("races", {**PLAIN_RACE, "key": "broken", field: value}, get_db())


# Progression -------------------------------------------------------------------

def test_origins_change_growth_skills_and_abilities(game):
    app, users = game
    with app.app_context():
        db = get_db()
        plain, deep = (mud.character_state(db, users[name]) for name in ("plain", "deep"))
        assert deep["maximum_health"] - plain["maximum_health"] == 20
        assert deep["maximum_mana"] - plain["maximum_mana"] == -5
        assert (plain["skill_points"], deep["skill_points"]) == (0, 2)
        assert mud._known_spells(db, _cid(users["deep"])) == ("ember",)
        assert "polearm" in mud._proficiencies(db, _cid(users["deep"]))
        assert "polearm" not in mud._proficiencies(db, _cid(users["plain"]))
        effects = [mud._character_effects(db, _cid(users[name])) for name in ("plain", "deep")]
        # The origin's +7 stacks with the Stealth skill this character chose.
        assert effects[1]["stealth"] == 7 + mud_skills.bonuses(db, _cid(users["deep"]))["stealth"]
        assert effects[0]["stealth"] == 0
        # Origin bonuses respect the same caps as skills: merchants never give goods away.
        db.execute("UPDATE mud_origin_definitions SET definition_json=json_set(definition_json,"
                   "'$.effects.shop_discount',100) WHERE key='stoneblood'")
        assert mud._character_effects(db, _cid(users["deep"]))["shop_discount"] == mud_skills.EFFECT_CAPS["shop_discount"]
        db.execute("UPDATE mud_origin_definitions SET definition_json=json_remove(definition_json,"
                   "'$.effects.shop_discount') WHERE key='stoneblood'")
        assert deep["origin"]["tags"] == ["arcane", "darkvision", "deep"]
        assert "Resists: physical 50% · poison 100% · weakness -50%" in deep["origin"]["summary"]
        assert any(line.startswith("Traits: arcane, darkvision") for line in
                   mud._character_action(db, _user(db, users["deep"]), {})[0].body.splitlines())

        for name in ("plain", "deep"):
            mud.award_experience(db, users[name], mud.experience_for_level(3))
        plain, deep = (mud.character_state(db, users[name]) for name in ("plain", "deep"))
        assert deep["maximum_health"] - plain["maximum_health"] == 20 + 2 * 2
        # Applying the origin again changes nothing.
        assert not character_creation.sync_origin_resources(db, _cid(users["deep"]))


def test_bonus_starting_skill_choices_are_required(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        uid = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('x','X','unused')").lastrowid
        with pytest.raises(ValueError, match="required number"):
            character_creation.complete(db, uid, {"name": "Short", "race": "stoneblood", "class": "drifter",
                                                  "attributes": ATTRIBUTES, "skills": ["lore", "guard"]})


def test_skill_affinity_speeds_practice(game):
    app, users = game
    with app.app_context():
        db = get_db()
        assert mud_skills.practice_gain(3, 2, 100) == 12
        assert mud_skills.practice_gain(1, 1, -50) == 1  # an affinity never erases practice
        assert mud_skills.practice_gain(0, 1, 100) == 0
        db.execute("INSERT INTO mud_character_skills(character_id,skill_key,rank) VALUES(?,?,1)",
                   (_cid(users["plain"]), "stealth"))
        for name in ("plain", "deep"):
            mud_skills.practice(db, _cid(users[name]), "hide", 2)
        gained = [db.execute("SELECT rank,experience FROM mud_character_skills WHERE character_id=? "
                             "AND skill_key='stealth'", (_cid(users[name]),)).fetchone()
                  for name in ("plain", "deep")]
        total = [sum(mud_skills.next_rank_cost(rank) for rank in range(1, row["rank"])) + row["experience"]
                 for row in gained]
        assert total[1] == 2 * total[0]


def test_replacing_origins_resyncs_growth(game):
    app, users = game
    with app.app_context():
        db = get_db()
        before = mud.character_state(db, users["deep"])["maximum_health"]
        db.execute("UPDATE mud_origin_definitions SET enabled=0 WHERE key='stoneblood'")
        mud_compat.replace_origins(db, users["deep"], "plainfolk", "ashcaller")
        after = mud.character_state(db, users["deep"])
        assert before - after["maximum_health"] == 20
        assert after["origin"]["tags"] == ["arcane"]
        assert db.execute("SELECT MAX(origin_health) FROM mud_character_progression WHERE character_id=?",
                          (_cid(users["deep"]),)).fetchone()[0] == 0


# Combat ----------------------------------------------------------------------

def test_resistances_reduce_blows_and_block_effects(game):
    app, users = game
    assert mud._resisted(10, 50) == 5 and mud._resisted(3, 10) == 3 and mud._resisted(10, 100) == 0
    assert mud._resisted(10, -50) == 15 and mud._resisted(1, 90) == 1
    assert mud_effects.resist({"key": "poison", "potency": 4, "duration": 4}, 50) == \
        {"key": "poison", "potency": 2, "duration": 2}
    assert mud_effects.resist({"key": "poison", "potency": 4}, 100) is None
    assert mud_effects.resist({"key": "weakness", "potency": 2, "duration": 2}, -50)["potency"] == 3
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO mud_npc_definitions(id,name,description,properties_json) VALUES(?,?,?,?)",
                   ("test:spider", "Spider", "A spider.", json.dumps({"on_hit_effects": [
                       {"key": "poison", "potency": 3, "duration": 3}]})))
        db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack,defense,"
                   "experience_reward) VALUES('test:spider',500,40,0,1)")
        db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES('test:spider:1','test:spider',"
                   "'test:den')")
        lost, messages = {}, {}
        for name in ("plain", "deep"):
            user = _user(db, users[name])
            start = mud.character_state(db, users[name])["health"]
            npc = mud._room_npc_rows(db, "test:den")[0]
            drafts, _ = mud._enemy_strike(db, user, npc, mud._encounter_row(db, npc))
            lost[name] = start - mud.character_state(db, users[name])["health"]
            messages[name] = " ".join(draft.body for draft in drafts)
        assert lost["deep"] == lost["plain"] - lost["plain"] * 50 // 100
        assert "inflicts poison" in messages["plain"] and "shrug off poison" in messages["deep"]
        assert not any(row["effect_key"] == "poison"
                       for row in mud_effects.list_effects(db, "character", _cid(users["deep"])))


# Equipment requirements --------------------------------------------------------

def test_item_requirements_validate_and_describe():
    with pytest.raises(ValueError, match="unsupported requirement"):
        mud_conditions.validate_item_requirements({"luck": 3})
    with pytest.raises(ValueError, match="lowercase keys"):
        mud_conditions.validate_item_requirements({"tags": "darkvision"})
    requirements = mud_conditions.validate_item_requirements(
        {"strength": 12, "level": 3, "tags": ["deep", "arcane"], "not_classes": ["drifter"]})
    assert requirements["tags"] == ["arcane", "deep"]
    character = {"strength": 10, "agility": 10, "intellect": 10, "vitality": 10, "level": 1}
    facts = {"races": {"plainfolk"}, "classes": {"drifter"}, "tags": set()}
    assert mud_conditions.requirement_problems(requirements, character, facts) == [
        "Strength 12", "level 3", "a different class", "the arcane or deep trait"]
    assert "trait: arcane or deep" in mud_conditions.describe_item_requirements(requirements)


def test_equipment_honors_race_class_and_tag_requirements(game):
    app, users = game
    with app.app_context():
        db = get_db()
        _item(db, "deep-pick", {"attack": 5, "requirements": {"tags": ["darkvision"]}})
        _item(db, "plain-hoe", {"attack": 5, "requirements": {"not_races": ["stoneblood"]}}, "off_hand")
        _item(db, "elder-staff", {"attack": 5, "requirements": {"level": 5}}, "accessory")
        for name in ("plain", "deep"):
            for key in ("deep-pick", "plain-hoe", "elder-staff"):
                mud._grant_item(db, users[name], key)
        plain, deep = _user(db, users["plain"]), _user(db, users["deep"])
        with pytest.raises(mud.ActionError, match="requires the darkvision trait"):
            mud._equip_action(db, plain, {"arguments": ["Deep-Pick"]})
        mud._equip_action(db, deep, {"arguments": ["Deep-Pick"]})
        with pytest.raises(mud.ActionError, match="a different race"):
            mud._equip_action(db, deep, {"arguments": ["Plain-Hoe"]})
        mud._equip_action(db, plain, {"arguments": ["Plain-Hoe"]})
        with pytest.raises(mud.ActionError, match="level 5"):
            mud._equip_action(db, plain, {"arguments": ["Elder-Staff"]})
        assert mud._equipment_combat_totals(db, character_id=_cid(users["deep"]))["attack"] == 5
        # Losing the qualifying origin later makes equipped gear inert rather than breaking play.
        db.execute("UPDATE mud_characters SET race_key='plainfolk' WHERE id=?", (_cid(users["deep"]),))
        assert mud._equipment_combat_totals(db, character_id=_cid(users["deep"]))["attack"] == 0


# World content -------------------------------------------------------------------

def _world():
    return {
        "id": "deeproads", "name": "Deep Roads", "author": "Test", "start_room": "gate",
        "rooms": [{"id": "gate", "name": "Gate", "description": "A stone gate."},
                  {"id": "dark", "name": "Dark Hall", "description": "Pitch black."}],
        "exits": [{"from": "gate", "to": "dark", "name": "stair", "direction": "down",
                   "conditions": [{"any": [{"type": "tag", "keys": ["darkvision"]},
                                           {"type": "race", "keys": ["deepkin"]}]}],
                   "conditions_message": "It is far too dark to go down."},
                  {"from": "dark", "to": "gate", "name": "stair", "direction": "up"}],
        "origins": {"races": [{"key": "deepkin", "name": "Deepkin", "description": "Born below.",
                               "traits": "Dark-adapted", "tags": ["darkvision"]}]},
        "content": {
            "mud_item_definitions": [{"id": "lamp", "name": "Deep Lamp", "description": "A lamp.",
                                      "item_type": "equipment", "equipment_slot": "off_hand",
                                      "properties_json": {"requirements": {"races": ["deepkin"]}}}],
            "mud_conversations": [{"id": "warden-talk", "name": "Warden"}],
            "mud_dialogue_nodes": [{"id": "warden-start", "conversation_id": "warden-talk", "node_key": "start",
                                    "body": "Halt.", "is_start": 1}],
            "mud_npc_definitions": [{"id": "warden", "name": "Warden", "description": "A warden.",
                                     "conversation_id": "warden-talk"}],
            "mud_npc_instances": [{"id": "warden-here", "definition_id": "warden", "room_id": "gate"}],
            "mud_dialogue_responses": [
                {"id": "kin", "node_id": "warden-start", "response_text": "I am kin.",
                 "conditions_json": [{"type": "race", "keys": ["deepkin"]}]},
                {"id": "seer", "node_id": "warden-start", "response_text": "I see in the dark.",
                 "conditions_json": [{"type": "tag", "keys": ["darkvision"]}]}],
        },
    }


def test_world_content_responds_to_origins_and_round_trips(game):
    app, users = game
    with app.app_context():
        db = get_db()
        document = _world()
        mud_worlds.install(db, mud_worlds.package_bytes(document))
        db.execute("UPDATE mud_world_packages SET active=CASE WHEN id='deeproads' THEN 1 ELSE 0 END")
        rooms = {row["name"]: row["id"] for row in db.execute(
            "SELECT id,name FROM mud_world_rooms WHERE world_id='deeproads'")}
        deepkin = mud_worlds.bundled_key("deeproads", "races", "deepkin")
        lamp = json.loads(db.execute("SELECT properties_json FROM mud_item_definitions WHERE name='Deep Lamp'")
                          .fetchone()[0])
        assert lamp["requirements"]["races"] == [deepkin]
        for uid in users.values():
            mud._set_player_location(db, uid, "traditional", "world_room", rooms["Gate"])
        plain, deep = _user(db, users["plain"]), _user(db, users["deep"])

        assert "I see in the dark." in mud._talk_action(db, deep, {"arguments": ["Warden"]})[0].body
        greeting = mud._talk_action(db, plain, {"arguments": ["Warden"]})[0].body
        assert "I see in the dark." not in greeting and "I am kin." not in greeting
        with pytest.raises(mud.ActionError, match="far too dark"):
            mud._go_action(db, plain, {"arguments": ["down"]})
        assert "[barred]" in mud._player_exit_label(db, users["plain"], mud._room_exits(db, rooms["Gate"])[0])
        mud._go_action(db, deep, {"arguments": ["down"]})

        # A character of the bundled race matches content that named it by local key.
        db.execute("UPDATE mud_characters SET race_key=? WHERE id=?", (deepkin, _cid(users["plain"])))
        assert "I am kin." in mud._talk_action(db, plain, {"arguments": ["Warden"]})[0].body
        mud._go_action(db, plain, {"arguments": ["down"]})

        exported = mud_worlds.read_package(mud_worlds.export(db, "deeproads"))
        stair = next(item for item in exported["exits"] if item["direction"] == "down")
        assert {"type": "race", "keys": ["deepkin"]} in stair["conditions"][0]["any"]
        assert stair["conditions_message"] == "It is far too dark to go down."
        lamp = next(row for row in exported["content"]["mud_item_definitions"] if row["name"] == "Deep Lamp")
        assert lamp["properties_json"]["requirements"]["races"] == ["deepkin"]


def test_world_rejects_invalid_item_requirements_and_exit_conditions(game):
    app, _ = game
    with app.app_context():
        document = _world()
        document["content"]["mud_item_definitions"][0]["properties_json"] = {"requirements": {"luck": 1}}
        with pytest.raises(mud_worlds.WorldError, match="Invalid item requirements"):
            mud_worlds.read_package(mud_worlds.package_bytes(document))
        document = _world()
        document["exits"][0]["conditions"] = [{"type": "zodiac"}]
        with pytest.raises(mud_worlds.WorldError, match="Invalid exit conditions"):
            mud_worlds.read_package(mud_worlds.package_bytes(document))


def test_dungeon_themes_resolve_bundled_origin_keys():
    template = {"id": "caves", "version": "1.0.0", "origins": {"races": [{"key": "molefolk"}]},
                "definitions": {"items": [{"id": "claw"}]}}
    mole = dungeons.bundled_origin_key("caves", "races", "molefolk")
    stored = json.loads(dungeons._item_properties_json(
        template, {"requirements": {"races": ["molefolk", "human"], "tags": ["burrower"]}}))
    assert stored["requirements"] == {"races": sorted([mole, "human"]), "tags": ["burrower"]}
    conditions, _ = dungeons._response_rules_json(template, {"conditions": [
        {"type": "race", "keys": ["molefolk"]}, {"type": "tag", "keys": ["burrower"]},
        {"type": "has_item", "item": "claw"}]})
    conditions = json.loads(conditions)
    assert conditions[0]["keys"] == [mole] and conditions[1]["keys"] == ["burrower"]
    assert conditions[2]["item"] == dungeons.local_definition_id(template, "item", "claw")
