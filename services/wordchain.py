"""Word Chain service: lobbies, turns, timers, eliminations (in memory - a game lasts minutes, a redeploy simply ends it).

No Telegram imports here: everything the game wants to say goes through a `send(text)` coroutine the handler passes in,
so the whole game can be tested with a fake sender.  Rules, modes and word list: game/wordchain.py.
Finished games are handed to `recorder` (set by handlers/wordchain.py -> services/wordchain_stats.py) so wins / words /
longest word are remembered for /wcstats and /wclb.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from game import wordchain as W
from services.errors import GameError
from utils.fmt import esc, mention

log = logging.getLogger(__name__)
Send = Callable[[str], Awaitable[Any]]
React = Callable[[], Awaitable[bool]]


@dataclass
class Player:
    uid: int
    name: str
    alive: bool = True
    words: int = 0
    letters: int = 0


@dataclass
class Game:
    chat_id: int
    host_id: int
    mode: str = W.DEFAULT_MODE
    state: str = "lobby"                                        # lobby -> playing
    players: dict[int, Player] = field(default_factory=dict)    # join order
    order: list[int] = field(default_factory=list)              # turn order (shuffled at start)
    idx: int = 0                                                # whose turn, as an index into order
    round: int = 1
    level_n: int = 0
    letter: str = ""                                            # letter the next word must start with
    fixed: str = ""                                             # 'chosen first letter' mode: the letter of EVERY word
    banned: frozenset = frozenset()                             # 'banned letters' mode
    required: str = ""                                          # 'required letter' mode: this turn's must-have letter
    round_scores: dict[int, int] = field(default_factory=dict)  # elimination mode: uid -> points this round
    last_word: str = ""
    used: set[str] = field(default_factory=set)
    log: list[tuple[int, str]] = field(default_factory=list)    # (uid, word) in play order
    out: list[tuple[int, str]] = field(default_factory=list)    # (uid, why) in elimination order
    token: int = 0                                              # bumps on every turn so stale timers do nothing
    deadline: float = 0.0
    timer: asyncio.Task | None = None
    lobby_task: asyncio.Task | None = None
    lobby_deadline: float = 0.0
    lobby_msg: int | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created: float = field(default_factory=time.time)
    started: float = 0.0

    @property
    def spec(self) -> W.Mode:
        return W.MODES[self.mode]

    @property
    def current(self) -> Player | None:
        return self.players.get(self.order[self.idx]) if self.order else None

    @property
    def alive(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]


GAMES: dict[int, Game] = {}                                     # chat id -> game (lobby or running)
recorder: Callable[[Game, "Player | None"], Awaitable[Any]] | None = None     # set by the handler: saves a finished game


@dataclass
class Move:
    handled: bool                                               # False = not this player's turn: treat as normal chat
    error: str | None = None                                    # why the word was refused (the player may try again)
    ok: bool = False                                            # the word was accepted


# ----------------------------------------------------------------------------------------------- lobby

def create(chat_id: int, host_id: int, host_name: str, mode: str = W.DEFAULT_MODE, letter: str = "") -> Game:
    if chat_id in GAMES:
        raise GameError("🎮 A Word Chain game is already " + ("running" if GAMES[chat_id].state == "playing" else "open") + " here. /end stops it.")
    if mode not in W.MODES:
        raise GameError("❓ Unknown mode. See /wcmodes")
    spec = W.MODES[mode]
    letter = letter.strip().lower()
    if letter:
        if not spec.fixed_first:
            raise GameError(f"ℹ️ {spec.name} mode has no starting letter option.")
        if len(letter) != 1 or letter not in W.fixed_letters():
            raise GameError("🔤 Pick one of these letters: " + " ".join(c.upper() for c in W.fixed_letters()))
    g = Game(chat_id, host_id, mode=mode, fixed=letter)
    g.players[host_id] = Player(host_id, host_name[:30])
    g.lobby_deadline = time.time() + W.LOBBY_S
    GAMES[chat_id] = g
    return g


def join(g: Game, uid: int, name: str) -> int:
    """Add a player to the lobby -> number of players. Raises GameError with a friendly reason."""
    if g.state != "lobby":
        raise GameError("⏳ The game already started - join the next one!")
    if uid in g.players:
        raise GameError("✅ You're already in!")
    if len(g.players) >= W.MAX_PLAYERS:
        raise GameError(f"👥 The lobby is full ({W.MAX_PLAYERS} players).")
    g.players[uid] = Player(uid, name[:30])
    return len(g.players)


def lobby_left(g: Game) -> int:
    return max(0, int(round(g.lobby_deadline - time.time())))


def extend(g: Game, seconds: int = 0) -> int:
    """Give the lobby more time -> seconds left. The lobby never lives longer than W.LOBBY_MAX_S in total."""
    if g.state != "lobby":
        raise GameError("ℹ️ /extend only works while the lobby is open.")
    seconds = seconds if seconds > 0 else W.LOBBY_EXTEND_S
    seconds = min(seconds, 600)
    cap = g.created + W.LOBBY_MAX_S
    new = min(g.lobby_deadline + seconds, cap)
    if new <= g.lobby_deadline + 0.5:
        raise GameError(f"⏳ The lobby can't be extended any more (max {W.LOBBY_MAX_S // 60} minutes in total). Use /forcestart.")
    g.lobby_deadline = new
    return lobby_left(g)


def rules_line(g: Game) -> str:
    s = g.spec
    return f"{s.emoji} <b>{s.name}</b> - {s.blurb}"


def lobby_text(g: Game) -> str:
    names = "\n".join(f"• {esc(p.name)}" for p in g.players.values())
    extra = f"\n🔤 Every word starts with <b>{g.fixed.upper()}</b>" if g.fixed else ""
    return (f"⛓ <b>Word Chain</b> - lobby open!\n\n{rules_line(g)}{extra}\n\n"
            f"👥 <b>Players ({len(g.players)}/{W.MAX_PLAYERS})</b>\n{names}\n\n"
            f"⏳ Starts in about <b>{lobby_left(g)}s</b> (min {W.MIN_PLAYERS} players)\n"
            f"/join · /flee · /extend · /forcestart · /end")


def start_lobby_timer(g: Game, send: Send) -> None:
    g.lobby_task = asyncio.create_task(_lobby_timer(g, send))


async def _lobby_timer(g: Game, send: Send) -> None:
    try:
        while True:
            left = g.lobby_deadline - time.time()
            if left <= 0:
                break
            await asyncio.sleep(left)                            # /extend only moves the deadline; we re-check after waking
    except asyncio.CancelledError:
        return
    if GAMES.get(g.chat_id) is not g or g.state != "lobby":
        return
    if len(g.players) < W.MIN_PLAYERS:
        _drop(g)
        await _say(send, "⛓ Word Chain cancelled - not enough players joined. Start again with /wc")
        return
    try:
        await start(g, send)
    except GameError:
        pass                                                     # somebody used /forcestart at the same moment


async def start(g: Game, send: Send) -> None:
    """Close the lobby and play the first turn. Raises GameError when it can't start."""
    async with g.lock:
        if g.state != "lobby":
            raise GameError("🎮 The game already started.")
        if len(g.players) < W.MIN_PLAYERS:
            raise GameError(f"👥 Need at least {W.MIN_PLAYERS} players - ask a friend to /join !")
        if g.lobby_task and g.lobby_task is not asyncio.current_task():
            g.lobby_task.cancel()
        g.state = "playing"
        g.started = time.time()
        g.order = list(g.players)
        random.shuffle(g.order)
        g.idx, g.round = 0, 1
        spec = g.spec
        lv = W.level_for(1, spec)
        if spec.banned:
            g.banned = W.pick_banned(W.banned_count(len(g.players)))
        if spec.fixed_first:
            g.fixed = g.fixed or W.pick_letter(lv.min_len, g.used, banned=g.banned)
            g.letter = g.fixed
        else:
            g.letter = W.pick_letter(lv.min_len, g.used, banned=g.banned)
        who = " → ".join(esc(g.players[u].name) for u in g.order)
        await _begin_turn(g, send, f"⛓ <b>Word Chain starts!</b> {len(g.order)} players\n{rules_line(g)}\n🔀 Order: {who}\n\n")


