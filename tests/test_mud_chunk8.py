import uuid
import json

import pytest

from stationnet import create_app, mud
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "chunk-8-tests",
        "DATABASE": str(tmp_path / "stationnet.sqlite"),
    })


def add_user(database, name, admin=False):
    cursor = database.execute(
        "INSERT INTO users(username,callsign,password_hash,connection_id,is_admin) VALUES(?,?,?,?,?)",
        (name, name.upper(), "unused", uuid.uuid4().hex, int(admin)),
    )
    database.commit()
    return database.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()


def seed_enemy(database, room_id, maximum_health=12, attack=6, defense=2, experience=25):
    definition_id = f"test:enemy:def:{uuid.uuid4().hex}"
    instance_id = f"test:enemy:{uuid.uuid4().hex}"
    database.execute(
        "INSERT INTO mud_npc_definitions(id,name,description) VALUES(?,?,?)",
        (definition_id, "Security Drone", "A damaged but dangerous drone."),
    )
    database.execute(
        "INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack,defense,"
        "experience_reward,respawn_seconds) VALUES(?,?,?,?,?,60)",
        (definition_id, maximum_health, attack, defense, experience),
    )
    database.execute(
        "INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES(?,?,?)",
        (instance_id, definition_id, room_id),
    )
    database.commit()
    return definition_id, instance_id


def place(database, user, room_id):
    database.execute("INSERT INTO mud_world_locations(user_id,room_id) VALUES(?,?)", (user["id"], room_id))
    database.commit()


def test_multiplayer_enemy_health_and_rewards_are_shared_and_once(app):
    with app.app_context():
        database = get_db()
        first = add_user(database, "first")
        second = add_user(database, "second")
        room_id = "test:arena"
        database.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES(?,?,?)", (room_id, "Arena", "A test arena."))
        definition_id, instance_id = seed_enemy(database, room_id)
        loot_id = "test:loot"
        weapon_id = "test:weapon"
        database.execute("INSERT INTO mud_item_definitions(id,name,description) VALUES(?,?,?)", (loot_id, "Drone Part", "Salvage."))
        database.execute(
            "INSERT INTO mud_item_definitions(id,name,description,item_type,equipment_slot,properties_json) "
            "VALUES(?,?,?,?,?,?)", (weapon_id, "Shock Baton", "A weapon.", "equipment", "main_hand", '{"attack_bonus":3}'),
        )
        database.execute(
            "INSERT INTO mud_npc_combat_rewards(id,npc_definition_id,reward_type,target_id,amount) "
            "VALUES(?,?,?,?,?)", ("test:loot:reward", definition_id, "item", loot_id, 1),
        )
        first_character = mud._character_row(database, first["id"])
        database.execute(
            "INSERT INTO mud_item_instances(id,definition_id,owner_character_id,equipped_slot) VALUES(?,?,?,?)",
            ("test:baton", weapon_id, first_character["id"], "main_hand"),
        )
        place(database, first, room_id); place(database, second, room_id)

        first_events = mud._attack_action(database, first, {"npc_id": instance_id})
        shared = mud.combat_state(database, second["id"])["enemies"][0]
        assert shared["health"] == 1
        assert any("11 damage" in event.body for event in first_events)

        second_events = mud._attack_action(database, second, {"npc_id": instance_id})
        assert any("is defeated" in event.body for event in second_events)
        assert mud.character_state(database, first["id"])["experience"] == 25
        assert mud.character_state(database, second["id"])["experience"] == 25
        for user in (first, second):
            character = mud._character_row(database, user["id"])
            count = database.execute(
                "SELECT COUNT(*) AS amount FROM mud_item_instances WHERE owner_character_id=? AND definition_id=?",
                (character["id"], loot_id),
            ).fetchone()["amount"]
            assert count == 1
        with pytest.raises(mud.ActionError):
            mud._attack_action(database, first, {"npc_id": instance_id})
        assert database.execute(
            "SELECT COUNT(*) AS amount FROM mud_item_instances WHERE definition_id=?", (loot_id,),
        ).fetchone()["amount"] == 2


def test_player_defeat_recovers_at_start_and_abandons_empty_encounter(app):
    with app.app_context():
        database = get_db()
        user = add_user(database, "fragile")
        room_id = "test:danger"
        database.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES(?,?,?)", (room_id, "Danger", "Unsafe."))
        _definition_id, instance_id = seed_enemy(database, room_id, maximum_health=100, attack=250)
        place(database, user, room_id)

        events = mud._attack_action(database, user, {"npc_id": instance_id})
        assert any("fully recovered" in event.body for event in events)
        character = mud.character_state(database, user["id"])
        assert character["health"] == character["maximum_health"]
        assert mud._current_room(database, user["id"])["id"] == mud.START_ROOM_ID
        participant = database.execute(
            "SELECT p.defeated_at,p.left_at,e.status FROM mud_combat_participants AS p "
            "JOIN mud_combat_encounters AS e ON e.id=p.encounter_id WHERE p.character_id=?",
            (character["id"],),
        ).fetchone()
        assert participant["defeated_at"] is not None
        assert participant["left_at"] is not None
        assert participant["status"] == "abandoned"


