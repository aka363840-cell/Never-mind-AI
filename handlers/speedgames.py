"""Group speed games.  Plain commands + inline buttons.

⚡ Fastest Tap   /tap <amount>    open a lobby · /jointap   join   (button turns green -> first tap wins)
💥 Crash Count   /crash <amount>  open a lobby · /joincrash join   (count the letters of the word -> first correct number wins)
🛑 /end          ends the running speed game (host or group admin); it also ends a running WordSeek for admins.

The router is attached to the arcade router (handlers/arcade.py: `router.include_router(...)`), so bot.py needs no change and
the /end below is tried BEFORE WordSeek's own /end (it only matches while a speed game is running in the chat).
Crash answers are plain numbers typed in the chat; CoreMiddleware drops unaddressed group chatter, so they are read by an OUTER
middleware of this router (the same trick WordSeek uses) - everything that is not a live answer passes through untouched.
Rules, numbers and the money model are in game/speedgames.py; state and settlement in services/speedgames.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message, TelegramObject

from database.models import User
from game import speedgames as G
from keyboards.menus import kb
from services import economy, speedgames as svc
from services.errors import GameError
from utils.fmt import esc, mention, num
from utils.telegram import safe_answer

log = logging.getLogger(__name__)
router = Router()
DIV = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"

META = {
    "tap": dict(emoji="⚡", title="Fastest Tap", cmd="tap", join="jointap",
                how="When the button turns <b>green</b>, be the first to tap it! Tap the red one early and you're out."),
    "crash": dict(emoji="💥", title="Crash Count", cmd="crash", join="joincrash",
                  how="A secret word appears: count its letters and <b>type the number</b> in the chat. First correct answer wins - one try each!"),
}


def _usage(kind: str) -> str:
    m = META[kind]
    return (f"{m['emoji']} <b>{m['title']}</b> - a group battle for diamonds (min. {G.MIN_PLAYERS} players)\n"
            f"/{m['cmd']} <code>&lt;amount&gt;</code> - open a lobby, everybody pays the same entry\n"
            f"/{m['join']} - join the lobby\n/end - end the game\n\nExample: <code>/{m['cmd']} 100</code>")


# ------------------------------------------------------------------ small telegram helpers
async def _edit(bot: Bot, chat_id: int, mid: int | None, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    if not mid:
        return
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=mid, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            log.info("speedgames edit failed: %s", e)


async def _unbutton(bot: Bot, chat_id: int, mid: int | None) -> None:
    if not mid:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=mid, reply_markup=None)
    except TelegramBadRequest:
        pass


# ------------------------------------------------------------------ lobby
def _lobby_text(g: svc.Game) -> str:
    m = META[g.kind]
    people = "\n".join(f"• {esc(n)}{' 👑' if u == g.host else ''}" for u, n in g.players.items())
    return (f"{m['emoji']} <b>{m['title']}</b> - lobby open!\n{DIV}\n"
            f"💎 Entry: <b>{num(g.amount)}</b> each · 👥 <b>{len(g.players)}/{G.MAX_PLAYERS}</b> players (min. {G.MIN_PLAYERS})\n\n{people}\n\n"
            f"{m['how']}\n\n<i>Join with the button or /{m['join']}. It starts by itself in {G.LOBBY_S} s. "
            f"The winner takes the other players' entries ({G.FEE_PCT:.0%} is burned). Host or admins: /end.</i>")


def _lobby_kb(g: svc.Game) -> InlineKeyboardMarkup:
    return kb([[B(text=f"🎮 Join ({num(g.amount)} 💎)", callback_data=f"spd:j:{g.gid}"), B(text="▶️ Start now", callback_data=f"spd:s:{g.gid}")]])


async def _refresh_lobby(bot: Bot, g: svc.Game) -> None:
    if g.phase == "lobby":
        await _edit(bot, g.chat_id, g.lobby_msg, _lobby_text(g), _lobby_kb(g))


async def _open(m: Message, command: CommandObject, bot: Bot, user: User, kind: str) -> None:
    meta = META[kind]
    if m.chat.type not in ("group", "supergroup"):
        raise GameError(f"👥 {meta['emoji']} <b>{meta['title']}</b> needs at least {G.MIN_PLAYERS} players - start it in a group chat.")
    wallet = (await economy.balance(user.id)).balance
    arg = (command.args or "").split()
    amount = G.parse_stake(arg[0] if arg else None, wallet)
    if not amount:
        raise GameError(_usage(kind))
    if (bad := G.stake_problem(amount)):
        raise GameError(bad)
    from services import wordgames as wg                          # one game at a time per chat (all games)
    if (other := await wg.busy(m.chat.id)) and svc.get(m.chat.id) is None:
        raise GameError(f"🎮 A {other} game is running here - finish it or /end it first.")
    g = svc.new(kind, m.chat.id, user.id, user.first_name, amount)      # reserve the chat first: two /tap at once -> one lobby
    try:
        await svc.check_funds(user.id, amount, esc(user.first_name))
        await svc.claim_start_cd(user.id)
        msg = await m.answer(_lobby_text(g), reply_markup=_lobby_kb(g))
    except BaseException:
        svc.drop(g)
        raise
    g.lobby_msg = msg.message_id
    g.timer = asyncio.create_task(_lobby_timer(bot, g))


async def _join(bot: Bot, chat_id: int, uid: int, name: str, kind: str | None, gid: str | None = None) -> svc.Game:
    g = svc.get(chat_id)
    if g is None or (gid and g.gid != gid):
        meta = META[kind or "tap"]
        raise GameError(f"ℹ️ There's no open lobby to join here. Start one with /{meta['cmd']} <code>&lt;amount&gt;</code>")
    if kind and g.kind != kind:
        other = META[g.kind]
        raise GameError(f"ℹ️ The game running here is {other['emoji']} <b>{other['title']}</b> - join it with /{other['join']}")
    async with g.lock:
        if g.phase != "lobby":
            raise GameError("⏱ Too late - that game has already started.")
        if uid in g.players:
            raise GameError("✅ You're already in this game.")
        if len(g.players) >= G.MAX_PLAYERS:
            raise GameError("👥 The lobby is full.")
        await svc.check_funds(uid, g.amount, esc(name))
        g.players[uid] = name
    await _refresh_lobby(bot, g)
    return g


@router.message(Command("tap"))
async def tap_cmd(m: Message, command: CommandObject, bot: Bot, db_user: User):
    await _open(m, command, bot, db_user, "tap")


@router.message(Command("crash"))
async def crash_cmd(m: Message, command: CommandObject, bot: Bot, db_user: User):
    await _open(m, command, bot, db_user, "crash")


async def _join_cmd(m: Message, bot: Bot, user: User, kind: str) -> None:
    if m.chat.type not in ("group", "supergroup"):
        raise GameError(f"👥 Speed games are played in groups. Add me to a group and use /{META[kind]['cmd']} there.")
    g = await _join(bot, m.chat.id, user.id, user.first_name, kind)
    await m.reply(f"{META[kind]['emoji']} {mention(user.id, user.first_name)} joined! (<b>{len(g.players)}</b>/{G.MAX_PLAYERS})")


@router.message(Command("jointap"))
async def jointap_cmd(m: Message, bot: Bot, db_user: User):
    await _join_cmd(m, bot, db_user, "tap")


@router.message(Command("joincrash"))
async def joincrash_cmd(m: Message, bot: Bot, db_user: User):
    await _join_cmd(m, bot, db_user, "crash")


@router.callback_query(F.data.startswith("spd:j:"))
async def join_btn(cb: CallbackQuery, bot: Bot):
    await _join(bot, cb.message.chat.id, cb.from_user.id, cb.from_user.first_name, None, cb.data.split(":")[2])
    await safe_answer(cb, "You're in! 🎮")


@router.callback_query(F.data.startswith("spd:s:"))
async def start_btn(cb: CallbackQuery, bot: Bot):
    g = svc.get(cb.message.chat.id)
    if g is None or g.gid != cb.data.split(":")[2] or g.phase != "lobby":
        await safe_answer(cb, "⌛ This lobby is already closed.")
        return
    if cb.from_user.id != g.host:
        await safe_answer(cb, "Only the host can start early - or just wait, it starts by itself ⏳", alert=True)
        return
    await safe_answer(cb)
    await _begin(bot, g)


async def _lobby_timer(bot: Bot, g: svc.Game) -> None:
    try:
        await asyncio.sleep(G.LOBBY_S)
        await _begin(bot, g)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("speedgames lobby timer failed")
        svc.drop(g)


async def _begin(bot: Bot, g: svc.Game) -> None:
    """Lobby -> round. Needs MIN_PLAYERS who can still pay; otherwise the game is cancelled (nobody paid anything)."""
    async with g.lock:
        if g.phase != "lobby" or svc.get(g.chat_id) is not g:
            return
        for uid in list(g.players):                     # somebody may have spent their diamonds while waiting
            if (await economy.balance(uid)).balance < g.amount:
                name = g.players.pop(uid)
                try:
                    await bot.send_message(g.chat_id, f"💸 {esc(name)} can't pay the entry any more and was removed from the lobby.")
                except Exception:
                    pass
        if len(g.players) < G.MIN_PLAYERS:
            svc.drop(g)
            reason = f"👥 Not enough players (min. {G.MIN_PLAYERS}) - game cancelled. Nobody paid anything."
            await _unbutton(bot, g.chat_id, g.lobby_msg)
            await bot.send_message(g.chat_id, f"{META[g.kind]['emoji']} <b>{META[g.kind]['title']}</b>\n{reason}")
            return
        g.phase = "ready"
        if g.timer is not None and g.timer is not asyncio.current_task():
            g.timer.cancel()
    await _edit(bot, g.chat_id, g.lobby_msg, f"{META[g.kind]['emoji']} <b>{META[g.kind]['title']}</b> - lobby closed, here we go! 🎬\n👥 {len(g.players)} players · 💎 {num(g.amount)} each")
    g.tasks.append(asyncio.create_task(_round(bot, g)))


async def _round(bot: Bot, g: svc.Game) -> None:
    try:
        await (_run_tap if g.kind == "tap" else _run_crash)(bot, g)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("speedgames round failed")
        svc.drop(g)
        try:
            await bot.send_message(g.chat_id, "⚠️ Something went wrong - the game was cancelled. No diamonds moved.")
        except Exception:
            pass


async def _finish(bot: Bot, g: svc.Game, winner: int | None, note: str = "") -> None:
    """Close the round: settle the diamonds and announce the result. The caller already set g.phase = 'done' under the lock."""
    svc.drop(g)
    got, burned = await svc.settle(g, winner)
    meta = META[g.kind]
    head = f"{meta['emoji']} <b>{meta['title']}</b>\n{DIV}\n"
    if winner is None:
        body = f"{note}\n\n🤝 No winner - nobody paid anything."
    else:
        body = (f"🏆 {mention(winner, g.players[winner])} wins!{' ' + note if note else ''}\n"
                + (f"💎 <b>+{num(got)}</b> from the other {len(g.players) - 1} player(s) · 🔥 {num(burned)} burned" if got
                   else "<i>Nobody could pay their entry, so no diamonds moved.</i>"))
    await _unbutton(bot, g.chat_id, g.round_msg)
    await bot.send_message(g.chat_id, f"{head}{body}\n\n<i>Again: /{meta['cmd']} {num(g.amount)}</i>")


# ------------------------------------------------------------------ ⚡ Fastest Tap
def _tap_kb(g: svc.Game, live: bool) -> InlineKeyboardMarkup:
    if live:
        return kb([[B(text="🟢  TAP NOW!  🟢", callback_data=f"spd:tap:{g.gid}")]])
    return kb([[B(text="🔴  wait for green…", callback_data=f"spd:fs:{g.gid}")]])


async def _run_tap(bot: Bot, g: svc.Game) -> None:
    names = ", ".join(esc(n) for n in g.players.values())
    msg = await bot.send_message(g.chat_id, f"⚡ <b>Fastest Tap</b> - {len(g.players)} players · <b>{num(g.amount)}</b> 💎 each\n{DIV}\n{names}\n\n"
                                            "🔴 <b>Wait for the green button…</b>\n<i>Tap the red one too early and you're out of the round!</i>",
                                 reply_markup=_tap_kb(g, False))
    g.round_msg = msg.message_id
    await asyncio.sleep(G.tap_delay())
    async with g.lock:
        if g.phase != "ready":                          # everybody false-started, or /end
            return
        g.phase = "live"
        g.t0 = time.monotonic()
    await _edit(bot, g.chat_id, g.round_msg, f"⚡ <b>Fastest Tap</b>\n{DIV}\n🟢 <b>NOW! TAP!</b>", _tap_kb(g, True))
    await asyncio.sleep(G.TAP_TIMEOUT_S)
    async with g.lock:
        if g.phase != "live":
            return
        g.phase = "done"
    await _finish(bot, g, None, f"⏱ Nobody tapped within {G.TAP_TIMEOUT_S} s.")


@router.callback_query(F.data.startswith("spd:fs:"))
async def tap_false_start(cb: CallbackQuery, bot: Bot):
    g = svc.get(cb.message.chat.id)
    uid = cb.from_user.id
    if g is None or g.gid != cb.data.split(":")[2] or g.kind != "tap":
        await safe_answer(cb, "⌛ This round is over.")
        return
    async with g.lock:
        if uid not in g.players:
            await safe_answer(cb, "You're not in this game - start your own with /tap ⚡", alert=True)
            return
        if g.phase != "ready":
            await safe_answer(cb, "Too late - tap the green button!")
            return
        if uid in g.eliminated:
            await safe_answer(cb, "You're already out of this round 🚨")
            return
        g.eliminated.add(uid)
        all_out = len(g.eliminated) >= len(g.players)
        if all_out:
            g.phase = "done"
    await safe_answer(cb, "🚨 FALSE START! You're out of this round.", alert=True)
    if all_out:
        await _finish(bot, g, None, "🚨 Everybody jumped the gun!")


@router.callback_query(F.data.startswith("spd:tap:"))
async def tap_button(cb: CallbackQuery, bot: Bot):
    g = svc.get(cb.message.chat.id)
    uid = cb.from_user.id
    if g is None or g.gid != cb.data.split(":")[2] or g.kind != "tap":
        await safe_answer(cb, "⌛ Too slow - someone was faster!")
        return
    async with g.lock:
        if uid not in g.players:
            await safe_answer(cb, "You're not in this game - start your own with /tap ⚡", alert=True)
            return
        if g.phase != "live":
            await safe_answer(cb, "⌛ Too slow - someone was faster!")
            return
        if uid in g.eliminated:
            await safe_answer(cb, "You false-started, you're out of this round 🚨")
            return
        g.phase = "done"                               # the first tap closes the round: later taps see 'done'
        took = time.monotonic() - g.t0
    await safe_answer(cb, "⚡ FIRST!")
    await _finish(bot, g, uid, f"({G.reaction(took)})")


# ------------------------------------------------------------------ 💥 Crash Count
async def _run_crash(bot: Bot, g: svc.Game) -> None:
    names = ", ".join(esc(n) for n in g.players.values())
    await bot.send_message(g.chat_id, f"💥 <b>Crash Count</b> - {len(g.players)} players · <b>{num(g.amount)}</b> 💎 each\n{DIV}\n{names}\n\n"
                                      f"Get ready! The secret word appears in {G.CRASH_INTRO_S} s.\n<i>Count its letters and send the number - one try each, a wrong number puts you out!</i>")
    await asyncio.sleep(G.CRASH_INTRO_S)
    word = G.pick_word()
    async with g.lock:
        if g.phase != "ready":                          # /end during the countdown
            return
        g.word, g.answer = word, G.letter_count(word)
        g.phase = "live"
        g.t0 = time.monotonic()
    msg = await bot.send_message(g.chat_id, f"💥 <b>How many letters?</b>\n\n<code>{word}</code>\n\n<i>Type the number now! ({G.CRASH_TIMEOUT_S} s)</i>")
    g.round_msg = msg.message_id
    await asyncio.sleep(G.CRASH_TIMEOUT_S)
    async with g.lock:
        if g.phase != "live":
            return
        g.phase = "done"
    await _finish(bot, g, None, f"⏱ Time's up! <b>{g.word}</b> has <b>{g.answer}</b> letters.")


async def _answer(m: Message, bot: Bot, g: svc.Game, number: int) -> bool:
    """One typed number from a player. True = it was a real answer (consumed), False = pass it on as normal chat."""
    uid = m.from_user.id
    won = all_out = False
    async with g.lock:
        if g.phase != "live" or uid in g.eliminated or uid not in g.players:
            return False
        if number == g.answer:
            g.phase = "done"
            won, took = True, time.monotonic() - g.t0
        else:
            g.eliminated.add(uid)
            all_out = len(g.eliminated) >= len(g.players)
            if all_out:
                g.phase = "done"
    if won:
        await _finish(bot, g, uid, f"<b>{g.word}</b> has <b>{g.answer}</b> letters ({G.reaction(took)})")
    elif all_out:
        await m.reply(f"❌ Not {number}!")
        await _finish(bot, g, None, f"💥 Everybody got it wrong! <b>{g.word}</b> has <b>{g.answer}</b> letters.")
    else:
        await m.reply(f"❌ Not {number} - you're out of this round!")
    return True


class CrashAnswerMiddleware(BaseMiddleware):
    """Reads plain numbers from the players of a live Crash Count; every other message continues to the normal handlers."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if isinstance(event, Message) and event.text and event.from_user and not event.from_user.is_bot:
                g = svc.get(event.chat.id)
                if g is not None and g.kind == "crash" and g.phase == "live" and event.from_user.id in g.players:
                    n = G.parse_number(event.text)
                    if n is not None and await _answer(event, data["bot"], g, n):
                        return None
        except Exception:
            log.exception("crash answer failed")
        return await handler(event, data)


