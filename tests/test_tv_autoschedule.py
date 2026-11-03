"""Auto scheduler engine: ordering, fitting, breaks, preferences and small libraries."""
from collections import Counter
from datetime import datetime, timedelta, timezone

from stationnet import tv_autoschedule as auto

UTC = timezone.utc
MONDAY = datetime(2026, 11, 2, 6, tzinfo=UTC)


def _config(**changes):
    config, error = auto.normalize_config(changes)
    assert error is None
    return config


def _episodes(series, count, minutes=22.5, first_id=1, content_type="show"):
    return [auto.Program(first_id + n, f"Episode {n + 1}", minutes * 60, content_type, "comedy", series, 1, n + 1)
            for n in range(count)]


def _ads(count, seconds=30, first_id=900):
    return [auto.Clip(first_id + n, f"Ad {n}", seconds, "commercial") for n in range(count)]


def _windows(days=7, hours=20):
    return [(MONDAY + timedelta(days=d), MONDAY + timedelta(days=d, hours=hours)) for d in range(days)]


def _programme_ids(slots):
    return [slot.program_id for slot in slots if slot.program_id is not None]


def test_episodes_air_in_order_wrap_and_continue_from_history():
    episodes = _episodes("Lab Hours", 4)
    config = _config(mix={"show": 100}, reruns="minimal")
    slots, warnings = auto.generate(_windows(1, 4), episodes, _ads(4), config, UTC, seed=3)
    assert _programme_ids(slots)[:6] == [1, 2, 3, 4, 1, 2]
    assert any("starts over" in warning for warning in warnings)
    slots, _ = auto.generate(_windows(1, 1), episodes, _ads(4), config, UTC,
                             series_pointer={"Lab Hours": 2}, seed=3)
    assert _programme_ids(slots)[0] == 3


def test_slots_stay_inside_windows_align_to_half_hours_and_never_cut_programmes():
    windows = [(MONDAY + timedelta(minutes=7), MONDAY + timedelta(hours=5)),
               (MONDAY + timedelta(hours=6, minutes=15), MONDAY + timedelta(hours=9))]
    programs = _episodes("Show", 6) + [auto.Program(50, "Feature", 95 * 60, "movie")]
    slots, _ = auto.generate(windows, programs, _ads(6), _config(), UTC, seed=1)
    for slot in slots:
        assert any(start <= slot.start and slot.end <= end for start, end in windows)
        if slot.program_id is not None:
            program = next(p for p in programs if p.media_id == slot.program_id)
            assert program.duration <= (slot.end - slot.start).total_seconds()
    aligned = [slot for slot in slots if slot.start >= MONDAY + timedelta(minutes=30) and slot.start < windows[0][1]]
    assert all(slot.start.minute in (0, 30) or slot.program_id is None or slot.end.minute in (0, 30)
               for slot in aligned)
    assert any(slot.start == MONDAY + timedelta(minutes=30) for slot in slots)


def test_breaks_fill_to_the_slot_and_rotate_commercials():
    programs = _episodes("Show", 8, minutes=24)
    slots, _ = auto.generate(_windows(1, 4), programs, _ads(5), _config(mix={"show": 100}), UTC, seed=2)
    for slot in slots:
        assert slot.fill
        breaks = [media_id for media_id, _title, _desc in slot.items[1:]]
        assert all(a != b for a, b in zip(breaks, breaks[1:]))
    uses = Counter(media_id for slot in slots for media_id, _t, _d in slot.items[1:])
    assert max(uses.values()) - min(uses.values()) <= 2


def test_without_interstitials_slots_do_not_loop_and_warn():
    slots, warnings = auto.generate(_windows(1, 2), _episodes("Show", 4), [], _config(), UTC, seed=1)
    assert slots and not any(slot.fill for slot in slots)
    assert any("gap screen" in warning for warning in warnings)


def test_movies_prefer_prime_time_rule():
    programs = _episodes("Show", 30) + [auto.Program(100 + n, f"Movie {n}", 100 * 60, "movie") for n in range(10)]
    config = _config(mix={"show": 70, "movie": 30}, on_air_from="06:00", on_air_until="02:00")
    windows = [(MONDAY + timedelta(days=d), MONDAY + timedelta(days=d, hours=20)) for d in range(7)]
    slots, _ = auto.generate(windows, programs, _ads(6), config, UTC, seed=5)
    movies = [slot for slot in slots if slot.content_type == "movie"]
    prime = [slot for slot in movies if 19 <= slot.start.hour < 23]
    assert movies and len(prime) / len(movies) >= 0.4


def test_tiny_library_still_fills_the_week_and_warns_about_repeats():
    programs = [auto.Program(1, "Only Show", 25 * 60, "show"), auto.Program(2, "Other Show", 26 * 60, "documentary")]
    slots, warnings = auto.generate(_windows(), programs, _ads(1), _config(), UTC, seed=4)
    covered = sum((slot.end - slot.start).total_seconds() for slot in slots)
    assert covered >= 0.95 * 7 * 20 * 3600
    assert any("Only 2 programme" in warning for warning in warnings)
    assert any("airs" in warning and "times" in warning for warning in warnings)


def test_mix_weights_shape_airtime():
    programs = _episodes("Show", 40) + [auto.Program(200 + n, f"Doc {n}", 25 * 60, "documentary") for n in range(40)]
    slots, _ = auto.generate(_windows(3), programs, _ads(6), _config(mix={"show": 75, "documentary": 25}, rules=[]),
                             UTC, seed=6)
    time = Counter()
    for slot in slots:
        time[slot.content_type] += (slot.end - slot.start).total_seconds()
    share = time["show"] / (time["show"] + time["documentary"])
    assert 0.6 <= share <= 0.9


def test_rerun_level_controls_encores():
    programs = _episodes("Show", 60) + [auto.Program(300 + n, f"Doc {n}", 50 * 60, "documentary") for n in range(30)]
    counts = {}
    for level in ("minimal", "frequent"):
        slots, _ = auto.generate(_windows(4), programs, _ads(6), _config(reruns=level), UTC, seed=7)
        counts[level] = sum(slot.rerun for slot in slots)
    assert counts["minimal"] == 0 and counts["frequent"] > 0


def test_short_programmes_share_a_half_hour():
    toons = _episodes("Toons", 10, minutes=11, content_type="animation")
    slots, _ = auto.generate(_windows(1, 2), toons, _ads(6), _config(mix={"animation": 100}), UTC, seed=1)
    assert len(_programme_ids(slots)) >= 7


def test_config_validation():
    assert auto.normalize_config({"mix": {"show": 0}})[1]
    assert auto.normalize_config({"commercials": "nonstop"})[1]
    assert auto.normalize_config({"days": 99})[1]
    assert auto.normalize_config({"rules": [{"from": "25:00", "until": "01:00", "types": ["movie"]}]})[1]
    assert auto.normalize_config({"rules": [{"from": "20:00", "until": "22:00"}]})[1]
    config, error = auto.normalize_config({"themes": {"4": {"types": [], "genres": ["Horror"]}}, "days": 3})
    assert error is None and config["themes"] == {"4": {"types": [], "genres": ["Horror"]}} and config["days"] == 3
