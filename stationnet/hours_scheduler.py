"""Carries out station hours for every station of one install.

The Station Manager calls tick() about once a second. A station opens or
closes when its opening or closing time is crossed, so a station stopped by
hand during its hours stays stopped, and a computer that wakes late still
opens its stations. Sleep and wake are decided for the whole computer: it
sleeps only after a station with SLEEP closes while no other station is
active, and it wakes before the earliest opening of any station with hours
(while at least one station has SLEEP on).
"""
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import station_hours
from .station_hours import is_open, next_time, previous_time
from .station_manager import ACTIVE


@dataclass
class HoursState:
    phase: str | None = None  # "warning", "closing" or "sleep"
    seconds_left: int | None = None
    note: str | None = None


class HoursScheduler:
    def __init__(self, manager, power, clock=datetime.now):
        self.manager = manager
        self.power = power
        self.clock = clock
        self.next_wake = None
        self.wake_error = None
        self._last = None
        self._closing = set()  # stopped at closing time, not yet stopped
        self._sleep_wanted = None  # a SLEEP station closed; waits for the others closing with it
        self._sleep_at = None
        self._sleep_station = None
        self._notes = {}
        self._wake = None
        self._wake_known = False

    def settings(self):
        return station_hours.load_host(self.manager.root)

    def tick(self, stations):
        """Act on this moment; returns {station id: HoursState} for the cards."""
        now = self.clock()
        # The first tick, or a clock set backwards, crosses nothing.
        since = self._last if self._last is not None and self._last <= now else now
        self._last = now
        settings = self.settings()
        self.power.settings = settings
        states = {}
        for info in stations:
            if info.error or info.hours is None:
                self._closing.discard(info.id)
                continue
            states[info.id] = self._station_tick(info, since, now)
        self._sleep_tick(stations, states, now, settings)
        self._update_wake(stations, now, settings)
        self.power.keep_awake(self._wants_awake(stations, states, now, settings))
        return states

    def _station_tick(self, info, since, now):
        hours = info.hours
        extended = info.extended_until is not None and info.extended_until > now
        last_open, last_close = previous_time(hours.open, now), previous_time(hours.close, now)
        # A long sleep can cross both times; only the later one counts.
        opened = since < now and last_open > since and last_open > last_close
        closed = since < now and last_close > since and last_close > last_open
        if info.state in ACTIVE:
            self._notes.pop(info.id, None)
        if opened:
            if info.extended_until is not None:
                self._set_extended(info.id, None)
                extended = False
            if info.state in ("stopped", "crashed") and not self.manager.restarting(info.id):
                try:
                    self.manager.start(info.id)
                    self._notes.pop(info.id, None)
                except (ValueError, OSError) as exc:
                    self._notes[info.id] = f"Opening failed: {exc}"
            return HoursState(note=self._notes.get(info.id))
        if info.extended_until is not None and not extended:
            self._set_extended(info.id, None)  # expired
        if info.id in self._closing:
            if info.state in ACTIVE:
                return HoursState("closing")
            self._closing.discard(info.id)
            if hours.sleep:
                self._sleep_wanted = info.id
            return HoursState()
        if info.state not in ("starting", "running") or extended:
            return HoursState(note=self._notes.get(info.id))
        if closed:
            self.manager.stop(info.id)
            self._closing.add(info.id)
            return HoursState("closing")
        left = (next_time(hours.close, now) - now).total_seconds()
        if left <= hours.warning_minutes * 60:
            return HoursState("warning", math.ceil(left))
        return HoursState()

    def _sleep_tick(self, stations, states, now, settings):
        # Busy: a station that is not closing right now, or one about to close.
        busy = (any(info.state in ACTIVE and info.id not in self._closing for info in stations)
                or any(state.phase == "warning" for state in states.values()))
        if self._sleep_at is not None:
            if busy:
                self.cancel_sleep()
            elif now >= self._sleep_at:
                self._sleep(stations, now, settings)
                return
            else:
                left = math.ceil((self._sleep_at - now).total_seconds())
                states[self._sleep_station] = HoursState("sleep", left)
                return
        if self._sleep_wanted is None:
            return
        if busy:
            self._sleep_wanted = None  # another station is still open: it decides when it closes
        elif not self._closing:
            self._sleep_station, self._sleep_wanted = self._sleep_wanted, None
            self._sleep_at = now + timedelta(minutes=settings["sleep_countdown_minutes"])
            states[self._sleep_station] = HoursState("sleep", settings["sleep_countdown_minutes"] * 60)

    def _sleep(self, stations, now, settings):
        station_id = self._sleep_station
        self._sleep_at = self._sleep_station = None
        # The wake must be in place before the computer goes to sleep.
        self._update_wake(stations, now, settings)
        self.power.keep_awake(False)
        try:
            self.power.sleep_now()
        except OSError as exc:
            self._notes[station_id] = f"Sleep failed: {exc}"

    def _wants_awake(self, stations, states, now, settings):
        """Whether idle sleep must wait: a station is open, opening soon, or closing."""
        if self._sleep_at is not None or self._closing or any(state.phase for state in states.values()):
            return True
        lead = timedelta(minutes=settings["wake_lead_minutes"])
        for info in stations:
            if info.error or info.hours is None:
                continue
            extended = info.extended_until is not None and info.extended_until > now
            if info.state in ACTIVE and (extended or is_open(info.hours, now)):
                return True
            if next_time(info.hours.open, now) - now <= lead:
                return True
        return False

    @staticmethod
    def wake_time(stations, now, lead):
        """When to wake the computer: before the earliest opening of any station
        with hours, if any station may put the computer to sleep."""
        hours = [info.hours for info in stations if info.hours is not None and not info.error]
        if not any(item.sleep for item in hours):
            return None
        return min(next_time(item.open, now + lead) for item in hours) - lead

    def _update_wake(self, stations, now, settings):
        when = self.wake_time(stations, now, timedelta(minutes=settings["wake_lead_minutes"]))
        self.next_wake = when
        if not self._wake_known:
            self._wake_known = True
            try:
                saved = settings.get("registered_wake")
                self._wake = datetime.fromisoformat(saved) if saved else None
            except (TypeError, ValueError):
                self._wake = None
        if when == self._wake:
            return
        # Tried once per change; a failure is shown rather than retried every second.
        self._wake = when
        try:
            if when is None:
                self.power.cancel_wake()
            else:
                self.power.schedule_wake(when)
        except OSError as exc:
            self.wake_error = f"Could not set the wake timer: {exc}"
            return
        self.wake_error = None
        try:
            station_hours.save_host(self.manager.root,
                                    registered_wake=when.isoformat(timespec="seconds") if when else None)
        except OSError:
            pass

    def _set_extended(self, station_id, until):
        try:
            self.manager.set_extended(station_id, until)
        except (OSError, ValueError):
            pass

    def stay_online(self, station_id):
        """Cancel the coming closing; the station stays open until its next opening."""
        info = self.manager.station(station_id)
        if info.hours is None:
            return
        close = next_time(info.hours.close, self.clock())
        self.manager.set_extended(station_id, next_time(info.hours.open, close))

    def cancel_sleep(self):
        self._sleep_at = self._sleep_station = self._sleep_wanted = None

    def shutdown(self):
        """Release the keep-awake request (the wake timer stays registered)."""
        self.power.keep_awake(False)
