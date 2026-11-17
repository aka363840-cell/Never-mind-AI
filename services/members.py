"""Finding who is in a group. Telegram gives bots NO member list, so the roster (group_members) is built from three sources:
people who talk / are replied to / are tagged, the group's admins, and this module's scan: ask getChatMember about every
player the bot knows (from all its groups) and keep the ones that are in this chat."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select

from database.models import GroupMember, User
from database.session import db, insert_ignore

log = logging.getLogger(__name__)
_scanning: set[int] = set()


def scanning(chat_id: int) -> bool:
    return chat_id in _scanning


async def roster_size(chat_id: int) -> int:
    async with db.tx() as s:
        return (await s.execute(select(func.count()).select_from(GroupMember).where(GroupMember.chat_id == chat_id))).scalar() or 0


async def full_scan(chat_id: int, check, admins=None, concurrency: int = 20, since: float | None = None) -> tuple[int, int]:
    """Check known players against this group. -> (checked, newly found). One scan per group at a time.
    since=None: EVERYONE known; since=<unix time>: only players the bot got to know after that moment (the cheap daily top-up).
    Progress is saved every 50 results, so a crash / redeploy mid-scan loses nothing. check(uid, strict) -> True / False / None
    (None = Telegram hiccup: those people are asked again in a second pass instead of being skipped)."""
    if chat_id in _scanning:
        return 0, 0
    _scanning.add(chat_id)
    try:
        from datetime import datetime, timezone
        async with db.tx() as s:
            have = set((await s.execute(select(GroupMember.user_id).where(GroupMember.chat_id == chat_id))).scalars())
            q = select(User.id).where(User.deleted_account == False, User.is_banned == False)  # noqa: E712
            if since is not None:
                q = q.where(User.created_at >= datetime.fromtimestamp(since, timezone.utc).replace(tzinfo=None))
            cand = [i for i in (await s.execute(q.order_by(User.last_seen.desc()))).scalars() if i not in have and i > 0]
        found_total = 0
        buf: list[int] = []

        async def flush() -> None:
            nonlocal found_total, buf
            if not buf:
                return
            out, buf = buf, []
            async with db.tx() as s:
                for uid in out:
                    await insert_ignore(s, GroupMember, dict(chat_id=chat_id, user_id=uid))
            found_total += len(out)

        try:
            buf += [a for a in (await admins() if admins else []) if a not in have]
        except Exception:
            pass
        await flush()
        sem = asyncio.Semaphore(concurrency)

        async def sweep(ids: list[int]) -> list[int]:
            unsure: list[int] = []

            async def one(uid: int) -> None:
                async with sem:
                    r = await check(uid, True)
                    if r:
                        buf.append(uid)
                        if len(buf) >= 50:
                            await flush()
                    elif r is None:
                        unsure.append(uid)

            await asyncio.gather(*(one(u) for u in ids))
            await flush()
            return unsure

        unsure = await sweep(cand)
        for _ in range(2):                                     # second / third look at everyone Telegram was unsure about
            if not unsure:
                break
            await asyncio.sleep(5)
            unsure = await sweep(unsure)
        import time
        from services import users
        async with db.tx() as s:
            now = str(int(time.time()))
            await users.set_state(s, f"scan:{chat_id}", now)
            if since is None:
                await users.set_state(s, f"scanfull:{chat_id}", now)       # remembered in the DB: a redeploy won't repeat finished work
        return len(cand), found_total
    finally:
        _scanning.discard(chat_id)


FULL_EVERY, TOPUP_EVERY = 7 * 86400, 86400


async def scan_loop(bot) -> None:
    """No commands needed: shortly after every start, and then every 30 minutes, any group that was never scanned (or not for a week)
    is scanned - one group at a time. The time of the last scan is kept in the database, so a redeploy doesn't redo finished work."""
    import time
    from aiogram.exceptions import TelegramForbiddenError
    from database.models import GroupSetting
    from services import users
    await asyncio.sleep(45)
    while True:
        try:
            async with db.tx() as s:
                groups = list((await s.execute(select(GroupSetting.chat_id).where(GroupSetting.removed == False))).scalars())  # noqa: E712
            for cid in groups:
                now = time.time()
                full = int(await users.get_state(f"scanfull:{cid}", "0") or 0)
                last = int(await users.get_state(f"scan:{cid}", "0") or 0)
                if scanning(cid) or (now - full < FULL_EVERY and now - last < TOPUP_EVERY):
                    continue
                since = None if now - full >= FULL_EVERY else float(last)        # weekly: everyone; daily: only players new to the bot
                try:
                    await bot.get_chat(cid)                    # can I still reach this group at all?
                except TelegramForbiddenError:
                    await users.mark_group_removed(cid)
                    continue
                except Exception:
                    continue
                await auto_scan(bot, cid, force=True, since=since)
                log.info("scanned members of group %s", cid)
        except Exception:
            log.exception("member scan loop error")
        await asyncio.sleep(1800)
