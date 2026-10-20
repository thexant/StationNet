"""Station user directory and owner-editable profiles."""
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .db import get_db
from .security import validate_csrf

bp = Blueprint("users", __name__, url_prefix="/users")


def _cutoff():
    return (datetime.now(timezone.utc) - timedelta(seconds=45)).replace(microsecond=0).isoformat()


@bp.get("/presence")
@login_required
def presence():
    """A lightweight browser heartbeat; the app hook refreshes last_seen."""
    return "", 204


@bp.get("")
@login_required
def directory():
    users = get_db().execute(
        "SELECT id,username,callsign,is_admin,tagline,created_at," 
        "(connection_id IS NOT NULL AND last_seen>=?) online FROM users "
        "ORDER BY username COLLATE NOCASE",
        (_cutoff(),),
    ).fetchall()
    return render_template("users/index.html", users=users)


@bp.route("/<int:user_id>", methods=("GET", "POST"))
@login_required
def profile(user_id):
    database = get_db()
    profile_user = database.execute(
        "SELECT id,username,callsign,is_admin,created_at,tagline,about,birthday,gender," 
        "(connection_id IS NOT NULL AND last_seen>=?) online FROM users WHERE id=?",
        (_cutoff(), user_id),
    ).fetchone()
    if profile_user is None:
        abort(404)
    if request.method == "POST":
        if user_id != g.user["id"]:
            abort(403)
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
        if error:
            flash(error, "error")
        else:
            database.execute(
                "UPDATE users SET tagline=?,about=?,birthday=?,gender=? WHERE id=?",
                (tagline, about, birthday, gender, user_id),
            )
            database.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("users.profile", user_id=user_id))
    return render_template("users/profile.html", profile=profile_user)
