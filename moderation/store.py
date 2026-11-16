"""Moderation persistence layer. Uses the bot's existing database engine (database.session.db) but only its own mod_* tables."""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from sqlalchemy import delete, func, inspect, select, text, update

from database.session import db, insert_ignore
from . import defaults
from .models import (ModAction, ModApproved, ModBase, ModCaptcha, ModFilter, ModMember, ModMeta, ModMsg, ModPending,
                     ModPunishment, ModReport, ModSettings, ModWarn)

log = logging.getLogger("moderation.store")
SCHEMA_VERSION = 1


def now() -> int:
    return int(time.time())


# ------------------------------------------------------------------------------------------------ schema & migrations
async def init_schema() -> None:
    """Create missing mod_* tables (never touches the game's tables) and run additive migrations."""
    assert db.engine is not None
    async with db.engine.begin() as conn:
        await conn.run_sync(ModBase.metadata.create_all)
    async with db.tx() as s:
        row = await s.get(ModMeta, "schema_version")
        ver = int(row.value) if row and row.value.isdigit() else 0
    if ver < SCHEMA_VERSION:
        await _migrate(ver)


async def _migrate(from_version: int) -> None:
    """Ordered, additive, idempotent steps. Add `if from_version < N:` blocks here for future schema changes
    (e.g. `await _add_column('mod_members', 'note', 'VARCHAR(200)')`)."""
    async with db.tx() as s:
        row = await s.get(ModMeta, "schema_version")
        if row is None:
            s.add(ModMeta(key="schema_version", value=str(SCHEMA_VERSION)))
        else:
            row.value = str(SCHEMA_VERSION)
    log.info("moderation schema at version %s", SCHEMA_VERSION)


async def _add_column(table: str, column: str, ddl_type: str) -> None:   # helper for future migrations
    assert db.engine is not None
    async with db.engine.begin() as conn:
        cols = await conn.run_sync(lambda c: [x["name"] for x in inspect(c).get_columns(table)])
        if column not in cols:
            await conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}'))


# ------------------------------------------------------------------------------------------------ settings (cached)
_settings: dict[int, dict] = {}


def peek_settings(chat_id: int) -> dict | None:
    return _settings.get(chat_id)


async def get_settings(chat_id: int) -> dict:
    cached = _settings.get(chat_id)
    if cached is not None:
        return cached
    try:
        async with db.tx() as s:
            row = await s.get(ModSettings, chat_id)
            stored = json.loads(row.data) if row else {}
    except Exception:
        log.exception("could not load settings for %s (using defaults)", chat_id)
        return defaults.with_defaults(None)
    _settings[chat_id] = defaults.with_defaults(stored)
    return _settings[chat_id]


async def save_settings(chat_id: int, mutate) -> dict:
    """Read-modify-write inside one transaction. `mutate(settings_dict)` edits in place (or returns a new dict)."""
    async with db.tx() as s:
        row = await s.get(ModSettings, chat_id)
        cur = defaults.with_defaults(json.loads(row.data) if row else {})
        new = mutate(cur) or cur
        payload = json.dumps(new, ensure_ascii=False)
        if row is None:
            s.add(ModSettings(chat_id=chat_id, data=payload, updated_at=now()))
        else:
            row.data, row.updated_at = payload, now()
    _settings[chat_id] = new
    return new


async def reset_settings(chat_id: int) -> None:
    async with db.tx() as s:
        await s.execute(delete(ModSettings).where(ModSettings.chat_id == chat_id))
    _settings.pop(chat_id, None)


async def all_chat_ids() -> list[int]:
    async with db.tx() as s:
        return list((await s.execute(select(ModSettings.chat_id))).scalars())


# ------------------------------------------------------------------------------------------------ warnings
def _warn_filter(chat_id: int, user_id: int, expire: int):
    q = [ModWarn.chat_id == chat_id, ModWarn.user_id == user_id, ModWarn.active == True]   # noqa: E712
    if expire and expire > 0:
        q.append(ModWarn.created_at > now() - expire)
    return q


async def add_warn(chat_id: int, user_id: int, by_id: int, reason: str, expire: int = 0) -> int:
    async with db.tx() as s:
        s.add(ModWarn(chat_id=chat_id, user_id=user_id, by_id=by_id, reason=(reason or "")[:300], created_at=now()))
        await s.flush()
        return (await s.execute(select(func.count()).select_from(ModWarn).where(*_warn_filter(chat_id, user_id, expire)))).scalar() or 0


