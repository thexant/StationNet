"""Welcome, workspace, and SYSOP settings."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from .auth import admin_required
from .db import get_db
from .security import validate_csrf
bp = Blueprint("station", __name__)

@bp.get("/")
def index(): return render_template("welcome.html")

@bp.get("/terminal")
def terminal(): return render_template("terminal.html")

@bp.route("/sysop/settings", methods=("GET", "POST"))
@admin_required
def settings():
    if request.method == "POST":
        validate_csrf()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        landing_message = request.form.get(
            "landing_message", g.station["landing_message"]
        ).strip()
        brand_tagline = request.form.get(
            "brand_tagline", g.station["brand_tagline"]
        ).strip()
        upload_permission = request.form.get("upload_permission", g.station["upload_permission"])
        storage_limit = request.form.get("file_storage_limit_mb", g.station["file_storage_limit_mb"], type=int)
        file_limit = request.form.get("file_size_limit_mb", g.station["file_size_limit_mb"], type=int)
        if not 2 <= len(name) <= 80: flash("Station name must be between 2 and 80 characters.", "error")
        elif len(description) > 500: flash("Station description cannot exceed 500 characters.", "error")
        elif len(landing_message) > 500: flash("Landing message cannot exceed 500 characters.", "error")
        elif not 1 <= len(brand_tagline) <= 160: flash("Header tagline must be between 1 and 160 characters.", "error")
        elif upload_permission not in {"members", "admins", "disabled"}: flash("Select a valid upload permission.", "error")
        elif storage_limit is None or not 1 <= storage_limit <= 10240: flash("Archive storage must be between 1 and 10240 MB.", "error")
        elif file_limit is None or not 1 <= file_limit <= 64: flash("Per-file limit must be between 1 and 64 MB.", "error")
        elif file_limit > storage_limit: flash("Per-file limit cannot exceed total archive storage.", "error")
        else:
            database = get_db()
            database.execute(
                "UPDATE station SET name=?,description=?,landing_message=?,brand_tagline=?,"
                "registration_open=?,guest_access=?,upload_permission=?,"
                "file_storage_limit_mb=?,file_size_limit_mb=? WHERE id=1",
                (
                    name,
                    description,
                    landing_message,
                    brand_tagline,
                    request.form.get("registration_open") == "on",
                    request.form.get("guest_access") == "on",
                    upload_permission,
                    storage_limit,
                    file_limit,
                ),
            )
            database.commit(); flash("Station parameters updated.", "success")
            return redirect(url_for("station.settings"))
    return render_template("settings.html")

