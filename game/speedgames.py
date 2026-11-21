"""Pure rules for the two group speed games (no Telegram, no database -> trivially testable).

⚡ Fastest Tap  (/tap <amount>)   - decoy buttons, one turns green: the first to tap the green one wins the round.
💥 Crash Count  (/crash <amount>) - a word drawn as a picture + a question (letters / vowels / ...): first correct number wins the round.
Both are best-of-3 matches (first to 2 round wins, at most MAX_ROUNDS rounds); `free` = practice, no diamonds, can be played alone.

Money rule: every player's entry is HELD (escrow, ledgered) when the match starts and settled once at the end:
  winner  -> gets every other player's entry minus FEE_PCT (burned) and his own entry back
  nobody / /end / a crash -> every entry is refunded
  a winner/loser pair that already played PAIR_DAILY paid games in 24 h -> that loser's entry is refunded (anti alt-farming)
"""
from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass

from game.rules import R
from utils.content.speedgames import CRASH_WORDS
from utils.fmt import parse_amount

MIN_PLAYERS = 2
MAX_PLAYERS = 10
LOBBY_S = 30                    # the lobby closes by itself after this long (host can start earlier with the button)
START_CD_S = 20                 # per player: at most one new lobby every 20 s (anti spam)
STALE_S = 420                   # a game older than this is considered dead, swept away and its entries refunded
FEE_PCT = 0.05                  # share of every payment that is burned
MIN_STAKE = R.casino_min_bet
MAX_STAKE = R.casino_max_bet
PRACTICE_WORDS = frozenset({"free", "practice", "demo", "0"})

WINS_TO_TAKE = 2                # best of 3: first to 2 round wins
MAX_ROUNDS = 5                  # rounds nobody wins also count; after this the leader wins (a tie = everybody is refunded)
BETWEEN_S = 3                   # pause between rounds
MATCH_INTRO_S = 1.5             # pause after the match intro (pot, goal) before round 1
PAIR_DAILY = 5                  # paid matches per winner/loser pair and 24 h; further ones refund the loser's entry
RECOVER_AFTER_S = 600           # entries still held this long after the match began belong to a crashed bot -> refunded
RECOVER_EVERY_S = 600           # how often (at most) the recovery sweep runs, lazily when a lobby is opened
REMATCH_TTL_S = 600             # the rematch button works this long after a match

TAP_DELAY = (2.0, 6.0)          # seconds of red "wait" before one button turns green
TAP_TIMEOUT_S = 12              # nobody tapped within this time -> round without winner
TAP_BUTTONS = 4                 # 1 green + 3 red decoys (2x2); the green one moves around every round
CRASH_INTRO_S = 3               # "get ready" before the picture appears (answers before that are ignored)
CRASH_TIMEOUT_S = 20            # nobody solved it -> round without winner

_NUM = re.compile(r"^\s*(\d{1,3})\s*$")
VOWELS = "AEIOU"


# ------------------------------------------------------------------ entry / money
def stake_problem(amount: int | None) -> str | None:
    """None when the entry is fine (0 = practice), else a user-facing (HTML) message."""
    if not amount:
        return None
    if amount < MIN_STAKE:
        return f"❌ The minimum entry is <b>{MIN_STAKE:,}</b> 💎."
    if amount > MAX_STAKE:
        return f"❌ The maximum entry is <b>{MAX_STAKE:,}</b> 💎."
    return None


def parse_stake(arg: str | None, wallet: int) -> int | None:
    """'100', '2k', 'all' (= your wallet, capped at MAX_STAKE) -> int;  'free' / 'practice' / '0' -> 0 (no diamonds);  anything else -> None."""
    if not arg:
        return None
    if arg.strip().lower() in PRACTICE_WORDS:
        return 0
    return parse_amount(arg, min(max(0, wallet), MAX_STAKE))


def split_pay(pay: int) -> tuple[int, int]:
    """A loser pays `pay`: returns (net to the winner, burned). net + burned == pay always."""
    fee = int(pay * FEE_PCT)
    return pay - fee, fee


