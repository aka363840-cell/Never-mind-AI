"""Academy daily life, DATABASE half (Part 2): /study /attend /hangout /rest /club /parttime.

One action = ONE transaction (the caller's session): gates -> daily limits -> Energy check -> atomic cooldown claim ->
Energy spend + stat gains + daily counters (+ club points / diamonds). Any GameError rolls everything back, so a refused
action never costs Energy, never starts a cooldown and never pays. The pure rules live in services/academy_actions.py.

    async with db.tx() as s:
        res = await life.perform(s, user_id, "study")     # may raise ActionBlocked / OnCooldown (both GameError)
    await life.announce(user_id, res)                      # AFTER the commit: emits the events

Diamonds: only /parttime pays, through stu.pay_diamonds with a unique ref, and never past AR.DAILY_DIAMOND_CAP per day.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update

from database.models import AcademyClubPoints, AcademyDaily, Cooldown, aware, utcnow
from database.session import db, insert_ignore
from game import academy_rules as AR
from services import academy_actions as A, academy_calendar as cal, academy_events as ev, academy_students as stu, cooldowns
from services.errors import GameError, OnCooldown
from utils.content import academy_texts as T
from utils.fmt import dur

log = logging.getLogger(__name__)

_COUNTER = {"study": "study_count", "attend": "attended", "hangout": "hangouts", "rest": "rest_count",
            "club": "club_count", "parttime": "parttime_count"}


# ------------------------------------------------------------------ daily rows
def counts_of(d: AcademyDaily) -> A.DailyCounts:
    return A.DailyCounts(study=d.study_count, attend=d.attended, hangout=d.hangouts, rest=d.rest_count, club=d.club_count,
                         parttime=d.parttime_count, energy_spent=d.energy_spent, diamonds=d.diamonds)


async def get_daily(s, student_id: int, day: int) -> AcademyDaily:
    """The student's counters for season day `day` (created empty on first use, race-safe)."""
    q = select(AcademyDaily).where(AcademyDaily.student_id == student_id, AcademyDaily.day == day)
    row = (await s.execute(q)).scalar_one_or_none()
    if row is None:
        await insert_ignore(s, AcademyDaily, dict(student_id=student_id, day=day, study_count=0, attended=0, hangouts=0,
                                                  club_count=0, parttime_count=0, energy_spent=0, rest_count=0, diamonds=0))
        row = (await s.execute(q)).scalar_one()
    return row


async def daily_rows(s, student_id: int) -> list[AcademyDaily]:
    """Every counter row of a student, ordered by day (at most 7)."""
    return list((await s.execute(select(AcademyDaily).where(AcademyDaily.student_id == student_id)
                                 .order_by(AcademyDaily.day))).scalars())


@dataclass
class SeasonTotals:
    """Whole-season sums for exams / awards (Part 4+)."""
    study: int = 0
    attended_days: int = 0
    hangouts: int = 0
    club: int = 0
    parttime: int = 0
    rest: int = 0
    energy_spent: int = 0
    diamonds: int = 0
    best_streak: int = 0


async def season_totals(s, student_id: int) -> SeasonTotals:
    rows = await daily_rows(s, student_id)
    t = SeasonTotals()
    for r in rows:
        t.study += r.study_count
        t.attended_days += 1 if r.attended else 0
        t.hangouts += r.hangouts
        t.club += r.club_count
        t.parttime += r.parttime_count
        t.rest += r.rest_count
        t.energy_spent += r.energy_spent
        t.diamonds += r.diamonds
    t.best_streak = A.best_streak(r.day for r in rows if r.study_count > 0)
    return t


# ------------------------------------------------------------------ club points
async def add_club_points(s, season_id: int, club: str, points: int) -> int:
    """Atomically add points to a club's season score. Returns the new total."""
    if club not in AR.CLUBS:
        raise ValueError(f"unknown club {club!r}")
    await insert_ignore(s, AcademyClubPoints, dict(season_id=season_id, club=club, points=0))
    await s.execute(update(AcademyClubPoints).where(AcademyClubPoints.season_id == season_id, AcademyClubPoints.club == club)
                    .values(points=AcademyClubPoints.points + int(points)))
    total = (await s.execute(select(AcademyClubPoints.points).where(AcademyClubPoints.season_id == season_id,
                                                                    AcademyClubPoints.club == club))).scalar_one()
    return int(total)


