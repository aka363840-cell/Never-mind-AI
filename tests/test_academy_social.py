"""Academy social, Part 4: database rules (rival pairing, duels vs a human and vs a bot, secret crushes and the privacy rule, campus
couples and dates, club battles, study buddies, reset) plus the real Dispatcher. Run: pytest tests/test_academy_social.py
(the pure half is tests/test_academy_social_logic.py)"""
import asyncio
import os
import random
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

DB = os.path.join(tempfile.gettempdir(), "academy_social_test.db")
URL = f"sqlite+aiosqlite:///{DB}"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)        # season 1 starts here (Day n = T0 + (n-1) days)
D = timedelta(days=1)


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


class FakeBot:
    id = 999

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append((chat_id, text))
        return object()

    async def send_animation(self, chat_id, url, caption=None, **kw):
        self.sent.append((chat_id, caption))
        return object()

    def to(self, chat_id):
        return [t for c, t in self.sent if c == chat_id]


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


async def _setup(n: int = 6):
    from database.session import db
    from services import academy_calendar as cal, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in range(1, n + 1):
        await users.touch(uid, f"u{uid}", f"User{uid}")
    async with db.tx() as s:
        await cal.ensure_season(s, T0)
    return db


async def _enrol(db, uid, stream="arts", club="music", stats=None, now=None):
    from services import academy_calendar as cal, academy_students as stu
    now = now or at(1, 1)
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        await stu.enroll(s, uid, season, stream, club, now)
        if stats:
            await stu.add_stats(s, uid, season.id, stats)
    return season


async def _give(db, uid, amount):
    from services import economy
    async with db.tx() as s:
        await economy.apply(s, uid, wallet=amount, kind="test", ref=f"test:{uid}:{amount}:{random.random()}")


async def _bal(uid):
    from services import economy
    return (await economy.balance(uid)).balance


async def _student(db, uid):
    from services import academy_students as stu
    async with db.tx() as s:
        return await stu.get_student(s, uid, 1)


async def _count(db, model):
    from sqlalchemy import func, select
    async with db.tx() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def _pair(db, now=None):
    from services import academy_calendar as cal, academy_social as soc
    now = now or at(3, 1)
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        return await soc.pair_rivals(s, season, now)


def _capture(kind):
    from services import academy_events as ev
    got = []

    async def fn(user_id, **data):
        got.append((user_id, data))
    ev.subscribe(kind)(fn)
    return got, lambda: ev._subs[kind].remove(fn)


SAFE_IDX = 0


def _safe_idx(card_key):
    from utils.content import academy_exam_rounds as CR
    return next(i for i, b in enumerate(CR.BY_KEY[card_key].buttons) if b.risk == "safe")


async def _play_duel(db, uid, now, rng=None, chat_id=None):
    """Play all three cards of uid's side with the safe move. Returns the last screen."""
    from services import academy_social as soc
    async with db.tx() as s:
        scr = await soc.open_duel(s, uid, now, rng or Dice(50), chat_id=chat_id)
    while scr.status == "question":
        idx = _safe_idx(scr.card_key)
        async with db.tx() as s:
            scr = await soc.duel_answer(s, uid, scr.duel_id, scr.n, idx, now, rng or Dice(50))
    return scr


# ------------------------------------------------------------------ rivals: pairing
def test_pairing_runs_once_pairs_similar_students_and_odd_gets_a_bot():
    async def go():
        from sqlalchemy import select
        from database.models import AcademyRival
        from services import academy_calendar as cal, academy_social as soc
        db = await _setup(5)
        for uid, k in ((1, 40), (2, 10), (3, 42), (4, 12), (5, 70)):
            await _enrol(db, uid, stats={"knowledge": k - 10})
        res = await _pair(db)
        assert res["pairs"] == 2 and res["bot"] is not None
        assert await _pair(db) is None                                    # idempotent: the run marker blocks a second pairing
        async with db.tx() as s:
            rows = list((await s.execute(select(AcademyRival))).scalars())
        assert len(rows) == 5 and len({r.user_id for r in rows}) == 5
        by = {r.user_id: r for r in rows}
        for r in rows:
            if r.rival_id is not None:
                assert by[r.rival_id].rival_id == r.user_id               # mirrored
            else:
                assert r.bot_name and r.user_id == res["bot"]
        assert by[1].rival_id == 3 and by[2].rival_id == 4 and res["bot"] == 5          # similar students pair up; the odd one out is the best fit
        await db.dispose()
    run(go())


