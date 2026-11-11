"""Station notification centre: alerts for replies, mentions, reminders, and SYSOP notices.

``notify`` runs inside the caller's transaction and never commits, so an alert is
stored exactly when the content that caused it is.
"""
from datetime import datetime, timedelta, timezone
import re

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import modules
from .auth import login_required
from .db import get_db
from .realtime import utc_now
from .security import validate_csrf

bp = Blueprint("notifications", __name__, url_prefix="/notifications")

KINDS = {
    "reply": "REPLIES TO YOUR POSTS",
    "mention": "@MENTIONS",
    "comment": "FEED COMMENTS",
    "reaction": "FEED REACTIONS",
    "event": "EVENT REMINDERS",
    "report": "REPORT OUTCOMES",
    "mud_tell": "MUD TELLS",
    "moderation": "SYSOP NOTICES",
    "system": "SYSTEM MESSAGES",
}
# Members cannot opt out of what the SYSOP or the station itself sends them.
MUTABLE_KINDS = tuple(kind for kind in KINDS if kind not in {"moderation", "system"})
PAGE_SIZE = 50
MAX_MENTIONS = 10
MENTION_RE = re.compile(r"(?<![\w@/.-])@([A-Za-z0-9_-]{3,24})(?![A-Za-z0-9_-])")


def _muted(database, user_id, kind):
    return database.execute(
        "SELECT 1 FROM notification_mutes WHERE user_id=? AND kind=?", (user_id, kind)
    ).fetchone() is not None


def _blocked(database, user_id, actor_id):
    return database.execute(
        "SELECT 1 FROM mud_user_blocks WHERE user_id=? AND blocked_user_id=?", (user_id, actor_id)
    ).fetchone() is not None


def notify(database, user_id, kind, text, *, url="", body="", actor_id=None,
           dedupe_key=None, once=False):
    """Store one alert for ``user_id``; returns False when it was suppressed.

    With ``dedupe_key`` a repeat replaces the earlier alert and marks it unread
    again, unless ``once`` is set, in which case the first alert is kept as is.
    """
    if kind not in KINDS or not user_id:
        raise ValueError(f"Unknown notification kind: {kind}")
    if actor_id is not None and actor_id == user_id:
        return False
    if kind in MUTABLE_KINDS and _muted(database, user_id, kind):
        return False
    if actor_id is not None and kind in MUTABLE_KINDS and _blocked(database, user_id, actor_id):
        return False
    url = url if isinstance(url, str) and url.startswith("/") and not url.startswith("//") else ""
    values = (user_id, kind, actor_id, text[:300], body[:4000], url, dedupe_key, utc_now())
    if dedupe_key is None:
        database.execute(
            "INSERT INTO notifications(user_id,kind,actor_id,text,body,url,dedupe_key,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)", values,
        )
    elif once:
        database.execute(
            "INSERT INTO notifications(user_id,kind,actor_id,text,body,url,dedupe_key,created_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,dedupe_key) WHERE dedupe_key IS NOT NULL "
            "DO NOTHING", values,
        )
    else:
        # Delete and reinsert so a coalesced alert gets a fresh id; the header
        # badge counts ids above its cursor to decide when to chime.
        database.execute("DELETE FROM notifications WHERE user_id=? AND dedupe_key=?",
                         (user_id, dedupe_key))
        database.execute(
            "INSERT INTO notifications(user_id,kind,actor_id,text,body,url,dedupe_key,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)", values,
        )
    return True


def mentioned_users(database, text):
    """Distinct accounts named with @username in ``text``, at most MAX_MENTIONS."""
    names = []
    for match in MENTION_RE.finditer(text or ""):
        name = match.group(1)
        if name.casefold() not in {item.casefold() for item in names}:
            names.append(name)
        if len(names) >= MAX_MENTIONS:
            break
    users = []
    for name in names:
        row = database.execute("SELECT id,username FROM users WHERE username=?", (name,)).fetchone()
        if row is not None:
            users.append(row)
    return users


