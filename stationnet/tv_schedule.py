"""Pure TV scheduling engine: programming blocks become broadcast airings.

A block is an anchored local start time followed by items airing back to
back.  Once blocks air on one date; weekly blocks air on selected weekdays.
With ``loop_until`` the items repeat until that local time.  Playback is a
function of the schedule and the wall clock, so nothing here keeps state.
"""
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

UTC = timezone.utc
# Gap filler and looping off-air media are positioned relative to this
# instant, so every viewer and every computed window agree.
EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
MAX_BLOCK = timedelta(hours=24)
MIN_ITEM_SECONDS = 1.0
MAX_AIRINGS_PER_BLOCK = 20000


@dataclass(frozen=True)
class Item:
    media_id: int
    title: str
    duration: float | None
    description: str = ""
    available: bool = True


@dataclass(frozen=True)
class Block:
    id: int
    title: str
    kind: str
    start_time: str
    items: tuple = ()
    description: str = ""
    start_date: str | None = None
    weekdays: int = 0
    loop_until: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None


@dataclass(frozen=True)
class Airing:
    start: datetime
    end: datetime
    kind: str  # program, standby, offair, filler
    title: str
    description: str = ""
    media_id: int | None = None
    media_offset: float = 0.0
    block_id: int | None = None
    block_title: str = ""

    @property
    def seconds(self):
        return (self.end - self.start).total_seconds()

    def clip(self, start, end):
        new_start, new_end = max(self.start, start), min(self.end, end)
        offset = self.media_offset
        if self.media_id is not None and self.kind != "offair":
            offset += (new_start - self.start).total_seconds()
        return replace(self, start=new_start, end=new_end, media_offset=offset)


@dataclass(frozen=True)
class Conflict:
    block_id: int
    block_title: str
    preempted_id: int
    preempted_title: str
    start: datetime
    end: datetime


def parse_hhmm(value):
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def parse_day(value):
    return date.fromisoformat(value) if value else None


def local_moment(day, hhmm, zone):
    """UTC instant for a local wall-clock time.

    Times skipped by a DST jump land just after the jump (fold=0 keeps the
    pre-transition offset); repeated times use their first occurrence.
    """
    return datetime.combine(day, parse_hhmm(hhmm), tzinfo=zone).astimezone(UTC)


def airs_on(block, day):
    if block.kind == "once":
        return parse_day(block.start_date) == day
    if not block.weekdays & (1 << day.weekday()):
        return False
    first, last = parse_day(block.valid_from), parse_day(block.valid_until)
    return (first is None or day >= first) and (last is None or day <= last)


def block_span(block, day, zone):
    start = local_moment(day, block.start_time, zone)
    if block.loop_until:
        end_day = day if parse_hhmm(block.loop_until) > parse_hhmm(block.start_time) else day + timedelta(days=1)
        return start, local_moment(end_day, block.loop_until, zone)
    total = sum(item.duration for item in block.items if _playable(item))
    return start, start + min(timedelta(seconds=total), MAX_BLOCK)


def _playable(item):
    return item.duration is not None and item.duration >= MIN_ITEM_SECONDS


def layout(block, start, end):
    """Back-to-back airings for one occurrence, cut at ``end``."""
    items = [item for item in block.items if _playable(item)]
    result, moment = [], start
    if not items:
        return result
    while moment < end and len(result) < MAX_AIRINGS_PER_BLOCK:
        for item in items:
            if moment >= end:
                break
            finish = min(end, moment + timedelta(seconds=item.duration))
            result.append(Airing(
                moment, finish, "program" if item.available else "standby", item.title,
                item.description, item.media_id, 0.0, block.id, block.title))
            moment = finish
        if not block.loop_until:
            break
    return result


def occurrences(blocks, start, end, zone):
    """(start, end, priority, block) for every occurrence touching the window."""
    first = start.astimezone(zone).date() - timedelta(days=2)
    last = end.astimezone(zone).date()
    found = []
    for block in blocks:
        day = first
        while day <= last:
            if airs_on(block, day):
                span_start, span_end = block_span(block, day, zone)
                if span_start < end and span_end > start and span_end > span_start:
                    found.append((span_start, span_end, 1 if block.kind == "once" else 0, block))
            day += timedelta(days=1)
    return found


