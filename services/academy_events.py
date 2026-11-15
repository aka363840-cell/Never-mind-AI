"""Tiny in-process event bus for Academy Life: emit() after every meaningful player action, @subscribe() to react.

    @subscribe("enrolled")
    async def on_enrolled(user_id, **data): ...

Rules: emit AFTER your transaction has committed (a subscriber normally opens its own transaction - doing it inside yours
would wait for your lock). A failing subscriber is logged and never breaks the others or the caller. Subscribers run one
after another, so keep them quick (spawn a task for anything slow).

Kinds emitted so far:  enrolled (user_id; season_id, stream, club, late_join) | season_started (None; season_id, number)
                       season_ended (None; season_id, number) | reset (staff id; nothing)
Part 2 (one per player action, all with season_id, day, energy plus the extras below):
                       studied (gains, streak, milestone) | attended (gains, first) | hangout (gains) | rested (energy_restored)
                       club_practice (club, points, total, gains) | parttime (paid, capped, diamonds_today)
                       study_streak (streak, bonus)   <- extra, only when a /study hits a 3 / 5 / 7 day streak
Part 3: exam_started | exam_round | exam_finished
Part 4 (social; emitted after the commit; see services/academy_social.py): rival_win (winner; season_id, duel_id, opponent_id, bot,
                       walkover, total, opponent_total) | crush_mutual (the player who completed it; season_id, partner_id, couple_id)
                       date (initiator; season_id, day, partner_id, couple_id, points, total) | club_win (each rewarded member;
                       season_id, club, battle_id, diamonds). NOTHING is emitted for a crush that is not mutual.
Part 5 (adventures; emitted after the commit by services/academy_adventures.announce): adventure_done (player; season_id, student_id,
                       run_id, adventure, ending, mood, day, fest, diamonds, base_diamonds, gains, trophy, trophy_new, wins, losses)
"""
from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)

ENROLLED = "enrolled"
SEASON_STARTED = "season_started"
SEASON_ENDED = "season_ended"
RESET = "reset"
# Part 2: daily life
STUDIED = "studied"
ATTENDED = "attended"
HANGOUT = "hangout"
RESTED = "rested"
CLUB_PRACTICE = "club_practice"
PARTTIME = "parttime"
STUDY_STREAK = "study_streak"
# Part 3: exams
EXAM_STARTED = "exam_started"
EXAM_ROUND = "exam_round"
EXAM_FINISHED = "exam_finished"
# Part 5: adventures
ADVENTURE_DONE = "adventure_done"
# Part 6: reminders and graduation
GRADUATED = "graduated"              # once per graduate: user_id; season_id, number, rank, gpa, grade, titles
CEREMONY_DONE = "ceremony_done"      # once per season: user_id=0; season_id, number, graduates
REMIND_CHANGED = "remind_changed"    # /academyremind: user_id; on
EVENT_FOR_ACTION = {"study": STUDIED, "attend": ATTENDED, "hangout": HANGOUT, "rest": RESTED,
                    "club": CLUB_PRACTICE, "parttime": PARTTIME}

_subs: dict[str, list[Callable[..., Any]]] = defaultdict(list)


def subscribe(kind: str) -> Callable[[Callable], Callable]:
    def deco(fn: Callable) -> Callable:
        if fn not in _subs[kind]:                  # importing a module twice must not double-register
            _subs[kind].append(fn)
        return fn
    return deco


def subscribers(kind: str) -> list[Callable[..., Any]]:
    return list(_subs.get(kind, ()))


async def emit(kind: str, user_id: int | None = None, **data: Any) -> int:
    """Call every subscriber of `kind` as fn(user_id, **data). Returns how many ran without error."""
    ok = 0
    for fn in subscribers(kind):
        try:
            res = fn(user_id, **data)
            if inspect.isawaitable(res):
                await res
            ok += 1
        except Exception:
            log.exception("academy event %r: subscriber %s failed", kind, getattr(fn, "__name__", fn))
    return ok
