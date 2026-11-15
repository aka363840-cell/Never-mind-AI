"""Academy ticks for Part 6: season-open announcement, enrolment reminders, reminders for enrolled players, the end-of-day recap.

Every message follows the same pattern: decide WHO from the database, CLAIM the (kind, key, target) row in academy_notify_log, and
only the claim's winner sends (services/academy_notify.py). So a restart, a slow tick or an overlap can never send twice, and each
tick stops starting new sends when its time budget is gone (the next tick carries on). Nothing here can crash the loop: the registry
in academy_ticks catches errors, and every send is wrapped.

Rules in one place (numbers in game/academy_rules.py, Part 6):
* quiet hours 23:00-07:00 (ACADEMY_TZ) apply to season-open, enrolment reminders and the reminders for enrolled players, and to DM recaps (a recap that is
  due at night waits for 07:00 as long as its season day is still running)
* ONE reminder DM per user per local day (the `dm_daily` slot); the recap DM and the report card are not reminders
* /academyremind off = no Academy DMs of any kind
"""
from __future__ import annotations

import logging
from datetime import datetime

from aiogram.types import InlineKeyboardButton as B
from sqlalchemy import select

from database.models import (AcademyAdventureRun, AcademyAttempt, AcademyDaily, AcademyNotifyLog, AcademySeason, AcademyStudent,
                             AcademyUserPrefs, User, aware)
from database.session import db
from game import academy_rules as AR
from keyboards.menus import kb
from services import (academy_calendar as cal, academy_exams as ex, academy_notify as N, academy_remind_logic as RL,
                      academy_remind_views as V, academy_social as soc, academy_students as stu, academy_adventure_logic as AL)
from services.academy_ticks import tick
from utils.content import academy_remind_texts as T

log = logging.getLogger(__name__)
SCAN_BATCH = 200                      # enrolled players looked at per reminder tick run
DM_BATCH = 150                        # DMs prepared per tick run
_cursor: dict[int, int] = {}          # season id -> last student id scanned by the reminder tick (a tiny in-memory round-robin)
_CURSOR_KEEP = 3                      # only the newest few seasons are remembered (a new season every ~7 days: bounded for good)


def _set_cursor(season_id: int, value: int) -> None:
    _cursor[season_id] = value
    for old in sorted(_cursor)[:-_CURSOR_KEEP]:
        _cursor.pop(old, None)


def enroll_kb(season_id: int):
    """The Enroll button of the group messages. It has no owner id on purpose: it acts only for the person who presses it (their own id
    comes from Telegram, never from the data) by sending THEM the enrolment in a private message. See handlers/academy_remind.py."""
    return kb([[B(text=T.ENROLL_BTN, callback_data=f"acn:e:{season_id}")]])


async def _season(now: datetime) -> AcademySeason:
    async with db.tx() as s:
        return await cal.ensure_season(s, now)


async def _group_once(sender: N.Sender, kind: str, key: str, chat_id: int, season_id: int, text: str, markup, now: datetime) -> bool:
    """Claim, then send ONE group message. True if it was handed to Telegram."""
    if not await N.claim(kind, key, chat_id, season_id, now):
        return False
    res = await N.deliver_group(sender, chat_id, text, markup)
    if res == "error":
        await N.release(kind, key, chat_id)
        return False
    return res == "sent"


# ------------------------------------------------------------------ 1. season open (groups)
@tick(every=120, name="academy.remind_season_open")
async def season_open_tick(bot, now: datetime) -> None:
    season = await _season(now)
    start = aware(season.start_at)
    if RL.season_day(start, now) != 1 or RL.in_quiet_hours(now):
        return
    key = f"s{season.id}:open"
    groups = await N.active_groups(now)
    done = await N.sent_targets("season_open", key, groups)
    todo = [g for g in groups if g not in done]
    if not todo:
        return
    text = V.season_open_text(season.number, season.theme, RL.enroll_closes(start))
    markup = enroll_kb(season.id)
    sender = N.Sender(bot)
    for chat_id in todo:
        if sender.exhausted:
            break
        try:
            await _group_once(sender, "season_open", key, chat_id, season.id, text, markup, now)
        except Exception:
            log.exception("academy season-open: group %s failed", chat_id)


# ------------------------------------------------------------------ 2. enrolment reminders (groups every ~6 h, DMs once a day)
@tick(every=300, name="academy.remind_enroll")
async def enroll_reminder_tick(bot, now: datetime) -> None:
    season = await _season(now)
    start = aware(season.start_at)
    if not RL.enroll_open(start, now) or RL.in_quiet_hours(now):
        return
    sender = N.Sender(bot)
    await _enroll_groups(sender, season, start, now)
    await _enroll_dms(sender, season, start, now)


