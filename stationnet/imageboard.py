"""Single station-wide imageboard."""
from io import BytesIO
from pathlib import Path
import secrets

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, send_from_directory, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError

from .auth import login_required
from .db import get_db
from .moderation import audit, active_restriction
from .realtime import utc_now
from .security import validate_csrf

bp = Blueprint("imageboard", __name__, url_prefix="/imageboard")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
PAGE_SIZE = 24


@bp.before_request
def require_enabled():
    if not g.station or not g.station["imageboard_enabled"]:
        abort(404)
    if g.user is None:
        return redirect(url_for("auth.login", next=request.path))


def can_moderate():
    if g.user is None:
        return False
    if g.user["is_admin"]:
        return True
    return get_db().execute(
        "SELECT 1 FROM radio_group_memberships m "
        "JOIN radio_permission_groups p ON p.id=m.group_id "
        "WHERE m.user_id=? AND p.moderate_imageboard=1 LIMIT 1",
        (g.user["id"],),
    ).fetchone() is not None


def _moderator_required():
    if not can_moderate():
        abort(403)


def _can_post():
    if active_restriction(g.user["id"], ("mute",)):
        abort(403, "Account muted")


def _image_dir():
    path = Path(current_app.config["DATA_DIR"]) / "imageboard"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_image(uploaded):
    payload = uploaded.read(MAX_IMAGE_BYTES + 1)
    if not payload or len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("Image must be between 1 byte and 10 MB.")
    try:
        with Image.open(BytesIO(payload)) as source:
            if source.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
                raise ValueError("Select a PNG, JPEG, GIF, or WebP image.")
            width, height = source.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise ValueError("Image dimensions are too large.")
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)
            image = image.convert("RGBA" if alpha else "RGB")
            image.load()
            thumb = image.copy()
            thumb.thumbnail((320, 320), Image.Resampling.LANCZOS)
            image_format = "PNG" if alpha else "JPEG"
            suffix = ".png" if alpha else ".jpg"
            mime = "image/png" if alpha else "image/jpeg"
            name = secrets.token_hex(20) + suffix
            thumb_name = secrets.token_hex(20) + ".jpg"
            folder = _image_dir()
            original = folder / name
            preview = folder / thumb_name
            try:
                image.save(original, format=image_format)
                if thumb.mode != "RGB":
                    background = Image.new("RGB", thumb.size, "#111111")
                    background.paste(thumb, mask=thumb.getchannel("A"))
                    thumb = background
                thumb.save(preview, format="JPEG", quality=85)
            except Exception:
                original.unlink(missing_ok=True)
                preview.unlink(missing_ok=True)
                raise
            return name, mime, thumb_name
    except ValueError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
        raise ValueError("Select a valid PNG, JPEG, GIF, or WebP image.") from None


def _remove_images(rows):
    folder = _image_dir()
    for row in rows:
        for key in ("image_name", "thumb_name"):
            name = row[key]
            if name and Path(name).name == name:
                try:
                    (folder / name).unlink(missing_ok=True)
                except PermissionError:
                    # Windows can keep a media response open after deletion.
                    pass


def _thread(thread_id):
    thread = get_db().execute(
        "SELECT t.*,u.username,u.callsign FROM imageboard_threads t "
        "JOIN users u ON u.id=t.author_id WHERE t.id=?", (thread_id,)
    ).fetchone()
    if thread is None:
        abort(404)
    return thread


@bp.get("")
def catalog():
    page = request.args.get("page", 1, type=int)
    if page is None or page < 1:
        abort(404)
    database = get_db()
    count = database.execute("SELECT COUNT(*) FROM imageboard_threads").fetchone()[0]
    pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        abort(404)
    threads = database.execute(
        "SELECT t.*,u.username,u.callsign,"
        "(SELECT COUNT(*) FROM imageboard_replies r WHERE r.thread_id=t.id) reply_count "
        "FROM imageboard_threads t JOIN users u ON u.id=t.author_id "
        "ORDER BY t.pinned DESC,t.bumped_at DESC,t.id DESC LIMIT ? OFFSET ?",
        (PAGE_SIZE, (page - 1) * PAGE_SIZE),
    ).fetchall()
    return render_template("imageboard/catalog.html", threads=threads, page=page, pages=pages,
                           can_moderate=can_moderate())


