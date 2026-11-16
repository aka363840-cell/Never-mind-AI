"""2. Mass moderation.  Telegram's Bot API cannot list a group's members or its banned users, so every bulk command works on
what the bot legitimately KNOWS: members it has seen (tracked in mod_members), plus - where noted - ids the admin supplies.
Every summary says exactly how many accounts that was; nothing pretends to cover "everybody"."""
from __future__ import annotations

import time

from aiogram import Bot
from aiogram.filters import CommandObject
from aiogram.types import Message

from . import actions, confirm, perms, store
from .base import Req, command, prepare, split_duration
from .confirm import Ctx, Spec, run_bulk, summary_text
from .perms import ModError, esc
from .util import fmt_duration, parse_duration

LIMIT_NOTE = ("ℹ️ Telegram doesn't let bots list every member, so this covers only the <b>{n}</b> accounts I have seen in this group "
              "(tracked since I joined / became admin). Members I never saw are not included.")
MASS_RIGHT = "restrict"


async def _known(chat_id: int, statuses, **kw) -> list[int]:
    return [m.user_id for m in await store.list_members(chat_id, statuses=statuses, **kw)]


def _extra_ids(args: list[str]) -> tuple[list[int], list[str]]:
    ids = [int(a) for a in args if a.lstrip("-").isdigit() and len(a.lstrip("-")) >= 5 and not a.startswith("-")]
    rest = [a for a in args if not (a.lstrip("-").isdigit() and len(a.lstrip("-")) >= 5)]
    return ids, rest


def _sample(ids_names: list[str]) -> str:
    return ", ".join(ids_names[:5]) + (" …" if len(ids_names) > 5 else "")


async def _ask(r: Req, action: str, payload: dict, *, title: str, effect: str, n: int, reason: str, extra: list[str] | None = None) -> None:
    if n == 0:
        raise ModError("ℹ️ I don't know any matching member yet, so there is nothing to do.\n" + LIMIT_NOTE.format(n=0))
    lines = [effect, f"👥 Accounts affected: <b>{n}</b>"]
    if reason:
        lines.append(f"📝 Reason: {esc(reason)}")
    lines += extra or []
    lines += ["🛡 Admins, the bot owner, protected accounts and me are always skipped.", LIMIT_NOTE.format(n=n)]
    await confirm.request(r.m, r.bot, action, payload, title=title, lines=lines,
                          warning="This cannot be undone with one click. Answer YES to execute or NO to cancel.")


def _bulk_spec(verb: str, op_factory, targets_fn, right=MASS_RIGHT, bot_right=MASS_RIGHT):
    """op_factory(ctx, st) -> async fn(uid); targets_fn(ctx) -> list[int]"""
    async def run(ctx: Ctx) -> str:
        st = await store.get_settings(ctx.chat_id)
        targets = await targets_fn(ctx)
        if not targets:
            return "ℹ️ Nothing to do any more - no matching accounts."
        stats = await run_bulk(ctx, targets, op_factory(ctx, st), st=st, verb=verb)
        await store.log_action(ctx.chat_id, ctx.issuer, None, "bulk-" + ctx.action, reason=ctx.payload.get("reason", ""),
                               detail=f"ok={stats.ok} failed={stats.failed} skipped={stats.skipped} of {len(targets)}", source="bulk")
        return summary_text(verb, len(targets), stats, note=LIMIT_NOTE.format(n=len(targets)))
    return Spec(actor_right=right, bot_right=bot_right, run=run, bulk=True)


# ---------------------------------------------------------------------------------------------------------------- banall etc.
def _reason(r: Req, args: list[str]) -> str:
    return " ".join(args)[:300]


@command("banall", cat="mass", usage="/banall [reason] [user IDs…]", desc="Ban every tracked, non-protected member (asks YES/NO, shows progress)")
async def cmd_banall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT)
    extra, rest = _extra_ids(r.args)
    ids = sorted(set(await _known(r.chat_id, ("member", "restricted")) + extra))
    await _ask(r, "banall", {"reason": _reason(r, rest), "extra": extra}, title="BAN ALL known members", effect="🔨 Every known member will be <b>banned</b>.",
               n=len(ids), reason=_reason(r, rest))