def test_late_student_gets_a_bot_rival_on_demand_and_nobody_before_pairing():
    async def go():
        from services import academy_social as soc
        db = await _setup(3)
        await _enrol(db, 1)
        async with db.tx() as s:
            assert await soc.ensure_rival(s, 1, 1, at(2, 1)) is None     # pairing has not happened yet
        await _pair(db)
        await _enrol(db, 2, now=at(4, 1))
        async with db.tx() as s:
            rv = await soc.ensure_rival(s, 1, 2, at(4, 2))
        assert rv is not None and rv.rival_id is None and rv.bot_name
        await db.dispose()
    run(go())


def test_pairing_tick_only_on_its_days_and_dms_each_student_once():
    async def go():
        from services import academy_social as soc
        db = await _setup(4)
        for uid in (1, 2, 3, 4):
            await _enrol(db, uid)
        bot = FakeBot()
        await soc.rival_pairing_tick(bot, at(2, 5))
        assert bot.sent == [] and await _count(db, __import__("database.models", fromlist=["x"]).AcademyRival) == 0
        await soc.rival_pairing_tick(bot, at(3, 1))
        assert sorted(c for c, _ in bot.sent) == [1, 2, 3, 4]               # one DM each
        await soc.rival_pairing_tick(bot, at(3, 2))
        assert len(bot.sent) == 4                                           # nothing again
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ rivals: duels
def test_duel_against_a_human_winner_paid_once_badge_dm_and_one_group_message():
    async def go():
        from game import academy_rules as AR
        from services import academy_social as soc
        db = await _setup(2)
        await _enrol(db, 1, stats={"knowledge": 90, "charisma": 90, "fitness": 90, "creativity": 90})
        await _enrol(db, 2)
        await _pair(db)
        got, undo = _capture("rival_win")
        bot = FakeBot()
        try:
            s1 = await _play_duel(db, 1, at(3, 2), chat_id=-100)
            assert s1.status == "wait"
            before1, before2 = await _bal(1), await _bal(2)
            async with db.tx() as s:
                scr = await soc.open_duel(s, 2, at(3, 3), Dice(40))
            assert scr.status == "question"
            for _ in range(AR.RIVAL_DUEL_ROUNDS):
                async with db.tx() as s:
                    scr = await soc.duel_answer(s, 2, scr.duel_id, scr.n, _safe_idx(scr.card_key), at(3, 3), Dice(40))
            assert scr.status == "done"
            assert await _bal(1) == before1 + AR.RIVAL_WIN_DIAMONDS and await _bal(2) == before2
            async with db.tx() as s:
                assert await soc.badge_points(s, 1, "rival") == AR.RIVAL_BADGE_POINTS and await soc.badge_points(s, 2, "rival") == 0
            assert [u for u, _t, _g in scr.out.dm] == [1]                   # the presser (2) is not DM'd, the other side is
            assert scr.out.announce and scr.out.announce[0] == -100        # ONE group message
            await soc.deliver(bot, scr.out)
            assert len(bot.to(-100)) == 1 and len(bot.to(1)) == 1 and bot.to(2) == []
            assert [u for u, _ in got] == [1]
            async with db.tx() as s:                                        # settling again changes nothing
                from database.models import AcademyRivalDuel
                duel = await s.get(AcademyRivalDuel, scr.duel_id)
                assert await soc._resolve(s, duel, at(3, 4)) is None
            assert await _bal(1) == before1 + AR.RIVAL_WIN_DIAMONDS
        finally:
            undo()
        await db.dispose()
    run(go())


