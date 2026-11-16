"""1. Member moderation commands."""
from __future__ import annotations

from aiogram import Bot, F
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from . import actions, api, confirm, perms, store
from .base import Req, command, prepare, reply, router, split_duration, throttled
from .defaults import LOCKS, lock_name
from .perms import ModError, status_of
from .util import esc, fmt_duration, mention


def _btn(text: str, data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text=text, callback_data=data)]])


async def _done(r: Req, icon: str, what: str, *, reason: str = "", dur: int | None = None, kb=None) -> None:
    text = f"{icon} {await r.who()} {what}" + (f" for <b>{fmt_duration(dur)}</b>" if dur else "")
    if reason:
        text += f"\n📝 {esc(reason)}"
    text += f"\n👮 {mention(r.actor, r.m.from_user.full_name)}"
    await reply(r.m, text, kb)


# ----------------------------------------------------------------------------------------------------- ban / unban / kick
@command("ban", "banuser", cat="member", usage="/ban <target> [duration] [reason]", shared=True,
         desc="Ban a member (permanent, or temporary when you add a duration like 2h / 3d)")
async def cmd_ban(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True)
    dur, reason = split_duration(r.args)
    await api.flair(m, "ban")
    await actions.ban(bot, r.chat_id, r.target, actor=r.actor, reason=reason, duration=dur)
    await _done(r, "🔨", "is <b>banned</b>", reason=reason, dur=dur, kb=_btn("🔓 Unban", f"mod:q:ub:{r.target}"))


@command("tban", "tempban", cat="member", usage="/tban <target> <duration> [reason]", desc="Temporary ban; the ban is lifted automatically")
async def cmd_tban(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True)
    dur, reason = split_duration(r.args)
    if dur is None:
        raise ModError("⏱ Give a duration: <code>/tban @user 2h reason</code> (s, m, h, d, w - e.g. 30m, 1d12h).")
    await api.flair(m, "ban")
    await actions.ban(bot, r.chat_id, r.target, actor=r.actor, reason=reason, duration=dur)
    await _done(r, "⏳🔨", "is <b>temporarily banned</b>", reason=reason, dur=dur, kb=_btn("🔓 Unban", f"mod:q:ub:{r.target}"))


@command("dban", "delban", cat="member", usage="/dban (reply) [reason]", desc="Delete the replied message AND ban its sender")
async def cmd_dban(m: Message, command: CommandObject, bot: Bot):
    if not m.reply_to_message:
        raise ModError("↩️ Reply to the offending message with <code>/dban [reason]</code>.")
    r = await prepare(m, command, bot, right="restrict", target=True)
    await perms.require_actor(bot, r.chat_id, r.actor, "delete")
    await perms.require_bot(bot, r.chat_id, "delete")
    dur, reason = split_duration(r.args)
    await api.flair(m, "ban")
    await actions.ban(bot, r.chat_id, r.target, actor=r.actor, reason=reason, duration=dur)
    gone = await actions.delete_message(bot, r.chat_id, m.reply_to_message.message_id)
    await actions.delete_message(bot, r.chat_id, m.message_id)
    await store.log_action(r.chat_id, r.actor, r.target, "dban-delete", result="ok" if gone else "failed", detail="message deleted" if gone else "message could not be deleted")
    await bot.send_message(r.chat_id, f"🔨🗑 {await r.who()} is <b>banned</b> and the message deleted." + (f"\n📝 {esc(reason)}" if reason else "")
                           + (f" (⏳ {fmt_duration(dur)})" if dur else "") + ("" if gone else "\n⚠️ The message itself could not be deleted."),
                           reply_markup=_btn("🔓 Unban", f"mod:q:ub:{r.target}"))


@command("unban", "unbanuser", cat="member", usage="/unban <target> [reason]", desc="Lift a ban (works with a user ID too)", shared=True)
async def cmd_unban(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True, allow_admin_target=True)
    reason = " ".join(r.args)[:300]
    await actions.unban(bot, r.chat_id, r.target, actor=r.actor, reason=reason)
    await _done(r, "🔓", "is <b>unbanned</b> (they can rejoin)", reason=reason)


@command("kick", "kickuser", cat="member", usage="/kick <target> [reason]", staff_shared=True,
         desc="Remove a member (they may rejoin). Regular members' /kick stays the game's joke action")
async def cmd_kick(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True)
    reason = " ".join(r.args)[:300]
    await api.flair(m, "kick")                      # the kick GIF first, then the real action, then "X is kicked"
    await actions.kick(bot, r.chat_id, r.target, actor=r.actor, reason=reason)
    await _done(r, "👢", "is <b>kicked</b>", reason=reason)


