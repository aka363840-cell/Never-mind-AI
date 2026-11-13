"""Button colours + premium (custom) emoji icons, applied centrally to every outgoing inline keyboard.

* Colours use Telegram's button `style` (primary = blue, success = green, danger = red). Older Telegram apps simply show normal buttons.
* Premium icons use `icon_custom_emoji_id`. Telegram only shows them when the BOT OWNER has Telegram Premium. If Telegram refuses
  a keyboard with icons, the message is re-sent automatically without the icons, so nothing can break.
* Three emoji packs are built in by default (DEFAULT_PACKS, override with EMOJI_PACKS in .env). At startup the bot reads them with
  getStickerSet and maps every pack emoji -> its custom_emoji_id, so any button starting with e.g. 💎 / 💰 / ⚔️ gets the premium icon.
* /seticon <emoji> <custom_emoji_id> (stored in the database) still works and always wins over the packs.
* Premium logic: icons are ALWAYS attempted. If Telegram refuses them (owner has no Premium / it expired) the message is re-sent with
  normal emojis + coloured buttons, and the bot keeps using normal emojis for a few minutes, then tries premium again automatically.
  As soon as Premium is active again the premium icons come back by themselves - no restart, no command.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from keyboards import premium_text

log = logging.getLogger(__name__)
DEFAULT_PACKS = ("RestrictedEmoji", "sexypiyush69_79e604_by_SexyPiyush_BOT", "FinanceEmoji")   # earlier pack wins on a clash
ICONS: dict[str, str] = {}          # manual (/seticon): leading emoji of a button text -> custom_emoji_id  (highest priority)
PACK_ICONS: dict[str, str] = {}     # from the default emoji packs
_ORDERED: list[tuple[str, str]] = []  # merged (normalised emoji, id), longest emoji first
_PREFIX = "icon:"
_CACHE = Path(os.getenv("EMOJI_CACHE_FILE", "emoji_packs_cache.json"))
_RETRY_AFTER = 300                  # seconds to stay on normal emojis after Telegram refused premium icons
_refused_until = 0.0
_VS = "\ufe0f\ufe0e"              # variation selectors, ignored when matching emoji


def _norm(e: str) -> str:
    return "".join(c for c in e if c not in _VS)


def _rebuild() -> None:
    merged = {**{_norm(k): v for k, v in PACK_ICONS.items()}, **{_norm(k): v for k, v in ICONS.items()}}
    _ORDERED[:] = sorted(merged.items(), key=lambda kv: -len(kv[0]))
    premium_text.set_index(list(_ORDERED))


def _match_icon(text: str) -> tuple[str, str] | None:
    """If `text` starts with a known emoji -> (custom_emoji_id, remaining text)."""
    for key, cid in _ORDERED:
        i = 0
        for ch in key:
            while i < len(text) and text[i] in _VS:
                i += 1
            if i < len(text) and text[i] == ch:
                i += 1
            else:
                break
        else:
            while i < len(text) and text[i] in _VS:
                i += 1
            return cid, text[i:].strip()
    return None


def style_for(b: InlineKeyboardButton) -> str | None:
    data, text = (b.callback_data or ""), (b.text or "")
    low = text.lower()
    if (text.startswith(("✖️", "❌", "🧹")) or "cancel" in low or "decline" in low
            or data.endswith(":no") or ":dec:" in data or data.startswith("boss:") or data == "ai:clear"):
        return "danger"
    if (text.startswith("✅") or data.endswith(":ok") or data.startswith(("buyok:", "duel:acc:", "drop:", "ms:", "buy:", "hbuy:", "mr:ok"))
            or low.startswith(("buy ", "claim"))):
        return "success"
    if data.startswith(("nav:", "lb:", "inv:", "shop:", "eq:", "use:", "up:", "gs:", "duel:ch:", "gs:")) or data == "act":
        return "primary"
    if low.startswith(("➕", "🎁", "💰")) or "accept" in low or "join" in low:
        return "success"
    return "primary"          # every other button is blue, so no button is left uncoloured


def decorate(markup: InlineKeyboardMarkup, icons: bool = True) -> tuple[InlineKeyboardMarkup, bool]:
    """Returns (new markup, whether any premium icon was added). The original keyboard object is never modified."""
    used = False
    rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for b in row:
            upd: dict = {}
            if not getattr(b, "style", None):
                st = style_for(b)
                if st:
                    upd["style"] = st
            if icons and _ORDERED and not getattr(b, "icon_custom_emoji_id", None):
                hit = _match_icon(b.text or "")
                if hit:
                    cid, rest = hit
                    upd["icon_custom_emoji_id"] = cid
                    if rest:
                        upd["text"] = rest
                    used = True
            new_row.append(b.model_copy(update=upd) if upd else b)
        rows.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=rows), used


_TEXT_METHODS = {"SendMessage": "text", "EditMessageText": "text", "SendPhoto": "caption", "SendAnimation": "caption",
                 "SendVideo": "caption", "SendDocument": "caption", "EditMessageCaption": "caption"}
_text_refused_until = 0.0


def _premium_text_update(bot: Bot, method) -> dict:
    """Premium emojis inside the message text / caption. Returns the fields to change (empty if nothing to do)."""
    field = _TEXT_METHODS.get(type(method).__name__)
    if not field or not _ORDERED or time.monotonic() < _text_refused_until:
        return {}
    text = getattr(method, field, None)
    if not isinstance(text, str) or not text:
        return {}
    pm = getattr(method, "parse_mode", None)
    if isinstance(pm, Default):                          # aiogram's "use the bot default" marker
        pm = getattr(bot.default, "parse_mode", None)
    if pm is not None and str(getattr(pm, "value", pm)).upper() == "HTML":
        new, n = premium_text.to_html(text)
        return {field: new} if n else {}
    if pm is None:                                       # plain text (e.g. AI replies): use entities
        ents_field = "entities" if field == "text" else "caption_entities"
        if getattr(method, ents_field, None):
            return {}
        raw = premium_text.to_entities(text)
        if raw:
            from aiogram.types import MessageEntity
            return {ents_field: [MessageEntity(**e) for e in raw]}
    return {}


class ButtonStyleMiddleware(BaseRequestMiddleware):
    """Every outgoing message: premium emojis in the text + coloured buttons with premium icons. Falls back to plain on refusal."""

    async def __call__(self, make_request, bot: Bot, method):
        global _refused_until, _text_refused_until
        mk = getattr(method, "reply_markup", None)
        is_kb = isinstance(mk, InlineKeyboardMarkup)
        try:
            upd: dict = _premium_text_update(bot, method)
            used = False
            if is_kb:
                new, used = decorate(mk, icons=time.monotonic() >= _refused_until)
                upd["reply_markup"] = new
            if not upd:
                return await make_request(bot, method)
            m2 = method.model_copy(update=upd)
        except Exception:                                   # never let styling break a message
            log.exception("styling failed; sending the message as it is")
            return await make_request(bot, method)
        text_used = any(k in upd for k in ("text", "caption", "entities", "caption_entities"))
        try:
            res = await make_request(bot, m2)
            if used and _refused_until:
                _refused_until = 0.0
                log.info("premium button icons are working again")
            if text_used and _text_refused_until:
                _text_refused_until = 0.0
            return res
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower() or not (used or text_used):
                raise
            plain = {"reply_markup": decorate(mk, icons=False)[0]} if is_kb else {}
            res = await make_request(bot, method.model_copy(update=plain))   # raises its own error if it was not about premium
            if used:
                _refused_until = time.monotonic() + _RETRY_AFTER
            if text_used:
                _text_refused_until = time.monotonic() + _RETRY_AFTER
            log.warning("Telegram refused premium emojis (%s) -> normal emojis for %ss, then premium is retried", e, _RETRY_AFTER)
            return res


async def load_icons() -> None:
    from database.models import AppState
    from database.session import db
    async with db.tx() as s:
        rows = (await s.execute(select(AppState).where(AppState.key.like(_PREFIX + "%")))).scalars().all()
    ICONS.clear()
    ICONS.update({r.key[len(_PREFIX):]: r.value for r in rows})
    _rebuild()


def _read_cache() -> dict[str, dict[str, str]]:
    try:
        return json.loads(_CACHE.read_text("utf-8"))
    except Exception:
        return {}


async def load_packs(bot: Bot) -> None:
    """Reads the default emoji packs from Telegram (falls back to the last saved copy if a pack can't be fetched)."""
    names = tuple(n.strip().lstrip("@") for n in os.getenv("EMOJI_PACKS", "").replace(";", ",").split(",") if n.strip()) or DEFAULT_PACKS
    cache, fresh = _read_cache(), {}
    for name in names:
        mapping: dict[str, str] = {}
        try:
            pack = await bot.get_sticker_set(name)
            for st in pack.stickers:
                if st.custom_emoji_id and st.emoji and st.emoji not in mapping:
                    mapping[st.emoji] = st.custom_emoji_id
        except Exception as e:
            log.warning("emoji pack %s not loaded (%s)%s", name, e, " - using saved copy" if name in cache else "")
            mapping = cache.get(name, {})
        fresh[name] = mapping
    PACK_ICONS.clear()
    for name in names:                      # first pack in the list wins when two packs have the same emoji
        for emo, cid in fresh[name].items():
            PACK_ICONS.setdefault(emo, cid)
    _rebuild()
    if any(fresh.values()):
        try:
            _CACHE.write_text(json.dumps(fresh, ensure_ascii=False), "utf-8")
        except OSError:
            pass
    log.info("premium emoji packs ready: %s icons from %s", len(PACK_ICONS), ", ".join(names))


async def set_icon(emoji: str, cid: str | None) -> None:
    from database.models import AppState
    from database.session import db
    from services import users
    async with db.tx() as s:
        if cid is None:
            row = await s.get(AppState, _PREFIX + emoji)
            if row:
                await s.delete(row)
        else:
            await users.set_state(s, _PREFIX + emoji, cid)
    if cid is None:
        ICONS.pop(emoji, None)
    else:
        ICONS[emoji] = cid
    _rebuild()
