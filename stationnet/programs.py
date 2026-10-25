"""Unified mixed-mode broadcast programs, automation, and live controller leases."""
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock, Timer
from time import monotonic

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, send_file, session, url_for)

from .auth import admin_required, login_required
from .db import get_db
from .radio import (can_manage_broadcasts, effective_access, find_band,
                    format_frequency, parse_frequency, transmission_authorized,
                    _connection_id)
from .security import validate_csrf

bp = Blueprint("programs", __name__, url_prefix="/radio/manage")
ACTION_TYPES = {"playlist", "resume_playlist", "music", "announcement", "jingle",
                "morse", "sstv", "live"}
CONTENT_TYPES = {"music", "announcement", "jingle", "morse", "sstv"}


def _host_required(view):
    from functools import wraps
    @wraps(view)
    @login_required
    def wrapped(**kwargs):
        if not can_manage_broadcasts(g.user["id"]):
            abort(403)
        return view(**kwargs)
    return wrapped


def _owned_program(database, program_id, owner_id=None):
    row = database.execute("SELECT * FROM radio_programs WHERE id=? AND owner_id=?",
                           (program_id, owner_id or g.user["id"])).fetchone()
    if not row:
        abort(404)
    return row


def _audio_track(database, file_id, action_type="music", label="", content_id=None):
    row = database.execute("SELECT * FROM radio_audio_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        return None
    path = Path(current_app.config["DATA_DIR"]) / "radio_audio" / str(row["owner_id"]) / row["stored_name"]
    if not path.is_file():
        return None
    return {"file_id": row["id"], "name": label or row["display_name"],
            "title": label or row["title"] or Path(row["display_name"]).stem,
            "artist": row["artist"], "duration": max(.1, float(row["duration_seconds"])),
            "media_type": row["media_type"],
            "action_type": action_type, "content_id": content_id}


def compile_program(database, program_id):
    """Flatten persisted actions into the existing server-owned playback timeline."""
    program = database.execute("SELECT * FROM radio_programs WHERE id=?", (program_id,)).fetchone()
    if not program:
        raise ValueError("Program not found.")
    actions = database.execute(
        "SELECT a.*,c.kind content_kind,c.name content_name,c.audio_file_id,c.text_content content_text "
        "FROM radio_program_actions a LEFT JOIN radio_broadcast_content c ON c.id=a.content_id "
        "WHERE a.program_id=? ORDER BY a.position,a.id", (program_id,)).fetchall()
    result, last_playlist = [], None
    serial = 0
    for action in actions:
        kind = action["action_type"]
        repetitions = max(1, action["repeat_count"])
        for _ in range(repetitions):
            if kind in {"playlist", "resume_playlist"}:
                playlist_id = action["playlist_id"] or last_playlist
                if not playlist_id:
                    continue
                last_playlist = playlist_id
                rows = database.execute(
                    "SELECT t.*,f.duration_seconds,f.title,f.artist,f.owner_id,f.stored_name,f.media_type "
                    "FROM radio_playlist_tracks t JOIN radio_audio_files f ON f.id=t.audio_file_id "
                    "WHERE t.playlist_id=? ORDER BY t.position,t.id", (playlist_id,)).fetchall()
                for row in rows:
                    path = Path(current_app.config["DATA_DIR"]) / "radio_audio" / str(row["owner_id"]) / row["stored_name"]
                    if path.is_file():
                        result.append({"file_id": row["audio_file_id"], "name": row["display_name"],
                                       "title": row["title"] or Path(row["display_name"]).stem,
                                       "artist": row["artist"], "duration": max(.1, float(row["duration_seconds"])),
                                       "media_type": row["media_type"],
                                       "action_type": "music", "source_action_id": action["id"]})
                continue
            if kind in {"music", "announcement", "jingle"}:
                file_id = action["content_id"] and database.execute(
                    "SELECT audio_file_id FROM radio_broadcast_content WHERE id=?", (action["content_id"],)
                ).fetchone()
                track = _audio_track(database, file_id[0] if file_id else None, kind,
                                     action["label"] or action["content_name"] or "", action["content_id"])
                if track:
                    result.append(track)
                continue
            text = action["text_content"] or action["content_text"] or ""
            duration = float(action["duration_seconds"] or (max(2, len(text) * .12) if kind == "morse" else 8))
            if kind == "live" and duration <= 0:
                duration = 300
            result.append({"file_id": None, "name": action["label"] or action["content_name"] or kind.upper(),
                           "title": action["label"] or action["content_name"] or kind.upper(), "artist": "",
                           "duration": max(.1, duration), "action_type": kind, "text": text,
                           "content_id": action["content_id"]})
    for index, item in enumerate(result):
        serial += 1
        item.update(entry_id=-(program_id * 100000 + serial), position=index)
    return program, result


def _validate_program_for_frequency(database, program_id, frequency):
    band = find_band(frequency)
    if not band or not band["supports_hosted_broadcasts"]:
        return "This frequency does not support hosted broadcasts."
    kinds = {row[0] for row in database.execute(
        "SELECT DISTINCT action_type FROM radio_program_actions WHERE program_id=?", (program_id,))}
    if "morse" in kinds and not band["supports_cw"]:
        return "This program contains Morse actions, but the frequency does not support CW."
    if "sstv" in kinds and not band["supports_sstv"]:
        return "This program contains SSTV actions, but the frequency does not support SSTV."
    if "live" in kinds and not band["supports_live_voice"]:
        return "This program contains live segments, but the frequency does not support live voice."
    return None


class BroadcastCoordinator:
    """Own controller leases and a process-local timer backed by persisted due dates."""
    def __init__(self, app, store):
        self.app, self.store = app, store
        self._lock, self._leases, self._timer = RLock(), {}, None
        self._observed = {}
        self._event_counts = {}
        self._closed = False
        self._schedule(1)

    def _schedule(self, delay=15):
        if self._closed:
            return
        self._timer = Timer(delay, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self):
        try:
            with self.app.app_context():
                self.expire_leases()
                database = get_db()
                now = datetime.now(timezone.utc)
                due = database.execute(
                    "SELECT * FROM radio_automation_rules WHERE enabled=1 AND next_run_at IS NOT NULL "
                    "AND next_run_at<=? ORDER BY next_run_at,id", (now.isoformat(),)).fetchall()
                for rule in due:
                    self.start_rule(database, rule)
                current = self.store.all_public()
                for key, item in current.items():
                    if not item or item.get("status") != "playing":
                        continue
                    previous = self._observed.get(key)
                    marker = item.get("track_serial")
                    if previous and previous[0] != marker:
                        self.notify("track_interval", item.get("frequency_khz"))
                    self._observed[key] = (marker, item.get("frequency_khz"))
                for key, (_marker, frequency) in list(self._observed.items()):
                    if key not in current:
                        self._observed.pop(key, None)
                        self.notify("playlist_complete", frequency)
        finally:
            self._schedule()

    def start_rule(self, database, rule):
        key = f"rule:{rule['id']}"
        try:
            program, tracks = compile_program(database, rule["program_id"])
            if (_validate_program_for_frequency(database, rule["program_id"], rule["frequency_khz"]) or
                    not tracks or not transmission_authorized(
                        rule["created_by"], rule["frequency_khz"], "HOSTED_BROADCAST")):
                raise RuntimeError("Program unavailable or permission revoked.")
            self.store.start(key, rule["name"], rule["frequency_khz"], -rule["program_id"],
                             program["name"], tracks, program["loop_enabled"],
                             rule["runtime_minutes"] * 60, False,
                             permission_user_id=rule["created_by"], public_user_id=None, automated=True,
                             resume_kind="rule", resume_id=rule["id"])
        except (RuntimeError, ValueError):
            pass
        now = datetime.now(timezone.utc)
        next_run = None
        if rule["trigger_type"] == "interval":
            try:
                next_run = now + timedelta(minutes=max(1, int(rule["trigger_value"])))
            except ValueError:
                next_run = now + timedelta(hours=1)
        elif rule["trigger_type"] == "time":
            try:
                hour, minute = map(int, rule["trigger_value"].split(":"))
                next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_run <= now:
                    next_run += timedelta(days=1)
            except (ValueError, AttributeError):
                next_run = now + timedelta(days=1)
        database.execute("UPDATE radio_automation_rules SET last_run_at=?,next_run_at=? WHERE id=?",
                         (now.isoformat(), next_run.isoformat() if next_run else None, rule["id"]))
        database.commit()

    def notify(self, event, frequency=None):
        with self.app.app_context():
            database = get_db()
            rows = database.execute(
                "SELECT * FROM radio_automation_rules WHERE enabled=1 AND trigger_type=?", (event,)).fetchall()
            for rule in rows:
                if frequency is None or rule["frequency_khz"] == frequency:
                    if event == "track_interval":
                        try: interval = max(1, int(rule["trigger_value"] or "1"))
                        except ValueError: interval = 1
                        count = self._event_counts.get(rule["id"], 0) + 1
                        self._event_counts[rule["id"]] = count
                        if count % interval:
                            continue
                    self.start_rule(database, rule)

    def acquire(self, owner_key, user_id, connection_id, force=False):
        with self._lock:
            self.expire_leases()
            lease = self._leases.get(owner_key)
            if lease and lease["user_id"] != user_id and not force:
                raise RuntimeError("Another authorized broadcaster controls this broadcast.")
            self._leases[owner_key] = {"user_id": user_id, "connection_id": connection_id,
                                       "expires": monotonic() + 12}
            return dict(self._leases[owner_key])

    def heartbeat(self, owner_key, user_id, connection_id):
        with self._lock:
            lease = self._leases.get(owner_key)
            if not lease or lease["user_id"] != user_id or lease["connection_id"] != connection_id:
                return False
            lease["expires"] = monotonic() + 12
            return True

    def release(self, owner_key, user_id, connection_id, disconnected=False):
        frequency = None
        with self._lock:
            lease = self._leases.get(owner_key)
            if not lease or lease["user_id"] != user_id or lease["connection_id"] != connection_id:
                return False
            active = self.store.public(owner_key)
            frequency = active.get("frequency_khz") if active else None
            self._leases.pop(owner_key, None)
            self.store.set_live_mix(owner_key, False, controller=None)
        if disconnected:
            self.notify("dj_disconnect", frequency)
        return True

    def expire_leases(self):
        expired = []
        with self._lock:
            now = monotonic()
            for key, lease in list(self._leases.items()):
                if lease["expires"] <= now:
                    active = self.store.public(key)
                    expired.append((key, active.get("frequency_khz") if active else None))
                    self._leases.pop(key, None)
                    self.store.set_live_mix(key, False, controller=None)
        if expired:
            for _key, frequency in expired:
                self.notify("dj_disconnect", frequency)
        return [key for key, _frequency in expired]

    def owns(self, owner_key, user_id, connection_id):
        return self.heartbeat(owner_key, user_id, connection_id)


def _coordinator():
    return current_app.extensions["broadcast_coordinator"]


def _requested_session_key():
    """Resolve a public session key without trusting client-supplied key types."""
    requested = request.values.get("owner_key")
    sessions = current_app.extensions["hosted_broadcasts"].all_public()
    if requested in (None, ""):
        return g.user["id"], sessions.get(g.user["id"])
    match = next(((key, value) for key, value in sessions.items()
                  if str(key) == requested), None)
    if not match:
        abort(404)
    key, item = match
    if isinstance(key, str) and not g.user["is_admin"]:
        abort(403)
    if not transmission_authorized(g.user["id"], item["frequency_khz"], "HOSTED_BROADCAST"):
        abort(403)
    return key, item


@bp.get("/programs")
@_host_required
def programs():
    database = get_db()
    items = database.execute("SELECT * FROM radio_programs WHERE owner_id=? ORDER BY name,id",
                             (g.user["id"],)).fetchall()
    actions = database.execute(
        "SELECT a.*,c.name content_name,p.name playlist_name FROM radio_program_actions a "
        "LEFT JOIN radio_broadcast_content c ON c.id=a.content_id "
        "LEFT JOIN radio_playlists p ON p.id=a.playlist_id "
        "JOIN radio_programs r ON r.id=a.program_id WHERE r.owner_id=? ORDER BY a.program_id,a.position,a.id",
        (g.user["id"],)).fetchall()
    content = database.execute("SELECT * FROM radio_broadcast_content WHERE owner_id=? ORDER BY kind,name",
                               (g.user["id"],)).fetchall()
    playlists = database.execute("SELECT * FROM radio_playlists WHERE owner_id=? ORDER BY name",
                                 (g.user["id"],)).fetchall()
    audio = database.execute("SELECT * FROM radio_audio_files WHERE owner_id=? ORDER BY display_name",
                             (g.user["id"],)).fetchall()
    grouped = {item["id"]: [a for a in actions if a["program_id"] == item["id"]] for item in items}
    return render_template("radio/programs.html", programs=items, actions_by_program=grouped,
                           content=content, playlists=playlists, audio_files=audio)


@bp.post("/programs")
@_host_required
def create_program():
    validate_csrf()
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 80:
        flash("Program names must be between 1 and 80 characters.", "error")
    else:
        try:
            get_db().execute("INSERT INTO radio_programs(owner_id,name,description) VALUES(?,?,?)",
                             (g.user["id"], name, request.form.get("description", "").strip()[:240]))
            get_db().commit(); flash("Broadcast program created.", "success")
        except sqlite3.IntegrityError:
            flash("A program with that name already exists.", "error")
    return redirect(url_for("programs.programs"))


@bp.post("/programs/<int:program_id>")
@_host_required
def update_program(program_id):
    validate_csrf(); database = get_db(); _owned_program(database, program_id)
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 80:
        flash("Program names must be between 1 and 80 characters.", "error")
    else:
        database.execute("UPDATE radio_programs SET name=?,description=?,loop_enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (name, request.form.get("description", "").strip()[:240],
                          int(request.form.get("loop_enabled") == "on"), program_id))
        database.commit(); flash("Program updated.", "success")
    return redirect(url_for("programs.programs") + f"#program-{program_id}")


