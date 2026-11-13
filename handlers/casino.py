from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database.models import User
from game.rules import R
from services import casino, economy
from services.errors import GameError
from utils.fmt import num, parse_amount

router = Router()


async def _bet(m: Message, command: CommandObject, db_user: User, usage: str, arg_index: int = 0) -> int:
    args = (command.args or "").split()
    if len(args) <= arg_index:
        raise GameError(usage)
    w = await economy.balance(db_user.id)
    bet = parse_amount(args[arg_index], min(w.balance, R.casino_max_bet))
    if bet is None:
        raise GameError("❌ Invalid bet. Try a number like <code>500</code>, <code>2k</code> or <code>all</code>.")
    return bet


@router.message(Command("slots", "slot"))
async def slots(m: Message, command: CommandObject, db_user: User):
    bet = await _bet(m, command, db_user, "🎰 Usage: /slots &lt;bet&gt;  (e.g. /slots 500)")
    reels, pay, label = await casino.slots(db_user.id, bet)
    net = pay - bet
    result = f"+{num(net)} 💎 profit" if net > 0 else (f"{num(net)} 💎" if net < 0 else "Break even")
    await m.reply(f"🎰 <b>SLOTS</b>\n\n┃ {' │ '.join(reels)} ┃\n\n{label}\nBet {num(bet)} → won <b>{num(pay)}</b> 💎\n<b>{result}</b>")


@router.message(Command("flip", "coinflip", "cf"))
async def flip(m: Message, command: CommandObject, db_user: User):
    args = (command.args or "").split()
    side = next((a.lower() for a in args if a.lower() in ("heads", "tails", "h", "t")), "heads")
    side = "heads" if side in ("h", "heads") else "tails"
    rest = [a for a in args if a.lower() not in ("heads", "tails", "h", "t")]
    command = CommandObject(prefix="/", command="flip", args=" ".join(rest))
    bet = await _bet(m, command, db_user, "🪙 Usage: /flip &lt;bet&gt; [heads|tails]")
    landed, won = await casino.flip(db_user.id, bet, side)
    if won:
        await m.reply(f"🪙 It landed <b>{landed}</b>! You called {side} and won <b>+{num(bet)}</b> 💎 🎉")
    else:
        await m.reply(f"🪙 It landed <b>{landed}</b>. You called {side} and lost <b>{num(bet)}</b> 💎 😢")


@router.message(Command("spin", "wheel"))
async def spin(m: Message, db_user: User):
    label, amount = await casino.spin(db_user.id)
    await m.reply(f"🎡 The wheel spins… and stops on\n\n<b>{label}</b>\nYou won <b>{num(amount)}</b> 💎!\n\n"
                  f"<i>Free spin every {R.spin_cooldown_s // 3600}h.</i>")
