"""Academy exams, DATABASE half (Part 3).

    async with db.tx() as s:
        scr = await ex.open_exam(s, user_id)                      # /exam: start or resume
    async with db.tx() as s:
        scr = await ex.answer(s, user_id, attempt_id, n, idx)     # a strategy button
    await ex.announce(user_id, scr)                               # events AFTER the commit

One attempt per student and exam (UNIQUE). The 25 s of a round are checked against attempts.round_started_at (no timers).
Everything of one button press is one transaction. Rewards go through stu.pay_diamonds with a unique ref (idempotent).
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import and_, delete, func, or_, select

from database.models import (AcademyAttempt, AcademyChatMember, AcademyExam, AcademyRound, AcademySeason, AcademyStudent,
                             GroupStat, User, aware, utcnow)
from database.session import db, insert_ignore
from game import academy_rules as AR
from services.academy_actions import study_streak as _streak
from services import academy_calendar as cal, academy_events as ev, academy_exam_logic as L, academy_life as life, academy_students as stu
from services.academy_ticks import tick
from services.errors import GameError
from utils.content import academy_exam_rounds as CARDS, academy_exam_texts as T
from utils.telegram import safe_send

log = logging.getLogger(__name__)


class ExamBlocked(GameError):
    """An expected 'not now' answer (not enrolled, no exam open ...). `text` is the friendly message."""
    def __init__(self, text: str, *, enroll: bool = False):
        super().__init__(text)
        self.text, self.enroll = text, enroll


@dataclass
class Screen:
    """What the handler shows. status: question | result | done."""
    status: str
    kind: str
    attempt_id: int
    season_id: int
    n: int = 0                          # round on screen (question) or the round just played (result)
    total: int = 0
    card_key: str = ""
    seconds_left: int = 0
    last: dict = field(default_factory=dict)        # result: card_key, choice, success, points, line, stat, risk, chance, timed_out, focused
    can_focus: bool = False
    focus_left: int = 0
    first: bool = False                 # the attempt was just created (show the intro)
    score: float = 0.0
    grade: str = ""
    gpa: float = 0.0
    won: int = 0
    reward: int = 0
    expired: bool = False
    events: list = field(default_factory=list)      # (kind, data) emitted by announce()


# ------------------------------------------------------------------ windows
async def ensure_exam(s, season: AcademySeason, kind: str) -> AcademyExam:
    q = select(AcademyExam).where(AcademyExam.season_id == season.id, AcademyExam.kind == kind)
    row = (await s.execute(q)).scalar_one_or_none()
    if row is None:
        lo, hi = L.exam_window(aware(season.start_at), kind)
        await insert_ignore(s, AcademyExam, dict(season_id=season.id, kind=kind, opens_at=lo, closes_at=hi))
        row = (await s.execute(q)).scalar_one()
    return row


async def _attempt(s, student_id: int, kind: str, lock: bool = False) -> AcademyAttempt | None:
    q = select(AcademyAttempt).where(AcademyAttempt.student_id == student_id, AcademyAttempt.exam_kind == kind)
    if lock:
        q = q.with_for_update()
    return (await s.execute(q)).scalar_one_or_none()


async def _rounds(s, attempt_id: int) -> list[AcademyRound]:
    return list((await s.execute(select(AcademyRound).where(AcademyRound.attempt_id == attempt_id)
                                 .order_by(AcademyRound.n))).scalars())


def _keys(a: AcademyAttempt) -> list[str]:
    return [k for k in (a.cards or "").split(",") if k]


# ------------------------------------------------------------------ preparation (from academy_daily)
async def prep_of(s, st: AcademyStudent, season: AcademySeason, kind: str) -> L.ExamStudent:
    """The student's stats + study streak + attendance as the exam sees them."""
    day = AR.EXAM_DAY[kind]
    rows = await life.daily_rows(s, st.id)
    studied = {r.day for r in rows if r.study_count > 0 and r.day <= day}
    streak = max(_streak(studied, day), _streak(studied, day - 1))
    attended = sum(1 for r in rows if r.attended and r.day <= day)
    ph = cal.phase_at(aware(season.start_at), aware(st.enrolled_at))
    enrolled_day = ph.day if 1 <= ph.day <= AR.SEASON_DAYS else 1
    ratio = L.attendance_ratio(attended, L.possible_class_days(enrolled_day, day))
    return L.ExamStudent(st.stream, st.knowledge, st.charisma, st.fitness, st.creativity, streak, ratio)


