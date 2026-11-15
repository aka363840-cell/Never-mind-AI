"""Academy calendar. The first half is PURE (everything takes `now` / `season_start` as a parameter, no DB, no clock),
so a whole season can be simulated in tests. The second half keeps the academy_seasons table up to date.

Time model: a season starts at `start_at`; Day n is [start + (n-1) days, start + n days). After Day 7 comes the break
(ACADEMY_BREAK_HOURS), then the next season starts exactly one cycle (7 days + break) after the previous start.
Start is inclusive, end is exclusive: at the exact boundary you are already in the next phase.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from database.models import AcademySeason, AcademyStudent, aware, utcnow
from database.session import insert_ignore
from game import academy_rules as AR

log = logging.getLogger(__name__)
ZERO = timedelta(0)


@dataclass(frozen=True)
class PhaseInfo:
    key: str               # admission .. results | "break" | "upcoming"
    day: int               # 1..7, 0 for break / upcoming
    name: str
    emoji: str
    starts_at: datetime
    ends_at: datetime


def _info(ph: AR.Phase, starts: datetime, ends: datetime) -> PhaseInfo:
    return PhaseInfo(ph.key, ph.day, ph.name, ph.emoji, starts, ends)


def _day_info(start: datetime, day: int) -> PhaseInfo:
    return _info(AR.PHASE_BY_DAY[day], start + (day - 1) * AR.DAY, start + day * AR.DAY)


def season_end(season_start: datetime, days: int = AR.SEASON_DAYS) -> datetime:
    return aware(season_start) + days * AR.DAY


def cycle(break_td: timedelta | None = None) -> timedelta:
    """Distance between the start of one season and the start of the next."""
    return AR.SEASON_DAYS * AR.DAY + (AR.break_delta() if break_td is None else break_td)


# ------------------------------------------------------------------ pure functions
def phase_at(season_start: datetime, now: datetime, break_td: timedelta | None = None) -> PhaseInfo:
    start, now = aware(season_start), aware(now)
    if now < start:
        return _info(AR.UPCOMING_PHASE, start, start)
    idx = (now - start) // AR.DAY
    if idx < AR.SEASON_DAYS:
        return _day_info(start, idx + 1)
    end = season_end(start)
    return _info(AR.BREAK_PHASE, end, end + (AR.break_delta() if break_td is None else break_td))


def time_left(season_start: datetime, now: datetime, break_td: timedelta | None = None) -> timedelta:
    """Time until the CURRENT phase ends (never negative)."""
    return max(ZERO, phase_at(season_start, now, break_td).ends_at - aware(now))


def next_phase(season_start: datetime, now: datetime, break_td: timedelta | None = None) -> PhaseInfo:
    """The phase that follows the current one. After Day 7 that is the break; during the break it is Day 1 of the NEXT season."""
    start = aware(season_start)
    cur = phase_at(start, now, break_td)
    if cur.key == AR.UPCOMING_KEY:
        return _day_info(start, 1)
    if 1 <= cur.day < AR.SEASON_DAYS:
        return _day_info(start, cur.day + 1)
    if cur.day == AR.SEASON_DAYS:
        return phase_at(start, season_end(start), break_td)
    return _day_info(start + cycle(break_td), 1)


def week(season_start: datetime) -> list[PhaseInfo]:
    start = aware(season_start)
    return [_day_info(start, d) for d in range(1, AR.SEASON_DAYS + 1)]


@dataclass(frozen=True)
class EnrollWindow:
    open: bool
    late: bool                 # joining after Day 1 (lighter workload)
    closes_at: datetime


def enroll_window(season_start: datetime, now: datetime) -> EnrollWindow:
    """Enrolment is open on Days 1-4 (Day 1 on time, Days 2-4 late) and closes at the end of Day 4."""
    start = aware(season_start)
    closes = start + AR.LATE_JOIN_LAST_DAY * AR.DAY
    ph = phase_at(start, now)
    if ph.key == AR.UPCOMING_KEY or ph.day == 0 or ph.day > AR.LATE_JOIN_LAST_DAY:
        return EnrollWindow(False, False, closes)
    return EnrollWindow(True, ph.day > 1, closes)


def local(dt: datetime) -> datetime:
    return aware(dt).astimezone(AR.tz())


def fmt_when(dt: datetime) -> str:
    """'Sat 14 Nov, 09:30 IST' in ACADEMY_TZ."""
    d = local(dt)
    return f"{d:%a %d %b, %H:%M} {d.tzname() or ''}".strip()


# ------------------------------------------------------------------ database
async def latest_season(s) -> AcademySeason | None:
    return (await s.execute(select(AcademySeason).order_by(AcademySeason.number.desc()).limit(1))).scalar_one_or_none()


async def _create(s, number: int, start: datetime) -> AcademySeason:
    start = aware(start)
    await insert_ignore(s, AcademySeason, dict(
        number=number, start_at=start, end_at=season_end(start), status="active",
        theme=AR.THEMES[(number - 1) % len(AR.THEMES)]))
    row = (await s.execute(select(AcademySeason).where(AcademySeason.number == number))).scalar_one()
    log.info("academy season %s starts %s", number, start.isoformat())
    return row


async def ensure_season(s, now: datetime | None = None) -> AcademySeason:
    """Return the current season, creating / rolling over as needed (idempotent, safe to call from any handler).
    * no season yet            -> season 1 starts now (rounded down to the hour)
    * break is over            -> the next season is created at its exact calendar start
    * season over, in break    -> the OLD season is returned (status 'ended'); phase_at(...) says 'break'
    Missed cycles (bot offline for weeks) are skipped, not backfilled: one new row, numbered last+1."""
    now = aware(now) if now else utcnow()
    last = await latest_season(s)
    if last is None:
        return await _create(s, 1, now.replace(minute=0, second=0, microsecond=0))
    start = aware(last.start_at)
    if now >= aware(last.end_at) and last.status != "ended":
        last.status = "ended"
    if now >= start + cycle():
        k = (now - start) // cycle()
        last.status = "ended"
        return await _create(s, last.number + 1, start + k * cycle())
    return last


async def current_student_count(s, season_id: int) -> int:
    return (await s.execute(select(func.count()).select_from(AcademyStudent).where(AcademyStudent.season_id == season_id))).scalar_one()


async def advance(s, now: datetime | None = None) -> tuple[AcademySeason, PhaseInfo]:
    """TESTING: jump to the start of the next phase by moving the season's start back in time.
    During the break it starts the next season right now. Returns (season, the phase we are in now)."""
    now = aware(now) if now else utcnow()
    season = await ensure_season(s, now)
    start = aware(season.start_at)
    nxt = next_phase(start, now)
    if phase_at(start, now).key == AR.BREAK_KEY:
        season.status = "ended"
        season = await _create(s, season.number + 1, now)
        return season, phase_at(season.start_at, now)
    shift = nxt.starts_at - now
    if shift < ZERO:
        shift = ZERO
    season.start_at = start - shift
    season.end_at = season_end(season.start_at)
    if now >= aware(season.end_at):
        season.status = "ended"
    return season, phase_at(season.start_at, now)


async def reset_all(s) -> tuple[int, int]:
    """TESTING: delete every student and season (the caller emits the 'reset' event first so later parts clear their tables).
    The next ensure_season starts a fresh season 1."""
    st = (await s.execute(delete(AcademyStudent))).rowcount or 0
    se = (await s.execute(delete(AcademySeason))).rowcount or 0
    return st, se
