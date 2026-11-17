"""Whispers: type  @<bot> <username/userid> <text>  in ANY chat. The group sees only "a whisper for X";
only that person (and the sender) can open it. Needs Inline Mode switched on in BotFather (/setinline)."""
from __future__ import annotations

import hashlib
import json
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, InlineQuery, InlineQueryResultArticle,
                           InputTextMessageContent, Message)

from services.kv import kv
from utils import brand
from utils.fmt import esc

log = logging.getLogger(__name__)
router = Router()
MAX_LEN = 200                    # Telegram pop-up alerts hold 200 characters
TTL = 7 * 86400


def _usage() -> str:
    return (f"🔒 <b>Whisper</b>\nType in any chat:\n<code>@{esc(brand.username() or 'bot')} &lt;username/userid&gt; &lt;your whisper text&gt;</code>\n\n"
            "Only that person can read it, even in a group. 🤫")


def parse(q: str) -> tuple[str, str] | None:
    parts = q.strip().split(None, 1)
    if len(parts) < 2:
        return None
    who, text = parts[0], parts[1].strip()
    who = who.lstrip("@")
    if not who or not text or not (who.isdigit() or who.replace("_", "").isalnum()):
        return None
    return who, text[:MAX_LEN]


@router.inline_query()
async def whisper_inline(iq: InlineQuery):
    p = parse(iq.query or "")
    if p is None:
        r = InlineQueryResultArticle(id="help", title="🔒 Whisper - how to use",
                                     description="@bot <username/userid> <your whisper text>",
                                     input_message_content=InputTextMessageContent(message_text=_usage()))
        return await iq.answer([r], cache_time=0, is_personal=True)
    who, text = p
    token = hashlib.sha256(f"{iq.from_user.id}:{who.lower()}:{text}".encode()).hexdigest()[:16]
    await kv.put(f"wh:{token}", json.dumps({"s": iq.from_user.id, "sn": iq.from_user.full_name, "w": who.lower(), "t": text}), TTL)
    target = f'<a href="tg://user?id={who}">this person</a>' if who.isdigit() else f"@{esc(who)}"
    msg = (f"🔒 <b>A whisper for {target}</b>\n<i>from {esc(iq.from_user.full_name)} · only they can read it</i>")
    r = InlineQueryResultArticle(
        id=token, title=f"🔒 Whisper to {'@' + who if not who.isdigit() else who}", description=text[:60],
        input_message_content=InputTextMessageContent(message_text=msg),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text="🔓 Show whisper", callback_data=f"wh:{token}")]]))
    await iq.answer([r], cache_time=0, is_personal=True)


@router.callback_query(F.data.startswith("wh:"))
async def whisper_open(cb: CallbackQuery):
    raw = await kv.get(f"wh:{cb.data[3:]}")
    if raw is None:
        return await cb.answer("⌛ This whisper has expired.", show_alert=True)
    d = json.loads(raw)
    me = cb.from_user
    ok = me.id == d["s"] or (d["w"].isdigit() and me.id == int(d["w"])) or (not d["w"].isdigit() and (me.username or "").lower() == d["w"])
    if not ok:
        return await cb.answer("🔒 This whisper isn't for you.", show_alert=True)
    await cb.answer(f"🤫 {d['sn']}:\n\n{d['t']}"[:200], show_alert=True)


@router.message(Command("whisper"))
async def whisper_help(m: Message):
    await m.reply(_usage() + "\n\n<i>If nothing shows up, the owner must enable Inline Mode for the bot in BotFather (/setinline).</i>")
