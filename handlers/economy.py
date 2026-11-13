from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database.models import User
from game.rules import R
from handlers import views
from services import economy, users
from services.errors import GameError
from utils.fmt import esc, mention, num, parse_amount
from utils.targets import resolve_target

router = Router()


@router.message(Command("bal", "balance"))
async def bal(m: Message, command: CommandObject, db_user: User):
    """/bal = your own balance. Reply to someone, tag them, /bal @user or /bal <id> = THEIR balance (with their name)."""
    target, _ = await resolve_target(m, (command.args or "").split())
    if target is None or target == db_user.id:
        text, _ = await views.balance(db_user.id, db_user.first_name)
    else:
        text, _ = await views.balance(target, await users.display_name(target), own=False)
    await m.reply(text)


@router.message(Command("daily"))
async def daily(m: Message, db_user: User):
    amt, streak, bonus, clan_bonus = await economy.claim_daily(db_user.id)
    extra = f"\n🔥 <b>{streak}-day streak!</b> Bonus: +{num(bonus)} 💎" if bonus else "\n🔥 Streak started! Come back within 48h to grow it."
    if clan_bonus:
        extra += f"\n🏰 Clan bonus: +{num(clan_bonus)} 💎"
    await m.reply(f"🎁 Daily claimed: <b>+{num(amt)}</b> 💎{extra}")


@router.message(Command("weekly"))
async def weekly(m: Message, db_user: User):
    amt = await economy.claim_weekly(db_user.id)
    await m.reply(f"🎉 Weekly claimed: <b>+{num(amt)}</b> 💎")


@router.message(Command("work"))
async def work(m: Message, db_user: User):
    text, amt = await economy.work(db_user.id)
    await m.reply(f"🔨 {text} and earned <b>{num(amt)}</b> 💎")


@router.message(Command("crime"))
async def crime(m: Message, db_user: User):
    ok, text, amt = await economy.crime(db_user.id)
    if ok:
        await m.reply(f"🕶 {text}. You got away with <b>{num(amt)}</b> 💎")
    else:
        await m.reply(f"🚔 {text}! You paid a fine of <b>{num(amt)}</b> 💎 and are in jail for {R.crime_jail_s // 60} min.")


@router.message(Command("rob"))
async def rob(m: Message, command: CommandObject, db_user: User):
    target, rest = await resolve_target(m, (command.args or "").split())
    if target is None:
        raise GameError("Usage: reply to a player with <code>/rob &lt;amount&gt;</code>, or <code>/rob @user &lt;amount&gt;</code>\n"
                        "Example: <code>/rob 500</code>")
    if not rest:
        raise GameError("❌ You must write the amount you want to rob.\n"
                        "Example: reply to a player with <code>/rob 500</code>")
    amount = parse_amount(rest[0])
    if amount is None:
        raise GameError("❌ Invalid amount. Use a whole number like <code>/rob 500</code>.")
    ok, amt, jail = await economy.rob(db_user.id, target, amount)
    victim = mention(target, await users.display_name(target))
    if ok:
        await m.reply(f"🦝 Robbery successful! You stole <b>{num(amt)}</b> 💎 from {victim}.")
    else:
        await m.reply(f"🚨 You got caught and paid <b>{num(amt)}</b> 💎 to {victim}. Jailed for {jail // 60} min.")


@router.message(Command("pay"))
async def pay(m: Message, command: CommandObject, db_user: User):
    target, rest = await resolve_target(m, (command.args or "").split())
    if target is None or not rest:
        raise GameError("Usage: reply with /pay &lt;amount&gt;  or  /pay @user &lt;amount&gt;")
    w = await economy.balance(db_user.id)
    amount = parse_amount(rest[0], w.balance)
    if amount is None:
        raise GameError("❌ Invalid amount.")
    # idempotency: the Telegram message id makes a re-delivered command a no-op
    await economy.pay(db_user.id, target, amount, ref=f"pay:{m.chat.id}:{m.message_id}")
    await m.reply(f"💸 Sent <b>{num(amount)}</b> 💎 to <a href=\"tg://user?id={target}\">that player</a>.")


@router.message(Command("deposit", "dep"))
async def deposit(m: Message, command: CommandObject, db_user: User):
    arg = (command.args or "").strip()
    if not arg:
        raise GameError("Usage: /deposit &lt;amount|all&gt;")
    if arg.lower() in ("all", "max"):
        amt = None                                   # deposit as much as the 50% rule allows
    else:
        amt = parse_amount(arg)
        if amt is None: raise GameError("❌ Invalid amount.")
    done = await economy.deposit(db_user.id, amt)
    await m.reply(f"🏦 Deposited <b>{num(done)}</b> 💎. Bank deposits are safe from robbers and kills.\n"
                  f"<i>Only {int(R.bank_max_pct * 100)}% of your diamonds can be kept in the bank.</i>")


@router.message(Command("withdraw", "wd"))
async def withdraw(m: Message, command: CommandObject, db_user: User):
    arg = (command.args or "").strip()
    if not arg:
        raise GameError("Usage: /withdraw &lt;amount|all&gt;")
    w = await economy.balance(db_user.id)
    amt = parse_amount(arg, w.bank)
    if amt is None: raise GameError("❌ Invalid amount.")
    done = await economy.withdraw(db_user.id, amt)
    await m.reply(f"💰 Withdrew <b>{num(done)}</b> 💎 to your wallet.")
