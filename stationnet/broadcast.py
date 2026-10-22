"""Private audio libraries and playlist preparation for broadcast hosts."""
import secrets
import sqlite3
import random
import struct
from threading import RLock, Timer
from time import monotonic
from functools import wraps
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, send_file, session, url_for)

from .auth import login_required
from .db import get_db
from .radio import (can_manage_broadcasts, effective_access, find_band,
                    format_frequency, parse_frequency, transmission_authorized)
from .security import validate_csrf

bp = Blueprint("broadcast", __name__, url_prefix="/radio/manage")

MIB = 1024 * 1024
MAX_AUDIO_FILE_BYTES = 64 * MIB
MAX_AUDIO_BATCH_FILES = 20
AUDIO_FORMATS = {
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}
BROADCAST_NAME_MAX_LENGTH = 80
BROADCAST_MESSAGE_MAX_LENGTH = 240
TRACK_METADATA_MAX_LENGTH = 240


class HostedBroadcastStore:
    """Server-owned playlist clocks; browsers are synchronized renderers only."""
    def __init__(self, radio_store):
        self.radio = radio_store
        self._lock = RLock()
        self._sessions = {}
        self._paused = {}

    @staticmethod
    def _elapsed(item, now=None):
        return item.get("resume_elapsed", 0) + max(
            0, (monotonic() if now is None else now) - item["clock_started"]
        )

    @staticmethod
    def _track_map(item):
        return {track["entry_id"]: track for track in item["tracks"]}

    @staticmethod
    def _set_current(item, track):
        item["current_track"] = track
        item["track_serial"] = item.get("track_serial", -1) + 1

    def _shuffle_ids(self, item, exclude_file_id=None):
        ids = [track["entry_id"] for track in item["tracks"]]
        random.shuffle(ids)
        by_id = self._track_map(item)
        if len(ids) > 1 and exclude_file_id is not None and \
                by_id[ids[0]]["file_id"] == exclude_file_id:
            replacement = next(
                (index for index, entry_id in enumerate(ids[1:], 1)
                 if by_id[entry_id]["file_id"] != exclude_file_id), None,
            )
            if replacement is not None:
                ids[0], ids[replacement] = ids[replacement], ids[0]
        return ids

    def _advance_track(self, item):
        tracks = item["tracks"]
        if not tracks:
            return False
        current = item.get("current_track")
        previous_file = current["file_id"] if current else None
        pending = item.pop("pending_shuffle", None)
        if pending is not None:
            item["shuffle"] = pending
            item["shuffle_remaining"] = self._shuffle_ids(item, previous_file) if pending else []
            if current:
                item["shuffle_remaining"] = [
                    entry_id for entry_id in item["shuffle_remaining"]
                    if entry_id != current["entry_id"]
                ]

        if current and len(tracks) == 1:
            self._set_current(item, tracks[0])
            item["shuffle_remaining"] = []
            return True

        if item["shuffle"]:
            valid_ids = set(self._track_map(item))
            remaining = [entry_id for entry_id in item.get("shuffle_remaining", [])
                         if entry_id in valid_ids and (not current or entry_id != current["entry_id"])]
            if not remaining:
                if current and not item["loop"]:
                    return False
                remaining = self._shuffle_ids(item, previous_file)
            entry_id = remaining.pop(0)
            item["shuffle_remaining"] = remaining
            self._set_current(item, self._track_map(item)[entry_id])
            return True

        if not current:
            self._set_current(item, tracks[0])
            return True
        ids = [track["entry_id"] for track in tracks]
        if current["entry_id"] in ids:
            next_index = ids.index(current["entry_id"]) + 1
        else:
            next_index = next(
                (index for index, track in enumerate(tracks)
                 if track.get("position", index) > current.get("position", -1)), len(tracks),
            )
        if next_index >= len(tracks):
            if not item["loop"]:
                return False
            next_index = 0
        self._set_current(item, tracks[next_index])
        return True

    def _position(self, item, now=None):
        elapsed = self._elapsed(item, now)
        if not item["indefinite"] and elapsed >= item["limit_seconds"]:
            return None, elapsed, "duration_limit"
        if not item["tracks"] or not item.get("current_track"):
            return None, elapsed, "playlist_complete"

        delta = max(0, elapsed - item.get("timeline_elapsed", 0))
        item["timeline_elapsed"] = elapsed
        item["track_offset"] = item.get("track_offset", 0) + delta
        transitions = 0
        while item["track_offset"] >= item["current_track"]["duration"]:
            item["track_offset"] -= item["current_track"]["duration"]
            if not self._advance_track(item):
                return None, elapsed, "playlist_complete"
            transitions += 1
            # Corrupt duration data must never create an unbounded retry loop.
            if transitions > max(1000, len(item["tracks"]) * 10):
                return None, elapsed, "playlist_complete"
        track = item["current_track"]
        index = next((i for i, candidate in enumerate(item["tracks"])
                      if candidate["entry_id"] == track["entry_id"]), 0)
        return (index, track, item["track_offset"]), elapsed, None

    def _schedule_end(self, item):
        old = item.pop("timer", None)
        if old:
            old.cancel()
        position, elapsed, reason = self._position(item)
        if reason:
            return
        deadlines = [position[1]["duration"] - position[2]]
        if not item["indefinite"]:
            deadlines.append(item["limit_seconds"] - elapsed)
        delay = max(.01, min(deadlines))
        timer = Timer(delay, self._expire, args=(item["user_id"], item["transmission_id"]))
        timer.daemon = True
        item["timer"] = timer
        timer.start()

    def _expire(self, user_id, transmission_id):
        with self._lock:
            item = self._sessions.get(user_id)
            if not item or item.get("transmission_id") != transmission_id:
                return
            position, _elapsed, reason = self._position(item)
            if reason:
                self.radio.end_hosted(user_id, reason)
                self._sessions.pop(user_id, None)
            elif position:
                track = position[1]
                self.radio.update_hosted(
                    user_id, playlist_name=item["playlist_name"], track_name=track["name"],
                    track_title=track.get("title") or Path(track["name"]).stem,
                    track_artist=track.get("artist") or "",
                    audio_file_id=track["file_id"], track_offset=round(position[2], 3),
                    track_serial=item["track_serial"],
                )
                self._schedule_end(item)

    def refresh(self, user_id=None):
        """Advance sessions and mirror current metadata into the radio arbiter."""
        from .radio import transmission_authorized
        with self._lock:
            selected = list(self._sessions.items())
            for owner_id, item in selected:
                if user_id is not None and owner_id != user_id:
                    continue
                position, elapsed, reason = self._position(item)
                if not reason and not transmission_authorized(owner_id, item["frequency"], "HOSTED_BROADCAST"):
                    reason = "permission_revoked"
                if reason:
                    self.radio.end_hosted(owner_id, reason)
                    if item.get("timer"):
                        item["timer"].cancel()
                    self._sessions.pop(owner_id, None)
                    continue
                index, track, offset = position
                item.update(track_index=index, elapsed=elapsed)
                self.radio.update_hosted(owner_id, playlist_name=item["playlist_name"],
                                         track_name=track["name"],
                                         track_title=track.get("title") or Path(track["name"]).stem,
                                         track_artist=track.get("artist") or "", audio_file_id=track["file_id"],
                                         track_offset=round(offset, 3),
                                         track_serial=item["track_serial"])

    def start(self, user_id, callsign, frequency, playlist_id, playlist_name,
              tracks, loop, limit_seconds, shuffle=False):
        with self._lock:
            self.refresh(user_id)
            if user_id in self._sessions:
                raise RuntimeError("You already have an active hosted broadcast.")
            item = {"user_id": user_id, "callsign": callsign, "frequency": frequency,
                    "playlist_id": playlist_id, "playlist_name": playlist_name,
                    "tracks": tracks, "loop": bool(loop), "shuffle": bool(shuffle),
                    "pending_shuffle": None, "shuffle_remaining": [],
                    "limit_seconds": limit_seconds,
                    "indefinite": False, "base_loop": bool(loop),
                    "clock_started": monotonic(), "resume_elapsed": 0,
                    "timeline_elapsed": 0, "track_offset": 0, "track_serial": -1}
            if not self._advance_track(item):
                raise RuntimeError("This playlist has no available audio tracks.")
            first = item["current_track"]
            transmission_id, _event = self.radio.start_hosted(
                user_id, frequency, callsign, playlist_name=playlist_name,
                track_name=first["name"], track_title=first.get("title") or Path(first["name"]).stem,
                track_artist=first.get("artist") or "", audio_file_id=first["file_id"], track_offset=0,
                track_serial=item["track_serial"],
            )
            item["transmission_id"] = transmission_id
            self._sessions[user_id] = item
            self._schedule_end(item)
            return self.public(user_id)

    def stop(self, user_id, reason="ended"):
        with self._lock:
            existed = self._sessions.pop(user_id, None) or self._paused.pop(user_id, None)
            if existed and existed.get("timer"):
                existed["timer"].cancel()
            self.radio.end_hosted(user_id, reason)
            return existed is not None

    def pause(self, user_id):
        with self._lock:
            self.refresh(user_id)
            item = self._sessions.pop(user_id, None)
            if not item:
                return False
            _position, elapsed, _reason = self._position(item)
            item["resume_elapsed"] = elapsed
            if item.get("timer"):
                item["timer"].cancel()
                item.pop("timer", None)
            self.radio.end_hosted(user_id, "paused")
            self._paused[user_id] = item
            return True

    def resume(self, user_id):
        with self._lock:
            item = self._paused.get(user_id)
            if not item:
                return False
            first = item["current_track"]
            transmission_id, _event = self.radio.start_hosted(
                user_id, item["frequency"], item["callsign"],
                playlist_name=item["playlist_name"], track_name=first["name"],
                track_title=first.get("title") or Path(first["name"]).stem,
                track_artist=first.get("artist") or "",
                audio_file_id=first["file_id"], track_offset=item.get("track_offset", 0),
                track_serial=item["track_serial"],
            )
            item["transmission_id"] = transmission_id
            item["clock_started"] = monotonic()
            item["timeline_elapsed"] = item["resume_elapsed"]
            self._paused.pop(user_id)
            self._sessions[user_id] = item
            self.refresh(user_id)
            self._schedule_end(item)
            return True

    def sync_playlist(self, user_id, playlist_id, playlist_name, tracks, loop, shuffle):
        """Apply playlist edits without restarting the current audio unnecessarily."""
        with self._lock:
            item = self._sessions.get(user_id) or self._paused.get(user_id)
            if not item or item["playlist_id"] != playlist_id:
                return False
            current = item.get("current_track")
            item["playlist_name"] = playlist_name
            item["tracks"] = tracks
            item["loop"] = True if item["indefinite"] else bool(loop)
            item["base_loop"] = bool(loop)
            if bool(shuffle) != item["shuffle"]:
                item["pending_shuffle"] = bool(shuffle)
            valid_ids = {track["entry_id"] for track in tracks}
            item["shuffle_remaining"] = [entry_id for entry_id in item.get("shuffle_remaining", [])
                                         if entry_id in valid_ids]
            if not current or current["entry_id"] not in valid_ids:
                item["current_track"] = current
                if not self._advance_track(item):
                    self.stop(user_id, "playlist_complete")
                    return True
                item["track_offset"] = 0
                item["timeline_elapsed"] = (item["resume_elapsed"] if user_id in self._paused
                                            else self._elapsed(item))
            elif current:
                item["current_track"] = next(
                    track for track in tracks if track["entry_id"] == current["entry_id"]
                )
            if user_id in self._sessions:
                position, _elapsed, reason = self._position(item)
                if reason:
                    self.stop(user_id, reason)
                else:
                    track = position[1]
                    self.radio.update_hosted(
                        user_id, playlist_name=playlist_name, track_name=track["name"],
                        track_title=track.get("title") or Path(track["name"]).stem,
                        track_artist=track.get("artist") or "",
                        audio_file_id=track["file_id"], track_offset=round(position[2], 3),
                        track_serial=item["track_serial"],
                    )
                    self._schedule_end(item)
            return True

    def advance(self, user_id, file_id, track_serial):
        """Advance one shared session when a browser proves the current file ended/failed."""
        with self._lock:
            item = self._sessions.get(user_id)
            if (not item or item["current_track"]["file_id"] != file_id or
                    item["track_serial"] != track_serial):
                return False
            elapsed = self._elapsed(item)
            item["timeline_elapsed"] = elapsed
            item["track_offset"] = 0
            if not self._advance_track(item):
                self.stop(user_id, "playlist_complete")
                return True
            track = item["current_track"]
            self.radio.update_hosted(
                user_id, playlist_name=item["playlist_name"], track_name=track["name"],
                track_title=track.get("title") or Path(track["name"]).stem,
                track_artist=track.get("artist") or "",
                audio_file_id=track["file_id"], track_offset=0,
                track_serial=item["track_serial"],
            )
            self._schedule_end(item)
            return True

    def correct_duration(self, user_id, file_id, track_serial, duration):
        """Align the server clock with duration metadata decoded by a listener."""
        with self._lock:
            item = self._sessions.get(user_id)
            if (not item or item["current_track"]["file_id"] != file_id or
                    item["track_serial"] != track_serial):
                return False
            for track in item["tracks"]:
                if track["file_id"] == file_id:
                    track["duration"] = duration
            position, _elapsed, reason = self._position(item)
            if reason:
                self.stop(user_id, reason)
                return True
            track = position[1]
            self.radio.update_hosted(
                user_id, playlist_name=item["playlist_name"], track_name=track["name"],
                audio_file_id=track["file_id"], track_offset=round(position[2], 3),
                track_serial=item["track_serial"],
            )
            self._schedule_end(item)
            return True

    def set_indefinite(self, user_id, enabled):
        with self._lock:
            item = self._sessions.get(user_id) or self._paused.get(user_id)
            if not item:
                return False
            item["indefinite"] = bool(enabled)
            item["loop"] = True if enabled else item["base_loop"]
            if user_id in self._sessions:
                self._schedule_end(item)
            return True

    def active_user(self, user_id):
        self.refresh(user_id)
        return user_id in self._sessions

    def public(self, user_id):
        self.refresh(user_id)
        item = self._sessions.get(user_id)
        paused = False
        if not item:
            item = self._paused.get(user_id)
            paused = item is not None
        if not item:
            return None
        position, elapsed, _reason = self._position(item) if not paused else (None, item["resume_elapsed"], None)
        track = position[1] if position else item["current_track"]
        offset = position[2] if position else item.get("track_offset", 0)
        remaining = None if item["indefinite"] else max(0, item["limit_seconds"] - elapsed)
        return {"user_id": user_id, "callsign": item["callsign"],
                "frequency": format_frequency(item["frequency"]),
                "frequency_khz": item["frequency"], "playlist_id": item["playlist_id"],
                "playlist_name": item["playlist_name"], "track_name": track["name"],
                "track_title": track.get("title") or Path(track["name"]).stem,
                "track_artist": track.get("artist") or "",
                "audio_file_id": track["file_id"], "track_offset": round(offset, 3),
                "track_serial": item["track_serial"],
                "status": "paused" if paused else "playing", "remaining_seconds": remaining,
                "indefinite": item["indefinite"], "shuffle": item["shuffle"]}

    def all_public(self):
        self.refresh()
        user_ids = set(self._sessions) | set(self._paused)
        return {user_id: self.public(user_id) for user_id in user_ids}


