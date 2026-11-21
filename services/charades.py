"""Charades state (in memory - a game lasts minutes, a redeploy simply ends it)."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class Game:
    chat_id: int
    host_id: int
    host_name: str
    starter_id: int
    phase: str = "prep"                      # prep (waiting for the host's DM) -> live -> between (button to become host)
    word: str = ""
    round: int = 0
    changes: int = 0
    token: int = 0                           # bumped on every phase change so stale timers do nothing
    used: set[str] = field(default_factory=set)
    scores: dict[int, list] = field(default_factory=dict)          # uid -> [name, points]
    timer: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created: float = field(default_factory=time.time)


GAMES: dict[int, Game] = {}


def add_points(g: Game, uid: int, name: str, pts: int) -> None:
    row = g.scores.setdefault(uid, [name, 0])
    row[0] = name
    row[1] += pts


def board(g: Game) -> list[tuple[str, int]]:
    return sorted(((n, p) for n, p in g.scores.values()), key=lambda x: -x[1])
