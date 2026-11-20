"""WordSeek - the group word game. /new starts it; then everybody just types 5-letter words in the chat.
🟩 right letter, right place · 🟨 right letter, wrong place · 🟥 not in the word.  First to find the word wins.
/new (5 letters) · /new4 · /new5 · /new6 pick the word length."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message, TelegramObject

from config import get_settings
from game import wordseek as W
from services import users, wordseek as ws
from services.errors import GameError
from utils.fmt import esc, mention, num

log = logging.getLogger(__name__)
router = Router()
SHOW = 14                                    # guesses shown on the running board (older ones are summarised)
PERIODS = {"today": "today", "day": "today", "week": "this week", "weekly": "this week", "month": "this month", "monthly": "this month", "all": "all time", "alltime": "all time"}
_ARGS = {"today": "today", "day": "today", "week": "week", "weekly": "week", "month": "month", "monthly": "month", "all": "all", "alltime": "all"}
_NICE = {"today": "today", "week": "this week", "month": "this month", "all": "all time"}


def _lines(st: ws.State, last: int | None = None) -> str:
    rows = st.guesses if last is None else st.guesses[-last:]
    out = []
    for word, _uid, name in rows:
        out.append(f"{W.squares(W.feedback(st.word, word))} <code>{word.upper()}</code> · {esc(name[:14])}")
    hidden = len(st.guesses) - len(rows)
    return (f"<i>… {hidden} earlier guesses</i>\n" if hidden else "") + "\n".join(out)


def _board(st: ws.State) -> str:
    n = len(st.guesses)
    absent = W.absent_letters(st.word, [g[0] for g in st.guesses])
    return (f"🔎 <b>WordSeek</b> · {len(st.word)} letters · guess <b>{n}/{W.max_guesses(len(st.word))}</b>\n\n{_lines(st, SHOW)}"
            + (f"\n\n🚫 Not in the word: <code>{' '.join(c.upper() for c in absent)}</code>" if absent else "")
            + f"\n\n<i>Type any {len(st.word)}-letter word to guess.</i>")


def _again(st: "ws.State") -> InlineKeyboardMarkup:
    n = len(st.word)
    return InlineKeyboardMarkup(inline_keyboard=[[B(text=f"🔁 Play again ({n} letters)", callback_data=f"ws:new{n}")]])


def _intro(n: int) -> str:
    return (f"I picked a hidden <b>{n}-letter word</b>. Everybody can guess: just type a {n}-letter word in the chat.\n"
            "🟩 right letter, right place\n🟨 right letter, wrong place\n🟥 letter is not in the word\n\n"
            f"You have <b>{W.max_guesses(n)}</b> guesses together. The first to find the word wins points! 🏆")


async def _is_admin(bot: Bot, m: Message) -> bool:
    if m.chat.type == "private":
        return True
    from handlers.members import _is_admin as adm
    return await adm(bot, m.chat.id, m.from_user.id)


async def _start(m: Message, bot: Bot, length: int = W.DEFAULT_LEN) -> None:
    from services import wordchain as wc, wordgames as wg      # Wordly / Hack / Word Chain: one game per chat
    if m.chat.id in wc.GAMES:
        raise GameError("⛓ A Word Chain game is running here - finish it or /end it first.")
    if wg.running(m.chat.id):
        await m.reply("🎮 A Wordly / Hack game is running here - /end it first.")
        return
    cur = await ws.get(m.chat.id)
    note = ""
    if cur is not None:
        if ws.idle_for(cur) < ws.IDLE_REPLACE_S:
            await m.reply("🎮 A game is already running here!\n\n" + _board(cur))
            return
        note = f"⌛ The old game was left unfinished - the word was <b>{cur.word.upper()}</b>.\n\n"
        await ws.finish(cur)
    st = await ws.start(m.chat.id, m.from_user.id, length)
    await m.answer(note + "🔎 <b>WordSeek</b> - a new game has started!\n\n" + _intro(length))


def _len_of(command: CommandObject) -> int:
    d = (command.command or "")[-1:]
    return int(d) if d.isdigit() and int(d) in W.LENGTHS else W.DEFAULT_LEN


@router.message(Command("new", "wordseek", "wnew", "new4", "new5", "new6"))
async def new_cmd(m: Message, command: CommandObject, bot: Bot):
    if m.from_user is None:
        return
    await _start(m, bot, _len_of(command))


@router.callback_query(F.data.startswith("ws:new"))
async def new_cb(cb: CallbackQuery, bot: Bot):
    from services import wordchain as wc, wordgames as wg
    if cb.message.chat.id in wc.GAMES:
        return await cb.answer("A Word Chain game is running here!", show_alert=True)
    if wg.running(cb.message.chat.id):
        return await cb.answer("A Wordly / Hack game is running here - /end it first.", show_alert=True)
    tail = cb.data[6:]
    length = int(tail) if tail.isdigit() and int(tail) in W.LENGTHS else W.DEFAULT_LEN
    cur = await ws.get(cb.message.chat.id)
    if cur is not None and ws.idle_for(cur) < ws.IDLE_REPLACE_S:
        return await cb.answer("A game is already running!", show_alert=True)
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    if cur is not None:
        await ws.finish(cur)
    await ws.start(cb.message.chat.id, cb.from_user.id, length)
    await cb.message.answer(f"🔎 <b>WordSeek</b> - new game started by {mention(cb.from_user.id, cb.from_user.full_name)}!\n\n" + _intro(length))


@router.message(Command("end", "wend"))
async def end_cmd(m: Message, bot: Bot):
    st = await ws.get(m.chat.id)
    if st is None:
        raise GameError("ℹ️ There's no WordSeek game running. Start one with /new")
    if not await _is_admin(bot, m):
        raise GameError("⛔ Only group admins can end the game.")
    await ws.finish(st)
    await m.reply(f"🛑 Game ended. The word was <b>{st.word.upper()}</b>.\n\n{_lines(st, SHOW) if st.guesses else ''}".strip(), reply_markup=_again(st))


async def _guess(m: Message, bot: Bot, st: ws.State) -> bool:
    """Handle one guess. True = the message was used as a guess."""
    async with st.lock:
        if await ws.get(st.chat_id) is not st:                 # the game ended while we waited for the lock
            return False
        word, uid, name = m.text.strip().lower(), m.from_user.id, m.from_user.full_name
        if any(g[0] == word for g in st.guesses):
            await m.reply(f"⚠️ <code>{word.upper()}</code> was already guessed.")
            return True
        if m.chat.type != "private":
            try:
                await users.note_member(m.chat.id, uid)
            except Exception:
                pass
        await ws.add_guess(st, word, uid, name)
        old = st.board_msg
        if word == st.word:
            await users.touch(uid, m.from_user.username, name)
            pts = await ws.finish(st, (uid, name))
            msg = await m.reply(f"🎉 <b>{mention(uid, name)}</b> found the word <b>{word.upper()}</b> in <b>{len(st.guesses)}</b> guesses!\n"
                                f"⭐ <b>+{pts}</b> points\n\n{_lines(st)}", reply_markup=_again(st))
        elif len(st.guesses) >= W.max_guesses(len(st.word)):
            await ws.finish(st)
            msg = await m.reply(f"💀 Out of guesses! The word was <b>{st.word.upper()}</b>.\n\n{_lines(st, SHOW)}", reply_markup=_again(st))
        else:
            msg = await m.reply(_board(st))
            st.board_msg = msg.message_id
        if old:
            try:
                await bot.delete_message(m.chat.id, old)       # keep the chat tidy: only the newest board stays
            except Exception:
                pass
        return True


class WordSeekMiddleware(BaseMiddleware):
    """Plain 5-letter messages are guesses while a game runs in that chat. Everything else passes through untouched."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if isinstance(event, Message) and event.text and event.from_user and not event.from_user.is_bot and W.is_candidate(event.text):
                st = await ws.get(event.chat.id)
                if st is not None and len(event.text.strip()) == len(st.word) and W.is_valid(event.text) and await _guess(event, data["bot"], st):
                    return None
        except Exception:
            log.exception("wordseek guess failed")
        return await handler(event, data)


