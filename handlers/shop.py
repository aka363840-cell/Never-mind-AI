from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message

from database.models import User
from game.rules import CATEGORIES, POWERS
from handlers import views
from keyboards.menus import kb
from services import shop
from services.errors import GameError
from utils.fmt import dur, esc, num
from utils.telegram import safe_answer, safe_edit

router = Router()


def _private(m: Message) -> bool:
    return m.chat.type == "private"


_ALIAS = {"power": "powers", "weapon": "weapons", "armor": "armour", "armors": "armour", "shield": "shields",
          "consumable": "consumables", "potion": "consumables", "potions": "consumables", "cosmetic": "cosmetics",
          "collectible": "collectibles", "skin": "cosmetics"}


def _category(arg: str | None) -> str | None:
    a = (arg or "").strip().lower().lstrip("/")
    if not a:
        return None
    a = _ALIAS.get(a, a)
    return a if a in CATEGORIES else next((c for c in CATEGORIES if c.startswith(a)), None)


@router.message(Command("shop"))
async def shop_cmd(m: Message, command: CommandObject):
    """/shop → categories · /shop power (or weapons, armour ...) → that category. Groups get plain text (no buttons)."""
    cat, txt = _category(command.args), not _private(m)
    if cat:
        text, markup = await views.shop_category(cat, text_only=txt)
    else:
        text, markup = views.shop_home(text_only=txt)
    await m.reply(text, reply_markup=markup)


@router.message(Command("powers"))
async def powers_cmd(m: Message, db_user: User):
    text, markup = await views.powers(db_user.id, text_only=not _private(m))
    await m.reply(text, reply_markup=markup)


@router.message(Command("inventory", "inv"))
async def inv_cmd(m: Message, db_user: User):
    text, markup = await views.inventory(db_user.id, text_only=not _private(m))
    await m.reply(text, reply_markup=markup)


def _key_qty(command: CommandObject) -> tuple[str, int]:
    a = (command.args or "").split()
    if not a:
        raise GameError("Usage: /buy &lt;item_key&gt; [qty] — see /shop for keys.")
    qty = int(a[1]) if len(a) > 1 and a[1].isdigit() else 1
    return a[0], qty


@router.message(Command("buy"))
async def buy_cmd(m: Message, command: CommandObject, db_user: User):
    key, qty = _key_qty(command)
    q = await shop.quote(key)
    name, total = await shop.buy(db_user.id, key, qty)
    await m.reply(f"🛒 Bought <b>{esc(name)}</b>{f' ×{qty}' if qty > 1 else ''} for <b>{num(total)}</b> 💎.\n"
                  f"{views.purchase_next_step(q['category'], q['key'])}")


@router.message(Command("sell"))
async def sell_cmd(m: Message, command: CommandObject, db_user: User):
    key, qty = _key_qty(command)
    name, gain = await shop.sell(db_user.id, key, qty)
    await m.reply(f"💱 Sold <b>{esc(name)}</b> for <b>{num(gain)}</b> 💎.")


@router.message(Command("equip"))
async def equip_cmd(m: Message, command: CommandObject, db_user: User):
    key, _ = _key_qty(command)
    name, on = await shop.equip(db_user.id, key)
    await m.reply(f"{'✅ Equipped' if on else '➖ Unequipped'} <b>{esc(name)}</b>.")


@router.message(Command("upgrade"))
async def upgrade_cmd(m: Message, command: CommandObject, db_user: User):
    key, _ = _key_qty(command)
    name, lvl, cost = await shop.upgrade(db_user.id, key)
    await m.reply(f"⬆️ <b>{esc(name)}</b> is now level <b>{lvl}</b> (cost {num(cost)} 💎).")


@router.message(Command("use"))
async def use_cmd(m: Message, command: CommandObject, db_user: User):
    key, _ = _key_qty(command)
    await m.reply(await shop.use_item(db_user.id, key))


# ---- callbacks
@router.callback_query(F.data.startswith("shop:"))
async def shop_cat(cb: CallbackQuery):
    cat = cb.data.split(":")[1]
    if cat not in CATEGORIES:
        raise GameError("❌ Unknown category.")
    text, markup = await views.shop_category(cat)
    await safe_edit(cb, text, markup); await safe_answer(cb)


