"""Hack stats, rank titles and the daily code - pure Python (no Telegram, no database).

Stats are lifetime counters in the existing `user_stats` table (no new tables, no migration; keys are <= 32 characters).
Only finished GROUP games with 2+ players count, so practice and the daily code can't farm ranks.

    hack_play   games finished        hack_win    games won (a crack, or last player standing)
    hack_crack  wins by cracking      hack_tries  tries used in those cracks      hack_fast  fewest tries of a crack (lower = better)

The DAILY code is the same for everybody for one UTC day. It is derived from a server secret, so reading the source can't
reveal it. One attempt per player per day, played in a private chat; a solve pays diamonds once (ledger ref = idempotency).
"""
from __future__ import annotations

import hashlib
import random
from datetime import date

from game import hack as H, jumble_stats as JS

K_PLAY, K_WIN, K_CRACK, K_TRIES, K_FAST = "hack_play", "hack_win", "hack_crack", "hack_tries", "hack_fast"

# (wins needed, emoji, title) - best title you have reached
RANKS = ((0, "🐣", "Script Kiddie"), (3, "📡", "Packet Sniffer"), (10, "🎩", "Black Hat"),
         (25, "🔓", "Cipher Breaker"), (50, "💥", "Zero-Day"), (100, "👑", "Elite Hacker"))


def rank_for(wins: int) -> tuple[str, str]:
    """-> (emoji, title) for a number of wins."""
    best = RANKS[0]
    for r in RANKS:
        if wins >= r[0]:
            best = r
    return best[1], best[2]


def next_rank(wins: int) -> tuple[int, str] | None:
    """-> (wins still needed, title of the next rank) or None at the top."""
    for need, emoji, title in RANKS:
        if wins < need:
            return need - wins, f"{emoji} {title}"
    return None


def summarize(stats: dict[str, int]) -> dict:
    play, win = stats.get(K_PLAY, 0), stats.get(K_WIN, 0)
    crack, tries = stats.get(K_CRACK, 0), stats.get(K_TRIES, 0)
    return {"play": play, "win": win, "rate": round(100 * win / play) if play else 0,
            "avg_tries": round(tries / crack, 1) if crack else None, "fast": stats.get(K_FAST, 0) or None,
            "rank": rank_for(win), "next": next_rank(win)}


# ----------------------------------------------------------------------------- the daily code
DAILY_LEN = 4
DAILY_KIND = "hack_daily"          # reward + record row of one player's solved daily code
DAILY_BASE = 100                   # diamonds for solving it ...
DAILY_PER_TRY = 25                 # ... plus this much for every try left unused
LOOKBACK_DAYS = JS.LOOKBACK_DAYS


def daily_code(day: date, secret: str, n: int = DAILY_LEN) -> str:
    """The code of a day: deterministic (same for everyone, survives restarts) but unguessable without the secret."""
    seed = hashlib.sha256(f"hackdaily:{secret}:{day.isoformat()}:{n}".encode()).digest()
    return H.new_code(n, random.Random(int.from_bytes(seed[:8], "big")))


def daily_ref(day: date, uid: int) -> str:
    return f"hackdaily:{day:%Y%m%d}:{uid}"


def daily_ref_prefix(day: date) -> str:
    return f"hackdaily:{day:%Y%m%d}:"


def daily_reward(tries: int, streak: int, n: int = DAILY_LEN) -> tuple[int, int]:
    """-> (diamonds, streak bonus in %). `streak` = days in a row BEFORE today (Jumble's +5 % per day, up to +50 %)."""
    left = max(0, H.max_guesses(n) - max(1, tries))
    bonus_pct = min(max(streak, 0), JS.STREAK_MAX_DAYS) * JS.STREAK_STEP_PCT
    total = DAILY_BASE + DAILY_PER_TRY * left
    return total + total * bonus_pct // 100, bonus_pct


def history_rows(secret: str, guesses: list[str]) -> list[tuple[str, str, int, int]]:
    """Guesses scored against the secret, in the (name, guess, hacks, glitches) shape the Hack board uses."""
    return [("", g, *H.score(secret, g)) for g in guesses]
