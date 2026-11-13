from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Cooldown, aware, utcnow
from database.session import insert_ignore


async def remaining(s: AsyncSession, uid: int, key: str) -> int:
    exp = (await s.execute(select(Cooldown.expires_at).where(Cooldown.user_id == uid, Cooldown.key == key))).scalar()
    if exp is None:
        return 0
    left = (aware(exp) - utcnow()).total_seconds()
    return math.ceil(left) if left > 0 else 0


async def claim(s: AsyncSession, uid: int, key: str, seconds: int) -> int:
    """Atomically start a cooldown. Returns 0 on success, else seconds remaining.
    Two concurrent claims: exactly one wins (single UPDATE / unique insert)."""
    t = utcnow()
    new = t + timedelta(seconds=seconds)
    res = await s.execute(update(Cooldown).where(Cooldown.user_id == uid, Cooldown.key == key,
                                                 Cooldown.expires_at <= t).values(expires_at=new))
    if res.rowcount == 1:
        return 0
    if await insert_ignore(s, Cooldown, dict(user_id=uid, key=key, expires_at=new)):
        return 0
    return max(1, await remaining(s, uid, key))


async def set_(s: AsyncSession, uid: int, key: str, seconds: int) -> None:
    new = utcnow() + timedelta(seconds=seconds)
    res = await s.execute(update(Cooldown).where(Cooldown.user_id == uid, Cooldown.key == key).values(expires_at=new))
    if res.rowcount == 0:
        await insert_ignore(s, Cooldown, dict(user_id=uid, key=key, expires_at=new))


async def clear(s: AsyncSession, uid: int, key: str) -> None:
    from sqlalchemy import delete
    await s.execute(delete(Cooldown).where(Cooldown.user_id == uid, Cooldown.key == key))