def _store():
    return current_app.extensions["hosted_broadcasts"]


def host_required(view):
    """Re-check current permissions for every management operation."""
    @wraps(view)
    @login_required
    def wrapped(**kwargs):
        if not can_manage_broadcasts(g.user["id"]):
            abort(403)
        return view(**kwargs)
    return wrapped


def _audio_dir(user_id):
    path = Path(current_app.config["DATA_DIR"]) / "radio_audio" / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _valid_audio(payload, suffix):
    """Check a conservative browser-compatible format set by file signature."""
    if suffix == ".wav":
        return (len(payload) >= 44 and payload[:4] == b"RIFF" and
                payload[8:12] == b"WAVE" and b"fmt " in payload[:128] and
                b"data" in payload[:256])
    if suffix == ".ogg":
        return len(payload) >= 27 and payload.startswith(b"OggS\x00")
    if suffix == ".mp3":
        offset = 0
        if len(payload) >= 10 and payload.startswith(b"ID3"):
            size_bytes = payload[6:10]
            if any(value & 0x80 for value in size_bytes):
                return False
            offset = 10 + sum(value << shift for value, shift in zip(size_bytes, (21, 14, 7, 0)))
        if len(payload) < offset + 4:
            return False
        header = int.from_bytes(payload[offset:offset + 4], "big")
        return ((header >> 21) & 0x7FF) == 0x7FF and ((header >> 17) & 0x3) != 0 and \
            ((header >> 12) & 0xF) not in {0, 0xF} and ((header >> 10) & 0x3) != 0x3
    return False


