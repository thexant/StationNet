"""Persistent private BBS mail."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .db import get_db
from .security import validate_csrf

bp = Blueprint("mail", __name__, url_prefix="/mail")


@bp.get("")
@login_required
def inbox():
    messages = get_db().execute(
        "SELECT m.id,m.subject,m.is_read,m.created_at,u.callsign,u.username "
        "FROM mail m JOIN users u ON u.id=m.sender_id "
        "WHERE m.recipient_id=? AND m.recipient_deleted=0 ORDER BY m.created_at DESC,m.id DESC",
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
            database.execute(
                "INSERT INTO mail(sender_id,recipient_id,subject,body) VALUES(?,?,?,?)",
                (g.user["id"], recipient_id, subject, body),
            )
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
    if message["recipient_id"] == g.user["id"] and not message["is_read"]:
        database.execute("UPDATE mail SET is_read=1 WHERE id=?", (message_id,))
        database.commit()
    return render_template("mail/view.html", message=message)


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
    database.execute("DELETE FROM mail WHERE id=? AND sender_deleted=1 AND recipient_deleted=1", (message_id,))
    database.commit()
    flash("Mail removed from your mailbox.", "success")
    return redirect(url_for("mail.sent" if field == "sender_deleted" else "mail.inbox"))
