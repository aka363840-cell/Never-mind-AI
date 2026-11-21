"""🐙 Word Jumble - a multiplayer word unscramble game with rounds, difficulties, themes, rewards and an optional entry-fee pot.
Deliberately NOT in the Telegram "/" menu (they are listed in /help, Games).

/jumble or /jum   start a new game: pick mode (rounds / race), difficulty, speed, theme and entry fee with the buttons, then Start
/jskip            vote to skip the current word
/jlb              leaderboard: /jlb [global|group] [today|week|month|all] · /jlb solo = best solo (private chat) games
/jstats           your Jumble stats and daily streak (reply to someone to see theirs)
/end              end the running Jumble (the player who started it, or a group admin); when no Jumble runs,
                  /end falls through to Wordly / Hack / WordSeek exactly as before
While a round is open, a plain message that unscrambles the word is the answer (needs normal group messages, same as WordSeek).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message, ReactionTypeEmoji, TelegramObject

from game import jumble as D, jumble_stats as S
from game.jumble_img import render_tiles
from services import jumble as svc
from services.errors import GameError
from utils.fmt import esc, mention, num
from utils.telegram import safe_answer

log = logging.getLogger(__name__)
router = Router()
MEDALS = ["🥇", "🥈", "🥉"]
_tasks: set[asyncio.Task] = set()
PERIOD_LABEL = {"today": "today", "week": "this week", "month": "last 30 days", "all": "all time"}


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
    return {"jumble": "🐙 A Jumble game is already running here - finish it or /end it first.",
            "wordly": "🔠 A Wordly game is running here - finish it or /end it first.",
            "hack": "🔐 A Hack game is running here - finish it or /end it first.",
            "WordSeek": "🔎 A WordSeek game is running here - finish it or /end it first."}.get(what, f"🎮 A {what} game is already running here - finish it or /end it first.")


async def _react(m: Message, emoji: str) -> bool:
    try:
        await m.react([ReactionTypeEmoji(emoji=emoji)])
        return True
    except Exception:
        return False


async def _delete(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if message_id:
        try:
            await bot.delete_message(chat_id, message_id)
        except Exception:
            pass


async def _reap(chat_id: int, bot: Bot | None = None) -> None:
    """A game that was never finished (bot lost rights, runner crashed ...) must not block the chat for ever."""
    g = svc.GAMES.get(chat_id)
    if g is not None and svc.now() - g.created > D.MAX_AGE_S:
        svc.GAMES.pop(chat_id, None)
        g.phase, g.open = "done", False
        _cancel(g.task)
        if g.entry and g.players:
            await svc.refund_all(g)


def _entry_choices() -> list[int]:
    """Entry fees offered in the panel: D.ENTRY_CHOICES inside the bot's casino bet limits."""
    try:
        from game.rules import R
        lo, hi = R.casino_min_bet, R.casino_max_bet
    except Exception:
        return list(D.ENTRY_CHOICES)
    return [n for n in D.ENTRY_CHOICES if lo <= n <= hi]


# ============================================================================== texts
def _mode_line(g: svc.JumbleGame) -> str:
    return f"🏁 Race to <b>{g.target}</b> points" if g.mode == "race" else f"🔢 <b>{g.rounds}</b> rounds"


def _diff_line(g: svc.JumbleGame) -> str:
    if g.diff == "mixed":
        return "🌈 <b>Mixed</b> - words get harder as the game goes on"
    d = D.diff(g.diff)
    return f"{d.icon} <b>{d.label}</b> - {d.length_label}-letter words · {D.time_for(g.diff, g.speed)}s per word"


def _theme_line(g: svc.JumbleGame) -> str:
    ic, lab = D.THEME_LABELS.get(g.theme, D.THEME_LABELS["all"])
    return f"{ic} <b>{lab}</b>"


def _entry_line(g: svc.JumbleGame) -> str:
    return (f"💰 <b>{num(g.entry)}</b> 💎 per player - the best player takes the whole pot" if g.entry else "🆓 <b>Free</b> - play for points and daily rewards")


def _setup_text(g: svc.JumbleGame) -> str:
    sp = D.SPEEDS.get(g.speed, D.SPEEDS[D.DEFAULT_SPEED])
    return ("🐙 <b>WORD JUMBLE</b>\n"
            "A multiplayer word unscramble game!\n\n"
            "Choose your settings, then press <b>Start</b>.\n\n"
            f"🎮 Mode: {_mode_line(g)}\n"
            f"🎚 Difficulty: {_diff_line(g)}\n"
            f"⏲ Speed: <b>{sp[0]} {sp[1]}</b>\n"
            f"🎨 Theme: {_theme_line(g)}\n"
            + (f"💰 Entry: {_entry_line(g)}" if g.chat_id < 0 else "🆓 Solo play in a private chat - no rewards"))


