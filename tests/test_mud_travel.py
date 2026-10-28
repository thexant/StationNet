"""Wilderness long-distance travel: /travel walks one real room per tick and stops when something matters."""
from datetime import datetime, timedelta, timezone

import pytest
from flask import session

from stationnet import mud
from stationnet.db import get_db

from test_mud_wilderness import _cell, _client, _place, _town, _user, game  # noqa: F401  (shared fixture)


def _travel(db, uid, direction):
    return mud._travel_action(db, _user(db, uid), {"arguments": [direction]})


def _tick(db, steps=1):
    """Advance travel as the scheduler would, one interval at a time."""
    for _ in range(steps):
        due = db.execute("SELECT MIN(next_due) FROM mud_travel").fetchone()[0]
        if due is None:
            return
        mud.process_travel(db, datetime.fromisoformat(due))
        # The walker keeps polling, so their presence stays live between steps.
        db.execute("UPDATE mud_presence SET last_seen=? WHERE disconnected_at IS NULL", (mud._scheduled_now(),))


def _bodies(db, uid):
    return [row["body"] for row in mud._events_for_user(db, uid)]


def _command(app, uid, text):
    with app.test_request_context("/mud/w/stationnet/actions", method="POST"):
        db = get_db()
        session["connection_id"] = db.execute("SELECT connection_id FROM users WHERE id=?", (uid,)).fetchone()[0]
        definition, parameters = mud.actions.from_command(text)
        return mud.service.execute(mud.ActionRequest(definition, parameters), _user(db, uid))


