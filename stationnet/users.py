"""Station user directory and owner-editable profiles."""
import secrets
import re
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, g, redirect, render_template, request,
    send_from_directory, url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError

from .auth import login_required
from .db import get_db
from . import themes
from .security import check_password, hash_password, validate_csrf, validate_password

bp = Blueprint("users", __name__, url_prefix="/users")
PROFILE_PHOTO_MAX_BYTES = 10 * 1024 * 1024
PROFILE_PHOTO_MAX_PIXELS = 20_000_000
PROFILE_PHOTO_SIZE = (128, 128)
BIRTHDAY_WITH_YEAR_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
BIRTHDAY_WITHOUT_YEAR_RE = re.compile(r"\d{2}/\d{2}")


def _cutoff():
    return (datetime.now(timezone.utc) - timedelta(seconds=45)).replace(microsecond=0).isoformat()


def _valid_birthday(value):
    """Accept an ISO date or a recurring month/day birthday without a year."""
    try:
        if BIRTHDAY_WITH_YEAR_RE.fullmatch(value):
            date.fromisoformat(value)
        elif BIRTHDAY_WITHOUT_YEAR_RE.fullmatch(value):
            month, day = (int(part) for part in value.split("/"))
            # Use a leap year so February 29 can be represented without a year.
            date(2000, month, day)
        else:
            return False
    except ValueError:
        return False
    return True


def _profile_photo_dir():
    path = Path(current_app.config["DATA_DIR"]) / "profile_photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _delete_profile_photo(filename):
    """Delete only simple filenames from the dedicated profile-photo directory."""
    if filename and Path(filename).name == filename:
        try:
            (_profile_photo_dir() / filename).unlink(missing_ok=True)
        except PermissionError:
            # A Windows client may still have the previous response open. A
            # later visit to the editor will retry it as an orphaned file.
            pass


def _cleanup_orphaned_profile_photos(database):
    referenced = {
        row[0] for row in database.execute(
            "SELECT profile_photo_filename FROM users WHERE profile_photo_filename IS NOT NULL"
        )
    }
    for path in _profile_photo_dir().glob("*.png"):
        if path.name not in referenced:
            _delete_profile_photo(path.name)


def _save_profile_photo(uploaded):
    """Validate, flatten, resize, and store an uploaded image as metadata-free PNG."""
    payload = uploaded.read(PROFILE_PHOTO_MAX_BYTES + 1)
    if len(payload) > PROFILE_PHOTO_MAX_BYTES:
        raise ValueError("Profile photo cannot exceed 10 MB.")
    if not payload:
        raise ValueError("The selected profile photo is empty.")

    try:
        with Image.open(BytesIO(payload)) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > PROFILE_PHOTO_MAX_PIXELS:
                raise ValueError("Profile photo dimensions are too large.")
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            has_transparency = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_transparency else "RGB")
            image.thumbnail(PROFILE_PHOTO_SIZE, Image.Resampling.LANCZOS)
            clean = Image.new(image.mode, image.size)
            clean.paste(image)
    except ValueError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
        raise ValueError("Select a valid PNG, JPEG, GIF, or WebP image.") from None

    filename = f"{secrets.token_hex(20)}.png"
    destination = _profile_photo_dir() / filename
    clean.save(destination, format="PNG", optimize=True)
    return filename


@bp.get("/presence")
@login_required
def presence():
    """A lightweight browser heartbeat; the app hook refreshes last_seen."""
    return "", 204


@bp.get("")
@login_required
def directory():
    users = get_db().execute(
        "SELECT id,username,callsign,is_admin,tagline,created_at,last_seen,profile_photo_filename,"
        "(connection_id IS NOT NULL AND last_seen>=?) online FROM users "
        "ORDER BY username COLLATE NOCASE",
        (_cutoff(),),
    ).fetchall()
    return render_template("users/index.html", users=users)


