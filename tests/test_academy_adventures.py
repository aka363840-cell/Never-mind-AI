"""Academy adventures, Part 5: database rules (start, Energy, gates, stat checks, expiry, daily limit, idempotent rewards, Fest Day,
events, reset, tick) and end to end through the real Dispatcher. The pure half is tests/test_academy_adventure_logic.py and
tests/test_academy_adventure_data.py. Run: pytest tests/test_academy_adventures.py"""
import asyncio
import os
import random
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest

DB = os.path.join(tempfile.gettempdir(), "academy_adventures_test.db")
URL = f"sqlite+aiosqlite:///{DB}"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)        # season 1 starts here (Day n = T0 + (n-1) days); 09:00 UTC = 14:30 IST
H = timedelta(hours=1)
D = timedelta(days=1)
MIN = timedelta(minutes=1)


def at(day: int, hours: float = 2) -> datetime:
    """Day `day`, `hours` after the season start. hours=2 -> 16:30 IST (daylight); hours=7 -> 21:30 IST (night)."""
    return T0 + (day - 1) * D + timedelta(hours=hours)


def run(c):
    return asyncio.run(c)


class Dice(random.Random):
    """A Random whose dice (randint) always roll `v` (1 = every check succeeds, 100 = every check fails)."""
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
    monkeypatch.delenv("ACADEMY_TZ", raising=False)              # the default Asia/Kolkata: "night" is local 20:00-06:00
    monkeypatch.setenv("ACADEMY_ENABLED", "1")
    from utils import gifs
    monkeypatch.setattr(gifs, "ENABLED", False)
    yield
    _detach_routers()


async def _setup(n: int = 3, season: bool = True):
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
        await stu.enroll(s, uid, season, stream, club, now)
        if stats:
            await stu.add_stats(s, uid, season.id, stats)
    return season


async def _balance(uid):
    from services import economy
    return (await economy.balance(uid)).balance


async def _energy(db, uid, now):
    from services import academy_calendar as cal, academy_students as stu
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        return stu.current_energy(await stu.get_student(s, uid, season.id), now)


async def _stats(db, uid, now=None):
    from services import academy_calendar as cal, academy_students as stu
    async with db.tx() as s:
        season = await cal.ensure_season(s, now or at(1, 1))
        return stu.stats_of(await stu.get_student(s, uid, season.id))


async def _runs(db):
    from sqlalchemy import select
    from database.models import AcademyAdventureRun
    async with db.tx() as s:
        return list((await s.execute(select(AcademyAdventureRun).order_by(AcademyAdventureRun.id))).scalars())


async def _start(db, uid, key, now, rng=None):
    from services import academy_adventures as adv
    async with db.tx() as s:
        return await adv.start(s, uid, key, now, rng or Dice(1))


async def _press(db, uid, scr, idx, now, rng=None):
    from services import academy_adventures as adv
    async with db.tx() as s:
        return await adv.choose(s, uid, scr.run_id, scr.step, idx, now, rng or Dice(1))


async def _play(db, uid, key, now, picks, rng=None):
    """Start `key` and press the given button indexes (index in node.choices). Returns the last screen."""
    scr = await _start(db, uid, key, now, rng)
    for idx in picks:
        scr = await _press(db, uid, scr, idx, now, rng)
    return scr


SOLVE = [0, 0, 0, 0]          # missing_paper with Dice(1): staffroom, smudge (glitter), sparkle trail, ask the Art Club -> end_solved
WRONG = [0, 0, 3, 0]          # missing_paper with Dice(100): staffroom, smudge fails, gather clues, accuse Mr. Das -> end_wrong


# ------------------------------------------------------------------ start
def test_start_costs_energy_and_creates_one_run():
    async def go():
        from game import academy_rules as AR
        db = await _setup()
        await _enrol(db, 2)
        scr = await _start(db, 2, "missing_paper", at(2))
        assert scr.kind == "node" and scr.node == "office" and scr.step == 0 and scr.run_id
        assert [c.idx for c in scr.choices] == [0, 1]
        assert await _energy(db, 2, at(2)) == AR.ENERGY_START - 15
        runs = await _runs(db)
        assert len(runs) == 1 and runs[0].adventure_key == "missing_paper" and runs[0].finished is False and runs[0].node == "office"
        assert '"fest":false' in runs[0].state_json and runs[0].ending is None
        await db.dispose()
    run(go())


