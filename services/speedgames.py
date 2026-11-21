"""Speed games (⚡ Fastest Tap, 💥 Crash Count): the running game of each chat, escrow + settlement, rematch memory, leaderboards.

* One running game per chat, kept in memory (a match lasts a few minutes).
* ESCROW without a new table: every entry is held as a ledger row (`tap_hold` / `crash_hold`, ref `spd:<gid>:<uid>:h`) when the match
  starts and closed by exactly one row with ref `spd:<gid>:<uid>:c` (refund, or a 0-diamond `_loss` row). If the bot dies mid-match the
  open holds are found by recover_orphans() (runs lazily, at most every G.RECOVER_EVERY_S, when a lobby is opened) and refunded.
* Every diamond change goes through economy.apply() (atomic, ledgered, never negative, idempotent `ref`).
* Lifetime counters reuse `user_stats` through realm.bump -> no new tables, no migration.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select

from database.models import Transaction, User, UserStat, utcnow
from database.session import db
from game import speedgames as G
from services import cooldowns, economy, progress, realm
from services.errors import GameError, OnCooldown

log = logging.getLogger(__name__)


@dataclass
class Game:
    kind: str                                   # "tap" | "crash"
    chat_id: int
    host: int
    amount: int                                 # entry per player; 0 = practice (no diamonds)
    gid: str = field(default_factory=lambda: secrets.token_urlsafe(5))
    players: dict[int, str] = field(default_factory=dict)       # uid -> display name, in join order
    invited: list[tuple[int, str]] = field(default_factory=list)  # rematch: the earlier players who are asked to join again
    phase: str = "lobby"                        # lobby -> ready -> live -> between -> ... -> done
    held: dict[int, int] = field(default_factory=dict)          # uid -> entry held in escrow
    closed: bool = False                        # the escrow was settled / refunded (exactly once)
    scores: dict[int, int] = field(default_factory=dict)
    wins_needed: int = 2
    max_rounds: int = 5
    round_no: int = 0
    eliminated: set[int] = field(default_factory=set)           # this round: false starts / decoys (tap) / wrong answers (crash)
    challenge: G.Challenge | None = None
    used: set[str] = field(default_factory=set)                 # words already used in this match
    green: int = 0                              # tap: index of the green button this round
    reason: str = ""                            # why a round had no winner
    took: float = 0.0                           # seconds the round winner needed
    t0: float = 0.0                             # monotonic time the round went live
    fut: asyncio.Future | None = None           # resolved with the round winner's uid (None = no winner)
    lobby_msg: int | None = None
    round_msg: int | None = None
    created: float = field(default_factory=time.monotonic)
    timer: asyncio.Task | None = None           # the lobby countdown
    tasks: list[asyncio.Task] = field(default_factory=list)     # the running match
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def practice(self) -> bool:
        return self.amount == 0


@dataclass
class Result:
    got: int = 0                                # diamonds the winner received
    burned: int = 0
    refunded: bool = False                      # everybody got the entry back (no winner / /end)
    capped: list[int] = field(default_factory=list)             # losers refunded because of the daily pair limit
    unlocked: list[str] = field(default_factory=list)           # achievements the winner just unlocked


@dataclass
class Last:
    gid: str
    kind: str
    amount: int
    players: dict[int, str]
    at: float


GAMES: dict[int, Game] = {}
LAST: dict[int, Last] = {}
_BG: set[asyncio.Task] = set()
_last_recover = 0.0


def get(chat_id: int) -> Game | None:
    """The running game of a chat (None if there is none). A game far older than any match can last is swept away and refunded."""
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


def _in_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _spawn(coro) -> None:
    t = asyncio.get_running_loop().create_task(coro)
    _BG.add(t)
    t.add_done_callback(lambda x: (_BG.discard(x), x.cancelled() or x.exception() and log.error("speedgames background task failed: %r", x.exception())))


def drop(g: Game, refund: bool = True) -> None:
    """Forget the game and stop its timers/tasks (the task that calls this keeps running). Held entries that were not settled yet
    are refunded in the background (refund=False: the caller settles them itself)."""
    g.phase = "done"
    if GAMES.get(g.chat_id) is g:
        del GAMES[g.chat_id]
    if _in_loop():
        me = asyncio.current_task()
        for t in [g.timer, *g.tasks]:
            if t is not None and t is not me and not t.done():
                t.cancel()
        if g.fut is not None and not g.fut.done():
            g.fut.cancel()
        if refund and g.held and not g.closed:
            _spawn(release(g, None, played=False))


# ------------------------------------------------------------------ funds, cooldown, escrow
def _ref(g: Game, uid: int, tag: str) -> str:
    return f"spd:{g.gid}:{uid}:{tag}"


async def check_funds(uid: int, amount: int, name: str) -> None:
    """A player must be able to pay the entry (checked when joining; the real hold happens when the match starts)."""
    if amount and (await economy.balance(uid)).balance < amount:
        raise GameError(f"❌ <b>{name}</b> needs at least <b>{amount:,}</b> 💎 in the wallet to play.")


async def claim_start_cd(uid: int) -> None:
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "speedgame", G.START_CD_S)
    if rem:
        raise OnCooldown(rem, "Starting a new speed game")


async def hold_entries(g: Game) -> list[tuple[int, str]]:
    """Escrow: take every player's entry now. Players who can't pay any more are removed and returned as [(uid, name)]."""
    removed: list[tuple[int, str]] = []
    if g.practice:
        return removed
    for uid in list(g.players):
        try:
            async with db.tx() as s:
                await economy.apply(s, uid, wallet=-g.amount, kind=f"{g.kind}_hold", ref=_ref(g, uid, "h"))
            g.held[uid] = g.amount
        except GameError:
            removed.append((uid, g.players.pop(uid)))
    return removed


async def _pair_games(s, a: int, b: int) -> int:
    """Paid matches between these two players in the last 24 h (either of them winning)."""
    day = utcnow() - timedelta(days=1)
    return (await s.execute(select(func.count()).select_from(Transaction).where(
        Transaction.kind.in_(("tap_win", "crash_win")), Transaction.created_at >= day,
        ((Transaction.user_id == a) & (Transaction.counterparty_id == b)) |
        ((Transaction.user_id == b) & (Transaction.counterparty_id == a))))).scalar() or 0


async def release(g: Game, winner: int | None, played: bool = True) -> Result:
    """Settle the escrow ONCE, atomically. winner=None (no winner, /end, error) refunds everybody.
    With a winner every other player's entry goes to him minus FEE_PCT (burned); a loser who already lost PAIR_DAILY paid matches
    to this winner today gets the entry back instead. `played` = a real match was decided (counters, achievements, missions)."""
    res = Result()
    if g.closed:
        return res
    g.closed = True
    try:
        async with db.tx() as s:
            capped: set[int] = set()
            if winner is not None:
                for uid in g.held:
                    if uid != winner and await _pair_games(s, winner, uid) >= G.PAIR_DAILY:
                        capped.add(uid)
            for uid, hold in g.held.items():
                close = _ref(g, uid, "c")
                if winner is None or uid == winner or uid in capped:
                    await economy.apply(s, uid, wallet=hold, kind=f"{g.kind}_refund", ref=close)
                else:
                    await economy.apply(s, uid, wallet=0, kind=f"{g.kind}_loss", counterparty=winner, ref=close)
                    net, fee = G.split_pay(hold)
                    await economy.apply(s, winner, wallet=net, kind=f"{g.kind}_win", counterparty=uid, ref=_ref(g, uid, "w"))
                    res.got += net
                    res.burned += fee
            if played and not g.practice:
                for uid in g.held:
                    await realm.bump(s, uid, f"{g.kind}_play")
                if winner is not None:
                    res.unlocked = await realm.bump(s, winner, f"{g.kind}_win")
            if res.got:
                await progress.emit(s, winner, "earn", res.got)
            res.capped = sorted(capped)
    except BaseException:
        g.closed = False                          # nothing was applied (rolled back): a later refund / recovery may still run
        raise
    res.refunded = winner is None and bool(g.held)
    return res


async def recover_orphans() -> int:
    """Refund entries that were held by a match that never got settled (the bot crashed or was restarted mid-match)."""
    now = utcnow()
    old, floor = now - timedelta(seconds=G.RECOVER_AFTER_S), now - timedelta(days=2)
    n = 0
    async with db.tx() as s:
        rows = (await s.execute(select(Transaction.user_id, Transaction.ref, Transaction.kind, Transaction.amount).where(
            Transaction.kind.in_(("tap_hold", "crash_hold")), Transaction.created_at < old, Transaction.created_at > floor))).all()
        for uid, ref, kind, amount in rows:
            if not ref or not ref.endswith(":h"):
                continue
            close = ref[:-1] + "c"
            if (await s.execute(select(Transaction.id).where(Transaction.ref == close))).first():
                continue
            await economy.apply(s, uid, wallet=-amount, kind=kind.replace("_hold", "_refund"), ref=close)
            n += 1
    if n:
        log.warning("speedgames: refunded %s orphaned entries", n)
    return n


async def maybe_recover() -> None:
    """Called when a lobby is opened: runs the recovery sweep at most every RECOVER_EVERY_S. Never raises."""
    global _last_recover
    if time.monotonic() - _last_recover < G.RECOVER_EVERY_S and _last_recover:
        return
    _last_recover = time.monotonic()
    try:
        await recover_orphans()
    except Exception:
        log.exception("speedgames recovery sweep failed")


# ------------------------------------------------------------------ rematch memory
def remember(g: Game) -> None:
    LAST[g.chat_id] = Last(g.gid, g.kind, g.amount, dict(g.players), time.monotonic())


def last(chat_id: int) -> Last | None:
    x = LAST.get(chat_id)
    if x is not None and time.monotonic() - x.at > G.REMATCH_TTL_S:
        del LAST[chat_id]
        return None
    return x


# ------------------------------------------------------------------ leaderboards (counters live in user_stats)
async def top(kind: str, limit: int = 10) -> list[tuple[int, str, int, int]]:
    """Best players by match wins: (user id, name, wins, matches played)."""
    ok = (UserStat.key == f"{kind}_win", UserStat.value > 0, User.is_flagged == False, User.is_banned == False)  # noqa: E712
    async with db.tx() as s:
        rows = (await s.execute(select(UserStat.user_id, User.first_name, UserStat.value).join(User, User.id == UserStat.user_id)
                                .where(*ok).order_by(UserStat.value.desc(), UserStat.user_id).limit(limit))).all()
        ids = [r[0] for r in rows]
        plays = {}
        if ids:
            plays = dict((await s.execute(select(UserStat.user_id, UserStat.value).where(
                UserStat.key == f"{kind}_play", UserStat.user_id.in_(ids)))).all())
    return [(r[0], r[1], int(r[2]), int(plays.get(r[0], 0))) for r in rows]


async def my_stats(kind: str, uid: int) -> tuple[int, int, int | None]:
    """(wins, matches, rank by wins or None)."""
    st = await realm.stats_of(uid)
    wins, plays = st.get(f"{kind}_win", 0), st.get(f"{kind}_play", 0)
    rank = None
    if wins:
        async with db.tx() as s:
            better = (await s.execute(select(func.count()).select_from(UserStat).join(User, User.id == UserStat.user_id).where(
                UserStat.key == f"{kind}_win", UserStat.value > wins, User.is_flagged == False, User.is_banned == False))).scalar() or 0  # noqa: E712
        rank = int(better) + 1
    return wins, plays, rank