# ------------------------------------------------------------------ grades of a student
async def _scores(s, student_id: int) -> dict[str, float]:
    rows = (await s.execute(select(AcademyAttempt.exam_kind, AcademyAttempt.score)
                            .where(AcademyAttempt.student_id == student_id,
                                   AcademyAttempt.state.in_(("done", "expired"))))).all()
    return {k: float(v) for k, v in rows if v is not None}


def _season_score(scores: dict[str, float], season_start: datetime, now: datetime) -> tuple[float | None, bool]:
    closed = {k: now >= L.exam_window(season_start, k)[1] for k in AR.EXAM_KINDS}
    return L.combine(scores.get("midterm"), scores.get("finals"), closed["midterm"], closed["finals"])


async def probation_status(s, user_id: int, season_id: int, now: datetime | None = None) -> bool:
    """True when the student's current season GPA is under AR.PROBATION_GPA (Focus is off; later parts may remove more perks).
    Recomputed live: a better finals can lift it. It always ends with the season (the next season is a fresh student)."""
    now = aware(now) if now else utcnow()
    st = await stu.get_student(s, user_id, season_id)
    season = await s.get(AcademySeason, season_id)
    if st is None or season is None:
        return False
    score, _ = _season_score(await _scores(s, st.id), aware(season.start_at), now)
    return L.on_probation(score)


# ------------------------------------------------------------------ finishing an attempt + reward
def _reward_ref(user_id: int, st: AcademyStudent, kind: str) -> str:
    # the enrolment time keeps the ref unique even after an /academyreset (ids can be reused)
    return f"exam:{user_id}:{int(aware(st.enrolled_at).timestamp() * 1_000_000)}:{kind}"


async def _finalize(s, a: AcademyAttempt, st: AcademyStudent, now: datetime, expired: bool) -> Screen:
    """Score, grade and pay the attempt once. Idempotent: an attempt that is already done just returns its screen."""
    kind = a.exam_kind
    rows = await _rounds(s, a.id)
    total = AR.EXAM_ROUNDS[kind]
    if a.state == "active":
        a.score = L.exam_score((r.points for r in rows), total)
        a.grade = L.grade_of(a.score)
        a.state = "expired" if expired else "done"
        a.finished_at = now
        a.round_started_at = None
        amount = L.reward_for(a.grade)
        if amount > 0:
            paid = await stu.pay_diamonds(s, st.user_id, amount, _reward_ref(st.user_id, st, kind), kind="academy_exam",
                                          meta={"exam": kind, "grade": a.grade, "season": st.season_id})
            a.reward = amount if paid else 0
        await s.flush()
    scr = Screen("done", kind, a.id, st.season_id, n=len(rows), total=total, score=float(a.score or 0), grade=a.grade or "F",
                 gpa=L.gpa_of(float(a.score or 0)), won=sum(1 for r in rows if r.success), reward=a.reward,
                 expired=a.state == "expired")
    scr.events.append((ev.EXAM_FINISHED, dict(season_id=st.season_id, exam=kind, score=scr.score, grade=scr.grade, gpa=scr.gpa,
                                              reward=a.reward, expired=scr.expired, student_id=st.id)))
    return scr


# ------------------------------------------------------------------ screens
def _question(a: AcademyAttempt, st_kind: str, n: int, now: datetime, season_id: int, total: int) -> Screen:
    key = _keys(a)[n - 1]
    return Screen("question", st_kind, a.id, season_id, n=n, total=total, card_key=key,
                  seconds_left=L.seconds_left(aware(a.round_started_at), now))


