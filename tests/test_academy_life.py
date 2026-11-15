"""Academy Life, Part 2 (daily life): database rules + end to end through the real Dispatcher.
Phase gating, Energy costs, cooldowns, daily limits, the diamond cap, the study streak, club points, idempotent rewards,
events, reset. Run: pytest tests/test_academy_life.py   (the pure half is tests/test_academy_actions.py)"""
import asyncio
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest

DB = os.path.join(tempfile.gettempdir(), "academy_life_test.db")
URL = f"sqlite+aiosqlite:///{DB}"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)        # season 1 starts here (Day n = T0 + (n-1) days)
H = timedelta(hours=1)
D = timedelta(days=1)


def at(day: int, hours: float = 2) -> datetime:
    """A moment on season day `day` of season 1 (all in-season tests use the same clock offsets)."""
    return T0 + (day - 1) * D + timedelta(hours=hours)


def run(c):
    return asyncio.run(c)


class Fixed:
    """A 'random' that always rolls the top of every range / the first item, with a fixed random() value."""
    def __init__(self, high=True, p=0.99):
        self.high, self.p = high, p

    def randint(self, a, b):
        return b if self.high else a

    def random(self):
        return self.p

    def choice(self, seq):
        return seq[0]


def _owner1(monkeypatch):
    from config import get_settings
    monkeypatch.setenv("OWNER_ID", "1")
    get_settings.cache_clear()


def _detach_routers():
    """Module-level routers can only belong to one Dispatcher; free them so other test files can build their own."""
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
    monkeypatch.setattr(gifs, "ENABLED", False)               # no network in tests
    yield
    _detach_routers()


# ------------------------------------------------------------------ helpers
async def _setup(n: int = 3, season: bool = True):
    from database.session import db
    from services import academy_calendar as cal, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in range(1, n + 1):
        await users.touch(uid, f"u{uid}", f"User{uid}")
    if season:                                                # simulated-clock tests: season 1 starts exactly at T0
        async with db.tx() as s:
            await cal.ensure_season(s, T0)
    return db


async def _enrol(db, uid: int, stream: str = "music", club: str = "music", now: datetime | None = None):
    from services import academy_calendar as cal, academy_students as stu
    now = now or at(1, 1)
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        await stu.enroll(s, uid, season, stream, club, now)
    return season


async def _free(db):
    """Cooldowns use the real clock, so tests that simulate hours clear them between actions."""
    from sqlalchemy import delete
    from database.models import Cooldown
    async with db.tx() as s:
        await s.execute(delete(Cooldown))


async def _do(db, uid: int, key: str, now: datetime, rng=None, free: bool = True):
    """One action in ONE transaction, exactly like the handler. An exception rolls everything back."""
    from services import academy_life as life
    if free:
        await _free(db)
    async with db.tx() as s:
        return await life.perform(s, uid, key, now, rng or Fixed())


async def _student(db, uid: int, season_id: int = 1):
    from services import academy_students as stu
    async with db.tx() as s:
        st = await stu.get_student(s, uid, season_id)
        return st


async def _daily(db, uid: int, day: int, season_id: int = 1):
    from services import academy_life as life, academy_students as stu
    async with db.tx() as s:
        st = await stu.get_student(s, uid, season_id)
        return await life.get_daily(s, st.id, day)


async def _balance(uid: int) -> int:
    from services import economy
    return (await economy.balance(uid)).balance


async def _cooldown_left(db, uid: int, key: str) -> int:
    from services import cooldowns
    async with db.tx() as s:
        return await cooldowns.remaining(s, uid, f"academy:{key}")


# ------------------------------------------------------------------ phase gating
def test_not_enrolled_gets_a_friendly_message_with_enroll_button_only_while_enrolment_is_open():
    async def go():
        from services import academy_actions as A
        db = await _setup()
        for key in ("study", "attend", "hangout", "rest", "club", "parttime"):
            with pytest.raises(A.ActionBlocked) as e:
                await _do(db, 1, key, at(2))                                     # Day 2: late enrolment still open
            assert e.value.enroll is True and "Join the Academy" in e.value.text, key
        with pytest.raises(A.ActionBlocked) as e:
            await _do(db, 1, "study", at(5))                                     # Day 5: enrolment closed -> no button
        assert e.value.enroll is False and "closed" in e.value.text
        await db.dispose()
    run(go())


