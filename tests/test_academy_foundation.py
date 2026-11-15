"""Academy Life, Part 1 (calendar, enrolment, Energy, dashboard). Run: pytest tests/test_academy_foundation.py"""
import asyncio
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest

DB = os.path.join(tempfile.gettempdir(), "academy_foundation_test.db")
URL = f"sqlite+aiosqlite:///{DB}"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)       # a season start used by the pure tests
H = timedelta(hours=1)
D = timedelta(days=1)


def _owner1(monkeypatch):
    from config import get_settings
    monkeypatch.setenv("OWNER_ID", "1")
    get_settings.cache_clear()


def run(c):
    return asyncio.run(c)


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
    yield from _env_gen(monkeypatch)


def _env_gen(monkeypatch):
    monkeypatch.delenv("ACADEMY_BREAK_HOURS", raising=False)
    monkeypatch.setenv("ACADEMY_ENABLED", "1")
    from utils import gifs
    monkeypatch.setattr(gifs, "ENABLED", False)               # no network in tests
    yield
    _detach_routers()


# ------------------------------------------------------------------ pure calendar
def test_phases_over_a_full_week():
    from services import academy_calendar as cal
    keys = ["admission", "classes", "campus", "midterms", "fest", "finals", "results"]
    for i, key in enumerate(keys):
        for off in (timedelta(0), 12 * H, 23 * H + 59 * timedelta(minutes=1)):
            ph = cal.phase_at(T0, T0 + i * D + off)
            assert (ph.key, ph.day) == (key, i + 1), (i, off)
    # boundaries: start inclusive, end exclusive
    assert cal.phase_at(T0, T0 + D).key == "classes"
    assert cal.phase_at(T0, T0 + D - timedelta(seconds=1)).key == "admission"
    assert cal.phase_at(T0, T0 - timedelta(seconds=1)).key == "upcoming"
    brk = cal.phase_at(T0, T0 + 7 * D)
    assert brk.key == "break" and brk.day == 0 and brk.ends_at == T0 + 7 * D + 6 * H


def test_time_left_and_next_phase():
    from services import academy_calendar as cal
    now = T0 + 2 * D + 5 * H                                    # Day 3, 5h in
    assert cal.time_left(T0, now) == 19 * H
    nxt = cal.next_phase(T0, now)
    assert (nxt.key, nxt.day, nxt.starts_at) == ("midterms", 4, T0 + 3 * D)
    d7 = cal.next_phase(T0, T0 + 6 * D + H)
    assert d7.key == "break" and d7.starts_at == T0 + 7 * D
    inbreak = cal.next_phase(T0, T0 + 7 * D + H)                # next one is Day 1 of the NEXT season
    assert inbreak.key == "admission" and inbreak.starts_at == T0 + 7 * D + 6 * H
    assert cal.time_left(T0, T0 + 7 * D + 2 * H) == 4 * H
    assert cal.next_phase(T0, T0 - H).key == "admission"
    assert [p.day for p in cal.week(T0)] == list(range(1, 8))


def test_break_length_follows_env(monkeypatch):
    from services import academy_calendar as cal
    monkeypatch.setenv("ACADEMY_BREAK_HOURS", "12")
    assert cal.phase_at(T0, T0 + 7 * D).ends_at == T0 + 7 * D + 12 * H
    assert cal.cycle() == 7 * D + 12 * H
    monkeypatch.setenv("ACADEMY_BREAK_HOURS", "nonsense")
    assert cal.cycle() == 7 * D + 6 * H


