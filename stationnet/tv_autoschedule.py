"""Pure automatic programming: a categorised library becomes one-off blocks.

The generator fills free broadcast windows with half-hour aligned slots.  A
slot is one programme followed by a break of commercials, bumpers and idents
that runs exactly to the slot boundary; the caller stores each slot as an
ordinary one-off block, so playback and the guide need nothing new.
Nothing here touches the database: inputs are plain data and a seed.
"""
from dataclasses import dataclass, field
from datetime import timedelta
import math
import random
import re

CONTENT_TYPES = ("show", "miniseries", "documentary", "movie", "short", "animation", "special", "other")
INTERSTITIAL_KINDS = ("ident", "bumper", "commercial")
COMMERCIAL_LEVELS = {"light": 0.3, "standard": 0.7, "heavy": 0.9}
# Preferred break per half hour of slot, in seconds.
IDEAL_BREAK = {"light": 120, "standard": 300, "heavy": 420}
RERUN_LEVELS = {"minimal": 0.0, "moderate": 0.15, "frequent": 0.35}
# Hours before a standalone programme may comfortably air again.
REPEAT_GAP_HOURS = {"minimal": 96, "moderate": 48, "frequent": 18}
SLOT = timedelta(minutes=30)
MAX_INTERLUDE = timedelta(minutes=15)
MAX_BREAK_PER_HALF = 480
MIN_SLOT = timedelta(minutes=1)
LONG_FORM_SECONDS = 75 * 60
PRIME_HOURS = range(19, 23)
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_RULES = 50
MAX_DAYS = 28

DEFAULT_CONFIG = {
    "mix": {"show": 40, "miniseries": 10, "documentary": 15, "movie": 20, "short": 5, "animation": 10,
            "special": 5, "other": 5},
    "commercials": "standard",
    "reruns": "moderate",
    "days": 7,
    "on_air_from": "06:00",
    "on_air_until": "02:00",
    "rules": [
        {"days": 127, "from": "19:00", "until": "23:00", "types": ["movie", "miniseries"], "genres": [], "mode": "prefer"},
        {"days": 96, "from": "07:00", "until": "11:00", "types": ["animation"], "genres": [], "mode": "prefer"},
    ],
    "themes": {},
}


@dataclass(frozen=True)
class Program:
    media_id: int
    title: str
    duration: float
    content_type: str = "other"
    genre: str = ""
    series: str = ""
    season: int | None = None
    episode: int | None = None
    description: str = ""


@dataclass(frozen=True)
class Clip:
    media_id: int
    title: str
    duration: float
    kind: str


@dataclass
class Slot:
    start: object
    end: object
    title: str
    items: list
    content_type: str = ""
    genre: str = ""
    program_id: int | None = None
    series: str = ""
    rerun: bool = False
    fill: bool = False
    interlude: bool = False


# Configuration ---------------------------------------------------------------

def _minutes(hhmm):
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _clean_list(values, allowed=None, limit=20):
    if not isinstance(values, list):
        return None
    cleaned = []
    for value in values[:limit]:
        text = str(value or "").strip()[:60]
        if text and (allowed is None or text in allowed) and text.lower() not in {v.lower() for v in cleaned}:
            cleaned.append(text)
    return cleaned


def _clean_rule(raw, days_default=127):
    if not isinstance(raw, dict):
        return None, "Invalid rule."
    start, until = str(raw.get("from", "")), str(raw.get("until", ""))
    if not TIME_RE.fullmatch(start) or not TIME_RE.fullmatch(until):
        return None, "Rule times must be HH:MM."
    try:
        days = int(raw.get("days", days_default))
    except (TypeError, ValueError):
        days = -1
    if not 1 <= days <= 127:
        return None, "Each rule needs at least one weekday."
    types, genres = _clean_list(raw.get("types", []), CONTENT_TYPES), _clean_list(raw.get("genres", []))
    if types is None or genres is None:
        return None, "Invalid rule."
    if not types and not genres:
        return None, "Each rule needs a content type or genre."
    mode = raw.get("mode", "prefer")
    if mode not in {"prefer", "only"}:
        return None, "Rule mode must be prefer or only."
    return {"days": days, "from": start, "until": until, "types": types, "genres": genres, "mode": mode}, None


