"""Academy exams, Part 3: database rules (attempts, resume, 25 s limit, Focus, grades, rewards, ranks, tick, reset) and end to end
through the real Dispatcher. Run: pytest tests/test_academy_exams.py   (the pure half is tests/test_academy_exam_logic.py)"""
import asyncio
import os
import random
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest

DB = os.path.join(tempfile.gettempdir(), "academy_exams_test.db")
URL = f"sqlite+aiosqlite:///{DB}"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)        # season 1 starts here (Day n = T0 + (n-1) days)
H = timedelta(hours=1)
D = timedelta(days=1)
S = timedelta(seconds=1)


def at(day: int, hours: float = 2) -> datetime:
    return T0 + (day - 1) * D + timedelta(hours=hours)


def run(c):
    return asyncio.run(c)


class Dice(random.Random):
    """A Random whose dice (randint) always roll `v`; shuffle etc. still work (seeded)."""
    def __init__(self, v=50, seed=0):
        super().__init__(seed)
        self.v = v

    def randint(self, a, b):
        return self.v


def _owner1(monkeypatch):
    from config import get_settings
    monkeypatch.setenv("OWNER_ID", "1")
    get_settings.cache_clear()


def _detach_routers():
    import importlib
    from handlers import academy_routers
    for name in ("admin", "ai_chat", "casino", "clan", "combat", "economy", "events", "fun", "pets", "rank", "shop", "start", "welcome",
                 "extras", "life", "realm", "family", "arcade", "romance", *[m.split(".")[1] for m in academy_routers.MODULES]):
        try:
            importlib.import_module(f"handlers.{name}").router._parent_router = None
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ACADEMY_BREAK_HOURS", raising=False)
    monkeypatch.setenv("ACADEMY_ENABLED", "1")
    from utils import gifs
    monkeypatch.setattr(gifs, "ENABLED", False)
    yield
    _detach_routers()


async def _setup(n: int = 4, season: bool = True):
    from database.session import db
    from services import academy_calendar as cal, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in range(1, n + 1):
        await users.touch(uid, f"u{uid}", f"User{uid}")
    if season:
        async with db.tx() as s:
            await cal.ensure_season(s, T0)
    return db


async def _enrol(db, uid, stream="arts", club="music", now=None, stats=None):
    from services import academy_calendar as cal, academy_students as stu
    now = now or at(1, 1)
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        st, _ = await stu.enroll(s, uid, season, stream, club, now)
        if stats:
            await stu.add_stats(s, uid, season.id, stats)
    return season


async def _balance(uid):
    from services import economy
    return (await economy.balance(uid)).balance


def _bal_idx(card_key, risk="balanced"):
    from utils.content import academy_exam_rounds as CR
    return next(i for i, b in enumerate(CR.BY_KEY[card_key].buttons) if b.risk == risk)


async def _open(db, uid, now, rng=None):
    from services import academy_exams as ex
    async with db.tx() as s:
        return await ex.open_exam(s, uid, now, rng or random.Random(1))


async def _answer(db, uid, scr, idx, now, rng=None):
    from services import academy_exams as ex
    async with db.tx() as s:
        return await ex.answer(s, uid, scr.attempt_id, scr.n, idx, now, rng or Dice(50))


async def _next(db, uid, scr, now):
    from services import academy_exams as ex
    async with db.tx() as s:
        return await ex.next_round(s, uid, scr.attempt_id, scr.n, now)


async def _play_all(db, uid, kind_day, rng=None, strat="balanced"):
    """Play a whole exam with the balanced button of every card. Returns the final screen."""
    now = at(kind_day, 2)
    scr = await _open(db, uid, now)
    while scr.status != "done":
        if scr.status == "question":
            scr = await _answer(db, uid, scr, _bal_idx(scr.card_key, strat), now, rng)
        else:
            scr = await _next(db, uid, scr, now)
    return scr


STRONG = {"knowledge": 60, "charisma": 60, "fitness": 60, "creativity": 60}


async def _attempts(db):
    from sqlalchemy import select
    from database.models import AcademyAttempt
    async with db.tx() as s:
        return list((await s.execute(select(AcademyAttempt))).scalars())