def _audio_duration(payload, suffix):
    """Read enough container metadata for the server timeline; use a safe fallback."""
    if suffix == ".wav" and len(payload) >= 44:
        byte_rate = int.from_bytes(payload[28:32], "little")
        marker = payload.find(b"data", 12, 256)
        if byte_rate > 0 and marker >= 0 and marker + 8 <= len(payload):
            return max(.1, int.from_bytes(payload[marker + 4:marker + 8], "little") / byte_rate)
    if suffix == ".mp3":
        offset = 10 + sum(value << shift for value, shift in zip(payload[6:10], (21, 14, 7, 0))) \
            if payload.startswith(b"ID3") and len(payload) >= 10 else 0
        if len(payload) >= offset + 4:
            header = int.from_bytes(payload[offset:offset + 4], "big")
            version = (header >> 19) & 3
            bitrate_index = (header >> 12) & 15
            table = ([0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
                     if version == 3 else
                     [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160])
            if 0 < bitrate_index < 15:
                return max(.1, len(payload) * 8 / (table[bitrate_index] * 1000))
    return 180.0


def _clean_metadata(value):
    """Normalize embedded text for compact, safe listener display."""
    if not value:
        return ""
    value = " ".join(str(value).replace("\x00", " ").split())
    return value[:TRACK_METADATA_MAX_LENGTH]


def _decode_id3_text(payload):
    if not payload:
        return ""
    encoding = payload[0]
    raw = payload[1:]
    codecs = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    try:
        return _clean_metadata(raw.decode(codecs.get(encoding, "utf-8"), errors="replace"))
    except (LookupError, UnicodeError):
        return ""


def _audio_metadata(payload, suffix, filename):
    """Read common embedded title/artist tags without requiring an external service."""
    title = artist = ""
    if suffix == ".mp3" and payload.startswith(b"ID3") and len(payload) >= 10:
        version = payload[3]
        tag_size = sum(value << shift for value, shift in zip(payload[6:10], (21, 14, 7, 0)))
        offset, limit = 10, min(len(payload), 10 + tag_size)
        while offset + (6 if version == 2 else 10) <= limit:
            if version == 2:
                frame_id = payload[offset:offset + 3].decode("latin-1")
                size = int.from_bytes(payload[offset + 3:offset + 6], "big")
                header_size = 6
            else:
                frame_id = payload[offset:offset + 4].decode("latin-1")
                size_bytes = payload[offset + 4:offset + 8]
                size = (sum(value << shift for value, shift in zip(size_bytes, (21, 14, 7, 0)))
                        if version == 4 else int.from_bytes(size_bytes, "big"))
                header_size = 10
            if not frame_id.strip("\x00") or size <= 0 or offset + header_size + size > limit:
                break
            value = _decode_id3_text(payload[offset + header_size:offset + header_size + size])
            if frame_id in {"TIT2", "TT2"}:
                title = value
            elif frame_id in {"TPE1", "TP1"}:
                artist = value
            offset += header_size + size
    elif suffix == ".ogg":
        # Vorbis comments are UTF-8 length-prefixed fields. Searching within the
        # validated container also tolerates Opus/Vorbis packet layout differences.
        upper = payload.upper()
        for key, target in ((b"TITLE=", "title"), (b"ARTIST=", "artist")):
            marker = upper.find(key)
            if marker >= 0:
                field_size = int.from_bytes(payload[marker - 4:marker], "little") if marker >= 4 else 0
                end = marker + field_size
                if field_size < len(key) or end > len(payload):
                    end = payload.find(b"\x00", marker, min(len(payload), marker + 1024))
                    end = end if end >= 0 else min(len(payload), marker + 1024)
                raw = payload[marker + len(key):end]
                value = _clean_metadata(raw.decode("utf-8", errors="replace"))
                if target == "title":
                    title = value
                else:
                    artist = value
    elif suffix == ".wav":
        for chunk, target in ((b"INAM", "title"), (b"IART", "artist")):
            marker = payload.find(chunk, 12)
            if marker >= 0 and marker + 8 <= len(payload):
                size = struct.unpack_from("<I", payload, marker + 4)[0]
                value = _clean_metadata(payload[marker + 8:marker + 8 + size].decode("latin-1", errors="replace"))
                if target == "title":
                    title = value
                else:
                    artist = value
    return title or _clean_metadata(Path(filename).stem) or "Untitled track", artist


def _quota(database, user_id):
    row = database.execute(
        "SELECT u.radio_quota_mb,s.radio_default_quota_mb FROM users u CROSS JOIN station s "
        "WHERE u.id=? AND s.id=1", (user_id,),
    ).fetchone()
    return row["radio_quota_mb"] or row["radio_default_quota_mb"]


def _redirect(playlist_id=None):
    anchor = f"playlist-{playlist_id}" if playlist_id else "audio-library"
    return redirect(url_for("broadcast.manage") + f"#{anchor}")


def _owned_playlist(database, playlist_id):
    item = database.execute(
        "SELECT * FROM radio_playlists WHERE id=? AND owner_id=?",
        (playlist_id, g.user["id"]),
    ).fetchone()
    if item is None:
        abort(404)
    return item


def _available_tracks(database, playlist_id):
    rows = database.execute(
        "SELECT t.id entry_id,t.position,t.display_name,f.id file_id,f.owner_id,"
        "f.stored_name,f.duration_seconds,f.title,f.artist FROM radio_playlist_tracks t "
        "JOIN radio_audio_files f ON f.id=t.audio_file_id "
        "WHERE t.playlist_id=? ORDER BY t.position,t.id", (playlist_id,),
    ).fetchall()
    result = []
    for row in rows:
        path = Path(current_app.config["DATA_DIR"]) / "radio_audio" / str(row["owner_id"]) / row["stored_name"]
        if path.is_file():
            result.append({
                "entry_id": row["entry_id"], "position": row["position"],
                "file_id": row["file_id"], "name": row["display_name"],
                "title": row["title"] or Path(row["display_name"]).stem or "Untitled track",
                "artist": row["artist"] or "",
                "duration": max(.1, float(row["duration_seconds"])),
            })
    return result


def _sync_playlist(database, playlist_id):
    playlist = database.execute("SELECT * FROM radio_playlists WHERE id=?", (playlist_id,)).fetchone()
    if playlist:
        _store().sync_playlist(
            playlist["owner_id"], playlist["id"], playlist["name"],
            _available_tracks(database, playlist_id), playlist["loop_enabled"],
            playlist["shuffle_enabled"],
        )


@bp.get("")
@host_required
def manage():
    database = get_db()
    assignment = database.execute(
        "SELECT * FROM radio_broadcast_assignments WHERE user_id=?", (g.user["id"],),
    ).fetchone()
    audio_files = database.execute(
        "SELECT * FROM radio_audio_files WHERE owner_id=? ORDER BY created_at DESC,id DESC",
        (g.user["id"],),
    ).fetchall()
    playlists = database.execute(
        "SELECT * FROM radio_playlists WHERE owner_id=? ORDER BY name,id",
        (g.user["id"],),
    ).fetchall()
    tracks = database.execute(
        "SELECT t.* FROM radio_playlist_tracks t JOIN radio_playlists p ON p.id=t.playlist_id "
        "WHERE p.owner_id=? ORDER BY t.playlist_id,t.position,t.id", (g.user["id"],),
    ).fetchall()
    tracks_by_playlist = {
        playlist["id"]: [track for track in tracks if track["playlist_id"] == playlist["id"]]
        for playlist in playlists
    }
    used = sum(item["size_bytes"] for item in audio_files)
    quota_mb = _quota(database, g.user["id"])
    return render_template(
        "radio/manage.html", assignment=assignment, audio_files=audio_files, playlists=playlists,
        tracks_by_playlist=tracks_by_playlist, used_bytes=used,
        quota_bytes=quota_mb * MIB, available_bytes=max(0, quota_mb * MIB - used),
        quota_mb=quota_mb,
        usage_percent=min(100, round(used * 100 / (quota_mb * MIB), 1)),
        accepted_formats=", ".join(suffix[1:].upper() for suffix in AUDIO_FORMATS),
        max_batch_files=MAX_AUDIO_BATCH_FILES, format_frequency=format_frequency,
    )


@bp.post("/identity")
@host_required
def update_identity():
    """Update only the current host's existing registered assignment."""
    validate_csrf()
    name = request.form.get("broadcast_name", "").strip()
    message = request.form.get("broadcast_message", "").strip()
    if len(name) > BROADCAST_NAME_MAX_LENGTH:
        flash(f"Broadcast names cannot exceed {BROADCAST_NAME_MAX_LENGTH} characters.", "error")
    elif len(message) > BROADCAST_MESSAGE_MAX_LENGTH:
        flash(f"Broadcast messages cannot exceed {BROADCAST_MESSAGE_MAX_LENGTH} characters.", "error")
    elif any(ord(character) < 32 and character not in "\t\n\r" for character in name + message):
        flash("Broadcast identity cannot contain control characters.", "error")
    elif "\n" in name or "\r" in name:
        flash("Broadcast names must fit on one line.", "error")
    else:
        database = get_db()
        result = database.execute(
            "UPDATE radio_broadcast_assignments SET broadcast_name=?,broadcast_message=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (name, message, g.user["id"]),
        )
        if not result.rowcount:
            database.rollback()
            flash("A registered broadcast frequency is required before setting an identity.", "error")
        else:
            database.commit()
            flash("Broadcast identity saved.", "success")
    return redirect(url_for("broadcast.manage") + "#broadcast-identity")


@bp.post("/audio")
@host_required
def upload_audio():
    validate_csrf()
    uploads = [item for item in request.files.getlist("file") if item and item.filename]
    if not uploads:
        flash("Select one or more audio files to upload.", "error")
        return _redirect()
    if len(uploads) > MAX_AUDIO_BATCH_FILES:
        flash(f"Select no more than {MAX_AUDIO_BATCH_FILES} audio files per batch.", "error")
        return _redirect()
    database = get_db()
    quota_bytes = _quota(database, g.user["id"]) * MIB
    used = database.execute(
        "SELECT COALESCE(SUM(size_bytes),0) FROM radio_audio_files WHERE owner_id=?",
        (g.user["id"],),
    ).fetchone()[0]
    remaining = max(0, quota_bytes - used)
    batch = []
    batch_size = 0
    for uploaded in uploads:
        filename = Path(uploaded.filename).name.strip()
        suffix = Path(filename).suffix.lower()
        if not filename or filename in {".", ".."} or len(filename) > 200 or "\x00" in filename:
            flash("One of the selected audio file names is invalid or too long.", "error")
            return _redirect()
        if suffix not in AUDIO_FORMATS:
            flash(f"{filename}: audio files must use MP3, OGG, or WAV format.", "error")
            return _redirect()
        payload = uploaded.read(MAX_AUDIO_FILE_BYTES + 1)
        if not payload:
            flash(f"{filename}: audio file is empty.", "error")
            return _redirect()
        if len(payload) > MAX_AUDIO_FILE_BYTES:
            flash(f"{filename}: audio files cannot exceed 64 MB each.", "error")
            return _redirect()
        if not _valid_audio(payload, suffix):
            flash(f"{filename}: the file does not contain valid supported audio data.", "error")
            return _redirect()
        batch_size += len(payload)
        if batch_size > remaining:
            flash("The selected audio batch would exceed your radio library storage quota.", "error")
            return _redirect()
        batch.append({
            "filename": filename, "suffix": suffix, "payload": payload,
            "stored_name": f"{secrets.token_hex(20)}{suffix}",
            "duration": _audio_duration(payload, suffix),
            "metadata": _audio_metadata(payload, suffix, filename),
        })

    destinations = []
    try:
        # Serialize the all-or-nothing quota reservation with concurrent uploads.
        database.execute("BEGIN IMMEDIATE")
        current_used = database.execute(
            "SELECT COALESCE(SUM(size_bytes),0) FROM radio_audio_files WHERE owner_id=?",
            (g.user["id"],),
        ).fetchone()[0]
        if current_used + batch_size > _quota(database, g.user["id"]) * MIB:
            database.rollback()
            flash("The selected audio batch would exceed your radio library storage quota.", "error")
            return _redirect()
        directory = _audio_dir(g.user["id"])
        for item in batch:
            destination = directory / item["stored_name"]
            destination.write_bytes(item["payload"])
            destinations.append(destination)
            database.execute(
                "INSERT INTO radio_audio_files(owner_id,display_name,stored_name,media_type,size_bytes,duration_seconds,title,artist) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (g.user["id"], item["filename"], item["stored_name"],
                 AUDIO_FORMATS[item["suffix"]], len(item["payload"]), item["duration"],
                 item["metadata"][0], item["metadata"][1]),
            )
        database.commit()
    except Exception:
        database.rollback()
        for destination in destinations:
            destination.unlink(missing_ok=True)
        raise
    label = "file" if len(batch) == 1 else "files"
    flash(f"{len(batch)} audio {label} added to your broadcast library.", "success")
    return _redirect()


