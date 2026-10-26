"""Firearms use item state and the ordinary combat and inventory boundaries."""
import json
import shutil
import uuid
from pathlib import Path

import pytest

from stationnet import create_app, dungeons, mud, mud_firearms
from stationnet.db import get_db


def test_reload_fire_and_melee_remain_independent():
    directory = Path(__file__).parent / f"_mud_firearms_{uuid.uuid4().hex}"
    directory.mkdir()
    app = None
    try:
        app = create_app({"TESTING": True, "DATA_DIR": str(directory)})
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled) VALUES('Test','Test','TST',1)")
            user_id = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('ranger','RNG','unused')").lastrowid
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            character = mud._character_row(db, user_id)
            db.execute("UPDATE mud_characters SET creation_state='complete' WHERE id=?", (character["id"],))
            mud._set_player_location(db, user_id, "traditional", "world_room", mud.START_ROOM_ID)
            for item_id, name, item_type, slot, properties in (
                ("test:gun", "Pistol", "equipment", "main_hand", {"firearm": True, "ammo_type": "9mm", "magazine_size": 2, "attack_bonus": 3}),
                ("test:ammo", "9mm rounds", "misc", None, {"ammo_type": "9mm", "rounds": 3}),
                ("test:wrong", "Other rounds", "misc", None, {"ammo_type": "12g", "rounds": 3}),
                ("test:sword", "Sword", "equipment", "main_hand", {"attack_bonus": 3}),
            ):
                assert mud_firearms.validate_item(item_type, slot, properties) is None
                db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,equipment_slot,properties_json) VALUES(?,?,?,?,?,?)",
                           (item_id, name, name, item_type, slot, json.dumps(properties)))
            gun = mud._grant_item(db, user_id, "test:gun")
            ammo = mud._grant_item(db, user_id, "test:ammo")
            mud._grant_item(db, user_id, "test:wrong")
            sword = mud._grant_item(db, user_id, "test:sword")
            mud._equip_action(db, user, {"item_id": gun})
            db.execute("INSERT INTO mud_npc_definitions(id,name,description) VALUES('test:target','Target','Target')")
            db.execute("INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack) VALUES('test:target',1000,1)")
            db.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES('test:target:1','test:target',?)", (mud.START_ROOM_ID,))
            assert mud._equipment_combat_totals(db, character_id=character["id"])["attack"] == 0
            with pytest.raises(mud.ActionError, match="empty"):
                mud._fire_action(db, user, {"arguments": ["Target"]})
            mud._reload_action(db, user, {})
            assert mud._loaded_rounds(mud._item_row(db, gun)) == 2
            assert mud._item_state(mud._item_row(db, ammo))["rounds"] == 1
            assert mud._item_value(mud._item_row(db, ammo)) == 2
            assert "attacks" in mud._melee_action(db, user, {"arguments": ["Target"]})[0].body
            assert mud._loaded_rounds(mud._item_row(db, gun)) == 2
            assert "fires at" in mud._attack_action(db, user, {"arguments": ["Target"]})[0].body
            mud._fire_action(db, user, {"arguments": ["Target"]})
            with pytest.raises(mud.ActionError, match="empty"):
                mud._fire_action(db, user, {"arguments": ["Target"]})
            mud._reload_action(db, user, {})
            assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (ammo,)).fetchone() is None
            assert mud._loaded_rounds(mud._item_row(db, gun)) == 1
            assert "fires at" in mud._attack_action(db, user, {"arguments": ["Target"]})[0].body
            assert "attacks" in mud._attack_action(db, user, {"arguments": ["Target"]})[0].body
            # Melee gear continues to contribute its usual attack bonus.
            db.execute("DELETE FROM mud_character_equipment WHERE character_id=?", (character["id"],))
            db.execute("INSERT INTO mud_character_equipment(character_id,loadout_key,slot,item_instance_id) VALUES(?,?,?,?)",
                       (character["id"], mud._inventory_context(db, user_id)["loadout"], "main_hand", sword))
            assert mud._equipment_combat_totals(db, character_id=character["id"])["attack"] == 3
            db.execute("INSERT INTO mud_npc_equipment(npc_definition_id,equipment_slot,item_definition_id) "
                       "VALUES('test:target','main_hand','test:gun')")
            mud._enemy_turn(db, user)
            mud._enemy_turn(db, user)
            assert "reloads" in mud._enemy_turn(db, user)[0].body
    finally:
        if app is not None and "broadcast_coordinator" in app.extensions:
            coordinator = app.extensions["broadcast_coordinator"]
            coordinator._closed = True
            coordinator._timer.cancel()
        shutil.rmtree(directory)


@pytest.mark.parametrize("item_type,slot,properties", [
    ("equipment", "body", {"firearm": True, "ammo_type": "9mm", "magazine_size": 6}),
    ("equipment", "main_hand", {"firearm": True, "ammo_type": "9mm", "magazine_size": 0}),
    ("misc", None, {"ammo_type": "9mm", "rounds": 0}),
    ("misc", None, {"ammo_type": "9mm", "rounds": True}),
])
def test_invalid_firearm_definitions(item_type, slot, properties):
    assert mud_firearms.validate_item(item_type, slot, properties)


def test_firearms_are_optional_in_dungeon_themes():
    path = Path(dungeons.__file__).parent / "dungeon_templates" / "generic-fantasy-dungeon.json"
    theme = json.loads(path.read_text(encoding="utf-8"))
    assert dungeons.validate_template(theme) == []
    theme["definitions"]["items"].extend([
        {"id": "sidearm", "name": "Sidearm", "description": "A pistol", "item_type": "equipment",
         "equipment_slot": "main_hand", "properties": {"firearm": True, "ammo_type": "9mm", "magazine_size": 6, "attack_bonus": 4}},
        {"id": "sidearm-ammo", "name": "9mm rounds", "description": "A box", "item_type": "misc",
         "properties": {"ammo_type": "9mm", "rounds": 18}},
    ])
    assert dungeons.validate_template(theme) == []
    theme["definitions"]["items"][-1]["properties"]["rounds"] = -1
    assert any("ammunition rounds" in error for error in dungeons.validate_template(theme))