router.message.outer_middleware(CrashAnswerMiddleware())


# ------------------------------------------------------------------ /end (shared with WordSeek)
class RunningSpeedGame(Filter):
    """Matches only while a speed game runs in this chat, so WordSeek's own /end still gets every other /end."""

    async def __call__(self, message: Message) -> bool:
        return svc.get(message.chat.id) is not None


@router.message(Command("end"), RunningSpeedGame())
async def end_cmd(m: Message, bot: Bot):
    from handlers.members import _is_admin
    g = svc.get(m.chat.id)
    if g is None or m.from_user is None:
        return
    uid = m.from_user.id
    admin = await _is_admin(bot, m.chat.id, uid)
    if uid != g.host and not admin:
        raise GameError("⛔ Only the host of this game or a group admin can end it.")
    async with g.lock:
        svc.drop(g)
    meta = META[g.kind]
    await _unbutton(bot, g.chat_id, g.lobby_msg)
    await _unbutton(bot, g.chat_id, g.round_msg)
    text = f"🛑 {meta['emoji']} <b>{meta['title']}</b> ended. No diamonds moved."
    if admin:                                           # /end = "end any active game": a running WordSeek goes too (admins only, as before)
        try:
            from services import wordseek as ws
            st = await ws.get(m.chat.id)
            if st is not None:
                await ws.finish(st)
                text += f"\n🛑 The WordSeek game was ended as well - the word was <b>{st.word.upper()}</b>."
        except Exception:
            log.exception("could not end WordSeek together with the speed game")
    await m.reply(text)


# ------------------------------------------------------------------ how-to cards for the /arcade hub buttons
async def do_tap_info(msg: Message, user: User, reply: bool = True) -> None:
    await (msg.reply if reply else msg.answer)(_usage("tap") + "\n\n" + META["tap"]["how"])


async def do_crash_info(msg: Message, user: User, reply: bool = True) -> None:
    await (msg.reply if reply else msg.answer)(_usage("crash") + "\n\n" + META["crash"]["how"])
