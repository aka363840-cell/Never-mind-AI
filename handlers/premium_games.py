"""/uno, /ludo and /chess - open Premium Games tables. Private chat: opens the Mini App. Group: the Mini App server posts a LIVE lobby card
(who is waiting / playing / whose turn) and anyone can join from it. Only talks to the Ella Gaming server over HTTPS."""
from __future__ import annotations

import hashlib
import hmac
import logging

import aiohttp
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup, Message, WebAppInfo

from handlers.ella_app import app_url, direct_link

router = Router()
log = logging.getLogger(__name__)


@router.message(Command("uno", "ludo", "chess", ignore_case=True))
async def premium_cmd(m: Message, command: CommandObject):
    game, url = command.command.lower(), app_url()
    if not url:
        return await m.reply("🎮 Premium Games aren't switched on yet. The owner needs to set <code>ELLA_GAMING_URL</code>.")
    if m.chat.type == "private":
        sep = "&" if "?" in url else "?"
        kb = InlineKeyboardMarkup(inline_keyboard=[[B(text=f"👑 Open {game.upper()}", web_app=WebAppInfo(url=f"{url}{sep}pg={game}"))]])
        return await m.reply(f"👑 <b>Premium {game.upper()}</b>\nCreate a table and invite friends by link.", reply_markup=kb)
    from services import wordgames as wg
    if other := await wg.busy(m.chat.id):
        return await m.reply(f"🎮 A {other} game is running here - finish it or /endgame it first.")
    token = m.bot.token
    body = {"game": game, "chat_id": m.chat.id, "title": m.chat.title or "", "link": direct_link(),
            "user": {"id": m.from_user.id, "first_name": m.from_user.first_name or "", "last_name": m.from_user.last_name or "", "username": m.from_user.username or ""}}
    key = hmac.new(token.encode(), b"premium-bot", hashlib.sha256).hexdigest()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(url.rstrip("/") + "/api/premium/bot/create", json=body, headers={"X-Ella-Key": key}) as r:
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
    except Exception:
        log.exception("premium table create failed")
        await m.reply("⚠️ Couldn't open the table right now. Try again in a moment.")
