import json
import uuid

import pytest

from stationnet import create_app, mud
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "chunk-11-tests",
        "DATABASE": str(tmp_path / "stationnet.sqlite"),
        "DATA_DIR": str(tmp_path),
        "DUNGEON_THEMES_DIR": str(tmp_path / "dungeon_themes"),
    })
    theme_dir = tmp_path / "dungeon_themes"
    theme_dir.mkdir(exist_ok=True)
    (theme_dir / "neutral.json").write_text(json.dumps(_theme()), encoding="utf-8")
    return app


def _theme():
    return {
        "schema_version": 1,
        "id": "neutral-run",
        "version": "1.0.0",
        "name": "Neutral run",
        "generation": {
            "size": {"minimum": 3, "default": 5, "maximum": 8},
            "difficulty": {"minimum": 1, "default": 2, "maximum": 5},
            "layouts": [{"type": "linear", "weight": 1}],
            "special_roles": [],
        },
        "pools": {
            "rooms": [
                {"id": "entrance", "conditions": {"roles": ["entrance"]},
                 "names": [{"text": "Entrance"}], "descriptions": [{"text": "Start."}]},
                {"id": "exit", "conditions": {"roles": ["exit"]},
                 "names": [{"text": "Exit"}], "descriptions": [{"text": "Finish."}]},
                {"id": "normal", "conditions": {"roles": ["normal"]},
                 "names": [{"text": "Room"}], "descriptions": [{"text": "Plain."}]},
            ],
            "environments": [], "enemies": [], "items": [], "encounters": [],
        },
    }


def _content_theme(theme_id, enemy=False, item=False):
    theme = _theme()
    theme["id"] = theme_id
    theme["name"] = theme_id
    theme["generation"]["size"] = {"minimum": 3, "default": 3, "maximum": 3}
    encounter = {
        "id": "fixture", "chance": 1,
        "conditions": {"roles": ["normal"]},
    }
    if enemy:
        theme["pools"]["enemies"] = [{
            "id": "enemies", "chance": 1,
            "count": {"minimum": 1, "maximum": 1},
            "choices": [{"definition_id": "test:enemy"}],
        }]
        encounter["enemy_pool"] = "enemies"
    if item:
        theme["pools"]["items"] = [{
            "id": "items", "chance": 1,
            "count": {"minimum": 1, "maximum": 1},
            "choices": [{"definition_id": "test:item"}],
        }]
        encounter["item_pool"] = "items"
    theme["pools"]["encounters"] = [encounter]
    return theme


def _station(database):
    database.execute(
        "INSERT INTO station(id,name,description,callsign,mud_enabled,"
        "mud_traditional_enabled,mud_procedural_enabled) VALUES(1,'Test','Test','TEST',1,0,1)"
    )


def _user(database, name):
    cursor = database.execute(
        "INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
        (name, name.upper(), "unused", uuid.uuid4().hex),
    )
    return database.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone()


def _walk_to_exit(database, user):
    while mud._current_room(database, user["id"])["role"] != "exit":
        exit_row = mud._room_exits(
            database, mud._current_room(database, user["id"])["id"],
            mud._current_room(database, user["id"])["instance_id"],
        )[0]
        mud._go_action(database, user, {"exit_id": exit_row["id"]})


def test_solo_lifecycle_rewards_once_and_returns_to_portal(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database, "solo")
        mud._expedition_action(database, user, {"theme_id": "neutral-run", "difficulty": 3})
        run = mud._active_expedition(database, user["id"])
        assert run["status"] == "active" and run["difficulty"] == 3
        _walk_to_exit(database, user)
        before = mud.character_state(database, user["id"])["experience"]
        mud._complete_expedition_action(database, user, {})
        after = mud.character_state(database, user["id"])["experience"]
        assert after > before
        assert mud._current_room(database, user["id"])["id"] == mud.PORTAL_ROOM_ID
        participant = database.execute(
            "SELECT * FROM mud_expedition_participants WHERE instance_id=? AND user_id=?",
            (run["id"], user["id"]),
        ).fetchone()
        assert participant["status"] == "completed" and participant["rewarded_at"]
        with pytest.raises(mud.ActionError):
            mud._complete_expedition_action(database, user, {})
        assert mud.character_state(database, user["id"])["experience"] == after


def test_cooperative_run_shares_state_and_completion_rewards_party(app):
    with app.app_context():
        database = get_db(); _station(database)
        leader = _user(database, "leader"); partner = _user(database, "partner")
        mud._expedition_action(database, leader, {"theme_id": "neutral-run"})
        run = mud._active_expedition(database, leader["id"])
        mud._join_expedition_action(database, partner, {"code": run["join_code"]})
        assert mud._active_expedition(database, partner["id"])["id"] == run["id"]
        _walk_to_exit(database, leader)
        mud._complete_expedition_action(database, leader, {})
        states = database.execute(
            "SELECT status,rewarded_at FROM mud_expedition_participants WHERE instance_id=?",
            (run["id"],),
        ).fetchall()
        assert len(states) == 2 and all(row["status"] == "completed" for row in states)
        assert all(row["rewarded_at"] for row in states)
        assert mud._current_room(database, partner["id"])["id"] == mud.PORTAL_ROOM_ID


