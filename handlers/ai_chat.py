"""Free-form chat with the bot. Touches ONLY the ai package — never wallets, missions or combat."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import random
import time
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity
from aiogram.types import User as TgUser
from sqlalchemy import select
from aiogram.utils.chat_action import ChatActionSender

from ai.gemini import BLOCKED_REPLY
from ai.base import AIQuotaExceeded, AIRateLimited, AIUnavailable
from ai.service import ChatService
from ai import stt, tts
from services import voiceaccess as voice_access
from aiogram.types import BufferedInputFile
from config import get_settings
from database.models import GroupMember, GroupStat, User
from database.session import db
from handlers import admin as admin_h
from services import admin as adm
from services.errors import GameError
from services.kv import kv
from utils import brand, deck
from utils.identity import DEVELOPER_ID, classify, dev_reply, leaks_provider, maybe_identity
from utils.fmt import esc, num
from utils.targets import resolve_target
from utils.addressed import addressed
from utils.fmt import dur
from utils.telegram import safe_answer, split_text

log = logging.getLogger(__name__)
router = Router()

BURST_MAX = 5            # AI replies one person may get ...
BURST_WINDOW_S = 60      # ... per this many seconds; the next one starts the cooldown
COOLDOWN_S = 60          # 1 minute


def _cooldown_text(name: str, left: int) -> str:
    return (f"⏳ Easy there, <b>{esc(name)}</b>! Too much chatting too fast — I need a short break from talking to you.\n"
            f"Chat with me again in <b>{dur(max(left, 1))}</b>. Game commands still work meanwhile! 🦋")


async def _cooldown_blocked(m: Message) -> bool:
    """Per-person AI cooldown (tag / reply / name / /ask all count). Saves the free AI quota. Owner is exempt."""
    uid = m.from_user.id
    if uid == get_settings().owner_id:
        return False
    try:
        left = await kv.ttl(f"aicd:{uid}")
        if left > 0:                                              # already cooling down: remind at most every 25s, else stay quiet
            if await kv.set_nx(f"aicdmsg:{uid}", 25):
                await m.reply(_cooldown_text(m.from_user.first_name, left))
            return True
        if await kv.incr_window(f"aiuse:{uid}", BURST_WINDOW_S) > BURST_MAX:
            await kv.put(f"aicd:{uid}", "1", COOLDOWN_S)
            await kv.set_nx(f"aicdmsg:{uid}", 25)
            await m.reply(_cooldown_text(m.from_user.first_name, COOLDOWN_S))
            return True
    except Exception:
        log.exception("AI cooldown check failed; allowing the message")
    return False


async def _dev_tag(bot: Bot) -> str:
    """HTML mention of the developer (tappable). Name from the DB, else Telegram, else a plain label."""
    name = None
    try:
        from database.models import User
        async with db.tx() as s:
            u = await s.get(User, DEVELOPER_ID)
            name = u.first_name if u else None
    except Exception:
        pass
    if not name:
        try:
            name = (await bot.get_chat(DEVELOPER_ID)).full_name
        except Exception:
            name = "my developer"
    return f'<a href="tg://user?id={DEVELOPER_ID}">{esc(name)}</a>'


STICKER_REPLY_TTL_S = 120          # (unused now: replies to stickers are kept, not deleted)
_bg: set[asyncio.Task] = set()     # keep references so the delete tasks are not garbage-collected


STICKER_PACK = os.getenv("ELLA_STICKER_PACK", "stik_79969_by_TgEmojis_bot").strip()
_stk: tuple[float, list[str]] = (0.0, [])


async def _pack_ids(bot: Bot) -> list[str]:
    """file_ids of Ella's own sticker pack, cached for an hour. Empty list when the pack can't be read."""
    global _stk
    if time.monotonic() - _stk[0] < 3600 and (_stk[1] or time.monotonic() - _stk[0] < 120):
        return _stk[1]
    try:
        ids = [x.file_id for x in (await bot.get_sticker_set(STICKER_PACK)).stickers]
    except Exception:
        log.warning("could not read sticker pack %s", STICKER_PACK, exc_info=True)
        ids = []
    _stk = (time.monotonic(), ids)
    return ids


async def _send_sticker(m: Message, bot: Bot) -> bool:
    try:
        ids = await _pack_ids(bot)
        if not ids:
            return False
        await m.reply_sticker(deck.pick("ellastk", ids, deck.chat_of(m)))
        return True
    except Exception:
        log.warning("sticker send failed", exc_info=True)
        return False


def _clip(v: str, n: int) -> str:
    return " ".join((v or "").split())[:n]


def _who(u) -> str:
    if not u:
        return "someone"
    nm = _clip(u.full_name or u.first_name or "", 40) or "someone"
    return f"{nm} (@{u.username})" if getattr(u, "username", None) else nm


def _msg_text(r: Message) -> str:
    t = r.text or r.caption or ""
    if t:
        return _clip(t, 500)
    if r.sticker:
        return f"[a sticker {r.sticker.emoji or ''}]".replace(" ]", "]")
    kind = r.content_type.value if hasattr(r.content_type, "value") else str(r.content_type)
    return f"[a {kind} with no text]"


def _now_line() -> str:
    """The REAL current date and time, so she never guesses (the model has no clock). Zone: ELLA_TZ, default India (IST)."""
    from datetime import datetime, timedelta, timezone
    name = os.getenv("ELLA_TZ", "Asia/Kolkata").strip()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(name)
    except Exception:
        tz, name = timezone(timedelta(hours=5, minutes=30)), "IST"
    n = datetime.now(tz)
    return (f"[The real current date and time is {n.strftime('%A, %d %B %Y, %I:%M %p')} ({name}). "
            f"Use exactly this for any question about today, the date, the day, the time, the month or the year; never guess it. "
            f"For other time zones or 'days until', calculate from this.]")


def _context(m: Message, bot_id: int, voice: bool = False) -> str:
    """Tells the AI WHO is talking to her and WHAT message they tagged her on (the replied-to message), so she can read it
    and knows the people. Only for the model; it is not saved in the chat history. Names/text are untrusted: single line, clipped."""
    lines = [f"[Info for you, do not repeat it: the person talking to you is {_who(m.from_user)}.]", _now_line()]
    if voice and m.from_user:                                   # her answer will be SPOKEN: write it in the voice's own language
        vid = tts.voice_id(voice_access.get_name(m.from_user.id))
        if vid.startswith(("en-US", "en-GB")):
            lines.append("[Your answer will be spoken aloud by an English voice: reply in plain English only, no Hindi/Hinglish words.]")
        elif vid.startswith("hi-"):
            lines.append("[Your answer will be spoken aloud by a Hindi voice: reply in Hindi written in Devanagari script only, no English letters.]")
    if m.from_user and m.from_user.id == get_settings().owner_id:
        lines.append(OWNER_LINE.format(hint=deck.pick('ownerhint', _OWNER_HINTS, deck.chat_of(m))))
    r = m.reply_to_message
    if r is not None and not (r.from_user and r.from_user.id == bot_id and not (r.text or r.caption)):
        who = "your own earlier message" if (r.from_user and r.from_user.id == bot_id) else f"a message from {_who(r.from_user)}"
        lines.append(f"[They are replying to {who}. That message says: \"{_msg_text(r)}\". Use it when answering.]")
    return " ".join(lines) + "\n"


_TAG_WORDS = re.compile(r"\b(bol|bolo|bolna|bata|bta|batao|bataao|keh|kah|kaho|kehna|pooch|puch|poochh|tell|ask|say|tag|bula|bulao|call|msg|message|inform|remind|yaad)\b", re.I)
_OWNER_WORDS = re.compile(r"\b(owner|malik|master|boss|creator)\b", re.I)
_MARK = re.compile(r"@@(\d{3,15})@@")
OWNER_LINE = ("[This person is your owner and creator. Be extra warm, respectful and caring with him, a bit more attentive than with others, "
              "like a loyal assistant who adores him. Never be rude. You like to address him with a respectful title, but NEVER the same one "
              "twice in a row and never keep repeating one word; sound natural and fresh each time, and never open replies the same way twice. {hint}]")
_TITLES = ("love", "malik", "master", "baby", "sweetheart", "owner ji", "malik ji", "master ji", "jaan", "darling", "mere malik",
           "my love", "dear master", "sweet malik", "boss ji", "owner sahab")
_OWNER_HINTS = tuple(f"This time address him once as '{t}'." for t in _TITLES) + (
    "This time use his name once instead of a title.", "This time use no title, just talk naturally.", "This time use no title, just talk naturally.",
    "This time start with the actual answer, then end with a respectful title of your own choice (different from the one you used last).",
)


async def _owner_name() -> str:
    oid = get_settings().owner_id
    try:
        async with db.tx() as s:
            u = await s.get(User, oid) if oid else None
        return (u.first_name if u else "") or "your owner"
    except Exception:
        return "your owner"


async def _tag_candidates(m: Message, bot_id: int, text: str) -> dict[int, str]:
    """People the asker may want Ella to tag ("Ravi ko bol ki ..."). Found ONLY from what is in the message (a @username,
    a tapped mention, or a first name of someone seen in this group) - the AI can later tag nobody outside this dict."""
    out: dict[int, str] = {}
    ents = list(m.entities or [])
    wants = bool(_TAG_WORDS.search(text)) or any(e.type in ("mention", "text_mention") for e in ents)
    if not wants:
        return out
    low = text.lower()
    try:
        async with db.tx() as s:
            for e in ents:
                if e.type == "text_mention" and e.user and e.user.id != bot_id and not e.user.is_bot:
                    out[e.user.id] = _clip(e.user.full_name or e.user.first_name, 40)
                elif e.type == "mention":
                    un = (m.text or "")[e.offset + 1:e.offset + e.length].lower()
                    row = (await s.execute(select(User.id, User.first_name).where(User.username_lc == un))).first()
                    if row and row[0] != bot_id:
                        out[row[0]] = _clip(row[1], 40)
            ids = set((await s.execute(select(GroupMember.user_id).where(GroupMember.chat_id == m.chat.id))).scalars())
            ids.update((await s.execute(select(GroupStat.user_id).where(GroupStat.chat_id == m.chat.id))).scalars())
            if _OWNER_WORDS.search(text) and get_settings().owner_id:
                ids.add(get_settings().owner_id)
            if ids:
                rows = (await s.execute(select(User.id, User.first_name).where(User.id.in_(ids), User.deleted_account == False))).all()  # noqa: E712
                for uid, nm in rows:
                    nm = _clip(nm or "", 40)
                    if uid != bot_id and len(nm) >= 3 and re.search(rf"(?<!\w){re.escape(nm.lower())}(?!\w)", low):
                        out[uid] = nm
                    elif uid == get_settings().owner_id and _OWNER_WORDS.search(text):
                        out[uid] = nm or "owner"
    except Exception:
        log.warning("tag candidate lookup failed", exc_info=True)
    return dict(list(out.items())[:5])


def _tag_line(cands: dict[int, str]) -> str:
    if not cands:
        return ""
    who = "; ".join(f"{n} = @@{i}@@" for i, n in cands.items())
    return (f"[If they ask you to tell/ask/call one of these people, address that person in your reply by writing their marker exactly "
            f"as given (e.g. @@123@@), once, where their name belongs. People: {who}. Never invent other markers.]")


def _with_tags(text: str, cands: dict[int, str]) -> tuple[str, list[MessageEntity]]:
    """Swap each allowed @@id@@ for the person's name and make it a real tappable mention. Unknown markers are just removed."""
    out, ents, pos = "", [], 0
    for mt in _MARK.finditer(text):
        out += text[pos:mt.start()]
        pos = mt.end()
        uid = int(mt.group(1))
        if uid not in cands:
            continue
        nm = cands[uid]
        off = len(out.encode("utf-16-le")) // 2
        ents.append(MessageEntity(type="text_mention", offset=off, length=len(nm.encode("utf-16-le")) // 2,
                                  user=TgUser(id=uid, is_bot=False, first_name=nm)))
        out += nm
    return out + text[pos:], ents