async def list_warns(chat_id: int, user_id: int, expire: int = 0) -> list[ModWarn]:
    async with db.tx() as s:
        return list((await s.execute(select(ModWarn).where(*_warn_filter(chat_id, user_id, expire)).order_by(ModWarn.id))).scalars())


async def remove_last_warn(chat_id: int, user_id: int, expire: int = 0) -> bool:
    async with db.tx() as s:
        row = (await s.execute(select(ModWarn).where(*_warn_filter(chat_id, user_id, expire)).order_by(ModWarn.id.desc()).limit(1))).scalar()
        if row is None:
            return False
        row.active = False
        return True


async def reset_warns(chat_id: int, user_id: int) -> int:
    async with db.tx() as s:
        r = await s.execute(update(ModWarn).where(ModWarn.chat_id == chat_id, ModWarn.user_id == user_id, ModWarn.active == True).values(active=False))   # noqa: E712
        return r.rowcount or 0


async def clear_all_warns(chat_id: int) -> int:
    async with db.tx() as s:
        r = await s.execute(update(ModWarn).where(ModWarn.chat_id == chat_id, ModWarn.active == True).values(active=False))   # noqa: E712
        return r.rowcount or 0


async def warn_stats(chat_id: int) -> dict:
    async with db.tx() as s:
        act = (await s.execute(select(func.count()).select_from(ModWarn).where(ModWarn.chat_id == chat_id, ModWarn.active == True))).scalar() or 0   # noqa: E712
        users = (await s.execute(select(func.count(func.distinct(ModWarn.user_id))).where(ModWarn.chat_id == chat_id, ModWarn.active == True))).scalar() or 0   # noqa: E712
        week = (await s.execute(select(func.count()).select_from(ModWarn).where(ModWarn.chat_id == chat_id, ModWarn.created_at > now() - 7 * 86400))).scalar() or 0
    return {"active": act, "users": users, "week": week}


# ------------------------------------------------------------------------------------------------ action log
async def log_action(chat_id: int, actor_id: int, target_id: int | None, action: str, *, reason: str = "",
                     duration: int | None = None, result: str = "ok", detail: str = "", source: str = "manual") -> None:
    """Never raises: a logging failure must not break (or hide) the moderation action itself."""
    try:
        async with db.tx() as s:
            s.add(ModAction(chat_id=chat_id, actor_id=actor_id or 0, target_id=target_id, action=action[:32], reason=(reason or "")[:300],
                            duration=duration, result=result, detail=(detail or "")[:300], source=source, created_at=now()))
    except Exception:
        log.exception("could not write moderation log")


async def recent_actions(chat_id: int, limit: int = 10, target_id: int | None = None, actions: tuple[str, ...] | None = None) -> list[ModAction]:
    async with db.tx() as s:
        q = select(ModAction).where(ModAction.chat_id == chat_id)
        if target_id is not None:
            q = q.where(ModAction.target_id == target_id)
        if actions:
            q = q.where(ModAction.action.in_(actions))
        return list((await s.execute(q.order_by(ModAction.id.desc()).limit(min(limit, 100)))).scalars())


async def action_counts(chat_id: int, since: int) -> dict[str, int]:
    async with db.tx() as s:
        rows = (await s.execute(select(ModAction.action, func.count()).where(ModAction.chat_id == chat_id, ModAction.created_at > since,
                                                                             ModAction.result == "ok").group_by(ModAction.action))).all()
    return {a: n for a, n in rows}


async def top_targets(chat_id: int, since: int, limit: int = 5) -> list[tuple[int, int]]:
    async with db.tx() as s:
        rows = (await s.execute(select(ModAction.target_id, func.count().label("n")).where(
            ModAction.chat_id == chat_id, ModAction.created_at > since, ModAction.target_id.is_not(None), ModAction.result == "ok")
            .group_by(ModAction.target_id).order_by(func.count().desc()).limit(limit))).all()
    return [(t, n) for t, n in rows]


