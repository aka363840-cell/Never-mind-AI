"""Moderation primitives. Every function: calls Telegram first, treats the action as done ONLY if Telegram confirmed it,
then records it. Failures raise ModError with a clear message (and are logged as 'failed')."""
from __future__ import annotations

import json
import logging

from aiogram import Bot
from aiogram.types import ChatPermissions

from . import store
from .defaults import ALL_PERM_FIELDS, LOCKS, WARN_MODES
from .perms import ModError, explain, get_member, status_of, tg
from .util import esc, fmt_duration

log = logging.getLogger("moderation.actions")
MIN_TEMP = 30                       # Telegram treats anything shorter than 30 s as "forever"
NATIVE_MAX = 366 * 86400            # ... and anything longer than 366 days as "forever" too (our scheduler takes over then)


def _plain(e: BaseException) -> str:
    return str(e)[:200]


def _until(duration: int | None) -> int | None:
    return store.now() + duration if duration and MIN_TEMP <= duration <= NATIVE_MAX else None


def _check_duration(duration: int | None) -> None:
    if duration is not None and duration < MIN_TEMP:
        raise ModError("⏱ The shortest temporary punishment is <b>30 seconds</b> (Telegram's own limit).")


def perms_obj(d: dict) -> ChatPermissions:
    return ChatPermissions(**{f: bool(d.get(f, False)) for f in ALL_PERM_FIELDS})


def member_perm_dict(member) -> dict | None:
    if status_of(member) != "restricted":
        return None
    return {f: bool(getattr(member, f, False)) for f in ALL_PERM_FIELDS}


async def default_perm_dict(bot: Bot, chat_id: int) -> dict:
    ch = await tg(bot.get_chat, chat_id)
    p = getattr(ch, "permissions", None)
    out = {}
    for f in ALL_PERM_FIELDS:
        v = getattr(p, f, None) if p is not None else None
        out[f] = True if v is None else bool(v)
    return out


async def _fail(chat_id, actor, uid, action, e, reason="", duration=None, source="manual"):
    await store.log_action(chat_id, actor, uid, action, reason=reason, duration=duration, result="failed", detail=_plain(e), source=source)
    raise ModError(explain(e)) from e


async def _book(label: str, coro) -> None:
    """Bookkeeping after Telegram already confirmed: a DB hiccup must not turn a real success into a reported failure."""
    try:
        await coro
    except Exception:
        log.exception("bookkeeping failed (%s)", label)


# --------------------------------------------------------------------------------------------------------------- ban family
async def ban(bot: Bot, chat_id: int, uid: int, *, actor: int, reason: str = "", duration: int | None = None,
              revoke: bool = False, source: str = "manual") -> None:
    _check_duration(duration)
    act = "tban" if duration else "ban"
    try:
        await tg(bot.ban_chat_member, chat_id, uid, until_date=_until(duration), revoke_messages=True if revoke else None)
    except Exception as e:
        await _fail(chat_id, actor, uid, act, e, reason, duration, source)
    await _book("cancel", store.cancel_punishments(chat_id, uid))
    if duration:
        await _book("schedule", store.add_punishment(chat_id, uid, "ban", store.now() + duration, actor, reason))
    await _book("status", store.set_member_status(chat_id, uid, "banned"))
    await store.log_action(chat_id, actor, uid, act, reason=reason, duration=duration, source=source)


async def unban(bot: Bot, chat_id: int, uid: int, *, actor: int, reason: str = "", source: str = "manual") -> None:
    try:
        m = await get_member(bot, chat_id, uid)
        if status_of(m) != "kicked":
            raise ModError("ℹ️ That user isn't banned.")
        await tg(bot.unban_chat_member, chat_id, uid, only_if_banned=True)
    except ModError:
        raise
    except Exception as e:
        await _fail(chat_id, actor, uid, "unban", e, reason, None, source)
    await _book("cancel", store.cancel_punishments(chat_id, uid, ("ban",)))
    await _book("status", store.set_member_status(chat_id, uid, "left"))
    await store.log_action(chat_id, actor, uid, "unban", reason=reason, source=source)


async def kick(bot: Bot, chat_id: int, uid: int, *, actor: int, reason: str = "", source: str = "manual", action: str = "kick") -> None:
    """Remove without a lasting ban: ban, then immediately unban so they can rejoin by link."""
    try:
        await tg(bot.ban_chat_member, chat_id, uid)
    except Exception as e:
        await _fail(chat_id, actor, uid, action, e, reason, None, source)
    try:
        await tg(bot.unban_chat_member, chat_id, uid, only_if_banned=True)
    except Exception as e:
        await store.log_action(chat_id, actor, uid, action, reason=reason, result="failed", detail="removed but unban failed: " + _plain(e), source=source)
        await _book("status", store.set_member_status(chat_id, uid, "banned"))
        raise ModError("⚠️ I removed them, but Telegram wouldn't lift the ban afterwards, so they are <b>still banned</b> (use /unban).") from e
    await _book("cancel", store.cancel_punishments(chat_id, uid))
    await _book("status", store.set_member_status(chat_id, uid, "left"))
    await store.log_action(chat_id, actor, uid, action, reason=reason, source=source)