@bp.route("/new", methods=("GET", "POST"))
@login_required
def create():
    if request.method == "POST":
        validate_csrf()
        _can_post()
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()
        uploaded = request.files.get("image")
        if not 1 <= len(subject) <= 120:
            flash("Subject must be between 1 and 120 characters.", "error")
        elif len(body) > 5000:
            flash("Message cannot exceed 5000 characters.", "error")
        elif not uploaded or not uploaded.filename:
            flash("An image is required to start a thread.", "error")
        else:
            try:
                name, mime, thumb = _save_image(uploaded)
            except ValueError as error:
                flash(str(error), "error")
            else:
                database = get_db()
                try:
                    now = utc_now()
                    database.execute("BEGIN IMMEDIATE")
                    cursor = database.execute(
                        "INSERT INTO imageboard_threads(author_id,subject,body,image_name,image_type,thumb_name,created_at,bumped_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (g.user["id"], subject, body, name, mime, thumb, now, now),
                    )
                    excess = database.execute(
                        "SELECT MAX(0,COUNT(*)-?) FROM imageboard_threads",
                        (g.station["imageboard_max_threads"],),
                    ).fetchone()[0]
                    pruned = database.execute(
                        "SELECT id,image_name,thumb_name FROM imageboard_threads "
                        "ORDER BY created_at,id LIMIT ?", (excess,)
                    ).fetchall()
                    images = list(pruned)
                    for old in pruned:
                        images.extend(database.execute(
                            "SELECT image_name,thumb_name FROM imageboard_replies WHERE thread_id=?",
                            (old["id"],),
                        ).fetchall())
                        database.execute("DELETE FROM imageboard_threads WHERE id=?", (old["id"],))
                    database.commit()
                except Exception:
                    database.rollback()
                    _remove_images([{"image_name": name, "thumb_name": thumb}])
                    raise
                _remove_images(images)
                flash("Thread posted.", "success")
                return redirect(url_for("imageboard.thread", thread_id=cursor.lastrowid))
    return render_template("imageboard/new.html")


@bp.get("/<int:thread_id>")
def thread(thread_id):
    item = _thread(thread_id)
    replies = get_db().execute(
        "SELECT r.*,u.username,u.callsign FROM imageboard_replies r "
        "JOIN users u ON u.id=r.author_id WHERE r.thread_id=? ORDER BY r.id",
        (thread_id,),
    ).fetchall()
    return render_template("imageboard/thread.html", thread=item, replies=replies,
                           can_moderate=can_moderate())


@bp.post("/<int:thread_id>/reply")
@login_required
def reply(thread_id):
    validate_csrf()
    _can_post()
    item = _thread(thread_id)
    if item["locked"]:
        abort(403, "Thread locked")
    body = request.form.get("body", "").strip()
    uploaded = request.files.get("image")
    has_image = bool(uploaded and uploaded.filename)
    if (not body and not has_image) or len(body) > 5000:
        flash("Add a message or image; messages cannot exceed 5000 characters.", "error")
        return redirect(url_for("imageboard.thread", thread_id=thread_id) + "#reply-form")
    if has_image and get_db().execute(
        "SELECT COUNT(*) FROM imageboard_replies WHERE thread_id=? AND image_name IS NOT NULL",
        (thread_id,),
    ).fetchone()[0] >= g.station["imageboard_max_image_replies"]:
        flash("This thread has reached its image reply limit. You can still post a text reply.", "error")
        return redirect(url_for("imageboard.thread", thread_id=thread_id) + "#reply-form")
    name = mime = thumb = None
    if has_image:
        try:
            name, mime, thumb = _save_image(uploaded)
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("imageboard.thread", thread_id=thread_id) + "#reply-form")
    database = get_db()
    try:
        now = utc_now()
        database.execute("BEGIN IMMEDIATE")
        cursor = database.execute(
            "INSERT INTO imageboard_replies(thread_id,author_id,body,image_name,image_type,thumb_name,created_at) "
            "SELECT id,?,?,?,?,?,? FROM imageboard_threads WHERE id=? AND locked=0 "
            "AND (? IS NULL OR (SELECT COUNT(*) FROM imageboard_replies "
            "WHERE thread_id=? AND image_name IS NOT NULL) < ?)",
            (g.user["id"], body, name, mime, thumb, now, thread_id, name, thread_id,
             g.station["imageboard_max_image_replies"]),
        )
        if cursor.rowcount == 0:
            database.rollback()
            if name:
                _remove_images([{"image_name": name, "thumb_name": thumb}])
            if not get_db().execute("SELECT 1 FROM imageboard_threads WHERE id=? AND locked=0", (thread_id,)).fetchone():
                abort(403, "Thread locked")
            flash("This thread has reached its image reply limit. You can still post a text reply.", "error")
            return redirect(url_for("imageboard.thread", thread_id=thread_id) + "#reply-form")
        database.execute("UPDATE imageboard_threads SET bumped_at=? WHERE id=?", (now, thread_id))
        database.commit()
    except Exception:
        database.rollback()
        if name:
            _remove_images([{"image_name": name, "thumb_name": thumb}])
        raise
    flash("Reply posted.", "success")
    return redirect(url_for("imageboard.thread", thread_id=thread_id) + f"#reply-{cursor.lastrowid}")