@bp.post("/audio/<int:file_id>/delete")
@host_required
def delete_audio(file_id):
    validate_csrf()
    database = get_db()
    item = database.execute(
        "SELECT * FROM radio_audio_files WHERE id=? AND owner_id=?",
        (file_id, g.user["id"]),
    ).fetchone()
    if item is None:
        abort(404)
    playlist_ids = [row["playlist_id"] for row in database.execute(
        "SELECT DISTINCT playlist_id FROM radio_playlist_tracks WHERE audio_file_id=?", (file_id,),
    )]
    try:
        (_audio_dir(g.user["id"]) / item["stored_name"]).unlink(missing_ok=True)
    except PermissionError:
        flash("Audio file is currently in use and could not be removed.", "error")
        return _redirect()
    database.execute("DELETE FROM radio_audio_files WHERE id=?", (file_id,))
    database.commit()
    for playlist_id in playlist_ids:
        _sync_playlist(database, playlist_id)
    flash("Audio file removed. Playlist references are marked missing.", "success")
    return _redirect()


@bp.post("/playlists")
@host_required
def create_playlist():
    validate_csrf()
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 80:
        flash("Playlist names must be between 1 and 80 characters.", "error")
    else:
        try:
            get_db().execute(
                "INSERT INTO radio_playlists(owner_id,name) VALUES(?,?)",
                (g.user["id"], name),
            )
            get_db().commit()
            flash("Playlist created.", "success")
        except sqlite3.IntegrityError:
            flash("You already have a playlist with that name.", "error")
    return _redirect()


