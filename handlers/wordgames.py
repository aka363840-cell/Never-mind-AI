"""Wordly + Hack - two group word / number games. Deliberately NOT in the Telegram "/" menu (they are listed in /help).

🔠 Word scramble (wordly)   /wordly or /wl - start · type words made from the 8 letters (2 minutes)
🔐 Password hacking         /hack3 /hack4 /hack5 /hack6 <amount> - open a lobby (the host's bet) · /joinhack - join (same bet)
/endgame                        ends the running game (Wordly: starter or group admin · Hack: host only · otherwise WordSeek's /endgame)
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message, ReactionTypeEmoji, TelegramObject

from database.models import User
from game import hack as H, hack_stats as HS, wordly as WL
from game.rules import R
from services import economy, hackstats as hs, wordgames as svc
from services.errors import GameError
from utils.fmt import esc, mention, num, parse_amount
from utils.telegram import safe_answer

log = logging.getLogger(__name__)
router = Router()
MEDALS = ["🥇", "🥈", "🥉"]
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


def _cancel(task: asyncio.Task | None) -> None:
    if task is not None and task is not asyncio.current_task():
        task.cancel()


async def _is_admin(bot: Bot, m: Message) -> bool:
    if m.chat.type == "private":
        return True
    from handlers.members import _is_admin as adm
    return await adm(bot, m.chat.id, m.from_user.id)


def _busy_text(what: str) -> str:
    return {"wordly": "🔠 A Wordly game is running here - finish it or /endgame it first.",
            "hack": "🔐 A Hack game is running here - finish it or /endgame it first.",
            "WordSeek": "🔎 A WordSeek game is running here - finish it or /endgame it first."}.get(what, f"🎮 A {what} game is already running here - finish it or /endgame it first.")


async def _react(m: Message, emoji: str) -> bool:
    try:
        await m.react([ReactionTypeEmoji(emoji=emoji)])
        return True
    except Exception:
        return False


async def _reap(chat_id: int) -> None:
    """A game that was never finished (bot lost rights, timer crashed ...) must not block the chat for ever."""
    h = svc.HACK.get(chat_id)
    if h is not None and svc.now() - h.created > 2 * 3600:
        async with h.lock:
            if svc.HACK.get(chat_id) is h:
                svc.HACK.pop(chat_id, None)
                h.phase = "done"
                _cancel(h.task)
                await svc.refund_all(h)
    w = svc.WORDLY.get(chat_id)
    if w is not None and svc.now() > w.ends_at + 120:
        svc.WORDLY.pop(chat_id, None)
        _cancel(w.task)


# ============================================================================== 🔠 WORDLY
def _wordly_intro(g: svc.WordlyGame) -> str:
    return (f"🔠 <b>WORDLY</b> - word scramble!\n\n<code>{WL.show_rack(g.rack)}</code>\n\n"
            "Make as many words as you can from these <b>8 letters</b>. Just type them in the chat!\n"
            f"• {WL.MIN_LEN}-{WL.MAX_LEN} letter words, each letter as often as it appears above\n"
            "• points: " + " · ".join(f"{n} letters = <b>{p}</b>" for n, p in WL.SCORE.items()) + "\n"
            "• every word counts once - the first one to type it gets it\n"
            "• 👍 = accepted · 🔥 = 6+ letters\n\n"
            f"⏱ <b>{WL.ROUND_S // 60} minutes</b> · /endgame to stop early")


def _wordly_result(g: svc.WordlyGame, title: str) -> str:
    by: dict[int, list] = {}
    for w, (uid, name) in g.found.items():
        row = by.setdefault(uid, [name, 0, []])
        row[1] += WL.points(w)
        row[2].append(w)
    head = f"{title}\n\n🔠 <code>{WL.show_rack(g.rack)}</code>\n\n"
    if not by:
        body = "Nobody found a word this time 😅"
    else:
        rank = sorted(by.values(), key=lambda r: (-r[1], -len(r[2])))
        lines = []
        for i, (name, pts, words) in enumerate(rank[:10]):
            shown = " ".join(w.upper() for w in sorted(words, key=lambda w: (-len(w), w))[:14])
            more = f" +{len(words) - 14}" if len(words) > 14 else ""
            lines.append(f"{MEDALS[i] if i < 3 else f'<b>{i + 1}.</b>'} <b>{esc(name[:20])}</b> - <b>{pts}</b> pts · {len(words)} words\n<code>{shown}</code>{more}")
        body = "\n".join(lines) + f"\n\n📚 {len(g.found)} of {len(g.words)} possible words found"
    missed = [w for w in WL.solutions(g.rack, common=True) if w not in g.found][:12]
    if missed:
        body += "\n\n💡 You missed: " + " ".join(w.upper() for w in missed)
    return head + body


def _wordly_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="🔁 Play again", callback_data="wl:new")]])


async def _new_wordly(bot: Bot, chat_id: int, uid: int) -> None:
    await _reap(chat_id)
    cur = svc.WORDLY.get(chat_id)
    if cur is not None:
        await bot.send_message(chat_id, "🔠 A Wordly game is already running here!\n\n" + _wordly_intro(cur))
        return
    what = await svc.busy(chat_id)
    if what:
        raise GameError(_busy_text(what))
    if chat_id in svc.WORDLY:                                  # somebody else won the race while we looked
        return
    rack, sols = WL.make_rack()
    g = svc.WordlyGame(chat_id, rack, frozenset(sols), uid, svc.now() + WL.ROUND_S)
    svc.WORDLY[chat_id] = g
    try:
        await bot.send_message(chat_id, _wordly_intro(g))
    except Exception:
        svc.WORDLY.pop(chat_id, None)
        raise
    g.task = _spawn(_wordly_timer(bot, g))


async def _wordly_timer(bot: Bot, g: svc.WordlyGame) -> None:
    try:
        await asyncio.sleep(max(0.0, g.ends_at - svc.now() - 20))
        if svc.WORDLY.get(g.chat_id) is not g:
            return
        await bot.send_message(g.chat_id, f"⏰ <b>20 seconds left!</b> {len(g.found)} words so far.\n<code>{WL.show_rack(g.rack)}</code>")
        await asyncio.sleep(max(0.0, g.ends_at - svc.now()))
        await _wordly_finish(bot, g, "⏰ <b>Time's up!</b>")
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("wordly timer failed")
        svc.WORDLY.pop(g.chat_id, None)


async def _wordly_finish(bot: Bot, g: svc.WordlyGame, title: str) -> None:
    async with g.lock:
        if svc.WORDLY.get(g.chat_id) is not g:
            return
        del svc.WORDLY[g.chat_id]
    _cancel(g.task)
    await bot.send_message(g.chat_id, _wordly_result(g, title), reply_markup=_wordly_kb())


@router.message(Command("wordly", "wl"))
async def wordly_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    await _new_wordly(bot, m.chat.id, m.from_user.id)


@router.callback_query(F.data == "wl:new")
async def wordly_again(cb: CallbackQuery, bot: Bot):
    if cb.message is None:
        return await safe_answer(cb)
    await safe_answer(cb)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _new_wordly(bot, cb.message.chat.id, cb.from_user.id)


async def _wordly_guess(m: Message, g: svc.WordlyGame) -> bool:
    """True = the message was a word of this rack (accepted or already taken) and must not reach other handlers."""
    w = m.text.strip().lower()
    if w not in g.words or svc.now() > g.ends_at:
        return False
    async with g.lock:
        if svc.WORDLY.get(g.chat_id) is not g:
            return False
        if w in g.found:
            await _react(m, "👀")
            return True
        g.found[w] = (m.from_user.id, m.from_user.full_name)
    if not await _react(m, "🔥" if len(w) >= 6 else "👍"):
        try:
            await m.reply(f"✅ <b>{w.upper()}</b> +{WL.points(w)}")
        except Exception:
            pass
    return True


# ============================================================================== 🔐 HACK
def _lobby_text(g: svc.HackGame) -> str:
    names = "\n".join(f"{i + 1}. {mention(p.uid, p.name)}{' 👑' if p.uid == g.host else ''}" for i, p in enumerate(g.players))
    return (f"🔐 <b>HACK {g.n}</b> - crack the {g.n}-digit code!\n\n"
            f"💰 Bet <b>{num(g.bet)}</b> 💎 each · pot <b>{num(g.pot)}</b> 💎\n\n"
            f"👥 <b>Players</b> ({len(g.players)}/{H.MAX_PLAYERS})\n{names}\n\n"
            f"Join with the button or /joinhack (same bet). Starts in {H.LOBBY_S}s with 2+ players, or when the host taps Start.")


def _lobby_kb(g: svc.HackGame) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text=f"🔓 Join ({num(g.bet)} 💎)", callback_data=f"hk:j:{g.gid}"),
                                                  B(text="▶️ Start", callback_data=f"hk:s:{g.gid}")]])


def _intro(g: svc.HackGame) -> str:
    ex = "0123456789"[: g.n]
    head = (f"🚀 <b>HACK {g.n} - practice</b> (free, no bet)" if g.solo else f"🚀 <b>HACK {g.n} started!</b> Pot: <b>{num(g.pot)}</b> 💎")
    tail = ("Beat the code in as few tries as you can. /endgame to stop." if g.solo else
            "Everybody sees every guess. <b>First to crack it takes the pot!</b> 🏆")
    return (f"{head}\n\n"
            f"I locked a secret code of <b>{g.n} different digits</b> (a 0 can be first, no digit repeats).\n"
            f"Send a code like <code>{ex}</code> on your turn and I tell you:\n"
            "🔓 <b>hacks</b> - right digit, right position\n⚡ <b>glitches</b> - right digit, wrong position\n\n"
            f"{H.max_guesses(g.n)} tries each · {H.TURN_S}s per turn · miss {H.MAX_FORFEIT} turns in a row and you're out.\n"
            f"{tail}")


def _board(g: svc.HackGame) -> str:
    rows = g.history[-10:]
    lines = [f"<code>{code}</code> 🔓{h} ⚡{gl} · {esc(name[:14])}" for name, code, h, gl in rows]
    older = len(g.history) - len(rows)
    out = f"🔐 <b>HACK {g.n}</b> · " + ("practice" if g.solo else f"pot <b>{num(g.pot)}</b> 💎") + "\n\n"
    if lines:
        out += (f"<i>… {older} earlier guesses</i>\n" if older else "") + "\n".join(lines) + "\n\n"
    gone = H.eliminated(g.history)
    if gone:
        out += f"🚫 Not in the code: <code>{' '.join(gone)}</code>\n\n"
    cur = g.current()
    if cur is not None:
        out += (f"🎯 {mention(cur.uid, cur.name)} - your turn! Send a {g.n}-digit code.\n"
                f"⏱ {H.TURN_S}s · tries left: <b>{H.max_guesses(g.n) - cur.guesses}</b>")
    return out


async def _prompt(bot: Bot, g: svc.HackGame, note: str = "", reply_to: Message | None = None) -> None:
    """Show the board + whose turn it is, and start that turn's timer. Caller holds g.lock."""
    g.token += 1
    old = g.board_msg
    text = (note + "\n\n" if note else "") + _board(g)
    msg = await (reply_to.reply(text) if reply_to is not None else bot.send_message(g.chat_id, text))
    g.board_msg = msg.message_id
    if old:
        try:
            await bot.delete_message(g.chat_id, old)             # keep the chat tidy: only the newest board stays
        except Exception:
            pass
    _cancel(g.task)
    g.task = _spawn(_turn_timer(bot, g, g.token))