@bp.post("/programs/<int:program_id>/delete")
@_host_required
def delete_program(program_id):
    validate_csrf(); database = get_db(); _owned_program(database, program_id)
    try:
        database.execute("DELETE FROM radio_programs WHERE id=?", (program_id,)); database.commit()
        flash("Program deleted.", "success")
    except sqlite3.IntegrityError:
        database.rollback(); flash("This program is used by automation and cannot be deleted.", "error")
    return redirect(url_for("programs.programs"))


@bp.post("/programs/<int:program_id>/actions")
@_host_required
def add_action(program_id):
    validate_csrf(); database = get_db(); _owned_program(database, program_id)
    kind = request.form.get("action_type", "")
    if kind not in ACTION_TYPES:
        abort(400)
    playlist_id = request.form.get("playlist_id", type=int) if kind in {"playlist", "resume_playlist"} else None
    content_id = request.form.get("content_id", type=int) if kind not in {"playlist", "resume_playlist", "live"} else None
    if playlist_id and not database.execute("SELECT 1 FROM radio_playlists WHERE id=? AND owner_id=?", (playlist_id, g.user["id"])).fetchone():
        abort(404)
    content_row = (database.execute("SELECT kind FROM radio_broadcast_content WHERE id=? AND owner_id=?",
                                    (content_id, g.user["id"])).fetchone() if content_id else None)
    if content_id and not content_row: abort(404)
    if kind == "playlist" and not playlist_id:
        return jsonify(error="Choose a playlist for this action."), 400
    if kind in {"music", "announcement", "jingle", "sstv"} and (
            not content_row or content_row["kind"] != kind):
        return jsonify(error=f"Choose matching {kind} content for this action."), 400
    if kind == "morse" and content_row and content_row["kind"] != "morse":
        return jsonify(error="Choose Morse content, or enter inline Morse text."), 400
    if kind == "morse" and not content_row and not request.form.get("text_content", "").strip():
        return jsonify(error="Enter Morse text or choose Morse content."), 400
    repeat = min(100, max(1, request.form.get("repeat_count", 1, type=int)))
    duration = min(86400, max(0, request.form.get("duration_seconds", 0, type=float)))
    position = database.execute("SELECT COALESCE(MAX(position),-1)+1 FROM radio_program_actions WHERE program_id=?", (program_id,)).fetchone()[0]
    database.execute("INSERT INTO radio_program_actions(program_id,action_type,playlist_id,content_id,label,text_content,duration_seconds,repeat_count,position) VALUES(?,?,?,?,?,?,?,?,?)",
                     (program_id, kind, playlist_id, content_id, request.form.get("label", "").strip()[:80],
                      request.form.get("text_content", "").strip()[:2000], duration, repeat, position))
    database.commit(); flash("Program action added.", "success")
    return redirect(url_for("programs.programs") + f"#program-{program_id}")


