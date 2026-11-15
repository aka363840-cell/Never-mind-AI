"""Academy notifications, DATABASE + sending half (Part 6).

* `claim()` is the 'send once' lock: it INSERTs an academy_notify_log row (UNIQUE kind+key+target) and only the caller whose insert
  won may send. Claim first, send second: a restart, a retry or two overlapping ticks can never send the same message twice.
  If the send fails with an exception (network error, nothing delivered) the caller `release()`s the claim so the next tick retries.
* `Sender` is the ONLY way Part 6 sends: every message goes through utils.telegram.safe_send behind one global rate limit
  (AR.NOTIFY_RATE_PER_SEC), in chunks with small sleeps, with a time budget per tick (the rest goes out on the next tick).
* Users who blocked the bot are marked unreachable (academy_user_prefs); groups that keep refusing are skipped (academy_group_seen.fails).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable

from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import and_, delete, or_, select, update

from database.models import AcademyGroupSeen, AcademyNotifyLog, AcademyUserPrefs, GroupSetting, User, aware, utcnow
from database.session import db, insert_ignore
from game import academy_rules as AR
from services import academy_remind_logic as RL
from utils.telegram import safe_send, split_text

log = logging.getLogger(__name__)
LIMITER = RL.Limiter()                      # ONE limiter for everything Part 6 sends


# ------------------------------------------------------------------ the 'send once' log
class _Dup(Exception):
    pass


async def claim(kind: str, key: str, target_id: int, season_id: int | None = None, now: datetime | None = None) -> bool:
    """True if THIS call recorded (kind, key, target) and so owns the message; False if it was already recorded."""
    async with db.tx() as s:
        return await insert_ignore(s, AcademyNotifyLog, dict(kind=kind, key=key, season_id=season_id, target_id=int(target_id),
                                                               sent_at=aware(now) if now else utcnow()))


async def claim_all(entries: Iterable[tuple[str, str, int, int | None]], now: datetime | None = None) -> bool:
    """All-or-nothing claim of several rows (e.g. the daily DM slot AND the reminder itself). False = nothing was recorded."""
    when = aware(now) if now else utcnow()
    try:
        async with db.tx() as s:
            for kind, key, target, sid in entries:
                if not await insert_ignore(s, AcademyNotifyLog, dict(kind=kind, key=key, season_id=sid, target_id=int(target), sent_at=when)):
                    raise _Dup()
    except _Dup:
        return False
    return True


async def release(kind: str, key: str, target_id: int) -> None:
    """Give a claim back (only after a send that did NOT reach Telegram, e.g. a network error) so a later tick may retry."""
    async with db.tx() as s:
        await s.execute(delete(AcademyNotifyLog).where(AcademyNotifyLog.kind == kind, AcademyNotifyLog.key == key,
                                                       AcademyNotifyLog.target_id == int(target_id)))


async def was_sent(kind: str, key: str, target_id: int) -> bool:
    async with db.tx() as s:
        return (await s.execute(select(AcademyNotifyLog.id).where(AcademyNotifyLog.kind == kind, AcademyNotifyLog.key == key,
                                                                  AcademyNotifyLog.target_id == int(target_id)))).first() is not None


async def sent_targets(kind: str, key: str, targets: Iterable[int]) -> set[int]:
    """Which of `targets` already have (kind, key) in the log."""
    ids = list(dict.fromkeys(int(t) for t in targets))
    out: set[int] = set()
    async with db.tx() as s:
        for i in range(0, len(ids), 500):
            rows = await s.execute(select(AcademyNotifyLog.target_id).where(
                AcademyNotifyLog.kind == kind, AcademyNotifyLog.key == key, AcademyNotifyLog.target_id.in_(ids[i:i + 500])))
            out.update(int(r) for r in rows.scalars())
    return out


async def purge_old(now: datetime | None = None) -> int:
    """Housekeeping: daily DM slots older than 3 days and any row older than 90 days. Returns rows deleted."""
    now = aware(now) if now else utcnow()
    async with db.tx() as s:
        a = await s.execute(delete(AcademyNotifyLog).where(AcademyNotifyLog.kind == AR.DM_REMINDER_SLOT,
                                                           AcademyNotifyLog.sent_at < now - timedelta(days=3)))
        b = await s.execute(delete(AcademyNotifyLog).where(AcademyNotifyLog.sent_at < now - timedelta(days=90)))
    return int((a.rowcount or 0) + (b.rowcount or 0))


# ------------------------------------------------------------------ user preferences
async def reminders_on(user_id: int) -> bool:
    async with db.tx() as s:
        row = await s.get(AcademyUserPrefs, int(user_id))
        return True if row is None else bool(row.remind_on)


async def set_remind(user_id: int, on: bool, now: datetime | None = None, *, private: bool = False) -> None:
    """/academyremind on|off. Writing it in a PRIVATE chat also clears 'unreachable' (a private chat proves we can DM them)."""
    now = aware(now) if now else utcnow()
    async with db.tx() as s:
        await insert_ignore(s, AcademyUserPrefs, dict(user_id=int(user_id), remind_on=True, unreachable=False, updated_at=now))
        vals: dict[str, Any] = dict(remind_on=bool(on), updated_at=now)
        if private:
            vals.update(unreachable=False, unreachable_at=None)
        await s.execute(update(AcademyUserPrefs).where(AcademyUserPrefs.user_id == int(user_id)).values(**vals))


async def mark_unreachable(user_id: int, now: datetime | None = None) -> None:
    now = aware(now) if now else utcnow()
    async with db.tx() as s:
        await insert_ignore(s, AcademyUserPrefs, dict(user_id=int(user_id), remind_on=True, unreachable=False, updated_at=now))
        await s.execute(update(AcademyUserPrefs).where(AcademyUserPrefs.user_id == int(user_id))
                        .values(unreachable=True, unreachable_at=now, updated_at=now))


async def clear_unreachable(user_id: int) -> None:
    async with db.tx() as s:
        await s.execute(update(AcademyUserPrefs).where(AcademyUserPrefs.user_id == int(user_id)).values(unreachable=False, unreachable_at=None))


async def is_unreachable(user_id: int) -> bool:
    async with db.tx() as s:
        row = await s.get(AcademyUserPrefs, int(user_id))
        return bool(row and row.unreachable)


def dm_eligible(now: datetime, *, require_recent: bool):
    """SQL condition for `select(User.id).outerjoin(AcademyUserPrefs, ...)`: not banned / blocked, reminders on, and either never
    marked unreachable or due for a retry (see academy_remind_logic.dm_allowed, which is the same rule in Python).
    require_recent: also seen in the last AR.DM_ACTIVE_DAYS days (for users who are not enrolled)."""
    now = aware(now)
    retry_cut = now - AR.UNREACHABLE_RETRY_DAYS * AR.DAY
    cond = and_(
        User.is_banned.is_(False), User.blocked_bot.is_(False),
        or_(AcademyUserPrefs.user_id.is_(None),
            and_(AcademyUserPrefs.remind_on.is_(True),
                 or_(AcademyUserPrefs.unreachable.is_(False),
                     and_(AcademyUserPrefs.unreachable_at <= retry_cut, User.last_seen > AcademyUserPrefs.unreachable_at)))))
    if require_recent:
        cond = and_(cond, User.last_seen >= now - AR.DM_ACTIVE_DAYS * AR.DAY)
    return cond


async def _is_blocked(user_id: int) -> bool:
    async with db.tx() as s:
        u = await s.get(User, int(user_id))
        return bool(u and u.blocked_bot)


# ------------------------------------------------------------------ groups
_seen_mono: dict[int, float] = {}
SEEN_MAX = 5000                          # hard bound of the throttle dict (one float per group; a bot is rarely in that many groups)


def seen_due(chat_id: int, mono: float | None = None) -> bool:
    """In-memory throttle for the middleware: True at most once per AR.SEEN_WRITE_EVERY_S per group (one float per group).
    The dict is bounded: past SEEN_MAX entries the expired ones go first, then the oldest half (a pruned group is simply
    written once more, which is harmless)."""
    now = time.monotonic() if mono is None else mono
    last = _seen_mono.get(chat_id)
    if last is not None and now - last < AR.SEEN_WRITE_EVERY_S:
        return False
    _seen_mono[chat_id] = now
    if len(_seen_mono) > SEEN_MAX:
        for k in [k for k, v in _seen_mono.items() if now - v >= AR.SEEN_WRITE_EVERY_S]:
            del _seen_mono[k]
        if len(_seen_mono) > SEEN_MAX:
            for k in sorted(_seen_mono, key=_seen_mono.get)[:len(_seen_mono) // 2]:
                del _seen_mono[k]
    return True


async def note_group_seen(chat_id: int, now: datetime | None = None) -> None:
    """Record 'the bot saw a message in this group' (and forgive earlier send failures). Called by the middleware at most every 6 h per group."""
    now = aware(now) if now else utcnow()
    async with db.tx() as s:
        if not await insert_ignore(s, AcademyGroupSeen, dict(chat_id=int(chat_id), last_seen=now, fails=0)):
            await s.execute(update(AcademyGroupSeen).where(AcademyGroupSeen.chat_id == int(chat_id)).values(last_seen=now, fails=0))


async def active_groups(now: datetime | None = None) -> list[int]:
    """Groups the bot saw activity in during the last 7 days, with games enabled (the /settings switch) and not refusing our messages."""
    now = aware(now) if now else utcnow()
    q = (select(GroupSetting.chat_id)
         .join(AcademyGroupSeen, AcademyGroupSeen.chat_id == GroupSetting.chat_id)
         .where(GroupSetting.games_enabled.is_(True), AcademyGroupSeen.last_seen >= now - AR.ACTIVE_GROUP_DAYS * AR.DAY,
                AcademyGroupSeen.fails < AR.GROUP_FAILS_SKIP)
         .order_by(GroupSetting.chat_id))
    async with db.tx() as s:
        return [int(c) for c in (await s.execute(q)).scalars()]


async def group_result(chat_id: int, ok: bool) -> None:
    async with db.tx() as s:
        if ok:
            await s.execute(update(AcademyGroupSeen).where(AcademyGroupSeen.chat_id == int(chat_id), AcademyGroupSeen.fails > 0).values(fails=0))
        else:
            await s.execute(update(AcademyGroupSeen).where(AcademyGroupSeen.chat_id == int(chat_id)).values(fails=AcademyGroupSeen.fails + 1))


# ------------------------------------------------------------------ the sender
SendFn = Callable[..., Awaitable[Any]]


class Sender:
    """One per tick run. `await send(chat_id, text, kb, gif)` -> 'ok' | 'refused' (Telegram said no: blocked, kicked, bad chat) |
    'error' (an exception, nothing delivered). Every call waits for the global limiter; after every AR.NOTIFY_CHUNK sends it sleeps a
    little. `exhausted` turns True when the time budget or the message cap of this tick is used up: stop starting new sends then."""

    def __init__(self, bot, *, limiter: RL.Limiter | None = None, budget_s: float | None = None, max_msgs: int | None = None,
                 send_fn: SendFn | None = None, clock: Callable[[], float] | None = None,
                 sleep: Callable[[float], Awaitable[None]] | None = None) -> None:
        self.bot = bot
        self.limiter = limiter or LIMITER
        self.budget_s = AR.NOTIFY_TICK_BUDGET_S if budget_s is None else budget_s
        self.max_msgs = AR.NOTIFY_MAX_PER_TICK if max_msgs is None else max_msgs
        self._send_fn = send_fn or safe_send
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._t0 = self._clock()
        self.sent = 0                    # API calls made (messages and GIFs)

    @property
    def exhausted(self) -> bool:
        return self.sent >= self.max_msgs or self._clock() - self._t0 >= self.budget_s

    async def _pace(self) -> None:
        await self.limiter.acquire()
        self.sent += 1
        if self.sent % AR.NOTIFY_CHUNK == 0:
            await self._sleep(AR.NOTIFY_CHUNK_SLEEP)

    async def send(self, chat_id: int, text: str, kb=None, gif: str | None = None) -> str:
        try:
            parts = split_text(text, 3900) or [text]
            if gif and len(parts) == 1 and len(text) <= 1000:           # a SFW reaction GIF with the text as caption
                from utils import gifs
                url = await gifs.pick(gif)
                if url:
                    await self._pace()
                    try:
                        await self.bot.send_animation(chat_id, url, caption=text, reply_markup=kb)
                        return "ok"
                    except TelegramForbiddenError:
                        if chat_id > 0:
                            from services import users
                            await users.mark_blocked(chat_id)
                        return "refused"
                    except Exception:
                        log.info("academy notify: GIF refused, sending text", exc_info=True)
            for i, part in enumerate(parts):
                await self._pace()
                if await self._send_fn(self.bot, chat_id, part, kb if i == len(parts) - 1 else None) is None:
                    return "refused" if i == 0 else "ok"
            return "ok"
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("academy notify: send to %s failed", chat_id)
            return "error"


async def deliver_dm(sender: Sender, user_id: int, text: str, kb=None, gif: str | None = None, now: datetime | None = None) -> str:
    """-> 'sent' | 'blocked' (user marked unreachable) | 'failed' (refused for another reason) | 'error' (not delivered, may retry)."""
    res = await sender.send(user_id, text, kb, gif)
    if res == "ok":
        return "sent"
    if res == "error":
        return "error"
    if await _is_blocked(user_id):                  # safe_send already flagged users.blocked_bot on Forbidden
        await mark_unreachable(user_id, now)
        return "blocked"
    return "failed"


async def deliver_group(sender: Sender, chat_id: int, text: str, kb=None, gif: str | None = None) -> str:
    """-> 'sent' | 'failed' (refused: counts towards skipping the group) | 'error' (not delivered, may retry)."""
    res = await sender.send(chat_id, text, kb, gif)
    if res == "error":
        return "error"
    try:
        await group_result(chat_id, res == "ok")
    except Exception:
        log.exception("academy notify: could not record the result for group %s", chat_id)
    return "sent" if res == "ok" else "failed"
