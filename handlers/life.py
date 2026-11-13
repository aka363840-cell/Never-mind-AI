"""Life-sim commands: dates, gifts, children, family, houses."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message

from config import get_settings
from database.models import User
from keyboards.menus import kb
from services import life
from services.errors import GameError
from utils.fmt import esc, mention, num
from utils.telegram import safe_answer, safe_edit

router = Router()


async def _partner_name(uid: int) -> str:
    v = await life.family_view(uid)
    return v["partner"][1] if v["partner"] else "your partner"


@router.message(Command("date"))
async def date_cmd(m: Message, db_user: User):
    text, gain, total, pid = await life.go_on_date(db_user.id)
    await m.reply(f"💑 <b>Date night!</b>\n{text}\n\n❤️ Love +{gain}  (total <b>{total}</b> · {life.love_title(total)})\n<i>Cost: {life.DATE_COST} 💎</i>")


@router.message(Command("gifts"))
async def gifts_list(m: Message):
    rows = "\n".join(f"{e} <code>{k}</code> — {n}: {num(p)} 💎 → ❤️ +{l}" for k, (e, n, p, l) in life.GIFTS.items())
    await m.reply(f"🎁 <b>Gifts for your partner</b>\n\n{rows}\n\nSend one with <code>/gift flowers</code>")


@router.message(Command("gift"))
async def gift_cmd(m: Message, command: CommandObject, db_user: User):
    key = (command.args or "").strip().lower()
    if not key:
        await gifts_list(m)
        return
    what, love, total, pid = await life.send_gift(db_user.id, key.split()[0])
    await m.reply(f"🎁 {mention(db_user.id, db_user.first_name)} gave {what} to <a href=\"tg://user?id={pid}\">their partner</a>!\n"
                  f"❤️ Love +{love} (total <b>{total}</b> · {life.love_title(total)})")


@router.message(Command("baby", "child"))
async def baby_cmd(m: Message, command: CommandObject, db_user: User):
    born, name, g = await life.have_baby(db_user.id, command.args)
    if not born:
        await m.reply("🍼 No baby this time… Try again later! (No diamonds were spent.)")
        return
    await m.reply(f"👶 <b>It's a {'boy' if g == 'b' else 'girl'}!</b> Welcome <b>{esc(name)}</b> to the family! 🎉\n<i>Cost: {num(life.BABY_COST)} 💎</i>")


@router.message(Command("purgeadopted"), F.from_user.id == get_settings().owner_id)
async def purge_adopted_cmd(m: Message):
    """Owner-only, deliberately undocumented (not in menus, /help or README)."""
    kids, parents = await life.purge_random_adopted()
    await m.reply(f"✅ Removed <b>{kids}</b> auto-named adopted children from <b>{parents}</b> players.")


@router.message(Command("disown"))
async def disown_cmd(m: Message, command: CommandObject, db_user: User):
    if not (command.args or "").strip():
        raise GameError("Usage: /disown &lt;child name&gt;")
    name = await life.disown(db_user.id, command.args)
    await m.reply(f"💔 <b>{esc(name)}</b> left the family.")


@router.message(Command("familybonus", "fbonus"))
async def family_bonus_cmd(m: Message, db_user: User):
    amt = await life.family_bonus(db_user.id)
    await m.reply(f"👨‍👩‍👧‍👦 Your family sent you <b>+{num(amt)}</b> 💎 today!")


# ------------------------------------------------------------ houses
def _houses_kb(owned: list[str]):
    rows = [[B(text=f"{e} Buy {n} — {num(p)} 💎", callback_data=f"hbuy:{k}")] for k, (e, n, p, r) in life.HOUSES.items() if k not in owned]
    return kb(rows) if rows else None


@router.message(Command("houses", "house", "property"))
async def houses_cmd(m: Message, command: CommandObject, db_user: User):
    args = (command.args or "").split()
    if args and args[0].lower() in ("sell", "buy"):
        if len(args) != 2:
            raise GameError(f"Usage: /house {args[0].lower()} &lt;property&gt; — see /houses for names.")
        key = life.resolve_house(args[1])
        if args[0].lower() == "sell":
            name, refund = await life.sell_house(db_user.id, key)
            await m.reply(f"💱 Sold {name} for <b>{num(refund)}</b> 💎 (60% of the price).")
        else:
            name, price = await life.buy_house(db_user.id, key)
            await m.reply(f"🏠 You bought {name} for <b>{num(price)}</b> 💎!")
        return
    if args:
        raise GameError("Usage: /houses · /house buy &lt;property&gt; · /house sell &lt;property&gt;")
    v = await life.family_view(db_user.id)
    lines = ["🏠 <b>Real estate</b> — earn rent every day with /rent\n"]
    for k, (e, n, p, r) in life.HOUSES.items():
        lines.append(f"{e} <b>{n}</b> (<code>{k}</code>) — {num(p)} 💎 · rent {num(r)}/day" + ("  ✅ owned" if k in v["houses"] else ""))
    lines.append("\n<i>Sell: /house sell &lt;name&gt; (60% back)</i>")
    await m.reply("\n".join(lines), reply_markup=_houses_kb(v["houses"]))


@router.callback_query(F.data.startswith("hbuy:"))
async def house_buy(cb: CallbackQuery):
    name, price = await life.buy_house(cb.from_user.id, cb.data.split(":")[1])
    await safe_answer(cb, f"You bought {name} for {num(price)} 💎!", alert=True)
    v = await life.family_view(cb.from_user.id)
    lines = ["🏠 <b>Real estate</b> — earn rent every day with /rent\n"]
    for k, (e, n, p, r) in life.HOUSES.items():
        lines.append(f"{e} <b>{n}</b> (<code>{k}</code>) — {num(p)} 💎 · rent {num(r)}/day" + ("  ✅ owned" if k in v["houses"] else ""))
    await safe_edit(cb, "\n".join(lines), _houses_kb(v["houses"]))


@router.message(Command("collect", "rent"))
async def collect_cmd(m: Message, command: CommandObject, db_user: User):
    """/rent -> collect every property that is ready · /rent <property> -> just that one (e.g. /rent apartment)."""
    which = (command.args or "").strip() or None
    total, names = await life.collect_rent(db_user.id, which)
    if which:
        await m.reply(f"🏠 Rent from your {names[0]}: <b>+{num(total)}</b> 💎")
    else:
        n = len(names)
        text = f"🏠 Rent from your <b>{n}</b> propert{'y' if n == 1 else 'ies'}: <b>+{num(total)}</b> 💎\n" + ", ".join(names)
        waiting = [w for w in await life.rent_waiting(db_user.id) if w[0] not in names]   # only the ones skipped just now
        if waiting:
            from utils.fmt import dur
            text += "\n\n⏳ Not ready yet: " + " · ".join(f"{lbl} ({dur(t)})" for lbl, t in waiting)
        await m.reply(text)
