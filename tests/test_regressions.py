"""Regression tests for bugs found in the full audit. Run: pytest tests/test_regressions.py"""
import asyncio
import os
import tempfile
import time
from datetime import timedelta

DB = os.path.join(tempfile.gettempdir(), "regress_test.db")
URL = f"sqlite+aiosqlite:///{DB}"


def run(c):
    return asyncio.run(c)


async def _setup(n_users: int = 3, funds: int = 0):
    from database.session import db
    from services import economy, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in range(1, n_users + 1):
        await users.touch(uid, f"u{uid}", f"User{uid}")
    if funds:
        async with db.tx() as s:
            for uid in range(1, n_users + 1):
                await economy.apply(s, uid, wallet=funds, kind="test")
    return db


def test_rent_messages_name_the_property_and_list_timers():
    async def go():
        from database.models import Property, utcnow
        from services import life
        from services.errors import GameError
        db = await _setup(1)
        async with db.tx() as s:
            for k in ("tent", "flat", "villa"):
                s.add(Property(user_id=1, key=k, bought_at=utcnow()))
        total, names = await life.collect_rent(1)
        assert total == 40 + 300 + 1600 and len(names) == 3
        for arg in ("apartment", "flat", "/flat", "Apartment"):
            try:
                await life.collect_rent(1, arg)
                raise AssertionError("should be on cooldown")
            except GameError as e:
                assert "Apartment rent" in str(e) and "cooldown" in str(e)
        try:
            await life.collect_rent(1)
            raise AssertionError("should be on cooldown")
        except GameError as e:                       # no argument: per-property list, not a vague message
            assert "Tent" in str(e) and "Apartment" in str(e) and "Villa" in str(e)
        try:
            await life.collect_rent(1, "palace")     # not owned
            raise AssertionError
        except GameError as e:
            assert "don't own" in str(e)
        # a newly bought property is collectable on its own while the others still wait
        async with db.tx() as s:
            s.add(Property(user_id=1, key="mansion", bought_at=utcnow()))
        total, names = await life.collect_rent(1)
        assert total == 6000 and len(names) == 1
        assert len(await life.rent_waiting(1)) == 4
        await db.dispose()
    run(go())


def test_baby_needs_funds_before_cooldown_is_spent():
    async def go():
        from database.models import CoupleLove
        from services import cooldowns, extras, life
        from services.errors import GameError
        db = await _setup(2, funds=0)
        async with db.tx() as s:
            from services import economy
            await economy.apply(s, 1, wallet=600, kind="test")
        await extras.marry(1, 2, ref="m1") if False else None
        from database.models import Marriage, utcnow
        async with db.tx() as s:
            s.add(Marriage(user_a=1, user_b=2, married_at=utcnow()))
            s.add(CoupleLove(user_a=1, love=100))
        for _ in range(10):                          # broke (<1000): never "tries", never burns the 12h cooldown
            try:
                await life.have_baby(1, None)
                raise AssertionError("must refuse")
            except GameError as e:
                assert "1,000" in str(e)
        async with db.tx() as s:
            assert await cooldowns.remaining(s, 1, "baby") == 0
        await db.dispose()
    run(go())


def test_marriage_ring_is_checked_on_the_proposer():
    async def go():
        from services import economy, extras
        from services.errors import GameError
        db = await _setup(2)
        try:
            await extras.can_propose(1, 2)           # proposer has 0 diamonds
            raise AssertionError
        except GameError as e:
            assert "ring" in str(e)
        async with db.tx() as s:
            await economy.apply(s, 1, wallet=extras.RING_PRICE, kind="test")
        await extras.can_propose(1, 2)
        async with db.tx() as s:                     # money vanished before the other person accepted
            await economy.apply(s, 1, wallet=-extras.RING_PRICE, kind="test")
        try:
            await extras.marry(1, 2, ref="r1")
            raise AssertionError
        except GameError as e:
            assert "proposer" in str(e) and "enough" not in str(e)
        await db.dispose()
    run(go())


def test_expired_bounty_cancel_is_full_refund():
    async def go():
        from sqlalchemy import update
        from database.models import Bounty, utcnow
        from services import bounty, economy
        db = await _setup(2, funds=5000)
        bid = await bounty.place(1, 2, 1000)
        assert await bounty.cancel(1, bid) == 800    # normal cancel keeps the 20% fee
        bid = await bounty.place(1, 2, 1000)
        async with db.tx() as s:
            await s.execute(update(Bounty).where(Bounty.id == bid).values(expires_at=utcnow() - timedelta(seconds=5)))
        assert await bounty.cancel(1, bid) == 1000
        await db.dispose()
    run(go())


