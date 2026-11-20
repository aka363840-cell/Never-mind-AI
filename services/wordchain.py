"""Word Chain service: lobbies, turns, timers, eliminations (in memory - a game lasts minutes, a redeploy simply ends it).

No Telegram imports here: everything the game wants to say goes through a `send(text)` coroutine the handler passes in,
so the whole game can be tested with a fake sender.  Rules and word list: game/wordchain.py.
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


@dataclass
class Player:
    uid: int
    name: str
    alive: bool = True
    words: int = 0


@dataclass
class Game:
    chat_id: int
    host_id: int
    state: str = "lobby"                                        # lobby -> playing
    players: dict[int, Player] = field(default_factory=dict)    # join order
    order: list[int] = field(default_factory=list)              # turn order (shuffled at start)
    idx: int = 0                                                # whose turn, as an index into order
    round: int = 1
    level_n: int = 0
    letter: str = ""
    last_word: str = ""
    used: set[str] = field(default_factory=set)
    log: list[tuple[int, str]] = field(default_factory=list)    # (uid, word) in play order
    out: list[tuple[int, str]] = field(default_factory=list)    # (uid, why) in elimination order
    token: int = 0                                              # bumps on every turn so stale timers do nothing
    deadline: float = 0.0
    timer: asyncio.Task | None = None
    lobby_task: asyncio.Task | None = None
    lobby_msg: int | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created: float = field(default_factory=time.time)

    @property
    def current(self) -> Player | None:
        return self.players.get(self.order[self.idx]) if self.order else None

    @property
    def alive(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]


GAMES: dict[int, Game] = {}                                     # chat id -> game (lobby or running)


@dataclass
class Move:
    handled: bool                                               # False = not this player's turn: treat as normal chat
    error: str | None = None                                    # why the word was refused (the player may try again)


# ----------------------------------------------------------------------------------------------- lobby

def create(chat_id: int, host_id: int, host_name: str) -> Game:
    if chat_id in GAMES:
        raise GameError("🎮 A Word Chain game is already " + ("running" if GAMES[chat_id].state == "playing" else "open") + " here. /end stops it.")
    g = Game(chat_id, host_id)
    g.players[host_id] = Player(host_id, host_name[:30])
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


def lobby_text(g: Game) -> str:
    names = "\n".join(f"• {esc(p.name)}" for p in g.players.values())
    return (f"⛓ <b>Word Chain</b> - lobby open!\n\n"
            f"Say a word that starts with the <b>last letter</b> of the previous word. Run out of time and you're out - last one standing wins.\n"
            f"Words get longer and the clock gets shorter as the game goes on. 🔥\n\n"
            f"👥 <b>Players ({len(g.players)}/{W.MAX_PLAYERS})</b>\n{names}\n\n"
            f"/join to play · /forcestart to begin now (min {W.MIN_PLAYERS}) · starts automatically in {W.LOBBY_S}s")


def start_lobby_timer(g: Game, send: Send) -> None:
    g.lobby_task = asyncio.create_task(_lobby_timer(g, send))


async def _lobby_timer(g: Game, send: Send) -> None:
    try:
        await asyncio.sleep(W.LOBBY_S)
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
        g.order = list(g.players)
        random.shuffle(g.order)
        g.idx, g.round = 0, 1
        lv = W.level_for(1)
        g.letter = W.pick_letter(lv.min_len, g.used)
        who = " → ".join(esc(g.players[u].name) for u in g.order)
        await _begin_turn(g, send, f"⛓ <b>Word Chain starts!</b> {len(g.order)} players\n🔀 Order: {who}\n\n")


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


def _advance(g: Game) -> None:
    """Move to the next living player; a new round starts whenever the order wraps around."""
    n = len(g.order)
    for _ in range(n):
        g.idx += 1
        if g.idx >= n:
            g.idx = 0
            g.round += 1
        if g.players[g.order[g.idx]].alive:
            return


async def _begin_turn(g: Game, send: Send, prefix: str = "") -> None:
    """Announce whose turn it is and start that player's clock. Caller holds g.lock."""
    lv = W.level_for(g.round)
    if lv.n != g.level_n:
        if g.level_n:
            prefix += (f"🔥 <b>Level {lv.n}!</b> Words need <b>{lv.min_len}+</b> letters and you get <b>{lv.seconds}s</b> per turn.\n\n")
        g.level_n = lv.n
    if not W.has_word(g.letter, lv.min_len, g.used):             # dead end (e.g. 'x' at level 5): restart the chain
        old = g.letter
        g.letter = W.pick_letter(lv.min_len, g.used, avoid=old)
        prefix += f"🔀 No words left that start with <b>{old.upper()}</b> - the chain restarts with a new letter!\n\n"
    p = g.current
    assert p is not None
    g.token += 1
    _cancel_timer(g)
    g.deadline = time.time() + lv.seconds
    await _say(send, f"{prefix}🎯 {mention(p.uid, p.name)}, your word starts with <b>{g.letter.upper()}</b>\n"
                     f"<i>{lv.min_len}+ letters · ⏱ {lv.seconds}s · round {g.round} · level {lv.n}</i>")
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
        if p is not None:
            await _eliminate(g, p, "ran out of time", send)