async def _update_lobby(bot: Bot, g: svc.HackGame) -> None:
    if g.lobby_msg:
        try:
            await bot.edit_message_text(_lobby_text(g), chat_id=g.chat_id, message_id=g.lobby_msg, reply_markup=_lobby_kb(g))
        except Exception:
            pass


async def _hack_begin(bot: Bot, g: svc.HackGame) -> None:
    """Lobby -> play. Caller holds g.lock and has checked there are 2+ players."""
    g.phase = "play"
    g.secret = H.new_code(g.n)
    random.shuffle(g.players)
    g.turn = 0
    _cancel(g.task)
    if g.lobby_msg:
        try:
            await bot.edit_message_reply_markup(chat_id=g.chat_id, message_id=g.lobby_msg, reply_markup=None)
        except Exception:
            pass
    await bot.send_message(g.chat_id, _intro(g))
    await _prompt(bot, g)


def _win_title(win: list) -> str:
    if len(win) == 1:
        p = win[0]
        return f"🎉 <b>{mention(p.uid, p.name)}</b> cracked the code in <b>{p.guesses}</b> {'try' if p.guesses == 1 else 'tries'}!"
    names = " & ".join(f"<b>{mention(p.uid, p.name)}</b>" for p in win)
    return f"🤝 {names} cracked the code in <b>{win[0].guesses}</b> tries - it's a tie!"


