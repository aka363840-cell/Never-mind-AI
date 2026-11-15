"""Academy reminders, recaps, ceremony timing (Part 6): PURE rules. No database, no Telegram, no clock: everything takes
`now` (and the season start) as a parameter, so a whole season can be simulated in tests.

What lives here: season-day maths, quiet hours, the recap / ceremony / enrolment-reminder schedule, the global send
limiter, who may be messaged, and which ONE reminder an enrolled player gets. The database half is services/academy_notify.py
and the ticks are in services/academy_reminders.py and services/academy_graduation.py.
"""
from __future__ import annotations

import asyncio
import time
import zlib
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Awaitable, Callable

from game import academy_rules as AR
from services import academy_adventure_logic as AL
from services import academy_exam_logic as EL
from utils.content import academy_remind_texts as T
from utils.fmt import dur

UTC = timezone.utc
H = timedelta(hours=1)


# ------------------------------------------------------------------ time basics
def variant(options: tuple[str, ...], key: str) -> str:
    """A stable pick from several wordings (same key = same text, so a retry never changes a message; different keys vary)."""
    return options[zlib.crc32(key.encode()) % len(options)]


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def local(dt: datetime) -> datetime:
    return aware(dt).astimezone(AR.tz())


def when(dt: datetime) -> str:
    """'Sat 14 Nov, 09:30 IST' in ACADEMY_TZ."""
    d = local(dt)
    return f"{d:%a %d %b, %H:%M} {d.tzname() or ''}".strip()


def season_day(start: datetime, now: datetime) -> int:
    """1..7 while the season runs, 0 before it starts and during the break."""
    start, now = aware(start), aware(now)
    if now < start:
        return 0
    idx = (now - start) // AR.DAY
    return int(idx) + 1 if idx < AR.SEASON_DAYS else 0


def day_bounds(start: datetime, day: int) -> tuple[datetime, datetime]:
    start = aware(start)
    return start + (day - 1) * AR.DAY, start + day * AR.DAY


def enroll_closes(start: datetime) -> datetime:
    return aware(start) + AR.LATE_JOIN_LAST_DAY * AR.DAY


def enroll_open(start: datetime, now: datetime) -> bool:
    return 1 <= season_day(start, now) <= AR.LATE_JOIN_LAST_DAY


def in_quiet_hours(now: datetime, from_hour: int | None = None, to_hour: int | None = None) -> bool:
    """True from 23:00 to 07:00 local time (ACADEMY_TZ) by default. Works for a window that wraps midnight and for one that does not."""
    a = AR.QUIET_FROM_HOUR if from_hour is None else from_hour
    b = AR.QUIET_TO_HOUR if to_hour is None else to_hour
    h = local(now).hour
    if a == b:
        return False
    return (h >= a or h < b) if a > b else (a <= h < b)


def local_day_key(now: datetime) -> str:
    """The 'one DM reminder a day' key: the calendar date in ACADEMY_TZ."""
    return "d:" + local(now).date().isoformat()


def local_date(now: datetime) -> date:
    return local(now).date()


