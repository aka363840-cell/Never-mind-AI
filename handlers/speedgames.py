"""Group speed games.  Plain commands + inline buttons.

⚡ Fastest Tap   /tap <amount|free>    open a lobby · /jointap   join · /taptop   leaderboard
💥 Crash Count   /crash <amount|free>  open a lobby · /joincrash join · /crashtop leaderboard
🛑 /endgame          ends the running speed game (host or group admin, entries are refunded); admins also end a running WordSeek with it.

A match is best of 3 (first to 2 round wins, at most 5 rounds). The entries are held in escrow when the match starts and settled once
at the end (services/speedgames.py). `free` = practice: no diamonds, can be played alone.

The router is attached to the arcade router (handlers/arcade.py: `router.include_router(...)`), so bot.py needs no change and the
/endgame below is tried BEFORE WordSeek's /endgame (it only matches while a speed game is running in the chat).
Crash answers are plain numbers typed in the chat; CoreMiddleware drops unaddressed group chatter, so they are read by an OUTER
middleware of this router (the same trick WordSeek uses) - everything that is not a live answer passes through untouched.
Rules and numbers: game/speedgames.py.  The word picture: utils/crashcard.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message,
                           ReactionTypeEmoji, TelegramObject)

from database.models import User
from game import speedgames as G
from keyboards.menus import kb
from services import economy, speedgames as svc
from services.errors import GameError
from utils import crashcard
from utils.fmt import esc, mention, num
from utils.telegram import safe_answer

log = logging.getLogger(__name__)
router = Router()
DIV = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"

META = {
    "tap": dict(emoji="⚡", title="Fastest Tap", cmd="tap", join="jointap", top="taptop",
                how="Several buttons appear - only <b>one is green</b> and it moves every round. Tap the green one first! Hit a red one and you're out of the round."),
    "crash": dict(emoji="💥", title="Crash Count", cmd="crash", join="joincrash", top="crashtop",
                  how="A word appears as a <b>picture</b> with a question (letters? vowels? one letter?). <b>Type the number</b> in the chat - first correct answer wins the round, one try each."),
}


def _usage(kind: str) -> str:
    m = META[kind]
    return (f"{m['emoji']} <b>{m['title']}</b> - best of 3 for diamonds (groups, min. {G.MIN_PLAYERS} players)\n"
            f"/{m['cmd']} <code>&lt;amount&gt;</code> - open a lobby, everybody puts in the same entry\n"
            f"/{m['cmd']} <code>free</code> - practice: no diamonds, you can even play alone\n"
            f"/{m['join']} - join the lobby · /{m['top']} - leaderboard\n/endgame - end the game (entries are refunded)\n\nExample: <code>/{m['cmd']} 100</code>")


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


async def _say(bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> Any:
    try:
        return await bot.send_message(chat_id, text, reply_markup=markup)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("speedgames could not send a message")
        return None


# ------------------------------------------------------------------ lobby
def _mode(g: svc.Game) -> str:
    return "🎓 Practice - no diamonds" if g.practice else f"💎 Entry: <b>{num(g.amount)}</b> each"


def _rules_line(g: svc.Game) -> str:
    solo = "one round if you play alone, " if g.practice else ""
    return f"🏁 Best of 3: {solo}first to <b>{G.WINS_TO_TAKE}</b> round wins takes it."


def _lobby_text(g: svc.Game) -> str:
    m = META[g.kind]
    people = "\n".join(f"• {esc(n)}{' 👑' if u == g.host else ''}" for u, n in g.players.items())
    floor = 1 if g.practice else G.MIN_PLAYERS
    invited = [f"{mention(u, n)}" for u, n in g.invited if u not in g.players]
    inv = f"\n🔁 <b>Rematch!</b> Invited: {', '.join(invited)}\n" if invited else ""
    tail = ("The winner takes the other players' entries "
            f"({G.FEE_PCT:.0%} is burned); everybody gets the entry back if nobody wins. " if not g.practice else "")
    return (f"{m['emoji']} <b>{m['title']}</b> - lobby open!\n{DIV}\n"
            f"{_mode(g)} · 👥 <b>{len(g.players)}/{G.MAX_PLAYERS}</b> players (min. {floor})\n{_rules_line(g)}\n{inv}\n{people}\n\n"
            f"{m['how']}\n\n<i>Join with the button or /{m['join']}. It starts by itself in {G.LOBBY_S} s. {tail}Host or admins: /endgame.</i>")


def _lobby_kb(g: svc.Game) -> InlineKeyboardMarkup:
    label = "🎮 Join" if g.practice else f"🎮 Join ({num(g.amount)} 💎)"
    return kb([[B(text=label, callback_data=f"spd:j:{g.gid}"), B(text="▶️ Start now", callback_data=f"spd:s:{g.gid}")]])


async def _refresh_lobby(bot: Bot, g: svc.Game) -> None:
    if g.phase == "lobby":
        await _edit(bot, g.chat_id, g.lobby_msg, _lobby_text(g), _lobby_kb(g))


async def _open_lobby(bot: Bot, chat_id: int, uid: int, name: str, kind: str, amount: int, invited: list[tuple[int, str]] | None = None) -> svc.Game:
    from services import wordgames as wg                          # one game at a time per chat (all games)
    if (other := await wg.busy(chat_id)) and svc.get(chat_id) is None:
        raise GameError(f"🎮 A {other} game is running here - finish it or /endgame it first.")
    g = svc.new(kind, chat_id, uid, name, amount)          # reserve the chat first: two /tap at once -> one lobby
    g.invited = list(invited or [])
    try:
        await svc.check_funds(uid, amount, esc(name))
        await svc.claim_start_cd(uid)
        await svc.maybe_recover()
        msg = await bot.send_message(chat_id, _lobby_text(g), reply_markup=_lobby_kb(g))
    except BaseException:
        svc.drop(g)
        raise
    g.lobby_msg = msg.message_id
    g.timer = asyncio.create_task(_lobby_timer(bot, g))
    return g


async def _open(m: Message, command: CommandObject, bot: Bot, user: User, kind: str) -> None:
    meta = META[kind]
    if m.chat.type not in ("group", "supergroup"):
        raise GameError(f"👥 {meta['emoji']} <b>{meta['title']}</b> is a group game - start it in a group chat "
                        f"(or try <code>/{meta['cmd']} free</code> there to practice).")
    wallet = (await economy.balance(user.id)).balance
    arg = (command.args or "").split()
    amount = G.parse_stake(arg[0] if arg else None, wallet)
    if amount is None:
        raise GameError(_usage(kind))
    if (bad := G.stake_problem(amount)):
        raise GameError(bad)
    await _open_lobby(bot, m.chat.id, user.id, user.first_name, kind, amount)


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
    """Lobby -> match. The entries are held now; players who can't pay any more are removed. Needs MIN_PLAYERS (practice: 1)."""
    floor = 1 if g.practice else G.MIN_PLAYERS
    async with g.lock:
        if g.phase != "lobby" or svc.get(g.chat_id) is not g:
            return
        if len(g.players) >= floor:
            for _uid, name in await svc.hold_entries(g):
                await _say(bot, g.chat_id, f"💸 {esc(name)} can't pay the entry any more and was removed from the lobby.")
        if len(g.players) < floor:
            svc.drop(g, refund=False)
            await svc.release(g, None, played=False)               # gives back the entries that were already held
            await _unbutton(bot, g.chat_id, g.lobby_msg)
            await _say(bot, g.chat_id, f"{META[g.kind]['emoji']} <b>{META[g.kind]['title']}</b>\n👥 Not enough players (min. {floor}) - game cancelled. Nobody paid anything.")
            return
        g.wins_needed = G.wins_needed(len(g.players), g.practice)
        g.max_rounds = G.max_rounds(g.wins_needed)
        g.scores = {u: 0 for u in g.players}
        g.phase = "ready"
        if g.timer is not None and g.timer is not asyncio.current_task():
            g.timer.cancel()
    await _edit(bot, g.chat_id, g.lobby_msg, f"{META[g.kind]['emoji']} <b>{META[g.kind]['title']}</b> - lobby closed, here we go! 🎬\n"
                                              f"👥 {len(g.players)} players · {_mode(g)}")
    g.tasks.append(asyncio.create_task(_match(bot, g)))