def test_duel_against_the_bot_resolves_at_once_and_pays_consistently():
    async def go():
        from game import academy_rules as AR
        from services import academy_social as soc
        db = await _setup(1)
        await _enrol(db, 1, stats={"knowledge": 60, "charisma": 60, "fitness": 60, "creativity": 60})
        assert (await _pair(db))["bot"] == 1
        got, undo = _capture("rival_win")
        try:
            before = await _bal(1)
            scr = await _play_duel(db, 1, at(3, 2), chat_id=-5)
            assert scr.status == "done" and scr.rival_name and scr.bot
            await soc.deliver(FakeBot(), scr.out)
            from sqlalchemy import select
            from database.models import AcademyRivalDuel, AcademyRivalRound
            async with db.tx() as s:
                duel = (await s.execute(select(AcademyRivalDuel))).scalar_one()
                n_rounds = (await s.execute(select(AcademyRivalRound))).scalars().all()
            assert duel.state == "done" and len(n_rounds) == 2 * AR.RIVAL_DUEL_ROUNDS
            assert {r.card_key for r in n_rounds if r.user_id == 0} == {r.card_key for r in n_rounds if r.user_id == 1}   # same cards
            gain = await _bal(1) - before
            expect = {"a": AR.RIVAL_WIN_DIAMONDS, "tie": AR.RIVAL_TIE_DIAMONDS, "b": 0}[duel.result]
            assert gain == expect
            assert (len(got) == 1) == (duel.result == "a")                  # a bot that wins is not a rival_win
            assert scr.out.announce is None                                 # no group spam for bot duels
        finally:
            undo()
        again = await _play_duel(db, 1, at(3, 5))
        assert again.status == "done" and await _bal(1) - before == expect   # reopening pays nothing more
        await db.dispose()
    run(go())


def test_duel_needs_energy_and_charges_it_once():
    async def go():
        from game import academy_rules as AR
        from services import academy_students as stu, academy_social as soc
        from services.errors import GameError
        db = await _setup(1)
        await _enrol(db, 1)
        await _pair(db)
        async with db.tx() as s:
            await stu.spend_energy(s, 1, 1, 95, at(3, 2))                    # 5 left
        async with db.tx() as s:
            scr = await soc.open_duel(s, 1, at(3, 2), Dice(50))
        with pytest.raises(GameError):
            async with db.tx() as s:
                await soc.duel_answer(s, 1, scr.duel_id, 1, 0, at(3, 2), Dice(50))
        from database.models import AcademyRivalRound
        assert await _count(db, AcademyRivalRound) == 0 and stu.current_energy(await _student(db, 1), at(3, 2)) == 5
        async with db.tx() as s:
            await stu.restore_energy(s, 1, 1, 100, at(3, 2))
        scr = await _play_duel(db, 1, at(3, 3))
        assert scr.status == "done"
        assert stu.current_energy(await _student(db, 1), at(3, 3)) == 100 - AR.RIVAL_DUEL_ENERGY
        await db.dispose()
    run(go())


def test_duel_days_and_stale_buttons():
    async def go():
        from services import academy_social as soc
        db = await _setup(1)
        await _enrol(db, 1)
        with pytest.raises(soc.SocialBlocked):                               # day 2: no rival yet
            async with db.tx() as s:
                await soc.open_duel(s, 1, at(2, 2))
        await _pair(db)
        with pytest.raises(soc.SocialBlocked) as e:
            async with db.tx() as s:
                await soc.open_duel(s, 1, at(7, 2))
        assert "closed" in e.value.text
        async with db.tx() as s:
            scr = await soc.open_duel(s, 1, at(3, 2), Dice(50))
        with pytest.raises(soc.SocialBlocked):                               # wrong round number
            async with db.tx() as s:
                await soc.duel_answer(s, 1, scr.duel_id, 2, 0, at(3, 2), Dice(50))
        with pytest.raises(soc.SocialBlocked):                               # not your duel
            async with db.tx() as s:
                await soc.duel_answer(s, 2, scr.duel_id, 1, 0, at(3, 2), Dice(50))
        await db.dispose()
    run(go())


