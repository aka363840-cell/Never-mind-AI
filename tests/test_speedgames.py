"""Speed games (⚡ /tap, 💥 /crash) tests.  Run: pytest tests/test_speedgames.py

* the pure rules / word bank / picture / help wiring tests need no database (Pillow only for the picture test);
* the escrow / settlement / recovery / leaderboard tests use a real SQLite database exactly like tests/test_arcade.py.
"""
import ast
import asyncio
import os
import random
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(tempfile.gettempdir(), "speedgames_test.db")
URL = f"sqlite+aiosqlite:///{DB}"          # module-local: test files must not share one database


def run(coro):
    return asyncio.run(coro)


def _read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------------------ pure rules
def test_word_bank_is_clean():
    from utils.content.speedgames import CRASH_WORDS as W
    assert len(W) >= 100 and len(set(W)) == len(W)
    assert all(w.isalpha() and w.isupper() and w.isascii() and 5 <= len(w) <= 14 for w in W)


def test_counting_and_number_parsing():
    from game import speedgames as G
    assert G.letter_count("ELEPHANT") == 8 and G.letter_count("ICE-CREAM 2") == 8
    assert G.count_for("letters", "ELEPHANT") == 8 and G.count_for("vowels", "ELEPHANT") == 3
    assert G.count_for("consonants", "ELEPHANT") == 5 and G.count_for("letter", "ELEPHANT", "e") == 2
    assert G.parse_number("7") == 7 and G.parse_number("  12 ") == 12 and G.parse_number("007") == 7
    for chatter in ("", None, "lol", "7 8", "7.5", "-3", "1234", "seven", "/7"):
        assert G.parse_number(chatter) is None, chatter


def test_stake_parsing_limits_and_practice():
    from game import speedgames as G
    assert G.parse_stake("100", 5) == 100 and G.parse_stake("2k", 5) == 2000 and G.parse_stake("1,000", 5) == 1000
    assert G.parse_stake("all", 700) == 700 and G.parse_stake("all", 10**9) == G.MAX_STAKE
    for word in ("free", "FREE", "practice", "demo", "0"):
        assert G.parse_stake(word, 100) == 0, word                                   # practice = 0, not "invalid"
    assert G.parse_stake(None, 100) is None and G.parse_stake("abc", 100) is None and G.parse_stake("-5", 100) is None
    assert G.stake_problem(0) is None and G.stake_problem(G.MIN_STAKE) is None and G.stake_problem(G.MAX_STAKE) is None
    assert "minimum" in G.stake_problem(G.MIN_STAKE - 1) and "maximum" in G.stake_problem(G.MAX_STAKE + 1)


def test_payment_split_never_creates_or_loses_diamonds():
    from game import speedgames as G
    for pay in (1, 9, 10, 37, 100, 999, 50_000):
        net, fee = G.split_pay(pay)
        assert net + fee == pay and fee == int(pay * G.FEE_PCT) and net > 0
    assert G.split_pay(100) == (95, 5)


def test_match_flow_best_of_three():
    from game import speedgames as G
    assert G.wins_needed(2, False) == 2 and G.wins_needed(1, True) == 1 and G.wins_needed(2, True) == 2
    assert G.max_rounds(1) == 1 and G.max_rounds(2) == G.MAX_ROUNDS
    ms = G.match_state
    assert ms({1: 0, 2: 0}, 2, 1, 5) == ("continue", None)
    assert ms({1: 1, 2: 0}, 2, 1, 5) == ("continue", None)
    assert ms({1: 2, 2: 0}, 2, 2, 5) == ("winner", 1)
    assert ms({1: 1, 2: 2}, 2, 3, 5) == ("winner", 2)
    assert ms({1: 1, 2: 1}, 2, 4, 5) == ("continue", None)
    assert ms({1: 1, 2: 1}, 2, 5, 5) == ("draw", None)                              # cap reached, tied -> everybody is refunded
    assert ms({1: 1, 2: 0}, 2, 5, 5) == ("winner", 1)                               # cap reached, a clear leader wins
    assert ms({1: 0, 2: 0}, 2, 5, 5) == ("draw", None)
    assert ms({1: 0}, 1, 1, 1) == ("draw", None) and ms({1: 1}, 1, 1, 1) == ("winner", 1)   # solo practice