@bp.post("/playlists/<int:playlist_id>")
@host_required
def update_playlist(playlist_id):
    validate_csrf()
    database = get_db()
    _owned_playlist(database, playlist_id)
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 80:
        flash("Playlist names must be between 1 and 80 characters.", "error")
    else:
        try:
            database.execute(
                "UPDATE radio_playlists SET name=?,loop_enabled=?,shuffle_enabled=? WHERE id=?",
                (name, request.form.get("loop_enabled") == "on",
                 request.form.get("shuffle_enabled") == "on", playlist_id),
            )
            database.commit()
            _sync_playlist(database, playlist_id)
            flash("Playlist settings saved.", "success")
        except sqlite3.IntegrityError:
            flash("You already have a playlist with that name.", "error")
    return _redirect(playlist_id)


@bp.post("/playlists/<int:playlist_id>/delete")
@host_required
def delete_playlist(playlist_id):
    validate_csrf()
    database = get_db()
    playlist = _owned_playlist(database, playlist_id)
    database.execute("DELETE FROM radio_playlists WHERE id=?", (playlist_id,))
    database.commit()
    _store().sync_playlist(playlist["owner_id"], playlist_id, playlist["name"], [], False, False)
    flash("Playlist deleted.", "success")
    return _redirect()


@bp.post("/playlists/<int:playlist_id>/tracks")
@host_required
def add_track(playlist_id):
    validate_csrf()
    database = get_db()
    _owned_playlist(database, playlist_id)
    audio_id = request.form.get("audio_file_id", type=int)
    audio = database.execute(
        "SELECT id,display_name FROM radio_audio_files WHERE id=? AND owner_id=?",
        (audio_id, g.user["id"]),
    ).fetchone()
    if audio is None:
        abort(404)
    position = database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM radio_playlist_tracks WHERE playlist_id=?",
        (playlist_id,),
    ).fetchone()[0]
    database.execute(
        "INSERT INTO radio_playlist_tracks(playlist_id,audio_file_id,display_name,position) "
        "VALUES(?,?,?,?)", (playlist_id, audio["id"], audio["display_name"], position),
    )
    database.commit()
    _sync_playlist(database, playlist_id)
    flash("Track added to playlist.", "success")
    return _redirect(playlist_id)


