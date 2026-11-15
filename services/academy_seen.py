"""'Was this group active in the last 7 days?' for the Academy announcements (Part 6).

The existing group tables have no activity time, so this tiny dispatcher-level middleware notes it: for every update from a group it
checks an in-memory dict (one float per group) and writes the academy_group_seen row AT MOST ONCE EVERY 6 HOURS per group. It is
never a per-message database write, never raises, and never changes or stops an update. Registered once in bot.build_dispatcher
(dp.update.outer_middleware), so it also sees updates that an earlier router consumes.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from services import academy_notify as N

log = logging.getLogger(__name__)
GROUP_TYPES = ("group", "supergroup")


def group_chat_id(update: Any) -> int | None:
    """The id of the group an Update came from (message, edit, button press, membership change), else None."""
    for attr in ("message", "edited_message", "my_chat_member", "chat_member"):
        obj = getattr(update, attr, None)
        chat = getattr(obj, "chat", None)
        if chat is not None and getattr(chat, "type", None) in GROUP_TYPES:
            return int(chat.id)
    cb = getattr(update, "callback_query", None)
    chat = getattr(getattr(cb, "message", None), "chat", None)
    if chat is not None and getattr(chat, "type", None) in GROUP_TYPES:
        return int(chat.id)
    return None


class GroupSeenMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        chat_id = None
        try:
            chat_id = group_chat_id(event)
            if chat_id is not None and N.seen_due(chat_id):
                await N.note_group_seen(chat_id)
        except Exception:
            log.debug("academy group-seen note failed", exc_info=True)
            if chat_id is not None:
                N._seen_mono.pop(chat_id, None)                      # try again on the next update
        return await handler(event, data)