def test_start_twice_resumes_for_free():
    async def go():
        from game import academy_rules as AR
        db = await _setup()
        await _enrol(db, 2)
        a = await _start(db, 2, "missing_paper", at(2))
        b = await _start(db, 2, "missing_paper", at(2, 2.1))                 # a double tap on the Start button
        assert a.run_id == b.run_id and len(await _runs(db)) == 1
        assert await _energy(db, 2, at(2, 2.1)) >= AR.ENERGY_START - 15 and await _energy(db, 2, at(2, 2.1)) <= AR.ENERGY_START
        await db.dispose()
    run(go())


def test_gates_enrolment_day_closed_and_wrong_day():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        for uid, key, now, text in (
                (3, "missing_paper", at(2), "enrolled students"),            # never enrolled
                (2, "missing_paper", at(1, 3), "Day 2"),                     # Day 1 is for enrolling
                (2, "haunted_library", at(2, 2), "night only"),              # 16:30 IST
                (2, "rooftop_concert", at(3, 2), "Day 5"),                   # Fest Day only
                (2, "canteen_heist", at(2, 2), "Day 3"),                     # Days 3-7
                (2, "nope", at(2, 2), "does not exist")):
            with pytest.raises(adv.AdventureBlocked) as e:
                await _start(db, uid, key, now)
            assert text in e.value.text, (key, e.value.text)
        assert await _runs(db) == []
        # closed enrolment: a student who never joined cannot use adventures after Day 4
        with pytest.raises(adv.AdventureBlocked) as e:
            await _start(db, 3, "missing_paper", at(5))
        assert "closed" in e.value.text and not e.value.enroll
        await db.dispose()
    run(go())


def test_break_blocks_everything():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        with pytest.raises(adv.AdventureBlocked) as e:
            await _start(db, 2, "missing_paper", at(7, 24.5))                # just after Day 7 = the break
        assert "break" in e.value.text
        await db.dispose()
    run(go())


def test_the_haunted_library_opens_at_night():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        scr = await _start(db, 2, "haunted_library", at(2, 7))                 # 21:30 IST
        assert scr.kind == "node" and scr.node == "gates"
        await db.dispose()
    run(go())


def test_no_energy_means_no_run_and_nothing_spent():
    async def go():
        from services import academy_adventures as adv, academy_calendar as cal, academy_students as stu
        from services.errors import GameError
        db = await _setup()
        season = await _enrol(db, 2)
        async with db.tx() as s:
            await stu.spend_energy(s, 2, season.id, 95, at(2))                 # 5 left
        with pytest.raises(GameError) as e:
            await _start(db, 2, "missing_paper", at(2))
        assert "Energy" in str(e.value)
        assert await _runs(db) == [] and await _energy(db, 2, at(2)) == 5
        await db.dispose()
    run(go())


def test_one_live_run_at_a_time():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        await _start(db, 2, "missing_paper", at(3))
        with pytest.raises(adv.AdventureBlocked) as e:
            await _start(db, 2, "canteen_heist", at(3))
        assert "Missing Question Paper" in e.value.text
        assert len(await _runs(db)) == 1
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ playing
def test_full_run_pays_once_and_gives_stats_and_a_title():
    async def go():
        from sqlalchemy import select
        from database.models import AcademyAdventureTrophy
        db = await _setup()
        await _enrol(db, 2)
        bal, before = await _balance(2), await _stats(db, 2)
        scr = await _play(db, 2, "missing_paper", at(2), SOLVE)
        assert scr.kind == "ending" and scr.node == "end_solved" and scr.mood == "great"
        assert scr.paid == 30 and scr.gains == {"knowledge": 2, "charisma": 1} and scr.trophy_new and scr.done_today == 1
        assert await _balance(2) == bal + 30
        after = await _stats(db, 2)
        assert after["knowledge"] == before["knowledge"] + 2 and after["charisma"] == before["charisma"] + 1
        run_ = (await _runs(db))[0]
        assert run_.finished is True and run_.ending == "end_solved" and run_.step == 4
        async with db.tx() as s:
            rows = (await s.execute(select(AcademyAdventureTrophy))).scalars().all()
        assert [(r.user_id, r.key, r.kind) for r in rows] == [(2, "master_detective", "title")]
        await db.dispose()
    run(go())