@bp.post("/playlists/<int:playlist_id>/tracks/<int:track_id>/move")
@host_required
def move_track(playlist_id, track_id):
    validate_csrf()
    database = get_db()
    _owned_playlist(database, playlist_id)
    tracks = database.execute(
        "SELECT id FROM radio_playlist_tracks WHERE playlist_id=? ORDER BY position,id",
        (playlist_id,),
    ).fetchall()
    ids = [row["id"] for row in tracks]
    if track_id not in ids:
        abort(404)
    index = ids.index(track_id)
    target = index - 1 if request.form.get("direction") == "up" else index + 1
    if 0 <= target < len(ids):
        ids[index], ids[target] = ids[target], ids[index]
        database.executemany(
            "UPDATE radio_playlist_tracks SET position=? WHERE id=?",
            [(position, item_id) for position, item_id in enumerate(ids)],
        )
        database.commit()
        _sync_playlist(database, playlist_id)
    return _redirect(playlist_id)


@bp.post("/playlists/<int:playlist_id>/tracks/<int:track_id>/delete")
@host_required
def remove_track(playlist_id, track_id):
    validate_csrf()
    database = get_db()
    _owned_playlist(database, playlist_id)
    result = database.execute(
        "DELETE FROM radio_playlist_tracks WHERE id=? AND playlist_id=?",
        (track_id, playlist_id),
    )
    if not result.rowcount:
        abort(404)
    tracks = database.execute(
        "SELECT id FROM radio_playlist_tracks WHERE playlist_id=? ORDER BY position,id",
        (playlist_id,),
    ).fetchall()
    database.executemany(
        "UPDATE radio_playlist_tracks SET position=? WHERE id=?",
        [(position, row["id"]) for position, row in enumerate(tracks)],
    )
    database.commit()
    _sync_playlist(database, playlist_id)
    flash("Track removed from playlist.", "success")
    return _redirect(playlist_id)


