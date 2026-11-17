"""/waifu - see services/waifu.py. Pure fun: no wallets, no family, no marriage."""
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message

from database.models import User
from services import waifu as wf
from utils.fmt import esc

router = Router()
log = logging.getLogger(__name__)


async def _still_here(bot: Bot, chat_id: int, uid: int, strict: bool = False) -> bool:
    try:
        mem = await bot.get_chat_member(chat_id, uid)
        return mem.status in ("member", "administrator", "creator") or (mem.status == "restricted" and getattr(mem, "is_member", True))
    except TelegramBadRequest:
        return False
    except Exception:
        return not strict                                      # a hiccup is no reason to drop someone (but no reason to add one either)


async def _admins(bot: Bot, chat_id: int) -> list[int]:
    return [a.user.id for a in await bot.get_chat_administrators(chat_id) if not a.user.is_bot]


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
                      "🌸 I haven't seen enough members in this group yet. Chat for a bit, then try /waifu again!")
        return
    text = wf.caption(esc(db_user.first_name), esc(r.waifu_name), r.bond, r.seconds_left)
    try:
        photos = await bot.get_user_profile_photos(r.waifu_id, limit=1)
        if photos.total_count and photos.photos:
            await m.reply_photo(photos.photos[0][-1].file_id, caption=text)
            return
    except Exception:
        log.info("no profile photo for %s", r.waifu_id)      # privacy settings / no photo: fall back to text only
    await m.reply(text)