def test_late_join_rules():
    from services import academy_calendar as cal
    w = cal.enroll_window(T0, T0 + 3 * H)
    assert (w.open, w.late) == (True, False)                   # Day 1 = on time
    for day in (2, 3, 4):
        w = cal.enroll_window(T0, T0 + (day - 1) * D + H)
        assert (w.open, w.late) == (True, True), day           # Days 2-4 = late joiner
    assert cal.enroll_window(T0, T0 + 4 * D - timedelta(seconds=1)).open       # last second of Day 4
    for later in (T0 + 4 * D, T0 + 5 * D, T0 + 6 * D + H, T0 + 7 * D + H):
        assert cal.enroll_window(T0, later).open is False
    assert cal.enroll_window(T0, T0 - H).open is False         # not started yet
    assert cal.enroll_window(T0, T0).closes_at == T0 + 4 * D


def test_times_are_shown_in_academy_tz(monkeypatch):
    from services import academy_calendar as cal
    assert cal.fmt_when(datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)) == "Mon 02 Nov, 05:30 IST"
    monkeypatch.setenv("ACADEMY_TZ", "UTC")
    assert cal.fmt_when(datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)).endswith("UTC")


# ------------------------------------------------------------------ pure energy
def test_energy_regeneration_math():
    from services.academy_students import energy_bar, regen_energy
    t = T0
    assert regen_energy(50, t, t + 3 * H) == (80, t + 3 * H)                        # 10 per hour
    assert regen_energy(50, t, t + timedelta(seconds=359)) == (50, t)                      # 6 min per point: not yet
    e, at = regen_energy(50, t, t + timedelta(minutes=100))                         # 1 point per 6 min: 16 points + 4 min left over
    assert e == 66 and at == t + timedelta(minutes=96)
    assert regen_energy(95, t, t + 5 * H)[0] == 100 and regen_energy(100, t, t + 5 * H) == (100, t + 5 * H)
    assert regen_energy(40, t, t - H) == (40, t)                                    # clock going backwards: nothing happens
    assert energy_bar(100) == "▰" * 10 and energy_bar(0) == "▱" * 10 and energy_bar(50) == "▰" * 5 + "▱" * 5


# ------------------------------------------------------------------ database
async def _setup(n: int = 3):
    from database.session import db
    from services import users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in range(1, n + 1):
        await users.touch(uid, f"u{uid}", f"User{uid}")
    return db


def test_season_creation_and_rollover():
    async def go():
        from sqlalchemy import select, func
        from database.models import AcademySeason, aware
        from services import academy_calendar as cal
        db = await _setup()
        now = T0 + 2 * H + timedelta(minutes=20)
        async with db.tx() as s:
            s1 = await cal.ensure_season(s, now)
            assert s1.number == 1 and aware(s1.start_at) == T0 + 2 * H and aware(s1.end_at) == T0 + 2 * H + 7 * D
            assert s1.status == "active" and s1.theme
        async with db.tx() as s:                                 # idempotent
            assert (await cal.ensure_season(s, now + D)).number == 1
            assert (await s.execute(select(func.count()).select_from(AcademySeason))).scalar_one() == 1
        start = T0 + 2 * H
        async with db.tx() as s:                                 # in the break: still season 1, marked ended
            s1 = await cal.ensure_season(s, start + 7 * D + 3 * H)
            assert s1.number == 1 and s1.status == "ended"
            assert cal.phase_at(s1.start_at, start + 7 * D + 3 * H).key == "break"
        async with db.tx() as s:                                 # break is over: season 2 at its exact calendar start
            s2 = await cal.ensure_season(s, start + 7 * D + 6 * H + timedelta(minutes=5))
            assert s2.number == 2 and aware(s2.start_at) == start + 7 * D + 6 * H
            assert cal.phase_at(s2.start_at, start + 7 * D + 6 * H + timedelta(minutes=5)).day == 1
        async with db.tx() as s:                                 # bot offline for 3 cycles: one new row, on the calendar grid
            s3 = await cal.ensure_season(s, start + 3 * (7 * D + 6 * H) + 2 * D)
            assert s3.number == 3 and aware(s3.start_at) == start + 3 * (7 * D + 6 * H)
            assert (await s.execute(select(func.count()).select_from(AcademySeason))).scalar_one() == 3
        await db.dispose()
    run(go())


