"""/members - admins: why does the bot (not) know people here, and /members scan to look everyone up.
Telegram gives bots no member list. This shows what the bot can see and fixes the roster by checking every known player."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from config import get_settings
from handlers.waifu import _admins, _still_here
from services import members as mem

router = Router()
log = logging.getLogger(__name__)


async def _is_admin(bot: Bot, chat_id: int, uid: int) -> bool:
    if uid == get_settings().owner_id:
        return True
    try:
        return (await bot.get_chat_member(chat_id, uid)).status in ("administrator", "creator")
    except Exception:
        return False


async def _scan_and_report(bot: Bot, chat_id: int) -> None:
    try:
        checked, found = await mem.full_scan(chat_id, lambda uid, strict=False: _still_here(bot, chat_id, uid, strict), lambda: _admins(bot, chat_id))
        total = await mem.roster_size(chat_id)
        await bot.send_message(chat_id, f"🔎 Scan finished: checked <b>{checked}</b> known players, found <b>{found}</b> new member(s). "
                                        f"I now know <b>{total}</b> people in this group.")
    except Exception:
        log.exception("member scan failed")
        try:
            await bot.send_message(chat_id, "⚠️ The scan stopped because of an error (see logs).")
        except Exception:
            pass


@router.message(Command("members", "scanmembers"))
async def members_cmd(m: Message, command: CommandObject, bot: Bot):
    if m.chat.type not in ("group", "supergroup") or m.from_user is None:
        await m.reply("👥 Use /members in a group.")
        return
    if not await _is_admin(bot, m.chat.id, m.from_user.id):
        await m.reply("⛔ Group admins only.")
        return
    if (command.args or "").strip().lower().startswith("scan") or (m.text or "").lower().startswith("/scanmembers"):
        if mem.scanning(m.chat.id):
            await m.reply("⏳ A scan is already running here.")
            return
        await m.reply("🔎 Scanning every player I know against this group… this can take a minute or two. I'll post the result here.")
        asyncio.create_task(_scan_and_report(bot, m.chat.id))
        return
    try:
        me = await bot.get_me()
        st = (await bot.get_chat_member(m.chat.id, me.id)).status
        total = await bot.get_chat_member_count(m.chat.id)
    except Exception:
        me, st, total = None, "?", "?"
    known = await mem.roster_size(m.chat.id)
    sees_all = bool(me and me.can_read_all_group_messages)
    lines = [f"👥 <b>Members I know here:</b> {known} of {total}",
             f"🛡 I am: <b>{'admin' if st == 'administrator' else st}</b>",
             f"👀 I can read all messages: <b>{'yes' if sees_all or st == 'administrator' else 'NO'}</b>"]
    if not (sees_all or st == "administrator"):
        lines.append("\n⚠️ Group Privacy is ON and I'm not an admin, so I only see commands and replies. Fix: make me an <b>admin</b>, "
                     "or in BotFather use /setprivacy → Disable (then remove and re-add me).")
    lines.append("\nTelegram has no 'list members' for bots, so I learn people when they chat, join or are replied to. "
                 "Send <b>/members scan</b> to look up everyone I know from other groups.")
    await m.reply("\n".join(lines))
