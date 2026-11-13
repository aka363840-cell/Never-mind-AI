from __future__ import annotations

from aiogram.types import Message

from services import users
from services.errors import GameError


async def _register(u) -> None:
    """A player who is replied to / tagged is registered automatically, so nobody has to /start the bot first."""
    try:
        await users.touch(u.id, u.username, u.full_name or u.first_name)
    except Exception:
        pass


async def _ask_telegram(message: Message, ref) -> int | None:
    """Best effort: let Telegram resolve an @username / numeric id, and register the player if it can."""
    try:
        chat = await message.bot.get_chat(ref)
    except Exception:
        return None
    if getattr(chat, "type", "") != "private":
        return None
    await _register(chat)
    return chat.id


async def _from_admins(message: Message, username: str) -> int | None:
    """In groups, admins can always be found by @username through the admin list."""
    if message.chat.type not in ("group", "supergroup"):
        return None
    try:
        for m in await message.bot.get_chat_administrators(message.chat.id):
            if m.user and not m.user.is_bot and (m.user.username or "").lower() == username.lstrip("@").lower():
                await _register(m.user)
                return m.user.id
    except Exception:
        pass
    return None


async def resolve_target(message: Message, args: list[str]) -> tuple[int | None, list[str]]:
    """Target by reply > tag (text_mention) > @username > numeric ID. Returns (uid, remaining args)."""
    rep = message.reply_to_message
    if rep and rep.from_user and not rep.from_user.is_bot:
        await _register(rep.from_user)
        return rep.from_user.id, args
    for ent in message.entities or []:
        if ent.type == "text_mention" and ent.user and not ent.user.is_bot:
            await _register(ent.user)
            return ent.user.id, args
    for i, a in enumerate(args):
        if a.startswith("@") and len(a) > 2:
            uid = await users.find_by_username(a) or await _ask_telegram(message, a) or await _from_admins(message, a)
            if uid is None:
                raise GameError(f"🔎 I haven't seen {a} yet. Just <b>reply to any message of theirs</b> with the command and it works right away.")
            return uid, args[:i] + args[i + 1:]
    if args and args[0].isdigit() and len(args[0]) >= 5:
        uid = int(args[0])
        await _ask_telegram(message, uid)
        return uid, args[1:]
    return None, args
