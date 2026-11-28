from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from database.models import User
from keyboards.menus import back, kb
from services import market
from services.errors import GameError
from utils.fmt import esc, num
from utils.telegram import safe_answer, safe_edit

router = Router()


async def page() -> tuple[str, InlineKeyboardMarkup]:
    ls = await market.listings(12)
    head = ("🏪 <b>Player Market</b>\nBuy and sell shop items with other players. Sellers pay a "
            f"{int(market.FEE * 100)}% fee.\n\n")
    if not ls:
        body = "<i>No listings yet. Be the first: </i><code>/mlist KEY QTY PRICE</code>"
    else:
        body = "\n".join(f"<code>{d['id']}</code> {d.get('e', '')} <b>{esc(d.get('n', d['k']))}</b> x{d['q']}"
                         f"{' Lv' + str(d['l']) if d.get('l', 1) > 1 else ''} — <b>{num(d['p'])}</b> 💎" for d in ls)
        body += "\n\nBuy: <code>/mbuy ID</code>"
    foot = "\n\nSell: <code>/mlist KEY QTY PRICE</code> · Take back: <code>/mcancel ID</code>"
    rows = [[B(text="🔄 Refresh", callback_data="mkt:list")], back("shop")]
    return head + body + foot, kb(rows)


@router.message(Command("market", "mkt"))
async def market_cmd(m: Message):
    text, markup = await page()
    await m.reply(text, reply_markup=markup if m.chat.type == "private" else None)


@router.callback_query(F.data == "mkt:list")
async def market_cb(cb: CallbackQuery):
    text, markup = await page()
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.message(Command("mlist"))
async def mlist_cmd(m: Message, command: CommandObject, db_user: User):
    a = (command.args or "").split()
    if len(a) != 3 or not a[1].isdigit() or not a[2].replace(",", "").isdigit():
        raise GameError("Usage: /mlist &lt;item_key&gt; &lt;qty&gt; &lt;total price&gt;  e.g. /mlist healpot 2 500")
    d = await market.create(db_user.id, a[0], int(a[1]), int(a[2].replace(",", "")))
    await m.reply(f"✅ Listed {d['e']} <b>{esc(d['n'])}</b> x{d['q']} for <b>{num(d['p'])}</b> 💎\nID: <code>{d['id']}</code> "
                  f"(you get {num(market.net_after_fee(d['p']))} 💎 after the fee) · cancel: <code>/mcancel {d['id']}</code>")


@router.message(Command("mbuy"))
async def mbuy_cmd(m: Message, command: CommandObject, db_user: User):
    if not (command.args or "").strip():
        raise GameError("Usage: /mbuy &lt;id&gt; — see /market")
    d, fee = await market.buy(db_user.id, command.args)
    await m.reply(f"🛍 Bought {d['e']} <b>{esc(d['n'])}</b> x{d['q']} for <b>{num(d['p'])}</b> 💎")


@router.message(Command("mcancel"))
async def mcancel_cmd(m: Message, command: CommandObject, db_user: User):
    if not (command.args or "").strip():
        mine = await market.mine(db_user.id)
        raise GameError("Your listings: " + (", ".join(f"<code>{d['id']}</code>" for d in mine) or "none") +
                        "\nUsage: /mcancel &lt;id&gt;")
    d = await market.cancel(db_user.id, command.args)
    await m.reply(f"↩️ Listing <code>{d['id']}</code> cancelled — {d['e']} {esc(d['n'])} x{d['q']} is back in your inventory.")