@command("unbanall", cat="mass", usage="/unbanall [user IDs…]", desc="Unban every member I know was banned (asks YES/NO)")
async def cmd_unbanall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT, allow_admin_target=True)
    extra, _ = _extra_ids(r.args)
    ids = sorted(set(await _known(r.chat_id, ("banned",), include_bots=True) + extra))
    await _ask(r, "unbanall", {"extra": extra}, title="UNBAN ALL known banned members", effect="🔓 Everyone I know was banned will be <b>unbanned</b> (they can rejoin).",
               n=len(ids), reason="", extra=["ℹ️ Telegram has no list of banned users for bots: bans made while I wasn't watching, or by other tools, are unknown to me. Add their IDs after the command."])


@command("muteall", cat="mass", usage="/muteall [duration] [reason] [user IDs…]", desc="Mute every tracked non-protected member (for a group-wide silence /lock all or /lockdown is faster)")
async def cmd_muteall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT)
    extra, rest = _extra_ids(r.args)
    dur, reason = split_duration(rest)
    ids = sorted(set(await _known(r.chat_id, ("member", "restricted")) + extra))
    await _ask(r, "muteall", {"reason": reason, "dur": dur, "extra": extra}, title="MUTE ALL known members",
               effect="🔇 Every known member will be <b>muted</b>" + (f" for {fmt_duration(dur)}." if dur else "."), n=len(ids), reason=reason,
               extra=["💡 To silence the WHOLE group instantly (including members I never saw) use /lockdown on."])


@command("unmuteall", cat="mass", usage="/unmuteall", desc="Unmute every tracked member who is currently restricted (asks YES/NO)")
async def cmd_unmuteall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT, allow_admin_target=True)
    ids = await _known(r.chat_id, ("restricted",))
    await _ask(r, "unmuteall", {}, title="UNMUTE ALL restricted members", effect="🔊 Every member I know is restricted will get the group's normal permissions back.", n=len(ids), reason="")


@command("kickall", cat="mass", usage="/kickall [reason] [user IDs…]", desc="Remove every tracked non-protected member without banning (asks YES/NO)")
async def cmd_kickall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT)
    extra, rest = _extra_ids(r.args)
    ids = sorted(set(await _known(r.chat_id, ("member", "restricted")) + extra))
    await _ask(r, "kickall", {"reason": _reason(r, rest), "extra": extra}, title="KICK ALL known members", effect="👢 Every known member will be <b>removed</b> (they may rejoin by link).",
               n=len(ids), reason=_reason(r, rest))


@command("restrictall", cat="mass", usage="/restrictall <types…> [duration]", desc="Restrict tracked members from given types, e.g. media stickers (asks YES/NO)")
async def cmd_restrictall(m: Message, command: CommandObject, bot: Bot):
    from .commands_member import _lock_tokens
    r = await prepare(m, command, bot, right=MASS_RIGHT)
    extra, rest = _extra_ids(r.args)
    names, rest = _lock_tokens(rest)
    fields, bad = actions.native_fields(names)
    if not names or bad or not fields:
        raise ModError("ℹ️ Say what to restrict, e.g. <code>/restrictall media stickers 1d</code>. Per-member types: text media photos videos audios documents voice videonotes polls other previews.")
    dur, reason = split_duration(rest)
    ids = sorted(set(await _known(r.chat_id, ("member", "restricted")) + extra))
    await _ask(r, "restrictall", {"names": names, "dur": dur, "reason": reason, "extra": extra}, title="RESTRICT ALL known members",
               effect=f"🚫 Every known member loses: <b>{esc(', '.join(names))}</b>" + (f" for {fmt_duration(dur)}." if dur else "."), n=len(ids), reason=reason)


@command("unrestrictall", cat="mass", usage="/unrestrictall", desc="Lift restrictions of every tracked restricted member (asks YES/NO)")
async def cmd_unrestrictall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT, allow_admin_target=True)
    ids = await _known(r.chat_id, ("restricted",))
    await _ask(r, "unrestrictall", {}, title="UNRESTRICT ALL members", effect="✅ Every tracked restricted member gets normal permissions back.", n=len(ids), reason="")


