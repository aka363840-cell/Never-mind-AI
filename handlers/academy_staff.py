"""Academy Life, hidden staff commands (testing): /academyadvance, /academyreset. Listed in STAFF, never advertised."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message

from database.models import utcnow
from database.session import db
from keyboards.menus import kb
from services import academy_calendar as cal, academy_events as ev, academy_ticks as ticks, admin as adm
from services.errors import GameError
from utils.telegram import safe_answer, safe_edit

log = logging.getLogger(__name__)
router = Router()


class IsStaff(BaseFilter):
    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and adm.role(u.id) != "none")


router.message.filter(IsStaff())
router.callback_query.filter(IsStaff())


@router.message(Command("academyadvance"))
async def advance_cmd(m: Message, bot: Bot):
    try:
        now = utcnow()
        async with db.tx() as s:
            season, ph = await cal.advance(s, now)
        await m.reply(f"⏩ Season {season.number} → <b>{ph.emoji} {ph.name}</b>" + (f" (Day {ph.day})" if ph.day else "")
                      + f"\nPhase started {cal.fmt_when(ph.starts_at)}.")
        try:
            await ticks.run_ticks(bot, now, force=True)          # let season events / later ticks react right away
        except Exception:
            log.exception("academyadvance: ticks failed")
    except GameError:
        raise
    except Exception:
        log.exception("academyadvance failed")
        await m.reply("⚠️ Advance failed, see the log.")


@router.message(Command("academyreset"))
async def reset_cmd(m: Message):
    uid = m.from_user.id
    await m.reply("⚠️ <b>Reset the Academy?</b>\nThis deletes ALL seasons and students (and whatever later parts store) and starts a fresh "
                  "Season 1. Diamonds already paid out stay paid.\n<i>Testing tool.</i>",
                  reply_markup=kb([[B(text="✅ Yes, reset", callback_data=f"acs:rs:{uid}"),
                                    B(text="✖️ Cancel", callback_data=f"acs:rx:{uid}")]]))


@router.callback_query(F.data.startswith("acs:rx:"))
async def reset_cancel(cb: CallbackQuery):
    if str(cb.from_user.id) != cb.data.split(":")[2]:
        return await safe_answer(cb, "Not yours.", True)
    await safe_edit(cb, "✖️ Reset cancelled.")
    await safe_answer(cb)


@router.callback_query(F.data.startswith("acs:rs:"))
async def reset_confirm(cb: CallbackQuery):
    if str(cb.from_user.id) != cb.data.split(":")[2]:
        return await safe_answer(cb, "Not yours.", True)
    try:
        await ev.emit(ev.RESET, cb.from_user.id)                 # later parts clear their own tables first (own transactions)
        async with db.tx() as s:
            students, seasons = await cal.reset_all(s)
            await ticks.clear_marker(s)
        async with db.tx() as s:
            season = await cal.ensure_season(s, utcnow())
        await safe_edit(cb, f"🧹 Academy reset: removed {students} student(s) and {seasons} season(s).\n"
                            f"Season {season.number} starts now (Day 1).")
        await safe_answer(cb)
    except Exception:
        log.exception("academyreset failed")
        await safe_answer(cb, "Reset failed, see the log.", True)
