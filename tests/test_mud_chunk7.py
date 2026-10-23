import uuid

import pytest

from stationnet import create_app
from stationnet.db import get_db
from stationnet import mud


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "chunk-7-tests",
        "DATABASE": str(tmp_path / "stationnet.sqlite"),
    })


def add_user(database, name):
    cursor = database.execute(
        "INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
        (name, name.upper(), "unused", uuid.uuid4().hex),
    )
    database.commit()
    return database.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()


def seed_content(database, builder_id):
    room_id = "test:room"
    item_id = "test:item"
    conversation_id = "test:conversation"
    node_a = "test:node:a"
    node_b = "test:node:b"
    npc_definition_id = "test:npc:def"
    npc_instance_id = "test:npc:instance"
    quest_id = "test:quest"
    database.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES(?,?,?)", (room_id, "Archive", "Dusty shelves."))
    database.execute("INSERT INTO mud_item_definitions(id,name,description,created_by) VALUES(?,?,?,?)", (item_id, "Seal", "A brass seal.", builder_id))
    database.execute("INSERT INTO mud_conversations(id,name,created_by) VALUES(?,?,?)", (conversation_id, "Archivist", builder_id))
    database.execute("INSERT INTO mud_dialogue_nodes(id,conversation_id,node_key,body,is_start) VALUES(?,?,?,?,1)", (node_a, conversation_id, "start", "Can you help?"))
    database.execute("INSERT INTO mud_dialogue_nodes(id,conversation_id,node_key,body) VALUES(?,?,?,?)", (node_b, conversation_id, "thanks", "Thank you."))
    database.execute("INSERT INTO mud_npc_definitions(id,name,description,conversation_id,created_by) VALUES(?,?,?,?,?)", (npc_definition_id, "Archivist", "A careful record keeper.", conversation_id, builder_id))
    database.execute("INSERT INTO mud_npc_instances(id,definition_id,room_id) VALUES(?,?,?)", (npc_instance_id, npc_definition_id, room_id))
    database.execute("INSERT INTO mud_quests(id,name,description,created_by) VALUES(?,?,?,?)", (quest_id, "Meet the Archivist", "Speak, visit, and carry the seal.", builder_id))
    database.execute("INSERT INTO mud_quest_givers(quest_id,npc_definition_id) VALUES(?,?)", (quest_id, npc_definition_id))
    database.executemany(
        "INSERT INTO mud_quest_objectives(id,quest_id,objective_type,target_id,description) VALUES(?,?,?,?,?)",
        (("test:objective:talk", quest_id, "talk_npc", npc_definition_id, "Speak to the Archivist"),
         ("test:objective:visit", quest_id, "visit_room", room_id, "Visit the archive"),
         ("test:objective:item", quest_id, "possess_item", item_id, "Possess the seal")),
    )
    database.execute("INSERT INTO mud_quest_rewards(id,quest_id,reward_type,amount) VALUES(?,?,?,?)", ("test:reward:xp", quest_id, "experience", 125))
    database.execute("INSERT INTO mud_quest_rewards(id,quest_id,reward_type,target_id,amount) VALUES(?,?,?,?,?)", ("test:reward:item", quest_id, "item", item_id, 1))
    database.execute("INSERT INTO mud_dialogue_responses(id,node_id,response_text,next_node_id,accept_quest_id,state_key,state_value) VALUES(?,?,?,?,?,?,?)", ("test:response", node_a, "I will help.", node_b, quest_id, "volunteered", "yes"))
    database.execute("INSERT INTO mud_dialogue_responses(id,node_id,response_text,required_state_key,required_state_value) VALUES(?,?,?,?,?)", ("test:response:state", node_b, "We have an agreement.", "volunteered", "yes"))
    database.commit()
    return locals()


def place_player(database, user, room_id):
    database.execute("INSERT INTO mud_world_locations(user_id,room_id) VALUES(?,?)", (user["id"], room_id))
    database.commit()


def test_dialogue_and_quest_progress_are_independent_and_rewards_are_once(app):
    with app.app_context():
        database = get_db()
        first = add_user(database, "first")
        second = add_user(database, "second")
        content = seed_content(database, first["id"])
        place_player(database, first, content["room_id"])
        place_player(database, second, content["room_id"])

        talk_events = mud._talk_action(database, first, {"npc_id": content["npc_instance_id"]})
        assert "Can you help?" in talk_events[0].body
        mud._talk_action(database, second, {"npc_id": content["npc_instance_id"]})
        response_events = mud._respond_action(database, first, {"response_id": "test:response"})
        assert any("Quest accepted" in event.body for event in response_events)
        assert any("We have an agreement." in event.body for event in response_events)
        assert database.execute("SELECT 1 FROM mud_player_quests WHERE character_id=?", (f"stationnet:character:{second['id']}",)).fetchone() is None
        assert database.execute("SELECT node_id FROM mud_conversation_sessions WHERE character_id=?", (f"stationnet:character:{second['id']}",)).fetchone()["node_id"] == content["node_a"]
        state = database.execute("SELECT state_json FROM mud_npc_player_states WHERE character_id=?", (f"stationnet:character:{first['id']}",)).fetchone()
        assert '"volunteered":"yes"' in state["state_json"]

        mud._talk_action(database, first, {"npc_id": content["npc_instance_id"]})
        character = mud._character_row(database, first["id"])
        database.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) VALUES(?,?,?)", ("test:owned:seal", content["item_id"], character["id"]))
        finished = mud._set_objective_progress(database, first, "possess_item", content["item_id"])
        assert any("Quest complete" in event.body for event in finished)
        assert mud.character_state(database, first["id"])["experience"] == 125
        assert database.execute("SELECT COUNT(*) AS count FROM mud_item_instances WHERE owner_character_id=? AND definition_id=?", (character["id"], content["item_id"])).fetchone()["count"] == 2

        assert mud._finish_ready_quests(database, first) == []
        assert mud.character_state(database, first["id"])["experience"] == 125
        assert database.execute("SELECT COUNT(*) AS count FROM mud_item_instances WHERE owner_character_id=? AND definition_id=?", (character["id"], content["item_id"])).fetchone()["count"] == 2


def test_builder_permission_remains_distinct_from_admin(app):
    with app.app_context():
        database = get_db()
        user = add_user(database, "builder")
        assert not mud.can_build(database, user)
        database.execute("INSERT INTO mud_builder_permissions(user_id) VALUES(?)", (user["id"],))
        database.commit()
        assert mud.can_build(database, user)
        assert not user["is_admin"]


def test_player_and_builder_pages_render_chunk_seven_content(app):
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(id,name,description,callsign,mud_enabled) VALUES(1,?,?,?,1)",
            ("Test Station", "Testing", "TEST"),
        )
        user = add_user(database, "sysop")
        database.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
        content = seed_content(database, user["id"])
        place_player(database, user, content["room_id"])
        user_id = user["id"]

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-csrf-token"
    player = client.get("/mud")
    assert player.status_code == 200
    assert b"Archivist" in player.data
    assert b"Quests" in player.data
    builder = client.get("/mud/builder")
    assert builder.status_code == 200
    assert b"BRANCHING CONVERSATIONS" in builder.data
    assert b"Meet the Archivist" in builder.data
