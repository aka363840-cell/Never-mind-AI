"""Word Chain: pure rules + the whole game flow (modes, lobby, turns, timers, eliminations, flee, skip, win) with a fake sender.
Also checks that NO Word Chain command is advertised in Telegram's '/' menu.
Run: pytest tests/test_wordchain.py   (no Telegram, no database)   or   python tests/run_pure.py tests.test_wordchain"""
import ast
import asyncio
import os
import re

from game import wordchain as W
from services import wordchain as wc
from services.errors import GameError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(coro):
    return asyncio.run(coro)


class Chat:
    """Fake chat: records everything the game says."""
    def __init__(self): self.said = []
    async def send(self, text): self.said.append(text)


def _setup(n=3, mode="classic", letter=""):
    wc.GAMES.clear()
    wc.recorder = None
    chat = Chat()
    g = wc.create(1, 10, "Host", mode, letter)
    for i in range(1, n):
        wc.join(g, 10 + i, f"P{i}")
    return g, chat


def _find(letter, n=3, used=(), banned=(), required="", longest=False, max_len=12):
    c = [w for w in W.words() if w[0] == letter and n <= len(w) <= max_len and w not in used
         and (not required or required in w) and not any(b in w for b in banned)]
    assert c, (letter, n)
    return (max(c, key=lambda w: (len(w), w)) if longest else min(c, key=lambda w: (len(w), w)))


def _fast():
    old = W.TIME_SCALE
    W.TIME_SCALE = 0.003
    return old


# ------------------------------------------------------------------------------------------ pure rules

def test_levels_get_harder_in_every_mode():
    for key, mode in W.MODES.items():
        lv = [W.level_for(r, key) for r in range(1, 40)]
        assert all(a.min_len <= b.min_len and a.seconds >= b.seconds for a, b in zip(lv, lv[1:])), key
        assert lv[0].min_len == mode.min_start and lv[-1].min_len == mode.min_cap and lv[-1].seconds == mode.t_floor, key
    c = [W.level_for(r) for r in range(1, 8)]
    assert c[0].min_len == 3 and c[0].seconds == 45 and W.level_for(2) == c[0] and W.level_for(3).min_len == 4      # classic: 2 rounds per level
    assert W.level_for(1, "hard").min_len == 5 and W.level_for(2, "hard").min_len == 6                              # hard: a level every round


def test_check_rules():
    assert W.check("apple", "a", 3, set()) is None
    assert "start with" in W.check("apple", "b", 3, set())
    assert "Too short" in W.check("ant", "a", 5, set())
    assert "already used" in W.check("apple", "a", 3, {"apple"})
    assert "dictionary" in W.check("aqzxv", "a", 3, set())
    assert W.is_candidate("Apple") and not W.is_candidate("two words") and not W.is_candidate("/join") and not W.is_candidate("hi!")


def test_banned_and_required_letters():
    assert W.check("apple", "a", 3, set(), banned={"p"}) and "banned" in W.check("apple", "a", 3, set(), banned={"p"})
    assert W.check("apple", "a", 3, set(), banned={"z"}) is None
    assert "must contain" in W.check("apple", "a", 3, set(), required="z")
    assert W.check("apple", "a", 3, set(), required="l") is None
    assert not W.has_word("p", 3, set(), banned={"p"})                                   # a banned letter can't start a word
    assert W.has_word("a", 3, set(), banned={"q"}, required="e")
    r = W.pick_required("a", 4, set())
    assert r and W.has_word("a", 4, set(), required=r)
    assert len(W.pick_banned(3)) == 3 and 2 <= W.banned_count(2) <= W.banned_count(30) <= 4


def test_dictionary_is_big_and_every_letter_has_words():
    assert len(W.words()) > 80_000
    for c in "abcdefghijklmnopqrstuvwxyz":
        assert W.has_word(c, 3, set()), c
    assert not W.has_word("a", 3, set(w for w in W.words() if w[0] == "a"))             # all used up -> dead end
    assert W.pick_letter(7, set()) in W.START_LETTERS


