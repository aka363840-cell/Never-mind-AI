"""Wordly rules - pure Python (no Telegram, no database).
A rack of 8 letters; everybody in the chat types words (4-6 letters) that can be made from it. Each word counts once:
the first player to type it gets it. Words come from the WordSeek dictionary (game/wordseek_data.py)."""
from __future__ import annotations

import random
import re
from collections import Counter

from game.wordly_data import EXTRA
from game.wordseek_data import ANSWERS, VALID

RACK = 8
ROUND_S = 120                       # length of a round
MIN_LEN, MAX_LEN = 3, 8             # 3-8 letters (4-6 from the WordSeek lists, 3/7/8 from game/wordly_data.py)
MIN_WORDS = 25                      # a rack must allow at least this many accepted words ...
MIN_COMMON = 12                     # ... and at least this many everyday words
SCORE = {3: 1, 4: 2, 5: 4, 6: 7, 7: 11, 8: 16}      # points per word length
_WORD = re.compile(r"[A-Za-z]{3,8}")
_EXTRA = "eeeeeaaaaiiiooutnnrrsssslldcmhpgbfywkv"      # weighted pool for the 2 letters added to a 6-letter base word


def is_candidate(text: str) -> bool:
    """Does this chat message look like a Wordly word (3-8 letters, nothing else)?"""
    return bool(_WORD.fullmatch(text.strip()))


def points(word: str) -> int:
    return SCORE.get(len(word), 0)


def can_make(word: str, rack: str) -> bool:
    return not (Counter(word.lower()) - Counter(rack.lower()))


def solutions(rack: str, common: bool = False) -> list[str]:
    """Every dictionary word of 4-6 letters that can be spelled with the rack (each letter used at most as often as it appears).
    common=True: only everyday words (used to show the words that were missed - the full list also holds rare ones)."""
    letters, have = set(rack.lower()), Counter(rack.lower())
    out = []
    for n in range(MIN_LEN, MAX_LEN + 1):
        src = (set(ANSWERS.get(n, ())) | EXTRA.get(n, frozenset())) if common else (VALID.get(n, frozenset()) | EXTRA.get(n, frozenset()))
        for w in src:
            if set(w) <= letters and not (Counter(w) - have):
                out.append(w)
    return sorted(out, key=lambda w: (-len(w), w))


def make_rack(rng: random.Random | None = None, min_words: int = MIN_WORDS, tries: int = 40) -> tuple[str, list[str]]:
    """(rack, accepted words). Base = a shuffled 8-letter word, or a common 6-letter word + 2 random letters; retried until the rack allows enough words."""
    rng = rng or random.SystemRandom()
    best: tuple[str, list[str]] = ("", [])
    for _ in range(tries):
        if rng.random() < 0.5 and EXTRA[8]:             # half of the racks are an 8-letter word shuffled (a 16-point word exists)
            letters = list(rng.choice(sorted(EXTRA[8])))
        else:
            letters = list(rng.choice(ANSWERS[6])) + [rng.choice(_EXTRA) for _ in range(RACK - 6)]
        rng.shuffle(letters)
        rack = "".join(letters)
        sols = solutions(rack)
        if len(sols) > len(best[1]):
            best = (rack, sols)
        if len(sols) >= min_words and len(solutions(rack, common=True)) >= MIN_COMMON:
            return rack, sols
    return best


def show_rack(rack: str) -> str:
    return "  ".join(rack.upper())
