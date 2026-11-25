"""Maps GameZipper's own category + tags onto Ella's 12 categories.

GameZipper's feed carries ONE category per game (values seen in the feed: puzzle, arcade, skill, idle, card, board,
casual, sports, racing, word, simulation) plus free-text tags. Ella's categories are broader, so:
  * the provider category gives the primary Ella category (direct or via PRIMARY_MAP);
  * tags/name can ADD secondary categories (action, adventure, strategy, multiplayer) by whole-word keywords.
A game with an unknown provider category falls back to Casual, so nothing is silently dropped.
Which Ella categories end up with how many games depends on the live feed - /api/admin/report shows the real numbers."""
from __future__ import annotations

import re

CATEGORIES: list[dict] = [
    {"id": "arcade", "name": "Arcade", "icon": "arcade"},
    {"id": "puzzle", "name": "Puzzle", "icon": "puzzle"},
    {"id": "racing", "name": "Racing", "icon": "racing"},
    {"id": "sports", "name": "Sports", "icon": "sports"},
    {"id": "action", "name": "Action", "icon": "action"},
    {"id": "adventure", "name": "Adventure", "icon": "adventure"},
    {"id": "strategy", "name": "Strategy", "icon": "strategy"},
    {"id": "board", "name": "Board Games", "icon": "board"},
    {"id": "casual", "name": "Casual", "icon": "casual"},
    {"id": "multiplayer", "name": "Multiplayer", "icon": "multiplayer"},
    {"id": "card", "name": "Card", "icon": "card"},
    {"id": "word", "name": "Word", "icon": "word"},
]
IDS = [c["id"] for c in CATEGORIES]

PRIMARY_MAP = {
    "arcade": "arcade", "skill": "arcade", "puzzle": "puzzle", "racing": "racing", "sports": "sports", "sport": "sports",
    "action": "action", "adventure": "adventure", "strategy": "strategy", "board": "board", "casual": "casual",
    "idle": "casual", "simulation": "casual", "multiplayer": "multiplayer", "card": "card", "word": "word",
    # GamePix categories
    "cards": "card", "driving": "racing", "shooting": "action", "shooter": "action", "fighting": "action", "rpg": "adventure",
    "io": "multiplayer", "mahjong": "board", "bubble shooter": "puzzle", "match 3": "puzzle", "hidden object": "puzzle",
    "quiz": "word", "trivia": "word", "football": "sports", "soccer": "sports", "basketball": "sports", "defense": "strategy",
}

# whole-word / phrase keywords found in tags or the name
EXTRA = {
    "action": ("shooter", "shoot", "fighting", "fighter", "zombie", "zombies", "ninja", "battle", "brawl", "survival", "platformer", "beat em up"),
    "adventure": ("adventure", "rpg", "dungeon", "explore", "exploration", "escape room", "roguelike", "roguelite"),
    "strategy": ("strategy", "tower defense", "tactics", "tactical", "management", "tycoon", "turn based", "turn-based", "city builder"),
    "multiplayer": ("multiplayer", "2 player", "two player", "2-player", "pvp", "versus", "co-op", "coop", "online multiplayer"),
}
_RX = {cat: re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(k) for k in kws) + r")(?![a-z0-9])") for cat, kws in EXTRA.items()}


def classify(provider_category: str, tags: list[str], name: str = "") -> list[str]:
    """Primary category first, then any extra ones. Always returns at least one id."""
    primary = PRIMARY_MAP.get((provider_category or "").strip().lower(), "casual")
    out = [primary]
    hay = " | ".join([t.lower() for t in tags] + [name.lower()])
    for cat, rx in _RX.items():
        if cat != primary and rx.search(hay):
            out.append(cat)
    return out
