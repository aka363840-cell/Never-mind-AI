"""Word Jumble rules - pure Python (no Telegram, no database).

A multiplayer word unscramble game. The bot shows a scrambled word, the first player to type a valid unscrambling wins
the round. A game is either a fixed number of ROUNDS or a RACE to a points target, with one difficulty (or MIXED, which gets
harder as the game goes on), a speed (time per word), and an optional word THEME.

Words: WordSeek dictionary (game/wordseek_data.py: ANSWERS = everyday words used as puzzles, VALID = every accepted word),
plus game/jumble_words.py (themes and 7-8 letter words). Any real anagram of the puzzle is accepted ("listen" / "silent").
"""
from __future__ import annotations

import random
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from game.jumble_words import EXTRA, THEMES
from game.wordseek_data import ANSWERS, VALID

# ----------------------------------------------------------------------------- settings offered in the setup panel
ROUND_CHOICES = (3, 5, 10)          # "Rounds" mode
TARGET_CHOICES = (10, 20, 30)       # "Race" mode: first to this many points
DEFAULT_ROUNDS = 5
DEFAULT_TARGET = 20
MAX_RACE_ROUNDS = 40                # a race that nobody wins stops after this many words
DEFAULT_DIFF = "medium"
DEFAULT_SPEED = "normal"
DEFAULT_THEME = "all"
SETUP_S = 120                       # an unstarted setup panel is closed after this long
BREAK_S = 4                         # pause between two rounds
IDLE_ROUNDS = 2                     # this many rounds in a row without an answer = the game stops (nobody is playing)
MAX_AGE_S = 3 * 3600                # a game that is still registered after this long is considered stuck and dropped

# ----------------------------------------------------------------------------- entry fee (diamonds; groups only)
ENTRY_CHOICES = (100, 500, 1000)    # buttons; hidden when outside the bot's casino bet limits
LOBBY_S = 60                        # lobby of a paid game: starts after this long with 2+ players, or when the host taps Start
MIN_POT_PLAYERS = 2
MAX_POT_PLAYERS = 8
FEE_PCT = 0                         # % of the pot kept by the house (0 = the winner takes everything)

MODES = {"rounds": ("🔢", "Rounds"), "race": ("🏁", "Race")}
SPEEDS = {"fast": ("⚡", "Fast", 0.6), "normal": ("⏱", "Normal", 1.0), "relaxed": ("🐢", "Relaxed", 1.6)}
THEME_LABELS = {"all": ("🎲", "Any words"), "animals": ("🐾", "Animals"), "food": ("🍕", "Food"),
                "countries": ("🌍", "Countries"), "nature": ("🌿", "Nature"), "home": ("🏠", "Home")}
HINT_AT = (0.5, 0.75)               # a letter is revealed after 50 % and after 75 % of the time (each hint costs 1 point)


@dataclass(frozen=True)
class Diff:
    key: str
    label: str
    icon: str
    lengths: tuple[int, ...]        # letters in the word (one of these, picked at random per round)
    time_s: int                     # seconds per word at Normal speed
    base: int                       # points for a correct answer (a speed bonus of 0-2 is added, hints subtract)

    @property
    def length_label(self) -> str:
        return "-".join(str(n) for n in dict.fromkeys((min(self.lengths), max(self.lengths))))


DIFFS: dict[str, Diff] = {
    "easy": Diff("easy", "Easy", "🟢", (4,), 30, 2),
    "medium": Diff("medium", "Medium", "🟡", (5,), 40, 3),
    "hard": Diff("hard", "Hard", "🔴", (6,), 55, 5),
    "extreme": Diff("extreme", "Extreme", "🟣", (7, 8), 75, 8),
}
CHOICES = ("easy", "medium", "hard", "extreme", "mixed")        # what the setup panel offers ("mixed" is not a real Diff)


def diff(key: str) -> Diff:
    """Settings of a difficulty key; 'mixed' (and anything unknown) shows the default one."""
    return DIFFS.get(key, DIFFS[DEFAULT_DIFF])


def round_diff(key: str, i: int, total: int | None = None, race: bool = False) -> str:
    """The real difficulty of round `i` (1-based). 'mixed' climbs easy -> medium -> hard."""
    if key != "mixed":
        return key if key in DIFFS else DEFAULT_DIFF
    if race or not total:
        return "easy" if i <= 3 else "medium" if i <= 7 else "hard"
    return "easy" if i <= total / 3 else "medium" if i <= total * 2 / 3 else "hard"


def time_for(diff_key: str, speed: str = DEFAULT_SPEED) -> int:
    mult = SPEEDS.get(speed, SPEEDS[DEFAULT_SPEED])[2]
    return max(10, round(diff(diff_key).time_s * mult))


