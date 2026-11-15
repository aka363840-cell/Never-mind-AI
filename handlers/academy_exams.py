"""Academy exams (Part 3): /exam /grades /classrank. Thin handlers: rules in game/academy_rules.py, logic in
services/academy_exams.py (+ academy_exam_logic.py), texts in utils/content/academy_exam_texts.py.
Callback data (all < 64 bytes, all carry the owner id, all checked): acx:c:<uid>:<attempt>:<n>:<idx> answer · acx:f:.. focus ·
acx:n:.. next round / finish · acx:g:<uid> refresh grades · acx:r:<uid>:<g|w> class rank scope (g = global, w = this group)."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message, ReplyParameters

from database.models import User
from database.session import db
from game import academy_rules as AR
from handlers.academy_core import guard
from keyboards.menus import kb
from services import academy_exam_views as V, academy_exams as ex
from services.errors import GameError
from utils import gifs
from utils.content import academy_exam_rounds as CARDS, academy_exam_texts as T
from utils.telegram import safe_answer, safe_edit, safe_send

log = logging.getLogger(__name__)
router = Router()


def _is_group(m: Message) -> bool:
    return m.chat.type in ("group", "supergroup")


async def _say(m: Message, text: str, markup=None) -> None:
    extra: dict = {"reply_parameters": ReplyParameters(message_id=m.message_id, allow_sending_without_reply=True)}
    if getattr(m, "is_topic_message", False) and m.message_thread_id:
        extra["message_thread_id"] = m.message_thread_id
    await safe_send(m.bot, m.chat.id, text, markup, **extra)


def _uid(part: str) -> int:
    try:
        return int(part)
    except ValueError:
        return 0


def _kb(scr, uid: int):
    """Inline keyboard of a screen."""
    if scr.status == "question":
        card = CARDS.BY_KEY[scr.card_key]
        rows = [[B(text=lab, callback_data=f"acx:c:{uid}:{scr.attempt_id}:{scr.n}:{i}")]
                for i, lab in enumerate(V.button_labels(scr.card_key))]
        return kb(rows)
    if scr.status == "result":
        row = []
        if scr.can_focus:
            row.append(B(text=T.FOCUS_BTN.format(left=scr.focus_left, energy=AR.FOCUS_ENERGY), callback_data=f"acx:f:{uid}:{scr.attempt_id}:{scr.n}"))
        row.append(B(text=T.FINISH_BTN if scr.n >= scr.total else T.NEXT_BTN, callback_data=f"acx:n:{uid}:{scr.attempt_id}:{scr.n}"))
        return kb([row])
    return kb([[B(text="📊 My grades", callback_data=f"acx:g:{uid}"), B(text="🏅 Class rank", callback_data=f"acx:r:{uid}:g")]])


def _text(scr) -> str:
    if scr.status == "question":
        return V.question_text(scr)
    if scr.status == "result":
        return V.result_text(scr)
    return V.final_text(scr)


def _gif_for(scr) -> str | None:
    if scr.status != "done":
        return None
    if scr.grade in AR.PASS_GRADES_GIF:
        return AR.EXAM_GIFS["top"]
    if scr.grade in AR.FAIL_GRADES:
        return AR.EXAM_GIFS["fail"]
    return None


async def _show(cb: CallbackQuery, scr, uid: int) -> None:
    await safe_edit(cb, _text(scr), _kb(scr, uid))
    gif = _gif_for(scr)
    if gif and cb.message is not None:
        await gifs.bonus(cb.message, gif)                       # falls back to nothing when GIFs are off / down
    await safe_answer(cb)


# ------------------------------------------------------------------ commands
@router.message(Command("exam"))
@guard
async def exam_cmd(m: Message, db_user: User):
    uid = db_user.id
    try:
        async with db.tx() as s:
            if _is_group(m):
                await ex.note_chat(s, m.chat.id, uid)
            scr = await ex.open_exam(s, uid)
    except ex.ExamBlocked as e:
        markup = kb([[B(text="📝 Enroll now", callback_data=f"ac:en:{uid}")]]) if e.enroll else None
        return await _say(m, e.text, markup)
    await _say(m, _text(scr), _kb(scr, uid))
    await ex.announce(uid, scr)


@router.message(Command("grades"))
@guard
async def grades_cmd(m: Message, db_user: User):
    async with db.tx() as s:
        if _is_group(m):
            await ex.note_chat(s, m.chat.id, db_user.id)
        rep = await ex.grades_report(s, db_user.id)
    await _say(m, V.grades_text(rep), kb([[B(text="🏅 Class rank", callback_data=f"acx:r:{db_user.id}:g"),
                                           B(text="🔄 Refresh", callback_data=f"acx:g:{db_user.id}")]]))
    for k, d in rep["events"]:
        await ex.ev.emit(k, db_user.id, **d)


@router.message(Command("classrank"))
@guard
async def classrank_cmd(m: Message, db_user: User):
    uid = db_user.id
    chat = m.chat.id if _is_group(m) else None
    async with db.tx() as s:
        if chat is not None:
            await ex.note_chat(s, chat, uid)
        data = await ex.class_rank(s, uid, chat)
    await _say(m, V.rank_text(data), _rank_kb(uid, data["scope"], chat is not None))


def _rank_kb(uid: int, scope: str, in_group: bool):
    row = [B(text="🌍 Global" + (" ✓" if scope == "global" else ""), callback_data=f"acx:r:{uid}:g")]
    if in_group:
        row.append(B(text="👥 This group" + (" ✓" if scope == "group" else ""), callback_data=f"acx:r:{uid}:w"))
    return kb([row])


# ------------------------------------------------------------------ buttons
def _parse(cb: CallbackQuery, n_parts: int) -> list[str] | None:
    parts = (cb.data or "").split(":")
    return parts if len(parts) == n_parts else None


async def _owner_or_deny(cb: CallbackQuery, uid: int) -> bool:
    if cb.from_user.id != uid:
        await safe_answer(cb, T.NOT_YOURS, True)
        return False
    return True


async def _run(cb: CallbackQuery, uid: int, fn, *args) -> None:
    """Run one exam step in one transaction; a refusal is shown as a popup and changes nothing."""
    try:
        async with db.tx() as s:
            scr = await fn(s, uid, *args)
    except GameError as e:
        return await safe_answer(cb, str(getattr(e, "text", e))[:190].replace("<b>", "").replace("</b>", ""), True)
    await _show(cb, scr, uid)
    await ex.announce(uid, scr)


@router.callback_query(F.data.startswith("acx:c:"))
@guard
async def cb_answer(cb: CallbackQuery):
    p = _parse(cb, 6)
    if not p:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if await _owner_or_deny(cb, uid):
        await _run(cb, uid, ex.answer, _uid(p[3]), _uid(p[4]), _uid(p[5]))


@router.callback_query(F.data.startswith("acx:f:"))
@guard
async def cb_focus(cb: CallbackQuery):
    p = _parse(cb, 5)
    if not p:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if await _owner_or_deny(cb, uid):
        await _run(cb, uid, ex.focus, _uid(p[3]), _uid(p[4]))


@router.callback_query(F.data.startswith("acx:n:"))
@guard
async def cb_next(cb: CallbackQuery):
    p = _parse(cb, 5)
    if not p:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if await _owner_or_deny(cb, uid):
        await _run(cb, uid, ex.next_round, _uid(p[3]), _uid(p[4]))


@router.callback_query(F.data.startswith("acx:g:"))
@guard
async def cb_grades(cb: CallbackQuery):
    uid = _uid((cb.data or "").split(":")[2])
    if not await _owner_or_deny(cb, uid):
        return
    async with db.tx() as s:
        rep = await ex.grades_report(s, uid)
    await safe_edit(cb, V.grades_text(rep), kb([[B(text="🏅 Class rank", callback_data=f"acx:r:{uid}:g"),
                                                  B(text="🔄 Refresh", callback_data=f"acx:g:{uid}")]]))
    await safe_answer(cb)


@router.callback_query(F.data.startswith("acx:r:"))
@guard
async def cb_rank(cb: CallbackQuery):
    p = _parse(cb, 4)
    if not p:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if not await _owner_or_deny(cb, uid):
        return
    in_group = cb.message is not None and cb.message.chat.type in ("group", "supergroup")
    chat = cb.message.chat.id if (in_group and p[3] == "w") else None
    async with db.tx() as s:
        data = await ex.class_rank(s, uid, chat)
    await safe_edit(cb, V.rank_text(data), _rank_kb(uid, data["scope"], in_group))
    await safe_answer(cb)
