"""Academy reminders and graduation (Part 6): /academyremind on|off, /reportcard [last], /halloffame, and the Enroll button of the
group announcements. Thin handlers: logic in services/academy_graduation.py and services/academy_notify.py, texts in
utils/content/academy_remind_texts.py.

Callback data: acn:e:<season_id> (< 64 bytes). This ONE button is posted in groups, so it has no owner id: it acts only for the person
who presses it (their id comes from Telegram, never from the data) and only sends THEM their own enrolment menu (whose buttons carry
their id and are owner-checked in handlers/academy_core.py). Pressing it for someone else is impossible."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from database.models import User, utcnow
from game import academy_rules as AR
from handlers.academy_core import _enroll_intro, guard
from services import academy_events as ev, academy_graduation as grad, academy_notify as N
from utils.content import academy_remind_texts as T
from utils.telegram import safe_answer, safe_send

log = logging.getLogger(__name__)
router = Router()


@router.message(Command("academyremind"))
@guard
async def remind_cmd(m: Message, command: CommandObject, db_user: User):
    arg = (command.args or "").strip().lower()
    private = m.chat.type == "private"
    if arg in ("on", "off"):
        on = arg == "on"
        await N.set_remind(db_user.id, on, utcnow(), private=private)
        await ev.emit(ev.REMIND_CHANGED, db_user.id, on=on)
        text = T.REMIND_ON.format(q_from=AR.QUIET_FROM_HOUR, q_to=f"{AR.QUIET_TO_HOUR:02d}") if on else T.REMIND_OFF
        if on and not private:
            text += T.REMIND_DM_HINT
        return await m.reply(text)
    state = "🔔 on" if await N.reminders_on(db_user.id) else "🔕 off"
    await m.reply(T.REMIND_STATUS.format(state=state) + "\n" + T.REMIND_USAGE)


@router.message(Command("reportcard"))
@guard
async def reportcard_cmd(m: Message, command: CommandObject, db_user: User):
    last = (command.args or "").strip().lower() in ("last", "prev", "previous")
    text = await grad.report_card(db_user.id, utcnow(), last=last)
    if m.chat.type == "private":
        await N.clear_unreachable(db_user.id)
    await m.reply(text or T.NO_REPORT)


@router.message(Command("halloffame"))
@guard
async def halloffame_cmd(m: Message):
    await m.reply(await grad.hall_of_fame())


@router.callback_query(F.data.startswith("acn:e:"))
@guard
async def cb_enroll_from_group(cb: CallbackQuery):
    uid = cb.from_user.id                                   # always the presser, never an id from the data
    text, markup = await _enroll_intro(uid)
    if cb.message is not None and cb.message.chat.type == "private":
        await safe_send(cb.bot, uid, text, markup)
        return await safe_answer(cb)
    if await safe_send(cb.bot, uid, text, markup) is None:
        return await safe_answer(cb, "📩 I can't message you yet. Open a private chat with me, press Start, then tap the button again "
                                     "(or send /enroll there).", True)
    await safe_answer(cb, "📬 I sent you the enrolment in a private message.")
