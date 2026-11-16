"""Authorization, Telegram-error translation, target resolution and protection rules.
Identity is ALWAYS decided by numeric user id - never by username or display name."""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError,
                                TelegramRetryAfter, TelegramServerError)
from aiogram.types import Message

from config import get_settings
from . import store
from .util import esc, mention

log = logging.getLogger("moderation.perms")

ANON_ADMIN_ID = 1087968824            # @GroupAnonymousBot: the sender id of anonymous-admin messages
SERVICE_IDS = frozenset({777000, 1087968824, 136817688, 1271266957})     # Telegram service accounts / channel-forward bots

RIGHT_FIELD = {"restrict": "can_restrict_members", "delete": "can_delete_messages", "pin": "can_pin_messages",
               "promote": "can_promote_members", "info": "can_change_info", "invite": "can_invite_users",
               "voice": "can_manage_video_chats", "manage": "can_manage_chat"}
RIGHT_LABEL = {"restrict": "Ban users", "delete": "Delete messages", "pin": "Pin messages", "promote": "Add new admins",
               "info": "Change group info", "invite": "Invite users via link", "voice": "Manage video chats", "manage": "Manage group"}


class ModError(Exception):
    """A user-facing failure: the message is HTML and is shown as-is."""


def owner_id() -> int:
    return get_settings().owner_id


# ----------------------------------------------------------------------------------------------------------- Telegram calls
async def tg(method, *args, **kw):
    """Call a Bot API method: waits out flood limits (RetryAfter) and retries transient network / 5xx errors."""
    last: Exception | None = None
    for attempt in range(4):
        try:
            return await method(*args, **kw)
        except TelegramRetryAfter as e:
            last = e
            await asyncio.sleep(min(float(e.retry_after) + 0.5, 60))
        except (TelegramNetworkError, TelegramServerError) as e:
            last = e
            if attempt >= 2:
                break
            await asyncio.sleep(1.0 + attempt)
    assert last is not None
    raise last


def explain(e: BaseException) -> str:
    """Telegram / network error -> a clear HTML sentence for the admin."""
    if isinstance(e, ModError):
        return str(e)
    msg = str(e).lower()
    if isinstance(e, TelegramRetryAfter):
        return "⏳ Telegram is rate-limiting me right now. Please try again in a moment."
    if isinstance(e, (TelegramNetworkError, TelegramServerError)):
        return "🌐 Network trouble reaching Telegram. Nothing was confirmed - please try again."
    if isinstance(e, TelegramForbiddenError):
        return "🚫 Telegram forbids me from doing that here (I may have been removed or restricted)."
    if any(s in msg for s in ("not enough rights", "chat_admin_required", "need administrator rights", "have no rights", "rights to")):
        return "🔒 I don't have the admin right needed for that. Open the group's admin settings for me and enable the matching right."
    if any(s in msg for s in ("can't remove chat owner", "user is an administrator", "can't restrict an administrator", "user_admin_invalid", "administrator of the chat")):
        return "🛡 Telegram won't let me act on that user (they are an admin / owner, or an admin that someone else promoted)."
    if any(s in msg for s in ("user not found", "participant_id_invalid", "user_id_invalid", "chat not found", "member not found")):
        return "🔎 Telegram can't find that user (wrong ID, or they were never in this group)."
    if "user_not_participant" in msg or "not a member" in msg:
        return "🔎 That user is not in this group."
    if "message to delete not found" in msg or "message can't be deleted" in msg or "message_id_invalid" in msg:
        return "🗑 That message is already gone, too old, or can't be deleted."
    if "supergroup" in msg or "method is available only for" in msg:
        return "ℹ️ Telegram only allows this in supergroups. Convert the group to a supergroup first."
    if "not modified" in msg:
        return "ℹ️ Nothing to change: it is already set that way."
    if "can't parse entities" in msg or "can't find end" in msg:
        return "✍️ That text has invalid formatting. Check your bold/italic/link markup."
    if "admin_rank" in msg or "custom_title" in msg or ("title" in msg and "invalid" in msg):
        return "✍️ Telegram rejected that title (max 16 characters, no emoji)."
    if "bot_group_forbidden" in msg or "bots can't" in msg:
        return "🤖 Telegram doesn't allow bots to do that to this account."
    if "user_is_bot" in msg or "can't be used for bots" in msg:
        return "🤖 Telegram doesn't allow that for bot accounts."
    if "can_be_edited" in msg or "not the creator" in msg or "chat_creator_required" in msg:
        return "🛡 Only the group owner (or the admin who promoted them) can change that admin."
    if isinstance(e, TelegramBadRequest):
        return f"⚠️ Telegram refused: <code>{esc(str(e)[:160])}</code>"
    return "⚠️ Something went wrong talking to Telegram. Nothing was confirmed."