def test_fixed_letters_are_the_ones_with_enough_words():
    f = W.fixed_letters()
    assert set("abcdefghijklmnoprstuvw") <= set(f) and not set("xyzq") & set(f)


def test_every_mode_has_a_start_command():
    assert set(W.START_COMMANDS.values()) == set(W.MODES)
    assert W.START_COMMANDS["wc"] == "classic" and W.START_COMMANDS["startcfl"] == "chosenfirst" and W.START_COMMANDS["startelim"] == "elimination"


# ------------------------------------------------------------------------------------------ lobby

def test_lobby_rules():
    async def go():
        g, chat = _setup(1)
        for bad in (lambda: wc.create(1, 99, "X"), lambda: wc.join(g, 10, "Host"), lambda: wc.create(2, 1, "X", "nope"),
                    lambda: wc.create(2, 1, "X", "classic", "k"), lambda: wc.create(2, 1, "X", "chosenfirst", "z")):
            try:
                bad(); assert False
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


def test_lobby_text_lists_the_commands_and_the_mode():
    g, _ = _setup(2, "hard")
    t = wc.lobby_text(g)
    for c in ("/join", "/flee", "/extend", "/forcestart", "/end", "Hard", "P1"):
        assert c in t, c


def test_extend_adds_time_but_never_past_the_cap():
    g, _ = _setup(2)
    d0 = g.lobby_deadline
    wc.extend(g)
    assert abs(g.lobby_deadline - (d0 + W.LOBBY_EXTEND_S)) < 1
    wc.extend(g, 45)
    assert abs(g.lobby_deadline - (d0 + W.LOBBY_EXTEND_S + 45)) < 1
    try:
        for _ in range(30):
            wc.extend(g, 600)
        assert False, "the cap never kicked in"
    except GameError:
        assert g.lobby_deadline <= g.created + W.LOBBY_MAX_S + 0.01
    g.state = "playing"
    try:
        wc.extend(g); assert False
    except GameError: pass


def test_extend_keeps_the_lobby_alive_longer():
    async def go():
        g, chat = _setup(2)
        old = W.LOBBY_S
        W.LOBBY_S = 0.1
        g.lobby_deadline = __import__("time").time() + 0.1
        try:
            wc.start_lobby_timer(g, chat.send)
            g.lobby_deadline += 0.4                               # what /extend does
            await asyncio.sleep(0.25)
            assert g.state == "lobby" and 1 in wc.GAMES           # old deadline passed, the lobby is still open
            await asyncio.sleep(0.5)
            assert g.state == "playing"                           # new deadline reached -> auto start
            await wc.end(g, chat.send)
        finally:
            W.LOBBY_S = old
    run(go())


def test_flee_in_lobby_moves_the_host_and_closes_an_empty_lobby():
    async def go():
        g, chat = _setup(3)
        assert "new host" in await wc.leave(g, 10, chat.send) and g.host_id == 11 and len(g.players) == 2
        try:
            await wc.leave(g, 99, chat.send); assert False
        except GameError: pass
        await wc.leave(g, 11, chat.send)
        assert "closed" in await wc.leave(g, 12, chat.send) and 1 not in wc.GAMES
    run(go())


def test_end_in_lobby_and_auto_cancel():
    async def go():
        g, chat = _setup(1)
        g.lobby_deadline = __import__("time").time() + 0.05
        wc.start_lobby_timer(g, chat.send)
        await asyncio.sleep(0.3)
        assert 1 not in wc.GAMES and "cancelled" in chat.said[-1]
    run(go())


# ------------------------------------------------------------------------------------------ classic