# ------------------------------------------------------------------------------------------------ temporary punishments
async def add_punishment(chat_id: int, user_id: int, kind: str, expires_at: int, by_id: int, reason: str = "", prev_perms: dict | None = None) -> int:
    async with db.tx() as s:
        await s.execute(update(ModPunishment).where(ModPunishment.chat_id == chat_id, ModPunishment.user_id == user_id,
                                                    ModPunishment.kind == kind, ModPunishment.status == "active").values(status="cancelled"))
        row = ModPunishment(chat_id=chat_id, user_id=user_id, kind=kind, expires_at=expires_at, by_id=by_id, reason=(reason or "")[:300],
                            prev_perms=json.dumps(prev_perms) if prev_perms is not None else None, created_at=now())
        s.add(row)
        await s.flush()
        return row.id


async def cancel_punishments(chat_id: int, user_id: int, kinds: tuple[str, ...] = ("ban", "mute", "restrict")) -> int:
    """A manual ban/unban/mute/unmute supersedes any scheduled expiry (otherwise the old timer could undo the new decision)."""
    async with db.tx() as s:
        r = await s.execute(update(ModPunishment).where(ModPunishment.chat_id == chat_id, ModPunishment.user_id == user_id,
                                                        ModPunishment.kind.in_(kinds), ModPunishment.status == "active").values(status="cancelled"))
        return r.rowcount or 0


async def active_punishments(chat_id: int, limit: int = 50) -> list[ModPunishment]:
    async with db.tx() as s:
        return list((await s.execute(select(ModPunishment).where(ModPunishment.chat_id == chat_id, ModPunishment.status == "active")
                                     .order_by(ModPunishment.expires_at).limit(limit))).scalars())


async def due_punishments(limit: int = 50) -> list[int]:
    async with db.tx() as s:
        return list((await s.execute(select(ModPunishment.id).where(ModPunishment.status == "active", ModPunishment.expires_at <= now())
                                     .order_by(ModPunishment.expires_at).limit(limit))).scalars())


async def claim_punishment(pid: int) -> ModPunishment | None:
    """Atomic active -> expiring. Exactly one worker can win, so an expiry can never run twice."""
    async with db.tx() as s:
        r = await s.execute(update(ModPunishment).where(ModPunishment.id == pid, ModPunishment.status == "active").values(status="expiring", claimed_at=now()))
        if (r.rowcount or 0) != 1:
            return None
        return await s.get(ModPunishment, pid)


async def finish_punishment(pid: int, status: str = "done") -> None:
    async with db.tx() as s:
        await s.execute(update(ModPunishment).where(ModPunishment.id == pid).values(status=status))


async def release_punishment(pid: int, max_retries: int = 8) -> str:
    """Temporary failure (network / rate limit): put it back for a later retry, or give up after max_retries."""
    async with db.tx() as s:
        row = await s.get(ModPunishment, pid)
        if row is None:
            return "gone"
        row.retries += 1
        if row.retries >= max_retries:
            row.status = "failed"
            return "failed"
        row.status, row.expires_at = "active", max(row.expires_at, now() + min(300, 15 * row.retries))
        return "retry"


async def recover() -> dict:
    """Run once at startup: 'expiring' rows left by a crash go back to 'active'; confirmations that were mid-run are marked interrupted
    (NOT re-run: re-running a half-done mass operation unattended would be unsafe)."""
    async with db.tx() as s:
        a = await s.execute(update(ModPunishment).where(ModPunishment.status == "expiring").values(status="active"))
        b = await s.execute(update(ModPending).where(ModPending.status == "running").values(status="interrupted", result="Bot restarted while this was running"))
    return {"punishments": a.rowcount or 0, "interrupted": b.rowcount or 0}


# ------------------------------------------------------------------------------------------------ tracked members + message ids
_member_buf: dict[tuple[int, int], dict] = {}
_msg_buf: list[tuple[int, int, int, str, int]] = []
_MAX_BUF = 20000


def note_member(chat_id: int, user, msg_id: int | None = None, kind: str = "text", status: str | None = None, count_msg: bool = True) -> None:
    """In-memory (instant, cannot fail); written to the DB in batches by flush()."""
    if user is None or len(_member_buf) > _MAX_BUF:
        return
    t = now()
    e = _member_buf.setdefault((chat_id, user.id), {"msgs": 0})
    e.update(username=getattr(user, "username", None), name=(getattr(user, "full_name", None) or getattr(user, "first_name", "") or "")[:128],
             is_bot=bool(getattr(user, "is_bot", False)), last=t)
    if count_msg and msg_id:
        e["msgs"] += 1
    if status:
        e["status"] = status
    if msg_id and len(_msg_buf) < _MAX_BUF:
        _msg_buf.append((chat_id, msg_id, user.id, kind, t))