@bp.post("/programs/<int:program_id>/actions/<int:action_id>/delete")
@_host_required
def delete_action(program_id, action_id):
    validate_csrf(); database = get_db(); _owned_program(database, program_id)
    if not database.execute("DELETE FROM radio_program_actions WHERE id=? AND program_id=?", (action_id, program_id)).rowcount:
        abort(404)
    rows = database.execute("SELECT id FROM radio_program_actions WHERE program_id=? ORDER BY position,id", (program_id,)).fetchall()
    database.executemany("UPDATE radio_program_actions SET position=? WHERE id=?", [(i, row[0]) for i, row in enumerate(rows)])
    database.commit(); return redirect(url_for("programs.programs") + f"#program-{program_id}")


@bp.post("/programs/<int:program_id>/actions/<int:action_id>/move")
@_host_required
def move_action(program_id, action_id):
    validate_csrf(); database = get_db(); _owned_program(database, program_id)
    rows = database.execute("SELECT id FROM radio_program_actions WHERE program_id=? ORDER BY position,id", (program_id,)).fetchall()
    ids = [row[0] for row in rows]
    if action_id not in ids: abort(404)
    index = ids.index(action_id); target = index + (1 if request.form.get("direction") == "down" else -1)
    if 0 <= target < len(ids):
        ids[index], ids[target] = ids[target], ids[index]
        database.executemany("UPDATE radio_program_actions SET position=? WHERE id=?", [(i, value) for i, value in enumerate(ids)])
        database.commit()
    return redirect(url_for("programs.programs") + f"#program-{program_id}")


