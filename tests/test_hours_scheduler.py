from datetime import datetime, timedelta
from pathlib import Path

import pytest

from stationnet import station_hours
from stationnet.hours_scheduler import HoursScheduler
from stationnet.station_hours import Hours
from stationnet.station_manager import StationInfo


class FakeManager:
    """Stations whose state changes as soon as they are started or stopped."""

    def __init__(self, root, stations):
        self.root = Path(root)
        self.stations = {info.id: info for info in stations}
        self.calls = []

    def list(self):
        return list(self.stations.values())

    def station(self, station_id):
        return self.stations[station_id]

    def start(self, station_id):
        self.calls.append(("start", station_id))
        self.stations[station_id].state = "running"

    def stop(self, station_id):
        self.calls.append(("stop", station_id))
        self.stations[station_id].state = "stopping"

    def restarting(self, _station_id):
        return False

    def set_extended(self, station_id, until):
        self.stations[station_id].extended_until = until


class FakePower:
    def __init__(self):
        self.settings = {}
        self.awake = False
        self.sleeps = 0
        self.wakes = []

    def keep_awake(self, on):
        self.awake = on

    def sleep_now(self):
        self.sleeps += 1

    def schedule_wake(self, when):
        self.wakes.append(when)

    def cancel_wake(self):
        self.wakes.append(None)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


def _station(station_id, state="running", **hours):
    values = {"open": "08:00", "close": "21:00", **hours}
    return StationInfo(id=station_id, name=station_id, data_dir=Path(station_id), state=state,
                       hours=Hours(**values))


@pytest.fixture
def setup(tmp_path):
    station_hours.save_host(tmp_path, sleep_countdown_minutes=5, wake_lead_minutes=5)

    def make(*stations, now=datetime(2026, 10, 28, 20, 50)):
        manager = FakeManager(tmp_path, stations)
        clock = Clock(now)
        power = FakePower()
        return manager, power, clock, HoursScheduler(manager, power, clock=clock)
    return make


def test_warning_then_close_then_sleep(setup):
    manager, power, clock, scheduler = setup(_station("a", sleep=True))
    assert scheduler.tick(manager.list())["a"].phase is None
    clock.advance(minutes=5, seconds=30)  # 20:55:30
    state = scheduler.tick(manager.list())["a"]
    assert (state.phase, state.seconds_left) == ("warning", 270)
    assert power.awake
    clock.advance(minutes=4, seconds=30)  # 21:00
    assert scheduler.tick(manager.list())["a"].phase == "closing"
    assert manager.calls == [("stop", "a")]
    manager.stations["a"].state = "stopped"
    clock.advance(seconds=1)
    assert scheduler.tick(manager.list())["a"].phase == "sleep"
    clock.advance(minutes=4)
    assert scheduler.tick(manager.list())["a"].seconds_left == 60
    clock.advance(minutes=1, seconds=1)
    scheduler.tick(manager.list())
    assert power.sleeps == 1
    # Woken before tomorrow's opening, then the opening starts the station.
    assert power.wakes[-1] == datetime(2026, 10, 29, 7, 55)
    clock.now = datetime(2026, 10, 29, 7, 55, 30)
    scheduler.tick(manager.list())
    assert power.awake
    clock.now = datetime(2026, 10, 29, 8, 0, 1)
    scheduler.tick(manager.list())
    assert manager.calls[-1] == ("start", "a")


def test_stay_online_cancels_the_closing(setup):
    manager, power, clock, scheduler = setup(_station("a", sleep=True), now=datetime(2026, 10, 28, 20, 56))
    scheduler.tick(manager.list())
    scheduler.stay_online("a")
    assert manager.stations["a"].extended_until == datetime(2026, 10, 29, 8, 0)
    clock.advance(minutes=4, seconds=1)
    assert scheduler.tick(manager.list())["a"].phase is None
    assert manager.calls == [] and power.sleeps == 0
    # The next opening ends the extension; that day's closing happens as usual.
    clock.now = datetime(2026, 10, 29, 8, 0, 1)
    scheduler.tick(manager.list())
    assert manager.stations["a"].extended_until is None
    clock.now = datetime(2026, 10, 29, 21, 0, 1)
    scheduler.tick(manager.list())
    assert manager.calls == [("stop", "a")]


def test_cancel_sleep(setup):
    manager, power, clock, scheduler = setup(_station("a", state="stopped", sleep=True),
                                             now=datetime(2026, 10, 28, 20, 59, 59))
    manager.stations["a"].state = "running"
    scheduler.tick(manager.list())
    clock.advance(seconds=2)
    scheduler.tick(manager.list())
    manager.stations["a"].state = "stopped"
    assert scheduler.tick(manager.list())["a"].phase == "sleep"
    scheduler.cancel_sleep()
    clock.advance(minutes=10)
    assert scheduler.tick(manager.list())["a"].phase is None
    assert power.sleeps == 0


