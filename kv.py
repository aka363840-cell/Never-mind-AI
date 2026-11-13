"""Redis-backed ephemeral state (rate limits, nonces) with automatic in-memory fallback.
PostgreSQL stays the source of truth; losing this layer never corrupts game state."""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


class KV:
    def __init__(self, url: str | None = None) -> None:
        self.configure(url)

    def configure(self, url: str | None) -> None:
        """Re-point in place (modules hold a reference to the singleton, so never rebind it)."""
        self._mem: dict[str, tuple[str, float]] = {}
        self._r = None
        if url:
            try:
                import redis.asyncio as aioredis
                self._r = aioredis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
            except Exception:  # pragma: no cover
                log.warning("Redis unavailable, using in-memory KV")

    def _purge(self) -> None:
        if len(self._mem) > 5000:
            now = time.monotonic()
            self._mem = {k: v for k, v in self._mem.items() if v[1] > now}

    async def _try(self, fn):
        if self._r is None:
            return None, False
        try:
            return await fn(self._r), True
        except Exception as e:
            log.warning("Redis error (%s) – falling back to memory", e.__class__.__name__)
            return None, False

    async def incr_window(self, key: str, window_s: int) -> int:
        async def f(r):
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, window_s)
            return n
        val, ok = await self._try(f)
        if ok:
            return val
        now = time.monotonic()
        v, exp = self._mem.get(key, ("0", 0))
        if exp <= now:
            v, exp = "0", now + window_s
        self._mem[key] = (str(int(v) + 1), exp)
        self._purge()
        return int(v) + 1

    async def set_nx(self, key: str, ttl_s: float) -> bool:
        async def f(r):
            return bool(await r.set(key, "1", nx=True, px=int(ttl_s * 1000)))
        val, ok = await self._try(f)
        if ok:
            return val
        now = time.monotonic()
        cur = self._mem.get(key)
        if cur and cur[1] > now:
            return False
        self._mem[key] = ("1", now + ttl_s)
        self._purge()
        return True

    async def put(self, key: str, value: str, ttl_s: int) -> None:
        _, ok = await self._try(lambda r: r.set(key, value, ex=ttl_s))
        if not ok:
            self._mem[key] = (value, time.monotonic() + ttl_s)

    async def get(self, key: str) -> str | None:
        val, ok = await self._try(lambda r: r.get(key))
        if ok:
            return val
        cur = self._mem.get(key)
        return cur[0] if cur and cur[1] > time.monotonic() else None

    async def take(self, key: str) -> str | None:
        """Atomic get-and-delete (one-time nonces)."""
        val, ok = await self._try(lambda r: r.getdel(key))
        if ok:
            return val
        cur = self._mem.pop(key, None)
        return cur[0] if cur and cur[1] > time.monotonic() else None

    async def ttl(self, key: str) -> int:
        val, ok = await self._try(lambda r: r.ttl(key))
        if ok:
            return max(0, int(val))
        cur = self._mem.get(key)
        return max(0, int(cur[1] - time.monotonic())) if cur else 0


kv = KV()


def init_kv(url: str | None) -> None:
    kv.configure(url)