async def _force_cards(db, attempt_id, keys):
    from database.models import AcademyAttempt
    async with db.tx() as s:
        a = await s.get(AcademyAttempt, attempt_id)
        a.cards = ",".join(keys)



# ------------------------------------------------------------------ gating
def test_exam_only_in_the_window_and_only_for_students():
    async def go():
        from services import academy_exams as ex
        db = await _setup()
        await _enrol(db, 1)
        for day, ok in ((1, False), (2, False), (3, False), (4, True), (5, False), (6, True), (7, False)):
            if ok:
                scr = await _open(db, 1, at(day, 2))
                assert scr.kind == ("midterm" if day == 4 else "finals") and scr.status == "question"
            else:
                with pytest.raises(ex.ExamBlocked) as e:
                    await _open(db, 1, at(day, 2))
                assert "No exam is open" in e.value.text
        with pytest.raises(ex.ExamBlocked) as e:                       # day 1: next exam is announced
            await _open(db, 1, at(1, 2))
        assert "Midterm" in e.value.text and "Next exam" in e.value.text
        with pytest.raises(ex.ExamBlocked) as e:                       # not enrolled, enrolment closed on Day 6
            await _open(db, 3, at(6, 2))
        assert e.value.enroll is False and "not enrolled" in e.value.text
        with pytest.raises(ex.ExamBlocked) as e:                       # not enrolled, enrolment open on Day 2 -> Enroll button
            await _open(db, 3, at(2, 2))
        assert e.value.enroll is True
        with pytest.raises(ex.ExamBlocked) as e:
            await _open(db, 1, T0 + 7 * D + H)
        assert "break" in e.value.text
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ a whole exam
def test_midterm_flow_grade_and_reward_paid_once():
    async def go():
        from sqlalchemy import select, func
        from database.models import AcademyRound
        from game import academy_rules as AR
        from services import academy_exams as ex
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        before = await _balance(1)
        fin = await _play_all(db, 1, 4)
        assert fin.status == "done" and fin.total == 5 and fin.won == 5
        assert fin.score == 100.0 and fin.grade == "A+" and fin.gpa == 10.0 and fin.reward == AR.EXAM_REWARD["A+"]
        assert await _balance(1) == before + AR.EXAM_REWARD["A+"]
        atts = await _attempts(db)
        assert len(atts) == 1 and atts[0].state == "done" and atts[0].grade == "A+" and atts[0].finished_at is not None
        async with db.tx() as s:
            assert (await s.execute(select(func.count()).select_from(AcademyRound))).scalar_one() == 5
        again = await _open(db, 1, at(4, 5))                           # one attempt only: shows the result, pays nothing
        assert again.status == "done" and again.score == 100.0
        assert await _balance(1) == before + AR.EXAM_REWARD["A+"] and len(await _attempts(db)) == 1
        await db.dispose()
    run(go())


def test_finals_has_eight_rounds_and_new_cards():
    async def go():
        from database.models import AcademyAttempt
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        await _play_all(db, 1, 4)
        fin = await _play_all(db, 1, 6)
        assert fin.total == 8 and fin.won == 8
        atts = {a.exam_kind: a for a in await _attempts(db)}
        m, f = atts["midterm"].cards.split(","), atts["finals"].cards.split(",")
        assert len(m) == 5 and len(f) == 8 and not set(m) & set(f)
        await db.dispose()
    run(go())