def test_enrol_is_idempotent_and_applies_stream_bonus():
    async def go():
        from sqlalchemy import select, func
        from database.models import AcademyStudent
        from game import academy_rules as AR
        from services import academy_calendar as cal, academy_students as stu
        from services.errors import GameError
        db = await _setup()
        now = T0 + H
        async with db.tx() as s:
            season = await cal.ensure_season(s, now)
            st, created = await stu.enroll(s, 1, season, "science", "coding", now)
            assert created and (st.stream, st.club, st.late_join) == ("science", "coding", False)
            assert st.knowledge == 15 and st.creativity == 12 and st.charisma == 10 and st.fitness == 10
            assert st.energy == 100
        async with db.tx() as s:                                 # double tap / retry / different pick: nothing changes
            st2, created2 = await stu.enroll(s, 1, season, "arts", "drama", now + H)
            assert not created2 and (st2.stream, st2.club, st2.knowledge) == ("science", "coding", 15)
            assert (await s.execute(select(func.count()).select_from(AcademyStudent))).scalar_one() == 1
        async with db.tx() as s:
            with pytest.raises(GameError):
                await stu.enroll(s, 2, season, "wizardry", "coding", now)
            with pytest.raises(GameError):
                await stu.enroll(s, 2, season, "arts", "chess", now)
        # late joiner on Day 3 is flagged; after Day 4 nobody can join
        async with db.tx() as s:
            st3, c3 = await stu.enroll(s, 2, season, "sports", "sports", T0 + 2 * D + H)
            assert c3 and st3.late_join and st3.fitness == 15
        async with db.tx() as s:
            with pytest.raises(GameError):
                await stu.enroll(s, 3, season, "music", "music", T0 + 4 * D + H)
        # same user, next season: a fresh enrolment is allowed
        async with db.tx() as s:
            s2 = await cal.ensure_season(s, T0 + H + 7 * D + 6 * H)
            _, c = await stu.enroll(s, 1, s2, "arts", "drama", T0 + H + 7 * D + 6 * H)
            assert c and s2.id != season.id
        await db.dispose()
    run(go())


def test_spend_restore_energy_and_stats():
    async def go():
        from services import academy_calendar as cal, academy_students as stu
        from services.errors import GameError
        db = await _setup()
        now = T0 + H
        async with db.tx() as s:
            season = await cal.ensure_season(s, now)
            await stu.enroll(s, 1, season, "arts", "drama", now)
        async with db.tx() as s:
            assert await stu.spend_energy(s, 1, season.id, 30, now) == 70
        async with db.tx() as s:                                 # 2h later: +20 (lazy, no loop)
            st = await stu.get_student(s, 1, season.id)
            assert stu.current_energy(st, now + 2 * H) == 90
            assert await stu.spend_energy(s, 1, season.id, 10, now + 2 * H) == 80
        async with db.tx() as s:
            with pytest.raises(GameError):
                await stu.spend_energy(s, 1, season.id, 95, now + 2 * H)
        async with db.tx() as s:                                 # a failed spend took nothing
            st = await stu.get_student(s, 1, season.id)
            assert stu.current_energy(st, now + 2 * H) == 80
            assert await stu.restore_energy(s, 1, season.id, 500, now + 2 * H) == 100
        async with db.tx() as s:
            with pytest.raises(GameError):
                await stu.spend_energy(s, 2, season.id, 1, now)           # not enrolled
            st = await stu.add_stats(s, 1, season.id, {"knowledge": 7, "fitness": -50, "charisma": 500})
            assert (st.knowledge, st.fitness, st.charisma) == (17, 0, 100)   # arts: knowledge 10 + 7, clamped at 0 / 100
            with pytest.raises(ValueError):
                await stu.add_stats(s, 1, season.id, {"luck": 1})
        await db.dispose()
    run(go())


