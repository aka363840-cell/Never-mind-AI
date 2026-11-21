"""Speed games (⚡ /tap, 💥 /crash) tests.  Run: pytest tests/test_speedgames.py

* the pure rules / word bank / help wiring tests need no third-party packages;
* the settlement tests use a real SQLite database exactly like tests/test_arcade.py (they need the packages from requirements*.txt).
"""
import ast
import asyncio
import os
import random
import re
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


def test_letter_count_and_number_parsing():
    from game import speedgames as G
    assert G.letter_count("ELEPHANT") == 8 and G.letter_count("ICE-CREAM 2") == 8     # only letters count
    assert G.parse_number("7") == 7 and G.parse_number("  12 ") == 12 and G.parse_number("007") == 7
    for chatter in ("", None, "lol", "7 8", "7.5", "-3", "1234", "seven", "/7"):
        assert G.parse_number(chatter) is None, chatter


def test_stake_parsing_and_limits():
    from game import speedgames as G
    assert G.parse_stake("100", 5) == 100 and G.parse_stake("2k", 5) == 2000 and G.parse_stake("1,000", 5) == 1000
    assert G.parse_stake("all", 700) == 700 and G.parse_stake("all", 10**9) == G.MAX_STAKE
    assert G.parse_stake(None, 100) is None and G.parse_stake("abc", 100) is None and G.parse_stake("-5", 100) is None
    assert G.stake_problem(G.MIN_STAKE) is None and G.stake_problem(G.MAX_STAKE) is None
    assert "minimum" in G.stake_problem(G.MIN_STAKE - 1) and "maximum" in G.stake_problem(G.MAX_STAKE + 1)
    assert G.MIN_PLAYERS == 2 and G.MAX_PLAYERS >= G.MIN_PLAYERS


def test_payment_split_never_creates_or_loses_diamonds():
    from game import speedgames as G
    for pay in (1, 9, 10, 37, 100, 999, 50_000):
        net, fee = G.split_pay(pay)
        assert net + fee == pay and fee == int(pay * G.FEE_PCT) and net > 0
    assert G.split_pay(100) == (95, 5)


def test_timings_and_word_pick():
    from game import speedgames as G
    rng = random.Random(1)
    for _ in range(200):
        assert G.TAP_DELAY[0] <= G.tap_delay(rng) <= G.TAP_DELAY[1]
    w = G.pick_word(rng, avoid="ELEPHANT")
    assert w != "ELEPHANT" and w.isalpha()
    assert G.reaction(0.4321) == "≈0.43s" and G.reaction(-1) == "≈0.00s"


# ------------------------------------------------------------------ help / menu / wiring (pure: reads sources)
def test_commands_are_in_help_menu_and_games_category():
    from utils import helpcatalog as H
    for c in ("tap", "jointap", "crash", "joincrash"):
        assert c in H.DESC and H.DESC[c].strip(), c
        assert any(c in cmds for title, cmds in H.CATEGORIES if title == "🕹 Games"), c        # listed with the other games, exactly once
        assert sum(c in cmds for _, cmds in H.CATEGORIES) == 1, c
    assert {"tap", "crash"} <= set(H.MENU) and not {"jointap", "joincrash"} & set(H.MENU)        # the '/' menu shows the two openers
    assert "end" in H.DESC and "Tap" in H.DESC["end"] and "WordSeek" in H.DESC["end"]
    dm, grp = H.menu("Bot", "*", False), H.menu("Bot", "*", True)
    assert 20 <= len(dm) <= 40 and len(grp) <= 100
    assert {c for c, _ in H.menu("Bot", "*", False)} >= {"tap", "crash"}


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
    names = sorted(sum(_commands_of("handlers/speedgames.py"), []))
    assert names == ["crash", "end", "joincrash", "jointap", "tap"]


