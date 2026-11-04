"""Timestamp display helpers.

Timestamps are stored in UTC. Pages render them inside ``<time data-local-time>``
with readable UTC fallback text; station.js rewrites them into the viewer's
local time. Plain text (MUD events, flash messages) uses ``utc_text``, whose
"YYYY-MM-DD HH:MM UTC" form station.js also recognises and converts.
"""

from datetime import datetime, timezone

from markupsafe import Markup, escape

STYLES = ("datetime", "date", "time", "full")


def parse_utc(value):
    """Return an aware UTC datetime from a datetime, SQLite string or ISO string, else None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if text.endswith(" UTC"):
            text = text[:-4]
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def utc_iso(value):
    """Normalized ``YYYY-MM-DDTHH:MM:SSZ``, or None when the value is not a timestamp."""
    moment = parse_utc(value)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def utc_text(value, empty=""):
    """Readable ``YYYY-MM-DD HH:MM UTC`` text that station.js localizes."""
    moment = parse_utc(value)
    return moment.strftime("%Y-%m-%d %H:%M UTC") if moment else empty


def local_time(value, style="datetime", empty=""):
    """A ``<time>`` element that station.js shows in the viewer's local time."""
    iso = utc_iso(value)
    if iso is None:
        return escape(value) if value not in (None, "") else empty
    if style not in STYLES:
        style = "datetime"
    fallback = iso[:10] if style == "date" else utc_text(value)
    return Markup('<time datetime="{}" data-local-time="{}">{}</time>').format(iso, style, fallback)


def init_app(app):
    app.add_template_filter(local_time, "localtime")
    app.add_template_filter(utc_text, "utctext")