def test_pay_diamonds_is_idempotent():
    async def go():
        from services import academy_students as stu, economy
        db = await _setup()
        start = (await economy.balance(1)).balance
        async with db.tx() as s:
            assert await stu.pay_diamonds(s, 1, 250, "test:pay1") is True
        async with db.tx() as s:
            assert await stu.pay_diamonds(s, 1, 250, "test:pay1") is False       # retry pays nothing
        assert (await economy.balance(1)).balance == start + 250
        async with db.tx() as s:
            assert await stu.pay_diamonds(s, 1, 100, "academy:test:pay2") is True
        assert (await economy.balance(1)).balance == start + 350
        with pytest.raises(ValueError):
            async with db.tx() as s:
                await stu.pay_diamonds(s, 1, 0, "x")
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ events, ticks, rollover tick
def test_events_and_tick_registry():
    async def go():
        from services import academy_events as ev, academy_ticks as tk
        got = []

        @ev.subscribe("unit_test")
        async def good(uid, **d):
            got.append((uid, d))

        @ev.subscribe("unit_test")
        def boom(uid, **d):
            raise RuntimeError("subscriber bug")

        assert await ev.emit("unit_test", 7, a=1) == 1 and got == [(7, {"a": 1})]
        assert await ev.emit("nobody_listens", 1) == 0
        ev.subscribe("unit_test")(good)                          # re-registering does not duplicate
        assert len(ev.subscribers("unit_test")) == 2

        calls = []

        @tk.tick(name="unit.ok")
        async def ok(bot, now):
            calls.append("ok")

        @tk.tick(name="unit.bad")
        async def bad(bot, now):
            raise RuntimeError("tick bug")

        @tk.tick(name="unit.slow", every=3600)
        async def slow(bot, now):
            calls.append("slow")

        r1 = await tk.run_ticks(None)
        assert r1["unit.ok"] == "ok" and r1["unit.bad"] == "error" and r1["unit.slow"] == "ok"
        r2 = await tk.run_ticks(None)
        assert r2["unit.slow"] == "skipped" and calls.count("slow") == 1
        assert (await tk.run_ticks(None, force=True))["unit.slow"] == "ok"
        for n in ("unit.ok", "unit.bad", "unit.slow"):
            tk._ticks.pop(n, None)
    run(go())


def test_rollover_tick_announces_each_boundary_once():
    async def go():
        from database.session import db
        from services import academy_events as ev, academy_ticks as tk
        await _setup(1)
        seen = []

        @ev.subscribe(ev.SEASON_STARTED)
        def st(uid, **d):
            seen.append(("start", d["number"]))

        @ev.subscribe(ev.SEASON_ENDED)
        def en(uid, **d):
            seen.append(("end", d["number"]))

        now = T0
        await tk.rollover_tick(None, now)
        await tk.rollover_tick(None, now + D)
        assert seen == [("start", 1)]                            # once, however often the tick runs
        await tk.rollover_tick(None, now + 7 * D + H)            # in the break: season 1 ended
        await tk.rollover_tick(None, now + 7 * D + 2 * H)
        assert seen == [("start", 1), ("end", 1)]
        await tk.rollover_tick(None, now + 7 * D + 6 * H + 1 * timedelta(minutes=1))
        assert seen == [("start", 1), ("end", 1), ("start", 2)]
        # a handler created season 3 while the bot was "off"; the tick still announces end(2) then start(3)
        from services import academy_calendar as cal
        later = now + 2 * (7 * D + 6 * H) + D
        async with db.tx() as s:
            await cal.ensure_season(s, later)
        await tk.rollover_tick(None, later)
        assert seen[3:] == [("end", 2), ("start", 3)]
        for fn in (st, en):
            ev._subs[ev.SEASON_STARTED if fn is st else ev.SEASON_ENDED].remove(fn)
        await db.dispose()
    run(go())


