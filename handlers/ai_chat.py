"""Free-form chat with the bot. Touches ONLY the ai package — never wallets, missions or combat."""
from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

from ai.gemini import BLOCKED_REPLY
from ai.base import AIQuotaExceeded, AIRateLimited, AIUnavailable
from ai.service import ChatService
from config import get_settings
from database.session import db
from handlers import admin as admin_h
from services import admin as adm
from services.errors import GameError
from services.kv import kv
from utils import brand
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


STICKER_REPLY_TTL_S = 120          # the bot's reply to a sticker disappears after 2 minutes
_bg: set[asyncio.Task] = set()     # keep references so the delete tasks are not garbage-collected


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
                image: bytes | None = None, mime: str = "image/webp", sticker: bool = False) -> None:
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
    if not sticker and maybe_identity(text):         # unusual wording: let the AI decide if it is really "who made you / your code"
        kind = await ai.identity(text)
        if kind:
            await m.reply(dev_reply(kind, await _dev_tag(bot)))
            return
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=m.chat.id):
            answer = await ai.reply(m.from_user.id, m.chat.id, text, image, mime)
    except AIRateLimited as e:
        await _send(m, f"✋ Slow down a little — try again in {dur(e.retry_after)}.", expire=sticker)
        return
    except (AIQuotaExceeded, AIUnavailable):
        await _send(m, unavailable(), expire=sticker)
        return
    except Exception:
        log.exception("AI failure")
        await _send(m, unavailable(), expire=sticker)
        return
    if leaks_provider(answer):                        # the AI named a company/model as its maker: never send that
        await m.reply(dev_reply("dev", await _dev_tag(bot)))
        return
    if sticker and answer.strip() == BLOCKED_REPLY:    # the AI's safety filter refused the picture (nudity etc.)
        answer = "🙈 Let's keep the stickers fun and clean here, please! 🦋"
    for part in split_text(answer):
        await _send(m, part, expire=sticker, parse_mode=None)       # model output is untrusted: send as plain text


@router.message(Command("ask", "chat", "bot"))
async def ask_cmd(m: Message, command: CommandObject, bot: Bot, ai: ChatService | None):
    """Works in groups and DMs without needing a mention."""
    await _talk(m, bot, ai, (command.args or "").strip())


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