def test_walkover_when_the_rival_never_plays():
    async def go():
        from game import academy_rules as AR
        from services import academy_social as soc
        db = await _setup(2)
        await _enrol(db, 1)
        await _enrol(db, 2)
        await _pair(db)
        await _play_duel(db, 1, at(3, 2))
        before = await _bal(1)
        bot = FakeBot()
        await soc.social_close_tick(bot, at(6, 5))                          # still before the deadline (end of Day 6)
        assert await _bal(1) == before
        await soc.social_close_tick(bot, at(7, 1))
        assert await _bal(1) == before + AR.RIVAL_WIN_DIAMONDS * AR.RIVAL_WALKOVER_PCT // 100
        async with db.tx() as s:
            assert await soc.badge_points(s, 1, "rival") == 0               # no badge for a walkover
        await soc.social_close_tick(bot, at(7, 2))
        assert await _bal(1) == before + AR.RIVAL_WIN_DIAMONDS * AR.RIVAL_WALKOVER_PCT // 100      # once
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ crushes: privacy
async def _crush(db, a, b, now, chat=None, bot=False):
    from services import academy_love as love
    async with db.tx() as s:
        return await love.send_crush(s, a, b, chat_id=chat, target_is_bot=bot, now=now)


def test_a_crush_that_is_not_mutual_is_never_revealed():
    async def go():
        from database.models import AcademyCouple, AcademyCrush
        from services import academy_love as love
        db = await _setup(4)
        for uid in (1, 2, 3):
            await _enrol(db, uid)
        got, undo = _capture("crush_mutual")
        try:
            r = await _crush(db, 1, 2, at(3, 2), chat=-7)
            assert r.status == "saved" and r.out.dm == [] and r.out.announce is None and r.out.events == []
            r2 = await _crush(db, 2, 3, at(3, 3), chat=-7)                  # 2 likes somebody else: still nothing for 1 or 2
            assert r2.status == "saved" and r2.out.dm == [] and r2.out.events == []
            async with db.tx() as s:                                        # the target's own screens know nothing about incoming crushes
                st2 = await love.couple_status(s, 2, at(3, 4), private=True)
                st3 = await love.couple_status(s, 3, at(3, 4), private=True)
            assert st2["couple"] is None and st2["pending"] == "User3"      # only their OWN crush
            assert st3["couple"] is None and st3["pending"] is None
            assert await _count(db, AcademyCouple) == 0 and await _count(db, AcademyCrush) == 2 and got == []
        finally:
            undo()
        await db.dispose()
    run(go())


def test_mutual_crush_reveals_to_both_and_makes_a_couple():
    async def go():
        from database.models import AcademyCouple, AcademyCrush
        from services import academy_love as love, academy_social as soc
        db = await _setup(3)
        for uid in (1, 2, 3):
            await _enrol(db, uid)
        got, undo = _capture("crush_mutual")
        bot = FakeBot()
        try:
            assert (await _crush(db, 1, 2, at(3, 2), chat=-7)).status == "saved"
            r = await _crush(db, 2, 1, at(3, 3), chat=None)
            assert r.status == "mutual" and r.partner_name == "User1"
            assert sorted(u for u, _t, _g in r.out.dm) == [1, 2] and all(g == "blush" for _u, _t, g in r.out.dm)
            assert r.out.announce[0] == -7                                  # the group of the first crush (this one was a DM)
            await soc.deliver(bot, r.out)
            assert len(bot.to(1)) == 1 and len(bot.to(2)) == 1 and len(bot.to(-7)) == 1
            assert [u for u, _ in got] == [2] and got[0][1]["partner_id"] == 1
            assert await _count(db, AcademyCouple) == 1 and await _count(db, AcademyCrush) == 0
            async with db.tx() as s:
                assert await love.crush_off(s, 1, at(3, 4)) is False        # nothing left to remove
        finally:
            undo()
        await db.dispose()
    run(go())


def test_crush_rules_in_the_database():
    async def go():
        from services import academy_love as love, academy_social as soc
        db = await _setup(5)
        for uid in (1, 2, 3, 4):
            await _enrol(db, uid)                                           # 5 is NOT enrolled
        for args, kw, why in (((1, 1, at(3, 2)), {}, "self"), ((1, 5, at(3, 2)), {}, "target not enrolled"),
                              ((5, 1, at(3, 2)), {}, "sender not enrolled"), ((1, 2, at(2, 2)), {}, "too early"),
                              ((1, 2, at(3, 2)), {"bot": True}, "bot")):
            with pytest.raises(soc.SocialBlocked):
                await _crush(db, *args, **kw)
        with pytest.raises(soc.SocialBlocked):
            await _crush(db, 1, 2, at(8, 1))                                # the break
        await _crush(db, 1, 2, at(3, 2))
        with pytest.raises(soc.SocialBlocked) as e:                         # one active crush each
            await _crush(db, 1, 3, at(3, 3))
        assert "already have an active crush" in e.value.text
        async with db.tx() as s:
            assert await love.crush_off(s, 1, at(3, 4)) is True
        assert (await _crush(db, 1, 3, at(3, 5))).status == "saved"         # free again
        await _crush(db, 3, 4, at(3, 5))                                    # 3 and 4 ... 4 crushes back -> couple
        assert (await _crush(db, 4, 3, at(3, 6))).status == "mutual"
        with pytest.raises(soc.SocialBlocked):                              # a couple member cannot crush / be crushed
            await _crush(db, 3, 2, at(3, 7))
        with pytest.raises(soc.SocialBlocked):
            await _crush(db, 2, 3, at(3, 7))
        await db.dispose()
    run(go())


