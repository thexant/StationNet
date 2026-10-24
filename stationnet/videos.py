"""Station video library and uploads."""
from pathlib import Path
import secrets
import subprocess

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, send_file, url_for)

from .auth import login_required
from .db import get_db
from .moderation import audit
from .security import validate_csrf

bp = Blueprint("videos", __name__, url_prefix="/videos")
FORMATS = {".mp4": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg"}
MAX_VIDEO_BYTES = 120 * 1024 * 1024


@bp.before_request
def require_enabled():
    if not g.station or not g.station["videos_enabled"]:
        abort(404)


def _video_path(item):
    root = Path(current_app.config["DATA_DIR"]) / "videos"
    folder = root / ("station" if item["station_post"] else f"users/{item['uploader_id']}")
    return folder / item["stored_name"]


def _video(video_id):
    item = get_db().execute(
        "SELECT v.*,u.username FROM videos v JOIN users u ON u.id=v.uploader_id WHERE v.id=?",
        (video_id,),
    ).fetchone()
    if item is None:
        abort(404)
    return item


def _valid_signature(suffix, header):
    return (suffix == ".mp4" and len(header) >= 12 and header[4:8] == b"ftyp"
            or suffix == ".webm" and header.startswith(b"\x1a\x45\xdf\xa3")
            or suffix == ".ogv" and header.startswith(b"OggS"))


def _quota_remaining(database):
    row = database.execute(
        "SELECT COALESCE(u.videos_quota_mb,s.videos_default_quota_mb) quota_mb,"
        "(SELECT COALESCE(SUM(size_bytes),0) FROM videos "
        "WHERE uploader_id=u.id AND station_post=0) used_bytes "
        "FROM users u CROSS JOIN station s WHERE u.id=?", (g.user["id"],)
    ).fetchone()
    return row["quota_mb"] * 1024 * 1024 - row["used_bytes"]


@bp.get("")
def index():
    query = request.args.get("q", "").strip()[:100]
    page = max(1, request.args.get("page", 1, type=int) or 1)
    database = get_db()
    where = "WHERE instr(lower(v.title), lower(?)) > 0" if query else ""
    params = (query,) if query else ()
    total = database.execute(f"SELECT COUNT(*) FROM videos v {where}", params).fetchone()[0]
    pages = max(1, (total + 19) // 20)
    if page > pages:
        abort(404)
    items = database.execute(
        f"SELECT v.*,u.username FROM videos v JOIN users u ON u.id=v.uploader_id "
        f"{where} ORDER BY v.created_at DESC,v.id DESC LIMIT 20 OFFSET ?",
        (*params, (page - 1) * 20),
    ).fetchall()
    return render_template("videos/index.html", videos=items, query=query, page=page,
                           pages=pages,
                           thumbnail_ids={item["id"] for item in items
                                          if _video_path(item).with_suffix(".jpg").is_file()})


def _save_upload(station_post):
    uploaded = request.files.get("file")
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    suffix = Path(uploaded.filename or "").suffix.lower() if uploaded else ""
    if not 1 <= len(title) <= 150:
        return "Title must be between 1 and 150 characters."
    if len(description) > 5000:
        return "Description cannot exceed 5000 characters."
    if not uploaded or suffix not in FORMATS:
        return "Select an MP4, WebM, or Ogg video."
    header = uploaded.stream.read(16)
    if not _valid_signature(suffix, header):
        return "The selected file is not a supported video."
    remaining = _quota_remaining(get_db()) if not station_post else None
    if not station_post and remaining < len(header):
        return "Video upload quota reached. Remove a video or ask a SYSOP to raise your quota."
    folder = Path(current_app.config["DATA_DIR"]) / "videos" / (
        "station" if station_post else f"users/{g.user['id']}")
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = secrets.token_hex(20) + suffix
    destination = folder / stored_name
    size = len(header)
    saved = False
    try:
        with destination.open("xb") as output:
            output.write(header)
            while True:
                chunk = uploaded.stream.read(min(1024 * 1024, MAX_VIDEO_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    return "Video exceeds the 120 MB limit."
                if not station_post and size > remaining:
                    return "Video upload quota exceeded. Remove a video or ask a SYSOP to raise your quota."
                output.write(chunk)
        thumbnail_path = destination.with_suffix(".jpg")
        try:
            thumbnail_result = subprocess.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", "0.1",
                 "-i", str(destination), "-frames:v", "1", "-vf", "scale=320:-2",
                 str(thumbnail_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False,
            )
            if thumbnail_result.returncode != 0:
                thumbnail_path.unlink(missing_ok=True)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            thumbnail_path.unlink(missing_ok=True)
        database = get_db()
        if not station_post:
            database.execute("BEGIN IMMEDIATE")
            if size > _quota_remaining(database):
                database.rollback()
                return "Video upload quota exceeded. Remove a video or ask a SYSOP to raise your quota."
        cursor = database.execute(
            "INSERT INTO videos(uploader_id,station_post,title,description,stored_name,mime_type,size_bytes) "
            "VALUES(?,?,?,?,?,?,?)",
            (g.user["id"], station_post, title, description, stored_name, FORMATS[suffix], size),
        )
        database.commit()
        saved = True
        return cursor.lastrowid
    except Exception:
        get_db().rollback()
        raise
    finally:
        if not saved:
            destination.unlink(missing_ok=True)
            destination.with_suffix(".jpg").unlink(missing_ok=True)


@bp.route("/upload", methods=("GET", "POST"))
@login_required
def upload():
    if g.station["videos_mode"] != "community":
        abort(404)
    if request.method == "POST":
        validate_csrf()
        result = _save_upload(False)
        if isinstance(result, int):
            flash("Video uploaded.", "success")
            return redirect(url_for("videos.view", video_id=result))
        flash(result, "error")
    return render_template("videos/upload.html", station_post=False)


@bp.post("/station-upload")
@login_required
def station_upload():
    if not g.user["is_admin"]:
        abort(403)
    validate_csrf()
    result = _save_upload(True)
    flash("Station video uploaded." if isinstance(result, int) else result,
          "success" if isinstance(result, int) else "error")
    return redirect(url_for("station.settings") + "#videos-service")


@bp.get("/<int:video_id>")
def view(video_id):
    item = _video(video_id)
    comments_enabled = g.station["videos_mode"] == "community" and not item["station_post"]
    comments = get_db().execute(
        "SELECT c.*,u.username FROM video_comments c JOIN users u ON u.id=c.author_id "
        "WHERE c.video_id=? ORDER BY c.id", (video_id,),
    ).fetchall() if comments_enabled else []
    return render_template("videos/view.html", video=item, comments=comments,
                           comments_enabled=comments_enabled)


@bp.get("/<int:video_id>/media")
def media(video_id):
    item = _video(video_id)
    path = _video_path(item)
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype=item["mime_type"], conditional=True,
                         download_name=item["stored_name"])
    response.headers["Content-Disposition"] = "inline"
    return response


@bp.get("/<int:video_id>/thumbnail")
def thumbnail(video_id):
    path = _video_path(_video(video_id)).with_suffix(".jpg")
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg", conditional=True)


@bp.post("/<int:video_id>/comments")
@login_required
def comment(video_id):
    validate_csrf()
    item = _video(video_id)
    if g.station["videos_mode"] != "community" or item["station_post"]:
        abort(404)
    body = request.form.get("body", "").strip()
    if not 1 <= len(body) <= 2000:
        flash("Comment must be between 1 and 2000 characters.", "error")
    else:
        database = get_db()
        database.execute("INSERT INTO video_comments(video_id,author_id,body) VALUES(?,?,?)",
                         (video_id, g.user["id"], body))
        database.commit()
        flash("Comment added.", "success")
    return redirect(url_for("videos.view", video_id=video_id) + "#comments")


@bp.post("/<int:video_id>/delete")
@login_required
def delete(video_id):
    validate_csrf()
    item = _video(video_id)
    if item["uploader_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    path = _video_path(item)
    try:
        path.unlink(missing_ok=True)
        path.with_suffix(".jpg").unlink(missing_ok=True)
    except OSError:
        flash("Video could not be removed from storage. Try again shortly.", "error")
        return redirect(url_for("videos.view", video_id=video_id))
    database = get_db()
    database.execute("DELETE FROM videos WHERE id=?", (video_id,))
    if item["uploader_id"] != g.user["id"]:
        audit(database, g.user["id"], item["uploader_id"], "delete_video",
              request.form.get("reason", "SYSOP removal")[:500], "video", video_id)
    database.commit()
    flash("Video removed.", "success")
    return redirect(url_for("videos.index"))


@bp.post("/comments/<int:comment_id>/delete")
@login_required
def delete_comment(comment_id):
    validate_csrf()
    database = get_db()
    comment = database.execute(
        "SELECT c.author_id,c.video_id,v.uploader_id FROM video_comments c "
        "JOIN videos v ON v.id=c.video_id WHERE c.id=?", (comment_id,)
    ).fetchone()
    if comment is None:
        abort(404)
    if comment["author_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    database.execute("DELETE FROM video_comments WHERE id=?", (comment_id,))
    if comment["author_id"] != g.user["id"]:
        audit(database, g.user["id"], comment["author_id"], "delete_video_comment",
              request.form.get("reason", "SYSOP removal")[:500], "video_comment", comment_id)
    database.commit()
    flash("Comment removed.", "success")
    return redirect(url_for("videos.view", video_id=comment["video_id"]) + "#comments")
