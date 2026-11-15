"""Academy Life, player commands (Part 1): /enroll, /academy, /schedule.
Thin handlers - rules in game/academy_rules.py, logic in services/academy_*.py, texts in utils/content/academy_texts.py."""
from __future__ import annotations

import functools
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message

from database.models import User, utcnow
from database.session import db
from game import academy_rules as AR
from keyboards.menus import kb
from services import academy_calendar as cal, academy_events as ev, academy_students as stu, academy_views as views
from services.errors import GameError
from utils import gifs
from utils.content import academy_texts as T
from utils.fmt import esc
from utils.telegram import safe_answer, safe_edit

log = logging.getLogger(__name__)
router = Router()


def guard(fn):
    """Kill switch + never crash. GameError is re-raised on purpose: ErrorMiddleware turns it into a friendly reply."""
    @functools.wraps(fn)
    async def wrapper(event, *a, **kw):
        try:
            if not AR.enabled():
                if isinstance(event, CallbackQuery):
                    await safe_answer(event, T.DISABLED, True)
                else:
                    await event.reply(T.DISABLED)
                return
            return await fn(event, *a, **kw)
        except GameError:
            raise
        except (TelegramForbiddenError, TelegramBadRequest):
            log.info("academy: telegram refused a message", exc_info=True)
        except Exception:
            log.exception("academy handler %s failed", fn.__name__)
            try:
                if isinstance(event, CallbackQuery):
                    await safe_answer(event, "Something went wrong. Please try again.", True)
                else:
                    await event.reply("⚠️ Something went wrong. Please try again in a moment.")
            except Exception:
                pass
    return wrapper


# ------------------------------------------------------------------ keyboards (callback data < 64 bytes, always carries the owner id)
def _stream_kb(uid: int):
    keys = list(AR.STREAMS)
    btns = [B(text=f"{AR.STREAMS[k].emoji} {AR.STREAMS[k].name}", callback_data=f"ac:st:{uid}:{k}") for k in keys]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([B(text="✖️ Cancel", callback_data=f"ac:no:{uid}")])
    return kb(rows)


def _club_kb(uid: int, stream: str):
    btns = [B(text=f"{c.emoji} {c.name}", callback_data=f"ac:cl:{uid}:{stream}:{k}") for k, c in AR.CLUBS.items()]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([B(text="⬅️ Back", callback_data=f"ac:en:{uid}"), B(text="✖️ Cancel", callback_data=f"ac:no:{uid}")])
    return kb(rows)


def _dash_kb(uid: int, enrolled: bool, can_enroll: bool):
    row = [B(text="📅 Schedule", callback_data=f"ac:sc:{uid}"), B(text="🔄 Refresh", callback_data=f"ac:db:{uid}")]
    rows = [row]
    if not enrolled and can_enroll:
        rows.insert(0, [B(text="📝 Enroll now", callback_data=f"ac:en:{uid}")])
    return kb(rows)


def _owner(cb: CallbackQuery, uid: int) -> bool:
    return cb.from_user.id == uid


async def _deny(cb: CallbackQuery) -> None:
    await safe_answer(cb, "🚫 This isn't your Academy menu. Use /academy for your own.", True)


def _uid(part: str) -> int:
    try:
        return int(part)
    except ValueError:
        return 0


# ------------------------------------------------------------------ screens
async def _load(uid: int):
    now = utcnow()
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        st = await stu.get_student(s, uid, season.id)
    return season, st, now


async def _dashboard(uid: int):
    season, st, now = await _load(uid)
    win = cal.enroll_window(season.start_at, now)
    return views.dashboard(season, st, now), _dash_kb(uid, st is not None, win.open)


async def _enroll_intro(uid: int):
    """(text, keyboard) for the first enrolment step, or (text, None) if enrolling is not possible now."""
    season, st, now = await _load(uid)
    if st is not None:
        return "🎓 You are already enrolled this season. Here is your dashboard:\n\n" + views.dashboard(season, st, now), None
    win = cal.enroll_window(season.start_at, now)
    if not win.open:
        ph = cal.phase_at(season.start_at, now)
        extra = ""
        if ph.key == AR.BREAK_KEY:
            extra = f"\nNext season opens <b>{cal.fmt_when(cal.next_phase(season.start_at, now).starts_at)}</b>."
        return T.CLOSED_FOR_SEASON + extra, None
    lines = [f"📝 <b>Academy enrolment</b> — {views.season_title(season)}", "", "Step 1 of 2: choose your <b>stream</b>.", ""]
    lines += [f"{s.emoji} <b>{esc(s.name)}</b> — {esc(s.blurb)} ({_bonus(s.bonus)})" for s in AR.STREAMS.values()]
    if win.late:
        lines += ["", T.LATE_JOIN_NOTE]
    return "\n".join(lines), _stream_kb(uid)


