"""Word Chain - turn-based elimination game for groups (modelled on the popular Telegram Word Chain bots).
Say a word that starts with the last letter of the previous word. Difficulty rises over time. Last player standing wins.

START A GAME (each opens a lobby in its own mode - see /wcmodes):
  /startclassic (/wordchain /wc) · /starthard · /startchosenfirstletter [letter] (/startcfl) · /startbannedletters (/startbl)
  /startrequiredletter (/startrl) · /startelimination (/startelim)
IN THE LOBBY / DURING THE GAME:
  /join · /flee · /extend [seconds] · /forcestart · /forcejoin (reply) · /forceskip · /wcstatus · /end
STATS:  /wcstats [global|group] [period] · /wclb [global|group] [period]   HELP: /wchelp · /wcmodes
While it is your turn just type the word in the chat - no command needed (the bot puts a 👍 on accepted words).

ALL of these commands are listed in /help but deliberately NOT in Telegram's '/' menu (utils/helpcatalog.MENU).
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message, ReactionTypeEmoji, TelegramObject

from game import wordchain as W
from services import wordchain as wc, wordseek as ws
from services.errors import GameError
from utils.fmt import esc, num

log = logging.getLogger(__name__)
router = Router()

REACTION = "👍"


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="➕ Join", callback_data="wc:join"), B(text="🚪 Leave", callback_data="wc:flee")],
        [B(text="▶️ Start now", callback_data="wc:start"), B(text=f"⏱ +{W.LOBBY_EXTEND_S}s", callback_data="wc:ext")],
    ])


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
    """Keep the lobby message's player list / countdown up to date (best effort)."""
    if g.lobby_msg is None or g.state != "lobby":
        return
    try:
        await bot.edit_message_text(wc.lobby_text(g), chat_id=g.chat_id, message_id=g.lobby_msg, reply_markup=_kb())
    except Exception:
        pass


async def _freeze_lobby(bot: Bot, g: wc.Game) -> None:
    """The game started: the lobby message loses its buttons (best effort)."""
    if g.lobby_msg is None:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=g.chat_id, message_id=g.lobby_msg, reply_markup=None)
    except Exception:
        pass


async def _save_game(g: wc.Game, winner: wc.Player | None) -> None:
    from services import wordchain_stats as st
    await st.record_game(g, winner)


wc.recorder = _save_game                                         # finished games -> /wcstats and /wclb


# ------------------------------------------------------------------------------------------- lobbies

async def _open(m: Message, bot: Bot, mode: str, arg: str = "") -> None:
    if m.from_user is None:
        return
    if m.chat.type == "private":
        raise GameError("⛓ Word Chain needs at least 2 players - start it in a group!")
    if m.chat.id in wc.GAMES:
        raise GameError("🎮 A Word Chain game is already " + ("running" if wc.GAMES[m.chat.id].state == "playing" else "open") + " here. /end stops it.")
    from services import wordgames as wg
    if wg.running(m.chat.id):
        raise GameError("🎮 A Wordly / Hack game is running here - /end it first.")
    cur = await ws.get(m.chat.id)                                 # one game per chat: WordSeek reads plain words too
    if cur is not None and ws.idle_for(cur) < ws.IDLE_REPLACE_S:
        raise GameError("🔎 A WordSeek game is running here - finish it or /end it first.")
    g = wc.create(m.chat.id, m.from_user.id, m.from_user.full_name, mode, arg)     # may raise GameError (bad letter ...)
    if cur is not None:
        await ws.finish(cur)
    try:
        msg = await m.answer(wc.lobby_text(g), reply_markup=_kb())
    except Exception:
        wc.GAMES.pop(m.chat.id, None)                             # could not post the lobby: do not leave a ghost game behind
        raise
    g.lobby_msg = msg.message_id
    wc.start_lobby_timer(g, _sender(bot, m.chat.id))


@router.message(Command("startclassic", "wordchain", "wc"))
async def start_classic(m: Message, bot: Bot):
    await _open(m, bot, "classic")


@router.message(Command("starthard", "wchard"))
async def start_hard(m: Message, bot: Bot):
    await _open(m, bot, "hard")


