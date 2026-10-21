"""Ephemeral live-signal radio transport.

The server arbitrates frequency occupancy and relays short-lived signal events. It
does not retain radio messages, decoded text, or completed pictures.
"""
import base64
import binascii
import re
from collections import deque
from threading import RLock
from time import monotonic

from flask import Blueprint, current_app, g, jsonify, request, session

from .auth import login_required
from .db import get_db
from .security import validate_csrf

bp = Blueprint("radio", __name__, url_prefix="/radio")

FREQUENCY_RE = re.compile(r"^(?:\d{1,3})\.\d{3}$")
TUNING_STEP_KHZ = 1
SSTV_MAX_WIDTH = 80
SSTV_MAX_HEIGHT = 60


def format_frequency(khz):
    return f"{khz / 1000:.3f}"


def parse_frequency(value):
    """Parse fixed-point MHz without float rounding or malformed input."""
    if not isinstance(value, str) or not FREQUENCY_RE.fullmatch(value.strip()):
        return None
    whole, fraction = value.strip().split(".")
    khz = int(whole) * 1000 + int(fraction)
    return khz if 100 <= khz <= 999999 else None


def find_band(khz):
    if khz is None:
        return None
    return get_db().execute(
        "SELECT * FROM radio_bands WHERE start_khz<=? AND end_khz>=? "
        "ORDER BY start_khz LIMIT 1", (khz, khz),
    ).fetchone()


