"""Reaction GIFs for the important social actions (hug, kiss, slap, pat ...).

GIFs come live from free anime-reaction APIs (nekos.best first, waifu.pics as fallback), a small pool per action is
cached in memory. If an API is down / slow / blocked, the action is simply sent as normal text - nothing can break.
Disable completely with GIFS=off in .env.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time

import aiohttp
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message

log = logging.getLogger(__name__)

ENABLED = os.getenv("GIFS", "on").strip().lower() not in ("off", "0", "false", "no")
TIMEOUT = aiohttp.ClientTimeout(total=3.5)

# action -> (nekos.best endpoint, waifu.pics endpoint or None).  Only actions listed here ever get a GIF.
GIF_ACTIONS: dict[str, tuple[str, str | None]] = {
    "hug": ("hug", "hug"), "kiss": ("kiss", "kiss"), "slap": ("slap", "slap"), "pat": ("pat", "pat"),
    "cuddle": ("cuddle", "cuddle"), "punch": ("punch", None), "kick": ("kick", "kick"), "bite": ("bite", "bite"),
    "cry": ("cry", "cry"), "dance": ("dance", "dance"), "wave": ("wave", "wave"), "highfive": ("highfive", "highfive"),
    "poke": ("poke", "poke"), "blush": ("blush", "blush"), "wink": ("wink", "wink"), "handhold": ("handhold", "handhold"),
    "bonk": ("bonk", "bonk"), "yeet": ("yeet", "yeet"), "tickle": ("tickle", None),
    "laugh": ("laugh", None), "sleep": ("sleep", None), "facepalm": ("facepalm", None),
    # life events
    "marry": ("kiss", "kiss"), "family": ("hug", "hug"),
}

_pool: dict[str, list[str]] = {}
_down_until: dict[str, float] = {}
_session: aiohttp.ClientSession | None = None
_lock = asyncio.Lock()


def _sess() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=TIMEOUT)
    return _session


async def close() -> None:
    if _session and not _session.closed:
        await _session.close()


async def _nekos(endpoint: str) -> list[str]:
    async with _sess().get(f"https://nekos.best/api/v2/{endpoint}", params={"amount": 8}) as r:
        if r.status != 200:
            return []
        data = await r.json(content_type=None)
        return [x["url"] for x in data.get("results", []) if x.get("url")]


async def _waifu(endpoint: str) -> list[str]:
    async with _sess().get(f"https://api.waifu.pics/sfw/{endpoint}") as r:
        if r.status != 200:
            return []
        data = await r.json(content_type=None)
        return [data["url"]] if data.get("url") else []


async def _refill(action: str) -> None:
    nekos_ep, waifu_ep = GIF_ACTIONS[action]
    for name, fn, ep in (("nekos", _nekos, nekos_ep), ("waifu", _waifu, waifu_ep)):
        if not ep or _down_until.get(name, 0) > time.monotonic() or _down_until.get(f"{name}:{ep}", 0) > time.monotonic():
            continue
        try:
            urls = await fn(ep)
        except Exception as e:  # network / timeout / bad json: park this provider for a minute
            _down_until[name] = time.monotonic() + 60
            log.info("gif provider %s unavailable: %s", name, e)
            continue
        if urls:
            _pool.setdefault(action, []).extend(urls)
            return
        _down_until[f"{name}:{ep}"] = time.monotonic() + 600      # endpoint unknown / empty: don't ask again for 10 min


async def pick(action: str) -> str | None:
    if not ENABLED or action not in GIF_ACTIONS:
        return None
    pool = _pool.get(action)
    if not pool:
        async with _lock:
            if not _pool.get(action):
                await _refill(action)
        pool = _pool.get(action)
    if not pool:
        return None
    return pool.pop(random.randrange(len(pool)))


async def send(m: Message, action: str, text: str, *, reply_markup=None) -> Message:
    """Send `text` as the caption of a reaction GIF; falls back to a plain text message on any problem."""
    url = await pick(action)
    if url:
        try:
            return await m.answer_animation(url, caption=text[:1000], reply_markup=reply_markup)
        except TelegramRetryAfter:
            pass
        except TelegramBadRequest as e:
            log.info("gif %s refused by Telegram (%s) - sending text", url, e)
        except Exception:
            log.exception("gif send failed - sending text")
    return await m.answer(text, reply_markup=reply_markup)


async def bonus(m: Message, action: str) -> None:
    """Send only the GIF (no text) - used after a message that was already sent / edited."""
    url = await pick(action)
    if url:
        try:
            await m.answer_animation(url)
        except Exception:
            pass