def test_full_game_turns_errors_timeouts_and_win():
    async def go():
        g, chat = _setup(3)
        old = _fast()
        try:
            await wc.start(g, chat.send)
            first = g.current
            other = next(p for p in g.players.values() if p is not first)
            assert (await wc.submit(g, other.uid, "apple", chat.send)).handled is False       # not your turn = normal chat
            bad = await wc.submit(g, first.uid, "zzzzz" if g.letter != "z" else "aaaaa", chat.send)
            assert bad.handled and bad.error and g.current is first                           # refused, keeps the turn
            w1 = _find(g.letter)
            mv = await wc.submit(g, first.uid, w1.upper(), chat.send)
            assert mv.handled and mv.ok and mv.error is None and g.letter == w1[-1] and g.current is not first
            assert "✅" in chat.said[-1] and w1.upper() in chat.said[-1]                      # no reaction given -> the word is echoed
            assert "next:" in chat.said[-1] and "Players remaining" in chat.said[-1] and "Total words" in chat.said[-1]
            assert "already used" in (await wc.submit(g, g.current.uid, w1, chat.send)).error or g.letter != w1[0]
            await asyncio.sleep(0.7)                                                          # nobody answers: players drop one by one
            assert 1 not in wc.GAMES, "game should be over"
            assert any("wins Word Chain" in t for t in chat.said)
            assert sum(1 for t in chat.said if "eliminated" in t) >= 2
            assert "🥇" in chat.said[-1] and "Longest word" in chat.said[-1]
        finally:
            W.TIME_SCALE = old
    run(go())


def test_a_reaction_replaces_the_echo_line():
    async def go():
        g, chat = _setup(2)
        await wc.start(g, chat.send)
        reacted = []

        async def react():
            reacted.append(1); return True
        w = _find(g.letter)
        mv = await wc.submit(g, g.current.uid, w, chat.send, react)
        assert mv.ok and reacted and "✅" not in chat.said[-1] and "🎯" in chat.said[-1]
        async def broken():
            raise RuntimeError("no right to react")
        w2 = _find(g.letter, used=g.used)
        await wc.submit(g, g.current.uid, w2, chat.send, broken)                              # a failing reaction falls back to the echo
        assert "✅" in chat.said[-1] and w2.upper() in chat.said[-1]
        await wc.end(g, chat.send)
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


# ------------------------------------------------------------------------------------------ other modes

def test_chosen_first_letter_every_word_starts_with_it():
    async def go():
        g, chat = _setup(2, "chosenfirst", "k")
        await wc.start(g, chat.send)
        assert g.letter == "k" and "Every word starts with <b>K</b>" in chat.said[0]
        p = g.current
        assert (await wc.submit(g, p.uid, _find("a"), chat.send)).error                       # wrong first letter
        w = _find("k", 4)
        assert (await wc.submit(g, p.uid, w, chat.send)).ok
        assert g.letter == "k" and g.current is not p                                         # the letter does NOT follow the word
        assert (await wc.submit(g, g.current.uid, _find("k", 4, used=g.used), chat.send)).ok
        await wc.end(g, chat.send)
        g2, _ = _setup(2, "chosenfirst")                                                      # no letter given -> a random good one
        await wc.start(g2, chat.send)
        assert g2.fixed in W.START_LETTERS and g2.letter == g2.fixed
        await wc.end(g2, chat.send)
    run(go())


def test_banned_letters_game():
    async def go():
        g, chat = _setup(3, "banned")
        await wc.start(g, chat.send)
        assert len(g.banned) >= 2 and "Banned letters" in chat.said[0] and g.letter not in g.banned
        p = g.current
        bad = _find(g.letter, 3, banned=(), required=next(iter(g.banned)))                   # contains a banned letter
        err = (await wc.submit(g, p.uid, bad, chat.send)).error
        assert err and "banned" in err and g.current is p
        ok = _find(g.letter, 3, banned=g.banned)
        assert (await wc.submit(g, p.uid, ok, chat.send)).ok
        assert g.letter not in g.banned
        await wc.end(g, chat.send)
    run(go())