def _cut(airings, start, end):
    kept, removed = [], []
    for airing in airings:
        if airing.end <= start or airing.start >= end:
            kept.append(airing)
            continue
        removed.append(airing)
        if airing.start < start:
            kept.append(airing.clip(airing.start, start))
        if airing.end > end:
            kept.append(airing.clip(end, airing.end))
    return kept, removed


def resolve(blocks, start, end, zone):
    """Programmed airings plus conflicts, without gap filling.

    A block owns its own time span.  Once blocks beat weekly blocks; among
    equals the later-starting block wins.  Anything an override covers is
    cut, and a longer underlying block resumes where it would have been.
    """
    paint_order = sorted(occurrences(blocks, start, end, zone), key=lambda o: (o[2], o[0], o[3].id))
    airings, conflicts = [], []
    for span_start, span_end, _priority, block in paint_order:
        airings, removed = _cut(airings, span_start, span_end)
        reported = set()
        for airing in removed:
            if airing.block_id != block.id and airing.block_id not in reported:
                reported.add(airing.block_id)
                conflicts.append(Conflict(block.id, block.title, airing.block_id, airing.block_title,
                                          max(span_start, airing.start), min(span_end, airing.end)))
        airings.extend(layout(block, span_start, span_end))
    airings.sort(key=lambda a: a.start)
    clipped = [a.clip(start, end) for a in airings if a.end > start and a.start < end]
    return [a for a in clipped if a.end > a.start], conflicts


def _filler_airings(gap_start, gap_end, filler):
    items = [item for item in filler if _playable(item) and item.available]
    if not items:
        return None
    cycle = sum(item.duration for item in items)
    position = (gap_start - EPOCH).total_seconds() % cycle
    index = 0
    while position >= items[index].duration:
        position -= items[index].duration
        index += 1
    result, moment = [], gap_start
    while moment < gap_end:
        item = items[index]
        finish = min(gap_end, moment + timedelta(seconds=item.duration - position))
        result.append(Airing(moment, finish, "filler", item.title, item.description, item.media_id, position))
        moment, position, index = finish, 0.0, (index + 1) % len(items)
    return result


def _offair(gap_start, gap_end, offair):
    if offair is not None and offair.available and _playable(offair):
        offset = (gap_start - EPOCH).total_seconds() % offair.duration
        return Airing(gap_start, gap_end, "offair", "OFF AIR", "", offair.media_id, offset)
    return Airing(gap_start, gap_end, "offair", "OFF AIR")


def fill_gaps(airings, start, end, gap_mode="offair", filler=(), offair=None):
    result, moment = [], start
    for airing in airings + [None]:
        gap_end = end if airing is None else airing.start
        if gap_end > moment:
            pieces = _filler_airings(moment, gap_end, filler) if gap_mode == "filler" else None
            result.extend(pieces or [_offair(moment, gap_end, offair)])
        if airing is not None:
            result.append(airing)
            moment = airing.end
    return result


def airings(blocks, start, end, zone, gap_mode="offair", filler=(), offair=None):
    programmed, _conflicts = resolve(blocks, start, end, zone)
    return fill_gaps(programmed, start, end, gap_mode, filler, offair)


def merge(airings):
    """Join pieces that were split only by window boundaries."""
    merged = []
    for airing in airings:
        if merged:
            last = merged[-1]
            continuous = (last.end == airing.start and last.kind == airing.kind
                          and last.media_id == airing.media_id and last.block_id == airing.block_id
                          and last.title == airing.title)
            if continuous and last.media_id is not None and last.kind != "offair":
                continuous = abs(last.media_offset + last.seconds - airing.media_offset) < 0.01
            if continuous:
                merged[-1] = replace(last, end=airing.end)
                continue
        merged.append(airing)
    return merged


def at(airings, moment):
    for index, airing in enumerate(airings):
        if airing.start <= moment < airing.end:
            return index
    return None
