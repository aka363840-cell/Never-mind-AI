"""AI isolation guard.

The moderation module is never imported by the AI code, and nothing in the AI code can reach a moderation function.
This guard adds the user-facing half: if a (non-owner) person asks the bot in plain language to ban / mute / kick / promote …,
the bot answers with a fixed message pointing to the real commands and the AI model is never even called.
(The bot owner's pre-existing plain-language *game-level* ban/unban path is untouched.)"""
from __future__ import annotations

import re
import time
import unicodedata

from aiogram import Bot, F
from aiogram.filters import BaseFilter
from aiogram.types import Message

from .base import tail_router, throttled
from .perms import owner_id

_STRONG = r"(?:ban|unban|kick|mute|unmute|warn|promote|demote|restrict|unrestrict|silence|punish|expel)"
_WEAK = r"(?:remove|delete|block|lock|unlock|pin|unpin|purge)"            # ordinary words ("remove the bug") - only when aimed at a person
_TARGET = r"(?:him|her|them|this|that|these|those|user|guy|girl|member|spammer|everyone|everybody|all|@\w+|me|isko|usko|ise)"
_PERSON = r"(?:him|her|them|user|member|spammer|everyone|everybody|@\w+|isko|usko|ise)"
_HINDI = r"(?:kar\s*do|kardo|karo|kro|kar\s*de|karde|कर\s*दो|करो)"
REQUEST = re.compile(rf"(?i)(?<!\w)(?:{_STRONG}\s+(?:{_TARGET}|{_HINDI})(?!\w)|{_WEAK}\s+(?:the\s+)?{_PERSON}(?!\w)|(?:isko|usko|ise|inko|unko)\s+(?:{_STRONG}|{_WEAK}))")
ANSWER = ("🛡 I'm sorry - I can't ban, mute, kick, promote or otherwise moderate anyone, and I can't delete messages or change group settings. "
          "My chat side has no administrative powers at all.\n\n"
          "Group admins can use the real moderation commands instead, e.g. <code>/ban</code>, <code>/mute</code>, <code>/warn</code>, <code>/kick</code> "
          "(reply to the person's message). Type <code>/modhelp</code> to see them all.")


def looks_like_moderation_request(text: str) -> bool:
    t = unicodedata.normalize("NFKC", text or "")
    return len(t) <= 300 and REQUEST.search(t) is not None


class AskedToModerate(BaseFilter):
    """Only for messages addressed to the bot (DM, @mention, reply to it, or its name) that read like a moderation order."""
    async def __call__(self, m: Message, bot: Bot) -> bool:
        txt = m.text or ""
        if not txt or txt.startswith(("/", "!")) or m.from_user is None or m.from_user.is_bot:
            return False
        if m.from_user.id == owner_id():            # owner keeps the existing behaviour exactly
            return False
        if not looks_like_moderation_request(txt):
            return False
        if m.chat.type == "private":
            return True
        me = await bot.me()
        low = unicodedata.normalize("NFKC", txt).lower()
        if m.reply_to_message and m.reply_to_message.from_user and m.reply_to_message.from_user.id == me.id:
            return True
        if me.username and f"@{me.username}".lower() in low:
            return True
        if any(e.type == "text_mention" and e.user and e.user.id == me.id for e in (m.entities or [])):
            return True
        first = (me.first_name or "").strip().lower()
        return len(first) >= 3 and re.search(rf"(?<!\w){re.escape(first)}(?!\w)", low) is not None


@tail_router.message(AskedToModerate())
async def refuse(m: Message):
    if throttled(m.from_user.id, "ai-mod-refusal", 20):
        return
    await m.reply(ANSWER, disable_web_page_preview=True)