@command("warnall", cat="mass", usage="/warnall [reason]", desc="Give every tracked non-protected member a warning (asks YES/NO)")
async def cmd_warnall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT)
    ids = await _known(r.chat_id, ("member", "restricted"))
    await _ask(r, "warnall", {"reason": _reason(r, r.args)}, title="WARN ALL known members",
               effect=f"⚠️ Every known member gets <b>one warning</b>. Anyone reaching the limit ({r.st['warn']['limit']}) is punished: <b>{r.st['warn']['mode']}</b>.",
               n=len(ids), reason=_reason(r, r.args))


@command("unwarnall", cat="mass", usage="/unwarnall", desc="Clear ALL warnings of ALL members in this group (asks YES/NO)")
async def cmd_unwarnall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT, bot_right=None)
    s = await store.warn_stats(r.chat_id)
    if not s["active"]:
        raise ModError("ℹ️ Nobody has active warnings.")
    await confirm.request(m, bot, "unwarnall", {}, title="CLEAR ALL WARNINGS",
                          lines=[f"🧹 <b>{s['active']}</b> active warnings of <b>{s['users']}</b> members will be cleared.", "Their history stays in the moderation log."],
                          warning="Answer YES to execute or NO to cancel.")


async def _t_extra(ctx: Ctx, statuses, **kw):
    return sorted(set(await _known(ctx.chat_id, statuses, **kw) + [int(x) for x in ctx.payload.get("extra", [])]))


def _banall_ops(ctx, st):
    async def op(uid): await actions.ban(ctx.bot, ctx.chat_id, uid, actor=ctx.issuer, reason=ctx.payload.get("reason", "") or "banall", source="bulk")
    return op


def _unbanall_ops(ctx, st):
    async def op(uid): await actions.unban(ctx.bot, ctx.chat_id, uid, actor=ctx.issuer, reason="unbanall", source="bulk")
    return op


def _muteall_ops(ctx, st):
    async def op(uid): await actions.mute(ctx.bot, ctx.chat_id, uid, actor=ctx.issuer, reason=ctx.payload.get("reason", "") or "muteall", duration=ctx.payload.get("dur"), source="bulk")
    return op


def _unmuteall_ops(ctx, st):
    async def op(uid): await actions.unmute(ctx.bot, ctx.chat_id, uid, actor=ctx.issuer, reason="unmuteall", source="bulk")
    return op


def _kickall_ops(ctx, st):
    async def op(uid): await actions.kick(ctx.bot, ctx.chat_id, uid, actor=ctx.issuer, reason=ctx.payload.get("reason", "") or "kickall", source="bulk")
    return op


def _restrictall_ops(ctx, st):
    async def op(uid): await actions.restrict(ctx.bot, ctx.chat_id, uid, ctx.payload["names"], actor=ctx.issuer, reason=ctx.payload.get("reason", ""), duration=ctx.payload.get("dur"), source="bulk")
    return op


def _unrestrictall_ops(ctx, st):
    async def op(uid): await actions.unmute(ctx.bot, ctx.chat_id, uid, actor=ctx.issuer, reason="unrestrictall", source="bulk")
    return op


def _warnall_ops(ctx, st):
    async def op(uid): await actions.warn(ctx.bot, ctx.chat_id, uid, actor=ctx.issuer, reason=ctx.payload.get("reason", "") or "warnall", st=st, source="bulk")
    return op


async def _t_members(ctx): return await _t_extra(ctx, ("member", "restricted"))
async def _t_banned(ctx): return await _t_extra(ctx, ("banned",), include_bots=True)
async def _t_restricted(ctx): return await _known(ctx.chat_id, ("restricted",))


confirm.register("banall", _bulk_spec("Banning members", _banall_ops, _t_members))
confirm.register("unbanall", _bulk_spec("Unbanning members", _unbanall_ops, _t_banned))
confirm.register("muteall", _bulk_spec("Muting members", _muteall_ops, _t_members))
confirm.register("unmuteall", _bulk_spec("Unmuting members", _unmuteall_ops, _t_restricted))
confirm.register("kickall", _bulk_spec("Kicking members", _kickall_ops, _t_members))
confirm.register("restrictall", _bulk_spec("Restricting members", _restrictall_ops, _t_members))
confirm.register("unrestrictall", _bulk_spec("Lifting restrictions", _unrestrictall_ops, _t_restricted))
confirm.register("warnall", _bulk_spec("Warning members", _warnall_ops, _t_members))