# ------------------------------------------------------------------------------------------------ turns

async def _say(send: Send, text: str) -> None:
    try:
        await send(text)
    except Exception:
        log.exception("wordchain: could not send a message")


def _cancel_timer(g: Game) -> None:
    if g.timer and g.timer is not asyncio.current_task():
        g.timer.cancel()
    g.timer = None


def _advance(g: Game) -> bool:
    """Move to the next living player; a new round starts whenever the order wraps around (-> True)."""
    n = len(g.order)
    wrapped = False
    for _ in range(n):
        g.idx += 1
        if g.idx >= n:
            g.idx = 0
            g.round += 1
            wrapped = True
        if g.players[g.order[g.idx]].alive:
            return wrapped
    return wrapped


def _peek_next(g: Game) -> Player | None:
    """The player after the current one (for the '(next: X)' hint)."""
    n = len(g.order)
    for step in range(1, n):
        p = g.players[g.order[(g.idx + step) % n]]
        if p.alive:
            return p
    return None


def _round_end(g: Game) -> tuple[str, str]:
    """Elimination mode: a round just finished. Knocks out the lowest score(s) -> (text, '' | 'draw')."""
    alive = g.alive
    scores = {p.uid: g.round_scores.get(p.uid, 0) for p in alive}
    n = g.round - 1
    table = " · ".join(f"{esc(p.name)} <b>{scores[p.uid]}</b>" for p in sorted(alive, key=lambda p: -scores[p.uid])[:10])
    g.round_scores = {}
    if not scores or max(scores.values()) == 0:
        return f"📊 <b>Round {n} results</b>\n{table}\n😴 Nobody scored - the game is called off.\n\n", "draw"
    low = min(scores.values())
    losers = [p for p in alive if scores[p.uid] == low]
    if len(losers) == len(alive):
        return f"📊 <b>Round {n} results</b>\n{table}\n🤝 Everyone tied - nobody is out.\n\n", ""
    for p in losers:
        p.alive = False
        g.out.append((p.uid, f"scored the lowest ({low})"))
    return (f"📊 <b>Round {n} results</b>\n{table}\n💀 Out: <b>{', '.join(esc(p.name) for p in losers)}</b> (lowest score: {low})\n\n"), ""


