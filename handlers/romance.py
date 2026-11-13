"""Romance commands: /engage, /wedding, /anniversary (gifts, letters, honeymoon, quests and streaks join in later parts).
Thin handlers - the rules live in services/romance.py, the numbers in game/rules.py, the texts in utils/content/romance.py."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import secrets

from aiogram import F, Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message

from database.models import User
from game.rules import R
from keyboards.menus import kb
from services import couplelife, gifting, romance
from services.errors import GameError
from services.kv import kv
from utils import gifs
from utils.content import romance as texts
from utils.fmt import dur, esc, mention, num
from utils.targets import resolve_target
from utils.telegram import safe_answer, safe_edit, safe_send

log = logging.getLogger(__name__)
router = Router()


# ------------------------------------------------------------------ /engage
@router.message(Command("engage"))
async def engage_cmd(m: Message, command: CommandObject, db_user: User):
    args = (command.args or "").split()
    if args and args[0].lower() in ("break", "cancel", "end"):
        e = await romance.engagement_view(db_user.id)
        if not e:
            raise GameError("💍 You are not engaged.")
        pid, pname, _ = e
        await m.reply(f"💔 Break your engagement with {mention(pid, pname)}?\n"
                      f"It costs <b>{num(R.engage_break_penalty)}</b> 💎 (or everything you hold, if less) and the ring is <b>not</b> refunded.",
                      reply_markup=kb([[B(text="💔 Yes, break it", callback_data=f"eg:brk:{db_user.id}"),
                                        B(text="↩️ Keep it", callback_data=f"eg:cx:{db_user.id}")]]))
        return
    target, _ = await resolve_target(m, args)
    if target is None:
        e = await romance.engagement_view(db_user.id)
        if e:
            pid, pname, days = e
            await m.reply(f"💍 You are engaged to {mention(pid, pname)} — <b>{days}</b> day{'s' if days != 1 else ''} so far.\n"
                          f"Plan the big day with /wedding · change your mind with <code>/engage break</code>")
        else:
            await m.reply(f"💍 Reply to someone (or use @user) to propose: /engage\n"
                          f"The ring costs {num(R.engage_ring_cost)} 💎 (paid by you when they accept). Then plan a /wedding together.")
        return
    await romance.can_engage(db_user.id, target)
    nonce = secrets.token_urlsafe(6)
    await kv.put(f"eg:{nonce}", json.dumps({"a": db_user.id, "b": target, "an": db_user.first_name}), R.engage_request_ttl_s)
    await m.reply(f"💍 {mention(db_user.id, db_user.first_name)} {random.choice(texts.ENGAGE_PROPOSAL)} for "
                  f"<a href=\"tg://user?id={target}\">you</a>!\n"
                  f"The ring costs {num(R.engage_ring_cost)} 💎 (paid by the proposer). Both of you must agree.\n"
                  f"<i>Expires in {R.engage_request_ttl_s // 60} minutes.</i>",
                  reply_markup=kb([[B(text="✅ Accept", callback_data=f"eg:ok:{nonce}"),
                                    B(text="✖️ Decline", callback_data=f"eg:no:{nonce}")]]))


@router.callback_query(F.data.startswith("eg:"))
async def engage_cb(cb: CallbackQuery):
    _, verb, arg = cb.data.split(":", 2)
    if verb in ("brk", "cx"):                                  # arg = owner's user id
        if cb.from_user.id != int(arg):
            await safe_answer(cb, "This isn't yours.", alert=True)
            return
        if verb == "cx":
            await safe_edit(cb, "💞 Engagement kept. Love wins!")
            await safe_answer(cb)
            return
        other, paid = await romance.break_engagement(cb.from_user.id)
        await safe_edit(cb, f"💔 {mention(cb.from_user.id, cb.from_user.first_name)} broke the engagement with "
                            f"<a href=\"tg://user?id={other}\">their ex-fiancé(e)</a>."
                            + (f"\n<i>Penalty paid: {num(paid)} 💎</i>" if paid else ""))
        await safe_answer(cb)
        return
    raw = await kv.get(f"eg:{arg}")
    if not raw:
        await safe_edit(cb, "⌛ This proposal expired.")
        await safe_answer(cb)
        return
    d = json.loads(raw)
    if verb == "no":
        if cb.from_user.id not in (d["a"], d["b"]):
            await safe_answer(cb, "This isn't for you.", alert=True)
            return
        await kv.take(f"eg:{arg}")
        await safe_edit(cb, "💔 The proposal was declined / cancelled.")
        await safe_answer(cb)
        return
    if cb.from_user.id != d["b"]:
        await safe_answer(cb, "Only the person being proposed to can accept 💍", alert=True)
        return
    if not await kv.take(f"eg:{arg}"):                         # atomic: a double tap can't charge the ring twice
        await safe_answer(cb)
        return
    await romance.engage(d["a"], d["b"], ref=f"engage:{arg}")
    await safe_edit(cb, f"💍 <b>It's official!</b> {mention(d['b'], cb.from_user.first_name)} "
                        f"{random.choice(texts.ENGAGED_ACCEPT)}\n"
                        f"{mention(d['a'], d.get('an') or 'The proposer')} 💞 {mention(d['b'], cb.from_user.first_name)} are now <b>engaged</b>. "
                        f"Plan the big day with /wedding 💒")
    await safe_answer(cb)


# ------------------------------------------------------------------ /wedding
def _venue_kb(uid: int):
    rows = [[B(text=f"{e} {n} — {num(c)} 💎 total", callback_data=f"wd:v:{k}:{uid}")] for k, e, n, c, _b in R.wedding_venues]
    return kb(rows)


@router.message(Command("wedding"))
async def wedding_cmd(m: Message, db_user: User):
    pid, pname = await romance.wedding_options(db_user.id)
    lines = [f"💒 <b>Plan your wedding</b> with {mention(pid, pname)}\n",
             "The venue price is <b>split 50/50</b>. Pick a venue — your partner then has to confirm.\n"]
    for k, e, n, c, b in R.wedding_venues:
        lines.append(f"{e} <b>{esc(n)}</b> — {num(c)} 💎 ({num(c // 2)} each) · wedding gift +{num(b)} 💎 each")
    lines.append("\n<i>The gift is paid once per couple and is always smaller than the venue price.</i>")
    await m.reply("\n".join(lines), reply_markup=_venue_kb(db_user.id))


@router.callback_query(F.data.startswith("wd:"))
async def wedding_cb(cb: CallbackQuery):
    parts = cb.data.split(":")
    verb = parts[1]
    if verb == "v":                                            # wd:v:<venue>:<owner uid>
        venue, uid = parts[2], int(parts[3])
        if cb.from_user.id != uid:
            await safe_answer(cb, "Plan your own wedding with /wedding 💒", alert=True)
            return
        pid, pname = await romance.can_wed(uid, venue)
        v = romance.venue_of(venue)
        nonce = secrets.token_urlsafe(6)
        await kv.put(f"wd:{nonce}", json.dumps({"a": uid, "b": pid, "v": venue, "an": cb.from_user.first_name}), R.wedding_request_ttl_s)
        await safe_edit(cb, f"💒 {mention(uid, cb.from_user.first_name)} wants to marry "
                            f"<a href=\"tg://user?id={pid}\">{esc(pname)}</a> at the {v[1]} <b>{esc(v[2])}</b>!\n"
                            f"Each of you pays <b>{num(v[3] // 2)}</b> 💎. Do you accept?\n<i>Expires in {R.wedding_request_ttl_s // 60} minutes.</i>",
                  kb([[B(text="💍 I do!", callback_data=f"wd:ok:{nonce}"), B(text="✖️ Not now", callback_data=f"wd:no:{nonce}")]]))
        await safe_answer(cb)
        return
    nonce = parts[2]
    raw = await kv.get(f"wd:{nonce}")
    if not raw:
        await safe_edit(cb, "⌛ This wedding plan expired. Start again with /wedding.")
        await safe_answer(cb)
        return
    d = json.loads(raw)
    if verb == "no":
        if cb.from_user.id not in (d["a"], d["b"]):
            await safe_answer(cb, "This isn't for you.", alert=True)
            return
        await kv.take(f"wd:{nonce}")
        await safe_edit(cb, "💔 The wedding was called off for now.")
        await safe_answer(cb)
        return
    if cb.from_user.id != d["b"]:
        await safe_answer(cb, "Only the partner can confirm 💍", alert=True)
        return
    if not await kv.take(f"wd:{nonce}"):
        await safe_answer(cb)
        return
    info = await romance.hold_wedding(d["a"], d["b"], d["v"], ref=f"wedding:{nonce}")
    await safe_edit(cb, romance.wedding_card(info))
    await safe_answer(cb)
    if cb.message:
        await gifs.bonus(cb.message, "marry")


# ------------------------------------------------------------------ /anniversary
@router.message(Command("anniversary"))
async def anniversary_cmd(m: Message, db_user: User):
    got = await romance.claim_milestones(db_user.id)           # pays anything newly reached, once
    v = await romance.anniversary_view(db_user.id)
    lines = [f"🎂 <b>Anniversary</b> · {mention(db_user.id, db_user.first_name)} 💞 {mention(v['other'], v['name'])}\n",
             f"📅 Together since {v['start']:%d %b %Y} — <b>{v['days']}</b> day{'s' if v['days'] != 1 else ''}"
             + (f" ({v['years']} year{'s' if v['years'] != 1 else ''})" if v["years"] else "")]
    if v["venue"]:
        lines.append(f"💒 Wedding: {v['venue'][1]} {esc(v['venue'][2])}")
    lines.append("")
    for ms in v["milestones"]:
        mark = "✅" if ms["claimed"] else ("🎁" if ms["reached"] else "🔒")
        lines.append(f"{mark} <b>{esc(ms['label'])}</b> — +{num(ms['reward'])} 💎")
    lines.append(f"\n🎈 Next wedding anniversary in <b>{v['days_to_anniversary']}</b> day{'s' if v['days_to_anniversary'] != 1 else ''} "
                 f"(+{num(R.anniv_yearly_bonus)} 💎 each, I'll greet you).")
    if got:
        lines.append("\n🎉 <b>Milestone reward!</b> " + " · ".join(f"{esc(lbl)} +{num(r)} 💎" for lbl, r in got))
    await m.reply("\n".join(lines))


# ------------------------------------------------------------------ B: /sendgift, /loveletter
@router.message(Command("sendgift"))
async def sendgift_cmd(m: Message, command: CommandObject, db_user: User):
    target, _ = await resolve_target(m, (command.args or "").split())
    if target is None:
        target = await gifting.partner_id(db_user.id)
        if target is None:
            raise GameError("🎁 Reply to your partner / a relative (or use @user): /sendgift")
    kind, tname, left = await gifting.gift_options(db_user.id, target)
    rows, row = [], []
    for k, e, n, price, pts in R.gift_shop:
        row.append(B(text=f"{e} {n} · {num(price)} 💎 (+{pts})", callback_data=f"sg:{db_user.id}:{target}:{k}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    await m.reply(f"🎁 <b>Gift shop</b> — for {mention(target, tname)} ({'partner' if kind == 'partner' else 'family'})\n"
                  f"Gifts raise your {'love' if kind == 'partner' else 'bond'} points. "
                  f"You can still add <b>{left}</b> points today (limit {R.gift_daily_cap_points} per day).", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("sg:"))
async def sendgift_cb(cb: CallbackQuery):
    _, uid, target, key = cb.data.split(":", 3)
    if cb.from_user.id != int(uid):
        await safe_answer(cb, "Open your own shop with /sendgift 🎁", alert=True)
        return
    mid = cb.message.message_id if cb.message else 0
    cid = cb.message.chat.id if cb.message else 0
    if not await kv.set_nx(f"sg:{cid}:{mid}", 3600):                 # one gift per shop message; a double tap can't pay twice
        await safe_answer(cb)
        return
    r = await gifting.send_gift(cb.from_user.id, int(target), key, ref=f"sg:{cid}:{mid}")
    reaction = random.choice(texts.GIFT_REACTIONS.get(key, [""]))
    what = "love" if r["kind"] == "partner" else "bond"
    await safe_edit(cb, f"{r['emoji']} {mention(cb.from_user.id, cb.from_user.first_name)} gave "
                        f"{mention(int(target), r['target_name'])} <b>{esc(r['name'])}</b>!\n<i>{esc(reaction)}</i>\n\n"
                        f"💞 {what.capitalize()} +{r['points']} → <b>{num(r['total'])}</b>"
                        + (f" · {esc(r['title'])}" if r["title"] else "") + f"\n<i>−{num(r['price'])} 💎 · {r['left']} points left today</i>")
    await safe_answer(cb)


@router.message(Command("loveletter"))
async def loveletter_cmd(m: Message, command: CommandObject, db_user: User):
    target, _ = await resolve_target(m, [])                          # reply / mention only: the text itself is never parsed as a target
    if target is None:
        target = await gifting.partner_id(db_user.id)
        if target is None:
            raise GameError("💌 Reply to your partner / a relative, or marry someone first: <code>/loveletter your text</code>")
    r = await gifting.send_letter(db_user.id, target, command.args or "")
    await m.answer(gifting.letter_card(db_user.id, r["sender_name"], target, r["target_name"], r["text"], random.choice(texts.LETTER_OPENERS)))
    await m.answer(f"<i>−{num(r['cost'])} 💎" + (f" · +{r['points']} {'love' if r['kind'] == 'partner' else 'bond'} points" if r["points"] else " · daily point limit reached") + "</i>")


# ------------------------------------------------------------------ C: /honeymoon, /couplequest, /lovestreak
def _trip_text(v: dict) -> str:
    lines = [f"{v['emoji']} <b>{esc(v['place'])}</b> — honeymoon with {mention(v['other'], v['name'])}\n"]
    lines += [f"📖 {esc(c)}" for c in v["chapters"]]
    if v["finale"]:
        lines.append(f"\n🌅 {esc(v['finale'])}")
    return "\n".join(lines)


@router.message(Command("honeymoon"))
async def honeymoon_cmd(m: Message, db_user: User):
    v = await couplelife.honeymoon_status(db_user.id)
    if v["phase"] == "away":
        await m.reply(_trip_text(v) + f"\n\n⏳ Back in <b>{dur(v['left'])}</b>.")
    elif v["phase"] == "back":
        await m.reply(_trip_text(v) + "\n\n🎁 You're home! Collect your bonus:",
                      reply_markup=kb([[B(text="🎁 Collect bonus", callback_data=f"hn:c:{db_user.id}")]]))
    elif v["phase"] == "done":
        await m.reply("🏝 You two already had your honeymoon — what a trip! Keep the love going with /couplequest and /lovestreak.")
    else:
        pid, pname, half = await couplelife.honeymoon_offer(db_user.id)
        nonce = secrets.token_urlsafe(6)
        await kv.put(f"hn:{nonce}", json.dumps({"a": db_user.id, "b": pid, "an": db_user.first_name}), R.honeymoon_request_ttl_s)
        await m.reply(f"🏝 {mention(db_user.id, db_user.first_name)} wants to take <a href=\"tg://user?id={pid}\">{esc(pname)}</a> on a honeymoon!\n"
                      f"⏱ {R.honeymoon_duration_s // 3600} hours away · story chapters · bonus <b>{num(R.honeymoon_bonus)}</b> 💎 each when you're back.\n"
                      f"Each of you pays <b>{num(half)}</b> 💎. Do you accept?\n<i>Expires in {R.honeymoon_request_ttl_s // 60} minutes.</i>",
                      reply_markup=kb([[B(text="✈️ Let's go!", callback_data=f"hn:ok:{nonce}"), B(text="✖️ Not now", callback_data=f"hn:no:{nonce}")]]))


@router.callback_query(F.data.startswith("hn:"))
async def honeymoon_cb(cb: CallbackQuery):
    _, verb, arg = cb.data.split(":", 2)
    if verb == "c":                                                   # hn:c:<owner uid>
        if cb.from_user.id != int(arg):
            await safe_answer(cb, "This isn't yours.", alert=True)
            return
        r = await couplelife.collect_honeymoon(cb.from_user.id)
        await safe_edit(cb, f"{r['emoji']} <b>Welcome back from {esc(r['place'])}!</b>\n<i>{esc(r['finale'])}</i>\n\n"
                            f"🎁 You both received <b>+{num(r['bonus'])}</b> 💎 and <b>+{r['love']}</b> love.")
        await safe_answer(cb)
        return
    raw = await kv.get(f"hn:{arg}")
    if not raw:
        await safe_edit(cb, "⌛ This trip offer expired. Start again with /honeymoon.")
        await safe_answer(cb)
        return
    d = json.loads(raw)
    if verb == "no":
        if cb.from_user.id not in (d["a"], d["b"]):
            await safe_answer(cb, "This isn't for you.", alert=True)
            return
        await kv.take(f"hn:{arg}")
        await safe_edit(cb, "🏝 Trip postponed.")
        await safe_answer(cb)
        return
    if cb.from_user.id != d["b"]:
        await safe_answer(cb, "Only the partner can accept ✈️", alert=True)
        return
    if not await kv.take(f"hn:{arg}"):
        await safe_answer(cb)
        return
    r = await couplelife.start_honeymoon(d["a"], d["b"], ref=f"honeymoon:{arg}")
    emoji, place, chapters, _f = texts.HONEYMOON_DESTINATIONS[r["destination"]]
    await safe_edit(cb, f"{emoji} <b>Bon voyage!</b> {mention(d['a'], r['a_name'])} 💞 {mention(d['b'], r['b_name'])} are off to <b>{esc(place)}</b>.\n\n"
                        f"📖 {esc(chapters[0])}\n\n⏳ Back in {dur(R.honeymoon_duration_s)} — I'll message you. Peek at the story with /honeymoon.")
    await safe_answer(cb)


@router.message(Command("couplequest"))
async def couplequest_cmd(m: Message, db_user: User):
    q = await couplelife.couple_quest(db_user.id)
    lines = [f"💞 <b>Couple quest</b> · {mention(db_user.id, db_user.first_name)} 💞 {mention(q['other'], q['name'])}",
             f"<i>Two shared tasks a day — each finished task pays you both {num(R.couple_quest_reward)} 💎 + {R.couple_quest_love} love.</i>\n"]
    for t in q["tasks"]:
        mark = "✅" if t["paid"] else "⬜"
        who = "" if t["mode"] == "either" else f" ({'you ✓' if t['mine'] else 'you ✗'} · {'partner ✓' if t['theirs'] else 'partner ✗'})"
        lines.append(f"{mark} {t['emoji']} {esc(t['text'])}{who}")
    if q["paid_now"]:
        lines.append(f"\n🎉 <b>Reward paid to both of you!</b> +{num(R.couple_quest_reward * len(q['paid_now']))} 💎 each")
    lines.append("\n<i>Come back after you've done them — I check when you run /couplequest. New tasks every UTC day.</i>")
    await m.reply("\n".join(lines))


@router.message(Command("lovestreak"))
async def lovestreak_cmd(m: Message, db_user: User):
    r = await couplelife.love_checkin(db_user.id)
    lines = [f"🔥 <b>Love streak</b> · {mention(db_user.id, db_user.first_name)} 💞 {mention(r['other'], r['name'])}\n"]
    if r["already"]:
        lines.append("✅ You already checked in today." + ("" if r["both"] else " Waiting for your partner — ask them to /lovestreak!"))
    elif r["both"]:
        lines.append(f"💖 <b>Both of you checked in!</b> {esc(random.choice(texts.STREAK_LINES))}")
    else:
        lines.append("💌 Checked in! Your partner needs to /lovestreak today too, or the day doesn't count.")
    lines.append(f"\n🔥 Streak: <b>{r['streak']}</b> day{'s' if r['streak'] != 1 else ''} · best <b>{r['best']}</b>")
    if r["next"]:
        lines.append(f"🎯 Next reward at <b>{r['next'][0]}</b> days: +{num(r['next'][1])} 💎 each (once per couple)")
    for n, reward in r["rewards"]:
        lines.append(f"\n🎉 <b>{n}-day streak!</b> You both received +{num(reward)} 💎")
    await m.reply("\n".join(lines))


# ------------------------------------------------------------------ background loop
async def romance_loop(bot: Bot) -> None:
    """Every minute: tell couples their honeymoon is over. Every 15 minutes: greet couples on their wedding anniversary and pay the
    yearly bonus (once per couple and year)."""
    tick = 0
    while True:
        await asyncio.sleep(60)
        tick += 1
        try:
            for c in await couplelife.due_honeymoon_notices():
                emoji, place, *_ = texts.HONEYMOON_DESTINATIONS[c["destination"]]
                for uid in (c["a"], c["b"]):
                    await safe_send(bot, uid, f"{emoji} <b>Your honeymoon in {esc(place)} is over!</b> Time to collect your bonus.",
                                    kb([[B(text="🎁 Collect bonus", callback_data=f"hn:c:{uid}")]]))
        except Exception:
            log.exception("honeymoon loop")
        if tick % 15:
            continue
        try:
            for c in await romance.pay_yearly_bonuses():
                n = c["years"]
                greet = random.choice(texts.ANNIV_GREETINGS).format(years=n, plural="" if n == 1 else "s", ordinal=texts.ordinal(n))
                for uid, other, oname in ((c["a"], c["b"], c["b_name"]), (c["b"], c["a"], c["a_name"])):
                    await safe_send(bot, uid, f"🎂 <b>{esc(greet)}</b>\n💞 You and {mention(other, oname)} — "
                                              f"your bonus: <b>+{num(c['bonus'])}</b> 💎. See /anniversary")
        except Exception:
            log.exception("romance loop")
