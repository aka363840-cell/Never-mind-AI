"""Shared plumbing: the two routers, the command-registration helper, error middleware, anonymous-admin gate, request preparation."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message, Update

from . import perms, store
from .perms import ANON_ADMIN_ID, ModError, explain
from .util import esc, parse_duration

log = logging.getLogger("moderation")

router = Router(name="moderation")            # included FIRST: moderation commands win over game commands with the same name (groups only)
tail_router = Router(name="moderation_tail")  # included right before the AI chat router: only the AI-isolation guard lives here
GROUP = F.chat.type.in_({"group", "supergroup"})

# name -> info. `shared` = also a name used by the game: moderation only answers staff (or only in groups) and leaves the rest to the game.
COMMANDS: dict[str, dict] = {}
HELP: list[dict] = []
GATED: set[str] = set()
_dp = {"dp": None}


def set_dispatcher(dp) -> None:
    _dp["dp"] = dp


class ModNS(BaseFilter):
    """Marks every moderation handler. CoreMiddleware (game throttling / game-ban / games-enabled gating) steps aside for these,
    so moderation works for admins even when games are off, and a game-level ban/mute can't disable moderation."""
    async def __call__(self, event, **kw):
        return {"mod_ns": True}


router.message.filter(ModNS())
router.callback_query.filter(ModNS())
tail_router.message.filter(ModNS())


class IsStaff(BaseFilter):
    """For command names the game also uses (e.g. the joke /kick): only group admins / the bot owner get the moderation version."""
    async def __call__(self, m: Message, bot: Bot) -> bool:
        u = m.from_user
        if u is None:
            return False
        if m.sender_chat and m.sender_chat.id == m.chat.id:
            return True
        try:
            return await perms.staff_role(bot, m.chat.id, u.id) != "none"
        except Exception:
            return False


def command(*names: str, cat: str, usage: str, desc: str, gate: bool = True, staff_shared: bool = False, shared: bool = False):
    """Register a moderation command (prefixes / and !). Every name must also appear in HELP so /modhelp never lies."""
    def deco(fn):
        flt = [Command(*names, prefix="/!"), GROUP]
        if staff_shared:
            flt.append(IsStaff())
        router.message.register(fn, *flt, flags={"moderation": True})
        for n in names:
            COMMANDS[n] = {"cat": cat, "shared": shared or staff_shared, "primary": names[0]}
            if gate:
                GATED.add(n)
        HELP.append({"names": names, "cat": cat, "usage": usage, "desc": desc})
        return fn
    return deco


# ----------------------------------------------------------------------------------------------------------- errors
class ModErrors(BaseMiddleware):
    """Turns every expected failure into a clear message. A moderation bug can never propagate into other features."""
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except ModError as e:
            await _say(event, str(e))
        except TelegramAPIError as e:
            await _say(event, explain(e))
        except Exception:
            log.exception("moderation handler crashed")
            await _say(event, "⚠️ Something went wrong in the moderation module. Nothing was confirmed; other bot features are unaffected.")


async def _say(event, text: str) -> None:
    try:
        if isinstance(event, CallbackQuery):
            await event.answer(re.sub(r"<[^>]+>", "", text)[:190], show_alert=True)
        elif isinstance(event, Message):
            await event.reply(text, disable_web_page_preview=True)
    except Exception:
        pass


router.message.middleware(ModErrors())
router.callback_query.middleware(ModErrors())
tail_router.message.middleware(ModErrors())


# ----------------------------------------------------------------------------------------------------------- anonymous admins
_anon: dict[str, tuple[float, Message]] = {}
_CMD = re.compile(r"^[/!](\w+)(?:@(\w+))?")


class AnonGate(BaseMiddleware):
    """Messages sent 'as the group' carry no user id, so identity can't be trusted. Instead of guessing, ask the admin to tap a
    button; Telegram then tells us who really tapped, and the command is re-run as THAT person."""
    async def __call__(self, handler, m: Message, data):
        if not (isinstance(m, Message) and m.sender_chat and m.sender_chat.id == m.chat.id and m.text):
            return await handler(m, data)
        mt = _CMD.match(m.text)
        bot: Bot = data["bot"]
        if not mt or mt.group(1).lower() not in GATED:
            return await handler(m, data)
        me = await bot.me()
        if mt.group(2) and mt.group(2).lower() != (me.username or "").lower():
            return await handler(m, data)
        now = time.monotonic()
        for k in [k for k, (t, _) in _anon.items() if now - t > 600]:
            _anon.pop(k, None)
        if len(_anon) > 500:
            return None
        token = f"{m.chat.id % 10**6}{m.message_id}"
        _anon[token] = (now, m)
        try:
            await m.reply("🕶 You're posting anonymously, so I can't tell which admin you are.\nTap to verify - the command then runs as <b>you</b>.",
                          reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text="✅ Verify I'm an admin", callback_data=f"mod:an:{token}")]]))
        except Exception:
            pass
        return None


