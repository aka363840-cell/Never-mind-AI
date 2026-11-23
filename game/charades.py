"""Charades rules - pure Python (no Telegram, no database).
The host gets a secret word in DM and describes it in the group (emojis, clues, text - never the word itself).
Everybody else guesses by typing. First correct guess wins the round; then anybody can tap a button to become the next host."""
from __future__ import annotations

import random
import re

ROUND_S = 180                  # one round lasts this long
HOST_WAIT_S = 120              # after a round, how long the "become the host" button waits before the game ends
DM_WAIT_S = 120                # how long a new host has to open the bot in DM (so the word can be sent)
MAX_CHANGES = 3                # "change word" presses per round
GUESSER_POINTS, HOST_POINTS = 2, 1

WORDS = """
elephant giraffe penguin kangaroo dolphin butterfly spider monkey crocodile octopus snake rabbit tiger lion bear camel horse owl peacock
scorpion turtle shark whale frog bat parrot cow dog cat mouse chicken duck
pizza burger sandwich pancake noodles popcorn chocolate banana mango watermelon pineapple coffee tea ice-cream cake biscuit egg
umbrella mirror candle ladder scissors toothbrush bicycle helicopter train airplane rocket submarine boat truck bus camera telephone
television laptop guitar piano drum violin flute trumpet clock watch glasses key lock door window bed pillow blanket chair table
swimming running dancing singing cooking sleeping laughing crying jumping climbing fishing painting writing reading driving shopping
sneezing yawning cycling boxing skating surfing sweeping brushing
doctor teacher police farmer pilot chef singer soldier magician clown king queen robot ghost vampire zombie dragon mermaid superhero
cricket football basketball tennis chess carrom badminton volleyball hockey kabaddi
beach mountain desert forest island volcano waterfall rainbow thunder snowman moon sun star rain
birthday wedding school hospital library market temple airport castle bridge tower
mobile charger selfie wifi password emoji video-game remote-control
pillow-fight hide-and-seek tug-of-war treasure-hunt
""".split()
from utils.content.pack2 import CHARADES as _MORE_WORDS  # noqa: E402

WORDS = tuple(dict.fromkeys(w.replace("-", " ") for w in [*WORDS, *_MORE_WORDS]))

_CLEAN = re.compile(r"[^a-z0-9]+")


def norm(text: str) -> str:
    """lowercase, letters and digits only, single spaces"""
    return _CLEAN.sub(" ", (text or "").lower()).strip()


def matches(text: str, word: str) -> bool:
    """Does this chat message contain the secret word (as a whole word / phrase, a plural 's' is fine)?"""
    t, w = f" {norm(text)} ", norm(word)
    if not w:
        return False
    return f" {w} " in t or f" {w}s " in t or f" {w}es " in t


def pick(avoid: set[str] | None = None, rng: random.Random | None = None) -> str:
    pool = [w for w in WORDS if not avoid or w not in avoid] or list(WORDS)
    return (rng or random).choice(pool)