async def _enroll_groups(sender: N.Sender, season: AcademySeason, start: datetime, now: datetime) -> None:
    plan = RL.enroll_group_plan(start, now)
    if plan is None:
        return
    key = f"s{season.id}:{plan.suffix}"
    groups = await N.active_groups(now)
    done = await N.sent_targets(plan.kind, key, groups)
    todo = [g for g in groups if g not in done]
    if not todo:
        return
    text = V.enroll_group_text(plan, season.number, RL.enroll_closes(start), now, RL.enroll_window_index(start, now))
    markup = enroll_kb(season.id)
    for chat_id in todo:
        if sender.exhausted:
            break
        try:
            await _group_once(sender, plan.kind, key, chat_id, season.id, text, markup, now)
        except Exception:
            log.exception("academy enroll reminder: group %s failed", chat_id)


def _slot_taken(slot: str):
    """SQL: this user already has today's reminder-DM slot."""
    return (select(AcademyNotifyLog.id).where(AcademyNotifyLog.kind == AR.DM_REMINDER_SLOT, AcademyNotifyLog.key == slot,
                                              AcademyNotifyLog.target_id == User.id).correlate(User).exists())


async def _enroll_dms(sender: N.Sender, season: AcademySeason, start: datetime, now: datetime) -> None:
    day = RL.season_day(start, now)
    slot = RL.local_day_key(now)
    enrolled = select(AcademyStudent.id).where(AcademyStudent.season_id == season.id, AcademyStudent.user_id == User.id).correlate(User).exists()
    q = (select(User.id).outerjoin(AcademyUserPrefs, AcademyUserPrefs.user_id == User.id)
         .where(N.dm_eligible(now, require_recent=True), ~enrolled, ~_slot_taken(slot))
         .order_by(User.last_seen.desc()).limit(DM_BATCH))
    async with db.tx() as s:
        users = [int(u) for u in (await s.execute(q)).scalars()]
    if not users:
        return
    text = V.enroll_dm_text(day, season.number, RL.enroll_closes(start))
    markup = enroll_kb(season.id)
    for uid in users:
        if sender.exhausted:
            break
        entries = [(AR.DM_REMINDER_SLOT, slot, uid, season.id), ("enroll_dm", f"s{season.id}:d{day}", uid, season.id)]
        try:
            if not await N.claim_all(entries, now):
                continue
            if await N.deliver_dm(sender, uid, text, markup, now=now) == "error":
                for kind, key, target, _ in entries:
                    await N.release(kind, key, target)
        except Exception:
            log.exception("academy enroll reminder: DM to %s failed", uid)


# ------------------------------------------------------------------ 3. reminders for enrolled players (DM, one a day)
@tick(every=300, name="academy.remind_enrolled")
async def enrolled_reminder_tick(bot, now: datetime) -> None:
    season = await _season(now)
    start = aware(season.start_at)
    day = RL.season_day(start, now)
    if not 1 <= day <= AR.REMINDER_LAST_DAY or RL.in_quiet_hours(now):
        return
    slot = RL.local_day_key(now)
    q = (select(AcademyStudent).join(User, User.id == AcademyStudent.user_id)
         .outerjoin(AcademyUserPrefs, AcademyUserPrefs.user_id == User.id)
         .where(AcademyStudent.season_id == season.id, AcademyStudent.id > _cursor.get(season.id, 0),
                N.dm_eligible(now, require_recent=False), ~_slot_taken(slot))
         .order_by(AcademyStudent.id).limit(SCAN_BATCH))
    async with db.tx() as s:
        students = list((await s.execute(q)).scalars())
        if not students:
            _set_cursor(season.id, 0)                        # reached the end: start again from the first student next time
            return
        ids = [st.id for st in students]
        uids = [st.user_id for st in students]
        done: dict[int, set[str]] = {}
        for sid, kind in (await s.execute(select(AcademyAttempt.student_id, AcademyAttempt.exam_kind)
                                          .where(AcademyAttempt.student_id.in_(ids), AcademyAttempt.state.in_(("done", "expired"))))).all():
            done.setdefault(int(sid), set()).add(kind)
        adv: dict[int, int] = {}
        for sid, ending, at in (await s.execute(select(AcademyAdventureRun.student_id, AcademyAdventureRun.ending, AcademyAdventureRun.updated_at)
                                                .where(AcademyAdventureRun.student_id.in_(ids), AcademyAdventureRun.finished.is_(True)))).all():
            if ending and ending not in AL.ENDED_NO_REWARD and RL.season_day(start, aware(at)) == day:
                adv[int(sid)] = adv.get(int(sid), 0) + 1
        sent: dict[int, set[tuple[str, str]]] = {}
        for uid, kind, key in (await s.execute(select(AcademyNotifyLog.target_id, AcademyNotifyLog.kind, AcademyNotifyLog.key)
                                               .where(AcademyNotifyLog.season_id == season.id, AcademyNotifyLog.target_id.in_(uids),
                                                      AcademyNotifyLog.kind.in_([RL.log_kind(k) for k in AR.REMINDER_KINDS])))).all():
            sent.setdefault(int(uid), set()).add((kind, key))
    sender = N.Sender(bot)
    last = _cursor.get(season.id, 0)
    for st in students:
        if sender.exhausted:
            break
        last = st.id
        try:
            state = RL.StudentState(season.id, start, stu.current_energy(st, now), st.energy, aware(st.energy_at),
                                    frozenset(done.get(st.id, ())), adv.get(st.id, 0))
            rem = RL.pick_reminder(state, now, frozenset(sent.get(st.user_id, ())))
            if rem is None:
                continue
            entries = [(AR.DM_REMINDER_SLOT, slot, st.user_id, season.id), (RL.log_kind(rem.kind), rem.key, st.user_id, season.id)]
            if not await N.claim_all(entries, now):
                continue
            if await N.deliver_dm(sender, st.user_id, V.reminder_dm_text(rem.text), now=now) == "error":
                for kind, key, target, _ in entries:
                    await N.release(kind, key, target)
        except Exception:
            log.exception("academy reminder: student %s failed", st.id)
    else:
        last = students[-1].id                               # whole batch handled
    _set_cursor(season.id, last)


