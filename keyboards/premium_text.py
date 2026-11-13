"""Turns normal emojis inside outgoing message text into Telegram premium (custom) emojis.

Pure text logic (no Telegram imports): keyboards/style.py feeds it the emoji -> custom_emoji_id table and calls it
from the request middleware. HTML messages get <tg-emoji emoji-id="..."> tags, plain-text messages get custom_emoji
entities. Emojis inside <code>/<pre> blocks and existing <tg-emoji> tags are left alone.
"""
from __future__ import annotations

import re

MAX_PER_MESSAGE = 40
_VS = "\ufe0f\ufe0e"
_TAG = re.compile(r"(<[^>]*>)")
_NAME = re.compile(r"^<\s*(/?)\s*([a-zA-Z0-9-]+)")
_SKIP = {"code", "pre", "tg-emoji"}
_BY_FIRST: dict[str, list[tuple[str, str]]] = {}


def set_index(pairs: list[tuple[str, str]]) -> None:
    """pairs = [(emoji without variation selectors, custom_emoji_id)], longest emoji first."""
    _BY_FIRST.clear()
    for key, cid in pairs:
        if key:
            _BY_FIRST.setdefault(key[0], []).append((key, cid))


def _match(text: str, i: int) -> tuple[str, int] | None:
    """Emoji starting at text[i] -> (custom_emoji_id, index after it)."""
    for key, cid in _BY_FIRST.get(text[i], ()):
        j = i
        for ch in key:
            while j < len(text) and text[j] in _VS:
                j += 1
            if j < len(text) and text[j] == ch:
                j += 1
            else:
                break
        else:
            while j < len(text) and text[j] in _VS:
                j += 1
            return cid, j
    return None


def to_html(text: str) -> tuple[str, int]:
    """-> (text with <tg-emoji> tags, number of premium emojis used)."""
    if not _BY_FIRST or not text:
        return text, 0
    out: list[str] = []
    skip, used = 0, 0
    for part in _TAG.split(text):
        if part.startswith("<") and part.endswith(">"):
            m = _NAME.match(part)
            if m and m.group(2).lower() in _SKIP and not part.endswith("/>"):
                skip += -1 if m.group(1) else 1
                skip = max(skip, 0)
            out.append(part)
            continue
        if skip or used >= MAX_PER_MESSAGE:
            out.append(part)
            continue
        i, buf = 0, []
        while i < len(part):
            hit = _match(part, i) if ord(part[i]) > 0x2000 and used < MAX_PER_MESSAGE else None
            if hit:
                cid, j = hit
                buf.append(f'<tg-emoji emoji-id="{cid}">{part[i:j]}</tg-emoji>')
                used += 1
                i = j
            else:
                buf.append(part[i])
                i += 1
        out.append("".join(buf))
    return "".join(out), used


def to_entities(text: str) -> list[dict]:
    """Plain-text messages: custom_emoji entities with UTF-16 offsets."""
    if not _BY_FIRST or not text:
        return []
    ents, i, off = [], 0, 0
    while i < len(text) and len(ents) < MAX_PER_MESSAGE:
        hit = _match(text, i) if ord(text[i]) > 0x2000 else None
        if hit:
            cid, j = hit
            length = len(text[i:j].encode("utf-16-le")) // 2
            ents.append({"type": "custom_emoji", "offset": off, "length": length, "custom_emoji_id": cid})
            off += length
            i = j
        else:
            off += len(text[i].encode("utf-16-le")) // 2
            i += 1
    return ents