async def _hack_finish(bot: Bot, g: svc.HackGame, winners, title: str, reply_to: Message | None = None,
                       cracked: bool = False, record: bool = True) -> None:
    """End the game: pay the winner(s) (a tie splits the pot), or refund everybody when there is none. Practice games move no diamonds.
    `winners` = one HackPlayer, a list of them, or None. `cracked` = the winner cracked the code (not "last one standing");
    `record` = count it in the players' Hack stats (not for a game the host ended). Caller holds g.lock."""
    if svc.HACK.get(g.chat_id) is g:
        del svc.HACK[g.chat_id]
    g.phase, g.token = "done", g.token + 1
    _cancel(g.task)
    win = [winners] if isinstance(winners, svc.HackPlayer) else list(winners or [])
    recent = "\n".join(f"<code>{code}</code> 🔓{h} ⚡{gl} · {esc(name[:14])}" for name, code, h, gl in g.history[-6:])
    code = f"🔑 The code was <code>{g.secret}</code>\n" if g.secret else ""
    if win and g.solo:
        text = f"{title}\n\n{code}"
    elif win:
        try:
            shares = await svc.pay_winners(g, [p.uid for p in win])
            lines = "\n".join(f"💰 <b>{mention(p.uid, p.name)}</b> takes <b>+{num(a)}</b> 💎" for p, a in zip(win, shares))
            text = f"{title}\n\n{code}{lines}"
        except Exception:
            log.exception("hack payout failed - refunding")
            await svc.refund_all(g)
            text = f"⚠️ The payout failed, so every bet was refunded.\n\n{code}"
    else:
        await svc.refund_all(g)                                 # bet 0 (practice) -> nothing to give back
        text = f"{title}\n\n{code}" + ("" if g.solo else f"💸 Every bet was refunded ({num(g.bet)} 💎 each).")
    text += ("\n\n" + recent) if recent and win else ""
    if record and g.secret and not g.solo:                      # lifetime stats / ranks (best effort: never blocks the result)
        try:
            wins = await hs.record_game([p.uid for p in g.players], win[0].uid if win else None, win[0].guesses if win and cracked else None)
            if win and wins and HS.rank_for(wins - 1) != HS.rank_for(wins):
                em, title_ = HS.rank_for(wins)
                text += f"\n\n🎖 <b>{esc(win[0].name)}</b> ranked up: {em} <b>{title_}</b>!"
        except Exception:
            log.exception("hack stats failed")
    old = g.board_msg
    try:
        await (reply_to.reply(text) if reply_to is not None else bot.send_message(g.chat_id, text))
    finally:
        if old:
            try:
                await bot.delete_message(g.chat_id, old)
            except Exception:
                pass


