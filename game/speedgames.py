"""Pure rules for the two group speed games (no Telegram, no database -> trivially testable).

⚡ Fastest Tap  (/tap <amount>)   - a lobby, then a decoy button turns green: the first to tap wins.
💥 Crash Count  (/crash <amount>) - a lobby, then a secret word: the first to send its letter count wins.

Money rule (same house style as /ttt): nothing is held in escrow. Everybody must be able to pay the entry when they join and
again when the round starts; when there is a winner every other player pays min(entry, wallet) to the winner and
FEE_PCT of each payment is burned (a diamond sink, and it makes funnelling diamonds through alt accounts lossy).
No winner (time out, everybody wrong, /end) = nobody pays anything.
"""
from __future__ import annotations

import random
import re

from game.rules import R
from utils.content.speedgames import CRASH_WORDS
from utils.fmt import parse_amount

MIN_PLAYERS = 2
MAX_PLAYERS = 10
LOBBY_S = 30                    # the lobby closes by itself after this long (host can start earlier with the button)
START_CD_S = 20                 # per player: at most one new lobby every 20 s (anti spam)
STALE_S = 300                   # a game older than this is considered dead and is swept away
FEE_PCT = 0.05                  # share of every payment that is burned
MIN_STAKE = R.casino_min_bet
MAX_STAKE = R.casino_max_bet

TAP_DELAY = (2.0, 6.0)          # seconds of red "wait" before the button turns green
TAP_TIMEOUT_S = 15              # nobody tapped within this time -> no winner
CRASH_INTRO_S = 3               # "get ready" before the word appears (answers before that are ignored)
CRASH_TIMEOUT_S = 20            # nobody solved it -> no winner

_NUM = re.compile(r"^\s*(\d{1,3})\s*$")


def stake_problem(amount: int | None) -> str | None:
    """None when the entry is fine, else a user-facing (HTML) message."""
    if not amount:
        return None
    if amount < MIN_STAKE:
        return f"❌ The minimum entry is <b>{MIN_STAKE:,}</b> 💎."
    if amount > MAX_STAKE:
        return f"❌ The maximum entry is <b>{MAX_STAKE:,}</b> 💎."
    return None


def parse_stake(arg: str | None, wallet: int) -> int | None:
    """'100', '2k', 'all' (= your wallet, capped at MAX_STAKE) -> int, None if it is not an amount."""
    if not arg:
        return None
    return parse_amount(arg, min(max(0, wallet), MAX_STAKE))


def split_pay(pay: int) -> tuple[int, int]:
    """A loser pays `pay`: returns (net to the winner, burned). net + burned == pay always."""
    fee = int(pay * FEE_PCT)
    return pay - fee, fee


def letter_count(word: str) -> int:
    return sum(1 for ch in word if ch.isalpha())


def parse_number(text: str | None) -> int | None:
    """A plain 1-3 digit number typed in the chat ('7', ' 12 '), else None. Anything else is ordinary chatter."""
    m = _NUM.match(text or "")
    return int(m.group(1)) if m else None


def tap_delay(rng: random.Random | None = None) -> float:
    return (rng or random).uniform(*TAP_DELAY)


def pick_word(rng: random.Random | None = None, avoid: str = "") -> str:
    pool = [w for w in CRASH_WORDS if w != avoid]
    return (rng or random).choice(pool)


def reaction(seconds: float) -> str:
    return f"≈{max(0.0, seconds):.2f}s"