@command("kickme", cat="member", usage="/kickme", desc="Leave the group (you can rejoin by link)", gate=False)
async def cmd_kickme(m: Message, bot: Bot):
    uid = m.from_user.id
    if m.sender_chat and m.sender_chat.id == m.chat.id:
        raise ModError("🕶 Admins can't use this.")
    if throttled(uid, "kickme", 30):
        return
    await perms.require_bot(bot, m.chat.id, "restrict")
    st = await store.get_settings(m.chat.id)
    why, _ = await perms.protection_reason(bot, m.chat.id, uid, st)
    if why:
        raise ModError(f"🛡 I can't remove you - you are {why}.")
    await actions.kick(bot, m.chat.id, uid, actor=uid, reason="kickme (self)")
    await bot.send_message(m.chat.id, f"👋 {mention(uid, m.from_user.full_name)} left the group.")


@command("banme", cat="member", usage="/banme", desc="Ban yourself (asks YES/NO first; admins can unban you)", gate=False)
async def cmd_banme(m: Message, bot: Bot):
    uid = m.from_user.id
    if m.sender_chat and m.sender_chat.id == m.chat.id:
        raise ModError("🕶 Admins can't use this.")
    await perms.require_bot(bot, m.chat.id, "restrict")
    st = await store.get_settings(m.chat.id)
    why, _ = await perms.protection_reason(bot, m.chat.id, uid, st)
    if why:
        raise ModError(f"🛡 I can't ban you - you are {why}.")
    await confirm.request(m, bot, "banme", {"uid": uid}, title="Ban yourself?",
                          lines=["You will be <b>banned from this group</b>.", "Only an admin can let you back in."])


async def _run_banme(ctx: confirm.Ctx) -> str:
    uid = ctx.payload["uid"]
    st = await store.get_settings(ctx.chat_id)
    why, _ = await perms.protection_reason(ctx.bot, ctx.chat_id, uid, st)
    if why:
        raise ModError(f"🛡 Not possible: you are {why}.")
    await actions.ban(ctx.bot, ctx.chat_id, uid, actor=uid, reason="banme (self)")
    return "🔨 Done. You banned yourself. Goodbye!"


confirm.register("banme", confirm.Spec(actor_right=None, bot_right="restrict", run=_run_banme, any_member=True))


# ----------------------------------------------------------------------------------------------------- mute / unmute / restrict
@command("mute", "silence", cat="member", usage="/mute <target> [duration] [reason]", shared=True,
         desc="Mute a member (permanent, or temporary with a duration)")
async def cmd_mute(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True)
    dur, reason = split_duration(r.args)
    await api.flair(m, "mute")
    await actions.mute(bot, r.chat_id, r.target, actor=r.actor, reason=reason, duration=dur, member=r.member)
    await _done(r, "🔇", "is <b>muted</b>", reason=reason, dur=dur, kb=_btn("🔊 Unmute", f"mod:q:um:{r.target}"))


@command("tmute", "tempmute", cat="member", usage="/tmute <target> <duration> [reason]", desc="Temporary mute; permissions come back automatically")
async def cmd_tmute(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True)
    dur, reason = split_duration(r.args)
    if dur is None:
        raise ModError("⏱ Give a duration: <code>/tmute @user 30m reason</code> (s, m, h, d, w).")
    await api.flair(m, "mute")
    await actions.mute(bot, r.chat_id, r.target, actor=r.actor, reason=reason, duration=dur, member=r.member)
    await _done(r, "⏳🔇", "is <b>temporarily muted</b>", reason=reason, dur=dur, kb=_btn("🔊 Unmute", f"mod:q:um:{r.target}"))


@command("unmute", "unsilence", cat="member", usage="/unmute <target>", desc="Restore a muted / restricted member's permissions", shared=True)
async def cmd_unmute(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True, allow_admin_target=True)
    await actions.unmute(bot, r.chat_id, r.target, actor=r.actor)
    await _done(r, "🔊", "can talk again (<b>unmuted</b>)")


def _lock_tokens(args: list[str]) -> tuple[list[str], list[str]]:
    names, i = [], 0
    while i < len(args) and lock_name(args[i]):
        n = lock_name(args[i])
        if n not in names:
            names.append(n)
        i += 1
    return names, args[i:]


@command("restrict", cat="member", usage="/restrict <target> <types…> [duration] [reason]",
         desc="Limit ONE member: text media photos videos audios documents voice videonotes polls other previews invite pin info")
async def cmd_restrict(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True)
    names, rest = _lock_tokens(r.args)
    if not names:
        raise ModError("ℹ️ Say what to restrict, e.g. <code>/restrict @user media stickers 1d</code>.\nTypes: " + ", ".join(
            n for n, v in LOCKS.items() if v[0] and n != "all"))
    dur, reason = split_duration(rest)
    await actions.restrict(bot, r.chat_id, r.target, names, actor=r.actor, reason=reason, duration=dur)
    await _done(r, "🚫", f"is now restricted from <b>{esc(', '.join(names))}</b>", reason=reason, dur=dur, kb=_btn("✅ Lift", f"mod:q:ur:{r.target}"))