# ------------------------------------------------------------------ the match
def _board(g: svc.Game) -> str:
    rows = sorted(g.scores.items(), key=lambda kv: -kv[1])
    return " · ".join(f"{esc(g.players[u])} <b>{v}</b>" for u, v in rows)


async def _match(bot: Bot, g: svc.Game) -> None:
    try:
        m = META[g.kind]
        pot = sum(g.held.values())
        names = ", ".join(esc(n) for n in g.players.values())
        goal = ("one round" if g.wins_needed == 1 else f"first to <b>{g.wins_needed}</b> round wins (max. {g.max_rounds} rounds)")
        await _say(bot, g.chat_id, f"{m['emoji']} <b>{m['title']}</b> - {len(g.players)} player(s)\n{DIV}\n{names}\n\n"
                                   f"{'💰 Pot: <b>' + num(pot) + '</b> 💎 (held safely until the match is over)' if pot else '🎓 Practice match'}\n🏁 Goal: {goal}")
        await asyncio.sleep(G.MATCH_INTRO_S)
        winner: int | None = None
        while True:
            g.round_no += 1
            rid = await (_tap_round if g.kind == "tap" else _crash_round)(bot, g)
            if g.phase == "done":                                  # /endgame during the round
                return
            if rid is not None:
                g.scores[rid] += 1
            state, who = G.match_state(g.scores, g.wins_needed, g.round_no, g.max_rounds)
            await _announce_round(bot, g, rid, state)
            if state != "continue":
                winner = who
                break
            await asyncio.sleep(G.BETWEEN_S)
        await _finish(bot, g, winner)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("speedgames match failed")
        svc.drop(g)                                                # refunds the escrow in the background
        await _say(bot, g.chat_id, "⚠️ Something went wrong - the game was cancelled and all entries are refunded.")


