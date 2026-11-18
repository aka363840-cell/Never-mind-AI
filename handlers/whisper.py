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
from utils.fmt import esc, mention

log = logging.getLogger(__name__)
router = Router()
MAX_LEN = 200                    # Telegram pop-up alerts hold 200 characters
TTL = 7 * 86400


def _usage() -> str:
    return (f"🔒 <b>Whisper</b>\nType in any chat:\n<code>@{esc(brand.username() or 'bot')} &lt;username/userid&gt; &lt;your whisper text&gt;</code>\n\n"
            "Only that person can read it, even in a group. 🤫\n\n💡 Next time just type your text after the bot name and pick someone from your recent list.")


async def _lookup(who: str) -> tuple[int | None, str | None]:
    """(user id, first name) when the bot knows this person, else (id if numeric, None)."""
    from sqlalchemy import select
    from database.models import User
    from database.session import db
    try:
        async with db.tx() as s:
            q = select(User.id, User.first_name).where(User.id == int(who)) if who.isdigit() else \
                select(User.id, User.first_name).where(User.username_lc == who.lower())
            row = (await s.execute(q)).first()
        if row:
            return row[0], (row[1] or None)
    except Exception:
        log.exception("whisper lookup failed")
    return (int(who) if who.isdigit() else None), None


def parse(q: str) -> tuple[str, str] | None:
    parts = q.strip().split(None, 1)
    if len(parts) < 2:
        return None
    who, text = parts[0], parts[1].strip()
    who = who.lstrip("@")
    if not who or not text or not (who.isdigit() or who.replace("_", "").isalnum()):
        return None
    return who, text[:MAX_LEN]


async def _remember(sender: int, target: int) -> None:
    """Keep the 8 most recent people this sender whispered to (stored in the DB, so it survives redeploys and backups)."""
    from sqlalchemy import delete, select, update
    from database.models import WhisperRecent, utcnow
    from database.session import db, insert_ignore
    try:
        if not await kv.set_nx(f"wr:{sender}:{target}", 30):
            return
        async with db.tx() as s:
            res = await s.execute(update(WhisperRecent).where(WhisperRecent.sender_id == sender, WhisperRecent.target_id == target).values(last_at=utcnow()))
            if res.rowcount == 0:
                await insert_ignore(s, WhisperRecent, dict(sender_id=sender, target_id=target, last_at=utcnow()))
            old = (await s.execute(select(WhisperRecent.target_id).where(WhisperRecent.sender_id == sender)
                                   .order_by(WhisperRecent.last_at.desc()).offset(8))).scalars().all()
            if old:
                await s.execute(delete(WhisperRecent).where(WhisperRecent.sender_id == sender, WhisperRecent.target_id.in_(old)))
    except Exception:
        log.exception("could not remember whisper target")


async def _recent(sender: int, n: int = 6) -> list[tuple[int, str]]:
    from sqlalchemy import select
    from database.models import User, WhisperRecent
    from database.session import db
    try:
        async with db.tx() as s:
            rows = (await s.execute(select(User.id, User.first_name).join(WhisperRecent, WhisperRecent.target_id == User.id)
                                    .where(WhisperRecent.sender_id == sender).order_by(WhisperRecent.last_at.desc()).limit(n))).all()
        return [(i, nm or "member") for i, nm in rows]
    except Exception:
        log.exception("whisper recents failed")
        return []


def _result(iq: InlineQuery, tid: int | None, who: str, name: str | None, text: str, token: str) -> InlineQueryResultArticle:
    if tid and name:                                     # priority: Name > @username > user id
        target, label = mention(tid, name), name
    elif who.isdigit():
        target, label = f'<a href="tg://user?id={who}">{who}</a>', who
    else:
        target, label = f"@{esc(who)}", "@" + who
    msg = f"🔒 <b>A whisper message to {target}</b>\nOnly they can read the message."
    return InlineQueryResultArticle(
        id=token, title=f"🔒 Whisper to {label}", description=text[:60],
        input_message_content=InputTextMessageContent(message_text=msg),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text="🔓 Show whisper", callback_data=f"wh:{token}")]]))


async def _store(iq: InlineQuery, who: str, tid: int | None, text: str) -> str:
    token = hashlib.sha256(f"{iq.from_user.id}:{who.lower()}:{text}".encode()).hexdigest()[:16]
    await kv.put(f"wh:{token}", json.dumps({"s": iq.from_user.id, "sn": iq.from_user.full_name, "w": str(tid) if tid else who.lower(), "t": text}), TTL)
    return token


@router.inline_query()
async def whisper_inline(iq: InlineQuery):
    q = (iq.query or "").strip()
    help_card = InlineQueryResultArticle(id="help", title="🔒 Whisper - how to use",
                                         description="@bot <name/username/userid> <text>  ·  or just type your text and pick a recent person",
                                         input_message_content=InputTextMessageContent(message_text=_usage()))
    if not q:
        return await iq.answer([help_card], cache_time=0, is_personal=True)
    p = parse(q)
    if p is not None:
        who, text = p
        tid, tname = await _lookup(who)
        explicit = who.isdigit() or q.startswith("@") or tid is not None        # "hello there" must not be read as a person called hello
        if explicit:
            if tid:
                await _remember(iq.from_user.id, tid)
            token = await _store(iq, who, tid, text)
            return await iq.answer([_result(iq, tid, who, tname, text, token)], cache_time=0, is_personal=True)
    text = q[:MAX_LEN]
    recents = await _recent(iq.from_user.id)
    if not recents or q.startswith("@"):
        return await iq.answer([help_card], cache_time=0, is_personal=True)
    out = []
    for tid, nm in recents:                              # no need to type the name again: pick from the people you whispered to lately
        out.append(_result(iq, tid, str(tid), nm, text, await _store(iq, str(tid), tid, text)))
    await iq.answer(out, cache_time=0, is_personal=True)


@router.callback_query(F.data.startswith("wh:"))
async def whisper_open(cb: CallbackQuery, bot: Bot):
    raw = await kv.get(f"wh:{cb.data[3:]}")
    if raw is None:
        return await cb.answer("⌛ This whisper has expired.", show_alert=True)
    d = json.loads(raw)
    me = cb.from_user
    ok = me.id == d["s"] or (d["w"].isdigit() and me.id == int(d["w"])) or (not d["w"].isdigit() and (me.username or "").lower() == d["w"])
    if not ok:
        return await cb.answer("🔒 This whisper isn't for you.", show_alert=True)
    await cb.answer(f"🤫 {d['sn']}:\n\n{d['t']}"[:200], show_alert=True)
    if me.id != d["s"] and cb.inline_message_id and await kv.set_nx(f"whr:{cb.data[3:]}", TTL):     # first time the TARGET opens it
        try:
            await bot.edit_message_text(f"👀 <b>{mention(me.id, me.full_name)} read the whisper!</b>", inline_message_id=cb.inline_message_id,
                                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text="🔓 Read again", callback_data=cb.data)]]))
        except Exception:
            log.info("could not mark the whisper as read")


@router.message(Command("whisper"))
async def whisper_help(m: Message):
    await m.reply(_usage() + "\n\n<i>If nothing shows up, the owner must enable Inline Mode for the bot in BotFather (/setinline).</i>")
