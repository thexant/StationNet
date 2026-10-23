import json
import uuid

from stationnet import create_app, dungeons, mud
from stationnet.db import get_db


def _standalone_theme():
    return {
        "schema_version": 1, "id": "standalone-fixture", "version": "1.0.0",
        "name": "Standalone fixture",
        "definitions": {
            "items": [
                {"id": "gear", "name": "Fixture Gear", "description": "Fixture.",
                 "item_type": "equipment", "equipment_slot": "main_hand",
                 "properties": {"damage": 1}},
                {"id": "drop", "name": "Fixture Item", "description": "Fixture."},
            ],
            "npcs": [{
                "id": "npc", "name": "Fixture NPC", "description": "Fixture.",
                "conversation": {"nodes": [{
                    "id": "start", "body": "Fixture dialogue.", "start": True,
                    "responses": [{"id": "done", "text": "Continue.",
                                   "state_key": "finished", "state_value": "yes"}],
                }]},
            }],
            "enemies": [{
                "id": "enemy", "name": "Fixture Enemy", "description": "Fixture.",
                "combat": {"maximum_health": 1, "attack": 1, "defense": 0,
                           "experience_reward": 1, "respawn_seconds": 60},
                "equipment": [{"slot": "main_hand", "item_local_id": "gear"}],
                "loot": [{"type": "item", "item_local_id": "drop", "amount": 1}],
            }],
        },
        "generation": {
            "size": {"minimum": 3, "default": 3, "maximum": 3},
            "difficulty": {"minimum": 1, "default": 1, "maximum": 1},
            "layouts": [{"type": "linear"}], "special_roles": [],
        },
        "pools": {
            "rooms": [
                {"id": "entrance", "conditions": {"roles": ["entrance"]},
                 "names": [{"text": "Entrance"}], "descriptions": [{"text": "Fixture."}]},
                {"id": "normal", "conditions": {"roles": ["normal"]},
                 "names": [{"text": "Room"}], "descriptions": [{"text": "Fixture."}]},
                {"id": "exit", "conditions": {"roles": ["exit"]},
                 "names": [{"text": "Exit"}], "descriptions": [{"text": "Fixture."}]},
            ],
            "environments": [],
            "npcs": [{"id": "npcs", "count": {"minimum": 1, "maximum": 1},
                      "choices": [{"local_id": "npc"}]}],
            "enemies": [{"id": "enemies", "count": {"minimum": 1, "maximum": 1},
                         "choices": [{"local_id": "enemy"}]}],
            "items": [{"id": "items", "count": {"minimum": 1, "maximum": 1},
                       "choices": [{"local_id": "drop"}]}],
            "encounters": [{"id": "complete", "conditions": {"roles": ["normal"]},
                            "npc_pool": "npcs", "enemy_pool": "enemies",
                            "item_pool": "items", "config": {"fixture": True}}],
        },
    }


def test_embedded_only_theme_generates_and_instantiates_without_world_content(tmp_path):
    theme_dir = tmp_path / "dungeon_themes"
    theme_dir.mkdir()
    theme = _standalone_theme()
    (theme_dir / "standalone.json").write_text(json.dumps(theme), encoding="utf-8")
    app = create_app({
        "TESTING": True, "SECRET_KEY": "standalone", "DATABASE": str(tmp_path / "db.sqlite"),
        "DATA_DIR": str(tmp_path), "DUNGEON_THEMES_DIR": str(theme_dir),
    })
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(id,name,description,callsign,mud_enabled,"
            "mud_traditional_enabled,mud_procedural_enabled) VALUES(1,'Test','Test','TEST',1,0,1)"
        )
        cursor = database.execute(
            "INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
            ("solo", "SOLO", "unused", uuid.uuid4().hex),
        )
        user = database.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()
        assert database.execute(
            "SELECT COUNT(*) amount FROM mud_npc_definitions"
        ).fetchone()["amount"] == 0
        assert dungeons.validate_template(theme, database) == []
        first = dungeons.generate_manifest(theme, "fixed-seed", database=database)
        second = dungeons.generate_manifest(theme, "fixed-seed", database=database)
        assert first == second

        mud._expedition_action(database, user, {"theme_id": theme["id"]})
        run = mud._active_expedition(database, user["id"])
        content = database.execute(
            "SELECT content_type,definition_id,snapshot_json,config_json "
            "FROM mud_dungeon_content WHERE instance_id=? ORDER BY content_type",
            (run["id"],),
        ).fetchall()
        assert {row["content_type"] for row in content} == {"encounter", "npc", "enemy", "item"}
        assert all(row["definition_id"].startswith("dungeon:standalone-fixture@1.0.0:")
                   for row in content if row["definition_id"])
        assert json.loads(next(row["config_json"] for row in content
                               if row["content_type"] == "encounter"))["fixture"] is True

        first_exit = mud._room_exits(database, "room-0000", run["id"])[0]
        mud._go_action(database, user, {"exit_id": first_exit["id"]})
        npcs = mud._room_npc_rows(database, "room-0001", run["id"])
        assert {npc["name"] for npc in npcs} == {"Fixture NPC", "Fixture Enemy"}
        talker = next(npc for npc in npcs if npc["name"] == "Fixture NPC")
        mud._talk_action(database, user, {"npc_id": talker["id"]})
        mud._respond_action(database, user, {"arguments": ["1"]})
        assert mud.combat_state(database, user["id"])["enemies"][0]["name"] == "Fixture Enemy"

        item = mud._room_item_rows(database, "room-0001", run["id"])[0]
        mud._get_action(database, user, {"item_id": item["id"]})
        inventory = mud._inventory_rows(database, user["id"])
        assert [entry["name"] for entry in inventory] == ["Fixture Item"]
        enemy = next(npc for npc in npcs if npc["name"] == "Fixture Enemy")
        equipment = database.execute(
            "SELECT equipment_slot,item_definition_id FROM mud_npc_equipment "
            "WHERE npc_definition_id=?", (enemy["definition_id"],),
        ).fetchone()
        assert equipment["equipment_slot"] == "main_hand"
        assert equipment["item_definition_id"].endswith(":item:gear")
        mud._attack_action(database, user, {"npc_id": enemy["id"]})
        assert [entry["name"] for entry in mud._inventory_rows(database, user["id"])] == [
            "Fixture Item", "Fixture Item",
        ]
        assert database.execute(
            "SELECT COUNT(*) amount FROM mud_dungeon_managed_definitions"
        ).fetchone()["amount"] == 5
