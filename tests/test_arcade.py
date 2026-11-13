"""Arcade & social tests. Run: pytest tests/test_arcade.py"""
import asyncio
import os
import random
import tempfile
from datetime import date

import pytest

DB = os.path.join(tempfile.gettempdir(), "arcade_test.db")
URL = f"sqlite+aiosqlite:///{DB}"          # module-local: test files must not share one database


def run(coro):
    return asyncio.run(coro)


async def _setup(funds: int = 10_000):
    from database.session import db
    from services import economy, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in (1, 2, 3):
        await users.touch(uid, f"u{uid}", f"User{uid}")
        async with db.tx() as s:
            await economy.apply(s, uid, wallet=funds, kind="test_seed", ref=f"seed:{uid}")
    return db


async def _clear(db, uid, key):
    from services import cooldowns
    async with db.tx() as s:
        await cooldowns.clear(s, uid, key)


# ------------------------------------------------------------------ pure logic
def test_scratch_grids_are_always_consistent():
    from services import arcade
    rng = random.Random(7)
    for _ in range(500):
        for _, prize, sym in arcade.SCRATCH_TABLE:
            cells = arcade.scratch_grid(sym, rng)
            assert len(cells) == 6
            counts = {c: cells.count(c) for c in set(cells)}
            if prize:
                assert counts[sym] == 3 and sum(1 for n in counts.values() if n >= 3) == 1   # exactly one winning triple
            else:
                assert max(counts.values()) <= 2                                             # a losing card never shows 3 alike


def test_scratch_return_to_player_is_below_100_percent():
    from services import arcade
    total = sum(w for w, _, _ in arcade.SCRATCH_TABLE)
    assert total == 1000
    rtp = sum(w * p for w, p, _ in arcade.SCRATCH_TABLE) / total / arcade.SCRATCH_COST
    assert 0.90 <= rtp < 1.0, rtp


def test_rps_rules_and_tictactoe():
    from services import arcade
    assert arcade.rps_outcome("r", "s") == "win" and arcade.rps_outcome("r", "p") == "lose" and arcade.rps_outcome("p", "p") == "tie"
    assert arcade.ttt_winner("xxx      ") == "x" and arcade.ttt_winner("o  o  o  ") == "o" and arcade.ttt_winner("x   x   x") == "x"
    assert arcade.ttt_over("xoxxoooxx") == "draw" and arcade.ttt_over("x        ") is None and arcade.ttt_over("xxxoo    ") == "x"


def test_content_is_valid_and_fits_telegram_limits():
    from services import arcade
    for words in arcade.WORDS.values():
        for w in words:
            assert w.isalpha() and w.isupper() and w.isascii() and 4 <= len(w) <= 12, w
    assert len(arcade.WYR) >= 20 and len(arcade.RIDDLES) >= 15 and len(arcade.FACTS) >= 20
    assert len(arcade.SIGNS) == 12
    assert arcade.hangman_mask("CAT", "A") == "＿ A ＿"
    assert arcade.hangman_reward(6) > arcade.hangman_reward(0) >= 30
    # callback_data must stay <= 64 bytes
    assert len(f"rp:{2**40}:{50_000}:r".encode()) <= 64 and len("ttt:" + "x" * 8 + ":8") <= 64 and len("hm:" + "x" * 8 + ":Z") <= 64


def test_horoscope_and_fortune_are_deterministic_per_day():
    from services import arcade
    d1, d2 = date(2026, 11, 11), date(2026, 11, 12)
    assert arcade.horoscope("leo", d1) == arcade.horoscope("Leo", d1)
    assert arcade.horoscope("leo", d1) != arcade.horoscope("leo", d2) or arcade.horoscope("virgo", d1) != arcade.horoscope("leo", d1)
    assert arcade.fortune(5, d1) == arcade.fortune(5, d1)
    assert arcade.find_sign("sag") == "Sagittarius" and arcade.find_sign("♌") == "Leo" and arcade.find_sign("dragon") is None


# ------------------------------------------------------------------ database flows
def test_scratch_pays_exactly_and_has_a_cooldown():
    async def go():
        from services import arcade, economy
        from services.errors import OnCooldown
        db = await _setup()

        class Win(random.Random):                       # always draws the 1000-diamond row
            def choices(self, pop, weights=None, k=1):
                return [next(t for t in pop if t[1] == 1000)]
        before = (await economy.balance(1)).balance
        cells, prize = await arcade.scratch_buy(1, Win(1))
        assert prize == 1000 and cells.count("💎") == 3
        assert (await economy.balance(1)).balance == before - arcade.SCRATCH_COST + 1000
        with pytest.raises(OnCooldown):
            await arcade.scratch_buy(1)
        await db.dispose()
    run(go())