# --------------------------------------------------------------------------------------------------------------- mute family
async def mute(bot: Bot, chat_id: int, uid: int, *, actor: int, reason: str = "", duration: int | None = None,
               source: str = "manual", member=None) -> None:
    _check_duration(duration)
    act = "tmute" if duration else "mute"
    try:
        m = member if member is not None else await get_member(bot, chat_id, uid)
        st = status_of(m)
        if st in ("left", "kicked", "unknown"):
            raise ModError("ℹ️ That user isn't in the group right now.")
        prev = member_perm_dict(m)
        await tg(bot.restrict_chat_member, chat_id, uid, permissions=perms_obj({}), use_independent_chat_permissions=True, until_date=_until(duration))
    except ModError:
        raise
    except Exception as e:
        await _fail(chat_id, actor, uid, act, e, reason, duration, source)
    await _book("cancel", store.cancel_punishments(chat_id, uid, ("mute", "restrict")))
    if duration:
        await _book("schedule", store.add_punishment(chat_id, uid, "mute", store.now() + duration, actor, reason, prev))
    await _book("status", store.set_member_status(chat_id, uid, "restricted"))
    await store.log_action(chat_id, actor, uid, act, reason=reason, duration=duration, source=source)


async def unmute(bot: Bot, chat_id: int, uid: int, *, actor: int, reason: str = "", source: str = "manual") -> None:
    try:
        m = await get_member(bot, chat_id, uid)
        st = status_of(m)
        if st == "kicked":
            raise ModError("ℹ️ That user is banned, not muted. Use /unban.")
        if st != "restricted":
            raise ModError("ℹ️ That user isn't muted or restricted.")
        restore = await default_perm_dict(bot, chat_id)
        await tg(bot.restrict_chat_member, chat_id, uid, permissions=perms_obj(restore), use_independent_chat_permissions=True)
    except ModError:
        raise
    except Exception as e:
        await _fail(chat_id, actor, uid, "unmute", e, reason, None, source)
    await _book("cancel", store.cancel_punishments(chat_id, uid, ("mute", "restrict")))
    await _book("status", store.set_member_status(chat_id, uid, "member"))
    await store.log_action(chat_id, actor, uid, "unmute", reason=reason, source=source)


def native_fields(names: list[str]) -> tuple[set[str], list[str]]:
    """Lock names -> Telegram permission fields; second value = names that have no per-user Telegram permission."""
    fields: set[str] = set()
    unsupported: list[str] = []
    for n in names:
        f = LOCKS[n][0]
        if f and n != "all":
            fields.update(f)
        elif n == "all":
            fields.update(LOCKS["all"][0])
        else:
            unsupported.append(n)
    return fields, unsupported


async def restrict(bot: Bot, chat_id: int, uid: int, names: list[str], *, actor: int, reason: str = "",
                   duration: int | None = None, source: str = "manual") -> None:
    _check_duration(duration)
    fields, bad = native_fields(names)
    if bad or not fields:
        raise ModError("ℹ️ Telegram can't limit <b>" + esc(", ".join(bad or names)) + "</b> for one single member. "
                       "Per-member options: text, media, photos, videos, audios, documents, voice, videonotes, polls, other (stickers/GIFs/games), previews, invite, pin, info.")
    try:
        m = await get_member(bot, chat_id, uid)
        if status_of(m) in ("left", "kicked", "unknown"):
            raise ModError("ℹ️ That user isn't in the group right now.")
        prev = member_perm_dict(m)
        base = prev or await default_perm_dict(bot, chat_id)
        new = {**base, **{f: False for f in fields}}
        await tg(bot.restrict_chat_member, chat_id, uid, permissions=perms_obj(new), use_independent_chat_permissions=True, until_date=_until(duration))
    except ModError:
        raise
    except Exception as e:
        await _fail(chat_id, actor, uid, "restrict", e, reason, duration, source)
    await _book("cancel", store.cancel_punishments(chat_id, uid, ("restrict",)))
    if duration:
        await _book("schedule", store.add_punishment(chat_id, uid, "restrict", store.now() + duration, actor, reason, prev))
    await _book("status", store.set_member_status(chat_id, uid, "restricted"))
    await store.log_action(chat_id, actor, uid, "restrict", reason=",".join(names) + (" | " + reason if reason else ""), duration=duration, source=source)


async def unrestrict(bot: Bot, chat_id: int, uid: int, names: list[str], *, actor: int, source: str = "manual") -> None:
    if not names:
        return await unmute(bot, chat_id, uid, actor=actor, source=source)
    fields, bad = native_fields(names)
    if bad or not fields:
        raise ModError("ℹ️ I can only lift per-member limits Telegram supports: " + esc(", ".join(bad or names)) + " isn't one of them.")
    try:
        m = await get_member(bot, chat_id, uid)
        if status_of(m) != "restricted":
            raise ModError("ℹ️ That user has no restrictions.")
        new = {**member_perm_dict(m), **{f: True for f in fields}}
        await tg(bot.restrict_chat_member, chat_id, uid, permissions=perms_obj(new), use_independent_chat_permissions=True)
    except ModError:
        raise
    except Exception as e:
        await _fail(chat_id, actor, uid, "unrestrict", e, "", None, source)
    await _book("cancel", store.cancel_punishments(chat_id, uid, ("restrict",)))
    await store.log_action(chat_id, actor, uid, "unrestrict", reason=",".join(names), source=source)


