"""Welcome / goodbye messages for groups (on by default, switchable per group)."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import ChatMemberUpdatedFilter, Command, CommandObject, JOIN_TRANSITION, LEAVE_TRANSITION
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


def _flag_key(chat_id: int, kind: str) -> str:
    return f"wl_off:{'leave' if kind == 'leave' else 'join'}:{chat_id}"


async def _switched_off(chat_id: int, kind: str) -> bool:
    try:
        async with db.tx() as s:
            row = await s.get(AppState, _flag_key(chat_id, kind))
            return bool(row and row.value == "1")
    except Exception:
        return False


async def welcome_on(chat_id: int, kind: str = "join") -> bool:
    """Welcome / goodbye are ON by default. A group admin can switch each off with /welcomemsg off and /leavemsg off.
    A group that configured its OWN welcome / goodbye with the moderation module gets that instead."""
    if await _switched_off(chat_id, kind):
        return False
    try:
        from moderation import legacy_welcome_suppressed
        if await legacy_welcome_suppressed(chat_id, kind):
            return False
    except Exception:
        pass                                          # moderation missing / broken -> behave exactly as before
    return True


async def _is_admin(bot: Bot, chat_id: int, uid: int) -> bool:
    try:
        from config import get_settings
        if uid == get_settings().owner_id:
            return True
    except Exception:
        pass
    try:
        return (await bot.get_chat_member(chat_id, uid)).status in ("administrator", "creator")
    except Exception:
        return False


async def _toggle(m: Message, bot: Bot, command: CommandObject, kind: str, label: str, cmd: str) -> None:
    if m.chat.type not in ("group", "supergroup"):
        await m.reply("This works in groups only.")
        return
    arg = (command.args or "").strip().lower()
    if arg not in ("on", "off"):
        state = "OFF 🔕" if await _switched_off(m.chat.id, kind) else "ON 🔔"
        await m.reply(f"{label} messages are <b>{state}</b>\nAdmins: <code>/{cmd} on</code> or <code>/{cmd} off</code>")
        return
    if m.from_user is None or not await _is_admin(bot, m.chat.id, m.from_user.id):
        await m.reply("❌ Only group admins can change this.")
        return
    from services import users
    async with db.tx() as s:
        await users.set_state(s, _flag_key(m.chat.id, kind), "1" if arg == "off" else "0")
    await m.reply(f"{'🔔' if arg == 'on' else '🔕'} {label} messages are now <b>{arg.upper()}</b>.")


@router.message(Command("welcomemsg", ignore_case=True))
async def nm_welcome(m: Message, bot: Bot, command: CommandObject):
    await _toggle(m, bot, command, "join", "Welcome", "welcomemsg")


@router.message(Command("leavemsg", ignore_case=True))
async def nm_goodbye(m: Message, bot: Bot, command: CommandObject):
    await _toggle(m, bot, command, "leave", "Leaving", "leavemsg")


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
    if u.is_bot or not await welcome_on(m.chat.id, "leave") or not await _once("l", m.chat.id, u.id):
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
    if ev.chat.type not in ("group", "supergroup") or u.is_bot or not await welcome_on(ev.chat.id, "leave"):
        return
    if await _once("l", ev.chat.id, u.id):
        await _announce_leave(bot, ev.chat, u)


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def added_to_group(ev: ChatMemberUpdated, bot: Bot):
    """The bot itself was added to a group: introduce yourself. (In a private chat this is someone starting / unblocking the bot.)"""
    if ev.chat.type == "private":
        from services import users
        await users.mark_started(ev.chat.id)
        return
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
    import asyncio
    from services import members
    asyncio.create_task(members.auto_scan(bot, ev.chat.id, force=True))      # learn who is here right away, no command needed


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def removed_from_group(ev: ChatMemberUpdated) -> None:
    """The bot itself was kicked from, or left, a group. Flag it (never delete the row - its history stays)
    so /stats can report 'groups removed' separately from groups we're still in, and broadcasts skip it.
    (In a private chat this is the person blocking the bot - flagged right away, no need to wait for a failed send.)"""
    if ev.chat.type == "private":
        from services import users
        await users.mark_blocked(ev.chat.id)
        return
    if ev.chat.type not in ("group", "supergroup"):
        return
    from services import users
    try:
        await users.mark_group_removed(ev.chat.id)
    except Exception:
        log.exception("failed to flag group %s as removed", ev.chat.id)


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
