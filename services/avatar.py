"""Profile pictures that keep working. get_user_profile_photos returns nothing when the person hides their photo or when
Telegram hiccups, so every time a photo IS visible its file id is remembered (table avatar_cache, part of the backup)."""
from __future__ import annotations

import logging

from sqlalchemy import update

from database.models import AvatarCache, utcnow
from database.session import db, insert_ignore
from services.kv import kv

log = logging.getLogger(__name__)
REFRESH_S = 3 * 86400


def _best(sizes):
    sizes = [x for x in (sizes or []) if getattr(x, "file_id", None)]
    if not sizes:
        return None
    big = [x for x in sizes if max(x.width or 0, x.height or 0) >= 320]          # sharp enough for a card, small enough to be quick
    return (min(big, key=lambda x: x.width or 0) if big else max(sizes, key=lambda x: x.width or 0)).file_id


async def remembered(uid: int) -> str | None:
    async with db.tx() as s:
        row = await s.get(AvatarCache, uid)
        return row.file_id if row else None


async def _store(uid: int, file_id: str) -> None:
    async with db.tx() as s:
        res = await s.execute(update(AvatarCache).where(AvatarCache.user_id == uid).values(file_id=file_id, checked_at=utcnow()))
        if res.rowcount == 0:
            await insert_ignore(s, AvatarCache, dict(user_id=uid, file_id=file_id, checked_at=utcnow()))


async def current(bot, uid: int) -> str | None:
    """The person's photo right now (and remember it); None if Telegram shows none."""
    try:
        photos = await bot.get_user_profile_photos(uid, limit=1)
    except Exception as e:
        log.info("profile photo lookup for %s failed: %s", uid, e)
        return None
    if photos.total_count and photos.photos:
        fid = _best(photos.photos[0])
        if fid:
            await _store(uid, fid)
            return fid
    return None


async def best(bot, uid: int) -> str | None:
    """Live photo, else the last one we ever saw, else None (the caller then draws an initials picture)."""
    return await current(bot, uid) or await remembered(uid)


async def refresh_quietly(bot, uid: int) -> None:
    """Called when someone writes: at most every 3 days it re-reads their photo, so the cache fills up on its own."""
    try:
        if await kv.set_nx(f"av:{uid}", REFRESH_S):
            await current(bot, uid)
    except Exception:
        pass
