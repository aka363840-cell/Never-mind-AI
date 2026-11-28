from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database.models import User
from services import birthdays as B
from services.errors import GameError

router = Router()


@router.message(Command("birthday", "bday"))
async def birthday_cmd(m: Message, command: CommandObject, db_user: User):
    arg = (command.args or "").strip()
    if arg.lower() in ("clear", "remove", "off"):
        done = await B.clear(db_user.id)
        return await m.reply("🗑 Birthday removed." if done else "You haven't set a birthday.")
    if not arg:
        cur = await B.get(db_user.id)
        if cur:
            return await m.reply(f"🎂 Your birthday: <b>{B.pretty(cur)}</b>. I'll wish you in your groups and send "
                                 f"<b>{B.GIFT}</b> 💎! Change: <code>/birthday DD-MM</code> · remove: <code>/birthday clear</code>")
        return await m.reply("🎂 Tell me your birthday: <code>/birthday DD-MM</code> (e.g. 25-12). I'll wish you in your "
                             f"groups and gift <b>{B.GIFT}</b> 💎 every year! (No year needed.)")
    md = B.parse_md(arg)
    await B.set_(db_user.id, md)
    await m.reply(f"🎂 Saved: <b>{B.pretty(md)}</b>. See you then! 🥳")