def test_runs_are_isolated_and_abandoning_last_participant_closes_only_that_run(app):
    with app.app_context():
        database = get_db(); _station(database)
        first = _user(database, "first"); second = _user(database, "second")
        mud._expedition_action(database, first, {"theme_id": "neutral-run"})
        mud._expedition_action(database, second, {"theme_id": "neutral-run"})
        run_a = mud._active_expedition(database, first["id"])
        run_b = mud._active_expedition(database, second["id"])
        assert run_a["id"] != run_b["id"] and run_a["join_code"] != run_b["join_code"]
        mud._abandon_expedition_action(database, first, {})
        assert database.execute(
            "SELECT status FROM mud_procedural_instances WHERE id=?", (run_a["id"],)
        ).fetchone()["status"] == "abandoned"
        assert mud._active_expedition(database, second["id"])["id"] == run_b["id"]


def test_saved_dungeon_location_recovers_after_new_app_instance(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database, "returning")
        mud._expedition_action(database, user, {"theme_id": "neutral-run"})
        run = mud._active_expedition(database, user["id"])
        database.commit()
        database_path = app.config["DATABASE"]
        data_dir = app.config["DATA_DIR"]
        themes = app.config["DUNGEON_THEMES_DIR"]
        user_id = user["id"]
    restarted = create_app({
        "TESTING": True, "SECRET_KEY": "restart", "DATABASE": database_path,
        "DATA_DIR": data_dir, "DUNGEON_THEMES_DIR": themes,
    })
    with restarted.app_context():
        room = mud._current_room(get_db(), user_id)
        assert room["instance_id"] == run["id"] and room["role"] == "entrance"


def test_defeat_fails_player_and_safely_closes_solo_run(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database, "defeated")
        database.execute(
            "INSERT INTO mud_conversations(id,name) VALUES('test:conversation','Fixture')"
        )
        database.execute(
            "INSERT INTO mud_dialogue_nodes(id,conversation_id,node_key,body,is_start) "
            "VALUES('test:node','test:conversation','start','Test dialogue.',1)"
        )
        database.execute(
            "INSERT INTO mud_dialogue_responses(id,node_id,response_text,state_key,state_value) "
            "VALUES('test:response','test:node','Continue','spoke','yes')"
        )
        database.execute(
            "INSERT INTO mud_npc_definitions(id,name,description,conversation_id) "
            "VALUES('test:enemy','Test Enemy','Fixture.','test:conversation')"
        )
        database.execute(
            "INSERT INTO mud_npc_combat_profiles(npc_definition_id,maximum_health,attack,defense) "
            "VALUES('test:enemy',1000,1000,0)"
        )
        path = app.config["DUNGEON_THEMES_DIR"] + "/danger.json"
        with open(path, "w", encoding="utf-8") as target:
            json.dump(_content_theme("danger", enemy=True), target)
        mud._expedition_action(database, user, {"theme_id": "danger"})
        run = mud._active_expedition(database, user["id"])
        first_exit = mud._room_exits(database, "room-0000", run["id"])[0]
        mud._go_action(database, user, {"exit_id": first_exit["id"]})
        enemy = mud._room_npc_rows(database, "room-0001", run["id"])[0]
        mud._talk_action(database, user, {"npc_id": enemy["id"]})
        mud._respond_action(database, user, {"arguments": ["1"]})
        assert json.loads(database.execute(
            "SELECT state_json FROM mud_dungeon_npc_player_states WHERE character_id=?",
            (mud._character_row(database, user["id"])["id"],),
        ).fetchone()["state_json"])["spoke"] == "yes"
        mud._attack_action(database, user, {"npc_id": enemy["id"]})
        participant = database.execute(
            "SELECT status FROM mud_expedition_participants WHERE instance_id=? AND user_id=?",
            (run["id"], user["id"]),
        ).fetchone()
        assert participant["status"] == "failed"
        assert mud._current_room(database, user["id"])["id"] == mud.PORTAL_ROOM_ID
        assert database.execute(
            "SELECT status,terminal_reason FROM mud_procedural_instances WHERE id=?", (run["id"],)
        ).fetchone()["terminal_reason"] == "failure"


def test_generated_item_can_only_be_claimed_once_in_coop_run(app):
    with app.app_context():
        database = get_db(); _station(database)
        leader = _user(database, "collector"); partner = _user(database, "witness")
        database.execute(
            "INSERT INTO mud_item_definitions(id,name,description) "
            "VALUES('test:item','Test Item','Fixture.')"
        )
        path = app.config["DUNGEON_THEMES_DIR"] + "/items.json"
        with open(path, "w", encoding="utf-8") as target:
            json.dump(_content_theme("items", item=True), target)
        mud._expedition_action(database, leader, {"theme_id": "items"})
        run = mud._active_expedition(database, leader["id"])
        mud._join_expedition_action(database, partner, {"code": run["join_code"]})
        first_exit = mud._room_exits(database, "room-0000", run["id"])[0]
        mud._go_action(database, leader, {"exit_id": first_exit["id"]})
        mud._go_action(database, partner, {"exit_id": first_exit["id"]})
        item = mud._room_item_rows(database, "room-0001", run["id"])[0]
        mud._get_action(database, leader, {"item_id": item["id"]})
        with pytest.raises(mud.ActionError):
            mud._get_action(database, partner, {"item_id": item["id"]})
        assert database.execute(
            "SELECT COUNT(*) AS amount FROM mud_item_instances WHERE definition_id='test:item'"
        ).fetchone()["amount"] == 1