async def _lobby_timer(bot: Bot, g: svc.HackGame) -> None:
    try:
        await asyncio.sleep(H.LOBBY_S)
        async with g.lock:
            if svc.HACK.get(g.chat_id) is not g or g.phase != "lobby":
                return
            if len(g.players) >= 2:
                await _hack_begin(bot, g)
            else:
                await _hack_finish(bot, g, None, "😴 <b>Nobody joined</b> - the hack was cancelled.")
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("hack lobby timer failed")


async def _turn_timer(bot: Bot, g: svc.HackGame, token: int) -> None:
    try:
        await asyncio.sleep(H.TURN_S)
        async with g.lock:
            if svc.HACK.get(g.chat_id) is not g or g.token != token or g.phase != "play":
                return
            cur = g.current()
            if cur is None:
                return
            cur.skips += 1
            if cur.skips >= H.MAX_FORFEIT:
                cur.forfeit = True
                note = f"💤 <b>{esc(cur.name)}</b> was away twice in a row and is out. (The bet stays in the pot.)"
            else:
                note = f"⏭ <b>{esc(cur.name)}</b> ran out of time - turn skipped ({cur.skips}/{H.MAX_FORFEIT})."
            left = g.active()
            if g.solo and not left:
                await _hack_finish(bot, g, None, f"{note}\n\n🛑 Practice ended.")
            elif not g.solo and len(left) == 1:
                await _hack_finish(bot, g, left[0], f"{note}\n\n🏁 <b>{esc(left[0].name)}</b> is the last hacker standing!")
            elif g.next_player() is None:
                await _hack_finish(bot, g, None, f"{note}\n\n💀 <b>Nobody cracked the code.</b>")
            else:
                await _prompt(bot, g, note)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("hack turn timer failed")