def test_resume_shows_the_same_question_then_the_result():
    async def go():
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        a = await _open(db, 1, at(4, 2))
        assert a.first and a.status == "question" and a.n == 1
        b = await _open(db, 1, at(4, 2) + 5 * S)                        # left and came back: same card, less time
        assert b.card_key == a.card_key and b.n == 1 and not b.first and b.seconds_left < a.seconds_left
        r = await _answer(db, 1, b, 0, at(4, 2) + 8 * S)
        assert r.status == "result" and r.n == 1
        c = await _open(db, 1, at(4, 3))                                # left at the result screen: no clock runs
        assert c.status == "result" and c.n == 1 and c.card_key == a.card_key
        q = await _next(db, 1, c, at(4, 3))
        assert q.status == "question" and q.n == 2 and q.seconds_left == 25
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ the 25 seconds
def test_time_limit_is_enforced_by_timestamps():
    async def go():
        from sqlalchemy import select
        from database.models import AcademyRound
        from game import academy_rules as AR
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        t = at(4, 2)
        q = await _open(db, 1, t)
        # on time (inside 25 s + grace): counts
        ok = await _answer(db, 1, q, _bal_idx(q.card_key), t + (AR.ROUND_SECONDS + AR.ROUND_GRACE_SECONDS) * S)
        assert not ok.last["timed_out"] and ok.last["success"]
        q2 = await _next(db, 1, ok, t + 60 * S)
        late = await _answer(db, 1, q2, _bal_idx(q2.card_key), t + 60 * S + (AR.ROUND_SECONDS + AR.ROUND_GRACE_SECONDS + 1) * S)
        assert late.last["timed_out"] and not late.last["success"] and late.last["points"] == AR.TIMEOUT_POINTS and not late.can_focus
        async with db.tx() as s:
            rows = list((await s.execute(select(AcademyRound).order_by(AcademyRound.n))).scalars())
        assert [r.choice == "timeout" for r in rows] == [False, True]
        await db.dispose()
    run(go())


def test_leaving_and_returning_after_the_time_costs_that_round():
    async def go():
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        t = at(4, 2)
        q = await _open(db, 1, t)
        back = await _open(db, 1, t + 10 * 60 * S)                       # came back ten minutes later
        assert back.status == "result" and back.last["timed_out"] and back.n == 1
        nxt = await _next(db, 1, back, t + 11 * 60 * S)
        assert nxt.status == "question" and nxt.n == 2 and nxt.card_key != q.card_key
        await db.dispose()
    run(go())


def test_stale_double_and_foreign_presses_change_nothing():
    async def go():
        from services import academy_exams as ex
        from services.errors import GameError
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        await _enrol(db, 2, stats=STRONG)
        t = at(4, 2)
        q = await _open(db, 1, t)
        await _answer(db, 1, q, 0, t + S)
        with pytest.raises(GameError):                                   # double tap on the same round
            await _answer(db, 1, q, 1, t + 2 * S)
        with pytest.raises(GameError):                                   # somebody else's attempt
            await _answer(db, 2, q, 1, t + 2 * S)
        async with db.tx() as s:
            with pytest.raises(GameError):                               # Next for a round that is not on screen
                await ex.next_round(s, 1, q.attempt_id, 3, t + 3 * S)
        from sqlalchemy import select, func
        from database.models import AcademyRound
        async with db.tx() as s:
            assert (await s.execute(select(func.count()).select_from(AcademyRound))).scalar_one() == 1
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ Focus
def _band_setup():
    """Student whose group_project/knowledge chance is exactly 0.5: roll 1 fails, roll 100 succeeds."""
    return {"knowledge": 35}


def test_focus_rerolls_one_failed_round_costs_energy_and_is_limited():
    async def go():
        from sqlalchemy import select
        from database.models import AcademyAttempt
        from game import academy_rules as AR
        from services import academy_exams as ex, academy_students as stu
        from services.errors import GameError
        db = await _setup()
        await _enrol(db, 1, stream="arts", stats=_band_setup())
        t = at(4, 2)
        q = await _open(db, 1, t)
        await _force_cards(db, q.attempt_id, ["group_project"] * 5)
        q = await _open(db, 1, t + S)
        idx = _bal_idx("group_project")                                   # the knowledge button
        r = await _answer(db, 1, q, idx, t + 2 * S, Dice(1))
        assert not r.last["success"] and r.can_focus and r.focus_left == AR.FOCUS_MAX_USES
        async with db.tx() as s:
            before = stu.current_energy(await stu.get_student(s, 1, 1), t + 3 * S)
        async with db.tx() as s:
            f = await ex.focus(s, 1, r.attempt_id, r.n, t + 3 * S, Dice(100))
        assert f.last["success"] and f.last["focused"] and not f.can_focus and f.focus_left == AR.FOCUS_MAX_USES - 1
        async with db.tx() as s:
            assert stu.current_energy(await stu.get_student(s, 1, 1), t + 3 * S) == before - AR.FOCUS_ENERGY
        async with db.tx() as s:
            with pytest.raises(GameError):                                 # a won / already focused round cannot be re-rolled
                await ex.focus(s, 1, r.attempt_id, r.n, t + 4 * S, Dice(1))
        # next round: fail, then max uses reached
        q2 = await _next(db, 1, f, t + 5 * S)
        r2 = await _answer(db, 1, q2, idx, t + 6 * S, Dice(1))
        async with db.tx() as s:
            a = await s.get(AcademyAttempt, r2.attempt_id)
            a.focus_used = AR.FOCUS_MAX_USES
        async with db.tx() as s:
            with pytest.raises(GameError):
                await ex.focus(s, 1, r2.attempt_id, r2.n, t + 7 * S, Dice(100))
        await db.dispose()
    run(go())


