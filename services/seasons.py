"""Weekly + monthly 'Top Earners' prizes.

Score = diamonds EARNED by activity (work, hunts, dungeons, expeditions, missions, trivia, hack, heists) in the
period, read from the transaction ledger. Gambling wins, /pay, market sales and interest do NOT count, so it can't
be bought or laundered through alt accounts. Prizes are paid once per period (idempotent ledger ref)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from database.models import AppState, Transaction, User, utcnow
from database.session import db
from services import economy
from services.errors import GameError

log = logging.getLogger(__name__)

KINDS = ("work", "hunt", "expedition", "dungeon", "mission", "trivia", "hack_win", "heist")
WEEKLY_PRIZES = (3000, 2000, 1000)
MONTHLY_PRIZES = (15000, 10000, 5000)
MIN_SCORE = 100            # nobody gets a prize for a token amount


def week_id(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def month_id(now: datetime) -> str:
    return f"{now.year}-{now.month:02d}"


def week_start(now: datetime) -> datetime:
    d = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return d


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_reset(period: str, now: datetime) -> datetime:
    if period == "week":
        return week_start(now) + timedelta(days=7)
    m = month_start(now)
    return (m + timedelta(days=32)).replace(day=1)


async def standings(start: datetime, end: datetime | None = None, limit: int = 10) -> list[tuple[int, str, int]]:
    q = (select(Transaction.user_id, User.first_name, func.sum(Transaction.amount))
         .join(User, User.id == Transaction.user_id)
         .where(Transaction.kind.in_(KINDS), Transaction.amount > 0, Transaction.created_at >= start,
                User.is_banned == False, User.is_flagged == False)  # noqa: E712
         .group_by(Transaction.user_id, User.first_name).order_by(func.sum(Transaction.amount).desc(), Transaction.user_id)
         .limit(limit))
    if end is not None:
        q = q.where(Transaction.created_at < end)
    async with db.tx() as s:
        rows = (await s.execute(q)).all()
    return [(r[0], r[1] or "Player", int(r[2])) for r in rows if int(r[2]) >= MIN_SCORE]


async def rank_of(uid: int, start: datetime) -> tuple[int, int] | None:
    """(rank, score) of a player in the current period, or None if they have no score."""
    rows = await standings(start, None, 1000)
    for i, (u, _, sc) in enumerate(rows, 1):
        if u == uid:
            return i, sc
    return None


async def _last(key: str) -> str | None:
    async with db.tx() as s:
        row = await s.get(AppState, key)
        return row.value if row else None


async def _set(key: str, val: str) -> None:
    async with db.tx() as s:
        row = await s.get(AppState, key)
        if row:
            row.value = val
        else:
            s.add(AppState(key=key, value=val))


async def settle(period: str, now: datetime | None = None) -> list[tuple[int, str, int, int]]:
    """Pay the PREVIOUS finished period once. Returns [(uid, name, score, prize)] (empty if already paid / nobody)."""
    now = now or utcnow()
    cur_start = week_start(now) if period == "week" else month_start(now)
    prev_end = cur_start
    prev_start = (cur_start - timedelta(days=7)) if period == "week" else month_start(cur_start - timedelta(days=1))
    pid = week_id(prev_start) if period == "week" else month_id(prev_start)
    key = f"season_paid:{period}"
    if await _last(key) == pid:
        return []
    prizes = WEEKLY_PRIZES if period == "week" else MONTHLY_PRIZES
    top = (await standings(prev_start, prev_end, len(prizes)))
    paid = []
    for (uid, name, score), prize in zip(top, prizes):
        try:
            async with db.tx() as s:
                await economy.apply(s, uid, wallet=prize, kind="season_prize", ref=f"season:{period}:{pid}:{uid}",
                                    meta={"period": period, "id": pid, "score": score})
            paid.append((uid, name, score, prize))
        except GameError:
            pass                                   # already paid (ref replay)
    await _set(key, pid)
    await _set(f"season_last:{period}", "|".join(f"{u}:{n[:20].replace('|', ' ').replace(':', ' ')}:{p}" for u, n, _, p in paid)[:250])
    return paid


async def last_winners(period: str) -> list[tuple[int, str, int]]:
    raw = await _last(f"season_last:{period}")
    out = []
    for part in (raw or "").split("|"):
        try:
            u, n, p = part.split(":")
            out.append((int(u), n, int(p)))
        except ValueError:
            continue
    return out


async def season_loop(bot) -> None:
    """Hourly check: pay the finished week / month once and tell the winners."""
    import asyncio
    await asyncio.sleep(90)
    while True:
        try:
            for period, label in (("week", "weekly"), ("month", "monthly")):
                for i, (uid, name, score, prize) in enumerate(await settle(period), 1):
                    try:
                        await bot.send_message(uid, f"🏆 <b>{label.title()} Top Earners — #{i}!</b>\n"
                                                    f"You earned {score:,} 💎 and won a prize of <b>{prize:,}</b> 💎. Congrats! (/season)")
                    except Exception:
                        pass
        except Exception:
            log.exception("season loop error")
        await asyncio.sleep(3600)