def test_challenges_vary_and_are_always_answerable():
    from game import speedgames as G
    rng = random.Random(7)
    kinds = set()
    for r in range(1, 7):
        for _ in range(80):
            c = G.make_challenge(r, rng, avoid=set())
            assert 1 <= c.answer <= 999 and c.answer == G.count_for(c.kind, c.word, c.letter)
            assert c.word.isalpha() and (c.kind not in ("letter", "position") or c.letter in c.word)
            kinds.add(c.kind)
            if r == 1: assert c.kind in ("letters", "vowels", "consonants") and 5 <= len(c.word) <= 8
            if r == 2: assert c.kind in ("vowels", "consonants", "distinct", "letter")
            if r == 3: assert c.kind in ("letter", "distinct", "repeated", "position")
    assert kinds == {"letters", "vowels", "consonants", "letter", "distinct", "repeated", "position"}
    assert G.count_for("position", "ELEPHANT", "P") == 4 and G.count_for("distinct", "ELEPHANT") == 7 and G.count_for("repeated", "ELEPHANT") == 1
    first = {G.make_challenge(1, rng, avoid=set()).word for _ in range(120)}
    assert len(first) > 20                                                          # words vary a lot


def test_recent_words_are_not_repeated_in_a_chat():
    from game import speedgames as G
    from utils.content.speedgames import CRASH_WORDS as W
    rng = random.Random(3)
    G._RECENT.clear()
    seen = []
    for _ in range(200):
        c = G.make_challenge(2, rng, avoid=G.recent_words(5))
        G.remember_word(5, c.word)
        seen.append(c.word)
    assert len(set(seen)) == len(seen) and len(W) >= 600


def test_tap_timings_and_layout():
    from game import speedgames as G
    rng = random.Random(1)
    for _ in range(200):
        assert G.TAP_DELAY[0] <= G.tap_delay(rng) <= G.TAP_DELAY[1]
    assert {G.tap_green(rng) for _ in range(200)} == set(range(G.TAP_BUTTONS))      # the green button really moves around
    assert [G.tap_count(r) for r in range(1, 7)] == [6, 9, 12, 15, 15, 15]
    assert {G.tap_green(rng, 15) for _ in range(600)} == set(range(15))
    assert G.reaction(0.4321) == "≈0.43s" and G.reaction(-1) == "≈0.00s"


def test_word_picture_is_a_png_and_never_the_same_twice():
    from utils import crashcard
    a, b = crashcard.render("ELEPHANT"), crashcard.render("ELEPHANT")
    for png in (a, b):
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and 5_000 < len(png) < 400_000
    assert a != b                                                                   # random fonts/tilt/noise: no fixed picture to hash
    import io
    from PIL import Image
    w, h = Image.open(io.BytesIO(crashcard.render("HIPPOPOTAMUS"))).size
    assert w >= 520 and h > 100 and Image.open(io.BytesIO(crashcard.render("A"))).size[0] >= 520


# ------------------------------------------------------------------ help / menu / wiring (pure: reads sources)
def test_commands_are_in_help_menu_and_games_category():
    from utils import helpcatalog as H
    for c in ("tap", "jointap", "taptop", "crash", "joincrash", "crashtop"):
        assert c in H.DESC and H.DESC[c].strip(), c
        assert any(c in cmds for title, cmds in H.CATEGORIES if title == "🕹 Games"), c
        assert sum(c in cmds for _, cmds in H.CATEGORIES) == 1, c
    assert {"tap", "crash"} <= set(H.MENU) and not {"jointap", "joincrash", "taptop", "crashtop"} & set(H.MENU)
    assert "endgame" in H.DESC and "Tap" in H.DESC["endgame"] and "WordSeek" in H.DESC["endgame"]
    dm, grp = H.menu("Bot", "*", False), H.menu("Bot", "*", True)
    assert 20 <= len(dm) <= 40 and len(grp) <= 100
    assert {c for c, _ in dm} >= {"tap", "crash"}


