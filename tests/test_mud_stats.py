"""Attribute choices affect progression and the gear a character can use."""
import json
import shutil
import uuid
from pathlib import Path

import pytest

from stationnet import character_creation, create_app, mud
from stationnet.db import get_db


def test_attributes_drive_resources_and_equipment():
    directory = Path(__file__).parent / f"_mud_stats_{uuid.uuid4().hex}"
    directory.mkdir()
    app = None
    try:
        app = create_app({"TESTING": True, "DATA_DIR": str(directory)})
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled) "
                       "VALUES('Test','Test','TST',1)")
            user_id = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                 "VALUES('player','PLY','unused')").lastrowid
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            for track in ("shared", "traditional", "procedural"):
                mud._character_row(db, user_id, track)
            character_creation.complete(db, user_id, {
                "name": "Stat Explorer", "race": "human", "class": "scholar",
                "attributes": {"strength": 8, "agility": 8, "intellect": 14, "vitality": 14},
                "skills": ["lore", "guard"],
            })
            character = mud.character_state(db, user_id)
            assert character["attributes"]["intellect"] == 15
            assert character["maximum_mana"] == 45
            assert character["maximum_health"] == 120
            assert all(row[0] == 45 for row in db.execute(
                "SELECT maximum_mana FROM mud_character_progression WHERE character_id=?",
                (character["id"],)))

            gained = mud.award_experience(db, user_id, mud.experience_for_level(2))
            assert gained["levels_gained"] >= 1
            after = gained["character"]
            assert after["maximum_health"] == 120 + gained["levels_gained"] * 12
            assert after["maximum_mana"] == 45 + gained["levels_gained"] * 4
            assert after["health"] == after["maximum_health"]
            assert after["mana"] == after["maximum_mana"]

            db.execute("INSERT INTO mud_item_definitions"
                       "(id,name,description,item_type,equipment_slot,properties_json) "
                       "VALUES(?,?,?,'equipment','main_hand',?)",
                       ("test:blade", "Heavy Blade", "A weighty sword.",
                        json.dumps({"attack_bonus": 3, "requirements": {"strength": 14}})))
            item_id = mud._grant_item(db, user_id, "test:blade")
            mud._set_player_location(db, user_id, "traditional", "world_room", mud.START_ROOM_ID)
            with pytest.raises(mud.ActionError, match="Strength 14"):
                mud._equip_action(db, user, {"item_id": item_id})
            item = mud._item_row(db, item_id)
            assert "Requires: Strength 14" in mud._describe_item(db, item, user_id)
            db.execute("UPDATE mud_character_progression SET strength=14 WHERE character_id=?",
                       (character["id"],))
            mud._equip_action(db, user, {"item_id": item_id})
            gear = mud._equipment_combat_totals(db, character_id=character["id"])
            assert gear["attack"] == 3
            assert mud._equipment_stat_bonus(gear, 14, "attack") == 1
            assert mud._equipment_stat_bonus(gear, 10, "attack") == 0
            db.execute("UPDATE mud_character_progression SET strength=8 WHERE character_id=?",
                       (character["id"],))
            assert mud._equipment_combat_totals(db, character_id=character["id"])["attack"] == 0
            assert mud.inventory_state(db, user_id)["equipment"][0]["requirements_met"] is False
    finally:
        if app is not None:
            coordinator = app.extensions.get("broadcast_coordinator")
            if coordinator:
                coordinator._closed = True
                coordinator._timer.cancel()
        shutil.rmtree(directory)
