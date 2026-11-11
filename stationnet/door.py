"""The login door: a BBS-style sequence of station bulletins shown after each call."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import header_taglines, notifications
from .auth import admin_required, login_required
from .db import get_db
from .moderation import active_restriction, audit
from .realtime import utc_now
from .security import validate_csrf

bp = Blueprint("door", __name__)

PANELS = {
    "banner": "Station banner",
    "waiting": "Waiting for you",
    "quote": "Quote of the day",
    "callers": "Last callers",
    "oneliners": "Oneliners wall",
    "events": "Upcoming events",
    "daily": "MUD Dungeon of the Day",
}
DEFAULT_PANELS = tuple(PANELS)
CALL_HISTORY = 500
ONELINER_HISTORY = 200
ONELINER_LENGTH = 80
MAX_QUOTES = 100
QUOTE_LENGTH = 200


def configured_panels(station):
    """The enabled panel keys in display order."""
    raw = station["door_panels_json"] if station is not None else None
    if raw is None:
        return list(DEFAULT_PANELS)
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return list(DEFAULT_PANELS)
    if not isinstance(values, list):
        return list(DEFAULT_PANELS)
    seen = []
    for key in values:
        if isinstance(key, str) and key in PANELS and key not in seen:
            seen.append(key)
    return seen


def enabled(station):
    return bool(station is not None and station["door_enabled"])


# Opening the door again this soon after a call (a refresh, or arriving
# straight from sign-in) does not count as another call.
CALL_REPEAT_WINDOW = timedelta(minutes=10)


def record_call_unless_recent(database, user_id, now=None):
    """Count a visit to the door as a call, unless this member called moments ago."""
    from .timefmt import parse_utc
    row = database.execute(
        "SELECT called_at FROM station_calls WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
    ).fetchone()
    latest = parse_utc(row["called_at"]) if row else None
    if latest is not None and (now or datetime.now(timezone.utc)) - latest < CALL_REPEAT_WINDOW:
        return False
    record_call(database, user_id)
    return True


def record_call(database, user_id):
    """Log one call (login); the caller commits."""
    cursor = database.execute("INSERT INTO station_calls(user_id,called_at) VALUES(?,?)",
                              (user_id, utc_now()))
    database.execute("DELETE FROM station_calls WHERE id<=?", (cursor.lastrowid - CALL_HISTORY,))
    return cursor.lastrowid


def previous_call(database, user_id):
    """When this member called before the current call, or None on a first call."""
    row = database.execute(
        "SELECT called_at FROM station_calls WHERE user_id=? ORDER BY id DESC LIMIT 1 OFFSET 1",
        (user_id,),
    ).fetchone()
    return row["called_at"] if row else None


def last_callers(database, limit=10):
    """The most recent distinct callers, each shown once at their latest call."""
    return database.execute(
        "SELECT c.id,c.called_at,u.id user_id,u.username,u.callsign FROM station_calls c "
        "JOIN users u ON u.id=c.user_id "
        "WHERE c.id=(SELECT MAX(id) FROM station_calls WHERE user_id=c.user_id) "
        "ORDER BY c.id DESC LIMIT ?", (limit,),
    ).fetchall()


def quotes(station):
    return [line.strip() for line in (station["door_quotes"] or "").splitlines() if line.strip()]


def quote_of_the_day(station, today=None):
    """A stable pick for the whole day: station quotes first, then header taglines."""
    pool = quotes(station) or [item["text"] for item in header_taglines.configured(station)]
    if not pool:
        return None
    day = (today or date.today()).isoformat()
    index = int(hashlib.sha256(f"{station['callsign']}|{day}".encode()).hexdigest(), 16) % len(pool)
    return pool[index]


def recent_oneliners(database, limit=10):
    return database.execute(
        "SELECT o.id,o.body,o.created_at,o.author_id,u.username,u.callsign FROM oneliners o "
        "JOIN users u ON u.id=o.author_id ORDER BY o.id DESC LIMIT ?", (limit,),
    ).fetchall()


def upcoming_events(database, now=None, hours=48, limit=5):
    now = now or datetime.now(timezone.utc)

    def z(moment):
        return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    return database.execute(
        "SELECT id,name,start_at,location FROM events WHERE cancelled=0 "
        "AND start_at>=? AND start_at<=? ORDER BY start_at,id LIMIT ?",
        (z(now - timedelta(hours=1)), z(now + timedelta(hours=hours)), limit),
    ).fetchall()


def _muted():
    return active_restriction(g.user["id"], ("mute",)) is not None


@bp.get("/door")
@login_required
def door():
    if not enabled(g.station):
        return redirect(url_for("station.terminal"))
    database = get_db()
    if record_call_unless_recent(database, g.user["id"]):
        database.commit()
    if notifications.ensure_event_reminders(database, g.user["id"]):
        database.commit()
    panels = configured_panels(g.station)
    context = {"panels": panels, "panel_labels": PANELS}
    if "waiting" in panels:
        context["waiting"] = notifications.summary(database, g.user)
    if "quote" in panels:
        context["quote"] = quote_of_the_day(g.station)
    if "callers" in panels:
        context["callers"] = last_callers(database)
        context["previous_call"] = previous_call(database, g.user["id"])
    if "oneliners" in panels:
        context["oneliners"] = recent_oneliners(database)
        context["can_post_oneliner"] = not _muted()
    if "events" in panels and g.station["events_enabled"]:
        context["events"] = upcoming_events(database)
    if "daily" in panels and g.station["mud_enabled"] and g.station["mud_procedural_enabled"]:
        from . import mud
        context["daily"] = mud.daily_overview(database, g.user["id"])
        database.commit()
    return render_template("door.html", **context)


@bp.post("/door/oneliners")
@login_required
def post_oneliner():
    validate_csrf()
    if _muted():
        abort(403, "Your account is muted.")
    body = " ".join(request.form.get("body", "").split())
    if not 1 <= len(body) <= ONELINER_LENGTH:
        flash(f"Oneliners must be 1 to {ONELINER_LENGTH} characters.", "error")
        return redirect(url_for("door.door") + "#oneliners")
    database = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat()
    if database.execute("SELECT 1 FROM oneliners WHERE author_id=? AND created_at>=?",
                        (g.user["id"], cutoff)).fetchone():
        flash("One oneliner per minute. Try again shortly.", "error")
        return redirect(url_for("door.door") + "#oneliners")
    cursor = database.execute("INSERT INTO oneliners(author_id,body,created_at) VALUES(?,?,?)",
                              (g.user["id"], body, utc_now()))
    database.execute("DELETE FROM oneliners WHERE id<=?", (cursor.lastrowid - ONELINER_HISTORY,))
    notifications.notify_mentions(database, body, url_for("door.door") + "#oneliners",
                                  g.user, "the oneliners wall")
    database.commit()
    flash("Oneliner posted.", "success")
    return redirect(url_for("door.door") + "#oneliners")


@bp.post("/door/oneliners/<int:oneliner_id>/delete")
@login_required
def delete_oneliner(oneliner_id):
    validate_csrf()
    database = get_db()
    row = database.execute("SELECT id,author_id FROM oneliners WHERE id=?", (oneliner_id,)).fetchone()
    if row is None:
        abort(404)
    own = row["author_id"] == g.user["id"]
    if not own and not g.user["is_admin"]:
        abort(403)
    database.execute("DELETE FROM oneliners WHERE id=?", (oneliner_id,))
    if not own:
        audit(database, g.user["id"], row["author_id"], "delete_oneliner",
              request.form.get("reason", "SYSOP removal")[:500], "oneliner", oneliner_id)
    database.commit()
    flash("Oneliner removed.", "success")
    return redirect(url_for("door.door") + "#oneliners")


@bp.post("/sysop/settings/door")
@admin_required
def save_settings():
    validate_csrf()
    destination = url_for("station.settings") + "#login-sequence"
    chosen = set(request.form.getlist("panel"))
    positions = []
    for index, key in enumerate(PANELS):
        try:
            position = int(request.form.get(f"position_{key}", index + 1))
        except ValueError:
            position = index + 1
        positions.append((position, index, key))
    panels = [key for _position, _index, key in sorted(positions) if key in chosen]
    lines = [line.strip() for line in request.form.get("door_quotes", "").splitlines() if line.strip()]
    if len(lines) > MAX_QUOTES:
        flash(f"The login sequence can hold up to {MAX_QUOTES} quotes.", "error")
        return redirect(destination)
    if any(len(line) > QUOTE_LENGTH for line in lines):
        flash(f"Each quote must be {QUOTE_LENGTH} characters or fewer.", "error")
        return redirect(destination)
    database = get_db()
    database.execute(
        "UPDATE station SET door_enabled=?,door_panels_json=?,door_quotes=? WHERE id=1",
        (1 if request.form.get("door_enabled") == "on" else 0, json.dumps(panels), "\n".join(lines)),
    )
    database.commit()
    flash("Login sequence updated.", "success")
    return redirect(destination)