def test_rps_bets_move_exactly_the_right_amount():
    async def go():
        from services import arcade, economy
        from services.errors import GameError
        db = await _setup()

        class Fixed(random.Random):
            def __init__(self, v): super().__init__(1); self.v = v
            def choice(self, seq): return self.v
        start = (await economy.balance(1)).balance
        _, res, net = await arcade.rps_play(1, "r", 100, Fixed("s"))        # win
        assert (res, net) == ("win", 90) and (await economy.balance(1)).balance == start + 90
        await _clear(db, 1, "casino")
        _, res, net = await arcade.rps_play(1, "r", 100, Fixed("p"))        # lose
        assert (res, net) == ("lose", -100) and (await economy.balance(1)).balance == start - 10
        await _clear(db, 1, "casino")
        _, res, net = await arcade.rps_play(1, "r", 100, Fixed("r"))        # tie: nothing moves
        assert (res, net) == ("tie", 0) and (await economy.balance(1)).balance == start - 10
        _, res, net = await arcade.rps_play(1, "p", 0, Fixed("r"))          # free round: no cooldown, no diamonds
        assert net == 0
        await _clear(db, 1, "casino")
        with pytest.raises(GameError):
            await arcade.rps_play(1, "r", 10**9)                            # can't bet more than the casino limit / the wallet
        await db.dispose()
    run(go())


def test_hangman_reward_is_paid_once():
    async def go():
        from services import arcade, economy
        from services.errors import GameError, OnCooldown
        db = await _setup()
        await arcade.hangman_start(1)
        with pytest.raises(OnCooldown):
            await arcade.hangman_start(1)
        before = (await economy.balance(1)).balance
        got = await arcade.hangman_finish(1, "g1", True, 4)
        assert got == arcade.hangman_reward(4) and (await economy.balance(1)).balance == before + got
        with pytest.raises(GameError):                                       # same game id can never pay twice
            await arcade.hangman_finish(1, "g1", True, 4)
        assert (await economy.balance(1)).balance == before + got
        assert await arcade.hangman_finish(2, "g2", False, 0) == 0
        st = await arcade.stats_of(1)
        assert st.get("hangman_win") == 1
        await db.dispose()
    run(go())


def test_reputation_rules_and_leaderboard():
    async def go():
        from services import arcade
        from services.errors import GameError, OnCooldown
        db = await _setup()
        with pytest.raises(GameError):
            await arcade.give_rep(1, 1)                                      # not to yourself
        assert await arcade.give_rep(1, 2) == 1
        with pytest.raises(OnCooldown):
            await arcade.give_rep(1, 3)                                      # one per 12 h per giver
        assert await arcade.give_rep(3, 2) == 2
        with pytest.raises(GameError):
            await arcade.give_rep(3, 999)                                    # unknown player
        top = await arcade.top_rep()
        assert top[0][0] == 2 and top[0][2] == 2
        assert await arcade.rep_of(2) == 2 and await arcade.rep_cooldown(1) > 0
        await db.dispose()
    run(go())


def test_bank_interest():
    async def go():
        from services import arcade, economy
        from services.errors import GameError, OnCooldown
        db = await _setup(funds=100_000)
        with pytest.raises(GameError):
            await arcade.claim_interest(1)                                   # empty bank
        async with db.tx() as s:
            await economy.apply(s, 1, wallet=-50_000, bank=50_000, kind="deposit", ref="dep1")
        wallet_before = (await economy.balance(1)).balance
        amt, bank = await arcade.claim_interest(1)
        assert amt == arcade.INTEREST_CAP == 500 and bank == 50_500          # 1% of 50k = 500 -> capped at 500
        w = await economy.balance(1)
        assert w.bank == 50_500 and w.balance == wallet_before               # interest goes to the bank, wallet untouched
        with pytest.raises(OnCooldown):
            await arcade.claim_interest(1)
        assert arcade.interest_for(2_000) == 20
        await db.dispose()
    run(go())


def test_ttt_stats_are_recorded():
    async def go():
        from services import arcade
        db = await _setup()
        await arcade.ttt_record(1, 1, 2)
        await arcade.ttt_record(None, 1, 2)
        s1, s2 = await arcade.stats_of(1), await arcade.stats_of(2)
        assert (s1["ttt_win"], s1["ttt_play"], s2.get("ttt_win", 0), s2["ttt_play"]) == (1, 2, 0, 2)
        await db.dispose()
    run(go())


def test_router_and_menu_wiring():
    # (routers are module singletons that attach to ONE Dispatcher per process, so inspect the wiring instead of building another)
    import inspect
    import bot
    from utils import commands
    src = inspect.getsource(bot.build_dispatcher)
    assert "arcade as arcade_h" in src and "arcade_h.router" in src and src.index("arcade_h.router") < src.index("ai_chat.router")
    menu = commands.menu_for("owner", True)
    names = {c.command for c in menu}
    assert len(menu) <= 100 and {"arcade", "daily", "help"} <= names      # scratch / ttt live inside the /arcade hub
    assert not names & {"give", "take", "broadcast", "stats", "backupdb", "ban", "admin"}   # staff commands are never advertised
