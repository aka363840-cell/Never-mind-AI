"""Academy Part 6, DATABASE tests: send-once across restarts, quiet hours, opt-out, unreachable users, inactive groups, the global
rate limit through the real Sender, recap, ceremony + Hall of Fame + report card, reset, registration.
Run: pytest tests/test_academy_notify.py   (the pure half is tests/test_academy_notify_logic.py)"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

DB = os.path.join(tempfile.gettempdir(), "academy_notify_test.db")
URL = f"sqlite+aiosqlite:///{DB}"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)        # season 1: Day 1 starts 14:30 IST
H = timedelta(hours=1)
D = timedelta(days=1)


def at(day: int, hours: float = 2) -> datetime:
    return T0 + (day - 1) * D + timedelta(hours=hours)      # hours=2 -> 11:00 UTC = 16:30 IST (not quiet)


def run(c):
    return asyncio.run(c)


class FakeBot:
    id = 999

    def __init__(self, forbidden=()):
        self.sent = []
        self.forbidden = set(forbidden)

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        if chat_id in self.forbidden:
            from aiogram.exceptions import TelegramForbiddenError
            from aiogram.methods import SendMessage
            raise TelegramForbiddenError(method=SendMessage(chat_id=chat_id, text="x"), message="Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text, reply_markup))
        return object()

    async def send_animation(self, chat_id, url, caption=None, **kw):
        self.sent.append((chat_id, caption, None))
        return object()

    def to(self, chat_id):
        return [t for c, t, _ in self.sent if c == chat_id]


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
    monkeypatch.delenv("ACADEMY_DAILY_HOUR", raising=False)
    monkeypatch.setenv("ACADEMY_ENABLED", "1")
    monkeypatch.setenv("ACADEMY_TZ", "Asia/Kolkata")
    from utils import gifs
    monkeypatch.setattr(gifs, "ENABLED", False)
    yield
    _detach_routers()


async def _setup(users: int = 4, groups: tuple = (-100, -200)):
    from database.session import db
    from services import academy_calendar as cal, academy_notify as N, users as U
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in range(1, users + 1):
        await U.touch(uid, f"u{uid}", f"User{uid}")
    for g in groups:
        await U.touch_group(g, f"G{g}")
        await N.note_group_seen(g, T0)
    async with db.tx() as s:
        await cal.ensure_season(s, T0)
    return db


async def _restart(db):
    """A bot restart: new engine, all in-memory state gone."""
    from services import academy_notify as N, academy_reminders as R
    await db.dispose()
    db.init(URL)
    N._seen_mono.clear()
    N.LIMITER._stamps.clear()
    R._cursor.clear()


async def _enrol(uid, now=None, stream="arts", club="music", stats=None):
    from database.session import db
    from services import academy_calendar as cal, academy_students as stu
    now = now or at(1, 1)
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        await stu.enroll(s, uid, season, stream, club, now)
        if stats:
            await stu.add_stats(s, uid, season.id, stats)
    return season


async def _count(model, **where):
    from sqlalchemy import func, select
    from database.session import db
    async with db.tx() as s:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return (await s.execute(q)).scalar_one()


# ------------------------------------------------------------------ the send-once log
def test_claim_is_once_and_survives_a_restart():
    async def go():
        db = await _setup()
        from services import academy_notify as N
        assert await N.claim("k", "a", 5, 1, T0) is True
        assert await N.claim("k", "a", 5, 1, T0) is False
        assert await N.claim("k", "a", 6, 1, T0) is True              # another target
        await _restart(db)
        assert await N.claim("k", "a", 5, 1, T0) is False
        assert await N.was_sent("k", "a", 5)
        await N.release("k", "a", 5)
        assert await N.claim("k", "a", 5, 1, T0) is True
        assert await N.sent_targets("k", "a", [5, 6, 7]) == {5, 6}
        await db.dispose()
    run(go())


def test_claim_all_is_all_or_nothing():
    async def go():
        db = await _setup()
        from services import academy_notify as N
        assert await N.claim("slot", "d:1", 9, 1, T0)
        assert not await N.claim_all([("rem_x", "k1", 9, 1), ("slot", "d:1", 9, 1)], T0)
        assert not await N.was_sent("rem_x", "k1", 9)                  # the first insert was rolled back
        assert await N.claim_all([("rem_x", "k1", 9, 1), ("slot", "d:2", 9, 1)], T0)
        await db.dispose()
    run(go())


def test_purge_old_rows():
    async def go():
        db = await _setup()
        from services import academy_notify as N
        await N.claim("dm_daily", "d:old", 1, 1, T0 - 10 * D)
        await N.claim("dm_daily", "d:new", 1, 1, T0)
        assert await N.purge_old(T0) == 1
        assert await N.was_sent("dm_daily", "d:new", 1)
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ season open
def test_season_open_goes_to_active_groups_once_even_after_a_restart():
    async def go():
        db = await _setup()
        from services import academy_reminders as R
        bot = FakeBot()
        await R.season_open_tick(bot, at(1, 2))
        assert len(bot.to(-100)) == 1 and len(bot.to(-200)) == 1
        assert "Season 1" in bot.to(-100)[0]
        kb = [m for c, t, m in bot.sent if c == -100][0]
        assert kb.inline_keyboard[0][0].callback_data == "acn:e:1" and len(kb.inline_keyboard[0][0].callback_data.encode()) < 64
        await R.season_open_tick(bot, at(1, 2.5))
        await _restart(db)
        await R.season_open_tick(bot, at(1, 3))
        assert len(bot.sent) == 2
        await db.dispose()
    run(go())


def test_season_open_skips_quiet_hours_other_days_disabled_and_inactive_groups():
    async def go():
        db = await _setup(groups=(-100, -200, -300))
        from sqlalchemy import update
        from database.models import GroupSetting, AcademyGroupSeen
        from services import academy_notify as N, academy_reminders as R
        async with db.tx() as s:
            await s.execute(update(GroupSetting).where(GroupSetting.chat_id == -200).values(games_enabled=False))
            await s.execute(update(AcademyGroupSeen).where(AcademyGroupSeen.chat_id == -300).values(last_seen=T0 - 8 * D))
        bot = FakeBot()
        await R.season_open_tick(bot, at(1, 12))          # 21:30 UTC... local 02:30? 09:00+12h = 21:00 UTC = 02:30 IST (quiet)
        assert bot.sent == []
        await R.season_open_tick(bot, at(2, 2))           # Day 2: not the opening day
        assert bot.sent == []
        await R.season_open_tick(bot, at(1, 2))
        assert [c for c, _, _ in bot.sent] == [-100]
        assert await N.active_groups(at(1, 2)) == [-100]
        await db.dispose()
    run(go())


def test_group_that_refuses_messages_is_skipped_after_three_failures_until_it_is_seen_again():
    async def go():
        db = await _setup(groups=(-100,))
        from services import academy_notify as N
        bot = FakeBot(forbidden={-100})
        sender = N.Sender(bot)
        for _ in range(AR_FAILS()):
            assert await N.deliver_group(sender, -100, "hi") == "failed"
        assert await N.active_groups(at(1, 2)) == []
        await N.note_group_seen(-100, at(1, 3))
        assert await N.active_groups(at(1, 3)) == [-100]
        await db.dispose()
    run(go())


def AR_FAILS():
    from game import academy_rules as AR
    return AR.GROUP_FAILS_SKIP


# ------------------------------------------------------------------ enrolment reminders
def test_enroll_group_reminder_once_per_window_and_dm_once_a_day_only_to_the_not_enrolled():
    async def go():
        db = await _setup(users=3)
        await _enrol(1)
        from services import academy_reminders as R
        bot = FakeBot()
        t = at(1, 7)                                        # window 1 (>= 6 h after start), 16:30... = 21:30 IST? 09:00+7h=16:00 UTC=21:30 IST
        await R.enroll_reminder_tick(bot, t)
        assert len(bot.to(-100)) == 1 and len(bot.to(-200)) == 1
        assert bot.to(1) == [] and len(bot.to(2)) == 1 and len(bot.to(3)) == 1      # user 1 is enrolled
        await R.enroll_reminder_tick(bot, t + timedelta(minutes=10))               # same window, same day: nothing new
        assert len(bot.sent) == 4
        await _restart(db)
        await R.enroll_reminder_tick(bot, t + timedelta(minutes=20))
        assert len(bot.sent) == 4
        await R.enroll_reminder_tick(bot, at(1, 13.5))                               # next 6 h window but quiet hours (03:00 IST)
        assert len(bot.sent) == 4
        await R.enroll_reminder_tick(bot, at(2, 2))                                  # next day, window 4: groups again + DMs again
        assert len(bot.to(-100)) == 2 and len(bot.to(2)) == 2
        await db.dispose()
    run(go())


def test_last_call_for_groups_and_no_enroll_reminders_after_admissions_close():
    async def go():
        db = await _setup(users=1)
        from services import academy_reminders as R
        bot = FakeBot()
        await R.enroll_reminder_tick(bot, at(4, 22.5))      # 09:00 + 3d22.5h = 07:30 UTC... inside the last 2 h before close (T0+4d)
        last = [t for c, t, _ in bot.sent if c == -100]
        await R.enroll_reminder_tick(bot, at(5, 1))
        assert len(last) == 1 and "Last call" in last[0]
        assert len([1 for c, _, _ in bot.sent if c == -100]) == 1                    # nothing after close
        await db.dispose()
    run(go())


def test_opted_out_blocked_and_banned_users_get_no_dm():
    async def go():
        db = await _setup(users=5, groups=())
        from sqlalchemy import update
        from database.models import User
        from services import academy_notify as N, academy_reminders as R
        await N.set_remind(2, False, T0)
        async with db.tx() as s:
            await s.execute(update(User).where(User.id == 3).values(blocked_bot=True))
            await s.execute(update(User).where(User.id == 4).values(is_banned=True))
        bot = FakeBot()
        await R.enroll_reminder_tick(bot, at(1, 7))
        assert sorted(c for c, _, _ in bot.sent) == [1, 5]
        assert await N.reminders_on(2) is False and await N.reminders_on(1) is True
        await db.dispose()
    run(go())


def test_a_user_who_blocked_the_bot_is_marked_unreachable_and_not_tried_again():
    async def go():
        db = await _setup(users=2, groups=())
        from services import academy_notify as N, academy_reminders as R
        bot = FakeBot(forbidden={2})
        await R.enroll_reminder_tick(bot, at(1, 7))
        assert await N.is_unreachable(2) and not await N.is_unreachable(1)
        assert [c for c, _, _ in bot.sent] == [1]
        await R.enroll_reminder_tick(bot, at(2, 2))
        assert bot.to(2) == [] and len(bot.to(1)) == 2
        await N.set_remind(2, True, at(2, 3), private=True)                           # /academyremind in a private chat clears it
        assert not await N.is_unreachable(2)
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ the limiter through the real Sender
def test_sender_obeys_the_global_rate_limit_and_chunks_with_sleeps(monkeypatch):
    async def go():
        db = await _setup(users=1, groups=())
        from game import academy_rules as AR
        from services import academy_notify as N, academy_remind_logic as RL

        class Clk:
            t = 50.0
            slept = []

            def __call__(self):
                return self.t

            async def sleep(self, s):
                self.slept.append(s)
                self.t += s
        clk = Clk()
        lim = RL.Limiter(clock=clk, sleep=clk.sleep)
        stamps = []
        real_acquire = lim.acquire

        async def spy():                                   # the moment the limiter lets a send through (not after the chunk sleep)
            await real_acquire()
            stamps.append(clk.t)
        lim.acquire = spy
        sender = N.Sender(FakeBot(), limiter=lim, clock=clk, sleep=clk.sleep, budget_s=1000, max_msgs=1000)
        for i in range(60):
            assert await sender.send(100 + i, "x") == "ok"
        for t in stamps:
            assert sum(1 for x in stamps if t <= x < t + 1.0) <= AR.NOTIFY_RATE_PER_SEC
        assert AR.NOTIFY_CHUNK_SLEEP in clk.slept                                    # a chunk sleep happened
        await db.dispose()
    run(go())


def test_sender_budget_and_cap():
    async def go():
        db = await _setup(users=1, groups=())
        from services import academy_notify as N, academy_remind_logic as RL

        class Clk:
            t = 0.0

            def __call__(self):
                return self.t

            async def sleep(self, s):
                self.t += s
        clk = Clk()
        s1 = N.Sender(FakeBot(), limiter=RL.Limiter(clock=clk, sleep=clk.sleep), clock=clk, sleep=clk.sleep, max_msgs=3)
        for i in range(3):
            await s1.send(i + 1, "x")
        assert s1.exhausted
        s2 = N.Sender(FakeBot(), limiter=RL.Limiter(clock=clk, sleep=clk.sleep), clock=clk, sleep=clk.sleep, budget_s=5)
        assert not s2.exhausted
        clk.t += 6
        assert s2.exhausted
        await db.dispose()
    run(go())


def test_a_network_error_is_not_counted_as_sent_and_the_claim_can_be_released():
    async def go():
        db = await _setup(users=1, groups=())
        from services import academy_notify as N

        class Boom(FakeBot):
            async def send_message(self, *a, **k):
                raise RuntimeError("network down")
        sender = N.Sender(Boom())
        assert await N.claim("k", "x", 1, 1, T0)
        assert await N.deliver_dm(sender, 1, "hi") == "error"
        await N.release("k", "x", 1)
        assert await N.claim("k", "x", 1, 1, T0)
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ enrolled reminders
def test_midterm_reminder_one_dm_a_day_respects_quiet_hours_and_restarts():
    async def go():
        db = await _setup(users=2, groups=())
        await _enrol(1)
        await _enrol(2)
        from services import academy_notify as N, academy_reminders as R
        bot = FakeBot()
        lo = at(4, 0)                                        # Day 4 opens 14:30 IST
        await R.enrolled_reminder_tick(bot, lo - timedelta(minutes=30))
        assert len(bot.to(1)) == 1 and "Midterm" in bot.to(1)[0] and len(bot.to(2)) == 1
        R._cursor.clear()
        await R.enrolled_reminder_tick(bot, lo - timedelta(minutes=20))              # same day, slot used
        await R.enrolled_reminder_tick(bot, lo + H)
        assert len(bot.sent) == 2
        await _restart(db)
        await R.enrolled_reminder_tick(bot, lo + 2 * H)
        assert len(bot.sent) == 2
        await N.set_remind(1, False, lo)                                             # opt out: no reminder next day either
        R._cursor.clear()
        await R.enrolled_reminder_tick(bot, at(5, 2))
        assert len(bot.to(1)) == 1 and len(bot.to(2)) >= 1                            # user 1 opted out: nothing new
        await db.dispose()
    run(go())


def test_no_reminder_in_quiet_hours_then_it_waits_for_morning():
    async def go():
        db = await _setup(users=1, groups=())
        await _enrol(1)
        from services import academy_reminders as R
        R._cursor.clear()                                    # module-level round-robin cursor: do not inherit it from other tests
        bot = FakeBot()
        night = at(4, 12)                                    # 21:00 UTC = 02:30 IST on Day 4/5: quiet
        await R.enrolled_reminder_tick(bot, night)
        assert bot.sent == []
        await R.enrolled_reminder_tick(bot, night + 5 * H)   # 07:30 IST
        assert len(bot.sent) == 1
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ recap
def test_recap_group_and_dm_once_each_and_opt_out_gets_none():
    async def go():
        db = await _setup(users=3)
        await _enrol(1)
        await _enrol(2)
        await _enrol(3)
        from services import academy_notify as N, academy_reminders as R, academy_remind_logic as RL
        await N.set_remind(3, False, T0)
        bot = FakeBot()
        t = RL.recap_time(T0, 2) + timedelta(minutes=1)
        await R.recap_tick(bot, t)
        assert len(bot.to(-100)) == 1 and "end of Day 2" in bot.to(-100)[0] and "Tomorrow" in bot.to(-100)[0]
        assert len(bot.to(1)) == 1 and len(bot.to(2)) == 1 and bot.to(3) == []
        assert "Energy used" in bot.to(1)[0]
        await R.recap_tick(bot, t + timedelta(minutes=5))
        await _restart(db)
        await R.recap_tick(bot, t + timedelta(minutes=10))
        assert len(bot.sent) == 4
        before = RL.recap_time(T0, 3) - timedelta(minutes=1)                         # not yet due: nothing
        await R.recap_tick(bot, before)
        assert len(bot.sent) == 4
        await db.dispose()
    run(go())


def test_no_recap_on_day_7():
    async def go():
        db = await _setup(users=1)
        from services import academy_reminders as R
        bot = FakeBot()
        await R.recap_tick(bot, at(7, 14))
        assert bot.sent == []
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ graduation
def _ranked():
    from services import academy_exam_logic as EL
    return [EL.Ranked(1, 1, 1, "User1", 92.0, 9.2, "A+", 90.0, 94.0), EL.Ranked(2, 2, 2, "User2", 75.0, 7.5, "B+", 70.0, 80.0),
            EL.Ranked(3, 3, 3, "User3", 30.0, 3.0, "F", 30.0, 30.0)]


def _patch_results(monkeypatch):
    from services import academy_exams as ex

    async def fake(session, season_id, now=None, user_ids=None):
        return _ranked()

    async def noop(bot, now):
        return None
    monkeypatch.setattr(ex, "season_results", fake)
    monkeypatch.setattr(ex, "exam_close_tick", noop)


def test_ceremony_once_hall_of_fame_cards_titles_and_restart(monkeypatch):
    async def go():
        _patch_results(monkeypatch)
        db = await _setup(users=4)
        for u in (1, 2, 3):
            await _enrol(u)
        from database.models import AcademyHallOfFame
        from services import academy_events as ev, academy_graduation as grad, academy_notify as N
        got = []

        async def on_grad(uid, **d):
            got.append((uid, d["rank"]))
        ev.subscribe(ev.GRADUATED)(on_grad)
        bot = FakeBot()
        t = at(7, 2)                                         # 16:30 IST
        await grad.ceremony_tick(bot, t)
        g = bot.to(-100)
        assert len(g) == 1 and "Graduation Ceremony" in g[0] and "User1" in g[0] and "Valedictorian" in g[0]
        assert len(bot.to(1)) == 1 and "Report card" in bot.to(1)[0] and "A+" in bot.to(1)[0]
        assert len(bot.to(2)) == 1 and len(bot.to(3)) == 1 and bot.to(4) == []           # user 4 never enrolled
        assert await _count(AcademyHallOfFame, title="valedictorian") == 1
        assert sorted(got) == [(1, 1), (2, 2), (3, 3)]
        n = len(bot.sent)
        await grad.ceremony_tick(bot, t + timedelta(minutes=2))
        await _restart(db)
        await grad.ceremony_tick(bot, t + timedelta(minutes=4))
        assert len(bot.sent) == n and len(got) == 3
        assert await N.was_sent("ceremony_done", "s1", 0)
        hof = await grad.hall_of_fame()
        assert "Season 1" in hof and "User1" in hof
        await db.dispose()
    run(go())


def test_ceremony_respects_opt_out_quiet_hours_and_waits_for_morning(monkeypatch):
    async def go():
        _patch_results(monkeypatch)
        db = await _setup(users=3, groups=())
        for u in (1, 2, 3):
            await _enrol(u)
        from services import academy_graduation as grad, academy_notify as N
        await N.set_remind(2, False, T0)
        bot = FakeBot()
        night = at(7, 12)                                    # 02:30 IST on Day 7 -> Day 8: quiet
        await grad.ceremony_tick(bot, night)
        assert bot.sent == []
        await grad.ceremony_tick(bot, night + 5 * H)         # 07:30 IST: report cards go out
        assert sorted(c for c, _, _ in bot.sent) == [1, 3]   # user 2 opted out
        await grad.ceremony_tick(bot, night + 5 * H + timedelta(minutes=2))          # nothing left to send: the season is marked done
        assert len(bot.sent) == 2 and await N.was_sent("ceremony_done", "s1", 0)
        await db.dispose()
    run(go())


def test_ceremony_not_before_day_7_and_a_plugin_section_shows_up(monkeypatch):
    async def go():
        _patch_results(monkeypatch)
        db = await _setup(users=1)
        await _enrol(1)
        from services import academy_ceremony as C, academy_graduation as grad

        @C.ceremony_section(name="t_plugin", order=500, scope="both")
        def plug(ctx):
            return "🌟 PLUGIN-SECTION"
        try:
            bot = FakeBot()
            await grad.ceremony_tick(bot, at(6, 2))
            assert bot.sent == []
            await grad.ceremony_tick(bot, at(7, 0.05))        # inside the 10 minute delay
            assert bot.sent == []
            await grad.ceremony_tick(bot, at(7, 2))
            assert all("PLUGIN-SECTION" in t for _, t, _ in bot.sent) and len(bot.sent) == 3
        finally:
            C.unregister("t_plugin")
        await db.dispose()
    run(go())


def test_report_card_command_data_current_and_last_season(monkeypatch):
    async def go():
        _patch_results(monkeypatch)
        db = await _setup(users=2, groups=())
        await _enrol(1)
        from services import academy_graduation as grad
        mid = await grad.report_card(1, at(3, 2))
        assert mid is not None and "in progress" in mid and "Report card" in mid
        assert await grad.report_card(2, at(3, 2)) is None                           # never enrolled
        final = await grad.report_card(1, at(7, 3))
        assert "Final GPA" in final and "9.20" in final
        assert "Report card" in (await grad.report_card(1, T0 + 7 * D + H))           # break: still shows the finished season
        await db.dispose()
    run(go())


def test_hall_of_fame_empty_message():
    async def go():
        db = await _setup(users=1, groups=())
        from services import academy_graduation as grad
        assert "empty" in (await grad.hall_of_fame()).lower()
        await db.dispose()
    run(go())


def test_save_hall_is_idempotent(monkeypatch):
    async def go():
        _patch_results(monkeypatch)
        db = await _setup(users=3, groups=())
        for u in (1, 2, 3):
            await _enrol(u)
        from database.models import AcademyHallOfFame
        from services import academy_calendar as cal, academy_graduation as grad
        async with db.tx() as s:
            season = await cal.ensure_season(s, at(7, 2))
            data = await grad.gather(s, season, at(7, 2))
            a = await grad.save_hall(s, data)
            b = await grad.save_hall(s, data)
        assert a > 0 and b == 0
        assert await _count(AcademyHallOfFame, user_id=1, title="valedictorian") == 1
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ reset, registration, middleware
def test_reset_clears_notify_log_and_hall_but_keeps_prefs():
    async def go():
        db = await _setup(users=1, groups=())
        from database.models import AcademyHallOfFame, AcademyNotifyLog, AcademyUserPrefs
        from database.session import insert_ignore
        from services import academy_events as ev, academy_graduation, academy_notify as N  # noqa: F401  (registers the reset hook)
        await N.claim("k", "a", 1, 1, T0)
        await N.set_remind(1, False, T0)
        async with db.tx() as s:
            await insert_ignore(s, AcademyHallOfFame, dict(season_id=1, season_number=1, user_id=1, name="x", title="topper"))
        await ev.emit(ev.RESET, 0)
        assert await _count(AcademyNotifyLog) == 0 and await _count(AcademyHallOfFame) == 0
        assert await _count(AcademyUserPrefs) == 1
        await db.dispose()
    run(go())


def test_ticks_router_and_middleware_are_registered():
    async def go():
        db = await _setup(users=1, groups=())
        from services import academy_ticks as AT
        AT.load_tick_modules()
        names = set(AT._ticks)
        for n in ("academy.remind_season_open", "academy.remind_enroll", "academy.remind_enrolled", "academy.recap", "academy.ceremony", "academy.notify_purge"):
            assert n in names, n
        from bot import build_dispatcher
        from handlers import academy_routers
        from services.academy_seen import GroupSeenMiddleware
        dp = build_dispatcher(None)
        import handlers.academy_remind as h
        assert "handlers.academy_remind" in academy_routers.MODULES
        assert dp.sub_routers.index(h.router) < dp.sub_routers.index(__import__("handlers.ai_chat", fromlist=["router"]).router)
        mws = getattr(dp.update.outer_middleware, "_middlewares", None) or list(dp.update.outer_middleware)
        assert any(isinstance(m, GroupSeenMiddleware) for m in mws)
        await db.dispose()
    run(go())
    _detach_routers()


def test_group_seen_middleware_writes_at_most_once_per_window():
    async def go():
        db = await _setup(users=1, groups=())
        from types import SimpleNamespace as NS
        from database.models import AcademyGroupSeen
        from services import academy_notify as N
        from services.academy_seen import GroupSeenMiddleware, group_chat_id
        mw = GroupSeenMiddleware()
        calls = []

        async def handler(ev, data):
            calls.append(1)
            return "ok"
        upd = NS(message=NS(chat=NS(type="supergroup", id=-555)), edited_message=None, my_chat_member=None, chat_member=None, callback_query=None)
        assert group_chat_id(upd) == -555
        assert group_chat_id(NS(message=NS(chat=NS(type="private", id=5)), edited_message=None, my_chat_member=None, chat_member=None, callback_query=None)) is None
        for _ in range(5):
            assert await mw(handler, upd, {}) == "ok"
        assert len(calls) == 5 and await _count(AcademyGroupSeen, chat_id=-555) == 1
        await db.dispose()
    run(go())


def test_callback_data_fits_and_commands_exist_in_help():
    from services import academy_reminders as R
    cb = R.enroll_kb(10**9).inline_keyboard[0][0].callback_data
    assert len(cb.encode()) < 64
    from utils import helpcatalog as HC
    assert {"academyremind", "reportcard", "halloffame"} <= set(HC.DESC)


def test_schedule_agrees_with_the_calendar_service():
    from game import academy_rules as AR
    from services import academy_calendar as cal, academy_remind_logic as RL
    t = T0 - H
    while t < T0 + 8 * D:
        ph = cal.phase_at(T0, t)
        assert RL.season_day(T0, t) == (ph.day if 1 <= ph.day <= AR.SEASON_DAYS else 0), t
        t += timedelta(hours=5, minutes=7)


def test_graduation_diamonds_off_by_default_and_paid_once_when_on(monkeypatch):
    async def go():
        _patch_results(monkeypatch)
        db = await _setup(users=3, groups=())
        for u in (1, 2, 3):
            await _enrol(u)
        from services import academy_graduation as grad, economy
        bot = FakeBot()
        monkeypatch.delenv("ACADEMY_GRADUATION_DIAMONDS", raising=False)
        base = (await economy.balance(1)).balance
        await grad.ceremony_tick(bot, at(7, 2))
        assert (await economy.balance(1)).balance == base                            # switch is off: nothing paid
        monkeypatch.setenv("ACADEMY_GRADUATION_DIAMONDS", "1")
        await grad.ceremony_tick(bot, at(7, 2.1))                                    # hall already done, nothing pending: no payment path
        await db.dispose()
    run(go())


def test_prizes_pay_each_title_exactly_once(monkeypatch):
    async def go():
        _patch_results(monkeypatch)
        monkeypatch.setenv("ACADEMY_GRADUATION_DIAMONDS", "1")
        db = await _setup(users=3, groups=())
        for u in (1, 2, 3):
            await _enrol(u)
        from game import academy_rules as AR
        from services import academy_calendar as cal, academy_graduation as grad, economy
        base = (await economy.balance(1)).balance
        async with db.tx() as s:
            season = await cal.ensure_season(s, at(7, 2))
            data = await grad.gather(s, season, at(7, 2))
        paid1 = await grad.pay_prizes(season, data)
        paid2 = await grad.pay_prizes(season, data)                                  # a retry
        want = AR.GRADUATION_DIAMONDS["valedictorian"] + AR.GRADUATION_DIAMONDS["topper"]
        assert paid1.get(1) == want == paid2.get(1)                                  # reported both times ...
        assert (await economy.balance(1)).balance == base + want                     # ... but paid only once
        await db.dispose()
    run(go())