def test_everything_is_closed_during_the_break():
    async def go():
        from services import academy_actions as A
        from game import academy_rules as AR
        db = await _setup()
        await _enrol(db, 1)
        brk = T0 + 7 * D + H
        for key in AR.ACTIONS:
            with pytest.raises(A.ActionBlocked) as e:                            # enrolled player
                await _do(db, 1, key, brk)
            assert "break" in e.value.text and e.value.enroll is False, key
            with pytest.raises(A.ActionBlocked) as e:                            # not enrolled player
                await _do(db, 2, key, brk)
            assert "break" in e.value.text and e.value.enroll is False, key
        assert await _cooldown_left(db, 1, "study") == 0                          # nothing was started by a refused action
        await db.dispose()
    run(go())


def test_attend_only_on_class_days_and_parttime_never_on_finals():
    async def go():
        from services import academy_actions as A
        db = await _setup()
        for uid in (1, 2, 3):                                                     # one player per loop: the clock only moves forward
            await _enrol(db, uid)
        for day, ok in ((1, False), (2, True), (3, True), (4, True), (5, False), (6, False), (7, False)):
            if ok:
                res = await _do(db, 1, "attend", at(day))
                assert res.day == day and res.used == 1
            else:
                with pytest.raises(A.ActionBlocked) as e:
                    await _do(db, 1, "attend", at(day))
                assert "No classes" in e.value.text, day
                assert await _cooldown_left(db, 1, "attend") == 0
        for day, ok in ((1, True), (2, True), (3, True), (4, True), (5, True), (6, False), (7, True)):
            if ok:
                await _do(db, 2, "parttime", at(day))
            else:
                with pytest.raises(A.ActionBlocked) as e:
                    await _do(db, 2, "parttime", at(day))
                assert "Finals" in e.value.text
        for day in range(1, 8):                                                   # the other four are open all week
            for key in ("study", "hangout", "club", "rest"):
                res = await _do(db, 3, key, at(day))
                assert res.day == day, (day, key)
        await db.dispose()
    run(go())


def test_new_season_starts_fresh():
    async def go():
        from services import academy_actions as A, academy_life as life
        db = await _setup()
        await _enrol(db, 1, "sports", "sports")
        await _do(db, 1, "club", at(2))
        s2 = T0 + 7 * D + 6 * H                                                   # season 2 starts after the 6h break
        with pytest.raises(A.ActionBlocked) as e:                                 # must enrol again every season
            await _do(db, 1, "study", s2 + H)
        assert e.value.enroll is True
        season2 = await _enrol(db, 1, "arts", "coding", now=s2 + H)
        assert season2.number == 2
        res = await _do(db, 1, "club", s2 + 2 * H)
        assert res.club_total == res.club_points                                  # season 2's club score starts at 0
        async with db.tx() as s:
            assert dict(await life.club_standings(s, 1))["sports"] > 0 and dict(await life.club_standings(s, season2.id))["sports"] == 0
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ Energy
def test_energy_costs_per_action_and_rest():
    async def go():
        from game import academy_rules as AR
        db = await _setup()
        await _enrol(db, 1, "music", "music")
        now = at(3, 2)
        energy = 100
        for key in ("study", "hangout", "club", "parttime", "attend"):
            res = await _do(db, 1, key, now)
            energy -= AR.ACTIONS[key].energy
            assert res.energy == energy and res.energy_delta == -AR.ACTIONS[key].energy, key
            assert res.energy_bar and 0 <= res.energy <= 100
        st = await _student(db, 1)
        from services import academy_students as stu
        assert stu.current_energy(st, now) == energy
        res = await _do(db, 1, "rest", now)                                       # free, gives Energy back
        assert res.energy == energy + AR.REST_ENERGY and res.energy_delta == AR.REST_ENERGY
        d = await _daily(db, 1, 3)
        assert d.energy_spent == sum(AR.ACTIONS[k].energy for k in ("study", "hangout", "club", "parttime", "attend"))
        await db.dispose()
    run(go())


