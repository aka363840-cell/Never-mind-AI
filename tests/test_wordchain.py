"""Word Chain: pure rules + the whole game flow (lobby, turns, timers, eliminations, win) with a fake sender.
Run: pytest tests/test_wordchain.py   (no Telegram, no database)"""
import asyncio

from game import wordchain as W
from services import wordchain as wc
from services.errors import GameError


def run(coro):
    return asyncio.run(coro)


def test_levels_get_harder():
    lv = [W.level_for(r) for r in range(1, 30)]
    assert lv[0].min_len == 3 and lv[0].seconds == 45 and W.level_for(2) == lv[0]       # 2 rounds per level
    assert all(a.min_len <= b.min_len and a.seconds >= b.seconds for a, b in zip(lv, lv[1:]))
    assert lv[-1].min_len == W.MIN_LEN_CAP and lv[-1].seconds == W.TIME_FLOOR


def test_check_rules():
    assert W.check("apple", "a", 3, set()) is None
    assert "start with" in W.check("apple", "b", 3, set())
    assert "Too short" in W.check("ant", "a", 5, set())
    assert "already used" in W.check("apple", "a", 3, {"apple"})
    assert "dictionary" in W.check("aqzxv", "a", 3, set())
    assert W.is_candidate("Apple") and not W.is_candidate("two words") and not W.is_candidate("/join") and not W.is_candidate("hi!")


def test_dictionary_is_big_and_every_letter_has_words():
    assert len(W.words()) > 80_000
    for c in "abcdefghijklmnopqrstuvwxyz":
        assert W.has_word(c, 3, set()), c
    assert not W.has_word("a", 3, set(w for w in W.words() if w[0] == "a"))             # all used up -> dead end
    assert W.pick_letter(7, set()) in W.START_LETTERS


class Chat:
    """Fake chat: records everything the game says."""
    def __init__(self): self.said = []
    async def send(self, text): self.said.append(text)


def _setup(n=3):
    wc.GAMES.clear()
    chat = Chat()
    g = wc.create(1, 10, "Host")
    for i in range(1, n):
        wc.join(g, 10 + i, f"P{i}")
    return g, chat


def _word(letter, used, n=3):
    return next(w for w in sorted(W.words()) if w[0] == letter and len(w) >= n and w.isalpha() and w not in used)


def test_lobby_rules():
    async def go():
        g, chat = _setup(1)
        try:
            wc.create(1, 99, "X"); assert False
        except GameError: pass
        try:
            wc.join(g, 10, "Host"); assert False
        except GameError: pass
        try:
            await wc.start(g, chat.send); assert False          # one player is not enough
        except GameError: pass
        wc.join(g, 11, "P1")
        await wc.start(g, chat.send)
        assert g.state == "playing" and len(g.order) == 2 and "Word Chain starts" in chat.said[0]
        try:
            wc.join(g, 12, "Late"); assert False
        except GameError: pass
        await wc.end(g, chat.send, "Host")
        assert 1 not in wc.GAMES and chat.said[-1].startswith("🛑")
    run(go())


def test_full_game_turns_errors_timeouts_and_win(monkeypatch=None):
    async def go():
        g, chat = _setup(3)
        old = W.TIME_START, W.TIME_FLOOR
        W.TIME_START = W.TIME_FLOOR = 0.15                       # fast clock for the test
        try:
            await wc.start(g, chat.send)
            first = g.current
            # a message from somebody else is not a move
            other = next(p for p in g.players.values() if p is not first)
            assert (await wc.submit(g, other.uid, "apple", chat.send)).handled is False
            # wrong first letter / too short / not a word are refused, the player keeps the turn
            bad = await wc.submit(g, first.uid, "zzzzz" if g.letter != "z" else "aaaaa", chat.send)
            assert bad.handled and bad.error and g.current is first
            # a good word moves the chain on, the next word must start with its last letter
            w1 = _word(g.letter, g.used)
            mv = await wc.submit(g, first.uid, w1.upper(), chat.send)
            assert mv.handled and mv.error is None and g.letter == w1[-1] and g.current is not first
            # the same word again is refused (even by the right player, right letter)
            assert "already used" in (await wc.submit(g, g.current.uid, w1, chat.send)).error or g.letter != w1[0]
            # now nobody answers: players drop one by one until a single winner is left
            await asyncio.sleep(0.7)
            assert 1 not in wc.GAMES, "game should be over"
            assert any("wins Word Chain" in t for t in chat.said)
            assert sum(1 for t in chat.said if "eliminated" in t) >= 2
            assert "🥇" in chat.said[-1] and w1.upper() not in "" and "Longest word" in chat.said[-1]
        finally:
            W.TIME_START, W.TIME_FLOOR = old
    run(go())


def test_rounds_raise_the_level_and_dead_ends_restart_the_chain():
    async def go():
        g, chat = _setup(2)
        await wc.start(g, chat.send)
        g.round = 3                                              # level 2 (4+ letters)
        g.level_n = 1
        g.letter = "a"
        g.used |= {w for w in W.words() if w[0] == "a"}          # nothing left for 'a' -> dead end
        async with g.lock:
            await wc._begin_turn(g, chat.send)
        assert "Level 2" in chat.said[-1] and "No words left" in chat.said[-1] and g.letter != "a"
        await wc.end(g, chat.send)
    run(go())


def test_end_in_lobby_and_auto_cancel():
    async def go():
        g, chat = _setup(1)
        old = W.LOBBY_S; W.LOBBY_S = 0.05
        try:
            wc.start_lobby_timer(g, chat.send)
            await asyncio.sleep(0.3)
            assert 1 not in wc.GAMES and "cancelled" in chat.said[-1]
        finally:
            W.LOBBY_S = old
    run(go())
