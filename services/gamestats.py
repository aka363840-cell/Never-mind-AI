"""Small helpers for lifetime game counters kept in the existing `user_stats` table (no new tables).
realm.bump() only adds; this adds "keep the best value" (highest or lowest)."""
from __future__ import annotations

from sqlalchemy import update

from database.models import UserStat
from database.session import insert_ignore


async def set_best(s, uid: int, key: str, value: int, lower: bool = False) -> bool:
    """Keep the best value of a counter inside the caller's transaction (0 / missing = not set yet).
    True when `value` is a new personal best."""
    from services import realm
    cur = await realm.stat(s, uid, key)
    if cur and (value >= cur if lower else value <= cur):
        return False
    res = await s.execute(update(UserStat).where(UserStat.user_id == uid, UserStat.key == key).values(value=value))
    if res.rowcount == 0 and not await insert_ignore(s, UserStat, dict(user_id=uid, key=key, value=value)):
        await s.execute(update(UserStat).where(UserStat.user_id == uid, UserStat.key == key).values(value=value))
    return True
