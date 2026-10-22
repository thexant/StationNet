"""Small station-owned shared file archive."""
import io
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, send_from_directory, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

from .auth import login_required
from .db import get_db
from .security import validate_csrf

bp = Blueprint("archive", __name__, url_prefix="/files")
CATEGORIES = ("General", "Documents", "Images", "Software", "Other")
IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
PREVIEW_MAX_SIZE = (480, 320)
PREVIEW_MAX_SOURCE_PIXELS = 40_000_000


def _archive_dir():
    path = Path(current_app.config["DATA_DIR"]) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _can_upload():
    permission = g.station["upload_permission"]
    return permission == "members" or (permission == "admins" and g.user["is_admin"])


@bp.get("")
@login_required
def index():
    category = request.args.get("category", "")
    database = get_db()
    if category in CATEGORIES:
        files = database.execute(
            "SELECT f.*,u.callsign,u.username FROM archive_files f JOIN users u ON u.id=f.uploader_id "
            "WHERE f.category=? ORDER BY f.created_at DESC,f.id DESC", (category,)
        ).fetchall()
    else:
        category = ""
        files = database.execute(
            "SELECT f.*,u.callsign,u.username FROM archive_files f JOIN users u ON u.id=f.uploader_id "
            "ORDER BY f.created_at DESC,f.id DESC"
        ).fetchall()
    used = database.execute("SELECT COALESCE(SUM(size_bytes),0) FROM archive_files").fetchone()[0]
    return render_template(
        "archive/index.html", files=files, categories=CATEGORIES, category=category,
        can_upload=_can_upload(), used_bytes=used,
        preview_file_ids={
            item["id"] for item in files
            if Path(item["display_name"]).suffix.lower() in IMAGE_SUFFIXES
        },
    )


@bp.route("/upload", methods=("GET", "POST"))
@login_required
def upload():
    if not _can_upload():
        abort(403)
    if request.method == "POST":
        validate_csrf()
        uploaded = request.files.get("file")
        category = request.form.get("category", "")
        description = request.form.get("description", "").strip()
        filename = Path(uploaded.filename or "").name.strip() if uploaded else ""
        error = None
        if not uploaded or not filename or filename in (".", ".."):
            error = "Select a file to upload."
        elif len(filename) > 200 or "\x00" in filename:
            error = "File name is invalid or too long."
        elif category not in CATEGORIES:
            error = "Select a valid archive category."
        elif len(description) > 500:
            error = "Description cannot exceed 500 characters."
        if error is None:
            maximum = g.station["file_size_limit_mb"] * 1024 * 1024
            payload = uploaded.read(maximum + 1)
            if len(payload) > maximum:
                error = f"File exceeds the {g.station['file_size_limit_mb']} MB per-file limit."
            else:
                database = get_db()
                used = database.execute("SELECT COALESCE(SUM(size_bytes),0) FROM archive_files").fetchone()[0]
                storage_limit = g.station["file_storage_limit_mb"] * 1024 * 1024
                if used + len(payload) > storage_limit:
                    error = "The station archive storage limit would be exceeded."
        if error:
            flash(error, "error")
        else:
            suffix = Path(filename).suffix[:16]
            stored_name = f"{secrets.token_hex(20)}{suffix}"
            destination = _archive_dir() / stored_name
            destination.write_bytes(payload)
            try:
                database.execute(
                    "INSERT INTO archive_files(uploader_id,display_name,stored_name,category,description,size_bytes) "
                    "VALUES(?,?,?,?,?,?)",
                    (g.user["id"], filename, stored_name, category, description, len(payload)),
                )
                database.commit()
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            flash("File added to the station archive.", "success")
            return redirect(url_for("archive.index"))
    return render_template("archive/upload.html", categories=CATEGORIES)


@bp.get("/<int:file_id>/download")
@login_required
def download(file_id):
    item = get_db().execute("SELECT * FROM archive_files WHERE id=?", (file_id,)).fetchone()
    if item is None:
        abort(404)
    return send_from_directory(
        _archive_dir(), item["stored_name"], as_attachment=True,
        download_name=item["display_name"], mimetype="application/octet-stream",
    )


@bp.get("/<int:file_id>/preview")
@login_required
def preview(file_id):
    """Return a bounded, re-encoded raster preview instead of the original file."""
    item = get_db().execute("SELECT * FROM archive_files WHERE id=?", (file_id,)).fetchone()
    if item is None or Path(item["display_name"]).suffix.lower() not in IMAGE_SUFFIXES:
        abort(404)

    try:
        with Image.open(_archive_dir() / item["stored_name"]) as source:
            if source.format not in {"GIF", "JPEG", "PNG", "WEBP"}:
                abort(404)
            if source.width * source.height > PREVIEW_MAX_SOURCE_PIXELS:
                abort(404)
            image = ImageOps.exif_transpose(source)
            image.thumbnail(PREVIEW_MAX_SIZE)
            output = io.BytesIO()
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                image.convert("RGBA").save(output, "PNG", optimize=True)
                mimetype = "image/png"
            else:
                image.convert("RGB").save(output, "JPEG", quality=82, optimize=True)
                mimetype = "image/jpeg"
    except (FileNotFoundError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        abort(404)

    output.seek(0)
    return send_file(output, mimetype=mimetype, max_age=0)


@bp.post("/<int:file_id>/delete")
@login_required
def delete(file_id):
    validate_csrf()
    database = get_db()
    item = database.execute("SELECT * FROM archive_files WHERE id=?", (file_id,)).fetchone()
    if item is None:
        abort(404)
    if item["uploader_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    try:
        (_archive_dir() / item["stored_name"]).unlink(missing_ok=True)
    except PermissionError:
        flash("File is currently in use and could not be removed. Try again shortly.", "error")
        return redirect(url_for("archive.index"))
    database.execute("DELETE FROM archive_files WHERE id=?", (file_id,))
    database.commit()
    flash("Archive file removed.", "success")
    return redirect(url_for("archive.index"))
