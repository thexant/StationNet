"""Spell costs and content effects share one persistent turn system."""
import json
import shutil
import uuid
from pathlib import Path

from stationnet import create_app, mud, mud_effects
from stationnet.db import get_db


def test_spell_effects_and_custom_item():
    directory = Path(__file__).parent / f"_mud_magic_{uuid.uuid4().hex}"
    directory.mkdir()
    try:
        app = create_app({"TESTING": True, "DATA_DIR": str(directory)})
        try:
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO station(name,description,callsign,mud_enabled) "
                           "VALUES('Test','Test','TST',1)")
                user_id = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                     "VALUES('mage','MGE','unused')").lastrowid
                user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                character = mud._character_row(db, user_id)
                db.execute("UPDATE mud_characters SET creation_state='complete',class_key='scholar' "
                           "WHERE id=?", (character["id"],))
                mud._set_player_location(db, user_id, "traditional", "world_room", mud.START_ROOM_ID)
                drafts = mud._cast_action(db, user, {"arguments": ["ward"]})
                assert "ward" in drafts[0].body
                # Scholars start with 10 extra mana; ward costs 6 of the 40.
                assert mud.character_state(db, user_id)["mana"] == 34
                assert mud_effects.modifier(db, "character", character["id"], "defense") == 3
                mud_effects.apply(db, "character", character["id"],
                                  {"key": "poison", "potency": 2, "duration": 2})
                assert mud_effects.tick(db, "character", character["id"]) == [("poison", 2)]
                assert mud_effects.tick(db, "character", character["id"]) == [("poison", 2)]
                assert all(effect["effect_key"] != "poison" for effect in
                           mud_effects.list_effects(db, "character", character["id"]))
                db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,properties_json) "
                           "VALUES(?,?,?,'consumable',?)",
                           ("test:tonic", "Ward Tonic", "Protective drink",
                            json.dumps({"on_use_effects": [
                                {"key": "ward", "potency": 4, "duration": 3}]})))
                item_id = mud._grant_item(db, user_id, "test:tonic")
                mud._use_action(db, user, {"item_id": item_id})
                assert mud_effects.modifier(db, "character", character["id"], "defense") == 4
                assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (item_id,)).fetchone() is None
                mud._rest_action(db, user, {})
                assert mud.character_state(db, user_id)["mana"] == 40
                db.execute("INSERT INTO mud_npc_definitions(id,name,description,properties_json) "
                           "VALUES('test:witch','Witch','A hostile caster',?)",
                           (json.dumps({"on_hit_effects": [
                               {"key": "poison", "potency": 2, "duration": 2}]}),))
                db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health) "
                           "VALUES('test:witch',40)")
                db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) "
                           "VALUES('test:witch:1','test:witch',?)", (mud.START_ROOM_ID,))
                mud._cast_action(db, user, {"arguments": ["ember", "Witch"]})
                encounter = db.execute("SELECT id FROM mud_combat_encounters WHERE npc_instance_id=?",
                                       ("test:witch:1",)).fetchone()
                assert mud_effects.list_effects(db, "encounter", encounter["id"])[0]["effect_key"] == "burning"
                assert any(effect["effect_key"] == "poison" for effect in
                           mud_effects.list_effects(db, "character", character["id"]))
                before = db.execute("SELECT current_health FROM mud_combat_encounters WHERE id=?",
                                    (encounter["id"],)).fetchone()[0]
                mud._attack_action(db, user, {"arguments": ["Witch"]})
                after = db.execute("SELECT current_health FROM mud_combat_encounters WHERE id=?",
                                   (encounter["id"],)).fetchone()[0]
                assert before - after >= 2
        finally:
            coordinator = app.extensions.get("broadcast_coordinator")
            if coordinator:
                coordinator._closed = True
                coordinator._timer.cancel()
    finally:
        shutil.rmtree(directory)