def test_not_enough_energy_blocks_without_charging_anything():
    async def go():
        from services import academy_actions as A, academy_students as stu
        db = await _setup()
        await _enrol(db, 1)
        now = at(2, 2)
        async with db.tx() as s:
            await stu.spend_energy(s, 1, 1, 95, now)                              # 5 left
        with pytest.raises(A.ActionBlocked) as e:
            await _do(db, 1, "study", now)                                        # needs 15
        assert "Not enough Energy" in e.value.text and "5" in e.value.text and e.value.enroll is False
        st = await _student(db, 1)
        assert stu.current_energy(st, now) == 5
        assert (await _daily(db, 1, 2)).study_count == 0 and await _cooldown_left(db, 1, "study") == 0
        res = await _do(db, 1, "rest", now)                                       # resting is free, so it always works
        assert res.energy == 5 + 20
        # Energy comes back by itself: 3 hours later there is enough again
        res = await _do(db, 1, "study", now + 3 * H)
        assert res.energy == 5 + 20 + 30 - 15
        await db.dispose()
    run(go())


def test_rest_is_capped_and_refused_when_energy_is_full():
    async def go():
        from services import academy_actions as A, academy_students as stu
        db = await _setup()
        await _enrol(db, 1, "sports", "sports")
        now = at(2, 2)
        with pytest.raises(A.ActionBlocked) as e:
            await _do(db, 1, "rest", now)
        assert "already full" in e.value.text and await _cooldown_left(db, 1, "rest") == 0
        assert (await _daily(db, 1, 2)).rest_count == 0
        async with db.tx() as s:
            await stu.spend_energy(s, 1, 1, 90, now)                              # 10 left
        res = await _do(db, 1, "rest", now)
        assert res.energy == 10 + 25 and res.perk and "Sports perk" in res.perk      # sports stream: +5 on top of +20
        async with db.tx() as s:
            await stu.restore_energy(s, 1, 1, 100, now)
            await stu.spend_energy(s, 1, 1, 5, now)                               # 95 left: rest only gives what fits
        res = await _do(db, 1, "rest", now)
        assert res.energy == 100 and res.energy_delta == 5
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ cooldowns and daily limits
def test_cooldown_blocks_a_second_use_and_charges_nothing():
    async def go():
        from services import academy_students as stu
        from services.errors import OnCooldown
        db = await _setup()
        await _enrol(db, 1)
        await _enrol(db, 2)
        now = at(2, 2)
        await _do(db, 1, "study", now)
        with pytest.raises(OnCooldown) as e:                                      # double tap / retry: refused
            await _do(db, 1, "study", now, free=False)
        assert e.value.seconds > 0 and "Study" in str(e.value)
        st = await _student(db, 1)
        assert stu.current_energy(st, now) == 85 and (await _daily(db, 1, 2)).study_count == 1
        await _do(db, 1, "hangout", now, free=False)                              # another action has its own cooldown
        await _do(db, 2, "study", now, free=False)                                # and so does another player
        await db.dispose()
    run(go())