async def _next_turn(g: Game, send: Send, prefix: str = "") -> None:
    """Hand the turn on (round-end check in elimination mode) and start the next clock. Caller holds g.lock."""
    wrapped = _advance(g)
    if wrapped and g.spec.elimination:
        text, verdict = _round_end(g)
        left = g.alive
        if verdict == "draw" or len(left) <= 1:
            await _finish(g, send, None if verdict == "draw" else (left[0] if left else None), prefix + text)
            return
        prefix += text
        if not g.current or not g.current.alive:
            _advance(g)
    await _begin_turn(g, send, prefix)


async def _begin_turn(g: Game, send: Send, prefix: str = "") -> None:
    """Announce whose turn it is and start that player's clock. Caller holds g.lock."""
    spec = g.spec
    lv = W.level_for(g.round, spec)
    if lv.n != g.level_n:
        if g.level_n:
            prefix += f"🔥 <b>Level {lv.n}!</b> Words need <b>{lv.min_len}+</b> letters and you get <b>{lv.seconds:g}s</b> per turn.\n\n"
        g.level_n = lv.n
    told = False
    for _ in range(6):                                           # dead end (e.g. 'x' at level 5): restart the chain
        ok = W.has_word(g.letter, lv.min_len, g.used, g.banned)
        g.required = ""
        if ok and spec.required:
            g.required = W.pick_required(g.letter, lv.min_len, g.used, g.banned)
            ok = bool(g.required)
        if ok:
            break
        if spec.fixed_first:                                     # the letter can't change: the words are simply gone
            await _finish(g, send, None, prefix + f"📭 No words left that start with <b>{g.letter.upper()}</b>!\n\n")
            return
        old = g.letter
        g.letter = W.pick_letter(lv.min_len, g.used, avoid=old, banned=g.banned)
        if not told:
            prefix += f"🔀 No words left that start with <b>{old.upper()}</b> - the chain restarts with a new letter!\n\n"
            told = True
    p = g.current
    assert p is not None
    nxt = _peek_next(g)
    g.token += 1
    _cancel_timer(g)
    g.deadline = time.time() + lv.seconds
    must = f"start with <b>{g.letter.upper()}</b>" + (f", contain <b>{g.required.upper()}</b>" if g.required else "") + f" and have at least <b>{lv.min_len}</b> letters"
    lines = [f"{prefix}🎯 {mention(p.uid, p.name)}" + (f" <i>(next: {esc(nxt.name)})</i>" if nxt else ""),
             f"Your word must {must}."]
    if g.banned:
        lines.append("🚫 Banned letters: " + " ".join(c.upper() for c in sorted(g.banned)))
    if spec.fixed_first:
        lines.append(f"🔤 Every word starts with <b>{g.fixed.upper()}</b> in this game.")
    lines.append(f"⏱ <b>{lv.seconds:g}s</b> · round {g.round} · level {lv.n}")
    lines.append(f"👥 Players remaining: <b>{len(g.alive)}/{len(g.players)}</b> · 📝 Total words: <b>{len(g.log)}</b>")
    if spec.elimination and g.round_scores:
        top = sorted(g.round_scores.items(), key=lambda kv: -kv[1])[:8]
        lines.append("📊 This round: " + " · ".join(f"{esc(g.players[u].name)} {s}" for u, s in top))
    await _say(send, "\n".join(lines))
    g.timer = asyncio.create_task(_timeout(g, g.token, lv.seconds, send))