@router.message(Command("startchosenfirstletter", "startcfl"))
async def start_cfl(m: Message, bot: Bot, command: CommandObject):
    await _open(m, bot, "chosenfirst", (command.args or "").strip()[:1])


@router.message(Command("startbannedletters", "startbl"))
async def start_banned(m: Message, bot: Bot):
    await _open(m, bot, "banned")


@router.message(Command("startrequiredletter", "startrl"))
async def start_required(m: Message, bot: Bot):
    await _open(m, bot, "required")


@router.message(Command("startelimination", "startelim"))
async def start_elim(m: Message, bot: Bot):
    await _open(m, bot, "elimination")


def _lobby(chat_id: int) -> wc.Game:
    g = wc.GAMES.get(chat_id)
    if g is None or g.state != "lobby":
        raise GameError("ℹ️ There's no Word Chain lobby open. Start one with /wc")
    return g


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


@router.message(Command("forcejoin"))
async def forcejoin_cmd(m: Message, bot: Bot):
    """Host / admins: add the person you reply to."""
    if m.from_user is None:
        return
    g = _lobby(m.chat.id)
    if not await _may_control(bot, g, m.chat.id, m.from_user.id):
        raise GameError("⛔ Only the person who opened the lobby or a group admin can add players.")
    t = m.reply_to_message.from_user if m.reply_to_message and m.reply_to_message.from_user else None
    if t is None or t.is_bot:
        raise GameError("↩️ Reply to the person you want to add with /forcejoin")
    n = wc.join(g, t.id, t.full_name)
    await m.reply(f"✅ <b>{esc(t.full_name)}</b> was added - {n} players in the lobby.")
    await _refresh_lobby(bot, g)


@router.message(Command("flee"))
async def flee_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    g = wc.GAMES.get(m.chat.id)
    if g is None:
        raise GameError("ℹ️ There's no Word Chain game here.")
    text = await wc.leave(g, m.from_user.id, _sender(bot, m.chat.id))
    if text:
        await m.reply(text)
    await _refresh_lobby(bot, g)


@router.message(Command("extend"))
async def extend_cmd(m: Message, command: CommandObject, bot: Bot):
    if m.from_user is None:
        return
    g = _lobby(m.chat.id)
    if not await _may_control(bot, g, m.chat.id, m.from_user.id):
        raise GameError("⛔ Only the person who opened the lobby or a group admin can extend it.")
    arg = (command.args or "").strip()
    left = wc.extend(g, int(arg) if arg.isdigit() else 0)
    await m.reply(f"⏱ Lobby extended - the game starts in about <b>{left}s</b>.")
    await _refresh_lobby(bot, g)


@router.message(Command("forcestart"))
async def forcestart_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    g = _lobby(m.chat.id)
    if not await _may_control(bot, g, m.chat.id, m.from_user.id):
        raise GameError("⛔ Only the person who opened the lobby or a group admin can force-start.")
    await wc.start(g, _sender(bot, m.chat.id))
    await _freeze_lobby(bot, g)


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


@router.callback_query(F.data == "wc:flee")
async def flee_cb(cb: CallbackQuery, bot: Bot):
    g = wc.GAMES.get(cb.message.chat.id) if cb.message else None
    if g is None or g.state != "lobby":
        return await cb.answer("This lobby is closed.", show_alert=True)
    try:
        await wc.leave(g, cb.from_user.id, _sender(bot, cb.message.chat.id))
    except GameError:
        return await cb.answer("You're not in this lobby.", show_alert=True)
    await cb.answer("You left the lobby.")
    await _refresh_lobby(bot, g)


@router.callback_query(F.data == "wc:ext")
async def ext_cb(cb: CallbackQuery, bot: Bot):
    g = wc.GAMES.get(cb.message.chat.id) if cb.message else None
    if g is None or g.state != "lobby":
        return await cb.answer("This lobby is closed.", show_alert=True)
    if not await _may_control(bot, g, cb.message.chat.id, cb.from_user.id):
        return await cb.answer("Only the host or a group admin can extend the lobby.", show_alert=True)
    try:
        left = wc.extend(g)
    except GameError as e:
        return await cb.answer(str(e).lstrip("⏳ ")[:190], show_alert=True)
    await cb.answer(f"+{W.LOBBY_EXTEND_S}s - starts in {left}s")
    await _refresh_lobby(bot, g)