async def _hack_guess(m: Message, bot: Bot, g: svc.HackGame) -> bool:
    """True = the message was the current player's guess."""
    uid = m.from_user.id
    cur = g.current()
    if cur is None or cur.uid != uid:
        return False
    async with g.lock:
        cur = g.current()
        if svc.HACK.get(g.chat_id) is not g or g.phase != "play" or cur is None or cur.uid != uid:
            return False
        code = m.text.strip()
        if H.has_repeats(code):                                 # the code never repeats a digit: no try is used, the turn timer keeps running
            await m.reply("🔁 The code never repeats a digit - send a different one (no try used).")
            return True
        if any(c == code for _, c, _, _ in g.history):
            await m.reply("♻️ That code was already tried - look at the board and send a new one (no try used).")
            return True
        hacks, glitches = H.score(g.secret, code)
        cur.guesses += 1
        cur.skips = 0
        g.history.append((cur.name, code, hacks, glitches))
        if hacks == g.n:
            await _hack_finish(bot, g, cur, _win_title([cur]), reply_to=m, cracked=True)
        elif g.next_player() is None:
            await _hack_finish(bot, g, None, "💀 <b>Everybody is out of tries - nobody cracked the code.</b>", reply_to=m)
        else:
            await _prompt(bot, g, reply_to=m)
    return True


async def _join(bot: Bot, g: svc.HackGame, uid: int, name: str) -> None:
    async with g.lock:
        if svc.HACK.get(g.chat_id) is not g or g.phase != "lobby":
            raise GameError("🔐 This hack lobby is closed.")
        if g.player(uid) is not None:
            raise GameError("✅ You're already in this hack.")
        if len(g.players) >= H.MAX_PLAYERS:
            raise GameError("🔐 The lobby is full.")
        await svc.charge(g, uid)                                # raises 'not enough 💎' - nothing is taken then
        g.players.append(svc.HackPlayer(uid, name[:30]))
        await _update_lobby(bot, g)


async def _solo(m: Message, bot: Bot, user: User, n: int) -> None:
    """Free practice in a private chat: one player, no bet, no pot, nothing is paid or refunded."""
    chat = m.chat.id
    if user.id in hs.DAILY:
        raise GameError("📅 You have an open daily Hack - finish it, or send /hackdaily stop first.")
    await _reap(chat)
    what = svc.running(chat) or await svc.busy(chat)
    if what:
        raise GameError(_busy_text(what))
    g = svc.HackGame(chat, svc.new_gid(), n, 0, user.id, solo=True)
    g.players.append(svc.HackPlayer(user.id, m.from_user.full_name[:30]))
    svc.HACK[chat] = g                                          # reserved before any further await
    try:
        async with g.lock:
            await _hack_begin(bot, g)
    except BaseException:
        svc.HACK.pop(chat, None)
        raise


@router.message(Command("hack3", "hack4", "hack5", "hack6"))
async def hack_cmd(m: Message, command: CommandObject, bot: Bot, db_user: User):
    if m.from_user is None:
        return
    n = int((command.command or "hack4")[-1])
    if m.chat.type == "private":
        return await _solo(m, bot, db_user, n)                  # free practice against the bot
    args = (command.args or "").split()
    if not args:
        raise GameError(f"🔐 Usage: /hack{n} &lt;amount&gt; - e.g. /hack{n} 500 (everybody who joins bets the same)")
    w = await economy.balance(db_user.id)
    bet = parse_amount(args[0], min(w.balance, R.casino_max_bet))
    if bet is None:
        raise GameError("❌ Invalid bet. Try a number like <code>500</code>, <code>2k</code> or <code>all</code>.")
    if bet < R.casino_min_bet:
        raise GameError(f"❌ Minimum bet is <b>{R.casino_min_bet}</b> 💎.")
    if bet > R.casino_max_bet:
        raise GameError(f"❌ Maximum bet is <b>{R.casino_max_bet:,}</b> 💎.")
    chat = m.chat.id
    await _reap(chat)
    what = svc.running(chat) or await svc.busy(chat)
    if what:
        raise GameError(_busy_text(what))
    if svc.running(chat):                                       # lost a race while we looked
        raise GameError(_busy_text(svc.running(chat)))
    g = svc.HackGame(chat, svc.new_gid(), n, bet, db_user.id)
    svc.HACK[chat] = g                                          # reserved before any further await
    try:
        await svc.charge(g, db_user.id)
        g.players.append(svc.HackPlayer(db_user.id, m.from_user.full_name[:30]))
        msg = await m.answer(_lobby_text(g), reply_markup=_lobby_kb(g))
    except BaseException:
        svc.HACK.pop(chat, None)
        if g.players:                                           # charged, but the lobby message failed: give the bet back
            await svc.refund_all(g)
        raise
    g.lobby_msg = msg.message_id
    g.task = _spawn(_lobby_timer(bot, g))