def test_no_sleep_while_another_station_runs(setup):
    early = _station("a", close="19:00", sleep=True)
    late = _station("b", close="23:59", sleep=True)
    manager, power, clock, scheduler = setup(early, late, now=datetime(2026, 10, 28, 18, 59, 59))
    scheduler.tick(manager.list())
    clock.advance(seconds=2)
    scheduler.tick(manager.list())
    manager.stations["a"].state = "stopped"
    clock.advance(seconds=1)
    assert scheduler.tick(manager.list())["a"].phase is None
    clock.advance(minutes=30)
    scheduler.tick(manager.list())
    assert power.sleeps == 0
    # The last station to close does put the computer to sleep.
    clock.now = datetime(2026, 10, 28, 23, 59, 1)
    scheduler.tick(manager.list())
    manager.stations["b"].state = "stopped"
    clock.advance(seconds=1)
    assert scheduler.tick(manager.list())["b"].phase == "sleep"
    clock.advance(minutes=6)
    scheduler.tick(manager.list())
    assert power.sleeps == 1


def test_stations_closing_together_still_sleep(setup):
    manager, power, clock, scheduler = setup(_station("a", sleep=True), _station("b"),
                                             now=datetime(2026, 10, 28, 20, 59, 59))
    scheduler.tick(manager.list())
    clock.advance(seconds=2)
    scheduler.tick(manager.list())
    assert {call[1] for call in manager.calls} == {"a", "b"}
    manager.stations["a"].state = "stopped"
    clock.advance(seconds=1)
    scheduler.tick(manager.list())  # b still stopping
    manager.stations["b"].state = "stopped"
    clock.advance(seconds=1)
    assert scheduler.tick(manager.list())["a"].phase == "sleep"


def test_a_starting_station_cancels_the_countdown(setup):
    other = StationInfo(id="plain", name="plain", data_dir=Path("plain"), state="stopped")
    manager, power, clock, scheduler = setup(_station("a", sleep=True), other,
                                             now=datetime(2026, 10, 28, 20, 59, 59))
    scheduler.tick(manager.list())
    clock.advance(seconds=2)
    scheduler.tick(manager.list())
    manager.stations["a"].state = "stopped"
    assert scheduler.tick(manager.list())["a"].phase == "sleep"
    manager.stations["plain"].state = "starting"
    clock.advance(minutes=10)
    assert scheduler.tick(manager.list())["a"].phase is None
    assert power.sleeps == 0


def test_wake_time_is_host_level(setup):
    manager, power, clock, scheduler = setup(_station("a", open="09:00"), _station("b", open="07:00"),
                                             now=datetime(2026, 10, 28, 12, 0))
    scheduler.tick(manager.list())
    assert power.wakes == [] and scheduler.next_wake is None  # nothing may sleep: no wake needed
    manager.stations["a"].hours = Hours(open="09:00", close="21:00", sleep=True)
    scheduler.tick(manager.list())
    assert power.wakes == [datetime(2026, 10, 29, 6, 55)]  # b opens first, so it decides
    clock.advance(seconds=1)
    scheduler.tick(manager.list())
    assert len(power.wakes) == 1  # registered once, not every tick
    assert station_hours.load_host(manager.root)["registered_wake"] == "2026-10-29T06:55:00"
    # A new manager finds the registration already in place.
    fresh = HoursScheduler(manager, power, clock=clock)
    fresh.tick(manager.list())
    assert len(power.wakes) == 1
    manager.stations["a"].hours = Hours(open="09:00", close="21:00")
    scheduler.tick(manager.list())
    assert power.wakes[-1] is None


def test_opening_after_waking_late_and_manual_stops(setup):
    manager, power, clock, scheduler = setup(_station("a", state="stopped"), now=datetime(2026, 10, 28, 7, 50))
    scheduler.tick(manager.list())
    clock.now = datetime(2026, 10, 28, 8, 20)  # slept through the opening
    scheduler.tick(manager.list())
    assert manager.calls == [("start", "a")]
    manager.stations["a"].state = "stopped"  # stopped by hand during hours
    clock.advance(minutes=5)
    scheduler.tick(manager.list())
    assert manager.calls == [("start", "a")]


def test_a_day_slept_through_only_counts_the_last_crossing(setup):
    manager, power, clock, scheduler = setup(_station("a", state="stopped"), now=datetime(2026, 10, 28, 7, 0))
    scheduler.tick(manager.list())
    clock.now = datetime(2026, 10, 28, 22, 0)  # opening and closing both passed
    scheduler.tick(manager.list())
    assert manager.calls == []


def test_failed_opening_is_reported(setup):
    manager, power, clock, scheduler = setup(_station("a", state="stopped"), now=datetime(2026, 10, 28, 7, 59))

    def refuse(_station_id):
        raise ValueError("Port 8080 is in use by another program.")
    manager.start = refuse
    scheduler.tick(manager.list())
    clock.advance(minutes=2)
    assert "Port 8080" in scheduler.tick(manager.list())["a"].note