def test_failed_checks_end_badly_but_still_pay_the_consolation():
    async def go():
        db = await _setup()
        await _enrol(db, 2)
        bal = await _balance(2)
        scr = await _play(db, 2, "missing_paper", at(2), WRONG, Dice(100))
        assert scr.kind == "ending" and scr.node == "end_wrong" and scr.mood == "bad" and scr.paid == 4
        assert "🎲" in scr.line or scr.line == ""                              # the last press was a plain move
        assert await _balance(2) == bal + 4
        await db.dispose()
    run(go())


def test_each_check_line_reports_the_roll():
    async def go():
        db = await _setup()
        await _enrol(db, 2)
        scr = await _start(db, 2, "missing_paper", at(2))
        scr = await _press(db, 2, scr, 0, at(2))                                # plain move: no line
        assert scr.line == "" and scr.node == "staffroom"
        scr = await _press(db, 2, scr, 0, at(2), Dice(1))                       # the smudge check
        assert "Knowledge check" in scr.line and "rolled 1" in scr.line and "success" in scr.line
        assert scr.node == "corridor" and any(c.idx == 0 for c in scr.choices)  # the sparkle trail button appeared (clue flag)
        await db.dispose()
    run(go())


def test_a_clue_button_stays_hidden_without_the_clue():
    async def go():
        db = await _setup()
        await _enrol(db, 2)
        scr = await _start(db, 2, "missing_paper", at(2))
        scr = await _press(db, 2, scr, 0, at(2))
        scr = await _press(db, 2, scr, 0, at(2), Dice(100))                     # smudge check fails: no glitter
        assert scr.node == "corridor" and [c.idx for c in scr.choices] == [2, 3]
        from services import academy_adventures as adv
        async with db.tx() as s:
            nxt = await adv.choose(s, 2, scr.run_id, scr.step, 0, at(2), Dice(1))   # pressing the hidden button
        assert nxt.kind == "stale"
        await db.dispose()
    run(go())


def test_fest_day_pays_a_bigger_bonus_on_every_adventure():
    async def go():
        db = await _setup()
        await _enrol(db, 2)
        await _enrol(db, 3)
        bal2, bal3 = await _balance(2), await _balance(3)
        s2, s3 = await _stats(db, 2), await _stats(db, 3)
        normal = await _play(db, 2, "missing_paper", at(2), SOLVE)             # Day 2
        fest = await _play(db, 3, "missing_paper", at(5), SOLVE)               # Day 5 = Fest Day
        assert normal.paid == 30 and fest.paid == 45 and fest.rewards.fest and not normal.rewards.fest
        assert fest.gains == {"knowledge": 3, "charisma": 2} and normal.gains == {"knowledge": 2, "charisma": 1}
        assert await _balance(2) == bal2 + 30 and await _balance(3) == bal3 + 45
        assert '"fest":true' in (await _runs(db))[1].state_json
        await db.dispose()
    run(go())


