"""Private audio libraries and playlist preparation for broadcast hosts."""
import secrets
import sqlite3
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


class HostedBroadcastStore:
    """Server-owned playlist clocks; browsers are synchronized renderers only."""
    def __init__(self, radio_store):
        self.radio = radio_store
        self._lock = RLock()
        self._sessions = {}
        self._paused = {}

    def _position(self, item, now=None):
        elapsed = item.get("resume_elapsed", 0) + max(0, (now or monotonic()) - item["clock_started"])
        if not item["indefinite"] and elapsed >= item["limit_seconds"]:
            return None, elapsed, "duration_limit"
        total = sum(track["duration"] for track in item["tracks"])
        if total <= 0:
            return None, elapsed, "playlist_complete"
        playlist_time = elapsed % total if item["loop"] else elapsed
        if not item["loop"] and playlist_time >= total:
            return None, elapsed, "playlist_complete"
        cursor = playlist_time
        for index, track in enumerate(item["tracks"]):
            if cursor < track["duration"] or index == len(item["tracks"]) - 1:
                return (index, track, cursor), elapsed, None
            cursor -= track["duration"]
        return None, elapsed, "playlist_complete"

    def _schedule_end(self, item):
        old = item.pop("timer", None)
        if old:
            old.cancel()
        total = sum(track["duration"] for track in item["tracks"])
        elapsed = item.get("resume_elapsed", 0)
        deadlines = []
        if not item["indefinite"]:
            deadlines.append(item["limit_seconds"] - elapsed)
        if not item["loop"]:
            deadlines.append(total - elapsed)
        if not deadlines:
            return
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
            _position, _elapsed, reason = self._position(item)
            self.radio.end_hosted(user_id, reason or "duration_limit")
            self._sessions.pop(user_id, None)

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
                                         track_name=track["name"], audio_file_id=track["file_id"],
                                         track_offset=round(offset, 3))

    def start(self, user_id, callsign, frequency, playlist_id, playlist_name,
              tracks, loop, limit_seconds):
        with self._lock:
            self.refresh(user_id)
            if user_id in self._sessions:
                raise RuntimeError("You already have an active hosted broadcast.")
            item = {"user_id": user_id, "callsign": callsign, "frequency": frequency,
                    "playlist_id": playlist_id, "playlist_name": playlist_name,
                    "tracks": tracks, "loop": bool(loop), "limit_seconds": limit_seconds,
                    "indefinite": False, "base_loop": bool(loop),
                    "clock_started": monotonic(), "resume_elapsed": 0}
            first = tracks[0]
            transmission_id, _event = self.radio.start_hosted(
                user_id, frequency, callsign, playlist_name=playlist_name,
                track_name=first["name"], audio_file_id=first["file_id"], track_offset=0,
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
            first = item["tracks"][0]
            transmission_id, _event = self.radio.start_hosted(
                user_id, item["frequency"], item["callsign"],
                playlist_name=item["playlist_name"], track_name=first["name"],
                audio_file_id=first["file_id"], track_offset=0,
            )
            item["transmission_id"] = transmission_id
            item["clock_started"] = monotonic()
            self._paused.pop(user_id)
            self._sessions[user_id] = item
            self.refresh(user_id)
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
        track = position[1] if position else item["tracks"][min(item.get("track_index", 0), len(item["tracks"])-1)]
        offset = position[2] if position else 0
        remaining = None if item["indefinite"] else max(0, item["limit_seconds"] - elapsed)
        return {"user_id": user_id, "callsign": item["callsign"],
                "frequency": format_frequency(item["frequency"]),
                "frequency_khz": item["frequency"], "playlist_id": item["playlist_id"],
                "playlist_name": item["playlist_name"], "track_name": track["name"],
                "audio_file_id": track["file_id"], "track_offset": round(offset, 3),
                "status": "paused" if paused else "playing", "remaining_seconds": remaining,
                "indefinite": item["indefinite"]}

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


@bp.get("")
@host_required
def manage():
    database = get_db()
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
        "radio/manage.html", audio_files=audio_files, playlists=playlists,
        tracks_by_playlist=tracks_by_playlist, used_bytes=used,
        quota_bytes=quota_mb * MIB, available_bytes=max(0, quota_mb * MIB - used),
        quota_mb=quota_mb,
        usage_percent=min(100, round(used * 100 / (quota_mb * MIB), 1)),
        accepted_formats=", ".join(suffix[1:].upper() for suffix in AUDIO_FORMATS),
        max_batch_files=MAX_AUDIO_BATCH_FILES,
    )


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
                "INSERT INTO radio_audio_files(owner_id,display_name,stored_name,media_type,size_bytes,duration_seconds) "
                "VALUES(?,?,?,?,?,?)",
                (g.user["id"], item["filename"], item["stored_name"],
                 AUDIO_FORMATS[item["suffix"]], len(item["payload"]), item["duration"]),
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
    try:
        (_audio_dir(g.user["id"]) / item["stored_name"]).unlink(missing_ok=True)
    except PermissionError:
        flash("Audio file is currently in use and could not be removed.", "error")
        return _redirect()
    database.execute("DELETE FROM radio_audio_files WHERE id=?", (file_id,))
    database.commit()
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
                "UPDATE radio_playlists SET name=?,loop_enabled=? WHERE id=?",
                (name, request.form.get("loop_enabled") == "on", playlist_id),
            )
            database.commit()
            flash("Playlist settings saved.", "success")
        except sqlite3.IntegrityError:
            flash("You already have a playlist with that name.", "error")
    return _redirect(playlist_id)


