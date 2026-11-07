"""Cold TV timelines load only relevant blocks and check shared media once."""
from datetime import date, datetime, timedelta, timezone
import sqlite3
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from flask import Flask, g
import pytest

from stationnet import db, tv, tv_library, tv_schedule as schedule

UTC = timezone.utc


@pytest.fixture
def television(tmp_path):
    app = Flask(__name__)
    app.config["DATA_DIR"] = str(tmp_path)
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute("CREATE TABLE station (id INTEGER PRIMARY KEY)")
    db._migrate_tv(database)
    with app.app_context():
        g.db = database
        g.station = {"tv_timezone": "UTC"}
        yield app, database
    database.close()


def _media(database, name="show", duration=1800, kind="program", missing=0):
    path = tv_library.folder("upload") / f"{name}.mp4"
    if not missing:
        path.write_bytes(b"test media")
    return database.execute(
        "INSERT INTO tv_media(title,source,rel_path,mime_type,duration_s,kind,missing) "
        "VALUES(?,'upload',?,'video/mp4',?,?,?)",
        (name, f"upload/{name}.mp4", duration, kind, missing)).lastrowid


def _channel(database, number=1, **values):
    channel_id = database.execute(
        "INSERT INTO tv_channels(number,name,gap_mode,offair_media_id) VALUES(?,?,?,?)",
        (number, f"Channel {number}", values.get("gap_mode", "offair"), values.get("offair_media_id"))).lastrowid
    return database.execute("SELECT * FROM tv_channels WHERE id=?", (channel_id,)).fetchone()


def _block(database, channel_id, media, **values):
    fields = dict(channel_id=channel_id, title="Block", kind="once", start_date="2026-11-06",
                  start_time="12:00", weekdays=0, loop_until=None, valid_from=None,
                  valid_until=None, enabled=1) | values
    block_id = database.execute(
        f"INSERT INTO tv_blocks({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
        tuple(fields.values())).lastrowid
    database.executemany("INSERT INTO tv_block_items(block_id,position,media_id) VALUES(?,?,?)",
                         [(block_id, position, item) for position, item in enumerate(media)])
    return block_id


def test_date_window_filters_block_rows_and_items(television, monkeypatch):
    app, database = television
    channel = _channel(database)
    show = _media(database)
    ignored = _media(database, "irrelevant")
    first, last = date(2026, 11, 4), date(2026, 11, 7)
    wanted = {
        _block(database, channel["id"], [show], start_date=first.isoformat()),
        _block(database, channel["id"], [show], start_date=last.isoformat()),
        _block(database, channel["id"], [show], kind="weekly", weekdays=127,
               valid_from=last.isoformat()),
        _block(database, channel["id"], [show], kind="weekly", weekdays=127,
               valid_until=first.isoformat()),
    }
    _block(database, channel["id"], [ignored], start_date="2026-11-03")
    _block(database, channel["id"], [ignored], kind="weekly", weekdays=127, valid_until="2026-11-03")
    _block(database, channel["id"], [ignored], kind="weekly", weekdays=127, valid_from="2026-11-08")
    disabled = _block(database, channel["id"], [ignored], enabled=0)
    for _ in range(1000):
        _block(database, channel["id"], [ignored], start_date="2027-01-01")

    with app.test_request_context():
        rows, items = tv._blocks(channel["id"], date_window=(first, last))
        assert {row["id"] for row in rows} == set(items) == wanted
        resolve = Mock(wraps=tv_library.resolve)
        monkeypatch.setattr(tv_library, "resolve", resolve)
        assert {block.id for block in tv._engine_blocks(channel["id"], (first, last))} == wanted
        assert resolve.call_count == 1
        assert resolve.call_args.args == ("upload/show.mp4",)
        all_rows, all_items = tv._blocks(channel["id"], include_disabled=True)
        assert len(all_rows) == len(all_items) == 1008
        assert disabled in all_items


