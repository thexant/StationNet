"""Station user directory and owner-editable profiles."""
import secrets
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
from .security import check_password, hash_password, validate_csrf, validate_password

bp = Blueprint("users", __name__, url_prefix="/users")
PROFILE_PHOTO_MAX_BYTES = 10 * 1024 * 1024
PROFILE_PHOTO_MAX_PIXELS = 20_000_000
PROFILE_PHOTO_SIZE = (128, 128)


def _cutoff():
    return (datetime.now(timezone.utc) - timedelta(seconds=45)).replace(microsecond=0).isoformat()


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
    return render_template("users/profile.html", profile=profile_user)


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
        elif birthday:
            try:
                date.fromisoformat(birthday)
            except ValueError:
                error = "Birthday must be a valid date."
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
    return render_template("users/edit.html", profile=profile_user)


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
