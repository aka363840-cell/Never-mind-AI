from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from database.models import User
from game.engine import STRATEGIES, stance_help
from game.rules import POWERS, R
from keyboards.menus import kb
from services import bounty, combat, duel, shop
from services.errors import GameError
from utils import paging
from utils.fmt import dur, esc, mention, num, parse_amount
from utils.targets import resolve_target
from utils.telegram import safe_answer, safe_edit, safe_send

router = Router()


def render_fight(o: combat.FightOutcome) -> str:
    """Short result: who won (clickable names) and, if someone died, the revive line. Nothing else."""
    r = o.result
    if r.winner is None:
        return "🤝 <b>Draw</b>"
    a, d = mention(o.attacker, o.attacker_name), mention(o.defender, o.defender_name)
    w, l = (a, d) if r.winner == o.attacker else (d, a)
    if o.killed:
        return (f"🏆 <b>{w}</b> kills {l}!\n"
                f"💀 <b>{l}</b> is dead. Pay {num(R.revive_cost)} 💎 with /revive to come back to life.")
    return f"🏆 <b>{w}</b> defeats {l}!"


async def _attack(m: Message, command: CommandObject, db_user: User, bot: Bot, kind: str):
    target, _ = await resolve_target(m, (command.args or "").split())
    if kind == "revenge" and target is None:
        from sqlalchemy import select
        from datetime import timedelta
        from database.models import CombatRecord, utcnow
        from database.session import db
        async with db.tx() as s:
            target = (await s.execute(select(CombatRecord.attacker_id).where(
                CombatRecord.defender_id == db_user.id, CombatRecord.winner_id == CombatRecord.attacker_id,
                CombatRecord.revenged == False, CombatRecord.kind.in_(("kill", "revenge")),  # noqa: E712
                CombatRecord.created_at >= utcnow() - timedelta(seconds=R.revenge_window_s))
                .order_by(CombatRecord.id.desc()).limit(1))).scalar()
    if target is None:
        raise GameError("Usage: reply to a player with /kill, or /kill @user" if kind == "kill" else "❌ Nobody to take revenge on.")
    o = await combat.attack(db_user.id, target, m.chat.id if m.chat.type != "private" else None, kind=kind)
    text = render_fight(o)
    await m.reply(text)
    if m.chat.type == "private":
        await safe_send(bot, target, text)


@router.message(Command("kill"))
async def kill(m: Message, command: CommandObject, db_user: User, bot: Bot):
    await _attack(m, command, db_user, bot, "kill")


@router.message(Command("revenge"))
async def revenge(m: Message, command: CommandObject, db_user: User, bot: Bot):
    await _attack(m, command, db_user, bot, "revenge")


@router.message(Command("revive"))
async def revive(m: Message, db_user: User):
    paid = await combat.revive(db_user.id)
    await m.reply(f"✨ You paid <b>{num(paid)}</b> 💎 and came back to life! You're briefly protected from kills "
                  f"({dur(R.kill_victim_immunity_s)}).")


def _protect_menu() -> str:
    rows = "\n".join(f"• <code>/protect {h}h</code> — {num(combat.protect_price(h))} 💎" for h in (1, 2, 3, 6, 12, 24)
                     if h <= R.protect_max_hours)
    return (f"🛡 <b>Protection</b>\n{rows}\n\nAny whole number of hours up to {R.protect_max_hours}h works "
            f"({num(R.protect_cost_per_hour)} 💎 per hour). Paid from your wallet. Attacking anyone drops it.")


@router.message(Command("protect"))
async def protect(m: Message, command: CommandObject, db_user: User):
    arg = (command.args or "").strip().lower()
    if not arg:
        return await m.reply(_protect_menu())
    raw = arg.split()[0]
    raw = raw[:-1] if raw.endswith("h") else raw
    if not raw.isdigit() or len(raw) > 3:
        raise GameError("❌ Use hours, e.g. <code>/protect 1h</code> or <code>/protect 2h</code>.")
    secs, price = await combat.protect(db_user.id, int(raw))
    await m.reply(f"🛡 Protected for <b>{dur(secs)}</b> (paid {num(price)} 💎). Nobody can kill you meanwhile. "
                  f"Attacking anyone drops it.")


WANTED_PER = 10


async def wanted_view(page: int = 0) -> tuple[str, InlineKeyboardMarkup | None]:
    """Most-wanted board, WANTED_PER targets per page with ◀️ ▶️ (shared: anyone's tap turns the page)."""
    rows, total = await bounty.wanted_page(0, WANTED_PER)
    page = paging.clamp(page, total, WANTED_PER)
    pages = paging.total_pages(total, WANTED_PER)
    if page:
        rows, _ = await bounty.wanted_page(page, WANTED_PER)
    if rows:
        lines = ["🎯 <b>Most Wanted</b>"] + [f"• {mention(t, n)} — <b>{num(tot)}</b> 💎 ({c})" for t, n, tot, c in rows]
    else:
        lines = ["🎯 No active bounties."]
    if pages > 1:
        lines.append(paging.footer(page, pages))
    lines.append(f"\n/bounty @user &lt;amount&gt; (min {R.bounty_min}) · /bounty cancel &lt;id&gt;")
    row = paging.nav(lambda p: f"bty:{p}", page, pages)
    return "\n".join(lines), (kb([row]) if row else None)