# ----------------------------------------------------------------------------------------------------------- admin cache
_cache: dict[int, tuple[float, dict]] = {}
CACHE_TTL = 45


def invalidate(chat_id: int | None = None) -> None:
    if chat_id is None:
        _cache.clear()
    else:
        _cache.pop(chat_id, None)


async def admins(bot: Bot, chat_id: int, force: bool = False) -> dict:
    hit = _cache.get(chat_id)
    if hit and not force and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    lst = await tg(bot.get_chat_administrators, chat_id)
    d = {m.user.id: m for m in lst}
    _cache[chat_id] = (time.monotonic(), d)
    return d


def status_of(member) -> str:
    if member is None:
        return "unknown"
    s = getattr(member, "status", "")
    return str(getattr(s, "value", s))


def is_staff_status(status: str) -> bool:
    return status in ("creator", "administrator")


async def get_member(bot: Bot, chat_id: int, uid: int):
    try:
        return await tg(bot.get_chat_member, chat_id, uid)
    except TelegramBadRequest as e:
        if any(s in str(e).lower() for s in ("user not found", "participant_id_invalid", "user_id_invalid", "member not found")):
            return None
        raise


def has_right(member, right: str | None) -> bool:
    st = status_of(member)
    if st == "creator":
        return True
    if st != "administrator":
        return False
    return True if right is None else bool(getattr(member, RIGHT_FIELD[right], False))


async def staff_role(bot: Bot, chat_id: int, uid: int, fresh: bool = False) -> str:
    """owner (bot owner) | creator | admin | none"""
    if uid == owner_id():
        return "owner"
    try:
        m = (await get_member(bot, chat_id, uid)) if fresh else (await admins(bot, chat_id)).get(uid)
    except TelegramAPIError:
        m = (await admins(bot, chat_id)).get(uid) if fresh else None
    st = status_of(m)
    return "creator" if st == "creator" else "admin" if st == "administrator" else "none"


async def require_actor(bot: Bot, chat_id: int, uid: int, right: str | None = None, fresh: bool = True) -> None:
    """The person asking must be the bot owner, the group creator, or an admin holding `right`."""
    if uid == owner_id():
        return
    if uid == ANON_ADMIN_ID:
        raise ModError("🕶 I can't tell which admin you are while you post anonymously. Use the Verify button I send, or turn anonymity off.")
    try:
        m = await get_member(bot, chat_id, uid) if fresh else (await admins(bot, chat_id)).get(uid)
    except TelegramAPIError as e:
        raise ModError(explain(e))
    st = status_of(m)
    if st == "creator":
        return
    if st != "administrator":
        raise ModError("⛔ Only group admins can use this command.")
    if right and not has_right(m, right):
        raise ModError(f"⛔ You need the admin right <b>{RIGHT_LABEL[right]}</b> to do this.")


async def bot_member(bot: Bot, chat_id: int, force: bool = False):
    return (await admins(bot, chat_id, force)).get(bot.id)


