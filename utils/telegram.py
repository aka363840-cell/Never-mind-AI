from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

log = logging.getLogger(__name__)
_TAG = re.compile(r"<[^>]+>")


def strip_html(t: str) -> str:
    return _TAG.sub("", t).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


async def safe_answer(cb: CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    """Callback queries expire after ~15 min; answering a stale one must never crash a handler."""
    try:
        await cb.answer(strip_html(text)[:190] if text else None, show_alert=alert)
    except TelegramBadRequest:
        pass


async def safe_edit(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "not modified" in str(e).lower():
            return
        try:
            await cb.message.answer(text, reply_markup=kb)
        except Exception:
            log.debug("edit fallback failed", exc_info=True)


async def safe_send(bot: Bot, chat_id: int, text: str, kb: InlineKeyboardMarkup | None = None, **kw) -> Message | None:
    """Returns None if the user blocked the bot (and records it)."""
    for _ in range(2):
        try:
            return await bot.send_message(chat_id, text, reply_markup=kb, **kw)
        except TelegramForbiddenError:
            from services import users
            if chat_id > 0:
                await users.mark_blocked(chat_id)
            return None
        except TelegramRetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 10))
        except TelegramBadRequest:
            return None
    return None


def split_text(text: str, n: int = 4000) -> list[str]:
    parts: list[str] = []
    while len(text) > n:
        cut = text.rfind("\n", 0, n)
        cut = cut if cut > n // 2 else n
        parts.append(text[:cut]); text = text[cut:].lstrip("\n")
    return parts + [text] if text else parts