@bp.post("/content")
@_host_required
def create_content():
    validate_csrf(); database = get_db()
    kind, name = request.form.get("kind", ""), request.form.get("name", "").strip()
    if kind not in CONTENT_TYPES or not 1 <= len(name) <= 80:
        flash("Choose a valid content type and a name up to 80 characters.", "error")
        return redirect(url_for("programs.programs") + "#content-library")
    audio_id = request.form.get("audio_file_id", type=int) if kind in {"music", "announcement", "jingle"} else None
    if audio_id and not database.execute("SELECT 1 FROM radio_audio_files WHERE id=? AND owner_id=?", (audio_id, g.user["id"])).fetchone():
        abort(404)
    text = request.form.get("text_content", "").strip()[:2000] if kind == "morse" else ""
    if kind in {"music", "announcement", "jingle"} and not audio_id:
        flash("Choose an existing audio file for this content type.", "error")
        return redirect(url_for("programs.programs") + "#content-library")
    if kind == "morse" and not text:
        flash("Morse content requires text.", "error")
        return redirect(url_for("programs.programs") + "#content-library")
    stored_name = media_type = None; size = 0
    if kind == "sstv":
        upload = request.files.get("image")
        payload = upload.read(5 * 1024 * 1024 + 1) if upload and upload.filename else b""
        signatures = ((b"\x89PNG\r\n\x1a\n", ".png", "image/png"), (b"\xff\xd8\xff", ".jpg", "image/jpeg"))
        match = next(((suffix, mime) for signature, suffix, mime in signatures if payload.startswith(signature)), None)
        if not match and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
            match = (".webp", "image/webp")
        if not payload or len(payload) > 5 * 1024 * 1024 or not match:
            flash("Choose a valid PNG, JPEG, or WebP image up to 5 MB.", "error")
            return redirect(url_for("programs.programs") + "#content-library")
        stored_name = secrets.token_hex(16) + match[0]; media_type = match[1]; size = len(payload)
        folder = Path(current_app.config["DATA_DIR"]) / "radio_content" / str(g.user["id"])
        folder.mkdir(parents=True, exist_ok=True); (folder / stored_name).write_bytes(payload)
    try:
        database.execute("INSERT INTO radio_broadcast_content(owner_id,kind,name,audio_file_id,text_content,stored_name,media_type,size_bytes) VALUES(?,?,?,?,?,?,?,?)",
                         (g.user["id"], kind, name, audio_id, text, stored_name, media_type or "", size))
        database.commit(); flash("Reusable broadcast content added.", "success")
    except sqlite3.IntegrityError:
        database.rollback(); flash("That content name is already used for this type.", "error")
    return redirect(url_for("programs.programs") + "#content-library")