def test_removing_a_crush_stops_a_later_mutual():
    async def go():
        from services import academy_love as love
        db = await _setup(2)
        await _enrol(db, 1)
        await _enrol(db, 2)
        await _crush(db, 1, 2, at(3, 2))
        async with db.tx() as s:
            await love.crush_off(s, 1, at(3, 3))
        assert (await _crush(db, 2, 1, at(3, 4))).status == "saved"        # 1 took it back, so 2 is NOT told anything
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ campus date
async def _couple(db, a=1, b=2, now=None):
    await _enrol(db, a)
    await _enrol(db, b)
    now = now or at(3, 2)
    await _crush(db, a, b, now)
    return await _crush(db, b, a, now)


def test_campus_date_costs_energy_and_diamonds_pays_off_and_is_once_a_day():
    async def go():
        from game import academy_rules as AR
        from services import academy_love as love, academy_students as stu, academy_social as soc
        db = await _setup(2)
        await _couple(db)
        await _give(db, 1, 100)
        got, undo = _capture("date")
        try:
            b0 = await _bal(1)
            s1b, s2b = await _student(db, 1), await _student(db, 2)
            k1 = sum(getattr(s1b, k) for k in AR.STAT_KEYS)
            async with db.tx() as s:
                res = await love.campus_date(s, 1, at(3, 5), random.Random(4))
            await soc.deliver(FakeBot(), res.out)                            # events fire after the commit, through deliver
            assert await _bal(1) == b0 - AR.DATE_DIAMONDS and res.total_points == AR.DATE_POINTS
            s1a, s2a = await _student(db, 1), await _student(db, 2)
            assert stu.current_energy(s1a, at(3, 5)) == 100 - AR.DATE_ENERGY
            assert stu.current_energy(s2a, at(3, 5)) == 100                  # the partner pays nothing
            assert sum(getattr(s1a, k) for k in AR.STAT_KEYS) > k1 and s2a.charisma > s2b.charisma
            assert len(got) == 1 and got[0][1]["points"] == AR.DATE_POINTS
            with pytest.raises(soc.SocialBlocked) as e:                      # once a day, and nothing more is charged
                async with db.tx() as s:
                    await love.campus_date(s, 2, at(3, 6), random.Random(4))
            assert "already had today's date" in e.value.text
            assert await _bal(1) == b0 - AR.DATE_DIAMONDS
            async with db.tx() as s:                                         # tomorrow it works again (partner can start it too)
                await _give_inline(s, 2)
            async with db.tx() as s:
                res = await love.campus_date(s, 2, at(4, 2), random.Random(5))
            assert res.total_points == 2 * AR.DATE_POINTS
        finally:
            undo()
        await db.dispose()
    run(go())


async def _give_inline(s, uid):
    from services import economy
    await economy.apply(s, uid, wallet=100, kind="test", ref=f"inline:{uid}:{random.random()}")


