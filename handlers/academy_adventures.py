"""Academy adventures (Part 5): /adventure. Thin handlers: rules in game/academy_rules.py, the engine in
services/academy_adventures.py (+ academy_adventure_logic.py), stories in utils/content/academy_adventures.py, texts in
utils/content/academy_adventure_texts.py, rendering in services/academy_adventure_views.py.

Callback data (prefix acv:, all < 64 bytes, all carry the owner id, all checked):
  acv:l:<uid>                      adventure list (refresh)
  acv:s:<uid>:<adventure key>      start (or resume) an adventure
  acv:r:<uid>                      continue my live adventure
  acv:c:<uid>:<run>:<step>:<idx>   a story choice (the step makes an old / double press harmless)
  acv:q:<uid>:<run>                leave the adventure
"""
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
from services import academy_adventure_logic as L, academy_adventure_views as V, academy_adventures as adv
from services.errors import GameError
from utils import gifs
from utils.content import academy_adventure_texts as T
from utils.telegram import safe_answer, safe_edit, safe_send

log = logging.getLogger(__name__)
router = Router()

_LABEL_MAX = 56          # button text (Telegram shows about this much on a phone)


def _uid(part: str) -> int:
    try:
        return int(part)
    except ValueError:
        return 0


async def _say(m: Message, text: str, markup=None) -> None:
    extra: dict = {"reply_parameters": ReplyParameters(message_id=m.message_id, allow_sending_without_reply=True)}
    if getattr(m, "is_topic_message", False) and m.message_thread_id:
        extra["message_thread_id"] = m.message_thread_id
    await safe_send(m.bot, m.chat.id, text, markup, **extra)


def _plain(text: str) -> str:
    return text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")[:190]


async def _owner_or_deny(cb: CallbackQuery, uid: int) -> bool:
    if cb.from_user.id != uid:
        await safe_answer(cb, T.NOT_YOURS, True)
        return False
    return True


def _parse(cb: CallbackQuery, n_parts: int) -> list[str] | None:
    parts = (cb.data or "").split(":")
    return parts if len(parts) == n_parts else None


# ------------------------------------------------------------------ keyboards
def _list_kb(uid: int, v: L.ListView):
    rows: list[list[B]] = []
    if v.live is not None:
        rows.append([B(text="▶️ Continue", callback_data=f"acv:r:{uid}"),
                     B(text="🚪 Leave", callback_data=f"acv:q:{uid}:{v.live.run_id}")])
    elif v.done_today < v.limit:
        for e in v.entries:
            if e.open:
                rows.append([B(text=f"{e.emoji} {e.name} ⚡{e.energy}"[:_LABEL_MAX], callback_data=f"acv:s:{uid}:{e.key}")])
    rows.append([B(text="🔄 Refresh", callback_data=f"acv:l:{uid}"), B(text="🎓 Dashboard", callback_data=f"ac:db:{uid}")])
    return kb(rows)


def _node_kb(uid: int, scr: L.Screen):
    rows = []
    for c in scr.choices:
        label = f"{c.text} · {c.hint}" if c.hint else c.text
        rows.append([B(text=label[:_LABEL_MAX], callback_data=f"acv:c:{uid}:{scr.run_id}:{scr.step}:{c.idx}")])
    return kb(rows)


def _end_kb(uid: int):
    return kb([[B(text="🗺 More adventures", callback_data=f"acv:l:{uid}"), B(text="🎓 Dashboard", callback_data=f"ac:db:{uid}")]])


def _enroll_kb(uid: int):
    return kb([[B(text="📝 Enroll now", callback_data=f"ac:en:{uid}")]])                 # Part 1 handler checks the owner


def _screen_kb(uid: int, scr: L.Screen):
    if scr.kind == "node":
        return _node_kb(uid, scr)
    return _end_kb(uid)


# ------------------------------------------------------------------ screens
async def _list_screen(uid: int):
    """(text, keyboard). AdventureBlocked (break, not enrolled ...) becomes a friendly text (+ Enroll button when possible)."""
    try:
        async with db.tx() as s:
            v = await adv.list_view(s, uid)
    except adv.AdventureBlocked as e:
        return e.text, (_enroll_kb(uid) if e.enroll else None)
    return V.list_text(v), _list_kb(uid, v)


async def _show(cb: CallbackQuery, scr: L.Screen, uid: int) -> None:
    """Draw a returned Screen into the message the button was on."""
    if scr.kind == "stale":                         # an old or double press: a popup only, the message stays as it is
        return await safe_answer(cb, _plain(scr.message), True)
    await safe_edit(cb, V.screen_text(scr), _screen_kb(uid, scr))
    await safe_answer(cb)
    if scr.kind == "ending" and scr.gif and cb.message is not None:
        await gifs.bonus(cb.message, scr.gif)       # optional SFW GIF; text-only when GIFs are off or down


async def _run(cb: CallbackQuery, uid: int, fn, *args) -> None:
    """Run one adventure step in ONE transaction. A refusal is a popup and changed nothing; events fire after the commit."""
    try:
        async with db.tx() as s:
            scr = await fn(s, uid, *args)
    except adv.AdventureBlocked as e:
        return await safe_answer(cb, _plain(e.text), True)
    except GameError as e:                          # e.g. not enough Energy
        return await safe_answer(cb, _plain(str(e)), True)
    if scr is None:
        text, markup = await _list_screen(uid)
        await safe_edit(cb, text, markup)
        return await safe_answer(cb)
    await _show(cb, scr, uid)
    await adv.announce(uid, scr)


# ------------------------------------------------------------------ command
@router.message(Command("adventure"))
@guard
async def adventure_cmd(m: Message, db_user: User):
    text, markup = await _list_screen(db_user.id)
    await _say(m, text, markup)


# ------------------------------------------------------------------ buttons
@router.callback_query(F.data.startswith("acv:l:"))
@guard
async def cb_list(cb: CallbackQuery):
    uid = _uid((cb.data or "").split(":")[2])
    if not await _owner_or_deny(cb, uid):
        return
    text, markup = await _list_screen(uid)
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.callback_query(F.data.startswith("acv:s:"))
@guard
async def cb_start(cb: CallbackQuery):
    p = _parse(cb, 4)
    if not p:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if await _owner_or_deny(cb, uid):
        await _run(cb, uid, adv.start, p[3])


@router.callback_query(F.data.startswith("acv:r:"))
@guard
async def cb_resume(cb: CallbackQuery):
    uid = _uid((cb.data or "").split(":")[2])
    if await _owner_or_deny(cb, uid):
        await _run(cb, uid, adv.current)


@router.callback_query(F.data.startswith("acv:c:"))
@guard
async def cb_choose(cb: CallbackQuery):
    p = _parse(cb, 6)
    if not p:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if await _owner_or_deny(cb, uid):
        await _run(cb, uid, adv.choose, _uid(p[3]), _uid(p[4]), _uid(p[5]))


@router.callback_query(F.data.startswith("acv:q:"))
@guard
async def cb_quit(cb: CallbackQuery):
    p = _parse(cb, 4)
    if not p:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if await _owner_or_deny(cb, uid):
        await _run(cb, uid, adv.quit_run, _uid(p[3]))
