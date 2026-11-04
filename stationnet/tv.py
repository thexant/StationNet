"""Linear television: channels, scheduled programming, guide and media delivery.

Schedules are computed by the server from programming blocks and the wall
clock; browsers only seek existing files to the computed broadcast offset.
"""
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
import hmac
from io import BytesIO
import json
import re
import secrets
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, send_file, session, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError

from . import tv_autoschedule as auto
from . import tv_library as library
from . import tv_schedule as schedule
from .db import get_db
from .moderation import audit

bp = Blueprint("tv", __name__, url_prefix="/tv")
admin_bp = Blueprint("tv_admin", __name__, url_prefix="/sysop/tv")

UTC = timezone.utc
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
KINDS = ("program", "ident", "bumper", "commercial", "offair")
CONTENT_TYPES = auto.CONTENT_TYPES
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
MAX_GUIDE_HOURS = 6
GUIDE_DAYS_AHEAD = 7
MAX_LOGO_BYTES = 4 * 1024 * 1024
MAX_UPLOAD_MB_LIMIT = 1024 * 1024
MAX_DURATION_SECONDS = 7 * 86400
CACHE_DAYS = 512
_cache_lock = threading.Lock()


@bp.before_request
def require_enabled():
    if not g.station or not g.station["tv_enabled"]:
        abort(404)


@admin_bp.before_request
def require_sysop():
    if g.user is None or not g.user["is_admin"]:
        if request.path.startswith("/sysop/tv/api/"):
            return jsonify(error="SYSOP authorization required."), 403
        flash("SYSOP authorization required.", "error")
        return redirect(url_for("station.terminal"))
    if request.method != "GET":
        token = request.headers.get("X-CSRF-Token", "")
        if not session.get("csrf_token") or not hmac.compare_digest(token, session["csrf_token"]):
            return jsonify(error="Invalid or expired form token."), 400
    return None


# Shared helpers ----------------------------------------------------------

def _now():
    return datetime.now(UTC)