async def _run_unwarnall(ctx: Ctx) -> str:
    n = await store.clear_all_warns(ctx.chat_id)
    await store.log_action(ctx.chat_id, ctx.issuer, None, "unwarnall", detail=f"{n} cleared", source="bulk")
    return f"✅ <b>{n}</b> warnings cleared."


confirm.register("unwarnall", Spec(actor_right=MASS_RIGHT, bot_right=None, run=_run_unwarnall))


# ---------------------------------------------------------------------------------------------------------------- inactive / zombies
@command("kickinactive", cat="mass", usage="/kickinactive <days>", desc="Remove tracked members silent for N days (only members I have seen; asks YES/NO)")
async def cmd_kickinactive(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT)
    if not r.args or not r.args[0].isdigit() or not 1 <= int(r.args[0]) <= 3650:
        raise ModError("ℹ️ Usage: <code>/kickinactive 30</code> (days of silence). I can only judge members I have been tracking.")
    days = int(r.args[0])
    cutoff = store.now() - days * 86400
    ids = await _known(r.chat_id, ("member", "restricted"), inactive_before=cutoff)
    await _ask(r, "kickinactive", {"days": days}, title=f"KICK members inactive for {days}+ days",
               effect=f"👢 Tracked members whose last message/activity I saw more than <b>{days} days</b> ago will be removed (they may rejoin).", n=len(ids), reason=f"inactive {days}d",
               extra=["ℹ️ Members who never spoke since I started tracking are judged by when I first saw them."])


def _inactive_ops(ctx, st):
    async def op(uid): await actions.kick(ctx.bot, ctx.chat_id, uid, actor=ctx.issuer, reason=f"inactive {ctx.payload['days']}d", source="bulk")
    return op


async def _t_inactive(ctx):
    return await _known(ctx.chat_id, ("member", "restricted"), inactive_before=store.now() - int(ctx.payload["days"]) * 86400)


confirm.register("kickinactive", _bulk_spec("Kicking inactive members", _inactive_ops, _t_inactive))


@command("zombies", cat="mass", usage="/zombies [clean]", desc="Find deleted accounts among tracked members; '/zombies clean' removes them (asks YES/NO)")
async def cmd_zombies(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=MASS_RIGHT)
    clean = bool(r.args and r.args[0].lower() == "clean")
    known = await store.list_members(r.chat_id, statuses=("member", "restricted"))
    if not clean:
        notice = await m.reply(f"🧟 Scanning {len(known)} tracked members…")
        found = await _scan_zombies(bot, r.chat_id, [x.user_id for x in known], notice)
        try:
            await notice.edit_text(f"🧟 Scan done: <b>{found}</b> deleted account(s) among the <b>{len(known)}</b> members I track." +
                                   (f"\nRemove them with <code>/zombies clean</code>." if found else "") + "\n" + LIMIT_NOTE.format(n=len(known)))
        except Exception:
            pass
        return
    n = len([x for x in known if x.deleted])
    if n == 0:
        raise ModError("ℹ️ No deleted accounts are known. Run <code>/zombies</code> first to scan.")
    await _ask(r, "zombies", {}, title="REMOVE deleted accounts", effect="🧟 Accounts flagged as <b>Deleted Account</b> will be removed.", n=n, reason="deleted account")


async def _scan_zombies(bot: Bot, chat_id: int, ids: list[int], notice: Message | None) -> int:
    found, last = 0, time.monotonic()
    for i, uid in enumerate(ids, 1):
        try:
            mem = await perms.get_member(bot, chat_id, uid)
            if mem is not None and (mem.user.first_name or "").strip().lower() in ("deleted account", ""):
                if perms.status_of(mem) in ("member", "restricted"):
                    await store.mark_deleted(chat_id, uid)
                    found += 1
            elif mem is None or perms.status_of(mem) in ("left", "kicked"):
                await store.set_member_status(chat_id, uid, "left" if mem is None or perms.status_of(mem) == "left" else "banned")
        except Exception:
            pass
        import asyncio
        await asyncio.sleep(confirm.THROTTLE)
        if notice and time.monotonic() - last > 3:
            last = time.monotonic()
            try:
                await notice.edit_text(f"🧟 Scanning… {i}/{len(ids)} · found {found}")
            except Exception:
                pass
    return found


