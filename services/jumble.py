"""Word Jumble: running games (one per chat, kept in memory), the diamond escrow of paid games, rewards and stats.

Phases: setup (host picks the settings) -> [lobby (paid games: players pay the entry fee)] -> play -> done.
A game that sits in GAMES blocks every other word game in that chat (Wordly, Hack, WordSeek) - see busy().

Money rules (same as the rest of the bot): every diamond change goes through economy.apply() with an idempotency ref.
  entry fee   kind jumble_bet     ref jumblebet:<gid>:b:<uid>     (taken when a player joins)
  refund      kind jumble_refund  ref jumblebet:<gid>:r:<uid>     (cancelled / ended / nobody scored)
  pot         kind jumble_pot     ref jumblebet:<gid>:w:<uid>     (paid to the best player(s), all in ONE transaction)
  reward      kind jumble_win     ref jumble:<chat>:<gid>:<uid>:<pts>:<words>:<rank>   (points reward + the stats row, see game/jumble_stats.py)
Games live in memory, so a restart would lose the escrow - recover_orphans() (called at boot) refunds every entry fee that has
neither a pot nor a refund row. No new tables, no migration.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import timedelta

from game import jumble as D, jumble_stats as S

log = logging.getLogger(__name__)


@dataclass
class JumbleGame:
    chat_id: int
    gid: str
    host: int
    # --- settings (setup panel)
    mode: str = "rounds"                                   # rounds | race
    rounds: int = D.DEFAULT_ROUNDS                         # rounds mode: number of words; race mode: filled with D.MAX_RACE_ROUNDS at start
    target: int = D.DEFAULT_TARGET                         # race mode: points to reach
    diff: str = D.DEFAULT_DIFF                             # easy | medium | hard | extreme | mixed
    speed: str = D.DEFAULT_SPEED
    theme: str = D.DEFAULT_THEME
    entry: int = 0                                         # diamonds per player (0 = free); groups only
    # --- state
    phase: str = "setup"                                   # setup -> lobby (paid games) -> play -> done
    players: dict[int, str] = field(default_factory=dict)  # paid games: uid -> name of everybody who paid the entry fee
    round_no: int = 0
    rdiff: str = D.DEFAULT_DIFF                            # the real difficulty of the current round (differs from `diff` when mixed)
    rtime: int = 0                                         # seconds of the current round
    word: str = ""                                         # the current word
    scrambled: str = ""
    answers: frozenset = frozenset()                       # every accepted answer of the current round
    open: bool = False                                     # True while the current round can still be won
    started: float = 0.0                                   # when the current round's puzzle was shown
    hints: int = 0                                         # hints shown in the current round
    winner: tuple | None = None                            # (uid, name, word typed, points, seconds) of the current round
    skipped: bool = False                                  # the current round was skipped by vote
    skip_votes: set = field(default_factory=set)
    idle: int = 0                                          # rounds in a row nobody answered
    used: set = field(default_factory=set)                 # puzzle words already played in this game
    scores: dict[int, list] = field(default_factory=dict)  # uid -> [name, points, words solved]
    created: float = field(default_factory=time.time)
    setup_msg: int | None = None                           # the setup / lobby message
    msg_id: int | None = None                              # the one round message (edited: puzzle -> hint -> result)
    photo: bool = False                                    # the round message is a picture of letter tiles (edit its caption, not its text)
    near: set = field(default_factory=set)                 # players already cheered on for a real-but-wrong word this round
    solved: asyncio.Event | None = None
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def pot(self) -> int:
        return self.entry * len(self.players)


GAMES: dict[int, JumbleGame] = {}
NAMES: dict[int, str] = {}                                 # uid -> last seen name (leaderboards); filled while games run


def new_gid() -> str:
    return secrets.token_hex(4)


def now() -> float:
    return time.time()


def running(chat_id: int) -> str | None:
    """'jumble' while a Jumble game (setup, lobby or play) exists in the chat - synchronous check."""
    return "jumble" if chat_id in GAMES else None


async def busy(chat_id: int) -> str | None:
    """Name of the game that blocks a new Jumble in this chat ('jumble', 'wordly', 'hack', 'WordSeek'), or None."""
    if chat_id in GAMES:
        return "jumble"
    try:                                                   # Wordly / Hack add-on (also covers WordSeek)
        from services import wordgames as wg
    except ImportError:
        wg = None
    if wg is not None:
        return await wg.busy(chat_id)
    try:
        from services import wordseek as ws
    except ImportError:
        return None
    cur = await ws.get(chat_id)
    if cur is not None and ws.idle_for(cur) < ws.IDLE_REPLACE_S:
        return "WordSeek"
    return None


# ============================================================== diamonds: entry fee, refund, pot
async def charge(g: JumbleGame, uid: int) -> None:
    """Take the entry fee (raises InsufficientFunds - a GameError - when the player can't pay; nothing is taken then)."""
    from database.session import db
    from services import economy
    async with db.tx() as s:
        await economy.apply(s, uid, wallet=-g.entry, kind=S.BET_KIND, ref=f"jumblebet:{g.gid}:b:{uid}", meta={"chat": g.chat_id})


async def refund_all(g: JumbleGame) -> int:
    """Give every entry fee back. Idempotent; a failure is logged and left to recover_orphans()."""
    from database.session import db
    from services import economy
    n = 0
    for uid in list(g.players):
        try:
            async with db.tx() as s:
                await economy.apply(s, uid, wallet=g.entry, kind=S.REFUND_KIND, ref=f"jumblebet:{g.gid}:r:{uid}")
            n += 1
        except Exception:
            log.exception("jumble refund failed for %s in game %s (will be retried at next boot)", uid, g.gid)
    return n


async def pay_pot(g: JumbleGame, winners: list[int]) -> list[int]:
    """Pay the pot (minus D.FEE_PCT) to the winner(s) in ONE transaction; returns the amounts."""
    from database.session import db
    from services import economy
    shares = S.split_pot(g.pot, len(winners), D.FEE_PCT)
    async with db.tx() as s:
        for uid, amount in zip(winners, shares):
            await economy.apply(s, uid, wallet=amount, kind=S.POT_KIND, ref=f"jumblebet:{g.gid}:w:{uid}", meta={"chat": g.chat_id, "pot": g.pot})
    return shares


async def recover_orphans() -> int:
    """Boot-time safety net: refund entry fees of paid games that were running when the bot stopped (no pot and no refund row)."""
    from sqlalchemy import select
    from database.models import Transaction, utcnow
    from database.session import db
    from services import economy
    since = utcnow() - timedelta(days=2)
    async with db.tx() as s:
        rows = (await s.execute(select(Transaction.user_id, Transaction.kind, Transaction.amount, Transaction.ref)
                                .where(Transaction.kind.in_((S.BET_KIND, S.REFUND_KIND, S.POT_KIND)),
                                       Transaction.created_at >= since))).all()
    bets: dict[tuple[str, int], int] = {}
    closed: set[str] = set()
    refunded: set[tuple[str, int]] = set()
    for uid, kind, amount, ref in rows:
        parts = (ref or "").split(":")
        if len(parts) < 4 or parts[0] != "jumblebet":
            continue
        gid = parts[1]
        if kind == S.BET_KIND:
            bets[(gid, uid)] = -int(amount)
        elif kind == S.POT_KIND:
            closed.add(gid)
        else:
            refunded.add((gid, uid))
    done = 0
    for (gid, uid), amount in bets.items():
        if gid in closed or (gid, uid) in refunded or amount <= 0:
            continue
        try:
            async with db.tx() as s:
                await economy.apply(s, uid, wallet=amount, kind=S.REFUND_KIND, ref=f"jumblebet:{gid}:r:{uid}")
            done += 1
        except Exception:
            log.exception("jumble orphan refund failed (game %s, user %s)", gid, uid)
    return done


# ============================================================== rewards + stats (from the ledger)
def _ranking(g: JumbleGame) -> list[tuple[int, str, int, int]]:
    """[(uid, name, points, words)] best first."""
    return [(uid, r[0], r[1], r[2]) for uid, r in sorted(g.scores.items(), key=lambda kv: (-kv[1][1], -kv[1][2], kv[1][0].lower()))]


async def record_results(g: JumbleGame) -> dict[int, tuple[int, int, int]]:
    """Pay the points reward and write the stats row of every scoring player (groups only, and only when 2+ players scored).
    -> {uid: (diamonds, streak days including today, streak bonus %)} for the players that were paid."""
    rank = [r for r in _ranking(g) if r[2] > 0]
    if g.chat_id > 0 or len(rank) < S.MIN_SCORERS:
        return {}
    from sqlalchemy import select
    from database.models import Transaction, utcnow
    from database.session import db
    from services import economy
    uids = [r[0] for r in rank]
    today = utcnow().date()
    try:
        async with db.tx() as s:
            rows = (await s.execute(select(Transaction.user_id, Transaction.created_at, Transaction.amount)
                                    .where(Transaction.kind == S.KIND, Transaction.user_id.in_(uids),
                                           Transaction.created_at >= utcnow() - timedelta(days=S.LOOKBACK_DAYS)))).all()
    except Exception:
        log.exception("jumble: could not read streaks - paying without streak bonus")
        rows = []
    out: dict[int, tuple[int, int, int]] = {}
    for pos, (uid, name, pts, words) in enumerate(rank, 1):
        mine = [(c, a) for u, c, a in rows if u == uid]
        days = {c.date() for c, _ in mine}
        streak = S.streak_from_days(days, today)
        earned = sum(int(a) for c, a in mine if c.date() == today)
        amount, bonus = S.reward(pts, streak, earned)
        try:
            async with db.tx() as s:
                await economy.apply(s, uid, wallet=amount, kind=S.KIND, ref=S.make_ref(g.chat_id, g.gid, uid, pts, words, pos),
                                    meta={"chat": g.chat_id, "points": pts})
            out[uid] = (amount, streak + (0 if today in days else 1), bonus)
        except Exception:
            log.exception("jumble reward failed for %s in game %s", uid, g.gid)
    return out


async def leaderboard(chat_id: int | None, period: str) -> list[tuple[int, S.Stat]]:
    """Top players of a group (chat_id) or of every group (None) in a period (today / week / month / all)."""
    from sqlalchemy import select
    from database.models import Transaction, utcnow
    from database.session import db
    q = select(Transaction.user_id, Transaction.ref).where(Transaction.kind == S.KIND)
    start = S.since(period, utcnow())
    if start is not None:
        q = q.where(Transaction.created_at >= start)
    if chat_id is not None:
        q = q.where(Transaction.ref.like(f"jumble:{chat_id}:%"))
    async with db.tx() as s:
        rows = (await s.execute(q)).all()
    return S.top(S.aggregate((u, r) for u, r in rows), 10)


async def user_stats(uid: int) -> dict:
    """{'stat': Stat, 'streak': days, 'earned_today': diamonds} for one player (all groups, all time)."""
    from sqlalchemy import select
    from database.models import Transaction, utcnow
    from database.session import db
    async with db.tx() as s:
        rows = (await s.execute(select(Transaction.ref, Transaction.created_at, Transaction.amount)
                                .where(Transaction.kind == S.KIND, Transaction.user_id == uid))).all()
    today = utcnow().date()
    stat = S.aggregate((uid, r) for r, _, _ in rows).get(uid, S.Stat())
    days = {c.date() for _, c, _ in rows}
    return {"stat": stat, "streak": S.streak_from_days(days, today),
            "earned_today": sum(int(a) for _, c, a in rows if c.date() == today)}


# ============================================================== solo (private chat): personal bests
async def record_solo(g: JumbleGame) -> dict[int, tuple[int, int, bool]]:
    """A finished PRIVATE game pays nothing and isn't on the group boards, but it keeps the player's own counters:
    games, words, points and their best single game. -> {uid: (points, previous best, is a new personal best)} for players who scored."""
    if g.chat_id <= 0:
        return {}
    from database.session import db
    from services import gamestats, realm
    out: dict[int, tuple[int, int, bool]] = {}
    scored = [(uid, r[1], r[2]) for uid, r in g.scores.items() if r[1] > 0]
    if not scored:
        return {}
    async with db.tx() as s:
        for uid, pts, words in scored:
            before = await realm.stat(s, uid, S.SOLO_BEST)
            await realm.bump(s, uid, S.SOLO_GAMES)
            await realm.bump(s, uid, S.SOLO_WORDS, words)
            await realm.bump(s, uid, S.SOLO_POINTS, pts)
            new_best = await gamestats.set_best(s, uid, S.SOLO_BEST, pts)
            out[uid] = (pts, before, new_best)
    return out


async def solo_stats(uid: int) -> dict | None:
    from services import realm
    return S.solo_summary(await realm.stats_of(uid))


async def solo_leaderboard(limit: int = 10) -> list[tuple[int, str, int, int]]:
    """Best single solo games -> [(uid, name, best points, solo games)]."""
    from sqlalchemy import select
    from database.models import User, UserStat
    from database.session import db
    async with db.tx() as s:
        rows = (await s.execute(
            select(UserStat.user_id, User.first_name, UserStat.value).join(User, User.id == UserStat.user_id)
            .where(UserStat.key == S.SOLO_BEST, UserStat.value > 0, User.is_flagged == False, User.is_banned == False)  # noqa: E712
            .order_by(UserStat.value.desc(), UserStat.user_id).limit(limit))).all()
        games = {}
        if rows:
            games = {u: v for u, v in (await s.execute(
                select(UserStat.user_id, UserStat.value).where(UserStat.key == S.SOLO_GAMES, UserStat.user_id.in_([r[0] for r in rows])))).all()}
    return [(r[0], r[1], r[2], games.get(r[0], 0)) for r in rows]
