"""WordSeek rules - pure Python (no Telegram, no database).
A hidden 4, 5 or 6-letter word; everybody in the chat guesses; 🟩 right letter right place, 🟨 right letter wrong place, 🟥 not in the word.
The game ends when somebody finds the word or the guesses run out (25 / 30 / 35 for 4 / 5 / 6 letters).
Winner's points: guess limit + 6 - guesses used (never below 5), e.g. 36 - guesses for a 5-letter game."""
from __future__ import annotations

import random
import re

from game.wordseek_data import ANSWERS, VALID

LENGTHS = (4, 5, 6)
DEFAULT_LEN = 5
LIMITS = {4: 25, 5: 30, 6: 35}
SQUARE = {"g": "🟩", "y": "🟨", "r": "🟥"}
_WORD = re.compile(r"[A-Za-z]{4,6}")


def is_candidate(text: str) -> bool:
    """Does this chat message look like a guess (4-6 letters, nothing else)?"""
    return bool(_WORD.fullmatch(text.strip()))


def max_guesses(length: int) -> int:
    return LIMITS.get(length, 30)


def is_valid(word: str) -> bool:
    w = word.lower()
    return w in VALID.get(len(w), ())


def feedback(secret: str, guess: str) -> list[str]:
    """Standard Wordle marking, repeated letters handled correctly. -> ['g'|'y'|'r'] per letter"""
    secret, guess = secret.lower(), guess.lower()
    n = len(secret)
    res = ["r"] * n
    left: dict[str, int] = {}
    for i in range(n):
        if guess[i] == secret[i]:
            res[i] = "g"
        else:
            left[secret[i]] = left.get(secret[i], 0) + 1
    for i in range(n):
        if res[i] == "r" and left.get(guess[i], 0) > 0:
            res[i] = "y"
            left[guess[i]] -= 1
    return res


def squares(fb: list[str]) -> str:
    return "".join(SQUARE[c] for c in fb)


def points(n_guesses: int, length: int = DEFAULT_LEN) -> int:
    return max(5, max_guesses(length) + 6 - n_guesses)


def absent_letters(secret: str, guesses: list[str]) -> list[str]:
    """Letters guessed so far that are definitely not in the word."""
    secret = secret.lower()
    return sorted({c for g in guesses for c in g.lower() if c not in secret})


def pick_word(length: int = DEFAULT_LEN, avoid: set[str] | None = None, rng: random.Random | None = None) -> str:
    words = ANSWERS[length]
    pool = [w for w in words if not avoid or w not in avoid] or list(words)
    return (rng or random).choice(pool)
