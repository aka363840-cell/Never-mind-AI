"""WordSeek rules - pure Python (no Telegram, no database).
A hidden 5-letter word; everybody in the chat guesses; 🟩 right letter right place, 🟨 right letter wrong place, 🟥 not in the word.
The game ends when somebody finds the word or after MAX_GUESSES guesses. Winner's points: 36 - guesses used (5..35)."""
from __future__ import annotations

import random
import re

from game.wordseek_data import ANSWERS, VALID

MAX_GUESSES = 30
LENGTH = 5
SQUARE = {"g": "🟩", "y": "🟨", "r": "🟥"}
_WORD = re.compile(r"[A-Za-z]{5}")


def is_candidate(text: str) -> bool:
    """Does this chat message look like a guess (exactly five letters, nothing else)?"""
    return bool(_WORD.fullmatch(text.strip()))


def is_valid(word: str) -> bool:
    return word.lower() in VALID


def feedback(secret: str, guess: str) -> list[str]:
    """Standard Wordle marking, repeated letters handled correctly. -> ['g'|'y'|'r'] * 5"""
    secret, guess = secret.lower(), guess.lower()
    res = ["r"] * LENGTH
    left: dict[str, int] = {}
    for i in range(LENGTH):
        if guess[i] == secret[i]:
            res[i] = "g"
        else:
            left[secret[i]] = left.get(secret[i], 0) + 1
    for i in range(LENGTH):
        if res[i] == "r" and left.get(guess[i], 0) > 0:
            res[i] = "y"
            left[guess[i]] -= 1
    return res


def squares(fb: list[str]) -> str:
    return "".join(SQUARE[c] for c in fb)


def points(n_guesses: int) -> int:
    return max(5, 36 - n_guesses)


def absent_letters(secret: str, guesses: list[str]) -> list[str]:
    """Letters guessed so far that are definitely not in the word."""
    secret = secret.lower()
    return sorted({c for g in guesses for c in g.lower() if c not in secret})


def pick_word(avoid: set[str] | None = None, rng: random.Random | None = None) -> str:
    pool = [w for w in ANSWERS if not avoid or w not in avoid] or list(ANSWERS)
    return (rng or random).choice(pool)
