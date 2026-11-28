"""Birthdays + holiday greetings. Birthday is stored as AppState `bday:<uid>` = "MM-DD" (no year/age = no privacy issue).
Gift is paid at most once per user per year (idempotent ledger ref), announced in the groups the bot has seen them in."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from database.models import AppState, GroupMember, GroupSetting, User, aware, utcnow
from database.session import db
from services import economy
from services.errors import GameError

log = logging.getLogger(__name__)
GIFT = 500
MAX_GROUPS = 3
IST = timezone(timedelta(hours=5, minutes=30))
HOLIDAYS = {                       # fixed-date holidays only (moving ones like Diwali/Eid/Holi are not guessed)
    "01-01": "🎆 Happy New Year!", "02-14": "💘 Happy Valentine's Day!", "08-15": "🇮🇳 Happy Independence Day!",
    "10-31": "🎃 Happy Halloween!", "12-25": "🎄 Merry Christmas!", "12-31": "🥂 Last day of the year — make it count!",
}


def parse_md(text: str) -> str:
    """'25-12', '25/12', '25 12' (DD-MM) -> 'MM-DD'. Raises GameError if not a real calendar day."""
    import re
    m = re.fullmatch(r"\s*(\d{1,2})\s*[-/. ]\s*(\d{1,2})\s*", text or "")
    if not m:
        raise GameError("Usage: /birthday DD-MM  e.g. /birthday 25-12")
    d, mo = int(m.group(1)), int(m.group(2))
    try:
        datetime(2024, mo, d)                      # 2024 is a leap year, so 29-02 is accepted
    except ValueError:
        raise GameError("❌ That's not a real date. Use DD-MM, e.g. 25-12.")
    return f"{mo:02d}-{d:02d}"


def pretty(md: str) -> str:
    return datetime(2024, int(md[:2]), int(md[3:])).strftime("%d %b")


async def get(uid: int) -> str | None:
    async with db.tx() as s:
        row = await s.get(AppState, f"bday:{uid}")
        return row.value if row else None


async def set_(uid: int, md: str) -> None:
    async with db.tx() as s:
        row = await s.get(AppState, f"bday:{uid}")
        if row:
            row.value = md
        else:
            s.add(AppState(key=f"bday:{uid}", value=md))


async def clear(uid: int) -> bool:
    async with db.tx() as s:
        row = await s.get(AppState, f"bday:{uid}")
        if not row:
            return False
        await s.delete(row)
        return True


async def _today_birthdays(md: str) -> list[int]:
    async with db.tx() as s:
        rows = (await s.execute(select(AppState.key).where(AppState.key.like("bday:%"), AppState.value == md))).scalars().all()
    return [int(k.split(":", 1)[1]) for k in rows]


async def _groups_of(uid: int) -> list[int]:
    async with db.tx() as s:
        return list((await s.execute(
            select(GroupMember.chat_id).join(GroupSetting, GroupSetting.chat_id == GroupMember.chat_id)
            .where(GroupMember.user_id == uid, GroupSetting.removed == False).limit(MAX_GROUPS))).scalars())  # noqa: E712


async def _mark_once(key: str, val: str) -> bool:
    """True the first time (key,val) is recorded."""
    async with db.tx() as s:
        row = await s.get(AppState, key)
        if row and row.value == val:
            return False
        if row:
            row.value = val
        else:
            s.add(AppState(key=key, value=val))
        return True


async def run_once(bot, now: datetime | None = None) -> None:
    now = now or datetime.now(IST)
    md, year = now.strftime("%m-%d"), now.year
    for uid in await _today_birthdays(md):
        try:
            async with db.tx() as s:
                u = await s.get(User, uid)
                if u is None or u.is_banned:
                    continue
                if (utcnow() - aware(u.created_at)).total_seconds() < 7 * 86400:
                    continue                          # accounts younger than a week get no gift (alt-farming guard)
                await economy.apply(s, uid, wallet=GIFT, kind="birthday", ref=f"bday:{year}:{uid}")
                name = u.first_name or "friend"
        except GameError:
            continue                                  # already gifted this year
        except Exception:
            log.exception("birthday gift failed")
            continue
        text = f"🎂 <b>Happy Birthday, {name}!</b> 🎉\nHere's a gift of <b>{GIFT}</b> 💎 from me. Everyone wish them! 🥳"
        for chat in await _groups_of(uid):
            try:
                await bot.send_message(chat, text)
            except Exception:
                pass
        try:
            await bot.send_message(uid, text)
        except Exception:
            pass
    greet = HOLIDAYS.get(md)
    if greet and await _mark_once("holiday_sent", f"{year}-{md}"):
        async with db.tx() as s:
            chats = list((await s.execute(select(GroupSetting.chat_id).where(GroupSetting.removed == False))).scalars())  # noqa: E712
        for chat in chats:
            try:
                await bot.send_message(chat, f"{greet}\nWishing everyone here a great day from me ♡")
            except Exception:
                pass
            await asyncio.sleep(0.2)


async def birthday_loop(bot) -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await run_once(bot)
        except Exception:
            log.exception("birthday loop error")
        await asyncio.sleep(3600)
