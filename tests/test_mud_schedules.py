"""World clock, the time condition, NPC daily schedules, and working hours."""
from datetime import datetime, timezone
import json
import zipfile
from io import BytesIO

import pytest

from stationnet import mud, mud_conditions, mud_society, mud_triggers, mud_worlds
from stationnet.db import get_db

from mud_society_support import WORLD, act, at_hour, client, document, installed, npc_room, place, room, \
    running, set_society


def _clocked(**clock):
    return document(society={"clock": {"enabled": True, "day_minutes": 1440, "start_hour": 0, **clock}})


def _hour(monkeypatch, hour):
    monkeypatch.setattr(mud_society, "_now", lambda: at_hour(hour).timestamp())


def _tick(db, hour):
    mud.sync_npc_schedule(db, at_hour(hour))
    db.execute("UPDATE mud_scheduled_events SET due_at=? WHERE event_kind='schedule'", (mud._scheduled_now(at_hour(0)),))
    mud.process_scheduled_events(db, at_hour(hour))


def test_game_time_math_and_periods():
    clock = {"enabled": True, "day_minutes": 1440, "start_hour": 0}
    assert mud_society.game_time(clock, at_hour(9)) == {"hour": 9, "minute": 0, "period": "morning"}
    assert mud_society.game_time({**clock, "start_hour": 20}, at_hour(9))["hour"] == 5
    assert mud_society.game_time(clock, at_hour(22))["period"] == "night"
    # A two-hour day runs twelve times faster than real time.
    fast = {"enabled": True, "day_minutes": 120, "start_hour": 0}
    midnight = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)
    assert mud_society.game_time(fast, midnight.replace(minute=5))["hour"] == 1
    assert mud_society.game_time({**clock, "enabled": False}, at_hour(9)) is None
    assert mud_society.hour_between(23, 22, 2) and mud_society.hour_between(1, 22, 2)
    assert not mud_society.hour_between(2, 22, 2)


def test_settings_validation_and_defaults():
    assert mud_society.validate_settings(None) == mud_society.DEFAULT_SOCIETY
    assert mud_society.changed_settings(mud_society.validate_settings({})) == {}
    with pytest.raises(mud_society.SocietyError):
        mud_society.validate_settings({"clock": {"day_minutes": 2}})
    with pytest.raises(mud_society.SocietyError):
        mud_society.validate_settings({"clock": {"enabled": "yes"}})
    with pytest.raises(mud_society.SocietyError):
        mud_society.validate_settings({"weather": {}})
    assert mud_society.stored_settings("not json") == mud_society.DEFAULT_SOCIETY


def test_schedule_validation():
    good = {"schedule": {"entries": [{"from": 8, "to": 17, "room": "forge"}]}}
    assert mud.validate_npc_behaviors(good) == good
    for bad in ({"schedule": {"entries": []}},
                {"schedule": {"entries": [{"from": 8, "to": 8, "room": "forge"}]}},
                {"schedule": {"entries": [{"from": 8, "to": 9, "room": "forge"}], "travel": "fly"}},
                {"schedule": {"entries": [{"from": 8, "to": 9, "room": "forge", "open": 1}]}},
                {"schedule": {"entries": [{"from": 8, "to": 9, "room": "forge"}]}, "wander": {"interval_seconds": 60}}):
        with pytest.raises(ValueError):
            mud.validate_npc_behaviors(bad)


def test_time_condition_validation_and_timers():
    assert mud_conditions.validate_conditions([{"type": "time", "from": 20, "to": 4}]) == [
        {"type": "time", "from": 20, "to": 4}]
    with pytest.raises(ValueError):
        mud_conditions.validate_conditions([{"type": "time", "from": 3, "to": 3}])
    with pytest.raises(ValueError):
        mud_conditions.validate_conditions([{"type": "time", "from": 3, "to": 4}], allow_quests=False)
    timer = mud_triggers.validate_trigger({"id": "bell", "event": "timer", "source": {"room": "square"},
                                           "interval_seconds": 60, "conditions": [{"type": "time", "from": 6, "to": 7}],
                                           "actions": [{"type": "message", "to": "room", "text": "Bells."}]})
    assert timer["conditions"][0]["type"] == "time"


def test_clock_off_keeps_old_behaviour(monkeypatch):
    with running() as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            assert "keeps no time" in pytest.raises(mud.ActionError, act, db, one, "/time").value.args[0]
            assert "time" in mud._hidden_actions(db, one)
            # The schedule rests: no timer is created and the smith stays home.
            mud.sync_npc_schedule(db, at_hour(9))
            assert db.execute("SELECT COUNT(*) FROM mud_scheduled_events WHERE event_kind='schedule'").fetchone()[0] == 0
            assert npc_room(db, "smith-1") == room(db, "home")
            assert mud_conditions.evaluate([{"type": "time", "from": 0, "to": 23}], mud._ConditionContext(db, one)) is False
            # A scheduled merchant without a clock keeps trading as before.
            place(db, one, room(db, "home"))
            assert "Smith offers" in act(db, one, "/shop")