def note_service(chat_id: int, msg_id: int, kind: str = "service") -> None:
    if len(_msg_buf) < _MAX_BUF:
        _msg_buf.append((chat_id, msg_id, 0, kind, now()))


async def flush() -> None:
    global _msg_buf
    members, _member_buf_snapshot = dict(_member_buf), None
    _member_buf.clear()
    msgs, _msg_buf = _msg_buf, []
    if not members and not msgs:
        return
    try:
        async with db.tx() as s:
            for (chat, uid), e in members.items():
                row = await s.get(ModMember, (chat, uid))
                if row is None:
                    row = ModMember(chat_id=chat, user_id=uid, first_seen=e["last"], msgs=0, status="member")
                    s.add(row)
                row.username, row.username_lc = e["username"], (e["username"] or "").lower() or None
                row.name, row.is_bot, row.last_seen = e["name"], e["is_bot"], e["last"]
                row.msgs = (row.msgs or 0) + e["msgs"]
                if "status" in e:
                    row.status = e["status"]
                elif row.status in ("left", "banned") and e["msgs"]:
                    row.status = "member"                 # they are talking again => they are in the group
            for chat, mid, uid, kind, ts in msgs:
                await insert_ignore(s, ModMsg, dict(chat_id=chat, message_id=mid, user_id=uid, kind=kind, ts=ts))
    except Exception:
        log.exception("flush failed - merging buffers back")
        for k, e in members.items():
            cur = _member_buf.setdefault(k, {"msgs": 0})
            e2 = dict(e); e2["msgs"] += cur.get("msgs", 0)
            cur.update(e2)
        _msg_buf = (msgs + _msg_buf)[:_MAX_BUF]


async def set_member_status(chat_id: int, user_id: int, status: str, user=None) -> None:
    try:
        async with db.tx() as s:
            row = await s.get(ModMember, (chat_id, user_id))
            if row is None:
                t = now()
                row = ModMember(chat_id=chat_id, user_id=user_id, first_seen=t, last_seen=t, msgs=0)
                s.add(row)
            row.status = status
            if user is not None:
                row.username, row.username_lc = getattr(user, "username", None), (getattr(user, "username", None) or "").lower() or None
                row.name = (getattr(user, "full_name", "") or row.name or "")[:128]
                row.is_bot = bool(getattr(user, "is_bot", False))
    except Exception:
        log.exception("set_member_status failed")


async def mark_deleted(chat_id: int, user_id: int) -> None:
    async with db.tx() as s:
        await s.execute(update(ModMember).where(ModMember.chat_id == chat_id, ModMember.user_id == user_id).values(deleted=True))


async def find_member_by_username(chat_id: int, username: str) -> int | None:
    u = username.lstrip("@").lower()
    async with db.tx() as s:
        row = (await s.execute(select(ModMember.user_id).where(ModMember.chat_id == chat_id, ModMember.username_lc == u).limit(1))).scalar()
    return row


async def member_row(chat_id: int, user_id: int) -> ModMember | None:
    async with db.tx() as s:
        return await s.get(ModMember, (chat_id, user_id))


async def list_members(chat_id: int, statuses: tuple[str, ...] | None = None, inactive_before: int | None = None,
                       include_bots: bool = False, deleted_only: bool = False, limit: int = 100000) -> list[ModMember]:
    async with db.tx() as s:
        q = select(ModMember).where(ModMember.chat_id == chat_id)
        if statuses:
            q = q.where(ModMember.status.in_(statuses))
        if inactive_before is not None:
            q = q.where(ModMember.last_seen < inactive_before)
        if not include_bots:
            q = q.where(ModMember.is_bot == False)    # noqa: E712
        if deleted_only:
            q = q.where(ModMember.deleted == True)    # noqa: E712
        return list((await s.execute(q.order_by(ModMember.user_id).limit(limit))).scalars())