def _setup_kb(g: svc.JumbleGame) -> InlineKeyboardMarkup:
    def btn(label: str, act: str, val, on: bool) -> B:
        return B(text=("✅ " if on else "") + label, callback_data=f"jm:{act}:{g.gid}:{val}")
    rows = [[btn("🔢 Rounds", "m", "rounds", g.mode == "rounds"), btn("🏁 Race", "m", "race", g.mode == "race")]]
    if g.mode == "rounds":
        rows.append([btn(f"{n} rounds", "r", n, g.rounds == n) for n in D.ROUND_CHOICES])
    else:
        rows.append([btn(f"{n} pts", "t", n, g.target == n) for n in D.TARGET_CHOICES])
    rows.append([btn(f"{D.DIFFS[k].icon} {D.DIFFS[k].label}", "d", k, g.diff == k) for k in ("easy", "medium", "hard")])
    rows.append([btn(f"{D.DIFFS['extreme'].icon} Extreme", "d", "extreme", g.diff == "extreme"), btn("🌈 Mixed", "d", "mixed", g.diff == "mixed")])
    rows.append([btn(f"{ic} {lab}", "s", k, g.speed == k) for k, (ic, lab, _) in D.SPEEDS.items()])
    rows.append([btn(ic, "h", k, g.theme == k) for k, (ic, _) in D.THEME_LABELS.items()])
    if g.chat_id < 0:
        choices = _entry_choices()
        if choices:
            rows.append([btn("🆓 Free", "e", 0, g.entry == 0)] + [btn(f"💰 {num(n)}", "e", n, g.entry == n) for n in choices])
    rows.append([B(text="▶️ Start", callback_data=f"jm:go:{g.gid}"), B(text="✖️ Cancel", callback_data=f"jm:x:{g.gid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _lobby_text(g: svc.JumbleGame) -> str:
    names = "\n".join(f"{i + 1}. {mention(uid, name)}{' 👑' if uid == g.host else ''}" for i, (uid, name) in enumerate(g.players.items()))
    return (f"💰 <b>WORD JUMBLE</b> - entry <b>{num(g.entry)}</b> 💎 · pot <b>{num(g.pot)}</b> 💎\n\n"
            f"🎮 {_mode_line(g)} · {_theme_line(g)}\n🎚 {_diff_line(g)}\n\n"
            f"👥 <b>Players</b> ({len(g.players)}/{D.MAX_POT_PLAYERS})\n{names}\n\n"
            f"Join with the button (same entry fee). The best player takes the pot. Starts in {D.LOBBY_S}s with 2+ players, "
            "or when the host taps Start. /end cancels and refunds everybody.")


def _lobby_kb(g: svc.JumbleGame) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text=f"🔓 Join ({num(g.entry)} 💎)", callback_data=f"jm:j:{g.gid}"),
                                                  B(text="▶️ Start", callback_data=f"jm:b:{g.gid}")]])


def _intro(g: svc.JumbleGame) -> str:
    sp = D.SPEEDS.get(g.speed, D.SPEEDS[D.DEFAULT_SPEED])
    return (f"🐙 <b>WORD JUMBLE</b> - {_mode_line(g)}\n\n"
            f"🎚 {_diff_line(g)}\n🎨 {_theme_line(g)} · {sp[0]} {sp[1]}\n"
            + (f"💰 Pot: <b>{num(g.pot)}</b> 💎 - the best player takes it all\n" if g.entry else "")
            + "\nI show a scrambled word. Type the real word in the chat - the <b>first correct answer</b> wins the round!\n"
              "• points per word + a speed bonus (up to +2)\n"
              "• 💡 a letter is revealed at 50 % and 75 % of the time (−1 point each)\n"
              "• any real anagram counts · /jskip to vote to skip · /end to stop early\n\n"
              "Get ready… 🚀")


def _head(g: svc.JumbleGame) -> str:
    d = D.DIFFS.get(g.rdiff, D.diff(g.diff))
    rnd = (f"round <b>{g.round_no}/{g.rounds}</b>" if g.mode == "rounds"
           else f"round <b>{g.round_no}</b> · first to <b>{g.target}</b> pts")
    theme = f" · {D.THEME_LABELS[g.theme][0]} {D.THEME_LABELS[g.theme][1]}" if g.theme != "all" else ""
    pot = f" · 💰 {num(g.pot)} 💎" if g.entry else ""
    return f"🐙 <b>JUMBLE</b> · {rnd} · {d.icon} {d.label}{theme}{pot}"