async def _delete_later(msg: Message, delay: int = STICKER_REPLY_TTL_S) -> None:
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:              # already deleted, too old, or no longer allowed: nothing to do
        pass


async def _send(m: Message, text: str, *, expire: bool = False, **kw) -> None:
    sent = await m.reply(text, **kw)
    if expire:
        t = asyncio.create_task(_delete_later(sent))
        _bg.add(t)
        t.add_done_callback(_bg.discard)


def unavailable() -> str:
    return f"{brand.EMOJI} {esc(brand.name())} is a little busy right now. Please try again in a bit — all game commands still work!"


@router.message(Command("clear", "reset"))
async def clear_cmd(m: Message, ai: ChatService | None):
    if ai:
        await ai.clear(m.from_user.id, m.chat.id)
    await m.reply("🧹 Done — I've forgotten our chat. Fresh start! 🦋")


def _vlabel(key: str) -> str:
    return f"{'♀' if 'woman' in tts.VOICES[key][1] else '♂'} {key.capitalize()}"


def _voices_text(current: str) -> str:
    lst = "\n".join(f"<code>{k}</code> - {d}" for k, (_, d) in tts.VOICES.items())
    return (f"🎙 <b>Choose my voice</b>\nOnly you can use these buttons. Now: <b>{current or tts.DEFAULT}</b>\n\n{lst}\n\n"
            f"👉 <b>How to use:</b> type <code>/v</code> and then your message (or reply to a message with <code>/v</code>) "
            f"and I will answer with a <b>voice message</b>. Normal chat stays text.\n"
            f"<i>Limit: {voice_access.LIMIT} voice messages per hour. English-only voices read Hinglish badly, so for Hinglish pick Neerja or Prabhat.</i>")