@bp.get("/<int:user_id>")
@login_required
def profile(user_id):
    database = get_db()
    _cleanup_orphaned_profile_photos(database)
    profile_user = database.execute(
        "SELECT id,username,callsign,is_admin,created_at,tagline,about,birthday,gender,"
        "profile_photo_filename,"
        "(connection_id IS NOT NULL AND last_seen>=?) online FROM users WHERE id=?",
        (_cutoff(), user_id),
    ).fetchone()
    if profile_user is None:
        abort(404)
    feed_posts = []
    feed_comments = {}
    feed_reactions = {}
    feed_comment_counts = {}
    feed_page = 1
    feed_has_next = False
    selected_post = None
    comment_page = 1
    comment_has_next = False
    if g.station["user_feeds_enabled"]:
        feed_page = min(1000, max(1, request.args.get("page", 1, type=int)))
        selected_post = request.args.get("post", type=int)
        if selected_post is not None:
            if selected_post < 1:
                abort(404)
            comment_page = min(1000, max(1, request.args.get("comments", 1, type=int)))
            feed_posts = database.execute(
                "SELECT p.*,u.username,u.callsign FROM user_feed_posts p "
                "JOIN users u ON u.id=p.owner_id WHERE p.owner_id=? AND p.id=?",
                (user_id, selected_post),
            ).fetchall()
            if not feed_posts:
                abort(404)
        else:
            rows = database.execute(
                "SELECT p.*,u.username,u.callsign FROM user_feed_posts p "
                "JOIN users u ON u.id=p.owner_id WHERE p.owner_id=? "
                "ORDER BY p.id DESC LIMIT 21 OFFSET ?", (user_id, (feed_page - 1) * 20),
            ).fetchall()
            feed_has_next = len(rows) > 20
            feed_posts = rows[:20]
        for post in feed_posts:
            feed_comment_counts[post["id"]] = database.execute(
                "SELECT COUNT(*) FROM user_feed_comments WHERE post_id=?", (post["id"],)
            ).fetchone()[0]
            if selected_post is not None:
                rows = database.execute(
                    "SELECT c.*,u.username,u.callsign FROM user_feed_comments c "
                    "JOIN users u ON u.id=c.author_id WHERE c.post_id=? "
                    "ORDER BY c.id DESC LIMIT 31 OFFSET ?",
                    (post["id"], (comment_page - 1) * 30),
                ).fetchall()
                comment_has_next = len(rows) > 30
                feed_comments[post["id"]] = rows[:30]
            else:
                feed_comments[post["id"]] = database.execute(
                    "SELECT c.*,u.username,u.callsign FROM user_feed_comments c "
                    "JOIN users u ON u.id=c.author_id WHERE c.post_id=? "
                    "ORDER BY c.id DESC LIMIT 3", (post["id"],),
                ).fetchall()
            counts = database.execute(
                "SELECT kind,COUNT(*) count FROM user_feed_reactions WHERE post_id=? GROUP BY kind",
                (post["id"],),
            ).fetchall()
            chosen = database.execute(
                "SELECT kind FROM user_feed_reactions WHERE post_id=? AND user_id=?",
                (post["id"], g.user["id"]),
            ).fetchone()
            feed_reactions[post["id"]] = {
                "counts": {row["kind"]: row["count"] for row in counts},
                "chosen": chosen["kind"] if chosen else None,
            }
    return render_template(
        "users/profile.html", profile=profile_user, feed_posts=feed_posts,
        feed_comments=feed_comments, feed_reactions=feed_reactions,
        feed_comment_counts=feed_comment_counts,
        feed_page=feed_page, feed_has_next=feed_has_next,
        selected_post=selected_post, comment_page=comment_page,
        comment_has_next=comment_has_next,
    )


