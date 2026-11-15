"""Academy adventures, PURE half (Part 5): availability, stat checks, story transitions, rewards, expiry and the data validator.

Nothing here touches the database, the clock or Telegram: every function takes its inputs (and an `rng` with `randint`) as
parameters, so whole adventures can be simulated in tests. The database half is services/academy_adventures.py.

A run's memory is a tiny dict (stored as JSON in academy_adventure_runs.state_json):
    {"flags": [...], "fest": bool, "day": int, "wins": int, "losses": int, "trail": [node keys visited]}
`fest` is decided when the run STARTS (Fest Day = AR.ADV_FEST_DAY), so a run that crosses midnight keeps its bonus.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from game import academy_rules as AR
from utils.content import academy_adventures as CONTENT
from utils.content.academy_adventures import Adventure, Check, Choice, Ending, Node, Trophy

STAT_EMOJI = {"knowledge": "🧠", "charisma": "💬", "fitness": "💪", "creativity": "🎨", "luck": "🍀"}
STAT_NAME = {"knowledge": "Knowledge", "charisma": "Charisma", "fitness": "Fitness", "creativity": "Creativity", "luck": "Luck"}
ENDED_NO_REWARD = ("expired", "quit")           # values of academy_adventure_runs.ending for runs that paid nothing


class BadChoice(ValueError):
    """The button index is not a visible choice of this node."""


# ------------------------------------------------------------------ lookup / availability
def get(key: str) -> Adventure | None:
    return CONTENT.ADVENTURES.get(key)


def is_open(adv: Adventure, day: int, local_hour: int) -> bool:
    if day < AR.ADV_FIRST_DAY or day not in adv.days:
        return False
    return AR.adv_night(local_hour) if adv.night else True


def closed_reason(adv: Adventure, day: int, local_hour: int) -> str:
    """Why an adventure is closed right now ('' when it is open). Plain text."""
    if day < AR.ADV_FIRST_DAY:
        return f"adventures open from Day {AR.ADV_FIRST_DAY}"
    if day not in adv.days:
        return "not on today's schedule (Day " + ", ".join(str(d) for d in adv.days) + ")"
    if adv.night and not AR.adv_night(local_hour):
        return f"night only ({AR.ADV_NIGHT_FROM_HOUR:02d}:00 to {AR.ADV_NIGHT_TO_HOUR:02d}:00)"
    return ""


def available(day: int, local_hour: int) -> list[Adventure]:
    return [CONTENT.ADVENTURES[k] for k in CONTENT.ORDER if is_open(CONTENT.ADVENTURES[k], day, local_hour)]


def expired(updated_at: datetime, now: datetime) -> bool:
    """True when the run has had no button press for AR.ADV_EXPIRE_MINUTES (strictly longer)."""
    u = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    n = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return n - u > timedelta(minutes=AR.ADV_EXPIRE_MINUTES)


def seconds_left(updated_at: datetime, now: datetime) -> int:
    u = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    n = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return max(0, int((u + timedelta(minutes=AR.ADV_EXPIRE_MINUTES) - n).total_seconds()))


# ------------------------------------------------------------------ run state
def new_state(day: int, fest: bool | None = None) -> dict:
    return {"flags": [], "fest": (day == AR.ADV_FEST_DAY) if fest is None else bool(fest), "day": int(day),
            "wins": 0, "losses": 0, "trail": []}


def dump_state(state: dict) -> str:
    return json.dumps(state, separators=(",", ":"), sort_keys=True)


def load_state(raw: str | None) -> dict:
    try:
        d = json.loads(raw or "{}")
    except (ValueError, TypeError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    try:
        day = int(d.get("day", 0) or 0)
    except (TypeError, ValueError):
        day = 0
    out = new_state(day, bool(d.get("fest", False)))
    flags, trail = d.get("flags"), d.get("trail")
    out["flags"] = [f for f in flags if isinstance(f, str)] if isinstance(flags, list) else []
    out["trail"] = [x for x in trail if isinstance(x, str)] if isinstance(trail, list) else []
    for k in ("wins", "losses"):
        try:
            out[k] = int(d.get(k, 0) or 0)
        except (TypeError, ValueError):
            out[k] = 0
    return out


# ------------------------------------------------------------------ choices and checks
def visible(node: Node, flags) -> list[tuple[int, Choice]]:
    """[(index in node.choices, choice)] for the buttons the player can see with these flags."""
    fl = set(flags)
    return [(i, c) for i, c in enumerate(node.choices)
            if all(f in fl for f in c.needs) and not any(f in fl for f in c.hides)]


@dataclass(frozen=True)
class CheckResult:
    stat: str
    difficulty: int
    skill: int | None           # None for a luck check
    chance: float               # 0.05..0.95 BEFORE the dice
    threshold: int              # the dice must land on this or lower
    roll: int | None            # None when only the chance was asked for
    success: bool | None


def _clamp(v: float) -> float:
    return min(AR.ADV_CHANCE_MAX, max(AR.ADV_CHANCE_MIN, v))


def chance_of(adv: Adventure, check: Check, stats: dict, stream: str, flags) -> CheckResult:
    """Chance of a check before the dice. Stat check: base + (stat + flag bonuses + stream bonus - target) / 100.
    Luck check: the fixed luck chance of its difficulty + flag bonuses / 100. Always inside 0.05..0.95."""
    fl = set(flags)
    flag_bonus = sum(v for f, v in check.bonus if f in fl)
    if check.stat == "luck":
        chance, skill = _clamp(AR.ADV_LUCK_CHANCE[check.difficulty] + flag_bonus / 100.0), None
    else:
        skill = int(stats.get(check.stat, 0)) + flag_bonus + int(adv.stream_bonus.get(stream, 0))
        chance = _clamp(AR.ADV_CHANCE_BASE + (skill - AR.ADV_TARGET[check.difficulty]) / 100.0)
    return CheckResult(check.stat, check.difficulty, skill, chance, int(chance * 100 + 0.5), None, None)


def resolve_check(adv: Adventure, check: Check, stats: dict, stream: str, flags, rng=None) -> CheckResult:
    """Roll ONE dice (randint 1..100): success when roll <= threshold. A 5% floor and 95% ceiling keep luck in every check."""
    base = chance_of(adv, check, stats, stream, flags)
    roll = (rng or random).randint(1, 100)
    return CheckResult(base.stat, base.difficulty, base.skill, base.chance, base.threshold, roll, roll <= base.threshold)


def hint(adv: Adventure, choice: Choice, stats: dict, stream: str, flags) -> str:
    """Short button suffix: '' for a plain move, '💬 45%' for a check."""
    if choice.check is None:
        return ""
    r = chance_of(adv, choice.check, stats, stream, flags)
    return f"{STAT_EMOJI[r.stat]} {r.threshold}%"


def check_line(r: CheckResult) -> str:
    """One plain-text line about a rolled check (the view escapes it)."""
    stars = "★" * r.difficulty
    verdict = "success!" if r.success else "not this time."
    return (f"🎲 {STAT_NAME[r.stat]} check {stars}: you needed {r.threshold} or less and rolled {r.roll}. {verdict}")


@dataclass
class Move:
    node: str                       # the node the player is on now
    check: CheckResult | None
    state: dict
    choice_text: str


def advance(adv: Adventure, node_key: str, state: dict, idx: int, stats: dict, stream: str, rng=None) -> Move:
    """Press button `idx` (index in node.choices) on `node_key`. Pure: returns the new state, never mutates the old one.
    BadChoice if the node is an ending, the index is unknown, or the button is hidden by the flags."""
    node = adv.nodes.get(node_key)
    if node is None or node.ending is not None:
        raise BadChoice("this part of the story has no choices")
    vis = dict(visible(node, state.get("flags", [])))
    if idx not in vis:
        raise BadChoice("that choice is not available")
    ch = vis[idx]
    st = {**state, "flags": list(state.get("flags", [])), "trail": list(state.get("trail", []))}

    def add(flags) -> None:
        for f in flags:
            if f not in st["flags"]:
                st["flags"].append(f)

    add(ch.sets)
    result = None
    if ch.check is not None:
        result = resolve_check(adv, ch.check, stats, stream, st["flags"], rng)
        if result.success:
            add(ch.check.win_sets)
            st["wins"] = int(st.get("wins", 0)) + 1
            target = ch.check.success
        else:
            st["losses"] = int(st.get("losses", 0)) + 1
            target = ch.check.fail
    else:
        target = ch.to
    st["trail"].append(target)
    return Move(target, result, st, ch.text)


# ------------------------------------------------------------------ rewards
@dataclass(frozen=True)
class Rewards:
    diamonds: int                   # what is paid (already includes the Fest Day bonus)
    base_diamonds: int
    stats: dict                     # {stat: gain} including the Fest Day +1
    trophy: Trophy | None
    fest: bool

    @property
    def fest_diamonds(self) -> int:
        return self.diamonds - self.base_diamonds


def rewards_for(ending: Ending, fest: bool) -> Rewards:
    """What an ending pays. Fest Day: +AR.ADV_FEST_DIAMOND_PCT % diamonds (rounded up) and +AR.ADV_FEST_STAT_BONUS on every stat
    the ending already gives. Nothing is invented for an ending that gives nothing."""
    base = int(ending.diamonds)
    gains = {k: int(v) for k, v in ending.stats.items() if int(v) > 0}
    if fest:
        gains = {k: v + AR.ADV_FEST_STAT_BONUS for k, v in gains.items()}
    return Rewards(AR.adv_fest_diamonds(base) if fest else base, base, gains, ending.trophy, bool(fest))


def reward_ref(user_id: int, enrolled_us: int, run_id: int) -> str:
    """Ledger ref of an adventure payout: unique per player + enrolment + run, so a retry can never pay twice
    (pay_diamonds adds the 'academy:' prefix). The enrolment time keeps it unique even after an /academyreset."""
    return f"adv:{user_id}:{enrolled_us}:{run_id}"


# ------------------------------------------------------------------ screens (what the DB half hands to the views)
@dataclass
class ChoiceView:
    idx: int
    text: str
    hint: str = ""


@dataclass
class Screen:
    kind: str                       # node | ending | expired | stale | quit
    adventure: str = ""
    run_id: int = 0
    step: int = 0
    node: str = ""
    text: str = ""                  # story text of the node (plain)
    line: str = ""                  # the previous check's result line (plain)
    choices: list = field(default_factory=list)
    message: str = ""               # for expired / stale / quit
    fest: bool = False
    seconds_left: int = 0
    # ending
    title: str = ""
    mood: str = ""
    rewards: Rewards | None = None
    paid: int = 0
    gains: dict = field(default_factory=dict)       # what the stats really moved (clamped at the cap of 100)
    trophy_new: bool = False
    gif: str | None = None
    done_today: int = 0
    event_data: dict = field(default_factory=dict)


def node_choices(adv: Adventure, node: Node, state: dict, stats: dict, stream: str) -> list[ChoiceView]:
    return [ChoiceView(i, c.text, hint(adv, c, stats, stream, state.get("flags", []))) for i, c in visible(node, state.get("flags", []))]


@dataclass
class ListEntry:
    key: str
    name: str
    emoji: str
    blurb: str
    energy: int
    open: bool
    reason: str = ""                # why it is closed right now (plain text)


@dataclass
class LiveRun:
    key: str
    name: str
    emoji: str
    run_id: int
    step: int
    seconds_left: int


@dataclass
class ListView:
    """What /adventure shows: the adventures of today, the live run (if any), the daily count, Energy and trophies."""
    day: int
    phase_name: str
    phase_emoji: str
    fest: bool
    entries: list = field(default_factory=list)
    live: LiveRun | None = None
    done_today: int = 0
    limit: int = AR.ADV_MAX_PER_DAY
    energy: int = 0
    trophies: list = field(default_factory=list)        # [(kind, name)]


# ------------------------------------------------------------------ the data validator (also run by the tests)
def explore(adv: Adventure) -> tuple[set, set, set]:
    """Walk the story from the start with REAL flag semantics. Returns (reached node keys, reached (node, idx) buttons,
    reached ending keys). A button counts as reached when it is visible in some reachable state."""
    start = (adv.start, frozenset())
    seen, todo = {start}, [start]
    nodes, buttons = {adv.start}, set()
    while todo:
        key, flags = todo.pop()
        node = adv.nodes.get(key)
        if node is None or node.ending is not None:
            continue
        for i, c in visible(node, flags):
            buttons.add((key, i))
            fl = frozenset(flags | set(c.sets))
            nxt = [(c.to, fl)] if c.check is None else [(c.check.success, fl | frozenset(c.check.win_sets)), (c.check.fail, fl)]
            for t, f in nxt:
                if t in adv.nodes:
                    nodes.add(t)
                    if (t, f) not in seen:
                        seen.add((t, f))
                        todo.append((t, f))
    return nodes, buttons, {k for k in nodes if adv.nodes[k].ending is not None}


def _targets(c: Choice) -> list[str]:
    return [c.to] if c.check is None else [c.check.success, c.check.fail]


def _settable(adv: Adventure) -> set[str]:
    out: set[str] = set()
    for n in adv.nodes.values():
        for c in n.choices:
            out.update(c.sets)
            if c.check:
                out.update(c.check.win_sets)
    return out


def _has_cycle(adv: Adventure) -> bool:
    state: dict[str, int] = {}

    def dfs(k: str) -> bool:
        state[k] = 1
        for c in adv.nodes[k].choices:
            for t in _targets(c):
                if t not in adv.nodes:
                    continue
                if state.get(t) == 1 or (t not in state and dfs(t)):
                    return True
        state[k] = 2
        return False
    return any(k not in state and dfs(k) for k in adv.nodes)


def problems(adv: Adventure) -> list[str]:
    """Everything wrong with an adventure's data (an empty list = valid): structure, links, reachability and reward caps."""
    p: list[str] = []
    add = p.append
    if adv.start not in adv.nodes:
        return [f"{adv.key}: start node {adv.start!r} does not exist"]
    if not adv.days or any(not 1 <= d <= AR.SEASON_DAYS for d in adv.days):
        add(f"{adv.key}: bad days {adv.days}")
    if not AR.ADV_ENERGY_MIN <= adv.energy <= AR.ADV_ENERGY_MAX:
        add(f"{adv.key}: energy {adv.energy} outside {AR.ADV_ENERGY_MIN}..{AR.ADV_ENERGY_MAX}")
    for stream, v in adv.stream_bonus.items():
        if stream not in AR.STREAMS or not 0 < v <= AR.ADV_STREAM_BONUS_LIMIT:
            add(f"{adv.key}: bad stream bonus {stream}={v}")
    settable = _settable(adv)
    for key, n in adv.nodes.items():
        if n.key != key:
            add(f"{adv.key}: node key mismatch {key!r} vs {n.key!r}")
        if n.ending is None:
            if not 1 <= len(n.choices) <= 4:
                add(f"{adv.key}/{key}: needs 1-4 choices, has {len(n.choices)} (dead end)")
            if not any(not c.needs and not c.hides for c in n.choices):
                add(f"{adv.key}/{key}: no always-visible choice (a flag combination could leave a dead end)")
        else:
            if n.choices:
                add(f"{adv.key}/{key}: an ending must not have choices")
            e = n.ending
            if e.mood not in ("great", "good", "ok", "bad"):
                add(f"{adv.key}/{key}: bad mood {e.mood!r}")
            if not 0 <= e.diamonds <= AR.ADV_MAX_DIAMONDS:
                add(f"{adv.key}/{key}: diamonds {e.diamonds} over the cap {AR.ADV_MAX_DIAMONDS}")
            if rewards_for(e, True).diamonds > AR.ADV_FEST_MAX_DIAMONDS:
                add(f"{adv.key}/{key}: Fest Day diamonds over {AR.ADV_FEST_MAX_DIAMONDS}")
            bad = [k for k in e.stats if k not in AR.STAT_KEYS]
            if bad:
                add(f"{adv.key}/{key}: unknown stat(s) {bad}")
            if any(not 0 < int(v) <= AR.ADV_MAX_STAT_GAIN for v in e.stats.values()):
                add(f"{adv.key}/{key}: a stat gain is outside 1..{AR.ADV_MAX_STAT_GAIN}")
            if sum(int(v) for v in e.stats.values()) > AR.ADV_MAX_STAT_TOTAL:
                add(f"{adv.key}/{key}: stat gains total over {AR.ADV_MAX_STAT_TOTAL}")
            if e.gif is not None and e.gif not in AR.ADV_GIFS_ALLOWED:
                add(f"{adv.key}/{key}: gif {e.gif!r} is not an allowed SFW action")
            if e.trophy is not None and e.trophy.kind not in AR.ADV_TROPHY_KINDS:
                add(f"{adv.key}/{key}: bad trophy kind {e.trophy.kind!r}")
        for i, c in enumerate(n.choices):
            where = f"{adv.key}/{key}#{i}"
            if (c.to is None) == (c.check is None):
                add(f"{where}: needs exactly one of `to` and `check`")
            for t in _targets(c):
                if t is None or t not in adv.nodes:
                    add(f"{where}: link to missing node {t!r}")
            if c.check is not None:
                if c.check.stat not in AR.ADV_CHECK_STATS:
                    add(f"{where}: unknown check stat {c.check.stat!r}")
                if c.check.difficulty not in AR.ADV_TARGET:
                    add(f"{where}: unknown difficulty {c.check.difficulty!r}")
                for f, v in c.check.bonus:
                    if abs(v) > AR.ADV_FLAG_BONUS_LIMIT:
                        add(f"{where}: flag bonus {f}={v} over {AR.ADV_FLAG_BONUS_LIMIT}")
                    if f not in settable:
                        add(f"{where}: bonus flag {f!r} is never set")
            for f in (*c.needs, *c.hides):
                if f not in settable:
                    add(f"{where}: flag {f!r} is never set")
    if p:
        return p                                   # reachability below assumes valid links
    if _has_cycle(adv):
        add(f"{adv.key}: the story has a loop (runs must always end)")
    nodes, buttons, endings = explore(adv)
    for k in adv.nodes:
        if k not in nodes:
            add(f"{adv.key}/{k}: unreachable node")
    for k, n in adv.nodes.items():
        if k in nodes and n.ending is None:
            for i in range(len(n.choices)):
                if (k, i) not in buttons:
                    add(f"{adv.key}/{k}#{i}: this choice can never be shown")
    for k, n in adv.nodes.items():
        if n.ending is not None and k not in endings:
            add(f"{adv.key}/{k}: unreachable ending")
    return p


def all_problems() -> list[str]:
    out: list[str] = []
    keys: dict[str, str] = {}
    for a in CONTENT.ADVENTURES.values():
        out += problems(a)
        for n in a.nodes.values():
            if n.ending and n.ending.trophy:
                t = n.ending.trophy
                if t.key in keys:
                    out.append(f"trophy key {t.key!r} used in {keys[t.key]} and {a.key}")
                keys[t.key] = a.key
    return out
