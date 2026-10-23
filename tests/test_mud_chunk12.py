from datetime import datetime, timezone
import json
import uuid

import pytest

from stationnet import create_app, mud
from stationnet.db import get_db


def _theme(version="1.0.0", normal_name="Daily Room"):
    return {
        "schema_version": 1,
        "id": "daily-neutral",
        "version": version,
        "name": "Daily Neutral",
        "definitions": {
            "items": [{"id": "marker", "name": "Template Marker",
                       "description": "Template-owned test item.", "item_type": "misc"}],
            "npcs": [], "enemies": [],
        },
        "generation": {
            "size": {"minimum": 3, "default": 4, "maximum": 8},
            "difficulty": {"minimum": 1, "default": 2, "maximum": 5},
            "layouts": [{"type": "linear", "weight": 1}],
            "special_roles": [],
        },
        "pools": {
            "rooms": [
                {"id": "entrance", "conditions": {"roles": ["entrance"]},
                 "names": [{"text": "Daily Entrance"}],
                 "descriptions": [{"text": "Template entrance."}]},
                {"id": "exit", "conditions": {"roles": ["exit"]},
                 "names": [{"text": "Daily Exit"}],
                 "descriptions": [{"text": "Template exit."}]},
                {"id": "normal", "conditions": {"roles": ["normal"]},
                 "names": [{"text": normal_name}],
                 "descriptions": [{"text": "Template room."}]},
            ],
            "environments": [], "npcs": [], "enemies": [],
            "items": [{"id": "markers", "chance": 1,
                       "count": {"minimum": 1, "maximum": 1},
                       "choices": [{"local_id": "marker"}]}],
            "encounters": [{"id": "marker-encounter", "chance": 1,
                            "conditions": {"roles": ["normal"]},
                            "item_pool": "markers"}],
        },
    }


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "chunk-12-tests",
        "DATABASE": str(tmp_path / "stationnet.sqlite"),
        "DATA_DIR": str(tmp_path),
        "DUNGEON_THEMES_DIR": str(tmp_path / "dungeon_themes"),
    })
    theme_dir = tmp_path / "dungeon_themes"
    theme_dir.mkdir(exist_ok=True)
    (theme_dir / "daily.json").write_text(json.dumps(_theme()), encoding="utf-8")
    return app


def _station(database):
    database.execute(
        "INSERT INTO station(id,name,description,callsign,mud_enabled,"
        "mud_traditional_enabled,mud_procedural_enabled) "
        "VALUES(1,'Test','Test','TEST',1,0,1)"
    )


def _user(database, name):
    user_id = database.execute(
        "INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
        (name, name.upper(), "unused", uuid.uuid4().hex),
    ).lastrowid
    return database.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def _walk_to_exit(database, user):
    while mud._current_room(database, user["id"])["role"] != "exit":
        room = mud._current_room(database, user["id"])
        exit_row = mud._room_exits(database, room["id"], room["instance_id"])[0]
        mud._go_action(database, user, {"exit_id": exit_row["id"]})


def _room_signature(database, instance_id):
    return [tuple(row) for row in database.execute(
        "SELECT room_key,ordinal,x,y,role,depth,difficulty,name,description,"
        "room_template_id,environment_id FROM mud_dungeon_rooms "
        "WHERE instance_id=? ORDER BY ordinal", (instance_id,),
    ).fetchall()]


def test_daily_design_is_deterministic_and_snapshotted_mid_day(app):
    instant = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    with app.app_context():
        database = get_db(); _station(database)
        first = mud.ensure_daily_dungeon(database, instant)
        signature = _room_signature(database, first["design_instance_id"])
        theme_path = app.config["DUNGEON_THEMES_DIR"] + "/daily.json"
        with open(theme_path, "w", encoding="utf-8") as target:
            json.dump(_theme("2.0.0", "Changed Midday"), target)
        second = mud.ensure_daily_dungeon(database, instant)
        assert second["design_instance_id"] == first["design_instance_id"]
        assert _room_signature(database, second["design_instance_id"]) == signature
        tomorrow = mud.ensure_daily_dungeon(
            database, datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        )
        assert tomorrow["day_key"] != first["day_key"]
        assert tomorrow["theme_version"] == "2.0.0"
        assert tomorrow["design_instance_id"] != first["design_instance_id"]


def test_separate_daily_runs_share_design_but_isolate_mutable_state(app):
    with app.app_context():
        database = get_db(); _station(database)
        first = _user(database, "first"); second = _user(database, "second")
        daily = mud.ensure_daily_dungeon(database)
        mud._start_daily_run(database, first, daily)
        mud._start_daily_run(database, second, daily)
        run_a = mud._active_expedition(database, first["id"])
        run_b = mud._active_expedition(database, second["id"])
        assert run_a["id"] != run_b["id"]
        assert _room_signature(database, run_a["id"]) == _room_signature(database, run_b["id"])
        database.execute(
            "INSERT INTO mud_dungeon_content_state(content_id,status,claimed_by) "
            "SELECT id,'claimed',? FROM mud_dungeon_content WHERE instance_id=? LIMIT 1",
            (first["id"], run_a["id"]),
        )
        assert database.execute(
            "SELECT COUNT(*) FROM mud_dungeon_content_state AS s "
            "JOIN mud_dungeon_content AS c ON c.id=s.content_id WHERE c.instance_id=?",
            (run_b["id"],),
        ).fetchone()[0] == 0


