"""Arcade & social commands. Plain commands + inline buttons only (no web app).

/arcade hub · /scratch · /rps · /ttt · /hangman · /wyr · /riddle · /fact · /horoscope · /fortune · /rep /toprep · /interest
"""
from __future__ import annotations

import json
import secrets

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database.models import User
from database.session import db
from game.rules import R
from keyboards.menus import back, kb
from services import arcade, economy
from services.errors import GameError
from services.kv import kv
from utils import deck, paging
from utils.fmt import bar, dur, esc, mention, num, parse_amount
from utils.targets import resolve_target
from utils.telegram import safe_answer, safe_edit

router = Router()
DIV = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"


async def _say(msg: Message, text: str, markup: InlineKeyboardMarkup | None = None, reply: bool = True) -> Message:
    return await (msg.reply if reply else msg.answer)(text, reply_markup=markup)


async def _name(uid: int) -> str:
    async with db.tx() as s:
        return (await s.execute(select(User.first_name).where(User.id == uid))).scalar() or "Player"


def _gid() -> str:
    return secrets.token_urlsafe(5)


# =============================================================== hub
async def hub(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    st = await arcade.stats_of(uid)
    text = (f"🕹 <b>Arcade &amp; Social</b>\n{DIV}\n"
            f"🌟 Reputation <b>{st.get('rep', 0)}</b> · 🎟 Cards <b>{st.get('scratch', 0)}</b> · "
            f"⭕ TTT wins <b>{st.get('ttt_win', 0)}</b>/{st.get('ttt_play', 0)} · 🔤 Hangman wins <b>{st.get('hangman_win', 0)}</b>\n\n"
            f"🎟 /scratch — {arcade.SCRATCH_COST} 💎 card, match 3 to win up to <b>3,000</b>\n"
            "✊ /rps <i>[bet]</i> — rock-paper-scissors vs me\n"
            "⭕ /ttt <i>[reply/@user]</i> — tic-tac-toe with a friend (winner takes 25 💎)\n"
            "🔤 /hangman — guess the word, win 💎\n"
            "🤔 /wyr — would you rather? the group votes live\n"
            "🧩 /riddle · 💡 /fact · 🔮 /horoscope · 🥠 /fortune\n"
            "🌟 /rep <i>(reply)</i> · /toprep — give respect, climb the board\n"
            f"🏦 /interest — daily interest on your bank (from {arcade.INTEREST_MIN_BANK:,} 💎)")
    rows = [[B(text="🎟 Scratch card", callback_data="ar:sc"), B(text="🔤 Hangman", callback_data="ar:hm")],
            [B(text="⭕ Tic-Tac-Toe", callback_data="ar:ttt"), B(text="🤔 Would you rather", callback_data="ar:wy")],
            [B(text="🧩 Riddle", callback_data="ar:rd"), B(text="💡 Fun fact", callback_data="ar:fc")],
            [B(text="🔮 Horoscope", callback_data="ar:ho"), B(text="🥠 Fortune", callback_data="ar:fo")],
            [B(text="🏦 Bank interest", callback_data="ar:in"), B(text="🌟 Top reputation", callback_data="ar:tr")],
            back()]
    return text, kb(rows)


@router.message(Command("arcade", "games"))
async def arcade_cmd(m: Message, db_user: User):
    text, markup = await hub(db_user.id)
    await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ar:"))
async def arcade_nav(cb: CallbackQuery, db_user: User):
    what = cb.data.split(":")[1]
    msg = cb.message
    table = {"sc": do_scratch, "hm": do_hangman, "ttt": do_ttt, "wy": do_wyr, "rd": do_riddle, "fc": do_fact,
             "ho": do_horoscope, "fo": do_fortune, "in": do_interest, "tr": do_toprep}
    fn = table.get(what)
    if fn is None:
        raise GameError("❌ Unknown button.")
    await safe_answer(cb)
    await fn(msg, db_user, reply=False)


# =============================================================== scratch card
def _sc_kb(gid: str, revealed: list[int], cells: list[str], done: bool) -> InlineKeyboardMarkup:
    rows = []
    for r in range(2):
        rows.append([B(text=cells[i] if i in revealed or done else "🎟", callback_data=f"sc:{gid}:{i}") for i in range(r * 3, r * 3 + 3)])
    if not done:
        rows.append([B(text="✨ Scratch all", callback_data=f"sc:{gid}:a")])
    return kb(rows)


def _sc_text(prize: int, done: bool) -> str:
    if not done:
        return f"🎟 <b>Scratch card</b> <i>(−{arcade.SCRATCH_COST} 💎)</i>\n\nTap the squares to scratch.\n<b>Match 3 symbols</b> to win!"
    if prize:
        return (f"🎟 <b>Scratch card</b>\n\n🎉 <b>Three of a kind!</b> You won <b>{num(prize)}</b> 💎\n"
                f"<i>Net {'+' if prize >= arcade.SCRATCH_COST else ''}{num(prize - arcade.SCRATCH_COST)} 💎 — already in your wallet.</i>")
    return "🎟 <b>Scratch card</b>\n\n💨 No match this time. Try another? /scratch"


async def do_scratch(msg: Message, user: User, reply: bool = True) -> None:
    cells, prize = await arcade.scratch_buy(user.id)
    gid = _gid()
    await kv.put(f"sc:{gid}", json.dumps({"u": user.id, "c": cells, "p": prize, "r": []}), 900)
    await _say(msg, _sc_text(prize, False), _sc_kb(gid, [], cells, False), reply)


@router.message(Command("scratch", "scratchcard"))
async def scratch_cmd(m: Message, db_user: User):
    await do_scratch(m, db_user)


@router.callback_query(F.data.startswith("sc:"))
async def scratch_tap(cb: CallbackQuery):
    _, gid, cell = cb.data.split(":")
    raw = await kv.get(f"sc:{gid}")
    if not raw:
        await safe_edit(cb, "🎟 This card expired. Any prize was already added to your wallet.")
        await safe_answer(cb)
        return
    st = json.loads(raw)
    if cb.from_user.id != st["u"]:
        await safe_answer(cb, "This isn't your card — buy your own with /scratch 🎟", alert=True)
        return
    if cell == "a":
        st["r"] = list(range(6))
    elif int(cell) not in st["r"]:
        st["r"].append(int(cell))
    done = len(st["r"]) >= 6
    if done:
        await kv.take(f"sc:{gid}")
    else:
        await kv.put(f"sc:{gid}", json.dumps(st), 900)
    await safe_edit(cb, _sc_text(st["p"], done), _sc_kb(gid, st["r"], st["c"], done))
    await safe_answer(cb)


# =============================================================== rock paper scissors
def _rps_kb(uid: int, bet: int) -> InlineKeyboardMarkup:
    return kb([[B(text=label, callback_data=f"rp:{uid}:{bet}:{k}") for k, label in arcade.RPS.items()]])


@router.message(Command("rps", "rockpaperscissors"))
async def rps_cmd(m: Message, command: CommandObject, db_user: User):
    arg = (command.args or "").strip().split()
    bet = 0
    if arg:
        w = await economy.balance(db_user.id)
        bet = parse_amount(arg[0], min(w.balance, R.casino_max_bet)) or 0
        if not bet:
            raise GameError("❌ Invalid bet. Try <code>/rps 200</code>, <code>/rps 2k</code> or just <code>/rps</code> for a free round.")
    head = (f"✊ <b>Rock · Paper · Scissors</b>\n{mention(db_user.id, db_user.first_name)} vs me — "
            + (f"bet <b>{num(bet)}</b> 💎 (win pays x{arcade.RPS_WIN_MULT}, tie = refund)" if bet else "free practice round"))
    await m.reply(f"{head}\n\n<i>Pick your move:</i>", reply_markup=_rps_kb(db_user.id, bet))


@router.callback_query(F.data.startswith("rp:"))
async def rps_pick(cb: CallbackQuery):
    _, uid, bet, pick = cb.data.split(":")
    if cb.from_user.id != int(uid):
        await safe_answer(cb, "This game isn't yours. Start your own with /rps ✊", alert=True)
        return
    bet = int(bet)
    bot, res, net = await arcade.rps_play(cb.from_user.id, pick, bet)
    verdict = {"win": "🎉 <b>You win!</b>", "lose": "😢 <b>You lose.</b>", "tie": "🤝 <b>Tie!</b>"}[res]
    money = ""
    if bet:
        money = f"\n<b>{'+' if net > 0 else ''}{num(net)}</b> 💎" if net else "\n<i>Bet refunded.</i>" if res == "tie" else ""
    await safe_edit(cb, f"✊ <b>Rock · Paper · Scissors</b>\n\nYou: {arcade.RPS[pick]}\nMe: {arcade.RPS[bot]}\n\n{verdict}{money}\n\n<i>Play again:</i>",
                    _rps_kb(cb.from_user.id, bet))
    await safe_answer(cb)


# =============================================================== tic-tac-toe
_MARK = {"x": "❌", "o": "⭕", " ": "▫️"}


def _ttt_head(st: dict) -> str:
    o = esc(st["on"]) if st["o"] else "…waiting for a player"
    return f"⭕❌ <b>Tic-Tac-Toe</b> · stake {arcade.TTT_STAKE} 💎\n❌ {esc(st['xn'])}  vs  ⭕ {o}"


def _ttt_board_text(b: str) -> str:
    return "\n".join("".join(_MARK[b[r * 3 + c]] for c in range(3)) for r in range(3))


def _ttt_kb(gid: str, st: dict) -> InlineKeyboardMarkup:
    if not st["o"]:
        return kb([[B(text="🎮 Join the game", callback_data=f"tj:{gid}")]])
    return kb([[B(text=_MARK[st["b"][r * 3 + c]], callback_data=f"ttt:{gid}:{r * 3 + c}") for c in range(3)] for r in range(3)])


async def do_ttt(msg: Message, user: User, target: int | None = None, reply: bool = True) -> None:
    st = {"x": user.id, "xn": user.first_name, "o": None, "on": "", "b": " " * 9, "t": "x"}
    if target:
        if target == user.id:
            raise GameError("😅 You can't play against yourself — leave out the name for an open game.")
        st["o"], st["on"] = target, await _name(target)
        await arcade.ttt_check_funds(target, esc(st["on"]))
    await arcade.ttt_check_funds(user.id, esc(user.first_name))
    gid = _gid()
    await kv.put(f"ttt:{gid}", json.dumps(st), 3600)
    tail = (f"\n\n{mention(st['o'], st['on'])}, you've been challenged! ❌ goes first." if st["o"]
            else "\n\n<i>Anyone can tap Join to play against you.</i>")
    await _say(msg, _ttt_head(st) + tail, _ttt_kb(gid, st), reply)


@router.message(Command("ttt", "tictactoe", "xo"))
async def ttt_cmd(m: Message, command: CommandObject, db_user: User):
    target, _ = await resolve_target(m, (command.args or "").split())
    await do_ttt(m, db_user, target)


@router.callback_query(F.data.startswith("tj:"))
async def ttt_join(cb: CallbackQuery):
    gid = cb.data.split(":")[1]
    raw = await kv.get(f"ttt:{gid}")
    if not raw:
        await safe_edit(cb, "⌛ This game expired. Start a new one with /ttt")
        await safe_answer(cb)
        return
    st = json.loads(raw)
    if st["o"]:
        await safe_answer(cb, "Someone already joined.", alert=True)
        return
    if cb.from_user.id == st["x"]:
        await safe_answer(cb, "Wait for another player to join 🙂", alert=True)
        return
    await arcade.ttt_check_funds(cb.from_user.id, esc(cb.from_user.first_name))
    st["o"], st["on"] = cb.from_user.id, cb.from_user.first_name
    await kv.put(f"ttt:{gid}", json.dumps(st), 3600)
    await safe_edit(cb, f"{_ttt_head(st)}\n\n❌ {esc(st['xn'])} goes first.", _ttt_kb(gid, st))
    await safe_answer(cb)


@router.callback_query(F.data.startswith("ttt:"))
async def ttt_move(cb: CallbackQuery):
    _, gid, cell = cb.data.split(":")
    raw = await kv.get(f"ttt:{gid}")
    if not raw:
        await safe_edit(cb, "⌛ This game expired. Start a new one with /ttt")
        await safe_answer(cb)
        return
    st = json.loads(raw)
    uid = cb.from_user.id
    if uid not in (st["x"], st["o"]):
        await safe_answer(cb, "You're not in this game. Start your own with /ttt ⭕", alert=True)
        return
    if uid != (st["x"] if st["t"] == "x" else st["o"]):
        await safe_answer(cb, "It's not your turn ⏳")
        return
    i = int(cell)
    if st["b"][i] != " ":
        await safe_answer(cb, "That square is taken.")
        return
    if not await kv.set_nx(f"ttl:{gid}:{uid}", 0.25):    # tiny per-player lock against double taps (never blocks the opponent)
        await safe_answer(cb)
        return
    b = list(st["b"])
    b[i] = st["t"]
    st["b"] = "".join(b)
    res = arcade.ttt_over(st["b"])
    if res:
        if not await kv.take(f"ttt:{gid}"):
            await safe_answer(cb)
            return
        winner = st["x"] if res == "x" else st["o"] if res == "o" else None
        moved = await arcade.ttt_record(winner, st["x"], st["o"])
        who = (f"🏆 <b>{mention(winner, st['xn'] if res == 'x' else st['on'])} wins!</b>"
               f"{f' +{moved} 💎 (taken from the loser)' if moved else ' (no diamonds moved: daily limit with this player)'}" if winner else "🤝 <b>It's a draw!</b>")
        await safe_edit(cb, f"{_ttt_head(st)}\n\n{_ttt_board_text(st['b'])}\n\n{who}\n<i>Rematch: /ttt</i>")
    else:
        st["t"] = "o" if st["t"] == "x" else "x"
        await kv.put(f"ttt:{gid}", json.dumps(st), 3600)
        nxt = st["xn"] if st["t"] == "x" else st["on"]
        await safe_edit(cb, f"{_ttt_head(st)}\n\nTurn: {_MARK[st['t']]} <b>{esc(nxt)}</b>", _ttt_kb(gid, st))
    await safe_answer(cb)


# =============================================================== hangman
def _hm_text(st: dict, name: str, end: str = "") -> str:
    wrong = [c for c in st["g"] if c not in st["w"]]
    lives = "❤️" * st["l"] + "🖤" * (arcade.HANGMAN_LIVES - st["l"])
    return (f"🔤 <b>Hangman</b> · {st['c']}\n{esc(name)}\n\n<code>{arcade.hangman_mask(st['w'], st['g'])}</code>\n\n"
            f"{lives}\n❌ Wrong: {' '.join(wrong) or '—'}{end}")


def _hm_kb(gid: str, st: dict) -> InlineKeyboardMarkup:
    cells = [B(text="·" if L in st["g"] else L, callback_data="hm:x" if L in st["g"] else f"hm:{gid}:{L}") for L in arcade.ALPHABET]
    return kb([cells[i:i + 7] for i in range(0, 26, 7)])


async def do_hangman(msg: Message, user: User, reply: bool = True) -> None:
    await arcade.hangman_start(user.id)
    cat, word = arcade.hangman_pick()
    st = {"u": user.id, "n": user.first_name, "w": word, "c": cat, "g": "", "l": arcade.HANGMAN_LIVES}
    gid = _gid()
    await kv.put(f"hm:{gid}", json.dumps(st), 1800)
    await _say(msg, _hm_text(st, user.first_name) + "\n\n<i>Pick letters. Fewer mistakes = bigger prize!</i>", _hm_kb(gid, st), reply)


@router.message(Command("hangman", "hang"))
async def hangman_cmd(m: Message, db_user: User):
    await do_hangman(m, db_user)


@router.callback_query(F.data.startswith("hm:"))
async def hangman_pick(cb: CallbackQuery):
    parts = cb.data.split(":")
    if len(parts) < 3:                                  # an already-used letter
        await safe_answer(cb, "You already tried that letter.")
        return
    gid, letter = parts[1], parts[2]
    raw = await kv.get(f"hm:{gid}")
    if not raw:
        await safe_edit(cb, "⌛ This hangman game expired. Start a new one with /hangman")
        await safe_answer(cb)
        return
    st = json.loads(raw)
    if cb.from_user.id != st["u"]:
        await safe_answer(cb, "This isn't your game. Start your own with /hangman 🔤", alert=True)
        return
    if letter not in arcade.ALPHABET or letter in st["g"]:
        await safe_answer(cb)
        return
    st["g"] += letter
    if letter not in st["w"]:
        st["l"] -= 1
    won = all(ch in st["g"] for ch in st["w"])
    lost = st["l"] <= 0
    if won or lost:
        if not await kv.take(f"hm:{gid}"):
            await safe_answer(cb)
            return
        amount = await arcade.hangman_finish(st["u"], gid, won, st["l"])
        end = (f"\n\n🎉 <b>Solved!</b> The word was <b>{st['w']}</b>\nYou won <b>+{num(amount)}</b> 💎" if won
               else f"\n\n💀 <b>Game over.</b> The word was <b>{st['w']}</b>")
        st["g"] += st["w"]                               # reveal everything
        await safe_edit(cb, _hm_text(st, st["n"], end))
    else:
        await kv.put(f"hm:{gid}", json.dumps(st), 1800)
        await safe_edit(cb, _hm_text(st, st["n"]), _hm_kb(gid, st))
    await safe_answer(cb)


# =============================================================== would you rather
def _wy_text(q: tuple[str, str], votes: dict) -> str:
    a = sum(1 for v in votes.values() if v == 0)
    b = sum(1 for v in votes.values() if v == 1)
    tot = max(1, a + b)
    return (f"🤔 <b>Would you rather…</b>\n\n🅰️ {esc(q[0])}\n{bar(a, tot)} <b>{a}</b> ({round(100 * a / tot) if a + b else 0}%)\n\n"
            f"🅱️ {esc(q[1])}\n{bar(b, tot)} <b>{b}</b> ({round(100 * b / tot) if a + b else 0}%)\n\n<i>{a + b} vote{'s' if a + b != 1 else ''} · tap to vote (you can change it)</i>")


def _wy_kb(gid: str) -> InlineKeyboardMarkup:
    return kb([[B(text="🅰️ Option A", callback_data=f"wy:{gid}:0"), B(text="🅱️ Option B", callback_data=f"wy:{gid}:1")],
               [B(text="🔁 Another one", callback_data="ar:wy")]])


async def do_wyr(msg: Message, user: User, reply: bool = True) -> None:
    idx = deck.draw("wyr", len(arcade.WYR), deck.chat_of(msg))      # shuffle-bag: no repeats until all were shown
    gid = _gid()
    await kv.put(f"wy:{gid}", json.dumps({"q": idx, "v": {}}), 86400)
    await _say(msg, _wy_text(arcade.WYR[idx], {}), _wy_kb(gid), reply)


@router.message(Command("wyr", "wouldyourather"))
async def wyr_cmd(m: Message, db_user: User):
    await do_wyr(m, db_user)


@router.callback_query(F.data.startswith("wy:"))
async def wyr_vote(cb: CallbackQuery):
    _, gid, choice = cb.data.split(":")
    raw = await kv.get(f"wy:{gid}")
    if not raw:
        await safe_answer(cb, "This poll expired. Start a new one with /wyr", alert=True)
        return
    st = json.loads(raw)
    st["v"][str(cb.from_user.id)] = int(choice)
    await kv.put(f"wy:{gid}", json.dumps(st), 86400)
    await safe_edit(cb, _wy_text(arcade.WYR[st["q"]], st["v"]), _wy_kb(gid))
    await safe_answer(cb, "Vote saved ✅")


# =============================================================== riddle & fact
async def do_riddle(msg: Message, user: User, reply: bool = True) -> None:
    idx = deck.draw("riddle", len(arcade.RIDDLES), deck.chat_of(msg))
    await _say(msg, f"🧩 <b>Riddle</b>\n\n{esc(arcade.RIDDLES[idx][0])}\n\n<i>Think you know it? Reply in the chat, then reveal!</i>",
               kb([[B(text="💡 Show answer", callback_data=f"rd:{idx}"), B(text="🔁 Another", callback_data="ar:rd")]]), reply)


@router.message(Command("riddle"))
async def riddle_cmd(m: Message, db_user: User):
    await do_riddle(m, db_user)


@router.callback_query(F.data.regexp(r"^rd:\d+$"))
async def riddle_answer(cb: CallbackQuery):
    idx = int(cb.data.split(":")[1])
    if idx >= len(arcade.RIDDLES):
        raise GameError("❌ Unknown riddle.")
    q, a = arcade.RIDDLES[idx]
    await safe_edit(cb, f"🧩 <b>Riddle</b>\n\n{esc(q)}\n\n✅ <b>Answer:</b> {esc(a)}", kb([[B(text="🔁 Another riddle", callback_data="ar:rd")]]))
    await safe_answer(cb)


async def do_fact(msg: Message, user: User, reply: bool = True) -> None:
    await _say(msg, f"💡 <b>Did you know?</b>\n\n{esc(deck.pick('fact', arcade.FACTS, deck.chat_of(msg)))}",
               kb([[B(text="🔁 Another fact", callback_data="ar:fc")]]), reply)


@router.message(Command("fact", "facts"))
async def fact_cmd(m: Message, db_user: User):
    await do_fact(m, db_user)


# =============================================================== horoscope & fortune
def _ho_kb() -> InlineKeyboardMarkup:
    btns = [B(text=f"{e} {n}", callback_data=f"ho:{n}") for e, n in arcade.SIGNS]
    return kb([btns[i:i + 3] for i in range(0, 12, 3)])


async def do_horoscope(msg: Message, user: User, reply: bool = True, sign: str | None = None) -> None:
    if sign:
        await _say(msg, "🔮 <b>Daily horoscope</b>\n\n" + arcade.horoscope(sign), kb([[B(text="🔮 Other signs", callback_data="ar:ho")]]), reply)
    else:
        await _say(msg, "🔮 <b>Daily horoscope</b>\n\nPick your sign:", _ho_kb(), reply)


@router.message(Command("horoscope", "zodiac"))
async def horoscope_cmd(m: Message, command: CommandObject, db_user: User):
    sign = arcade.find_sign(command.args or "")
    if command.args and not sign:
        raise GameError("🔮 I don't know that sign. Try <code>/horoscope leo</code> or just <code>/horoscope</code>.")
    await do_horoscope(m, db_user, sign=sign)


@router.callback_query(F.data.startswith("ho:"))
async def horoscope_pick(cb: CallbackQuery):
    sign = arcade.find_sign(cb.data.split(":")[1])
    if not sign:
        raise GameError("❌ Unknown sign.")
    await safe_edit(cb, "🔮 <b>Daily horoscope</b>\n\n" + arcade.horoscope(sign), kb([[B(text="🔮 Other signs", callback_data="ar:ho")]]))
    await safe_answer(cb)


async def do_fortune(msg: Message, user: User, reply: bool = True) -> None:
    await _say(msg, arcade.fortune(user.id), None, reply)


@router.message(Command("fortune", "cookie"))
async def fortune_cmd(m: Message, db_user: User):
    await do_fortune(m, db_user)


# =============================================================== reputation
@router.message(Command("rep", "respect"))
async def rep_cmd(m: Message, command: CommandObject, db_user: User):
    target, _ = await resolve_target(m, (command.args or "").split())
    if target is None:
        total, rem = await arcade.rep_of(db_user.id), await arcade.rep_cooldown(db_user.id)
        await m.reply(f"🌟 Your reputation: <b>{num(total)}</b>\n"
                      + (f"You can give rep again in <b>{dur(rem)}</b>." if rem else "Reply to someone with <b>/rep</b> to give them +1 🌟 (once every 12 h)."))
        return
    total = await arcade.give_rep(db_user.id, target)
    await m.reply(f"🌟 {mention(db_user.id, db_user.first_name)} gave <b>+1 reputation</b> to {mention(target, await _name(target))}!\n"
                  f"<i>They now have {num(total)} 🌟</i>")


REP_PER = 10


async def toprep_view(page: int = 0) -> tuple[str, InlineKeyboardMarkup | None]:
    """Reputation board, REP_PER players per page with ◀️ ▶️ (shared: anyone's tap turns the page)."""
    rows, total = await arcade.top_rep_page(0, REP_PER)
    if not total:
        return "🌟 Nobody has reputation yet. Be the first: reply to a friend with /rep", None
    page = paging.clamp(page, total, REP_PER)
    pages = paging.total_pages(total, REP_PER)
    if page:
        rows, _ = await arcade.top_rep_page(page, REP_PER)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, name, v) in enumerate(rows):
        rank = page * REP_PER + i + 1
        lines.append(f"{medals[rank - 1] if rank <= 3 else f'{rank}.'} {mention(uid, name)} — <b>{num(v)}</b> 🌟")
    text = f"🌟 <b>Top reputation</b>\n{DIV}\n" + "\n".join(lines) + (paging.footer(page, pages) if pages > 1 else "")
    row = paging.nav(lambda p: f"trp:{p}", page, pages)
    return text, (kb([row]) if row else None)


async def do_toprep(msg: Message, user: User, reply: bool = True) -> None:
    text, markup = await toprep_view(0)
    await _say(msg, text, markup, reply)


@router.callback_query(F.data.startswith("trp:"))
async def toprep_page(cb: CallbackQuery):
    text, markup = await toprep_view(int(cb.data.split(":")[1]))
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.message(Command("toprep", "reps"))
async def toprep_cmd(m: Message, db_user: User):
    await do_toprep(m, db_user)


# =============================================================== bank interest
async def do_interest(msg: Message, user: User, reply: bool = True) -> None:
    amt, bank = await arcade.claim_interest(user.id)
    await _say(msg, f"🏦 <b>Bank interest</b>\n\n+<b>{num(amt)}</b> 💎 added to your bank ({arcade.INTEREST_RATE:.0%}, max {arcade.INTEREST_CAP})\n"
                    f"Bank balance: <b>{num(bank)}</b> 💎\n<i>Come back tomorrow!</i>", None, reply)


@router.message(Command("interest"))
async def interest_cmd(m: Message, db_user: User):
    await do_interest(m, db_user)