def _zombie_ops(ctx, st):
    async def op(uid): await actions.kick(ctx.bot, ctx.chat_id, uid, actor=ctx.issuer, reason="deleted account", source="bulk", action="zombie-kick")
    return op


async def _t_zombies(ctx):
    return [x.user_id for x in await store.list_members(ctx.chat_id, statuses=("member", "restricted"), deleted_only=True)]


confirm.register("zombies", _bulk_spec("Removing deleted accounts", _zombie_ops, _t_zombies))


# ---------------------------------------------------------------------------------------------------------------- message clean-up
MAX_PURGE = 1000
MSG_NOTE = ("ℹ️ Telegram doesn't report which of the submitted message ids still existed, so I can only tell you how many I <i>submitted</i>. "
            "Bots can't delete messages older than 48 hours in most chats.")


@command("purge", cat="mass", usage="/purge (reply to the first message to delete)  or  /purge <N>", desc="Delete everything from the replied message down to now (asks YES/NO above 10 messages)")
async def cmd_purge(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="delete")
    if m.reply_to_message:
        first = m.reply_to_message.message_id
    elif r.args and r.args[0].isdigit() and 1 <= int(r.args[0]) <= MAX_PURGE:
        first = max(1, m.message_id - int(r.args[0]))
    else:
        raise ModError(f"↩️ Reply to the first message you want gone, or use <code>/purge 50</code> (max {MAX_PURGE}).")
    last = m.message_id
    n = last - first + 1
    if n > MAX_PURGE:
        raise ModError(f"✂️ That's {n} messages; the limit per purge is {MAX_PURGE}. Reply to a more recent message.")
    if n <= 10:
        done = await actions.delete_many(bot, r.chat_id, list(range(first, last + 1)))
        await store.log_action(r.chat_id, r.actor, None, "purge", detail=f"{first}-{last}", source="manual")
        return
    await confirm.request(m, bot, "purge", {"first": first, "last": last}, title="MASS DELETE messages",
                          lines=[f"🗑 Message ids <b>{first}</b> → <b>{last}</b> (about <b>{n}</b> messages, from everyone) will be deleted.", MSG_NOTE])


async def _run_purge(ctx: Ctx) -> str:
    first, last = int(ctx.payload["first"]), int(ctx.payload["last"])
    done = await actions.delete_many(ctx.bot, ctx.chat_id, list(range(first, last + 1)))
    await store.log_action(ctx.chat_id, ctx.issuer, None, "purge", detail=f"{first}-{last} submitted={done}", source="bulk")
    return f"🗑 Purge finished: <b>{done}</b> message ids were submitted for deletion and accepted by Telegram.\n" + MSG_NOTE


confirm.register("purge", Spec(actor_right="delete", bot_right="delete", run=_run_purge))


@command("delall", cat="mass", usage="/delall <target>", desc="Delete every recent message (last ~48h that I tracked) of one member (asks YES/NO)")
async def cmd_delall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="delete", target=True)
    msgs = await store.recent_msgs(r.chat_id, user_id=r.target, since=store.now() - 2 * 86400)
    if not msgs:
        raise ModError("ℹ️ I have no recent messages of that member on record (I only keep ~48 hours; Telegram offers no 'delete all by user' call).")
    await confirm.request(m, bot, "delall", {"uid": r.target}, title="DELETE all messages of one member",
                          lines=[f"👤 {await r.who()}", f"🗑 <b>{len(msgs)}</b> tracked messages from the last 48 hours.", MSG_NOTE])


async def _run_delall(ctx: Ctx) -> str:
    uid = int(ctx.payload["uid"])
    st = await store.get_settings(ctx.chat_id)
    why, _ = await perms.protection_reason(ctx.bot, ctx.chat_id, uid, st)
    if why:
        raise ModError(f"🛡 Not possible: the target is {why}.")
    ids = [x.message_id for x in await store.recent_msgs(ctx.chat_id, user_id=uid, since=store.now() - 2 * 86400)]
    done = await actions.delete_many(ctx.bot, ctx.chat_id, ids)
    await store.forget_msgs(ctx.chat_id, ids)
    await store.log_action(ctx.chat_id, ctx.issuer, uid, "delall", detail=f"submitted={done}/{len(ids)}", source="bulk")
    return f"🗑 <b>{done}</b> of <b>{len(ids)}</b> tracked messages were deleted."