def test_router_wiring_keeps_bot_py_untouched_and_end_before_wordseek():
    arcade = _read("handlers/arcade.py")
    assert "from handlers import speedgames as speed_h" in arcade and arcade.count("include_router(speed_h.router)") == 1
    assert "ar:tap" in arcade and "ar:cr" in arcade and "speed_h.do_tap_info" in arcade and "speed_h.do_crash_info" in arcade
    assert "speedgames" not in _read("bot.py")                                   # nothing to register there: the arcade router carries it
    bot = _read("bot.py")
    line = next(l for l in bot.splitlines() if l.strip().startswith("routers = ["))
    assert line.index("arcade_h.router") < line.index("wordseek_h.router") < line.index("ai_chat.router")   # our /end (only while a speed game runs) beats WordSeek's
    src = _read("handlers/speedgames.py")
    assert "RunningSpeedGame()" in src and "outer_middleware(CrashAnswerMiddleware())" in src


def test_no_new_tables_and_no_forbidden_files_touched():
    for path in ("handlers/speedgames.py", "services/speedgames.py", "game/speedgames.py"):
        src = _read(path)
        assert "__tablename__" not in src and "Base)" not in src, path
    assert re.search(r"economy\.apply\(", _read("services/speedgames.py"))       # diamonds only move through economy.apply


# ------------------------------------------------------------------ settlement (real SQLite)
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


def _game(kind="tap", amount=100, players=(1, 2, 3)):
    from services import speedgames as svc
    g = svc.Game(kind=kind, chat_id=-100, host=players[0], amount=amount)
    for u in players:
        g.players[u] = f"User{u}"
    return g


def test_settle_moves_diamonds_burns_the_fee_and_records_stats():
    async def go():
        from services import realm, speedgames as svc
        db = await _setup()
        got, burned = await svc.settle(_game(), 2)
        assert (got, burned) == (190, 10)
        assert [await _bal(u) for u in (1, 2, 3)] == [900, 1190, 900]          # 20 left the losers, 10 of them were burned
        st = {u: await realm.stats_of(u) for u in (1, 2, 3)}
        assert st[2]["tap_win"] == 1 and all(st[u]["tap_play"] == 1 for u in (1, 2, 3)) and "tap_win" not in st[1]
        await db.dispose()
    run(go())


def test_settle_without_winner_moves_nothing_and_a_crash_game_uses_its_own_counters():
    async def go():
        from services import realm, speedgames as svc
        db = await _setup()
        assert await svc.settle(_game("crash"), None) == (0, 0)
        assert [await _bal(u) for u in (1, 2, 3)] == [1000, 1000, 1000]
        st = await realm.stats_of(1)
        assert st["crash_play"] == 1 and "crash_win" not in st and "tap_play" not in st
        await db.dispose()
    run(go())


def test_poor_loser_pays_only_what_he_has():
    async def go():
        from database.session import db as _db
        from services import economy, speedgames as svc
        db = await _setup()
        async with _db.tx() as s:
            await economy.apply(s, 3, wallet=-960, kind="test_spend", ref="spend:3")      # user 3 has 40 left
        got, burned = await svc.settle(_game(), 1)
        assert (got, burned) == (95 + 38, 5 + 2)
        assert [await _bal(u) for u in (1, 2, 3)] == [1000 + 133, 900, 0]
        await db.dispose()
    run(go())


def test_settling_the_same_game_twice_is_refused_and_changes_nothing():
    async def go():
        from services import speedgames as svc
        from services.errors import GameError
        import pytest
        db = await _setup()
        g = _game()
        await svc.settle(g, 2)
        before = [await _bal(u) for u in (1, 2, 3)]
        with pytest.raises(GameError):
            await svc.settle(g, 2)                                                       # same gid -> same idempotency refs
        assert [await _bal(u) for u in (1, 2, 3)] == before
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
        with pytest.raises(GameError):
            await svc.check_funds(1, 51, "User1")
        await svc.claim_start_cd(1)
        with pytest.raises(OnCooldown):
            await svc.claim_start_cd(1)
        await svc.claim_start_cd(2)                                                       # cooldowns are per player
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
        assert svc.get(-555) is g and list(g.players) == [1]
        with pytest.raises(GameError):
            svc.new("crash", -555, 2, "User2", 100)
        svc.drop(g)
        assert svc.get(-555) is None and g.phase == "done"
        g2 = svc.new("crash", -555, 2, "User2", 100)
        assert svc.get(-555) is g2
        svc.drop(g2)
    run(go())
