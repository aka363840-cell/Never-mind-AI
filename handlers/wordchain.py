"""Word Chain - turn-based elimination game for groups.  Say a word that starts with the last letter of the previous word.
Difficulty increases over time (longer words, shorter clock).  Last player standing wins.

/wordchain or /wc - open a lobby · /join - join it · /forcestart - start early · /end - end the running game (WordSeek or Word Chain)
While it is your turn just type the word in the chat - no command needed.
These commands are listed in /help but deliberately NOT in Telegram's '/' menu (utils/helpcatalog.MENU).
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message, TelegramObject

from game import wordchain as W
from services import wordchain as wc, wordseek as ws
from services.errors import GameError
from utils.fmt import esc

log = logging.getLogger(__name__)
router = Router()


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="➕ Join the game", callback_data="wc:join")]])


def _sender(bot: Bot, chat_id: int) -> Callable[[str], Awaitable[Any]]:
    async def send(text: str):
        return await bot.send_message(chat_id, text, disable_web_page_preview=True)
    return send


async def _is_admin(bot: Bot, chat_id: int, uid: int) -> bool:
    from handlers.members import _is_admin as adm
    return await adm(bot, chat_id, uid)


async def _may_control(bot: Bot, g: wc.Game, chat_id: int, uid: int) -> bool:
    return uid == g.host_id or await _is_admin(bot, chat_id, uid)


async def _refresh_lobby(bot: Bot, g: wc.Game) -> None:
    """Keep the lobby message's player list up to date (best effort)."""
    if g.lobby_msg is None or g.state != "lobby":
        return
    try:
        await bot.edit_message_text(wc.lobby_text(g), chat_id=g.chat_id, message_id=g.lobby_msg, reply_markup=_kb())
    except Exception:
        pass


@router.message(Command("wordchain", "wc"))
async def open_lobby(m: Message, bot: Bot):
    if m.from_user is None:
        return
    if m.chat.type == "private":
        raise GameError("⛓ Word Chain needs at least 2 players - start it in a group!")
    from services import wordgames as wg
    if wg.running(m.chat.id):
        raise GameError("🎮 A Wordly / Hack game is running here - /end it first.")
    cur = await ws.get(m.chat.id)                                 # one game per chat: WordSeek reads plain words too
    if cur is not None and ws.idle_for(cur) < ws.IDLE_REPLACE_S:
        raise GameError("🔎 A WordSeek game is running here - finish it or /end it first.")
    if cur is not None:
        await ws.finish(cur)
    g = wc.create(m.chat.id, m.from_user.id, m.from_user.full_name)
    msg = await m.answer(wc.lobby_text(g), reply_markup=_kb())
    g.lobby_msg = msg.message_id
    wc.start_lobby_timer(g, _sender(bot, m.chat.id))


@router.message(Command("join"))
async def join_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    g = wc.GAMES.get(m.chat.id)
    if g is None:
        raise GameError("ℹ️ There's no Word Chain lobby open. Start one with /wc")
    n = wc.join(g, m.from_user.id, m.from_user.full_name)
    await m.reply(f"✅ <b>{esc(m.from_user.full_name)}</b> joined Word Chain - {n} player{'s' if n != 1 else ''} in the lobby.")
    await _refresh_lobby(bot, g)


@router.callback_query(F.data == "wc:join")
async def join_cb(cb: CallbackQuery, bot: Bot):
    g = wc.GAMES.get(cb.message.chat.id) if cb.message else None
    if g is None or g.state != "lobby":
        return await cb.answer("This lobby is closed.", show_alert=True)
    try:
        n = wc.join(g, cb.from_user.id, cb.from_user.full_name)
    except GameError as e:
        return await cb.answer(str(e).lstrip("✅⏳👥 ")[:190], show_alert=True)
    await cb.answer(f"You're in! {n} players")
    await _refresh_lobby(bot, g)


@router.message(Command("forcestart"))
async def forcestart_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    g = wc.GAMES.get(m.chat.id)
    if g is None or g.state != "lobby":
        raise GameError("ℹ️ There's no Word Chain lobby to start. Open one with /wc")
    if not await _may_control(bot, g, m.chat.id, m.from_user.id):
        raise GameError("⛔ Only the person who opened the lobby or a group admin can force-start.")
    await wc.start(g, _sender(bot, m.chat.id))


def _has_game(m: Message) -> bool:
    return m.chat.id in wc.GAMES


# Registered before the WordSeek router: with a Word Chain game running /end is ours, otherwise it falls through to WordSeek.
@router.message(Command("end", "wend"), _has_game)
async def end_cmd(m: Message, bot: Bot):
    g = wc.GAMES.get(m.chat.id)
    if g is None or m.from_user is None:
        return
    if not await _may_control(bot, g, m.chat.id, m.from_user.id):
        raise GameError("⛔ Only the person who started the game or a group admin can end it.")
    await wc.end(g, _sender(bot, m.chat.id), m.from_user.full_name)


class WordChainMiddleware(BaseMiddleware):
    """While a Word Chain game runs, a plain one-word message from the player whose turn it is = their move.
    Everybody else's messages (and commands) pass through untouched."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if (isinstance(event, Message) and event.text and event.from_user and not event.from_user.is_bot
                    and event.chat.id in wc.GAMES and W.is_candidate(event.text)):
                g = wc.GAMES[event.chat.id]
                if g.state == "playing":
                    mv = await wc.submit(g, event.from_user.id, event.text, _sender(data["bot"], event.chat.id))
                    if mv.handled:
                        if mv.error:
                            await event.reply(mv.error)
                        return None
        except Exception:
            log.exception("wordchain move failed")
        return await handler(event, data)
