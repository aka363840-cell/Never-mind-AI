"""Academy Life, daily actions (Part 2): /study /attend /hangout /rest /club /parttime.
Thin handlers: rules in game/academy_rules.py, logic in services/academy_life.py (+ academy_actions.py), texts in
utils/content/academy_texts.py. Buttons reuse Part 1's owner-checked callbacks (ac:db: dashboard, ac:en: enrol)."""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton as B, Message, ReplyParameters

from database.models import User
from database.session import db
from handlers.academy_core import guard
from keyboards.menus import kb
from services import academy_actions as A, academy_life as life
from utils import gifs
from utils.telegram import safe_send

log = logging.getLogger(__name__)
router = Router()


def _result_kb(uid: int):
    return kb([[B(text="🎓 Dashboard", callback_data=f"ac:db:{uid}")]])          # Part 1 handler checks the owner


def _enroll_kb(uid: int):
    return kb([[B(text="📝 Enroll now", callback_data=f"ac:en:{uid}")]])         # Part 1 handler checks the owner


async def _say(m: Message, text: str, markup=None) -> None:
    """Reply to the command through safe_send (handles blocked users / refused messages, keeps forum topics)."""
    extra: dict = {"reply_parameters": ReplyParameters(message_id=m.message_id, allow_sending_without_reply=True)}
    if getattr(m, "is_topic_message", False) and m.message_thread_id:
        extra["message_thread_id"] = m.message_thread_id
    await safe_send(m.bot, m.chat.id, text, markup, **extra)


async def run_action(m: Message, db_user: User, key: str) -> None:
    """Shared body of the six commands. GameError (cooldown...) is left to the error middleware via `guard`."""
    uid = db_user.id
    try:
        async with db.tx() as s:
            res = await life.perform(s, uid, key)
    except A.ActionBlocked as e:
        await _say(m, e.text, _enroll_kb(uid) if e.enroll else None)
        return
    text, markup = A.render(res), _result_kb(uid)
    if res.gif:
        await gifs.send(m, res.gif, text, reply_markup=markup)      # falls back to plain text when GIFs are off / down
    else:
        await _say(m, text, markup)
    await life.announce(uid, res)                                    # events AFTER the commit and after the reply


@router.message(Command("study"))
@guard
async def study_cmd(m: Message, db_user: User):
    await run_action(m, db_user, "study")


@router.message(Command("attend"))
@guard
async def attend_cmd(m: Message, db_user: User):
    await run_action(m, db_user, "attend")


@router.message(Command("hangout"))
@guard
async def hangout_cmd(m: Message, db_user: User):
    await run_action(m, db_user, "hangout")


@router.message(Command("rest"))
@guard
async def rest_cmd(m: Message, db_user: User):
    await run_action(m, db_user, "rest")


@router.message(Command("club"))
@guard
async def club_cmd(m: Message, db_user: User):
    await run_action(m, db_user, "club")


@router.message(Command("parttime"))
@guard
async def parttime_cmd(m: Message, db_user: User):
    await run_action(m, db_user, "parttime")
