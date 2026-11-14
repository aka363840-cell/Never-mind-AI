"""Welcome / goodbye messages for groups (always on)."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import ChatMemberUpdatedFilter, JOIN_TRANSITION, LEAVE_TRANSITION
from aiogram.types import ChatJoinRequest, ChatMemberUpdated, InlineKeyboardButton as B, Message

from database.models import AppState
from database.session import db
from services.kv import kv
from keyboards.menus import kb
from utils import brand, content, deck
from utils.fmt import esc, mention
from utils.telegram import safe_send

log = logging.getLogger(__name__)

router = Router()

WELCOME = [
    "🎉 Welcome {n} to <b>{g}</b>! Make yourself at home {e}",
    "🌟 Hey {n}, welcome to <b>{g}</b>! Say hi to everyone 👋",
    "🤗 {n} just joined <b>{g}</b>! Be nice, be fun, enjoy 💖",
    "✨ A wild {n} appeared in <b>{g}</b>! Welcome 🥳",
    "💎 {n}, welcome to <b>{g}</b>! Try /daily and /help to start 🎁",
]
GOODBYE = [
    "👋 {n} left <b>{g}</b>. Take care!",
    "😢 {n} has left the chat. We'll miss you!",
    "🚪 {n} walked out of <b>{g}</b>. Bye bye {e}",
    "🌙 {n} left the group. Good luck out there!",
]
WELCOME += content.WELCOME_MORE
GOODBYE += content.GOODBYE_MORE


async def welcome_on(chat_id: int) -> bool:
    """Welcome / goodbye messages are ALWAYS on (no switch any more; an old 'off' flag in the DB is ignored)."""
    return True


async def _once(kind: str, chat_id: int, uid: int) -> bool:
    """Telegram can report one join/leave twice (service message AND chat_member update): announce it only once."""
    try:
        return await kv.set_nx(f"wl:{kind}:{chat_id}:{uid}", 30)
    except Exception:
        return True


async def _announce_join(bot: Bot, chat, people) -> None:
    from services import users
    for u in people:                       # new members are registered right away (no /start needed)
        try:
            await users.touch(u.id, u.username, u.full_name or u.first_name)
        except Exception:
            pass
    names = ", ".join(mention(u.id, u.full_name or u.first_name) for u in people[:5])
    text = deck.pick("welcome", WELCOME, chat.id).format(n=names, g=esc(chat.title or "the group"), e=brand.EMOJI)
    await bot.send_message(chat.id, f"{text}\n<i>— {esc(brand.name())}</i>",
                           reply_markup=kb([[B(text=f"{brand.EMOJI} Start me in DM", url=brand.link("start=menu"))]]) if brand.username() else None)


async def _announce_leave(bot: Bot, chat, u) -> None:
    await bot.send_message(chat.id, deck.pick("goodbye", GOODBYE, chat.id).format(n=mention(u.id, u.full_name or u.first_name),
                                                                    g=esc(chat.title or "the group"), e=brand.EMOJI))


# Way 1: Telegram's own "X joined / X left" service messages (not sent in big groups or when join/leave messages are hidden).
@router.message(F.new_chat_members)
async def on_join(m: Message, bot: Bot):
    if not await welcome_on(m.chat.id):
        return
    people = [u for u in m.new_chat_members if not u.is_bot]
    people = [u for u in people if await _once("j", m.chat.id, u.id)]
    if people:
        await _announce_join(bot, m.chat, people)


@router.message(F.left_chat_member)
async def on_leave(m: Message, bot: Bot):
    u = m.left_chat_member
    if u.is_bot or not await welcome_on(m.chat.id) or not await _once("l", m.chat.id, u.id):
        return
    await _announce_leave(bot, m.chat, u)


# Way 2 (reliable, also in big groups): member-status updates. Telegram sends these ONLY when the bot is an ADMIN of the group.
@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def cm_join(ev: ChatMemberUpdated, bot: Bot):
    u = ev.new_chat_member.user
    if ev.chat.type not in ("group", "supergroup") or u.is_bot or not await welcome_on(ev.chat.id):
        return
    if await _once("j", ev.chat.id, u.id):
        await _announce_join(bot, ev.chat, [u])


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def cm_leave(ev: ChatMemberUpdated, bot: Bot):
    u = ev.new_chat_member.user
    if ev.chat.type not in ("group", "supergroup") or u.is_bot or not await welcome_on(ev.chat.id):
        return
    if await _once("l", ev.chat.id, u.id):
        await _announce_leave(bot, ev.chat, u)


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def added_to_group(ev: ChatMemberUpdated, bot: Bot):
    """The bot itself was added to a group: introduce yourself."""
    if ev.chat.type not in ("group", "supergroup"):
        return
    from services import users
    try:
        await users.touch_group(ev.chat.id, ev.chat.title)
    except Exception:
        pass
    me = await bot.get_me()
    brand.update(me)
    text = (f"╭━━━━━━━━━━━━━━━━╮\n      ◈ <b>{esc(brand.fancy(brand.name()))}</b> {brand.EMOJI}\n╰━━━━━━━━━━━━━━━━╯\n\n"
            f"👋 Hello <b>{esc(ev.chat.title or 'everyone')}</b>! Thanks for adding me.\n\n"
            "💎 Economy &amp; shop · ⚔️ duels · 🗝 dungeons · 🐾 pets · 🏰 clans\n"
            "💍 marriage &amp; 👨‍👩‍👧‍👦 family trees · 🎭 fun actions with GIFs\n"
            f"🤖 Say <b>{esc(brand.name())}</b> or reply to me to chat\n\n"
            "🎁 Everyone: send /daily to start!\n"
            "⚙️ Group admins: /settings · 📖 /help · ℹ️ /about")
    try:
        await bot.send_message(ev.chat.id, text, reply_markup=kb([[B(text=f"{brand.EMOJI} Open menu in DM", url=brand.link("start=menu"))]]))
    except Exception:
        pass


JOIN_REQUEST_DM = (
    "Your join request has been received! An admin or moderator will need to approve it before you can join.\n\n"
    "In the meantime, feel free to /start the bot and explore!"
)


@router.chat_join_request()
async def on_join_request(ev: ChatJoinRequest, bot: Bot) -> None:
    """A user submitted a join request to a group we're in. Telegram grants a one-time private-messaging
    privilege for this (Bot API 5.5+): we may DM the user via `ev.user_chat_id` for a short window after the
    request, even if they never started the bot — but ONLY if the bot is a group admin with the
    'can_invite_users' right. We never touch the request itself (no approve/decline): that stays with the
    group's admins. Nothing here can crash the dispatcher; a failed DM is just logged, never raised."""
    if not await kv.set_nx(f"jr:{ev.chat.id}:{ev.from_user.id}", 300):
        return   # Telegram can redeliver the same update; don't double-DM for one request.
    msg = await safe_send(bot, ev.user_chat_id, JOIN_REQUEST_DM)
    if msg is None:
        log.info("join-request DM to %s (chat %s) not delivered (blocked, no admin right, or window expired)",
                  ev.from_user.id, ev.chat.id)