async def _announce_round(bot: Bot, g: svc.Game, rid: int | None, state: str) -> None:
    head = f"🏁 <b>Round {g.round_no}</b> - "
    if rid is not None:
        head += f"{mention(rid, g.players[rid])} wins the round! ({G.reaction(g.took)})"
    else:
        head += f"no winner ({g.reason or 'nobody was quick enough'})"
    if g.kind == "crash" and g.challenge is not None:
        head += f"\n✅ <b>{g.challenge.word}</b> → <b>{g.challenge.answer}</b>"
    tail = f"\n📊 {_board(g)}" if len(g.players) > 1 or g.wins_needed > 1 else ""
    nxt = f"\n<i>Next round in {G.BETWEEN_S} s…</i>" if state == "continue" else ""
    await _say(bot, g.chat_id, head + tail + nxt)


async def _finish(bot: Bot, g: svc.Game, winner: int | None) -> None:
    g.phase = "done"
    res = await svc.release(g, winner, played=True)
    svc.remember(g)
    svc.drop(g)                                                    # (already settled -> no refund task)
    m = META[g.kind]
    lines = [f"{m['emoji']} <b>{m['title']}</b> - match over\n{DIV}"]
    if winner is None:
        lines.append("🤝 <b>Draw - nobody takes it.</b>" + ("\n💎 Everybody got the entry back." if g.held else ""))
    else:
        lines.append(f"🏆 {mention(winner, g.players[winner])} wins the match! 📊 {_board(g)}")
        if g.practice:
            lines.append("<i>Practice - no diamonds moved. Ready for the real thing? /" + m["cmd"] + " 100</i>")
        elif res.got:
            lines.append(f"💎 <b>+{num(res.got)}</b> from the other {len(g.held) - 1 - len(res.capped)} player(s) · 🔥 {num(res.burned)} burned")
        if res.capped:
            who = ", ".join(esc(g.players[u]) for u in res.capped if u in g.players)
            lines.append(f"🔒 {who}: entry refunded - daily limit of {G.PAIR_DAILY} paid matches against the same player reached.")
        if res.unlocked:
            lines.append("🏅 Achievement unlocked: " + ", ".join(esc(a) for a in res.unlocked))
    await _say(bot, g.chat_id, "\n".join(lines), kb([[B(text="🔁 Rematch", callback_data=f"spd:r:{g.gid}")]]))


