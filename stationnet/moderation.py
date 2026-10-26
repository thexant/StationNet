"""Sysop moderation, reports, and accountable content removal."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .auth import admin_required, login_required
from .account_deletion import delete_account, is_station_owner
from .db import get_db
from .realtime import utc_now
from .security import validate_csrf

bp = Blueprint("moderation", __name__, url_prefix="/sysop/moderation")
report_bp = Blueprint("reports", __name__, url_prefix="/reports")


def active_restriction(user_id, kinds):
    placeholders = ",".join("?" for _ in kinds)
    return get_db().execute(
        f"SELECT * FROM moderation_restrictions WHERE user_id=? AND kind IN ({placeholders}) "
        "AND ended_at IS NULL AND (expires_at IS NULL OR expires_at>?) "
        "ORDER BY CASE WHEN kind='ban' THEN 0 ELSE 1 END,id DESC LIMIT 1", (user_id, *kinds, utc_now()),
    ).fetchone()


def audit(database, actor_id, user_id, action, reason="", target_type=None, target_id=None, note=""):
    database.execute(
        "INSERT INTO moderation_audit(actor_id,user_id,action,target_type,target_id,reason,private_note,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (actor_id, user_id, action, target_type, target_id, reason, note, utc_now()),
    )


@bp.get("")
@admin_required
def dashboard():
    database = get_db()
    query = request.args.get("q", "").strip()[:80]
    users = database.execute(
        "SELECT u.id,u.username,u.callsign,u.is_admin,u.created_at,u.last_seen, "
        "(SELECT COUNT(*) FROM moderation_audit a WHERE a.user_id=u.id AND a.action='warn') warnings, "
        "(SELECT COUNT(*) FROM posts p WHERE p.author_id=u.id) bulletins, "
        "(SELECT COUNT(*) FROM station_chat_messages m WHERE m.author_id=u.id) messages, "
        "(SELECT kind FROM moderation_restrictions r WHERE r.user_id=u.id AND r.kind IN ('ban','suspension') "
        "AND r.ended_at IS NULL AND (r.expires_at IS NULL OR r.expires_at>?) ORDER BY CASE WHEN r.kind='ban' THEN 0 ELSE 1 END,r.id DESC LIMIT 1) account_status, "
        "(SELECT 1 FROM moderation_restrictions r WHERE r.user_id=u.id AND r.kind='mute' "
        "AND r.ended_at IS NULL AND (r.expires_at IS NULL OR r.expires_at>?) LIMIT 1) muted "
        "FROM users u WHERE u.username LIKE ? OR u.callsign LIKE ? ORDER BY u.username LIMIT 100",
        (utc_now(), utc_now(), f"%{query}%", f"%{query}%"),
    ).fetchall()
    reports = database.execute(
        "SELECT r.*,u.username reporter,s.username subject FROM moderation_reports r "
        "JOIN users u ON u.id=r.reporter_id LEFT JOIN users s ON s.id=r.subject_user_id "
        "WHERE r.status='open' AND (? OR r.target_type NOT IN ('feed_post','feed_comment')) "
        "AND (? OR r.target_type NOT IN ('imageboard_thread','imageboard_reply')) "
        "ORDER BY r.id DESC LIMIT 100",
        (g.station["user_feeds_enabled"], g.station["imageboard_enabled"]),
    ).fetchall()
    history = database.execute(
        "SELECT a.*,u.username actor,s.username subject FROM moderation_audit a "
        "LEFT JOIN users u ON u.id=a.actor_id LEFT JOIN users s ON s.id=a.user_id "
        "WHERE a.action LIKE ? OR u.username LIKE ? OR s.username LIKE ? OR a.reason LIKE ? OR a.created_at LIKE ? "
        "ORDER BY a.id DESC LIMIT 100", (f"%{query}%",) * 5,
    ).fetchall()
    return render_template("moderation/index.html", users=users, reports=reports, history=history, query=query)


@bp.get("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    database = get_db()
    user = database.execute("SELECT id,username,callsign,is_admin,created_at,last_seen FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None: abort(404)
    restrictions = database.execute(
        "SELECT r.*,a.username actor FROM moderation_restrictions r LEFT JOIN users a ON a.id=r.actor_id "
        "WHERE r.user_id=? ORDER BY r.id DESC LIMIT 100", (user_id,),
    ).fetchall()
    history = database.execute(
        "SELECT a.*,u.username actor FROM moderation_audit a LEFT JOIN users u ON u.id=a.actor_id "
        "WHERE a.user_id=? ORDER BY a.id DESC LIMIT 100", (user_id,),
    ).fetchall()
    groups = database.execute(
        "SELECT g.name FROM radio_group_memberships m JOIN radio_permission_groups g ON g.id=m.group_id "
        "WHERE m.user_id=? ORDER BY g.name", (user_id,),
    ).fetchall()
    return render_template("moderation/user.html", user=user, restrictions=restrictions, history=history,
                           groups=groups, now=utc_now(), can_delete=not is_station_owner(database, user_id))


@bp.post("/users/<int:user_id>/delete")
@admin_required
def delete_user_account(user_id):
    validate_csrf()
    database = get_db()
    user = database.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    if is_station_owner(database, user_id):
        abort(403)
    if (request.form.get("username") != user["username"]
            or request.form.get("confirm") != "on"):
        abort(400, "Account deletion confirmation is incomplete.")
    delete_account(user_id)
    if user_id == g.user["id"]:
        from flask import session
        session.clear()
        return redirect(url_for("station.index"))
    flash(f"Account {user['username']} and all its content were deleted.", "success")
    return redirect(url_for("moderation.dashboard"))


@bp.post("/users/<int:user_id>/action")
@admin_required
def user_action(user_id):
    validate_csrf()
    database = get_db()
    user = database.execute("SELECT id,is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None: abort(404)
    action = request.form.get("action", "")
    if action not in {"warn", "mute", "suspend", "ban", "reinstate", "unmute"}: abort(400)
    if user_id == g.user["id"] or (user["is_admin"] and action in {"mute", "suspend", "ban"}):
        abort(403)
    reason = request.form.get("reason", "").strip()[:500]
    note = request.form.get("private_note", "").strip()[:1000]
    if not reason: abort(400, "A reason is required.")
    if action in {"mute", "suspend", "ban"}:
        duration = request.form.get("duration", "")
        if action == "suspend" and duration not in {"1", "24", "168", "720"}: abort(400)
        if action == "ban" and duration != "indefinite": abort(400)
        if action == "mute" and duration not in {"1", "24", "168", "720", "indefinite"}: abort(400)
        kind = {"mute": "mute", "suspend": "suspension", "ban": "ban"}[action]
        expires = None if duration == "indefinite" else (datetime.now(timezone.utc) + timedelta(hours=int(duration))).replace(microsecond=0).isoformat()
        database.execute(
            "INSERT INTO moderation_restrictions(user_id,kind,starts_at,expires_at,actor_id,reason,private_note) "
            "VALUES(?,?,?,?,?,?,?)", (user_id, kind, utc_now(), expires, g.user["id"], reason, note),
        )
    elif action == "warn":
        cursor = database.execute(
            "INSERT INTO mail(sender_id,recipient_id,subject,body,sender_deleted) VALUES(?,?,?,?,1)",
            (g.user["id"], user_id, "Formal SYSOP warning", reason),
        )
        database.execute("UPDATE mail SET thread_id=id WHERE id=?", (cursor.lastrowid,))
    elif action in {"reinstate", "unmute"}:
        kinds = ("suspension", "ban") if action == "reinstate" else ("mute",)
        placeholders = ",".join("?" for _ in kinds)
        database.execute(
            f"UPDATE moderation_restrictions SET ended_at=? WHERE user_id=? AND kind IN ({placeholders}) "
            "AND ended_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
            (utc_now(), user_id, *kinds, utc_now()),
        )
    audit(database, g.user["id"], user_id, action, reason, note=note)
    database.commit()
    flash("Moderation action recorded.", "success")
    return redirect(url_for("moderation.user_detail", user_id=user_id))


@bp.post("/reports/<int:report_id>/resolve")
@admin_required
def resolve_report(report_id):
    validate_csrf()
    status = request.form.get("status")
    if status not in {"resolved", "dismissed"}: abort(400)
    note = request.form.get("note", "").strip()[:1000]
    if not note: abort(400, "Resolution note is required.")
    database = get_db()
    report = database.execute("SELECT * FROM moderation_reports WHERE id=? AND status='open'", (report_id,)).fetchone()
    if report is None: abort(404)
    if report["target_type"] in {"feed_post", "feed_comment"} and not g.station["user_feeds_enabled"]: abort(404)
    if report["target_type"] in {"imageboard_thread", "imageboard_reply"} and not g.station["imageboard_enabled"]: abort(404)
    if report["target_type"] == "library_book" and not g.station["library_enabled"]: abort(404)
    database.execute("UPDATE moderation_reports SET status=?,resolution_note=?,resolver_id=?,resolved_at=? WHERE id=?",
                     (status, note, g.user["id"], utc_now(), report_id))
    audit(database, g.user["id"], report["subject_user_id"], f"report_{status}", note,
          report["target_type"], report["target_id"])
    database.commit()
    flash("Report reviewed.", "success")
    return redirect(url_for("moderation.dashboard"))


@bp.post("/chat/<int:message_id>/delete")
@admin_required
def delete_chat(message_id):
    validate_csrf()
    database = get_db()
    message = database.execute("SELECT author_id FROM station_chat_messages WHERE id=?", (message_id,)).fetchone()
    if message is None: abort(404)
    author = database.execute("SELECT is_admin FROM users WHERE id=?", (message["author_id"],)).fetchone()
    if author["is_admin"] and message["author_id"] != g.user["id"]: abort(403)
    reason = request.form.get("reason", "").strip()[:500]
    if not reason: abort(400, "A reason is required.")
    database.execute("DELETE FROM station_chat_messages WHERE id=?", (message_id,))
    database.execute("INSERT INTO station_chat_deletions(message_id,deleted_at) VALUES(?,?)", (message_id, utc_now()))
    audit(database, g.user["id"], message["author_id"], "delete_chat", reason, "chat", message_id)
    database.commit()
    return jsonify(ok=True)


@bp.post("/chat/users/<int:user_id>/delete")
@admin_required
def delete_user_chat(user_id):
    validate_csrf()
    reason = request.form.get("reason", "").strip()[:500]
    if not reason or request.form.get("confirm") != "DELETE": abort(400)
    database = get_db()
    user = database.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None: abort(404)
    if user["is_admin"]: abort(403)
    rows = database.execute("SELECT id FROM station_chat_messages WHERE author_id=? ORDER BY id DESC LIMIT 500", (user_id,)).fetchall()
    for row in rows:
        database.execute("INSERT INTO station_chat_deletions(message_id,deleted_at) VALUES(?,?)", (row["id"], utc_now()))
        database.execute("DELETE FROM station_chat_messages WHERE id=?", (row["id"],))
    audit(database, g.user["id"], user_id, "delete_user_chat", f"{reason} ({len(rows)} messages)")
    database.commit()
    flash(f"Removed {len(rows)} recent chat messages.", "success")
    return redirect(url_for("moderation.user_detail", user_id=user_id))


@report_bp.post("")
@login_required
def create_report():
    validate_csrf()
    target_type = request.form.get("target_type", "")
    target_id = request.form.get("target_id", type=int)
    if request.form.get("target") and not target_type:
        target_type, _, raw_id = request.form["target"].partition(":")
        target_id = int(raw_id) if raw_id.isdecimal() else None
    reason = request.form.get("reason", "").strip()[:500]
    if target_type not in {"user", "chat", "bulletin", "file", "feed_post", "feed_comment", "imageboard_thread", "imageboard_reply", "library_book"} or not target_id or len(reason) < 10: abort(400)
    if target_type in {"feed_post", "feed_comment"} and not g.station["user_feeds_enabled"]: abort(404)
    if target_type in {"imageboard_thread", "imageboard_reply"} and not g.station["imageboard_enabled"]: abort(404)
    if target_type == "library_book" and not g.station["library_enabled"]: abort(404)
    database = get_db()
    if target_type == "user":
        target = database.execute("SELECT id subject FROM users WHERE id=?", (target_id,)).fetchone()
    else:
        table, owner = {"chat": ("station_chat_messages", "author_id"),
                        "bulletin": ("posts", "author_id"), "file": ("archive_files", "uploader_id"),
                        "feed_post": ("user_feed_posts", "owner_id"),
                        "feed_comment": ("user_feed_comments", "author_id"),
                        "imageboard_thread": ("imageboard_threads", "author_id"),
                        "imageboard_reply": ("imageboard_replies", "author_id"),
                        "library_book": ("library_books", "uploader_id")}[target_type]
        target = database.execute(f"SELECT {owner} subject FROM {table} WHERE id=?", (target_id,)).fetchone()
    if target is None: abort(404)
    if target["subject"] == g.user["id"]: abort(400, "You cannot report yourself.")
    recent = database.execute("SELECT COUNT(*) FROM moderation_reports WHERE reporter_id=? AND created_at>?",
                              (g.user["id"], (datetime.now(timezone.utc)-timedelta(hours=24)).isoformat())).fetchone()[0]
    if recent >= 10: abort(429, "Daily report limit reached.")
    duplicate = database.execute("SELECT 1 FROM moderation_reports WHERE reporter_id=? AND target_type=? AND target_id=? AND status='open'",
                                 (g.user["id"], target_type, target_id)).fetchone()
    if duplicate: abort(409, "You have already reported this item.")
    database.execute("INSERT INTO moderation_reports(reporter_id,target_type,target_id,subject_user_id,reason,created_at) "
                     "VALUES(?,?,?,?,?,?)", (g.user["id"], target_type, target_id, target["subject"], reason, utc_now()))
    database.commit()
    flash("Report sent to the sysops.", "success")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else url_for("station.terminal"))
