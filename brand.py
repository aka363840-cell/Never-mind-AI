"""The bot's display name, taken LIVE from Telegram (BotFather name).

Rename the bot in BotFather and every screen, the AI persona, the '/' menu descriptions and the
name-triggers in groups follow automatically within a few minutes (or at the next restart).
Nothing in the code hard-codes the name any more.
"""
from __future__ import annotations

import os
import re
import unicodedata

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

_state = {"name": os.getenv("BOT_DISPLAY_NAME", "").strip() or "Bot", "username": "", "id": 0}
EMOJI = os.getenv("BRAND_EMOJI", "🦋")

_UP, _LO = 0x1D608, 0x1D622          # 𝘈 / 𝘢 (sans-serif italic)


def fancy(text: str) -> str:
    """A-Z a-z -> sans-serif italic; every other script (Hindi, digits, emoji) is left untouched."""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(_UP + ord(ch) - 65))
        elif "a" <= ch <= "z":
            out.append(chr(_LO + ord(ch) - 97))
        else:
            out.append(ch)
    return "".join(out)


def update(me) -> bool:
    """Feed it the result of bot.get_me(). Returns True when the name changed."""
    new = (getattr(me, "first_name", "") or "").strip() or _state["name"]
    changed = new != _state["name"]
    _state.update(name=new, username=getattr(me, "username", "") or "", id=getattr(me, "id", 0) or 0)
    return changed


def name() -> str:
    return _state["name"]


def plain(text: str) -> str:
    """'𝑬𝒍𝒍𝒂 ♡' -> 'Ella': fancy Unicode letters folded to normal ones, decorations (♡ ✦ 🦋 ...) dropped."""
    t = unicodedata.normalize("NFKC", text or "")
    t = "".join(ch if (ch.isalnum() or ch.isspace() or ch in "-_'") else " " for ch in t)
    return re.sub(r"\s+", " ", t).strip()


def plain_name() -> str:
    return plain(_state["name"]) or _state["name"]


def username() -> str:
    return _state["username"]


def link(param: str = "") -> str:
    base = f"https://t.me/{_state['username']}"
    return f"{base}?{param}" if param else base


def possessive() -> str:
    n = _state["name"]
    return f"{n}'" if n.endswith("s") else f"{n}'s"


def line() -> str:
    """Header used on every screen: ◈ 𝘕𝘢𝘮𝘦🦋"""
    from utils.fmt import esc
    return f"◈ <b>{esc(fancy(_state['name']))}{EMOJI}</b>"


def aliases(extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Every spelling that should make the bot answer in a group: the live name, its words, the username + configured extras."""
    pn = plain_name()
    names = [_state["name"], pn, _state["username"], *extra]
    first = pn.split()[0] if pn.split() else ""
    if len(first) >= 3:
        names.append(first)
    seen, out = set(), []
    for n in names:
        n = (n or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return tuple(out)