def test_only_one_final_blow_on_a_boss():
    async def go():
        from services import events
        from services.errors import GameError
        from services.kv import kv
        paid = []

        async def slow_set_nx(key, ttl):
            await asyncio.sleep(0.01)
            return True

        async def fake_finish(bot, b, last):
            paid.append(last)
            await asyncio.sleep(0.05)
        real = (kv.set_nx, events._finish)
        kv.set_nx, events._finish = slow_set_nx, fake_finish
        try:
            b = events.Boss("tok", 1, 1, "x", "B", 10, 10, time.time() + 60)
            events.BOSSES["tok"] = b
            res = await asyncio.gather(*(events.hit_boss(object(), "tok", u, "n", 50) for u in (1, 2, 3)), return_exceptions=True)
        finally:
            kv.set_nx, events._finish = real
            events.BOSSES.clear()
        assert len(paid) == 1                         # was 3: every simultaneous tapper got the last-hit bonus
        assert sum(isinstance(r, GameError) for r in res) == 2
    run(go())


def test_levelup_announce_waits_for_the_commit():
    async def go():
        from services import announce
        db = await _setup(1)

        class Bot:
            sent = []

            async def send_message(self, chat_id, text, **kw):
                Bot.sent.append(chat_id)

        # level 3 "announced" while the DB still says level 1 (transaction not committed yet) -> retry, don't drop
        assert await announce._announce(Bot(), 1, 1, 3) is True
        await db.dispose()
    run(go())


# ---------------------------------------------------------------- /top in groups: every board, ◀️ ▶️ pages
def _fake_leaderboard(n=25):
    from types import SimpleNamespace as NS
    kinds = {"rich": ("💎 Richest Players", None), "kills": ("⚔️ Most Kills", None), "level": ("⭐ Highest Level", None), "wins": ("🏆 Most Wins", None)}

    async def page(kind, p):
        rows = [(i + 1, i, "N" * 64 if i % 2 else f"P{i}", 5_000_000 + i if kind == "level" else 1000 - i) for i in range(p * 10, min(n, p * 10 + 10))]
        return rows, n

    async def rank_of(kind, uid):
        return 4, 1
    return NS(KINDS=kinds, PAGE=10, page=page, rank_of=rank_of)


def _buttons(markup):
    return [] if markup is None else [b.callback_data for row in markup.inline_keyboard for b in row]


def test_top_all_boards_paged_with_arrows(monkeypatch):
    import asyncio
    from handlers import views
    monkeypatch.setattr(views, "leaderboard", _fake_leaderboard(25))
    text, mk = asyncio.run(views.rankings_all(7, "Aman", 0))
    for title in ("Richest Players", "Most Kills", "Highest Level", "Most Wins"):
        assert title in text
    assert "Page 1/3" in text and "Aman" in text and len(text) < 4096
    assert _buttons(mk) == ["lbg:1:7"]                                   # first page: only ▶️
    text, mk = asyncio.run(views.rankings_all(7, "Aman", 1))
    assert _buttons(mk) == ["lbg:0:7", "lbg:2:7"] and "11." in text      # middle page: ◀️ and ▶️
    text, mk = asyncio.run(views.rankings_all(7, "Aman", 99))            # stale page number -> last page
    assert "Page 3/3" in text and _buttons(mk) == ["lbg:1:7"] and "25." in text and "26." not in text
    monkeypatch.setattr(views, "leaderboard", _fake_leaderboard(10))
    text, mk = asyncio.run(views.rankings_all(7, "Aman", 0))             # everybody fits on one page -> no arrows
    assert mk is None and "Page" not in text


def test_top_command_group_pages_dm_keeps_tabs(monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS
    from handlers import rank, views

    async def one(kind, page, uid):
        return "ONE", "TABS"

    async def every(uid, name, page=0):
        return "ALL", "ARROWS"
    monkeypatch.setattr(views, "rankings", one)
    monkeypatch.setattr(views, "rankings_all", every)

    def msg(chat_type):
        sent = []

        async def reply(text, reply_markup=None):
            sent.append((text, reply_markup))
        return NS(chat=NS(type=chat_type), reply=reply), sent

    for chat_type in ("group", "supergroup"):
        m, sent = msg(chat_type)
        asyncio.run(rank.top(m, NS(id=1, first_name="A")))
        assert sent == [("ALL", "ARROWS")]                  # every board, with page arrows
    m, sent = msg("private")
    asyncio.run(rank.top(m, NS(id=1, first_name="A")))
    assert sent == [("ONE", "TABS")]                         # DM unchanged
