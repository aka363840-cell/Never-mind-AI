from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Bounty, Transaction, aware, utcnow
from database.session import db
from game.rules import R
from services import economy, users
from services.errors import GameError


async def place(placer: int, target: int, amount: int) -> int:
    if not isinstance(amount, int) or not (R.bounty_min <= amount <= R.bounty_max):
        raise GameError(f"❌ Bounty must be between {R.bounty_min:,} and {R.bounty_max:,} 💎.")
    if placer == target:
        raise GameError("❌ You can't place a bounty on yourself.")
    async with db.tx() as s:
        await users.require(s, placer)
        await users.require(s, target)
        dup = (await s.execute(select(func.count()).select_from(Bounty).where(
            Bounty.placer_id == placer, Bounty.target_id == target, Bounty.status == "active"))).scalar()
        if dup:
            raise GameError("❌ You already have an active bounty on that player.")
        b = Bounty(placer_id=placer, target_id=target, amount=amount,
                   expires_at=utcnow() + timedelta(seconds=R.bounty_ttl_s))
        s.add(b)
        await s.flush()
        await economy.apply(s, placer, wallet=-amount, kind="bounty_escrow", counterparty=target, ref=f"bounty:{b.id}:escrow")
        return b.id


async def claim_for_kill(s: AsyncSession, winner: int, loser: int) -> int:
    """Pay all eligible active bounties on `loser` to `winner` (inside the combat transaction)."""
    now = utcnow()
    rows = (await s.execute(select(Bounty).where(Bounty.target_id == loser, Bounty.status == "active",
                                                  Bounty.expires_at > now))).scalars().all()
    total = 0
    for b in rows:
        if b.placer_id == winner or loser == winner:
            continue
        # collusion guard: no payouts to someone who traded diamonds with the placer in the last 24h
        day = now - timedelta(days=1)
        traded = (await s.execute(select(func.count()).select_from(Transaction).where(
            Transaction.user_id == winner, Transaction.counterparty_id == b.placer_id,
            Transaction.kind.in_(("pay_in", "pay_out")), Transaction.created_at >= day))).scalar()
        if traded:
            continue
        r = await s.execute(update(Bounty).where(Bounty.id == b.id, Bounty.status == "active")
                            .values(status="claimed", claimed_by=winner))
        if r.rowcount == 1:
            await economy.apply(s, winner, wallet=b.amount, kind="bounty_claim", counterparty=b.placer_id,
                                ref=f"bounty:{b.id}:claim")
            total += b.amount
    return total


async def cancel(placer: int, bounty_id: int) -> int:
    async with db.tx() as s:
        r = await s.execute(update(Bounty).where(Bounty.id == bounty_id, Bounty.placer_id == placer,
                                                 Bounty.status == "active").values(status="cancelled"))
        if r.rowcount != 1:
            raise GameError("❌ No active bounty of yours with that ID.")
        b = await s.get(Bounty, bounty_id)
        expired = aware(b.expires_at) <= utcnow()
        refund = b.amount if expired else int(b.amount * R.bounty_cancel_refund_pct)   # remainder is burned (fee)
        await economy.apply(s, placer, wallet=refund, kind="bounty_refund", ref=f"bounty:{bounty_id}:cancel")
        return refund


async def expire_stale() -> int:
    """Refund expired bounties in full. Safe to call repeatedly / after restarts."""
    n = 0
    async with db.tx() as s:
        rows = (await s.execute(select(Bounty.id).where(Bounty.status == "active", Bounty.expires_at <= utcnow()))).scalars().all()
    for bid in rows:
        try:
            async with db.tx() as s:
                r = await s.execute(update(Bounty).where(Bounty.id == bid, Bounty.status == "active").values(status="expired"))
                if r.rowcount == 1:
                    b = await s.get(Bounty, bid)
                    await economy.apply(s, b.placer_id, wallet=b.amount, kind="bounty_refund", ref=f"bounty:{bid}:expire")
                    n += 1
        except GameError:
            pass
    return n


async def active_on(target: int, limit: int = 10) -> list[Bounty]:
    await expire_stale()
    async with db.tx() as s:
        return list((await s.execute(select(Bounty).where(Bounty.target_id == target, Bounty.status == "active")
                                     .order_by(Bounty.amount.desc()).limit(limit))).scalars())


async def wanted_page(page: int = 0, per: int = 10) -> tuple[list[tuple[int, str, int, int]], int]:
    """One page of the most-wanted board (one row per target) -> (rows, total targets)."""
    from database.models import User
    await expire_stale()
    async with db.tx() as s:
        total = (await s.execute(select(func.count(func.distinct(Bounty.target_id))).where(Bounty.status == "active"))).scalar() or 0
        rows = (await s.execute(select(Bounty.target_id, User.first_name, func.sum(Bounty.amount), func.count())
                                .join(User, User.id == Bounty.target_id).where(Bounty.status == "active")
                                .group_by(Bounty.target_id, User.first_name).order_by(func.sum(Bounty.amount).desc(), Bounty.target_id)
                                .limit(per).offset(max(0, page) * per))).all()
        return [(r[0], r[1], int(r[2]), int(r[3])) for r in rows], int(total)


async def top_active(limit: int = 10) -> list[tuple[int, str, int, int]]:
    from database.models import User
    await expire_stale()
    async with db.tx() as s:
        rows = (await s.execute(select(Bounty.target_id, User.first_name, func.sum(Bounty.amount), func.count())
                                .join(User, User.id == Bounty.target_id).where(Bounty.status == "active")
                                .group_by(Bounty.target_id, User.first_name).order_by(func.sum(Bounty.amount).desc())
                                .limit(limit))).all()
        return [(r[0], r[1], int(r[2]), int(r[3])) for r in rows]
