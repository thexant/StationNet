"""Ephemeral live-signal radio transport.

The server arbitrates frequency occupancy and relays short-lived signal events. It
does not retain radio messages, decoded text, or completed pictures.
"""
import base64
import binascii
import json
import logging
import re
from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import RLock
from time import monotonic

from flask import Blueprint, Response, current_app, g, jsonify, render_template, request, session

from .auth import login_required
from .db import get_db
from .external_radio import STREAM_CHUNK_BYTES, open_audio_stream
from .security import validate_csrf

bp = Blueprint("radio", __name__, url_prefix="/radio")

FREQUENCY_RE = re.compile(r"^(?:\d{1,3})(?:\.\d{1,2})?$")
TUNING_STEP_KHZ = 500
MIN_FREQUENCY_KHZ = 100
MAX_FREQUENCY_KHZ = 999990
SSTV_MAX_WIDTH = 80
SSTV_MAX_HEIGHT = 60
VOICE_SIGNAL_MAX_BYTES = 65536
VOICE_SIGNAL_TYPES = {"offer", "answer", "candidate"}


def format_frequency(khz):
    return f"{khz / 1000:.2f}"


def parse_frequency(value):
    """Parse fixed-point MHz without float rounding or malformed input."""
    if not isinstance(value, str) or not FREQUENCY_RE.fullmatch(value.strip()):
        return None
    pieces = value.strip().split(".")
    whole, fraction = pieces[0], pieces[1] if len(pieces) == 2 else ""
    khz = int(whole) * 1000 + int(fraction.ljust(2, "0")) * 10
    return khz if MIN_FREQUENCY_KHZ <= khz <= MAX_FREQUENCY_KHZ else None


def find_band(khz):
    if khz is None:
        return None
    return get_db().execute(
        "SELECT * FROM radio_bands WHERE start_khz<=? AND end_khz>=? "
        "ORDER BY start_khz LIMIT 1", (khz, khz),
    ).fetchone()


def effective_access(user_id, khz):
    """Resolve group rules at a frequency: specificity per group, then deny wins."""
    band = find_band(khz)
    if khz is None or not MIN_FREQUENCY_KHZ <= khz <= MAX_FREQUENCY_KHZ:
        return "DENIED"
    band_id = band["id"] if band else None
    rows = get_db().execute(
        "SELECT r.* FROM radio_permission_rules r "
        "JOIN radio_group_memberships m ON m.group_id=r.group_id "
        "WHERE m.user_id=? AND ((r.scope_type='band' AND r.band_id=?) "
        "OR (r.scope_type IN ('range','frequency') AND r.start_khz<=? AND r.end_khz>=?)) "
        "ORDER BY r.group_id, CASE r.scope_type WHEN 'frequency' THEN 3 "
        "WHEN 'range' THEN 2 ELSE 1 END DESC, r.id DESC",
        (user_id, band_id, khz, khz),
    ).fetchall()
    per_group = {}
    for rule in rows:
        per_group.setdefault(rule["group_id"], rule["permission"])
    permissions = set(per_group.values())
    if "DENIED" in permissions:
        return "DENIED"
    if "FULL" in permissions:
        return "FULL"
    if "LISTEN" in permissions:
        return "LISTEN"
    return "DENIED"


def can_host_broadcast(user_id, khz):
    """Return whether group capabilities and frequency access permit hosting."""
    if effective_access(user_id, khz) != "FULL":
        return False
    return get_db().execute(
        "SELECT 1 FROM radio_permission_groups g "
        "JOIN radio_group_memberships m ON m.group_id=g.id "
        "WHERE m.user_id=? AND g.host_broadcasts=1 LIMIT 1", (user_id,),
    ).fetchone() is not None


