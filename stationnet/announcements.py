"""Simple operator-managed station announcements."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import admin_required
from .db import get_db
from .security import validate_csrf

bp = Blueprint("announcements", __name__, url_prefix="/announcements")


@bp.get("")
def index():
    announcements = get_db().execute(
        "SELECT a.*,u.callsign,u.username FROM announcements a JOIN users u ON u.id=a.author_id "
        "ORDER BY a.created_at DESC,a.id DESC"
    ).fetchall()
    return render_template("announcements/index.html", announcements=announcements)


def _validated_fields():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if not 1 <= len(title) <= 120:
        return None, None, "Title must be between 1 and 120 characters."
    if not 1 <= len(body) <= 5000:
        return None, None, "Announcement must be between 1 and 5000 characters."
    return title, body, None


@bp.route("/new", methods=("GET", "POST"))
@admin_required
def create():
    if request.method == "POST":
        validate_csrf()
        title, body, error = _validated_fields()
        if error:
            flash(error, "error")
        else:
            database = get_db()
            database.execute(
                "INSERT INTO announcements(author_id,title,body) VALUES(?,?,?)",
                (g.user["id"], title, body),
            )
            database.commit()
            flash("Station announcement posted.", "success")
            return redirect(url_for("announcements.index"))
    return render_template("announcements/form.html", announcement=None)


@bp.route("/<int:announcement_id>/edit", methods=("GET", "POST"))
@admin_required
def edit(announcement_id):
    database = get_db()
    announcement = database.execute("SELECT * FROM announcements WHERE id=?", (announcement_id,)).fetchone()
    if announcement is None:
        abort(404)
    if request.method == "POST":
        validate_csrf()
        title, body, error = _validated_fields()
        if error:
            flash(error, "error")
        else:
            database.execute(
                "UPDATE announcements SET title=?,body=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (title, body, announcement_id),
            )
            database.commit()
            flash("Station announcement updated.", "success")
            return redirect(url_for("announcements.index"))
    return render_template("announcements/form.html", announcement=announcement)


@bp.post("/<int:announcement_id>/delete")
@admin_required
def delete(announcement_id):
    validate_csrf()
    database = get_db()
    if database.execute("SELECT id FROM announcements WHERE id=?", (announcement_id,)).fetchone() is None:
        abort(404)
    database.execute("DELETE FROM announcements WHERE id=?", (announcement_id,))
    database.commit()
    flash("Station announcement removed.", "success")
    return redirect(url_for("announcements.index"))