def _opts(args: str, in_group: bool) -> tuple[bool, str]:
    """(global?, period) from free-form words like 'global week'."""
    glob, period = (not in_group), "all"
    for t in args.lower().split():
        if t in ("global", "world", "all-groups"):
            glob = True
        elif t in ("group", "chat", "here"):
            glob = False
        elif t in _ARGS:
            period = _ARGS[t]
    return glob, period


@router.message(Command("wlb", "wleaderboard", "wordtop"))
async def lb_cmd(m: Message, command: CommandObject):
    glob, period = _opts(command.args or "", m.chat.type != "private")
    rows = await ws.board(None if glob else m.chat.id, period)
    head = f"🏆 <b>WordSeek leaderboard</b> · {'global' if glob else 'this group'} · {_NICE[period]}\n\n"
    if not rows:
        await m.reply(head + "Nobody has won yet. Start with /new !")
        return
    medals = ["🥇", "🥈", "🥉"]
    body = "\n".join(f"{medals[i] if i < 3 else f'<b>{i + 1}.</b>'} {esc(n[:20])} - <b>{num(p)}</b> pts · {w} win{'s' if w != 1 else ''}" for i, (_u, n, p, w) in enumerate(rows))
    await m.reply(head + body + "\n\n<i>Try: /wlb global week · /wlb group today · /wlb month</i>")


