"""Persistent private BBS mail."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import login_required
from .db import get_db
from .security import validate_csrf

bp = Blueprint("mail", __name__, url_prefix="/mail")


def latest_id(database, user_id):
    return database.execute(
        "SELECT COALESCE(MAX(id),0) FROM mail WHERE recipient_id=?", (user_id,)
    ).fetchone()[0]


def counts(database, user_id, after=0):
    """Unread mail totals for the header badge; ``new_count`` counts mail after ``after``."""
    row = database.execute(
        "SELECT COUNT(*) FILTER (WHERE id>?) AS new_count, "
        "COUNT(*) FILTER (WHERE is_read=0) AS unread_count "
        "FROM mail WHERE recipient_id=? AND recipient_deleted=0",
        (max(0, after), user_id),
    ).fetchone()
    return {"latest_id": latest_id(database, user_id), "new_count": row["new_count"],
            "unread_count": row["unread_count"]}


@bp.get("/notifications")
@login_required
def notifications():
    after = max(0, request.args.get("after", 0, type=int))
    return jsonify(**counts(get_db(), g.user["id"], after))


@bp.get("")
@login_required
def inbox():
    messages = get_db().execute(
        "SELECT m.id,m.subject,m.is_read,m.created_at,u.callsign,u.username "
        "FROM mail m JOIN users u ON u.id=m.sender_id "
        "WHERE m.recipient_id=? AND m.recipient_deleted=0 "
        "ORDER BY m.created_at DESC,m.id DESC",
        (g.user["id"],),
    ).fetchall()
    return render_template("mail/index.html", messages=messages, folder="inbox")


@bp.get("/sent")
@login_required
def sent():
    messages = get_db().execute(
        "SELECT m.id,m.subject,m.is_read,m.created_at,u.callsign,u.username "
        "FROM mail m JOIN users u ON u.id=m.recipient_id "
        "WHERE m.sender_id=? AND m.sender_deleted=0 ORDER BY m.created_at DESC,m.id DESC",
        (g.user["id"],),
    ).fetchall()
    return render_template("mail/index.html", messages=messages, folder="sent")


@bp.route("/compose", methods=("GET", "POST"))
@login_required
def compose():
    database = get_db()
    recipient_id = request.args.get("to", type=int)
    if request.method == "POST":
        validate_csrf()
        recipient_id = request.form.get("recipient_id", type=int)
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()
        recipient = database.execute("SELECT id FROM users WHERE id=?", (recipient_id,)).fetchone()
        if recipient is None:
            flash("Select a registered recipient.", "error")
        elif not 1 <= len(subject) <= 120:
            flash("Subject must be between 1 and 120 characters.", "error")
        elif not 1 <= len(body) <= 10000:
            flash("Message must be between 1 and 10000 characters.", "error")
        else:
            cursor = database.execute(
                "INSERT INTO mail(sender_id,recipient_id,subject,body) VALUES(?,?,?,?)",
                (g.user["id"], recipient_id, subject, body),
            )
            database.execute("UPDATE mail SET thread_id=id WHERE id=?", (cursor.lastrowid,))
            database.commit()
            flash("Private mail delivered.", "success")
            return redirect(url_for("mail.sent"))
    recipients = database.execute(
        "SELECT id,username,callsign FROM users ORDER BY username COLLATE NOCASE"
    ).fetchall()
    return render_template("mail/compose.html", recipients=recipients, recipient_id=recipient_id)


@bp.get("/<int:message_id>")
@login_required
def view(message_id):
    database = get_db()
    message = database.execute(
        "SELECT m.*,s.username sender_name,s.callsign sender_callsign," 
        "r.username recipient_name,r.callsign recipient_callsign FROM mail m "
        "JOIN users s ON s.id=m.sender_id JOIN users r ON r.id=m.recipient_id "
        "WHERE m.id=? AND ((m.recipient_id=? AND m.recipient_deleted=0) "
        "OR (m.sender_id=? AND m.sender_deleted=0))",
        (message_id, g.user["id"], g.user["id"]),
    ).fetchone()
    if message is None:
        abort(404)
    thread_id = message["thread_id"] or message["id"]
    messages = database.execute(
        "SELECT m.*,s.username sender_name,s.callsign sender_callsign,"
        "r.username recipient_name,r.callsign recipient_callsign FROM mail m "
        "JOIN users s ON s.id=m.sender_id JOIN users r ON r.id=m.recipient_id "
        "WHERE COALESCE(m.thread_id,m.id)=? AND "
        "((m.recipient_id=? AND m.recipient_deleted=0) OR "
        "(m.sender_id=? AND m.sender_deleted=0)) ORDER BY m.created_at,m.id",
        (thread_id, g.user["id"], g.user["id"]),
    ).fetchall()
    read_update = database.execute(
        "UPDATE mail SET is_read=1 WHERE COALESCE(thread_id,id)=? AND recipient_id=? "
        "AND recipient_deleted=0 AND is_read=0",
        (thread_id, g.user["id"]),
    )
    if read_update.rowcount:
        database.commit()
    other_user = {
        "id": message["sender_id"],
        "username": message["sender_name"],
        "callsign": message["sender_callsign"],
    } if message["recipient_id"] == g.user["id"] else {
        "id": message["recipient_id"],
        "username": message["recipient_name"],
        "callsign": message["recipient_callsign"],
    }
    return render_template(
        "mail/view.html", message=message, messages=messages, other_user=other_user
    )


@bp.post("/<int:message_id>/reply")
@login_required
def reply(message_id):
    validate_csrf()
    database = get_db()
    message = database.execute(
        "SELECT * FROM mail WHERE id=? AND "
        "((recipient_id=? AND recipient_deleted=0) OR (sender_id=? AND sender_deleted=0))",
        (message_id, g.user["id"], g.user["id"]),
    ).fetchone()
    if message is None:
        abort(404)

    body = request.form.get("body", "").strip()
    if not 1 <= len(body) <= 10000:
        flash("Reply must be between 1 and 10000 characters.", "error")
        return redirect(url_for("mail.view", message_id=message_id) + "#reply")

    recipient_id = (
        message["sender_id"] if message["recipient_id"] == g.user["id"]
        else message["recipient_id"]
    )
    subject = message["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    thread_id = message["thread_id"] or message["id"]
    cursor = database.execute(
        "INSERT INTO mail(sender_id,recipient_id,thread_id,reply_to_id,subject,body) "
        "VALUES(?,?,?,?,?,?)",
        (g.user["id"], recipient_id, thread_id, message_id, subject, body),
    )
    database.commit()
    flash("Reply delivered.", "success")
    return redirect(url_for("mail.view", message_id=cursor.lastrowid) + "#message-latest")


@bp.post("/<int:message_id>/delete")
@login_required
def delete(message_id):
    validate_csrf()
    database = get_db()
    message = database.execute("SELECT sender_id,recipient_id FROM mail WHERE id=?", (message_id,)).fetchone()
    if message is None or g.user["id"] not in (message["sender_id"], message["recipient_id"]):
        abort(404)
    field = "sender_deleted" if message["sender_id"] == g.user["id"] else "recipient_deleted"
    database.execute(f"UPDATE mail SET {field}=1 WHERE id=?", (message_id,))
    # Keep doubly-deleted rows as thread tombstones. Physically deleting a root
    # would otherwise orphan or cascade-delete replies that either participant kept.
    database.commit()
    flash("Mail removed from your mailbox.", "success")
    return redirect(url_for("mail.sent" if field == "sender_deleted" else "mail.inbox"))
