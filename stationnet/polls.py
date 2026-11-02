"""Polls attached to bulletins and announcements."""
from flask import Blueprint, abort, flash, g, redirect, request, url_for

from . import modules
from .auth import login_required
from .db import get_db
from .moderation import audit
from .realtime import utc_now
from .security import validate_csrf

bp = Blueprint("polls", __name__, url_prefix="/polls")

MAX_OPTIONS = 10


def parse_form(form):
    """Return (poll fields, error); both None when no poll was requested."""
    if not form.get("poll_enabled"):
        return None, None
    question = form.get("poll_question", "").strip()
    options = [option.strip() for option in form.getlist("poll_option") if option.strip()]
    if not 1 <= len(question) <= 200:
        return None, "Poll question must be between 1 and 200 characters."
    if not 2 <= len(options) <= MAX_OPTIONS:
        return None, f"A poll needs between 2 and {MAX_OPTIONS} responses."
    if any(len(option) > 100 for option in options):
        return None, "Poll responses cannot be longer than 100 characters."
    if len({option.casefold() for option in options}) != len(options):
        return None, "Poll responses must be different from each other."
    return {"question": question, "options": options,
            "allow_multiple": int(bool(form.get("poll_multiple")))}, None


def create(database, owner_id, poll, post_id=None, announcement_id=None):
    """Store a parsed poll; the caller commits it with its bulletin or announcement."""
    poll_id = database.execute(
        "INSERT INTO polls(post_id,announcement_id,owner_id,question,allow_multiple) VALUES(?,?,?,?,?)",
        (post_id, announcement_id, owner_id, poll["question"], poll["allow_multiple"]),
    ).lastrowid
    database.executemany(
        "INSERT INTO poll_options(poll_id,position,label) VALUES(?,?,?)",
        [(poll_id, position, label) for position, label in enumerate(poll["options"])],
    )


def _view(database, poll):
    user_id = g.user["id"] if g.user else None
    can_manage = bool(g.user and (poll["owner_id"] == user_id or g.user["is_admin"]))
    options = database.execute(
        "SELECT o.id,o.label,COUNT(v.user_id) votes,COALESCE(MAX(v.user_id=?),0) selected "
        "FROM poll_options o LEFT JOIN poll_votes v ON v.option_id=o.id "
        "WHERE o.poll_id=? GROUP BY o.id ORDER BY o.position",
        (user_id, poll["id"]),
    ).fetchall()
    voted = any(option["selected"] for option in options)
    closed = poll["closed_at"] is not None
    show_results = can_manage or voted or closed
    voters = database.execute(
        "SELECT COUNT(DISTINCT v.user_id) FROM poll_votes v JOIN poll_options o ON o.id=v.option_id "
        "WHERE o.poll_id=?", (poll["id"],),
    ).fetchone()[0] if show_results else None
    return {
        "id": poll["id"], "question": poll["question"], "allow_multiple": poll["allow_multiple"],
        "closed": closed, "voted": voted, "can_manage": can_manage,
        "can_vote": g.user is not None and not closed,
        "show_results": show_results, "voters": voters,
        # Tallies stay out of the view entirely until the viewer may see them.
        "options": [{"id": option["id"], "label": option["label"], "selected": bool(option["selected"]),
                     "votes": option["votes"] if show_results else None} for option in options],
    }


def view_for_post(post_id):
    database = get_db()
    poll = database.execute("SELECT * FROM polls WHERE post_id=?", (post_id,)).fetchone()
    return _view(database, poll) if poll else None


def views_for_announcements():
    database = get_db()
    return {poll["announcement_id"]: _view(database, poll) for poll in
            database.execute("SELECT * FROM polls WHERE announcement_id IS NOT NULL").fetchall()}


def _poll_or_404(poll_id):
    poll = get_db().execute("SELECT * FROM polls WHERE id=?", (poll_id,)).fetchone()
    if poll is None or not modules.enabled(g.station, "board" if poll["post_id"] else "announcements"):
        abort(404)
    return poll


def _back_to(poll):
    anchor = f"poll-{poll['id']}"
    if poll["post_id"]:
        return redirect(url_for("board.thread", post_id=poll["post_id"], _anchor=anchor))
    return redirect(url_for("announcements.index", _anchor=anchor))


@bp.post("/<int:poll_id>/vote")
@login_required
def vote(poll_id):
    validate_csrf()
    poll = _poll_or_404(poll_id)
    clearing = request.form.get("action") == "clear"
    chosen = set(request.form.getlist("option_id", type=int))
    database = get_db()
    database.execute("BEGIN IMMEDIATE")
    if database.execute("SELECT closed_at FROM polls WHERE id=?", (poll_id,)).fetchone()["closed_at"]:
        database.rollback()
        flash("This poll is closed.", "error")
        return _back_to(poll)
    valid = {row["id"] for row in database.execute("SELECT id FROM poll_options WHERE poll_id=?", (poll_id,))}
    if not clearing and (not chosen or not chosen <= valid or (len(chosen) > 1 and not poll["allow_multiple"])):
        database.rollback()
        flash("Select a response." if not chosen else "That selection is not valid for this poll.", "error")
        return _back_to(poll)
    database.execute(
        "DELETE FROM poll_votes WHERE user_id=? AND option_id IN (SELECT id FROM poll_options WHERE poll_id=?)",
        (g.user["id"], poll_id),
    )
    if not clearing:
        database.executemany("INSERT INTO poll_votes(option_id,user_id) VALUES(?,?)",
                             [(option_id, g.user["id"]) for option_id in sorted(chosen)])
    database.commit()
    flash("Vote cleared." if clearing else "Vote recorded.", "success")
    return _back_to(poll)


@bp.post("/<int:poll_id>/close")
@login_required
def close(poll_id):
    validate_csrf()
    poll = _poll_or_404(poll_id)
    own = poll["owner_id"] == g.user["id"]
    if not own and not g.user["is_admin"]:
        abort(403)
    database = get_db()
    closed = database.execute("UPDATE polls SET closed_at=? WHERE id=? AND closed_at IS NULL",
                              (utc_now(), poll_id)).rowcount
    if closed and not own:
        audit(database, g.user["id"], poll["owner_id"], "close_poll", "SYSOP closed poll", "poll", poll_id)
    database.commit()
    flash("Poll closed.", "success")
    return _back_to(poll)