def _standings(g: svc.JumbleGame) -> str:
    top = svc._ranking(g)[:3]
    return "📊 " + " · ".join(f"{esc(n[:16])} <b>{p}</b>" for _, n, p, _ in top) if top else ""


def _puzzle_text(g: svc.JumbleGame) -> str:
    out = f"{_head(g)}\n\n" + ("" if g.photo else f"<code>{D.show(g.scrambled)}</code>\n")
    if g.hints:
        out += f"💡 <code>{D.hint(g.word, g.hints)}</code>\n"
    out += f"\nUnscramble the word! ⏱ <b>{g.rtime}s</b> · /jskip to skip"
    stand = _standings(g)
    return out + (f"\n\n{stand}" if stand else "")


def _also(g: svc.JumbleGame, typed: str = "") -> str:
    others = sorted(g.answers - {g.word, typed})
    return ("\n🔀 Also valid: " + " ".join(w.upper() for w in others[:6])) if others else ""


def _final_text(g: svc.JumbleGame, title: str, reveal: str = "", paid: dict | None = None, pot_text: str = "") -> str:
    paid = paid or {}
    mode = f"first to {g.target} pts" if g.mode == "race" else f"{g.round_no}/{g.rounds} rounds"
    mix = "🌈 Mixed" if g.diff == "mixed" else f"{D.diff(g.diff).icon} {D.diff(g.diff).label}"
    theme = f" · {D.THEME_LABELS[g.theme][0]} {D.THEME_LABELS[g.theme][1]}" if g.theme != "all" else ""
    head = f"{title}\n\n🐙 <b>Word Jumble</b> · {mode} · {mix}{theme}\n\n"
    rank = svc._ranking(g)
    if not rank:
        body = "Nobody solved a word this time 😅"
    else:
        lines = []
        for i, (uid, name, pts, n) in enumerate(rank[:10]):
            extra = ""
            if uid in paid:
                amount, streak, _bonus = paid[uid]
                extra = f" · 💎 +{num(amount)}" + (f" 🔥{streak}" if streak >= 2 else "")
            lines.append(f"{MEDALS[i] if i < 3 else f'<b>{i + 1}.</b>'} <b>{esc(name[:20])}</b> - <b>{pts}</b> pts · "
                         f"{n} {'word' if n == 1 else 'words'}{extra}")
        body = "\n".join(lines)
        if paid:
            body += "\n\n<i>💎 = points reward · 🔥 = day streak (+5 % per day)</i>"
    if pot_text:
        body += f"\n\n{pot_text}"
    if reveal:
        body += f"\n\n🔤 The unsolved word was <b>{reveal.upper()}</b>"
    return head + body


def _again_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="🔁 Play again", callback_data="jm:new")]])


# ============================================================================== start / setup / lobby
async def _new(bot: Bot, chat_id: int, uid: int) -> None:
    await _reap(chat_id)
    cur = svc.GAMES.get(chat_id)
    if cur is not None:
        raise GameError("🐙 A Jumble game is already running here - finish it or /end it first." if cur.phase == "play"
                        else "🐙 A Jumble setup is already open here - pick the settings, or /end it.")
    what = await svc.busy(chat_id)
    if what:
        raise GameError(_busy_text(what))
    if chat_id in svc.GAMES:                                   # somebody else won the race while we looked
        raise GameError(_busy_text("jumble"))
    g = svc.JumbleGame(chat_id, svc.new_gid(), uid)
    svc.GAMES[chat_id] = g                                     # reserved before any further await
    try:
        msg = await bot.send_message(chat_id, _setup_text(g), reply_markup=_setup_kb(g))
    except BaseException:
        svc.GAMES.pop(chat_id, None)
        raise
    g.setup_msg = msg.message_id
    g.task = _spawn(_setup_timer(bot, g))


async def _edit_setup(bot: Bot, g: svc.JumbleGame, text: str, kb: InlineKeyboardMarkup | None) -> None:
    if g.setup_msg:
        try:
            await bot.edit_message_text(text, chat_id=g.chat_id, message_id=g.setup_msg, reply_markup=kb)
        except Exception:                                      # "message is not modified" etc.
            pass