@router.message(Command("joinhack"))
async def joinhack_cmd(m: Message, bot: Bot, db_user: User):
    if m.from_user is None:
        return
    g = svc.HACK.get(m.chat.id)
    if g is None or g.phase != "lobby":
        raise GameError("🔐 No hack lobby is open here. Start one with /hack4 &lt;amount&gt;")
    await _join(bot, g, db_user.id, m.from_user.full_name)
    await m.reply(f"✅ {mention(db_user.id, m.from_user.full_name)} joined the hack! Pot: <b>{num(g.pot)}</b> 💎")


@router.callback_query(F.data.startswith("hk:"))
async def hack_cb(cb: CallbackQuery, bot: Bot, db_user: User):
    parts = cb.data.split(":")
    if len(parts) != 3 or cb.message is None:
        return await safe_answer(cb)
    act, gid = parts[1], parts[2]
    g = svc.HACK.get(cb.message.chat.id)
    if g is None or g.gid != gid or g.phase != "lobby":
        return await safe_answer(cb, "🔐 This lobby is over.", alert=True)
    if act == "j":
        await _join(bot, g, db_user.id, cb.from_user.full_name)
        await safe_answer(cb, "✅ You're in!")
    elif act == "s":
        if cb.from_user.id != g.host:
            return await safe_answer(cb, "⛔ Only the host can start the game.", alert=True)
        async with g.lock:
            if svc.HACK.get(g.chat_id) is not g or g.phase != "lobby":
                return await safe_answer(cb)
            if len(g.players) < 2:
                return await safe_answer(cb, "👥 Wait for at least 2 players.", alert=True)
            await safe_answer(cb)
            await _hack_begin(bot, g)
    else:
        await safe_answer(cb)


