"""Installed abilities and effects work in play, built only from the engine's fixed primitives."""
import json
from pathlib import Path
import shutil
import uuid

import pytest

from stationnet import create_app, character_creation, mud, mud_abilities, mud_effects
from stationnet.db import get_db


PACK = {
    "effects": [
        {"key": "frostbite", "name": "Frostbite", "kind": "damage", "resist_as": "burning"},
        {"key": "stoneskin", "name": "Stoneskin", "kind": "defense", "sign": 1},
        {"key": "renewal", "name": "Renewal", "kind": "regen"},
    ],
    "abilities": [
        {"key": "frost-lance", "name": "Frost Lance", "command": "lance", "target": "enemy", "cost": 7,
         "damage": 8, "effects": [{"key": "frostbite", "potency": 2, "duration": 3}]},
        {"key": "granite-skin", "name": "Granite Skin", "command": "granite", "target": "self", "cost": 6,
         "effects": [{"key": "stoneskin", "potency": 3, "duration": 4}]},
        {"key": "renew", "name": "Renew", "target": "self", "cost": 5,
         "effects": [{"key": "renewal", "potency": 4, "duration": 3}]},
    ],
}
RACE = {"key": "frostkin", "name": "Frostkin", "description": "Cold-born.", "traits": "Icy",
        "resistances": {"burning": 50}}
CLASS = {"key": "rimecaller", "name": "Rimecaller", "description": "Ice casters.", "traits": "Frost",
         "skill": "guard", "spells": ["frost-lance", "granite-skin", "renew", "ward"]}
ATTRIBUTES = {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11}


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_abilities_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        db.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES('test:den','Den','A den.')")
        mud_abilities.import_pack(db, json.dumps(PACK).encode())
        character_creation.import_pack(db, json.dumps({"races": [RACE], "classes": [CLASS]}).encode())
        uid = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('rime','RIM','x')").lastrowid
        character_creation.complete(db, uid, {"name": "Rime", "race": "frostkin", "class": "rimecaller",
                                              "attributes": ATTRIBUTES, "skills": ["lore", "guard"]})
        mud._set_player_location(db, uid, "traditional", "world_room", "test:den")
        db.execute("INSERT INTO mud_npc_definitions(id,name,description) VALUES('test:wolf','Wolf','Grey.')")
        db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack) "
                   "VALUES('test:wolf',200,1)")
        db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES('test:wolf:1','test:wolf','test:den')")
        db.commit()
    yield app, uid
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data)


def _user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def test_custom_ability_costs_mana_and_applies_named_custom_effect(game):
    app, uid = game
    with app.app_context():
        db = get_db()
        character = mud._character_row(db, uid)
        listing = mud._spells_action(db, _user(db, uid), {})[0].body
        assert "Frost Lance (lance): 7" in listing and "ward: 6" in listing
        drafts = mud._cast_action(db, _user(db, uid), {"arguments": ["lance", "Wolf"]})
        assert mud.character_state(db, uid)["mana"] == character["maximum_mana"] - 7
        encounter = db.execute("SELECT id,current_health FROM mud_combat_encounters "
                               "WHERE npc_instance_id='test:wolf:1'").fetchone()
        assert encounter["current_health"] < 200
        effects = mud_effects.list_effects(db, "encounter", encounter["id"])
        assert [(row["effect_key"], row["name"]) for row in effects] == [("frostbite", "Frostbite")]
        assert any("suffers Frostbite" in draft.body for draft in drafts)
        mud._cast_action(db, _user(db, uid), {"arguments": ["granite"]})
        assert mud_effects.modifier(db, "character", character["id"], "defense") == 3


def test_regen_heals_and_resist_as_uses_origin_resistance(game):
    app, uid = game
    with app.app_context():
        db = get_db()
        character = mud._character_row(db, uid)
        db.execute("UPDATE mud_character_progression SET current_health=10 WHERE character_id=?", (character["id"],))
        mud._cast_action(db, _user(db, uid), {"arguments": ["renew"]})
        before = mud._character_row(db, uid)["current_health"]
        detail = mud_effects.tick_detail(db, "character", character["id"])
        assert detail["regen"] == [("Renewal", 4)]
        drafts = mud._regenerate_character(db, uid, detail["regen"])
        assert mud._character_row(db, uid)["current_health"] == before + 4 and "Renewal" in drafts[0].body
        # Frostbite resists as burning, and Frostkin halve burning.
        assert mud_effects.resist_key(db, "frostbite") == "burning"
        assert mud_effects.resist_key(db, "poison") == "poison"
        assert mud_effects.resist_key(db, "stoneskin") is None