async def _result_screen(s, a: AcademyAttempt, st: AcademyStudent, season: AcademySeason, rows: list[AcademyRound], now: datetime) -> Screen:
    last = rows[-1]
    card = CARDS.BY_KEY[last.card_key]
    timed_out = last.choice == "timeout"
    if timed_out:
        btn = None
        line, stat, risk, chance = "", "", "", 0
    else:
        btn = card.buttons[int(last.choice)]
        line, stat, risk = (btn.win if last.success else btn.lose), btn.stat, btn.risk
        prep = await prep_of(s, st, season, a.exam_kind)
        chance = round(L.chance_of(prep, card, btn)[0] * 100)
    left = max(0, AR.FOCUS_MAX_USES - a.focus_used)
    probation = await probation_status(s, st.user_id, st.season_id, now) if a.exam_kind == "finals" else False
    can_focus = (not last.success and not last.focused and left > 0 and not probation and not timed_out
                 and L.exam_open_now(aware(season.start_at), now) == a.exam_kind)
    return Screen("result", a.exam_kind, a.id, st.season_id, n=last.n, total=AR.EXAM_ROUNDS[a.exam_kind], card_key=last.card_key,
                  last=dict(card_key=last.card_key, choice=last.choice, success=bool(last.success), points=last.points,
                            line=line, stat=stat, risk=risk, chance=chance, timed_out=timed_out, focused=bool(last.focused)),
                  can_focus=can_focus, focus_left=left)


async def _load(s, user_id: int, now: datetime):
    season = await cal.ensure_season(s, now)
    ph = cal.phase_at(aware(season.start_at), now)
    st = await stu.get_student(s, user_id, season.id, lock=True)
    return season, ph, st


# ------------------------------------------------------------------ /exam
async def open_exam(s, user_id: int, now: datetime | None = None, rng: random.Random | None = None) -> Screen:
    """Start (first time) or resume (later) the exam that is open now. Raises ExamBlocked with a friendly text otherwise."""
    now = aware(now) if now else utcnow()
    rng = rng or random.Random()
    season, ph, st = await _load(s, user_id, now)
    start = aware(season.start_at)
    if ph.key == AR.BREAK_KEY:
        raise ExamBlocked(T.BREAK)
    if st is None:
        win = cal.enroll_window(start, now)
        raise ExamBlocked(T.NEED_ENROLL if win.open else T.NEED_ENROLL_CLOSED, enroll=win.open)
    kind = L.exam_open_now(start, now)
    if kind is None:
        txt = T.NOT_OPEN
        nxt = L.next_exam(start, now)
        if nxt:
            txt += "\n" + T.NEXT_EXAM.format(name=AR.EXAM_NAMES[nxt[0]], when=cal.fmt_when(nxt[1]))
        raise ExamBlocked(txt)
    await ensure_exam(s, season, kind)
    a = await _attempt(s, st.id, kind, lock=True)
    total = AR.EXAM_ROUNDS[kind]
    first = False
    if a is None:
        seen: list[str] = []
        if kind == "finals":
            m = await _attempt(s, st.id, "midterm")
            seen = _keys(m) if m else []
        keys = L.draw_cards(st.stream, kind, rng, exclude=seen)
        await insert_ignore(s, AcademyAttempt, dict(student_id=st.id, exam_kind=kind, started_at=now, state="active",
                                                    cards=",".join(keys), round_started_at=now, focus_used=0, reward=0))
        a = await _attempt(s, st.id, kind, lock=True)
        first = True
    if a.state != "active":
        return await _finalize(s, a, st, now, expired=a.state == "expired")
    rows = await _rounds(s, a.id)
    if a.round_started_at is not None:                           # a question is on screen
        if L.round_expired(aware(a.round_started_at), now) and not first:
            await _record(s, a, len(rows) + 1, "timeout", 0, False, AR.TIMEOUT_POINTS)
            a.round_started_at = None
            rows = await _rounds(s, a.id)
            return await _result_screen(s, a, st, season, rows, now)
        scr = _question(a, kind, len(rows) + 1, now, season.id, total)
        scr.first = first
        if first:
            scr.events.append((ev.EXAM_STARTED, dict(season_id=season.id, exam=kind, student_id=st.id)))
        return scr
    return await _result_screen(s, a, st, season, rows, now)         # waiting at a result: Next / Focus


