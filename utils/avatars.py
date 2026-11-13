"""Telegram profile pictures for the /familytree picture.

Best effort by design: a user with no photo, a photo hidden by their privacy settings, a deleted account or a Telegram
timeout simply gets no entry, and the tree falls back to the initial letter for that person. This module never raises.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time

log = logging.getLogger(__name__)

TTL_HIT = 6 * 3600          # a fetched photo is reused for 6 hours
TTL_MISS = 20 * 60          # "no photo / couldn't fetch" is retried after 20 minutes
MAX_CACHE = int(os.getenv("AVATAR_CACHE", "100"))
MAX_BYTES = 2_000_000
WANT_PX = 160               # smallest size that still looks sharp in a ~100px circle
TIMEOUT = 6.0               # per user
CONCURRENCY = 6

_cache: dict[int, tuple[float, bytes | None]] = {}


def _pick(sizes) -> object | None:
    """Smallest photo size that is at least WANT_PX wide, else the largest available."""
    sizes = [s for s in (sizes or []) if getattr(s, "file_id", None)]
    if not sizes:
        return None
    big_enough = [s for s in sizes if max(s.width or 0, s.height or 0) >= WANT_PX]
    return min(big_enough, key=lambda s: s.width or 0) if big_enough else max(sizes, key=lambda s: s.width or 0)


async def _fetch_one(bot, uid: int) -> bytes | None:
    photos = await bot.get_user_profile_photos(uid, offset=0, limit=1)
    if not photos.photos:
        return None
    size = _pick(photos.photos[0])
    if size is None:
        return None
    buf = io.BytesIO()
    await bot.download(size.file_id, destination=buf)
    data = buf.getvalue()
    return data if 0 < len(data) <= MAX_BYTES else None


async def fetch(bot, user_ids) -> dict[int, bytes]:
    """-> {user_id: image bytes} for every user whose profile picture could be loaded."""
    ids = list(dict.fromkeys(i for i in user_ids if isinstance(i, int) and i > 0))
    now = time.monotonic()
    out: dict[int, bytes] = {}
    todo: list[int] = []
    for i in ids:
        hit = _cache.get(i)
        if hit and hit[0] > now:
            if hit[1]:
                out[i] = hit[1]
        else:
            todo.append(i)
    if not todo:
        return out

    sem = asyncio.Semaphore(CONCURRENCY)

    async def job(uid: int) -> None:
        data = None
        async with sem:
            try:
                data = await asyncio.wait_for(_fetch_one(bot, uid), TIMEOUT)
            except Exception as e:                      # privacy settings, blocked, network, flood control ...
                log.debug("profile photo for %s unavailable: %s", uid, e)
        _cache[uid] = (time.monotonic() + (TTL_HIT if data else TTL_MISS), data)
        if data:
            out[uid] = data

    await asyncio.gather(*(job(u) for u in todo))
    if len(_cache) > MAX_CACHE:                         # drop expired first, then the oldest
        t = time.monotonic()
        live = {k: v for k, v in _cache.items() if v[0] > t}
        _cache.clear()
        _cache.update(dict(sorted(live.items(), key=lambda kv: kv[1][0])[-MAX_CACHE // 2:]))
    return out