def test_daily_limits_reset_on_the_next_day():
    async def go():
        from game import academy_rules as AR
        from services import academy_actions as A
        db = await _setup()
        await _enrol(db, 1)

        async def hit_limit(key, day):
            lim = AR.ACTIONS[key].daily_limit
            for i in range(lim):
                res = await _do(db, 1, key, at(day, 2 + i))                       # a little later each time: Energy regenerates
                assert res.used == i + 1 and res.limit == lim
            with pytest.raises(A.ActionBlocked) as e:
                await _do(db, 1, key, at(day, 2 + lim))
            assert f"{lim}/{lim}" in e.value.text, key

        await _do(db, 1, "attend", at(2, 2))                                      # attend: once a day
        with pytest.raises(A.ActionBlocked) as e:
            await _do(db, 1, "attend", at(2, 3))
        assert "already attended" in e.value.text
        await hit_limit("study", 2)
        res = await _do(db, 1, "study", at(3, 2))                                 # a new Academy day: the counter starts again
        assert res.used == 1
        await hit_limit("hangout", 4)
        await hit_limit("club", 5)
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ diamonds and the daily cap
def test_parttime_pays_through_the_ledger_and_respects_the_daily_cap():
    async def go():
        from sqlalchemy import func, select
        from database.models import Transaction
        from game import academy_rules as AR
        from services import academy_actions as A
        db = await _setup()
        await _enrol(db, 1, "arts", "drama")
        start = await _balance(1)
        paid = []
        for i in range(AR.ACTIONS["parttime"].daily_limit):
            res = await _do(db, 1, "parttime", at(2, 2))
            paid.append((res.paid, res.capped, res.diamonds_today))
        # best rolls: 40, 40, then only 20 is left under the 100 cap
        assert paid == [(40, False, 40), (40, False, 80), (20, True, 100)]
        assert await _balance(1) == start + 100 == start + AR.DAILY_DIAMOND_CAP
        with pytest.raises(A.ActionBlocked):                                       # 4th shift: over the shift limit
            await _do(db, 1, "parttime", at(2, 2))
        assert await _balance(1) == start + 100
        d = await _daily(db, 1, 2)
        assert d.diamonds == 100 and d.parttime_count == 3
        res = await _do(db, 1, "parttime", at(3, 2))                              # next day: the cap starts again
        assert res.paid == 40 and res.diamonds_today == 40 and await _balance(1) == start + 140
        async with db.tx() as s:
            rows = (await s.execute(select(func.count()).select_from(Transaction).where(Transaction.user_id == 1, Transaction.kind == "academy_pt"))).scalar_one()
        assert rows == 4
        await db.dispose()
    run(go())


def test_cap_stops_pay_even_when_shifts_are_left(monkeypatch):
    async def go():
        from game import academy_rules as AR
        from services import academy_actions as A
        monkeypatch.setattr(AR, "DAILY_DIAMOND_CAP", 50)
        db = await _setup()
        await _enrol(db, 1, "arts", "drama")
        start = await _balance(1)
        assert (await _do(db, 1, "parttime", at(2, 2))).paid == 40
        res = await _do(db, 1, "parttime", at(2, 2))
        assert res.paid == 10 and res.capped                                       # partial payout
        before_energy = (await _student(db, 1)).energy
        with pytest.raises(A.ActionBlocked) as e:                                  # 2 of 3 shifts used, but the cap is reached
            await _do(db, 1, "parttime", at(2, 2))
        assert "maximum of <b>50</b>" in e.value.text
        assert (await _student(db, 1)).energy == before_energy                     # refused: no Energy spent
        assert await _cooldown_left(db, 1, "parttime") == 0 and await _balance(1) == start + 50
        await db.dispose()
    run(go())


def test_max_diamonds_for_a_whole_season_is_bounded_even_for_commerce():
    async def go():
        from game import academy_rules as AR
        from services import academy_actions as A
        db = await _setup()
        await _enrol(db, 1, "commerce", "coding")
        start = await _balance(1)
        per_day = {}
        for day in AR.ALL_DAYS:
            for i in range(AR.ACTIONS["parttime"].daily_limit + 2):               # try more shifts than allowed
                try:
                    res = await _do(db, 1, "parttime", at(day, 2))
                except A.ActionBlocked:
                    break
                per_day[day] = res.diamonds_today
        assert set(per_day) == set(AR.ALL_DAYS) - {AR.FINALS_DAY}                  # nothing on Finals day
        assert all(v == AR.DAILY_DIAMOND_CAP for v in per_day.values())            # 48 + 48 + 4 with the commerce perk
        assert await _balance(1) - start == AR.DAILY_DIAMOND_CAP * 6 == 600
        await db.dispose()
    run(go())


def test_a_replayed_payment_changes_nothing():
    async def go():
        from database.models import aware
        from services import academy_students as stu
        from services.errors import GameError
        db = await _setup()
        await _enrol(db, 1, "arts", "drama")
        st = await _student(db, 1)
        ref = f"pt:1:{int(aware(st.enrolled_at).timestamp() * 1_000_000)}:d2:n1"         # the ref the first shift of Day 2 will use
        async with db.tx() as s:
            assert await stu.pay_diamonds(s, 1, 5, ref) is True                           # "already paid" (e.g. an earlier crash)
        start = await _balance(1)
        before = (await _student(db, 1)).energy
        with pytest.raises(GameError) as e:
            await _do(db, 1, "parttime", at(2, 2))
        assert "already paid" in str(e.value)
        d = await _daily(db, 1, 2)
        assert d.parttime_count == 0 and d.diamonds == 0 and d.energy_spent == 0          # the WHOLE action was rolled back
        assert (await _student(db, 1)).energy == before and await _balance(1) == start
        assert await _cooldown_left(db, 1, "parttime") == 0
        await db.dispose()
    run(go())