def _commands_of(path: str) -> list[list[str]]:
    out = []
    for n in ast.walk(ast.parse(_read(path))):
        if isinstance(n, ast.AsyncFunctionDef):
            for d in n.decorator_list:
                if (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "message" and d.args
                        and isinstance(d.args[0], ast.Call) and getattr(d.args[0].func, "id", "") == "Command"):
                    out.append([a.value for a in d.args[0].args if isinstance(a, ast.Constant)])
    return out


def test_handler_registers_exactly_the_documented_commands():
    assert sorted(sum(_commands_of("handlers/speedgames.py"), [])) == ["crash", "crashtop", "endcrash", "endgame", "endtap", "joincrash", "jointap", "tap", "taptop"]


def test_router_wiring_keeps_bot_py_untouched_and_end_before_wordseek():
    arcade = _read("handlers/arcade.py")
    assert "from handlers import speedgames as speed_h" in arcade and arcade.count("include_router(speed_h.router)") == 1
    assert "ar:tap" in arcade and "ar:cr" in arcade and "speed_h.do_tap_info" in arcade and "speed_h.do_crash_info" in arcade
    bot = _read("bot.py")
    assert "speedgames" not in bot                                                   # nothing to register there: the arcade router carries it
    line = next(l for l in bot.splitlines() if l.strip().startswith("routers = ["))
    assert line.index("arcade_h.router") < line.index("wordseek_h.router") < line.index("ai_chat.router")   # our /endgame (only while a speed game runs) beats WordSeek's
    src = _read("handlers/speedgames.py")
    assert "RunningSpeedGame()" in src and "outer_middleware(CrashAnswerMiddleware())" in src


def test_arcade_achievements_are_wired():
    from game.rules import ACHIEVEMENTS
    realm_src = _read("services/realm.py")
    for key in ("tap_first", "tap_25", "crash_first", "crash_25"):
        assert key in ACHIEVEMENTS and f'"{key}"' in realm_src
    assert '"tap_win"' in realm_src and '"crash_win"' in realm_src


def test_no_new_tables_and_diamonds_only_move_through_economy_apply():
    for path in ("handlers/speedgames.py", "services/speedgames.py", "game/speedgames.py", "utils/crashcard.py"):
        src = _read(path)
        assert "__tablename__" not in src and "Base)" not in src, path
    svc = _read("services/speedgames.py")
    assert "economy.apply(" in svc and "UPDATE" not in svc.upper().replace("UPDATED", "")
    assert "wallet.balance" not in svc.lower() and "set(balance" not in svc.lower()


# ------------------------------------------------------------------ escrow / settlement / recovery / leaderboards (real SQLite)
async def _setup(funds: int = 1000, users_n: int = 3):
    from database.session import db
    from services import economy, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in range(1, users_n + 1):
        await users.touch(uid, f"u{uid}", f"User{uid}")
        async with db.tx() as s:
            await economy.apply(s, uid, wallet=funds, kind="test_seed", ref=f"seed:{uid}")
    return db


async def _bal(uid: int) -> int:
    from services import economy
    return (await economy.balance(uid)).balance


async def _kinds(uid: int) -> list[str]:
    from database.models import Transaction
    from database.session import db
    from sqlalchemy import select
    async with db.tx() as s:
        return list((await s.execute(select(Transaction.kind).where(Transaction.user_id == uid).order_by(Transaction.id))).scalars())


async def _held_game(kind="tap", amount=100, players=(1, 2, 3)):
    from services import speedgames as svc
    g = svc.Game(kind=kind, chat_id=-100, host=players[0], amount=amount)
    for u in players:
        g.players[u] = f"User{u}"
    assert await svc.hold_entries(g) == []
    return g


