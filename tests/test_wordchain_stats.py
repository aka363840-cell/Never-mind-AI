"""Word Chain stats (/wcstats, /wclb) against a real SQLite database, like tests/test_regressions.py.
Needs the packages from requirements*.txt.  Run: pytest tests/test_wordchain_stats.py"""
import asyncio
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(tempfile.gettempdir(), "wordchain_stats_test.db")
URL = f"sqlite+aiosqlite:///{DB}"          # module-local: test files must not share one database


def run(coro):
    return asyncio.run(coro)


async def _setup():
    from database.session import db
    from services import users, wordchain_stats as st
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    st._ready = False                      # the table is (re)created lazily on first use - exercise that path too
    for uid, name in ((1, "Ann"), (2, "Bob"), (3, "Cy")):
        await users.touch(uid, name.lower(), name)
    return db, st


def _game(chat_id, winner_uid, rows, mode="classic"):
    """rows = {uid: [words]} -> a finished services.wordchain.Game with those words in play order."""
    from services import wordchain as wc
    g = wc.Game(chat_id, min(rows), mode=mode)
    for uid, words in rows.items():
        g.players[uid] = wc.Player(uid, f"P{uid}", alive=(uid == winner_uid), words=len(words), letters=sum(len(w) for w in words))
    for i in range(max(len(w) for w in rows.values())):
        for uid, words in rows.items():
            if i < len(words):
                g.log.append((uid, words[i]))
    return g, (g.players[winner_uid] if winner_uid else None)


def test_record_and_stats_and_boards():
    async def go():
        db, st = await _setup()
        g, w = _game(10, 1, {1: ["apple", "elephant"], 2: ["tiger", "rabbit"]})
        assert await st.record_game(g, w) == 2
        g, w = _game(10, 1, {1: ["cat", "table"], 2: ["eagle"]})
        await st.record_game(g, w)
        g, w = _game(20, 2, {1: ["dog"], 2: ["goat", "tomato"], 3: ["oak"]}, "elimination")
        await st.record_game(g, w)
        g, w = _game(10, 0, {1: ["sun"], 2: ["nest"]})                    # a draw: nobody won
        await st.record_game(g, w)
        assert await st.record_game(_game(10, 1, {1: [], 2: []})[0], None) == 0      # nobody said a word: not recorded

        s1 = await st.stats(1, None, "all")
        assert (s1["games"], s1["wins"], s1["words"]) == (4, 2, 6) and s1["longest"] == "elephant" and s1["rank"] == 1
        s1g = await st.stats(1, 10, "all")
        assert (s1g["games"], s1g["wins"]) == (3, 2) and s1g["rank"] == 1
        s2 = await st.stats(2, None, "all")
        assert (s2["games"], s2["wins"]) == (4, 1) and s2["rank"] == 2
        none = await st.stats(99, None, "all")
        assert none["games"] == 0 and none["rank"] is None and none["longest"] == ""

        top = await st.board(None, "all")
        assert [r[0] for r in top][:2] == [1, 2] and top[0][1] == "Ann" and top[0][2:4] == (2, 4)
        grp = await st.board(20, "all")
        assert [r[0] for r in grp][0] == 2 and {r[0] for r in grp} == {1, 2, 3}
        assert await st.board(555, "all") == []
    run(go())


def test_periods_only_count_recent_games():
    async def go():
        from datetime import timedelta
        from database.models import utcnow
        db, st = await _setup()
        g, w = _game(10, 1, {1: ["apple"], 2: ["eagle"]})
        await st.record_game(g, w)
        async with db.tx() as s:                                         # a game from 40 days ago
            s.add(st.WordChainResult(chat_id=10, user_id=1, won=1, words=5, letters=30, longest="wonderful", players=2, at=utcnow() - timedelta(days=40)))
        assert (await st.stats(1, 10, "all"))["games"] == 2
        assert (await st.stats(1, 10, "month"))["games"] == 1
        assert (await st.stats(1, 10, "today"))["games"] == 1
        assert (await st.stats(1, 10, "all"))["longest"] == "wonderful" and (await st.stats(1, 10, "month"))["longest"] == "apple"
        assert (await st.board(10, "week"))[0][2] == 1
    run(go())