@router.callback_query(F.data == "wc:start")
async def start_cb(cb: CallbackQuery, bot: Bot):
    g = wc.GAMES.get(cb.message.chat.id) if cb.message else None
    if g is None or g.state != "lobby":
        return await cb.answer("This lobby is closed.", show_alert=True)
    if not await _may_control(bot, g, cb.message.chat.id, cb.from_user.id):
        return await cb.answer("Only the host or a group admin can start the game.", show_alert=True)
    await wc.start(g, _sender(bot, cb.message.chat.id))           # GameError (too few players ...) -> shown as an alert
    await cb.answer("Game started!")
    await _freeze_lobby(bot, g)


# ------------------------------------------------------------------------------------- in the game

def _has_game(m: Message) -> bool:
    return m.chat.id in wc.GAMES


@router.message(Command("forceskip"))
async def forceskip_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    g = wc.GAMES.get(m.chat.id)
    if g is None or g.state != "playing":
        raise GameError("ℹ️ There's no running Word Chain game to skip in.")
    if not await _may_control(bot, g, m.chat.id, m.from_user.id):
        raise GameError("⛔ Only the person who started the game or a group admin can skip a player.")
    await wc.skip(g, _sender(bot, m.chat.id), m.from_user.full_name)


@router.message(Command("wcstatus", "wcinfo"))
async def status_cmd(m: Message):
    g = wc.GAMES.get(m.chat.id)
    if g is None:
        raise GameError("ℹ️ There's no Word Chain game here. Start one with /wc")
    await m.reply(wc.status_text(g))


# Registered before the WordSeek router: with a Word Chain game running /end is ours, otherwise it falls through to WordSeek.
@router.message(Command("end", "wend"), _has_game)
async def end_cmd(m: Message, bot: Bot):
    g = wc.GAMES.get(m.chat.id)
    if g is None or m.from_user is None:
        return
    if not await _may_control(bot, g, m.chat.id, m.from_user.id):
        raise GameError("⛔ Only the person who started the game or a group admin can end it.")
    await wc.end(g, _sender(bot, m.chat.id), m.from_user.full_name)


# ----------------------------------------------------------------------------------- help & stats

@router.message(Command("wcmodes"))
async def modes_cmd(m: Message):
    rows = "\n\n".join(f"{s.emoji} <b>{s.name}</b>\n{s.blurb}\n<code>/{s.commands[0]}</code>" + (f" · /{s.commands[1]}" if len(s.commands) > 1 else "") for s in W.MODES.values())
    await m.reply("⛓ <b>Word Chain modes</b>\n\n" + rows + "\n\n<i>Chosen first letter: /startcfl k picks the letter yourself.</i>")


@router.message(Command("wchelp"))
async def help_cmd(m: Message):
    await m.reply(
        "⛓ <b>Word Chain</b> - say a word that starts with the <b>last letter</b> of the previous word. "
        "Too slow, a wrong word or a repeated word and you lose your turn - last player standing wins!\n\n"
        "<b>Start a game</b> (groups)\n"
        "/startclassic (/wc) · /starthard · /startchosenfirstletter [letter] · /startbannedletters · /startrequiredletter · /startelimination\n"
        "<i>See /wcmodes for what each mode does.</i>\n\n"
        "<b>Lobby</b>\n/join · /flee (leave) · /extend [seconds] (host/admin) · /forcestart (host/admin) · /forcejoin (reply, host/admin)\n\n"
        "<b>During the game</b>\nJust type your word when it's your turn - a 👍 means it counted.\n"
        "/wcstatus - who's alive, whose turn, last words\n/flee - give up · /forceskip - knock out the current player (host/admin) · /end - stop the game (host/admin)\n\n"
        "<b>Stats</b>\n/wcstats [global|group] [today|week|month|all] - your stats (reply to see someone else's)\n/wclb [global|group] [today|week|month|all] - leaderboard\n\n"
        "Words must be real English words and can't be used twice. Classic mode: the words get longer and the clock shorter as the game goes on.")