async def _setup_timer(bot: Bot, g: svc.JumbleGame) -> None:
    try:
        await asyncio.sleep(D.SETUP_S)
        async with g.lock:
            if svc.GAMES.get(g.chat_id) is not g or g.phase != "setup":
                return
            del svc.GAMES[g.chat_id]
            g.phase = "done"
        await _edit_setup(bot, g, "⌛ <b>Jumble setup expired.</b> Start again with /jumble", None)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("jumble setup timer failed")
        svc.GAMES.pop(g.chat_id, None)


async def _lobby_timer(bot: Bot, g: svc.JumbleGame) -> None:
    try:
        await asyncio.sleep(D.LOBBY_S)
        async with g.lock:
            if svc.GAMES.get(g.chat_id) is not g or g.phase != "lobby":
                return
            if len(g.players) >= D.MIN_POT_PLAYERS:
                await _begin(bot, g)
                return
            del svc.GAMES[g.chat_id]
            g.phase = "done"
        await svc.refund_all(g)
        await _edit_setup(bot, g, f"😴 <b>Nobody joined</b> - the game was cancelled and the entry fee ({num(g.entry)} 💎) refunded.", None)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("jumble lobby timer failed")


async def _begin(bot: Bot, g: svc.JumbleGame) -> None:
    """Setup / lobby -> play. Caller holds g.lock."""
    g.phase = "play"
    if g.mode == "race":
        g.rounds = D.MAX_RACE_ROUNDS                           # a race nobody wins stops after this many words
    _cancel(g.task)
    await _edit_setup(bot, g, _intro(g), None)
    g.task = _spawn(_run(bot, g))


@router.message(Command("jumble", "jum"))
async def jumble_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    svc.NAMES[m.from_user.id] = m.from_user.full_name[:30]
    await _new(bot, m.chat.id, m.from_user.id)


@router.callback_query(F.data.startswith("jm:"))
async def jumble_cb(cb: CallbackQuery, bot: Bot):
    parts = cb.data.split(":")
    if cb.message is None or len(parts) < 2:
        return await safe_answer(cb)
    act = parts[1]
    if act == "new":                                           # "Play again" under the final ranking
        await safe_answer(cb)
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _new(bot, cb.message.chat.id, cb.from_user.id)
        return
    if len(parts) < 3:
        return await safe_answer(cb)
    val = parts[3] if len(parts) > 3 else ""
    g = svc.GAMES.get(cb.message.chat.id)
    if g is None or g.gid != parts[2]:
        return await safe_answer(cb, "🐙 This game panel is over.", alert=True)
    uid, name = cb.from_user.id, cb.from_user.full_name[:30]

    # ---- paid game lobby: join / host starts
    if act in ("j", "b"):
        if g.phase != "lobby":
            return await safe_answer(cb, "🐙 This lobby is over.", alert=True)
        if act == "j":
            async with g.lock:
                if svc.GAMES.get(g.chat_id) is not g or g.phase != "lobby":
                    return await safe_answer(cb, "🐙 This lobby is over.", alert=True)
                if uid in g.players:
                    return await safe_answer(cb, "✅ You're already in.", alert=True)
                if len(g.players) >= D.MAX_POT_PLAYERS:
                    return await safe_answer(cb, "🐙 The lobby is full.", alert=True)
                await svc.charge(g, uid)                       # raises 'not enough 💎' - nothing is taken then
                g.players[uid] = name
                svc.NAMES[uid] = name
                await safe_answer(cb, "✅ You're in!")
                await _edit_setup(bot, g, _lobby_text(g), _lobby_kb(g))
            return
        if uid != g.host:
            return await safe_answer(cb, "⛔ Only the host can start the game.", alert=True)
        async with g.lock:
            if svc.GAMES.get(g.chat_id) is not g or g.phase != "lobby":
                return await safe_answer(cb)
            if len(g.players) < D.MIN_POT_PLAYERS:
                return await safe_answer(cb, "👥 Wait for at least 2 players.", alert=True)
            await safe_answer(cb)
            await _begin(bot, g)
        return

    # ---- setup panel (host only)
    if g.phase != "setup":
        return await safe_answer(cb, "🐙 This setup is over.", alert=True)
    if uid != g.host:
        return await safe_answer(cb, "⛔ Only the player who started the game can change this.", alert=True)

    if act == "go":
        async with g.lock:
            if svc.GAMES.get(g.chat_id) is not g or g.phase != "setup":
                return await safe_answer(cb)
            if g.entry:                                        # paid game: the host pays first, then the lobby opens
                await svc.charge(g, uid)
                g.players[uid] = name
                g.phase = "lobby"
                _cancel(g.task)
                g.task = _spawn(_lobby_timer(bot, g))
                await safe_answer(cb)
                await _edit_setup(bot, g, _lobby_text(g), _lobby_kb(g))
            else:
                await safe_answer(cb)
                await _begin(bot, g)
        return
    if act == "x":
        async with g.lock:
            if svc.GAMES.get(g.chat_id) is not g or g.phase != "setup":
                return await safe_answer(cb)
            del svc.GAMES[g.chat_id]
            g.phase = "done"
            _cancel(g.task)
        await safe_answer(cb)
        await _edit_setup(bot, g, "✖️ <b>Jumble cancelled.</b>", None)
        return

    changed = True
    if act == "m" and val in D.MODES:
        g.mode = val
    elif act == "r" and val.isdigit() and int(val) in D.ROUND_CHOICES:
        g.rounds = int(val)
    elif act == "t" and val.isdigit() and int(val) in D.TARGET_CHOICES:
        g.target = int(val)
    elif act == "d" and val in D.CHOICES:
        g.diff = val
        if val == "extreme":
            g.theme = "all"                                    # themes only have 4-6 letter words
    elif act == "s" and val in D.SPEEDS:
        g.speed = val
    elif act == "h" and val in D.THEME_LABELS:
        g.theme = val
        if val != "all" and g.diff == "extreme":
            g.diff = "hard"
    elif act == "e" and val.isdigit() and g.chat_id < 0 and (int(val) == 0 or int(val) in _entry_choices()):
        g.entry = int(val)
    else:
        changed = False
    await safe_answer(cb)
    if changed:
        await _edit_setup(bot, g, _setup_text(g), _setup_kb(g))