def _bonus(b: dict[str, int]) -> str:
    return ", ".join(f"+{v} {k.capitalize()}" for k, v in b.items())


# ------------------------------------------------------------------ commands
@router.message(Command("enroll"))
@guard
async def enroll_cmd(m: Message, db_user: User):
    text, markup = await _enroll_intro(db_user.id)
    await m.reply(text, reply_markup=markup)


@router.message(Command("academy", "school"))
@guard
async def academy_cmd(m: Message, db_user: User):
    text, markup = await _dashboard(db_user.id)
    await m.reply(text, reply_markup=markup)


@router.message(Command("schedule", "timetable"))
@guard
async def schedule_cmd(m: Message, db_user: User):
    season, _, now = await _load(db_user.id)
    await m.reply(views.schedule(season, now), reply_markup=kb([[B(text="🎓 My dashboard", callback_data=f"ac:db:{db_user.id}")]]))


# ------------------------------------------------------------------ buttons
@router.callback_query(F.data.startswith("ac:en:"))
@guard
async def cb_enroll_start(cb: CallbackQuery):
    uid = _uid(cb.data.split(":")[2])
    if not _owner(cb, uid):
        return await _deny(cb)
    text, markup = await _enroll_intro(uid)
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.callback_query(F.data.startswith("ac:st:"))
@guard
async def cb_pick_stream(cb: CallbackQuery):
    _, _, u, stream = cb.data.split(":")[:4]
    uid = _uid(u)
    if not _owner(cb, uid):
        return await _deny(cb)
    if stream not in AR.STREAMS:
        return await safe_answer(cb, "❌ Unknown stream.", True)
    s = AR.STREAMS[stream]
    await safe_edit(cb, f"{s.emoji} <b>{esc(s.name)}</b> — nice choice!\n\nStep 2 of 2: join a <b>club</b>.\n\n"
                        + "\n".join(f"{c.emoji} <b>{esc(c.name)}</b> — {esc(c.blurb)}" for c in AR.CLUBS.values()),
                    _club_kb(uid, stream))
    await safe_answer(cb)


@router.callback_query(F.data.startswith("ac:cl:"))
@guard
async def cb_pick_club(cb: CallbackQuery):
    _, _, u, stream, club = cb.data.split(":")[:5]
    uid = _uid(u)
    if not _owner(cb, uid):
        return await _deny(cb)
    now = utcnow()
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        st, created = await stu.enroll(s, uid, season, stream, club, now)
    if created:
        await ev.emit(ev.ENROLLED, uid, season_id=season.id, stream=st.stream, club=st.club, late_join=st.late_join)
    head = ("🎉 <b>Welcome to the Academy!</b> You are in." if created
            else "🎓 You were already enrolled, nothing changed.")
    await safe_edit(cb, head + "\n\n" + views.dashboard(season, st, now), _dash_kb(uid, True, False))
    await safe_answer(cb)
    if created:
        await gifs.bonus(cb.message, "wave")


@router.callback_query(F.data.startswith("ac:db:"))
@guard
async def cb_dashboard(cb: CallbackQuery):
    uid = _uid(cb.data.split(":")[2])
    if not _owner(cb, uid):
        return await _deny(cb)
    text, markup = await _dashboard(uid)
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.callback_query(F.data.startswith("ac:sc:"))
@guard
async def cb_schedule(cb: CallbackQuery):
    uid = _uid(cb.data.split(":")[2])
    if not _owner(cb, uid):
        return await _deny(cb)
    season, _, now = await _load(uid)
    await safe_edit(cb, views.schedule(season, now), kb([[B(text="🎓 My dashboard", callback_data=f"ac:db:{uid}")]]))
    await safe_answer(cb)


@router.callback_query(F.data.startswith("ac:no:"))
@guard
async def cb_cancel(cb: CallbackQuery):
    uid = _uid(cb.data.split(":")[2])
    if not _owner(cb, uid):
        return await _deny(cb)
    await safe_edit(cb, "✖️ Enrolment cancelled. Use /enroll whenever you are ready.")
    await safe_answer(cb)