@bp.get("/content/<int:content_id>/image")
@login_required
def content_image(content_id):
    row = get_db().execute("SELECT * FROM radio_broadcast_content WHERE id=? AND kind='sstv'", (content_id,)).fetchone()
    if not row or not row["stored_name"]:
        abort(404)
    tuned = current_app.extensions["radio"].receiver_frequency(_connection_id())
    active = current_app.extensions["hosted_broadcasts"].all_public()
    transmission = next((item for item in active.values() if item and
                         item.get("status") == "playing" and
                         item.get("content_id") == content_id and
                         item.get("frequency_khz") == tuned), None)
    if not transmission or effective_access(g.user["id"], tuned) == "DENIED":
        abort(403)
    path = Path(current_app.config["DATA_DIR"]) / "radio_content" / str(row["owner_id"]) / row["stored_name"]
    if not path.is_file(): abort(404)
    return send_file(path, mimetype=row["media_type"], conditional=True)


@bp.post("/content/<int:content_id>/delete")
@_host_required
def delete_content(content_id):
    validate_csrf(); database = get_db()
    row = database.execute("SELECT * FROM radio_broadcast_content WHERE id=? AND owner_id=?",
                           (content_id, g.user["id"])).fetchone()
    if not row: abort(404)
    if database.execute("SELECT 1 FROM radio_program_actions WHERE content_id=?", (content_id,)).fetchone():
        flash("Remove this content from programs before deleting it.", "error")
    else:
        database.execute("DELETE FROM radio_broadcast_content WHERE id=?", (content_id,)); database.commit()
        if row["stored_name"]:
            folder = (Path(current_app.config["DATA_DIR"]) / "radio_content" / str(g.user["id"])).resolve()
            path = (folder / row["stored_name"]).resolve()
            if path.parent == folder and path.is_file(): path.unlink()
        flash("Broadcast content deleted.", "success")
    return redirect(url_for("programs.programs") + "#content-library")


