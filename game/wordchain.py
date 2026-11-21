"""Word Chain rules - pure Python (no Telegram, no database).

Players take turns saying a word that STARTS with the LAST letter of the previous word.
A word must be a real English word, long enough for the current level, and not used before in this game.
Whoever runs out of time is eliminated; the last player standing wins.

GAME MODES (same idea as the popular Telegram Word Chain bots):
  classic          the normal game - every 2 rounds the level rises (longer words, shorter clock)
  hard             starts at 5+ letters, a level every round, much shorter clock
  chosenfirst      every word of the game must START with one chosen letter (random, or /startcfl k)
  banned           a few letters are BANNED: no word may contain them
  required         every turn has a REQUIRED letter that the word must contain
  elimination      everybody plays every round, a word scores its length, the lowest score of the round is out

Word list: game/wordchain_words.txt.gz (flat English word list, generated from the open-source hunspell en_US
dictionary) plus the WordSeek answer/guess lists.
"""
from __future__ import annotations

import gzip
import random
import re
from dataclasses import dataclass
from pathlib import Path

LOBBY_S = 60                  # the lobby closes by itself after this long ...
LOBBY_EXTEND_S = 30           # ... /extend adds this much by default
LOBBY_MAX_S = 300             # ... but a lobby never lives longer than this in total
MIN_PLAYERS, MAX_PLAYERS = 2, 30
TIME_SCALE = 1.0              # tests set this to a tiny number to play a whole game in a blink
START_LETTERS = "abcdefghilmnoprstw"          # letters with plenty of words, so the first turn is always fair
ALPHABET = "abcdefghijklmnopqrstuvwxyz"
BANNED_POOL = "bcdfghjkmpvwxyzq"               # letters that can be banned (never the ones every chain needs)
REQUIRED_POOL = "aeiorstnlcdumphgbyk"          # letters that can be required
_CANDIDATE = re.compile(r"[A-Za-z]{1,45}")
_FILE = Path(__file__).with_name("wordchain_words.txt.gz")

# kept for old imports / tests that read the classic numbers
ROUNDS_PER_LEVEL = 2
MIN_LEN_START, MIN_LEN_CAP = 3, 7
TIME_START, TIME_STEP, TIME_FLOOR = 45, 5, 10


@dataclass(frozen=True)
class Mode:
    key: str
    name: str
    emoji: str
    blurb: str                    # one line, shown in the lobby and /wcmodes
    commands: tuple[str, ...]     # the command that opens a lobby in this mode (first one is the main name)
    min_start: int = 3
    min_cap: int = 7
    t_start: int = 45
    t_step: int = 5
    t_floor: int = 10
    per_level: int = 2            # rounds per level
    fixed_first: bool = False     # every word starts with the same letter
    banned: bool = False          # some letters are banned for the whole game
    required: bool = False        # every turn has a letter the word must contain
    elimination: bool = False     # lowest score of each round is out (instead of 'timeout = out')


MODES: dict[str, Mode] = {m.key: m for m in (
    Mode("classic", "Classic", "⛓", "Say a word starting with the last letter of the previous word. Words get longer, the clock gets shorter.",
         ("startclassic", "wordchain", "wc")),
    Mode("hard", "Hard", "🔥", "Starts at 5+ letters, a new level EVERY round and a much shorter clock.",
         ("starthard", "wchard"), min_start=5, min_cap=9, t_start=30, t_step=4, t_floor=8, per_level=1),
    Mode("chosenfirst", "Chosen first letter", "🔤", "ONE letter is chosen - every word of the game must start with it.",
         ("startchosenfirstletter", "startcfl"), min_cap=6, t_floor=15, fixed_first=True),
    Mode("banned", "Banned letters", "🚫", "A few letters are banned - no word may contain them.",
         ("startbannedletters", "startbl"), min_cap=6, t_start=40, t_floor=15, banned=True),
    Mode("required", "Required letter", "📌", "Every turn has a required letter - the word must contain it.",
         ("startrequiredletter", "startrl"), min_cap=6, t_floor=20, required=True),
    Mode("elimination", "Elimination", "💀", "Everyone plays each round, a word scores its length - the lowest score of the round is out.",
         ("startelimination", "startelim"), t_start=30, t_step=3, t_floor=15, per_level=1, elimination=True),
)}
DEFAULT_MODE = "classic"
START_COMMANDS: dict[str, str] = {c: m.key for m in MODES.values() for c in m.commands}      # command -> mode key


