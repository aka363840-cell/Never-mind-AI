"""Close actions (/kiss /hug /cuddle /handhold /love /sex) need the other person to ACCEPT or REJECT first.
Only the person asked can press the buttons. Every outcome has several random lines. Nothing here touches wallets.
/sex stays 'fade to black' and family-friendly: a cosy night, and sometimes a cute baby arrives (sometimes not)."""
from __future__ import annotations

import json
import logging
import random
import secrets

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from services.errors import GameError
from services.kv import kv
from utils import gifs
from utils.fmt import esc, mention
from utils.targets import resolve_target

log = logging.getLogger(__name__)
router = Router()
TTL = 300

# action: (emoji, gif key or None, ask lines, accept lines, reject lines)   {a}=asker {b}=person asked
ACTS: dict[str, tuple[str, str | None, list[str], list[str], list[str]]] = {
    "kiss": ("😘", "kiss",
             ["{a} leans in to kiss {b}… will {b} say yes?", "{a} wants to give {b} a kiss 💋 What do you say?", "{a} puckers up for {b}… 😳"],
             ["{b} says yes! {a} gives {b} a sweet kiss 💋", "💞 {a} and {b} share a lovely kiss. Aww!", "{b} blushes and accepts - a kiss for the story books 😘"],
             ["{b} turns away. No kiss for {a} today 💔", "🚫 {b} politely declines {a}'s kiss.", "{b} gently pushes {a} away: \"not now\" 😅"]),
    "hug": ("🤗", "hug",
            ["{a} opens their arms for {b} 🤗 Accept the hug?", "{a} wants a big hug from {b}!", "{a} is offering {b} a warm hug…"],
            ["{b} runs into {a}'s arms - the warmest hug 🤗", "🫂 {a} and {b} hug it out. So wholesome!", "{b} says yes! {a} hugs {b} tightly 💖"],
            ["{b} steps back. {a} hugs the air instead 🌬", "🚫 {b} isn't in a hugging mood right now.", "{b} declines {a}'s hug. Maybe later! 🙃"]),
    "cuddle": ("🥰", "cuddle",
               ["{a} wants to cuddle with {b} 🥰 Cosy up?", "{a} is holding out a blanket for {b}… cuddle time?", "{a} asks {b} for a cosy cuddle 🧸"],
               ["{b} snuggles up to {a} - cuddle accepted 🥰", "🧸 {a} and {b} cuddle under a blanket. Pure comfort.", "{b} says yes! {a} and {b} get all cosy 💞"],
               ["{b} says no to cuddles today 🙅", "🚫 {b} declines. {a} cuddles a pillow instead 🛏", "{b} isn't up for cuddling right now 😶"]),
    "handhold": ("🤝", "handhold",
                 ["{a} reaches for {b}'s hand… hold it?", "{a} would like to hold {b}'s hand 🤝", "{a} slowly extends a hand to {b} 🌸"],
                 ["{b} takes {a}'s hand. Sweet 🤝", "💞 {a} and {b} walk hand in hand.", "{b} smiles and holds {a}'s hand 🌸"],
                 ["{b} keeps their hands in their pockets 🙈", "🚫 {b} declines to hold hands.", "{b} awkwardly pulls their hand away 😅"]),
    "love": ("❤️", "hug",
             ["{a} wants to send {b} all their love ❤️ Accept it?", "{a} is feeling affectionate towards {b} 💕", "{a} has a heart to give {b} 💝"],
             ["{b} accepts {a}'s love with a happy smile ❤️", "💖 {a} and {b} share a heartfelt moment.", "{b} says yes! Love is in the air 💕"],
             ["{b} isn't ready for this feeling right now 💔", "🚫 {b} gently turns down {a}'s love.", "{b} says \"let's stay friends\" 🤍"]),
    "sex": ("💞", None,
            ["{a} invites {b} for a cosy night in 🌙 Do you accept?", "{a} gives {b} a meaningful look… 🕯 Accept?", "{a} whispers a sweet invitation to {b} 🌹"],
            ["🌙 {a} and {b} spend a lovely, cosy night together…", "🕯 The lights go down. {a} and {b} share a romantic night 💞", "💞 {b} says yes. A night to remember for {a} and {b} 🌹"],
            ["{b} says not tonight 🙅 {a} gets a blanket and a cold drink.", "🚫 {b} declines {a}'s invitation.", "{b} is not in the mood. {a} respects it 🙂"]),
}
ALIASES = {"smooch": "kiss", "hold": "handhold"}

BABY_CHANCE = 0.4
BABY_LINES = ["👶 <b>A cute baby arrives!</b> It's a <b>{g}</b> {e} - tiny fingers, big eyes, instant love.",
              "👶 Surprise! {a} and {b} are welcoming a <b>{g}</b> {e}. Congratulations!",
              "🍼 A little <b>{g}</b> {e} is on the way for {a} and {b}. So adorable!"]