_PERIODS = {"today": "today", "day": "today", "week": "week", "weekly": "week", "month": "month", "monthly": "month", "all": "all", "alltime": "all"}
_NICE = {"today": "today", "week": "last 7 days", "month": "last 30 days", "all": "all time"}


def _opts(args: str, in_group: bool) -> tuple[bool, str]:
    """(global?, period) from free-form words like 'global week'."""
    glob, period = (not in_group), "all"
    for t in args.lower().split():
        if t in ("global", "world"):
            glob = True
        elif t in ("group", "chat", "here"):
            glob = False
        elif t in _PERIODS:
            period = _PERIODS[t]
    return glob, period


@router.message(Command("wcstats", "wcscore"))
async def stats_cmd(m: Message, command: CommandObject):
    from services import wordchain_stats as st
    if m.from_user is None:
        return
    t = m.reply_to_message.from_user if m.reply_to_message and m.reply_to_message.from_user else m.from_user
    glob, period = _opts(command.args or "", m.chat.type != "private")
    s = await st.stats(t.id, None if glob else m.chat.id, period)
    scope = f"{'global' if glob else 'this group'} · {_NICE[period]}"
    if not s["games"]:
        await m.reply(f"📊 {esc(t.full_name)} hasn't finished a Word Chain game yet ({scope}). Start one with /wc")
        return
    rate = 100 * s["wins"] / s["games"]
    avg = s["letters"] / s["words"] if s["words"] else 0
    await m.reply(f"📊 <b>{esc(t.full_name)}</b> · Word Chain · {scope}\n"
                  f"🎮 Games played: <b>{s['games']}</b>\n🏆 Games won: <b>{s['wins']}</b> ({rate:.0f}%)\n"
                  f"📝 Words: <b>{num(s['words'])}</b> · 🔤 Letters: <b>{num(s['letters'])}</b> (avg {avg:.1f} per word)\n"
                  + (f"📏 Longest word: <b>{esc(s['longest']).upper()}</b> ({len(s['longest'])} letters)\n" if s["longest"] else "")
                  + (f"🥇 Rank: <b>#{s['rank']}</b>" if s["rank"] else ""))


@router.message(Command("wclb", "wctop", "wcleaderboard"))
async def lb_cmd(m: Message, command: CommandObject):
    from services import wordchain_stats as st
    glob, period = _opts(command.args or "", m.chat.type != "private")
    rows = await st.board(None if glob else m.chat.id, period)
    head = f"🏆 <b>Word Chain leaderboard</b> · {'global' if glob else 'this group'} · {_NICE[period]}\n\n"
    if not rows:
        await m.reply(head + "Nobody has finished a game yet. Start with /wc !")
        return
    medals = ["🥇", "🥈", "🥉"]
    body = "\n".join(f"{medals[i] if i < 3 else f'<b>{i + 1}.</b>'} {esc(n[:20])} - <b>{w}</b> win{'s' if w != 1 else ''} · {g} game{'s' if g != 1 else ''} · {num(wd)} words"
                     for i, (_u, n, w, g, wd) in enumerate(rows))
    await m.reply(head + body + "\n\n<i>Try: /wclb global week · /wclb group today · /wclb month</i>")


# --------------------------------------------------------------------------------------- the moves

class WordChainMiddleware(BaseMiddleware):
    """While a Word Chain game runs, a plain one-word message from the player whose turn it is = their move.
    Everybody else's messages (and commands) pass through untouched."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if (isinstance(event, Message) and event.text and event.from_user and not event.from_user.is_bot
                    and event.chat.id in wc.GAMES and W.is_candidate(event.text)):
                g = wc.GAMES[event.chat.id]
                if g.state == "playing":
                    bot: Bot = data["bot"]

                    async def react() -> bool:                    # 👍 on the player's word (the bot may not have the right to react)
                        try:
                            await bot.set_message_reaction(chat_id=event.chat.id, message_id=event.message_id, reaction=[ReactionTypeEmoji(emoji=REACTION)])
                            return True
                        except Exception:
                            return False

                    mv = await wc.submit(g, event.from_user.id, event.text, _sender(bot, event.chat.id), react)
                    if mv.handled:
                        if mv.error:
                            await event.reply(mv.error)
                        return None
        except Exception:
            log.exception("wordchain move failed")
        return await handler(event, data)