@bp.route("/<int:user_id>/edit", methods=("GET", "POST"))
@login_required
def edit_profile(user_id):
    if user_id != g.user["id"]:
        abort(403)
    database = get_db()
    profile_user = database.execute(
        "SELECT id,username,callsign,tagline,about,birthday,gender,profile_photo_filename "
        "FROM users WHERE id=?", (user_id,),
    ).fetchone()
    if profile_user is None:
        abort(404)
    if request.method == "POST":
        validate_csrf()
        tagline = request.form.get("tagline", "").strip()
        about = request.form.get("about", "").strip()
        birthday = request.form.get("birthday", "").strip() or None
        gender = request.form.get("gender", "").strip()
        error = None
        if len(tagline) > 120:
            error = "Tagline cannot exceed 120 characters."
        elif len(about) > 2000:
            error = "About me cannot exceed 2000 characters."
        elif len(gender) > 40:
            error = "Gender cannot exceed 40 characters."
        elif birthday and not _valid_birthday(birthday):
            error = "Birthday must be a valid MM/DD or YYYY-MM-DD date."
        uploaded = request.files.get("profile_photo")
        new_photo = None
        if error is None and uploaded and uploaded.filename:
            try:
                new_photo = _save_profile_photo(uploaded)
            except ValueError as exc:
                error = str(exc)
        if error:
            flash(error, "error")
        else:
            old_photo = profile_user["profile_photo_filename"]
            try:
                database.execute(
                    "UPDATE users SET tagline=?,about=?,birthday=?,gender=?,"
                    "profile_photo_filename=COALESCE(?,profile_photo_filename) WHERE id=?",
                    (tagline, about, birthday, gender, new_photo, user_id),
                )
                database.commit()
            except Exception:
                _delete_profile_photo(new_photo)
                raise
            if new_photo:
                _delete_profile_photo(old_photo)
                _cleanup_orphaned_profile_photos(database)
            flash("Profile updated.", "success")
            return redirect(url_for("users.profile", user_id=user_id))
    return render_template("users/edit.html", profile=profile_user, themes=themes.THEMES)


@bp.post("/<int:user_id>/themes")
@login_required
def save_themes(user_id):
    if user_id != g.user["id"]:
        abort(403)
    validate_csrf()
    site_theme = request.form.get("site_theme", "")
    masthead_theme = request.form.get("masthead_theme", "")
    if ((site_theme and not themes.valid(site_theme)) or
            (masthead_theme and not themes.valid(masthead_theme))):
        flash("Select valid theme preferences.", "error")
    else:
        database = get_db()
        database.execute("UPDATE users SET site_theme=?,masthead_theme=? WHERE id=?",
                         (site_theme or None, masthead_theme or None, user_id))
        database.commit()
        flash("Theme preferences updated.", "success")
    return redirect(url_for("users.edit_profile", user_id=user_id) + "#theme-preferences")


@bp.post("/<int:user_id>/password")
@login_required
def change_password(user_id):
    if user_id != g.user["id"]:
        abort(403)
    validate_csrf()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    database = get_db()
    user = database.execute(
        "SELECT password_hash FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if user is None:
        abort(404)

    error = None
    if not check_password(user["password_hash"], current_password):
        error = "Existing password is incorrect."
    elif new_password != confirm_password:
        error = "New passwords do not match."
    else:
        error = validate_password(new_password)
        if error is None and check_password(user["password_hash"], new_password):
            error = "New password must be different from the existing password."

    if error:
        flash(error, "error")
    else:
        database.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (hash_password(new_password), user_id),
        )
        database.commit()
        flash("Password changed.", "success")
    return redirect(url_for("users.edit_profile", user_id=user_id) + "#change-password")


@bp.get("/<int:user_id>/photo")
@login_required
def profile_photo(user_id):
    item = get_db().execute(
        "SELECT profile_photo_filename FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if item is None or not item["profile_photo_filename"]:
        abort(404)
    filename = item["profile_photo_filename"]
    if Path(filename).name != filename:
        abort(404)
    return send_from_directory(_profile_photo_dir(), filename, mimetype="image/png")
