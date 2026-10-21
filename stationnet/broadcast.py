"""Private audio libraries and playlist preparation for broadcast hosts."""
import secrets
import sqlite3
from functools import wraps
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, url_for)

from .auth import login_required
from .db import get_db
from .radio import can_manage_broadcasts
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
                "INSERT INTO radio_audio_files(owner_id,display_name,stored_name,media_type,size_bytes) "
                "VALUES(?,?,?,?,?)",
                (g.user["id"], item["filename"], item["stored_name"],
                 AUDIO_FORMATS[item["suffix"]], len(item["payload"])),
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
