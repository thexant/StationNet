"""Station event listings and RSVPs."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .db import get_db
from .security import validate_csrf

bp = Blueprint("events", __name__, url_prefix="/events")


@bp.before_request
def require_enabled():
    if not g.station or not g.station["events_enabled"]:
        abort(404)
    if not g.user and not g.station["guest_access"]:
        return redirect(url_for("auth.login", next=request.path))


def can_create():
    if not g.user or not g.station or not g.station["events_enabled"]:
        return False
    if g.user["is_admin"] or g.station["events_create_permission"] == "members":
        return True
    if g.station["events_create_permission"] != "groups":
        return False
    return get_db().execute(
        "SELECT 1 FROM event_creation_groups e JOIN radio_group_memberships m "
        "ON m.group_id=e.group_id WHERE m.user_id=? LIMIT 1", (g.user["id"],)
    ).fetchone() is not None


def _parse_time(date, time, zone_name):
    try:
        zone = ZoneInfo(zone_name)
        naive = datetime.fromisoformat(f"{date}T{time}")
        if naive.tzinfo or len(date) != 10 or len(time) != 5:
            raise ValueError
        local = naive.replace(tzinfo=zone)
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) != naive:
            raise ValueError
        return utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (ValueError, ZoneInfoNotFoundError, TypeError, KeyError):
        return None


def _form_data():
    data = {key: request.form.get(key, "").strip() for key in
            ("name", "description", "start_date", "start_time", "end_date", "end_time",
             "timezone", "location", "url")}
    if not 1 <= len(data["name"]) <= 150 or not 1 <= len(data["description"]) <= 5000:
        return None, "Name (1–150 characters) and description (1–5000 characters) are required."
    if len(data["location"]) > 200 or len(data["url"]) > 2000 or len(data["timezone"]) > 80:
        return None, "Location, URL, or timezone is too long."
    if any(ord(char) < 32 for char in data["name"] + data["location"]):
        return None, "Name and location cannot contain control characters."
    if any(ord(char) < 32 and char not in "\n\r\t" for char in data["description"]):
        return None, "Description contains invalid characters."
    if data["url"]:
        try:
            url = urlsplit(data["url"])
            valid_url = (url.scheme in ("http", "https") and bool(url.hostname)
                         and not url.username and not url.password
                         and not any(ord(char) <= 32 for char in data["url"]))
        except ValueError:
            valid_url = False
        if not valid_url:
            return None, "Enter an HTTP or HTTPS event URL."
    start = _parse_time(data["start_date"], data["start_time"], data["timezone"])
    if not start:
        return None, "Enter a valid start date, time, and IANA timezone."
    if bool(data["end_date"]) != bool(data["end_time"]):
        return None, "Provide both an end date and end time."
    end = _parse_time(data["end_date"], data["end_time"], data["timezone"]) if data["end_date"] else None
    if data["end_date"] and not end:
        return None, "Enter a valid end date and time."
    if end and end <= start:
        return None, "End time must follow start time."
    data["start_at"], data["end_at"] = start, end
    return data, None


def _event(event_id):
    event = get_db().execute(
        "SELECT e.*,u.username organizer,u.callsign organizer_callsign,"
        "(SELECT COUNT(*) FROM event_rsvps WHERE event_id=e.id AND response='going') going_count,"
        "(SELECT COUNT(*) FROM event_rsvps WHERE event_id=e.id AND response='interested') interested_count "
        "FROM events e LEFT JOIN users u ON u.id=e.creator_id WHERE e.id=?", (event_id,)
    ).fetchone()
    if event is None:
        abort(404)
    return event


def _status(event, now):
    if event["cancelled"]:
        return "Cancelled"
    if event["start_at"] > now:
        return "Upcoming"
    if event["end_at"] and event["end_at"] > now:
        return "In Progress"
    return "Ended"


@bp.get("")
@bp.get("/")
def index():
    rows = get_db().execute(
        "SELECT e.*,u.username organizer,"
        "(SELECT COUNT(*) FROM event_rsvps WHERE event_id=e.id AND response='going') going_count,"
        "(SELECT COUNT(*) FROM event_rsvps WHERE event_id=e.id AND response='interested') interested_count "
        "FROM events e LEFT JOIN users u ON u.id=e.creator_id ORDER BY e.start_at,e.id"
    ).fetchall()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    active = [(row, _status(row, now)) for row in rows if _status(row, now) in ("Upcoming", "In Progress")]
    past = [(row, _status(row, now)) for row in reversed(rows) if _status(row, now) in ("Ended", "Cancelled")]
    return render_template("events/index.html", active=active, past=past, can_create=can_create())


@bp.get("/<int:event_id>")
def detail(event_id):
    event = _event(event_id)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    members = get_db().execute(
        "SELECT r.response,u.id,u.username,u.callsign FROM event_rsvps r "
        "JOIN users u ON u.id=r.user_id WHERE r.event_id=? "
        "ORDER BY r.response,u.username", (event_id,)
    ).fetchall()
    response = next((m["response"] for m in members if g.user and m["id"] == g.user["id"]), None)
    can_edit = bool(g.user and (g.user["is_admin"] or event["creator_id"] == g.user["id"]))
    return render_template("events/detail.html", event=event, status=_status(event, now),
                           members=members, response=response, can_edit=can_edit)


@bp.route("/new", methods=("GET", "POST"))
def create():
    if not can_create():
        abort(403)
    if request.method == "POST":
        validate_csrf()
        data, error = _form_data()
        if error:
            flash(error, "error")
        else:
            cursor = get_db().execute(
                "INSERT INTO events(creator_id,name,description,start_at,end_at,timezone,location,url) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (g.user["id"], data["name"], data["description"], data["start_at"],
                 data["end_at"], data["timezone"], data["location"], data["url"]),
            )
            get_db().commit()
            flash("Event created.", "success")
            return redirect(url_for("events.detail", event_id=cursor.lastrowid))
    return render_template("events/form.html", event=None)


@bp.route("/<int:event_id>/edit", methods=("GET", "POST"))
def edit(event_id):
    event = _event(event_id)
    if not g.user or not (g.user["is_admin"] or event["creator_id"] == g.user["id"]):
        abort(403)
    if request.method == "POST":
        validate_csrf()
        data, error = _form_data()
        if error:
            flash(error, "error")
        else:
            get_db().execute(
                "UPDATE events SET name=?,description=?,start_at=?,end_at=?,timezone=?,location=?,url=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (data["name"], data["description"], data["start_at"], data["end_at"],
                 data["timezone"], data["location"], data["url"], event_id),
            )
            get_db().commit()
            flash("Event updated.", "success")
            return redirect(url_for("events.detail", event_id=event_id))
    zone = ZoneInfo(event["timezone"])
    start_local = datetime.fromisoformat(event["start_at"].replace("Z", "+00:00")).astimezone(zone)
    end_local = (datetime.fromisoformat(event["end_at"].replace("Z", "+00:00")).astimezone(zone)
                 if event["end_at"] else None)
    return render_template("events/form.html", event=event, start_local=start_local,
                           end_local=end_local)


@bp.post("/<int:event_id>/cancel")
def cancel(event_id):
    validate_csrf()
    event = _event(event_id)
    if not g.user or not (g.user["is_admin"] or event["creator_id"] == g.user["id"]):
        abort(403)
    get_db().execute("UPDATE events SET cancelled=1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (event_id,))
    get_db().commit()
    return redirect(url_for("events.detail", event_id=event_id))


@bp.post("/<int:event_id>/delete")
def delete(event_id):
    validate_csrf()
    if not g.user or not g.user["is_admin"]:
        abort(403)
    _event(event_id)
    get_db().execute("DELETE FROM events WHERE id=?", (event_id,))
    get_db().commit()
    return redirect(url_for("events.index"))


@bp.post("/<int:event_id>/rsvp")
def rsvp(event_id):
    validate_csrf()
    if not g.user:
        abort(403)
    response = request.form.get("response")
    if response not in ("going", "interested", "withdraw"):
        abort(400)
    database = get_db()
    database.execute("BEGIN IMMEDIATE")
    event = _event(event_id)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if _status(event, now) in ("Cancelled", "Ended"):
        database.rollback()
        abort(409)
    if response == "withdraw":
        database.execute("DELETE FROM event_rsvps WHERE event_id=? AND user_id=?", (event_id, g.user["id"]))
    else:
        database.execute(
            "INSERT INTO event_rsvps(event_id,user_id,response) VALUES(?,?,?) "
            "ON CONFLICT(event_id,user_id) DO UPDATE SET response=excluded.response,updated_at=CURRENT_TIMESTAMP",
            (event_id, g.user["id"], response),
        )
    database.commit()
    return redirect(url_for("events.detail", event_id=event_id))
