"""Academy social, PURE half (Part 4): rival pairing, bot rivals, duel scoring and prizes, club-battle scoring, crush / date /
buddy rules. Nothing here touches the database, the clock or Telegram: everything takes its inputs as parameters, so a whole
season can be simulated in tests. The database half is services/academy_social.py.

Every scenario round goes through Part 3's play_round (services.academy_exam_logic): no new chance maths, no real-subject questions.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Sequence

from game import academy_rules as AR
from services import academy_exam_logic as L
from utils.content import academy_exam_rounds as CARDS
from utils.content import academy_social_texts as T


# ================================================================== rivals: pairing
@dataclass(frozen=True)
class Candidate:
    user_id: int
    gpa: float          # last season's GPA (AR.RIVAL_DEFAULT_GPA when the student has none)
    knowledge: int


def pair_score(c: Candidate) -> float:
    """One number that puts GPA (0..10) and Knowledge (0..100) on the same scale: close scores = similar students."""
    return c.gpa * AR.RIVAL_GPA_WEIGHT + c.knowledge


def _order(cands: Iterable[Candidate]) -> list[Candidate]:
    return sorted(cands, key=lambda c: (pair_score(c), c.user_id))          # user id breaks ties: fully deterministic


def _pair_costs(scores: Sequence[float]) -> tuple[list[float], list[float]]:
    """pre[k] = cost of pairing (0,1),(2,3).. over the first k items (k even); suf[j] = cost of pairing (j,j+1),(j+2,j+3).. to the end."""
    n = len(scores)
    pre = [0.0] * (n + 1)
    for k in range(2, n + 1, 2):
        pre[k] = pre[k - 2] + abs(scores[k - 1] - scores[k - 2])
    suf = [0.0] * (n + 2)
    for j in range(n - 2, -1, -1):
        if (n - j) % 2 == 0:
            suf[j] = suf[j + 2] + abs(scores[j + 1] - scores[j])
    return pre, suf


def pair_students(cands: Iterable[Candidate]) -> tuple[list[tuple[int, int]], int | None]:
    """([(user_a, user_b), ...], odd_user_id | None). Students are sorted by pair_score and paired with their neighbour, so each
    pair is as similar as the list allows. With an odd number the one who is left out is chosen to keep the total distance
    smallest (that student gets a bot rival). Every student appears exactly once. Pure and deterministic."""
    rows = _order(cands)
    n = len(rows)
    if n == 0:
        return [], None
    scores = [pair_score(c) for c in rows]
    odd_idx = -1
    if n % 2:
        pre, suf = _pair_costs(scores)
        best = None
        for i in range(0, n, 2):                      # removing an even index keeps the earlier pairs aligned
            cost = pre[i] + (suf[i + 1] if i + 1 <= n else 0.0)
            if best is None or cost < best - 1e-9:
                best, odd_idx = cost, i
    rest = [c for i, c in enumerate(rows) if i != odd_idx]
    pairs = [(rest[i].user_id, rest[i + 1].user_id) for i in range(0, len(rest), 2)]
    return pairs, (rows[odd_idx].user_id if odd_idx >= 0 else None)


# ================================================================== rivals: bot rival
def bot_name(seed: int) -> str:
    names = T.BOT_RIVAL_NAMES
    return names[abs(int(seed)) % len(names)]


def bot_student(stats: dict, rng: random.Random) -> L.ExamStudent:
    """A bot rival that is about as good as the player: each stat = the player's stat +- RIVAL_BOT_STAT_SPREAD."""
    spread = AR.RIVAL_BOT_STAT_SPREAD
    vals = {k: min(AR.STAT_MAX, max(AR.STAT_MIN, int(stats.get(k, AR.START_STATS[k])) + rng.randint(-spread, spread)))
            for k in AR.STAT_KEYS}
    return L.ExamStudent(rng.choice(sorted(AR.STREAMS)), vals["knowledge"], vals["charisma"], vals["fitness"], vals["creativity"])