@router.callback_query(F.data.startswith("buy:"))
async def buy_confirm(cb: CallbackQuery):
    q = await shop.quote(cb.data.split(":", 1)[1])
    back = "nav:powers" if q["kind"] == "power" else "nav:shop"
    await safe_edit(cb, f"🛒 Buy <b>{esc(q['name'])}</b> for <b>{num(q['price'])}</b> 💎?\n<i>{esc(q['desc'])}</i>",
                    kb([[B(text="✅ Confirm", callback_data=f"buyok:{q['key']}"), B(text="✖️ Cancel", callback_data=back)]]))
    await safe_answer(cb)


@router.callback_query(F.data.startswith("buyok:"))
async def buy_ok(cb: CallbackQuery, db_user: User):
    key = cb.data.split(":", 1)[1]
    q = await shop.quote(key)
    name, total = await shop.buy(db_user.id, key)
    if q["kind"] == "power":
        text, markup = await views.powers(db_user.id)
    else:
        # a clear "what now" screen instead of silently going back to the shop
        step = views.purchase_next_step(q["category"], q["key"])
        btns = []
        if q["category"] in ("weapons", "armour", "shields", "cosmetics"):
            btns.append(B(text="✅ Equip now", callback_data=f"eq:{q['key']}"))
        elif q["category"] == "consumables":
            btns.append(B(text="🧪 Use now", callback_data=f"use:{q['key']}"))
        text = f"🛒 Bought <b>{esc(name)}</b> for <b>{num(total)}</b> 💎.\n\n{step}"
        markup = kb([btns, [B(text="🎒 Inventory", callback_data="inv:0"), B(text="🛒 Shop", callback_data="nav:shop")]]
                    if btns else [[B(text="🎒 Inventory", callback_data="inv:0"), B(text="🛒 Shop", callback_data="nav:shop")]])
    await safe_edit(cb, text, markup)
    await safe_answer(cb, f"Bought {name} for {total:,} 💎", alert=True)


@router.callback_query(F.data.startswith("inv:"))
async def inv_page(cb: CallbackQuery, db_user: User):
    text, markup = await views.inventory(db_user.id, int(cb.data.split(":")[1]))
    await safe_edit(cb, text, markup); await safe_answer(cb)


async def _refresh(cb: CallbackQuery, uid: int, key: str):
    if key.upper() in POWERS:
        text, markup = await views.powers(uid)
    else:
        text, markup = await views.inventory(uid)
    await safe_edit(cb, text, markup)


@router.callback_query(F.data.startswith("eq:"))
async def eq_cb(cb: CallbackQuery, db_user: User):
    key = cb.data.split(":", 1)[1]
    name, on = await shop.equip(db_user.id, key)
    await _refresh(cb, db_user.id, key)
    await safe_answer(cb, f"{'Equipped' if on else 'Unequipped'} {name}")


@router.callback_query(F.data.startswith("up:"))
async def up_cb(cb: CallbackQuery, db_user: User):
    key = cb.data.split(":", 1)[1]
    name, lvl, cost = await shop.upgrade(db_user.id, key)
    await _refresh(cb, db_user.id, key)
    await safe_answer(cb, f"{name} → level {lvl} (-{cost:,} 💎)", alert=True)


@router.callback_query(F.data.startswith("use:"))
async def use_cb(cb: CallbackQuery, db_user: User):
    msg = await shop.use_item(db_user.id, cb.data.split(":", 1)[1])
    text, markup = await views.inventory(db_user.id)
    await safe_edit(cb, text, markup)
    await safe_answer(cb, msg, alert=True)


@router.callback_query(F.data == "act")
async def act_cb(cb: CallbackQuery, db_user: User):
    info = await shop.activate(db_user.id)     # Guardian/Oracle raise a "reply with /activate" GameError
    p = POWERS[info["power"]]
    text, markup = await views.powers(db_user.id)
    await safe_edit(cb, text, markup)
    await safe_answer(cb, f"{p.emoji} {p.name} activated for {dur(info.get('seconds', 0))}", alert=True)