def test_availability_is_shared_across_days_channels_and_gap_sources(television, monkeypatch):
    app, database = television
    show = _media(database)
    for number in (2, 1):
        channel = _channel(database, number, gap_mode="filler", offair_media_id=show)
        database.execute("INSERT INTO tv_channel_filler VALUES(?,0,?)", (channel["id"], show))
        _block(database, channel["id"], [show, show], kind="weekly", weekdays=127)
    now = datetime(2026, 11, 6, 12, 10, tzinfo=UTC)
    monkeypatch.setattr(tv, "_now", lambda: now)
    resolve = Mock(wraps=tv_library.resolve)
    monkeypatch.setattr(tv_library, "resolve", resolve)
    with app.test_request_context():
        listings = tv.activity_listings()
        assert [entry["number"] for entry in listings] == [1, 2]
        assert all(entry["now"]["kind"] == "program" for entry in listings)
        assert resolve.call_count == 1
        assert not tv._media_available({"rel_path": "upload/show.mp4", "missing": 1})
    with app.test_request_context():
        assert tv.activity_listings() == listings
        assert resolve.call_count == 1  # Warm day cache needs no filesystem checks.
    tv.bump_version(database)
    with app.test_request_context():
        assert tv.activity_listings() == listings
        assert resolve.call_count == 2
    tv_library.resolve("upload/show.mp4").unlink()
    tv.bump_version(database)
    resolve.reset_mock()
    with app.test_request_context():
        assert all(entry["now"]["kind"] == "standby" for entry in tv.activity_listings())
        assert resolve.call_count == 1  # Neither positive nor negative results cross requests.


@pytest.mark.parametrize("zone_name,day", [
    ("UTC", "2026-11-06"),
    ("Europe/London", "2026-03-29"),
    ("Europe/London", "2026-10-25"),
    ("Pacific/Auckland", "2026-11-06"),
])
@pytest.mark.parametrize("gap_mode", ["offair", "filler"])
def test_filtered_day_matches_unfiltered_engine(television, zone_name, day, gap_mode):
    app, database = television
    g.station = {"tv_timezone": zone_name}
    zone = ZoneInfo(zone_name)
    start = datetime.fromisoformat(day).replace(tzinfo=UTC)
    end = start + timedelta(days=1)
    local_day = start.astimezone(zone).date()
    show = _media(database)
    ident = _media(database, "ident", 15, "ident")
    missing = _media(database, "missing", 1800, missing=1)
    unknown = _media(database, "unknown", None)
    filler = _media(database, "filler", 3600)
    offair = _media(database, "offair", 600, "offair")
    channel = _channel(database, gap_mode=gap_mode, offair_media_id=offair)
    database.execute("INSERT INTO tv_channel_filler VALUES(?,0,?)", (channel["id"], filler))
    _block(database, channel["id"], [ident, show, missing, unknown], kind="weekly", weekdays=127,
           start_time="23:00", loop_until="02:30",
           valid_from=(local_day - timedelta(days=1)).isoformat(), valid_until=local_day.isoformat())
    _block(database, channel["id"], [show], start_date=local_day.isoformat(), start_time="01:15")
    _block(database, channel["id"], [show], start_date=local_day.isoformat(), start_time="08:00",
           title="override", loop_until="09:00")
    _block(database, channel["id"], [missing], kind="weekly", weekdays=127,
           start_time="08:00", loop_until="10:00")
    _block(database, channel["id"], [show], start_date=(local_day - timedelta(days=10)).isoformat())
    _block(database, channel["id"], [show], start_date=(local_day + timedelta(days=10)).isoformat())
    with app.test_request_context():
        gap_mode, filler, offair = tv._gap_sources(channel)
        expected = schedule.airings(tv._engine_blocks(channel["id"]), start, end, zone,
                                    gap_mode, filler, offair)
        actual = tv._day_timeline(channel, (start - tv.UNIX_EPOCH).days, zone, tv.version())
        assert actual == expected
        assert schedule.fold_interstitials(actual) == schedule.fold_interstitials(expected)


def test_activity_retains_seven_day_lookahead(television, monkeypatch):
    app, database = television
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    monkeypatch.setattr(tv, "_now", lambda: now)
    show = _media(database)
    channel = _channel(database)
    _block(database, channel["id"], [show], start_date="2026-11-12")
    beyond = _channel(database, 2)
    _block(database, beyond["id"], [show], start_date="2026-11-14")
    with app.test_request_context():
        listings = tv.activity_listings()
        assert [entry["number"] for entry in listings] == [1]
        assert listings[0]["now"]["kind"] == "offair"
        assert listings[0]["next"] == {"title": "show", "start": "2026-11-12T12:00:00.000Z", "far": True}