def test_required_letter_game():
    async def go():
        g, chat = _setup(2, "required")
        await wc.start(g, chat.send)
        assert g.required and "contain" in chat.said[0] and g.required.upper() in chat.said[0]
        p, req = g.current, g.required
        no = next(w for w in sorted(W.words()) if w[0] == g.letter and len(w) >= 3 and req not in w)
        err = (await wc.submit(g, p.uid, no, chat.send)).error
        assert err and "must contain" in err and g.current is p
        yes = _find(g.letter, 3, required=req)
        assert (await wc.submit(g, p.uid, yes, chat.send)).ok and g.required                  # a new required letter for the next turn
        await wc.end(g, chat.send)
    run(go())


def test_hard_mode_starts_at_five_letters():
    async def go():
        g, chat = _setup(2, "hard")
        await wc.start(g, chat.send)
        assert "5</b> letters" in chat.said[0] and "30s" in chat.said[0]
        assert "Too short" in (await wc.submit(g, g.current.uid, _find(g.letter, 3, max_len=4), chat.send)).error
        await wc.end(g, chat.send)
    run(go())


def test_elimination_lowest_score_of_each_round_is_out():
    async def go():
        g, chat = _setup(3, "elimination")
        old = _fast()
        try:
            await wc.start(g, chat.send)
            first, second, third = (g.players[u] for u in g.order)
            plan = {first.uid: dict(longest=True, max_len=9), second.uid: dict(max_len=3), third.uid: dict(longest=True, max_len=5)}
            for p in (first, second, third):                                                  # round 1: 9+ / 3 / 5 letters
                assert g.current is p
                w = _find(g.letter, 3, used=g.used, **plan[p.uid])
                assert (await wc.submit(g, p.uid, w, chat.send)).ok, w
            assert not second.alive and first.alive and third.alive and g.round == 2
            assert any("Round 1 results" in t and "Out:" in t for t in chat.said)
            # round 2: one player answers, the other one times out (0 points) -> the one who timed out is out
            a = g.current
            assert (await wc.submit(g, a.uid, _find(g.letter, 4, used=g.used), chat.send)).ok
            await asyncio.sleep(0.6)
            assert 1 not in wc.GAMES and any("wins Word Chain" in t for t in chat.said)
            assert any("ran out of time" in t and "0</b> points" in t for t in chat.said)
        finally:
            W.TIME_SCALE = old
    run(go())


def test_elimination_nobody_scores_calls_it_a_draw_and_ties_dont_eliminate():
    async def go():
        g, chat = _setup(2, "elimination")
        await wc.start(g, chat.send)
        g.round_scores = {}
        async with g.lock:
            await wc._next_turn(g, chat.send); await wc._next_turn(g, chat.send)             # a whole round of nobody answering
        assert 1 not in wc.GAMES and "Nobody scored" in "".join(chat.said)
        g, chat = _setup(2, "elimination")
        await wc.start(g, chat.send)
        for p in list(g.players.values()):
            g.round_scores[p.uid] = 6                                                         # everybody tied
        g.idx = len(g.order) - 1
        async with g.lock:
            await wc._next_turn(g, chat.send)
        assert len(g.alive) == 2 and "Everyone tied" in "".join(chat.said)
        await wc.end(g, chat.send)
    run(go())


# ------------------------------------------------------------------------------------------ flee / skip / stats hook

