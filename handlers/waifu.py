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


async def _owner_wives(m: Message, bot: Bot, db_user: User, in_group: bool) -> bool:
    """The owner's /waifu: his two wives. In a group only the wife who is in that group (both when both - or neither - are there)."""
    wl = [w for w in await wf.wives() if w]
    if not wl:
        return False
    show = wl
    if in_group and len(wl) == 2:
        here = [w for w in wl if await _still_here(bot, m.chat.id, w, False)]
        show = here if len(here) == 1 else wl
    from database.session import db
    for slot_uid in show:
        async with db.tx() as s:
            u = await s.get(User, slot_uid)
        name = u.first_name if u else "Wife"
        text = wf.wife_caption(f"<b>{mention(db_user.id, db_user.first_name)}</b>", f"<b>{mention(slot_uid, name)}</b>", 1 + wl.index(slot_uid))
        await _send_card(m, bot, slot_uid, name, text)
    return True


@router.message(Command("wife", "wives"))
async def wife_cmd(m: Message, bot: Bot, db_user: User):
    """Owner only: /wife (show) · /wife 1|2 <reply | @username | id> (set) · /wife clear 1|2"""
    from services import admin as admin_svc, users
    if admin_svc.role(db_user.id) != "owner":
        return
    parts = (m.text or "").split()[1:]
    cur = await wf.wives()
    if not parts:
        names = []
        for i, w in enumerate(cur, 1):
            u = await users.find_name(w) if w else None
            names.append(f"{i}. {mention(w, u or 'Wife')}" if w else f"{i}. —")
        return await m.reply("💍 <b>Your wives</b> (shown by /waifu: the one who is in the group, or both)\n" + "\n".join(names) +
                             "\n\n<code>/wife 1 @username</code> · <code>/wife 2 (reply)</code> · <code>/wife clear 1</code>")
    if parts[0].lower() == "clear" and len(parts) > 1 and parts[1] in ("1", "2"):
        await wf.set_wife(int(parts[1]), 0)
        return await m.reply(f"✅ Wife {parts[1]} cleared.")
    if parts[0] not in ("1", "2"):
        return await m.reply("Use <code>/wife 1 @username</code>, <code>/wife 2</code> (reply to her message), or <code>/wife clear 1</code>.")
    uid = None
    if m.reply_to_message and m.reply_to_message.from_user and not m.reply_to_message.from_user.is_bot:
        uid = m.reply_to_message.from_user.id
        await users.touch(uid, m.reply_to_message.from_user.username, m.reply_to_message.from_user.full_name)
    elif len(parts) > 1:
        t = parts[1].lstrip("@")
        uid = int(t) if t.isdigit() else await users.find_by_username(t)
    if not uid:
        return await m.reply("❌ I don't know her yet. Reply to one of her messages with <code>/wife %s</code>, or use her user id." % parts[0])
    await wf.set_wife(int(parts[0]), uid)
    await m.reply(f"💍 Wife {parts[0]} is now {mention(uid, (await users.find_name(uid)) or 'her')}.")


@router.message(Command("waifu"))
async def waifu_cmd(m: Message, bot: Bot, db_user: User):
    in_group = m.chat.type in ("group", "supergroup")
    from services import admin as admin_svc
    if admin_svc.role(db_user.id) == "owner" and await _owner_wives(m, bot, db_user, in_group):
        return                                                              # owner: his wives, no limit, no cooldown
    unlimited = db_user.id in await wf.wives()                              # the wives themselves: a new pick every time
    if in_group:
        try:
            await bot.send_chat_action(m.chat.id, "upload_photo")      # the first look through a group can take a few seconds
        except Exception:
            pass
    r = await wf.pick(db_user.id, m.chat.id if in_group else None,
                      (lambda uid, strict=False: _still_here(bot, m.chat.id, uid, strict)) if in_group else None,
                      (lambda: _admins(bot, m.chat.id)) if in_group else None, unlimited=unlimited)
    if not r.ok:
        await m.reply("🌸 Use /waifu in a group — I pick your waifu of the day from its members." if r.reason == "group" else
                      await _empty_hint(bot, m))
        return
    text = wf.caption(f"<b>{mention(db_user.id, db_user.first_name)}</b>", f"<b>{mention(r.waifu_id, r.waifu_name)}</b>", r.bond, r.seconds_left)
    await _send_card(m, bot, r.waifu_id, r.waifu_name, text)