@bp.get("/media/<name>")
def media(name):
    if Path(name).name != name:
        abort(404)
    exists = get_db().execute(
        "SELECT 1 FROM imageboard_threads WHERE image_name=? OR thumb_name=? "
        "UNION SELECT 1 FROM imageboard_replies WHERE image_name=? OR thumb_name=? LIMIT 1",
        (name, name, name, name),
    ).fetchone()
    if not exists:
        abort(404)
    response = send_from_directory(_image_dir(), name, conditional=True)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return response


@bp.post("/<int:thread_id>/moderate")
def moderate_thread(thread_id):
    validate_csrf()
    item = _thread(thread_id)
    action = request.form.get("action")
    self_delete = action == "delete" and item["author_id"] == g.user["id"]
    if not self_delete:
        _moderator_required()
    if action not in {"pin", "unpin", "lock", "unlock", "delete"}:
        abort(400)
    database = get_db()
    if action == "delete":
        replies = database.execute(
            "SELECT image_name,thumb_name FROM imageboard_replies WHERE thread_id=?", (thread_id,)
        ).fetchall()
        database.execute("DELETE FROM imageboard_threads WHERE id=?", (thread_id,))
        destination = url_for("imageboard.catalog")
    else:
        column = "pinned" if action in {"pin", "unpin"} else "locked"
        value = int(action in {"pin", "lock"})
        database.execute(f"UPDATE imageboard_threads SET {column}=? WHERE id=?", (value, thread_id))
        destination = url_for("imageboard.thread", thread_id=thread_id)
    if not self_delete:
        audit(database, g.user["id"], item["author_id"], f"imageboard_{action}_thread",
              request.form.get("reason", "")[:500], "imageboard_thread", thread_id)
    database.commit()
    if action == "delete":
        _remove_images([item, *replies])
    flash({"pin": "Thread pinned.", "unpin": "Thread unpinned.",
           "lock": "Thread locked.", "unlock": "Thread unlocked.",
           "delete": "Thread deleted."}[action], "success")
    return redirect(destination)


@bp.post("/replies/<int:reply_id>/delete")
def delete_reply(reply_id):
    validate_csrf()
    database = get_db()
    reply = database.execute("SELECT * FROM imageboard_replies WHERE id=?", (reply_id,)).fetchone()
    if reply is None:
        abort(404)
    own = reply["author_id"] == g.user["id"]
    if not own:
        _moderator_required()
    database.execute("DELETE FROM imageboard_replies WHERE id=?", (reply_id,))
    database.execute(
        "UPDATE imageboard_threads SET bumped_at=COALESCE((SELECT MAX(created_at) "
        "FROM imageboard_replies WHERE thread_id=?),created_at) WHERE id=?",
        (reply["thread_id"], reply["thread_id"]),
    )
    if not own:
        audit(database, g.user["id"], reply["author_id"], "imageboard_delete_reply",
              request.form.get("reason", "")[:500], "imageboard_reply", reply_id)
    database.commit()
    _remove_images([reply])
    flash("Reply deleted.", "success")
    return redirect(url_for("imageboard.thread", thread_id=reply["thread_id"]))