def test_focus_needs_energy_and_a_failed_round_and_is_refused_after_time_up():
    async def go():
        from database.models import AcademyStudent
        from game import academy_rules as AR
        from services import academy_exams as ex
        from services.errors import GameError
        db = await _setup()
        await _enrol(db, 1, stream="arts", stats=_band_setup())
        t = at(4, 2)
        q = await _open(db, 1, t)
        await _force_cards(db, q.attempt_id, ["group_project"] * 5)
        q = await _open(db, 1, t + S)
        r = await _answer(db, 1, q, _bal_idx("group_project"), t + 2 * S, Dice(1))
        async with db.tx() as s:                                            # too tired
            st = await s.get(AcademyStudent, 1)
            st.energy, st.energy_at = AR.FOCUS_ENERGY - 1, t + 2 * S
        async with db.tx() as s:
            with pytest.raises(GameError):
                await ex.focus(s, 1, r.attempt_id, r.n, t + 3 * S, Dice(100))
        async with db.tx() as s:                                            # nothing was changed by the refusal
            assert (await s.get(AcademyStudent, 1)).energy == AR.FOCUS_ENERGY - 1
        await db.dispose()
    run(go())


def test_probation_removes_focus_in_the_finals():
    async def go():
        from database.models import AcademyAttempt
        from services import academy_exams as ex
        from services.errors import GameError
        db = await _setup()
        await _enrol(db, 1, stream="arts", stats=_band_setup())
        await _finished(db, 1, "midterm", 20.0)                              # F in the midterm -> GPA 2.0
        async with db.tx() as s:
            assert await ex.probation_status(s, 1, 1, at(6, 2)) is True
        t = at(6, 2)
        q = await _open(db, 1, t)
        await _force_cards(db, q.attempt_id, ["group_project"] * 8)
        q = await _open(db, 1, t + S)
        r = await _answer(db, 1, q, _bal_idx("group_project"), t + 2 * S, Dice(1))
        assert not r.last["success"] and not r.can_focus
        async with db.tx() as s:
            with pytest.raises(GameError) as e:
                await ex.focus(s, 1, r.attempt_id, r.n, t + 3 * S, Dice(100))
        assert "probation" in str(e.value)
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ rewards
def test_reward_is_idempotent_even_if_the_finish_is_replayed():
    async def go():
        from game import academy_rules as AR
        from database.models import AcademyAttempt, AcademyStudent
        from services import academy_exams as ex, academy_students as stu
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        before = await _balance(1)
        fin = await _play_all(db, 1, 4)
        paid = AR.EXAM_REWARD[fin.grade]
        assert await _balance(1) == before + paid > before
        # a crash-and-retry: the attempt is back to "active" but the ledger ref was already used
        async with db.tx() as s:
            a = (await s.get(AcademyAttempt, fin.attempt_id))
            a.state, a.reward = "active", 0
            st = await s.get(AcademyStudent, a.student_id)
            again = await ex._finalize(s, a, st, at(4, 9), expired=False)
        assert await _balance(1) == before + paid                          # nothing paid twice
        assert again.reward == 0
        async with db.tx() as s:
            st = await stu.get_student(s, 1, 1)
            assert await stu.pay_diamonds(s, 1, 5, ex._reward_ref(1, st, "midterm")) is False
        await db.dispose()
    run(go())