def test_advance_and_reset():
    async def go():
        from database.models import aware
        from services import academy_calendar as cal, academy_students as stu
        db = await _setup()
        now = T0 + 5 * H
        async with db.tx() as s:
            season = await cal.ensure_season(s, now)
            await stu.enroll(s, 1, season, "music", "music", now)
        seen = []
        for _ in range(7):                                       # walk the whole week
            async with db.tx() as s:
                season, ph = await cal.advance(s, now)
            seen.append((ph.key, ph.day))
            assert cal.phase_at(season.start_at, now) == ph
        assert [d for _, d in seen] == [2, 3, 4, 5, 6, 7, 0] and seen[-1][0] == "break"
        async with db.tx() as s:
            season, ph = await cal.advance(s, now)               # from the break: next season, Day 1, right now
            assert season.number == 2 and (ph.key, ph.day) == ("admission", 1) and aware(season.start_at) == now
        async with db.tx() as s:
            st, se = await cal.reset_all(s)
        assert (st, se) == (1, 2)
        async with db.tx() as s:
            assert (await cal.ensure_season(s, now)).number == 1
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ views
def test_dashboard_and_schedule_text():
    async def go():
        from services import academy_calendar as cal, academy_students as stu, academy_views as views
        db = await _setup()
        now = T0 + 2 * D + 3 * H                                 # Day 3
        async with db.tx() as s:
            season = await cal.ensure_season(s, T0)
            await stu.enroll(s, 1, season, "science", "coding", T0 + 2 * D)
        async with db.tx() as s:
            st = await stu.get_student(s, 1, season.id)
            await stu.spend_energy(s, 1, season.id, 40, now)
            st = await stu.get_student(s, 1, season.id)
        txt = views.dashboard(season, st, now)
        for needle in ("Season 1", "Science", "Coding Club", "Knowledge", "Energy", "60/100", "Day 3", "Club &amp; Campus Day", "lighter workload"):
            assert needle in txt, needle
        assert "Not enrolled" not in txt
        out = views.dashboard(season, None, now)
        assert "/enroll" in out and "lighter workload" in out
        late = views.dashboard(season, None, T0 + 4 * D + H)
        assert "closed" in late
        sch = views.schedule(season, now)
        assert sch.count("Day ") >= 8 and "✅ Day 1" in sch and "▶️ Day 3" in sch and "▫️ Day 4" in sch
        assert "Late enrolment closes" in sch and "Next season opens" in sch and "Next in" in sch
        brk = views.schedule(season, T0 + 7 * D + H)
        assert "Break" in brk and "new season" in brk
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ help catalog / menus
def test_commands_are_in_help_but_never_in_the_slash_menu():
    from utils import helpcatalog as H
    from utils import commands as C
    items = {c for c, _, _ in H.entries("Bot", "*")}
    assert {"enroll", "academy", "schedule"} <= items
    assert not {"academyadvance", "academyreset"} & items and {"academyadvance", "academyreset"} <= H.STAFF
    names = {c for g in (False, True) for c, _ in H.menu("Bot", "*", g)}
    assert not names & {"enroll", "academy", "schedule", "academyadvance", "academyreset", "school", "timetable"}
    assert not {c.command for c in C.menu_for("owner", True)} & {"enroll", "academy", "schedule", "academyadvance", "academyreset"}
    secs = dict(H.sections(list(H.entries("Bot", "*")), {}, {}))
    assert "Academy 🎓" in secs and "/enroll" in secs["Academy 🎓"][0]


# ------------------------------------------------------------------ end to end through the real dispatcher
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