@router.callback_query(F.data.startswith("bty:"))
async def wanted_page_cb(cb: CallbackQuery):
    text, markup = await wanted_view(int(cb.data.split(":")[1]))
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.message(Command("bounty"))
async def bounty_cmd(m: Message, command: CommandObject, db_user: User):
    args = (command.args or "").split()
    if args and args[0].lower() == "cancel" and len(args) == 2 and args[1].isdigit():
        refund = await bounty.cancel(db_user.id, int(args[1]))
        return await m.reply(f"↩️ Bounty cancelled. Refunded <b>{num(refund)}</b> 💎.")
    target, rest = await resolve_target(m, args)
    if target is None or not rest:
        text, markup = await wanted_view(0)
        return await m.reply(text, reply_markup=markup)
    amount = parse_amount(rest[0])
    if amount is None: raise GameError("❌ Invalid amount.")
    bid = await bounty.place(db_user.id, target, amount)
    await m.reply(f"🎯 Bounty #{bid} placed: <b>{num(amount)}</b> 💎 escrowed. Expires in {dur(R.bounty_ttl_s)}.")


@router.message(Command("activate"))
async def activate(m: Message, command: CommandObject, db_user: User):
    target, _ = await resolve_target(m, (command.args or "").split())
    info = await shop.activate(db_user.id, target)
    p = POWERS[info["power"]]
    extra = ""
    if info["power"] == "ORACLE":
        extra = (f"\n👁 <b>{esc(info['target'])}</b>: Lv {info['level']} · ❤️ {info['hp']} · ⚔️ {info['atk']} · 🛡 {info['dfn']}\n"
                 f"⚡ Power: {esc(info['their_power'])} · 🛡 Protected: {dur(info['protected']) if info['protected'] else 'no'}")
    elif info["power"] == "GUARDIAN":
        extra = f"\n🛡 {esc(info['target'])} is protected for {dur(info['seconds'])}."
    elif "seconds" in info:
        extra = f"\nActive for {dur(info['seconds'])}."
    await m.reply(f"{p.emoji} <b>{p.name}</b> activated!{extra}")


# ------------------------------------------------------------ duels
@router.message(Command("duel"))
async def duel_cmd(m: Message, command: CommandObject, db_user: User):
    target, rest = await resolve_target(m, (command.args or "").split())
    if target is None:
        raise GameError(f"Usage: reply with /duel [wager] or /duel @user [wager] (min {R.duel_min_wager} 💎)")
    wager = R.duel_min_wager      # every duel has a stake: the winner takes diamonds, the loser loses them
    if rest:
        wager = parse_amount(rest[0])
        if wager is None: raise GameError("❌ Invalid wager.")
    d = await duel.create(db_user.id, target, wager, m.chat.id)
    sent = await m.answer(
        f"⚔️ <b>Duel challenge!</b>\n{mention(db_user.id, db_user.first_name)} challenges "
        f"<a href=\"tg://user?id={target}\">you</a> for <b>{num(wager)}</b> 💎.\nExpires in {dur(R.duel_invite_ttl_s)}.",
        reply_markup=kb([[B(text="✅ Accept", callback_data=f"duel:acc:{d.id}"), B(text="❌ Decline", callback_data=f"duel:dec:{d.id}")]]))
    await duel.set_message(d.id, sent.message_id)


def _strategy_kb(duel_id: int):
    return kb([[B(text=lbl, callback_data=f"duel:ch:{duel_id}:{k}") for k, (lbl, _, _) in STRATEGIES.items()]])


@router.callback_query(F.data.startswith("duel:"))
async def duel_cb(cb: CallbackQuery, db_user: User):
    _, action, *rest = cb.data.split(":")
    did = int(rest[0])
    if action == "acc":
        d = await duel.accept(did, db_user.id)
        await safe_edit(cb, f"⚔️ <b>Duel accepted!</b>{f' Pot: <b>{num(d.wager * 2)}</b> 💎.' if d.wager else ''}\n"
                            f"Both players: pick your strategy (your choice stays <b>secret</b>). {dur(R.duel_play_ttl_s)} to decide.\n<i>{stance_help()}</i>",
                        _strategy_kb(did))
        await safe_answer(cb, "Challenge accepted!")
    elif action == "dec":
        await duel.decline(did, db_user.id)
        await safe_edit(cb, "❌ Duel declined.")
        await safe_answer(cb)
    elif action == "ch":
        res = await duel.choose(did, db_user.id, rest[1])
        if res is None:
            await safe_answer(cb, f"Locked in: {STRATEGIES[rest[1]][0]}. Waiting for your opponent…", alert=True)
            return
        r, names = res.result, res.names
        ids = list(names)
        txt = "\n".join(esc(x) for x in r.log[-8:])
        head = "🤝 <b>Draw!</b>" if res.winner is None else f"🏆 <b>{esc(names[res.winner])}</b> wins!"
        pot = f"\n💎 Pot: <b>{num(res.pot)}</b>" if res.pot and res.winner else ("\n💎 Stakes returned." if res.pot else "")
        note = "" if res.credited else "\n<i>(Stats not credited: too many duels with this opponent today.)</i>"
        await safe_edit(cb, f"⚔️ <b>Duel result</b>\n\n{txt}\n\n❤️ {esc(names[ids[0]])}: {r.hp[ids[0]]} · {esc(names[ids[1]])}: {r.hp[ids[1]]}\n{head}{pot}{note}")
        await safe_answer(cb)
