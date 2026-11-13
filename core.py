from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message, TelegramObject

from game.rules import R
from services import admin as admin_svc, users
from services.errors import GameError
from services.kv import kv
from utils import brand
from utils.addressed import addressed
from utils.fmt import esc
from utils.commands import ensure_scope
from utils.telegram import safe_answer, strip_html

log = logging.getLogger(__name__)
GROUP_OK = ("/start", "/help", "/settings", "/menu", "/about")


async def maintenance_on() -> bool:
    cached = await kv.get("maint")
    if cached is None:
        cached = await users.get_state("maintenance", "0")
        await kv.put("maint", cached, 5)
    return cached == "1"


CMD_MAX, CMD_WINDOW_S, CMD_COOLDOWN_S = 5, 60, 20      # max 5 commands per minute, then a 20 s cooldown


async def _command_allowed(uid: int, event: Message) -> bool:
    """Max CMD_MAX commands per CMD_WINDOW_S per user; going over triggers a CMD_COOLDOWN_S lock (owner is exempt)."""
    left = await kv.ttl(f"cmdlock:{uid}")
    if left > 0:
        return False                                   # silent while locked: no reply spam
    if await kv.incr_window(f"cmd:{uid}", CMD_WINDOW_S) > CMD_MAX:
        await kv.put(f"cmdlock:{uid}", "1", CMD_COOLDOWN_S)
        try:
            await event.reply(f"⏳ Too many commands! Max {CMD_MAX} per minute. Try again in {CMD_COOLDOWN_S}s.")
        except Exception:
            pass
        return False
    return True


class ErrorMiddleware(BaseMiddleware):
    """Turns expected GameErrors into friendly replies; never leaks internals."""

    async def __call__(self, handler: Callable, event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            return await handler(event, data)
        except GameError as e:
            if isinstance(event, CallbackQuery):
                await safe_answer(event, str(e), alert=True)
            elif isinstance(event, Message):
                await event.reply(str(e))
        except TelegramForbiddenError:
            uid = getattr(data.get("event_from_user"), "id", None)
            if uid:
                await users.mark_blocked(uid)
        except TelegramBadRequest as e:
            if "query is too old" in str(e) or "not modified" in str(e):
                return
            log.exception("Telegram bad request")
        except Exception:
            log.exception("Unhandled error in handler")
            try:
                if isinstance(event, CallbackQuery):
                    await safe_answer(event, "Something went wrong. Please try again.", alert=True)
                elif isinstance(event, Message):
                    await event.reply("⚠️ Something went wrong. Please try again in a moment.")
            except Exception:
                pass


class CoreMiddleware(BaseMiddleware):
    """Throttling, callback-replay guard, auto-registration, ban/mute/maintenance, group settings."""

    async def __call__(self, handler: Callable, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg = data.get("event_from_user")
        if tg is None or tg.is_bot:
            return await handler(event, data)
        in_group = isinstance(event, Message) and event.chat.type in ("group", "supergroup")
        if in_group and event.new_chat_members:
            for nm in event.new_chat_members:        # register new joiners instantly, before they ever write
                if not nm.is_bot:
                    try:
                        await users.touch(nm.id, nm.username, nm.full_name or nm.first_name)
                    except Exception:
                        pass
        if in_group:
            from services import events as _ev
            _ev.note_activity(event.chat.id)
        if in_group and not (event.text or "").startswith("/") and not (event.new_chat_members or event.left_chat_member):
            await users.note_seen(tg)          # remember who is in the group, so tagging them later always works
            # group chatter that isn't for the bot (no @mention / reply / its name): skip the rest of the DB work
            if "plain_mod" not in data and not addressed(event, await data["bot"].me())[0]:
                return
        if isinstance(event, Message) and admin_svc.role(tg.id) != "none":
            await ensure_scope(data["bot"], event.chat.id, tg.id, group=in_group)   # owner/mod '/' menu
        if isinstance(event, Message) and (event.text or "").startswith("/") and admin_svc.role(tg.id) != "owner":
            if not await _command_allowed(tg.id, event):
                return
        if await kv.incr_window(f"thr:{tg.id}", R.command_rate_window_s) > R.command_rate_max:
            if isinstance(event, CallbackQuery):
                await safe_answer(event, "Slow down a little ✋")
            return
        if isinstance(event, CallbackQuery):
            mid = event.message.message_id if event.message else 0
            if not await kv.set_nx(f"cb:{tg.id}:{mid}:{event.data}", 1.5):   # double-tap / replay
                await safe_answer(event)
                return
        user = await users.touch(tg.id, tg.username, tg.full_name or tg.first_name)
        if user.is_banned:
            if isinstance(event, Message) and event.chat.type == "private":
                await event.answer("🚫 Your account is suspended.")
            elif isinstance(event, CallbackQuery):
                await safe_answer(event, "Your account is suspended.", True)
            return
        if user.is_muted:
            return
        if await maintenance_on() and admin_svc.role(tg.id) == "none":
            if isinstance(event, Message) and event.chat.type == "private":
                await event.answer(f"🛠 {esc(brand.name())} is under maintenance. Back soon!")
            elif isinstance(event, CallbackQuery):
                await safe_answer(event, "Maintenance in progress.", True)
            return
        chat = data.get("event_chat")
        if chat and chat.type in ("group", "supergroup"):
            g = await users.touch_group(chat.id, chat.title)
            data["group"] = g
            text = event.text if isinstance(event, Message) else None
            if text and text.startswith("/") and not g.games_enabled and text.split()[0].split("@")[0].lower() not in GROUP_OK:
                return
        data["db_user"] = user
        return await handler(event, data)