def normalize_config(raw):
    """Validated configuration with defaults filled in, or (None, message)."""
    raw = raw if isinstance(raw, dict) else {}
    config = {key: (dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value)
              for key, value in DEFAULT_CONFIG.items()}
    if "mix" in raw:
        if not isinstance(raw["mix"], dict):
            return None, "Invalid programming mix."
        mix = {}
        for kind in CONTENT_TYPES:
            try:
                weight = int(raw["mix"].get(kind, 0))
            except (TypeError, ValueError):
                return None, "Mix weights must be whole numbers."
            if not 0 <= weight <= 100:
                return None, "Mix weights must be between 0 and 100."
            mix[kind] = weight
        if not any(mix.values()):
            return None, "Give at least one content type a weight."
        config["mix"] = mix
    for key, allowed in (("commercials", COMMERCIAL_LEVELS), ("reruns", RERUN_LEVELS)):
        if key in raw:
            if raw[key] not in allowed:
                return None, f"Choose a valid {key} level."
            config[key] = raw[key]
    if "days" in raw:
        try:
            days = int(raw["days"])
        except (TypeError, ValueError):
            days = 0
        if not 1 <= days <= MAX_DAYS:
            return None, f"Generate between 1 and {MAX_DAYS} days."
        config["days"] = days
    for key in ("on_air_from", "on_air_until"):
        if key in raw:
            if not TIME_RE.fullmatch(str(raw[key])):
                return None, "Enter on-air hours as HH:MM."
            config[key] = str(raw[key])
    if "rules" in raw:
        if not isinstance(raw["rules"], list) or len(raw["rules"]) > MAX_RULES:
            return None, f"Use up to {MAX_RULES} time-of-day rules."
        rules = []
        for item in raw["rules"]:
            rule, error = _clean_rule(item)
            if error:
                return None, error
            rules.append(rule)
        config["rules"] = rules
    if "themes" in raw:
        if not isinstance(raw["themes"], dict):
            return None, "Invalid day themes."
        themes = {}
        for day, theme in raw["themes"].items():
            if str(day) not in {str(n) for n in range(7)} or not isinstance(theme, dict):
                return None, "Invalid day themes."
            types = _clean_list(theme.get("types", []), CONTENT_TYPES)
            genres = _clean_list(theme.get("genres", []))
            if types is None or genres is None:
                return None, "Invalid day themes."
            if types or genres:
                themes[str(day)] = {"types": types, "genres": genres}
        config["themes"] = themes
    return config, None


def _all_rules(config):
    rules = list(config["rules"])
    for day, theme in config["themes"].items():
        rules.append({"days": 1 << int(day), "from": "00:00", "until": "00:00", "types": theme["types"],
                      "genres": theme["genres"], "mode": "prefer"})
    return rules


def _rule_applies(rule, local):
    if not rule["days"] & (1 << local.weekday()):
        return False
    start, until = _minutes(rule["from"]), _minutes(rule["until"])
    now = local.hour * 60 + local.minute
    if start == until:
        return True
    if start < until:
        return start <= now < until
    return now >= start or now < until


# Generation ------------------------------------------------------------------

def _episode_key(program):
    return (program.season if program.season is not None else 0,
            program.episode if program.episode is not None else 10 ** 6, program.title.lower())


def _slot_length(seconds):
    return SLOT * max(1, math.ceil(seconds / SLOT.total_seconds() - 1e-9))


@dataclass
class _Series:
    name: str
    episodes: list
    content_type: str
    genre: str
    pointer: int = -1
    wraps: int = 0


@dataclass
class _State:
    rng: random.Random
    last_aired: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    type_seconds: dict = field(default_factory=dict)
    total_seconds: float = 0.0
    strip: dict = field(default_factory=dict)
    prime_log: list = field(default_factory=list)
    clip_uses: dict = field(default_factory=dict)
    last_series: str = ""
    gap_seconds: float = 0.0
    encores: int = 0


