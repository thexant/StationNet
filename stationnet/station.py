"""Welcome, workspace, and SYSOP settings."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
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
        validate_csrf(); name = request.form.get("name", "").strip(); description = request.form.get("description", "").strip()
        if not 2 <= len(name) <= 80: flash("Station name must be between 2 and 80 characters.", "error")
        elif len(description) > 500: flash("Station description cannot exceed 500 characters.", "error")
        else:
            database = get_db()
            database.execute("UPDATE station SET name=?,description=?,registration_open=?,guest_access=? WHERE id=1", (name, description, request.form.get("registration_open") == "on", request.form.get("guest_access") == "on"))
            database.commit(); flash("Station parameters updated.", "success")
            return redirect(url_for("station.settings"))
    return render_template("settings.html")