async def club_standings(s, season_id: int) -> list[tuple[str, int]]:
    """[(club key, points)] for ALL clubs (0 for a club nobody practised for), best first; ties keep the AR.CLUBS order."""
    rows = (await s.execute(select(AcademyClubPoints.club, AcademyClubPoints.points)
                            .where(AcademyClubPoints.season_id == season_id))).all()
    pts = {c: int(p) for c, p in rows}
    order = list(AR.CLUBS)
    out = [(k, pts.get(k, 0)) for k in order]
    out.sort(key=lambda kv: (-kv[1], order.index(kv[0])))
    return out


# ------------------------------------------------------------------ the action
async def perform(s, user_id: int, key: str, now: datetime | None = None, rng: random.Random | None = None) -> A.ActionResult:
    """Do one daily action for `user_id`. Raises ActionBlocked (not now / not enrolled / limit / no Energy), OnCooldown,
    or GameError; in every such case nothing was changed. `now` and `rng` are parameters so tests can simulate a season."""
    if key not in AR.ACTIONS:
        raise ValueError(f"unknown academy action {key!r}")
    act = AR.ACTIONS[key]
    rng = rng or random
    now = aware(now) if now else utcnow()

    season = await cal.ensure_season(s, now)
    ph = cal.phase_at(season.start_at, now)
    if ph.key == AR.BREAK_KEY:                                       # everything is closed during the break
        nxt = cal.next_phase(season.start_at, now)
        raise A.ActionBlocked(T.BREAK_CLOSED.format(when=cal.fmt_when(nxt.starts_at)))
    if ph.day == 0:                                                  # season not started yet
        raise A.ActionBlocked(T.NOT_STARTED)
    st = await stu.get_student(s, user_id, season.id, lock=True)
    if st is None:                                                   # not enrolled: friendly message (+ Enroll button if possible)
        win = cal.enroll_window(season.start_at, now)
        raise A.ActionBlocked(T.NEED_ENROLL_OPEN.format(verb=T.VERBS[key]) if win.open else T.NEED_ENROLL_CLOSED,
                              enroll=win.open)

    day = ph.day
    resets = dur(int((aware(ph.ends_at) - now).total_seconds()))
    msg = A.day_block(key, day)
    if msg:
        raise A.ActionBlocked(msg)
    d = await get_daily(s, st.id, day)
    counts = counts_of(d)
    msg = A.limit_block(key, counts, resets)
    if msg:
        raise A.ActionBlocked(msg)
    if key == "parttime" and A.diamonds_left(counts) <= 0:
        raise A.ActionBlocked(T.CAP_REACHED.format(cap=AR.DAILY_DIAMOND_CAP, resets=resets))
    have, at = stu.regen_energy(st.energy, st.energy_at, now)
    if key == "rest" and have >= AR.ENERGY_MAX:
        raise A.ActionBlocked(T.ALREADY_RESTED)
    if have < act.energy:
        wait = int((act.energy - have) * 3600.0 / AR.ENERGY_REGEN_PER_HOUR - (now - at).total_seconds())
        raise A.ActionBlocked(T.NO_ENERGY.format(have=have, need=act.energy, wait=dur(max(60, wait))))
    left = await cooldowns.claim(s, user_id, AR.cooldown_key(key), act.cooldown_s)      # atomic: a double tap loses here
    if left:
        raise OnCooldown(left, act.name)

    # ---- context for the roll
    streak, first_today, first_attend = 0, False, False
    if key == "study":
        studied = (await s.execute(select(AcademyDaily.day).where(AcademyDaily.student_id == st.id,
                                                                  AcademyDaily.study_count > 0))).scalars().all()
        first_today = d.study_count == 0
        streak = A.study_streak(set(studied) | {day}, day)
    elif key == "attend":
        prior = (await s.execute(select(func.coalesce(func.sum(AcademyDaily.attended), 0))
                                 .where(AcademyDaily.student_id == st.id))).scalar_one()
        first_attend = int(prior) == 0
    p = A.plan(key, stream=st.stream, club=st.club, rng=rng, streak=streak, first_study_today=first_today)

    # ---- apply
    before = stu.stats_of(st)
    if act.energy:
        energy = await stu.spend_energy(s, user_id, season.id, act.energy, now)
    else:
        energy = await stu.restore_energy(s, user_id, season.id, p.energy_restore, now) if p.energy_restore else have
    if p.stats:
        await stu.add_stats(s, user_id, season.id, p.stats)
    after = stu.stats_of(st)
    gains = {k: after[k] - before[k] for k in p.stats}

    attr = _COUNTER[key]
    setattr(d, attr, getattr(d, attr) + 1)
    d.energy_spent += act.energy

    club_total = club_rank = club_count = 0
    if key == "club":
        club_total = await add_club_points(s, season.id, st.club, p.club_points)
        standings = await club_standings(s, season.id)
        club_count = len(standings)
        club_rank = 1 + sum(1 for _, pts in standings if pts > club_total)

    paid, capped = 0, False
    if key == "parttime":
        paid = A.cap_pay(p.pay_roll, d.diamonds)
        if paid <= 0:                                                # defensive: the cap check above should have caught it
            raise A.ActionBlocked(T.CAP_REACHED.format(cap=AR.DAILY_DIAMOND_CAP, resets=resets))
        capped = paid < p.pay_roll
        # unique per student + day + shift number; the enrolment time keeps it unique even after an /academyreset
        ref = f"pt:{user_id}:{int(aware(st.enrolled_at).timestamp() * 1_000_000)}:d{day}:n{d.parttime_count}"
        if not await stu.pay_diamonds(s, user_id, paid, ref, kind="academy_pt", meta={"season": season.id, "day": day}):
            raise GameError(T.ALREADY_PAID)                          # replay of the same payment: roll the whole action back
        d.diamonds += paid

    used = counts_of(d).used(key)
    gif = A.choose_gif(key, milestone=p.milestone, first_attend=first_attend, energy_left=energy, rng=rng)
    data: dict = dict(season_id=season.id, day=day, student_id=st.id, energy=energy, gains=gains)
    if key == "study":
        data.update(streak=streak, milestone=bool(p.milestone))
    elif key == "attend":
        data.update(first=first_attend)
    elif key == "rest":
        data.update(energy_restored=energy - have)
    elif key == "club":
        data.update(club=st.club, points=p.club_points, total=club_total)
    elif key == "parttime":
        data.update(paid=paid, capped=capped, diamonds_today=d.diamonds)

    return A.ActionResult(
        key=key, day=day, line=p.line, stream=st.stream, club=st.club, gains=gains, stats=after, energy=energy,
        energy_bar=stu.energy_bar(energy), energy_delta=energy - have, used=used, limit=act.daily_limit, streak=streak,
        milestone=bool(p.milestone), first_attend=first_attend, perk=p.perk, club_points=p.club_points,
        club_total=club_total, club_rank=club_rank, club_count=club_count, paid=paid, capped=capped,
        diamonds_today=d.diamonds, gif=gif, event=ev.EVENT_FOR_ACTION[key], event_data=data)


async def announce(user_id: int, res: A.ActionResult) -> None:
    """Emit the events of a finished action. Call it AFTER the transaction committed. Never raises."""
    try:
        await ev.emit(res.event, user_id, action=res.key, **res.event_data)
        if res.milestone:
            await ev.emit(ev.STUDY_STREAK, user_id, season_id=res.event_data.get("season_id"), day=res.day,
                          streak=res.streak, bonus=A.streak_bonus(res.streak))
    except Exception:
        log.exception("academy: announcing %s failed", res.key)


# ------------------------------------------------------------------ /academyreset support
@ev.subscribe(ev.RESET)
async def _on_reset(user_id, **data) -> None:
    """Staff reset: forget every daily counter, club score and Academy cooldown (Part 1 then deletes students and seasons)."""
    async with db.tx() as s:
        await s.execute(delete(AcademyDaily))
        await s.execute(delete(AcademyClubPoints))
        await s.execute(delete(Cooldown).where(Cooldown.key.like("academy:%")))