class RadioStore:
    """Thread-safe receivers, frequency occupancy, and transient signal events."""

    event_lifetime = 15
    receiver_lifetime = 60
    transmission_timeout = 6

    def __init__(self):
        self._lock = RLock()
        self._events = deque()
        self._receivers = {}
        self._transmissions = {}
        self._frequency_owners = {}
        self._next_event_id = 1
        self._next_transmission_id = 1

    @staticmethod
    def _public_event(event):
        return {key: value for key, value in event.items() if key not in {"stamp", "owner_connection"}}

    def _append_event(self, transmission, kind, **payload):
        event = {
            "id": self._next_event_id,
            "transmission_id": transmission["id"],
            "frequency_khz": transmission["frequency"],
            "mode": transmission["mode"], "callsign": transmission["callsign"],
            "kind": kind, "stamp": monotonic(),
            "owner_connection": transmission["connection_id"], **payload,
        }
        self._next_event_id += 1
        self._events.append(event)
        return self._public_event(event)

    def _finish_locked(self, transmission, reason="ended"):
        if transmission["id"] not in self._transmissions:
            return None
        if transmission["mode"] == "CW" and transmission["key_down"] is not None:
            duration = max(1, min(5000, round((monotonic() - transmission["key_down"]) * 1000)))
            transmission["key_down"] = None
            transmission["last_key_up"] = monotonic()
            self._append_event(transmission, "key_up", duration_ms=duration)
        event = self._append_event(transmission, "end", reason=reason)
        del self._transmissions[transmission["id"]]
        self._frequency_owners.pop(transmission["frequency"], None)
        return event

    def _disconnect_locked(self, connection_id):
        self._receivers.pop(connection_id, None)
        for transmission in list(self._transmissions.values()):
            if transmission["connection_id"] == connection_id:
                self._finish_locked(transmission, "disconnected")

    def _prune(self, now):
        while self._events and now - self._events[0]["stamp"] > self.event_lifetime:
            self._events.popleft()
        for transmission in list(self._transmissions.values()):
            if now - transmission["last_seen"] > self.transmission_timeout:
                self._finish_locked(transmission, "timeout")
        for connection, receiver in list(self._receivers.items()):
            if now - receiver["stamp"] > self.receiver_lifetime:
                self._disconnect_locked(connection)

    def cursor(self):
        with self._lock:
            self._prune(monotonic())
            return self._next_event_id - 1

    def tune(self, connection_id, user_id, frequency):
        now = monotonic()
        with self._lock:
            self._prune(now)
            self._disconnect_locked(connection_id)
            self._receivers[connection_id] = {
                "user_id": user_id, "frequency": frequency, "stamp": now,
            }
            return self._next_event_id - 1

    def receiver_frequency(self, connection_id):
        with self._lock:
            self._prune(monotonic())
            receiver = self._receivers.get(connection_id)
            return receiver["frequency"] if receiver else None

    def start(self, connection_id, user_id, frequency, callsign, mode, **metadata):
        now = monotonic()
        with self._lock:
            self._prune(now)
            if self._receivers.get(connection_id, {}).get("frequency") != frequency:
                raise ValueError("Tune the receiver to this frequency before transmitting.")
            if frequency in self._frequency_owners:
                raise RuntimeError("Frequency busy.")
            transmission = {
                "id": self._next_transmission_id, "connection_id": connection_id,
                "user_id": user_id, "frequency": frequency, "callsign": callsign,
                "mode": mode, "started": now, "last_seen": now,
                "key_down": None, "last_key_up": None, "next_row": 0, **metadata,
            }
            self._next_transmission_id += 1
            self._transmissions[transmission["id"]] = transmission
            self._frequency_owners[frequency] = transmission["id"]
            event = self._append_event(
                transmission, "start", tone_hz=transmission.get("tone_hz"),
                width=transmission.get("width"), height=transmission.get("height"),
            )
            return transmission["id"], event

    def signal(self, connection_id, transmission_id, kind, **payload):
        now = monotonic()
        with self._lock:
            self._prune(now)
            transmission = self._transmissions.get(transmission_id)
            if not transmission or transmission["connection_id"] != connection_id:
                raise LookupError("Transmission is no longer active.")
            transmission["last_seen"] = now
            if kind == "heartbeat":
                return None
            if transmission["mode"] == "CW" and kind in {"key_down", "key_up"}:
                if kind == "key_down":
                    if transmission["key_down"] is not None:
                        return None
                    gap = None if transmission["last_key_up"] is None else round((now - transmission["last_key_up"]) * 1000)
                    transmission["key_down"] = now
                    return self._append_event(transmission, kind, tone_hz=transmission["tone_hz"], gap_ms=gap)
                if transmission["key_down"] is None:
                    return None
                duration = max(1, min(5000, round((now - transmission["key_down"]) * 1000)))
                transmission["key_down"] = None
                transmission["last_key_up"] = now
                return self._append_event(transmission, kind, duration_ms=duration)
            if transmission["mode"] == "SSTV" and kind == "scanline":
                row, pixels = payload.get("row"), payload.get("pixels", "")
                if row != transmission["next_row"] or row >= transmission["height"]:
                    raise ValueError("SSTV scanlines must arrive in order.")
                try:
                    decoded = base64.b64decode(pixels, validate=True)
                except (ValueError, binascii.Error):
                    raise ValueError("Invalid SSTV scanline data.") from None
                if len(decoded) != transmission["width"] * 3:
                    raise ValueError("Invalid SSTV scanline width.")
                transmission["next_row"] += 1
                return self._append_event(transmission, kind, row=row, pixels=pixels)
            raise ValueError("Signal event does not match the active mode.")

    def end(self, connection_id, transmission_id):
        with self._lock:
            self._prune(monotonic())
            transmission = self._transmissions.get(transmission_id)
            if not transmission or transmission["connection_id"] != connection_id:
                raise LookupError("Transmission is no longer active.")
            return self._finish_locked(transmission)

    def receive(self, connection_id, user_id, frequency, after):
        now = monotonic()
        with self._lock:
            self._prune(now)
            receiver = self._receivers.get(connection_id)
            if not receiver or receiver["frequency"] != frequency:
                raise ValueError("Receiver tuning changed; tune again before receiving.")
            receiver.update(user_id=user_id, stamp=now)
            events = []
            for event in self._events:
                if event["id"] > after and event["frequency_khz"] == frequency:
                    public = self._public_event(event)
                    public["mine"] = event["owner_connection"] == connection_id
                    events.append(public)
            active_id = self._frequency_owners.get(frequency)
            return events, self._next_event_id - 1, self._public_transmission(self._transmissions.get(active_id))

    @staticmethod
    def _public_transmission(transmission):
        if not transmission:
            return None
        return {"id": transmission["id"], "mode": transmission["mode"], "callsign": transmission["callsign"]}

    def scan(self, connection_id, user_id, current, direction, allocations):
        now = monotonic()
        with self._lock:
            self._prune(now)
            active = sorted(frequency for frequency in self._frequency_owners
                            if any(start <= frequency <= end for start, end in allocations))
            candidates = ([f for f in active if f > current] + [f for f in active if f <= current]) \
                if direction > 0 else ([f for f in reversed(active) if f < current] + [f for f in reversed(active) if f >= current])
            found = candidates[0] if candidates else None
            if found is not None:
                self._disconnect_locked(connection_id)
                self._receivers[connection_id] = {"user_id": user_id, "frequency": found, "stamp": now}
            return found, self._next_event_id - 1

    def disconnect(self, connection_id):
        with self._lock:
            self._disconnect_locked(connection_id)


def _store():
    return current_app.extensions["radio"]


def _bands_payload():
    return [{**dict(row), "start": format_frequency(row["start_khz"]), "end": format_frequency(row["end_khz"])}
            for row in get_db().execute("SELECT * FROM radio_bands ORDER BY start_khz")]


def _default_frequency():
    row = get_db().execute("SELECT start_khz FROM radio_bands ORDER BY start_khz LIMIT 1").fetchone()
    return row["start_khz"] if row else None