def notify_mentions(database, text, url, actor, where, skip=()):
    """Alert everyone @mentioned in ``text`` except the author and ids in ``skip``."""
    notified = []
    for user in mentioned_users(database, text):
        if user["id"] in skip:
            continue
        if notify(database, user["id"], "mention",
                  f"{actor['username']} mentioned you in {where}",
                  url=url, body=excerpt(text), actor_id=actor["id"]):
            notified.append(user["id"])
    return notified


def excerpt(text, length=240):
    text = " ".join((text or "").split())
    return text if len(text) <= length else text[:length - 1] + "…"


def latest_id(database, user_id):
    return database.execute(
        "SELECT COALESCE(MAX(id),0) FROM notifications WHERE user_id=?", (user_id,)
    ).fetchone()[0]


def unread_count(database, user_id):
    return database.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL", (user_id,)
    ).fetchone()[0]


def counts(database, user_id, after=0):
    return {
        "latest_id": latest_id(database, user_id),
        "unread_count": unread_count(database, user_id),
        "new_count": database.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL AND id>?",
            (user_id, max(0, after)),
        ).fetchone()[0],
    }


def mark_read(database, user_id, kind=None):
    query = "UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL"
    params = [utc_now(), user_id]
    if kind is not None:
        query += " AND kind=?"
        params.append(kind)
    return database.execute(query, params).rowcount


def _utc_z(moment):
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def upcoming_rsvps(database, user_id, hours=24, now=None):
    """Events this member answered going/interested to that start within ``hours``."""
    now = now or datetime.now(timezone.utc)
    return database.execute(
        "SELECT e.id,e.name,e.start_at,e.location,r.response FROM events e "
        "JOIN event_rsvps r ON r.event_id=e.id AND r.user_id=? "
        "WHERE e.cancelled=0 AND e.start_at>=? AND e.start_at<=? ORDER BY e.start_at,e.id",
        (user_id, _utc_z(now), _utc_z(now + timedelta(hours=hours))),
    ).fetchall()


def ensure_event_reminders(database, user_id, now=None):
    """Create each pending reminder once; there is no scheduler, so callers poll this."""
    if not g.station or not g.station["events_enabled"]:
        return 0
    created = 0
    for event in upcoming_rsvps(database, user_id, now=now):
        if notify(database, user_id, "event", f"Starting soon: {event['name']}",
                  url=url_for("events.detail", event_id=event["id"]),
                  body=event["location"] or "", dedupe_key=f"event:{event['id']}", once=True):
            created += 1
    return created


def summary(database, user):
    """The WAITING FOR YOU lines shown on the welcome screen and the login door."""
    from . import door

    station = g.station
    items = []
    user_id = user["id"]
    if modules.enabled(station, "mail"):
        unread = database.execute(
            "SELECT COUNT(*) FROM mail WHERE recipient_id=? AND recipient_deleted=0 AND is_read=0",
            (user_id,),
        ).fetchone()[0]
        if unread:
            items.append({"text": f"{unread} NEW MAIL", "url": url_for("mail.inbox")})
    if modules.enabled(station, "chat"):
        from .chat import direct_unread
        try:
            unread, _alerts = direct_unread(claim=False)
        except (KeyError, RuntimeError):
            unread = []
        count = sum(item["count"] for item in unread)
        if count:
            items.append({"text": f"{count} UNREAD DIRECT MSG{'S' if count != 1 else ''}",
                          "url": url_for("chat.direct", user_id=unread[0]["user_id"])})
    alerts = unread_count(database, user_id)
    if alerts:
        items.append({"text": f"{alerts} ALERT{'S' if alerts != 1 else ''}",
                      "url": url_for("notifications.index")})
    if modules.enabled(station, "board"):
        since = door.previous_call(database, user_id)
        if since is not None:
            fresh = database.execute(
                "SELECT COUNT(*) FROM posts WHERE author_id<>? AND datetime(created_at)>datetime(?)",
                (user_id, since),
            ).fetchone()[0]
            if fresh:
                items.append({"text": f"{fresh} NEW BOARD MSG{'S' if fresh != 1 else ''}",
                              "url": url_for("board.index")})
    if station["events_enabled"]:
        for event in upcoming_rsvps(database, user_id)[:3]:
            items.append({"text": f"EVENT: {event['name'].upper()}", "when": event["start_at"],
                          "url": url_for("events.detail", event_id=event["id"])})
    if station["mud_enabled"] and station["mud_procedural_enabled"]:
        from . import mud
        daily = mud.daily_overview(database, user_id)
        if daily and daily["attempts_left"] and not daily["completed"]:
            items.append({"text": f"DUNGEON OF THE DAY: {daily['theme'].upper()} "
                                  f"({daily['attempts_left']} ATTEMPT{'S' if daily['attempts_left'] != 1 else ''} LEFT)",
                          "url": url_for("mud.index")})
    return items