def bot_choice(student, card: CARDS.Card) -> int:
    """The bot plays the move with the best expected points (chance * win + (1 - chance) * lose). Ties: first button."""
    best_i, best_ev = 0, None
    for i, b in enumerate(card.buttons):
        p, _, _ = L.chance_of(student, card, b)
        win, lose = AR.ROUND_POINTS[b.risk]
        ev = p * win + (1 - p) * lose
        if best_ev is None or ev > best_ev + 1e-9:
            best_i, best_ev = i, ev
    return best_i


# ================================================================== scenario cards for duels and battles
def duel_cards(rng: random.Random) -> list[str]:
    """RIVAL_DUEL_ROUNDS different general cards (difficulty 1..2), shuffled by `rng`."""
    pool = [c.key for c in CARDS.GENERAL if c.difficulty in (1, 2)]
    rng.shuffle(pool)
    return pool[:AR.RIVAL_DUEL_ROUNDS]


def battle_card(seed: str) -> str:
    """The one general card of AR.BATTLE_DIFFICULTY everybody plays in a season's club battle (same seed = same card)."""
    pool = sorted(c.key for c in CARDS.GENERAL if c.difficulty == AR.BATTLE_DIFFICULTY)
    return random.Random(seed).choice(pool)


def boosted(student, stat: str, bonus: int) -> L.ExamStudent:
    """A copy of the student with `stat` raised by `bonus` (the club's focus stat in the battle). Stats may exceed 100 here:
    play_round clamps the chance anyway."""
    vals = {k: int(getattr(student, k)) for k in AR.STAT_KEYS}
    if stat in vals:
        vals[stat] += int(bonus)
    return L.ExamStudent(getattr(student, "stream", "science"), vals["knowledge"], vals["charisma"], vals["fitness"],
                         vals["creativity"], getattr(student, "streak", 0) or 0, getattr(student, "attendance", 0.0) or 0.0)


def battle_student(student, club: str) -> L.ExamStudent:
    focus = AR.CLUBS[club].focus if club in AR.CLUBS else ""
    return boosted(student, focus, AR.BATTLE_FOCUS_BONUS)


# ================================================================== duel result and prizes
def side_total(points: Iterable[int]) -> int:
    return int(sum(int(p) for p in points))


def duel_outcome(total_a: int, total_b: int) -> str:
    """'a' | 'b' | 'tie' : the higher total wins."""
    return "a" if total_a > total_b else ("b" if total_b > total_a else "tie")


@dataclass(frozen=True)
class Prize:
    diamonds: int
    badge: int