async def member_stats(chat_id: int) -> dict:
    t = now()
    async with db.tx() as s:
        c = lambda *w: select(func.count()).select_from(ModMember).where(ModMember.chat_id == chat_id, *w)   # noqa: E731
        total = (await s.execute(c(ModMember.status.in_(("member", "restricted"))))).scalar() or 0
        d1 = (await s.execute(c(ModMember.last_seen > t - 86400, ModMember.status.in_(("member", "restricted"))))).scalar() or 0
        d7 = (await s.execute(c(ModMember.last_seen > t - 7 * 86400, ModMember.status.in_(("member", "restricted"))))).scalar() or 0
        new7 = (await s.execute(c(ModMember.first_seen > t - 7 * 86400))).scalar() or 0
        left = (await s.execute(c(ModMember.status == "left"))).scalar() or 0
        banned = (await s.execute(c(ModMember.status == "banned"))).scalar() or 0
        silent = (await s.execute(c(ModMember.msgs == 0, ModMember.status.in_(("member", "restricted"))))).scalar() or 0
    return {"tracked": total, "active_24h": d1, "active_7d": d7, "new_7d": new7, "left": left, "banned": banned, "lurkers": silent}


async def recent_msgs(chat_id: int, user_id: int | None = None, kinds: tuple[str, ...] | None = None, since: int | None = None,
                      newer_than_id: int | None = None, limit: int = 5000) -> list[ModMsg]:
    async with db.tx() as s:
        q = select(ModMsg).where(ModMsg.chat_id == chat_id)
        if user_id is not None:
            q = q.where(ModMsg.user_id == user_id)
        if kinds:
            q = q.where(ModMsg.kind.in_(kinds))
        if since is not None:
            q = q.where(ModMsg.ts >= since)
        if newer_than_id is not None:
            q = q.where(ModMsg.message_id >= newer_than_id)
        return list((await s.execute(q.order_by(ModMsg.message_id.desc()).limit(limit))).scalars())


async def forget_msgs(chat_id: int, ids: list[int]) -> None:
    if not ids:
        return
    async with db.tx() as s:
        for i in range(0, len(ids), 500):
            await s.execute(delete(ModMsg).where(ModMsg.chat_id == chat_id, ModMsg.message_id.in_(ids[i:i + 500])))


async def prune(max_age: int = 2 * 86400 + 3600) -> int:
    async with db.tx() as s:
        r = await s.execute(delete(ModMsg).where(ModMsg.ts < now() - max_age))
        await s.execute(delete(ModPending).where(ModPending.created_at < now() - 7 * 86400, ModPending.status != "pending"))
        await s.execute(delete(ModPunishment).where(ModPunishment.status.in_(("done", "cancelled")), ModPunishment.created_at < now() - 90 * 86400))
        return r.rowcount or 0


# ------------------------------------------------------------------------------------------------ approved users
_approved: dict[int, set[int]] = {}


async def approved_set(chat_id: int) -> set[int]:
    if chat_id not in _approved:
        async with db.tx() as s:
            _approved[chat_id] = set((await s.execute(select(ModApproved.user_id).where(ModApproved.chat_id == chat_id))).scalars())
    return _approved[chat_id]


async def approve(chat_id: int, user_id: int, by_id: int) -> bool:
    async with db.tx() as s:
        added = await insert_ignore(s, ModApproved, dict(chat_id=chat_id, user_id=user_id, by_id=by_id, created_at=now()))
    (await approved_set(chat_id)).add(user_id)
    return added


async def unapprove(chat_id: int, user_id: int) -> bool:
    async with db.tx() as s:
        r = await s.execute(delete(ModApproved).where(ModApproved.chat_id == chat_id, ModApproved.user_id == user_id))
    (await approved_set(chat_id)).discard(user_id)
    return (r.rowcount or 0) > 0


# ------------------------------------------------------------------------------------------------ filters (blocklist + custom filters)
_filters: dict[int, list[dict]] = {}
MAX_FILTERS = 200


def _fdict(f: ModFilter) -> dict:
    return {"id": f.id, "kind": f.kind, "pattern": f.pattern, "action": f.action, "duration": f.duration, "response": f.response}


async def filters_of(chat_id: int) -> list[dict]:
    if chat_id not in _filters:
        async with db.tx() as s:
            rows = (await s.execute(select(ModFilter).where(ModFilter.chat_id == chat_id).order_by(ModFilter.id))).scalars().all()
            _filters[chat_id] = [_fdict(r) for r in rows]
    return _filters[chat_id]