# ============================================================================== the game
async def _wait(ev: asyncio.Event, seconds: float) -> bool:
    """True = the event was set (round solved / skipped) within `seconds`."""
    try:
        await asyncio.wait_for(ev.wait(), max(0.0, seconds))
        return True
    except asyncio.TimeoutError:
        return False


async def _edit_round(bot: Bot, g: svc.JumbleGame, text: str) -> None:
    """Edit the one round message (puzzle -> hint -> result); if it vanished, post it again."""
    if g.msg_id:
        try:
            if g.photo:
                await bot.edit_message_caption(chat_id=g.chat_id, message_id=g.msg_id, caption=text)
            else:
                await bot.edit_message_text(text, chat_id=g.chat_id, message_id=g.msg_id)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
        except Exception:
            pass
    try:
        g.photo = False                                        # a re-posted message is plain text
        msg = await bot.send_message(g.chat_id, text)
        g.msg_id = msg.message_id
    except Exception:
        pass


async def _send_puzzle(bot: Bot, g: svc.JumbleGame) -> Message:
    """Post the puzzle as a picture of letter tiles (a picture can't be pasted into an anagram solver). If the picture can't be
    made or sent, fall back to the plain text puzzle so the game never stops. Caller holds g.lock."""
    g.photo = True
    try:
        img = await asyncio.to_thread(render_tiles, g.scrambled)
        return await bot.send_photo(g.chat_id, BufferedInputFile(img, filename="jumble.png"), caption=_puzzle_text(g))
    except Exception:
        log.warning("jumble: tile picture failed - posting the puzzle as text", exc_info=True)
        g.photo = False
        return await bot.send_message(g.chat_id, _puzzle_text(g))


async def _near_miss(m: Message, g: svc.JumbleGame) -> None:
    """A real word of the right length that is not an answer: one 🤔 per player and round, so they know the bot saw it."""
    uid = m.from_user.id
    if uid in g.near or (g.entry and uid not in g.players) or not D.is_word(m.text):
        return
    g.near.add(uid)
    await _react(m, "🤔")


