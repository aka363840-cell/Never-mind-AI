"""Academy background work: ONE loop (academy_loop, started in bot.py) that runs every registered tick.

Add a tick in any services/academy_*.py module:

    from services.academy_ticks import tick

    @tick                       # or @tick(every=300) to run at most every 5 minutes (default: every loop = 60 s)
    async def my_tick(bot, now): ...

and list the module name in TICK_MODULES below so the loop imports it. Ticks must be idempotent (a restart or a slow
tick may run them again). Never create another loop. A crashing tick is logged and the others still run.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from database.models import utcnow
from database.session import db
from game import academy_rules as AR

log = logging.getLogger(__name__)

TICK_MODULES: tuple[str, ...] = ("services.academy_exams", "services.academy_social", "services.academy_adventures", "services.academy_reminders", "services.academy_graduation")   # Part 6: season_open, enroll/enrolled reminders, recap, purge, ceremony · Part 3: exam_close · Part 4: rival_pairing, club_battle_open, social_close · Part 5: adventure_expire          # later parts append their module names here
TICK_TIMEOUT_S = 50
MARKER_KEY = "academy:marker"               # "<last season announced as started>:<last announced as ended>" (AppState row)

TickFn = Callable[..., Awaitable[None]]


@dataclass
class _Tick:
    name: str
    fn: TickFn
    every: int


_ticks: dict[str, _Tick] = {}
_last_run: dict[str, float] = {}


def tick(fn: TickFn | None = None, *, every: int = 0, name: str | None = None):
    def deco(f: TickFn) -> TickFn:
        n = name or f"{f.__module__}.{f.__name__}"
        _ticks[n] = _Tick(n, f, every)
        return f
    return deco(fn) if fn is not None else deco


def registered() -> list[str]:
    return list(_ticks)


def load_tick_modules() -> None:
    for m in TICK_MODULES:
        try:
            importlib.import_module(m)
        except Exception:
            log.exception("could not import academy tick module %s", m)


async def run_ticks(bot, now: datetime | None = None, force: bool = False) -> dict[str, str]:
    """Run every due tick once. Returns {name: ok | skipped | error | timeout}. `force` ignores the `every` throttle."""
    now = now or utcnow()
    out: dict[str, str] = {}
    mono = time.monotonic()
    for t in list(_ticks.values()):
        if not force and t.every and mono - _last_run.get(t.name, -1e12) < t.every:
            out[t.name] = "skipped"
            continue
        _last_run[t.name] = mono
        try:
            await asyncio.wait_for(t.fn(bot, now), TICK_TIMEOUT_S)
            out[t.name] = "ok"
        except asyncio.TimeoutError:
            log.error("academy tick %s timed out", t.name)
            out[t.name] = "timeout"
        except Exception:
            log.exception("academy tick %s failed", t.name)
            out[t.name] = "error"
    return out


async def academy_loop(bot) -> None:
    """The only Academy loop. Respects the ACADEMY_ENABLED kill switch (checked every round)."""
    load_tick_modules()
    while True:
        try:
            if AR.enabled():
                await run_ticks(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("academy loop error")
        await asyncio.sleep(AR.TICK_SECONDS)


# ------------------------------------------------------------------ first tick: season rollover
def _parse_marker(v: str) -> tuple[int, int]:
    try:
        a, b = v.split(":")
        return int(a), int(b)
    except (ValueError, AttributeError):
        return 0, 0


@tick(name="academy.rollover")
async def rollover_tick(bot, now: datetime) -> None:
    """Keep the season table current and announce boundaries on the event bus (season_started / season_ended),
    once per season even if a handler already did the DB rollover. The marker lives in AppState."""
    from database.models import AcademySeason, aware
    from services import academy_calendar as cal, academy_events as ev, users
    from sqlalchemy import select
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        started, ended = _parse_marker(await _get_marker(s))
        todo: list[tuple[str, int, int]] = []
        if season.number > started:
            if started > ended:                              # the previous season's end was never announced (bot was off)
                prev = (await s.execute(select(AcademySeason).where(AcademySeason.number == started))).scalar_one_or_none()
                if prev is not None:
                    todo.append((ev.SEASON_ENDED, prev.id, prev.number))
                ended = started
            todo.append((ev.SEASON_STARTED, season.id, season.number))
            started = season.number
        if now >= aware(season.end_at) and ended < season.number <= started:
            todo.append((ev.SEASON_ENDED, season.id, season.number))
            ended = season.number
        if todo:
            await users.set_state(s, MARKER_KEY, f"{started}:{ended}")
    for kind, sid, num in todo:                              # after the commit
        await ev.emit(kind, None, season_id=sid, number=num)


async def _get_marker(s) -> str:
    from database.models import AppState
    row = await s.get(AppState, MARKER_KEY)
    return row.value if row else ""


async def clear_marker(s) -> None:
    from database.models import AppState
    row = await s.get(AppState, MARKER_KEY)
    if row:
        await s.delete(row)
