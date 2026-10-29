"""Station Hours: the daily opening and closing times a station keeps.

Hours belong to a station and live in its ``station.json`` (set only from the
Station Manager). Sleep and wake settings belong to the computer and live in
the install root's ``manager.json``. Times are local wall-clock ``HH:MM``; a
closing time earlier than the opening time runs past midnight.
"""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

from . import instance
from .station_runtime import _retry

HOST_NAME = "manager.json"
LABEL = "STATION HOURS:"
DEFAULT_MESSAGE = "HOURS TEMPORARILY EXTENDED"
DEFAULT_WARNING = 5
CLOCKS = ("12h", "24h")
HOST_DEFAULTS = {"sleep_countdown_minutes": 5, "wake_lead_minutes": 5, "registered_wake": None,
                 "sleep_command": None, "wake_command": None}
EXTENDED_KEY = "hours_extended_until"
_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s*m?\.?)?", re.IGNORECASE)


def parse_time(text, what="time"):
    """``"8:00 pm"``, ``"20:00"`` or ``"9pm"`` as ``"HH:MM"``."""
    match = _TIME.fullmatch(str(text or "").strip())
    if not match or (match[2] is None and match[3] is None):
        raise ValueError(f"Enter the {what} as 21:00 or 9:00 PM.")
    hour, minute, half = int(match[1]), int(match[2] or 0), (match[3] or "").lower()
    if half:
        if not 1 <= hour <= 12:
            raise ValueError(f"Enter the {what} as 21:00 or 9:00 PM.")
        hour = hour % 12 + (12 if half == "p" else 0)
    if hour > 23 or minute > 59:
        raise ValueError(f"Enter the {what} as 21:00 or 9:00 PM.")
    return f"{hour:02d}:{minute:02d}"


def format_time(value, clock="12h"):
    hour, minute = map(int, value.split(":"))
    if clock == "24h":
        return f"{hour:02d}:{minute:02d}"
    return f"{hour % 12 or 12}:{minute:02d}{'AM' if hour < 12 else 'PM'}"


def _minutes(value, what, low=1, high=60):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"The {what} must be a number of minutes.") from None
    if not low <= number <= high:
        raise ValueError(f"The {what} must be between {low} and {high} minutes.")
    return number


def check(values):
    """Validate hours from the manager; returns the ``hours`` value for station.json."""
    enabled = bool(values.get("enabled"))
    result = {"enabled": enabled}
    for key, what in (("open", "opening time"), ("close", "closing time")):
        text = str(values.get(key) or "").strip()
        if text:
            result[key] = parse_time(text, what)
        elif enabled:
            raise ValueError(f"Enter the {what}.")
    if enabled and result["open"] == result["close"]:
        raise ValueError("The opening and closing times must differ.")
    clock = values.get("clock") or "12h"
    if clock not in CLOCKS:
        raise ValueError("Choose a 12-hour or 24-hour clock.")
    message = " ".join(str(values.get("adjusted_message") or "").split()) or DEFAULT_MESSAGE
    if len(message) > 80:
        raise ValueError("The adjusted hours message cannot exceed 80 characters.")
    result.update(clock=clock, sleep=bool(values.get("sleep")),
                  warning_minutes=_minutes(values.get("warning_minutes", DEFAULT_WARNING), "closing warning"),
                  adjusted_message=message)
    return result


@dataclass(frozen=True)
class Hours:
    open: str
    close: str
    clock: str = "12h"
    sleep: bool = False
    warning_minutes: int = DEFAULT_WARNING
    adjusted_message: str = DEFAULT_MESSAGE

    @classmethod
    def from_config(cls, config):
        """The enabled hours in a station.json, else None (also for unusable values)."""
        value = config.get("hours")
        if not isinstance(value, dict) or not value.get("enabled"):
            return None
        try:
            checked = check(value)
        except (ValueError, TypeError):
            return None
        return cls(**{key: item for key, item in checked.items() if key != "enabled"})

    @property
    def text(self):
        return f"{format_time(self.open, self.clock)} - {format_time(self.close, self.clock)}"


def _at(day, value):
    hour, minute = map(int, value.split(":"))
    return datetime.combine(day, clock_time(hour, minute))


def next_time(value, now):
    """The first ``HH:MM`` strictly after ``now``."""
    moment = _at(now.date(), value)
    return moment if moment > now else _at(now.date() + timedelta(days=1), value)


def previous_time(value, now):
    """The last ``HH:MM`` at or before ``now``."""
    moment = _at(now.date(), value)
    return moment if moment <= now else _at(now.date() - timedelta(days=1), value)


def crossed(value, since, now):
    """True if ``HH:MM`` fell in ``(since, now]``."""
    return since < now and previous_time(value, now) > since


def is_open(hours, now):
    return previous_time(hours.open, now) > previous_time(hours.close, now)


def extended_until(config):
    """When a cancelled closing stops counting, as stored; None if absent or unreadable."""
    value = config.get(EXTENDED_KEY)
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def is_extended(config, now):
    until = extended_until(config)
    return until is not None and until > now


class HeaderHours(NamedTuple):
    """The line under the station's sub header. ``opens`` and ``closes`` are the
    current or next opening and closing as UTC ISO times, for pages to show in
    each visitor's own time zone; None while the hours are extended."""
    label: str
    value: str
    opens: str | None = None
    closes: str | None = None
    clock: str = "12h"


def _utc(moment):
    # A naive time here is this computer's local time.
    return moment.astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")


def header_text(config, now):
    hours = Hours.from_config(config)
    if hours is None:
        return None
    if is_extended(config, now):
        return HeaderHours(LABEL, hours.adjusted_message, clock=hours.clock)
    opens = previous_time(hours.open, now) if is_open(hours, now) else next_time(hours.open, now)
    return HeaderHours(LABEL, hours.text, _utc(opens), _utc(next_time(hours.close, opens)), hours.clock)


_configs = {}


def header_for(config_path, now=None):
    """header_text() for a running station's station.json, re-read only when it changes."""
    if not config_path:
        return None
    path = Path(config_path)
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _configs.get(path)
    if cached is None or cached[0] != key:
        try:
            config = instance.load_config(path.parent)
        except ValueError:
            config = {}
        cached = _configs[path] = (key, config)
    return header_text(cached[1], now or datetime.now())


def load_host(root):
    """This computer's sleep and wake settings, with defaults for anything missing."""
    values = dict(HOST_DEFAULTS)
    try:
        saved = json.loads(_retry(lambda: (Path(root) / HOST_NAME).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        saved = {}
    if isinstance(saved, dict):
        values.update(saved)
    return values


def check_host(sleep_countdown, wake_lead):
    return {"sleep_countdown_minutes": _minutes(sleep_countdown, "sleep countdown"),
            "wake_lead_minutes": _minutes(wake_lead, "wake lead time")}


def save_host(root, **changes):
    path = Path(root) / HOST_NAME
    try:
        saved = json.loads(_retry(lambda: path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        saved = {}
    values = {**(saved if isinstance(saved, dict) else {}), **changes}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{HOST_NAME}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    try:
        _retry(lambda: os.replace(temp, path))
    finally:
        temp.unlink(missing_ok=True)
    return {**HOST_DEFAULTS, **values}