def test_campus_date_refusals_change_nothing():
    async def go():
        from game import academy_rules as AR
        from services import academy_love as love, academy_students as stu, academy_social as soc
        from services.errors import GameError
        from database.models import AcademyCoupleDate
        db = await _setup(3)
        await _couple(db)
        await _enrol(db, 3)
        with pytest.raises(soc.SocialBlocked) as e:                          # no couple
            async with db.tx() as s:
                await love.campus_date(s, 3, at(3, 5))
        assert "Campus Couple" in e.value.text
        await _give(db, 1, 5 - await _bal(1))                                # leave 5 💎: a date costs more
        with pytest.raises(soc.SocialBlocked) as e:                          # too poor: Energy is NOT kept spent, the day is NOT used up
            async with db.tx() as s:
                await love.campus_date(s, 1, at(3, 5))
        assert "costs" in e.value.text
        assert stu.current_energy(await _student(db, 1), at(3, 5)) == 100 and await _count(db, AcademyCoupleDate) == 0
        await _give(db, 1, 100)
        async with db.tx() as s:
            await stu.spend_energy(s, 1, 1, 90, at(3, 5))                    # 10 left, a date needs 20
        b = await _bal(1)
        with pytest.raises(GameError):
            async with db.tx() as s:
                await love.campus_date(s, 1, at(3, 5))
        assert await _bal(1) == b and await _count(db, AcademyCoupleDate) == 0
        async with db.tx() as s:
            await love.campus_date(s, 1, at(3, 5) + timedelta(hours=2))      # Energy regenerated -> allowed
        assert await _bal(1) == b - AR.DATE_DIAMONDS
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ club battle
async def _open_battle(db, now=None):
    from services import academy_social as soc
    bot = FakeBot()
    await soc.club_battle_open_tick(bot, now or at(3, 1))


def test_battle_opens_on_day_3_once_and_each_member_plays_one_round():
    async def go():
        from game import academy_rules as AR
        from services import academy_social as soc, academy_students as stu
        from database.models import AcademyBattle
        db = await _setup(2)
        await _enrol(db, 1, club="coding")
        await _enrol(db, 2, club="music")
        await _open_battle(db, at(2, 5))
        assert await _count(db, AcademyBattle) == 0                          # not before Day 3
        await _open_battle(db, at(3, 1))
        await _open_battle(db, at(3, 2))
        assert await _count(db, AcademyBattle) == 1                          # idempotent
        async with db.tx() as s:
            b, st = await soc.battle_card_for(s, 1, at(3, 3))
        res = None
        async with db.tx() as s:
            res = await soc.play_battle(s, 1, b.id, 0, at(3, 3), Dice(50))
        assert stu.current_energy(await _student(db, 1), at(3, 3)) == 100 - AR.BATTLE_ENERGY and res.club == "coding"
        with pytest.raises(soc.SocialBlocked) as e:                          # one round only
            async with db.tx() as s:
                await soc.play_battle(s, 1, b.id, 0, at(3, 4), Dice(50))
        assert "already played" in e.value.text
        assert stu.current_energy(await _student(db, 1), at(3, 4)) >= 100 - AR.BATTLE_ENERGY
        with pytest.raises(soc.SocialBlocked):                               # closed after Day 5
            async with db.tx() as s:
                await soc.play_battle(s, 2, b.id, 0, at(6, 1), Dice(50))
        await db.dispose()
    run(go())


def test_battle_needs_energy_and_a_failed_try_does_not_use_up_the_round():
    async def go():
        from game import academy_rules as AR
        from services import academy_social as soc, academy_students as stu
        from services.errors import GameError
        from database.models import AcademyBattlePlay
        db = await _setup(1)
        await _enrol(db, 1, club="sports")
        await _open_battle(db)
        async with db.tx() as s:
            await stu.spend_energy(s, 1, 1, 95, at(3, 2))
        async with db.tx() as s:
            b, _ = await soc.battle_card_for(s, 1, at(3, 2))
        with pytest.raises(GameError):
            async with db.tx() as s:
                await soc.play_battle(s, 1, b.id, 0, at(3, 2), Dice(50))
        assert await _count(db, AcademyBattlePlay) == 0
        async with db.tx() as s:
            await soc.play_battle(s, 1, b.id, 0, at(3, 2) + timedelta(hours=3), Dice(50))
        assert await _count(db, AcademyBattlePlay) == 1
        await db.dispose()
    run(go())