def test_reward_table_pays_by_grade_and_nothing_for_f():
    async def go():
        from game import academy_rules as AR
        db = await _setup()
        await _enrol(db, 1)                                                  # a weak student: balanced rounds all fail -> F
        before = await _balance(1)
        fin = await _play_all(db, 1, 4)
        assert fin.grade == "F" and fin.reward == 0 and await _balance(1) == before
        assert AR.EXAM_REWARD["F"] == 0
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ window closes / tick
def test_window_closing_finishes_the_attempt_and_scores_the_rounds_played():
    async def go():
        from game import academy_rules as AR
        from services import academy_exams as ex
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        t = at(4, 2)
        q = await _open(db, 1, t)
        r = await _answer(db, 1, q, _bal_idx(q.card_key), t + S)
        before = await _balance(1)
        q2 = await _next(db, 1, r, t + 2 * S)
        done = await _answer(db, 1, q2, _bal_idx(q2.card_key), at(5, 3))     # Day 5: the Midterm window is over
        assert done.status == "done" and done.expired
        assert done.score == round(100 * 14 / (5 * AR.SCORE_REF), 1) == 20.0 and done.grade == "F"
        with pytest.raises(ex.ExamBlocked):
            await _open(db, 1, at(5, 3))
        await db.dispose()
    run(go())


def test_tick_finishes_abandoned_attempts_once_and_notifies():
    async def go():
        from services import academy_exams as ex
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        await _enrol(db, 2)
        t = at(4, 2)
        for uid in (1, 2):
            q = await _open(db, uid, t)
            await _answer(db, uid, q, _bal_idx(q.card_key), t + S)

        class Bot:
            sent = []

            async def send_message(self, chat_id, text, **kw):
                Bot.sent.append(chat_id)
                return None
        await ex.exam_close_tick(Bot(), t + 5 * S)                           # window still open: nothing happens
        assert Bot.sent == [] and all(a.state == "active" for a in await _attempts(db))
        before = await _balance(1)
        await ex.exam_close_tick(Bot(), at(5, 1))
        assert sorted(Bot.sent) == [1, 2] and all(a.state == "expired" for a in await _attempts(db))
        bal = await _balance(1)
        await ex.exam_close_tick(Bot(), at(5, 2))                            # again: idempotent
        assert sorted(Bot.sent) == [1, 2] and await _balance(1) == bal >= before
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ prep from academy_daily
def test_prep_uses_study_streak_and_attendance_and_late_joiners_are_lighter():
    async def go():
        from services import academy_exams as ex, academy_life as life, academy_students as stu
        from database.models import AcademySeason
        db = await _setup()
        await _enrol(db, 1)
        await _enrol(db, 2, now=at(4, 1))                                    # late joiner: enrolled on Day 4
        async with db.tx() as s:
            season = await s.get(AcademySeason, 1)
            st1, st2 = await stu.get_student(s, 1, 1), await stu.get_student(s, 2, 1)
            for day in (2, 3, 4):
                d = await life.get_daily(s, st1.id, day)
                d.study_count, d.attended = 2, 1
            d = await life.get_daily(s, st2.id, 4)
            d.attended = 1
            p1 = await ex.prep_of(s, st1, season, "midterm")
            p2 = await ex.prep_of(s, st2, season, "midterm")
        assert p1.streak == 3 and p1.attendance == 1.0
        assert p2.streak == 0 and p2.attendance == 1.0                       # 1 attended of the 1 class day it could attend
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ grades, ranking
async def _finished(db, uid, kind, score, state="done"):
    """Insert a finished attempt directly (for ranking tests)."""
    from database.models import AcademyAttempt
    from services import academy_exams as ex, academy_exam_logic as L, academy_students as stu
    async with db.tx() as s:
        st = await stu.get_student(s, uid, 1)
        await insert(s, AcademyAttempt, dict(student_id=st.id, exam_kind=kind, started_at=at(4, 1), finished_at=at(4, 2), score=score,
                                             grade=L.grade_of(score), state=state, cards="", focus_used=0, reward=0))