def test_the_fest_bonus_follows_the_start_day_even_after_midnight():
    async def go():
        db = await _setup()
        await _enrol(db, 2)
        scr = await _start(db, 2, "rooftop_concert", at(5, 23.5))
        assert scr.node == "roof" and scr.fest
        # finishing after the day change (Day 6) keeps the Fest bonus decided at the start; every gap stays under 30 minutes
        scr = await _press(db, 2, scr, 2, at(5, 23.7))                         # tiny: one guitar -> unplugged
        scr = await _press(db, 2, scr, 0, at(5, 23.95))                        # quiet original -> hit
        scr = await _press(db, 2, scr, 0, at(5, 24.1))                         # key-change finale -> headliner (now Day 6)
        assert scr.kind == "ending" and scr.node == "end_headliner" and scr.paid == 45
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ idempotency and safety
def test_a_replayed_last_press_never_pays_twice():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        scr = await _play(db, 2, "missing_paper", at(2), SOLVE[:-1])
        last_step = scr.step
        bal = await _balance(2)
        async with db.tx() as s:
            first = await adv.choose(s, 2, scr.run_id, last_step, 0, at(2), Dice(1))
        async with db.tx() as s:
            again = await adv.choose(s, 2, scr.run_id, last_step, 0, at(2), Dice(1))      # a retry of the very same button
        assert first.kind == "ending" and first.paid == 30
        assert again.kind in ("stale", "expired") and again.paid == 0
        assert await _balance(2) == bal + 30
        await db.dispose()
    run(go())


def test_the_ledger_ref_blocks_a_second_payment_for_the_same_run():
    async def go():
        from services import academy_adventure_logic as L, academy_students as stu, academy_calendar as cal
        db = await _setup()
        await _enrol(db, 2)
        scr = await _play(db, 2, "missing_paper", at(2), SOLVE)
        bal = await _balance(2)
        async with db.tx() as s:
            season = await cal.ensure_season(s, at(2))
            st = await stu.get_student(s, 2, season.id)
            ref = L.reward_ref(2, int(st.enrolled_at.replace(tzinfo=timezone.utc).timestamp() * 1_000_000), scr.run_id)
            paid_again = await stu.pay_diamonds(s, 2, 30, ref, kind="academy_adv")
        assert paid_again is False and await _balance(2) == bal
        await db.dispose()
    run(go())


def test_a_double_press_moves_the_story_only_once():
    async def go():
        db = await _setup()
        await _enrol(db, 2)
        scr = await _start(db, 2, "missing_paper", at(2))
        a = await _press(db, 2, scr, 0, at(2))
        b = await _press(db, 2, scr, 1, at(2))                                  # the OLD buttons pressed again: other branch
        assert a.kind == "node" and a.node == "staffroom" and b.kind == "stale"
        run_ = (await _runs(db))[0]
        assert run_.node == "staffroom" and run_.step == 1
        await db.dispose()
    run(go())


def test_only_the_owner_can_touch_a_run():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        await _enrol(db, 3)
        scr = await _start(db, 2, "missing_paper", at(2))
        async with db.tx() as s:
            other = await adv.choose(s, 3, scr.run_id, scr.step, 0, at(2), Dice(1))       # user 3 presses user 2's run id
        assert other.kind == "stale"
        async with db.tx() as s:
            q = await adv.quit_run(s, 3, scr.run_id, at(2))
        assert q.kind == "stale"
        run_ = (await _runs(db))[0]
        assert run_.step == 0 and run_.finished is False
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ expiry
def test_a_run_expires_after_thirty_quiet_minutes_without_reward_or_refund():
    async def go():
        from game import academy_rules as AR
        db = await _setup()
        await _enrol(db, 2)
        bal = await _balance(2)
        scr = await _start(db, 2, "missing_paper", at(2))
        scr = await _press(db, 2, scr, 0, at(2) + 30 * MIN)                      # exactly 30 min: still alive
        assert scr.kind == "node"
        late = await _press(db, 2, scr, 0, at(2) + 30 * MIN + 30 * MIN + timedelta(seconds=1))
        assert late.kind == "expired" and "timed out" in late.message
        run_ = (await _runs(db))[0]
        assert run_.finished is True and run_.ending == "expired"
        assert await _balance(2) == bal and await _energy(db, 2, at(2) + H) < AR.ENERGY_START      # Energy stays spent
        again = await _press(db, 2, scr, 0, at(2) + 2 * H)                       # pressing again: still "expired"
        assert again.kind == "expired"
        await db.dispose()
    run(go())


