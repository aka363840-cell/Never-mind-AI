"""/afk - away status. Kept in memory for speed (one small dict) and in the `afk` table so it survives restarts and backups."""
from __future__ import annotations

import time

from sqlalchemy import delete, select

from database.models import Afk, User, aware
from database.session import db

_afk: dict[int, tuple[str, float]] | None = None          # uid -> (reason, since_epoch)


async def _load() -> dict[int, tuple[str, float]]:
    global _afk
    if _afk is None:
        async with db.tx() as s:
            rows = (await s.execute(select(Afk))).scalars().all()
        _afk = {r.user_id: (r.reason or "", aware(r.since).timestamp()) for r in rows}
    return _afk


async def is_any() -> bool:
    return bool(await _load())


async def get(uid: int) -> tuple[str, float] | None:
    return (await _load()).get(uid)


async def set_away(uid: int, reason: str) -> None:
    d = await _load()
    reason = reason.strip()[:100]
    now = time.time()
    d[uid] = (reason, now)
    async with db.tx() as s:
        await s.execute(delete(Afk).where(Afk.user_id == uid))
        s.add(Afk(user_id=uid, reason=reason))


async def back(uid: int) -> tuple[str, float] | None:
    """Clear the status. Returns what it was (None if they were not away)."""
    d = await _load()
    old = d.pop(uid, None)
    if old is not None:
        async with db.tx() as s:
            await s.execute(delete(Afk).where(Afk.user_id == uid))
    return old


async def ids_by_username(names: list[str]) -> list[int]:
    if not names:
        return []
    async with db.tx() as s:
        return list((await s.execute(select(User.id).where(User.username_lc.in_([n.lower() for n in names])))).scalars())