async def insert(s, model, values):
    from database.session import insert_ignore
    await insert_ignore(s, model, values)


def test_season_results_ranking_ties_provisional_and_scopes():
    async def go():
        from database.models import AcademyChatMember, User
        from services import academy_exams as ex
        db = await _setup(7)
        for uid in range(1, 7):
            await _enrol(db, uid)
        scores = {1: (90, 90), 2: (80, 80), 3: (70, 70), 4: (70, 70), 5: (50, None)}      # user 6: never took an exam
        for uid, (m, f) in scores.items():
            await _finished(db, uid, "midterm", float(m))
            if f is not None:
                await _finished(db, uid, "finals", float(f))
        async with db.tx() as s:
            res = await ex.season_results(s, 1, at(7, 2))                    # both windows closed
        assert [(r.user_id, r.rank, r.grade) for r in res] == [(1, 1, "A+"), (2, 2, "A"), (3, 3, "B+"), (4, 3, "B+"), (5, 5, "F")]
        assert res[0].gpa == 9.0 and res[4].score == 25.0 and not any(r.provisional for r in res)   # 50 + missing finals = 25
        assert res[4].probation and not res[0].probation and res[0].name == "User1"
        async with db.tx() as s:
            mid = await ex.season_results(s, 1, at(5, 2))                    # finals not played yet: provisional
        assert {r.user_id: r.provisional for r in mid} == {1: False, 2: False, 3: False, 4: False, 5: True}
        assert next(r for r in mid if r.user_id == 5).score == 50.0                # only the midterm counts until the finals close
        async with db.tx() as s:
            u = await s.get(User, 2)
            u.is_flagged = True
        async with db.tx() as s:
            res2 = await ex.season_results(s, 1, at(7, 2))
        assert [r.user_id for r in res2] == [1, 3, 4, 5] and [r.rank for r in res2] == [1, 2, 2, 4]      # flagged player is left out
        async with db.tx() as s:
            await ex.note_chat(s, -100, 1)
            await ex.note_chat(s, -100, 1)                                    # idempotent
            await ex.note_chat(s, -100, 5)
            grp = await ex.class_rank(s, 5, -100, at(7, 2))
            glob = await ex.class_rank(s, 5, None, at(7, 2))
            assert await ex.season_results(s, 99) == []
        assert [r.user_id for r in grp["top"]] == [1, 5] and grp["me"].rank == 2 and grp["scope"] == "group"
        assert glob["total"] == 4 and glob["me"].rank == 4 and glob["scope"] == "global"
        await db.dispose()
    run(go())


def test_class_rank_top_ten_plus_me():
    async def go():
        from game import academy_rules as AR
        from services import academy_exams as ex
        db = await _setup(14)
        for uid in range(1, 15):
            await _enrol(db, uid)
            await _finished(db, uid, "midterm", float(100 - uid))
        async with db.tx() as s:
            data = await ex.class_rank(s, 13, None, at(4, 12))
        assert len(data["top"]) == AR.RANK_TOP == 10 and data["me"].rank == 13 and data["total"] == 14
        await db.dispose()
    run(go())


def test_grades_report_shows_progress_gpa_and_rank():
    async def go():
        from services import academy_exams as ex, academy_exam_views as V
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        async with db.tx() as s:
            rep = await ex.grades_report(s, 1, at(2, 2))
        assert rep["score"] is None and [e["state"] for e in rep["exams"]] == ["upcoming", "upcoming"]
        assert "No exam yet" in V.grades_text(rep)
        await _play_all(db, 1, 4)
        async with db.tx() as s:
            rep = await ex.grades_report(s, 1, at(5, 2))
        assert rep["grade"] == "A+" and rep["provisional"] and rep["rank"] == 1 and rep["exams"][0]["state"] == "done"
        txt = V.grades_text(rep)
        assert "Midterm" in txt and "A+" in txt and "provisional" in txt and "#1" in txt
        async with db.tx() as s:                                              # Day 7: finals missed counts 0 -> 50
            rep = await ex.grades_report(s, 1, at(7, 2))
        assert not rep["provisional"] and rep["score"] == 50.0 and rep["grade"] == "C" and rep["exams"][1]["state"] == "missed"
        async with db.tx() as s:
            assert (await ex.grades_report(s, 3, at(7, 2)))["enrolled"] is False
        await db.dispose()
    run(go())