async def add_filter(chat_id: int, kind: str, pattern: str, action: str, by_id: int, duration: int | None = None, response: str = "") -> int | None:
    cur = await filters_of(chat_id)
    if len(cur) >= MAX_FILTERS:
        return None
    async with db.tx() as s:
        dup = (await s.execute(select(ModFilter.id).where(ModFilter.chat_id == chat_id, ModFilter.kind == kind, ModFilter.pattern == pattern))).scalar()
        if dup:
            row = await s.get(ModFilter, dup)
            row.action, row.duration, row.response = action, duration, response
            fid = dup
        else:
            row = ModFilter(chat_id=chat_id, kind=kind, pattern=pattern, action=action, duration=duration, response=response, created_by=by_id, created_at=now())
            s.add(row)
            await s.flush()
            fid = row.id
    _filters.pop(chat_id, None)
    return fid


async def remove_filter(chat_id: int, ref: str) -> int:
    async with db.tx() as s:
        q = delete(ModFilter).where(ModFilter.chat_id == chat_id)
        q = q.where(ModFilter.id == int(ref)) if ref.isdigit() else q.where(ModFilter.pattern == ref.lower())
        r = await s.execute(q)
    _filters.pop(chat_id, None)
    return r.rowcount or 0


async def clear_filters(chat_id: int, kinds: tuple[str, ...] | None = None, actions: tuple[str, ...] | None = None) -> int:
    async with db.tx() as s:
        q = delete(ModFilter).where(ModFilter.chat_id == chat_id)
        if kinds:
            q = q.where(ModFilter.kind.in_(kinds))
        if actions:
            q = q.where(ModFilter.action.in_(actions))
        r = await s.execute(q)
    _filters.pop(chat_id, None)
    return r.rowcount or 0


# ------------------------------------------------------------------------------------------------ reports
async def add_report(chat_id: int, reporter: int, target: int, message_id: int | None, excerpt: str, reason: str) -> int | None:
    """None if the same reporter already has this exact message pending (prevents duplicates / spam)."""
    async with db.tx() as s:
        if message_id:
            dup = (await s.execute(select(ModReport.id).where(ModReport.chat_id == chat_id, ModReport.reporter_id == reporter,
                                                              ModReport.message_id == message_id, ModReport.status == "pending"))).scalar()
            if dup:
                return None
        row = ModReport(chat_id=chat_id, reporter_id=reporter, target_id=target, message_id=message_id, excerpt=(excerpt or "")[:300],
                        reason=(reason or "")[:300], created_at=now())
        s.add(row)
        await s.flush()
        return row.id


async def get_report(rid: int) -> ModReport | None:
    async with db.tx() as s:
        return await s.get(ModReport, rid)


async def pending_reports(chat_id: int, limit: int = 20) -> list[ModReport]:
    async with db.tx() as s:
        return list((await s.execute(select(ModReport).where(ModReport.chat_id == chat_id, ModReport.status == "pending").order_by(ModReport.id).limit(limit))).scalars())


async def resolve_report(rid: int, by: int, status: str, resolution: str) -> bool:
    async with db.tx() as s:
        r = await s.execute(update(ModReport).where(ModReport.id == rid, ModReport.status == "pending")
                            .values(status=status, resolved_by=by, resolved_at=now(), resolution=resolution))
        return (r.rowcount or 0) == 1


async def report_counts(chat_id: int) -> dict:
    async with db.tx() as s:
        rows = (await s.execute(select(ModReport.status, func.count()).where(ModReport.chat_id == chat_id).group_by(ModReport.status))).all()
    return {k: v for k, v in rows}


# ------------------------------------------------------------------------------------------------ confirmations
async def create_pending(chat_id: int, issuer: int, action: str, payload: dict, ttl: int = 90) -> str:
    token = secrets.token_urlsafe(9)
    async with db.tx() as s:
        s.add(ModPending(token=token, chat_id=chat_id, issuer_id=issuer, action=action, payload=json.dumps(payload), created_at=now(), expires_at=now() + ttl))
    return token


async def set_pending_message(token: str, message_id: int) -> None:
    async with db.tx() as s:
        await s.execute(update(ModPending).where(ModPending.token == token).values(message_id=message_id))


async def get_pending(token: str) -> ModPending | None:
    async with db.tx() as s:
        return await s.get(ModPending, token)


async def claim_pending(token: str, new_status: str) -> bool:
    """Atomic pending -> running/cancelled, only while not expired. True for exactly one caller."""
    async with db.tx() as s:
        r = await s.execute(update(ModPending).where(ModPending.token == token, ModPending.status == "pending", ModPending.expires_at > now())
                            .values(status=new_status))
        return (r.rowcount or 0) == 1


