"""/afk [reason] + the automatic parts: tagging / replying to an away person tells you they're away, and their
next message welcomes them back. The automatic part is an outer middleware, so it sees every group message."""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, TelegramObject

from services import afk
from services.kv import kv
from utils.fmt import dur, esc, mention

log = logging.getLogger(__name__)
router = Router()


@router.message(Command("afk"))
async def afk_cmd(m: Message, command: CommandObject):
    if m.from_user is None:
        return
    reason = (command.args or "").strip()
    await afk.set_away(m.from_user.id, reason)
    name = mention(m.from_user.id, m.from_user.full_name)
    await m.reply(f"😴 {name} is now <b>AFK</b>" + (f": {esc(reason[:100])}" if reason else "") +
                  "\n<i>I'll tell anyone who tags you, and welcome you back when you write again.</i>")


def _is_afk_cmd(m: Message) -> bool:
    t = (m.text or "").split(None, 1)
    return bool(t) and t[0].lower().split("@")[0] == "/afk"


class AfkMiddleware(BaseMiddleware):
    """Never blocks anything: only adds 'welcome back' / 'X is AFK' notes next to normal handling."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if isinstance(event, Message) and event.from_user and not event.from_user.is_bot \
                    and event.chat.type in ("group", "supergroup") and await afk.is_any():
                await self._notes(event)
        except Exception:
            log.exception("afk notes failed")
        return await handler(event, data)

    async def _notes(self, m: Message) -> None:
        uid = m.from_user.id
        if not _is_afk_cmd(m):
            old = await afk.back(uid)
            if old is not None:
                await m.reply(f"👋 Welcome back {mention(uid, m.from_user.full_name)}! You were away for <b>{dur(time.time() - old[1])}</b>.")
        ids: set[int] = set()
        if m.reply_to_message and m.reply_to_message.from_user:
            ids.add(m.reply_to_message.from_user.id)
        names: list[str] = []
        text = m.text or m.caption or ""
        for e in (m.entities or m.caption_entities or []):
            if e.type == "text_mention" and e.user:
                ids.add(e.user.id)
            elif e.type == "mention":
                names.append(text[e.offset + 1:e.offset + e.length])
        if names:
            ids.update(await afk.ids_by_username(names))
        ids.discard(uid)
        for tid in list(ids)[:3]:
            info = await afk.get(tid)
            if info is None or not await kv.set_nx(f"afknote:{m.chat.id}:{tid}", 60):      # at most one note a minute per person per group
                continue
            reason, since = info
            who = m.reply_to_message.from_user if m.reply_to_message and m.reply_to_message.from_user and m.reply_to_message.from_user.id == tid else None
            label = mention(tid, who.full_name) if who else mention(tid, "They")
            await m.reply(f"😴 {label} is <b>AFK</b>" + (f": {esc(reason)}" if reason else "") + f" · {dur(time.time() - since)} ago")
