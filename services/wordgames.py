"""Wordly + Hack: running games (one per chat, kept in memory) and the diamond escrow of Hack.

Money rules (same as the rest of the bot): every diamond change goes through economy.apply() with an idempotency ref.
  bet      ref  hack:<gid>:b:<uid>     (taken when a player joins)
  win      ref  hack:<gid>:win         (the pot, paid once)
  refund   ref  hack:<gid>:r:<uid>     (cancelled / ended / nobody cracked it)
Games live in memory, so a restart would lose the escrow - recover_orphans() (called at boot) refunds every bet that has
neither a win nor a refund row. No new tables, no migration."""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select

from database.models import Transaction, utcnow
from database.session import db
from game import hack as H
from services import economy

log = logging.getLogger(__name__)


# ============================================================== Wordly
@dataclass
class WordlyGame:
    chat_id: int
    rack: str
    words: frozenset                                   # every accepted word for this rack
    started_by: int
    ends_at: float
    found: dict[str, tuple[int, str]] = field(default_factory=dict)       # word -> (user id, name)
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# ============================================================== Hack
@dataclass
class HackPlayer:
    uid: int
    name: str
    guesses: int = 0
    skips: int = 0                                     # timed-out turns in a row
    forfeit: bool = False


@dataclass
class HackGame:
    chat_id: int
    gid: str
    n: int                                             # digits in the code
    bet: int
    host: int
    phase: str = "lobby"                               # lobby -> play
    secret: str = ""
    players: list[HackPlayer] = field(default_factory=list)
    turn: int = 0                                      # index into players
    created: float = field(default_factory=time.time)
    token: int = 0                                     # bumped on every new turn / phase: stale timers see a different token and stop
    history: list[tuple[str, str, int, int]] = field(default_factory=list)    # (name, guess, hacks, glitches)
    lobby_msg: int | None = None
    board_msg: int | None = None
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def pot(self) -> int:
        return self.bet * len(self.players)

    def player(self, uid: int) -> HackPlayer | None:
        return next((p for p in self.players if p.uid == uid), None)

    def current(self) -> HackPlayer | None:
        return self.players[self.turn] if self.phase == "play" and 0 <= self.turn < len(self.players) else None

    def active(self) -> list[HackPlayer]:
        return [p for p in self.players if not p.forfeit]

    def can_guess(self, p: HackPlayer) -> bool:
        return not p.forfeit and p.guesses < H.max_guesses(self.n)

    def next_player(self) -> HackPlayer | None:
        """The next player (after the current one) who can still guess; None = nobody can."""
        for i in range(1, len(self.players) + 1):
            p = self.players[(self.turn + i) % len(self.players)]
            if self.can_guess(p):
                self.turn = self.players.index(p)
                return p
        return None


WORDLY: dict[int, WordlyGame] = {}
HACK: dict[int, HackGame] = {}


def new_gid() -> str:
    return secrets.token_hex(4)


def running(chat_id: int) -> str | None:
    """'wordly' / 'hack' when one of this add-on's games is running in the chat (synchronous check)."""
    if chat_id in WORDLY:
        return "wordly"
    if chat_id in HACK:
        return "hack"
    return None


async def busy(chat_id: int) -> str | None:
    """Name of the game that blocks a new one in this chat ('wordly', 'hack' or 'WordSeek'), or None."""
    r = running(chat_id)
    if r:
        return r
    from services import wordseek as ws
    cur = await ws.get(chat_id)
    if cur is not None and ws.idle_for(cur) < ws.IDLE_REPLACE_S:
        return "WordSeek"
    return None


# ============================================================== Hack: diamonds
async def charge(g: HackGame, uid: int) -> None:
    """Take the bet (raises InsufficientFunds - a GameError - if the player can't pay)."""
    async with db.tx() as s:
        await economy.apply(s, uid, wallet=-g.bet, kind="hack_bet", ref=f"hack:{g.gid}:b:{uid}", meta={"chat": g.chat_id})


async def refund_all(g: HackGame) -> int:
    """Give every bet back. Idempotent; a failure is logged and left to recover_orphans()."""
    n = 0
    for p in g.players:
        try:
            async with db.tx() as s:
                await economy.apply(s, p.uid, wallet=g.bet, kind="hack_refund", ref=f"hack:{g.gid}:r:{p.uid}")
            n += 1
        except Exception:
            log.exception("hack refund failed for %s in game %s (will be retried at next boot)", p.uid, g.gid)
    return n


async def pay_winner(g: HackGame, uid: int) -> int:
    """Pay the pot (minus H.FEE_PCT) to the winner; returns the amount."""
    pot = g.pot
    prize = pot - pot * H.FEE_PCT // 100
    async with db.tx() as s:
        await economy.apply(s, uid, wallet=prize, kind="hack_win", ref=f"hack:{g.gid}:win", meta={"chat": g.chat_id, "pot": pot})
    return prize


async def recover_orphans() -> int:
    """Boot-time safety net: refund bets of games that were running when the bot stopped (no win and no refund row). Returns how many."""
    since = utcnow() - timedelta(days=2)
    async with db.tx() as s:
        rows = (await s.execute(select(Transaction.user_id, Transaction.kind, Transaction.amount, Transaction.ref)
                                .where(Transaction.kind.in_(("hack_bet", "hack_refund", "hack_win")),
                                       Transaction.created_at >= since))).all()
    bets: dict[tuple[str, int], int] = {}
    closed: set[str] = set()
    refunded: set[tuple[str, int]] = set()
    for uid, kind, amount, ref in rows:
        parts = (ref or "").split(":")
        if len(parts) < 3 or parts[0] != "hack":
            continue
        gid = parts[1]
        if kind == "hack_bet":
            bets[(gid, uid)] = -int(amount)
        elif kind == "hack_win":
            closed.add(gid)
        else:
            refunded.add((gid, uid))
    done = 0
    for (gid, uid), amount in bets.items():
        if gid in closed or (gid, uid) in refunded or amount <= 0:
            continue
        try:
            async with db.tx() as s:
                await economy.apply(s, uid, wallet=amount, kind="hack_refund", ref=f"hack:{gid}:r:{uid}")
            done += 1
        except Exception:
            log.exception("hack orphan refund failed (game %s, user %s)", gid, uid)
    return done


def now() -> float:
    return time.time()