def hint_times(time_s: int) -> tuple[int, ...]:
    """Seconds (from the start of the round) at which hint 1, 2 ... appear."""
    return tuple(int(time_s * f) for f in HINT_AT)


def is_guess(text: str, lengths) -> bool:
    """Does this chat message look like an answer (letters only, one of the possible lengths)?"""
    t = text.strip()
    return t.isascii() and t.isalpha() and len(t) in lengths


# ----------------------------------------------------------------------------- words
@lru_cache(maxsize=None)
def _pool(length: int, theme: str = "all") -> tuple[str, ...]:
    if theme != "all":
        return tuple(THEMES.get(theme, {}).get(length, ()))
    words = [w.lower() for w in ANSWERS.get(length, ()) if w.isalpha()] + list(EXTRA.get(length, ()))
    return tuple(dict.fromkeys(words))


@lru_cache(maxsize=None)
def _index(length: int) -> dict[str, frozenset]:
    """sorted letters -> every dictionary word made of exactly those letters."""
    idx: dict[str, set] = defaultdict(set)
    sources = [VALID.get(length, ()), ANSWERS.get(length, ()), EXTRA.get(length, ())] + [t.get(length, ()) for t in THEMES.values()]
    for src in sources:
        for w in src:
            w = w.lower()
            if w.isalpha():
                idx["".join(sorted(w))].add(w)
    return {k: frozenset(v) for k, v in idx.items()}


def anagrams(word: str) -> frozenset:
    """Every accepted answer for a puzzle word (the word itself and its real-word anagrams)."""
    word = word.lower()
    return _index(len(word)).get("".join(sorted(word)), frozenset()) | {word}


def pick_word(lengths, theme: str = "all", used=(), rng: random.Random | None = None) -> str:
    rng = rng or random.SystemRandom()
    for th in ((theme, "all") if theme != "all" else ("all",)):         # a theme without words of this length falls back to any words
        options = [n for n in lengths if _pool(n, th)]
        if options:
            words = _pool(rng.choice(options), th)
            fresh = [w for w in words if w not in used]
            return rng.choice(fresh or list(words))
    raise ValueError(f"no words of length {tuple(lengths)} in the dictionary")


def _fixed(word: str, out: str) -> int:
    """How many letters of the shuffle are still in their original position."""
    return sum(a == b for a, b in zip(word, out))


def scramble(word: str, rng: random.Random | None = None) -> str:
    """A shuffle of the word that is NOT the word itself, (when possible) not another real word, and (when possible) really mixed up:
    at most len//4 letters (at least 1) may stay where they were, so the word never "half shows" in the puzzle."""
    rng = rng or random.SystemRandom()
    real = anagrams(word)
    max_fixed = max(1, len(word) // 4)
    letters, fallback = list(word), ""
    for _ in range(200):
        rng.shuffle(letters)
        out = "".join(letters)
        if out == word:
            continue
        if out in real or _fixed(word, out) > max_fixed:
            fallback = fallback or out                       # usable, but only when nothing better turns up
            continue
        return out
    return fallback or word


@lru_cache(maxsize=None)
def _words(length: int) -> frozenset:
    return frozenset(w for ws in _index(length).values() for w in ws)


def is_word(text: str) -> bool:
    """Is this a real dictionary word (of any length the game knows)? Used to cheer on a close-but-wrong answer."""
    t = text.strip().lower()
    return t.isascii() and t.isalpha() and t in _words(len(t))


def new_puzzle(diff_key: str, theme: str = "all", used=(), rng: random.Random | None = None) -> tuple[str, str, frozenset]:
    """-> (word, scrambled, accepted answers). `diff_key` must be a real difficulty (use round_diff() first)."""
    word = pick_word(diff(diff_key).lengths, theme, used, rng)
    return word, scramble(word, rng), anagrams(word)


def points(diff_key: str, elapsed: float, time_s: int | None = None, hints: int = 0) -> int:
    """Base points + speed bonus (+2 in the first third of the time, +1 in the second third, +0 after) - 1 per hint shown; at least 1."""
    cfg = diff(diff_key)
    total = time_s or cfg.time_s
    frac = elapsed / total if total else 1.0
    bonus = 2 if frac <= 1 / 3 else 1 if frac <= 2 / 3 else 0
    return max(1, cfg.base + bonus - hints)


def show(letters: str) -> str:
    return "  ".join(letters.upper())


def hint(word: str, n: int = 1) -> str:
    """The first n letters shown, the rest hidden: 'C A _ _ _ _'."""
    n = max(0, min(n, len(word) - 1))
    return " ".join(list(word[:n].upper()) + ["_"] * (len(word) - n))
