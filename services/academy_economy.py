"""Academy Life - the diamond economy on paper (Part 7 audit). PURE: reads game/academy_rules.py and the adventure data, no I/O.

It answers "what is the MOST a player can earn from the Academy" from the rules alone (limits, caps, once-per-season prizes), so
the numbers in docs/ACADEMY.md, /academyhelp and the audit tests can never drift from the rules. These are UPPER BOUNDS for a
player who plays perfectly and is lucky (best ending of every adventure, an A+ in both exams, a winning duel and club).

Every diamond that the Academy pays goes through academy_students.pay_diamonds (ledger, unique ref); the only diamond SINK is the
campus date (AR.DATE_DIAMONDS). Energy is NOT the binding limit for diamonds: see energy_needed() vs energy_supply_per_day().
"""
from __future__ import annotations

from dataclasses import dataclass, field

from game import academy_rules as AR


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    day: int                    # the season day the diamonds arrive on
    diamonds: int
    energy: int = 0             # Energy it takes to earn them
    once: bool = False          # once per season (not per day)


@dataclass(frozen=True)
class DayBudget:
    day: int
    recurring: int              # part-time + adventures (limited per day)
    once_items: int             # prizes that arrive on this day (exam rewards, duel, club battle)
    total: int
    energy: int                 # Energy needed to earn `total` (best play)
    sources: tuple[Source, ...] = field(default_factory=tuple)


def _adventures():
    from utils.content import academy_adventures as C       # plain data module
    return [C.ADVENTURES[k] for k in C.ORDER]


def best_ending(adv) -> int:
    return max((n.ending.diamonds for n in adv.nodes.values() if n.ending is not None), default=0)


def parttime_best_pay() -> int:
    """The biggest single shift pay with the best stream perk (the daily cap still applies)."""
    pct = max((p.pay_pct for p in AR.STREAM_PERKS.values()), default=0)
    return AR.PARTTIME_PAY[1] * (100 + pct) // 100


def parttime_day_max(day: int) -> int:
    act = AR.ACTIONS["parttime"]
    if day not in act.days:
        return 0
    return min(AR.DAILY_DIAMOND_CAP, act.daily_limit * parttime_best_pay())


def adventure_day_max(day: int) -> tuple[int, int]:
    """(diamonds, Energy) of the best AR.ADV_MAX_PER_DAY adventures finished on `day` (the same one may be replayed)."""
    if day < AR.ADV_FIRST_DAY:
        return 0, 0
    open_today = [a for a in _adventures() if day in a.days]
    if not open_today:
        return 0, 0
    best = max(open_today, key=lambda a: (best_ending(a), -a.energy))
    base = best_ending(best)
    each = AR.adv_fest_diamonds(base) if day == AR.ADV_FEST_DAY else base
    return each * AR.ADV_MAX_PER_DAY, best.energy * AR.ADV_MAX_PER_DAY


def once_sources() -> list[Source]:
    """Prizes that can be won once per season, and the day they arrive."""
    out: list[Source] = []
    for kind in AR.EXAM_KINDS:
        out.append(Source(f"exam_{kind}", f"{AR.EXAM_NAMES[kind]} reward (A+)", AR.EXAM_DAY[kind], max(AR.EXAM_REWARD.values()), 0, True))
    out.append(Source("rival", "Rival duel win", AR.RIVAL_PAIR_FROM_DAY, AR.RIVAL_WIN_DIAMONDS, AR.RIVAL_DUEL_ENERGY, True))
    # the club prize is paid when the tick closes the battle: right after the end of BATTLE_LAST_DAY, i.e. on the next day
    out.append(Source("club", "Winning club prize", min(AR.SEASON_DAYS, AR.BATTLE_LAST_DAY + 1), AR.CLUB_WIN_DIAMONDS, AR.BATTLE_ENERGY, True))
    return out


def graduation_max() -> int:
    """Only paid when ACADEMY_GRADUATION_DIAMONDS=1 (off by default): all three prize titles at once."""
    return sum(AR.GRADUATION_DIAMONDS.values())


def day_budget(day: int) -> DayBudget:
    pt = parttime_day_max(day)
    adv, adv_e = adventure_day_max(day)
    once = [s for s in once_sources() if s.day == day]
    act = AR.ACTIONS["parttime"]
    pt_energy = (-(-pt // parttime_best_pay())) * act.energy if pt else 0      # shifts needed to reach the cap
    srcs = []
    if pt:
        srcs.append(Source("parttime", "Part-time shifts", day, pt, pt_energy))
    if adv:
        srcs.append(Source("adventure", "Adventures", day, adv, adv_e))
    srcs += once
    return DayBudget(day, pt + adv, sum(s.diamonds for s in once), pt + adv + sum(s.diamonds for s in once),
                     pt_energy + adv_e + sum(s.energy for s in once), tuple(srcs))


def days() -> list[DayBudget]:
    return [day_budget(d) for d in range(1, AR.SEASON_DAYS + 1)]


def peak_day() -> DayBudget:
    return max(days(), key=lambda b: (b.total, -b.day))


def max_per_day_recurring() -> int:
    """The most that the daily limits allow on a day with no one-off prize (the day-to-day ceiling)."""
    return max(b.recurring for b in days())


def max_per_season() -> int:
    return sum(b.total for b in days())


def sinks() -> list[tuple[str, int, int]]:
    """(label, diamonds each, most times per season). Only the campus date costs diamonds; Focus and every other action cost Energy."""
    dates = max(0, AR.SEASON_DAYS - AR.CRUSH_FROM_DAY + 1)
    return [("Campus date (once per couple and day)", AR.DATE_DIAMONDS, dates)]


def max_sink_per_season() -> int:
    return sum(each * n for _, each, n in sinks())


def energy_supply_per_day() -> int:
    """Energy a player gets per day: the natural refill plus the most /rest can add (a cap of ENERGY_MAX applies at any moment,
    so this is an upper bound)."""
    rest = AR.ACTIONS["rest"]
    bonus = max((p.rest_bonus for p in AR.STREAM_PERKS.values()), default=0)
    return AR.ENERGY_REGEN_PER_HOUR * 24 + rest.daily_limit * (AR.REST_ENERGY + bonus)


def energy_needed() -> int:
    """Energy of the busiest day of diamond-earning (peak day)."""
    return max(b.energy for b in days())


def summary() -> dict:
    p = peak_day()
    return {
        "per_day_recurring": max_per_day_recurring(),
        "peak_day": p.day,
        "peak_day_total": p.total,
        "season_total": max_per_season(),
        "graduation_optional": graduation_max(),
        "sink_season": max_sink_per_season(),
        "energy_needed_peak": energy_needed(),
        "energy_supply_day": energy_supply_per_day(),
    }
