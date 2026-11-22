"""Premium (custom) emoji + coloured buttons for the group cards the Ella server posts itself (same idea as keyboards/style.py in the bot).
Needs the bot owner to have Telegram Premium; if Telegram refuses, callers re-send the plain version."""
from __future__ import annotations

import logging
import os
import re
import time

log = logging.getLogger("ella.tgstyle")
DEFAULT_PACKS = ("RestrictedEmoji", "sexypiyush69_79e604_by_SexyPiyush_BOT", "FinanceEmoji", "Udif7rr7_by_fStikBot")
_VS = "️︎"
_IDX: dict[str, list[tuple[str, str]]] = {}
_state = {"loaded": 0.0, "refused": 0.0}
_TAG = re.compile(r"(<[^>]*>)")


def _norm(e):
    return "".join(c for c in e if c not in _VS)


async def load(token, tg):
    """Reads the emoji packs once an hour (tg = async Telegram API caller)."""
    if not token or time.time() - _state["loaded"] < 3600:
        return
    _state["loaded"] = time.time()
    names = [n.strip().lstrip("@") for n in os.getenv("EMOJI_PACKS", "").replace(";", ",").split(",") if n.strip()] or list(DEFAULT_PACKS)
    mp: dict[str, str] = {}
    for n in names:
        try:
            for st in (await tg(token, "getStickerSet", name=n))["result"]["stickers"]:
                if st.get("custom_emoji_id") and st.get("emoji"):
                    mp.setdefault(_norm(st["emoji"]), st["custom_emoji_id"])
        except Exception as ex:
            log.warning("emoji pack %s not loaded: %s", n, ex)
    _IDX.clear()
    for k, v in sorted(mp.items(), key=lambda kv: -len(kv[0])):
        _IDX.setdefault(k[0], []).append((k, v))


def _match(t, i):
    for key, cid in _IDX.get(t[i], ()):
        j = i
        for ch in key:
            while j < len(t) and t[j] in _VS:
                j += 1
            if j < len(t) and t[j] == ch:
                j += 1
            else:
                break
        else:
            while j < len(t) and t[j] in _VS:
                j += 1
            return cid, j
    return None


def html_text(text):
    """Normal emojis -> <tg-emoji> tags (tags and code are left alone)."""
    if not _IDX or time.time() < _state["refused"]:
        return text
    out = []
    for part in _TAG.split(text):
        if part.startswith("<"):
            out.append(part)
            continue
        i, buf = 0, []
        while i < len(part):
            hit = _match(part, i) if ord(part[i]) > 0x2000 else None
            if hit:
                buf.append(f'<tg-emoji emoji-id="{hit[0]}">{part[i:hit[1]]}</tg-emoji>')
                i = hit[1]
            else:
                buf.append(part[i])
                i += 1
        out.append("".join(buf))
    return "".join(out)


def markup(mk, styles=None):
    """Coloured buttons + a premium icon from the leading emoji of each label. styles = {button text start: success|primary|danger}."""
    if not mk:
        return mk
    rows = []
    for row in mk["inline_keyboard"]:
        new = []
        for b in row:
            b = dict(b)
            b["style"] = next((s for k, s in (styles or {}).items() if b["text"].startswith(k)), "primary")
            if _IDX and time.time() >= _state["refused"] and b["text"] and ord(b["text"][0]) > 0x2000:
                hit = _match(b["text"], 0)
                if hit:
                    b["icon_custom_emoji_id"], b["text"] = hit[0], b["text"][hit[1]:].strip() or b["text"]
            new.append(b)
        rows.append(new)
    return {"inline_keyboard": rows}


def refused():
    _state["refused"] = time.time() + 300
