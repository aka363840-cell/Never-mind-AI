"""Screen renderers shared by commands and menu buttons. Pure presentation + read queries."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup

from sqlalchemy import select

from database.models import Pet, Property, User
from database.session import db
from game.rules import ACHIEVEMENTS, CATEGORIES, PET_SPECIES, POWERS, R
from keyboards.menus import back, kb, main_menu
from services import arcade, clans, combat, cooldowns, economy, extras, family, leaderboard, life, progress, shop
from game.engine import stance_help
from utils import brand, paging
from utils.fmt import dur, esc, mention, num



DIV = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"


def banner() -> str:
    n = esc(brand.fancy(brand.name()))
    return f"╭━━━━━━━━━━━━━━━━╮\n      ◈ <b>{n}</b> {brand.EMOJI}\n╰━━━━━━━━━━━━━━━━╯"


async def main(first_name: str) -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n{DIV}\n✨ Hey <b>{esc(first_name)}</b>!\nWhat shall we do today?\n{DIV}\n<i>Pick an option below 👇</i>"), main_menu()


async def welcome(first_name: str) -> tuple[str, InlineKeyboardMarkup]:
    """First /start of a brand-new user in DM: greeting + about + quick start."""
    text = (f"{banner()}\n\n💖 <b>Welcome, {esc(first_name)}!</b>\n"
            f"I'm <b>{esc(brand.name())}</b> — your game, family &amp; fun companion on Telegram.\n\n"
            f"✦ <b>What you can do here</b> ✦\n"
            "💎 Earn diamonds — /daily /work /fish /mine\n"
            "⚔️ Fight, duel &amp; collect powers\n"
            "🗝 Dungeons, 🐾 pets &amp; 🏰 clans\n"
            "💍 Marry, 👶 have kids, 👨‍👩‍👧‍👦 build a whole family\n"
            "🎭 40+ fun actions with GIFs — /hug /kiss /pat\n"
            "🤖 AI chat in groups — tag me or say my name, in your own language\n\n"
            f"{DIV}\n🎁 <b>First step:</b> send /daily to claim your reward!\n"
            f"👥 Add me to your group and play with friends.")
    return text, welcome_kb()


def welcome_kb() -> InlineKeyboardMarkup:
    rows = [[B(text="🎮 Open Menu", callback_data="nav:main"), B(text="📖 Commands", callback_data="nav:help")],
            [B(text="ℹ️ About", callback_data="nav:about"), B(text="👨‍👩‍👧 Family", callback_data="nav:family")]]
    if brand.username():
        rows.append([B(text="➕ Add me to your group", url=brand.link("startgroup=true"))])
    return kb(rows)


async def about() -> tuple[str, InlineKeyboardMarkup]:
    from sqlalchemy import func, select
    from database.models import GroupSetting
    from config import get_settings
    async with db.tx() as s:
        users_n = (await s.execute(select(func.count()).select_from(User))).scalar() or 0
        groups_n = (await s.execute(select(func.count()).select_from(GroupSetting))).scalar() or 0
    owner = get_settings().owner_id
    text = (f"{banner()}\n\nℹ️ <b>About {esc(brand.name())}</b>\n{DIV}\n"
            f"{brand.EMOJI} A smart AI chat companion + social RPG-economy game: talk to me about anything, earn 💎, fight, explore dungeons, raise pets, join clans — "
            "and live a little life: marriage, kids, houses and a full family tree.\n\n"
            "✦ <b>Highlights</b>\n"
            "• 🤖 AI chat in groups — tag me, reply to me or say my name; I reply in your language\n"
            "• 💎 Economy, shop, powers &amp; missions\n• ⚔️ PvP, duels, bounties\n• 🗺 Dungeon, expeditions, group heists\n"
            f"• 💍 Marriage · 👶 kids · 👨‍👩‍👧‍👦 father, mother, brother, sister, grandparents, uncles, aunts, in-laws … {len(family.ROLES)} relations\n"
            "• 🎭 Fun actions with reaction GIFs\n• 🛡 Safe: every diamond move is logged\n\n"
            f"{DIV}\n👥 <b>{users_n:,}</b> players · 💬 <b>{groups_n:,}</b> groups\n"
            + (f"👑 Owner: <a href=\"tg://user?id={owner}\">contact</a>\n" if owner else "")
            + "\n<i>Need help? Open 📖 Commands.</i>")
    rows = [[B(text="📖 Commands", callback_data="nav:help"), B(text="🎮 Menu", callback_data="nav:main")]]
    if brand.username():
        rows.insert(0, [B(text="➕ Add me to your group", url=brand.link("startgroup=true"))])
    return text, kb(rows)


_HELP_SECTIONS: list[tuple[str, list[str]]] | None = None


def _help_sections() -> list[tuple[str, list[str]]]:
    """Every public command grouped by category (built once; owner/mod commands are left out)."""
    global _HELP_SECTIONS
    if _HELP_SECTIONS is None:
        from utils import helpcatalog
        try:
            from handlers import fun
            actions, action_aliases = fun.ACTIONS, fun.ACTION_ALIASES
        except Exception:                      # never let /help break because of an optional module
            actions, action_aliases = {}, {}
        items = helpcatalog.entries(brand.name(), brand.EMOJI, actions, action_aliases, family.ROLES, family.ALIASES)
        _HELP_SECTIONS = helpcatalog.sections(items, actions, family.ROLES)
    return _HELP_SECTIONS


def command_guide(cat: int | None = None, compact: bool = False, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """/help: category buttons (cat=None) or the commands of one category (cat=index).
    compact=True drops the About/Menu row (groups)."""
    secs = _help_sections()
    rows: list[list[B]] = []
    if cat is None or not (0 <= int(cat) < len(secs)):
        text = (f"{brand.line()}\n📖 <b>Command guide</b>\n{DIV}\n"
                f"Pick a category to see its commands 👇\n{DIV}\n"
                f"<i>Reply to a player or use @user where a target is needed.</i>")
        btns = [B(text=t, callback_data=f"help:c{i}") for i, (t, _) in enumerate(secs)]
        rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
        rows.append([B(text="📜 Rules (kill, rob, protection…)", callback_data="rules:home")])
    else:
        cat = int(cat)
        title, pages = secs[cat]
        page = min(max(0, int(page)), len(pages) - 1)
        text = (f"{brand.line()}\n<b>{esc(title)}</b>\n{DIV}\n{pages[page]}\n{DIV}\n"
                f"<i>Reply to a player or use @user where a target is needed.</i>"
                f"{paging.footer(page, len(pages))}")
        arrows = paging.nav(lambda p: f"help:c{cat}:{p}", page, len(pages))
        if arrows:
            rows.append(arrows)
        rows.append([B(text="◀️ Categories", callback_data="help:home")])
    if not compact:
        rows.append([B(text="ℹ️ About", callback_data="nav:about"), B(text="🎮 Menu", callback_data="nav:main")])
    return text, kb(rows)


# ---------------------------------------------------------------- /rules: every rule, with its real numbers
def _pct(x: float) -> str:
    return f"{x * 100:g}%"


def _rule_kill() -> str:
    return (f"⚔️ <b>Kill rules</b>\n{DIV}\n"
            f"<code>/kill</code> (reply or @user) starts a {R.fight_rounds}-round fight. Level, equipped gear, your active power and a Health Potion all count.\n\n"
            f"🏆 <b>If you win</b>\n• You take <b>{_pct(R.kill_reward_pct)}</b> of their wallet. Banked 💎 are never touched.\n"
            f"• They are <b>dead</b>: they must pay {R.revive_cost} 💎 with /revive before they can fight, rob or kill again.\n• +{R.xp_win} XP.\n\n"
            f"💔 <b>If you lose</b>\n• You pay <b>{_pct(R.kill_loss_pct)}</b> of your wallet (at most {R.kill_loss_max} 💎) to the winner. +{R.xp_loss} XP.\n\n"
            f"🚫 <b>Limits</b>\n• You can't attack a dead or protected player.\n"
            f"• Someone who just revived is immune for {dur(R.kill_victim_immunity_s)}.\n"
            f"• The same two players can fight at most <b>{R.kill_pair_daily_limit}×</b> per day.\n"
            f"• Attacking drops your own protection.\n\n"
            f"😠 <b>Revenge</b>: after someone kills you, <code>/revenge</code> within <b>{dur(R.revenge_window_s)}</b> gives you "
            f"+{_pct(R.revenge_attack_bonus)} attack against them.")


def _rule_rob() -> str:
    return (f"🕵️ <b>Rob rules</b>\n{DIV}\n"
            f"<code>/rob @user amount</code> (or reply). You choose the amount, at least <b>{R.rob_min_steal}</b> 💎.\n\n"
            f"📏 <b>How much you may ask for</b>\n• At most {_pct(R.rob_steal_pct_max)} of their wallet and never more than {num(R.rob_max_steal)} 💎.\n"
            f"• They need at least {R.rob_target_min_wallet} 💎 in their wallet; you need at least {R.rob_robber_min_wallet}.\n• Banked 💎 are safe.\n\n"
            f"🎲 <b>Outcome</b> ({_pct(R.rob_success_chance)} success)\n• ✅ You take exactly the amount you asked for.\n"
            f"• 🚨 Caught: you pay the victim <b>half</b> of what you tried to take (between {R.rob_fine_min} and {num(R.rob_fine_max)} 💎) "
            f"and go to <b>jail for {dur(R.rob_fail_jail_s)}</b>. Greedier attempts cost more.\n"
            f"• Jail blocks working, crimes, robbing, fishing, mining, dungeons and heists.\n\n"
            f"⏱ <b>Waiting times</b>\n• You: {dur(R.rob_cooldown_s)} between robberies.\n• Same victim again: {dur(R.rob_pair_cooldown_s)}.\n"
            f"• A robbed player is shielded for {dur(R.rob_victim_immunity_s)}.\n• Dead or protected players can't be robbed.")


def _rule_protect() -> str:
    return (f"🛡 <b>Protection, death and revive</b>\n{DIV}\n"
            f"• <code>/protect 2h</code> shields you from /kill and /rob. Costs <b>{R.protect_cost_per_hour} 💎 per hour</b>, up to {R.protect_max_hours}h.\n"
            f"• Attacking someone drops your own protection.\n• You can't buy more while one is running.\n\n"
            f"💀 <b>Dead?</b> You can't kill, rob or duel. <code>/revive</code> costs {R.revive_cost} 💎 from your wallet, then you are immune for {dur(R.kill_victim_immunity_s)}.\n\n"
            f"🏦 <b>Safest place:</b> the bank. Kills and robberies only take from the wallet.")


def _rule_duel() -> str:
    return (f"🤺 <b>Duel rules</b>\n{DIV}\n"
            f"<code>/duel @user wager</code> — a friendly fight with a real stake, wager {R.duel_min_wager}–{num(R.duel_max_wager)} 💎.\n"
            f"• Both players put the wager in escrow when it's accepted; the <b>winner takes the pot</b>.\n"
            f"• The invite expires after {dur(R.duel_invite_ttl_s)}, and each round must be played within {dur(R.duel_play_ttl_s)}.\n"
            f"• Only the first {R.duel_pair_daily_credit} duels per pair per day count for XP and win/loss stats.\n"
            f"• Each player secretly picks a stance: {stance_help()}.\n"
            f"• Duels never kill anyone.")


def _rule_bounty() -> str:
    return (f"🎯 <b>Bounty rules</b>\n{DIV}\n"
            f"<code>/bounty @user amount</code> puts diamonds on a head: {num(R.bounty_min)}–{num(R.bounty_max)} 💎 from your wallet.\n"
            f"• Whoever <b>kills</b> that player collects it.\n• It expires after {dur(R.bounty_ttl_s)}.\n"
            f"• Cancel it and get {_pct(R.bounty_cancel_refund_pct)} back.\n• <code>/bounty</code> alone lists the most wanted.")


def _rule_money() -> str:
    return (f"💰 <b>Earning and the bank</b>\n{DIV}\n"
            f"• /daily: <b>{R.daily_reward}</b> 💎 every {dur(R.daily_cooldown_s)}, +{R.streak_bonus} per streak day (up to {R.streak_max_days}). "
            f"Miss it by more than {dur(R.streak_window_s)} and the streak resets.\n"
            f"• /weekly: <b>{num(R.weekly_reward)}</b> 💎 every {dur(R.weekly_cooldown_s)}.\n"
            f"• /work: {R.work_min}–{R.work_max} 💎 every {dur(R.work_cooldown_s)}.\n"
            f"• /crime: {_pct(R.crime_success_chance)} to win {R.crime_reward_min}–{R.crime_reward_max} 💎; if caught you pay {R.crime_fine_min}–{R.crime_fine_max} 💎 and sit in jail {dur(R.crime_jail_s)}.\n"
            f"• /spin: a free lucky wheel every {dur(R.spin_cooldown_s)}.\n\n"
            f"🏦 <b>Bank</b>: /deposit and /withdraw. Banked 💎 can't be robbed or killed, but at most {_pct(R.bank_max_pct)} of your total may sit there.\n\n"
            f"💸 <b>/pay</b>: {R.pay_min}–{num(R.pay_max)} per payment; your account must be 24h old; max {R.pay_pair_daily_count} payments to the same player per day and {num(R.pay_sender_daily_cap)} 💎 per day in total.")


def _rule_casino() -> str:
    return (f"🎰 <b>Casino rules</b>\n{DIV}\n"
            f"• Bets: <b>{R.casino_min_bet}–{num(R.casino_max_bet)}</b> 💎, one every {R.casino_cooldown_s}s.\n"
            f"• /slots and /flip pay out from your wallet; the house keeps a small edge, so play for fun.\n"
            f"• /spin is free (every {dur(R.spin_cooldown_s)}).")


def _rule_gear() -> str:
    return (f"🛒 <b>Gear, powers and items</b>\n{DIV}\n"
            f"• Weapons, armour and shields only help in fights while <b>equipped</b> (one of each). /equip KEY.\n"
            f"• Each upgrade level adds +{_pct(R.upgrade_stat_bonus)} of an item's stats. The upgrade cost is shown on its button.\n"
            f"• Powers: buy, equip one, then /activate before a fight. Upgrades add +{_pct(R.power_level_bonus)} strength per level.\n"
            f"• Health Potion: +{R.potion_hp} HP in your fights for {dur(R.potion_duration_s)}.\n"
            f"• Selling gives back {_pct(R.sell_ratio)} of the shop price.\n"
            f"• Collectibles are trophies with no fight bonus.")


def _rule_home() -> str:
    houses = "\n".join(f"{e} {n}: {num(p)} 💎 → {num(r)} 💎/day" for e, n, p, r in life.HOUSES.values())
    return (f"🏠 <b>Houses and rent</b>\n{DIV}\n{houses}\n\n"
            f"• /houses to buy or sell, /collect for your rent (once every {dur(life.RENT_CD_S)}).\n• Selling a house returns part of its price.\n\n"
            f"🏰 <b>Clans</b>: creating costs {num(R.clan_create_cost)} 💎, up to {R.clan_max_members} members. Every {num(R.clan_level_step)} 💎 in the clan bank raises its level; "
            f"each level adds +{R.clan_daily_bonus} 💎 to every member's /daily.\n\n"
            f"🐾 <b>Pets</b>: adopt for {num(R.pet_adopt_cost)} 💎, feed for {R.pet_feed_cost} 💎 (+{R.pet_feed_xp} XP, every {dur(R.pet_feed_cooldown_s)}), max level {R.pet_max_level}. /hunt every {dur(R.hunt_cooldown_s)}.")


RULE_TOPICS = [("kill", "⚔️ Kill & Revenge", _rule_kill), ("rob", "🕵️ Rob", _rule_rob), ("protect", "🛡 Protection & Death", _rule_protect),
               ("duel", "🤺 Duels", _rule_duel), ("bounty", "🎯 Bounties", _rule_bounty), ("money", "💰 Earning & Bank", _rule_money),
               ("casino", "🎰 Casino", _rule_casino), ("gear", "🛒 Gear & Powers", _rule_gear), ("home", "🏠 Houses, Clans, Pets", _rule_home)]


def rules(topic: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """/rules: topic buttons, or one rule page. Numbers come from game.rules, so this can never go out of date."""
    page = next((fn for k, _, fn in RULE_TOPICS if k == topic), None)
    if page is None:
        text = (f"{brand.line()}\n📜 <b>Rules</b>\n{DIV}\nHow everything works, with the exact numbers. Pick a topic 👇")
        btns = [B(text=t, callback_data=f"rules:{k}") for k, t, _ in RULE_TOPICS]
        return text, kb([btns[i:i + 2] for i in range(0, len(btns), 2)] + [[B(text="📖 Commands", callback_data="help:home")]])
    return f"{brand.line()}\n\n{page()}", kb([[B(text="◀️ All rules", callback_data="rules:home")]])


def family_help() -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n👨‍👩‍👧‍👦 <b>Love &amp; Family</b>\n{DIV}\n"
            "💍 /marry <i>(reply)</i> · /couple · /divorce\n💑 /date · 🎁 /gift · 👶 /baby · 🧸 /adopt\n\n"
            "🌳 <b>Family tree</b>\n/family — see yours (or reply to see theirs)\n/familytree — a picture of the tree, family only (no friends)\n"
            "Reply to someone with <code>/addfamily brother</code> or just <code>/father</code> <code>/mother</code> <code>/brother</code> <code>/friend</code> …\n"
            f"They must accept 💞 · /roles shows all {len(family.ROLES)} roles\n/unfamily — remove a relation\n/gender — label relatives correctly\n\n"
            "💰 /familybonus — daily bonus from your family\n🏠 /houses — buy property · 💰 /collect — collect its rent (/rent) every 24h"), kb([back()])


async def profile(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    async with db.tx() as s:
        u = await s.get(User, uid)
        w = await economy.wallet_of(s, uid)
        pet = await s.get(Pet, uid)
        dead = await combat.is_dead(s, uid)
        prot_left = await combat.protection_left(s, uid)
        potion_left = await cooldowns.remaining(s, uid, "buff_potion")
        clan_row = await clans._member(s, uid)
        owned_houses = [k for k in (await s.execute(select(Property.key).where(Property.user_id == uid))).scalars()
                        if k in life.HOUSES]
    n_ach = len(await progress.achievements_of(uid))
    lo = await shop.loadout(uid)
    gear = "\n".join([
        "🎽 <b>Gear</b>  <i>(buy in /shop, wear with /equip)</i>",
        f"🗡 Weapon: {lo['weapon']['name'] + ' Lv' + str(lo['weapon']['level']) if lo['weapon'] else '— none'}",
        f"🥋 Armour: {lo['armour']['name'] + ' Lv' + str(lo['armour']['level']) if lo['armour'] else '— none'}",
        f"🛡 Shield: {lo['shield']['name'] + ' Lv' + str(lo['shield']['level']) if lo['shield'] else '— none'}",
        f"📈 Gear bonus: ⚔️ +{lo['atk']:.0f} · 🛡 +{lo['dfn']:.0f} · ❤️ +{lo['hp']:.0f}"])
    status = []
    cp = await extras.couple(uid)
    if cp:
        status.append(f"💍 Married to <b>{esc(cp[1])}</b> · {cp[2]} days")
    rep = await arcade.rep_of(uid)
    if rep:
        status.append(f"🌟 Reputation: <b>{num(rep)}</b>")
    if prot_left:
        status.append(f"🛡 Protected for <b>{dur(prot_left)}</b>")
    if potion_left:
        status.append(f"🧪 Health Potion active: +{R.potion_hp} HP for <b>{dur(potion_left)}</b>")
    if clan_row:
        status.append(f"🏰 Clan: <b>{esc(clan_row[1].name)}</b>")
    if owned_houses:
        rent = sum(life.HOUSES[k][3] for k in owned_houses)
        status.append(f"🏠 Property: {' '.join(life.HOUSES[k][0] for k in owned_houses)} · rent {num(rent)} 💎/day (/collect)")
    extra = ("\n" + "\n".join(status) + "\n") if status else ""
    p = POWERS.get(u.equipped_power or "")
    pet_line = f"🐾 Pet: {PET_SPECIES[pet.species][0]} {esc(pet.name)} (Lv {pet.level})" if pet else "🐾 Pet: — (/hunt or /pet adopt)"
    title = f" · <i>{esc(u.title)}</i>" if u.title else ""
    need = u.level * R.xp_per_level
    text = (f"{brand.line()}\n\n👤 Player: <b>{esc(u.first_name)}</b>{title}{' · 💀 <b>DEAD</b> (/revive)' if dead else ''}\n🆔 ID: <code>{u.id}</code>\n\n"
            f"⭐ Level: {u.level}  (XP {u.xp}/{need})\n💎 Diamonds: {num(w.balance)}  · 🏦 Bank: {num(w.bank)}\n\n"
            f"⚔️ Kills: {u.kills}\n💀 Deaths: {u.deaths}\n🏆 Wins: {u.wins}\n📉 Losses: {u.losses}\n\n"
            f"{gear}\n{extra}\n⚡ Equipped Power: {p.emoji + ' ' + p.name if p else '—'}\n{pet_line}\n🏅 Achievements: {n_ach}/{len(ACHIEVEMENTS)}  (/achievements)")
    return text, kb([[B(text="🎒 Inventory", callback_data="inv:0"), B(text="⚡ Powers", callback_data="nav:powers")],
                     [B(text="🏆 Leaderboard", callback_data="lb:rich:0"), B(text="🔄 Profile", callback_data="nav:profile")],
                     back()])


async def balance(uid: int, name: str | None = None, own: bool = True) -> tuple[str, InlineKeyboardMarkup]:
    """Balance card headed by the PLAYER's name (clickable), not the bot's. own=False hides the command footer."""
    if name is None:
        async with db.tx() as s:
            u = await s.get(User, uid)
        name = (u.first_name if u else None) or "Player"
    w = await economy.balance(uid)
    text = (f"◈ <b>{mention(uid, name)}</b>\n\n💎 Wallet: <b>{num(w.balance)}</b>\n🏦 Bank: <b>{num(w.bank)}</b>\n"
            f"📊 Total: <b>{num(w.balance + w.bank)}</b>")
    if own:
        text += ("\n\n<i>/daily · /weekly · /work · /crime · /rob &lt;amount&gt; · /pay · /deposit · /withdraw</i>\n"
                 f"<i>🏦 Bank limit: {_pct(R.bank_max_pct)} of your total. Kills and revives use the wallet only.</i>")
    return text, kb([back()] if own else [])


def combat_help() -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n\n⚔️ <b>Combat</b>\n\n"
            f"/kill <i>(reply or @user)</i> — kill a player and take {int(R.kill_reward_pct * 100)}% of their free 💎\n/duel <i>@user [wager]</i> — duel for diamonds (min {R.duel_min_wager})\n"
            f"/protect <i>1h, 2h…</i> — shield, {R.protect_cost_per_hour} 💎 per hour\n"
            f"/revenge — hit back within {dur(R.revenge_window_s)}\n"
            f"/revive — dead players pay <b>{R.revive_cost}</b> 💎 to come back (the dead can't kill)\n/bounty <i>@user amount</i> — put diamonds on a head\n"
            "/bounty — see the most wanted\n\n<i>Attacking drops your own protection. The winner of a fight wins diamonds, the loser loses them.</i>"), kb([[B(text="⚔️ Kill rules", callback_data="rules:kill"), B(text="🕵️ Rob rules", callback_data="rules:rob")], back()])


async def powers(uid: int, text_only: bool = False) -> tuple[str, InlineKeyboardMarkup | None]:
    owned = await shop.owned_powers(uid)
    async with db.tx() as s:
        eq = (await s.get(User, uid)).equipped_power
    lines, rows = [f"{brand.line()}\n\n⚡ <b>Powers</b>  <i>(1 equipped at a time)</i>\n"], []
    for k, p in POWERS.items():
        o = owned.get(k)
        status = f"Lv {o.level}/{p.max_level}{' ✅ equipped' if eq == k else ''}" if o else f"{num(p.price)} 💎"
        lines.append(f"{p.emoji} <b>{p.name}</b> — {status}  <code>{k}</code>\n   {p.description}\n   <i>Limit: {p.limitation} · Activate: {p.activation_cost}💎, cd {dur(p.cooldown_s)}</i>")
        if o:
            rows.append([B(text=f"{'❎ Unequip' if eq == k else '✅ Equip'} {p.name}", callback_data=f"eq:{k}")])
            if o.level < p.max_level:
                cost = int(p.price * R.power_upgrade_cost_ratio) * o.level
                rows.append([B(text=f"⬆️ {p.name} → Lv{o.level + 1} · {num(cost)}💎", callback_data=f"up:{k}")])
        else:
            rows.append([B(text=f"Buy {p.name} · {num(p.price)}💎", callback_data=f"buy:{k}")])
    rows.append([B(text="⚡ Activate equipped power", callback_data="act")])
    rows.append(back())
    lines.append("\n<b>How to use a power</b>\n1️⃣ Buy: <code>/buy KEY</code>  (KEY = the code shown above, e.g. /buy PHANTOM)\n"
                 "2️⃣ Equip: <code>/equip KEY</code>  (one power at a time)\n"
                 "3️⃣ Activate: <code>/activate</code>  — Guardian &amp; Oracle: <b>reply to a player</b> with /activate\n"
                 "⬆️ Level up: <code>/upgrade KEY</code>")
    return "\n".join(lines), (None if text_only else kb(rows))


CATEGORY_ROLE = {
    "powers": "⚡ <b>What they do:</b> special fight abilities (dodge a hit, +damage, reflect, survive a lethal hit…). "
              "Buy → <code>/equip KEY</code> → <code>/activate</code> before a fight. Upgrade to make them stronger.",
    "weapons": "🗡 <b>What they do:</b> add <b>attack</b> in every fight (/kill, /duel, /revenge) — but only while equipped. "
               "Buy → <code>/equip KEY</code>. One weapon at a time.",
    "armour": "🥋 <b>What it does:</b> adds <b>defence and health</b> in every fight, only while equipped. "
              "Buy → <code>/equip KEY</code>. One armour at a time.",
    "shields": "🛡 <b>What they do:</b> add <b>defence</b> in every fight, only while equipped. "
               "Buy → <code>/equip KEY</code>. One shield at a time.",
    "consumables": "🧪 <b>What they do:</b> single-use items. Buy → <code>/use KEY</code> "
                   "(Health Potion: +HP in your fights for a while · XP Scroll: instant XP).",
    "cosmetics": "🎭 <b>What they do:</b> a profile <b>title</b> shown next to your name. Buy → <code>/equip KEY</code>. "
                 "No fight bonus.",
    "collectibles": "💠 <b>What they do:</b> trophies with <b>no fight bonus</b>. They show in /inventory and can be "
                    "sold back (<code>/sell KEY</code>) for part of the price. Hunting and dungeons can drop them too.",
}


def purchase_next_step(category: str, key: str) -> str:
    """One line telling the player what to do right after buying (gear is useless until equipped)."""
    if category == "powers":
        return f"Next: <code>/equip {key}</code> then <code>/activate</code> before a fight."
    if category in ("weapons", "armour", "shields"):
        return f"⚠️ It only counts in fights once equipped: <code>/equip {key}</code>"
    if category == "cosmetics":
        return f"Next: <code>/equip {key}</code> to show it as your profile title."
    if category == "consumables":
        return f"Next: <code>/use {key}</code> when you want the effect."
    return "Kept in your /inventory (no fight bonus). Sell it any time with /sell."


def shop_home(text_only: bool = False) -> tuple[str, InlineKeyboardMarkup | None]:
    names = {"powers": "⚡ Powers", "weapons": "🗡 Weapons", "armour": "🥋 Armour", "shields": "🛡 Shields",
             "consumables": "🧪 Consumables", "cosmetics": "🎭 Cosmetics", "collectibles": "💠 Collectibles"}
    rows = [[B(text=names[c], callback_data=f"shop:{c}")] for c in CATEGORIES]
    head = f"{brand.line()}\n\n🛒 <b>Shop</b>\nAll prices in 💎. Choose a category — each page explains what its items do.\n<i>Gear (weapons, armour, shields) only works once you /equip it.</i>"
    if text_only:
        return head + "\n\n" + "\n".join(f"{names[c]} — <code>/shop {c}</code>" for c in CATEGORIES) + "\n\nBuy: <code>/buy KEY</code>", None
    return head, kb(rows + [back()])


async def shop_category(cat: str, text_only: bool = False) -> tuple[str, InlineKeyboardMarkup | None]:
    items = await shop.catalog(cat)
    lines, rows = [f"{brand.line()}\n\n🛒 <b>{cat.title()}</b>\n{CATEGORY_ROLE.get(cat, '')}\n"], []
    for it in items:
        stats = " ".join(x for x in (f"⚔️+{it['atk']}" if it.get("atk") else "", f"🛡+{it['dfn']}" if it.get("dfn") else "",
                                    f"❤️+{it['hp']}" if it.get("hp") else "") if x)
        lines.append(f"{it['name']} — <b>{num(it['price'])}</b> 💎 {stats}\n   <i>{esc(it['desc'])}</i> <code>{it['key']}</code>")
        rows.append([B(text=f"Buy {it['name']} · {num(it['price'])}", callback_data=f"buy:{it['key']}")])
    if text_only:
        return "\n".join(lines) + "\n\nBuy: <code>/buy CODE</code> · Equip: <code>/equip CODE</code> (CODE = the grey text next to each item) · all categories: /shop", None
    return "\n".join(lines), kb(rows + [back("shop")])


async def inventory(uid: int, page: int = 0, text_only: bool = False) -> tuple[str, InlineKeyboardMarkup | None]:
    order = {"weapons": 0, "armour": 1, "shields": 2, "cosmetics": 3, "consumables": 4, "collectibles": 5}
    items = sorted(await shop.inventory(uid), key=lambda it: order.get(it["cat"], 9))   # gear first, trophies last
    per = 6
    lines, rows = [f"{brand.line()}\n\n🎒 <b>Inventory</b>\n"], []
    if not items:
        lines.append("Empty. Visit the 🛒 Shop!")
    for it in items[page * per:(page + 1) * per]:
        gear = it["cat"] in ("weapons", "armour", "shields", "cosmetics")
        head = it["name"] + (f" ×{it['qty']}" if it["qty"] > 1 else "")
        if it["max"] > 1:                                   # only upgradable things have a level worth showing
            head += f" · Lv{it['level']}/{it['max']}"
        if gear:
            head += " · ✅ equipped" if it["equipped"] else " · ⚠️ not equipped"
        lines.append(f"{head}  <code>{it['key']}</code>")
        stats = " ".join(x for x in (f"⚔️ +{it['atk']:.0f}" if it["atk"] else "", f"🛡 +{it['dfn']:.0f}" if it["dfn"] else "",
                                     f"❤️ +{it['hp']:.0f}" if it["hp"] else "") if x)
        if stats:
            lines.append(f"   {stats} in fights" + ("" if it["equipped"] else " (once equipped)"))
        elif it["cat"] == "collectibles":
            lines.append(f"   🏆 trophy, no fight bonus · sells for {num(it['sell_value'])} 💎 (/sell {it['key']})")
        elif it["cat"] == "cosmetics":
            lines.append("   🎭 profile title, no fight bonus")
        elif it["cat"] == "consumables":
            lines.append("   🧪 single use")
        btns = []
        if gear and it["cat"] != "cosmetics" or it["cat"] == "cosmetics":
            btns.append(B(text=f"{'❎ Unequip' if it['equipped'] else '✅ Equip'} {it['plain']}"[:60], callback_data=f"eq:{it['key']}"))
        if it["cat"] in ("weapons", "armour", "shields") and it["max"] > 1 and it["level"] < it["max"]:
            btns.append(B(text=f"⬆️ {it['plain']} → Lv{it['level'] + 1} · {num(it['upgrade_cost'])}💎"[:60], callback_data=f"up:{it['key']}"))
        if it["cat"] == "consumables":
            btns.append(B(text=f"🧪 Use {it['plain']}"[:60], callback_data=f"use:{it['key']}"))
        for b in btns:                                       # one button per row: the label names the item, nothing to mix up
            rows.append([b])
    nav = []
    if page > 0: nav.append(B(text="◀️", callback_data=f"inv:{page - 1}"))
    if (page + 1) * per < len(items): nav.append(B(text="▶️", callback_data=f"inv:{page + 1}"))
    if nav: rows.append(nav)
    if items:
        lines.append(f"\n<i>Gear only works in fights while equipped. Upgrading costs 💎 (shown on the button) and adds +{_pct(R.upgrade_stat_bonus)} of the item's stats per level. "
                     "Sell with /sell followed by the grey code next to the item, e.g. <code>/sell golden_idol</code>.</i>")
    if text_only:
        if items:
            lines.append("Type a command followed by the item's code (the grey text above): <code>/equip CODE</code> · <code>/use CODE</code> · <code>/upgrade CODE</code> · <code>/sell CODE</code>")
        return "\n".join(lines), None
    return "\n".join(lines), kb(rows + [back()])


async def missions(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    ms = await progress.missions_for(uid)
    ach = await progress.achievements_of(uid)
    lines, rows = [f"{brand.line()}\n\n🎯 <b>Missions</b>"], []
    for period in ("daily", "weekly"):
        lines.append(f"\n<b>{period.title()}</b>")
        for m in [m for m in ms if m.period == period]:
            done = m.progress >= m.goal
            mark = "🎁 claimed" if m.claimed else ("✅ ready" if done else f"{min(m.progress, m.goal)}/{m.goal}")
            lines.append(f"• {esc(m.title)} — {mark}  (+{m.reward}💎)")
            if done and not m.claimed:
                rows.append([B(text=f"Claim +{m.reward}💎 · {m.title[:28]}", callback_data=f"ms:{m.id}")])
    lines.append(f"\n🏅 Achievements: <b>{len(ach & set(ACHIEVEMENTS))}/{len(ACHIEVEMENTS)}</b> — see /achievements")
    return "\n".join(lines), kb(rows + [back()])


_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
_TAB_EMOJI = {"rich": "💎", "kills": "⚔️", "level": "⭐", "wins": "🏆"}


def _board_lines(kind: str, rows_: list, name_limit: int | None = None) -> list[str]:
    out = []
    for rank, u, name, val in rows_:
        v = num(val) if kind != "level" else f"Lv {val // 1_000_000}"
        n = name if name_limit is None or len(name) <= name_limit else name[:name_limit - 1].rstrip() + "…"
        out.append(f"{_MEDALS.get(rank, f'{rank}.')} {esc(n)} — <b>{v}</b>")
    return out or ["No players yet."]


async def rankings_all(uid: int, name: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup | None]:
    """Every leaderboard in ONE message, PAGE players per board per page (used in groups).
    ◀️ ▶️ turn the page for everybody in the chat; the 📍 line is the rank of whoever opened /top (uid / name)."""
    per = leaderboard.PAGE
    kinds = list(leaderboard.KINDS)
    total = (await leaderboard.page(kinds[0], 0))[1]
    page = paging.clamp(page, total, per)
    pages = paging.total_pages(total, per)
    lines = [brand.line()]
    for k in kinds:
        rows_, _ = await leaderboard.page(k, page)
        lines.append(f"\n<b>{leaderboard.KINDS[k][0]}</b>")
        lines += _board_lines(k, rows_, name_limit=22)
    ranks = []
    for k in kinds:
        r, _ = await leaderboard.rank_of(k, uid)
        if r:
            ranks.append(f"{_TAB_EMOJI.get(k, '•')} #{r}")
    if ranks:
        who = name if len(name) <= 22 else name[:21].rstrip() + "…"
        lines.append(f"\n📍 <b>{esc(who)}</b>: " + "  ·  ".join(ranks))
    if pages > 1:
        lines.append(paging.footer(page, pages))
    row = paging.nav(lambda p: f"lbg:{p}:{uid}", page, pages)
    return "\n".join(lines), (kb([row]) if row else None)


async def rankings(kind: str, page: int, uid: int) -> tuple[str, InlineKeyboardMarkup]:
    title = leaderboard.KINDS[kind][0]
    rows_, total = await leaderboard.page(kind, page)
    lines = [f"{brand.line()}\n\n<b>{title}</b>\n"] + _board_lines(kind, rows_)
    r, val = await leaderboard.rank_of(kind, uid)
    if r:
        lines.append(f"\n📍 Your rank: <b>#{r}</b>")
    tabs = [B(text=("• " if k == kind else "") + lbl, callback_data=f"lb:{k}:0")
            for k, lbl in (("rich", "💎 Rich"), ("kills", "⚔️ Kills"), ("level", "⭐ Level"), ("wins", "🏆 Wins"))]
    nav = []
    if page > 0: nav.append(B(text="◀️", callback_data=f"lb:{kind}:{page - 1}"))
    if (page + 1) * leaderboard.PAGE < total: nav.append(B(text="▶️", callback_data=f"lb:{kind}:{page + 1}"))
    return "\n".join(lines), kb([tabs] + ([nav] if nav else []) + [back()])


def clan_help() -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n\n🏰 <b>Clans</b>\n\n/clan create <i>name</i> — {num(R.clan_create_cost)} 💎\n/clan join <i>name</i> · /clan leave · /clan info · /clan top\n"
            f"/clan deposit <i>amount</i> — level up your clan\n\nEach clan level = <b>+{R.clan_daily_bonus} 💎</b> on /daily for every member."), kb([back()])


def pet_help() -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n\n🐾 <b>Pets &amp; Hunting</b>\n\n/hunt — loot, XP and rare eggs every {R.hunt_cooldown_s // 60} min\n/pet — see your pet\n"
            f"/pet adopt — {num(R.pet_adopt_cost)} 💎\n/pet feed — level it up\n\nPets add attack, defence and HP in every fight.\n"
            "<i>Tip: in groups, watch for 🎁 diamond drops and 🐲 boss raids!</i>"), kb([back()])


def realm_help() -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n\n🗺 <b>The Realm</b>\n\n"
            "🗝 <b>Dungeon</b> — 5 floors of choices, stances, loot and a boss. Cash out or push on!\n"
            "🧭 <b>Expeditions</b> — send your pet away for hours; it comes home with treasure.\n"
            "🎭 <b>Heists</b> — group co-op jobs where every role matters.\n"
            "⏱ <b>Timers</b> — every cooldown on one screen.\n"
            "🏅 <b>Achievements</b> — collect them all.\n\n"
            "<i>All of it is skill &amp; teamwork — no betting.</i>"), kb([
                [B(text="🗝 Dungeon", callback_data="rl:dg"), B(text="🧭 Expedition", callback_data="rl:ex")],
                [B(text="🎭 Heist guide", callback_data="rl:hs"), B(text="⏱ Timers", callback_data="rl:cd")],
                [B(text="🏅 Achievements", callback_data="rl:ach")], back()])


def casino_help() -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n\n🎰 <b>Casino &amp; Luck</b>\n\n/spin — free lucky wheel every {R.spin_cooldown_s // 3600}h (jackpot up to 5,000 💎)\n"
            f"/slots <i>bet</i> — match 3 symbols, up to x77!\n/flip <i>bet [heads|tails]</i> — double or nothing\n"
            f"/daily — keep your 🔥 streak alive for bonus diamonds\n\n<i>Bets: {R.casino_min_bet}–{R.casino_max_bet:,} 💎. Play responsibly — it's only a game!</i>"), kb([back()])


def fun_help() -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n\n🎉 <b>Fun &amp; Social</b>\n\n/hug /slap /pat /poke /highfive /cuddle <i>(reply or @user)</i>\n"
            "/kiss /dance /wave /yeet … — see /actions for all of them\n/fish · /mine · /trivia — earn 💎 with mini-games\n/marry · /couple · /divorce 💍\n/date · /gift · /baby · /adopt · /family 👨‍👩‍👧‍👦\n/houses · /collect 🏠 · /familybonus\n"
            "/roast · /compliment · /rate · /choose · /iq · /lucky\n/ship — love calculator 💘\n/roll <i>2d6</i> — dice 🎲\n/8ball <i>question</i> 🎱\n/truth · /dare · /joke\n"
            f"/ask <i>anything</i> — talk to {brand.name()} {brand.EMOJI} (or just say my name in a group!)"), kb([back()])


def ai_info() -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n\n🤖 <b>Chat with {esc(brand.name())}</b>\n\nAI chat works in <b>groups</b>: tag me, reply to my message or say my name, in any language — I reply in the language you use. "
            "If you chat too fast I take a 2-minute break.\n\n"
            "<i>Chatting is just for fun: it never earns diamonds or affects the game.</i>"), kb([
                [B(text="🧹 Clear my chat history", callback_data="ai:clear")], back()])


def settings() -> tuple[str, InlineKeyboardMarkup]:
    return (f"{brand.line()}\n\n⚙️ <b>Settings</b>\n\n🧹 Clear my memory of our chat anytime.\n"
            "Group admins can use /settings inside a group."), kb([[B(text="🧹 Clear chat history", callback_data="ai:clear")], back()])