def test_player_flow_through_the_dispatcher(monkeypatch):
    async def go():
        from database.session import db
        from services import academy_events as ev
        _owner1(monkeypatch)
        await _setup(3)
        h = Harness()
        enrolled = []

        @ev.subscribe(ev.ENROLLED)
        def on(uid, **d):
            enrolled.append((uid, d["stream"], d["club"], d["late_join"]))

        out = await h.say(2, "/academy")
        assert "Season 1" in out[0] and "Day 1" in out[0] and "/enroll" in out[0]
        out = await h.say(2, "/enroll")
        assert "Step 1 of 2" in out[0]
        btns = h.buttons()
        assert all(len(b.encode()) < 64 for b in btns) and "ac:st:2:science" in btns
        await h.tap(3, "ac:st:2:science")                        # somebody else's menu
        assert "isn't your" in h.alerts()[0]
        out = await h.tap(2, "ac:st:2:science")
        assert "Step 2 of 2" in out[0] and "ac:cl:2:science:coding" in h.buttons()
        out = await h.tap(2, "ac:cl:2:science:coding")
        assert "Welcome to the Academy" in out[0] and "Science" in out[0] and "Coding Club" in out[0]
        assert enrolled == [(2, "science", "coding", False)]
        out = await h.tap(2, "ac:cl:2:arts:drama")               # tapping again later does not re-enrol
        assert "already enrolled" in out[0] and "Science" in out[0] and len(enrolled) == 1
        out = await h.say(2, "/enroll")
        assert "already enrolled" in out[0]
        out = await h.say(2, "/academy")
        assert "Science" in out[0] and "Energy" in out[0] and "100/100" in out[0]
        out = await h.say(2, "/schedule")
        assert "Academy schedule" in out[0] and "Next in" in out[0] and "ac:db:2" in h.buttons()
        out = await h.say(2, "/school")
        assert "Science" in out[0]
        out = await h.tap(2, "ac:sc:2")
        assert "Academy schedule" in out[0]
        # kill switch
        monkeypatch.setenv("ACADEMY_ENABLED", "off")
        out = await h.say(2, "/academy")
        assert "closed right now" in out[0]
        monkeypatch.setenv("ACADEMY_ENABLED", "1")
        # staff-only commands are invisible to players
        out = await h.say(2, "/academyadvance")
        assert not any("Season" in t and "→" in t for t in out)
        ev._subs[ev.ENROLLED].remove(on)
        await db.dispose()
    run(go())


def test_staff_advance_and_reset_through_the_dispatcher(monkeypatch):
    async def go():
        from database.session import db
        from services import academy_calendar as cal
        _owner1(monkeypatch)
        await _setup(2)
        h = Harness()
        out = await h.say(1, "/academy")
        assert "Day 1" in out[0]
        out = await h.say(1, "/academyadvance")
        assert "Day 2" in out[0] and "Classes Begin" in out[0]
        out = await h.say(1, "/academyadvance")
        assert "Day 3" in out[0]
        out = await h.say(2, "/enroll")                           # late joiner on Day 3
        assert "lighter workload" in out[0]
        await h.tap(2, "ac:st:2:music")
        out = await h.tap(2, "ac:cl:2:music:music")
        assert "Welcome" in out[0] and "lighter workload" in out[0]
        for _ in range(2):
            await h.say(1, "/academyadvance")                     # Day 4 -> Day 5: enrolment closed
        out = await h.say(1, "/academy")
        assert "Day 5" in out[0]
        await h.say(1, "/enroll")
        assert "closed" in h.sent()[0] or "already" in h.sent()[0]
        out = await h.say(1, "/academyreset")
        assert "Reset the Academy" in out[0] and "acs:rs:1" in h.buttons()
        await h.tap(2, "acs:rs:1")                                # a player pressing it does nothing
        out = await h.tap(1, "acs:rs:1")
        assert "Academy reset" in out[0] and "Season 1 starts now" in out[0]
        async with db.tx() as s:
            from services import academy_students as stu
            season = await cal.ensure_season(s)
            assert season.number == 1 and await cal.current_student_count(s, season.id) == 0
        await db.dispose()
    run(go())