# ------------------------------------------------------------------ match flow
def wins_needed(players: int, practice: bool) -> int:
    """Best of 3, except a solo practice run which is one single round."""
    return 1 if practice and players <= 1 else WINS_TO_TAKE


def max_rounds(wins: int) -> int:
    return 1 if wins <= 1 else MAX_ROUNDS


def match_state(scores: dict[int, int], wins: int, round_no: int, rounds_cap: int) -> tuple[str, int | None]:
    """After a round: ('winner', uid) | ('continue', None) | ('draw', None)."""
    top = max(scores.values(), default=0)
    leaders = [u for u, v in scores.items() if v == top]
    if top >= wins and len(leaders) == 1:
        return "winner", leaders[0]
    if round_no < rounds_cap:
        return "continue", None
    return ("winner", leaders[0]) if top > 0 and len(leaders) == 1 else ("draw", None)


# ------------------------------------------------------------------ ⚡ tap
def tap_delay(rng: random.Random | None = None) -> float:
    return (rng or random).uniform(*TAP_DELAY)


def tap_green(rng: random.Random | None = None) -> int:
    """Which of the TAP_BUTTONS buttons is the green one this round."""
    return (rng or random).randrange(TAP_BUTTONS)


def reaction(seconds: float) -> str:
    return f"≈{max(0.0, seconds):.2f}s"


# ------------------------------------------------------------------ 💥 crash
@dataclass(frozen=True)
class Challenge:
    kind: str           # letters | vowels | consonants | letter
    word: str
    answer: int
    question: str       # HTML
    letter: str = ""


def letter_count(word: str) -> int:
    return sum(1 for ch in word if ch.isalpha())


def count_for(kind: str, word: str, letter: str = "") -> int:
    w = word.upper()
    if kind == "letters":
        return letter_count(w)
    if kind == "vowels":
        return sum(1 for ch in w if ch in VOWELS)
    if kind == "consonants":
        return sum(1 for ch in w if ch.isalpha() and ch not in VOWELS)
    if kind == "letter":
        return w.count(letter.upper())
    raise ValueError(kind)


def _question(kind: str, letter: str) -> str:
    return {"letters": "How many <b>letters</b> does the word have?",
            "vowels": "How many <b>vowels</b> (A E I O U) does it have?",
            "consonants": "How many <b>consonants</b> does it have?",
            "letter": f"How many times does the letter <b>{letter}</b> appear?"}[kind]


_PLAN = {1: ("letters", (6, 8)), 2: ("vowels", (8, 10)), 3: ("letter", (8, 12))}      # rising difficulty; later (tie-break) rounds are random + long


def make_challenge(round_no: int, rng: random.Random | None = None, avoid: set[str] | frozenset[str] = frozenset()) -> Challenge:
    rng = rng or random
    kind, (lo, hi) = _PLAN.get(round_no) or (rng.choice(("vowels", "consonants", "letter")), (9, 12))
    pool = [w for w in CRASH_WORDS if lo <= len(w) <= hi and w not in avoid] or [w for w in CRASH_WORDS if w not in avoid] or list(CRASH_WORDS)
    word = rng.choice(pool)
    letter = ""
    if kind == "letter":
        counts = Counter(word)
        many = sorted(c for c, n in counts.items() if n >= 2)
        letter = rng.choice(many or sorted(counts))
    return Challenge(kind, word, count_for(kind, word, letter), _question(kind, letter), letter)


def parse_number(text: str | None) -> int | None:
    """A plain 1-3 digit number typed in the chat ('7', ' 12 '), else None. Anything else is ordinary chatter."""
    m = _NUM.match(text or "")
    return int(m.group(1)) if m else None


def pick_word(rng: random.Random | None = None, avoid: str = "") -> str:
    pool = [w for w in CRASH_WORDS if w != avoid]
    return (rng or random).choice(pool)