@router.message(Command("myscore", "wscore", "score"))
async def myscore_cmd(m: Message, command: CommandObject):
    t = m.reply_to_message.from_user if m.reply_to_message and m.reply_to_message.from_user else m.from_user
    glob, period = _opts(command.args or "", m.chat.type != "private")
    s = await ws.score(t.id, None if glob else m.chat.id, period)
    if not s["wins"]:
        await m.reply(f"📊 {esc(t.full_name)} has no WordSeek wins yet ({'global' if glob else 'this group'}, {_NICE[period]}).")
        return
    await m.reply(f"📊 <b>{esc(t.full_name)}</b> · WordSeek · {'global' if glob else 'this group'} · {_NICE[period]}\n"
                  f"⭐ Points: <b>{num(s['points'])}</b>\n🏆 Wins: <b>{s['wins']}</b>\n🎯 Average guesses per win: <b>{s['avg']:.1f}</b>\n"
                  + (f"🥇 Rank: <b>#{s['rank']}</b>" if s["rank"] else ""))


@router.message(Command("wordstats"))
async def stats_cmd(m: Message):
    if m.from_user is None or m.from_user.id != get_settings().owner_id:
        raise GameError("⛔ Not authorised.")
    t = await ws.totals()
    await m.reply(f"🔎 <b>WordSeek stats</b>\n🎮 Running now: {t['running']}\n✅ Games finished: {t['played']}\n🏆 Wins recorded: {t['wins']}\n"
                  f"👥 Different winners: {t['players']} in {t['chats']} chats")


@router.message(Command("wordhelp", "whelp"))
async def help_cmd(m: Message):
    await m.reply("🔎 <b>WordSeek</b> - guess the hidden 5-letter word together!\n\n"
                  "/new - start a 5-letter game (anyone, groups or DM)\n/new4 · /new5 · /new6 - choose 4, 5 or 6 letters\n/end - end the game (group admins)\n"
                  "/wlb [global|group] [today|week|month|all] - leaderboard\n/myscore [period] - your score (reply to see someone else's)\n\n"
                  "<b>How to play</b>: after /new just type words of that length. 🟩 right letter in the right place · 🟨 right letter, wrong place · 🟥 not in the word.\n"
                  "Guesses per game: 25 (4 letters) · 30 (5) · 35 (6). The winner earns the limit + 6 − guesses used (at least 5). Fewer guesses = more points!")
