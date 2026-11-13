"""Chain of AI providers: if one fails the next answers, and a SLOW one never makes the user wait.

How a reply is produced
1. Providers are tried in priority order (AI_PROVIDERS). Parked (recently failed / out of quota) ones are skipped.
2. The first one starts alone. If it fails, the next starts INSTANTLY. If it is merely slow (no answer after
   `hedge` seconds) the next one is started IN PARALLEL and whichever answers first wins; the loser is cancelled.
3. The user only sees 'busy' when every provider failed or the total `budget` ran out.
Imports nothing from the game (like the rest of the ai package).
"""
from __future__ import annotations

import asyncio
import logging
import time

from ai.base import AIError, AIQuotaExceeded, AIUnavailable, ChatTurn

log = logging.getLogger(__name__)


class MultiProvider:
    name = "multi"

    def __init__(self, providers: list, *, hedge: float = 2.5, budget: float = 10.0):
        if not providers:
            raise ValueError("no AI provider configured")
        self._providers = list(providers)
        self._hedge = hedge
        self.budget = budget
        self._rest: dict[int, float] = {}          # id(provider) -> parked until (monotonic)
        self.key_count = sum(getattr(p, "key_count", 1) for p in providers)
        self.last_error = ""
        self.last_winner = ""

    # ------------------------------------------------------------------ lifecycle
    async def close(self) -> None:
        await asyncio.gather(*(p.close() for p in self._providers if hasattr(p, "close")), return_exceptions=True)

    def names(self) -> str:
        return ", ".join(p.name for p in self._providers)

    # ------------------------------------------------------------------ generation
    def _usable(self) -> list:
        now = time.monotonic()
        return [p for p in self._providers if self._rest.get(id(p), 0.0) <= now]

    def _park(self, p, e: Exception) -> None:
        if isinstance(e, AIQuotaExceeded):
            secs = max(10, min(e.retry_after, 600))
        elif getattr(e, "auth", False):
            secs = 600
        else:
            secs = 20
        self._rest[id(p)] = time.monotonic() + secs
        self.last_error = f"{p.name}: {e}"
        log.warning("AI provider %s failed (%s) -> skipped for %ds", p.name, e or type(e).__name__, secs)

    async def generate(self, system: str, turns: list[ChatTurn], *, temperature: float = 0.9, json_mode: bool = False) -> str:
        queue = self._usable()
        if not queue:                                   # everything parked: don't wait for the park time, probe the one that frees first
            soonest = min(self._providers, key=lambda p: self._rest.get(id(p), 0.0))
            self._rest.pop(id(soonest), None)
            queue = [soonest]
        t_end = time.monotonic() + self.budget
        pending: dict[asyncio.Task, object] = {}
        errors: list[Exception] = []

        def launch() -> None:
            p = queue.pop(0)
            task = asyncio.create_task(p.generate(system, turns, temperature=temperature, json_mode=json_mode))
            pending[task] = p

        try:
            launch()
            while pending:
                left = t_end - time.monotonic()
                if left <= 0:
                    break
                wait = min(self._hedge, left) if queue else left
                done, _ = await asyncio.wait(pending, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    p = pending.pop(task)
                    try:
                        text = task.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception as e:  # noqa: BLE001
                        errors.append(e)
                        self._park(p, e)
                        continue
                    self.last_winner = p.name
                    return text
                if queue:                                # failed fast, or too slow: bring the next provider in now
                    launch()
                elif not pending:
                    break
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        quota = next((e for e in errors if isinstance(e, AIQuotaExceeded)), None)
        if quota is not None and all(isinstance(e, AIQuotaExceeded) for e in errors):
            raise quota
        raise next((e for e in errors if isinstance(e, AIError)), None) or AIUnavailable("AI took too long")

    # ------------------------------------------------------------------ owner diagnostics (/aitest, startup log)
    async def self_test(self) -> tuple[bool, str]:
        async def one(p) -> tuple[bool, str]:
            test = getattr(p, "self_test", None)
            if test is None:
                return True, f"{p.name}: no self-test"
            try:
                return await test()
            except Exception as e:  # noqa: BLE001
                return False, f"{p.name}: ❌ {type(e).__name__}: {e}"

        results = await asyncio.gather(*(one(p) for p in self._providers))
        ok = any(r[0] for r in results)
        good = sum(1 for r in results if r[0])
        head = f"{good}/{len(self._providers)} providers working ({self.names()}). If one fails the next answers automatically."
        return ok, head + "\n\n" + "\n\n".join(r[1] for r in results)