async def finish_pending(token: str, status: str, result: str = "") -> None:
    async with db.tx() as s:
        await s.execute(update(ModPending).where(ModPending.token == token).values(status=status, result=result[:2000]))


async def expire_pending() -> list[ModPending]:
    async with db.tx() as s:
        rows = list((await s.execute(select(ModPending).where(ModPending.status == "pending", ModPending.expires_at <= now()))).scalars())
        for r in rows:
            r.status = "expired"
        return rows


async def running_pending(chat_id: int) -> bool:
    async with db.tx() as s:
        return bool((await s.execute(select(ModPending.token).where(ModPending.chat_id == chat_id, ModPending.status == "running").limit(1))).scalar())


# ------------------------------------------------------------------------------------------------ captcha
async def add_captcha(chat_id: int, user_id: int, message_id: int | None, answer: str, timeout: int) -> None:
    async with db.tx() as s:
        await s.execute(delete(ModCaptcha).where(ModCaptcha.chat_id == chat_id, ModCaptcha.user_id == user_id))
        s.add(ModCaptcha(chat_id=chat_id, user_id=user_id, message_id=message_id, answer=answer, expires_at=now() + timeout, created_at=now()))


async def get_captcha(chat_id: int, user_id: int) -> ModCaptcha | None:
    async with db.tx() as s:
        return await s.get(ModCaptcha, (chat_id, user_id))


async def take_captcha(chat_id: int, user_id: int) -> ModCaptcha | None:
    """Atomic fetch+delete: only one verification / timeout path can ever complete a captcha."""
    async with db.tx() as s:
        row = await s.get(ModCaptcha, (chat_id, user_id))
        if row is None:
            return None
        r = await s.execute(delete(ModCaptcha).where(ModCaptcha.chat_id == chat_id, ModCaptcha.user_id == user_id))
        return row if (r.rowcount or 0) == 1 else None


async def bump_captcha(chat_id: int, user_id: int) -> int:
    async with db.tx() as s:
        row = await s.get(ModCaptcha, (chat_id, user_id))
        if row is None:
            return 99
        row.attempts += 1
        return row.attempts


async def expired_captchas() -> list[tuple[int, int]]:
    async with db.tx() as s:
        return [(c, u) for c, u in (await s.execute(select(ModCaptcha.chat_id, ModCaptcha.user_id).where(ModCaptcha.expires_at <= now()))).all()]


# ------------------------------------------------------------------------------------------------ backup / restore
BACKUP_VERSION = 1


async def export_chat(chat_id: int) -> dict[str, Any]:
    st = await get_settings(chat_id)
    async with db.tx() as s:
        warns = (await s.execute(select(ModWarn).where(ModWarn.chat_id == chat_id))).scalars().all()
        appr = (await s.execute(select(ModApproved).where(ModApproved.chat_id == chat_id))).scalars().all()
        fl = (await s.execute(select(ModFilter).where(ModFilter.chat_id == chat_id))).scalars().all()
        mem = (await s.execute(select(ModMember).where(ModMember.chat_id == chat_id))).scalars().all()
        pun = (await s.execute(select(ModPunishment).where(ModPunishment.chat_id == chat_id, ModPunishment.status == "active"))).scalars().all()
        act = (await s.execute(select(ModAction).where(ModAction.chat_id == chat_id).order_by(ModAction.id.desc()).limit(500))).scalars().all()
    return {"version": BACKUP_VERSION, "kind": "moderation-backup", "chat_id": chat_id, "created_at": now(), "settings": st,
            "warns": [dict(user_id=w.user_id, by_id=w.by_id, reason=w.reason, created_at=w.created_at, active=w.active) for w in warns],
            "approved": [dict(user_id=a.user_id, by_id=a.by_id, created_at=a.created_at) for a in appr],
            "filters": [dict(kind=f.kind, pattern=f.pattern, action=f.action, duration=f.duration, response=f.response) for f in fl],
            "members": [dict(user_id=m.user_id, username=m.username, name=m.name, is_bot=m.is_bot, status=m.status, deleted=m.deleted,
                             first_seen=m.first_seen, last_seen=m.last_seen, msgs=m.msgs) for m in mem],
            "punishments": [dict(user_id=p.user_id, kind=p.kind, expires_at=p.expires_at, prev_perms=p.prev_perms, reason=p.reason, by_id=p.by_id) for p in pun],
            "actions": [dict(actor_id=a.actor_id, target_id=a.target_id, action=a.action, reason=a.reason, duration=a.duration,
                             result=a.result, detail=a.detail, source=a.source, created_at=a.created_at) for a in act]}