async def _record(s, a: AcademyAttempt, n: int, choice: str, roll: int, success: bool, points: int) -> AcademyRound:
    key = _keys(a)[n - 1]
    await insert_ignore(s, AcademyRound, dict(attempt_id=a.id, n=n, card_key=key, choice=choice, roll=roll, success=success,
                                              points=points, focused=0))
    return (await s.execute(select(AcademyRound).where(AcademyRound.attempt_id == a.id, AcademyRound.n == n))).scalar_one()


async def _mine(s, user_id: int, attempt_id: int, now: datetime):
    """(attempt, student, season) if the attempt belongs to `user_id`, else raises GameError (not your exam)."""
    a = (await s.execute(select(AcademyAttempt).where(AcademyAttempt.id == attempt_id).with_for_update())).scalar_one_or_none()
    st = await s.get(AcademyStudent, a.student_id) if a else None
    if a is None or st is None or st.user_id != user_id:
        raise GameError(T.NOT_YOURS)
    season = await s.get(AcademySeason, st.season_id)
    return a, st, season


async def _closed_check(s, a, st, season, now) -> Screen | None:
    """If the exam window is over, finish the attempt (state 'expired') and return its final screen."""
    if a.state != "active":
        return await _finalize(s, a, st, now, expired=a.state == "expired")
    if L.exam_open_now(aware(season.start_at), now) != a.exam_kind:
        return await _finalize(s, a, st, now, expired=True)
    return None


# ------------------------------------------------------------------ a strategy button
async def answer(s, user_id: int, attempt_id: int, n: int, idx: int, now: datetime | None = None,
                 rng: random.Random | None = None) -> Screen:
    """Play round `n` with button `idx`. A late press (over 25 s) is recorded as a timeout. Stale / double presses are
    refused (round number must be the one on screen) and change nothing."""
    now = aware(now) if now else utcnow()
    rng = rng or random.Random()
    a, st, season = await _mine(s, user_id, attempt_id, now)
    done = await _closed_check(s, a, st, season, now)
    if done:
        return done
    rows = await _rounds(s, a.id)
    if n != len(rows) + 1 or a.round_started_at is None:
        raise GameError(T.STALE)
    card = CARDS.BY_KEY[_keys(a)[n - 1]]
    if L.round_expired(aware(a.round_started_at), now):
        await _record(s, a, n, "timeout", 0, False, AR.TIMEOUT_POINTS)
    else:
        if not 0 <= idx < len(card.buttons):
            raise GameError(T.STALE)
        prep = await prep_of(s, st, season, a.exam_kind)
        r = L.play_round(prep, card, idx, rng)
        await _record(s, a, n, str(idx), r.roll, r.success, r.points)
    a.round_started_at = None
    rows = await _rounds(s, a.id)
    scr = await _result_screen(s, a, st, season, rows, now)
    scr.events.append((ev.EXAM_ROUND, dict(season_id=st.season_id, exam=a.exam_kind, n=n, success=scr.last["success"],
                                           points=scr.last["points"], student_id=st.id)))
    return scr


async def focus(s, user_id: int, attempt_id: int, n: int, now: datetime | None = None,
                rng: random.Random | None = None) -> Screen:
    """Re-roll the failed round on screen (once per round, FOCUS_MAX_USES per exam, costs Energy, off on probation)."""
    now = aware(now) if now else utcnow()
    rng = rng or random.Random()
    a, st, season = await _mine(s, user_id, attempt_id, now)
    done = await _closed_check(s, a, st, season, now)
    if done:
        return done
    rows = await _rounds(s, a.id)
    if not rows or n != len(rows) or a.round_started_at is not None:
        raise GameError(T.STALE)
    last = rows[-1]
    if last.success or last.focused or last.choice == "timeout":
        raise GameError(T.FOCUS_NOT_NOW)
    if a.focus_used >= AR.FOCUS_MAX_USES:
        raise GameError(T.FOCUS_NONE)
    if a.exam_kind == "finals" and await probation_status(s, user_id, st.season_id, now):
        raise GameError(T.FOCUS_PROBATION)
    await stu.spend_energy(s, user_id, st.season_id, AR.FOCUS_ENERGY, now)      # GameError if too tired: nothing changes
    card = CARDS.BY_KEY[last.card_key]
    prep = await prep_of(s, st, season, a.exam_kind)
    r = L.play_round(prep, card, int(last.choice), rng)
    last.roll, last.success, last.points, last.focused = r.roll, r.success, r.points, 1
    a.focus_used += 1
    await s.flush()
    rows = await _rounds(s, a.id)
    scr = await _result_screen(s, a, st, season, rows, now)
    scr.events.append((ev.EXAM_ROUND, dict(season_id=st.season_id, exam=a.exam_kind, n=n, success=r.success, points=r.points,
                                           focus=True, student_id=st.id)))
    return scr


