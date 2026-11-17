from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AppState, GroupSetting, User, Wallet, utcnow, aware
from database.session import db, insert_ignore
from game.rules import R
from services.errors import GameError


async def touch(tg_id: int, username: str | None, first_name: str) -> User:
    """Register on first contact; keep username / name fresh. Idempotent and race-safe."""
    first_name = (first_name or "Player")[:100]
    async with db.tx() as s:
        u = await s.get(User, tg_id)
        if u is None:
            created = await insert_ignore(s, User, dict(id=tg_id, username=username,
                                          username_lc=username.lower() if username else None,
                                          first_name=first_name, created_at=utcnow(), last_seen=utcnow()))
            if created:
                await insert_ignore(s, Wallet, dict(user_id=tg_id, balance=0, bank=0))
                if R.start_balance:
                    from services import economy
                    await economy.apply(s, tg_id, wallet=R.start_balance, kind="start_bonus", ref=f"start:{tg_id}")
            u = await s.get(User, tg_id)
            return u
        if (u.username != username or u.first_name != first_name
                or aware(u.last_seen) < utcnow() - timedelta(seconds=60) or u.blocked_bot or u.deleted_account):
            await s.execute(update(User).where(User.id == tg_id).values(
                username=username, username_lc=username.lower() if username else None,
                first_name=first_name, last_seen=utcnow(), blocked_bot=False, deleted_account=False))
            await s.refresh(u)
        return u


async def note_member(chat_id: int, uid: int) -> None:
    """Remember that uid is in this group (for /waifu). One insert-ignore per pair per day; never raises."""
    try:
        from services.kv import kv
        if await kv.get(f"gm:{chat_id}:{uid}"):
            return
        from database.models import GroupMember
        async with db.tx() as s:
            await insert_ignore(s, GroupMember, dict(chat_id=chat_id, user_id=uid))
        await kv.put(f"gm:{chat_id}:{uid}", "1", 86400)
    except Exception:
        pass


async def note_seen(tg) -> None:
    """Group members are registered the first time they write (re-checked every 6h), so replying to / tagging
    / @-mentioning them works without them ever pressing /start. Never raises."""
    try:
        from services.kv import kv
        if await kv.get(f"seen:{tg.id}"):
            return
        await touch(tg.id, tg.username, tg.full_name or tg.first_name)
        await kv.put(f"seen:{tg.id}", "1", 21600)
    except Exception:
        pass


async def get(s: AsyncSession, uid: int) -> User | None:
    return await s.get(User, uid)


async def require(s: AsyncSession, uid: int) -> User:
    u = await s.get(User, uid)
    if u is None:
        raise GameError("❌ I haven't seen that player yet. Reply to any message of theirs with the command and it works right away.")
    if u.is_banned:
        raise GameError("❌ That player is suspended.")
    return u


async def find_by_username(username: str) -> int | None:
    async with db.tx() as s:
        return (await s.execute(select(User.id).where(User.username_lc == username.lower().lstrip("@")))).scalar()


async def mark_blocked(uid: int) -> None:
    async with db.tx() as s:
        await s.execute(update(User).where(User.id == uid).values(blocked_bot=True))


async def mark_deleted(uid: int) -> None:
    """Telegram told us this account was deactivated/deleted - stop messaging it (counted separately from blocked_bot)."""
    async with db.tx() as s:
        await s.execute(update(User).where(User.id == uid).values(deleted_account=True, blocked_bot=True))


async def touch_group(chat_id: int, title: str | None) -> GroupSetting:
    async with db.tx() as s:
        g = await s.get(GroupSetting, chat_id)
        if g is None:
            await insert_ignore(s, GroupSetting, dict(chat_id=chat_id, title=title, created_at=utcnow()))
            g = await s.get(GroupSetting, chat_id)
        elif g.removed or g.title != title:           # re-added after being removed, or just a title change
            await s.execute(update(GroupSetting).where(GroupSetting.chat_id == chat_id)
                            .values(title=title, removed=False, removed_at=None))
            await s.refresh(g)
        return g


async def mark_group_removed(chat_id: int) -> None:
    """The bot was kicked from / left a group. The row (and its history) is kept, just flagged - so stats
    can tell 'groups I'm in' from 'groups I used to be in', and broadcasts stop targeting it."""
    async with db.tx() as s:
        await s.execute(update(GroupSetting).where(GroupSetting.chat_id == chat_id).values(removed=True, removed_at=utcnow()))


async def get_state(key: str, default: str = "") -> str:
    async with db.tx() as s:
        row = await s.get(AppState, key)
        return row.value if row else default


async def set_state(s: AsyncSession, key: str, value: str) -> None:
    row = await s.get(AppState, key)
    if row:
        row.value = value
    else:
        s.add(AppState(key=key, value=value))


async def lock(s: AsyncSession, *ids: int) -> None:
    """Row-lock users in a fixed order (no deadlocks). Serialises concurrent actions that involve the
    same player, so check-then-act rules (immunity, protection, robbed-shield) can't be raced."""
    await s.execute(select(User.id).where(User.id.in_(sorted(set(ids)))).order_by(User.id).with_for_update())


async def display_name(uid: int) -> str:
    """The player's current first name (falls back to 'Player')."""
    async with db.tx() as s:
        u = await s.get(User, uid)
        return (u.first_name if u else None) or "Player"