def test_reset_clears_every_exam_table():
    async def go():
        from sqlalchemy import select, func
        from database.models import AcademyAttempt, AcademyChatMember, AcademyExam, AcademyRound
        from services import academy_events as ev, academy_exams as ex
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        await _play_all(db, 1, 4)
        async with db.tx() as s:
            await ex.note_chat(s, -5, 1)
        await ev.emit(ev.RESET, 1)
        async with db.tx() as s:
            for m in (AcademyRound, AcademyAttempt, AcademyExam, AcademyChatMember):
                assert (await s.execute(select(func.count()).select_from(m))).scalar_one() == 0, m
        await db.dispose()
    run(go())


def test_events_are_emitted_after_the_commit():
    async def go():
        from services import academy_events as ev, academy_exams as ex
        db = await _setup()
        await _enrol(db, 1, stats=STRONG)
        seen = []

        @ev.subscribe(ev.EXAM_STARTED)
        async def a(uid, **d): seen.append(("start", d["exam"]))

        @ev.subscribe(ev.EXAM_ROUND)
        async def b(uid, **d): seen.append(("round", d["n"]))

        @ev.subscribe(ev.EXAM_FINISHED)
        async def c(uid, **d): seen.append(("finish", d["grade"]))
        try:
            t = at(4, 2)
            scr = await _open(db, 1, t)
            await ex.announce(1, scr)
            r = await _answer(db, 1, scr, _bal_idx(scr.card_key), t + S)
            await ex.announce(1, r)
            fin = await _play_all(db, 1, 4)
            await ex.announce(1, fin)
        finally:
            for kind, fn in ((ev.EXAM_STARTED, a), (ev.EXAM_ROUND, b), (ev.EXAM_FINISHED, c)):
                ev._subs[kind].remove(fn)
        assert seen[0] == ("start", "midterm") and ("round", 1) in seen and seen[-1] == ("finish", "A+")
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ help catalog
def test_exam_commands_are_in_help_but_not_in_the_slash_menu():
    from utils import helpcatalog as H
    for c in ("exam", "grades", "classrank"):
        assert c in H.DESC
        assert c not in H.MENU and c not in H.GROUP_MENU_EXTRA
        assert any(c in cmds for cat, cmds in H.CATEGORIES if cat == "Academy 🎓")
    assert "exam" not in H.STAFF


