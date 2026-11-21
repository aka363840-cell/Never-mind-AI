"""🎭 Charades (describe it!) - group game.

/charades or /describe - start (you are the first host) · /end - end the game (host or group admin)
The host gets a secret word in DM and describes it in the group with emojis, clues or text (never the word itself).
Everybody else guesses by typing. After a correct guess a button lets anybody become the next host - and so it goes on.
Listed in /help, deliberately NOT in Telegram's '/' menu (utils/helpcatalog.MENU).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message, TelegramObject

from game import charades as C
from services import charades as cs
from services.errors import GameError
from utils import brand
from utils.fmt import esc, mention

log = logging.getLogger(__name__)
router = Router()


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def _arm(g: cs.Game, seconds: float, coro_fn) -> None:
    """Run coro_fn(g, token) after `seconds` unless the game moved on meanwhile."""
    if g.timer is not None and not g.timer.done():
        g.timer.cancel()
    g.token += 1
    tok = g.token

    async def run():
        try:
            await asyncio.sleep(seconds)
            if cs.GAMES.get(g.chat_id) is g and g.token == tok:
                await coro_fn(g, tok)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("charades timer failed")
    g.timer = asyncio.create_task(run())


def _drop(g: cs.Game) -> None:
    if g.timer is not None and not g.timer.done():
        g.timer.cancel()
    if cs.GAMES.get(g.chat_id) is g:
        del cs.GAMES[g.chat_id]


def _scores(g: cs.Game) -> str:
    rows = cs.board(g)
    if not rows:
        return ""
    medals = ["🥇", "🥈", "🥉"]
    return "\n\n🏆 <b>Scores</b>\n" + "\n".join(f"{medals[i] if i < 3 else '▫️'} {esc(n)} - {p}" for i, (n, p) in enumerate(rows[:8]))


def _host_kb(g: cs.Game) -> InlineKeyboardMarkup:
    return _kb([B(text="🎤 Become the host", callback_data=f"ch:host:{g.chat_id}")])


async def _end_game(bot: Bot, g: cs.Game, why: str) -> None:
    _drop(g)
    try:
        await bot.send_message(g.chat_id, f"🎭 <b>Charades is over</b> - {why}{_scores(g)}")
    except Exception:
        pass


async def _offer_host(bot: Bot, g: cs.Game, text: str) -> None:
    g.phase = "between"
    await bot.send_message(g.chat_id, f"{text}\n\n👇 Tap to become the <b>next host</b> (first tap wins, {C.HOST_WAIT_S // 60} min)." + _scores(g),
                           reply_markup=_host_kb(g))

    async def nobody(gg: cs.Game, tok: int):
        await _end_game(bot, gg, "nobody wanted to host.")
    _arm(g, C.HOST_WAIT_S, nobody)


# ------------------------------------------------------------------------------------------------------ a round
def _dm_text(g: cs.Game) -> str:
    return (f"🎭 <b>Your secret word:</b>\n\n<tg-spoiler>  {esc(g.word.upper())}  </tg-spoiler>\n\n"
            f"Describe it in the group with emojis, clues or text - <b>never type the word itself</b>!\n"
            f"The round lasts {C.ROUND_S // 60} min. Changes left: {C.MAX_CHANGES - g.changes}")


def _dm_kb(g: cs.Game) -> InlineKeyboardMarkup | None:
    if g.changes >= C.MAX_CHANGES:
        return None
    return _kb([B(text="🔄 Change word", callback_data=f"ch:new:{g.chat_id}")])


async def begin_round(bot: Bot, g: cs.Game) -> None:
    g.word = C.pick(g.used)
    g.used.add(g.word)
    g.changes = 0
    g.phase = "prep"
    try:
        await bot.send_message(g.host_id, _dm_text(g), reply_markup=_dm_kb(g))
    except Exception:
        link = brand.link("start=charades") if brand.username() else None
        rows = [[B(text="🤖 Open me in DM", url=link)]] if link else []
        rows.append([B(text="✅ I started the bot - retry", callback_data=f"ch:retry:{g.chat_id}")])
        await bot.send_message(g.chat_id, f"🎭 {mention(g.host_id, g.host_name)}, I can't send you the secret word yet!\n"
                                          f"Open me in DM, press <b>Start</b>, then tap <b>retry</b> ({C.DM_WAIT_S // 60} min).", reply_markup=_kb(*rows))

        async def too_slow(gg: cs.Game, tok: int):
            await _end_game(bot, gg, f"{esc(gg.host_name)} never opened the bot in DM.")
        _arm(g, C.DM_WAIT_S, too_slow)
        return
    g.phase, g.round = "live", g.round + 1
    await bot.send_message(g.chat_id, f"🎭 <b>Charades - round {g.round}</b>\n\n{mention(g.host_id, g.host_name)} is the host and got a secret word in DM.\n"
                                      f"They describe it with emojis, clues and text - <b>guess it by typing in the chat!</b> ⏱ {C.ROUND_S // 60} min")

    async def timeout(gg: cs.Game, tok: int):
        await _offer_host(bot, gg, f"⏰ Time's up! The word was <b>{esc(gg.word.upper())}</b>. Nobody got it.")
    _arm(g, C.ROUND_S, timeout)


# ------------------------------------------------------------------------------------------------------ commands
@router.message(Command("charades", "describe"))
async def start_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    if m.chat.type not in ("group", "supergroup"):
        raise GameError("🎭 Charades needs a group - start it there!")
    from services import wordgames as wg
    other = await wg.busy(m.chat.id)
    if other:
        raise GameError(f"🎮 A {other} game is running here - finish it or /end it first.")
    g = cs.Game(m.chat.id, m.from_user.id, m.from_user.full_name, m.from_user.id)
    cs.GAMES[m.chat.id] = g
    try:
        await begin_round(bot, g)
    except Exception:
        _drop(g)
        raise


def _has_game(m: Message) -> bool:
    return m.chat.id in cs.GAMES


async def _is_admin(bot: Bot, chat_id: int, uid: int) -> bool:
    from handlers.members import _is_admin as adm
    return await adm(bot, chat_id, uid)


@router.message(Command("end", "wend"), _has_game)
async def end_cmd(m: Message, bot: Bot):
    g = cs.GAMES.get(m.chat.id)
    if g is None or m.from_user is None:
        return
    if m.from_user.id not in (g.host_id, g.starter_id) and not await _is_admin(bot, m.chat.id, m.from_user.id):
        raise GameError("⛔ Only the host or a group admin can end Charades.")
    if g.phase == "live":
        await m.answer(f"🛑 The word was <b>{esc(g.word.upper())}</b>.")
    await _end_game(bot, g, f"ended by {esc(m.from_user.full_name)}.")


# ------------------------------------------------------------------------------------------------------ buttons
@router.callback_query(F.data.startswith("ch:retry:"))
async def retry_cb(cb: CallbackQuery, bot: Bot):
    g = cs.GAMES.get(int(cb.data.split(":")[2]))
    if g is None or g.phase != "prep":
        return await cb.answer("Nothing to retry.", show_alert=True)
    if cb.from_user.id != g.host_id:
        return await cb.answer("Only the host can do this.", show_alert=True)
    async with g.lock:
        if g.phase != "prep":
            return await cb.answer()
        await cb.answer()
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await begin_round(bot, g)


@router.callback_query(F.data.startswith("ch:new:"))
async def change_cb(cb: CallbackQuery, bot: Bot):
    g = cs.GAMES.get(int(cb.data.split(":")[2]))
    if g is None or g.phase != "live" or cb.from_user.id != g.host_id:
        return await cb.answer("This round is over.", show_alert=True)
    if g.changes >= C.MAX_CHANGES:
        return await cb.answer("No changes left!", show_alert=True)
    g.changes += 1
    g.word = C.pick(g.used)
    g.used.add(g.word)
    await cb.answer("New word!")
    try:
        await cb.message.edit_text(_dm_text(g), reply_markup=_dm_kb(g))
    except Exception:
        pass


@router.callback_query(F.data.startswith("ch:host:"))
async def host_cb(cb: CallbackQuery, bot: Bot):
    g = cs.GAMES.get(int(cb.data.split(":")[2]))
    if g is None or g.phase != "between":
        return await cb.answer("Too late - somebody else is hosting!", show_alert=True)
    async with g.lock:
        if g.phase != "between":
            return await cb.answer("Too late - somebody else is hosting!", show_alert=True)
        g.host_id, g.host_name = cb.from_user.id, cb.from_user.full_name
        g.phase = "prep"
        await cb.answer("You're the host!")
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await bot.send_message(g.chat_id, f"🎤 {mention(g.host_id, g.host_name)} is now the <b>host</b>!")
        await begin_round(bot, g)


# ------------------------------------------------------------------------------------------------------ guesses
class CharadesMiddleware(BaseMiddleware):
    """Plain group messages while a round runs: a correct guess wins, the host typing the word voids the round.
    Never blocks anything - the message always continues down the chain."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if (isinstance(event, Message) and event.text and event.from_user and not event.from_user.is_bot
                    and event.chat.id in cs.GAMES and not event.text.startswith("/")):
                g = cs.GAMES[event.chat.id]
                if g.phase == "live" and C.matches(event.text, g.word):
                    await self._hit(event, data["bot"], g)
        except Exception:
            log.exception("charades guess failed")
        return await handler(event, data)

    @staticmethod
    async def _hit(m: Message, bot: Bot, g: cs.Game) -> None:
        async with g.lock:
            if g.phase != "live":
                return
            word = g.word.upper()
            if m.from_user.id == g.host_id:                        # the host said the word: round void
                g.phase = "between"
                await _offer_host(bot, g, f"🚫 {mention(g.host_id, g.host_name)} typed the secret word <b>{esc(word)}</b> - round cancelled, no points!")
                return
            g.phase = "between"
            cs.add_points(g, m.from_user.id, m.from_user.full_name, C.GUESSER_POINTS)
            cs.add_points(g, g.host_id, g.host_name, C.HOST_POINTS)
            await _offer_host(bot, g, f"🎉 {mention(m.from_user.id, m.from_user.full_name)} guessed it! The word was <b>{esc(word)}</b>\n"
                                      f"+{C.GUESSER_POINTS} for the guesser · +{C.HOST_POINTS} for the host")