def can_manage_broadcasts(user_id):
    """Allow capable hosts to prepare media before a frequency is assigned."""
    database = get_db()
    capable = database.execute(
        "SELECT 1 FROM radio_permission_groups g "
        "JOIN radio_group_memberships m ON m.group_id=g.id "
        "WHERE m.user_id=? AND g.host_broadcasts=1 LIMIT 1", (user_id,),
    ).fetchone()
    if capable is None:
        return False
    bands = database.execute(
        "SELECT start_khz,end_khz FROM radio_bands WHERE supports_hosted_broadcasts=1"
    ).fetchall()
    candidates = {point for band in bands for point in (band["start_khz"], band["end_khz"])}
    for rule in database.execute(
        "SELECT r.start_khz,r.end_khz FROM radio_permission_rules r "
        "JOIN radio_group_memberships m ON m.group_id=r.group_id "
        "WHERE m.user_id=? AND r.scope_type IN ('range','frequency')", (user_id,),
    ):
        for band in bands:
            start = max(rule["start_khz"], band["start_khz"])
            end = min(rule["end_khz"], band["end_khz"])
            if start <= end:
                candidates.update(
                    point for point in (start - 10, start, end, end + 10)
                    if band["start_khz"] <= point <= band["end_khz"]
                )
    return any(effective_access(user_id, khz) == "FULL" for khz in candidates)