# --------------------------------------------------------------------------------------------------------------- warnings
async def warn(bot: Bot, chat_id: int, uid: int, *, actor: int, reason: str, st: dict, source: str = "manual") -> tuple[str, bool]:
    """Adds a warning; at the group's limit applies the group's warn mode. -> (html text, punished?)."""
    w = st["warn"]
    n = await store.add_warn(chat_id, uid, actor, reason, w["expire"])
    await store.log_action(chat_id, actor, uid, "warn", reason=reason, detail=f"{n}/{w['limit']}", source=source)
    if n < w["limit"]:
        return f"⚠️ Warning <b>{n}/{w['limit']}</b>" + (f"\n📝 {esc(reason)}" if reason else ""), False
    mode, dur = w["mode"] if w["mode"] in WARN_MODES else "mute", int(w.get("duration") or 3600)
    why = f"reached {w['limit']} warnings"
    try:
        if mode == "ban":
            await ban(bot, chat_id, uid, actor=actor, reason=why, source=source)
            what = "banned"
        elif mode == "tban":
            await ban(bot, chat_id, uid, actor=actor, reason=why, duration=dur, source=source)
            what = f"banned for {fmt_duration(dur)}"
        elif mode == "kick":
            await kick(bot, chat_id, uid, actor=actor, reason=why, source=source)
            what = "kicked"
        elif mode == "tmute":
            await mute(bot, chat_id, uid, actor=actor, reason=why, duration=dur, source=source)
            what = f"muted for {fmt_duration(dur)}"
        else:
            await mute(bot, chat_id, uid, actor=actor, reason=why, source=source)
            what = "muted"
    except ModError as e:
        return (f"⚠️ Warning <b>{n}/{w['limit']}</b> - the limit is reached but I could not apply the punishment:\n{e}"), False
    await _book("reset warns", store.reset_warns(chat_id, uid))
    return f"🚨 Warning <b>{n}/{w['limit']}</b> - limit reached: user <b>{what}</b>." + (f"\n📝 {esc(reason)}" if reason else ""), True


# --------------------------------------------------------------------------------------------------------------- messages
async def delete_message(bot: Bot, chat_id: int, message_id: int) -> bool:
    try:
        await tg(bot.delete_message, chat_id, message_id)
        return True
    except Exception:
        return False


async def delete_many(bot: Bot, chat_id: int, ids: list[int]) -> int:
    """Returns how many ids were SUBMITTED for deletion that Telegram accepted. Telegram does not say which of them still existed."""
    ids = sorted(set(ids))
    done = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            await tg(bot.delete_messages, chat_id, chunk)
            done += len(chunk)
        except Exception:
            for mid in chunk:                                   # fall back one by one so a single bad id can't block the rest
                done += 1 if await delete_message(bot, chat_id, mid) else 0
    return done


# --------------------------------------------------------------------------------------------------------------- scheduler hook
async def expire_punishment(bot: Bot, row) -> str:
    """Undo one expired temporary punishment. Returns a short result; raises on a retryable failure."""
    chat_id, uid = row.chat_id, row.user_id
    m = await get_member(bot, chat_id, uid)
    st = status_of(m)
    res = "skipped"
    if row.kind == "ban":
        if st == "kicked":
            await tg(bot.unban_chat_member, chat_id, uid, only_if_banned=True)
            res = "unbanned"
        await _book("status", store.set_member_status(chat_id, uid, "left"))
    else:
        prev = json.loads(row.prev_perms) if row.prev_perms else None
        if st == "restricted":
            ud = getattr(m, "until_date", None)
            if ud is not None and getattr(ud, "timestamp", lambda: 0)() > store.now() + 120:
                res = "superseded"                                 # someone set a newer, longer restriction: leave it alone
            else:
                restore = prev or await default_perm_dict(bot, chat_id)
                await tg(bot.restrict_chat_member, chat_id, uid, permissions=perms_obj(restore), use_independent_chat_permissions=True)
                res = "restored"
        elif st == "member" and prev:                             # Telegram already lifted it natively: put the older limits back
            await tg(bot.restrict_chat_member, chat_id, uid, permissions=perms_obj(prev), use_independent_chat_permissions=True)
            res = "restored"
        elif st == "member":
            res = "already-free"
        if res in ("restored", "already-free"):
            await _book("status", store.set_member_status(chat_id, uid, "restricted" if prev else "member"))
    await store.log_action(chat_id, 0, uid, "expire-" + row.kind, reason=row.reason, result="ok" if res != "skipped" else "skipped", detail=res, source="scheduler")
    return res