@command("unrestrict", cat="member", usage="/unrestrict <target> [types…]", desc="Lift restrictions (all, or just the listed types)")
async def cmd_unrestrict(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True, allow_admin_target=True)
    names, _ = _lock_tokens(r.args)
    await actions.unrestrict(bot, r.chat_id, r.target, names, actor=r.actor)
    await _done(r, "✅", "restrictions lifted" + (f": <b>{esc(', '.join(names))}</b>" if names else ""))


# ----------------------------------------------------------------------------------------------------- warnings
@command("warn", "warnuser", cat="member", usage="/warn <target> [reason]", desc="Warn a member; the group's warn limit triggers the automatic punishment")
async def cmd_warn(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True)
    reason = " ".join(r.args)[:300]
    text, punished = await actions.warn(bot, r.chat_id, r.target, actor=r.actor, reason=reason, st=r.st)
    await reply(m, f"{await r.who()}\n{text}\n👮 {mention(r.actor, m.from_user.full_name)}",
                None if punished else _btn("➖ Remove warning", f"mod:q:uw:{r.target}"))


@command("unwarn", "rmwarn", cat="member", usage="/unwarn <target>", desc="Remove a member's latest warning")
async def cmd_unwarn(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True, allow_admin_target=True)
    if not await store.remove_last_warn(r.chat_id, r.target, r.st["warn"]["expire"]):
        raise ModError("ℹ️ That user has no active warnings.")
    await store.log_action(r.chat_id, r.actor, r.target, "unwarn")
    n = len(await store.list_warns(r.chat_id, r.target, r.st["warn"]["expire"]))
    await _done(r, "➖", f"lost one warning ({n}/{r.st['warn']['limit']} left)")


@command("warnings", "warns", cat="member", usage="/warnings [target]", gate=False,
         desc="Show warnings (yours, or a member's); also shows the group's limit")
async def cmd_warnings(m: Message, command: CommandObject, bot: Bot):
    st = await store.get_settings(m.chat.id)
    args = (command.args or "").split()
    uid, user = m.from_user.id, m.from_user
    if args or m.reply_to_message:
        t, _, u = await perms.resolve_target(bot, m, args)
        if t is not None and t != uid:
            await perms.require_actor(bot, m.chat.id, uid, "restrict", fresh=False)
            uid, user = t, u
    rows = await store.list_warns(m.chat.id, uid, st["warn"]["expire"])
    w = st["warn"]
    head = f"📋 {await perms.label(bot, m.chat.id, uid, user)}: <b>{len(rows)}/{w['limit']}</b> warnings (at the limit → {w['mode']})"
    if not rows:
        return await reply(m, head + "\n✨ Clean record.")
    lines = [f"{i}. {esc(x.reason or 'no reason')}" for i, x in enumerate(rows[-10:], 1)]
    await reply(m, head + "\n" + "\n".join(lines))


