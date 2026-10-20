"""Persistent station chat and temporary direct chat."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for

from .auth import login_required
from .db import get_db
from .security import validate_csrf

bp = Blueprint("chat", __name__, url_prefix="/chat")


def _cutoff():
    return (datetime.now(timezone.utc) - timedelta(seconds=45)).replace(microsecond=0).isoformat()


def _online_user(user_id):
    return get_db().execute(
        "SELECT id,username,callsign,connection_id FROM users "
        "WHERE id=? AND connection_id IS NOT NULL AND last_seen>=?",
        (user_id, _cutoff()),
    ).fetchone()


@bp.get("")
@login_required
def station_chat():
    return render_template("chat/station.html")


@bp.get("/messages")
@login_required
def station_messages():
    after = request.args.get("after", 0, type=int)
    database = get_db()
    if after:
        rows = database.execute(
            "SELECT m.id,m.body,m.created_at,u.callsign,u.username FROM station_chat_messages m "
            "JOIN users u ON u.id=m.author_id WHERE m.id>? ORDER BY m.id LIMIT 200",
            (after,),
        ).fetchall()
    else:
        rows = database.execute(
            "SELECT * FROM (SELECT m.id,m.body,m.created_at,u.callsign,u.username "
            "FROM station_chat_messages m JOIN users u ON u.id=m.author_id "
            "ORDER BY m.id DESC LIMIT 100) ORDER BY id"
        ).fetchall()
    return jsonify(messages=[dict(row) for row in rows])


@bp.post("/messages")
@login_required
def send_station_message():
    validate_csrf()
    body = request.form.get("body", "").strip()
    if not 1 <= len(body) <= 1000:
        return jsonify(error="Message must be between 1 and 1000 characters."), 400
    database = get_db()
    cursor = database.execute(
        "INSERT INTO station_chat_messages(author_id,body) VALUES(?,?)",
        (g.user["id"], body),
    )
    database.commit()
    return jsonify(id=cursor.lastrowid), 201


@bp.get("/direct/<int:user_id>")
@login_required
def direct(user_id):
    if user_id == g.user["id"]:
        abort(404)
    peer = _online_user(user_id)
    if peer is None:
        flash("That operator is no longer online.", "warning")
        return redirect(url_for("users.directory"))
    return render_template("chat/direct.html", peer=peer)


@bp.get("/direct/<int:user_id>/messages")
@login_required
def direct_messages(user_id):
    peer = _online_user(user_id)
    if peer is None:
        return jsonify(active=False, messages=[])
    after = request.args.get("after", 0, type=int)
    messages = current_app.extensions["direct_chat"].list(
        g.user["id"], session["connection_id"], peer["id"], peer["connection_id"], after
    )
    for item in messages:
        item["callsign"] = g.user["callsign"] if item["sender_id"] == g.user["id"] else peer["callsign"]
        item["mine"] = item["sender_id"] == g.user["id"]
        del item["sender_id"]
    return jsonify(active=True, messages=messages)


@bp.post("/direct/<int:user_id>/messages")
@login_required
def send_direct_message(user_id):
    validate_csrf()
    peer = _online_user(user_id)
    if peer is None:
        return jsonify(error="That operator is no longer online."), 409
    body = request.form.get("body", "").strip()
    if not 1 <= len(body) <= 1000:
        return jsonify(error="Message must be between 1 and 1000 characters."), 400
    message = current_app.extensions["direct_chat"].send(
        g.user["id"], session["connection_id"], peer["id"], peer["connection_id"], body
    )
    return jsonify(id=message["id"]), 201