def test_club_score_is_practice_plus_battle_and_winner_is_paid_once():
    async def go():
        from game import academy_rules as AR
        from services import academy_life as life, academy_social as soc
        from database.models import AcademyBattlePlay
        db = await _setup(5)
        await _enrol(db, 1, club="music")
        await _enrol(db, 2, club="music")
        await _enrol(db, 3, club="music")                                   # never plays, never practises
        await _enrol(db, 4, club="coding")
        await _open_battle(db)
        async with db.tx() as s:
            await life.add_club_points(s, 1, "music", 200)
            await life.add_club_points(s, 1, "coding", 5)
        plays = {}
        for uid in (1, 2, 4):
            async with db.tx() as s:
                b, _ = await soc.battle_card_for(s, uid, at(3, 3))
            async with db.tx() as s:
                plays[uid] = await soc.play_battle(s, uid, b.id, 0, at(3, 3), Dice(50))
        async with db.tx() as s:
            rows = {r.club: r for r in await soc.standings(s, 1)}
        assert rows["music"].practice == 200 and rows["music"].battle == plays[1].points + plays[2].points
        assert rows["music"].total == 200 + plays[1].points + plays[2].points and rows["music"].players == 2
        assert rows["coding"].total == 5 + plays[4].points
        got, undo = _capture("club_win")
        bot = FakeBot()
        try:
            b1 = [await _bal(u) for u in (1, 2, 3, 4)]
            await soc.social_close_tick(bot, at(5, 20))                      # still open (closes at the END of Day 5)
            assert [await _bal(u) for u in (1, 2, 3, 4)] == b1
            await soc.social_close_tick(bot, at(6, 1))
            after = [await _bal(u) for u in (1, 2, 3, 4)]
            assert after[0] - b1[0] == AR.CLUB_WIN_DIAMONDS and after[1] - b1[1] == AR.CLUB_WIN_DIAMONDS
            assert after[2] == b1[2] and after[3] == b1[3]                   # a free rider and the losing club get nothing
            assert sorted(u for u, _ in got) == [1, 2] and sorted(c for c, _ in bot.sent) == [1, 2]
            async with db.tx() as s:
                assert await soc.badge_points(s, 1, "club") == AR.CLUB_WIN_BADGE_POINTS and await soc.badge_points(s, 3, "club") == 0
                assert (await soc.get_battle(s, 1)).winner == "music"
            await soc.social_close_tick(bot, at(6, 3))                       # idempotent: no second payment, no second event
            assert [await _bal(u) for u in (1, 2, 3, 4)] == after and len(got) == 2
        finally:
            undo()
        await db.dispose()
    run(go())


def test_a_season_nobody_played_has_no_club_winner():
    async def go():
        from services import academy_social as soc
        db = await _setup(1)
        await _enrol(db, 1)
        await _open_battle(db)
        await soc.social_close_tick(FakeBot(), at(6, 1))
        async with db.tx() as s:
            b = await soc.get_battle(s, 1)
        assert b.state == "closed" and b.winner is None
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ study buddies
def test_buddy_request_accept_and_daily_study_bonus():
    async def go():
        from game import academy_rules as AR
        from services import academy_events as ev, academy_love as love, academy_social as soc
        db = await _setup(4)
        for uid in (1, 2, 3):
            await _enrol(db, uid)
        async with db.tx() as s:
            br = await love.request_buddy(s, 1, 2, now=at(2, 2))
        assert br.status == "pending"
        with pytest.raises(soc.SocialBlocked):                               # one open request each
            async with db.tx() as s:
                await love.request_buddy(s, 1, 3, now=at(2, 2))
        with pytest.raises(soc.SocialBlocked):                               # only the target may answer
            async with db.tx() as s:
                await love.answer_buddy(s, 3, br.request_id, True)
        await ev.emit("studied", 1, season_id=1, day=2)                      # not buddies yet: no bonus
        k0 = (await _student(db, 1)).knowledge
        async with db.tx() as s:
            assert (await love.answer_buddy(s, 2, br.request_id, True))[0] == "active"
        with pytest.raises(soc.SocialBlocked):                               # stale
            async with db.tx() as s:
                await love.answer_buddy(s, 2, br.request_id, True)
        await ev.emit("studied", 1, season_id=1, day=2)
        await ev.emit("studied", 1, season_id=1, day=2)                      # the same day again: nothing
        assert (await _student(db, 1)).knowledge == k0 + AR.BUDDY_KNOWLEDGE_BONUS
        await ev.emit("studied", 1, season_id=1, day=3)
        await ev.emit("studied", 2, season_id=1, day=3)                      # the buddy gets it too
        assert (await _student(db, 1)).knowledge == k0 + 2 * AR.BUDDY_KNOWLEDGE_BONUS
        k3 = (await _student(db, 3)).knowledge
        await ev.emit("studied", 3, season_id=1, day=3)                      # no buddy: no bonus
        assert (await _student(db, 3)).knowledge == k3
        with pytest.raises(soc.SocialBlocked):                               # already has a buddy
            async with db.tx() as s:
                await love.request_buddy(s, 3, 1, now=at(3, 2))
        async with db.tx() as s:
            d = await love.buddy_status(s, 1, at(3, 5))
        assert d["kind"] == "active" and d["claimed_today"] is True
        async with db.tx() as s:
            assert await love.end_buddy(s, 1, at(3, 6)) == "active"
            assert await love.end_buddy(s, 1, at(3, 6)) is None
        await db.dispose()
    run(go())


