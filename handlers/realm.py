"""Realm commands: /dungeon, /expedition, /heist, /cd (timers), /achievements.  Buttons carry the owner's id, so
nobody can press somebody else's run."""
from __future__ import annotations

import asyncio
import logging
import random
import secrets

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database.models import User
from database.session import db
from game.engine import STRATEGIES, stance_help
from game.rules import ACHIEVEMENTS, ITEMS, PET_SPECIES, R
from keyboards.menus import back, kb
from services import realm
from services.errors import GameError
from services.kv import kv
from utils import brand
from utils.fmt import bar, dur, esc, mention, num
from utils.telegram import safe_answer, safe_edit

log = logging.getLogger(__name__)
router = Router()


def _owner(cb: CallbackQuery, uid: str) -> int:
    if int(uid) != cb.from_user.id:
        raise GameError("🔒 This isn't yours — start your own with the command!")
    return int(uid)


def _unlocked_lines(names: list[str]) -> str:
    return "".join(f"\n🏅 <b>Achievement unlocked:</b> {n}" for n in names)


# ================================================================== DUNGEON
def dungeon_intro(uid: int, cd_left: int, level: int, back_row: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    status = "✅ <b>Ready!</b>" if not cd_left else f"⏳ Available in <b>{dur(cd_left)}</b>"
    text = (f"{brand.line()}\n\n🗝 <b>The Deep Dungeon</b>\n\n"
            "Fight your way down <b>5 floors</b>. Each room is your choice: fight, sneak, loot, pray, trade… "
            "Your <b>gear, pet, level and active power</b> all count.\n\n"
            f"⚔️ Pick a stance every fight:\n<i>{stance_help()}</i>\n"
            "💎 Diamonds are only banked when the run ends: <b>cash out between floors</b> to keep it all, "
            "or push on — dying keeps only <b>half</b>.\n"
            "🐲 Floor 5 hides a boss, a clear bonus and rare loot.\n\n"
            f"☠️ <b>Hard</b> mode (level 5+): tougher monsters, ×{realm.HARD_GOLD} diamonds, bigger bonus.\n\n"
            f"Your level: <b>{level}</b>  ·  {status}  ·  <i>cooldown {dur(realm.DUNGEON_CD_S)}</i>")
    rows = [[B(text="🗝 Enter · Normal", callback_data=f"dgs:{uid}:n"),
             B(text="☠️ Enter · Hard", callback_data=f"dgs:{uid}:h")]]
    if back_row:
        rows.append(back("realm"))
    return text, kb(rows)


def _hero_line(st: dict) -> str:
    buff = f"  ⚔️ +{round(st['buff'] * 100)}%" if st["buff"] else ""
    pw = f"  ⚡ {st['power'].title()}" if st.get("power") else ""
    return (f"❤️ {bar(st['hp'], st['mhp'], 10)} <b>{st['hp']}/{st['mhp']}</b>\n"
            f"💰 <b>{num(st['gold'])}</b> 💎  ·  ✨ {st['xp']} XP  ·  🎒 {len(st['loot'])} loot{buff}{pw}")


def render_run(st: dict) -> tuple[str, InlineKeyboardMarkup]:
    uid, room = st["uid"], st["room"]
    mode = "☠️ Hard" if st["hard"] else "Normal"
    head = f"🗝 <b>Dungeon · {mode}</b> — Floor <b>{st['floor']}/{realm.DUNGEON_FLOORS}</b>\n{_hero_line(st)}\n"
    note = f"\n{st['note']}\n" if st.get("note") else ""
    d = lambda a: f"dg:{uid}:{a}"  # noqa: E731
    if room is None:
        nxt = ("⚠️ Face the boss" if st["floor"] + 1 == realm.DUNGEON_FLOORS else f"⬇️ Descend to floor {st['floor'] + 1}")
        body = "\n<b>Floor cleared.</b> Push deeper, or bank your winnings?"
        rows = [[B(text=nxt, callback_data=d("go"))],
                [B(text=f"💰 Cash out ({num(st['gold'])} 💎)", callback_data=d("cash"))]]
        return head + note + body, kb(rows)
    t = room["t"]
    if t == "monster":
        title = "🐲 BOSS" if room["boss"] else "Monster"
        body = (f"\n{room['emoji']} <b>{esc(room['name'])}</b> · {title}\n"
                f"❤️ {bar(room['hp'], room['max'], 10)} <b>{room['hp']}/{room['max']}</b>\n<i>Choose your stance:</i>\n<i>{stance_help()}</i>")
        rows = [[B(text=STRATEGIES["a"][0], callback_data=d("fa")), B(text=STRATEGIES["b"][0], callback_data=d("fb")),
                 B(text=STRATEGIES["d"][0], callback_data=d("fd"))]]
        if room.get("sneak"):
            rows.append([B(text="🥷 Try to sneak past (55%)", callback_data=d("sn"))])
        return head + note + body, kb(rows)
    body = f"\n<i>{realm.ROOM_FLAVOR[t]}</i>"
    if t == "chest":
        rows = [[B(text="📦 Open it", callback_data=d("open")), B(text="🚶 Leave it", callback_data=d("leave"))]]
    elif t == "shrine":
        rows = [[B(text="🙏 Pray (+30% HP)", callback_data=d("pray"))]]
        if st["gold"] >= 40:
            rows.append([B(text="💎 Offer 40 💎 (+12% attack)", callback_data=d("offer"))])
    elif t == "trap":
        rows = [[B(text="🧠 Disarm (55%)", callback_data=d("disarm")), B(text="🏃 Rush through", callback_data=d("rush"))]]
    elif t == "merchant":
        rows = []
        if st["gold"] >= 45:
            rows.append([B(text="🧪 Tonic · 45 💎 (+35% HP)", callback_data=d("potion"))])
        if st["gold"] >= 70:
            rows.append([B(text="🗡 Whetstone · 70 💎 (+10% atk)", callback_data=d("stone"))])
        rows.append([B(text="🚶 Leave", callback_data=d("leave"))])
    else:  # camp
        rows = [[B(text="🔥 Rest (+28% HP)", callback_data=d("rest")), B(text="🪨 Sharpen (+8% atk)", callback_data=d("sharpen"))]]
    return head + note + body, kb(rows)


def render_end(res: dict, st: dict) -> str:
    how = res["how"]
    last = f"{res['last_note']}\n\n" if res.get("last_note") else ""
    title = {"clear": "🏆 <b>DUNGEON CLEARED!</b>", "retreat": "💰 <b>You cashed out safely.</b>",
             "death": f"💀 <b>You fell on floor {res['floor']}…</b>", "abandon": "⌛ <b>Your old run was forfeited.</b>"}[how]
    lines = [title, "", last.rstrip()] if last else [title, ""]
    if how == "death":
        lines.append("You crawl out with <b>half</b> of your haul. Loot was lost.")
    if res["payout"]:
        extra = f" (incl. +{num(res['bonus'])} clear bonus)" if res["bonus"] else ""
        lines.append(f"💎 <b>+{num(res['payout'])}</b> diamonds{extra}")
    else:
        lines.append("💎 No diamonds this time.")
    if res["xp"]:
        lines.append(f"✨ +{res['xp']} XP")
    for key in res["loot"]:
        it = ITEMS.get(key)
        lines.append(f"🎒 Found: {it.emoji + ' ' + it.name if it else key}")
    if res["pet_levels"]:
        lines.append("🐾 Your pet gained a level from the adventure!")
    lines.append(_unlocked_lines(res["unlocked"]))
    lines.append(f"\n<i>Next run in {dur(realm.DUNGEON_CD_S)}. Use /dungeon.</i>")
    return "\n".join(x for x in lines if x is not None).replace("\n\n\n", "\n\n")


async def screen_dungeon(uid: int, back_row: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    st = await realm.dungeon_get(uid)
    if st:
        return render_run(st)
    async with db.tx() as s:
        u = await s.get(User, uid)
        from services import cooldowns
        left = await cooldowns.remaining(s, uid, "dungeon")
    return dungeon_intro(uid, left, u.level, back_row)


@router.message(Command("dungeon", "dg", "delve"))
async def dungeon_cmd(m: Message, db_user: User):
    text, markup = await screen_dungeon(db_user.id)
    await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("dgs:"))
async def dungeon_start_cb(cb: CallbackQuery):
    _, uid, mode = cb.data.split(":")
    uid = _owner(cb, uid)
    st = await realm.dungeon_enter(uid, mode == "h")
    text, markup = render_run(st)
    await safe_edit(cb, text, markup)
    await safe_answer(cb, "The gate closes behind you…")


@router.callback_query(F.data.startswith("dg:"))
async def dungeon_act_cb(cb: CallbackQuery):
    _, uid, action = cb.data.split(":")
    uid = _owner(cb, uid)
    st, res = await realm.dungeon_act(uid, action)
    if res:
        await safe_edit(cb, render_end(res, st), kb([[B(text="🗝 Dungeon info", callback_data=f"rl:dg:{uid}")]]))
    else:
        text, markup = render_run(st)
        await safe_edit(cb, text, markup)
    await safe_answer(cb)


# ================================================================== EXPEDITIONS
async def screen_expedition(uid: int, back_row: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    info = await realm.expedition_status(uid)
    pet = info["pet"]
    rows: list[list[B]] = []
    if pet is None:
        text = (f"{brand.line()}\n\n🧭 <b>Pet Expeditions</b>\n\nYou need a pet first!\n"
                "• /pet adopt — adopt a common pet\n• /hunt — find a rare egg")
    elif info["active"]:
        emoji, name, total, _, _, _ = realm.EXPEDITIONS[info["kind"]]
        e = PET_SPECIES[pet.species][0]
        if info["left"] > 0:
            text = (f"{brand.line()}\n\n🧭 <b>Expedition in progress</b>\n\n{e} <b>{esc(pet.name)}</b> is out on a {emoji} <b>{name}</b>.\n"
                    f"{bar(total - info['left'], total, 12)}\n⏳ Back in <b>{dur(info['left'])}</b>\n\n"
                    "<i>I'll message you the moment your pet is home.</i>")
            rows.append([B(text="🔄 Refresh", callback_data=f"ex:{uid}:r")])
        else:
            text = f"{brand.line()}\n\n🧭 <b>{e} {esc(pet.name)} is home!</b>\nIt's carrying something from the {emoji} {name}."
            rows.append([B(text="🎁 Collect rewards", callback_data=f"ex:{uid}:c")])
    else:
        mult = realm.exp_multiplier(pet)
        e = PET_SPECIES[pet.species][0]
        lines = [f"{brand.line()}\n\n🧭 <b>Pet Expeditions</b>\n\nSend {e} <b>{esc(pet.name)}</b> (Lv {pet.level}) exploring — "
                 f"it keeps working while you're away. Stronger pets bring home more (<b>×{mult:.2f}</b>).\n"]
        for key, (emoji, name, secs, (lo, hi), pxp, _) in realm.EXPEDITIONS.items():
            lines.append(f"{emoji} <b>{name}</b> · {dur(secs)} · 💎 {num(int(lo * mult))}–{num(int(hi * mult))} · 🐾 +{pxp} XP")
            rows.append([B(text=f"{emoji} {name} ({dur(secs)})", callback_data=f"ex:{uid}:go:{key}")])
        text = "\n".join(lines)
    if back_row:
        rows.append(back("realm"))
    return text, kb(rows)


@router.message(Command("expedition", "exp", "expeditions"))
async def expedition_cmd(m: Message, db_user: User):
    text, markup = await screen_expedition(db_user.id)
    await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ex:"))
async def expedition_cb(cb: CallbackQuery):
    parts = cb.data.split(":")
    uid = _owner(cb, parts[1])
    act = parts[2]
    if act == "go":
        await realm.expedition_start(uid, parts[3])
        text, markup = await screen_expedition(uid)
        await safe_edit(cb, text, markup)
        await safe_answer(cb, "Your pet sets off! 🧭")
    elif act == "c":
        r = await realm.expedition_collect(uid)
        emoji, name = realm.EXPEDITIONS[r["kind"]][0], realm.EXPEDITIONS[r["kind"]][1]
        lines = [f"{brand.line()}\n\n🧭 <b>{emoji} {name} complete!</b>\n", f"<i>{esc(r['tale'])}</i>\n",
                 f"💎 <b>+{num(r['amount'])}</b> diamonds  (×{r['mult']:.2f} pet bonus)"]
        if r["item"]:
            it = ITEMS[r["item"]]
            lines.append(f"🎒 Bonus find: {it.emoji} {it.name}")
        if r["levels"]:
            lines.append(f"🎉 <b>{esc(r['pet'].name)} leveled up</b> to {r['pet'].level}!")
        lines.append(_unlocked_lines(r["unlocked"]))
        await safe_edit(cb, "\n".join(lines), kb([[B(text="🧭 Send again", callback_data=f"ex:{uid}:r")]]))
        await safe_answer(cb, f"+{num(r['amount'])} 💎")
    else:
        text, markup = await screen_expedition(uid)
        await safe_edit(cb, text, markup)
        await safe_answer(cb)


async def expedition_loop(bot: Bot) -> None:
    """DM players when their pet comes home (once per expedition)."""
    from utils.telegram import safe_send
    while True:
        await asyncio.sleep(60)
        try:
            for uid, kind, pet_name in await realm.expeditions_due():
                emoji, name = realm.EXPEDITIONS[kind][0], realm.EXPEDITIONS[kind][1]
                await safe_send(bot, uid, f"🧭 <b>{esc(pet_name)}</b> is back from the {emoji} {name}!",
                                kb([[B(text="🎁 Collect rewards", callback_data=f"ex:{uid}:c")]]))
        except Exception:
            log.exception("expedition notify loop")


# ================================================================== HEIST (group co-op)
LOBBIES: dict[str, dict] = {}
_TASKS: set[asyncio.Task] = set()


def _heist_text(lob: dict, final: str | None = None) -> str:
    crew = lob["members"]
    lines = [f"{brand.line()}\n\n🎭 <b>HEIST</b> — target: <b>{lob['target']}</b>\n",
             "A job needs every role: 💪 Muscle · 💻 Hacker · 🔭 Lookout · 🚗 Driver.\n"
             f"Crew ({len(crew)}/{realm.HEIST_MAX}):"]
    for uid, (name, role) in crew.items():
        lines.append(f"{realm.HEIST_ROLES[role][0]} {mention(uid, name)} — {realm.HEIST_ROLES[role][1]}")
    have = {r for _, r in crew.values()}
    missing = [realm.HEIST_ROLES[r][1] for r in realm.HEIST_ROLES if r not in have]
    if missing and final is None:
        lines.append(f"\n⚠️ <i>No {' / '.join(missing)} yet — those stages will be much harder!</i>")
    if final:
        lines.append(final)
    else:
        lines.append(f"\nPick your role below. Starts automatically in {realm.HEIST_LOBBY_S}s, "
                     f"or {mention(lob['leader'], lob['leader_name'])} can start now (min {realm.HEIST_MIN}).")
    return "\n".join(lines)


def _heist_kb(token: str) -> InlineKeyboardMarkup:
    roles = [B(text=f"{e} {n}", callback_data=f"hs:{token}:{k}") for k, (e, n) in realm.HEIST_ROLES.items()]
    return kb([roles[:2], roles[2:], [B(text="▶️ Start now", callback_data=f"hs:{token}:go"),
                                        B(text="✖️ Cancel", callback_data=f"hs:{token}:x")]])


async def _edit(bot: Bot, lob: dict, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    for _ in range(2):
        try:
            await bot.edit_message_text(text, chat_id=lob["chat"], message_id=lob["mid"], reply_markup=markup,
                                        disable_web_page_preview=True)
            return
        except TelegramRetryAfter as e:
            await asyncio.sleep(min(e.retry_after, 5))
        except TelegramBadRequest:
            return


@router.message(Command("heist", "job"))
async def heist_cmd(m: Message, bot: Bot, db_user: User):
    if m.chat.type == "private":
        raise GameError("🎭 Heists are group jobs! Add me to a group, then gather a crew with /heist.")
    await realm.heist_check(db_user.id)
    if not await kv.set_nx(f"heist:{m.chat.id}", realm.HEIST_GROUP_CD_S):
        raise GameError("🎭 This group just pulled a job — lay low for a while before the next one.")
    token = secrets.token_hex(4)
    lob = {"chat": m.chat.id, "mid": 0, "leader": db_user.id, "leader_name": db_user.first_name, "state": "open",
           "target": random.choice(realm.HEIST_TARGETS), "lock": asyncio.Lock(),
           "members": {db_user.id: (db_user.first_name, "muscle")}}
    LOBBIES[token] = lob
    sent = await m.answer(_heist_text(lob), reply_markup=_heist_kb(token))
    lob["mid"] = sent.message_id
    t = asyncio.create_task(_heist_auto(bot, token))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)


async def _heist_auto(bot: Bot, token: str) -> None:
    await asyncio.sleep(realm.HEIST_LOBBY_S)
    lob = LOBBIES.get(token)
    if lob and lob["state"] == "open":
        try:
            await _heist_run(bot, token)
        except Exception:
            log.exception("heist run failed")
            LOBBIES.pop(token, None)


@router.callback_query(F.data.startswith("hs:"))
async def heist_cb(cb: CallbackQuery, bot: Bot, db_user: User):
    _, token, act = cb.data.split(":")
    lob = LOBBIES.get(token)
    if not lob or lob["state"] != "open":
        raise GameError("🎭 This job is no longer open.")
    uid = cb.from_user.id
    if act in realm.HEIST_ROLES:
        async with lob["lock"]:
            if uid not in lob["members"]:
                if len(lob["members"]) >= realm.HEIST_MAX:
                    raise GameError("🎭 The crew is full.")
                await realm.heist_check(uid)
                if len(lob["members"]) >= realm.HEIST_MAX or lob["state"] != "open":
                    raise GameError("🎭 The crew is full.")
            lob["members"][uid] = (db_user.first_name, act)
        await _edit(bot, lob, _heist_text(lob), _heist_kb(token))
        await safe_answer(cb, f"You're the {realm.HEIST_ROLES[act][1]}!")
        return
    if uid != lob["leader"]:
        raise GameError("🎭 Only the crew leader can do that.")
    if act == "x":
        lob["state"] = "cancelled"
        LOBBIES.pop(token, None)
        await kv.take(f"heist:{lob['chat']}")
        await _edit(bot, lob, f"{brand.line()}\n\n🎭 The heist was called off.")
        await safe_answer(cb)
        return
    if len(lob["members"]) < realm.HEIST_MIN:
        raise GameError(f"🎭 You need at least {realm.HEIST_MIN} crew members.")
    await safe_answer(cb, "Let's go!")
    await _heist_run(bot, token)


async def _heist_run(bot: Bot, token: str) -> None:
    lob = LOBBIES.get(token)
    if not lob:
        return
    async with lob["lock"]:
        if lob["state"] != "open":
            return
        lob["state"] = "running"
    ready: dict[int, tuple[str, str]] = {}
    for uid, (name, role) in lob["members"].items():          # personal cooldown starts now
        if await realm.heist_lock_in(uid) == 0:
            ready[uid] = (name, role)
    if len(ready) < realm.HEIST_MIN:
        for uid in ready:
            await realm.heist_release(uid)
        LOBBIES.pop(token, None)
        await kv.take(f"heist:{lob['chat']}")
        await _edit(bot, lob, f"{brand.line()}\n\n🎭 The job fell apart — not enough ready crew members.")
        return
    async with db.tx() as s:
        lv = dict((await s.execute(select(User.id, User.level).where(User.id.in_(list(ready))))).all())
    crew = {uid: (role, lv.get(uid, 1)) for uid, (_, role) in ready.items()}
    lob["members"] = ready
    stages = realm.heist_simulate(crew)
    header = _heist_text(lob, "\n🔥 <b>THE JOB BEGINS…</b>")
    shown = [f"\n\n<b>Operation log</b>"]
    await _edit(bot, lob, header + "\n".join(shown))
    for emoji, text, ok, chance in stages:
        await asyncio.sleep(2.0)
        shown.append(f"{emoji} {text} — {'✅ success' if ok else '❌ failed'} <i>({round(chance * 100)}%)</i>")
        await _edit(bot, lob, header + "\n".join(shown))
    passed = sum(1 for _, _, ok, _ in stages if ok)
    results: list[str] = []
    unlocked: list[str] = []
    for uid, (name, _) in ready.items():
        pay = realm.heist_payout(passed) if passed >= 2 else 0
        try:
            new = await realm.heist_pay(uid, token, passed, pay)
        except GameError:
            pay, new = 0, []
        results.append(f"• {mention(uid, name)} — {'💎 <b>+' + num(pay) + '</b>' if pay else 'nothing'}")
        unlocked += [f"{esc(name)}: {n}" for n in new]
    if passed == len(stages):
        verdict = "🏆 <b>PERFECT HEIST!</b> Not a single alarm tripped."
    elif passed >= 2:
        verdict = f"💰 <b>Job done</b> — {passed}/{len(stages)} stages cleared."
    else:
        verdict = f"🚨 <b>The heist failed.</b> {passed}/{len(stages)} stages cleared — the crew scatters with empty pockets."
    final = "\n\n" + verdict + "\n" + "\n".join(results) + "".join(f"\n🏅 {u}" for u in unlocked)
    await _edit(bot, lob, header.replace("\n🔥 <b>THE JOB BEGINS…</b>", "") + "\n".join(shown) + final)
    LOBBIES.pop(token, None)


# ================================================================== TIMERS
async def screen_timers(uid: int, back_row: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    b = await realm.cooldown_board(uid)
    ready = [lbl for lbl, t in b["items"] if t == 0]
    wait = [(lbl, t) for lbl, t in b["items"] if t > 0]
    lines = [f"{brand.line()}\n\n⏱ <b>Your timers</b>\n"]
    if ready:
        lines.append("✅ <b>Ready now</b>\n" + " · ".join(ready))
    if wait:
        lines.append("\n⏳ <b>Cooling down</b>")
        lines += [f"{lbl} — <b>{dur(t)}</b>" for lbl, t in sorted(wait, key=lambda x: x[1])]
    if b["expedition"]:
        kind, left = b["expedition"]
        e = realm.EXPEDITIONS[kind]
        lines.append(f"\n🧭 Expedition ({e[1]}): " + ("✅ <b>home — collect it!</b>" if left <= 0 else f"back in <b>{dur(left)}</b>"))
    if b["dungeon"]:
        lines.append("\n🗝 You have a dungeon run in progress — /dungeon")
    rows = [[B(text="🔄 Refresh", callback_data=f"cd:{uid}")]]
    if back_row:
        rows.append(back("realm"))
    return "\n".join(lines), kb(rows)


@router.message(Command("cd", "timers", "cooldowns", "cooldown"))
async def timers_cmd(m: Message, db_user: User):
    text, markup = await screen_timers(db_user.id)
    await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("cd:"))
async def timers_cb(cb: CallbackQuery):
    uid = _owner(cb, cb.data.split(":")[1])
    text, markup = await screen_timers(uid)
    await safe_edit(cb, text, markup)
    await safe_answer(cb, "Updated")


# ================================================================== ACHIEVEMENTS
async def screen_achievements(uid: int, back_row: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    new, have, counters = await realm.sweep_and_progress(uid)
    need_for = {ach: (stat, n) for stat, lst in realm.STAT_ACH.items() for n, ach in lst}
    lines = [f"{brand.line()}\n\n🏅 <b>Achievements</b>  <b>{len(have & set(ACHIEVEMENTS))}/{len(ACHIEVEMENTS)}</b>"]
    if new:
        lines.append(_unlocked_lines(new))
    for title, keys in realm.GROUPS:
        lines.append(f"\n<b>{esc(title)}</b>")
        for k in keys:
            name, desc = ACHIEVEMENTS[k]
            prog = ""
            if k not in have and k in need_for:
                stat, goal = need_for[k]
                if stat in counters and goal > 1:
                    prog = f" <b>({min(counters[stat], goal)}/{goal})</b>"
            lines.append(f"{'✅' if k in have else '🔒'} {name} — <i>{desc}</i>{prog}")
    rows = [back("realm")] if back_row else []
    return "\n".join(lines), kb(rows)


@router.message(Command("achievements", "badges", "ach"))
async def achievements_cmd(m: Message, db_user: User):
    text, markup = await screen_achievements(db_user.id)
    await m.reply(text, reply_markup=markup if m.chat.type == "private" else None)


# ================================================================== HUB buttons (main menu -> Realm)
HEIST_HELP = (f"{brand.line()}\n\n🎭 <b>Group Heists</b>\n\nIn a group, send /heist to open a job. Friends pick a role:\n"
              "💪 Muscle · 💻 Hacker · 🔭 Lookout · 🚗 Driver\n\n"
              "4 stages, one per role. A stage with <b>its role on the crew</b> is far likelier to succeed, so mix your "
              "crew! Pass at least 2 stages to get paid; a perfect run earns a bonus.\n\n"
              f"<i>Crew of {realm.HEIST_MIN}–{realm.HEIST_MAX} · personal cooldown {dur(realm.HEIST_CD_S)} · "
              "account must be 24h+ old.</i>")


@router.callback_query(F.data.startswith("rl:"))
async def realm_hub_cb(cb: CallbackQuery, db_user: User):
    parts = cb.data.split(":")
    which, uid = parts[1], db_user.id
    if which == "dg":
        text, markup = await screen_dungeon(uid, back_row=True)
    elif which == "ex":
        text, markup = await screen_expedition(uid, back_row=True)
    elif which == "cd":
        text, markup = await screen_timers(uid, back_row=True)
    elif which == "ach":
        text, markup = await screen_achievements(uid, back_row=True)
    elif which == "hs":
        text, markup = HEIST_HELP, kb([back("realm")])
    else:
        raise GameError("❌ Unknown page.")
    await safe_edit(cb, text, markup)
    await safe_answer(cb)
