"""/waifu - a random member of the group as your 'waifu of the day'.

Sticky for 24 hours from the moment it is picked, and the same pick follows you into every group (and DMs) until
it runs out. Stored in its own table (waifu_picks), so it is NOT a marriage / relative / family member in any way.
The caption builder is pure Python so it can be tested without aiogram or a database."""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select

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


THIN = 8            # fewer known members than this -> try to discover more
SCAN = 600          # how many recently-active known players one scan checks
SCAN_EVERY = 6 * 3600


async def _pool(s, chat_id: int, owner_id: int) -> list[User]:
    ids = set((await s.execute(select(GroupMember.user_id).where(GroupMember.chat_id == chat_id))).scalars())
    ids |= set((await s.execute(select(GroupStat.user_id).where(GroupStat.chat_id == chat_id))).scalars())   # players seen before the roster existed
    ids.discard(owner_id)
    if not ids:
        return []
    return list((await s.execute(select(User).where(User.id.in_(ids), User.is_banned == False,  # noqa: E712
                                                     User.deleted_account == False))).scalars())   # noqa: E712


async def _discover(chat_id: int, owner_id: int, check, admins) -> None:
    """Telegram gives bots no member list, so find members the other way round: ask getChatMember about the players this bot
    already knows (most recently active first) and about the group's admins, and keep everyone who is really in this group."""
    from services.kv import kv
    if not await kv.set_nx(f"wscan:{chat_id}", SCAN_EVERY):        # at most one scan per group every few hours
        return
    async with db.tx() as s:
        have = set((await s.execute(select(GroupMember.user_id).where(GroupMember.chat_id == chat_id))).scalars())
        cand = [i for i in (await s.execute(select(User.id).where(User.is_banned == False, User.deleted_account == False,  # noqa: E712
                                                                 User.id != owner_id).order_by(User.last_seen.desc()).limit(SCAN))).scalars()
                if i not in have]
    found: list[int] = []
    sem = asyncio.Semaphore(15)

    async def one(uid: int) -> None:
        async with sem:
            if await check(uid, True):          # strict: any error counts as "not a member"
                found.append(uid)

    try:
        found += [a for a in (await admins() if admins else []) if a != owner_id]
    except Exception:
        pass
    await asyncio.gather(*(one(u) for u in cand))
    async with db.tx() as s:
        for uid in dict.fromkeys(found):
            await insert_ignore(s, GroupMember, dict(chat_id=chat_id, user_id=uid))


WIVES_KEY = "owner_wives"


async def wives() -> list[int]:
    """The owner's two wives (slot 1, slot 2); 0 = not set. Kept in app_state, so it is in every backup."""
    import json
    from services import users
    try:
        v = json.loads(await users.get_state(WIVES_KEY, "[0,0]"))
        return [int(v[0]), int(v[1])]
    except Exception:
        return [0, 0]


async def set_wife(slot: int, uid: int) -> None:
    import json
    from services import users
    cur = await wives()
    cur[slot - 1] = uid
    async with db.tx() as s:
        await users.set_state(s, WIVES_KEY, json.dumps(cur))


async def pick(owner_id: int, chat_id: int | None, check=None, admins=None, unlimited: bool = False) -> Result:
    """check: optional async callable (uid, strict) -> bool ("is this person in the group?"; strict=True treats errors as no).
    admins: optional async callable -> list of the group's human admin ids. Candidates are tried in random order until one
    passes; the ones that don't are dropped from this group's roster. A thin roster triggers a discovery scan (_discover)."""
    lock = _locks.setdefault(owner_id, asyncio.Lock())
    async with lock:
        now = utcnow()
        async with db.tx() as s:
            row = await s.get(WaifuPick, owner_id)
            if not unlimited and row and aware(row.picked_at) + timedelta(hours=HOURS) > now:      # unlimited (owner's wives): a fresh pick every time
                w = await s.get(User, row.waifu_id)
                left = int((aware(row.picked_at) + timedelta(hours=HOURS) - now).total_seconds())
                return Result(True, waifu_id=row.waifu_id, waifu_name=w.first_name if w else "Someone", bond=row.bond, seconds_left=left)
            if chat_id is None:
                return Result(False, "group")
            pool = await _pool(s, chat_id, owner_id)
            prev = row.waifu_id if row else None
        if check is not None and len(pool) < THIN:
            await _discover(chat_id, owner_id, check, admins)
            async with db.tx() as s:
                pool = await _pool(s, chat_id, owner_id)
        random.shuffle(pool)
        if prev is not None and len(pool) > 1:
            pool.sort(key=lambda u: u.id == prev)                       # the previous waifu goes last: a fresh face when there is one
        chosen = None
        gone: list[int] = []
        for u in pool[:15]:
            if check is None or await check(u.id, False):                       # network calls happen outside the DB transaction
                chosen = u
                break
            gone.append(u.id)
        async with db.tx() as s:
            if gone:
                await s.execute(delete(GroupMember).where(GroupMember.chat_id == chat_id, GroupMember.user_id.in_(gone)))
            if chosen is None:
                return Result(False, "empty")
            bond = random.randint(1, 100)
            row = await s.get(WaifuPick, owner_id)
            if row:
                row.waifu_id, row.bond, row.picked_at = chosen.id, bond, now
            else:
                await insert_ignore(s, WaifuPick, dict(user_id=owner_id, waifu_id=chosen.id, bond=bond, picked_at=now))
            return Result(True, waifu_id=chosen.id, waifu_name=chosen.first_name, bond=bond, seconds_left=HOURS * 3600, fresh=True)