def test_after_expiry_a_new_adventure_can_start_and_the_daily_count_is_untouched():
    async def go():
        db = await _setup()
        await _enrol(db, 2)
        await _start(db, 2, "missing_paper", at(2))
        scr = await _start(db, 2, "missing_paper", at(2) + 45 * MIN)             # the old run timed out: closed, a new one starts
        runs = await _runs(db)
        assert len(runs) == 2 and runs[0].ending == "expired" and runs[1].finished is False and scr.run_id == runs[1].id
        await db.dispose()
    run(go())


def test_the_expiry_tick_closes_only_stale_runs_and_is_idempotent():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        await _enrol(db, 3)
        await _start(db, 2, "missing_paper", at(2))
        await _start(db, 3, "missing_paper", at(2) + 25 * MIN)
        async with db.tx() as s:
            n1 = await adv.expire_stale(s, at(2) + 40 * MIN)
        async with db.tx() as s:
            n2 = await adv.expire_stale(s, at(2) + 40 * MIN)
        assert (n1, n2) == (1, 0)
        runs = await _runs(db)
        assert [r.ending for r in runs] == ["expired", None] and runs[1].finished is False
        await adv.adventure_expire_tick(None, at(2) + 90 * MIN)
        assert [r.ending for r in await _runs(db)] == ["expired", "expired"]
        await db.dispose()
    run(go())


def test_quit_ends_the_run_without_reward_and_is_idempotent():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        bal = await _balance(2)
        scr = await _start(db, 2, "missing_paper", at(2))
        async with db.tx() as s:
            q = await adv.quit_run(s, 2, scr.run_id, at(2, 2.1))
        async with db.tx() as s:
            q2 = await adv.quit_run(s, 2, scr.run_id, at(2, 2.2))
        assert q.kind == "quit" and q2.kind == "stale" and await _balance(2) == bal
        assert (await _runs(db))[0].ending == "quit"
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ daily limit
def test_at_most_two_finished_adventures_a_day():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        await _play(db, 2, "missing_paper", at(2, 2), SOLVE)
        await _play(db, 2, "missing_paper", at(2, 3), SOLVE)
        with pytest.raises(adv.AdventureBlocked) as e:
            await _start(db, 2, "missing_paper", at(2, 4))
        assert "2/2" in e.value.text
        async with db.tx() as s:
            v = await adv.list_view(s, 2, at(2, 4))
        assert v.done_today == 2 and v.limit == 2
        scr = await _start(db, 2, "missing_paper", at(3, 2))                    # the next season day: the count starts again
        assert scr.kind == "node"
        await db.dispose()
    run(go())


def test_expired_and_quit_runs_do_not_count_toward_the_limit():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        a = await _start(db, 2, "missing_paper", at(2, 2))
        async with db.tx() as s:
            await adv.quit_run(s, 2, a.run_id, at(2, 2.1))
        await _start(db, 2, "missing_paper", at(2, 3))                           # left to expire
        await _play(db, 2, "missing_paper", at(2, 5), SOLVE)                     # the old one timed out here
        await _play(db, 2, "missing_paper", at(2, 6), SOLVE)
        async with db.tx() as s:
            v = await adv.list_view(s, 2, at(2, 6.5))
        assert v.done_today == 2
        await db.dispose()
    run(go())


def test_every_adventure_spends_its_own_energy_cost():
    async def go():
        db = await _setup()
        await _enrol(db, 2)
        await _play(db, 2, "rooftop_concert", at(5, 2), [2, 0, 0], Dice(1))        # 25 Energy, a full run
        await _play(db, 2, "sports_showdown", at(5, 2), [0, 0, 0, 0], Dice(1))     # 25 Energy
        assert await _energy(db, 2, at(5, 2)) == 50
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ trophies, events, list, reset
def test_a_trophy_is_kept_once_per_season():
    async def go():
        from sqlalchemy import select
        from database.models import AcademyAdventureTrophy
        db = await _setup()
        await _enrol(db, 2)
        first = await _play(db, 2, "missing_paper", at(2, 2), SOLVE)
        second = await _play(db, 2, "missing_paper", at(2, 3), SOLVE)
        assert first.trophy_new is True and second.trophy_new is False and second.paid == 30     # still paid, trophy kept once
        async with db.tx() as s:
            assert len((await s.execute(select(AcademyAdventureTrophy))).scalars().all()) == 1
        await db.dispose()
    run(go())