def _start_program(program_id, frequency, owner_key=None, automated=False, identity=None):
    database = get_db(); program, tracks = compile_program(database, program_id)
    if program["owner_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    error = _validate_program_for_frequency(database, program_id, frequency)
    if error: raise ValueError(error)
    if not transmission_authorized(g.user["id"], frequency, "HOSTED_BROADCAST"):
        raise PermissionError("Hosted broadcasting is not authorized on this frequency.")
    if not tracks: raise ValueError("This program has no playable actions.")
    limit = database.execute("SELECT radio_broadcast_limit_minutes FROM station WHERE id=1").fetchone()[0] * 60
    key = owner_key if owner_key is not None else g.user["id"]
    return current_app.extensions["hosted_broadcasts"].start(
        key, identity or g.user["callsign"], frequency, -program_id, program["name"], tracks,
        program["loop_enabled"], limit, False, permission_user_id=g.user["id"],
        public_user_id=None if automated else g.user["id"], automated=automated,
        resume_kind="program", resume_id=program_id)


@bp.post("/programs/<int:program_id>/start")
@_host_required
def start_program(program_id):
    validate_csrf(); database = get_db(); _owned_program(database, program_id)
    assignment = database.execute("SELECT frequency_khz FROM radio_broadcast_assignments WHERE user_id=?", (g.user["id"],)).fetchone()
    frequency = assignment[0] if assignment else parse_frequency(request.form.get("frequency"))
    try:
        result = _start_program(program_id, frequency)
    except PermissionError as problem: return jsonify(error=str(problem)), 403
    except ValueError as problem: return jsonify(error=str(problem)), 400
    except RuntimeError as problem: return jsonify(error=str(problem)), 409
    return jsonify(session=result), 201


@bp.post("/dj/control")
@_host_required
def dj_control():
    validate_csrf(); store = current_app.extensions["hosted_broadcasts"]
    owner_key, target_session = _requested_session_key()
    connection_id = _connection_id()
    action = request.form.get("action", "")
    force = bool(g.user["is_admin"] and request.form.get("force") == "1")
    if action == "takeover":
        if not target_session: return jsonify(error="Start a playlist or program first."), 409
        try: _coordinator().acquire(owner_key, g.user["id"], connection_id, force)
        except RuntimeError as problem: return jsonify(error=str(problem)), 409
        _coordinator().notify("dj_connect", target_session["frequency_khz"])
    elif action == "release":
        _coordinator().release(owner_key, g.user["id"], connection_id)
    elif not _coordinator().owns(owner_key, g.user["id"], connection_id):
        return jsonify(error="Take control of this broadcast first."), 409
    elif action == "pause":
        store.set_live_mix(owner_key, False, controller=connection_id)
        store.pause(owner_key)
    elif action == "resume":
        try: store.resume(owner_key)
        except RuntimeError as problem: return jsonify(error=str(problem)), 409
    elif action == "skip": store.skip(owner_key)
    elif action == "trigger":
        content_id = request.form.get("content_id", type=int)
        content = get_db().execute(
            "SELECT * FROM radio_broadcast_content WHERE id=? AND owner_id=?",
            (content_id, g.user["id"]),
        ).fetchone()
        if not content: return jsonify(error="Choose content from your broadcast library."), 404
        kind = content["kind"]
        band = find_band(target_session["frequency_khz"] if target_session else None)
        if kind == "morse" and (not band or not band["supports_cw"]):
            return jsonify(error="This frequency does not support Morse."), 409
        if kind == "sstv" and (not band or not band["supports_sstv"]):
            return jsonify(error="This frequency does not support SSTV."), 409
        if kind in {"music", "announcement", "jingle"}:
            injected = _audio_track(get_db(), content["audio_file_id"], kind,
                                    content["name"], content["id"])
        else:
            injected = {"file_id": None, "name": content["name"], "title": content["name"],
                        "artist": "", "duration": max(2, len(content["text_content"]) * .12)
                        if kind == "morse" else 8, "action_type": kind,
                        "text": content["text_content"], "content_id": content["id"]}
        if not injected or not store.inject(owner_key, injected):
            return jsonify(error="The content could not interrupt this broadcast."), 409
    elif action in {"ptt_on", "ptt_off", "heartbeat", "duck"}:
        session_data = store.public(owner_key)
        duck = request.form.get("ducking", session_data.get("ducking", .35) if session_data else .35, type=float)
        store.set_live_mix(owner_key, action == "ptt_on" or bool(session_data and session_data.get("mic_active") and action in {"heartbeat", "duck"}), duck, connection_id)
    else: return jsonify(error="Unknown DJ control action."), 400
    return jsonify(session=store.public(owner_key), controller=True, owner_key=str(owner_key))


@bp.get("/dj/status")
@_host_required
def dj_status():
    owner_key, _target_session = _requested_session_key()
    owned = _coordinator().heartbeat(owner_key, g.user["id"], _connection_id())
    return jsonify(session=current_app.extensions["hosted_broadcasts"].public(owner_key),
                   controller=owned, owner_key=str(owner_key))


@bp.get("/automation")
@admin_required
def automation():
    database = get_db()
    rules = database.execute("SELECT r.*,p.name program_name FROM radio_automation_rules r JOIN radio_programs p ON p.id=r.program_id ORDER BY r.frequency_khz,r.name").fetchall()
    programs = database.execute("SELECT * FROM radio_programs WHERE owner_id=? ORDER BY name", (g.user["id"],)).fetchall()
    return render_template("radio/automation.html", rules=rules, programs=programs, format_frequency=format_frequency)


def _next_run(trigger, value):
    now = datetime.now(timezone.utc)
    if trigger == "interval":
        return (now + timedelta(minutes=max(1, int(value)))).isoformat()
    if trigger == "time":
        hour, minute = map(int, value.split(":")); due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return (due + timedelta(days=due <= now)).isoformat()
    return None


@bp.post("/automation")
@admin_required
def create_rule():
    validate_csrf(); database = get_db()
    trigger, value = request.form.get("trigger_type", "time"), request.form.get("trigger_value", "").strip()
    frequency, program_id = parse_frequency(request.form.get("frequency")), request.form.get("program_id", type=int)
    name = request.form.get("name", "").strip()
    if trigger not in {"time", "interval", "playlist_complete", "track_interval", "dj_connect", "dj_disconnect"}:
        abort(400)
    program = database.execute("SELECT 1 FROM radio_programs WHERE id=? AND owner_id=?", (program_id, g.user["id"])).fetchone()
    error = _validate_program_for_frequency(database, program_id, frequency) if program and frequency else "Choose a valid program and frequency."
    if not 1 <= len(name) <= 80:
        error = "Automation names must be between 1 and 80 characters."
    elif frequency and (database.execute(
            "SELECT 1 FROM radio_broadcast_assignments WHERE frequency_khz=?", (frequency,)).fetchone() or
            database.execute("SELECT 1 FROM radio_automated_broadcasts WHERE frequency_khz=?", (frequency,)).fetchone() or
            database.execute("SELECT 1 FROM radio_external_broadcasts WHERE frequency_khz=?", (frequency,)).fetchone()):
        error = "That frequency is reserved for an existing broadcast."
    if error: flash(error, "error")
    else:
        try:
            next_run = _next_run(trigger, value)
            database.execute("INSERT INTO radio_automation_rules(created_by,name,message,frequency_khz,program_id,enabled,trigger_type,trigger_value,runtime_minutes,next_run_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (g.user["id"], name, request.form.get("message", "").strip()[:240], frequency, program_id,
                              int(request.form.get("enabled") == "on"), trigger, value, min(1440, max(1, request.form.get("runtime_minutes", 120, type=int))), next_run))
            database.commit(); flash("Automation rule created.", "success")
        except (sqlite3.IntegrityError, ValueError):
            database.rollback(); flash("The automation rule is invalid or conflicts with an existing rule.", "error")
    return redirect(url_for("programs.automation"))


@bp.post("/automation/<int:rule_id>/control")
@admin_required
def control_rule(rule_id):
    validate_csrf(); database = get_db(); rule = database.execute("SELECT * FROM radio_automation_rules WHERE id=?", (rule_id,)).fetchone()
    if not rule: abort(404)
    action = request.form.get("action")
    if action == "delete":
        current_app.extensions["hosted_broadcasts"].stop(f"rule:{rule_id}", "deleted")
        database.execute("DELETE FROM radio_automation_rules WHERE id=?", (rule_id,))
    elif action == "run": _coordinator().start_rule(database, rule)
    elif action in {"enable", "disable"}:
        enabled = action == "enable"
        next_run = _next_run(rule["trigger_type"], rule["trigger_value"]) if enabled else None
        database.execute("UPDATE radio_automation_rules SET enabled=?,next_run_at=? WHERE id=?", (enabled, next_run, rule_id))
    elif action == "stop": current_app.extensions["hosted_broadcasts"].stop(f"rule:{rule_id}", "sysop_stopped")
    else: abort(400)
    database.commit(); return redirect(url_for("programs.automation"))
