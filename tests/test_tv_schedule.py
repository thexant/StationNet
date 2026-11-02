"""Pure TV scheduling engine: occurrences, layout, overrides, gaps and DST."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from stationnet.tv_schedule import Block, Item, airings, at, merge, resolve

UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")


def _item(media_id, minutes, available=True, title=None):
    return Item(media_id, title or f"Show {media_id}", minutes * 60, available=available)


def _utc(*parts):
    return datetime(*parts, tzinfo=UTC)


def test_consecutive_items_follow_each_other():
    block = Block(1, "Evening", "once", "14:00", (_item(1, 30), _item(2, 45)), start_date="2026-01-05")
    result, _ = resolve([block], _utc(2026, 1, 5), _utc(2026, 1, 6), UTC)
    assert [(a.start, a.end, a.media_id) for a in result] == [
        (_utc(2026, 1, 5, 14), _utc(2026, 1, 5, 14, 30), 1),
        (_utc(2026, 1, 5, 14, 30), _utc(2026, 1, 5, 15, 15), 2),
    ]


def test_tuning_in_late_starts_mid_programme():
    block = Block(1, "Film", "once", "14:00", (_item(7, 120),), start_date="2026-01-05")
    now = _utc(2026, 1, 5, 14, 35)
    result = airings([block], now, now + timedelta(hours=1), UTC)
    current = result[at(result, now)]
    assert current.media_id == 7
    assert current.media_offset == 35 * 60


def test_weekly_block_keeps_local_time_across_dst():
    block = Block(1, "News", "weekly", "20:00", (_item(1, 30),), weekdays=1 << 5)  # Saturdays
    winter, _ = resolve([block], _utc(2026, 3, 21), _utc(2026, 3, 22), LONDON)
    summer, _ = resolve([block], _utc(2026, 4, 4), _utc(2026, 4, 5), LONDON)
    assert winter[0].start == _utc(2026, 3, 21, 20)
    assert summer[0].start == _utc(2026, 4, 4, 19)  # 20:00 BST


def test_weekly_validity_range():
    block = Block(1, "Run", "weekly", "10:00", (_item(1, 10),), weekdays=127,
                  valid_from="2026-02-02", valid_until="2026-02-03")
    result, _ = resolve([block], _utc(2026, 2, 1), _utc(2026, 2, 5), UTC)
    assert [a.start.day for a in result] == [2, 3]


def test_loop_until_repeats_and_cuts_at_boundary():
    block = Block(1, "Cartoons", "once", "08:00", (_item(1, 25), _item(2, 20)),
                  start_date="2026-01-05", loop_until="09:15")
    result, _ = resolve([block], _utc(2026, 1, 5), _utc(2026, 1, 6), UTC)
    assert [a.media_id for a in result] == [1, 2, 1, 2]
    assert result[-1].end == _utc(2026, 1, 5, 9, 15)
    assert result[-1].seconds == 5 * 60


def test_loop_past_midnight():
    block = Block(1, "Night", "once", "23:00", (_item(1, 60),), start_date="2026-01-05", loop_until="02:00")
    result, _ = resolve([block], _utc(2026, 1, 5), _utc(2026, 1, 7), UTC)
    assert result[0].start == _utc(2026, 1, 5, 23) and result[-1].end == _utc(2026, 1, 6, 2)


def test_once_beats_weekly_and_underlying_block_resumes():
    weekly = Block(1, "All day", "weekly", "00:00", (_item(1, 60),), weekdays=127, loop_until="00:00")
    special = Block(2, "Special", "once", "12:15", (_item(9, 30),), start_date="2026-01-05")
    result, conflicts = resolve([special, weekly], _utc(2026, 1, 5, 12), _utc(2026, 1, 5, 14), UTC)
    assert [(a.media_id, a.start.strftime("%H:%M"), a.end.strftime("%H:%M")) for a in result] == [
        (1, "12:00", "12:15"), (9, "12:15", "12:45"), (1, "12:45", "13:00"), (1, "13:00", "14:00")]
    assert result[2].media_offset == 45 * 60
    assert conflicts and conflicts[0].block_id == 2 and conflicts[0].preempted_id == 1


def test_later_block_preempts_earlier_equal_priority():
    first = Block(1, "Long", "once", "10:00", (_item(1, 120),), start_date="2026-01-05")
    second = Block(2, "Late", "once", "11:00", (_item(2, 30),), start_date="2026-01-05")
    result, conflicts = resolve([first, second], _utc(2026, 1, 5), _utc(2026, 1, 6), UTC)
    assert [(a.media_id, a.start.hour, a.start.minute) for a in result] == [(1, 10, 0), (2, 11, 0), (1, 11, 30)]
    assert conflicts[0].preempted_id == 1


def test_gaps_become_off_air_or_filler():
    block = Block(1, "Show", "once", "12:00", (_item(1, 60),), start_date="2026-01-05")
    start, end = _utc(2026, 1, 5, 11), _utc(2026, 1, 5, 14)
    plain = airings([block], start, end, UTC)
    assert [a.kind for a in plain] == ["offair", "program", "offair"]
    filled = airings([block], start, end, UTC, "filler", (_item(50, 1), _item(51, 2)))
    assert {a.kind for a in filled} == {"filler", "program"}
    assert sum(a.seconds for a in filled) == 3 * 3600
    # Filler is anchored to a fixed epoch, so any window agrees on what is airing.
    moment = _utc(2026, 1, 5, 11, 7, 30)
    later = airings([block], moment - timedelta(seconds=1), end, UTC, "filler", (_item(50, 1), _item(51, 2)))
    a, b = filled[at(filled, moment)], later[at(later, moment)]
    assert (a.media_id, a.media_offset + (moment - a.start).total_seconds()) == \
           (b.media_id, b.media_offset + (moment - b.start).total_seconds())


def test_unknown_durations_are_skipped_and_missing_files_stand_by():
    block = Block(1, "Mixed", "once", "12:00",
                  (Item(1, "Unmeasured", None), _item(2, 30, available=False)), start_date="2026-01-05")
    result, _ = resolve([block], _utc(2026, 1, 5), _utc(2026, 1, 6), UTC)
    assert len(result) == 1 and result[0].kind == "standby" and result[0].start == _utc(2026, 1, 5, 12)


def test_merge_rejoins_pieces_split_at_window_edges():
    block = Block(1, "Film", "once", "23:00", (_item(3, 120),), start_date="2026-01-05")
    first = airings([block], _utc(2026, 1, 5), _utc(2026, 1, 6), UTC)
    second = airings([block], _utc(2026, 1, 6), _utc(2026, 1, 7), UTC)
    film = [a for a in merge(first + second) if a.media_id == 3]
    assert len(film) == 1 and film[0].start == _utc(2026, 1, 5, 23) and film[0].end == _utc(2026, 1, 6, 1)