def test_adventure_done_is_emitted_after_an_ending_only():
    async def go():
        from services import academy_adventures as adv, academy_events as ev
        db = await _setup()
        await _enrol(db, 2)
        seen = []

        async def hook(user_id, **data):
            seen.append((user_id, data))
        ev.subscribe("adventure_done")(hook)
        try:
            mid = await _play(db, 2, "missing_paper", at(2), [0, 0])
            await adv.announce(2, mid)                                          # a node screen: nothing is emitted
            await adv.announce(2, await _finish_rest(db, 2, mid))              # the ending: exactly one event
        finally:
            ev._subs["adventure_done"].remove(hook)
        assert len(seen) == 1
        uid, d = seen[0]
        assert uid == 2 and d["adventure"] == "missing_paper" and d["ending"] == "end_solved" and d["mood"] == "great"
        assert d["diamonds"] == 30 and d["base_diamonds"] == 30 and d["fest"] is False and d["day"] == 2
        assert d["gains"] == {"knowledge": 2, "charisma": 1} and d["trophy"] == "master_detective" and d["trophy_new"] is True
        assert d["wins"] == 2 and d["losses"] == 0 and d["run_id"] and d["season_id"] and d["student_id"]
        await db.dispose()
    run(go())


async def _finish_rest(db, uid, scr):
    """From the screen after [staffroom, smudge], finish the solve line: sparkle trail, ask the Art Club."""
    scr = await _press(db, uid, scr, 0, at(2))
    return await _press(db, uid, scr, 0, at(2))


def test_the_adventure_list_follows_the_day_and_the_hour():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        async with db.tx() as s:
            d3 = await adv.list_view(s, 2, at(3, 2))
            d5 = await adv.list_view(s, 2, at(5, 7))
            d1 = await adv.list_view(s, 2, at(1, 3))
        open3 = {e.key for e in d3.entries if e.open}
        open5 = {e.key for e in d5.entries if e.open}
        assert open3 == {"canteen_heist", "missing_paper"} and d3.fest is False
        assert open5 == {"haunted_library", "canteen_heist", "missing_paper", "rooftop_concert", "sports_showdown"} and d5.fest is True
        assert not any(e.open for e in d1.entries) and all(e.reason for e in d1.entries)
        await db.dispose()
    run(go())


def test_list_shows_the_live_run_and_trophies():
    async def go():
        from services import academy_adventures as adv
        db = await _setup()
        await _enrol(db, 2)
        await _play(db, 2, "missing_paper", at(2, 2), SOLVE)
        scr = await _start(db, 2, "canteen_heist", at(3, 2))
        async with db.tx() as s:
            v = await adv.list_view(s, 2, at(3, 2.2))
            cur = await adv.current(s, 2, at(3, 2.2))
        assert v.live and v.live.key == "canteen_heist" and v.live.run_id == scr.run_id and 0 < v.live.seconds_left <= 1800
        assert v.trophies == [("title", "Master Detective")]
        assert cur.kind == "node" and cur.run_id == scr.run_id and cur.step == 0
        await db.dispose()
    run(go())


def test_reset_forgets_runs_and_trophies():
    async def go():
        from sqlalchemy import select
        from database.models import AcademyAdventureRun, AcademyAdventureTrophy
        from services import academy_adventures as adv, academy_events as ev
        db = await _setup()
        await _enrol(db, 2)
        await _play(db, 2, "missing_paper", at(2), SOLVE)
        assert adv._on_reset in ev.subscribers("reset")
        await adv._on_reset(1)
        async with db.tx() as s:
            assert (await s.execute(select(AcademyAdventureRun))).scalars().all() == []
            assert (await s.execute(select(AcademyAdventureTrophy))).scalars().all() == []
        await db.dispose()
    run(go())


