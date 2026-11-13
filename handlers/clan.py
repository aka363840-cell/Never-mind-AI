from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from database.models import User
from game.rules import R
from keyboards.menus import kb
from services import clans, economy
from services.errors import GameError
from utils import paging
from utils.fmt import esc, mention, num, parse_amount
from utils.targets import resolve_target
from utils.telegram import safe_answer, safe_edit

router = Router()

HELP = ("🏰 <b>Clans</b>\n\n/clan create <i>name</i> — costs {cost} 💎\n/clan join <i>name</i> · /clan leave\n"
        "/clan info [name] · /clan top\n/clan deposit <i>amount</i> — fills the clan bank (levels the clan up)\n"
        "/clan kick <i>@user</i> · /clan disband <i>(owner)</i>\n\n"
        "Every clan level gives all members <b>+{bonus} 💎</b> on /daily. A new level every {step} 💎 in the bank (max 10).")


def _card(d: dict) -> str:
    roster = "\n".join(f"{'👑' if uid == d['owner'] else '•'} {esc(name)} <i>(Lv {lvl})</i>" for uid, name, lvl in d["roster"])
    nxt = f"\nNext level at {num(d['next_level_at'])} 💎" if d["next_level_at"] else "\n🌟 Max level!"
    return (f"🏰 <b>{esc(d['name'])}</b> — Level {d['level']}\n🏦 Bank: {num(d['bank'])} 💎{nxt}\n"
            f"👥 Members: {d['members']}/{d['cap']}\n🎁 /daily bonus: +{d['level'] * R.clan_daily_bonus} 💎\n\n{roster}"
            + (paging.footer(d["page"], d["pages"]) if d["pages"] > 1 else ""))


def _card_kb(d: dict) -> InlineKeyboardMarkup | None:
    row = paging.nav(lambda p: f"clr:{d['id']}:{p}", d["page"], d["pages"])        # ◀️ ▶️ through the member list
    return kb([row]) if row else None


CLAN_TOP_PER = 10


async def _top_view(page: int) -> tuple[str, InlineKeyboardMarkup | None]:
    rows, total = await clans.top_page(0, CLAN_TOP_PER)
    if not total:
        return "🏰 No clans yet — be the first with /clan create &lt;name&gt;", None
    page = paging.clamp(page, total, CLAN_TOP_PER)
    pages = paging.total_pages(total, CLAN_TOP_PER)
    if page:
        rows, _ = await clans.top_page(page, CLAN_TOP_PER)
    lines = [f"{page * CLAN_TOP_PER + i}. <b>{esc(n)}</b> — Lv {lv} · {num(b)} 💎 · {c} members" for i, (n, b, c, lv) in enumerate(rows, 1)]
    row = paging.nav(lambda p: f"clt:{p}", page, pages)
    return "🏆 <b>Top Clans</b>\n\n" + "\n".join(lines) + (paging.footer(page, pages) if pages > 1 else ""), (kb([row]) if row else None)


@router.callback_query(F.data.startswith("clt:"))
async def clan_top_page(cb: CallbackQuery):
    text, markup = await _top_view(int(cb.data.split(":")[1]))
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.callback_query(F.data.startswith("clr:"))
async def clan_roster_page(cb: CallbackQuery):
    _, cid, page = cb.data.split(":")
    d = await clans.info(clan_id=int(cid), page=int(page))
    await safe_edit(cb, _card(d), _card_kb(d))
    await safe_answer(cb)


@router.message(Command("clan", "clans"))
async def clan_cmd(m: Message, command: CommandObject, db_user: User):
    parts = (command.args or "").split(None, 1)
    sub, rest = (parts[0].lower() if parts else ""), (parts[1].strip() if len(parts) > 1 else "")
    uid = db_user.id
    if sub in ("help", "?"):
        await m.reply(HELP.format(cost=num(R.clan_create_cost), bonus=R.clan_daily_bonus, step=num(R.clan_level_step)))
    elif sub == "create":
        if not rest: raise GameError("Usage: /clan create &lt;name&gt;")
        name = await clans.create(uid, rest)
        await m.reply(f"🏰 Clan <b>{esc(name)}</b> founded! Invite friends: <code>/clan join {esc(name)}</code>")
    elif sub == "join":
        if not rest: raise GameError("Usage: /clan join &lt;name&gt;")
        await m.reply(f"🤝 Welcome to <b>{esc(await clans.join(uid, rest))}</b>!")
    elif sub == "leave":
        await m.reply(f"👋 You left <b>{esc(await clans.leave(uid))}</b>.")
    elif sub == "kick":
        target, _ = await resolve_target(m, rest.split())
        if target is None: raise GameError("Usage: reply to a member or /clan kick @user")
        await m.reply(f"🚪 Removed that player from <b>{esc(await clans.kick(uid, target))}</b>.")
    elif sub == "disband":
        await m.reply(f"💥 Clan <b>{esc(await clans.disband(uid))}</b> was disbanded.")
    elif sub in ("deposit", "dep"):
        if not rest: raise GameError("Usage: /clan deposit &lt;amount&gt;")
        w = await economy.balance(uid)
        amt = parse_amount(rest.split()[0], w.balance)
        if amt is None: raise GameError("❌ Invalid amount.")
        name, bank, old, new = await clans.deposit(uid, amt)
        up = f"\n🎉 <b>{esc(name)} reached level {new}!</b>" if new > old else ""
        await m.reply(f"🏦 Deposited <b>{num(amt)}</b> 💎. Clan bank: <b>{num(bank)}</b> 💎.{up}")
    elif sub == "top":
        text, markup = await _top_view(0)
        await m.reply(text, reply_markup=markup)
    elif sub in ("info", ""):
        try:
            d = await clans.info(name=rest or None, uid=uid)
        except GameError as e:
            if str(e) != "NOCLAN": raise
            await m.reply(HELP.format(cost=num(R.clan_create_cost), bonus=R.clan_daily_bonus, step=num(R.clan_level_step)))
            return
        await m.reply(_card(d), reply_markup=_card_kb(d))
    else:
        await m.reply(HELP.format(cost=num(R.clan_create_cost), bonus=R.clan_daily_bonus, step=num(R.clan_level_step)))