async def _timeout(g: Game, token: int, seconds: float, send: Send) -> None:
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        return
    async with g.lock:
        if g.token != token or g.state != "playing" or GAMES.get(g.chat_id) is not g:
            return                                               # the player answered in time (or the game ended)
        p = g.current
        if p is None:
            return
        if g.spec.elimination:                                   # no word = no points this round, the game goes on
            await _next_turn(g, send, f"⏰ <b>{esc(p.name)}</b> ran out of time - <b>0</b> points this turn.\n\n")
        else:
            await _eliminate(g, p, "ran out of time", send)


async def _eliminate(g: Game, p: Player, why: str, send: Send, tag: str = "⏰") -> None:
    """Knock a player out; finish the game if one is left, else hand the turn on. Caller holds g.lock."""
    was_current = g.current is p
    p.alive = False
    g.out.append((p.uid, why))
    g.round_scores.pop(p.uid, None)
    left = g.alive
    line = f"{tag} <b>{esc(p.name)}</b> {why} - eliminated! 💀 <i>{len(left)} player{'s' if len(left) != 1 else ''} left</i>\n\n"
    if len(left) <= 1:
        await _finish(g, send, left[0] if left else None, line)
        return
    if was_current:
        await _next_turn(g, send, line)
    else:
        await _say(send, line.strip())                           # not their turn: the running clock is not touched


async def submit(g: Game, uid: int, text: str, send: Send, react: React | None = None) -> Move:
    """A chat message from `uid`. Only the player whose turn it is can make a move.
    `react` (optional) puts a 👍 on the player's message; when it works the '✅ WORD' line is left out."""
    async with g.lock:
        p = g.current
        if g.state != "playing" or GAMES.get(g.chat_id) is not g or p is None or p.uid != uid or not p.alive:
            return Move(False)
        word = text.strip().lower()
        lv = W.level_for(g.round, g.spec)
        err = W.check(word, g.letter, lv.min_len, g.used, g.banned, g.required)
        if err:
            return Move(True, "❌ " + err)
        g.used.add(word)
        g.log.append((uid, word))
        g.last_word = word
        p.words += 1
        p.letters += len(word)
        if g.spec.elimination:
            g.round_scores[uid] = g.round_scores.get(uid, 0) + W.points(word)
        if not g.fixed:
            g.letter = word[-1]
        reacted = False
        if react is not None:
            try:
                reacted = bool(await react())
            except Exception:
                reacted = False
        pts = f" <i>(+{len(word)})</i>" if g.spec.elimination else ""
        await _next_turn(g, send, "" if reacted and not pts else f"✅ <b>{word.upper()}</b>{pts}\n")
        return Move(True, ok=True)


# ----------------------------------------------------------------------------- leaving / skipping

async def leave(g: Game, uid: int, send: Send) -> str:
    """/flee: leave the lobby, or give up a running game. Returns what to say (raises GameError if not playing)."""
    async with g.lock:
        if uid not in g.players or (g.state == "playing" and not g.players[uid].alive):
            raise GameError("ℹ️ You're not in this game.")
        p = g.players[uid]
        if g.state == "lobby":
            del g.players[uid]
            if not g.players:
                _drop(g)
                return "🚪 You left - the lobby is empty, so it was closed."
            if g.host_id == uid:
                g.host_id = next(iter(g.players))
                return f"🚪 <b>{esc(p.name)}</b> left the lobby. <b>{esc(g.players[g.host_id].name)}</b> is the new host."
            return f"🚪 <b>{esc(p.name)}</b> left the lobby - {len(g.players)} player{'s' if len(g.players) != 1 else ''} left."
        await _eliminate(g, p, "fled", send, tag="🏃")
        return ""