async def _run(bot: Bot, g: svc.JumbleGame) -> None:
    """The round loop: puzzle -> hints -> winner / skip / time-out -> short break -> next round, until the rounds are over
    (or somebody reaches the race target)."""
    try:
        await asyncio.sleep(3)                                 # let people read the intro
        i = 0
        while True:
            i += 1
            key = D.round_diff(g.diff, i, g.rounds, race=g.mode == "race")
            time_s = D.time_for(key, g.speed)
            async with g.lock:
                if svc.GAMES.get(g.chat_id) is not g:
                    return
                g.round_no, g.rdiff, g.rtime, g.hints = i, key, time_s, 0
                g.skip_votes, g.skipped, g.winner, g.near = set(), False, None, set()
                g.word, g.scrambled, g.answers = D.new_puzzle(key, g.theme, g.used)
                g.used.add(g.word)
                g.solved = asyncio.Event()
                old = g.msg_id
                msg = await _send_puzzle(bot, g)
                g.msg_id = msg.message_id
                g.started = svc.now()
                g.open = True
            await _delete(bot, g.chat_id, old)                 # only the newest round message stays in the chat
            solved = False
            for n, at in enumerate(D.hint_times(time_s), 1):
                if await _wait(g.solved, g.started + at - svc.now()):
                    solved = True
                    break
                async with g.lock:
                    if g.open and svc.GAMES.get(g.chat_id) is g:
                        g.hints = n
                        await _edit_round(bot, g, _puzzle_text(g))
            if not solved:
                await _wait(g.solved, g.started + time_s - svc.now())
            async with g.lock:
                if svc.GAMES.get(g.chat_id) is not g:
                    return
                if g.open:                                     # nobody got it
                    g.open = False
                    g.idle += 1
                    text = f"⌛ <b>Time's up!</b> The word was <b>{g.word.upper()}</b>{_also(g)}"
                elif g.skipped:                                # skipped by vote (not "idle": people are there)
                    text = f"⏭ <b>Skipped.</b> The word was <b>{g.word.upper()}</b>{_also(g)}"
                else:                                          # solved: _guess() stored the winner
                    g.idle = 0
                    _uid, wname, typed, pts, secs = g.winner
                    text = f"✅ <b>{esc(wname[:24])}</b> got it! <b>{typed.upper()}</b> · +{pts} pts · {secs:.1f}s{_also(g, typed)}"
                best = svc._ranking(g)[:1]
                race_won = g.mode == "race" and bool(best) and best[0][2] >= g.target
                idle_stop = g.idle >= D.IDLE_ROUNDS
                last = race_won or idle_stop or i >= g.rounds
                stand = _standings(g)
                text = f"{_head(g)}\n\n{text}" + (f"\n\n{stand}" if stand else "") + ("" if last else f"\n\n⏭ Next word in {D.BREAK_S}s…")
                await _edit_round(bot, g, text)
                title = (f"🏁 <b>{esc(best[0][1][:24])}</b> reached <b>{g.target}</b> points!" if race_won
                         else "😴 <b>Nobody is playing - game over.</b>" if idle_stop else "🏁 <b>Game over!</b>")
            if last:
                await _finish(bot, g, title)
                return
            await asyncio.sleep(D.BREAK_S)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("jumble game failed")
        g2 = svc.GAMES.get(g.chat_id)
        if g2 is g:
            del svc.GAMES[g.chat_id]
        g.phase, g.open = "done", False
        if g.entry and g.players:
            await svc.refund_all(g)
        try:
            await bot.send_message(g.chat_id, "⚠️ The Jumble game hit an error and was stopped." + (" Every entry fee was refunded." if g.entry else ""))
        except Exception:
            pass


async def _settle_pot(g: svc.JumbleGame, refund: bool) -> str:
    """Paid game: pay the best player(s), or give every entry fee back. Returns the text for the final message."""
    rank = [r for r in svc._ranking(g) if r[2] > 0 and r[0] in g.players]
    if refund or not rank:
        await svc.refund_all(g)
        return f"💸 Every entry fee was refunded ({num(g.entry)} 💎 each)."
    winners = [r for r in rank if r[2] == rank[0][2]]
    try:
        shares = await svc.pay_pot(g, [w[0] for w in winners])
    except Exception:
        log.exception("jumble payout failed - refunding")
        await svc.refund_all(g)
        return "⚠️ The payout failed, so every entry fee was refunded."
    lines = "\n".join(f"💰 <b>{esc(w[1][:20])}</b> takes <b>+{num(s)}</b> 💎" for w, s in zip(winners, shares))
    return lines + ("\n<i>(tie - the pot was split)</i>" if len(winners) > 1 else "")