def test_daily_first_completion_reward_only_and_attempt_cap(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database, "repeat")
        daily = mud.ensure_daily_dungeon(database)
        mud._start_daily_run(database, user, daily)
        _walk_to_exit(database, user)
        before = mud.character_state(database, user["id"])["experience"]
        mud._complete_expedition_action(database, user, {})
        first_reward_total = mud.character_state(database, user["id"])["experience"]
        assert first_reward_total > before
        mud._start_daily_run(database, user, daily)
        _walk_to_exit(database, user)
        mud._complete_expedition_action(database, user, {})
        assert mud.character_state(database, user["id"])["experience"] == first_reward_total
        mud._start_daily_run(database, user, daily)
        mud._abandon_expedition_action(database, user, {})
        with pytest.raises(mud.ActionError, match="used all 3 attempts"):
            mud._start_daily_run(database, user, daily)
        record = mud._daily_participation(database, daily["day_key"], user["id"])
        assert (record["attempts"], record["completions"], record["abandons"]) == (3, 2, 1)
        assert record["rewarded_at"] and record["reward_experience"] > 0


def test_coop_join_counts_attempt_and_rollover_preserves_yesterday_run(app):
    day_one = datetime(2026, 10, 23, 23, tzinfo=timezone.utc)
    day_two = datetime(2026, 10, 24, 1, tzinfo=timezone.utc)
    with app.app_context():
        database = get_db(); _station(database)
        leader = _user(database, "leader"); partner = _user(database, "partner")
        old_daily = mud.ensure_daily_dungeon(database, day_one)
        mud._start_daily_run(database, leader, old_daily)
        old_run = mud._active_expedition(database, leader["id"])
        mud._join_daily_run(database, partner, old_daily, old_run["join_code"])
        new_daily = mud.ensure_daily_dungeon(database, day_two)
        assert new_daily["day_key"] != old_daily["day_key"]
        assert mud._active_expedition(database, leader["id"])["id"] == old_run["id"]
        assert mud._active_expedition(database, partner["id"])["id"] == old_run["id"]
        assert mud._daily_participation(database, old_daily["day_key"], partner["id"])["attempts"] == 1


def test_daily_catalog_and_active_run_survive_restart(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database, "returning")
        daily = mud.ensure_daily_dungeon(database)
        mud._start_daily_run(database, user, daily)
        run_id = mud._active_expedition(database, user["id"])["id"]
        day_key = daily["day_key"]
        database.commit()
        config = {"TESTING": True, "SECRET_KEY": "restart", "DATABASE": app.config["DATABASE"],
                  "DATA_DIR": app.config["DATA_DIR"],
                  "DUNGEON_THEMES_DIR": app.config["DUNGEON_THEMES_DIR"]}
        user_id = user["id"]
    restarted = create_app(config)
    with restarted.app_context():
        database = get_db()
        assert mud.ensure_daily_dungeon(database)["day_key"] == day_key
        assert mud._active_expedition(database, user_id)["id"] == run_id
        assert mud._current_room(database, user_id)["instance_id"] == run_id


def test_portal_actions_and_sysop_daily_configuration(app):
    with app.app_context():
        database = get_db(); _station(database)
        admin = _user(database, "sysop")
        member = _user(database, "member")
        database.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
        database.commit()
        member = database.execute("SELECT * FROM users WHERE id=?", (member["id"],)).fetchone()
        options = mud.actions.contextual(member, database)
        assert any(item["key"] == "daily" for item in options)
        definition, parameters = mud.actions.from_command("/daily")
        assert definition is mud.actions.get("daily")
        assert "Dungeon of the Day" in definition.handler(database, member, parameters)[0].body
        original = mud.ensure_daily_dungeon(database)["design_instance_id"]
        database.commit()

    form = {
        "csrf_token": "chunk12-token", "daily_timezone": "America/Edmonton",
        "daily_max_attempts": "5", "daily_all_themes": "on",
        "daily_difficulty": "", "daily_room_count": "", "daily_layout": "",
    }
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = member["id"]
        state["csrf_token"] = "chunk12-token"
    assert client.post("/sysop/settings/mud-daily", data=form).headers["Location"].endswith(
        "/terminal"
    )
    with client.session_transaction() as state:
        state["user_id"] = admin["id"]
        state["csrf_token"] = "chunk12-token"
    response = client.post("/sysop/settings/mud-daily", data=form)
    assert response.status_code == 302
    assert client.get("/sysop/settings").status_code == 200
    with app.app_context():
        settings = get_db().execute(
            "SELECT * FROM mud_daily_dungeon_settings WHERE id=1"
        ).fetchone()
        assert settings["timezone"] == "America/Edmonton"
        assert settings["max_attempts"] == 5
        assert get_db().execute(
            "SELECT status FROM mud_procedural_instances WHERE id=?", (original,)
        ).fetchone()["status"] == "pending"