# ------------------------------------------------------------------ the schedule
def enroll_window_index(start: datetime, now: datetime) -> int:
    """0, 1, 2 ... one number per 6-hour window since the season start (Days 1-4 = 0..15)."""
    return int((aware(now) - aware(start)) // (AR.ENROLL_GROUP_EVERY_H * H))


@dataclass(frozen=True)
class GroupPlan:
    kind: str          # season_open | enroll_g | enroll_last
    suffix: str        # the notify-log key is f"s{season_id}:{suffix}"
    last_call: bool = False


def enroll_group_plan(start: datetime, now: datetime) -> GroupPlan | None:
    """The enrolment message a group should get NOW, or None. Window 0 (the first 6 h) belongs to the season-open message; the
    last AR.ENROLL_LAST_CALL_H hours before admissions close are ONE 'last call'; in between one reminder per 6-hour window."""
    if not enroll_open(start, now):
        return None
    if aware(now) >= enroll_closes(start) - AR.ENROLL_LAST_CALL_H * H:
        return GroupPlan("enroll_last", "last", True)
    k = enroll_window_index(start, now)
    return None if k == 0 else GroupPlan("enroll_g", f"w{k}")


def recap_time(start: datetime, day: int) -> datetime | None:
    """The instant of the end-of-day recap of season day `day` (1..6): the first time the local clock shows ACADEMY_DAILY_HOUR:00
    inside that day. If that is less than AR.RECAP_MIN_AGE_H hours after the day began (the season started shortly before the
    daily hour, so it would not be an 'end of day' at all) the recap goes out one hour before the day ends instead."""
    if not 1 <= day <= AR.RECAP_LAST_DAY:
        return None
    ds, de = day_bounds(start, day)
    tz = AR.tz()
    l0 = ds.astimezone(tz)
    cand = datetime.combine(l0.date(), dtime(AR.daily_hour()), tzinfo=tz).astimezone(UTC)
    if cand < ds:
        cand = datetime.combine(l0.date() + timedelta(days=1), dtime(AR.daily_hour()), tzinfo=tz).astimezone(UTC)
    if cand >= de or cand - ds < AR.RECAP_MIN_AGE_H * H:
        cand = de - H
    return cand


def recap_due(start: datetime, now: datetime) -> int | None:
    """The season day whose recap should be out by now (its time has passed and the day is not over), else None."""
    now = aware(now)
    d = season_day(start, now)
    if not 1 <= d <= AR.RECAP_LAST_DAY:
        return None
    rt = recap_time(start, d)
    return d if rt is not None and now >= rt else None


def ceremony_window(start: datetime) -> tuple[datetime, datetime]:
    """(opens, expires): the ceremony starts shortly after Day 7 begins and can still go out for AR.CEREMONY_GRACE_H hours after the season ends."""
    start = aware(start)
    return (start + (AR.SEASON_DAYS - 1) * AR.DAY + timedelta(minutes=AR.CEREMONY_DELAY_MIN),
            start + AR.SEASON_DAYS * AR.DAY + AR.CEREMONY_GRACE_H * H)


def ceremony_due(start: datetime, now: datetime) -> bool:
    lo, hi = ceremony_window(start)
    return lo <= aware(now) < hi


# ------------------------------------------------------------------ the global send limiter
class Limiter:
    """At most `rate` sends in ANY rolling second. `await acquire()` before each send; it sleeps just long enough.
    Clock and sleep are injectable so tests can run a fake second in no time. No locks and no event-loop state: the check and the
    stamp happen with no await in between, so concurrent callers cannot both squeeze through."""

    def __init__(self, rate: int | None = None, clock: Callable[[], float] | None = None,
                 sleep: Callable[[float], Awaitable[None]] | None = None) -> None:
        self.rate = max(1, int(rate or AR.NOTIFY_RATE_PER_SEC))
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._stamps: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = self._clock()
            while self._stamps and now - self._stamps[0] >= 1.0:
                self._stamps.popleft()
            if len(self._stamps) < self.rate:
                self._stamps.append(now)
                return
            await self._sleep(max(0.001, self._stamps[0] + 1.0 - now))


# ------------------------------------------------------------------ who may be messaged
def dm_allowed(*, remind_on: bool, unreachable: bool, unreachable_at: datetime | None, blocked_bot: bool, banned: bool,
               last_seen: datetime | None, now: datetime) -> bool:
    """May the bot send this user an UNSOLICITED Academy DM? Opt-out, a ban and a block always win. A user marked unreachable is
    tried again only after AR.UNREACHABLE_RETRY_DAYS and only if we have seen them since (they may have pressed Start)."""
    if not remind_on or banned or blocked_bot:
        return False
    if not unreachable:
        return True
    if unreachable_at is None or last_seen is None:
        return False
    return aware(now) - aware(unreachable_at) >= AR.UNREACHABLE_RETRY_DAYS * AR.DAY and aware(last_seen) > aware(unreachable_at)


def group_allowed(*, games_enabled: bool, last_seen: datetime | None, fails: int, now: datetime) -> bool:
    """Is this group 'active' (seen in the last 7 days), enabled, and not refusing our messages?"""
    if not games_enabled or last_seen is None or fails >= AR.GROUP_FAILS_SKIP:
        return False
    return aware(now) - aware(last_seen) <= AR.ACTIVE_GROUP_DAYS * AR.DAY


# ------------------------------------------------------------------ reminders for enrolled players
@dataclass(frozen=True)
class Reminder:
    kind: str          # one of AR.REMINDER_KINDS
    key: str           # notify-log key (unique per player and reminder)
    text: str


def full_at(stored: int, stored_at: datetime) -> datetime | None:
    """When the lazily stored Energy reaches the maximum (None if it is already stored as full)."""
    if stored >= AR.ENERGY_MAX:
        return None
    return aware(stored_at) + timedelta(seconds=(AR.ENERGY_MAX - stored) * 3600.0 / AR.ENERGY_REGEN_PER_HOUR)


@dataclass(frozen=True)
class StudentState:
    season_id: int
    season_start: datetime
    energy_now: int
    energy_stored: int
    energy_at: datetime
    done_exams: frozenset = frozenset()       # exam kinds the student has finished or that expired
    adventures_today: int = 0                  # adventures FINISHED on today's season day


def log_kind(kind: str) -> str:
    """The notify-log kind of a reminder kind (kept short: the column is 24 characters)."""
    return "rem_" + kind


def reminder_candidates(st: StudentState, now: datetime) -> list[Reminder]:
    """Every reminder that applies to this player right now, most important first. Days 1-6 only."""
    now = aware(now)
    day = season_day(st.season_start, now)
    if not 1 <= day <= AR.REMINDER_LAST_DAY:
        return []
    sid, start = st.season_id, aware(st.season_start)
    out: list[Reminder] = []

    lo, hi = EL.exam_window(start, "midterm")                         # 1. midterm: from 1 h before it opens until 2 h before it closes
    if "midterm" not in st.done_exams and lo - timedelta(minutes=AR.MIDTERM_SOON_MIN) <= now < hi - 2 * H:
        text = (T.MIDTERM_SOON.format(mins=max(1, int((lo - now).total_seconds() // 60))) if now < lo
                else T.MIDTERM_OPEN.format(left=dur(int((hi - now).total_seconds()))))
        out.append(Reminder("midterm", f"s{sid}:midterm", text))

    lo, hi = EL.exam_window(start, "finals")                          # 2. finals: from 18 h before they open until 2 h before they close
    if "finals" not in st.done_exams and lo - AR.FINALS_SOON_H * H <= now < hi - 2 * H:
        text = (T.FINALS_SOON.format(when=when(lo), left=dur(int((lo - now).total_seconds()))) if now < lo
                else T.FINALS_OPEN.format(left=dur(int((hi - now).total_seconds()))))
        out.append(Reminder("finals", f"s{sid}:finals", text))

    if day >= AR.ADV_FIRST_DAY and st.adventures_today < AR.ADV_MAX_PER_DAY:   # 3. an adventure that is open and affordable
        if [a for a in AL.available(day, local(now).hour) if a.energy <= st.energy_now]:
            text = (T.ADVENTURE_FEST if day == AR.ADV_FEST_DAY
                    else variant(T.ADVENTURE_VARIANTS, f"s{sid}:d{day}").format(n=AR.ADV_MAX_PER_DAY - st.adventures_today, max=AR.ADV_MAX_PER_DAY))
            out.append(Reminder("adventure", f"s{sid}:d{day}", text))

    fa = full_at(st.energy_stored, st.energy_at)                      # 4. Energy refilled (recently) while the player was away
    if fa is not None and st.energy_now >= AR.ENERGY_MAX and now - fa <= AR.ENERGY_FULL_MAX_AGE_H * H:
        ekey = f"s{sid}:e{int(fa.timestamp() // 3600)}"
        out.append(Reminder("energy_full", ekey, variant(T.ENERGY_FULL_VARIANTS, ekey)))
    return out


def pick_reminder(st: StudentState, now: datetime, sent: frozenset | set = frozenset()) -> Reminder | None:
    """The ONE reminder an enrolled player should get now, or None. `sent` = {(log_kind, key)} already in the notify log, so a
    reminder that went out earlier never blocks the next one. Priority: midterm, finals, adventure, Energy full.
    (The caller adds quiet hours, the one-DM-a-day limit and the opt-out.)"""
    for r in reminder_candidates(st, now):
        if (log_kind(r.kind), r.key) not in sent:
            return r
    return None