def _check_permission(band):
    if band["tx_permission"] == "receive":
        return "This band is receive-only.", 403
    if band["tx_permission"] == "admins" and not g.user["is_admin"]:
        return "SYSOP transmission authority is required on this band.", 403
    return None


@bp.get("/state")
@login_required
def state():
    frequency = _store().receiver_frequency(session["connection_id"])
    already_tuned = find_band(frequency) is not None
    if not already_tuned:
        frequency = _default_frequency()
    band = find_band(frequency)
    cursor = (_store().cursor() if already_tuned else
              _store().tune(session["connection_id"], g.user["id"], frequency) if band else _store().cursor())
    return jsonify(frequency=format_frequency(frequency) if frequency else None,
                   band=dict(band) if band else None, bands=_bands_payload(), cursor=cursor,
                   step_khz=TUNING_STEP_KHZ)


@bp.post("/tune")
@login_required
def tune():
    validate_csrf()
    frequency = parse_frequency(request.form.get("frequency"))
    band = find_band(frequency)
    if band is None:
        return jsonify(error="Enter a frequency in MHz with exactly three decimal places inside an allocated band."), 400
    cursor = _store().tune(session["connection_id"], g.user["id"], frequency)
    return jsonify(frequency=format_frequency(frequency), band=dict(band), cursor=cursor)


@bp.get("/receive")
@login_required
def receive():
    frequency = parse_frequency(request.args.get("frequency"))
    if find_band(frequency) is None:
        return jsonify(error="Receiver is not tuned to an allocated frequency."), 400
    after = max(0, request.args.get("after", 0, type=int))
    try:
        events, cursor, active = _store().receive(session["connection_id"], g.user["id"], frequency, after)
    except ValueError as problem:
        return jsonify(error=str(problem)), 409
    return jsonify(events=events, cursor=cursor, active=active)


@bp.post("/transmit/start")
@login_required
def transmit_start():
    validate_csrf()
    frequency = parse_frequency(request.form.get("frequency"))
    band = find_band(frequency)
    if band is None:
        return jsonify(error="Frequency is outside the station's allocated spectrum."), 400
    denied = _check_permission(band)
    if denied:
        return jsonify(error=denied[0]), denied[1]
    mode = request.form.get("mode", "").upper()
    if mode not in {"CW", "SSTV"}:
        return jsonify(error="Select CW or SSTV mode."), 400
    metadata = {}
    if mode == "CW":
        tone_hz = request.form.get("tone_hz", type=int)
        if tone_hz is None or not 400 <= tone_hz <= 1000:
            return jsonify(error="CW tone must be between 400 and 1000 Hz."), 400
        metadata["tone_hz"] = tone_hz
    else:
        width, height = request.form.get("width", type=int), request.form.get("height", type=int)
        if not width or not height or not (1 <= width <= SSTV_MAX_WIDTH) or not (1 <= height <= SSTV_MAX_HEIGHT):
            return jsonify(error=f"SSTV pictures are limited to {SSTV_MAX_WIDTH} x {SSTV_MAX_HEIGHT} pixels."), 400
        metadata.update(width=width, height=height)
    try:
        transmission_id, event = _store().start(session["connection_id"], g.user["id"], frequency,
                                                 g.user["callsign"], mode, **metadata)
    except ValueError as problem:
        return jsonify(error=str(problem)), 409
    except RuntimeError as problem:
        return jsonify(error=str(problem)), 409
    return jsonify(transmission_id=transmission_id, event=event), 201


@bp.post("/transmit/signal")
@login_required
def transmit_signal():
    validate_csrf()
    transmission_id = request.form.get("transmission_id", type=int)
    kind = request.form.get("kind", "")
    try:
        event = _store().signal(session["connection_id"], transmission_id, kind,
                                row=request.form.get("row", type=int), pixels=request.form.get("pixels", ""))
    except LookupError as problem:
        return jsonify(error=str(problem)), 409
    except ValueError as problem:
        return jsonify(error=str(problem)), 400
    return jsonify(event=event)


@bp.post("/transmit/end")
@login_required
def transmit_end():
    validate_csrf()
    try:
        event = _store().end(session["connection_id"], request.form.get("transmission_id", type=int))
    except LookupError as problem:
        return jsonify(error=str(problem)), 409
    return jsonify(event=event)


@bp.post("/scan")
@login_required
def scan():
    validate_csrf()
    current = parse_frequency(request.form.get("frequency")) or _default_frequency()
    direction = -1 if request.form.get("direction") == "down" else 1
    allocations = [(row["start_khz"], row["end_khz"])
                   for row in get_db().execute("SELECT start_khz,end_khz FROM radio_bands")]
    found, cursor = _store().scan(session["connection_id"], g.user["id"], current, direction, allocations)
    if found is None:
        return jsonify(found=False, cursor=cursor)
    return jsonify(found=True, frequency=format_frequency(found), band=dict(find_band(found)), cursor=cursor)
