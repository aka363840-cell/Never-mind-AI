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


async def full_scan(chat_id: int, check, admins=None, concurrency: int = 20) -> tuple[int, int]:
    """Check EVERY known, non-deleted player against this group. -> (checked, newly found). One scan per group at a time.
    check(uid, strict) -> bool must retry on flood waits itself (handlers.waifu._still_here does)."""
    if chat_id in _scanning:
        return 0, 0
    _scanning.add(chat_id)
    try:
        async with db.tx() as s:
            have = set((await s.execute(select(GroupMember.user_id).where(GroupMember.chat_id == chat_id))).scalars())
            cand = [i for i in (await s.execute(select(User.id).where(User.deleted_account == False, User.is_banned == False)  # noqa: E712
                                                .order_by(User.last_seen.desc()))).scalars() if i not in have and i > 0]
        found: list[int] = []
        try:
            found += [a for a in (await admins() if admins else []) if a not in have]
        except Exception:
            pass
        sem = asyncio.Semaphore(concurrency)

        async def one(uid: int) -> None:
            async with sem:
                if await check(uid, True):
                    found.append(uid)

        await asyncio.gather(*(one(u) for u in cand))
        found = list(dict.fromkeys(found))
        import time
        from services import users
        async with db.tx() as s:
            for uid in found:
                await insert_ignore(s, GroupMember, dict(chat_id=chat_id, user_id=uid))
            await users.set_state(s, f"scan:{chat_id}", str(int(time.time())))      # remembered in the DB: a redeploy won't repeat it
        return len(cand), len(found)
    finally:
        _scanning.discard(chat_id)


async def auto_scan(bot, chat_id: int, force: bool = False) -> None:
    """Quiet background scan (no message in the chat). Runs when the bot joins a group, and again at most once a day in any group
    where the bot knows fewer than 8 people - so nobody has to type /members scan."""
    try:
        from services.kv import kv
        if scanning(chat_id):
            return
        if not force:
            if await roster_size(chat_id) >= 8 or not await kv.set_nx(f"autoscan:{chat_id}", 86400):
                return
        from handlers.waifu import _admins, _still_here
        await full_scan(chat_id, lambda uid, strict=False: _still_here(bot, chat_id, uid, strict), lambda: _admins(bot, chat_id))
    except Exception:
        log.exception("auto scan failed for %s", chat_id)


RESCAN_S = 7 * 86400


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
                last = int(await users.get_state(f"scan:{cid}", "0") or 0)
                if time.time() - last < RESCAN_S or scanning(cid):
                    continue
                try:
                    await bot.get_chat(cid)                    # can I still reach this group at all?
                except TelegramForbiddenError:
                    await users.mark_group_removed(cid)
                    continue
                except Exception:
                    continue
                await auto_scan(bot, cid, force=True)
                log.info("scanned members of group %s", cid)
        except Exception:
            log.exception("member scan loop error")
        await asyncio.sleep(1800)