# ------------------------------------------------------------------ ⚡ Fastest Tap
def _tap_kb(g: svc.Game, live: bool) -> InlineKeyboardMarkup:
    tail = f"{g.gid}:{g.round_no}"
    if not live:
        return kb([[B(text="🔴  wait for green…", callback_data=f"spd:fs:{tail}")]])
    cells = [B(text="🟢 TAP!", callback_data=f"spd:tap:{tail}") if i == g.green else B(text="🔴", callback_data=f"spd:fs:{tail}") for i in range(G.TAP_BUTTONS)]
    return kb([cells[:2], cells[2:]])


async def _resolve_none(g: svc.Game, reason: str) -> None:
    """Round over without a winner (caller holds g.lock)."""
    g.phase = "between"
    g.reason = reason
    if g.fut is not None and not g.fut.done():
        g.fut.set_result(None)


async def _tap_round(bot: Bot, g: svc.Game) -> int | None:
    g.eliminated, g.reason, g.took = set(), "", 0.0
    g.fut = asyncio.get_running_loop().create_future()
    g.phase = "ready"
    msg = await bot.send_message(g.chat_id, f"⚡ <b>Round {g.round_no}</b> · 📊 {_board(g)}\n{DIV}\n🔴 <b>Wait for the green button…</b>\n"
                                            "<i>Tap too early and you're out of the round!</i>", reply_markup=_tap_kb(g, False))
    g.round_msg = msg.message_id
    await asyncio.sleep(G.tap_delay())
    async with g.lock:
        if g.phase == "ready":
            g.green = G.tap_green()
            g.phase = "live"
            g.t0 = time.monotonic()
    if g.phase == "live":
        await _edit(bot, g.chat_id, g.round_msg, f"⚡ <b>Round {g.round_no}</b>\n{DIV}\n🟢 <b>NOW! Tap the GREEN one!</b>", _tap_kb(g, True))
        await asyncio.wait({g.fut}, timeout=G.TAP_TIMEOUT_S)
    async with g.lock:
        if not g.fut.done():
            await _resolve_none(g, f"nobody tapped within {G.TAP_TIMEOUT_S} s")
    await _unbutton(bot, g.chat_id, g.round_msg)
    return g.fut.result()


def _tap_target(cb: CallbackQuery) -> tuple[svc.Game | None, bool]:
    """(the running tap game this button belongs to, is it the CURRENT round's button)."""
    parts = cb.data.split(":")
    g = svc.get(cb.message.chat.id)
    if g is None or g.kind != "tap" or len(parts) < 4 or g.gid != parts[2]:
        return None, False
    return g, parts[3] == str(g.round_no)


