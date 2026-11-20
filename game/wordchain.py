"""Word Chain rules - pure Python (no Telegram, no database).

Players take turns saying a word that STARTS with the LAST letter of the previous word.
A word must be a real English word, long enough for the current level, and not used before in this game.
Whoever runs out of time is eliminated; the last player standing wins.

Difficulty goes up as the game goes on: every 2 full rounds the level rises -
  level 1: 3+ letters, 45 s  ->  level 2: 4+, 40 s  ->  level 3: 5+, 35 s  ->  level 4: 6+, 30 s
  ->  level 5: 7+, 25 s  ->  then the minimum length stays at 7 and the clock keeps shrinking down to 10 s.
Word list: game/wordchain_words.txt.gz (flat English word list, generated from the open-source hunspell en_US
dictionary) plus the WordSeek answer/guess lists.
"""
from __future__ import annotations

import gzip
import random
import re
from dataclasses import dataclass
from pathlib import Path

LOBBY_S = 90                  # the lobby closes by itself after this long
MIN_PLAYERS, MAX_PLAYERS = 2, 30
ROUNDS_PER_LEVEL = 2
MIN_LEN_START, MIN_LEN_CAP = 3, 7
TIME_START, TIME_STEP, TIME_FLOOR = 45, 5, 10
START_LETTERS = "abcdefghilmnoprstw"          # letters with plenty of words, so the first turn is always fair
_CANDIDATE = re.compile(r"[A-Za-z]{1,45}")
_FILE = Path(__file__).with_name("wordchain_words.txt.gz")


@dataclass(frozen=True)
class Level:
    n: int
    min_len: int
    seconds: int


def level_for(round_no: int) -> Level:
    """Level of a given round (rounds start at 1)."""
    n = (max(1, round_no) - 1) // ROUNDS_PER_LEVEL + 1
    return Level(n, min(MIN_LEN_START + n - 1, MIN_LEN_CAP), max(TIME_FLOOR, TIME_START - TIME_STEP * (n - 1)))


_words: frozenset[str] | None = None
_by_letter: dict[str, list[str]] | None = None


def words() -> frozenset[str]:
    global _words
    if _words is None:
        with gzip.open(_FILE, "rt", encoding="utf-8") as fh:
            found = {w for w in fh.read().split() if w.isalpha()}
        try:                                                    # the WordSeek lists are real words too
            from game.wordseek_data import VALID
            for group in VALID.values():
                found.update(w.lower() for w in group)
        except Exception:
            pass
        _words = frozenset(found)
    return _words


def _index() -> dict[str, list[str]]:
    """letter -> its words, longest first (so 'is there an unused word of length >= n' stops early)."""
    global _by_letter
    if _by_letter is None:
        idx: dict[str, list[str]] = {}
        for w in words():
            idx.setdefault(w[0], []).append(w)
        for lst in idx.values():
            lst.sort(key=len, reverse=True)
        _by_letter = idx
    return _by_letter


def is_candidate(text: str) -> bool:
    """Does this chat message look like a move (one plain word, nothing else)?"""
    return bool(_CANDIDATE.fullmatch(text.strip()))


def is_word(word: str) -> bool:
    return word.lower() in words()


def has_word(letter: str, min_len: int, used: set[str]) -> bool:
    """Is there still an unused word that starts with `letter` and has at least `min_len` letters?"""
    for w in _index().get(letter.lower(), ()):
        if len(w) < min_len:
            return False
        if w not in used:
            return True
    return False


def check(word: str, letter: str, min_len: int, used: set[str]) -> str | None:
    """None if the move is fine, else the reason (a short sentence for the player)."""
    w = word.strip().lower()
    if not w.startswith(letter.lower()):
        return f"The word must start with <b>{letter.upper()}</b>."
    if len(w) < min_len:
        return f"Too short - at least <b>{min_len}</b> letters at this level."
    if w in used:
        return f"<code>{w.upper()}</code> was already used in this game."
    if w not in words():
        return f"<code>{w.upper()}</code> isn't in my dictionary."
    return None


def pick_letter(min_len: int, used: set[str], rng: random.Random | None = None, avoid: str = "") -> str:
    """A random start letter that still has words (used after a dead end and for the very first turn)."""
    r = rng or random
    pool = [c for c in START_LETTERS if c != avoid and has_word(c, min_len, used)] or [c for c in "abcdefghijklmnopqrstuvwxyz" if has_word(c, min_len, used)]
    return r.choice(pool) if pool else r.choice(START_LETTERS)
