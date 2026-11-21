"""Optional full member lists through MTProto (Telethon), so the bot knows EVERYBODY in a group, not just the people who spoke.

The Bot API has no "list members" call; MTProto has (for a bot that is an ADMIN of the group). Needs two environment variables
(Railway -> Variables), never written into the code or the repository:
    TELEGRAM_API_ID, TELEGRAM_API_HASH      (from https://my.telegram.org -> API development tools)
Without them, or without the `telethon` package, everything keeps working exactly as before (this module just does nothing).
Everything found is written to the database (users + group_members), so it survives redeploys / crashes and is in every backup."""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

_client = None
_lock = asyncio.Lock()
_disabled = False
_warned: set[str] = set()


def configured() -> bool:
    return bool(os.getenv("TELEGRAM_API_ID", "").strip().isdigit() and os.getenv("TELEGRAM_API_HASH", "").strip())


def _once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(msg)


def clean(rows) -> list[tuple[int, str | None, str]]:
    """[(uid, username, first_name)] without bots, deleted accounts and duplicates."""
    seen: set[int] = set()
    out = []
    for u in rows:
        uid = getattr(u, "id", 0)
        if not uid or uid in seen or getattr(u, "bot", False) or getattr(u, "deleted", False):
            continue
        seen.add(uid)
        out.append((int(uid), getattr(u, "username", None), (getattr(u, "first_name", None) or "Player")[:100]))
    return out


async def _get_client():
    global _client, _disabled
    if _disabled or not configured():
        return None
    async with _lock:
        if _client is not None:
            return _client
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
        except Exception:
            _disabled = True
            _once("nolib", "mtproto: the 'telethon' package is not installed - full member lists are off")
            return None
        try:
            from config import get_settings
            c = TelegramClient(StringSession(), int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"].strip())
            await c.start(bot_token=get_settings().bot_token)
            _client = c
            log.info("mtproto: connected (full member lists on)")
        except Exception:
            _disabled = True
            log.exception("mtproto: could not connect - full member lists are off")
            return None
    return _client


async def sync_group(chat_id: int) -> int:
    """Write every real member of this group into the database. -> how many people were NEW to this group's roster."""
    c = await _get_client()
    if c is None:
        return 0
    try:
        people = clean([u async for u in c.iter_participants(chat_id, aggressive=True)])
    except Exception as e:                                       # not an admin there, private group, flood wait ... -> keep the old way
        _once(f"err:{chat_id}", f"mtproto: could not list members of {chat_id}: {type(e).__name__} (the bot must be an ADMIN of the group)")
        return 0
    if not people:
        return 0
    from sqlalchemy import select

    from database.models import GroupMember, User
    from database.session import db, insert_ignore
    from services import users
    new = 0
    async with db.tx() as s:
        have_rost = set((await s.execute(select(GroupMember.user_id).where(GroupMember.chat_id == chat_id))).scalars())
    for i in range(0, len(people), 400):                       # chunks: SQLite limits the number of ? in one query
        chunk = people[i:i + 400]
        async with db.tx() as s:
            have_users = set((await s.execute(select(User.id).where(User.id.in_([p[0] for p in chunk])))).scalars())
        for uid, username, name in chunk:
            if uid not in have_users:
                await users.touch(uid, username, name)
        todo = [p[0] for p in chunk if p[0] not in have_rost]
        if todo:
            async with db.tx() as s:
                for uid in todo:
                    await insert_ignore(s, GroupMember, dict(chat_id=chat_id, user_id=uid))
            new += len(todo)
        await asyncio.sleep(0)
    log.info("mtproto: group %s - %d members, %d new to the roster", chat_id, len(people), new)
    return new