NO_BABY = ["🌙 Sweet night, but no baby this time. Maybe next time! 💭", "💤 No little one this time - just happy memories.", "🕊 No baby this time. Rest up and try again later 💞"]


_baby_file_id: str | None = None


async def _baby_photo(m: Message, caption: str) -> bool:
    """Cute baby picture (assets/baby.jpg) with the caption; Telegram's file id is remembered so it uploads only once."""
    global _baby_file_id
    try:
        from pathlib import Path
        from aiogram.types import FSInputFile
        src = _baby_file_id or FSInputFile(Path(__file__).resolve().parent.parent / "assets" / "baby.jpg")
        sent = await m.answer_photo(src, caption=caption[:1000])
        if sent.photo:
            _baby_file_id = sent.photo[-1].file_id
        return True
    except Exception:
        log.exception("baby photo failed - sending text")
        return False


def _kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="✅ Accept", callback_data=f"cn:{token}:y"), B(text="❌ Reject", callback_data=f"cn:{token}:n")]])


async def _target_name(bot: Bot, m: Message, uid: int) -> str:
    rep = m.reply_to_message
    if rep and rep.from_user and rep.from_user.id == uid:
        return rep.from_user.full_name
    for e in m.entities or []:
        if e.type == "text_mention" and e.user and e.user.id == uid:
            return e.user.full_name
    try:
        c = await bot.get_chat(uid)
        return c.full_name or c.first_name or "them"
    except Exception:
        return "them"


def _make(act: str):
    cmds = [act] + [k for k, v in ALIASES.items() if v == act]

    @router.message(Command(*cmds))
    async def ask(m: Message, command: CommandObject, bot: Bot):
        if m.from_user is None:
            return
        try:
            target, _ = await resolve_target(m, (command.args or "").split())
        except GameError:
            target = None
        if target is None:
            raise GameError(f"↩️ Reply to someone (or use /{act} @user) - they get to accept or reject.")
        if target == m.from_user.id:
            raise GameError("🙃 You need another person for that.")
        if target == bot.id:
            raise GameError("😳 I'm just a bot… ask a real person!")
        a_name, b_name = m.from_user.full_name, await _target_name(bot, m, target)
        token = secrets.token_urlsafe(6)
        await kv.put(f"cn:{token}", json.dumps({"a": m.from_user.id, "b": target, "act": act, "an": a_name, "bn": b_name}), TTL)
        emoji, _, asks, _, _ = ACTS[act]
        await m.answer(f"{emoji} " + random.choice(asks).format(a=mention(m.from_user.id, a_name), b=mention(target, b_name)) +
                       f"\n<i>Only {esc(b_name)} can answer · {TTL // 60} min</i>", reply_markup=_kb(token))
    return ask


for _a in ACTS:
    _make(_a)


@router.callback_query(F.data.startswith("cn:"))
async def answer(cb: CallbackQuery, bot: Bot):
    try:
        _, token, ans = cb.data.split(":")
    except ValueError:
        return await cb.answer()
    raw = await kv.get(f"cn:{token}")
    if raw is None:
        await cb.answer("⌛ This request expired.", show_alert=True)
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    d = json.loads(raw)
    if cb.from_user.id != d["b"]:
        return await cb.answer("This isn't for you 🙂", show_alert=True)
    if not await kv.take(f"cn:{token}"):                      # one answer only (double tap safe)
        return await cb.answer()
    emoji, gif, _, yes, no = ACTS[d["act"]]
    a, b = mention(d["a"], d["an"]), mention(d["b"], d["bn"])
    await cb.answer()
    if ans != "y":
        text = f"{emoji} " + random.choice(no).format(a=a, b=b)
        try:
            await cb.message.edit_text(text)
        except Exception:
            await cb.message.answer(text)
        return
    text = f"{emoji} " + random.choice(yes).format(a=a, b=b)
    photo = False
    if d["act"] == "sex":                      # just for fun: not saved anywhere, no cost, nothing to do with /family
        if random.random() < BABY_CHANCE:
            gl, ge = random.choice([("baby girl", "👧"), ("baby boy", "👦")])
            text += "\n\n" + random.choice(BABY_LINES).format(a=a, b=b, g=gl, e=ge)
            photo = True
        else:
            text += "\n\n" + random.choice(NO_BABY)
    try:
        await cb.message.delete()
    except Exception:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    if photo and await _baby_photo(cb.message, text):
        return
    if gif and gif in gifs.GIF_ACTIONS:
        await gifs.send(cb.message, gif, text)
    else:
        await cb.message.answer(text)
