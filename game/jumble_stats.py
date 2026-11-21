"""Word Jumble stats, streaks and rewards - pure Python (no Telegram, no database).

No new tables: every finished group game writes ONE ledger row per scoring player (Transaction.kind == KIND, the diamond reward),
and the points / words / rank are encoded in the row's ref:

    jumble:<chat id>:<game id>:<user id>:<points>:<words>:<rank>

Leaderboards, personal stats and daily streaks are computed from those rows (services/jumble.py runs the queries).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

KIND = "jumble_win"            # reward + stats row of one player in one game
BET_KIND = "jumble_bet"        # entry fee taken
REFUND_KIND = "jumble_refund"  # entry fee given back
POT_KIND = "jumble_pot"        # pot paid to the winner(s)

PERIODS = ("today", "week", "month", "all")

# ----------------------------------------------------------------------------- rewards (tune here; the reward is paid in diamonds)
REWARD_PER_POINT = 5           # diamonds per point scored in a game
DAILY_REWARD_CAP = 1500        # most diamonds one player can earn from Jumble per UTC day (anti-farming)
MIN_REWARD = 1                 # after the cap (or with REWARD_PER_POINT = 0) a game still pays this, so its points stay on the leaderboard
STREAK_STEP_PCT = 5            # +5 % reward per day of streak ...
STREAK_MAX_DAYS = 10           # ... up to 10 days (+50 %)
MIN_SCORERS = 2                # a game only pays / counts when at least this many players scored (no solo farming)
LOOKBACK_DAYS = 40             # how far back streaks are looked up


def make_ref(chat_id: int, gid: str, uid: int, pts: int, words: int, rank: int) -> str:
    return f"jumble:{chat_id}:{gid}:{uid}:{pts}:{words}:{rank}"


def parse_ref(ref: str | None) -> tuple[int, str, int, int, int, int] | None:
    """-> (chat id, game id, user id, points, words, rank) or None for a ref that is not a Jumble stats row."""
    parts = (ref or "").split(":")
    if len(parts) != 7 or parts[0] != "jumble":
        return None
    try:
        return int(parts[1]), parts[2], int(parts[3]), int(parts[4]), int(parts[5]), int(parts[6])
    except ValueError:
        return None


@dataclass
class Stat:
    points: int = 0
    words: int = 0
    games: int = 0
    wins: int = 0


def aggregate(rows) -> dict[int, Stat]:
    """rows: (user id, ref) pairs -> {user id: Stat}."""
    out: dict[int, Stat] = {}
    for uid, ref in rows:
        p = parse_ref(ref)
        if p is None:
            continue
        st = out.setdefault(uid, Stat())
        st.points += p[3]
        st.words += p[4]
        st.games += 1
        st.wins += 1 if p[5] == 1 else 0
    return out


def top(stats: dict[int, Stat], n: int = 10) -> list[tuple[int, Stat]]:
    return sorted(stats.items(), key=lambda kv: (-kv[1].points, -kv[1].words, kv[0]))[:n]


def since(period: str, now: datetime) -> datetime | None:
    """Start of a leaderboard period (UTC); None = all time."""
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return now - timedelta(days=30)
    return None


def streak_from_days(days: set[date], today: date) -> int:
    """Consecutive days with a game, counted back from today (or from yesterday when today has no game yet)."""
    d = today if today in days else today - timedelta(days=1)
    n = 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


def reward(points: int, streak: int, earned_today: int) -> tuple[int, int]:
    """-> (diamonds, streak bonus in %). `streak` = days in a row BEFORE this game's day counts (see streak_from_days)."""
    bonus_pct = min(max(streak, 0), STREAK_MAX_DAYS) * STREAK_STEP_PCT
    total = points * REWARD_PER_POINT
    total += total * bonus_pct // 100
    room = max(0, DAILY_REWARD_CAP - earned_today)
    return max(MIN_REWARD, min(total, room)), bonus_pct


def split_pot(pot: int, winners: int, fee_pct: int = 0) -> list[int]:
    """Shares of the pot for `winners` tied winners (the remainder goes to the first one)."""
    if winners <= 0:
        return []
    prize = pot - pot * fee_pct // 100
    each = prize // winners
    shares = [each] * winners
    shares[0] += prize - each * winners
    return shares