def _iso(moment):
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _zone(station=None):
    try:
        return ZoneInfo((station or g.station)["tv_timezone"] or "UTC")
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def valid_timezone(name):
    try:
        ZoneInfo(name)
        return bool(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False


def version():
    return get_db().execute("SELECT version FROM tv_state WHERE id=1").fetchone()[0]


def bump_version(database):
    """Invalidate computed timelines; viewers refetch when they notice."""
    database.execute("UPDATE tv_state SET version=version+1 WHERE id=1")


def settings_data():
    """Values used by the Sysop Settings panel, even when TV is disabled."""
    channels = get_db().execute("SELECT id,number,name FROM tv_channels ORDER BY number").fetchall()
    return {"tv_channels": channels}


def _channel_json(row):
    return {"id": row["id"], "number": row["number"], "name": row["name"],
            "description": row["description"],
            "logo_url": url_for("tv.logo", channel_id=row["id"], v=row["logo_stored"]) if row["logo_stored"] else None}


def _viewer_channels():
    return get_db().execute(
        "SELECT * FROM tv_channels WHERE enabled=1 ORDER BY sort_order,number").fetchall()


def _media_available(row):
    if row["missing"]:
        return False
    path = library.resolve(row["rel_path"])
    return path is not None and path.is_file()


def _item(row, title=None, description=""):
    return schedule.Item(row["id"], title or row["title"], row["duration_s"],
                         description or row["description"], _media_available(row),
                         row["kind"] in auto.INTERSTITIAL_KINDS)


def _blocks(channel_id, include_disabled=False):
    database = get_db()
    rows = database.execute(
        "SELECT * FROM tv_blocks WHERE channel_id=?" + ("" if include_disabled else " AND enabled=1") +
        " ORDER BY id", (channel_id,)).fetchall()
    items = {}
    for row in database.execute(
            "SELECT i.block_id,i.title_override,i.description item_description,m.* FROM tv_block_items i "
            "JOIN tv_media m ON m.id=i.media_id JOIN tv_blocks b ON b.id=i.block_id "
            "WHERE b.channel_id=? ORDER BY i.block_id,i.position", (channel_id,)):
        items.setdefault(row["block_id"], []).append(row)
    return rows, items


def _engine_blocks(channel_id):
    rows, items = _blocks(channel_id)
    return [schedule.Block(
        row["id"], row["title"], row["kind"], row["start_time"],
        tuple(_item(media, media["title_override"], media["item_description"]) for media in items.get(row["id"], ())),
        row["description"], row["start_date"], row["weekdays"], row["loop_until"],
        row["valid_from"], row["valid_until"]) for row in rows]


def _gap_sources(channel):
    database = get_db()
    filler = tuple(_item(row) for row in database.execute(
        "SELECT m.* FROM tv_channel_filler f JOIN tv_media m ON m.id=f.media_id "
        "WHERE f.channel_id=? ORDER BY f.position", (channel["id"],)))
    offair = None
    if channel["offair_media_id"]:
        row = database.execute("SELECT * FROM tv_media WHERE id=?", (channel["offair_media_id"],)).fetchone()
        offair = _item(row) if row else None
    return channel["gap_mode"], filler, offair


def _day_timeline(channel, day_index, zone, current_version):
    key = (channel["id"], day_index, str(zone))
    with _cache_lock:
        cache = current_app.extensions.setdefault("tv_cache", {"version": None, "days": OrderedDict()})
        if cache["version"] != current_version:
            cache["version"], cache["days"] = current_version, OrderedDict()
        if key in cache["days"]:
            cache["days"].move_to_end(key)
            return cache["days"][key]
    start = UNIX_EPOCH + timedelta(days=day_index)
    gap_mode, filler, offair = _gap_sources(channel)
    result = schedule.airings(_engine_blocks(channel["id"]), start, start + timedelta(days=1),
                              zone, gap_mode, filler, offair)
    with _cache_lock:
        days = current_app.extensions["tv_cache"]["days"]
        days[key] = result
        while len(days) > CACHE_DAYS:
            days.popitem(last=False)
    return result


def timeline(channel, start, end):
    """Merged airings covering [start, end) assembled from cached UTC days."""
    zone, current_version = _zone(), version()
    first = int((start - UNIX_EPOCH).total_seconds() // 86400)
    last = int(((end - UNIX_EPOCH).total_seconds() - 0.001) // 86400)
    pieces = []
    for day_index in range(first, last + 1):
        pieces.extend(_day_timeline(channel, day_index, zone, current_version))
    merged = schedule.merge(pieces)
    return [a.clip(start, end) for a in merged if a.end > start and a.start < end]


def _airing_json(airing, now=None):
    playable = airing.media_id is not None and airing.kind != "standby"
    data = {"start": _iso(airing.start), "end": _iso(airing.end), "kind": airing.kind,
            "title": airing.title, "description": airing.description, "block": airing.block_title,
            "media_url": url_for("tv.media", media_id=airing.media_id) if playable else None,
            "media_offset": round(airing.media_offset, 3)}
    if now is not None:
        offset = airing.media_offset
        if airing.kind != "offair":
            offset += (now - airing.start).total_seconds()
        data["offset"] = round(offset, 3)
    return data


def _resumes_at(channel, after):
    """Start of the next scheduled programme within the guide horizon."""
    horizon = after + timedelta(days=GUIDE_DAYS_AHEAD)
    moment = after
    while moment < horizon:
        chunk_end = min(horizon, moment + timedelta(days=1))
        for airing in timeline(channel, moment, chunk_end):
            if airing.kind in {"program", "standby"} and airing.start >= after:
                return airing.start
        moment = chunk_end
    return None


def activity_listings():
    """Now/next per enabled channel with content on or scheduled, lowest number first.

    Used by the terminal's STATION ACTIVITY panel; never raises.
    """
    try:
        now = _now()
        listings = []
        for channel in sorted(_viewer_channels(), key=lambda row: row["number"]):
            airings = schedule.fold_interstitials(
                timeline(channel, now - timedelta(hours=12), now + timedelta(hours=24)))
            index = schedule.at(airings, now)
            current = airings[index] if index is not None else schedule.Airing(
                now, now + timedelta(hours=1), "offair", "OFF AIR")
            later = airings[index + 1:] if index is not None else [a for a in airings if a.start > now]
            following = next((a for a in later if a.kind in {"program", "standby"} and not a.interstitial), None)
            if following is None and current.kind in {"offair", "filler"}:
                resumes = _resumes_at(channel, now)
                if resumes is not None:
                    following = next((a for a in schedule.fold_interstitials(
                        timeline(channel, resumes, resumes + timedelta(hours=12)))
                        if a.kind in {"program", "standby"}), None)
            if current.kind not in {"program", "standby", "filler"} and following is None:
                continue
            listings.append({
                "id": channel["id"], "number": channel["number"], "name": channel["name"],
                "now": {"title": current.title, "kind": current.kind,
                        "start": _iso(current.start), "end": _iso(current.end)},
                "next": {"title": following.title, "start": _iso(following.start),
                         "far": following.start - now > timedelta(hours=18)} if following else None,
            })
        return listings
    except Exception:  # A schedule fault must not take down the terminal page.
        current_app.logger.exception("TV activity listings failed")
        return []


def _ranged(path, mimetype):
    """Serve at most one CHUNK per request so slow clients never buffer whole files."""
    stat = path.stat()
    size = stat.st_size
    headers = {"Accept-Ranges": "bytes", "ETag": f'"tv-{size:x}-{int(stat.st_mtime):x}"',
               "Cache-Control": "private, max-age=86400", "Content-Disposition": "inline"}
    if size == 0:
        return Response(b"", 200, mimetype=mimetype, headers=headers)
    start, end, partial = 0, size - 1, size > library.CHUNK_BYTES
    match = RANGE_RE.match(request.headers.get("Range", "").strip())
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
        else:
            start = max(0, size - int(match.group(2)))
        if start >= size or end < start:
            return Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        partial = True
    end = min(end, size - 1, start + library.CHUNK_BYTES - 1)
    length = end - start + 1

    def stream():
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                data = handle.read(min(256 * 1024, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers["Content-Length"] = str(length)
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(stream(), 206 if partial else 200, mimetype=mimetype, headers=headers,
                    direct_passthrough=True)


# Viewer ------------------------------------------------------------------

@bp.get("")
def index():
    channels = [_channel_json(row) for row in _viewer_channels()]
    return render_template("tv/index.html", channels=channels,
                           default_channel_id=g.station["tv_default_channel_id"])


@bp.get("/api/now")
def now_playing():
    channel = get_db().execute("SELECT * FROM tv_channels WHERE id=? AND enabled=1",
                               (request.args.get("channel", type=int),)).fetchone()
    if channel is None:
        return jsonify(error="No such channel."), 404
    now = _now()
    airings = timeline(channel, now - timedelta(hours=12), now + timedelta(hours=12))
    index = schedule.at(airings, now)
    if index is None:
        airings, index = [schedule.Airing(now, now + timedelta(hours=12), "offair", "OFF AIR")], 0
    current = airings[index]
    following = next((airing for airing in airings[index + 1:] if not airing.interstitial), None)
    data = {"server_now": _iso(now), "version": version(), "channel": _channel_json(channel),
            "airing": _airing_json(current, now), "next": _airing_json(following) if following else None}
    if current.kind in {"offair", "filler"}:
        resumes = _resumes_at(channel, now)
        data["resumes_at"] = _iso(resumes) if resumes else None
    return jsonify(data)


@bp.get("/api/guide")
def guide():
    now = _now()
    try:
        start = datetime.fromisoformat(request.args.get("start", "").replace("Z", "+00:00"))
        start = start.astimezone(UTC) if start.tzinfo else start.replace(tzinfo=UTC)
    except ValueError:
        start = now
    hours = min(max(request.args.get("hours", 3, type=float), 0.5), MAX_GUIDE_HOURS)
    start = min(max(start, now - timedelta(hours=2)), now + timedelta(days=GUIDE_DAYS_AHEAD))
    end = start + timedelta(hours=hours)
    channels = []
    for channel in _viewer_channels():
        data = _channel_json(channel)
        # A wider span keeps the true start and end of programmes crossing the window edges.
        data["airings"] = [_airing_json(airing) for airing in schedule.fold_interstitials(
                           timeline(channel, start - timedelta(hours=12), end + timedelta(hours=12)))
                           if airing.end > start and airing.start < end]
        channels.append(data)
    return jsonify(server_now=_iso(now), version=version(), start=_iso(start), end=_iso(end), channels=channels)


@bp.get("/media/<int:media_id>")
def media(media_id):
    database = get_db()
    row = database.execute("SELECT * FROM tv_media WHERE id=?", (media_id,)).fetchone()
    if row is None:
        abort(404)
    path = library.resolve(row["rel_path"])
    if path is None or not path.is_file():
        if not row["missing"]:
            database.execute("UPDATE tv_media SET missing=1 WHERE id=?", (media_id,))
            bump_version(database)
            database.commit()
        abort(404)
    return _ranged(path, row["mime_type"])


@bp.get("/logo/<int:channel_id>")
def logo(channel_id):
    row = get_db().execute("SELECT logo_stored FROM tv_channels WHERE id=?", (channel_id,)).fetchone()
    if row is None or not row["logo_stored"]:
        abort(404)
    path = library.folder("logos") / row["logo_stored"]
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/png", max_age=86400)


# Administration ----------------------------------------------------------

def _json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(message, status=400, **extra):
    return jsonify(error=message, **extra), status


def _media_json(row, usage=0):
    return {key: row[key] for key in ("id", "title", "description", "folder", "source", "rel_path",
                                      "original_name", "size_bytes", "duration_s", "width", "height",
                                      "kind", "content_type", "genre", "series", "season", "episode",
                                      "missing", "created_at")} | {
        "usage": usage, "url": url_for("tv.media", media_id=row["id"])}


def _admin_channel_json(row, filler):
    return _channel_json(row) | {key: row[key] for key in ("enabled", "sort_order", "gap_mode", "offair_media_id")} | {
        "filler": filler.get(row["id"], []), "has_logo": bool(row["logo_stored"]),
        "logo_admin_url": url_for("tv_admin.channel_logo", channel_id=row["id"], v=row["logo_stored"]) if row["logo_stored"] else None}


@admin_bp.get("")
def manage():
    return render_template("tv/admin.html")


@admin_bp.get("/api/state")
def state():
    database = get_db()
    usage = {row[0]: row[1] for row in database.execute(
        "SELECT media_id,COUNT(*) FROM tv_block_items GROUP BY media_id")}
    filler = {}
    for row in database.execute("SELECT channel_id,media_id FROM tv_channel_filler ORDER BY channel_id,position"):
        filler.setdefault(row[0], []).append(row[1])
    channels = database.execute("SELECT * FROM tv_channels ORDER BY sort_order,number").fetchall()
    media = database.execute("SELECT * FROM tv_media ORDER BY folder COLLATE NOCASE,title COLLATE NOCASE").fetchall()
    rules = database.execute("SELECT folder,kind,recursive FROM tv_folder_kinds ORDER BY folder COLLATE NOCASE")
    return jsonify(
        channels=[_admin_channel_json(row, filler) for row in channels],
        media=[_media_json(row, usage.get(row["id"], 0)) for row in media],
        folder_rules=[{"folder": row[0], "kind": row[1], "recursive": bool(row[2])} for row in rules],
        settings={"enabled": bool(g.station["tv_enabled"]), "timezone": g.station["tv_timezone"],
                  "max_upload_mb": g.station["tv_max_upload_mb"],
                  "default_channel_id": g.station["tv_default_channel_id"]},
        import_path=str(library.folder("import")), chunk_bytes=library.CHUNK_BYTES,
        formats=sorted(library.FORMATS), version=version())


def _channel_payload(data, channel_id=None):
    database = get_db()
    try:
        number = int(data.get("number"))
    except (TypeError, ValueError):
        return None, "Channel number must be a whole number."
    name = str(data.get("name", "")).strip()
    description = str(data.get("description", "")).strip()
    gap_mode = data.get("gap_mode", "offair")
    if not 1 <= number <= 9999:
        return None, "Channel number must be between 1 and 9999."
    if not 1 <= len(name) <= 60:
        return None, "Channel name must contain 1 to 60 characters."
    if len(description) > 500:
        return None, "Channel description cannot exceed 500 characters."
    if gap_mode not in {"offair", "filler"}:
        return None, "Select a valid gap behaviour."
    if database.execute("SELECT 1 FROM tv_channels WHERE number=? AND id<>?", (number, channel_id or -1)).fetchone():
        return None, f"Channel {number} already exists."
    filler = data.get("filler") or []
    if not isinstance(filler, list) or len(filler) > 200:
        return None, "Filler must be a list of up to 200 library items."
    try:
        filler = [int(value) for value in filler]
        offair = int(data["offair_media_id"]) if data.get("offair_media_id") else None
    except (TypeError, ValueError):
        return None, "A selected library item no longer exists."
    wanted = set(filler) | ({offair} if offair else set())
    if wanted:
        found = {row[0] for row in database.execute(
            f"SELECT id FROM tv_media WHERE id IN ({','.join('?' * len(wanted))})", tuple(wanted))}
        if found != wanted:
            return None, "A selected library item no longer exists."
    return {"number": number, "name": name, "description": description, "gap_mode": gap_mode,
            "enabled": int(bool(data.get("enabled", True))), "offair_media_id": offair,
            "filler": filler}, None


def _save_filler(database, channel_id, filler):
    database.execute("DELETE FROM tv_channel_filler WHERE channel_id=?", (channel_id,))
    database.executemany("INSERT INTO tv_channel_filler(channel_id,position,media_id) VALUES(?,?,?)",
                         [(channel_id, index, media_id) for index, media_id in enumerate(filler)])


@admin_bp.post("/api/channels")
def create_channel():
    values, error = _channel_payload(_json())
    if error:
        return _error(error)
    database = get_db()
    order = database.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM tv_channels").fetchone()[0]
    channel_id = database.execute(
        "INSERT INTO tv_channels(number,name,description,enabled,sort_order,gap_mode,offair_media_id) "
        "VALUES(?,?,?,?,?,?,?)", (values["number"], values["name"], values["description"], values["enabled"],
                                  order, values["gap_mode"], values["offair_media_id"])).lastrowid
    _save_filler(database, channel_id, values["filler"])
    bump_version(database)
    database.commit()
    return jsonify(id=channel_id)


@admin_bp.post("/api/channels/<int:channel_id>")
def update_channel(channel_id):
    database = get_db()
    if not database.execute("SELECT 1 FROM tv_channels WHERE id=?", (channel_id,)).fetchone():
        return _error("No such channel.", 404)
    values, error = _channel_payload(_json(), channel_id)
    if error:
        return _error(error)
    database.execute("UPDATE tv_channels SET number=?,name=?,description=?,enabled=?,gap_mode=?,offair_media_id=? "
                     "WHERE id=?", (values["number"], values["name"], values["description"], values["enabled"],
                                    values["gap_mode"], values["offair_media_id"], channel_id))
    _save_filler(database, channel_id, values["filler"])
    bump_version(database)
    database.commit()
    return jsonify(ok=True)


@admin_bp.post("/api/channels/reorder")
def reorder_channels():
    ids = _json().get("ids")
    database = get_db()
    existing = {row[0] for row in database.execute("SELECT id FROM tv_channels")}
    if not isinstance(ids, list) or {int(value) for value in ids if str(value).isdigit()} != existing:
        return _error("Send every channel exactly once.")
    database.executemany("UPDATE tv_channels SET sort_order=? WHERE id=?",
                         [(index, int(value)) for index, value in enumerate(ids)])
    database.commit()
    return jsonify(ok=True)


@admin_bp.post("/api/channels/<int:channel_id>/delete")
def delete_channel(channel_id):
    database = get_db()
    row = database.execute("SELECT * FROM tv_channels WHERE id=?", (channel_id,)).fetchone()
    if row is None:
        return _error("No such channel.", 404)
    database.execute("DELETE FROM tv_channels WHERE id=?", (channel_id,))
    database.execute("UPDATE station SET tv_default_channel_id=NULL WHERE tv_default_channel_id=?", (channel_id,))
    audit(database, g.user["id"], None, "tv_channel_delete", target_type="tv_channel", target_id=channel_id,
          note=f"{row['number']} {row['name']}")
    bump_version(database)
    database.commit()
    if row["logo_stored"]:
        (library.folder("logos") / row["logo_stored"]).unlink(missing_ok=True)
    return jsonify(ok=True)


@admin_bp.get("/logo/<int:channel_id>")
def channel_logo(channel_id):
    row = get_db().execute("SELECT logo_stored FROM tv_channels WHERE id=?", (channel_id,)).fetchone()
    path = library.folder("logos") / row["logo_stored"] if row and row["logo_stored"] else None
    if path is None or not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/png")


@admin_bp.post("/api/channels/<int:channel_id>/logo")
def upload_logo(channel_id):
    database = get_db()
    row = database.execute("SELECT logo_stored FROM tv_channels WHERE id=?", (channel_id,)).fetchone()
    if row is None:
        return _error("No such channel.", 404)
    uploaded = request.files.get("logo")
    if uploaded is None:
        return _error("Choose an image.")
    data = uploaded.read(MAX_LOGO_BYTES + 1)
    if len(data) > MAX_LOGO_BYTES:
        return _error("Logo images cannot exceed 4 MB.")
    try:
        image = ImageOps.exif_transpose(Image.open(BytesIO(data)))
        image.thumbnail((320, 160))
        image = image.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return _error("That file is not a supported image.")
    stored = f"{channel_id}-{secrets.token_hex(6)}.png"
    image.save(library.folder("logos") / stored, "PNG")
    database.execute("UPDATE tv_channels SET logo_stored=? WHERE id=?", (stored, channel_id))
    database.commit()
    if row["logo_stored"]:
        (library.folder("logos") / row["logo_stored"]).unlink(missing_ok=True)
    return jsonify(ok=True)


@admin_bp.post("/api/channels/<int:channel_id>/logo/delete")
def delete_logo(channel_id):
    database = get_db()
    row = database.execute("SELECT logo_stored FROM tv_channels WHERE id=?", (channel_id,)).fetchone()
    if row is None:
        return _error("No such channel.", 404)
    database.execute("UPDATE tv_channels SET logo_stored=NULL WHERE id=?", (channel_id,))
    database.commit()
    if row["logo_stored"]:
        (library.folder("logos") / row["logo_stored"]).unlink(missing_ok=True)
    return jsonify(ok=True)


# Library -----------------------------------------------------------------

def _media_row(media_id):
    return get_db().execute("SELECT * FROM tv_media WHERE id=?", (media_id,)).fetchone()


BULK_LIMIT = 5000
SQL_CHUNK = 500


def _chunks(values):
    for start in range(0, len(values), SQL_CHUNK):
        yield values[start:start + SQL_CHUNK]


def _ids(data, table="tv_media"):
    """De-duplicated ids from ``data["ids"]`` that exist in ``table``, or an error."""
    raw = data.get("ids")
    if not isinstance(raw, list) or not 1 <= len(raw) <= BULK_LIMIT:
        return None, f"Select between 1 and {BULK_LIMIT} items."
    try:
        wanted = list(dict.fromkeys(int(value) for value in raw))
    except (TypeError, ValueError):
        return None, "Invalid selection."
    found = set()
    for chunk in _chunks(wanted):
        found.update(row[0] for row in get_db().execute(
            f"SELECT id FROM {table} WHERE id IN ({','.join('?' * len(chunk))})", chunk))
    return [value for value in wanted if value in found], None


def _folder_match(folder, recursive):
    """SQL condition and parameters selecting a library folder, optionally with its sub-folders."""
    if not folder or not recursive:
        return "folder=?", (folder,)
    return "(folder=? OR substr(folder,1,?)=?)", (folder, len(folder) + 1, folder + "/")


def _rule_kind(database, folder):
    """Type remembered for new files in ``folder``: the most specific matching rule wins."""
    best = None
    for row in database.execute("SELECT folder,kind,recursive FROM tv_folder_kinds"):
        if row[0] == folder or (row[2] and row[0] and folder.startswith(row[0] + "/")):
            if best is None or len(row[0]) > len(best[0]):
                best = row
    return best[1] if best else "program"


def _insert_media(database, rel_path, source, name, folder, path, fingerprint):
    info = library.probe(path)
    kind = _rule_kind(database, folder)
    episode = library.guess_episode(name, folder) if kind == "program" else {}
    return database.execute(
        "INSERT INTO tv_media(title,folder,source,rel_path,original_name,mime_type,size_bytes,"
        "duration_s,width,height,fingerprint,kind,content_type,series,season,episode) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (library.title_from(name), folder, source, rel_path, name[:255],
         library.FORMATS[path.suffix.lower()], path.stat().st_size, info.get("duration_s"),
         info.get("width"), info.get("height"), fingerprint, kind, "show" if episode else "",
         episode.get("series", ""), episode.get("season"), episode.get("episode"))).lastrowid


def _metadata(data):
    """Validated scheduler metadata among the keys present in ``data``: (changes, error)."""
    changes = {}
    if "content_type" in data:
        if data["content_type"] not in ("", *CONTENT_TYPES):
            return None, "Select a valid content type."
        changes["content_type"] = data["content_type"]
    for key, limit in (("genre", 60), ("series", 150)):
        if key in data:
            value = " ".join(str(data[key] or "").split())
            if len(value) > limit:
                return None, f"{key.capitalize()} cannot exceed {limit} characters."
            changes[key] = value
    for key in ("season", "episode"):
        if key in data:
            if data[key] in (None, ""):
                changes[key] = None
                continue
            try:
                number = int(data[key])
            except (TypeError, ValueError):
                return None, f"{key.capitalize()} must be a whole number."
            if not 0 <= number <= 99999:
                return None, f"{key.capitalize()} must be between 0 and 99999."
            changes[key] = number
    return changes, None


@admin_bp.post("/api/media/<int:media_id>")
def update_media(media_id):
    database = get_db()
    if _media_row(media_id) is None:
        return _error("No such library item.", 404)
    data = _json()
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    kind = data.get("kind", "program")
    if not 1 <= len(title) <= 150:
        return _error("Title must contain 1 to 150 characters.")
    if len(description) > 5000:
        return _error("Description cannot exceed 5000 characters.")
    if kind not in KINDS:
        return _error("Select a valid programme type.")
    metadata, error = _metadata(data)
    if error:
        return _error(error)
    database.execute("UPDATE tv_media SET title=?,description=?,folder=?,kind=? WHERE id=?",
                     (title, description, library.clean_folder(data.get("folder")), kind, media_id))
    if metadata:
        database.execute(f"UPDATE tv_media SET {','.join(f'{key}=?' for key in metadata)} WHERE id=?",
                         (*metadata.values(), media_id))
    bump_version(database)
    database.commit()
    return jsonify(ok=True)


@admin_bp.post("/api/media/<int:media_id>/probe")
def report_probe(media_id):
    """Browser-reported metadata for stations without ffprobe."""
    database = get_db()
    row = _media_row(media_id)
    if row is None:
        return _error("No such library item.", 404)
    data = _json()
    try:
        duration = float(data.get("duration"))
        width, height = int(data.get("width") or 0), int(data.get("height") or 0)
    except (TypeError, ValueError):
        return _error("Invalid metadata.")
    if not 1 <= duration <= MAX_DURATION_SECONDS:
        return _error("Invalid duration.")
    if row["duration_s"] is None:
        database.execute("UPDATE tv_media SET duration_s=?,width=?,height=? WHERE id=?",
                         (duration, width or None, height or None, media_id))
        bump_version(database)
        database.commit()
    return jsonify(ok=True)


def _delete_media(database, row):
    """Remove one library entry; return (None, None) or (reason, scheduled-block list)."""
    usage = [f"{block['number']} {block['name']} / {block['title']}" for block in database.execute(
        "SELECT DISTINCT c.number,c.name,b.title FROM tv_block_items i JOIN tv_blocks b ON b.id=i.block_id "
        "JOIN tv_channels c ON c.id=b.channel_id WHERE i.media_id=? ORDER BY c.number", (row["id"],))]
    if usage:
        return "scheduled", usage
    if row["source"] == "upload":
        path = library.resolve(row["rel_path"])
        try:
            if path is not None:
                path.unlink(missing_ok=True)
        except OSError:
            return "file", None
    database.execute("DELETE FROM tv_media WHERE id=?", (row["id"],))
    audit(database, g.user["id"], None, "tv_media_delete", target_type="tv_media", target_id=row["id"],
          note=row["title"])
    return None, None


@admin_bp.post("/api/media/<int:media_id>/delete")
def delete_media(media_id):
    database = get_db()
    row = _media_row(media_id)
    if row is None:
        return _error("No such library item.", 404)
    reason, usage = _delete_media(database, row)
    if reason == "scheduled":
        return _error("This programme is still scheduled. Remove it from these blocks first.", 409, usage=usage)
    if reason == "file":
        return _error("The file could not be deleted; nothing was changed.", 500)
    bump_version(database)
    database.commit()
    return jsonify(ok=True, file_kept=row["source"] == "import")


@admin_bp.post("/api/media/bulk")
def bulk_update_media():
    data = _json()
    ids, error = _ids(data)
    if error:
        return _error(error)
    changes = {}
    if "kind" in data:
        if data["kind"] not in KINDS:
            return _error("Select a valid programme type.")
        changes["kind"] = data["kind"]
    if "folder" in data:
        changes["folder"] = library.clean_folder(data["folder"])
    if "description" in data:
        description = str(data["description"] or "").strip()
        if len(description) > 5000:
            return _error("Description cannot exceed 5000 characters.")
        changes["description"] = description
    metadata, error = _metadata({key: data[key] for key in ("content_type", "genre", "series", "season") if key in data})
    if error:
        return _error(error)
    changes |= metadata
    autonumber = bool(data.get("autonumber"))
    if not changes and not autonumber:
        return _error("Choose something to change.")
    database = get_db()
    if changes:
        assignments = ",".join(f"{key}=?" for key in changes)
        for chunk in _chunks(ids):
            database.execute(f"UPDATE tv_media SET {assignments} WHERE id IN ({','.join('?' * len(chunk))})",
                             (*changes.values(), *chunk))
    if autonumber:
        # Episodes become 1..n in title order within each series and season of the selection.
        rows = []
        for chunk in _chunks(ids):
            rows.extend(database.execute(
                f"SELECT id,title,series,season FROM tv_media WHERE id IN ({','.join('?' * len(chunk))})", chunk))
        groups = {}
        for row in sorted(rows, key=lambda r: (library.natural_key(r["title"]), r["id"])):
            groups.setdefault((row["series"].lower(), row["season"]), []).append(row["id"])
        database.executemany("UPDATE tv_media SET episode=? WHERE id=?",
                             [(number, media_id) for members in groups.values()
                              for number, media_id in enumerate(members, 1)])
    summary = ", ".join(f"{key}={str(value)[:40]}" if key != "description" else "description"
                        for key, value in changes.items()) + (" autonumber" if autonumber else "")
    audit(database, g.user["id"], None, "tv_media_bulk_edit", target_type="tv_media", note=f"{len(ids)} items: {summary}")
    bump_version(database)
    database.commit()
    return jsonify(updated=len(ids))


@admin_bp.post("/api/media/bulk-delete")
def bulk_delete_media():
    ids, error = _ids(_json())
    if error:
        return _error(error)
    database = get_db()
    deleted = kept_files = 0
    blocked, failed = [], []
    for media_id in ids:
        row = _media_row(media_id)
        reason, usage = _delete_media(database, row)
        if reason == "scheduled":
            blocked.append({"id": media_id, "title": row["title"], "usage": usage})
        elif reason == "file":
            failed.append({"id": media_id, "title": row["title"]})
        else:
            deleted += 1
            kept_files += row["source"] == "import"
    bump_version(database)
    database.commit()
    return jsonify(deleted=deleted, kept_files=kept_files, blocked=blocked, failed=failed)


@admin_bp.post("/api/folders/kind")
def folder_kind():
    data = _json()
    folder = library.clean_folder(data.get("folder"))
    kind = data.get("kind")
    recursive = bool(data.get("recursive"))
    if kind not in KINDS:
        return _error("Select a valid programme type.")
    database = get_db()
    condition, params = _folder_match(folder, recursive)
    updated = database.execute(f"UPDATE tv_media SET kind=? WHERE {condition}", (kind, *params)).rowcount
    if data.get("remember"):
        database.execute("INSERT INTO tv_folder_kinds(folder,kind,recursive) VALUES(?,?,?) "
                         "ON CONFLICT(folder) DO UPDATE SET kind=excluded.kind,recursive=excluded.recursive",
                         (folder, kind, int(recursive)))
    audit(database, g.user["id"], None, "tv_folder_kind", target_type="tv_media",
          note=f"{folder or '(no folder)'}{'/*' if recursive else ''} = {kind} ({updated} items)")
    bump_version(database)
    database.commit()
    return jsonify(updated=updated)


@admin_bp.post("/api/folders/rename")
def rename_folder():
    data = _json()
    source = library.clean_folder(data.get("from"))
    target = library.clean_folder(data.get("to"))
    if not source:
        return _error("Choose a folder to rename.")
    if source == target:
        return jsonify(updated=0)
    if target.startswith(source + "/"):
        return _error("A folder cannot be moved inside itself.")
    database = get_db()

    def renamed(value):
        return (target + value[len(source):]).lstrip("/")[:300]

    condition, params = _folder_match(source, True)
    rows = database.execute(f"SELECT id,folder FROM tv_media WHERE {condition}", params).fetchall()
    database.executemany("UPDATE tv_media SET folder=? WHERE id=?", [(renamed(row[1]), row[0]) for row in rows])
    rules = database.execute(f"SELECT folder,kind,recursive FROM tv_folder_kinds WHERE {condition}", params).fetchall()
    database.execute(f"DELETE FROM tv_folder_kinds WHERE {condition}", params)
    database.executemany("INSERT OR REPLACE INTO tv_folder_kinds(folder,kind,recursive) VALUES(?,?,?)",
                         [(renamed(rule[0]), rule[1], rule[2]) for rule in rules])
    audit(database, g.user["id"], None, "tv_folder_rename", target_type="tv_media",
          note=f"{source} -> {target or '(no folder)'} ({len(rows)} items)")
    database.commit()
    return jsonify(updated=len(rows))


@admin_bp.post("/api/folders/rule/delete")
def delete_folder_rule():
    database = get_db()
    database.execute("DELETE FROM tv_folder_kinds WHERE folder=?", (library.clean_folder(_json().get("folder")),))
    database.commit()
    return jsonify(ok=True)


@admin_bp.post("/api/media/recheck")
def recheck_media():
    database = get_db()
    missing = 0
    for row in database.execute("SELECT id,rel_path FROM tv_media").fetchall():
        path = library.resolve(row["rel_path"])
        absent = path is None or not path.is_file()
        missing += absent
        database.execute("UPDATE tv_media SET missing=? WHERE id=?", (int(absent), row["id"]))
    bump_version(database)
    database.commit()
    return jsonify(missing=missing)


@admin_bp.post("/api/uploads")
def create_upload():
    data = _json()
    name = str(data.get("name", "")).replace("\\", "/").rsplit("/", 1)[-1].strip()
    try:
        size = int(data.get("size"))
    except (TypeError, ValueError):
        return _error("Invalid file size.")
    if not name or len(name) > 255:
        return _error("Invalid file name.")
    if not library.supported(name):
        return jsonify(status="unsupported", message="Unsupported format.")
    limit = g.station["tv_max_upload_mb"] * 1024 * 1024
    if size <= 0 or size > limit:
        return jsonify(status="failed", message=f"File exceeds the {g.station['tv_max_upload_mb']} MB limit."
                       if size > 0 else "File is empty.")
    database = get_db()
    duplicate = database.execute(
        "SELECT id,title FROM tv_media WHERE (original_name=? AND size_bytes=?) OR (fingerprint<>'' AND fingerprint=?)",
        (name, size, str(data.get("fingerprint") or "-"))).fetchone()
    if duplicate:
        return jsonify(status="duplicate", message=f"Already in library as “{duplicate['title']}”.")
    library.purge_stale_uploads()
    folder = library.clean_folder(str(data.get("relpath", "")).replace("\\", "/").rpartition("/")[0])
    upload_id = library.create_session({"name": name, "size": size, "folder": folder})
    return jsonify(status="uploading", upload_id=upload_id, offset=0, chunk_bytes=library.CHUNK_BYTES)


@admin_bp.get("/api/uploads/<upload_id>")
def upload_status(upload_id):
    meta, part = library.load_session(upload_id)
    if meta is None:
        return _error("Upload session expired.", 404)
    return jsonify(offset=part.stat().st_size, size=meta["size"])


@admin_bp.put("/api/uploads/<upload_id>")
def upload_chunk(upload_id):
    meta, part = library.load_session(upload_id)
    if meta is None:
        return _error("Upload session expired.", 404)
    offset = request.args.get("offset", type=int)
    data = request.get_data(cache=False)
    if offset is None or not data or len(data) > library.CHUNK_BYTES or offset + len(data) > meta["size"]:
        return _error("Invalid chunk.")
    size = library.append_chunk(part, offset, data)
    if size is None:
        return _error("Offset mismatch.", 409, offset=part.stat().st_size)
    if size < meta["size"]:
        return jsonify(status="uploading", offset=size)
    return jsonify(_finish_upload(upload_id, meta, part))


def _finish_upload(upload_id, meta, part):
    if not library.valid_signature(part):
        library.discard_session(upload_id)
        return {"status": "unsupported", "message": "Not a playable MP4, WebM or Ogg video."}
    fingerprint = library.fingerprint(part)
    database = get_db()
    duplicate = database.execute("SELECT title FROM tv_media WHERE fingerprint=?", (fingerprint,)).fetchone()
    if duplicate:
        library.discard_session(upload_id)
        return {"status": "duplicate", "message": f"Already in library as “{duplicate['title']}”."}
    rel_path = library.store_upload(part, meta["name"])
    library.discard_session(upload_id)
    path = library.resolve(rel_path)
    try:
        media_id = _insert_media(database, rel_path, "upload", meta["name"], meta["folder"], path, fingerprint)
        database.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"status": "ok", "media": _media_json(_media_row(media_id))}


@admin_bp.post("/api/uploads/<upload_id>/cancel")
def cancel_upload(upload_id):
    library.discard_session(upload_id)
    return jsonify(ok=True)


@admin_bp.post("/api/import/scan")
def import_scan():
    try:
        cursor = max(0, int(_json().get("cursor", 0)))
    except (TypeError, ValueError):
        cursor = 0
    candidates = library.import_candidates()
    base = library.folder("import").resolve()
    database = get_db()
    results = []
    for rel in candidates[cursor:cursor + library.IMPORT_BATCH]:
        rel_path = f"import/{rel}"
        name = rel.rsplit("/", 1)[-1]
        if not library.supported(name):
            results.append({"path": rel, "status": "unsupported"})
            continue
        path = library.resolve(rel_path)
        if path is None or not path.is_relative_to(base) or not path.is_file():
            results.append({"path": rel, "status": "skipped", "message": "Outside the import folder."})
            continue
        known = database.execute("SELECT id,missing FROM tv_media WHERE rel_path=?", (rel_path,)).fetchone()
        if known:
            if known["missing"]:
                database.execute("UPDATE tv_media SET missing=0 WHERE id=?", (known["id"],))
                bump_version(database)
            results.append({"path": rel, "status": "known"})
            continue
        try:
            if not library.valid_signature(path):
                results.append({"path": rel, "status": "unsupported"})
                continue
            fingerprint = library.fingerprint(path)
            duplicate = database.execute("SELECT title FROM tv_media WHERE fingerprint=?", (fingerprint,)).fetchone()
            if duplicate:
                results.append({"path": rel, "status": "duplicate", "message": duplicate["title"]})
                continue
            folder = library.clean_folder(rel.rpartition("/")[0])
            _insert_media(database, rel_path, "import", name, folder, path, fingerprint)
            results.append({"path": rel, "status": "ok"})
        except OSError as exc:
            results.append({"path": rel, "status": "failed", "message": exc.strerror or "Read error."})
    database.commit()
    following = cursor + library.IMPORT_BATCH
    return jsonify(results=results, cursor=following, total=len(candidates), done=following >= len(candidates))


# Scheduling --------------------------------------------------------------

def _block_json(row, items, today=None, zone=None):
    entries = [{"media_id": item["id"], "title": item["title_override"] or item["title"],
                "media_title": item["title"], "title_override": item["title_override"],
                "description": item["item_description"], "duration": item["duration_s"],
                "available": _media_available(item)} for item in items]
    data = {key: row[key] for key in ("id", "channel_id", "title", "description", "kind", "start_date", "weekdays",
                                      "start_time", "loop_until", "valid_from", "valid_until", "enabled",
                                      "origin", "locked")}
    data["items"] = entries
    data["total_duration"] = sum(item["duration"] or 0 for item in entries)
    if today is not None:
        engine = schedule.Block(row["id"], row["title"], row["kind"], row["start_time"], (), "",
                                row["start_date"], row["weekdays"], row["loop_until"],
                                row["valid_from"], row["valid_until"])
        data["airs_on_day"] = bool(row["enabled"]) and schedule.airs_on(engine, today)
    return data


@admin_bp.get("/api/schedule")
def day_schedule():
    database = get_db()
    channel = database.execute("SELECT * FROM tv_channels WHERE id=?",
                               (request.args.get("channel", type=int),)).fetchone()
    if channel is None:
        return _error("No such channel.", 404)
    try:
        day = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        day = _now().astimezone(_zone()).date()
    zone = _zone()
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(UTC)
    programmed, conflicts = schedule.resolve(_engine_blocks(channel["id"]), start, end, zone)
    gap_mode, filler, offair = _gap_sources(channel)
    airings = schedule.fill_gaps(programmed, start, end, gap_mode, filler, offair)
    rows, items = _blocks(channel["id"], include_disabled=True)
    # Generated entries are numerous; list only the ones starting on this day.
    shown = [row for row in rows if row["origin"] != "auto" or row["start_date"] == day.isoformat()]
    return jsonify(
        auto_hidden=len(rows) - len(shown),
        timezone=str(zone), date=day.isoformat(), start=_iso(start), end=_iso(end),
        airings=[_airing_json(airing) for airing in schedule.merge(airings)],
        conflicts=[{"block": c.block_title, "preempted": c.preempted_title,
                    "start": _iso(c.start), "end": _iso(c.end)} for c in conflicts],
        blocks=[_block_json(row, items.get(row["id"], ()), day, zone) for row in shown])


def _valid_day(value):
    try:
        return date.fromisoformat(value).isoformat() if value else None
    except (TypeError, ValueError):
        return False


def _block_payload(data):
    database = get_db()
    channel_id = data.get("channel_id")
    if not isinstance(channel_id, int) or not database.execute(
            "SELECT 1 FROM tv_channels WHERE id=?", (channel_id,)).fetchone():
        return None, "Select a channel."
    kind = data.get("kind")
    start_time = str(data.get("start_time", ""))
    loop_until = str(data.get("loop_until") or "") or None
    start_date, valid_from, valid_until = (_valid_day(data.get(key)) for key in ("start_date", "valid_from", "valid_until"))
    try:
        weekdays = int(data.get("weekdays") or 0)
    except (TypeError, ValueError):
        weekdays = -1
    if kind not in {"once", "weekly"}:
        return None, "Choose a one-off or weekly block."
    if not TIME_RE.fullmatch(start_time):
        return None, "Enter a start time as HH:MM."
    if loop_until and not TIME_RE.fullmatch(loop_until):
        return None, "Enter the repeat-until time as HH:MM."
    if False in (start_date, valid_from, valid_until):
        return None, "Enter valid dates."
    if kind == "once" and not start_date:
        return None, "One-off blocks need an air date."
    if kind == "weekly" and not 1 <= weekdays <= 127:
        return None, "Select at least one weekday."
    if valid_from and valid_until and valid_from > valid_until:
        return None, "The valid-from date must come before the valid-until date."
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 500:
        return None, "Add between 1 and 500 programmes."
    items, total = [], 0.0
    for raw in raw_items:
        media = _media_row(raw.get("media_id")) if isinstance(raw, dict) and isinstance(raw.get("media_id"), int) else None
        if media is None:
            return None, "A selected programme no longer exists."
        if media["duration_s"] is None:
            return None, f"“{media['title']}” has no known duration yet. Open the library to measure it."
        override = str(raw.get("title_override") or "").strip()[:150] or None
        description = str(raw.get("description") or "").strip()[:2000]
        items.append((media["id"], override, description))
        total += media["duration_s"]
    if not loop_until and total > schedule.MAX_BLOCK.total_seconds():
        return None, "A block cannot run longer than 24 hours. Split it into several blocks."
    first = _media_row(items[0][0])
    title = str(data.get("title", "")).strip() or items[0][1] or first["title"]
    description = str(data.get("description", "")).strip()
    if len(title) > 150 or len(description) > 2000:
        return None, "Block title or description is too long."
    return {"channel_id": channel_id, "title": title, "description": description, "kind": kind,
            "start_date": start_date if kind == "once" else None,
            "weekdays": weekdays if kind == "weekly" else 0, "start_time": start_time,
            "loop_until": loop_until, "valid_from": valid_from if kind == "weekly" else None,
            "valid_until": valid_until if kind == "weekly" else None,
            "enabled": int(bool(data.get("enabled", True))), "items": items}, None


def _save_items(database, block_id, items):
    database.execute("DELETE FROM tv_block_items WHERE block_id=?", (block_id,))
    database.executemany(
        "INSERT INTO tv_block_items(block_id,position,media_id,title_override,description) VALUES(?,?,?,?,?)",
        [(block_id, index, *item) for index, item in enumerate(items)])


BLOCK_FIELDS = ("channel_id", "title", "description", "kind", "start_date", "weekdays", "start_time",
                "loop_until", "valid_from", "valid_until", "enabled")


@admin_bp.post("/api/blocks")
def create_block():
    values, error = _block_payload(_json())
    if error:
        return _error(error)
    database = get_db()
    block_id = database.execute(
        f"INSERT INTO tv_blocks({','.join(BLOCK_FIELDS)}) VALUES({','.join('?' * len(BLOCK_FIELDS))})",
        tuple(values[key] for key in BLOCK_FIELDS)).lastrowid
    _save_items(database, block_id, values["items"])
    bump_version(database)
    database.commit()
    return jsonify(id=block_id)


@admin_bp.post("/api/blocks/<int:block_id>")
def update_block(block_id):
    database = get_db()
    if not database.execute("SELECT 1 FROM tv_blocks WHERE id=?", (block_id,)).fetchone():
        return _error("No such block.", 404)
    values, error = _block_payload(_json())
    if error:
        return _error(error)
    # A hand-edited generated entry is locked so the next automatic run keeps it.
    database.execute(f"UPDATE tv_blocks SET {','.join(f'{key}=?' for key in BLOCK_FIELDS)},"
                     "locked=CASE WHEN origin='auto' THEN 1 ELSE locked END WHERE id=?",
                     (*(values[key] for key in BLOCK_FIELDS), block_id))
    _save_items(database, block_id, values["items"])
    bump_version(database)
    database.commit()
    return jsonify(ok=True)


@admin_bp.post("/api/blocks/<int:block_id>/delete")
def delete_block(block_id):
    database = get_db()
    database.execute("DELETE FROM tv_blocks WHERE id=?", (block_id,))
    bump_version(database)
    database.commit()
    return jsonify(ok=True)


@admin_bp.post("/api/blocks/bulk")
def bulk_blocks():
    data = _json()
    ids, error = _ids(data, "tv_blocks")
    if error:
        return _error(error)
    action = data.get("action")
    statements = {"enable": "UPDATE tv_blocks SET enabled=1", "disable": "UPDATE tv_blocks SET enabled=0",
                  "lock": "UPDATE tv_blocks SET locked=1", "unlock": "UPDATE tv_blocks SET locked=0",
                  "delete": "DELETE FROM tv_blocks"}
    if action not in statements:
        return _error("Choose enable, disable, lock, unlock or delete.")
    database = get_db()
    for chunk in _chunks(ids):
        database.execute(f"{statements[action]} WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    bump_version(database)
    database.commit()
    return jsonify(updated=len(ids))


@admin_bp.post("/api/blocks/copy-day")
def copy_day():
    data = _json()
    source = _valid_day(data.get("from_date"))
    targets = data.get("to_dates")
    if not source or not isinstance(targets, list) or not 1 <= len(targets) <= 62:
        return _error("Choose a source day and up to 62 target days.")
    targets = [_valid_day(value) for value in targets]
    if not all(targets):
        return _error("Enter valid target dates.")
    database = get_db()
    blocks = database.execute("SELECT * FROM tv_blocks WHERE channel_id=? AND kind='once' AND start_date=?",
                              (data.get("channel_id"), source)).fetchall()
    copied = 0
    for target in targets:
        if target == source:
            continue
        for block in blocks:
            values = dict(block) | {"start_date": target}
            new_id = database.execute(
                f"INSERT INTO tv_blocks({','.join(BLOCK_FIELDS)}) VALUES({','.join('?' * len(BLOCK_FIELDS))})",
                tuple(values[key] for key in BLOCK_FIELDS)).lastrowid
            database.execute(
                "INSERT INTO tv_block_items(block_id,position,media_id,title_override,description) "
                "SELECT ?,position,media_id,title_override,description FROM tv_block_items WHERE block_id=?",
                (new_id, block["id"]))
            copied += 1
    bump_version(database)
    database.commit()
    return jsonify(copied=copied)


# Auto scheduler ----------------------------------------------------------

AUTO_HISTORY_DAYS = 14
AUTO_PRUNE_DAYS = 60


def _channel_row(channel_id):
    return get_db().execute("SELECT * FROM tv_channels WHERE id=?", (channel_id,)).fetchone()


def _auto_config(channel_id):
    row = get_db().execute("SELECT settings FROM tv_auto_config WHERE channel_id=?", (channel_id,)).fetchone()
    try:
        stored = json.loads(row["settings"]) if row else {}
    except ValueError:
        stored = {}
    config, error = auto.normalize_config(stored)
    return config if not error else auto.normalize_config({})[0]


def _auto_library():
    """Scheduler inputs from the library: (programmes, interstitial clips, rows left out)."""
    programs, clips, excluded = [], [], 0
    for row in get_db().execute("SELECT * FROM tv_media WHERE kind IN ('program','ident','bumper','commercial') "
                                "ORDER BY id"):
        if not row["duration_s"] or not _media_available(row):
            excluded += 1
        elif row["kind"] == "program":
            programs.append(auto.Program(row["id"], row["title"], row["duration_s"], row["content_type"] or "other",
                                         row["genre"], row["series"], row["season"], row["episode"],
                                         row["description"]))
        else:
            clips.append(auto.Clip(row["id"], row["title"], row["duration_s"], row["kind"]))
    return programs, clips, excluded


def _on_air_spans(config, first_day, days, zone):
    """One (start, end) per day for the channel's broadcasting hours; equal times mean all day."""
    spans = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        until_day = day if config["on_air_until"] > config["on_air_from"] else day + timedelta(days=1)
        spans.append((schedule.local_moment(day, config["on_air_from"], zone),
                      schedule.local_moment(until_day, config["on_air_until"], zone)))
    return spans


def _replaceable(channel_id, spans, zone, now):
    """Unlocked generated blocks a run over ``spans`` replaces; anything already on air stays."""
    first = spans[0][0].astimezone(zone).date() - timedelta(days=1)
    last = spans[-1][1].astimezone(zone).date()
    rows = get_db().execute(
        "SELECT id,start_date,start_time FROM tv_blocks WHERE channel_id=? AND origin='auto' AND locked=0 "
        "AND kind='once' AND start_date BETWEEN ? AND ?", (channel_id, first.isoformat(), last.isoformat()))
    found = []
    for row in rows:
        moment = schedule.local_moment(schedule.parse_day(row["start_date"]), row["start_time"], zone)
        if moment >= now and any(start <= moment < end for start, end in spans):
            found.append(row["id"])
    return found


def _free_windows(spans, programmed, now):
    """Broadcast time not taken by fixed programming, from now on."""
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    windows = []
    for start, end in merged:
        cursor = max(start, now)
        for airing in programmed:
            if airing.end <= cursor or airing.start >= end:
                continue
            if airing.start > cursor:
                windows.append((cursor, airing.start))
            cursor = max(cursor, airing.end)
        if cursor < end:
            windows.append((cursor, end))
    return windows


def _slot_json(slot, zone):
    local_start, local_end = slot.start.astimezone(zone), slot.end.astimezone(zone)
    return {"start": _iso(slot.start), "end": _iso(slot.end), "date": local_start.date().isoformat(),
            "start_time": local_start.strftime("%H:%M"),
            "loop_until": local_end.strftime("%H:%M") if slot.fill else None,
            "title": slot.title[:150], "content_type": slot.content_type, "genre": slot.genre,
            "series": slot.series, "rerun": slot.rerun, "interlude": slot.interlude,
            "items": [list(item) for item in slot.items]}


def _generate_draft(channel, first_day, days, config, seed):
    zone, now = _zone(), _now()
    spans = _on_air_spans(config, first_day, days, zone)
    replaced = _replaceable(channel["id"], spans, zone, now)
    blocks = _engine_blocks(channel["id"])
    fixed = [block for block in blocks if block.id not in set(replaced)]
    window_start, window_end = spans[0][0], max(end for _start, end in spans)
    programmed, _conflicts = schedule.resolve(fixed, window_start, window_end, zone)
    windows = _free_windows(spans, programmed, now)
    programs, clips, excluded = _auto_library()
    history_end = max(window_start, now)
    past, _conflicts = schedule.resolve(blocks, history_end - timedelta(days=AUTO_HISTORY_DAYS), history_end, zone)
    history = [(airing.start, airing.media_id) for airing in past
               if airing.media_id is not None and not airing.interstitial]
    series_of = {program.media_id: program.series for program in programs if program.series}
    pointer = {}
    for _aired, media_id in sorted(history):
        if media_id in series_of:
            pointer[series_of[media_id]] = media_id
    slots, warnings = auto.generate(windows, programs, clips, config, zone, history, pointer, seed)
    if excluded:
        warnings.append(f"{excluded} library item(s) left out: unknown length or missing file.")
    if replaced:
        warnings.append(f"Publishing replaces {len(replaced)} earlier generated entries in this range. "
                        "Locked and manual programming is kept.")
    return {"slots": [_slot_json(slot, zone) for slot in slots], "replaced": replaced}, warnings


def _auto_preview(channel, draft):
    """Draft slots alongside the fixed programming they were fitted around, for the guide preview."""
    database, zone = get_db(), _zone()
    payload = json.loads(draft["payload"])
    first_day = date.fromisoformat(draft["start_date"])
    config = _auto_config(channel["id"])
    spans = _on_air_spans(config, first_day, draft["days"], zone)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=zone).astimezone(UTC)
    end = max(spans[-1][1], datetime.combine(first_day + timedelta(days=draft["days"]), datetime.min.time(),
                                             tzinfo=zone).astimezone(UTC))
    replaced = set(payload["replaced"])
    fixed_blocks = [block for block in _engine_blocks(channel["id"]) if block.id not in replaced]
    programmed, _conflicts = schedule.resolve(fixed_blocks, start, end, zone)
    origin = {row["id"]: (row["origin"], row["locked"]) for row in database.execute(
        "SELECT id,origin,locked FROM tv_blocks WHERE channel_id=?", (channel["id"],))}
    fixed = []
    for airing in schedule.fold_interstitials(programmed):
        if fixed and fixed[-1]["block_id"] == airing.block_id and fixed[-1]["end"] == airing.start:
            fixed[-1]["end"] = airing.end
            continue
        block_origin, locked = origin.get(airing.block_id, ("manual", 0))
        fixed.append({"block_id": airing.block_id, "title": airing.block_title or airing.title,
                      "start": airing.start, "end": airing.end,
                      "status": "locked" if block_origin == "auto" and locked else block_origin})
    media = {row["id"]: row for row in database.execute("SELECT id,title,kind,duration_s FROM tv_media")}
    slots = []
    for slot in payload["slots"]:
        items = [{"title": override or (media[media_id]["title"] if media_id in media else "(deleted)"),
                  "kind": media[media_id]["kind"] if media_id in media else "program",
                  "duration": media[media_id]["duration_s"] if media_id in media else None}
                 for media_id, override, _description in slot["items"]]
        slots.append({key: slot[key] for key in ("start", "end", "title", "content_type", "genre", "series",
                                                 "rerun", "interlude")} | {"items": items})
    return {"start_date": draft["start_date"], "days": draft["days"], "timezone": str(zone),
            "start": _iso(start), "end": _iso(end), "created_at": draft["created_at"],
            "stale": draft["version"] != version(), "warnings": json.loads(draft["warnings"]),
            "slots": slots, "replaced": len(replaced),
            "fixed": [{"title": entry["title"], "status": entry["status"], "start": _iso(entry["start"]),
                       "end": _iso(entry["end"])} for entry in fixed]}


def _draft_row(channel_id):
    return get_db().execute("SELECT * FROM tv_auto_drafts WHERE channel_id=?", (channel_id,)).fetchone()


def _library_stats():
    database = get_db()
    types = {}
    for row in database.execute(
            "SELECT content_type,COUNT(*) FROM tv_media WHERE kind='program' AND duration_s IS NOT NULL "
            "AND missing=0 GROUP BY content_type"):
        types[row[0] or "other"] = types.get(row[0] or "other", 0) + row[1]
    clips = {row[0]: row[1] for row in database.execute(
        "SELECT kind,COUNT(*) FROM tv_media WHERE kind IN ('ident','bumper','commercial') "
        "AND duration_s IS NOT NULL AND missing=0 GROUP BY kind")}
    series = [{"name": row[0], "episodes": row[1]} for row in database.execute(
        "SELECT series,COUNT(*) FROM tv_media WHERE kind='program' AND series<>'' "
        "GROUP BY series COLLATE NOCASE ORDER BY series COLLATE NOCASE")]
    genres = [row[0] for row in database.execute(
        "SELECT DISTINCT genre FROM tv_media WHERE genre<>'' ORDER BY genre COLLATE NOCASE")]
    unmeasured = database.execute("SELECT COUNT(*) FROM tv_media WHERE duration_s IS NULL").fetchone()[0]
    return {"types": types, "clips": clips, "series": series, "genres": genres, "unmeasured": unmeasured}


@admin_bp.get("/api/auto/<int:channel_id>")
def auto_state(channel_id):
    channel = _channel_row(channel_id)
    if channel is None:
        return _error("No such channel.", 404)
    draft = _draft_row(channel_id)
    return jsonify(config=_auto_config(channel_id), defaults=auto.DEFAULT_CONFIG, content_types=CONTENT_TYPES,
                   library=_library_stats(), timezone=str(_zone()),
                   today=_now().astimezone(_zone()).date().isoformat(),
                   draft=_auto_preview(channel, draft) if draft else None)


@admin_bp.post("/api/auto/<int:channel_id>/config")
def auto_save_config(channel_id):
    if _channel_row(channel_id) is None:
        return _error("No such channel.", 404)
    config, error = auto.normalize_config(_json())
    if error:
        return _error(error)
    database = get_db()
    database.execute("INSERT INTO tv_auto_config(channel_id,settings,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
                     "ON CONFLICT(channel_id) DO UPDATE SET settings=excluded.settings,updated_at=excluded.updated_at",
                     (channel_id, json.dumps(config)))
    database.commit()
    return jsonify(config=config)


@admin_bp.post("/api/auto/<int:channel_id>/generate")
def auto_generate(channel_id):
    channel = _channel_row(channel_id)
    if channel is None:
        return _error("No such channel.", 404)
    data = _json()
    config = _auto_config(channel_id)
    first_day = _valid_day(data.get("start_date"))
    if first_day is False:
        return _error("Enter a valid start date.")
    first_day = date.fromisoformat(first_day) if first_day else _now().astimezone(_zone()).date()
    try:
        days = int(data.get("days") or config["days"])
    except (TypeError, ValueError):
        days = 0
    if not 1 <= days <= auto.MAX_DAYS:
        return _error(f"Generate between 1 and {auto.MAX_DAYS} days.")
    seed = secrets.randbelow(2 ** 31)
    payload, warnings = _generate_draft(channel, first_day, days, config, seed)
    database = get_db()
    database.execute("INSERT OR REPLACE INTO tv_auto_drafts(channel_id,start_date,days,seed,version,payload,warnings) "
                     "VALUES(?,?,?,?,?,?,?)", (channel_id, first_day.isoformat(), days, seed, version(),
                                               json.dumps(payload), json.dumps(warnings)))
    database.commit()
    return jsonify(draft=_auto_preview(channel, _draft_row(channel_id)))


@admin_bp.post("/api/auto/<int:channel_id>/publish")
def auto_publish(channel_id):
    channel = _channel_row(channel_id)
    draft = _draft_row(channel_id) if channel else None
    if draft is None:
        return _error("Generate a schedule first.", 404)
    if draft["version"] != version():
        return _error("The schedule or library changed after this preview was generated. Regenerate it first.", 409)
    payload = json.loads(draft["payload"])
    database = get_db()
    for chunk in _chunks(payload["replaced"]):
        database.execute(f"DELETE FROM tv_blocks WHERE origin='auto' AND locked=0 AND id IN "
                         f"({','.join('?' * len(chunk))})", chunk)
    for slot in payload["slots"]:
        block_id = database.execute(
            "INSERT INTO tv_blocks(channel_id,title,description,kind,start_date,weekdays,start_time,loop_until,"
            "enabled,origin,locked) VALUES(?,?,?,'once',?,0,?,?,1,'auto',0)",
            (channel_id, slot["title"], "Encore presentation" if slot["rerun"] else "", slot["date"],
             slot["start_time"], slot["loop_until"])).lastrowid
        _save_items(database, block_id, [tuple(item) for item in slot["items"]])
    cutoff = (_now().astimezone(_zone()).date() - timedelta(days=AUTO_PRUNE_DAYS)).isoformat()
    database.execute("DELETE FROM tv_blocks WHERE channel_id=? AND origin='auto' AND locked=0 AND kind='once' "
                     "AND start_date<?", (channel_id, cutoff))
    database.execute("DELETE FROM tv_auto_drafts WHERE channel_id=?", (channel_id,))
    audit(database, g.user["id"], None, "tv_auto_publish", target_type="tv_channel", target_id=channel_id,
          note=f"{draft['start_date']} +{draft['days']}d: {len(payload['slots'])} entries, "
               f"{len(payload['replaced'])} replaced")
    bump_version(database)
    database.commit()
    return jsonify(published=len(payload["slots"]), replaced=len(payload["replaced"]))


@admin_bp.post("/api/auto/<int:channel_id>/discard")
def auto_discard(channel_id):
    database = get_db()
    database.execute("DELETE FROM tv_auto_drafts WHERE channel_id=?", (channel_id,))
    database.commit()
    return jsonify(ok=True)


@admin_bp.post("/api/auto/<int:channel_id>/clear")
def auto_clear(channel_id):
    """Remove unlocked generated programming from a range, keeping manual and locked entries."""
    if _channel_row(channel_id) is None:
        return _error("No such channel.", 404)
    data = _json()
    first_day = _valid_day(data.get("start_date"))
    try:
        days = int(data.get("days"))
    except (TypeError, ValueError):
        days = 0
    if not first_day or not 1 <= days <= 366:
        return _error("Choose a start date and between 1 and 366 days.")
    zone, first_day = _zone(), date.fromisoformat(first_day)
    spans = [(datetime.combine(first_day, datetime.min.time(), tzinfo=zone).astimezone(UTC),
              datetime.combine(first_day + timedelta(days=days), datetime.min.time(), tzinfo=zone).astimezone(UTC))]
    ids = _replaceable(channel_id, spans, zone, _now())
    database = get_db()
    for chunk in _chunks(ids):
        database.execute(f"DELETE FROM tv_blocks WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    audit(database, g.user["id"], None, "tv_auto_clear", target_type="tv_channel", target_id=channel_id,
          note=f"{first_day.isoformat()} +{days}d: {len(ids)} entries")
    bump_version(database)
    database.commit()
    return jsonify(removed=len(ids))


@admin_bp.post("/api/blocks/<int:block_id>/lock")
def lock_block(block_id):
    database = get_db()
    if not database.execute("SELECT 1 FROM tv_blocks WHERE id=?", (block_id,)).fetchone():
        return _error("No such block.", 404)
    database.execute("UPDATE tv_blocks SET locked=? WHERE id=?", (int(bool(_json().get("locked"))), block_id))
    database.commit()
    return jsonify(ok=True)
