"""/tagall <text> or @all <text>   - mention every known member by name
/etagall <text> or @eall <text> - mention them with random emojis instead of names
/cancel                         - stop a running tag-all.   Group admins only.
Telegram gives bots no member list, so this tags everyone the bot knows in the group (people who chatted, joined, or are admins)."""
from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, TelegramObject
from sqlalchemy import select

from config import get_settings
from database.models import GroupMember, GroupStat, User
from database.session import db
from services.kv import kv
from utils.fmt import esc

log = logging.getLogger(__name__)
router = Router()

EMOJIS = list("😀😎🥳🤩😍🦋🌸🌟✨🔥💎🍀🎈🎉🌈🍓🍒🐱🐶🦄🐼🐯🦊🐸🍭🎵🚀🌙☀️💫🍉🎯🧸")
BY_NAME, BY_EMOJI = 5, 10            # mentions per message
PAUSE_S = 1.3                        # stays under Telegram's ~20 messages/minute per group
MAX_TAGS = 300
_running: dict[int, asyncio.Event] = {}


async def _is_admin(bot: Bot, chat_id: int, uid: int) -> bool:
    if uid == get_settings().owner_id:
        return True
    try:
        mem = await bot.get_chat_member(chat_id, uid)
        return mem.status in ("administrator", "creator")
    except Exception:
        return False


async def _people(bot: Bot, chat_id: int) -> list[tuple[int, str]]:
    ids: set[int] = set()
    async with db.tx() as s:
        ids.update((await s.execute(select(GroupMember.user_id).where(GroupMember.chat_id == chat_id))).scalars())
        ids.update((await s.execute(select(GroupStat.user_id).where(GroupStat.chat_id == chat_id))).scalars())
    try:
        ids.update(a.user.id for a in await bot.get_chat_administrators(chat_id) if not a.user.is_bot)
    except Exception:
        pass
    if not ids:
        return []
    async with db.tx() as s:
        rows = (await s.execute(select(User.id, User.first_name).where(User.id.in_(ids), User.deleted_account == False))).all()  # noqa: E712
    return [(i, n or "member") for i, n in rows][:MAX_TAGS]


async def _run(bot: Bot, chat_id: int, text: str, reply_to: int | None, emoji: bool, stop: asyncio.Event, asker: int) -> None:
    try:
        people = [p for p in await _people(bot, chat_id) if p[0] != asker]
        if not people:
            await bot.send_message(chat_id, "🤷 I don't know any members here yet. People appear once they chat.")
            return
        random.shuffle(people)
        size = BY_EMOJI if emoji else BY_NAME
        head = esc(text[:300]) if text else ""
        for i in range(0, len(people), size):
            if stop.is_set():
                await bot.send_message(chat_id, "🛑 Tagging stopped.")
                return
            chunk = people[i:i + size]
            tags = " ".join(f'<a href="tg://user?id={u}">{random.choice(EMOJIS) if emoji else esc(n[:25])}</a>' for u, n in chunk)
            body = (head + "\n" if head else "") + tags
            try:
                await bot.send_message(chat_id, body, reply_to_message_id=reply_to, allow_sending_without_reply=True, disable_web_page_preview=True)
            except Exception:
                log.exception("tagall send failed")
                return
            await asyncio.sleep(PAUSE_S)
    finally:
        _running.pop(chat_id, None)


async def start(bot: Bot, m: Message, text: str, emoji: bool) -> None:
    if m.chat.type not in ("group", "supergroup") or m.from_user is None:
        await m.reply("👥 Tag-all only works in groups.")
        return
    if not await _is_admin(bot, m.chat.id, m.from_user.id):
        await m.reply("⛔ Only group admins can tag everyone.")
        return
    rep = m.reply_to_message
    if not text and rep is None:
        await m.reply("ℹ️ Add some text, e.g. <code>/tagall Hello everyone!</code>, or reply to a message with /tagall.")
        return
    if m.chat.id in _running:
        await m.reply("⏳ A tag-all is already running here. Use /cancel to stop it.")
        return
    if not await kv.set_nx(f"tagall:{m.chat.id}", 60):
        await m.reply("⏳ Wait a minute between tag-alls.")
        return
    stop = asyncio.Event()
    _running[m.chat.id] = stop
    asyncio.create_task(_run(bot, m.chat.id, text, rep.message_id if rep else m.message_id if text else None, emoji, stop, m.from_user.id))


@router.message(Command("tagall", "tag_all"))
async def tagall_cmd(m: Message, command: CommandObject, bot: Bot):
    await start(bot, m, (command.args or "").strip(), False)


@router.message(Command("etagall", "emojitagall"))
async def etagall_cmd(m: Message, command: CommandObject, bot: Bot):
    await start(bot, m, (command.args or "").strip(), True)


@router.message(Command("cancel"))
async def cancel_cmd(m: Message, bot: Bot):
    ev = _running.get(m.chat.id)
    if ev is None or m.from_user is None:
        return                                              # nothing to cancel here: stay silent
    if not await _is_admin(bot, m.chat.id, m.from_user.id):
        await m.reply("⛔ Only group admins can cancel it.")
        return
    ev.set()


_AT = re.compile(r"^@(all|eall)(?:\s+(.*))?$", re.I | re.S)


class AtAllMiddleware(BaseMiddleware):
    """'@all text' / '@eall text' typed as a normal group message (never blocks the message itself)."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if isinstance(event, Message) and (event.text or "")[:2].lower() in ("@a", "@e"):
                mt = _AT.match(event.text.strip())
                if mt and event.chat.type in ("group", "supergroup"):
                    await start(data["bot"], event, (mt.group(2) or "").strip(), mt.group(1).lower() == "eall")
        except Exception:
            log.exception("@all failed")
        return await handler(event, data)