@command("resetwarns", "clearwarns", cat="member", usage="/resetwarns <target>", desc="Clear ALL of one member's warnings")
async def cmd_resetwarns(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", target=True, allow_admin_target=True)
    n = await store.reset_warns(r.chat_id, r.target)
    await store.log_action(r.chat_id, r.actor, r.target, "resetwarns", detail=f"{n} cleared")
    await _done(r, "🧹", f"- <b>{n}</b> warning(s) cleared" if n else "had no warnings")


# ----------------------------------------------------------------------------------------------------- quick buttons
@router.callback_query(F.data.startswith("mod:q:"))
async def quick_action(cb: CallbackQuery, bot: Bot):
    """One-tap undo buttons under moderation messages. The tapper must be an admin with the Ban users right (checked live)."""
    _, _, verb, uid_s = cb.data.split(":")
    if cb.message is None:
        return await cb.answer()
    chat_id, uid = cb.message.chat.id, int(uid_s)
    await perms.require_actor(bot, chat_id, cb.from_user.id, "restrict")
    await perms.require_bot(bot, chat_id, "restrict")
    st = await store.get_settings(chat_id)
    if verb == "ub":
        await actions.unban(bot, chat_id, uid, actor=cb.from_user.id)
        msg = "🔓 Unbanned."
    elif verb == "um":
        await actions.unmute(bot, chat_id, uid, actor=cb.from_user.id)
        msg = "🔊 Unmuted."
    elif verb == "ur":
        await actions.unrestrict(bot, chat_id, uid, [], actor=cb.from_user.id)
        msg = "✅ Restrictions lifted."
    elif verb == "uw":
        if not await store.remove_last_warn(chat_id, uid, st["warn"]["expire"]):
            raise ModError("ℹ️ No active warnings left.")
        await store.log_action(chat_id, cb.from_user.id, uid, "unwarn")
        msg = "➖ Warning removed."
    else:
        return await cb.answer()
    await cb.answer(msg)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.reply(f"{msg} <i>(by {mention(cb.from_user.id, cb.from_user.full_name)})</i>")
    except Exception:
        pass


# ----------------------------------------------------------------------------------------------------- information
@command("userinfo", "uinfo", cat="member", usage="/userinfo [target]", shared=True, gate=False,
         desc="Everything the moderation system knows about a member (admins only)")
async def cmd_userinfo(m: Message, command: CommandObject, bot: Bot):
    await perms.require_actor(bot, m.chat.id, m.from_user.id, None)
    args = (command.args or "").split()
    uid, _, user = await perms.resolve_target(bot, m, args)
    uid = uid or m.from_user.id
    st = await store.get_settings(m.chat.id)
    member = await perms.get_member(bot, m.chat.id, uid)
    row = await store.member_row(m.chat.id, uid)
    u = (member.user if member else None) or user
    name = (u.full_name if u else None) or (row.name if row else None) or str(uid)
    uname = (u.username if u else None) or (row.username if row else None)
    status = status_of(member) if member else "not in group / never seen"
    warns = await store.list_warns(m.chat.id, uid, st["warn"]["expire"])
    hist = await store.recent_actions(m.chat.id, 5, target_id=uid)
    appr = uid in await store.approved_set(m.chat.id)
    pun = [p for p in await store.active_punishments(m.chat.id, 200) if p.user_id == uid]
    why, _ = await perms.protection_reason(bot, m.chat.id, uid, st)
    lines = [f"👤 <b>{esc(name)}</b>" + (f" @{esc(uname)}" if uname else ""), f"🆔 <code>{uid}</code>",
             f"📌 Status: <b>{esc(status)}</b>" + (f" · title: {esc(member.custom_title)}" if getattr(member, "custom_title", None) else ""),
             f"⚠️ Warnings: <b>{len(warns)}/{st['warn']['limit']}</b>   ✅ Approved: {'yes' if appr else 'no'}"]
    if why:
        lines.append(f"🛡 Protected: {why}")
    if row:
        lines.append(f"💬 Tracked messages: {row.msgs} · first seen {_ago(row.first_seen)} · last seen {_ago(row.last_seen)}")
    for p in pun:
        lines.append(f"⏳ Active {p.kind} until {fmt_duration(max(0, p.expires_at - store.now()))} from now")
    if hist:
        lines.append("\n<b>Recent actions</b>")
        lines += [f"• {_ago(h.created_at)}: {h.action}" + (f" ({esc(h.reason[:40])})" if h.reason else "") + ("" if h.result == "ok" else f" [{h.result}]") for h in hist]
    await reply(m, "\n".join(lines))


def _ago(ts: int | None) -> str:
    return "never" if not ts else fmt_duration(max(1, store.now() - ts)) + " ago"


@command("checkadmin", "isadmin", cat="admin", usage="/checkadmin [target]", gate=False,
         desc="Is this person an admin? (no target: checks you and me, incl. the rights I'm missing)")
async def cmd_checkadmin(m: Message, command: CommandObject, bot: Bot):
    args = (command.args or "").split()
    uid, _, _ = await perms.resolve_target(bot, m, args)
    if uid is None:
        me = await perms.bot_member(bot, m.chat.id, force=True)
        you = (await perms.admins(bot, m.chat.id)).get(m.from_user.id)
        need = ("restrict", "delete", "pin", "promote", "info", "invite")
        miss = [perms.RIGHT_LABEL[r] for r in need if not perms.has_right(me, r)]
        lines = [f"🙋 You: <b>{_role(m.from_user.id, you)}</b>",
                 f"🤖 Me: <b>{'admin' if status_of(me) in ('administrator', 'creator') else 'NOT an admin'}</b>"]
        lines.append("✅ I have every right I may need." if not miss and me else "⚠️ Missing for me: " + (", ".join(miss) or "all (I'm not admin)"))
        return await reply(m, "\n".join(lines))
    member = await perms.get_member(bot, m.chat.id, uid)
    st = await store.get_settings(m.chat.id)
    why, _ = await perms.protection_reason(bot, m.chat.id, uid, st)
    lines = [f"🔍 {await perms.label(bot, m.chat.id, uid)}: <b>{_role(uid, member)}</b>", f"Can the moderation system act on them? <b>{'no - ' + why if why else 'yes'}</b>"]
    await reply(m, "\n".join(lines))


def _role(uid: int, member) -> str:
    if uid == perms.owner_id():
        return "bot owner"
    return {"creator": "group owner", "administrator": "admin"}.get(status_of(member), "regular member")