# ------------------------------------------------------------------ end to end through the real Dispatcher
class Harness:
    def __init__(self):
        from aiogram import Bot
        from aiogram.client.session.base import BaseSession
        outer = self

        class FakeSession(BaseSession):
            async def close(self): pass
            async def stream_content(self, *a, **k):
                yield b""

            async def make_request(self, bot, method, timeout=None):
                name = type(method).__name__
                outer.calls.append((name, method))
                if name == "SendMessage":
                    outer.mid += 1
                    return {"message_id": outer.mid, "date": int(time.time()), "chat": {"id": method.chat_id, "type": "private"}, "text": method.text}
                return True

        self.calls, self.mid, self.n = [], 1000, 0
        self.bot = Bot("123456:TEST", session=FakeSession())
        from bot import build_dispatcher
        _detach_routers()
        self.dp = build_dispatcher(None)

    def _user(self, uid):
        return {"id": uid, "is_bot": False, "first_name": f"User{uid}", "username": f"u{uid}"}

    async def _feed(self, raw):
        from services.kv import kv
        kv._mem = {k: v for k, v in kv._mem.items() if not k.startswith(("thr:", "cb:", "cmd:"))}
        self.n += 1
        raw["update_id"] = self.n
        await self.dp.feed_raw_update(self.bot, raw)

    async def say(self, uid, text):
        self.n += 1
        self.calls.clear()
        await self._feed({"message": {"message_id": self.n, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "from": self._user(uid),
                                      "text": text, "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]}})
        return self.sent()

    async def tap(self, uid, data):
        self.calls.clear()
        await self._feed({"callback_query": {"id": str(self.n + 1), "from": self._user(uid), "chat_instance": "ci", "data": data,
                                              "message": {"message_id": 1, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "text": "x"}}})
        return self.sent()

    def sent(self):
        return [m.text for n, m in self.calls if n in ("SendMessage", "EditMessageText")]

    def buttons(self):
        return [b.callback_data for _, m in self.calls if getattr(m, "reply_markup", None) for row in m.reply_markup.inline_keyboard for b in row if b.callback_data]



def test_exam_through_the_dispatcher(monkeypatch):
    async def go():
        from services import economy
        from game import academy_rules as AR
        _owner1(monkeypatch)
        await _setup(3, season=False)
        h = Harness()
        for u in (2, 3):
            await h.tap(u, f"ac:st:{u}:science")
            await h.tap(u, f"ac:cl:{u}:science:coding")
        out = await h.say(2, "/exam")
        assert "No exam is open" in out[0]                                    # Day 1
        await h.say(1, "/academyadvance")
        for _ in range(2):                                                    # Day 2 -> Day 4 (Midterms)
            await h.say(1, "/academyadvance")
        out = await h.say(2, "/exam")
        assert "Midterm" in out[0] and "Round 1/5" in out[0] and "25" in out[0]
        btns = h.buttons()
        assert len(btns) in (3, 4) and all(b.startswith("acx:c:2:") and len(b.encode()) < 64 for b in btns)
        denied = await h.tap(3, btns[0])                                      # somebody else's button: no edit, no round played
        assert denied == []
        out = await h.tap(2, btns[0])
        assert ("Success" in out[0] or "Not this time" in out[0]) and "Round 1/5" in out[0]
        nxt = h.buttons()
        assert any(b.startswith("acx:n:2:") for b in nxt) and all(len(b.encode()) < 64 for b in nxt)
        nxt_btn = [b for b in nxt if b.startswith("acx:n:2:")][0]
        out = await h.tap(2, btns[0])                                         # stale button (same round again): refused, nothing edited
        assert out == []
        for n in range(2, 6):
            nxt = nxt_btn
            out = await h.tap(2, nxt)
            assert f"Round {n}/5" in out[0]
            btns = h.buttons()
            await h.tap(2, btns[0])
            nxt_btn = [b for b in h.buttons() if b.startswith("acx:n:2:")][0]
        fin_btn = nxt_btn
        out = await h.tap(2, fin_btn)
        assert "Midterm finished" in out[0] and "Grade" in out[0]
        out = await h.say(2, "/exam")                                         # one attempt only
        assert "Midterm finished" in out[0]
        out = await h.say(2, "/grades")
        assert "Your grades" in out[0] and "Midterm" in out[0] and "Season GPA" in out[0]
        assert "acx:r:2:g" in h.buttons()
        out = await h.say(2, "/classrank")
        assert "Class rank" in out[0] and "User2" in out[0]
        out = await h.say(3, "/classrank")
        assert "You: <b>#" in out[0] or "not ranked" in out[0]
        out = await h.tap(3, "acx:g:2")                                       # foreign grades refresh: refused
        assert out == []
        # the kill switch
        monkeypatch.setenv("ACADEMY_ENABLED", "0")
        out = await h.say(2, "/exam")
        assert "closed" in out[0]
        from database.session import db
        await db.dispose()
    run(go())


def test_gifs_dance_on_a_grades_and_facepalm_on_a_fail():
    from handlers import academy_exams as h
    from services.academy_exams import Screen
    mk = lambda grade, status="done": Screen(status, "midterm", 1, 1, grade=grade)
    assert [h._gif_for(mk(g)) for g in ("A+", "A")] == ["dance", "dance"]
    assert h._gif_for(mk("F")) == "facepalm"
    assert [h._gif_for(mk(g)) for g in ("B+", "B", "C", "D")] == [None] * 4
    assert h._gif_for(mk("A+", "result")) is None                       # only on the final screen
    from utils import gifs
    assert {"dance", "facepalm"} <= set(gifs.GIF_ACTIONS)               # SFW actions that the GIF helper knows
    _detach_routers()