@dataclass(frozen=True)
class Level:
    n: int
    min_len: int
    seconds: float


def level_for(round_no: int, mode: Mode | str = DEFAULT_MODE) -> Level:
    """Level of a given round (rounds start at 1) in a given mode."""
    m = MODES[mode] if isinstance(mode, str) else mode
    n = (max(1, round_no) - 1) // m.per_level + 1
    secs = max(m.t_floor, m.t_start - m.t_step * (n - 1)) * TIME_SCALE
    return Level(n, min(m.min_start + n - 1, m.min_cap), secs)


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


def _ok(w: str, banned: frozenset[str] | set[str], required: str) -> bool:
    if required and required not in w:
        return False
    return not banned or not any(c in w for c in banned)


def has_word(letter: str, min_len: int, used: set[str], banned: frozenset[str] | set[str] = frozenset(), required: str = "") -> bool:
    """Is there still an unused word that starts with `letter`, has at least `min_len` letters,
    contains none of the banned letters and (if given) contains the required letter?"""
    letter = letter.lower()
    if letter in banned:
        return False
    for w in _index().get(letter, ()):
        if len(w) < min_len:
            return False
        if w not in used and _ok(w, banned, required):
            return True
    return False


def check(word: str, letter: str, min_len: int, used: set[str], banned: frozenset[str] | set[str] = frozenset(), required: str = "") -> str | None:
    """None if the move is fine, else the reason (a short sentence for the player)."""
    w = word.strip().lower()
    if not w.startswith(letter.lower()):
        return f"The word must start with <b>{letter.upper()}</b>."
    if required and required not in w:
        return f"The word must contain the letter <b>{required.upper()}</b>."
    bad = sorted({c for c in banned if c in w})
    if bad:
        return "The word can't contain " + ", ".join(f"<b>{c.upper()}</b>" for c in bad) + " (banned)."
    if len(w) < min_len:
        return f"Too short - at least <b>{min_len}</b> letters at this level."
    if w in used:
        return f"<code>{w.upper()}</code> was already used in this game."
    if w not in words():
        return f"<code>{w.upper()}</code> isn't in my dictionary."
    return None


def pick_letter(min_len: int, used: set[str], rng: random.Random | None = None, avoid: str = "",
                banned: frozenset[str] | set[str] = frozenset(), required: str = "") -> str:
    """A random start letter that still has words (used after a dead end and for the very first turn)."""
    r = rng or random
    pool = [c for c in START_LETTERS if c != avoid and has_word(c, min_len, used, banned, required)] \
        or [c for c in ALPHABET if has_word(c, min_len, used, banned, required)]
    return r.choice(pool) if pool else r.choice([c for c in START_LETTERS if c not in banned] or list(START_LETTERS))


def pick_banned(count: int, rng: random.Random | None = None) -> frozenset[str]:
    """`count` banned letters for the 'banned letters' mode."""
    r = rng or random
    return frozenset(r.sample(BANNED_POOL, max(1, min(count, 6))))


def banned_count(players: int) -> int:
    """More players = more banned letters (2-4), so a big game stays interesting but never impossible."""
    return min(4, 2 + players // 6)


def pick_required(letter: str, min_len: int, used: set[str], banned: frozenset[str] | set[str] = frozenset(),
                  rng: random.Random | None = None) -> str:
    """A required letter that at least one unused word starting with `letter` really contains ('' if none)."""
    r = rng or random
    pool = [c for c in REQUIRED_POOL if c != letter and c not in banned and has_word(letter, min_len, used, banned, c)]
    return r.choice(pool) if pool else ""


FIXED_MIN_WORDS = 600          # 'chosen first letter' needs a letter with at least this many 6+ letter words (J K U V yes, Q X Y Z no)


def fixed_letters() -> str:
    """Letters that can carry a whole 'chosen first letter' game."""
    return "".join(c for c in ALPHABET if sum(1 for w in _index().get(c, ()) if len(w) >= 6) >= FIXED_MIN_WORDS)


def points(word: str) -> int:
    """Elimination mode: a word is worth its length."""
    return len(word)