def transmission_authorized(user_id, khz, transmission_type):
    """Central server-side policy for current and planned transmission types."""
    access = effective_access(user_id, khz)
    if access != "FULL":
        return False
    band = find_band(khz)
    kind = transmission_type.upper()
    if kind == "CW":
        return bool(band and band["supports_cw"])
    if kind == "SSTV":
        return bool(band and band["supports_sstv"])
    if kind == "LIVE_VOICE":
        return bool(band and band["supports_live_voice"])
    if kind == "HOSTED_BROADCAST":
        return bool(band and band["supports_hosted_broadcasts"] and
                    can_host_broadcast(user_id, khz))
    return False


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
        self._voice_signals = defaultdict(deque)
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
            "mode": transmission["mode"],
            "transmission_type": transmission["transmission_type"],
            "callsign": transmission["callsign"],
            "owner": {"user_id": transmission["user_id"], "callsign": transmission["callsign"]},
            "state": "ended" if kind == "end" else "active",
            "started_at": transmission["started_at"],
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
        event = self._append_event(
            transmission, "end", reason=reason,
            ended_at=datetime.now(timezone.utc).isoformat(),
        )
        del self._transmissions[transmission["id"]]
        self._frequency_owners.pop(transmission["frequency"], None)
        self._clear_voice_signals_locked(transmission["id"])
        return event

    def _clear_voice_signals_locked(self, transmission_id):
        for target, queue in list(self._voice_signals.items()):
            kept = deque(signal for signal in queue
                         if signal["transmission_id"] != transmission_id)
            if kept:
                self._voice_signals[target] = kept
            else:
                self._voice_signals.pop(target, None)

    def _disconnect_locked(self, connection_id):
        self._receivers.pop(connection_id, None)
        self._voice_signals.pop(connection_id, None)
        for target, queue in list(self._voice_signals.items()):
            kept = deque(signal for signal in queue if signal["from_peer"] != connection_id)
            if kept:
                self._voice_signals[target] = kept
            else:
                self._voice_signals.pop(target, None)
        for transmission in list(self._transmissions.values()):
            if transmission["connection_id"] == connection_id:
                self._finish_locked(transmission, "disconnected")

    def _prune(self, now):
        while self._events and now - self._events[0]["stamp"] > self.event_lifetime:
            self._events.popleft()
        for transmission in list(self._transmissions.values()):
            if not transmission.get("server_hosted") and now - transmission["last_seen"] > self.transmission_timeout:
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
                "mode": mode, "transmission_type": mode,
                "started": now, "started_at": datetime.now(timezone.utc).isoformat(),
                "state": "active", "last_seen": now,
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

    def start_hosted(self, owner_key, frequency, callsign, public_user_id=None, **metadata):
        """Reserve a frequency for server-owned playback, independent of a browser."""
        now = monotonic()
        connection_id = f"hosted:{owner_key}"
        if public_user_id is None and isinstance(owner_key, int):
            public_user_id = owner_key
        with self._lock:
            self._prune(now)
            if frequency in self._frequency_owners:
                raise RuntimeError("Frequency busy.")
            if any(item.get("hosted_owner_key") == owner_key and item.get("server_hosted")
                   for item in self._transmissions.values()):
                raise RuntimeError("You already have an active hosted broadcast.")
            transmission = {
                "id": self._next_transmission_id, "connection_id": connection_id,
                "user_id": public_user_id, "hosted_owner_key": owner_key,
                "frequency": frequency, "callsign": callsign,
                "mode": "MUSIC", "transmission_type": "HOSTED_BROADCAST",
                "started": now, "started_at": datetime.now(timezone.utc).isoformat(),
                "state": "active", "last_seen": now, "server_hosted": True,
                "key_down": None, "last_key_up": None, "next_row": 0, **metadata,
            }
            self._next_transmission_id += 1
            self._transmissions[transmission["id"]] = transmission
            self._frequency_owners[frequency] = transmission["id"]
            return transmission["id"], self._append_event(transmission, "start")

    def end_hosted(self, owner_key, reason="ended"):
        with self._lock:
            self._prune(monotonic())
            for item in list(self._transmissions.values()):
                if item.get("hosted_owner_key") == owner_key and item.get("server_hosted"):
                    return self._finish_locked(item, reason)
            return None

    def hosted_for_user(self, owner_key):
        with self._lock:
            self._prune(monotonic())
            for item in self._transmissions.values():
                if item.get("hosted_owner_key") == owner_key and item.get("server_hosted"):
                    return dict(item)
            return None

    def update_hosted(self, owner_key, **metadata):
        with self._lock:
            for item in self._transmissions.values():
                if item.get("hosted_owner_key") == owner_key and item.get("server_hosted"):
                    item.update(metadata)
                    return True
            return False

    def current(self, frequency):
        """Return the current authoritative transmission at a frequency."""
        with self._lock:
            self._prune(monotonic())
            transmission_id = self._frequency_owners.get(frequency)
            return self._public_transmission(self._transmissions.get(transmission_id))

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
            if receiver is None:
                # A sleeping tab or brief network outage can outlive receiver_lifetime.
                # Restore its listening lease without retuning (which would also end a
                # transmission and discard the event cursor).
                receiver = {"user_id": user_id, "frequency": frequency, "stamp": now}
                self._receivers[connection_id] = receiver
                logging.getLogger(__name__).info(
                    "Restored radio receiver lease for user %s on %s kHz", user_id, frequency
                )
            if receiver["frequency"] != frequency:
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
        result = {
            "id": transmission["id"],
            "mode": transmission["mode"],
            "transmission_type": transmission["transmission_type"],
            "owner": {"user_id": transmission["user_id"], "callsign": transmission["callsign"]},
            "callsign": transmission["callsign"],
            "frequency_khz": transmission["frequency"],
            "frequency": format_frequency(transmission["frequency"]),
            "started_at": transmission["started_at"],
            "state": transmission["state"],
        }
        if transmission.get("server_hosted"):
            result["hosted"] = True
            result["automated"] = bool(transmission.get("automated"))
            result["playlist_name"] = transmission.get("playlist_name")
            result["track_name"] = transmission.get("track_name")
            result["track_title"] = transmission.get("track_title") or transmission.get("track_name")
            result["track_artist"] = transmission.get("track_artist") or ""
            result["audio_file_id"] = transmission.get("audio_file_id")
            result["audio_media_type"] = transmission.get("audio_media_type")
            result["external"] = bool(transmission.get("external"))
            result["external_url"] = transmission.get("external_url") if transmission.get("external") else None
            result["external_hls"] = bool(transmission.get("external_hls"))
            result["track_offset"] = transmission.get("track_offset", 0)
            result["track_serial"] = transmission.get("track_serial", 0)
            result["action_type"] = transmission.get("action_type", "music")
            result["action_text"] = transmission.get("action_text", "")
            result["content_id"] = transmission.get("content_id")
            result["ducking"] = transmission.get("ducking", .35)
            result["mic_active"] = bool(transmission.get("mic_active"))
        return result

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

    def active_frequencies(self):
        with self._lock:
            self._prune(monotonic())
            return list(self._frequency_owners)

    def transmission(self, connection_id, transmission_id):
        with self._lock:
            self._prune(monotonic())
            item = self._transmissions.get(transmission_id)
            if not item or item["connection_id"] != connection_id:
                return None
            return dict(item)

    def voice_peers(self, connection_id, transmission_id):
        """Return current receivers for a voice transmitter without exposing identities."""
        with self._lock:
            self._prune(monotonic())
            transmission = self._transmissions.get(transmission_id)
            hosted_dj = bool(transmission and transmission.get("server_hosted") and
                             transmission.get("mic_active") and
                             transmission.get("controller") == connection_id)
            if not transmission or (transmission["connection_id"] != connection_id or
                                    transmission["transmission_type"] != "LIVE_VOICE") and not hosted_dj:
                raise LookupError("Voice transmission is no longer active.")
            return [
                {"peer_id": peer_id, "user_id": receiver["user_id"]}
                for peer_id, receiver in self._receivers.items()
                if peer_id != connection_id and receiver["frequency"] == transmission["frequency"]
            ]

    def voice_signal(self, connection_id, transmission_id, target, kind, payload):
        """Relay ephemeral WebRTC negotiation data between the owner and tuned listeners."""
        now = monotonic()
        with self._lock:
            self._prune(now)
            transmission = self._transmissions.get(transmission_id)
            hosted_dj = bool(transmission and transmission.get("server_hosted") and
                             transmission.get("mic_active") and transmission.get("controller"))
            if not transmission or (transmission["transmission_type"] != "LIVE_VOICE" and not hosted_dj):
                raise LookupError("Voice transmission is no longer active.")
            owner = transmission.get("controller") if hosted_dj else transmission["connection_id"]
            receiver = self._receivers.get(connection_id)
            target_receiver = self._receivers.get(target)
            participant = bool(receiver and receiver["frequency"] == transmission["frequency"])
            target_participant = bool(
                target_receiver and target_receiver["frequency"] == transmission["frequency"]
            )
            if not participant or not target_participant or not (
                    (connection_id == owner and target != owner) or
                    (connection_id != owner and target == owner)):
                raise PermissionError("Voice signaling peer is not on this frequency.")
            queue = self._voice_signals[target]
            if len(queue) >= 256:
                queue.popleft()
            queue.append({
                "transmission_id": transmission_id,
                "from_peer": connection_id,
                "type": kind,
                "payload": payload,
                "stamp": now,
            })

    def voice_signals(self, connection_id, transmission_id):
        with self._lock:
            self._prune(monotonic())
            transmission = self._transmissions.get(transmission_id)
            receiver = self._receivers.get(connection_id)
            hosted_dj = bool(transmission and transmission.get("server_hosted") and
                             transmission.get("mic_active") and transmission.get("controller"))
            if not transmission or (transmission["transmission_type"] != "LIVE_VOICE" and not hosted_dj) or not \
                    receiver or receiver["frequency"] != transmission["frequency"]:
                raise LookupError("Voice transmission is no longer active on this frequency.")
            queue = self._voice_signals.get(connection_id, deque())
            selected = []
            retained = deque()
            for signal in queue:
                if signal["transmission_id"] == transmission_id:
                    selected.append({key: value for key, value in signal.items() if key != "stamp"})
                else:
                    retained.append(signal)
            if retained:
                self._voice_signals[connection_id] = retained
            else:
                self._voice_signals.pop(connection_id, None)
            return selected

    def reconcile_permissions(self, access_lookup, authorization_lookup, user_id=None):
        """Immediately stop live activity invalidated by an administration change."""
        with self._lock:
            self._prune(monotonic())
            for transmission in list(self._transmissions.values()):
                if (user_id is None or transmission["user_id"] == user_id) and not \
                        authorization_lookup(transmission["user_id"], transmission["frequency"],
                                             transmission["transmission_type"]):
                    self._finish_locked(transmission, "permission_revoked")
            for connection_id, receiver in list(self._receivers.items()):
                if (user_id is None or receiver["user_id"] == user_id) and \
                        access_lookup(receiver["user_id"], receiver["frequency"]) == "DENIED":
                    self._disconnect_locked(connection_id)