def test_time_command_and_condition(monkeypatch):
    with running(_clocked()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            _hour(monkeypatch, 9)
            assert act(db, one, "/time") == "It is morning (09:00)."
            assert "time" not in mud._hidden_actions(db, one)
            context = mud._ConditionContext(db, one)
            assert mud_conditions.evaluate([{"type": "time", "from": 8, "to": 12}], context)
            assert not mud_conditions.evaluate([{"type": "time", "from": 20, "to": 4}], context)
            _hour(monkeypatch, 2)
            assert mud_conditions.evaluate([{"type": "time", "from": 20, "to": 4}], mud._ConditionContext(db, one))


def test_schedule_walks_to_work_and_home(monkeypatch):
    with running(_clocked()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            place(db, one, room(db, "square"))
            _tick(db, 9)  # Work time: one step east into the square...
            assert npc_room(db, "smith-1") == room(db, "square")
            seen = [row["body"] for row in db.execute("SELECT body FROM mud_events ORDER BY id DESC LIMIT 3")]
            assert "Smith arrives." in seen
            _tick(db, 9)  # ...then on to the forge.
            assert npc_room(db, "smith-1") == room(db, "forge")
            place(db, one, room(db, "forge"))
            _tick(db, 9)  # At work, the entry's lines play.
            assert db.execute("SELECT body FROM mud_events ORDER BY id DESC LIMIT 1").fetchone()["body"] == "The smith hammers."
            _tick(db, 19)
            _tick(db, 19)
            assert npc_room(db, "smith-1") == room(db, "tavern")
            for _ in range(3):
                _tick(db, 23)  # No entry covers 23:00, so the smith walks home.
            assert npc_room(db, "smith-1") == room(db, "home")
            state = json.loads(db.execute("SELECT state_json FROM mud_npc_instances WHERE id=?",
                                          (installed(db, "mud_npc_instances", "smith-1"),)).fetchone()[0])
            assert state["home_room"] == room(db, "home")


def test_teleport_and_unreachable_rooms_move_directly(monkeypatch):
    doc = _clocked()
    doc["rooms"].append({"id": "island", "name": "Island", "description": "Cut off."})
    doc["content"]["mud_npc_definitions"][0]["behaviors_json"]["schedule"]["entries"] = [
        {"from": 8, "to": 18, "room": "island"}]
    with running(doc) as (app, _users):
        with app.app_context():
            db = get_db()
            _tick(db, 9)
            assert npc_room(db, "smith-1") == room(db, "island")


def test_scheduled_merchant_keeps_hours(monkeypatch):
    with running(_clocked()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            smith = installed(db, "mud_npc_instances", "smith-1")
            db.execute("UPDATE mud_npc_instances SET room_id=? WHERE id=?", (room(db, "square"), smith))
            _hour(monkeypatch, 20)
            # The baker has no schedule and still trades; the smith is off duty.
            assert "isn't trading right now" in pytest.raises(
                mud.ActionError, act, db, one, "/shop Smith").value.args[0]
            assert "Baker offers" in act(db, one, "/shop Baker")
            _hour(monkeypatch, 10)
            assert "Smith offers" in act(db, one, "/shop Smith")


def test_package_round_trip_and_reference_checks():
    with running(_clocked(day_minutes=1440, start_hour=0)) as (app, _users):
        with app.app_context():
            db = get_db()
            payload = mud_worlds.export(db, WORLD)
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                content = json.loads(archive.read("content.json"))
            assert manifest["clock"] == {"enabled": True, "day_minutes": 1440, "start_hour": 0}
            smith = next(row for row in content["mud_npc_definitions"] if row["name"] == "Smith")
            rooms = {row["room"] for row in smith["behaviors_json"]["schedule"]["entries"]}
            assert rooms <= {f"room-{index}" for index in range(1, 5)}
            reread = mud_worlds.read_package(payload)
            assert reread["society"]["clock"]["enabled"]
    plain = mud_worlds.read_package(mud_worlds.package_bytes(document()))
    assert plain["society"] == mud_society.DEFAULT_SOCIETY
    with zipfile.ZipFile(BytesIO(mud_worlds.package_bytes(document()))) as archive:
        assert "clock" not in json.loads(archive.read("manifest.json"))
    broken = document()
    broken["content"]["mud_npc_definitions"][0]["behaviors_json"]["schedule"]["entries"][0]["room"] = "nowhere"
    with pytest.raises(mud_worlds.WorldError, match="unknown room"):
        mud_worlds.read_package(mud_worlds.package_bytes(broken))
    with pytest.raises(mud_worlds.WorldError):
        mud_worlds.package_bytes(document(society={"clock": {"day_minutes": 1}}))


def test_builder_saves_clock_and_checks_schedule_rooms():
    with running() as (app, (one, two)):
        web = client(app, one)
        response = web.post("/mud/builder/society/clock", data={
            "csrf_token": "test-token", "enabled": "1", "day_minutes": "60", "start_hour": "5"})
        assert response.status_code == 302
        with app.app_context():
            assert mud.society_settings(get_db())["clock"] == {"enabled": True, "day_minutes": 60, "start_hour": 5}
        assert web.post("/mud/builder/society/weather", data={"csrf_token": "test-token"}).status_code == 404
        assert client(app, two).post("/mud/builder/society/clock",
                                     data={"csrf_token": "test-token"}).status_code == 403
        response = web.post("/mud/builder/npcs", data={
            "csrf_token": "test-token", "name": "Ghost", "description": "Boo.", "properties_json": "{}",
            "behaviors_json": json.dumps({"schedule": {"entries": [{"from": 1, "to": 2, "room": "nowhere"}]}})},
            follow_redirects=True)
        assert b"unknown room" in response.data
        page = web.get("/mud/builder").data
        assert b"World life" in page and b"Daily schedules" in page