@bp.get("/status")
@login_required
def status():
    database = get_db()
    assignment = database.execute(
        "SELECT frequency_khz,broadcast_name,broadcast_message "
        "FROM radio_broadcast_assignments WHERE user_id=?",
        (g.user["id"],),
    ).fetchone()
    playlists = database.execute(
        "SELECT p.id,p.name,p.loop_enabled,p.shuffle_enabled,COUNT(t.id) track_count "
        "FROM radio_playlists p LEFT JOIN radio_playlist_tracks t ON t.playlist_id=p.id "
        "WHERE p.owner_id=? GROUP BY p.id ORDER BY p.name", (g.user["id"],),
    ).fetchall()
    directory = database.execute(
        "SELECT a.user_id,a.frequency_khz,a.broadcast_name,a.broadcast_message,"
        "u.callsign,u.username "
        "FROM radio_broadcast_assignments a "
        "JOIN users u ON u.id=a.user_id ORDER BY a.frequency_khz"
    ).fetchall()
    active = _store().all_public()
    return jsonify(
        assignment=format_frequency(assignment["frequency_khz"]) if assignment else None,
        assignment_khz=assignment["frequency_khz"] if assignment else None,
        identity=({"name": assignment["broadcast_name"] or
                           f"{g.user['username']} - [{g.user['callsign']}]",
                   "configured_name": assignment["broadcast_name"],
                   "message": assignment["broadcast_message"]} if assignment else None),
        session=_store().public(g.user["id"]),
        playlists=[dict(row) for row in playlists],
        directory=[{"user_id": row["user_id"], "callsign": row["callsign"],
                    "username": row["username"],
                    "name": row["broadcast_name"] or
                            f"{row['username']} - [{row['callsign']}]",
                    "message": row["broadcast_message"],
                    "frequency": format_frequency(row["frequency_khz"]),
                    "broadcasting": row["user_id"] in active and active[row["user_id"]]["status"] == "playing",
                    "now_playing": (active.get(row["user_id"], {}).get("track_title") or
                                    active.get(row["user_id"], {}).get("track_name"))}
                   for row in directory
                   if effective_access(g.user["id"], row["frequency_khz"]) != "DENIED"],
    )