class Generator:
    def __init__(self, programs, clips, config, zone, history=(), series_pointer=None, seed=0):
        self.config, self.zone = config, zone
        self.rules = _all_rules(config)
        self.state = _State(random.Random(seed))
        self.clips = [clip for clip in clips if clip.duration and clip.duration >= 1]
        self.commercials = [clip for clip in self.clips if clip.kind == "commercial"]
        self.bumpers = [clip for clip in self.clips if clip.kind == "bumper"]
        self.idents = [clip for clip in self.clips if clip.kind == "ident"]
        self.ideal_break = IDEAL_BREAK[config["commercials"]]
        grouped, self.standalone = {}, []
        for program in programs:
            if not program.duration or program.duration < 1:
                continue
            if program.series:
                grouped.setdefault(program.series.lower(), []).append(program)
            else:
                self.standalone.append(program)
        self.series = {}
        pointers = {key.lower(): value for key, value in (series_pointer or {}).items()}
        for key, episodes in grouped.items():
            episodes.sort(key=_episode_key)
            types = [e.content_type for e in episodes]
            genres = [e.genre for e in episodes if e.genre]
            entry = _Series(episodes[0].series, episodes, max(set(types), key=types.count),
                            max(set(genres), key=genres.count) if genres else "")
            last = pointers.get(key)
            entry.pointer = next((i for i, e in enumerate(episodes) if e.media_id == last), -1)
            self.series[key] = entry
        present = {p.content_type for p in self.standalone} | {s.content_type for s in self.series.values()}
        weights = {kind: config["mix"].get(kind, 0) for kind in present}
        total = sum(weights.values())
        if total == 0:
            weights, total = {kind: 1 for kind in present}, len(present) or 1
        self.share = {kind: weight / total for kind, weight in weights.items()}
        for airing_start, media_id in history:
            self.state.last_aired[media_id] = max(self.state.last_aired.get(media_id, airing_start), airing_start)
        self.by_id = {p.media_id: p for p in programs}
        for airing_start, media_id in history:
            program = self.by_id.get(media_id)
            if program and airing_start.astimezone(zone).hour in PRIME_HOURS:
                self.state.prime_log.append((airing_start, program))

    # Candidate scoring ----------------------------------------------------

    def _units(self):
        units = [(program, None, False) for program in self.standalone]
        for key, entry in self.series.items():
            index = (entry.pointer + 1) % len(entry.episodes)
            units.append((entry.episodes[index], key, False))
        return units

    def _encores(self, at):
        """Prime-time programmes from the last few days, offered at other hours as intentional reruns."""
        rate = RERUN_LEVELS[self.config["reruns"]]
        if not rate or at.astimezone(self.zone).hour in PRIME_HOURS or self.state.rng.random() >= rate:
            return []
        seen, pool = set(), []
        for aired, program in self.state.prime_log:
            if (program.media_id not in seen and timedelta(hours=10) <= at - aired <= timedelta(hours=72)
                    and at - self.state.last_aired.get(program.media_id, aired) >= timedelta(hours=10)):
                seen.add(program.media_id)
                pool.append((program, None, True))
        self.state.rng.shuffle(pool)
        return pool

    def _matches(self, rule, program, series_key):
        content_type = self.series[series_key].content_type if series_key else program.content_type
        genre = (program.genre or (self.series[series_key].genre if series_key else "")).lower()
        return content_type in rule["types"] or genre in {g.lower() for g in rule["genres"]}

    def _type_of(self, program, series_key):
        return self.series[series_key].content_type if series_key else program.content_type

    def _placement(self, program, series_key, local):
        """Time-of-day suitability: 0 when an "only" rule excludes it, larger when a rule prefers it."""
        factor = 1.0
        for rule in self.rules:
            if _rule_applies(rule, local):
                matched = self._matches(rule, program, series_key)
                if rule["mode"] == "only" and not matched:
                    return 0.0
                if rule["mode"] == "prefer" and matched:
                    factor *= 3.0
        if program.duration > LONG_FORM_SECONDS and local.weekday() < 5 and 6 <= local.hour < 17:
            factor *= 0.3
        return factor

    def _freshness(self, program, rerun, at):
        if rerun:
            return 1.0
        last = self.state.last_aired.get(program.media_id)
        fresh = 1.0
        if last is not None:
            hours = (at - last).total_seconds() / 3600
            fresh = max(0.01, min(1.0, hours / REPEAT_GAP_HOURS[self.config["reruns"]])) ** 2
        return fresh / (1 + 0.5 * self.state.counts.get(program.media_id, 0))

    def _item_score(self, program, series_key, rerun, at, length, placement):
        state, local = self.state, at.astimezone(self.zone)
        score = placement * self._freshness(program, rerun, at)
        if series_key:
            if state.last_series == series_key:
                score *= 0.35
            if state.strip.get(local.strftime("%H:%M")) == series_key:
                score *= 2.5
        if self.clips:
            break_per_half = (length.total_seconds() - program.duration) / (length / SLOT)
            score /= 1 + abs(break_per_half - self.ideal_break) / 240
        return score * state.rng.uniform(0.85, 1.15)

    def _type_score(self, content_type, options):
        """How much the mix wants this content type now: furthest below its share wins."""
        state = self.state
        target = self.share.get(content_type, 0)
        actual = state.type_seconds.get(content_type, 0) / state.total_seconds if state.total_seconds else 0
        deficit = min(5.0, max(0.2, (target + 0.02) / (actual + 0.02)))
        placement = max(option[0] for option in options)
        fresh = max(self._freshness(option[1], option[3], option[5]) for option in options)
        return target * deficit * placement * fresh ** 0.3 * state.rng.uniform(0.9, 1.1)

    def _pick(self, at, room, prefer_short=False):
        """Best (program, series_key, rerun, length) fitting in ``room`` from ``at``, or None."""
        local = at.astimezone(self.zone)
        for units in (self._encores(at), self._units()):
            by_type = {}
            for program, series_key, rerun in units:
                if program.duration > room.total_seconds():
                    continue
                length = room if prefer_short else min(_slot_length(program.duration), room)
                option = (self._placement(program, series_key, local), program, series_key, rerun, length, at)
                by_type.setdefault(self._type_of(program, series_key), []).append(option)
            if not by_type:
                continue
            ranked = sorted(((self._type_score(kind, options), kind) for kind, options in by_type.items()),
                            reverse=True)
            score, kind = ranked[0]
            options = [option for option in by_type[kind] if option[0] > 0]
            if score <= 0 or not options:
                # Nothing preferred fits: air the least-used programme rather than dead air.
                options = [option for group in by_type.values() for option in group]
                best = min(options, key=lambda option: self.state.counts.get(option[1].media_id, 0))
            else:
                best = max(options, key=lambda option: self._item_score(option[1], option[2], option[3], at, option[4], option[0]))
            return best[1:5]
        return None

    # Breaks -------------------------------------------------------------------

    def _least_used(self, pool, previous):
        choices = [clip for clip in pool if clip.media_id != previous] or pool
        lowest = min(self.state.clip_uses.get(clip.media_id, 0) for clip in choices)
        return self.state.rng.choice([clip for clip in choices if self.state.clip_uses.get(clip.media_id, 0) == lowest])

    def break_items(self, seconds, top_of_hour=False):
        """Clips covering at least ``seconds``; the last may run over and is cut by the slot end."""
        if seconds < 1 or not self.clips:
            return []
        share = COMMERCIAL_LEVELS[self.config["commercials"]]
        promos = self.bumpers or self.idents
        chosen, commercial_time, filled, previous = [], 0.0, 0.0, None
        closing = self._least_used(self.idents, None) if top_of_hour and self.idents else None
        if closing and closing.duration >= seconds:
            chosen = [closing]
        else:
            target = seconds - (closing.duration if closing else 0)
            while filled < target:
                remaining = target - filled
                want_commercial = self.commercials and (not promos or commercial_time <= share * (filled + 1))
                pool = self.commercials if want_commercial else promos
                longest = max(clip.duration for clip in self.clips)
                if remaining < longest:
                    covering = [clip for clip in self.clips if clip.duration >= remaining and clip.media_id != previous]
                    fitting = [clip for clip in pool if clip.duration <= remaining and clip.media_id != previous]
                    if fitting and remaining - max(c.duration for c in fitting) > 0:
                        clip = self._least_used(fitting, previous)
                    elif covering:
                        excess = min(clip.duration - remaining for clip in covering)
                        clip = self._least_used([c for c in covering if c.duration - remaining <= excess + 5], previous)
                    else:
                        clip = self._least_used(pool, previous)
                else:
                    clip = self._least_used(pool, previous)
                chosen.append(clip)
                self.state.clip_uses[clip.media_id] = self.state.clip_uses.get(clip.media_id, 0) + 1
                filled += clip.duration
                commercial_time += clip.duration if clip.kind == "commercial" else 0
                previous = clip.media_id
            if closing:
                # The station ident airs whole: before a final clip that the slot end will cut.
                chosen.insert(len(chosen) - 1 if chosen and filled > target else len(chosen), closing)
        if closing:
            self.state.clip_uses[closing.media_id] = self.state.clip_uses.get(closing.media_id, 0) + 1
        return chosen

    # Slots --------------------------------------------------------------------

    def _program_slot(self, at, program, series_key, rerun, length):
        state = self.state
        end = at + length
        local_end = end.astimezone(self.zone)
        clips = self.break_items(length.total_seconds() - program.duration,
                                 top_of_hour=local_end.minute == 0)
        title = program.title
        override = None
        if program.series and not title.lower().startswith(program.series.lower()):
            override = f"{program.series}: {title}"
        items = [(program.media_id, override, program.description)] + [(clip.media_id, None, "") for clip in clips]
        covered = program.duration + sum(clip.duration for clip in clips)
        content_type = self.series[series_key].content_type if series_key else program.content_type
        slot = Slot(at, end, program.series or title, items, content_type,
                    program.genre or (self.series[series_key].genre if series_key else ""),
                    program.media_id, program.series, rerun, covered >= length.total_seconds())
        if series_key and not rerun:
            entry = self.series[series_key]
            entry.pointer = (entry.pointer + 1) % len(entry.episodes)
            if entry.pointer == len(entry.episodes) - 1:
                entry.wraps += 1
            state.strip[at.astimezone(self.zone).strftime("%H:%M")] = series_key
        state.last_series = series_key or ""
        state.encores += rerun
        state.last_aired[program.media_id] = at
        state.counts[program.media_id] = state.counts.get(program.media_id, 0) + 1
        state.type_seconds[content_type] = state.type_seconds.get(content_type, 0) + program.duration
        state.total_seconds += program.duration
        if at.astimezone(self.zone).hour in PRIME_HOURS and not rerun:
            state.prime_log.append((at, program))
        return slot

    def _interlude(self, at, end):
        clips = self.break_items((end - at).total_seconds(), top_of_hour=end.astimezone(self.zone).minute == 0)
        if not clips:
            return None
        self.state.last_series = ""
        return Slot(at, end, "Station break", [(clip.media_id, None, "") for clip in clips], fill=True, interlude=True)

    def _compact(self, at, choice):
        """Shorten a slot whose break would be excessive when another programme can follow it.

        Two 11-minute cartoons share a half hour rather than one airing with
        19 minutes of commercials; the next pass fills the rest of the slot.
        """
        program, series_key, rerun, length = choice
        spare = length.total_seconds() - program.duration
        if spare / (length / SLOT) <= MAX_BREAK_PER_HALF:
            return choice
        compact = _ceil_minute(at + timedelta(seconds=program.duration + self.ideal_break * length / SLOT)) - at
        rest = (length - compact).total_seconds()
        follower = any(other.media_id != program.media_id and other.duration <= rest
                       for other in self.by_id.values() if other.duration)
        return (program, series_key, rerun, compact) if follower and compact < length else choice

    def _next_boundary(self, moment):
        local = moment.astimezone(self.zone)
        past = (local.minute % 30) * 60 + local.second + local.microsecond / 1e6
        return moment if past == 0 else moment + timedelta(seconds=1800 - past)

    def fill(self, window_start, window_end):
        slots = []
        moment = _ceil_minute(window_start)
        while window_end - moment >= MIN_SLOT:
            room = window_end - moment
            boundary = self._next_boundary(moment)
            if boundary > moment:
                lead = min(boundary - moment, room)
                choice = self._pick(moment, lead, prefer_short=True)
                if choice:
                    slot = self._program_slot(moment, *choice)
                else:
                    slot = self._interlude(moment, moment + lead) if lead <= MAX_INTERLUDE else None
                if slot is None:
                    self.state.gap_seconds += lead.total_seconds()
                moment += lead
            else:
                choice = self._pick(moment, room)
                if choice:
                    slot = self._program_slot(moment, *self._compact(moment, choice))
                    moment = slot.end
                else:
                    step = min(room, SLOT)
                    slot = self._interlude(moment, moment + step) if step <= MAX_INTERLUDE else None
                    if slot is None:
                        self.state.gap_seconds += step.total_seconds()
                    moment += step
            if slot is not None:
                slots.append(slot)
        return slots