def test_double_submit_pays_once():
    async def go():
        from services.errors import OnCooldown
        db = await _setup()
        await _enrol(db, 1, "arts", "drama")
        start = await _balance(1)
        await _do(db, 1, "parttime", at(2, 2))
        for _ in range(3):
            with pytest.raises(OnCooldown):
                await _do(db, 1, "parttime", at(2, 2), free=False)
        assert await _balance(1) == start + 40 and (await _daily(db, 1, 2)).parttime_count == 1
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ study streak
def test_study_streak_milestone_is_paid_once_and_resets_after_a_gap():
    async def go():
        from services import academy_events as ev, academy_life as life
        db = await _setup()
        await _enrol(db, 1, "music", "music")                                       # music: Knowledge 10, no study perk
        got = []

        @ev.subscribe(ev.STUDY_STREAK)
        def on_streak(uid, **d):
            got.append((uid, d["streak"], d["bonus"]))

        r1 = await _do(db, 1, "study", at(1))
        r2 = await _do(db, 1, "study", at(2))
        assert (r1.streak, r1.milestone, r1.gif) == (1, False, None) and (r2.streak, r2.milestone) == (2, False)
        assert (await _student(db, 1)).knowledge == 10 + 3 + 3
        r3 = await _do(db, 1, "study", at(3))                                       # 3 days in a row: milestone
        assert (r3.streak, r3.milestone, r3.gif) == (3, True, "highfive") and r3.gains == {"knowledge": 3 + 1}
        assert (await _student(db, 1)).knowledge == 16 + 4
        r3b = await _do(db, 1, "study", at(3, 3))                                   # second study the same day: no second bonus
        assert (r3b.streak, r3b.milestone, r3b.gif) == (3, False, None) and r3b.gains == {"knowledge": 3}
        r5 = await _do(db, 1, "study", at(5))                                       # Day 4 skipped: the streak starts over
        assert (r5.streak, r5.milestone) == (1, False)
        for r in (r1, r2, r3, r3b, r5):
            await life.announce(1, r)
        assert got == [(1, 3, 1)]                                                   # exactly one streak event
        async with db.tx() as s:
            from services import academy_students as stu
            st = await stu.get_student(s, 1, 1)
            tot = await life.season_totals(s, st.id)
        assert tot.study == 5 and tot.best_streak == 3
        ev._subs[ev.STUDY_STREAK].remove(on_streak)
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ the other actions
def test_attend_marks_attendance_and_first_attend_waves():
    async def go():
        from services import academy_actions as A, academy_life as life
        db = await _setup()
        await _enrol(db, 1, "music", "music")
        r = await _do(db, 1, "attend", at(2))
        assert r.first_attend and r.gif == "wave" and r.gains == {"knowledge": 1}
        assert (await _daily(db, 1, 2)).attended == 1
        with pytest.raises(A.ActionBlocked):
            await _do(db, 1, "attend", at(2, 5))
        r = await _do(db, 1, "attend", at(3))
        assert not r.first_attend and r.gif is None                                 # only the FIRST attendance waves
        async with db.tx() as s:
            tot = await life.season_totals(s, (await _student(db, 1)).id)
        assert tot.attended_days == 2
        await db.dispose()
    run(go())


def test_hangout_raises_charisma_and_sometimes_creativity():
    async def go():
        db = await _setup()
        await _enrol(db, 1, "sports", "sports")                                     # sports: no hangout perk
        r = await _do(db, 1, "hangout", at(2), rng=Fixed(p=0.0))
        assert r.gains == {"charisma": 2, "creativity": 1} and r.gif == "laugh"
        r = await _do(db, 1, "hangout", at(2), rng=Fixed(p=0.99))
        assert r.gains == {"charisma": 2} and r.gif is None
        st = await _student(db, 1)
        assert (st.charisma, st.creativity) == (12 + 4, 10 + 1)
        assert (await _daily(db, 1, 2)).hangouts == 2
        await db.dispose()
    run(go())