async def _eliminate(g: Game, p: Player, why: str, send: Send) -> None:
    """Knock a player out; finish the game if one is left, else hand the turn on. Caller holds g.lock."""
    p.alive = False
    g.out.append((p.uid, why))
    left = g.alive
    if len(left) <= 1:
        await _finish(g, send, left[0] if left else None, f"⏰ <b>{esc(p.name)}</b> {why} and is eliminated!\n\n")
        return
    _advance(g)
    await _begin_turn(g, send, f"⏰ <b>{esc(p.name)}</b> {why} - eliminated! 💀 <i>{len(left)} players left</i>\n\n")


async def submit(g: Game, uid: int, text: str, send: Send) -> Move:
    """A chat message from `uid`. Only the player whose turn it is can make a move."""
    async with g.lock:
        p = g.current
        if g.state != "playing" or GAMES.get(g.chat_id) is not g or p is None or p.uid != uid or not p.alive:
            return Move(False)
        word = text.strip().lower()
        lv = W.level_for(g.round)
        err = W.check(word, g.letter, lv.min_len, g.used)
        if err:
            return Move(True, "❌ " + err)
        g.used.add(word)
        g.log.append((uid, word))
        g.last_word = word
        p.words += 1
        g.letter = word[-1]
        _advance(g)
        await _begin_turn(g, send, f"✅ <b>{word.upper()}</b>\n")
        return Move(True)


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
    ranking = ([winner] if winner else []) + [g.players[u] for u, _ in reversed(g.out) if not winner or u != winner.uid]
    rows = "\n".join(f"{medals[i] if i < 3 else f'<b>{i + 1}.</b>'} {esc(p.name)} · {p.words} word{'s' if p.words != 1 else ''}" for i, p in enumerate(ranking[:10]))
    best = max(g.log, key=lambda e: len(e[1]), default=None)
    extra = f"\n📏 Longest word: <b>{best[1].upper()}</b> ({len(best[1])} letters, {esc(g.players[best[0]].name)})" if best else ""
    return f"{rows}\n\n🔗 Chain length: <b>{len(g.log)}</b> words · {g.round} round{'s' if g.round != 1 else ''}{extra}"


async def _finish(g: Game, send: Send, winner: Player | None, prefix: str = "") -> None:
    summary = _summary(g, winner)
    _drop(g)
    head = f"🏆 <b>{mention(winner.uid, winner.name)} wins Word Chain!</b>" if winner else "🏁 <b>Word Chain is over.</b>"
    await _say(send, f"{prefix}{head}\n\n{summary}\n\n<i>/wc to play again</i>")


async def end(g: Game, send: Send, by: str = "") -> None:
    """Stop the game now (/end)."""
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
