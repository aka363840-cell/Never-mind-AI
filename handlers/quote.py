"""/q - quote stickers.  Reply to a message with /q   ·   /q 3 quotes that message + the next two   ·   /q red | #cbafff | random   ·   /q r keeps the message it replied to.
Telegram bots can't read chat history, so '/q N' uses the last messages the bot saw in the group since it started."""
from __future__ import annotations

import io
import logging
from collections import deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message, TelegramObject

from services import avatar
from services.errors import GameError
from services.kv import kv
from utils import quotecard

log = logging.getLogger(__name__)
router = Router()
MAX_N = 6
_buf: dict[int, deque] = {}                  # chat -> last 150 text messages (id, user id, name, text)


def _who(m: Message) -> tuple[int, str]:
    if m.sender_chat is not None:
        return m.sender_chat.id, m.sender_chat.title or "Group"
    u = m.from_user
    return (u.id, u.full_name) if u else (0, "Unknown")


def _text(m: Message) -> str:
    return (m.text or m.caption or "").strip()


class QuoteBufferMiddleware(BaseMiddleware):
    """Remembers the last messages of each group in memory (for /q N). Never blocks or changes anything."""

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            if isinstance(event, Message) and event.chat.type in ("group", "supergroup") and _text(event) and not _text(event).startswith("/"):
                uid, name = _who(event)
                _buf.setdefault(event.chat.id, deque(maxlen=150)).append((event.message_id, uid, name, _text(event)[:600]))
        except Exception:
            pass
        return await handler(event, data)


async def _avatar_bytes(bot: Bot, uid: int, cache: dict) -> bytes | None:
    if uid in cache:
        return cache[uid]
    out = None
    try:
        fid = await avatar.best(bot, uid) if uid > 0 else None
        if fid:
            bio = io.BytesIO()
            await bot.download(fid, destination=bio)
            out = bio.getvalue()
    except Exception:
        out = None
    cache[uid] = out
    return out


@router.message(Command("q", "quote"))
async def quote_cmd(m: Message, command: CommandObject, bot: Bot):
    src = m.reply_to_message
    if src is None or not _text(src):
        raise GameError("↩️ Reply to a text message with /q to quote it. Options: /q 3 · /q red · /q #cbafff · /q random · /q r")
    if m.from_user and not await kv.set_nx(f"q:{m.from_user.id}", 3):
        return
    n, keep_reply, bg = 1, False, None
    for tok in (command.args or "").split():
        if tok.isdigit():
            n = max(1, min(MAX_N, int(tok)))
        elif tok.lower() in ("r", "reply"):
            keep_reply = True
        else:
            c = quotecard.parse_color(tok)
            if c is None:
                raise GameError("🎨 Unknown colour. Try: red, blue, green, yellow, purple, pink, black, white, random or a hex like #cbafff.")
            bg = c
    msgs = [(src.message_id, *_who(src), _text(src)[:600])]
    if n > 1:
        later = sorted((x for x in _buf.get(m.chat.id, ()) if x[0] > src.message_id), key=lambda x: x[0])
        msgs += [(i, u, nm, t) for i, u, nm, t in later[:n - 1]]
    cache: dict = {}
    items = []
    for _, uid, name, text in msgs:
        items.append({"name": name, "text": text, "avatar": await _avatar_bytes(bot, uid, cache)})
    reply = None
    rr = src.reply_to_message
    if keep_reply and rr is not None and _text(rr):
        reply = {"name": _who(rr)[1], "text": _text(rr)[:200]}
    webp = quotecard.render(items, bg, reply)
    await m.reply_sticker(BufferedInputFile(webp, "quote.webp"))