async def _finish(bot: Bot, g: svc.JumbleGame, title: str, refund: bool = False) -> None:
    async with g.lock:
        if svc.GAMES.get(g.chat_id) is not g:
            return
        del svc.GAMES[g.chat_id]
        reveal = g.word if g.open else ""
        g.phase, g.open = "done", False
    _cancel(g.task)
    pot_text = await _settle_pot(g, refund) if g.entry and g.players else ""
    paid: dict = {}
    try:
        paid = await svc.record_results(g)
    except Exception:
        log.exception("jumble rewards failed")
    solo_text = ""
    if g.chat_id > 0:                                          # private chat = solo: no reward, but a personal best
        try:
            for pts, before, new_best in (await svc.record_solo(g)).values():
                solo_text = (f"\n\n🏅 <b>New personal best: {pts} pts!</b>" + (f" (before: {before})" if before else "")) if new_best \
                    else f"\n\n🏅 Your best solo game: <b>{before}</b> pts · /jlb solo"
        except Exception:
            log.exception("jumble solo stats failed")
    await bot.send_message(g.chat_id, _final_text(g, title, reveal, paid, pot_text) + solo_text, reply_markup=_again_kb())
    await _delete(bot, g.chat_id, g.msg_id)


async def _guess(m: Message, g: svc.JumbleGame) -> bool:
    """True = the message was the winning answer of the open round (and must not reach other handlers)."""
    w = m.text.strip().lower()
    uid = m.from_user.id
    if not g.open or w not in g.answers or (g.entry and uid not in g.players):
        return False
    async with g.lock:
        if svc.GAMES.get(g.chat_id) is not g or not g.open or w not in g.answers:
            return False
        secs = max(0.0, svc.now() - g.started)
        pts = D.points(g.rdiff, secs, g.rtime, g.hints)
        name = m.from_user.full_name[:30]
        svc.NAMES[uid] = name
        row = g.scores.setdefault(uid, [name, 0, 0])
        row[0], row[1], row[2] = name, row[1] + pts, row[2] + 1
        g.winner = (uid, name, w, pts, secs)
        g.open = False
        if g.solved is not None:
            g.solved.set()
    await _react(m, "🎉")
    return True