def duel_prize(result: str, walkover: bool = False) -> Prize:
    """Prize of the SIDE that is being paid. result: 'win' | 'tie' | 'lose'. A walkover win pays RIVAL_WALKOVER_PCT % and no badge."""
    if result == "win":
        if walkover:
            return Prize(AR.RIVAL_WIN_DIAMONDS * AR.RIVAL_WALKOVER_PCT // 100, 0)
        return Prize(AR.RIVAL_WIN_DIAMONDS, AR.RIVAL_BADGE_POINTS)
    if result == "tie":
        return Prize(AR.RIVAL_TIE_DIAMONDS, 0)
    return Prize(0, 0)


# ================================================================== club battle scoring
@dataclass
class ClubRow:
    club: str
    practice: int       # Part 2 club points
    battle: int         # sum of the members' battle round points (a failed risky round can be negative)
    players: int        # members who played their round

    @property
    def total(self) -> int:
        return self.practice + self.battle


def club_rows(practice: dict[str, int], battle: dict[str, int], players: dict[str, int]) -> list[ClubRow]:
    """All clubs, best first. Tie-break: more players, then the order of AR.CLUBS (so the order is always the same)."""
    order = list(AR.CLUBS)
    rows = [ClubRow(c, int(practice.get(c, 0)), int(battle.get(c, 0)), int(players.get(c, 0))) for c in order]
    rows.sort(key=lambda r: (-r.total, -r.players, order.index(r.club)))
    return rows


def winning_club(rows: Sequence[ClubRow]) -> str | None:
    """The winner is the best club, but only if it scored above zero (a season nobody played has no winner)."""
    return rows[0].club if rows and rows[0].total > 0 else None


# ================================================================== crushes
def crush_block(*, day: int, sender_enrolled: bool, target_enrolled: bool, same_person: bool, target_is_bot: bool,
                has_active: bool, sender_in_couple: bool, target_in_couple: bool) -> str | None:
    """The reason a /crush is refused (a friendly text), or None if it may go ahead. Order matters: the cheapest, most
    obvious reasons first. None of the texts says anything about how the target feels."""
    if not sender_enrolled:
        return T.NEED_ENROLL
    if same_person:
        return T.SELF
    if target_is_bot:
        return T.BOT_TARGET
    if not AR.crush_open(day):
        return T.NOT_YET.format(day=AR.CRUSH_FROM_DAY)
    if sender_in_couple:
        return T.CRUSH_IN_COUPLE
    if has_active:
        return T.CRUSH_ALREADY
    if not target_enrolled:
        return T.TARGET_NOT_ENROLLED
    if target_in_couple:
        return T.CRUSH_TARGET_TAKEN
    return None


def is_mutual(a: int, b: int, crushes: dict[int, int]) -> bool:
    """`crushes` maps each player to the player they crush on. A crush is mutual only when BOTH point at each other."""
    return a != b and crushes.get(a) == b and crushes.get(b) == a


def couple_key(a: int, b: int) -> tuple[int, int]:
    """The canonical (smaller id, bigger id) of a couple."""
    return (a, b) if a < b else (b, a)


# ================================================================== campus date
def date_block(*, day: int, in_couple: bool, done_today: bool) -> str | None:
    if not in_couple:
        return T.DATE_NEED_COUPLE
    if not AR.crush_open(day):
        return T.DATE_CLOSED_DAY.format(day=AR.CRUSH_FROM_DAY)
    if done_today:
        return T.DATE_ONCE
    return None


def date_gains(rng: random.Random) -> dict[str, int]:
    """One partner's stat boost: Charisma +1..2 and +1 on one other random stat."""
    lo, hi = AR.DATE_STAT_GAIN
    out = {"charisma": rng.randint(lo, hi)}
    other = rng.choice([k for k in AR.STAT_KEYS if k != "charisma"])
    out[other] = out.get(other, 0) + 1
    return out


def date_line(rng: random.Random) -> str:
    return rng.choice(T.DATE_LINES)


# ================================================================== study buddies
def buddy_block(*, sender_enrolled: bool, target_enrolled: bool, same_person: bool, target_is_bot: bool,
                sender_has_buddy: bool, target_has_buddy: bool, sender_pending: int) -> str | None:
    if not sender_enrolled:
        return T.NEED_ENROLL
    if same_person:
        return T.SELF
    if target_is_bot:
        return T.BOT_TARGET
    if sender_has_buddy:
        return T.BUDDY_ALREADY_HAS
    if sender_pending >= AR.BUDDY_PENDING_MAX:
        return T.BUDDY_PENDING_LIMIT
    if not target_enrolled:
        return T.TARGET_NOT_ENROLLED
    if target_has_buddy:
        return T.BUDDY_TARGET_HAS
    return None


# ================================================================== calendar helpers (pure; day = season day 1..7)
def rival_pairing_due(day: int) -> bool:
    return AR.RIVAL_PAIR_FROM_DAY <= day <= AR.RIVAL_PAIR_LAST_DAY


def duel_open(day: int) -> bool:
    return AR.RIVAL_PAIR_FROM_DAY <= day <= AR.RIVAL_DUEL_LAST_DAY


def battle_due(day: int) -> bool:
    """The tick may open the battle (a late tick still opens it while it could be played)."""
    return AR.BATTLE_FROM_DAY <= day <= AR.BATTLE_LAST_DAY


def battle_window(season_start, ) -> tuple:
    """(opens_at, closes_at) of the club battle: from the start of Day BATTLE_FROM_DAY to the end of Day BATTLE_LAST_DAY."""
    return (season_start + (AR.BATTLE_FROM_DAY - 1) * AR.DAY, season_start + AR.BATTLE_LAST_DAY * AR.DAY)


def duel_deadline(season_start):
    """The end of Day RIVAL_DUEL_LAST_DAY."""
    return season_start + AR.RIVAL_DUEL_LAST_DAY * AR.DAY