@bp.post("/start")
@host_required
def start_broadcast():
    validate_csrf()
    database = get_db()
    playlist_id = request.form.get("playlist_id", type=int)
    playlist = database.execute(
        "SELECT * FROM radio_playlists WHERE id=? AND owner_id=?", (playlist_id, g.user["id"]),
    ).fetchone()
    if not playlist:
        return jsonify(error="Select one of your playlists."), 404
    assignment = database.execute(
        "SELECT frequency_khz FROM radio_broadcast_assignments WHERE user_id=?", (g.user["id"],),
    ).fetchone()
    if assignment:
        frequency = assignment["frequency_khz"]
    else:
        frequency = parse_frequency(request.form.get("frequency"))
        band = find_band(frequency)
        if not band or not band["allow_open_broadcasts"]:
            return jsonify(error="No broadcast frequency is assigned. Tune to a band that allows open broadcasts or contact a sysop."), 403
    if not transmission_authorized(g.user["id"], frequency, "HOSTED_BROADCAST"):
        return jsonify(error="Hosted broadcasting is not authorized on this frequency."), 403
    tracks = _available_tracks(database, playlist_id)
    if not tracks:
        return jsonify(error="This playlist has no available audio tracks."), 400
    station = database.execute(
        "SELECT radio_broadcast_limit_minutes FROM station WHERE id=1"
    ).fetchone()
    try:
        result = _store().start(
            g.user["id"], g.user["callsign"], frequency, playlist["id"], playlist["name"],
            tracks, playlist["loop_enabled"], station["radio_broadcast_limit_minutes"] * 60,
            playlist["shuffle_enabled"],
        )
    except RuntimeError as problem:
        return jsonify(error=str(problem)), 409
    return jsonify(session=result), 201


@bp.post("/stop")
@login_required
def stop_broadcast():
    validate_csrf()
    if not _store().stop(g.user["id"], "operator_stopped"):
        return jsonify(error="No hosted broadcast is active."), 409
    return jsonify(stopped=True)


def _tuned_broadcast(file_id, track_serial):
    tuned = current_app.extensions["radio"].receiver_frequency(session["connection_id"])
    if tuned is None or effective_access(g.user["id"], tuned) == "DENIED":
        return None
    return next((item for item in _store().all_public().values()
                 if item and item["status"] == "playing" and
                 item["frequency_khz"] == tuned and item["audio_file_id"] == file_id and
                 item["track_serial"] == track_serial), None)


@bp.post("/advance")
@login_required
def advance_broadcast():
    """Let an in-sync listener report actual media completion or decode failure."""
    validate_csrf()
    file_id = request.form.get("file_id", type=int)
    track_serial = request.form.get("track_serial", type=int)
    match = _tuned_broadcast(file_id, track_serial)
    if not match:
        return jsonify(advanced=False), 200
    advanced = _store().advance(match["user_id"], file_id, track_serial)
    return jsonify(advanced=advanced)


@bp.post("/timing")
@login_required
def broadcast_timing():
    """Use browser-decoded metadata to correct approximate container durations."""
    validate_csrf()
    file_id = request.form.get("file_id", type=int)
    track_serial = request.form.get("track_serial", type=int)
    duration = request.form.get("duration", type=float)
    if duration is None or not .1 <= duration <= 24 * 60 * 60:
        return jsonify(error="Invalid audio duration."), 400
    match = _tuned_broadcast(file_id, track_serial)
    if not match:
        return jsonify(updated=False), 200
    updated = _store().correct_duration(match["user_id"], file_id, track_serial, duration)
    if updated:
        get_db().execute(
            "UPDATE radio_audio_files SET duration_seconds=? WHERE id=?", (duration, file_id),
        )
        get_db().commit()
    return jsonify(updated=updated)


@bp.get("/audio/<int:file_id>")
@login_required
def broadcast_audio(file_id):
    sessions = _store().all_public()
    item = next((value for value in sessions.values()
                 if value and value["status"] == "playing" and value["audio_file_id"] == file_id), None)
    if not item or effective_access(g.user["id"], item["frequency_khz"]) == "DENIED":
        abort(403)
    tuned = current_app.extensions["radio"].receiver_frequency(session["connection_id"])
    if tuned != item["frequency_khz"]:
        abort(403)
    row = get_db().execute("SELECT * FROM radio_audio_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        abort(404)
    path = Path(current_app.config["DATA_DIR"]) / "radio_audio" / str(row["owner_id"]) / row["stored_name"]
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype=row["media_type"], conditional=True)