def describe_backup(data: dict) -> str | None:
    """None if the file is a valid backup, else the reason it is not."""
    if not isinstance(data, dict) or data.get("kind") != "moderation-backup":
        return "this is not a moderation backup file"
    if int(data.get("version", 0)) > BACKUP_VERSION:
        return "this backup was made by a newer version"
    for k in ("settings", "warns", "approved", "filters", "members"):
        if not isinstance(data.get(k), (dict, list)):
            return f"section '{k}' is missing or corrupt"
    return None


async def import_chat(chat_id: int, data: dict, replace_settings: bool = True) -> dict[str, int]:
    """Safe restore: records are only ADDED when missing (existing warnings / filters / approvals are never overwritten or deleted).
    Settings are replaced only when replace_settings is True (the caller confirms that with the admin first)."""
    n = dict(settings=0, warns=0, approved=0, filters=0, members=0, punishments=0)
    if replace_settings:
        await save_settings(chat_id, lambda cur: defaults.with_defaults(data["settings"]))
        n["settings"] = 1
    async with db.tx() as s:
        have_w = {(w.user_id, w.created_at, w.reason) for w in (await s.execute(select(ModWarn).where(ModWarn.chat_id == chat_id))).scalars()}
        for w in data["warns"]:
            key = (int(w["user_id"]), int(w["created_at"]), w.get("reason", ""))
            if key not in have_w:
                s.add(ModWarn(chat_id=chat_id, user_id=key[0], by_id=int(w.get("by_id", 0)), reason=key[2][:300], created_at=key[1], active=bool(w.get("active", True))))
                n["warns"] += 1
        for a in data["approved"]:
            if await insert_ignore(s, ModApproved, dict(chat_id=chat_id, user_id=int(a["user_id"]), by_id=int(a.get("by_id", 0)), created_at=int(a.get("created_at", now())))):
                n["approved"] += 1
        have_f = {(f.kind, f.pattern) for f in (await s.execute(select(ModFilter).where(ModFilter.chat_id == chat_id))).scalars()}
        for f in data["filters"]:
            if (f["kind"], f["pattern"]) not in have_f and len(have_f) + n["filters"] < MAX_FILTERS:
                s.add(ModFilter(chat_id=chat_id, kind=f["kind"], pattern=f["pattern"][:200], action=f.get("action", "delete"), duration=f.get("duration"),
                                response=f.get("response", ""), created_by=0, created_at=now()))
                n["filters"] += 1
        for m in data["members"]:
            if await insert_ignore(s, ModMember, dict(chat_id=chat_id, user_id=int(m["user_id"]), username=m.get("username"),
                                                      username_lc=(m.get("username") or "").lower() or None, name=(m.get("name") or "")[:128],
                                                      is_bot=bool(m.get("is_bot")), status=m.get("status", "member"), deleted=bool(m.get("deleted")),
                                                      first_seen=int(m.get("first_seen", 0)), last_seen=int(m.get("last_seen", 0)), msgs=int(m.get("msgs", 0)))):
                n["members"] += 1
        have_p = {(p.user_id, p.kind) for p in (await s.execute(select(ModPunishment).where(ModPunishment.chat_id == chat_id, ModPunishment.status == "active"))).scalars()}
        for p in data.get("punishments", []):
            if (int(p["user_id"]), p["kind"]) not in have_p and int(p["expires_at"]) > now():
                s.add(ModPunishment(chat_id=chat_id, user_id=int(p["user_id"]), kind=p["kind"], expires_at=int(p["expires_at"]), prev_perms=p.get("prev_perms"),
                                    by_id=int(p.get("by_id", 0)), reason=(p.get("reason") or "")[:300], created_at=now()))
                n["punishments"] += 1
    _approved.pop(chat_id, None)
    _filters.pop(chat_id, None)
    return n


async def reopen_report(rid: int) -> None:
    async with db.tx() as s:
        await s.execute(update(ModReport).where(ModReport.id == rid).values(status="pending", resolved_by=None, resolved_at=None, resolution=""))
