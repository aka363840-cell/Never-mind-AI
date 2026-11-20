"""Welcome / goodbye rendering and sending (custom text, {placeholders}, buttons)."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup

from . import store
from .perms import tg
from .util import esc, extract_buttons, mention, render

log = logging.getLogger("moderation.greet")
HELP_VARS = "{first} {last} {fullname} {username} {mention} {id} {chatname} {count}"
_last_welcome: dict[int, int] = {}


def build(text: str, user, chat_title: str, chat_id: int, count: int | None):
    """-> (html text, keyboard | None). Every substituted value is HTML-escaped; the admin's own text keeps its formatting."""
    body, rows = extract_buttons(text)
    uname = f"@{user.username}" if getattr(user, "username", None) else esc(user.first_name or "")
    vals = dict(first=esc(user.first_name or ""), last=esc(user.last_name or ""), fullname=esc(user.full_name), username=uname,
                mention=mention(user.id, user.full_name), id=str(user.id), chatname=esc(chat_title or ""), count=str(count or "?"))
    kb = InlineKeyboardMarkup(inline_keyboard=[[B(text=t, url=u) for t, u in row] for row in rows]) if rows else None
    return render(body, **vals), kb


async def send(bot: Bot, chat, user, kind: str = "welcome") -> bool:
    """Send the group's custom welcome / goodbye if enabled. Returns True if something was sent. Never raises."""
    try:
        try:                                   # /welcomemsg off and /leavemsg off silence this one too
            from handlers.welcome import _switched_off
            if await _switched_off(chat.id, "join" if kind == "welcome" else "leave"):
                return False
        except Exception:
            pass
        st = await store.get_settings(chat.id)
        sec = st["welcome" if kind == "welcome" else "goodbye"]
        if not sec["on"] or not sec["text"]:
            return False
        try:
            count = await tg(bot.get_chat_member_count, chat.id)
        except Exception:
            count = None
        text, kb = build(sec["text"], user, chat.title, chat.id, count)
        if kind == "welcome" and sec.get("clean") and chat.id in _last_welcome:
            try:
                await bot.delete_message(chat.id, _last_welcome.pop(chat.id))
            except Exception:
                pass
        sent = await tg(bot.send_message, chat.id, text, reply_markup=kb, disable_web_page_preview=True)
        if kind == "welcome":
            _last_welcome[chat.id] = sent.message_id
        return True
    except Exception:
        log.warning("could not send %s in %s", kind, getattr(chat, "id", "?"), exc_info=True)
        return False
