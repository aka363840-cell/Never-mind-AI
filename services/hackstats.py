"""Hack extras: lifetime stats + leaderboard, and the DAILY code (one attempt per player per UTC day, played in a private chat).

Money rules (same as the rest of the bot): every diamond change goes through economy.apply() with an idempotency ref.
  daily reward   kind hack_daily   ref hackdaily:<YYYYMMDD>:<uid>   meta {"tries": n}   (the ledger row is also the record of the solve)
Progress of an unfinished daily attempt lives in the KV layer (Redis, or memory), so it survives a restart when Redis is configured.
No new tables, no migration.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from config import get_settings
from database.models import Transaction, User, UserStat, utcnow
from database.session import db
from game import hack as H, hack_stats as HS, jumble_stats as JS
from services import economy, gamestats
from services.errors import GameError
from services.kv import kv

log = logging.getLogger(__name__)
TTL_S = 2 * 86400


# ============================================================== lifetime stats
async def record_game(uids: list[int], winner: int | None, tries: int | None) -> int | None:
    """One finished group game: everybody played, the winner won (tries = tries of a CRACK, None for last-player-standing).
    -> the winner's new win total (None without a winner)."""
    from services import realm
    total = None
    async with db.tx() as s:
        for uid in uids:
            await realm.bump(s, uid, HS.K_PLAY)
        if winner is not None:
            await realm.bump(s, winner, HS.K_WIN)
            total = await realm.stat(s, winner, HS.K_WIN)
            if tries:
                await realm.bump(s, winner, HS.K_CRACK)
                await realm.bump(s, winner, HS.K_TRIES, tries)
                await gamestats.set_best(s, winner, HS.K_FAST, tries, lower=True)
    return total


async def user_summary(uid: int) -> dict:
    from services import realm
    return HS.summarize(await realm.stats_of(uid))


async def leaderboard(limit: int = 10) -> list[tuple[int, str, int, int]]:
    """Top players by wins -> [(uid, name, wins, games played)]."""
    async with db.tx() as s:
        rows = (await s.execute(
            select(UserStat.user_id, User.first_name, UserStat.value).join(User, User.id == UserStat.user_id)
            .where(UserStat.key == HS.K_WIN, UserStat.value > 0, User.is_flagged == False, User.is_banned == False)  # noqa: E712
            .order_by(UserStat.value.desc(), UserStat.user_id).limit(limit))).all()
        plays = {}
        if rows:
            plays = {u: v for u, v in (await s.execute(
                select(UserStat.user_id, UserStat.value).where(UserStat.key == HS.K_PLAY, UserStat.user_id.in_([r[0] for r in rows])))).all()}
    return [(r[0], r[1], r[2], plays.get(r[0], 0)) for r in rows]


# ============================================================== daily code
@dataclass
class DailyGame:
    uid: int
    day: date
    secret: str
    guesses: list[str] = field(default_factory=list)
    n: int = HS.DAILY_LEN
    lock: object = None


DAILY: dict[int, DailyGame] = {}                   # uid -> the player's open daily attempt (private chat)


def today() -> date:
    return utcnow().date()


def secret_for(day: date) -> str:
    return HS.daily_code(day, get_settings().bot_token or "dev-secret")


def seconds_to_reset(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return int((now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - now).total_seconds())


def _gkey(day: date, uid: int) -> str:
    return f"hd:g:{day:%Y%m%d}:{uid}"


def _fkey(day: date, uid: int) -> str:
    return f"hd:f:{day:%Y%m%d}:{uid}"


async def load_guesses(uid: int, day: date) -> list[str]:
    raw = await kv.get(_gkey(day, uid))
    return [g for g in (raw or "").split(",") if g]


async def save_guesses(uid: int, day: date, guesses: list[str]) -> None:
    await kv.put(_gkey(day, uid), ",".join(guesses), TTL_S)


async def mark_failed(uid: int, day: date) -> None:
    await kv.put(_fkey(day, uid), "1", TTL_S)


async def has_failed(uid: int, day: date) -> bool:
    return await kv.get(_fkey(day, uid)) is not None


async def solved_today(uid: int, day: date) -> int | None:
    """Tries of the player's solved daily code, or None when they have not solved it today."""
    async with db.tx() as s:
        meta = (await s.execute(select(Transaction.meta).where(Transaction.kind == HS.DAILY_KIND, Transaction.user_id == uid,
                                                                 Transaction.ref == HS.daily_ref(day, uid)))).first()
    if meta is None:
        return None
    try:
        return int(json.loads(meta[0] or "{}").get("tries", 0)) or None
    except (ValueError, TypeError):
        return None


async def daily_streak(uid: int, day: date) -> int:
    """Consecutive days (up to `day`) with a solved daily code."""
    async with db.tx() as s:
        rows = (await s.execute(select(Transaction.created_at).where(
            Transaction.kind == HS.DAILY_KIND, Transaction.user_id == uid,
            Transaction.created_at >= utcnow() - timedelta(days=HS.LOOKBACK_DAYS)))).all()
    return JS.streak_from_days({r[0].date() for r in rows}, day)


async def pay_daily(uid: int, day: date, tries: int) -> tuple[int, int, int]:
    """Pay a solved daily code ONCE (the ledger ref makes a replay a no-op) -> (diamonds, streak days incl. today, bonus %)."""
    streak = await daily_streak(uid, day)                      # days in a row before today's solve (today has no row yet)
    amount, bonus = HS.daily_reward(tries, streak)
    try:
        async with db.tx() as s:
            await economy.apply(s, uid, wallet=amount, kind=HS.DAILY_KIND, ref=HS.daily_ref(day, uid), meta={"tries": tries})
    except GameError:
        return 0, streak, bonus                                # already paid
    return amount, streak + 1, bonus


async def daily_board(day: date, limit: int = 10) -> list[tuple[int, str, int]]:
    """Today's solvers, fewest tries first (earlier solve wins a tie) -> [(uid, name, tries)]."""
    async with db.tx() as s:
        rows = (await s.execute(
            select(Transaction.user_id, User.first_name, Transaction.meta, Transaction.created_at).join(User, User.id == Transaction.user_id)
            .where(Transaction.kind == HS.DAILY_KIND, Transaction.ref.like(HS.daily_ref_prefix(day) + "%")))).all()
    out = []
    for uid, name, meta, at in rows:
        try:
            tries = int(json.loads(meta or "{}").get("tries", 0))
        except (ValueError, TypeError):
            continue
        if tries > 0:
            out.append((tries, at, uid, name))
    out.sort(key=lambda r: (r[0], r[1]))
    return [(uid, name, tries) for tries, _at, uid, name in out[:limit]]


async def daily_solved_count(uid: int) -> int:
    async with db.tx() as s:
        return len((await s.execute(select(Transaction.id).where(Transaction.kind == HS.DAILY_KIND, Transaction.user_id == uid))).all())