def test_flee_in_game_current_and_not_current_then_the_last_one_wins():
    async def go():
        g, chat = _setup(3)
        saved = []

        async def rec(game, winner): saved.append((game.mode, winner.uid if winner else None, len(game.log)))
        wc.recorder = rec
        await wc.start(g, chat.send)
        assert (await wc.submit(g, g.current.uid, _find(g.letter), chat.send)).ok            # at least one word, so the game counts
        cur = g.current
        await wc.leave(g, cur.uid, chat.send)                                                 # the player on turn gives up
        assert not cur.alive and g.current is not cur and len(g.alive) == 2 and "fled" in chat.said[-2] + chat.said[-1]
        other = next(p for p in g.alive if p is not g.current)
        await wc.leave(g, other.uid, chat.send)                                               # somebody who is NOT on turn gives up
        assert 1 not in wc.GAMES and "wins Word Chain" in chat.said[-1]
        assert saved == [("classic", g.alive[0].uid, 1)]
        try:
            await wc.leave(g, cur.uid, chat.send); assert False
        except GameError: pass
    run(go())


def test_forceskip_knocks_out_the_current_player():
    async def go():
        g, chat = _setup(2)
        await wc.start(g, chat.send)
        p = g.current
        await wc.skip(g, chat.send, "Admin")
        assert not p.alive and 1 not in wc.GAMES and "skipped by Admin" in "".join(chat.said)
        try:
            await wc.skip(g, chat.send); assert False
        except GameError: pass
    run(go())


def test_aborted_games_are_not_recorded_but_finished_ones_are():
    async def go():
        g, chat = _setup(2)
        saved = []

        async def rec(game, winner): saved.append(winner)
        wc.recorder = rec
        await wc.start(g, chat.send)
        await wc.end(g, chat.send, "Host")
        assert saved == []
        g, chat = _setup(2)
        wc.recorder = rec
        await wc.start(g, chat.send)
        await wc.skip(g, chat.send)
        assert len(saved) == 1 and saved[0] is not None
        async def boom(game, winner): raise RuntimeError("db down")
        g, chat = _setup(2)
        wc.recorder = boom
        await wc.start(g, chat.send)
        await wc.skip(g, chat.send)                                                           # a failing recorder must never break the game
        assert 1 not in wc.GAMES and "wins Word Chain" in chat.said[-1]
        wc.recorder = None
    run(go())


def test_status_text():
    async def go():
        g, chat = _setup(3, "banned")
        assert "Lobby open" in wc.status_text(g)
        await wc.start(g, chat.send)
        t = wc.status_text(g)
        assert "Turn:" in t and "Banned:" in t and "Alive (3/3)" in t
        await wc.end(g, chat.send)
    run(go())


# ------------------------------------------------------------------------------------------ the '/' menu

def _handler_commands():
    names = []
    for n in ast.walk(ast.parse(open(os.path.join(ROOT, "handlers", "wordchain.py")).read())):
        if isinstance(n, ast.AsyncFunctionDef):
            for d in n.decorator_list:
                if (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "message" and d.args
                        and isinstance(d.args[0], ast.Call) and getattr(d.args[0].func, "id", "") == "Command"):
                    names += [a.value for a in d.args[0].args if isinstance(a, ast.Constant)]
    return names


def test_no_wordchain_command_is_in_the_slash_menu_but_all_are_in_help():
    from utils import helpcatalog as H
    cmds = set(_handler_commands())
    assert {"startclassic", "wc", "starthard", "startcfl", "startbl", "startrl", "startelim", "join", "flee", "extend", "forcestart",
            "forceskip", "forcejoin", "wcstats", "wclb", "wchelp", "wcmodes", "wcstatus", "end"} <= cmds
    assert set(W.START_COMMANDS) <= cmds                                                        # every mode's command really has a handler
    shown = {c for c, _ in H.menu("Bot", "*", False)} | {c for c, _ in H.menu("Bot", "*", True)} | set(H.MENU) | set(H.GROUP_MENU_EXTRA)
    assert not (cmds - {"end"}) & shown, sorted((cmds - {"end"}) & shown)                       # (/end is old and not in the menu either)
    assert "end" not in shown
    listed = {c for c, _, _ in H.entries("Bot", "*")} | {a for _, _, al in H.entries("Bot", "*") for a in al}
    assert cmds <= listed, sorted(cmds - listed)                                                # ... but /help lists every single one
    for c in W.START_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", c)
