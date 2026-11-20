"""Hack (password cracking) rules - pure Python (no Telegram, no database).
The secret is a code of N different digits (leading zeros allowed). Every guess is N digits and gets two numbers:
  hacks   = right digit in the right position
  glitches = right digit in the wrong position"""
from __future__ import annotations

import random
import re

LENGTHS = (3, 4, 5, 6)
MAX_GUESSES = {3: 8, 4: 10, 5: 12, 6: 14}      # attempts PER PLAYER
LOBBY_S = 60                                    # lobby waits this long, then starts (2+ players) or is cancelled
TURN_S = 45                                     # time for one guess
MAX_FORFEIT = 2                                 # this many timed-out turns in a row = out of the game
MAX_PLAYERS = 6
FEE_PCT = 0                                     # % of the pot kept by the house (0 = winner takes it all)


def max_guesses(n: int) -> int:
    return MAX_GUESSES.get(n, 10)


def new_code(n: int, rng: random.Random | None = None) -> str:
    return "".join((rng or random.SystemRandom()).sample("0123456789", n))


def is_guess(text: str, n: int) -> bool:
    return bool(re.fullmatch(r"[0-9]{%d}" % n, text.strip()))


def score(secret: str, guess: str) -> tuple[int, int]:
    """-> (hacks, glitches). Works for guesses with repeated digits too (every secret digit is matched at most once)."""
    hacks = sum(a == b for a, b in zip(secret, guess))
    common = sum(min(secret.count(d), guess.count(d)) for d in set(guess))
    return hacks, common - hacks