async def next_round(s, user_id: int, attempt_id: int, n: int, now: datetime | None = None) -> Screen:
    """Leave the result of round `n`: show round n+1 (its 25 s start NOW), or finish the exam after the last round."""
    now = aware(now) if now else utcnow()
    a, st, season = await _mine(s, user_id, attempt_id, now)
    done = await _closed_check(s, a, st, season, now)
    if done:
        return done
    rows = await _rounds(s, a.id)
    total = AR.EXAM_ROUNDS[a.exam_kind]
    if not rows or n != len(rows) or a.round_started_at is not None:
        raise GameError(T.STALE)
    if len(rows) >= total:
        return await _finalize(s, a, st, now, expired=False)
    a.round_started_at = now
    await s.flush()
    return _question(a, a.exam_kind, len(rows) + 1, now, season.id, total)


async def announce(user_id: int, scr: Screen) -> None:
    """Emit the events collected by a call (AFTER the commit)."""
    for kind, data in scr.events:
        try:
            await ev.emit(kind, user_id, **data)
        except Exception:
            log.exception("academy exam: announcing %s failed", kind)
    scr.events = []


# ------------------------------------------------------------------ /grades
async def grades_report(s, user_id: int, now: datetime | None = None) -> dict:
    """Everything /grades shows. Also finishes an attempt whose window closed (or whose last round is done) lazily."""
    now = aware(now) if now else utcnow()
    season = await cal.ensure_season(s, now)
    start = aware(season.start_at)
    st = await stu.get_student(s, user_id, season.id, lock=True)
    out: dict = dict(season=season, enrolled=st is not None, exams=[], score=None, grade=None, gpa=None, provisional=True,
                     probation=False, rank=None, ranked=0, events=[])
    if st is None:
        return out
    scores: dict[str, float] = {}
    for kind in AR.EXAM_KINDS:
        lo, hi = L.exam_window(start, kind)
        a = await _attempt(s, st.id, kind, lock=True)
        info = dict(kind=kind, opens=lo, closes=hi, state="upcoming" if now < lo else ("open" if now < hi else "missed"),
                    score=None, grade=None, rounds=0, total=AR.EXAM_ROUNDS[kind], reward=0)
        if a is not None:
            if a.state == "active" and now >= hi:                       # the window closed: finish it now
                scr = await _finalize(s, a, st, now, expired=True)
                out["events"].extend((k, d) for k, d in scr.events)
            rows = await _rounds(s, a.id)
            info["rounds"] = len(rows)
            info["reward"] = a.reward
            if a.state == "active":
                info["state"] = "in_progress"
            else:
                info.update(state="done", score=float(a.score or 0), grade=a.grade)
                scores[kind] = float(a.score or 0)
        out["exams"].append(info)
    score, prov = _season_score(scores, start, now)
    if score is not None:
        out.update(score=score, gpa=L.gpa_of(score), grade=L.grade_of(score), provisional=prov, probation=L.on_probation(score))
        ranked = await season_results(s, season.id, now)
        out["ranked"] = len(ranked)
        for r in ranked:
            if r.user_id == user_id:
                out["rank"] = r.rank
    return out