@bp.post("/playlists/<int:playlist_id>/delete")
@host_required
def delete_playlist(playlist_id):
    validate_csrf()
    database = get_db()
    _owned_playlist(database, playlist_id)
    database.execute("DELETE FROM radio_playlists WHERE id=?", (playlist_id,))
    database.commit()
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
    flash("Track removed from playlist.", "success")
    return _redirect(playlist_id)


@bp.get("/status")
@login_required
def status():
    database = get_db()
    assignment = database.execute(
        "SELECT frequency_khz FROM radio_broadcast_assignments WHERE user_id=?",
        (g.user["id"],),
    ).fetchone()
    playlists = database.execute(
        "SELECT p.id,p.name,p.loop_enabled,COUNT(t.id) track_count "
        "FROM radio_playlists p LEFT JOIN radio_playlist_tracks t ON t.playlist_id=p.id "
        "WHERE p.owner_id=? GROUP BY p.id ORDER BY p.name", (g.user["id"],),
    ).fetchall()
    directory = database.execute(
        "SELECT a.user_id,a.frequency_khz,u.callsign FROM radio_broadcast_assignments a "
        "JOIN users u ON u.id=a.user_id ORDER BY a.frequency_khz"
    ).fetchall()
    active = _store().all_public()
    return jsonify(
        assignment=format_frequency(assignment["frequency_khz"]) if assignment else None,
        assignment_khz=assignment["frequency_khz"] if assignment else None,
        session=_store().public(g.user["id"]),
        playlists=[dict(row) for row in playlists],
        directory=[{"user_id": row["user_id"], "callsign": row["callsign"],
                    "frequency": format_frequency(row["frequency_khz"]),
                    "broadcasting": row["user_id"] in active and active[row["user_id"]]["status"] == "playing",
                    "now_playing": active.get(row["user_id"], {}).get("track_name")}
                   for row in directory],
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
    rows = database.execute(
        "SELECT f.id file_id,t.display_name,f.duration_seconds FROM radio_playlist_tracks t "
        "JOIN radio_audio_files f ON f.id=t.audio_file_id "
        "WHERE t.playlist_id=? ORDER BY t.position,t.id", (playlist_id,),
    ).fetchall()
    if not rows:
        return jsonify(error="This playlist has no available audio tracks."), 400
    tracks = [{"file_id": row["file_id"], "name": row["display_name"],
               "duration": float(row["duration_seconds"])} for row in rows]
    station = database.execute(
        "SELECT radio_broadcast_limit_minutes FROM station WHERE id=1"
    ).fetchone()
    try:
        result = _store().start(
            g.user["id"], g.user["callsign"], frequency, playlist["id"], playlist["name"],
            tracks, playlist["loop_enabled"], station["radio_broadcast_limit_minutes"] * 60,
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