async def require_bot(bot: Bot, chat_id: int, right: str | None = None, force: bool = False) -> None:
    """The bot itself must be an admin holding `right` - otherwise nothing is attempted and the admin is told what to enable."""
    try:
        m = await bot_member(bot, chat_id, force)
    except TelegramAPIError as e:
        raise ModError(explain(e))
    if status_of(m) not in ("administrator", "creator"):
        need = f" with <b>{RIGHT_LABEL[right]}</b> enabled" if right else ""
        raise ModError(f"🤖 I'm not an admin in this group. Please make me an admin{need}, then try again.")
    if right and not has_right(m, right):
        raise ModError(f"🔒 I'm missing the admin right <b>{RIGHT_LABEL[right]}</b>.\nGroup settings → Administrators → me → enable it, then run the command again.")


async def bot_can(bot: Bot, chat_id: int, right: str) -> bool:
    try:
        return has_right(await bot_member(bot, chat_id), right)
    except Exception:
        return False


async def is_exempt(bot: Bot, chat_id: int, uid: int, st: dict) -> bool:
    """Exempt from automatic enforcement: bot owner, admins, approved members, service accounts, protected ids."""
    if uid == owner_id() or uid in SERVICE_IDS or uid in st.get("protected_ids", ()):
        return True
    if uid in await store.approved_set(chat_id):
        return True
    try:
        return uid in await admins(bot, chat_id)
    except Exception:
        return False


# ----------------------------------------------------------------------------------------------------------- targets
async def protection_reason(bot: Bot, chat_id: int, uid: int, st: dict):
    """-> (reason or None, ChatMember or None). A reason means: never moderate this account."""
    if uid == bot.id:
        return "that's me 🙂", None
    if uid == owner_id():
        return "the bot owner is protected", None
    if uid in SERVICE_IDS:
        return "a Telegram service account", None
    if uid in st.get("protected_ids", ()):
        return "a protected administrator", None
    m = await get_member(bot, chat_id, uid)
    if is_staff_status(status_of(m)):
        return "a group administrator", m
    return None, m


async def label(bot: Bot, chat_id: int, uid: int, user=None) -> str:
    if user is not None:
        return mention(uid, getattr(user, "full_name", None) or getattr(user, "first_name", None))
    row = await store.member_row(chat_id, uid)
    if row and row.name:
        return mention(uid, row.name)
    try:
        m = await get_member(bot, chat_id, uid)
        if m is not None:
            return mention(uid, m.user.full_name)
    except Exception:
        pass
    return mention(uid, str(uid))


async def resolve_target(bot: Bot, m: Message, args: list[str]):
    """reply > text-mention > @username > numeric id.  -> (uid | None, remaining args, user | None)"""
    rep = m.reply_to_message
    if rep is not None:
        if rep.sender_chat and rep.sender_chat.id == m.chat.id:
            raise ModError("🕶 That message was posted by an anonymous admin - admins can't be moderated.")
        if rep.sender_chat and rep.sender_chat.id != m.chat.id:
            raise ModError("📢 That message was sent on behalf of a channel. Reply to a member's own message instead.")
        if rep.from_user:
            return rep.from_user.id, args, rep.from_user
    for ent in m.entities or []:
        if ent.type == "text_mention" and ent.user:
            return ent.user.id, args, ent.user
    for i, a in enumerate(args):
        if a.startswith("@") and len(a) > 2:
            uid = await store.find_member_by_username(m.chat.id, a)
            if uid is None:
                uid = await _ask_telegram(bot, a)
            if uid is None:
                try:
                    for adm in (await admins(bot, m.chat.id)).values():
                        if (adm.user.username or "").lower() == a.lstrip("@").lower():
                            uid = adm.user.id
                except Exception:
                    pass
            if uid is None:
                raise ModError(f"🔎 I haven't seen {esc(a)} in this group. Reply to one of their messages, or use their numeric ID.")
            return uid, args[:i] + args[i + 1:], None
    if args and args[0].lstrip("-").isdigit() and len(args[0]) >= 5:
        uid = int(args[0])
        if uid < 0:
            raise ModError("❌ That looks like a chat ID, not a user ID.")
        return uid, args[1:], None
    return None, args, None


async def _ask_telegram(bot: Bot, ref: str) -> int | None:
    try:
        ch = await bot.get_chat(ref)
    except Exception:
        return None
    return ch.id if getattr(ch, "type", "") == "private" else None
