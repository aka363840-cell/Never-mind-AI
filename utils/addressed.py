"""Decides whether a group message is talking to the bot: @username, reply to it, or its live NAME."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from aiogram.types import Message

from config import get_settings
from utils import brand

_PUNCT = re.compile(r"[\s,.:;!?\-–—~*_()\[\]{}\"'🦋❤️💖✨]+")


@lru_cache(maxsize=8)
def _compile(names: tuple[str, ...]) -> re.Pattern | None:
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alt})(?![A-Za-z0-9_])", re.IGNORECASE)


def _name_re() -> re.Pattern | None:
    """The bot's LIVE Telegram name + username (+ optional BOT_NAMES extras). Re-evaluated on every call, so a rename works at once."""
    return _compile(brand.aliases(get_settings().bot_names))


def addressed(m: Message, me) -> tuple[bool, str]:
    """(is addressed to the bot, text to send to the AI). Empty text => user only called her name."""
    text = m.text or ""
    hit = False
    for e in m.entities or []:
        if e.type == "mention" and text[e.offset:e.offset + e.length].lower() == f"@{me.username}".lower():
            hit = True
            text = text[:e.offset] + text[e.offset + e.length:]
        elif e.type == "text_mention" and e.user and e.user.id == me.id:
            hit = True
    rep = m.reply_to_message
    if rep and rep.from_user and rep.from_user.id == me.id:
        hit = True
    rx = _name_re()
    text = unicodedata.normalize("NFKC", text)        # fancy-font messages (𝑬𝒍𝒍𝒂) still match
    if rx and rx.search(text):
        hit = True
        if not _PUNCT.sub("", rx.sub("", text)):      # only her name was written ("<name> 🦋")
            text = ""
    return hit, text.strip()