def test_movement_withdraws_and_resets_an_empty_encounter(app):
    with app.app_context():
        database = get_db()
        user = add_user(database, "runner")
        room_id, safe_id = "test:combat-room", "test:safe-room"
        database.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES(?,?,?)", (room_id, "Combat", "Fight."))
        database.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES(?,?,?)", (safe_id, "Safe", "Rest."))
        database.execute(
            "INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) VALUES(?,?,?,?,?)",
            ("test:exit", room_id, safe_id, "escape", "out"),
        )
        _definition_id, instance_id = seed_enemy(database, room_id, maximum_health=100)
        place(database, user, room_id)
        mud._attack_action(database, user, {"npc_id": instance_id})
        mud._go_action(database, user, {"exit_id": "test:exit"})
        assert mud._current_room(database, user["id"])["id"] == safe_id
        encounter = database.execute(
            "SELECT status FROM mud_combat_encounters WHERE npc_instance_id=?", (instance_id,),
        ).fetchone()
        assert encounter["status"] == "abandoned"


def test_builder_can_configure_hostile_npc_and_non_builder_cannot(app):
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(id,name,description,callsign,mud_enabled) VALUES(1,?,?,?,1)",
            ("Test Station", "Testing", "TEST"),
        )
        builder = add_user(database, "builder")
        outsider = add_user(database, "outsider")
        database.execute("INSERT INTO mud_builder_permissions(user_id) VALUES(?)", (builder["id"],))
        database.commit()

    data = {
        "csrf_token": "test-token", "name": "Raider", "description": "Hostile.",
        "properties_json": "{}", "behaviors_json": "{}", "is_hostile": "1",
        "maximum_health": "44", "combat_attack": "9", "combat_defense": "3",
        "experience_reward": "17", "respawn_seconds": "90",
    }
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = outsider["id"]; state["csrf_token"] = "test-token"
    assert client.post("/mud/builder/npcs", data=data).status_code == 403
    with client.session_transaction() as state:
        state["user_id"] = builder["id"]; state["csrf_token"] = "test-token"
    response = client.post("/mud/builder/npcs", data=data)
    assert response.status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT p.* FROM mud_npc_combat_profiles AS p JOIN mud_npc_definitions AS d "
            "ON d.id=p.npc_definition_id WHERE d.name='Raider'",
        ).fetchone()
        assert (row["maximum_health"], row["attack"], row["defense"],
                row["experience_reward"], row["respawn_seconds"]) == (44, 9, 3, 17, 90)


def test_command_and_context_attacks_share_handler_and_request_is_idempotent(app):
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(id,name,description,callsign,mud_enabled) VALUES(1,?,?,?,1)",
            ("Test Station", "Testing", "TEST"),
        )
        user = add_user(database, "fighter")
        room_id = "test:action-arena"
        database.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES(?,?,?)", (room_id, "Arena", "Fight."))
        _definition_id, instance_id = seed_enemy(database, room_id, maximum_health=100)
        place(database, user, room_id)
        user_id, connection_id = user["id"], user["connection_id"]

    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = user_id
        state["connection_id"] = connection_id
        state["csrf_token"] = "test-token"
    common = {"csrf_token": "test-token", "client_id": "chunk8client01"}
    command = client.post("/mud/actions", data={
        **common, "request_id": "chunk8command01", "command": "/attack Security Drone",
    })
    assert command.status_code == 200
    assert command.get_json()["action"] == "attack"
    after_command = command.get_json()["combat"]["enemies"][0]["health"]

    context = client.post("/mud/actions", data={
        **common, "request_id": "chunk8context01", "action": "attack",
        "parameters": json.dumps({"npc_id": instance_id}),
    })
    assert context.status_code == 200
    assert context.get_json()["action"] == "attack"
    after_context = context.get_json()["combat"]["enemies"][0]["health"]
    assert after_context < after_command

    duplicate = client.post("/mud/actions", data={
        **common, "request_id": "chunk8context01", "action": "attack",
        "parameters": json.dumps({"npc_id": instance_id}),
    })
    assert duplicate.status_code == 200
    assert duplicate.get_json()["combat"]["enemies"][0]["health"] == after_context

