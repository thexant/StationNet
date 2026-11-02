"""Timed removal of bulletins and announcements."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .events import parse_local_time


def _now():
    # Same shape as stored expires_at values, so the strings compare in time order.
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_form(form):
    """Return (expires_at, timezone name, error); all None when no time was set."""
    date = form.get("expires_date", "").strip()
    time = form.get("expires_time", "").strip()
    zone = form.get("expires_timezone", "").strip()
    if not date and not time:
        return None, None, None
    if not date or not time:
        return None, None, "Provide both a removal date and a removal time."
    expires_at = parse_local_time(date, time, zone) if len(zone) <= 80 else None
    if expires_at is None:
        return None, None, "Enter a valid removal date, time, and IANA timezone."
    if expires_at <= _now():
        return None, None, "Removal time must be in the future."
    return expires_at, zone, None


def local_fields(expires_at, zone):
    """Form values for a stored removal time, or None when there isn't one."""
    if not expires_at:
        return None
    moment = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).astimezone(ZoneInfo(zone))
    return {"date": moment.strftime("%Y-%m-%d"), "time": moment.strftime("%H:%M"), "timezone": zone}


def purge_expired(database):
    """Delete everything due; replies and polls follow by cascade."""
    now = _now()
    due = database.execute(
        "SELECT EXISTS(SELECT 1 FROM posts WHERE expires_at IS NOT NULL AND expires_at<=?) "
        "OR EXISTS(SELECT 1 FROM announcements WHERE expires_at IS NOT NULL AND expires_at<=?)",
        (now, now),
    ).fetchone()[0]
    if not due:
        return
    database.execute("DELETE FROM posts WHERE expires_at IS NOT NULL AND expires_at<=?", (now,))
    database.execute("DELETE FROM announcements WHERE expires_at IS NOT NULL AND expires_at<=?", (now,))
    database.commit()
