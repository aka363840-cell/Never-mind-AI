"""Academy exams, PURE half (Part 3): chance of success, play_round, scoring, grades, GPA, ranking, card drawing, deadlines.

Nothing here touches the database, the clock or Telegram: everything takes its inputs as parameters (and an `rng`), so a whole
exam can be simulated in tests. The database half is services/academy_exams.py.

play_round(student, card, choice, rng) -> RoundResult is the reusable core (Parts 4 and 8A call it too).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from game import academy_rules as AR
from utils.content import academy_exam_rounds as CARDS


# ------------------------------------------------------------------ the student as the exam sees it
@dataclass
class ExamStudent:
    """What play_round needs. An ORM AcademyStudent works too (it has the stats and stream; `streak` and `attendance`
    default to 0 through getattr). attendance is a ratio 0..1 of the class days the student could attend."""
    stream: str = "science"
    knowledge: int = 10
    charisma: int = 10
    fitness: int = 10
    creativity: int = 10
    streak: int = 0
    attendance: float = 0.0


@dataclass(frozen=True)
class RoundResult:
    card_key: str
    choice: int                 # index of the button
    stat: str
    risk: str
    chance: float               # chance of success from skill + risk, BEFORE the dice (0.02..0.98)
    roll: int                   # the dice, 1..100
    luck: float                 # what the dice added to the chance, always within +-AR.LUCK_CAP
    success: bool
    points: int                 # round points (risky: bigger win, bigger loss)
    line: str                   # flavour line (success or failure)
    skill: float = 0.0
    parts: dict = field(default_factory=dict)       # skill breakdown: stat / stream / streak / attendance


# ------------------------------------------------------------------ chance
def streak_bonus(streak: int) -> float:
    return min(AR.STREAK_BONUS_MAX, max(0, int(streak)) * AR.STREAK_BONUS_PER_DAY)


def attendance_bonus(attendance: float) -> float:
    return AR.ATTENDANCE_BONUS_MAX * min(1.0, max(0.0, float(attendance)))


def stream_bonus(stream: str | None, card_stream: str | None, stat: str) -> float:
    out = 0.0
    if card_stream is not None and card_stream == stream:
        out += AR.STREAM_CARD_BONUS
    s = AR.STREAMS.get(stream or "")
    if s is not None and stat in s.bonus:
        out += AR.STREAM_STAT_BONUS
    return out


def skill_parts(student, card: CARDS.Card, button: CARDS.Button) -> dict:
    return {
        "stat": float(getattr(student, button.stat)),
        "stream": stream_bonus(getattr(student, "stream", None), card.stream, button.stat),
        "streak": streak_bonus(getattr(student, "streak", 0) or 0),
        "attendance": attendance_bonus(getattr(student, "attendance", 0.0) or 0.0),
    }


def chance_of(student, card: CARDS.Card, button: CARDS.Button) -> tuple[float, float, dict]:
    """(chance before the dice, skill, breakdown)."""
    parts = skill_parts(student, card, button)
    skill = sum(parts.values())
    p = (AR.CHANCE_BASE + (skill - AR.STAT_TARGET[card.difficulty]) / AR.SKILL_SCALE + AR.RISK_CHANCE[button.risk])
    return min(AR.CHANCE_MAX, max(AR.CHANCE_MIN, p)), skill, parts


def luck_of(roll: int) -> float:
    """The dice (1..100) as a chance shift in [-LUCK_CAP, +LUCK_CAP]. Luck can never move the chance by more than that."""
    r = min(100, max(1, int(roll)))
    return round((r - 50.5) / 49.5 * AR.LUCK_CAP, 6)


def round_points(risk: str, success: bool) -> int:
    win, lose = AR.ROUND_POINTS[risk]
    return win if success else lose


def _button(card: CARDS.Card, choice) -> tuple[int, CARDS.Button]:
    if isinstance(choice, CARDS.Button):
        return card.buttons.index(choice), choice
    i = int(choice)
    if not 0 <= i < len(card.buttons):
        raise ValueError(f"card {card.key} has no button {i}")
    return i, card.buttons[i]


def play_round(student, card: CARDS.Card, choice, rng: random.Random | None = None) -> RoundResult:
    """Play ONE strategy round. `student`: an ExamStudent or an AcademyStudent row (stats + stream; optional `streak`,
    `attendance`). `choice`: button index or Button. `rng`: anything with randint(a, b) (random.Random(seed) in tests).
    Pure: one dice roll (randint(1, 100)), no I/O. Success when chance + luck >= AR.SUCCESS_LINE, where luck is within
    +-AR.LUCK_CAP, so the stat/prep decides everything outside a narrow band around the line."""
    rng = rng or random
    idx, btn = _button(card, choice)
    p, skill, parts = chance_of(student, card, btn)
    roll = int(rng.randint(1, 100))
    luck = luck_of(roll)
    ok = (p + luck) >= AR.SUCCESS_LINE
    return RoundResult(card.key, idx, btn.stat, btn.risk, round(p, 4), roll, luck, ok, round_points(btn.risk, ok),
                       btn.win if ok else btn.lose, round(skill, 2), parts)


# ------------------------------------------------------------------ score, grade, GPA
def exam_score(points: Iterable[int], rounds: int) -> float:
    """0..100 with one decimal. `rounds` = the number of rounds of the exam (missing rounds count as 0 points)."""
    total = sum(int(p) for p in points)
    return round(min(100.0, max(0.0, 100.0 * total / (max(1, rounds) * AR.SCORE_REF))), 1)


def grade_of(score: float) -> str:
    for g, lo in AR.GRADES:
        if score >= lo:
            return g
    return AR.GRADES[-1][0]


def gpa_of(score: float) -> float:
    """10-point GPA: score / 10, two decimals."""
    return round(min(100.0, max(0.0, score)) / 10.0, 2)


def combine(midterm: float | None, finals: float | None, midterm_closed: bool = False, finals_closed: bool = False) -> tuple[float | None, bool]:
    """(season score, provisional). Midterm and finals count 50/50. An exam not taken counts 0 once its window is closed;
    while it is still open (or not yet open) only the exams done count and the result is provisional. (None, True) = nothing yet."""
    vals: dict[str, float | None] = {}
    for kind, v, closed in (("midterm", midterm, midterm_closed), ("finals", finals, finals_closed)):
        vals[kind] = v if v is not None else (0.0 if closed else None)
    have = {k: v for k, v in vals.items() if v is not None}
    if not have:
        return None, True
    # a student who never took ANY exam is not ranked, even after the windows closed
    if midterm is None and finals is None:
        return None, not (midterm_closed and finals_closed)
    w = sum(AR.EXAM_WEIGHT[k] for k in have)
    total = sum(v * AR.EXAM_WEIGHT[k] for k, v in have.items()) / w
    return round(total, 2), len(have) < len(vals)


def on_probation(score: float | None) -> bool:
    return score is not None and gpa_of(score) < AR.PROBATION_GPA


def reward_for(grade: str) -> int:
    return int(AR.EXAM_REWARD.get(grade, 0))


# ------------------------------------------------------------------ ranking (ties share a rank: 1, 2, 2, 4)
@dataclass
class Ranked:
    rank: int
    user_id: int
    student_id: int
    name: str
    score: float
    gpa: float
    grade: str
    midterm: float | None = None
    finals: float | None = None
    provisional: bool = False
    probation: bool = False


def rank_rows(rows: Sequence[Ranked]) -> list[Ranked]:
    """Sort by score (best first; equal scores keep a stable order by user id) and give tied students the same rank."""
    out = sorted(rows, key=lambda r: (-r.score, r.user_id))
    prev, rank = None, 0
    for i, r in enumerate(out, 1):
        if r.score != prev:
            rank, prev = i, r.score
        r.rank = rank
    return out


# ------------------------------------------------------------------ calendar of the exams (pure, `now` is a parameter)
def exam_window(season_start: datetime, kind: str) -> tuple[datetime, datetime]:
    day = AR.EXAM_DAY[kind]
    return season_start + (day - 1) * AR.DAY, season_start + day * AR.DAY


def exam_open_now(season_start: datetime, now: datetime) -> str | None:
    """The exam kind whose window contains `now`, else None."""
    for kind in AR.EXAM_KINDS:
        lo, hi = exam_window(season_start, kind)
        if lo <= now < hi:
            return kind
    return None


def next_exam(season_start: datetime, now: datetime) -> tuple[str, datetime] | None:
    """(kind, opens_at) of the next exam that has not opened yet."""
    for kind in AR.EXAM_KINDS:
        lo, _ = exam_window(season_start, kind)
        if now < lo:
            return kind, lo
    return None


def round_deadline(started_at: datetime) -> datetime:
    return started_at + timedelta(seconds=AR.ROUND_SECONDS)


def round_expired(started_at: datetime, now: datetime) -> bool:
    """True once the 25 s (plus the small network grace) are over. Enforced by timestamps only: no timers anywhere."""
    return now > started_at + timedelta(seconds=AR.ROUND_SECONDS + AR.ROUND_GRACE_SECONDS)


def seconds_left(started_at: datetime, now: datetime) -> int:
    return max(0, int(round((started_at + timedelta(seconds=AR.ROUND_SECONDS) - now).total_seconds())))


def attendance_ratio(attended_days: int, possible_days: int) -> float:
    """Attendance of the class days the student could have attended (a late joiner has fewer: the 'lighter workload')."""
    if possible_days <= 0:
        return 1.0
    return min(1.0, max(0.0, attended_days / possible_days))


def possible_class_days(enrolled_day: int, exam_day: int) -> int:
    """Class days (AR.CLASS_DAYS) from the day the student enrolled up to the exam day, inclusive (at least 1)."""
    n = sum(1 for d in AR.CLASS_DAYS if enrolled_day <= d <= exam_day)
    return max(1, n)


# ------------------------------------------------------------------ drawing the cards of an attempt
def draw_cards(stream: str, kind: str, rng: random.Random, exclude: Iterable[str] = ()) -> list[str]:
    """Card keys for one attempt: `EXAM_STREAM_CARDS[kind]` from the player's stream, the rest general, all of the exam's
    difficulties, no repeats, shuffled; `exclude` (cards already seen this season) is avoided when there are enough others.
    Always returns exactly EXAM_ROUNDS[kind] keys."""
    n = AR.EXAM_ROUNDS[kind]
    diffs = AR.EXAM_DIFFICULTIES[kind]
    excl = set(exclude)

    def pool(cards, strict_diff=True, strict_excl=True):
        return [c.key for c in cards if (not strict_diff or c.difficulty in diffs) and (not strict_excl or c.key not in excl)]

    picked: list[str] = []

    def take(cards, k, relax=True):
        steps = ((True, True), (True, False), (False, True), (False, False)) if relax else ((True, True),)
        for strict_diff, strict_excl in steps:
            avail = [x for x in pool(cards, strict_diff, strict_excl) if x not in picked]
            rng.shuffle(avail)
            while avail and k > 0 and len(picked) < n:
                picked.append(avail.pop())
                k -= 1
            if k <= 0:
                break

    take(CARDS.stream_cards(stream), AR.EXAM_STREAM_CARDS[kind], relax=False)      # own-stream cards: only fresh ones
    take(CARDS.GENERAL, n - len(picked))
    take(CARDS.CARDS, n - len(picked))              # absolute fallback
    rng.shuffle(picked)
    return picked[:n]


def pick(key: str) -> CARDS.Card:
    return CARDS.BY_KEY[key]


# ------------------------------------------------------------------ text
def grade_line(score: float) -> str:
    g = grade_of(score)
    return f"{g} (GPA {gpa_of(score):.2f})"