def test_the_tick_is_registered_and_the_module_is_listed():
    from services import academy_adventures, academy_ticks
    assert "academy.adventure_expire" in academy_ticks.registered()
    assert "services.academy_adventures" in academy_ticks.TICK_MODULES
    from handlers import academy_routers
    assert "handlers.academy_adventures" in academy_routers.MODULES


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

    def alerts(self):
        return [m.text for n, m in self.calls if n == "AnswerCallbackQuery" and m.text]


def test_adventure_through_the_dispatcher(monkeypatch):
    async def go():
        _owner1(monkeypatch)
        await _setup(3, season=False)
        h = Harness()
        out = await h.say(2, "/adventure")                                         # not enrolled yet
        assert "Enroll" in out[0] or "enroll" in out[0]
        assert "ac:en:2" in h.buttons()
        await h.tap(2, "ac:st:2:science")
        await h.tap(2, "ac:cl:2:science:coding")
        out = await h.say(2, "/adventure")                                         # Day 1
        assert "Day 1" in out[0] and "Nothing is open" in out[0]
        await h.say(1, "/academyadvance")                                          # -> Day 2
        out = await h.say(2, "/adventure")
        assert "Day 2" in out[0] and "Missing Question Paper" in out[0]
        btns = h.buttons()
        start = [b for b in btns if b.startswith("acv:s:2:")]
        assert "acv:s:2:missing_paper" in start and "acv:s:2:canteen_heist" not in start      # (the Library depends on the real hour)
        assert all(len(b.encode()) < 64 for b in btns)
        assert await h.tap(3, "acv:s:2:missing_paper") == []                        # somebody else's button: nothing happens
        out = await h.tap(2, "acv:s:2:missing_paper")
        assert "Missing Question Paper" in out[0] and "Principal Mehta" in out[0]
        choices = [b for b in h.buttons() if b.startswith("acv:c:2:")]
        assert len(choices) == 2 and all(len(b.encode()) < 64 for b in choices)
        assert await h.tap(3, choices[0]) == []                                     # foreign press: nothing edited
        out = await h.tap(2, choices[0])
        assert "staff room" in out[0]
        screen_buttons = h.buttons()                                                # (tap() clears the call log: keep this screen's buttons)
        stale = await h.tap(2, choices[0])                                          # the old button again: a popup only
        assert stale == [] and h.alerts()
        h.calls[:] = []
        nxt = [b for b in screen_buttons if b.startswith("acv:c:2:")]
        for _ in range(40):                                                         # play on: always the first visible button
            if not nxt:
                break
            out = await h.tap(2, nxt[0])
            nxt = [b for b in h.buttons() if b.startswith("acv:c:2:")]
        assert "Rewards" in out[0] and "Adventures finished today" in out[0]
        assert any(b == "acv:l:2" for b in h.buttons())
        out = await h.tap(2, "acv:l:2")
        assert "Finished today" in out[0] and "1/2" in out[0]
        monkeypatch.setenv("ACADEMY_ENABLED", "0")                                  # the kill switch
        out = await h.say(2, "/adventure")
        assert "closed" in out[0]
        from database.session import db
        await db.dispose()
    run(go())


def test_adventure_command_is_unique_and_in_help_but_not_the_slash_menu():
    import glob
    import re
    hits = []
    for f in glob.glob("handlers/*.py"):
        src = open(f, encoding="utf-8").read()
        hits += [f for m in re.finditer(r'Command\(([^)]*)\)', src) if re.search(r'"adventure"', m.group(1))]
    assert [h.replace("\\", "/") for h in hits] == ["handlers/academy_adventures.py"]
    from utils import helpcatalog as H
    assert "adventure" in H.DESC and "adventure" not in H.MENU and "adventure" not in H.GROUP_MENU_EXTRA
    assert any("adventure" in cmds for cat, cmds in H.CATEGORIES if cat == "Academy 🎓")