# ============================================================================== /jskip
@router.message(Command("jskip"))
async def skip_cmd(m: Message):
    if m.from_user is None:
        return
    g = svc.GAMES.get(m.chat.id)
    if g is None or g.phase != "play" or not g.open:
        raise GameError("🐙 No Jumble word is open right now.")
    uid = m.from_user.id
    if g.entry and uid not in g.players:
        raise GameError("⛔ Only the players of this paid game can vote.")
    async with g.lock:
        if not g.open or svc.GAMES.get(g.chat_id) is not g:
            return
        g.skip_votes.add(uid)
        need = 1 if m.chat.type == "private" else (len(g.players) // 2 + 1 if g.entry else 2)
        have = len(g.skip_votes)
        if have >= need:
            g.open, g.skipped = False, True
            if g.solved is not None:
                g.solved.set()                                 # the round loop posts the result
            return
    if not await _react(m, "👍"):
        await m.reply(f"⏭ Skip votes: {have}/{need}")


# ============================================================================== /end
class _HasJumble(Filter):
    """True while a Jumble (setup, lobby or play) exists in the chat - otherwise /end falls through to the other games' /end."""

    async def __call__(self, message: Message) -> bool:
        return message.chat.id in svc.GAMES


@router.message(Command("end"), _HasJumble())
async def end_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    g = svc.GAMES.get(m.chat.id)
    if g is None:
        return
    if m.from_user.id != g.host and not await _is_admin(bot, m):
        raise GameError("⛔ Only the player who started Jumble or a group admin can end it.")
    if g.phase in ("setup", "lobby"):
        async with g.lock:
            if svc.GAMES.get(g.chat_id) is not g:
                return
            del svc.GAMES[g.chat_id]
            was_lobby, g.phase = g.phase == "lobby", "done"
            _cancel(g.task)
        if g.entry and g.players:
            await svc.refund_all(g)
        await _edit_setup(bot, g, "✖️ <b>Jumble cancelled.</b>" + (" Every entry fee was refunded." if g.entry and was_lobby else ""), None)
        await m.reply("🐙 Jumble cancelled.")
        return
    await _finish(bot, g, "🛑 <b>Jumble ended.</b>", refund=bool(g.entry))


# ============================================================================== /jlb and /jstats
async def _name(bot: Bot, uid: int, chat_id: int) -> str:
    n = svc.NAMES.get(uid)
    if n:
        return n
    try:
        n = (await bot.get_chat_member(chat_id, uid)).user.full_name if chat_id < 0 else None
    except Exception:
        n = None
    if not n:
        try:
            ch = await bot.get_chat(uid)
            n = ch.full_name or ch.first_name
        except Exception:
            n = None
    n = (n or f"Player {uid % 10000}")[:30]
    svc.NAMES[uid] = n
    return n


@router.message(Command("jlb"))
async def jlb_cmd(m: Message, command: CommandObject, bot: Bot):
    args = [a.lower() for a in (command.args or "").split()]
    if "solo" in args:                                         # best single solo (private chat) games
        rows = await svc.solo_leaderboard()
        if not rows:
            raise GameError("🐙 No solo scores yet - play /jumble in a private chat with me to set a personal best!")
        lines = [f"{MEDALS[i] if i < 3 else f'<b>{i + 1}.</b>'} <b>{esc(name[:20])}</b> - best <b>{best}</b> pts · {games} solo {'game' if games == 1 else 'games'}"
                 for i, (_uid, name, best, games) in enumerate(rows)]
        await m.reply("🎯 <b>Jumble solo - personal bests</b>\n\n" + "\n".join(lines) + "\n\n<i>Play /jumble in a private chat · /jlb for group scores</i>")
        return
    glob = "global" in args or m.chat.type == "private"
    period = next((a for a in args if a in S.PERIODS), "week")
    rows = await svc.leaderboard(None if glob else m.chat.id, period)
    where = "global" if glob else "this group"
    if not rows:
        raise GameError(f"🐙 No Jumble scores yet ({where}, {PERIOD_LABEL[period]}). Win games with /jumble to get on the board!")
    lines = []
    for i, (uid, st) in enumerate(rows):
        lines.append(f"{MEDALS[i] if i < 3 else f'<b>{i + 1}.</b>'} <b>{esc(await _name(bot, uid, m.chat.id))}</b> - "
                     f"<b>{st.points}</b> pts · {st.words} {'word' if st.words == 1 else 'words'} · {st.wins} {'win' if st.wins == 1 else 'wins'}")
    await m.reply(f"🏆 <b>Jumble leaderboard</b> · {where} · {PERIOD_LABEL[period]}\n\n" + "\n".join(lines)
                  + "\n\n<i>/jlb [global|group] [today|week|month|all]</i>")


@router.message(Command("jstats"))
async def jstats_cmd(m: Message, bot: Bot):
    if m.from_user is None:
        return
    target = m.from_user
    rep = m.reply_to_message
    if rep is not None and rep.from_user is not None and not rep.from_user.is_bot:
        target = rep.from_user
    info = await svc.user_stats(target.id)
    st = info["stat"]
    solo = await svc.solo_stats(target.id)
    if st.games == 0 and solo is None:
        raise GameError(f"🐙 {esc(target.full_name)} has no Jumble stats yet - play a group game with /jumble, or a solo game in a private chat!")
    out = f"🐙 <b>{esc(target.full_name)}</b> - Jumble stats"
    if st.games:
        streak = info["streak"]
        out += (f"\n\n⭐ Points: <b>{num(st.points)}</b> · 📝 Words: <b>{num(st.words)}</b>\n"
                f"🎮 Games: <b>{st.games}</b> · 🏆 Wins: <b>{st.wins}</b>\n"
                f"🔥 Streak: <b>{streak}</b> {'day' if streak == 1 else 'days'}"
                + (f" (+{min(streak, S.STREAK_MAX_DAYS) * S.STREAK_STEP_PCT} % reward)" if streak else "") + "\n"
                f"💎 Earned today: <b>{num(info['earned_today'])}</b> / {num(S.DAILY_REWARD_CAP)}")
    if solo is not None:
        out += (f"\n\n🎯 <b>Solo</b> (private chat)\n🏅 Best game: <b>{num(solo['best'])}</b> pts · 🎮 Games: <b>{solo['games']}</b>\n"
                f"⭐ Points: <b>{num(solo['points'])}</b> · 📝 Words: <b>{num(solo['words'])}</b>")
    await m.reply(out)


# ============================================================================== answers typed as normal messages
class JumbleMiddleware(BaseMiddleware):
    """While a round is open, a plain message that unscrambles the word is the answer (edited messages count too when the bot
    receives them). Everything else passes through untouched."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if isinstance(event, Message) and event.text and event.from_user and not event.from_user.is_bot:
                g = svc.GAMES.get(event.chat.id)
                if g is not None and g.phase == "play" and g.open and D.is_guess(event.text, D.DIFFS.get(g.rdiff, D.diff(g.diff)).lengths):
                    if await _guess(event, g):
                        return None
                    await _near_miss(event, g)
        except Exception:
            log.exception("jumble answer failed")
        return await handler(event, data)