# ============================================================================== 📊 stats, 🏆 leaderboard, 📅 daily code
def _left(seconds: int) -> str:
    h, m = divmod(max(0, seconds) // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _daily_intro(d: hs.DailyGame) -> str:
    top = HS.daily_reward(1, 0, d.n)[0]
    return (f"📅 <b>DAILY HACK</b> - one secret code, the same for everybody today!\n\n"
            f"I locked a code of <b>{d.n} different digits</b> (a 0 can be first). Send a code like <code>{'0123456789'[: d.n]}</code>:\n"
            "🔓 <b>hacks</b> - right digit, right position\n⚡ <b>glitches</b> - right digit, wrong position\n\n"
            f"<b>{H.max_guesses(d.n)}</b> tries, no timer, no bet. Crack it for 💎 <b>{HS.DAILY_BASE}</b> + <b>{HS.DAILY_PER_TRY}</b> per unused try "
            f"(up to <b>{top}</b>), +5 % per day of streak. Don't share the code - it's the same for everyone! 🤫\n"
            f"/hackdaily stop pauses (your tries are saved) · new code in {_left(hs.seconds_to_reset())}")


def _daily_board(d: hs.DailyGame) -> str:
    rows = HS.history_rows(d.secret, d.guesses)
    out = f"📅 <b>DAILY HACK</b> · {d.n}-digit code · {d.day:%d %b}\n\n"
    if rows:
        out += "\n".join(f"<code>{g}</code> 🔓{h_} ⚡{gl}" for _, g, h_, gl in rows) + "\n\n"
    gone = H.eliminated(rows)
    if gone:
        out += f"🚫 Not in the code: <code>{' '.join(gone)}</code>\n\n"
    return out + f"🎯 Tries left: <b>{H.max_guesses(d.n) - len(d.guesses)}</b> · new code in {_left(hs.seconds_to_reset())}"


async def _daily_solved(m: Message, d: hs.DailyGame) -> None:
    """The player cracked today's code: pay the reward once and show the result. Caller holds d.lock (or has no concurrent guesses)."""
    hs.DAILY.pop(d.uid, None)
    tries = len(d.guesses)
    text = f"🎉 <b>Cracked!</b> Today's code <code>{d.secret}</code> in <b>{tries}</b> {'try' if tries == 1 else 'tries'}!\n\n"
    try:
        amount, streak, bonus = await hs.pay_daily(d.uid, d.day, tries)
        if amount:
            text += f"💎 <b>+{num(amount)}</b>" + (f" · 🔥 {streak}-day streak (+{bonus} %)" if streak >= 2 else "") + "\n"
        else:
            text += "💎 Your reward for today was already paid.\n"
    except Exception:
        log.exception("daily hack payout failed")
        text += "⚠️ The reward could not be paid right now - send /hackdaily again in a minute to collect it.\n"
        hs.DAILY.pop(d.uid, None)
    text += f"\n🏆 /hlb daily - today's fastest · new code in {_left(hs.seconds_to_reset())}"
    await m.reply(text)


async def _daily_guess(m: Message, d: hs.DailyGame) -> bool:
    """True = the message was a guess at the daily code (and must not reach other handlers)."""
    day = hs.today()
    if d.day != day:                                            # midnight (UTC) passed while the attempt was open
        hs.DAILY.pop(d.uid, None)
        await m.reply("🌅 A new daily code is out! Send /hackdaily to start it.")
        return True
    code = m.text.strip()
    async with d.lock:
        if hs.DAILY.get(d.uid) is not d:
            return False
        if H.has_repeats(code):
            await m.reply("🔁 The code never repeats a digit - send a different one (no try used).")
            return True
        if code in d.guesses:
            await m.reply("♻️ You already tried that one (no try used).")
            return True
        d.guesses.append(code)
        await hs.save_guesses(d.uid, day, d.guesses)
        if code == d.secret:
            await _daily_solved(m, d)
        elif len(d.guesses) >= H.max_guesses(d.n):
            hs.DAILY.pop(d.uid, None)
            await hs.mark_failed(d.uid, day)
            await m.reply(_daily_board(d) + "\n\n💀 <b>Out of tries.</b> The code stays secret until tomorrow - come back for the next one!")
        else:
            await m.reply(_daily_board(d))
    return True


@router.message(Command("hackdaily"))
async def hackdaily_cmd(m: Message, command: CommandObject, db_user: User):
    if m.from_user is None:
        return
    if m.chat.type != "private":
        raise GameError("📅 The daily code is played in a private chat, so nobody sees your guesses. Open a chat with me and send /hackdaily")
    uid, day = db_user.id, hs.today()
    if (command.args or "").strip().lower() == "stop":
        hs.DAILY.pop(uid, None)
        await m.reply("⏸ Daily Hack paused - your tries are saved. Send /hackdaily to continue.")
        return
    if svc.HACK.get(m.chat.id) is not None:
        raise GameError("🔐 You have a practice game running - finish it or /endgame it first.")
    tries = await hs.solved_today(uid, day)
    if tries:
        raise GameError(f"✅ You already cracked today's code in <b>{tries}</b> {'try' if tries == 1 else 'tries'}. New code in {_left(hs.seconds_to_reset())} · /hlb daily")
    if await hs.has_failed(uid, day):
        raise GameError(f"💀 You're out of tries for today's code. New code in {_left(hs.seconds_to_reset())}.")
    d = hs.DAILY.get(uid)
    if d is None or d.day != day:
        d = hs.DailyGame(uid, day, hs.secret_for(day), await hs.load_guesses(uid, day), lock=asyncio.Lock())
        hs.DAILY[uid] = d
    if d.secret in d.guesses:                                   # solved, but the payout failed earlier: collect it now
        async with d.lock:
            await _daily_solved(m, d)
        return
    await m.answer(_daily_board(d) if d.guesses else _daily_intro(d))


@router.message(Command("hstats"))
async def hstats_cmd(m: Message):
    if m.from_user is None:
        return
    target = m.from_user
    rep_ = m.reply_to_message
    if rep_ is not None and rep_.from_user is not None and not rep_.from_user.is_bot:
        target = rep_.from_user
    sm = await hs.user_summary(target.id)
    solved = await hs.daily_solved_count(target.id)
    if sm["play"] == 0 and solved == 0:
        raise GameError(f"🔐 {esc(target.full_name)} has no Hack stats yet - play a group game with /hack4 &lt;amount&gt; or try /hackdaily in a private chat!")
    em, title = sm["rank"]
    out = f"🔐 <b>{esc(target.full_name)}</b> - Hack stats\n\n🎖 <b>{em} {title}</b>"
    if sm["next"]:
        out += f" · {sm['next'][0]} more {'win' if sm['next'][0] == 1 else 'wins'} to {sm['next'][1]}"
    out += f"\n🏆 Wins: <b>{sm['win']}</b> · 🎮 Games: <b>{sm['play']}</b> · 📈 Win rate: <b>{sm['rate']} %</b>"
    if sm["avg_tries"] is not None:
        out += f"\n🔓 Cracks: <b>{sm['avg_tries']}</b> tries on average" + (f" · fastest <b>{sm['fast']}</b>" if sm["fast"] else "")
    streak = await hs.daily_streak(target.id, hs.today())
    out += f"\n📅 Daily codes solved: <b>{solved}</b>" + (f" · 🔥 {streak}-day streak" if streak else "")
    await m.reply(out)


@router.message(Command("hlb"))
async def hlb_cmd(m: Message, command: CommandObject):
    args = [a.lower() for a in (command.args or "").split()]
    if "daily" in args:
        rows = await hs.daily_board(hs.today())
        if not rows:
            raise GameError("📅 Nobody has cracked today's daily code yet - be the first! Send /hackdaily in a private chat with me.")
        lines = [f"{MEDALS[i] if i < 3 else f'<b>{i + 1}.</b>'} <b>{esc(name[:20])}</b> - <b>{tries}</b> {'try' if tries == 1 else 'tries'}"
                 for i, (_uid, name, tries) in enumerate(rows)]
        await m.reply("📅 <b>Daily Hack</b> · today's fastest crackers\n\n" + "\n".join(lines) + "\n\n<i>/hackdaily in a private chat · /hlb for all-time wins</i>")
        return
    rows = await hs.leaderboard()
    if not rows:
        raise GameError("🔐 No Hack wins yet - win a group game with /hack4 &lt;amount&gt; to get on the board!")
    lines = []
    for i, (_uid, name, wins, plays) in enumerate(rows):
        em, title = HS.rank_for(wins)
        lines.append(f"{MEDALS[i] if i < 3 else f'<b>{i + 1}.</b>'} <b>{esc(name[:20])}</b> {em} {title} - <b>{wins}</b> {'win' if wins == 1 else 'wins'} · {plays} games")
    await m.reply("🏆 <b>Hack leaderboard</b> · all time\n\n" + "\n".join(lines) + "\n\n<i>/hlb daily - today's daily code · /hstats - your rank</i>")


# ============================================================================== /endgame
class _HasGame(Filter):
    """True while Wordly or Hack runs in the chat - otherwise /endgame falls through to WordSeek's own /endgame."""

    async def __call__(self, message: Message) -> bool:
        return svc.running(message.chat.id) is not None


@router.message(Command("endgame", "endwordly", "endhack"), _HasGame())
async def end_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    h = svc.HACK.get(m.chat.id)
    if h is not None:
        if m.from_user.id != h.host:
            raise GameError("⛔ Only the host can end the Hack game.")
        async with h.lock:
            if svc.HACK.get(m.chat.id) is h:
                await _hack_finish(bot, h, None, "🛑 <b>Hack ended by the host.</b>", reply_to=m, record=False)
        return
    w = svc.WORDLY.get(m.chat.id)
    if w is not None:
        if m.from_user.id != w.started_by and not await _is_admin(bot, m):
            raise GameError("⛔ Only the player who started Wordly or a group admin can end it.")
        await _wordly_finish(bot, w, "🛑 <b>Wordly ended.</b>")


# ============================================================================== guesses
class WordGamesMiddleware(BaseMiddleware):
    """Plain messages are moves while a game runs in that chat: a word of the rack (Wordly) or the current player's code (Hack).
    Everything else passes through untouched."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if isinstance(event, Message) and event.text and event.from_user and not event.from_user.is_bot:
                w = svc.WORDLY.get(event.chat.id)
                if w is not None and WL.is_candidate(event.text) and await _wordly_guess(event, w):
                    return None
                h = svc.HACK.get(event.chat.id)
                if h is not None and h.phase == "play" and H.is_guess(event.text, h.n) and await _hack_guess(event, data["bot"], h):
                    return None
                if event.chat.type == "private":                # an open daily attempt: a code of the right length is a guess
                    d = hs.DAILY.get(event.from_user.id)
                    if d is not None and H.is_guess(event.text, d.n) and await _daily_guess(event, d):
                        return None
        except Exception:
            log.exception("wordly/hack move failed")
        return await handler(event, data)
