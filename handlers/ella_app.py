"""/app - launcher for Ella Gaming, the separate free HTML5 games Mini App.

This bot is ONLY the launcher: nothing here reads or writes the bot's economy, XP, wallet or database. The Mini App has its
own server and database (see ella_gaming/). The only thing the two share is the bot token, which Telegram uses to sign the
Mini App login.

Two ways in, both open the same URL (ELLA_GAMING_URL):
  * /app                        -> a message with an "Open Ella Gaming" Mini App button
  * the "🎮 Ella Gaming" button on the 🕹 Games hub (reachable from /start)  -> the same button, edited in place
The BotFather "Main Mini App" / menu button (see ella_gaming/README.md) opens the same URL too.

Telegram only allows Mini App ("web_app") inline buttons in PRIVATE chats. In groups the button becomes a normal link:
ELLA_GAMING_LINK (e.g. https://t.me/YourBot/play, a BotFather direct link) if set, otherwise a link to the bot's DM.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message, WebAppInfo

from keyboards.menus import kb
from utils.telegram import safe_answer, safe_edit

router = Router()

TITLE = "🎮 <b>𝑬𝒍𝒍𝒂 𝑮𝒂𝒎𝒊𝒏𝒈</b>\n<i>Hundreds of free games, one tap away.</i>\n\n♡ Save your favourites\n♡ Pick up right where you left off"
NOT_READY = "🎮 <b>Ella Gaming</b> isn't switched on yet. The owner needs to set <code>ELLA_GAMING_URL</code>."


def app_url() -> str:
    """The https URL the Mini App is served from, or '' when it is not configured / not a valid https URL."""
    url = os.getenv("ELLA_GAMING_URL", "").strip()
    p = urlparse(url)
    return url if p.scheme == "https" and p.netloc else ""


def direct_link() -> str:
    link = os.getenv("ELLA_GAMING_LINK", "").strip()
    p = urlparse(link)
    return link if p.scheme == "https" and p.netloc in ("t.me", "telegram.me") else ""


def launch_markup(private: bool, bot_username: str = "", back_row: list[B] | None = None) -> InlineKeyboardMarkup | None:
    """The button that opens Ella Gaming. None when the app is not configured."""
    url = app_url()
    if not url:
        return None
    if private:
        row = [B(text="🎮 Open Ella Gaming", web_app=WebAppInfo(url=url))]
    else:
        link = direct_link() or (f"https://t.me/{bot_username}" if bot_username else "")
        if not link:
            return None
        row = [B(text="🎮 Open Ella Gaming", url=link)]
    rows = [row]
    if back_row:
        rows.append(back_row)
    return kb(rows)


def page_text(private: bool) -> str:
    return TITLE if private else TITLE + "\n\n<i>✦ Opens in our private chat, tap below to play.</i>"


@router.message(Command("app"))
async def app_cmd(m: Message):
    private = m.chat.type == "private"
    username = (await m.bot.me()).username or "" if not private else ""
    markup = launch_markup(private, username)
    if markup is None:
        await m.reply(NOT_READY)
        return
    await m.reply(page_text(private), reply_markup=markup)


@router.callback_query(F.data == "ella:app")
async def app_from_hub(cb: CallbackQuery):
    """The 🎮 Ella Gaming button on the Games hub: same page as /app, edited in place with a way back to the hub."""
    private = cb.message.chat.type == "private"
    username = (await cb.bot.me()).username or "" if not private else ""
    markup = launch_markup(private, username, back_row=[B(text="⬅️ Games", callback_data="ar:hub")])
    if markup is None:
        await safe_answer(cb, "Ella Gaming isn't switched on yet.", alert=True)
        return
    await safe_edit(cb, page_text(private), markup)
    await safe_answer(cb)