def test_entries_are_held_then_winner_collects_and_fee_is_burned():
    async def go():
        from services import realm, speedgames as svc
        db = await _setup()
        g = await _held_game()
        assert [await _bal(u) for u in (1, 2, 3)] == [900, 900, 900]                  # escrow: the entries left the wallets
        res = await svc.release(g, 2)
        assert (res.got, res.burned, res.refunded, res.capped) == (190, 10, False, [])
        assert [await _bal(u) for u in (1, 2, 3)] == [900, 900 + 100 + 190, 900]       # winner: own entry back + 2 x 95
        assert sum([await _bal(u) for u in (1, 2, 3)]) == 3000 - 10                      # only the burn left the economy
        assert sorted(await _kinds(2)) == sorted(["test_seed", "tap_hold", "tap_refund", "tap_win", "tap_win"])      # own entry refunded + one win per loser
        assert sorted(await _kinds(1)) == sorted(["test_seed", "tap_hold", "tap_loss"])
        st = {u: await realm.stats_of(u) for u in (1, 2, 3)}
        assert st[2]["tap_win"] == 1 and all(st[u]["tap_play"] == 1 for u in (1, 2, 3)) and "tap_win" not in st[1]
        assert res.unlocked and any("Quick Draw" in a for a in res.unlocked)
        await db.dispose()
    run(go())


def test_no_winner_refunds_everybody_and_release_is_idempotent():
    async def go():
        from services import realm, speedgames as svc
        db = await _setup()
        g = await _held_game("crash")
        res = await svc.release(g, None, played=True)
        assert res.refunded and res.got == 0
        assert [await _bal(u) for u in (1, 2, 3)] == [1000, 1000, 1000]
        st = await realm.stats_of(1)
        assert st["crash_play"] == 1 and "crash_win" not in st
        again = await svc.release(g, 2)                                                  # a late second settlement must not pay anything
        assert again.got == 0 and [await _bal(u) for u in (1, 2, 3)] == [1000, 1000, 1000]
        await db.dispose()
    run(go())


def test_players_who_cannot_pay_are_not_held_and_practice_holds_nothing():
    async def go():
        from database.session import db as _db
        from services import economy, speedgames as svc
        db = await _setup()
        async with _db.tx() as s:
            await economy.apply(s, 3, wallet=-950, kind="test_spend", ref="spend:3")
        g = svc.Game(kind="tap", chat_id=-100, host=1, amount=100)
        g.players.update({1: "User1", 2: "User2", 3: "User3"})
        removed = await svc.hold_entries(g)
        assert removed == [(3, "User3")] and list(g.players) == [1, 2] and g.held == {1: 100, 2: 100}
        assert await _bal(3) == 50
        p = svc.Game(kind="tap", chat_id=-101, host=1, amount=0)
        p.players[1] = "User1"
        assert await svc.hold_entries(p) == [] and p.practice and p.held == {} and await _bal(1) == 900
        res = await svc.release(p, 1)
        assert (res.got, res.burned) == (0, 0) and await _bal(1) == 900
        await db.dispose()
    run(go())


def test_pair_daily_limit_refunds_the_loser_instead_of_paying():
    async def go():
        from database.session import db as _db
        from game import speedgames as G
        from services import economy, speedgames as svc
        db = await _setup(funds=100_000, users_n=3)
        async with _db.tx() as s:                                                         # player 1 already beat player 2 PAIR_DAILY times today
            for i in range(G.PAIR_DAILY):
                await economy.apply(s, 1, wallet=1, kind="tap_win", counterparty=2, ref=f"prior:{i}")
        g = await _held_game()
        res = await svc.release(g, 1)
        assert res.capped == [2] and res.got == 95                                         # only player 3 pays
        assert await _bal(2) == 100_000 and await _bal(3) == 100_000 - 100                 # 2 got the entry back, 3 paid
        assert await _bal(1) == 100_000 + G.PAIR_DAILY + 95
        await db.dispose()
    run(go())