def test_club_practice_adds_points_to_the_players_club_and_raises_its_stat():
    async def go():
        from services import academy_life as life
        db = await _setup()
        await _enrol(db, 1, "music", "music")
        await _enrol(db, 2, "arts", "music")                                        # same club, other stream
        await _enrol(db, 3, "sports", "sports")
        r1 = await _do(db, 1, "club", at(2))
        assert (r1.club_points, r1.club_total, r1.club_rank, r1.club_count) == (5, 5, 1, 4)
        assert r1.gains == {"creativity": 2 + 1}                                    # music club -> Creativity (+1 music stream perk)
        r2 = await _do(db, 2, "club", at(2))
        assert r2.club_total == 10 and r2.club_rank == 1 and r2.gains == {"creativity": 2}      # both add to ONE club score
        r3 = await _do(db, 3, "club", at(2))
        assert (r3.club_total, r3.club_rank) == (5, 2) and r3.gains == {"fitness": 2 + 1}        # sports club -> Fitness
        async with db.tx() as s:
            assert await life.club_standings(s, 1) == [("music", 10), ("sports", 5), ("drama", 0), ("coding", 0)]
        assert (await _daily(db, 1, 2)).club_count == 1
        assert (await _student(db, 3)).fitness == 10 + 5 + 3
        await db.dispose()
    run(go())


def test_stats_never_pass_the_cap_and_the_message_says_so():
    async def go():
        from services import academy_actions as A, academy_students as stu
        db = await _setup()
        await _enrol(db, 1, "music", "music")
        async with db.tx() as s:
            await stu.add_stats(s, 1, 1, {"knowledge": 100})
        r = await _do(db, 1, "study", at(2))
        assert r.gains == {"knowledge": 0} and r.stats["knowledge"] == 100
        assert "maxed" in A.render(r)
        await db.dispose()
    run(go())


def test_low_energy_after_an_action_gets_the_facepalm_gif():
    async def go():
        from services import academy_students as stu
        db = await _setup()
        await _enrol(db, 1)
        now = at(2, 2)
        async with db.tx() as s:
            await stu.spend_energy(s, 1, 1, 70, now)                                # 30 left
        r = await _do(db, 1, "study", now)                                          # -15 -> 15, at or below the low-energy mark
        assert r.energy == 15 and r.gif == "facepalm"
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ events and reset
def test_every_action_emits_an_event():
    async def go():
        from services import academy_events as ev, academy_life as life
        db = await _setup()
        await _enrol(db, 1, "music", "music")
        seen = []
        kinds = (ev.STUDIED, ev.ATTENDED, ev.HANGOUT, ev.RESTED, ev.CLUB_PRACTICE, ev.PARTTIME)

        def make(kind):
            def fn(uid, **d):
                seen.append((kind, uid, d))
            return fn
        fns = {k: ev.subscribe(k)(make(k)) for k in kinds}
        for key in ("study", "attend", "hangout", "rest", "club", "parttime"):
            res = await _do(db, 1, key, at(2))
            await life.announce(1, res)
        assert [k for k, _, _ in seen] == list(kinds)                                # exactly one event per action, in order
        for kind, uid, d in seen:
            assert uid == 1 and d["season_id"] == 1 and d["day"] == 2 and isinstance(d["energy"], int) and d["action"]
        extra = {k: d for k, _, d in seen}
        assert extra[ev.CLUB_PRACTICE]["club"] == "music" and extra[ev.CLUB_PRACTICE]["total"] == 5
        assert extra[ev.PARTTIME]["paid"] == 40 and extra[ev.ATTENDED]["first"] is True
        assert extra[ev.RESTED]["energy_restored"] > 0
        for k, fn in fns.items():
            ev._subs[k].remove(fn)
        await db.dispose()
    run(go())


