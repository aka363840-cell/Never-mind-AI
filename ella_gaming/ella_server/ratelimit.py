"""Small in-memory token-bucket rate limiter (per process). Behind several workers each worker has its own
buckets, so limits are per worker; use a reverse-proxy limit as well for hard guarantees (see DEPLOYMENT.md)."""
from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, max_keys: int = 50_000):
        self._b: dict[str, tuple[float, float]] = {}       # key -> (tokens, last)
        self._max = max_keys

    def hit(self, key: str, per_minute: int, now: float | None = None) -> float:
        """Consume one token. Returns 0 if allowed, else seconds to wait."""
        t = time.monotonic() if now is None else now
        rate = per_minute / 60.0
        tokens, last = self._b.get(key, (float(per_minute), t))
        tokens = min(float(per_minute), tokens + (t - last) * rate)
        if tokens >= 1:
            self._b[key] = (tokens - 1, t)
            if len(self._b) > self._max:
                self._gc(t)
            return 0.0
        self._b[key] = (tokens, t)
        return max(0.1, (1 - tokens) / rate)

    def _gc(self, t: float) -> None:
        for k in [k for k, (_, last) in self._b.items() if t - last > 300]:
            self._b.pop(k, None)
        if len(self._b) > self._max:                        # still too many: drop the oldest half
            for k, _ in sorted(self._b.items(), key=lambda kv: kv[1][1])[: len(self._b) // 2]:
                self._b.pop(k, None)