@router.message(Command("voices"))
async def voices_cmd(m: Message):
    """Shows the voice buttons. Only the person who typed /voices can press them."""
    if not m.from_user:
        return
    uid = m.from_user.id
    cur = voice_access.get_name(uid)
    rows, row = [], []
    for k in tts.VOICES:
        row.append(InlineKeyboardButton(text=("✅ " if k == cur else "") + _vlabel(k), callback_data=f"vc:{uid}:{k}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    await m.reply(_voices_text(cur), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("vc:"))
async def voices_pick(cb: CallbackQuery):
    try:
        _, owner, key = cb.data.split(":", 2)
        owner = int(owner)
    except ValueError:
        await cb.answer()
        return
    if cb.from_user.id != owner:                          # not your menu
        await cb.answer("This menu is for the person who opened it. Type /voices to get your own 🙂", show_alert=True)
        return
    if key not in tts.VOICES or not await kv.set_nx(f"vc:{owner}", 4):      # unknown voice, or a double tap / spam: ignore
        await cb.answer()
        return
    voice_access.set_name(owner, key)
    await cb.answer(f"Voice set: {key.capitalize()} ✅")
    try:                                                  # the same message turns into the confirmation; the buttons are gone, so no more taps
        await cb.message.edit_text(
            f"✅ Voice set: <b>{key.capitalize()}</b> ({tts.VOICES[key][1]})\n\n"
            f"👉 Type <code>/v</code> and then your message (or reply to a message with <code>/v</code>) to get my answer as a voice message.\n"
            f"<i>Limit: {voice_access.LIMIT} voice messages per hour. Use /voices to change the voice again.</i>", reply_markup=None)
    except Exception:
        log.debug("voices confirm edit failed", exc_info=True)


@router.message(Command("aivoice"))
async def aivoice_cmd(m: Message, command: CommandObject):
    """Owner only: switch voice replies ON/OFF for everybody at once."""
    if not m.from_user or m.from_user.id != get_settings().owner_id:
        return
    a = (command.args or "").strip().lower()
    if a in ("on", "off"):
        voice_access.set_enabled(a == "on")
    await m.reply(f"🎙 Voice replies for everyone: <b>{'ON' if voice_access.enabled() else 'OFF'}</b>\n<code>/aivoice on</code> · <code>/aivoice off</code>")


async def _send_audio(m: Message, data: bytes, note: bool, title: str = "Ella") -> None:
    if note:
        await m.reply_voice(BufferedInputFile(data, "ella.ogg"))
    else:
        await m.reply_audio(BufferedInputFile(data, "ella.mp3"), title=title)


async def _speak(m: Message, answer: str) -> tuple[bool, str]:
    """/v: send the answer as a voice message. Returns (sent, note). When not sent the caller sends the text, with `note` in front.
    Never raises."""
    try:
        if not voice_access.enabled():
            return False, "🔇 Voice replies are switched off right now, so here is text:"
        uid = m.from_user.id
        w = voice_access.wait_secs(uid)
        if w:
            return False, f"⏳ Voice limit reached ({voice_access.LIMIT} per hour). Voice is back in {dur(w)}; here is text:"
        out = await tts.synth(answer, voice_access.get_name(uid))
        if not out:
            return False, f"⚠️ Voice failed ({tts.last_error or 'unknown'}), here is text:"
        await _send_audio(m, out[0], out[1])
        voice_access.count(uid)
        return True, ""
    except Exception:
        log.warning("voice reply failed", exc_info=True)
        return False, "⚠️ Voice failed, here is text:"


@router.message(Command("aitest"))
async def aitest_cmd(m: Message, ai: ChatService | None):
    """Owner-only: shows the exact reason when the AI is not answering."""
    if not m.from_user or m.from_user.id != get_settings().owner_id:
        return
    if ai is None:
        await m.reply("AI is disabled: no AI key found in .env (GEMINI_API_KEYS / GROQ_API_KEYS / ...). See the startup log.")
        return
    ok, why = await ai.diagnose()
    await m.reply(("✅ " if ok else "❌ ") + why, parse_mode=None)


@router.callback_query(F.data == "ai:clear")
async def clear_cb(cb: CallbackQuery, ai: ChatService | None):
    if ai:
        await ai.clear(cb.from_user.id)
    await safe_answer(cb, "Chat history cleared 🧹", alert=True)


UNSUPPORTED = ("🦋 I can't do that. I can only: ban / unban, mute / unmute, give / take / set diamonds, "
               "show user info and stats, and switch maintenance on/off. (Telegram bots can't remove anyone's profile photo; use /broadcast for broadcasts.)")


async def _find_target(m: Message, text: str, amount: int | None) -> int | None:
    """Target comes ONLY from the owner's message itself (reply > mention > @username > long numeric ID), never from the AI."""
    toks = text.split()
    args = [t for t in toks if t.startswith("@") and len(t) > 2]
    if not args:
        args = [t for t in toks if t.isdigit() and len(t) >= 7 and t != str(amount)]
    target, _ = await resolve_target(m, args)
    return target


async def _owner_command(m: Message, ai: ChatService, text: str) -> bool:
    """Natural-language admin orders. Authorisation is by sender ID in CODE (owner only); the AI only classifies the sentence.
    Risky actions still need the Confirm tap, and every action is written to the admin log by adm.execute."""
    if not m.from_user or adm.role(m.from_user.id) != "owner":
        return False
    it = await ai.intent(text)
    if it is None:
        return False
    act, amount = it["action"], it["amount"]
    if act == "unsupported":
        await m.reply(UNSUPPORTED); return True
    if act == "stats":
        await admin_h.stats(m); return True
    if act in ("maintenance_on", "maintenance_off"):
        on = act == "maintenance_on"
        await admin_h._ask(m, "maintenance", {"on": on}, f"maintenance <b>{'ON' if on else 'OFF'}</b>"); return True
    target = await _find_target(m, text, amount)
    if target is None:
        await m.reply("🦋 Who? Reply to their message, or write @username / numeric ID."); return True
    if act == "userinfo":
        await admin_h.send_userinfo(m, target); return True
    if target == m.from_user.id or adm.role(target) == "owner":
        raise GameError("❌ Not allowed on that account.")
    if act in ("give", "take", "setbalance"):
        if amount is None or amount < 0 or (amount == 0 and act != "setbalance"):
            raise GameError("❌ I couldn't understand the amount. Write a clear number (e.g. 5000).")
        await admin_h._ask(m, act, {"target": target, "amount": amount}, f"{act} <b>{num(amount)}</b> 💎 for <code>{target}</code>")
    elif act == "ban":
        await admin_h._ask(m, "ban", {"target": target, "reason": it["reason"]}, f"ban <code>{target}</code> {esc(it['reason'])}")
    else:   # unban / mute / unmute: reversible, logged, no confirm (same as the slash commands)
        await m.reply(await adm.execute(m.from_user.id, act, {"target": target, "reason": ""}))
    return True


async def _talk(m: Message, bot: Bot, ai: ChatService | None, text: str,
                image: bytes | None = None, mime: str = "image/webp", sticker: bool = False, voice: bool = False) -> None:
    if m.chat.type == "private":                    # AI chat is for groups only
        if await kv.set_nx(f"aidm:{m.from_user.id}", 60):
            await m.reply("💬 AI chat works only in <b>groups</b>. Add me to your group, then tag me, reply to me or say my name!\n"
                          "All game commands work here too. 🦋")
        return
    if not text:
        await m.reply("Yes? 🦋")
        return
    kind = None if sticker else classify(text)
    if kind:                                         # "who made you / developer / give me your code" -> fixed answer, tags the developer
        await m.reply(dev_reply(kind, await _dev_tag(bot)))
        return
    if ai is None:
        await m.reply("🦋 Chat isn't configured right now, but all game commands work!")
        return
    if not sticker and await _owner_command(m, ai, text):   # owner's orders (ban, give, mute ...) – nobody else gets this path
        return
    if await _cooldown_blocked(m):
        return
    roll = random.random()
    if sticker and roll < 0.5 and await _send_sticker(m, bot):     # to a sticker she answers with ONLY a sticker (50%) or ONLY text (50%); normal chat never gets stickers
        return
    if not sticker and maybe_identity(text):         # unusual wording: let the AI decide if it is really "who made you / your code"
        kind = await ai.identity(text)
        if kind:
            await m.reply(dev_reply(kind, await _dev_tag(bot)))
            return
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=m.chat.id):
            cands = {} if sticker else await _tag_candidates(m, bot.id, text)
            answer = await ai.reply(m.from_user.id, m.chat.id, text, image, mime, context=_context(m, bot.id, voice) + _tag_line(cands) + ("\n" if cands else ""))
    except AIRateLimited as e:
        await _send(m, f"✋ Slow down a little — try again in {dur(e.retry_after)}.")
        return
    except (AIQuotaExceeded, AIUnavailable):
        await _send(m, unavailable())
        return
    except Exception:
        log.exception("AI failure")
        await _send(m, unavailable())
        return
    if leaks_provider(answer):                        # the AI named a company/model as its maker: never send that
        await m.reply(dev_reply("dev", await _dev_tag(bot)))
        return
    if sticker and answer.strip() == BLOCKED_REPLY:    # the AI's safety filter refused the picture (nudity etc.)
        answer = "🙈 Let's keep the stickers fun and clean here, please! 🦋"
    if voice:                                          # /v: the answer goes out as a voice message
        sent, note = await _speak(m, _with_tags(answer, cands)[0])
        if sent:
            return
        answer = f"{note}\n\n{answer}"
    for part in split_text(answer):
        part, ents = _with_tags(part, cands)
        await _send(m, part, parse_mode=None, **({"entities": ents} if ents else {}))   # model output is untrusted: plain text + only our own mention entities


@router.message(Command("ask", "chat", "bot"))
async def ask_cmd(m: Message, command: CommandObject, bot: Bot, ai: ChatService | None):
    """Works in groups and DMs without needing a mention."""
    await _talk(m, bot, ai, (command.args or "").strip())


@router.message(Command("v"))
async def v_cmd(m: Message, command: CommandObject, bot: Bot, ai: ChatService | None):
    """/v <message> (or reply to a message with /v): Ella answers with a voice message."""
    text = (command.args or "").strip()
    r = m.reply_to_message
    if not text and r is not None:
        text = _msg_text(r) if (r.text or r.caption) else ""
    if not text:
        await m.reply("🎙 Type <code>/v</code> and then your message, or reply to a message with <code>/v</code>.\nPick my voice with /voices.")
        return
    await _talk(m, bot, ai, text, voice=True)


@router.message(F.text, ~F.text.startswith("/"))
async def chat(m: Message, bot: Bot, ai: ChatService | None, group=None):
    me = await bot.me()
    text = m.text
    if m.chat.type == "private":
        return                                      # AI chat is groups-only; plain DM text is ignored (no spam)
    if m.chat.type != "private":
        if group is not None and not group.ai_enabled:
            return
        hit, text = addressed(m, me)
        if not hit:
            return
    await _talk(m, bot, ai, text)


# ---------------------------------------------------------------- stickers
MAX_STICKER_BYTES = 400_000


async def _sticker_image(bot: Bot, st) -> tuple[bytes | None, str]:
    """Static stickers are WEBP pictures. Animated (.tgs) / video (.webm) ones can't be read by the AI, so use their still thumbnail."""
    fid = st.file_id if not (st.is_animated or st.is_video) else (st.thumbnail.file_id if st.thumbnail else None)
    if not fid:
        return None, "image/webp"
    try:
        buf = BytesIO()
        await bot.download(fid, destination=buf)
        data = buf.getvalue()
    except Exception:
        log.warning("could not download sticker", exc_info=True)
        return None, "image/webp"
    if not data or len(data) > MAX_STICKER_BYTES:
        return None, "image/webp"
    return data, ("image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "image/jpeg")


@router.message(F.voice | F.audio)
async def voice_in(m: Message, bot: Bot, ai: ChatService | None, group=None):
    """A voice note aimed at Ella (a reply to her message, or her @username in the caption) is turned into text and then goes through the
    normal chat flow. Max AI_VOICE_MAX_SECONDS (60). The audio is only kept in memory while it is transcribed."""
    if not m.from_user:
        return
    if m.chat.type == "private":                    # AI chat is groups-only: same notice as for text, and no transcription quota is spent
        await _talk(m, bot, ai, "")
        return
    if group is not None and not group.ai_enabled:
        return
    me = await bot.me()
    rep = m.reply_to_message
    hit = bool(rep and rep.from_user and rep.from_user.id == me.id) or bool(m.caption and f"@{me.username}".lower() in m.caption.lower())
    if not hit:
        return
    if ai is None:
        await _talk(m, bot, ai, "hi")
        return
    media = m.voice or m.audio
    limit = stt.max_seconds()
    if (media.duration or 0) > limit:
        await m.reply(f"🎙 That voice note is too long - please keep it under {limit} seconds.")
        return
    if (media.file_size or 0) > 5 * 1024 * 1024:
        await m.reply("🎙 That file is too big for me to listen to.")
        return
    w = voice_access.in_wait_secs(m.from_user.id)
    if w:
        await m.reply(f"🎙 Voice-note limit reached ({voice_access.in_limit()} per hour). Please type it, or try again in {dur(w)}.")
        return
    voice_access.in_count(m.from_user.id)
    text = None
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=m.chat.id):
            buf = BytesIO()
            await bot.download(media, destination=buf)
            text = await stt.get(get_settings()).transcribe(buf.getvalue(), media.mime_type or "audio/ogg")
    except Exception:
        log.warning("voice note handling failed", exc_info=True)
    if not text or not text.strip():
        await m.reply("🎙 Couldn't hear that, please type it.")
        return
    await _talk(m, bot, ai, text.strip()[:get_settings().ai_max_input_chars])


@router.message(F.sticker)
async def sticker_chat(m: Message, bot: Bot, ai: ChatService | None, group=None):
    """A sticker has no text, so the only way it can be 'for the bot' is a reply to one of its messages."""
    if m.chat.type == "private" or not m.from_user:
        return
    if group is not None and not group.ai_enabled:
        return
    me = await bot.me()
    rep = m.reply_to_message
    if not (rep and rep.from_user and rep.from_user.id == me.id):
        return
    st = m.sticker
    kind = "animated" if st.is_animated else "video" if st.is_video else "static"
    note = f"[sticker: emoji {st.emoji or 'none'}; pack {st.set_name or 'unknown'}; {kind}] React to this sticker."
    image, mime = (None, "image/webp") if ai is None else await _sticker_image(bot, st)
    await _talk(m, bot, ai, note, image, mime, sticker=True)