@bp.get("")
@login_required
def index():
    database = get_db()
    page = max(1, request.args.get("page", 1, type=int))
    rows = database.execute(
        "SELECT n.*,u.username actor_name,u.callsign actor_callsign FROM notifications n "
        "LEFT JOIN users u ON u.id=n.actor_id WHERE n.user_id=? "
        "ORDER BY (n.read_at IS NULL) DESC,n.id DESC LIMIT ? OFFSET ?",
        (g.user["id"], PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
    ).fetchall()
    muted = {row["kind"] for row in database.execute(
        "SELECT kind FROM notification_mutes WHERE user_id=?", (g.user["id"],))}
    return render_template(
        "notifications/index.html", notifications=rows[:PAGE_SIZE], page=page,
        has_more=len(rows) > PAGE_SIZE, kinds=KINDS, mutable_kinds=MUTABLE_KINDS, muted=muted,
        unread=unread_count(database, g.user["id"]),
    )


@bp.get("/poll")
@login_required
def poll():
    """One request for every header badge: mail, direct chat, and alerts."""
    database = get_db()
    payload = {}
    if modules.enabled(g.station, "mail"):
        from .mail import counts as mail_counts
        payload["mail"] = mail_counts(database, g.user["id"], request.args.get("mail_after", 0, type=int))
    if modules.enabled(g.station, "chat"):
        from .chat import direct_unread
        unread, alerts = direct_unread(claim=request.args.get("claim") == "1")
        payload["direct"] = {"unread": unread, "alerts": alerts}
    if ensure_event_reminders(database, g.user["id"]):
        database.commit()
    payload["notifications"] = counts(database, g.user["id"], request.args.get("after", 0, type=int))
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/<int:notification_id>/open")
@login_required
def open_notification(notification_id):
    validate_csrf()
    database = get_db()
    row = database.execute("SELECT url FROM notifications WHERE id=? AND user_id=?",
                           (notification_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    database.execute("UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=?",
                     (utc_now(), notification_id))
    database.commit()
    return redirect(row["url"] or url_for("notifications.index"))


@bp.post("/read-all")
@login_required
def read_all():
    validate_csrf()
    database = get_db()
    mark_read(database, g.user["id"])
    database.commit()
    flash("All alerts marked read.", "success")
    return redirect(url_for("notifications.index"))


@bp.post("/clear")
@login_required
def clear_read():
    validate_csrf()
    database = get_db()
    database.execute("DELETE FROM notifications WHERE user_id=? AND read_at IS NOT NULL",
                     (g.user["id"],))
    database.commit()
    flash("Read alerts cleared.", "success")
    return redirect(url_for("notifications.index"))


@bp.post("/mutes")
@login_required
def save_mutes():
    validate_csrf()
    enabled = set(request.form.getlist("kind"))
    database = get_db()
    database.execute("DELETE FROM notification_mutes WHERE user_id=?", (g.user["id"],))
    database.executemany(
        "INSERT INTO notification_mutes(user_id,kind) VALUES(?,?)",
        [(g.user["id"], kind) for kind in MUTABLE_KINDS if kind not in enabled],
    )
    database.commit()
    flash("Alert preferences saved.", "success")
    return redirect(url_for("notifications.index") + "#alert-preferences")
