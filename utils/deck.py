"""Shuffle-bag picker: every item is shown once before any item repeats, and the first item after a reshuffle
is never the one that was just shown. Kept per (kind, chat) so one busy group can't use up another group's variety.
In-memory only -- after a restart the bags simply start fresh (nothing important is stored)."""
from __future__ import annotations

import random
from collections import OrderedDict
from typing import Sequence, TypeVar

T = TypeVar("T")
_rng = random.SystemRandom()
_MAX_BAGS = 4000
_bags: "OrderedDict[tuple[str, int], tuple[int, list[int], int | None]]" = OrderedDict()   # key -> (size, remaining, last)


def chat_of(msg) -> int:
    """Chat id of a message-like object (0 if it has none, e.g. in tests)."""
    return int(getattr(getattr(msg, "chat", None), "id", 0) or 0)


def draw(kind: str, n: int, chat_id: int = 0) -> int:
    """Index in range(n), never repeating until all n were used."""
    if n <= 0:
        raise ValueError("empty deck")
    key = (kind, chat_id)
    size, bag, last = _bags.pop(key, (n, [], None))
    if size != n or not bag:                       # first use, list size changed, or bag is empty -> reshuffle
        bag = list(range(n))
        _rng.shuffle(bag)
        if n > 1 and last is not None and bag[-1] == last:
            bag[0], bag[-1] = bag[-1], bag[0]      # bag.pop() takes the end: make sure it isn't the last shown
    idx = bag.pop()
    _bags[key] = (n, bag, idx)
    if len(_bags) > _MAX_BAGS:
        _bags.popitem(last=False)                  # forget the least recently used chat
    return idx


def pick(kind: str, items: Sequence[T], chat_id: int = 0) -> T:
    return items[draw(kind, len(items), chat_id)]
