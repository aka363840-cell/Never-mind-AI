"""XP / levels, missions, achievements. All driven by real DB events, never by AI."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Achievement, InventoryItem, Mission, User, Wallet, UserPower, utcnow
from database.session import db, insert_ignore
from game.rules import ACHIEVEMENTS, R
from services.errors import GameError


async def add_xp(s: AsyncSession, uid: int, amount: int) -> int:
    """Returns new level."""
    u = (await s.execute(select(User).where(User.id == uid).with_for_update())).scalar_one()
    await s.refresh(u)
    xp, lvl = u.xp + amount, u.level
    while xp >= lvl * R.xp_per_level:
        xp -= lvl * R.xp_per_level
        lvl += 1
    await s.execute(update(User).where(User.id == uid).values(xp=xp, level=lvl))
    if lvl > u.level:
        from services import announce
        announce.QUEUE.append((uid, u.level, lvl))
    if lvl >= 5: await _unlock(s, uid, "level_5")
    if lvl >= 10: await _unlock(s, uid, "level_10")
    return lvl


def _periods() -> dict[str, date]:
    today = utcnow().date()
    return {"daily": today, "weekly": today - timedelta(days=today.weekday())}


async def ensure_missions(s: AsyncSession, uid: int) -> None:
    per = _periods()
    for period, defs in (("daily", R.daily_missions), ("weekly", R.weekly_missions)):
        for key, title, event, goal, reward in defs:
            await insert_ignore(s, Mission, dict(user_id=uid, mission_key=key, period=period,
                                period_start=per[period], event=event, title=title, goal=goal, reward=reward))


async def emit(s: AsyncSession, uid: int, event: str, amount: int = 1) -> None:
    """Called inside the same transaction as the real event, so progress can't be faked or lost."""
    await ensure_missions(s, uid)
    per = _periods()
    for period, start in per.items():
        await s.execute(update(Mission).where(
            Mission.user_id == uid, Mission.event == event, Mission.period == period,
            Mission.period_start == start, Mission.claimed == False)  # noqa: E712
            .values(progress=Mission.progress + amount))
    await check_achievements(s, uid, event)


async def missions_for(uid: int) -> list[Mission]:
    async with db.tx() as s:
        await ensure_missions(s, uid)
        per = _periods()
        rows = (await s.execute(select(Mission).where(Mission.user_id == uid).where(
            ((Mission.period == "daily") & (Mission.period_start == per["daily"])) |
            ((Mission.period == "weekly") & (Mission.period_start == per["weekly"]))).order_by(Mission.id))).scalars().all()
        return list(rows)


async def claim_mission(uid: int, mission_id: int) -> tuple[str, int]:
    from services import economy
    async with db.tx() as s:
        m = (await s.execute(select(Mission).where(Mission.id == mission_id, Mission.user_id == uid))).scalar_one_or_none()
        if m is None:
            raise GameError("❌ Mission not found.")
        per = _periods()
        if m.period_start != per[m.period]:
            raise GameError("⌛ That mission has expired.")
        res = await s.execute(update(Mission).where(Mission.id == m.id, Mission.claimed == False,  # noqa: E712
                                                    Mission.progress >= Mission.goal).values(claimed=True))
        if res.rowcount != 1:
            raise GameError("❌ Mission not complete (or already claimed).")
        await economy.apply(s, uid, wallet=m.reward, kind="mission", ref=f"mission:{m.id}")
        await add_xp(s, uid, 10)
        return m.title, m.reward


# -------------------------------------------------------------- achievements
async def _unlock(s: AsyncSession, uid: int, key: str) -> bool:
    return await insert_ignore(s, Achievement, dict(user_id=uid, key=key, unlocked_at=utcnow()))


async def check_achievements(s: AsyncSession, uid: int, event: str) -> None:
    u = await s.get(User, uid)
    await s.refresh(u)
    if event == "kill" and u.kills >= 1:
        await _unlock(s, uid, "first_blood")
    if event == "duel_win" and u.wins >= 5:   # wins also counts kills; duel-specific check below
        pass
    if event == "duel_win":
        from database.models import CombatRecord
        n = (await s.execute(select(func.count()).select_from(CombatRecord).where(
            CombatRecord.kind == "duel", CombatRecord.winner_id == uid))).scalar()
        if n >= 5:
            await _unlock(s, uid, "duelist")
    if event == "rob_success":
        from database.models import Transaction
        n = (await s.execute(select(func.count()).select_from(Transaction).where(
            Transaction.user_id == uid, Transaction.kind == "rob_in"))).scalar()
        if n >= 5:
            await _unlock(s, uid, "thief")
    if event in ("earn", "daily_claim"):
        w = (await s.execute(select(Wallet).where(Wallet.user_id == uid))).scalar_one_or_none()
        if w:
            await s.refresh(w)
            if w.balance + w.bank >= 10_000:
                await _unlock(s, uid, "tycoon")
    if event == "shop_buy":
        n = (await s.execute(select(func.count()).select_from(InventoryItem).where(InventoryItem.user_id == uid))).scalar()
        if n >= 5:
            await _unlock(s, uid, "collector")
        if (await s.execute(select(func.count()).select_from(UserPower).where(UserPower.user_id == uid))).scalar() >= 1:
            await _unlock(s, uid, "powered")


async def achievements_of(uid: int) -> set[str]:
    async with db.tx() as s:
        return set((await s.execute(select(Achievement.key).where(Achievement.user_id == uid))).scalars())