def test_buddy_decline_and_mutual_request():
    async def go():
        from services import academy_love as love
        db = await _setup(3)
        for uid in (1, 2, 3):
            await _enrol(db, uid)
        async with db.tx() as s:
            br = await love.request_buddy(s, 1, 2, now=at(2, 2))
        async with db.tx() as s:
            assert (await love.answer_buddy(s, 2, br.request_id, False))[0] == "declined"
        async with db.tx() as s:
            await love.request_buddy(s, 1, 3, now=at(2, 3))                 # free to ask somebody else
        async with db.tx() as s:
            res = await love.request_buddy(s, 3, 1, now=at(2, 4))           # they already asked me: that is an accept
        assert res.status == "active"
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ reset, ticks, dispatcher
def test_reset_removes_every_part_4_row():
    async def go():
        from sqlalchemy import func, select
        from database.models import (AcademyBadge, AcademyBattle, AcademyBuddy, AcademyCouple, AcademyCrush, AcademyRival,
                                     AcademyRivalDuel, AcademySocialRun)
        from services import academy_events as ev, academy_love as love, academy_social as soc
        db = await _setup(3)
        for uid in (1, 2, 3):
            await _enrol(db, uid)
        await _pair(db)
        await _open_battle(db)
        await _play_duel(db, 1, at(3, 2))
        await _crush(db, 1, 2, at(3, 2))
        await _crush(db, 2, 1, at(3, 2))
        async with db.tx() as s:
            await love.request_buddy(s, 3, 2, now=at(3, 2))
        assert await _count(db, AcademyCouple) and await _count(db, AcademyRival)
        await ev.emit("reset", 1)
        for m in (AcademyBadge, AcademyBattle, AcademyBuddy, AcademyCouple, AcademyCrush, AcademyRival, AcademyRivalDuel, AcademySocialRun):
            assert await _count(db, m) == 0
        await db.dispose()
    run(go())


def test_ticks_are_registered_in_the_one_academy_loop():
    from services import academy_ticks as T
    T.load_tick_modules()
    names = T.registered()
    for n in ("academy.rival_pairing", "academy.club_battle_open", "academy.social_close", "academy.rollover", "academy.exam_close"):
        assert n in names


def test_callback_data_fits_telegram_and_carries_the_owner():
    big = 10 ** 12
    for cb in (f"acr:c:{big}:{big}:3:2", f"acr:d:{big}", f"acr:r:{big}", f"acb:p:{big}", f"acb:c:{big}:{big}:2", f"acb:r:{big}",
               f"acp:d:{big}", f"acu:y:{big}:{big}", f"acu:n:{big}:{big}"):
        assert len(cb.encode()) < 64 and str(big) in cb


def test_dispatcher_has_the_new_commands_before_the_ai_chat_router():
    async def go():
        db = await _setup(1)
        from bot import build_dispatcher
        from handlers import academy_routers
        dp = build_dispatcher(None)
        import handlers.academy_social as h
        assert h.router in dp.sub_routers and dp.sub_routers.index(h.router) < dp.sub_routers.index(__import__("handlers.ai_chat", fromlist=["router"]).router)
        assert "handlers.academy_social" in academy_routers.MODULES
        await db.dispose()
    run(go())
    _detach_routers()