def _store():
    return current_app.extensions["radio"]


def _band_payload(row, user_id=None, frequency=None):
    if row is None:
        data = {"id": None, "name": "OUTSIDE DEFINED BAND", "start": None, "end": None}
    else:
        data = {**dict(row), "start": format_frequency(row["start_khz"]),
                "end": format_frequency(row["end_khz"])}
    if user_id is not None:
        data["access"] = effective_access(
            user_id, frequency if frequency is not None else row["start_khz"]
        )
    return data


def _bands_payload(user_id=None):
    return [_band_payload(row, user_id) for row in
            get_db().execute("SELECT * FROM radio_bands ORDER BY start_khz")]


def _default_frequency(user_id=None):
    rows = get_db().execute("SELECT start_khz FROM radio_bands ORDER BY start_khz").fetchall()
    if user_id is not None:
        for row in rows:
            if effective_access(user_id, row["start_khz"]) != "DENIED":
                return row["start_khz"]
    return rows[0]["start_khz"] if rows else None


def _with_username(item):
    """Attach the account name to an ephemeral radio identity for display."""
    if not item:
        return item
    public = dict(item)
    user_id = (public.get("owner") or {}).get("user_id")
    if user_id is not None:
        user = get_db().execute(
            "SELECT username FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if user:
            public["username"] = user["username"]
    return public


def _events_with_usernames(events):
    usernames = {}
    for event in events:
        user_id = (event.get("owner") or {}).get("user_id")
        if user_id is not None and user_id not in usernames:
            row = get_db().execute(
                "SELECT username FROM users WHERE id=?", (user_id,)
            ).fetchone()
            usernames[user_id] = row["username"] if row else None
    result = []
    for event in events:
        public = dict(event)
        username = usernames.get((event.get("owner") or {}).get("user_id"))
        if username:
            public["username"] = username
        result.append(public)
    return result


def broadcast_identity(frequency):
    """Return the registered assignment label for a frequency, if one exists."""
    if frequency is None:
        return None
    external = get_db().execute(
        "SELECT id,name,message FROM radio_external_broadcasts WHERE frequency_khz=?",
        (frequency,),
    ).fetchone()
    if external is not None:
        live = current_app.extensions["hosted_broadcasts"].public(f"external:{external['id']}")
        active = _store().current(frequency) if live else None
        name = external["name"] or (active or {}).get("playlist_name") or "External radio"
        return {"registered": True, "automated": True, "name": name,
                "message": external["message"] or (live or {}).get("description", ""),
                "callsign": "STATION AUTOMATION",
                "username": None, "configured_name": external["name"]}
    automated = get_db().execute(
        "SELECT name,message FROM radio_automated_broadcasts WHERE frequency_khz=?",
        (frequency,),
    ).fetchone()
    if automated is not None:
        return {
            "registered": True, "automated": True, "name": automated["name"],
            "message": automated["message"], "callsign": "STATION AUTOMATION",
            "username": None, "configured_name": automated["name"],
        }
    program_rule = get_db().execute(
        "SELECT name,message FROM radio_automation_rules WHERE frequency_khz=? "
        "ORDER BY id LIMIT 1", (frequency,),
    ).fetchone()
    if program_rule is not None:
        return {
            "registered": True, "automated": True, "name": program_rule["name"],
            "message": program_rule["message"], "callsign": "STATION AUTOMATION",
            "username": None, "configured_name": program_rule["name"],
        }
    row = get_db().execute(
        "SELECT a.broadcast_name,a.broadcast_message,u.callsign,u.username "
        "FROM radio_broadcast_assignments a JOIN users u ON u.id=a.user_id "
        "WHERE a.frequency_khz=?", (frequency,),
    ).fetchone()
    if row is None:
        return None
    return {
        "registered": True,
        "name": row["broadcast_name"] or row["callsign"],
        "message": row["broadcast_message"],
        "callsign": row["callsign"],
        "username": row["username"],
        "configured_name": row["broadcast_name"],
    }


def broadcast_status(identity, active):
    """Describe live content separately from a frequency's registered identity."""
    if active and active.get("hosted"):
        return {
            "kind": "external" if active.get("external") else ("automated" if active.get("automated") else "hosted"),
            "label": "EXTERNAL BROADCAST" if active.get("external") else ("AUTOMATED BROADCAST" if active.get("automated") else "HOSTED PLAYBACK"),
            "active": True, "title": active.get("track_title") or active.get("track_name") or "Untitled track",
            "artist": active.get("track_artist") or "",
        }
    if active:
        transmission_type = active.get("transmission_type")
        if transmission_type == "LIVE_VOICE":
            kind, label = "live_voice", "LIVE VOICE TRANSMISSION"
        elif active.get("mode") == "MUSIC":
            kind, label = "live_music", "LIVE MUSIC"
        else:
            kind, label = "live_transmission", "LIVE TRANSMISSION"
        return {"kind": kind, "label": label, "active": True, "title": "", "artist": ""}
    return {"kind": "inactive", "label": "OFF AIR" if identity else "NO ACTIVE BROADCAST",
            "active": False, "title": "", "artist": ""}


def _frequency_snapshot(frequency, allowed=True):
    identity = broadcast_identity(frequency) if allowed else None
    active = _store().current(frequency) if allowed else None
    return identity, active, broadcast_status(identity, active) if allowed else None


def reconcile_radio_permissions(user_id=None):
    _store().reconcile_permissions(effective_access, transmission_authorized, user_id)
    current_app.extensions["hosted_broadcasts"].refresh(user_id)


@bp.get("/")
@login_required
def console():
    return render_template(
        "radio/console.html", can_manage_broadcasts=can_manage_broadcasts(g.user["id"])
    )


@bp.get("/state")
@login_required
def state():
    current_app.extensions["hosted_broadcasts"].refresh()
    frequency = _store().receiver_frequency(session["connection_id"])
    already_tuned = frequency is not None and MIN_FREQUENCY_KHZ <= frequency <= MAX_FREQUENCY_KHZ
    if not already_tuned:
        frequency = _default_frequency(g.user["id"])
    band = find_band(frequency)
    access = effective_access(g.user["id"], frequency)
    # Merely reading configuration must not activate reception.
    cursor = _store().cursor()
    identity, active, broadcast = _frequency_snapshot(
        frequency, already_tuned and access != "DENIED"
    )
    return jsonify(frequency=format_frequency(frequency) if frequency else None,
                   band=_band_payload(band, g.user["id"], frequency) if frequency else None,
                   bands=_bands_payload(g.user["id"]), cursor=cursor,
                   access=access, powered=already_tuned, step_khz=TUNING_STEP_KHZ,
                   identity=identity, active=_with_username(active), broadcast=broadcast)


@bp.post("/power-off")
@login_required
def power_off():
    validate_csrf()
    _store().disconnect(session["connection_id"])
    return jsonify(powered=False)


@bp.post("/tune")
@login_required
def tune():
    validate_csrf()
    current_app.extensions["hosted_broadcasts"].refresh()
    frequency = parse_frequency(request.form.get("frequency"))
    band = find_band(frequency)
    if frequency is None:
        return jsonify(error="Enter a frequency from 0.10 to 999.99 MHz with no more than two decimal places."), 400
    access = effective_access(g.user["id"], frequency)
    _store().disconnect(session["connection_id"])
    cursor = (_store().tune(session["connection_id"], g.user["id"], frequency)
              if access != "DENIED" else _store().cursor())
    identity, active, broadcast = _frequency_snapshot(frequency, access != "DENIED")
    return jsonify(frequency=format_frequency(frequency),
                   band=_band_payload(band, g.user["id"], frequency), cursor=cursor, access=access,
                   identity=identity, active=_with_username(active), broadcast=broadcast)


@bp.get("/receive")
@login_required
def receive():
    current_app.extensions["hosted_broadcasts"].refresh()
    frequency = parse_frequency(request.args.get("frequency"))
    if frequency is None:
        _store().disconnect(session["connection_id"])
        return jsonify(error="ACCESS RESTRICTED", access="DENIED"), 403
    access = effective_access(g.user["id"], frequency)
    if access == "DENIED":
        _store().disconnect(session["connection_id"])
        return jsonify(error="ACCESS RESTRICTED", access=access), 403
    after = max(0, request.args.get("after", 0, type=int))
    try:
        events, cursor, active = _store().receive(session["connection_id"], g.user["id"], frequency, after)
    except ValueError as problem:
        return jsonify(error=str(problem), access=access), 409
    identity = broadcast_identity(frequency)
    return jsonify(events=_events_with_usernames(events), cursor=cursor,
                   active=_with_username(active), access=access,
                   band=_band_payload(find_band(frequency), g.user["id"], frequency),
                   identity=identity, broadcast=broadcast_status(identity, active))


@bp.get("/external/<int:transmission_id>/audio")
@login_required
def external_audio(transmission_id):
    """Relay continuous external audio to an authorized, tuned receiver."""
    source = current_app.extensions["hosted_broadcasts"].external_source(transmission_id)
    if not source:
        return jsonify(error="External broadcast is off air."), 404
    frequency, url = source
    if (_store().receiver_frequency(session["connection_id"]) != frequency or
            effective_access(g.user["id"], frequency) == "DENIED"):
        return jsonify(error="Tune to this broadcast to listen."), 403
    try:
        upstream, mime, prefix = open_audio_stream(url)
    except Exception as problem:
        current_app.logger.warning("External radio %s connection failed: %s", transmission_id, problem)
        return jsonify(error=f"External stream unavailable: {problem}"), 502
    logger = current_app.logger
    broadcasts = current_app.extensions["hosted_broadcasts"]
    receiver = session["connection_id"]
    radio_store = _store()

    def chunks():
        try:
            if prefix:
                yield prefix
            while True:
                # Stop promptly when a sysop takes the broadcast off air. The
                # upstream socket timeout bounds a stalled read.
                if (broadcasts.external_source(transmission_id) != source or
                        radio_store.receiver_frequency(receiver) != frequency):
                    break
                data = upstream.read(STREAM_CHUNK_BYTES)
                if not data:
                    break
                yield data
        except (OSError, ValueError) as problem:
            logger.warning("External radio %s interrupted: %s", transmission_id, problem)
        finally:
            upstream.close()

    return Response(chunks(), mimetype=mime, headers={
        "Cache-Control": "no-store, no-transform", "X-Content-Type-Options": "nosniff",
        "X-Accel-Buffering": "no", "Content-Disposition": "inline",
    })


@bp.post("/transmit/start")
@login_required
def transmit_start():
    validate_csrf()
    frequency = parse_frequency(request.form.get("frequency"))
    if frequency is None:
        return jsonify(error="Frequency is outside the supported spectrum."), 400
    access = effective_access(g.user["id"], frequency)
    if access != "FULL":
        message = "RX ONLY" if access == "LISTEN" else "ACCESS RESTRICTED"
        return jsonify(error=message, access=access), 403
    mode = request.form.get("mode", "").upper()
    if mode not in {"CW", "SSTV", "VOICE"}:
        return jsonify(error="Select CW, SSTV, or VOICE mode."), 400
    transmission_type = "LIVE_VOICE" if mode == "VOICE" else mode
    if not transmission_authorized(g.user["id"], frequency, transmission_type):
        return jsonify(error="Transmission is not permitted on this frequency."), 403
    metadata = {}
    if mode == "CW":
        tone_hz = request.form.get("tone_hz", type=int)
        if tone_hz is None or not 400 <= tone_hz <= 1000:
            return jsonify(error="CW tone must be between 400 and 1000 Hz."), 400
        metadata["tone_hz"] = tone_hz
    elif mode == "SSTV":
        width, height = request.form.get("width", type=int), request.form.get("height", type=int)
        if not width or not height or not (1 <= width <= SSTV_MAX_WIDTH) or not (1 <= height <= SSTV_MAX_HEIGHT):
            return jsonify(error=f"SSTV pictures are limited to {SSTV_MAX_WIDTH} x {SSTV_MAX_HEIGHT} pixels."), 400
        metadata.update(width=width, height=height)
    try:
        transmission_id, event = _store().start(
            session["connection_id"], g.user["id"], frequency,
            g.user["callsign"], mode, transmission_type=transmission_type, **metadata
        )
    except ValueError as problem:
        return jsonify(error=str(problem)), 409
    except RuntimeError as problem:
        return jsonify(error=str(problem)), 409
    return jsonify(transmission_id=transmission_id, event=_with_username(event)), 201


@bp.post("/transmit/signal")
@login_required
def transmit_signal():
    validate_csrf()
    transmission_id = request.form.get("transmission_id", type=int)
    kind = request.form.get("kind", "")
    transmission = _store().transmission(session["connection_id"], transmission_id)
    if transmission and not transmission_authorized(
            g.user["id"], transmission["frequency"], transmission["transmission_type"]):
        _store().disconnect(session["connection_id"])
        return jsonify(error="Radio permission was revoked."), 403
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
    current_app.extensions["hosted_broadcasts"].refresh()
    validate_csrf()
    current = parse_frequency(request.form.get("frequency")) or _default_frequency(g.user["id"])
    direction = -1 if request.form.get("direction") == "down" else 1
    allocations = [(frequency, frequency) for frequency in _store().active_frequencies()
                   if effective_access(g.user["id"], frequency) != "DENIED"]
    found, cursor = _store().scan(session["connection_id"], g.user["id"], current, direction, allocations)
    if found is None:
        return jsonify(found=False, cursor=cursor)
    identity, active, broadcast = _frequency_snapshot(found)
    return jsonify(found=True, frequency=format_frequency(found),
                   band=_band_payload(find_band(found), g.user["id"], found),
                   access=effective_access(g.user["id"], found), cursor=cursor,
                   identity=identity, active=_with_username(active), broadcast=broadcast)


@bp.get("/voice/peers")
@login_required
def voice_peers():
    transmission_id = request.args.get("transmission_id", type=int)
    try:
        peers = _store().voice_peers(session["connection_id"], transmission_id)
    except LookupError as problem:
        return jsonify(error=str(problem)), 409
    allowed = []
    for peer in peers:
        receiver = _store().receiver_frequency(peer["peer_id"])
        if receiver is not None and effective_access(peer["user_id"], receiver) != "DENIED":
            allowed.append({"peer_id": peer["peer_id"]})
    return jsonify(peers=allowed)


@bp.route("/voice/signals", methods=("GET", "POST"))
@login_required
def voice_signals():
    if request.method == "GET":
        transmission_id = request.args.get("transmission_id", type=int)
        try:
            signals = _store().voice_signals(session["connection_id"], transmission_id)
        except LookupError as problem:
            return jsonify(error=str(problem)), 409
        return jsonify(signals=signals)

    validate_csrf()
    transmission_id = request.form.get("transmission_id", type=int)
    target = request.form.get("target", "")
    kind = request.form.get("type", "")
    raw_payload = request.form.get("payload", "")
    if kind not in VOICE_SIGNAL_TYPES or not target:
        return jsonify(error="Invalid voice signaling message."), 400
    if len(raw_payload.encode("utf-8")) > VOICE_SIGNAL_MAX_BYTES:
        return jsonify(error="Voice signaling message is too large."), 413
    try:
        payload = json.loads(raw_payload)
    except (TypeError, json.JSONDecodeError):
        return jsonify(error="Invalid voice signaling payload."), 400
    if not isinstance(payload, dict):
        return jsonify(error="Invalid voice signaling payload."), 400
    try:
        _store().voice_signal(
            session["connection_id"], transmission_id, target, kind, payload
        )
    except LookupError as problem:
        return jsonify(error=str(problem)), 409
    except PermissionError as problem:
        return jsonify(error=str(problem)), 403
    return jsonify(relayed=True)