def test_travel_requires_wilderness(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        with pytest.raises(mud.ActionError, match="wilderness"):
            _travel(db, one, "north")
        _place(db, one, _cell(1, -1))
        with pytest.raises(mud.ActionError, match="which way"):
            _travel(db, one, "sideways")
        assert "set off north" in _travel(db, one, "n")[0].body
        assert mud.travel_state(db, one) == {"direction": "north", "steps": 0}


def test_travel_walks_room_by_room_until_blocked(game):
    app, (one, two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(2, -2))
        _place(db, two, _cell(1, -1))
        _travel(db, one, "west")
        mud.process_travel(db)  # Not due yet.
        assert mud._current_room(db, one)["id"] == _cell(2, -2)
        _tick(db)
        assert mud._current_room(db, one)["id"] == _cell(1, -2)
        assert mud.travel_state(db, one)["steps"] == 1
        # Each room is one concise line, not a full description.
        assert any(body.startswith("You travel west to") and "(1 room)" in body for body in _bodies(db, one))
        _tick(db, 2)
        # (-2,-2) walls off its east edge, so the walk ends one room short.
        assert mud._current_room(db, one)["id"] == _cell(-1, -2)
        assert mud.travel_state(db, one) is None
        assert any("no further west" in body for body in _bodies(db, one))


def test_observers_see_travellers_come_and_go(game):
    app, (one, two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(1, -1))
        _place(db, two, _cell(1, -1))
        _travel(db, one, "west")
        _tick(db)
        assert "One leaves via west." in _bodies(db, two)


def test_another_player_stops_travel(game):
    app, (one, two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(2, -2))
        _place(db, two, _cell(0, -2))
        _travel(db, one, "west")
        _tick(db, 3)
        assert mud._current_room(db, one)["id"] == _cell(0, -2)
        assert mud.travel_state(db, one) is None
        assert any("Two is here" in body for body in _bodies(db, one))
        assert "One arrives." in _bodies(db, two)


@pytest.mark.parametrize(("start", "direction", "stop", "message"), [
    ((2, 0), "south", (2, 1), "land changes"),          # forest into desert
    ((0, -1), "south", (0, 0), "arrive at Market Town"),  # authored room
    ((1, -1), "east", (2, -1), "leads out of the wilderness"),  # cave mouth exit
    ((-2, 0), "north", (-2, -2), "Hermit is here"),     # resident NPC
    ((-1, -1), "west", (-2, -1), "no further west"),    # edge of the map
])
def test_travel_interruptions(game, start, direction, stop, message):
    app, (one, two) = game
    with app.app_context():
        db = get_db()
        _place(db, two, _town(db) if stop != (0, 0) else _cell(2, 2))
        _place(db, one, _cell(*start))
        _travel(db, one, direction)
        _tick(db, 4)
        room = mud._current_room(db, one)
        assert room["id"] == (_town(db) if stop == (0, 0) else _cell(*stop))
        assert mud.travel_state(db, one) is None
        bodies = _bodies(db, one)
        assert any(message in body for body in bodies), bodies
        # Where travel stops, the player gets the full room view again.
        assert room["description"] in bodies[-1]


def test_encounter_stops_travel(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(2, 2))
        _travel(db, one, "west")
        _tick(db)
        assert mud._current_room(db, one)["id"] == _cell(1, 2)
        assert mud.travel_state(db, one) is None
        assert any("Grey Wolf" in body for body in _bodies(db, one))


def test_step_limit_pauses_travel(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        db.execute("UPDATE mud_travel_settings SET max_steps=1")
        _place(db, one, _cell(2, -2))
        _travel(db, one, "west")
        _tick(db, 2)
        assert mud._current_room(db, one)["id"] == _cell(1, -2)
        assert mud.travel_state(db, one) is None


def test_disconnect_and_death_end_travel(game):
    app, (one, two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(2, -2))
        _travel(db, one, "west")
        db.execute("UPDATE mud_presence SET disconnected_at=? WHERE user_id=?", (mud._scheduled_now(), one))
        mud.process_travel(db, datetime.now(timezone.utc) + timedelta(seconds=60))
        assert mud.travel_state(db, one) is None
        assert mud._current_room(db, one)["id"] == _cell(2, -2)

        _place(db, two, _cell(2, -2))
        _travel(db, two, "west")
        # Death and recovery move the player out of the wilds.
        mud._set_player_location(db, two, "procedural", "portal", mud.PORTAL_ROOM_ID)
        mud.process_travel(db, datetime.now(timezone.utc) + timedelta(seconds=60))
        assert mud.travel_state(db, two) is None


def test_teleport_ends_travel(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(2, -2))
        _travel(db, one, "west")
        _place(db, one, _cell(2, -1))
        _tick(db)
        assert mud._current_room(db, one)["id"] == _cell(2, -1)
        assert mud.travel_state(db, one) is None


def test_commands_stop_or_keep_travel(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(2, -2))
        db.commit()
    _command(app, one, "/travel west")
    with pytest.raises(mud.ActionError):
        _command(app, one, "/travel sw")  # forest rooms only link four ways
    _command(app, one, "/travel south")  # a second journey replaces the first
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM mud_travel").fetchone()[0] == 1
    _, events = _command(app, one, "/look")
    with app.app_context():
        assert mud.travel_state(get_db(), one)["direction"] == "south"
    _, events = _command(app, one, "/travel")
    assert "Travelling south" in events[0]["body"]
    _, events = _command(app, one, "/w")
    assert events[0]["body"] == "You stop travelling."
    with app.app_context():
        assert mud.travel_state(get_db(), one) is None
    _command(app, one, "/travel south")
    _, events = _command(app, one, "/stop")
    assert "You stop travelling after 0 rooms." in events[0]["body"]
    _, events = _command(app, one, "/stop")
    assert events[0]["body"] == "You are not travelling."


def test_scheduled_tick_drives_travel(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(2, -2))
        _travel(db, one, "west")
        db.commit()
    mud.run_scheduled_tick(app, datetime.now(timezone.utc) + timedelta(seconds=30))
    with app.app_context():
        db = get_db()
        assert mud._current_room(db, one)["id"] == _cell(1, -2)
        assert mud.travel_state(db, one)["steps"] == 1


def test_settings_save_travel_interval(game):
    app, (one, _two) = game
    client = _client(app, one)
    with app.app_context():
        db = get_db()
        portal = db.execute("SELECT name,description FROM mud_portal_settings WHERE id=1").fetchone()
    form = {"csrf_token": "test-token", "mud_mode_form": "1", "mud_traditional_enabled": "on",
            "mud_portal_name": portal["name"], "mud_portal_description": portal["description"],
            "mud_travel_max_steps": "50"}
    client.post("/sysop/settings/mud", data={**form, "mud_travel_interval": "45"})
    with app.app_context():
        assert mud._travel_settings(get_db())["interval_seconds"] == 6
    client.post("/sysop/settings/mud", data={**form, "mud_travel_interval": "8"})
    with app.app_context():
        assert mud._travel_settings(get_db()) == {"interval_seconds": 8, "max_steps": 50}