confirm.register("delall", Spec(actor_right="delete", bot_right="delete", run=_run_delall))


@command("cleanservice", cat="mass", usage="/cleanservice on|off [join leave pin title photo other] | now", desc="Auto-delete 'X joined / left / pinned…' service messages; 'now' removes the recent ones (asks YES/NO)")
async def cmd_cleanservice(m: Message, command: CommandObject, bot: Bot):
    from .defaults import SERVICE_TYPES
    r = await prepare(m, command, bot, right="delete")
    a = (r.args[0].lower() if r.args else "status")
    cs = r.st["cleanservice"]
    if a == "status":
        return await m.reply(f"🧽 Service-message cleaning: <b>{'ON' if cs['on'] else 'OFF'}</b> ({', '.join(cs['types'])})\n<code>/cleanservice on [join leave pin title photo other]</code> · <code>/cleanservice now</code>")
    if a in ("on", "off"):
        types = [t for t in r.args[1:] if t in SERVICE_TYPES] or cs["types"]
        await store.save_settings(r.chat_id, lambda s: s["cleanservice"].update(on=a == "on", types=types))
        await store.log_action(r.chat_id, r.actor, None, "cleanservice-" + a, reason=",".join(types))
        return await m.reply(f"🧽 Service-message cleaning <b>{a.upper()}</b>" + (f" for: {', '.join(types)}." if a == "on" else "."))
    if a == "now":
        msgs = await store.recent_msgs(r.chat_id, kinds=("service",), since=store.now() - 2 * 86400)
        if not msgs:
            raise ModError("ℹ️ I have no recent service messages on record.")
        return await confirm.request(m, bot, "cleanservice", {}, title="DELETE recent service messages", lines=[f"🗑 <b>{len(msgs)}</b> tracked service messages (joins, leaves, pins…).", MSG_NOTE])
    raise ModError("ℹ️ Usage: <code>/cleanservice on|off|now</code>")


async def _run_cleanservice(ctx: Ctx) -> str:
    ids = [x.message_id for x in await store.recent_msgs(ctx.chat_id, kinds=("service",), since=store.now() - 2 * 86400)]
    done = await actions.delete_many(ctx.bot, ctx.chat_id, ids)
    await store.forget_msgs(ctx.chat_id, ids)
    await store.log_action(ctx.chat_id, ctx.issuer, None, "cleanservice", detail=f"submitted={done}", source="bulk")
    return f"🧽 <b>{done}</b> service messages deleted."


confirm.register("cleanservice", Spec(actor_right="delete", bot_right="delete", run=_run_cleanservice))
DOWNLOAD_KINDS = ("document", "audio", "video", "voice", "video_note", "gif")


@command("cleandownloads", cat="mass", usage="/cleandownloads [hours]", desc="Delete recent file messages (documents, audio, video, voice, GIFs) that I tracked (asks YES/NO)")
async def cmd_cleandownloads(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="delete")
    hours = int(r.args[0]) if r.args and r.args[0].isdigit() and 1 <= int(r.args[0]) <= 48 else 48
    msgs = await store.recent_msgs(r.chat_id, kinds=DOWNLOAD_KINDS, since=store.now() - hours * 3600)
    if not msgs:
        raise ModError("ℹ️ I have no recent file messages on record (I keep ~48 hours).")
    await confirm.request(m, bot, "cleandownloads", {"hours": hours}, title="DELETE recent file messages",
                          lines=[f"🗑 <b>{len(msgs)}</b> tracked file messages (documents, audio, video, voice, GIFs) from the last <b>{hours}h</b>.", MSG_NOTE])


async def _run_cleandownloads(ctx: Ctx) -> str:
    ids = [x.message_id for x in await store.recent_msgs(ctx.chat_id, kinds=DOWNLOAD_KINDS, since=store.now() - int(ctx.payload["hours"]) * 3600)]
    done = await actions.delete_many(ctx.bot, ctx.chat_id, ids)
    await store.forget_msgs(ctx.chat_id, ids)
    await store.log_action(ctx.chat_id, ctx.issuer, None, "cleandownloads", detail=f"submitted={done}", source="bulk")
    return f"🧹 <b>{done}</b> file messages deleted."


confirm.register("cleandownloads", Spec(actor_right="delete", bot_right="delete", run=_run_cleandownloads))