def test_failed_settlement_rolls_back_and_can_be_retried():
    async def go():
        from services import economy, speedgames as svc
        import pytest
        db = await _setup()
        g = await _held_game(players=(1, 2))
        real = economy.apply
        calls = {"n": 0}

        async def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("db hiccup")
            return await real(*a, **k)
        economy.apply = flaky
        try:
            with pytest.raises(RuntimeError):
                await svc.release(g, None)
        finally:
            economy.apply = real
        assert not g.closed and [await _bal(u) for u in (1, 2)] == [900, 900]              # nothing half-applied
        await svc.release(g, None)
        assert [await _bal(u) for u in (1, 2)] == [1000, 1000]
        await db.dispose()
    run(go())


def test_orphaned_holds_of_a_crashed_bot_are_refunded_exactly_once():
    async def go():
        from game import speedgames as G
        from services import speedgames as svc
        db = await _setup()
        g = await _held_game(players=(1, 2))                                                # ... and the bot dies here: nothing settles
        settled = await _held_game(players=(3,))                                              # this one WAS settled (closing row exists)
        await svc.release(settled, None, played=False)
        old = G.RECOVER_AFTER_S
        G.RECOVER_AFTER_S = 10_000
        try:
            assert await svc.recover_orphans() == 0                                          # still fresh: might be a live match
            G.RECOVER_AFTER_S = -5
            assert await svc.recover_orphans() == 2 and [await _bal(u) for u in (1, 2, 3)] == [1000, 1000, 1000]
            assert await svc.recover_orphans() == 0                                          # idempotent
        finally:
            G.RECOVER_AFTER_S = old
        await db.dispose()
    run(go())


def test_leaderboard_and_my_stats():
    async def go():
        from database.session import db as _db
        from database.models import User
        from services import speedgames as svc
        db = await _setup(users_n=4)
        for winner, players in ((2, (1, 2, 3)), (2, (1, 2, 3)), (3, (1, 3))):
            await svc.release(await _held_game(players=players), winner)
        rows = await svc.top("tap", 10)
        assert [r[:4] for r in rows] == [(2, "User2", 2, 2), (3, "User3", 1, 3)]               # (id, name, wins, matches), best first
        assert await svc.my_stats("tap", 2) == (2, 2, 1)
        assert await svc.my_stats("tap", 3) == (1, 3, 2)
        assert await svc.my_stats("tap", 1) == (0, 3, None)                                     # played but never won: no rank
        assert await svc.top("crash", 10) == []
        async with _db.tx() as s:
            (await s.get(User, 3)).is_flagged = True                                            # flagged accounts are excluded from rankings
        assert [r[0] for r in await svc.top("tap", 10)] == [2]
        await db.dispose()
    run(go())


def test_start_cooldown_and_fund_check():
    async def go():
        from database.session import db as _db
        from services import cooldowns, speedgames as svc
        from services.errors import GameError, OnCooldown
        import pytest
        db = await _setup(funds=50, users_n=2)
        await svc.check_funds(1, 50, "User1")
        await svc.check_funds(1, 0, "User1")                                                 # practice needs nothing
        with pytest.raises(GameError):
            await svc.check_funds(1, 51, "User1")
        await svc.claim_start_cd(1)
        with pytest.raises(OnCooldown):
            await svc.claim_start_cd(1)
        await svc.claim_start_cd(2)
        async with _db.tx() as s:
            await cooldowns.clear(s, 1, "speedgame")
        await svc.claim_start_cd(1)
        await db.dispose()
    run(go())


def test_one_game_per_chat_and_drop_frees_the_chat():
    async def go():
        from services import speedgames as svc
        from services.errors import GameError
        import pytest
        g = svc.new("tap", -555, 1, "User1", 100)
        assert svc.get(-555) is g and list(g.players) == [1] and not g.practice
        with pytest.raises(GameError):
            svc.new("crash", -555, 2, "User2", 100)
        svc.drop(g)
        assert svc.get(-555) is None and g.phase == "done"
        g2 = svc.new("crash", -555, 2, "User2", 0)
        assert svc.get(-555) is g2 and g2.practice
        svc.drop(g2)
    run(go())