# ------------------------------------------------------------------ ranking
async def season_results(session, season_id: int, now: datetime | None = None, user_ids: set[int] | None = None) -> list[L.Ranked]:
    """Ranked students of a season (best first; ties share a rank) with GPA and grade, for the graduation ceremony.
    Students without a finished exam are not listed. While an exam window is still open the GPA is `provisional`.
    `user_ids` restricts the list (group scope). Flagged / banned players are left out."""
    now = aware(now) if now else utcnow()
    season = await session.get(AcademySeason, season_id)
    if season is None:
        return []
    start = aware(season.start_at)
    q = (select(AcademyStudent.id, AcademyStudent.user_id, User.first_name, AcademyAttempt.exam_kind, AcademyAttempt.score)
         .join(AcademyAttempt, AcademyAttempt.student_id == AcademyStudent.id)
         .join(User, User.id == AcademyStudent.user_id)
         .where(AcademyStudent.season_id == season_id, AcademyAttempt.state.in_(("done", "expired")),
                User.is_flagged.is_(False), User.is_banned.is_(False)))
    if user_ids is not None:
        if not user_ids:
            return []
        q = q.where(AcademyStudent.user_id.in_(user_ids))
    by: dict[int, dict] = {}
    for sid, uid, name, kind, score in (await session.execute(q)).all():
        d = by.setdefault(sid, dict(uid=uid, name=name or "Player", scores={}))
        if score is not None:
            d["scores"][kind] = float(score)
    rows: list[L.Ranked] = []
    for sid, d in by.items():
        score, prov = _season_score(d["scores"], start, now)
        if score is None:
            continue
        rows.append(L.Ranked(0, d["uid"], sid, d["name"], score, L.gpa_of(score), L.grade_of(score), d["scores"].get("midterm"),
                             d["scores"].get("finals"), prov, L.on_probation(score)))
    return L.rank_rows(rows)


async def note_chat(s, chat_id: int, user_id: int) -> None:
    """Remember that this player uses the bot in this group (group /classrank). One insert-ignore, only on exam commands."""
    await insert_ignore(s, AcademyChatMember, dict(chat_id=chat_id, user_id=user_id))


async def chat_user_ids(s, chat_id: int) -> set[int]:
    a = (await s.execute(select(AcademyChatMember.user_id).where(AcademyChatMember.chat_id == chat_id))).scalars().all()
    b = (await s.execute(select(GroupStat.user_id).where(GroupStat.chat_id == chat_id))).scalars().all()
    return set(a) | set(b)


async def class_rank(s, user_id: int, chat_id: int | None, now: datetime | None = None) -> dict:
    """{'top': [Ranked x10], 'me': Ranked|None, 'total': n, 'scope': 'group'|'global'} for the current season.
    chat_id = None -> global."""
    now = aware(now) if now else utcnow()
    season = await cal.ensure_season(s, now)
    ids = await chat_user_ids(s, chat_id) if chat_id is not None else None
    rows = await season_results(s, season.id, now, ids)
    me = next((r for r in rows if r.user_id == user_id), None)
    return dict(top=rows[:AR.RANK_TOP], me=me, total=len(rows), scope="group" if chat_id is not None else "global",
                provisional=any(r.provisional for r in rows))


# ------------------------------------------------------------------ tick: finish attempts whose window closed
@tick(every=120, name="academy.exam_close")
async def exam_close_tick(bot, now) -> None:
    now = aware(now) if now else utcnow()
    done: list[tuple[int, Screen]] = []
    async with db.tx() as s:
        q = (select(AcademyAttempt, AcademyExam.closes_at)
             .join(AcademyStudent, AcademyStudent.id == AcademyAttempt.student_id)
             .join(AcademyExam, and_(AcademyExam.season_id == AcademyStudent.season_id, AcademyExam.kind == AcademyAttempt.exam_kind))
             .where(AcademyAttempt.state == "active", AcademyExam.closes_at <= now).limit(200))
        for a, _ in (await s.execute(q)).all():
            st = await s.get(AcademyStudent, a.student_id)
            if st is None:
                continue
            done.append((st.user_id, await _finalize(s, a, st, now, expired=True)))
    for uid, scr in done:                                         # messages and events after the commit
        try:
            from services.academy_exam_views import final_text
            await safe_send(bot, uid, final_text(scr))
        except Exception:
            log.exception("academy exam: could not notify %s", uid)
        await announce(uid, scr)


# ------------------------------------------------------------------ /academyreset
@ev.subscribe(ev.RESET)
async def _on_reset(user_id, **data) -> None:
    async with db.tx() as s:
        for model in (AcademyRound, AcademyAttempt, AcademyExam, AcademyChatMember):
            await s.execute(delete(model))