@router.callback_query(F.data.startswith("spd:fs:"))
async def tap_false_start(cb: CallbackQuery, bot: Bot):
    g, current = _tap_target(cb)
    uid = cb.from_user.id
    if g is None or not current:
        await safe_answer(cb, "⌛ This round is over.")
        return
    async with g.lock:
        if uid not in g.players:
            await safe_answer(cb, "You're not in this game - start your own with /tap ⚡", alert=True)
            return
        if g.phase not in ("ready", "live") or g.fut is None or g.fut.done():
            await safe_answer(cb, "⌛ Too slow - the round is over.")
            return
        if uid in g.eliminated:
            await safe_answer(cb, "You're already out of this round 🚨")
            return
        early = g.phase == "ready"
        g.eliminated.add(uid)
        all_out = len(g.eliminated) >= len(g.players)
        if all_out:
            await _resolve_none(g, "everybody jumped the gun" if early else "everybody hit a decoy")
    await safe_answer(cb, "🚨 FALSE START! You're out of this round." if early else "🙈 A decoy! You're out of this round.", alert=True)
    if not all_out:
        await _say(bot, g.chat_id, f"{'👀' if early else '🙈'} {esc(g.players[uid])} {'jumped the gun' if early else 'hit a decoy'} and is out of round {g.round_no}!")


@router.callback_query(F.data.startswith("spd:tap:"))
async def tap_button(cb: CallbackQuery, bot: Bot):
    g, current = _tap_target(cb)
    uid = cb.from_user.id
    if g is None or not current:
        await safe_answer(cb, "⌛ Too slow - someone was faster!")
        return
    async with g.lock:
        if uid not in g.players:
            await safe_answer(cb, "You're not in this game - start your own with /tap ⚡", alert=True)
            return
        if g.phase != "live" or g.fut is None or g.fut.done():
            await safe_answer(cb, "⌛ Too slow - someone was faster!")
            return
        if uid in g.eliminated:
            await safe_answer(cb, "You're out of this round 🚨")
            return
        g.phase = "between"                                        # the first tap closes the round: later taps see 'between'
        g.took = time.monotonic() - g.t0
        g.fut.set_result(uid)
    await safe_answer(cb, "⚡ FIRST!")


# ------------------------------------------------------------------ 💥 Crash Count
async def _crash_round(bot: Bot, g: svc.Game) -> int | None:
    ch = G.make_challenge(g.round_no, avoid=g.used)
    g.used.add(ch.word)
    g.challenge, g.eliminated, g.reason, g.took = ch, set(), "", 0.0
    loop = asyncio.get_running_loop()
    g.fut = loop.create_future()
    g.phase = "ready"
    drawing = loop.run_in_executor(None, crashcard.render, ch.word)          # drawn while the countdown runs
    drawing.add_done_callback(lambda f: f.cancelled() or f.exception())       # never leave an un-retrieved exception behind if the round is cancelled
    await bot.send_message(g.chat_id, f"💥 <b>Round {g.round_no}</b> · 📊 {_board(g)}\n{DIV}\nGet ready! The word appears in {G.CRASH_INTRO_S} s.\n"
                                      f"❓ {ch.question}\n<i>One try each - a wrong number puts you out of the round (👎).</i>")
    await asyncio.sleep(G.CRASH_INTRO_S)
    caption = f"❓ {ch.question}\n<i>Type the number now! ({G.CRASH_TIMEOUT_S} s)</i>"
    try:
        png = await drawing
        msg = await bot.send_photo(g.chat_id, BufferedInputFile(png, filename="word.png"), caption=caption)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("could not send the word picture - falling back to text")
        msg = await bot.send_message(g.chat_id, f"💥 <b>The word:</b> <code>{' '.join(ch.word)}</code>\n{caption}")
    g.round_msg = msg.message_id
    async with g.lock:
        if g.phase != "ready":                                     # /endgame during the countdown
            return None
        g.phase = "live"
        g.t0 = time.monotonic()
    await asyncio.wait({g.fut}, timeout=G.CRASH_TIMEOUT_S)
    async with g.lock:
        if not g.fut.done():
            await _resolve_none(g, f"time's up after {G.CRASH_TIMEOUT_S} s")
    return g.fut.result()


async def _react(bot: Bot, m: Message, emoji: str) -> None:
    try:
        await bot.set_message_reaction(chat_id=m.chat.id, message_id=m.message_id, reaction=[ReactionTypeEmoji(emoji=emoji)])
    except asyncio.CancelledError:
        raise
    except Exception:
        pass                                                       # reactions can be disabled in a group: the round summary names everyone anyway


