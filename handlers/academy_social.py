"""Academy social (Part 4): /rival /rivalduel /crush /crushoff /campuscouple /campusdate /clubs /buddy. Thin handlers: rules in
game/academy_rules.py, logic in services/academy_social.py + academy_love.py (+ _logic / _views), texts in
utils/content/academy_social_texts.py.

Callback data (all < 64 bytes, all carry the owner id, all checked): acr:c:<uid>:<duel>:<n>:<idx> duel card · acr:d:<uid> start /
resume the duel · acr:r:<uid> refresh /rival · acb:p:<uid> show my battle card · acb:c:<uid>:<battle>:<idx> play it ·
acb:r:<uid> refresh /clubs · acp:d:<uid> campus date · acu:y|n:<uid>:<request> buddy answer (uid = the player who must answer).

Crush privacy: in a group the command message is deleted when the bot may, the answer goes to a private chat only, and the group
sees nothing but the single announcement of a MUTUAL couple.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message, ReplyParameters

from database.models import User
from database.session import db
from game import academy_rules as AR
from handlers.academy_core import guard
from keyboards.menus import kb
from services import (academy_calendar as cal, academy_love as love, academy_social as soc, academy_social_views as V,
                      academy_views as views)
from services.errors import GameError
from utils import gifs
from utils.content import academy_social_texts as T
from utils.fmt import esc, mention
from utils.targets import resolve_target
from utils.telegram import safe_answer, safe_edit, safe_send

log = logging.getLogger(__name__)
router = Router()


# ------------------------------------------------------------------ helpers
def _is_group(m: Message) -> bool:
    return m.chat.type in ("group", "supergroup")


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


def _text_of(e: Exception) -> str:
    return str(getattr(e, "text", e))


async def _target(m: Message, command: CommandObject) -> tuple[int | None, bool]:
    """(target id | None, target is a bot). Reply > text mention > @username > numeric id; a bot is never a valid target."""
    rep = m.reply_to_message
    if rep and rep.from_user and rep.from_user.is_bot:
        return rep.from_user.id, True
    uid, _ = await resolve_target(m, (command.args or "").split())
    if uid is not None and uid == m.bot.id:
        return uid, True
    return uid, False


async def _private(m: Message, uid: int, text: str) -> None:
    """Answer about a SECRET thing: in a private chat reply normally; in a group send it as a DM only (the group learns nothing)."""
    if not _is_group(m):
        return await _say(m, text)
    if await safe_send(m.bot, uid, text) is None:
        await _say(m, T.CRUSH_GROUP_NUDGE)


async def _hide(m: Message) -> None:
    """Best effort: remove the group command message so the target is not visible there (needs admin rights; failures are fine)."""
    if _is_group(m):
        try:
            await m.delete()
        except Exception:
            pass


async def _owner_or_deny(cb: CallbackQuery, uid: int) -> bool:
    if cb.from_user.id != uid:
        await safe_answer(cb, T.NOT_YOURS, True)
        return False
    return True


# ================================================================== rivals
def _duel_line(d: dict, uid: int) -> str:
    du = d["duel"]
    if du is None:
        return f"⚔️ No duel yet. Start it with /rivalduel (open Day {AR.RIVAL_PAIR_FROM_DAY} to {AR.RIVAL_DUEL_LAST_DAY})."
    if du.state == "done":
        mine = "a" if uid == du.user_a else "b"
        what = ("a draw" if du.result == "tie" else "no result" if du.result in (None, "none")
                else "you won! 🏆" if du.result == mine else "you lost this one")
        return f"🏁 Duel finished: {what}."
    if d["my_rounds"] >= AR.RIVAL_DUEL_ROUNDS:
        return T.DUEL_ALREADY
    return f"⚔️ Duel open: {d['my_rounds']}/{AR.RIVAL_DUEL_ROUNDS} cards played. /rivalduel"


def _rival_kb(uid: int):
    return kb([[B(text="⚔️ Rival duel", callback_data=f"acr:d:{uid}"), B(text="🔄 Refresh", callback_data=f"acr:r:{uid}")]])


async def _rival_screen(uid: int):
    async with db.tx() as s:
        d = await soc.duel_status(s, uid)
    if d["rival"] is None:
        return T.RIVAL_NONE + "\n\n" + T.BADGE_LINE.format(n=d["badge_points"]), None
    return (V.rival_text(name=d["name"], bot=d["bot"], stream=d["stream"], knowledge=d["knowledge"], duel=_duel_line(d, uid),
                         badge_points=d["badge_points"]), _rival_kb(uid))


@router.message(Command("rival"))
@guard
async def rival_cmd(m: Message, db_user: User):
    try:
        text, markup = await _rival_screen(db_user.id)
    except soc.SocialBlocked as e:
        return await _say(m, e.text)
    await _say(m, text, markup)


def _duel_text(scr: soc.DuelScreen) -> str:
    if scr.status == "done":
        pre = V.round_line(**{k: scr.last[k] for k in ("success", "points", "line")}) + "\n\n" if scr.last else ""
        return pre + scr.result_text
    if scr.status == "wait":
        pre = V.round_line(**{k: scr.last[k] for k in ("success", "points", "line")}) + "\n\n" if scr.last else ""
        return pre + T.DUEL_WAIT.format(total=scr.my_total)
    head = T.DUEL_HEAD.format(name=esc(scr.rival_name), n=scr.n, total=scr.total)
    pre = V.round_line(**{k: scr.last[k] for k in ("success", "points", "line")}) + "\n\n" if scr.last else ""
    return (pre + head + "\n\n" + V.card_block(scr.card_key) + "\n\n<i>Pick a move. Each button uses a different stat and risk. "
            f"Your total so far: {scr.my_total}</i>")


def _duel_kb(scr: soc.DuelScreen, uid: int):
    if scr.status == "question":
        return kb([[B(text=lab, callback_data=f"acr:c:{uid}:{scr.duel_id}:{scr.n}:{i}")] for i, lab in enumerate(V.button_labels(scr.card_key))])
    return kb([[B(text="🤝 My rival", callback_data=f"acr:r:{uid}")]])


@router.message(Command("rivalduel"))
@guard
async def rivalduel_cmd(m: Message, db_user: User):
    uid = db_user.id
    try:
        async with db.tx() as s:
            scr = await soc.open_duel(s, uid, chat_id=m.chat.id if _is_group(m) else None)
    except soc.SocialBlocked as e:
        return await _say(m, e.text, kb([[B(text="📝 Enroll now", callback_data=f"ac:en:{uid}")]]) if e.enroll else None)
    await _say(m, _duel_text(scr), _duel_kb(scr, uid))
    await soc.deliver(m.bot, scr.out, skip_chat=m.chat.id)


async def _run_duel(cb: CallbackQuery, uid: int, fn, *args) -> None:
    try:
        async with db.tx() as s:
            scr = await fn(s, uid, *args)
    except GameError as e:
        return await safe_answer(cb, _text_of(e)[:190], True)
    await safe_edit(cb, _duel_text(scr), _duel_kb(scr, uid))
    await safe_answer(cb)
    chat = cb.message.chat.id if cb.message else None
    if any(k == soc.RIVAL_WIN and u == uid for k, u, _d in scr.out.events) and cb.message is not None:
        await gifs.bonus(cb.message, AR.SOCIAL_GIFS["rival"])
    await soc.deliver(cb.bot, scr.out, skip_chat=chat)


@router.callback_query(F.data.startswith("acr:c:"))
@guard
async def cb_duel_answer(cb: CallbackQuery):
    p = (cb.data or "").split(":")
    if len(p) != 6:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if await _owner_or_deny(cb, uid):
        await _run_duel(cb, uid, soc.duel_answer, _uid(p[3]), _uid(p[4]), _uid(p[5]))


@router.callback_query(F.data.startswith("acr:d:"))
@guard
async def cb_duel_open(cb: CallbackQuery):
    uid = _uid((cb.data or "").split(":")[2])
    if not await _owner_or_deny(cb, uid):
        return
    chat = cb.message.chat.id if (cb.message and cb.message.chat.type in ("group", "supergroup")) else None
    await _run_duel(cb, uid, lambda s, u: soc.open_duel(s, u, chat_id=chat))


@router.callback_query(F.data.startswith("acr:r:"))
@guard
async def cb_rival_refresh(cb: CallbackQuery):
    uid = _uid((cb.data or "").split(":")[2])
    if not await _owner_or_deny(cb, uid):
        return
    try:
        text, markup = await _rival_screen(uid)
    except soc.SocialBlocked as e:
        return await safe_answer(cb, e.text, True)
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


# ================================================================== crushes (secret)
@router.message(Command("crush"))
@guard
async def crush_cmd(m: Message, command: CommandObject, db_user: User):
    uid = db_user.id
    target, is_bot = await _target(m, command)
    if target is None:
        return await _private(m, uid, T.NO_TARGET)
    try:
        async with db.tx() as s:
            res = await love.send_crush(s, uid, target, chat_id=m.chat.id if _is_group(m) else None, target_is_bot=is_bot)
    except soc.SocialBlocked as e:
        await _hide(m)
        return await _private(m, uid, e.text)
    await _hide(m)
    if res.status == "saved":
        await _private(m, uid, res.text)               # the target is never told
    await soc.deliver(m.bot, res.out)                 # a mutual reveal: DMs + blush GIF + ONE group message + the event


@router.message(Command("crushoff"))
@guard
async def crushoff_cmd(m: Message, db_user: User):
    async with db.tx() as s:
        removed = await love.crush_off(s, db_user.id)
    await _hide(m)
    await _private(m, db_user.id, T.CRUSH_OFF if removed else T.CRUSH_OFF_NONE)


# ================================================================== campus couple
def _couple_kb(uid: int):
    return kb([[B(text="💞 Campus date", callback_data=f"acp:d:{uid}")]])


@router.message(Command("campuscouple"))
@guard
async def campuscouple_cmd(m: Message, db_user: User):
    uid = db_user.id
    try:
        async with db.tx() as s:
            d = await love.couple_status(s, uid, private=not _is_group(m))
    except soc.SocialBlocked as e:
        return await _say(m, e.text, kb([[B(text="📝 Enroll now", callback_data=f"ac:en:{uid}")]]) if e.enroll else None)
    if d["couple"] is None:
        text = T.COUPLE_NONE + (("\n\n" + T.CRUSH_PENDING.format(name=esc(d["pending"]))) if d["pending"] else "")
        return await _say(m, text)
    await _say(m, V.couple_text(partner=d["partner"], points=d["points"], dates=d["dates"], done_today=d["done_today"],
                                open_today=AR.crush_open(d["day"])), _couple_kb(uid))


@router.message(Command("campusdate"))
@guard
async def campusdate_cmd(m: Message, db_user: User):
    uid = db_user.id
    try:
        async with db.tx() as s:
            res = await love.campus_date(s, uid)
    except soc.SocialBlocked as e:
        return await _say(m, e.text, kb([[B(text="📝 Enroll now", callback_data=f"ac:en:{uid}")]]) if e.enroll else None)
    if res.gif:
        await gifs.send(m, res.gif, res.text)
    else:
        await _say(m, res.text)
    await soc.deliver(m.bot, res.out)


@router.callback_query(F.data.startswith("acp:d:"))
@guard
async def cb_date(cb: CallbackQuery):
    uid = _uid((cb.data or "").split(":")[2])
    if not await _owner_or_deny(cb, uid):
        return
    try:
        async with db.tx() as s:
            res = await love.campus_date(s, uid)
    except GameError as e:
        return await safe_answer(cb, _text_of(e)[:190], True)
    await safe_edit(cb, res.text, _couple_kb(uid))
    await safe_answer(cb)
    await soc.deliver(cb.bot, res.out)


# ================================================================== clubs
async def _clubs_screen(uid: int):
    async with db.tx() as s:
        v = await soc.clubs_view(s, uid)
    season, ph = v["season"], v["phase"]
    if v["battle"] is None:
        line = T.CLUBS_BATTLE_SOON.format(day=AR.BATTLE_FROM_DAY) if ph.day < AR.BATTLE_FROM_DAY else T.CLUBS_BATTLE_DONE
    elif v["finished"]:
        line = T.CLUBS_BATTLE_DONE
    else:
        line = T.CLUBS_BATTLE_OPEN.format(when=cal.fmt_when(v["closes_at"])) + (" ✅ You played." if v["played"] else "")
    text = V.clubs_text(title=views.season_title(season), rows=v["rows"], winner=v["winner"], battle_line=line, finished=v["finished"])
    row = [B(text=T.CLUBS_PLAY_BTN, callback_data=f"acb:p:{uid}")] if v["can_play"] else []
    row.append(B(text="🔄 Refresh", callback_data=f"acb:r:{uid}"))
    return text, kb([row])


def _battle_kb(uid: int, battle_id: int, card_key: str):
    return kb([[B(text=lab, callback_data=f"acb:c:{uid}:{battle_id}:{i}")] for i, lab in enumerate(V.button_labels(card_key))])


@router.message(Command("clubs"))
@guard
async def clubs_cmd(m: Message, command: CommandObject, db_user: User):
    uid = db_user.id
    if (command.args or "").strip().lower() == "play":
        try:
            async with db.tx() as s:
                b, st = await soc.battle_card_for(s, uid)
        except soc.SocialBlocked as e:
            return await _say(m, e.text)
        return await _say(m, V.battle_card_text(st.club, b.card_key), _battle_kb(uid, b.id, b.card_key))
    text, markup = await _clubs_screen(uid)
    await _say(m, text, markup)


@router.callback_query(F.data.startswith("acb:r:"))
@guard
async def cb_clubs_refresh(cb: CallbackQuery):
    uid = _uid((cb.data or "").split(":")[2])
    if not await _owner_or_deny(cb, uid):
        return
    text, markup = await _clubs_screen(uid)
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.callback_query(F.data.startswith("acb:p:"))
@guard
async def cb_battle_card(cb: CallbackQuery):
    uid = _uid((cb.data or "").split(":")[2])
    if not await _owner_or_deny(cb, uid):
        return
    try:
        async with db.tx() as s:
            b, st = await soc.battle_card_for(s, uid)
    except GameError as e:
        return await safe_answer(cb, _text_of(e)[:190], True)
    await safe_edit(cb, V.battle_card_text(st.club, b.card_key), _battle_kb(uid, b.id, b.card_key))
    await safe_answer(cb)


@router.callback_query(F.data.startswith("acb:c:"))
@guard
async def cb_battle_play(cb: CallbackQuery):
    p = (cb.data or "").split(":")
    if len(p) != 5:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if not await _owner_or_deny(cb, uid):
        return
    try:
        async with db.tx() as s:
            res = await soc.play_battle(s, uid, _uid(p[3]), _uid(p[4]))
    except GameError as e:
        return await safe_answer(cb, _text_of(e)[:190], True)
    await safe_edit(cb, V.battle_result_text(res.club, res.line, res.success, res.points), kb([[B(text="🏟 Standings", callback_data=f"acb:r:{uid}")]]))
    await safe_answer(cb)


# ================================================================== study buddies
@router.message(Command("buddy"))
@guard
async def buddy_cmd(m: Message, command: CommandObject, db_user: User):
    uid = db_user.id
    arg = (command.args or "").strip().lower()
    if arg in ("off", "stop", "end", "cancel"):
        async with db.tx() as s:
            kind = await love.end_buddy(s, uid)
        return await _say(m, T.BUDDY_ENDED if kind else T.BUDDY_NOTHING)
    rep = m.reply_to_message
    if not arg and not (rep and rep.from_user):
        try:
            async with db.tx() as s:
                d = await love.buddy_status(s, uid)
        except soc.SocialBlocked as e:
            return await _say(m, e.text)
        if d["kind"] == "active":
            return await _say(m, V.buddy_text(partner=d["partner"], claimed_today=d["claimed_today"]))
        if d["kind"] == "pending":
            return await _say(m, T.BUDDY_PENDING_OUT.format(name=esc(d["partner"])))
        return await _say(m, T.BUDDY_NONE.format(k=AR.BUDDY_KNOWLEDGE_BONUS))
    target, is_bot = await _target(m, command)
    if target is None:
        return await _say(m, T.NO_TARGET)
    try:
        async with db.tx() as s:
            br = await love.request_buddy(s, uid, target, chat_id=m.chat.id if _is_group(m) else None, target_is_bot=is_bot)
    except soc.SocialBlocked as e:
        return await _say(m, e.text)
    if br.status == "active":
        return await _say(m, T.BUDDY_ACCEPTED.format(a=esc(br.name_me), b=esc(br.name_target)))
    markup = kb([[B(text="✅ Accept", callback_data=f"acu:y:{target}:{br.request_id}"),
                  B(text="✖️ Decline", callback_data=f"acu:n:{target}:{br.request_id}")]])
    text = T.BUDDY_REQUEST.format(who=esc(br.name_me), name=mention(target, br.name_target), k=AR.BUDDY_KNOWLEDGE_BONUS)
    if _is_group(m):
        return await _say(m, text, markup)
    if await safe_send(m.bot, target, text, markup) is None:          # they never opened a chat with the bot: cancel the request
        async with db.tx() as s:
            await love.end_buddy(s, uid)
        return await _say(m, T.BUDDY_CANT_DM)
    await _say(m, T.BUDDY_SENT.format(name=esc(br.name_target)))


async def _buddy_answer(cb: CallbackQuery, accept: bool) -> None:
    p = (cb.data or "").split(":")
    if len(p) != 4:
        return await safe_answer(cb, T.STALE, True)
    uid = _uid(p[2])
    if not await _owner_or_deny(cb, uid):
        return
    try:
        async with db.tx() as s:
            status, a, b = await love.answer_buddy(s, uid, _uid(p[3]), accept)
    except GameError as e:
        return await safe_answer(cb, _text_of(e)[:190], True)
    await safe_edit(cb, T.BUDDY_ACCEPTED.format(a=esc(a), b=esc(b)) if status == "active" else T.BUDDY_DECLINED)
    await safe_answer(cb)


@router.callback_query(F.data.startswith("acu:y:"))
@guard
async def cb_buddy_yes(cb: CallbackQuery):
    await _buddy_answer(cb, True)


@router.callback_query(F.data.startswith("acu:n:"))
@guard
async def cb_buddy_no(cb: CallbackQuery):
    await _buddy_answer(cb, False)