async def skip(g: Game, send: Send, by: str = "") -> None:
    """/forceskip: the player whose turn it is gets knocked out (admins / host)."""
    async with g.lock:
        if g.state != "playing" or GAMES.get(g.chat_id) is not g or g.current is None:
            raise GameError("ℹ️ There's no running Word Chain turn to skip.")
        await _eliminate(g, g.current, "was skipped" + (f" by {esc(by)}" if by else ""), send, tag="⏭")


def status_text(g: Game) -> str:
    s = g.spec
    if g.state == "lobby":
        return f"⛓ Lobby open ({s.emoji} {s.name}) - {len(g.players)} player{'s' if len(g.players) != 1 else ''}, starts in ~{lobby_left(g)}s."
    lv = W.level_for(g.round, s)
    cur = g.current
    alive = ", ".join(esc(p.name) for p in g.alive)
    last = ", ".join(w.upper() for _, w in g.log[-5:]) or "-"
    left = max(0, int(g.deadline - time.time()))
    return (f"⛓ <b>Word Chain</b> · {s.emoji} {s.name}\n"
            f"🎯 Turn: <b>{esc(cur.name) if cur else '-'}</b> · start with <b>{g.letter.upper()}</b>"
            + (f" · contain <b>{g.required.upper()}</b>" if g.required else "") + f" · {lv.min_len}+ letters · ⏱ {left}s left\n"
            + ("🚫 Banned: " + " ".join(c.upper() for c in sorted(g.banned)) + "\n" if g.banned else "")
            + f"👥 Alive ({len(g.alive)}/{len(g.players)}): {alive}\n📝 Words: {len(g.log)} · round {g.round} · level {lv.n}\n🔗 Last words: {last}")


# -------------------------------------------------------------------------------------------- the end

def _drop(g: Game) -> None:
    for t in (g.timer, g.lobby_task):
        if t and t is not asyncio.current_task():
            t.cancel()
    g.timer = g.lobby_task = None
    g.token += 1
    if GAMES.get(g.chat_id) is g:
        del GAMES[g.chat_id]


def _summary(g: Game, winner: Player | None) -> str:
    medals = ["🥇", "🥈", "🥉"]
    rest = sorted((p for p in g.alive if p is not winner), key=lambda p: -p.words)
    ranking = ([winner] if winner else []) + rest + [g.players[u] for u, _ in reversed(g.out) if g.players[u] is not winner]
    rows = "\n".join(f"{medals[i] if i < 3 else f'<b>{i + 1}.</b>'} {esc(p.name)} · {p.words} word{'s' if p.words != 1 else ''}" for i, p in enumerate(ranking[:10]))
    best = max(g.log, key=lambda e: len(e[1]), default=None)
    extra = f"\n📏 Longest word: <b>{best[1].upper()}</b> ({len(best[1])} letters, {esc(g.players[best[0]].name)})" if best else ""
    mins = max(0, int(time.time() - g.started)) if g.started else 0
    dur = f" · ⏱ {mins // 60}m {mins % 60:02d}s" if g.started else ""
    return f"{rows}\n\n🔗 Chain length: <b>{len(g.log)}</b> words · {g.round} round{'s' if g.round != 1 else ''}{dur}{extra}"


async def _record(g: Game, winner: Player | None) -> None:
    if recorder is None:
        return
    try:
        await recorder(g, winner)
    except Exception:
        log.exception("wordchain: could not save the game stats")


async def _finish(g: Game, send: Send, winner: Player | None, prefix: str = "") -> None:
    summary = _summary(g, winner)
    _drop(g)
    head = f"🏆 <b>{mention(winner.uid, winner.name)} wins Word Chain!</b>" if winner else "🏁 <b>Word Chain is over.</b>"
    await _say(send, f"{prefix}{head}\n\n{summary}\n\n<i>/wc to play again · /wcstats for your stats</i>")
    await _record(g, winner)


async def end(g: Game, send: Send, by: str = "") -> None:
    """Stop the game now (/end). An aborted game is not counted in the stats."""
    async with g.lock:
        was = g.state
        if GAMES.get(g.chat_id) is not g:
            return
        if was == "lobby":
            _drop(g)
            await _say(send, "🛑 Word Chain lobby closed" + (f" by {esc(by)}" if by else "") + ".")
            return
        summary = _summary(g, None) if g.log else ""
        _drop(g)
        await _say(send, "🛑 Word Chain ended" + (f" by {esc(by)}" if by else "") + "." + (f"\n\n{summary}" if summary else ""))