async def _answer(m: Message, bot: Bot, g: svc.Game, number: int) -> bool:
    """One typed number from a player. True = it was a real answer (consumed), False = pass it on as normal chat."""
    uid = m.from_user.id
    won = False
    async with g.lock:
        if g.phase != "live" or uid in g.eliminated or uid not in g.players or g.fut is None or g.fut.done() or g.challenge is None:
            return False
        if number == g.challenge.answer:
            g.phase = "between"
            g.took = time.monotonic() - g.t0
            g.fut.set_result(uid)
            won = True
        else:
            g.eliminated.add(uid)
            if len(g.eliminated) >= len(g.players):
                await _resolve_none(g, "everybody got it wrong")
    await _react(bot, m, "🔥" if won else "👎")
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


# ------------------------------------------------------------------ rematch
@router.callback_query(F.data.startswith("spd:r:"))
async def rematch_btn(cb: CallbackQuery, bot: Bot):
    chat_id, uid = cb.message.chat.id, cb.from_user.id
    last = svc.last(chat_id)
    if last is None or last.gid != cb.data.split(":")[2]:
        await safe_answer(cb, "⌛ This rematch offer has expired - start a new game with /tap or /crash.", alert=True)
        return
    if uid not in last.players:
        await safe_answer(cb, "Only the players of that match can start the rematch 🙂", alert=True)
        return
    others = [(u, n) for u, n in last.players.items() if u != uid]
    await _open_lobby(bot, chat_id, uid, cb.from_user.first_name, last.kind, last.amount, others)
    svc.LAST.pop(chat_id, None)                                    # one rematch per match
    await _unbutton(bot, chat_id, cb.message.message_id)
    await safe_answer(cb, "Rematch lobby is open! 🔁")


# ------------------------------------------------------------------ leaderboards
async def _top(m: Message, kind: str, uid: int) -> None:
    meta = META[kind]
    rows = await svc.top(kind, 10)
    if not rows:
        await m.reply(f"{meta['emoji']} Nobody has won a {meta['title']} match yet. Be the first: /{meta['cmd']} 100")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else f'{i + 1}.'} {mention(u, n)} - <b>{num(w)}</b> wins · {num(p)} matches" for i, (u, n, w, p) in enumerate(rows)]
    wins, plays, rank = await svc.my_stats(kind, uid)
    me = f"\n\n👤 You: <b>{num(wins)}</b> wins · {num(plays)} matches" + (f" · rank <b>#{rank}</b>" if rank else "")
    await m.reply(f"{meta['emoji']} <b>{meta['title']} - top players</b>\n{DIV}\n" + "\n".join(lines) + me)


@router.message(Command("taptop"))
async def taptop_cmd(m: Message, db_user: User):
    await _top(m, "tap", db_user.id)


@router.message(Command("crashtop"))
async def crashtop_cmd(m: Message, db_user: User):
    await _top(m, "crash", db_user.id)


# ------------------------------------------------------------------ /endgame (shared with WordSeek)
class RunningSpeedGame(Filter):
    """Matches only while a speed game runs in this chat, so WordSeek's own /endgame still gets every other /endgame."""

    async def __call__(self, message: Message) -> bool:
        return svc.get(message.chat.id) is not None


@router.message(Command("endgame", "endtap", "endcrash"), RunningSpeedGame())
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
        svc.drop(g, refund=False)
    held = bool(g.held)
    await svc.release(g, None, played=False)                       # every held entry goes back
    meta = META[g.kind]
    await _unbutton(bot, g.chat_id, g.lobby_msg)
    await _unbutton(bot, g.chat_id, g.round_msg)
    text = f"🛑 {meta['emoji']} <b>{meta['title']}</b> ended. " + ("Every entry was refunded." if held else "No diamonds moved.")
    if admin:                                                      # /endgame = "end any active game": a running WordSeek goes too (admins only, as before)
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