def _ceil_minute(moment):
    if moment.second or moment.microsecond:
        return moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return moment


def generate(windows, programs, clips, config, zone, history=(), series_pointer=None, seed=0):
    """Slots for every free window plus human-readable warnings.

    ``windows`` are (start, end) aware datetimes; ``history`` is (aired_at,
    media_id) pairs from before the run; ``series_pointer`` maps a series name
    to the media id of its most recently aired episode.
    """
    generator = Generator(programs, clips, config, zone, history, series_pointer, seed)
    slots = []
    for start, end in sorted(windows):
        slots.extend(generator.fill(start, end))
    return slots, _warnings(generator, slots)


def _warnings(generator, slots):
    warnings, state = [], generator.state
    distinct = len(generator.standalone) + sum(len(s.episodes) for s in generator.series.values())
    if distinct == 0:
        warnings.append("No programmes with a measured duration are available; nothing could be scheduled.")
        return warnings
    if distinct < 10:
        warnings.append(f"Only {distinct} programme(s) available; expect frequent repeats. Upload more to add variety.")
    if not generator.clips:
        warnings.append("No commercials, bumpers or idents: time between programmes will show the channel's gap screen.")
    elif not generator.commercials:
        warnings.append("No commercials in the library; breaks use bumpers and idents only.")
    elif len(generator.commercials) < 4:
        warnings.append(f"Only {len(generator.commercials)} commercial(s); each will repeat often.")
    absent = [kind for kind, weight in generator.config["mix"].items() if weight >= 10 and kind not in generator.share]
    if absent:
        warnings.append(f"No programmes yet for: {', '.join(absent)}. The mix is spread over the types you have.")
    repeated = sorted(((count, generator.by_id[media_id].title) for media_id, count in state.counts.items()
                       if count > 3 and not generator.by_id[media_id].series), reverse=True)
    for count, title in repeated[:5]:
        warnings.append(f"“{title}” airs {count} times in this schedule.")
    for entry in generator.series.values():
        if entry.wraps:
            warnings.append(f"{entry.name} has only {len(entry.episodes)} episode(s) and starts over "
                            f"{entry.wraps} time(s).")
    if state.gap_seconds >= 60:
        warnings.append(f"{round(state.gap_seconds / 60)} minute(s) could not be filled and will use the gap screen.")
    unfilled = sum(1 for slot in slots if not slot.fill and not slot.interlude)
    if unfilled and generator.clips:
        warnings.append(f"{unfilled} slot(s) end early because breaks could not be filled exactly.")
    return warnings
