"""/waifu - a random member of the group as your 'waifu of the day'.

Sticky for 24 hours from the moment it is picked, and the same pick follows you into every group (and DMs) until
it runs out. Stored in its own table (waifu_picks), so it is NOT a marriage / relative / family member in any way.
The caption builder is pure Python so it can be tested without aiogram or a database."""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from database.models import GroupMember, GroupStat, User, WaifuPick, aware, utcnow
from database.session import db, insert_ignore
from game.waifu_text import caption, HOURS  # noqa: F401  (caption re-exported for the handler)

_locks: dict[int, asyncio.Lock] = {}          # one pick at a time per player, so a double-sent command can't pick twice


@dataclass
class Result:
    ok: bool
    reason: str = ""                           # "group" (used in DM with no active pick) | "empty" (nobody else known here yet)
    waifu_id: int = 0
    waifu_name: str = ""
    bond: int = 0
    seconds_left: int = 0
    fresh: bool = False                        # picked just now (False = the existing pick for the day)


async def pick(owner_id: int, chat_id: int | None) -> Result:
    lock = _locks.setdefault(owner_id, asyncio.Lock())
    async with lock:
        now = utcnow()
        async with db.tx() as s:
            row = await s.get(WaifuPick, owner_id)
            if row and aware(row.picked_at) + timedelta(hours=HOURS) > now:
                w = await s.get(User, row.waifu_id)
                left = int((aware(row.picked_at) + timedelta(hours=HOURS) - now).total_seconds())
                return Result(True, waifu_id=row.waifu_id, waifu_name=w.first_name if w else "Someone", bond=row.bond, seconds_left=left)
            if chat_id is None:
                return Result(False, "group")
            ids = set((await s.execute(select(GroupMember.user_id).where(GroupMember.chat_id == chat_id))).scalars())
            ids |= set((await s.execute(select(GroupStat.user_id).where(GroupStat.chat_id == chat_id))).scalars())   # players seen before the roster existed
            ids.discard(owner_id)
            pool: list[User] = []
            if ids:
                pool = list((await s.execute(select(User).where(User.id.in_(ids), User.is_banned == False,  # noqa: E712
                                                                 User.deleted_account == False))).scalars())   # noqa: E712
            if row and len(pool) > 1:
                pool = [u for u in pool if u.id != row.waifu_id] or pool      # a fresh face after the 24h, when there is one
            if not pool:
                return Result(False, "empty")
            w = random.choice(pool)
            bond = random.randint(1, 100)
            if row:
                row.waifu_id, row.bond, row.picked_at = w.id, bond, now
            else:
                await insert_ignore(s, WaifuPick, dict(user_id=owner_id, waifu_id=w.id, bond=bond, picked_at=now))
            return Result(True, waifu_id=w.id, waifu_name=w.first_name, bond=bond, seconds_left=HOURS * 3600, fresh=True)