def test_unknown_or_removed_effects_never_crash_combat(game):
    app, uid = game
    with app.app_context():
        db = get_db()
        character = mud._character_row(db, uid)
        db.execute("INSERT INTO mud_active_effects(target_type,target_id,effect_key,potency,remaining_turns,source) "
                   "VALUES('character',?,'vanished',3,2,'test')", (character["id"],))
        assert mud_effects.modifier(db, "character", character["id"], "attack") == 0
        assert mud_effects.tick(db, "character", character["id"]) == []
        assert not mud_effects.list_effects(db, "character", character["id"])


def test_effect_active_on_someone_is_retained_when_removed(game):
    app, uid = game
    with app.app_context():
        db = get_db()
        character = mud._character_row(db, uid)
        with pytest.raises(ValueError, match="still used by ability Frost Lance"):
            mud_effects.remove_effects(db, {"frostbite"})
        with pytest.raises(ValueError, match="still granted by class Rimecaller"):
            mud_abilities.remove(db, {"renew"})
        mud_effects.apply(db, "character", character["id"], {"key": "renewal", "potency": 1, "duration": 5})
        mud_effects.remove_effects(db, {"renewal"}, force=True)
        row = db.execute("SELECT installed FROM mud_effect_definitions WHERE key='renewal'").fetchone()
        assert row["installed"] == 0
        assert "renewal" not in mud_effects.catalog(db)
        # It still resolves and ticks for the character who has it.
        assert mud_effects.tick_detail(db, "character", character["id"])["regen"] == [("Renewal", 1)]


@pytest.mark.parametrize("document,message", [
    ({"key": "zap", "name": "Zap", "target": "enemy", "cost": 1, "damage": 2,
      "effects": [{"key": "ward", "potency": 1, "duration": 1}]}, "cannot be applied by a enemy ability"),
    ({"key": "zap", "name": "Zap", "target": "self", "cost": 1,
      "effects": [{"key": "poison", "potency": 1, "duration": 1}]}, "cannot be applied by a self ability"),
    ({"key": "zap", "name": "Zap", "target": "self", "cost": 1}, "needs heal or at least one effect"),
    ({"key": "zap", "name": "Zap", "target": "self", "cost": 1, "damage": 3}, "cannot deal damage"),
    ({"key": "zap", "name": "Zap", "target": "enemy", "cost": 1, "damage": 2,
      "effects": [{"key": "nope", "potency": 1, "duration": 1}]}, "not installed or bundled"),
    ({"key": "zap", "name": "Zap", "target": "enemy", "damage": 2, "teleport": True}, "Unsupported ability fields"),
])
def test_invalid_abilities_are_rejected(game, document, message):
    app, _ = game
    with app.app_context():
        with pytest.raises(ValueError, match=message):
            mud_abilities.normalize_ability(document, mud_effects.catalog(get_db()))


@pytest.mark.parametrize("document,message", [
    ({"key": "glow", "name": "Glow", "kind": "light"}, "Effect kind must be one of"),
    ({"key": "glow", "name": "Glow", "kind": "attack"}, "need sign"),
    ({"key": "glow", "name": "Glow", "kind": "regen", "resist_as": "poison"}, "Only harmful effects"),
    ({"key": "glow", "name": "Glow", "kind": "damage", "resist_as": "fire"}, "resist_as must be one of"),
])
def test_invalid_effects_are_rejected(document, message):
    with pytest.raises(ValueError, match=message):
        mud_effects.normalize_effect(document)


def test_import_rejects_built_in_and_duplicate_keys(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        with pytest.raises(ValueError, match="already exists"):
            mud_abilities.import_pack(db, json.dumps({"key": "ember", "name": "Ember Two", "target": "enemy",
                                                      "cost": 1, "damage": 1}).encode())
        with pytest.raises(ValueError, match="already exists"):
            mud_abilities.import_pack(db, json.dumps({"key": "poison", "name": "Poison", "kind": "damage"}).encode())


def test_built_in_spells_keep_their_numbers(game):
    app, uid = game
    with app.app_context():
        assert mud.SPELLS["ember"]["cost"] == 8 and mud.SPELLS["ember"]["damage"] == 9
        assert mud.SPELLS["mend"] == {**mud.SPELLS["mend"], "cost": 7, "heal": 20, "target": "self"}
        assert mud_effects.EFFECTS["ward"]["kind"] == "defense" and mud_effects.EFFECTS["ward"]["name"] == "ward"


def test_apply_effect_trigger_action(game):
    app, uid = game
    with app.app_context():
        db = get_db()
        character = mud._character_row(db, uid)
        trigger = {"id": "frost-trap", "name": "Frost trap", "actions": [
            {"type": "apply_effect", "effect": "frostbite", "potency": 4, "duration": 2}]}
        drafts = mud._trigger_action(db, _user(db, uid), trigger, 0, "stationnet")
        assert "Frostbite" in drafts[0].body
        # Frostkin resist burning by half, and frostbite resists as burning.
        row = db.execute("SELECT potency FROM mud_active_effects WHERE target_id=? AND effect_key='frostbite'",
                         (character["id"],)).fetchone()
        assert row["potency"] == 2