# ------------------------------------------------------------------ 4. end-of-day recap (groups + DMs)
@tick(every=300, name="academy.recap")
async def recap_tick(bot, now: datetime) -> None:
    season = await _season(now)
    start = aware(season.start_at)
    day = RL.recap_due(start, now)
    if day is None:
        return
    key = f"s{season.id}:d{day}"
    sender = N.Sender(bot)
    await _recap_groups(sender, season, day, key, now)
    if not RL.in_quiet_hours(now):
        await _recap_dms(sender, season, start, day, key, now)


async def _recap_groups(sender: N.Sender, season: AcademySeason, day: int, key: str, now: datetime) -> None:
    groups = await N.active_groups(now)
    done = await N.sent_targets("recap_g", key, groups)
    todo = [g for g in groups if g not in done]
    if not todo:
        return
    async with db.tx() as s:
        results = await ex.season_results(s, season.id, now)
        progress: list[tuple[int, str, int, int]] = []
        if not results:
            rows = (await s.execute(select(AcademyStudent.user_id, User.first_name, AcademyStudent.knowledge, AcademyStudent.charisma,
                                           AcademyStudent.fitness, AcademyStudent.creativity)
                                    .join(User, User.id == AcademyStudent.user_id)
                                    .where(AcademyStudent.season_id == season.id, User.is_flagged.is_(False), User.is_banned.is_(False)))).all()
            progress = [(int(u), n or "Player", int(k), int(k + c + f + cr)) for u, n, k, c, f, cr in rows]
        clubs = await soc.standings(s, season.id)
        members = {g: await ex.chat_user_ids(s, g) for g in todo}
    for chat_id in todo:
        if sender.exhausted:
            break
        try:
            text = V.recap_group_text(day, V.top_student(results, progress, members.get(chat_id)), clubs)
            await _group_once(sender, "recap_g", key, chat_id, season.id, text, None, now)
        except Exception:
            log.exception("academy recap: group %s failed", chat_id)


async def _recap_dms(sender: N.Sender, season: AcademySeason, start: datetime, day: int, key: str, now: datetime) -> None:
    done_q = select(AcademyNotifyLog.id).where(AcademyNotifyLog.kind == "recap_dm", AcademyNotifyLog.key == key,
                                               AcademyNotifyLog.target_id == User.id).correlate(User).exists()
    q = (select(AcademyStudent).join(User, User.id == AcademyStudent.user_id)
         .outerjoin(AcademyUserPrefs, AcademyUserPrefs.user_id == User.id)
         .where(AcademyStudent.season_id == season.id, N.dm_eligible(now, require_recent=False), ~done_q)
         .order_by(AcademyStudent.id).limit(DM_BATCH))
    async with db.tx() as s:
        students = list((await s.execute(q)).scalars())
        if not students:
            return
        used = {int(sid): int(e or 0) for sid, e in (await s.execute(
            select(AcademyDaily.student_id, AcademyDaily.energy_spent)
            .where(AcademyDaily.day == day, AcademyDaily.student_id.in_([st.id for st in students])))).all()}
        results = await ex.season_results(s, season.id, now)
    by_user = {r.user_id: r for r in results}
    for st in students:
        if sender.exhausted:
            break
        try:
            r = by_user.get(st.user_id)
            text = V.recap_dm_text(day, energy_used=used.get(st.id, 0), energy_now=stu.current_energy(st, now), stats=stu.stats_of(st),
                                   rank=r.rank if r else None, total=len(results), gpa=r.gpa if r else None, provisional=bool(r and r.provisional))
            if not await N.claim("recap_dm", key, st.user_id, season.id, now):
                continue
            if await N.deliver_dm(sender, st.user_id, text, now=now) == "error":
                await N.release("recap_dm", key, st.user_id)
        except Exception:
            log.exception("academy recap: DM to student %s failed", st.id)


# ------------------------------------------------------------------ housekeeping
@tick(every=6 * 3600, name="academy.notify_purge")
async def purge_tick(bot, now: datetime) -> None:
    n = await N.purge_old(now)
    if n:
        log.info("academy notify log: purged %s old rows", n)
