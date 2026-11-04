"""Timestamps render as UTC fallbacks that station.js shows in the viewer's local time."""
from datetime import datetime, timedelta, timezone

from stationnet import create_app, mud, timefmt


def test_parses_stored_forms_as_utc():
    expected = "2026-11-03T14:05:09Z"
    assert timefmt.utc_iso("2026-11-03 14:05:09") == expected  # SQLite CURRENT_TIMESTAMP
    assert timefmt.utc_iso("2026-11-03T14:05:09+00:00") == expected
    assert timefmt.utc_iso("2026-11-03T14:05:09Z") == expected
    assert timefmt.utc_iso("2026-11-03T08:05:09-06:00") == expected
    assert timefmt.utc_iso("2026-11-03 14:05 UTC") == "2026-11-03T14:05:00Z"
    assert timefmt.utc_iso(datetime(2026, 11, 3, 14, 5, 9)) == expected
    assert timefmt.utc_iso(datetime(2026, 11, 3, 14, 5, 9, tzinfo=timezone.utc)) == expected
    assert timefmt.utc_iso(None) is None
    assert timefmt.utc_iso("") is None
    assert timefmt.utc_iso("never") is None


def test_utc_text_and_markup():
    assert timefmt.utc_text("2026-11-03T14:05:09+00:00") == "2026-11-03 14:05 UTC"
    assert timefmt.utc_text(None, "now") == "now"
    assert str(timefmt.local_time("2026-11-03 14:05:09")) == (
        '<time datetime="2026-11-03T14:05:09Z" data-local-time="datetime">2026-11-03 14:05 UTC</time>')
    assert 'data-local-time="date">2026-11-03</time>' in str(timefmt.local_time("2026-11-03 14:05:09", "date"))
    assert timefmt.local_time(None, "datetime", "never") == "never"
    assert str(timefmt.local_time("<b>soon</b>")) == "&lt;b&gt;soon&lt;/b&gt;"


def test_filters_are_registered(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        rendered = app.jinja_env.from_string("{{ v|localtime }} / {{ v|utctext }}").render(v="2026-11-03 14:05:09")
    assert rendered == ('<time datetime="2026-11-03T14:05:09Z" data-local-time="datetime">2026-11-03 14:05 UTC</time>'
                        " / 2026-11-03 14:05 UTC")


def test_mud_moments_use_utc_text():
    assert mud._moment_text("2026-11-03T14:05:09+00:00") == "2026-11-03 14:05 UTC"
    assert mud._moment_text(None) == "now"


def test_daily_dungeon_next_rotation_is_local_midnight_in_its_zone():
    now = datetime(2026, 11, 3, 5, 30, tzinfo=timezone.utc)  # 14:30 on Nov 3 in Tokyo (UTC+9, no DST)
    tokyo = mud._daily_next_rotation({"timezone": "Asia/Tokyo"}, now)
    assert tokyo == datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    assert mud._daily_next_rotation({"timezone": "UTC"}, now) == datetime(2026, 11, 4, tzinfo=timezone.utc)
    assert mud._daily_next_rotation({"timezone": "Not/AZone"}, now) - now == timedelta(hours=18, minutes=30)
