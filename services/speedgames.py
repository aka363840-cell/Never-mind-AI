"""Speed games (⚡ Fastest Tap, 💥 Crash Count): the running game of each chat + the diamond settlement.

* One running game per chat, kept in memory only. A round lasts well under two minutes and nothing is held in escrow
  (see game/speedgames.py), so a restart simply forgets the lobby and nobody loses diamonds.
* Every diamond change goes through economy.apply() (atomic, ledgered, never negative, idempotent `ref`).
* Lifetime counters reuse `user_stats` through realm.bump -> no new tables, no migration.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from database.session import db
from game import speedgames as G
from services import cooldowns, economy, realm
from services.errors import GameError, OnCooldown


@dataclass
class Game:
    kind: str                                   # "tap" | "crash"
    chat_id: int
    host: int
    amount: int                                 # entry per player
    gid: str = field(default_factory=lambda: secrets.token_urlsafe(5))
    players: dict[int, str] = field(default_factory=dict)       # uid -> display name, in join order
    phase: str = "lobby"                        # lobby -> ready -> live -> done
    eliminated: set[int] = field(default_factory=set)           # false starts (tap) / wrong answers (crash)
    word: str = ""
    answer: int = 0
    t0: float = 0.0                             # monotonic time the button turned green / the word appeared
    lobby_msg: int | None = None
    round_msg: int | None = None
    created: float = field(default_factory=time.monotonic)
    timer: asyncio.Task | None = None           # the lobby countdown
    tasks: list[asyncio.Task] = field(default_factory=list)     # the running round
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


GAMES: dict[int, Game] = {}


def get(chat_id: int) -> Game | None:
    """The running game of a chat (None if there is none). A game that is far older than any round can last is swept away."""
    g = GAMES.get(chat_id)
    if g is not None and time.monotonic() - g.created > G.STALE_S:
        drop(g)
        return None
    return g


def new(kind: str, chat_id: int, host: int, host_name: str, amount: int) -> Game:
    if get(chat_id) is not None:
        raise GameError("🎮 A speed game is already running in this chat. Join it, wait for it to finish, or /end it.")
    g = Game(kind=kind, chat_id=chat_id, host=host, amount=amount)
    g.players[host] = host_name
    GAMES[chat_id] = g
    return g


def drop(g: Game) -> None:
    """Forget the game and stop its timers (the task that calls this keeps running)."""
    g.phase = "done"
    if GAMES.get(g.chat_id) is g:
        del GAMES[g.chat_id]
    me = asyncio.current_task() if _in_loop() else None
    for t in [g.timer, *g.tasks]:
        if t is not None and t is not me and not t.done():
            t.cancel()


def _in_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


async def check_funds(uid: int, amount: int, name: str) -> None:
    """A player must be able to pay the entry (checked when joining and again when the round starts)."""
    if (await economy.balance(uid)).balance < amount:
        raise GameError(f"❌ <b>{name}</b> needs at least <b>{amount:,}</b> 💎 in the wallet to play.")


async def claim_start_cd(uid: int) -> None:
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "speedgame", G.START_CD_S)
    if rem:
        raise OnCooldown(rem, "Starting a new speed game")


async def settle(g: Game, winner: int | None) -> tuple[int, int]:
    """Records the round and, with a winner, moves the diamonds in ONE transaction.
    Every other player pays min(entry, wallet) to the winner; FEE_PCT of each payment is burned.
    Returns (diamonds the winner received, diamonds burned). No winner -> (0, 0), nobody pays."""
    got = burned = 0
    async with db.tx() as s:
        for uid in g.players:
            await realm.bump(s, uid, f"{g.kind}_play")
        if winner is None:
            return 0, 0
        await realm.bump(s, winner, f"{g.kind}_win")
        for uid in g.players:
            if uid == winner:
                continue
            w = await economy.wallet_of(s, uid, lock=True)
            pay = min(g.amount, w.balance)
            if pay <= 0:
                continue
            net, fee = G.split_pay(pay)
            await economy.apply(s, uid, wallet=-pay, kind=f"{g.kind}_loss", counterparty=winner, ref=f"{g.kind}:{g.gid}:{uid}:out")
            await economy.apply(s, winner, wallet=net, kind=f"{g.kind}_win", counterparty=uid, ref=f"{g.kind}:{g.gid}:{uid}:in")
            got += net
            burned += fee
    return got, burned
