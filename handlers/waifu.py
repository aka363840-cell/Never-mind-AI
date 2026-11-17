"""/waifu - see services/waifu.py. Pure fun: no wallets, no family, no marriage."""
import asyncio
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import Message

from database.models import User
from services import waifu as wf
from utils.fmt import esc, mention

router = Router()
log = logging.getLogger(__name__)


async def _still_here(bot: Bot, chat_id: int, uid: int, strict: bool = False) -> bool:
    for attempt in range(4):
        try:
            mem = await bot.get_chat_member(chat_id, uid)
            return mem.status in ("member", "administrator", "creator") or (mem.status == "restricted" and getattr(mem, "is_member", True))
        except TelegramRetryAfter as e:                        # flood wait: wait it out instead of wrongly saying "not a member"
            await asyncio.sleep(min(e.retry_after, 30) + 0.5)
        except TelegramBadRequest:
            return False
        except Exception:
            if attempt == 3:
                return None if strict else True                # still unsure: strict callers (scans) get None = "ask again later"
            await asyncio.sleep(1 + attempt)
    return None if strict else True


async def _admins(bot: Bot, chat_id: int) -> list[int]:
    """The group's human admins. They are also registered (name + username), otherwise they could not be picked."""
    from services import users
    out = []
    for a in await bot.get_chat_administrators(chat_id):
        if a.user.is_bot:
            continue
        out.append(a.user.id)
        try:
            await users.touch(a.user.id, a.user.username, a.user.full_name or a.user.first_name)
        except Exception:
            pass
    return out


async def _empty_hint(bot: Bot, m: Message) -> str:
    """Why nobody could be picked, and the exact fix."""
    try:
        me = await bot.get_me()
        blind = not me.can_read_all_group_messages
        admin = (await bot.get_chat_member(m.chat.id, me.id)).status == "administrator"
    except Exception:
        blind, admin = False, True
    if blind and not admin:
        return ("🌸 I can't see this group's messages, so I don't know its members yet.\n"
                "👉 Make me a group <b>admin</b> (or turn off Group Privacy in BotFather: /setprivacy → Disable), then send /members scan.")
    return "🌸 I don't know enough members of this group yet. An admin can send <b>/members scan</b> to look everyone up."


async def _send_card(m: Message, bot: Bot, uid: int, name: str, text: str) -> None:
    """The waifu's profile photo; else the last photo we ever saw of them; else a clean initials picture. Always a picture."""
    from aiogram.types import BufferedInputFile
    from services import avatar
    from utils import avatars
    fid = await avatar.best(bot, uid)
    if fid:
        try:
            await m.reply_photo(fid, caption=text)
            return
        except Exception:
            log.info("remembered photo for %s no longer works", uid)
    try:
        await m.reply_photo(BufferedInputFile(avatars.initial_card(name), "waifu.jpg"), caption=text)
    except Exception:
        log.exception("initials card failed")
        await m.reply(text)


@router.message(Command("waifu"))
async def waifu_cmd(m: Message, bot: Bot, db_user: User):
    in_group = m.chat.type in ("group", "supergroup")
    if in_group:
        try:
            await bot.send_chat_action(m.chat.id, "upload_photo")      # the first look through a group can take a few seconds
        except Exception:
            pass
    r = await wf.pick(db_user.id, m.chat.id if in_group else None,
                      (lambda uid, strict=False: _still_here(bot, m.chat.id, uid, strict)) if in_group else None,
                      (lambda: _admins(bot, m.chat.id)) if in_group else None)
    if not r.ok:
        await m.reply("🌸 Use /waifu in a group — I pick your waifu of the day from its members." if r.reason == "group" else
                      await _empty_hint(bot, m))
        return
    text = wf.caption(f"<b>{mention(db_user.id, db_user.first_name)}</b>", f"<b>{mention(r.waifu_id, r.waifu_name)}</b>", r.bond, r.seconds_left)
    await _send_card(m, bot, r.waifu_id, r.waifu_name, text)
