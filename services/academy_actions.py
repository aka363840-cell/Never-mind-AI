"""Academy daily life, PURE half (Part 2): which action is open when, per-day limits, the study streak, what an action
gives, the diamond cap, which GIF to show and how the result message looks.

Nothing in here touches the database, the clock or Telegram: everything takes its inputs as parameters (and an `rng`),
so a whole season can be simulated in tests. The database half is services/academy_life.py.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable

from game import academy_rules as AR
from services.errors import GameError
from utils.content import academy_texts as T
from utils.fmt import esc


class ActionBlocked(GameError):
    """An expected 'not now' answer (break, not enrolled, wrong day, daily limit, no Energy ...).
    `enroll=True` makes the handler add an Enroll button. Being a GameError, it is still shown as a friendly reply by
    the error middleware if a handler forgets to catch it."""

    def __init__(self, text: str, *, enroll: bool = False):
        super().__init__(text)
        self.text = text
        self.enroll = enroll


# ------------------------------------------------------------------ daily counters (a plain view of one academy_daily row)
@dataclass
class DailyCounts:
    study: int = 0
    attend: int = 0
    hangout: int = 0
    rest: int = 0
    club: int = 0
    parttime: int = 0
    energy_spent: int = 0
    diamonds: int = 0

    def used(self, action: str) -> int:
        return getattr(self, action)


# ------------------------------------------------------------------ gates
def day_block(action: str, day: int) -> str | None:
    """None if `action` is open on season `day`, else the friendly reason."""
    act = AR.ACTIONS[action]
    if day in act.days:
        return None
    if action == "attend":
        return T.NO_CLASS_TODAY.format(days=", ".join(str(d) for d in AR.CLASS_DAYS))
    if action == "parttime" and day == AR.FINALS_DAY:
        return T.FINALS_NO_JOB
    return T.CLOSED_TODAY.format(name=act.name, day=day)


def limit_block(action: str, counts: DailyCounts, resets: str = "a few hours") -> str | None:
    """None if the player can still do `action` today, else the friendly reason. `resets` = text like '5h 20m'."""
    act = AR.ACTIONS[action]
    if counts.used(action) < act.daily_limit:
        return None
    if action == "attend":
        return T.ATTENDED_TODAY
    return T.LIMIT_REACHED.format(emoji=act.emoji, name=act.name, n=act.daily_limit, resets=resets)


def diamonds_left(counts: DailyCounts) -> int:
    return max(0, AR.DAILY_DIAMOND_CAP - counts.diamonds)


def cap_pay(roll: int, earned_today: int) -> int:
    """What is actually paid: the roll, but never past the daily cap (0 once the cap is reached)."""
    return max(0, min(int(roll), AR.DAILY_DIAMOND_CAP - int(earned_today)))


# ------------------------------------------------------------------ streak
def study_streak(days_studied: Iterable[int], today: int) -> int:
    """Days in a row (ending at `today`) on which the player studied. `days_studied` may or may not contain today.
    Not studying today counts as 0 unless today is in the set: callers add today when they are about to study."""
    have = set(days_studied)
    n = 0
    d = today
    while d in have and d >= 1:
        n += 1
        d -= 1
    return n


def best_streak(days_studied: Iterable[int]) -> int:
    """The longest run of consecutive study days in the set (used by awards)."""
    have = sorted(set(days_studied))
    best = run = 0
    prev = None
    for d in have:
        run = run + 1 if prev is not None and d == prev + 1 else 1
        best = max(best, run)
        prev = d
    return best


def streak_bonus(streak: int) -> int:
    """Bonus Knowledge for reaching exactly this streak length (0 if not a milestone)."""
    return AR.STREAK_MILESTONES.get(streak, 0)


# ------------------------------------------------------------------ what an action gives
@dataclass
class Plan:
    """The outcome of one roll, before it is applied (stat points are the INTENDED deltas; the stat cap may trim them)."""
    line: str
    stats: dict[str, int] = field(default_factory=dict)
    club_points: int = 0
    energy_restore: int = 0
    pay_roll: int = 0
    perk: str | None = None          # the stream-perk flavour note, only when the perk really applied
    milestone: int = 0               # the streak length if this study hit a milestone (bonus included in stats)


def _add(stats: dict[str, int], more: dict[str, int]) -> None:
    for k, v in more.items():
        stats[k] = stats.get(k, 0) + v


def plan(action: str, *, stream: str, club: str, rng: random.Random | None = None,
         streak: int = 0, first_study_today: bool = False) -> Plan:
    """Roll the effects of one action. `streak` / `first_study_today` only matter for /study: the milestone bonus is
    given on the FIRST study of a day only, so repeating /study the same day can never pay it twice."""
    rng = rng or random
    if action not in AR.ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    perk = AR.STREAM_PERKS.get(stream)
    out = Plan(line="")
    if action == "study":
        out.line = rng.choice(T.STUDY_LINES)
        _add(out.stats, {"knowledge": rng.randint(*AR.STUDY_GAIN)})
        bonus = streak_bonus(streak) if first_study_today else 0
        if bonus:
            _add(out.stats, {"knowledge": bonus})
            out.milestone = streak
    elif action == "attend":
        out.line = rng.choice(T.ATTEND_LINES)
        _add(out.stats, {"knowledge": AR.ATTEND_GAIN})
    elif action == "hangout":
        out.line = rng.choice(T.HANGOUT_LINES)
        _add(out.stats, {"charisma": rng.randint(*AR.HANGOUT_CHARISMA)})
        if rng.random() < AR.HANGOUT_CREATIVITY_CHANCE:
            _add(out.stats, {"creativity": 1})
    elif action == "club":
        out.line = rng.choice(T.CLUB_LINES + T.CLUB_EXTRA.get(club, ()))
        out.club_points = rng.randint(*AR.CLUB_POINTS)
        _add(out.stats, {AR.CLUB_STAT.get(club, "creativity"): rng.randint(*AR.CLUB_STAT_GAIN)})
    elif action == "rest":
        out.line = rng.choice(T.REST_LINES)
        out.energy_restore = AR.REST_ENERGY + (perk.rest_bonus if perk else 0)
    elif action == "parttime":
        out.line = rng.choice(T.PARTTIME_LINES)
        roll = rng.randint(*AR.PARTTIME_PAY)
        out.pay_roll = roll * (100 + (perk.pay_pct if perk else 0)) // 100
    # stream perks (extra stat points on this action; pay / rest bonus were applied above)
    if perk and action in AR.perk_actions(stream):
        _add(out.stats, perk.stats.get(action, {}))
        out.perk = T.STREAM_NOTES.get((stream, action))
    return out


# ------------------------------------------------------------------ GIF moments (all optional, see utils.gifs)
def choose_gif(action: str, *, milestone: int = 0, first_attend: bool = False, energy_left: int = 100,
               rng: random.Random | None = None) -> str | None:
    """One GIF action name for the result message, or None. Priority: streak > first attend > low Energy > hangout."""
    rng = rng or random
    if action == "study" and milestone:
        return AR.GIF_MOMENTS["streak"]
    if action == "attend" and first_attend:
        return AR.GIF_MOMENTS["first_attend"]
    if action != "rest" and energy_left <= AR.LOW_ENERGY_GIF:
        return AR.GIF_MOMENTS["low_energy"]
    if action == "hangout" and rng.random() < AR.HANGOUT_GIF_CHANCE:
        return AR.GIF_MOMENTS["hangout"]
    return None


# ------------------------------------------------------------------ result + message
@dataclass
class ActionResult:
    key: str
    day: int
    line: str
    stream: str
    club: str
    gains: dict[str, int]                # stat changes that REALLY happened (after the 0..100 clamp)
    stats: dict[str, int]                # stats after the action
    energy: int                          # Energy left now
    energy_bar: str
    energy_delta: int                    # negative = spent, positive = restored
    used: int                            # uses of this action today, including this one
    limit: int
    streak: int = 0
    milestone: bool = False
    first_attend: bool = False
    perk: str | None = None
    club_points: int = 0
    club_total: int = 0
    club_rank: int = 0
    club_count: int = 0
    paid: int = 0
    capped: bool = False                 # the shift paid less than rolled because of the daily cap
    diamonds_today: int = 0
    gif: str | None = None
    event: str = ""
    event_data: dict = field(default_factory=dict)


def render(r: ActionResult) -> str:
    """The reply the player sees (HTML). Short on purpose: it also has to fit a GIF caption (1000 chars)."""
    act = AR.ACTIONS[r.key]
    lines = [f"{act.emoji} <b>{esc(act.name)}</b>", f"<i>{esc(r.line)}</i>", ""]
    for k in AR.STAT_KEYS:
        d = r.gains.get(k, 0)
        if d > 0:
            lines.append(f"{AR.STAT_LABELS[k]} <b>+{d}</b> → {r.stats[k]}")
    if r.key in ("study", "attend", "hangout", "club") and not any(v > 0 for v in r.gains.values()):
        lines.append("📈 Your stats are already maxed out here. Nice work!")
    if r.milestone:
        lines.append(T.STREAK_LINES.get(r.streak, f"🔥 <b>{r.streak}-day study streak!</b>"))
    elif r.key == "study" and r.streak > 1:
        lines.append(f"🔥 Study streak: <b>{r.streak}</b> days in a row")
    if r.first_attend:
        lines.append("✅ First attendance mark of the season!")
    elif r.key == "attend":
        lines.append("✅ Attendance marked for today.")
    if r.key == "club" and r.club_points:
        c = AR.CLUBS[r.club]
        lines.append(f"{c.emoji} {esc(c.name)} <b>+{r.club_points}</b> points · total <b>{r.club_total}</b> "
                     f"(#{r.club_rank} of {r.club_count})")
    if r.key == "parttime":
        lines.append(f"💎 You earned <b>{r.paid}</b> diamonds"
                     + (" (daily cap reached)" if r.capped else "")
                     + f" · today <b>{r.diamonds_today}/{AR.DAILY_DIAMOND_CAP}</b>")
    if r.perk:
        lines.append(r.perk)
    sign = f"+{r.energy_delta}" if r.energy_delta > 0 else (f"−{-r.energy_delta}" if r.energy_delta < 0 else "±0")
    lines.append(f"⚡ Energy {r.energy_bar} <b>{r.energy}/{AR.ENERGY_MAX}</b> ({sign})")
    lines.append(f"📆 Today: {act.name} {r.used}/{r.limit}")
    return "\n".join(lines)