def test_academy_reset_clears_daily_life_data():
    async def go():
        from sqlalchemy import func, select
        from database.models import AcademyClubPoints, AcademyDaily, Cooldown
        from services import academy_events as ev, academy_life as life, cooldowns
        db = await _setup()
        await _enrol(db, 1, "music", "music")
        await _do(db, 1, "club", at(2))
        await _do(db, 1, "study", at(2), free=False)
        async with db.tx() as s:
            await cooldowns.set_(s, 1, "work", 600)                                 # somebody else's cooldown must survive
        assert await ev.emit(ev.RESET, 1) >= 1
        async with db.tx() as s:
            assert (await s.execute(select(func.count()).select_from(AcademyDaily))).scalar_one() == 0
            assert (await s.execute(select(func.count()).select_from(AcademyClubPoints))).scalar_one() == 0
            keys = set((await s.execute(select(Cooldown.key))).scalars())
        assert keys == {"work"}
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ help catalog
def test_commands_are_in_help_but_never_in_the_slash_menu():
    from utils import commands as C, helpcatalog as H
    new = {"study", "attend", "hangout", "rest", "club", "parttime"}
    items = {c for c, _, _ in H.entries("Bot", "*")}
    assert new <= items and not new & H.STAFF
    assert not new & {c for g in (False, True) for c, _ in H.menu("Bot", "*", g)}
    assert not new & {c.command for c in C.menu_for("owner", True)}
    assert not new & (set(H.MENU) | set(H.GROUP_MENU_EXTRA))
    secs = dict(H.sections(list(H.entries("Bot", "*")), {}, {}))
    page = "\n".join(secs["Academy 🎓"])
    for c in new:
        assert f"/{c} " in page, c


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


def test_daily_life_through_the_dispatcher(monkeypatch):
    async def go():
        from database.session import db
        from services import economy
        from utils import gifs
        _owner1(monkeypatch)
        await _setup(3, season=False)                          # the real clock decides the season here
        h = Harness()

        # not enrolled: friendly message + Enroll button (owner id inside, < 64 bytes)
        out = await h.say(2, "/study")
        assert "Join the Academy" in out[0] and h.buttons() == ["ac:en:2"]
        await h.tap(2, "ac:st:2:science")
        await h.tap(2, "ac:cl:2:science:coding")

        out = await h.say(2, "/study")
        assert "Study" in out[0] and "Knowledge" in out[0] and "85/100" in out[0] and "Today: Study 1/6" in out[0]
        assert h.buttons() == ["ac:db:2"] and all(len(b.encode()) < 64 for b in h.buttons())
        out = await h.say(2, "/study")                                              # cooldown (friendly, via the error middleware)
        assert any("cooldown" in t for t in out)
        out = await h.say(2, "/attend")                                             # Day 1 is not a class day
        assert "No classes today" in out[0]
        out = await h.say(2, "/rest")
        assert "100/100" in out[0] and "+15" in out[0]
        out = await h.say(2, "/hangout")
        assert "Hang out" in out[0] and "Charisma" in out[0]
        out = await h.say(2, "/club")
        assert "Coding Club" in out[0] and "points" in out[0] and "Creativity" in out[0]
        before = (await economy.balance(2)).balance
        out = await h.say(2, "/parttime")
        assert "diamonds" in out[0] and (await economy.balance(2)).balance > before
        out = await h.say(2, "/academy")                                            # dashboard still works and shows the Energy used
        assert "Energy" in out[0]

        # Day 2 (staff moves the clock): first attendance waves with a GIF (optional: falls back to text)
        await h.say(1, "/academyadvance")
        asked = []
        orig = gifs.pick

        async def fake_pick(action):
            asked.append(action)
            return "https://example.com/wave.gif"
        gifs.pick = fake_pick
        try:
            await h.say(2, "/attend")
        finally:
            gifs.pick = orig
        assert asked == ["wave"]
        anim = [m for n, m in h.calls if n == "SendAnimation"]
        assert anim and "First attendance mark" in anim[0].caption

        # Finals (Day 6): the part-time job is closed
        for _ in range(4):
            await h.say(1, "/academyadvance")
        out = await h.say(2, "/parttime")
        assert "Finals" in out[0]
        # the break: everything closed
        for _ in range(2):
            await h.say(1, "/academyadvance")
        out = await h.say(2, "/study")
        assert "break" in out[0] and h.buttons() == []
        # kill switch
        monkeypatch.setenv("ACADEMY_ENABLED", "off")
        out = await h.say(2, "/study")
        assert "closed right now" in out[0]
        await db.dispose()
    run(go())