router.message.outer_middleware(AnonGate())


@router.callback_query(F.data.startswith("mod:an:"))
async def anon_verify(cb: CallbackQuery, bot: Bot):
    token = cb.data.split(":", 2)[2]
    item = _anon.get(token)
    if item is None:
        return await cb.answer("Expired - please send the command again.", show_alert=True)
    orig = item[1]
    if await perms.staff_role(bot, orig.chat.id, cb.from_user.id, fresh=True) == "none":
        return await cb.answer("⛔ Only group admins can verify.", show_alert=True)
    _anon.pop(token, None)
    await cb.answer("Verified ✔")
    try:
        await cb.message.delete()
    except Exception:
        pass
    m2 = orig.model_copy(update={"from_user": cb.from_user, "sender_chat": None}).as_(bot)
    dp = _dp["dp"]
    if dp is not None:
        await dp.feed_update(bot, Update(update_id=int(time.time() * 1000) % 2**31, message=m2))


# ----------------------------------------------------------------------------------------------------------- request helper
@dataclass
class Req:
    bot: Bot
    m: Message
    chat_id: int
    actor: int
    st: dict
    args: list[str]
    target: int | None = None
    user: object | None = None
    member: object | None = None
    extra: dict = field(default_factory=dict)

    async def who(self, uid: int | None = None) -> str:
        uid = uid or self.target
        return await perms.label(self.bot, self.chat_id, uid, self.user if uid == self.target else None)


async def prepare(m: Message, command: CommandObject | None, bot: Bot, *, right: str | None = None, bot_right: str | None = ...,
                  target: bool = False, allow_self: bool = False, allow_admin_target: bool = False, need_actor_admin: bool = True) -> Req:
    """Validate everything BEFORE any action: who is asking, what the bot may do, who the target is, whether they are protected."""
    if m.from_user is None:
        raise ModError("⛔ I can't identify the sender of this message.")
    chat_id, actor = m.chat.id, m.from_user.id
    if need_actor_admin:
        await perms.require_actor(bot, chat_id, actor, right)
    if bot_right is ...:
        bot_right = right
    if bot_right is not None or right is not None or need_actor_admin:
        await perms.require_bot(bot, chat_id, bot_right)
    st = await store.get_settings(chat_id)
    args = (command.args or "").split() if command else []
    req = Req(bot=bot, m=m, chat_id=chat_id, actor=actor, st=st, args=args)
    if target:
        uid, rest, user = await perms.resolve_target(bot, m, args)
        if uid is None:
            raise ModError("🎯 Who? Reply to their message, or give <b>@username</b> / a numeric <b>user ID</b>.")
        if uid == actor and not allow_self:
            raise ModError("🙃 You can't do that to yourself.")
        req.target, req.args, req.user = uid, rest, user
        if not allow_admin_target:
            why, member = await perms.protection_reason(bot, chat_id, uid, st)
            if why:
                raise ModError(f"🛡 I won't do that - the target is {why}.")
            req.member = member
    return req


def split_duration(args: list[str]) -> tuple[int | None, str]:
    """['1h', 'spam', 'links'] -> (3600, 'spam links'); ['spam'] -> (None, 'spam')"""
    if args:
        d = parse_duration(args[0])
        if d is not None:
            return d, " ".join(args[1:])[:300]
    return None, " ".join(args)[:300]


async def reply(m: Message, text: str, kb: InlineKeyboardMarkup | None = None) -> Message:
    return await m.reply(text, reply_markup=kb, disable_web_page_preview=True)


_thr: dict[tuple, float] = {}


def throttled(uid: int, key: str, secs: float) -> bool:
    """True if this user used `key` less than `secs` ago (and records the use otherwise). For public commands (report, banme ...)."""
    now = time.monotonic()
    if len(_thr) > 5000:
        for k in [k for k, t in _thr.items() if now - t > 300]:
            _thr.pop(k, None)
    last = _thr.get((uid, key))
    if last is not None and now - last < secs:
        return True
    _thr[(uid, key)] = now
    return False
