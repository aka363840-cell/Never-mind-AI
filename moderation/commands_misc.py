"""Reports, moderation log, backup / restore, community health & security reports, help."""
from __future__ import annotations

import json
import logging

from aiogram import Bot, F
from aiogram.filters import CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from . import actions, confirm, perms, store
from .base import COMMANDS, HELP, command, prepare, reply, router, throttled
from .perms import ModError, esc, tg
from .util import fmt_duration, mention

log = logging.getLogger("moderation.misc")


# ------------------------------------------------------------------------------------------------------------ reports
def _report_kb(rid: int) -> InlineKeyboardMarkup:
    b = lambda t, a: B(text=t, callback_data=f"mod:rp:{a}:{rid}")      # noqa: E731
    return InlineKeyboardMarkup(inline_keyboard=[[b("🗑 Delete msg", "del"), b("⚠️ Warn", "warn")], [b("🔇 Mute 1h", "mute"), b("🔨 Ban", "ban")], [b("✅ Resolve", "ok"), b("🙈 Dismiss", "no")]])


@command("report", "reportuser", cat="report", usage="/report [reason] (reply to the message)  or  /reportuser <@user|id> [reason]", gate=False,
         desc="Report a member / message to the admins (rate-limited)")
async def cmd_report(m: Message, command: CommandObject, bot: Bot):
    if m.from_user is None or (m.sender_chat and m.sender_chat.id == m.chat.id):
        raise ModError("🕶 Anonymous admins can handle things directly - no report needed.")
    st = await store.get_settings(m.chat.id)
    if not st["report"]["on"]:
        raise ModError("ℹ️ Reports are switched off in this group.")
    uid = m.from_user.id
    if throttled(uid, f"report:{m.chat.id}", float(st["report"]["cooldown"])):
        raise ModError(f"⏳ Please wait {st['report']['cooldown']}s between reports.")
    target, rest, user = await perms.resolve_target(bot, m, (command.args or "").split())
    if target is None:
        raise ModError("🎯 Reply to the message you want to report, or use <code>/reportuser @username reason</code>.")
    if target == uid:
        raise ModError("🙃 You can't report yourself.")
    why, _ = await perms.protection_reason(bot, m.chat.id, target, st)
    if why:
        raise ModError(f"ℹ️ That account is {why}; reports about it can't be handled here.")
    rep = m.reply_to_message
    mid = rep.message_id if rep else None
    excerpt = ((rep.text or rep.caption or f"[{_kind(rep)}]") if rep else "")[:200]
    reason = " ".join(rest)[:300]
    rid = await store.add_report(m.chat.id, uid, target, mid, excerpt, reason)
    if rid is None:
        raise ModError("ℹ️ You already reported that message - the admins have been notified.")
    admins = [a for a in (await perms.admins(bot, m.chat.id)).values() if not a.user.is_bot and not getattr(a, "is_anonymous", False)]
    ping = "".join(f'<a href="tg://user?id={a.user.id}">\u200b</a>' for a in admins[:15])
    link = ""
    if mid:
        ch = m.chat
        link = f'\n🔗 <a href="https://t.me/{ch.username}/{mid}">message</a>' if ch.username else f'\n🔗 <a href="https://t.me/c/{str(ch.id)[4:]}/{mid}">message</a>'
    text = (f"🚩 <b>Report #{rid}</b>{ping}\n👤 Reported: {await perms.label(bot, m.chat.id, target, user)}\n🙋 By: {mention(uid, m.from_user.full_name)}"
            + (f"\n📝 {esc(reason)}" if reason else "") + (f"\n💬 <i>{esc(excerpt)}</i>" if excerpt else "") + link)
    sent = await m.reply(text, reply_markup=_report_kb(rid), disable_web_page_preview=True)
    for a in admins[:15]:                                   # best-effort DM: only works for admins who started the bot
        try:
            await bot.send_message(a.user.id, f"🚩 New report in <b>{esc(m.chat.title)}</b> (#{rid})" + (f"\n📝 {esc(reason)}" if reason else "") + f"\n{sent.get_url() or ''}")
        except Exception:
            pass
    await store.log_action(m.chat.id, uid, target, "report", reason=reason, detail=f"#{rid}")


def _kind(msg) -> str:
    from .defaults import kinds_of
    return ", ".join(sorted(kinds_of(msg))) or "message"


@command("reports", cat="report", usage="/reports", desc="Pending reports with one-tap actions (admins)")
async def cmd_reports(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    rows = await store.pending_reports(r.chat_id, 8)
    counts = await store.report_counts(r.chat_id)
    head = f"🚩 <b>Reports</b> - pending {counts.get('pending', 0)} · resolved {counts.get('resolved', 0)} · dismissed {counts.get('dismissed', 0)}"
    if not rows:
        return await reply(m, head + "\n✨ Nothing pending.")
    lines = [head] + [f"<b>#{x.id}</b> {await perms.label(bot, r.chat_id, x.target_id)} - {esc(x.reason or x.excerpt or 'no reason')[:80]} ({fmt_duration(store.now() - x.created_at)} ago)" for x in rows]
    kb = InlineKeyboardMarkup(inline_keyboard=[[B(text=f"Open #{x.id}", callback_data=f"mod:rp:open:{x.id}") for x in rows[i:i + 4]] for i in range(0, len(rows), 4)])
    await reply(m, "\n".join(lines), kb)


@router.callback_query(F.data.startswith("mod:rp:"))
async def report_action(cb: CallbackQuery, bot: Bot):
    _, _, act, rid_s = cb.data.split(":")
    if cb.message is None:
        return await cb.answer()
    rep = await store.get_report(int(rid_s))
    chat_id = cb.message.chat.id
    if rep is None or rep.chat_id != chat_id:
        return await cb.answer("Report not found.", show_alert=True)
    actor = cb.from_user.id
    need = {"del": "delete", "warn": "restrict", "mute": "restrict", "ban": "restrict"}.get(act)
    await perms.require_actor(bot, chat_id, actor, need)
    if act == "open":
        await cb.message.answer(f"🚩 <b>Report #{rep.id}</b> [{rep.status}]\n👤 {await perms.label(bot, chat_id, rep.target_id)}\n📝 {esc(rep.reason or '-')}\n💬 <i>{esc(rep.excerpt or '-')}</i>",
                                reply_markup=_report_kb(rep.id) if rep.status == "pending" else None)
        return await cb.answer()
    if rep.status != "pending":
        return await cb.answer(f"Already {rep.status}.", show_alert=True)
    st = await store.get_settings(chat_id)
    if act in ("del", "warn", "mute", "ban"):
        await perms.require_bot(bot, chat_id, need)
        why, member = await perms.protection_reason(bot, chat_id, rep.target_id, st)
        if why and act != "del":
            return await cb.answer(f"🛡 Can't: the target is {why}.", show_alert=True)
    if not await store.resolve_report(rep.id, actor, "dismissed" if act == "no" else "resolved", act):
        return await cb.answer("Someone already handled this report.", show_alert=True)       # atomic: no double execution
    try:
        note = {"ok": "marked resolved", "no": "dismissed"}.get(act, "")
        if act == "del":
            if not rep.message_id or not await actions.delete_message(bot, chat_id, rep.message_id):
                raise ModError("🗑 I couldn't delete that message (gone or too old).")
            note = "message deleted"
        elif act == "warn":
            txt, _ = await actions.warn(bot, chat_id, rep.target_id, actor=actor, reason=f"report #{rep.id}: {rep.reason}"[:300], st=st)
            note = "warned"
        elif act == "mute":
            await actions.mute(bot, chat_id, rep.target_id, actor=actor, reason=f"report #{rep.id}", duration=3600)
            note = "muted for 1h"
        elif act == "ban":
            await actions.ban(bot, chat_id, rep.target_id, actor=actor, reason=f"report #{rep.id}: {rep.reason}"[:300])
            note = "banned"
    except Exception:
        await store.reopen_report(rep.id)
        raise
    await store.log_action(chat_id, actor, rep.target_id, f"report-{act}", detail=f"#{rep.id}")
    await cb.answer("Done: " + note)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.message.reply(f"✅ Report #{rep.id} {note} by {mention(actor, cb.from_user.full_name)}.")
    except Exception:
        pass


# ------------------------------------------------------------------------------------------------------------ log
@command("modlog", "mlogs", cat="report", usage="/modlog [target] [count]", desc="Moderation history: who did what, to whom, when, result")
async def cmd_modlog(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    target, rest, _ = await perms.resolve_target(bot, m, r.args)
    n = next((int(a) for a in rest if a.isdigit() and int(a) <= 30), 10)
    rows = await store.recent_actions(r.chat_id, n, target_id=target)
    if not rows:
        return await reply(m, "📒 No moderation records yet.")
    out = []
    for x in rows:
        who = "🤖 auto" if x.source in ("auto", "scheduler") and not x.actor_id else await perms.label(bot, r.chat_id, x.actor_id) if x.actor_id else "—"
        tgt = f" → {await perms.label(bot, r.chat_id, x.target_id)}" if x.target_id else ""
        flag = "" if x.result == "ok" else f" ❌{x.result}"
        out.append(f"• <i>{fmt_duration(store.now() - x.created_at)} ago</i> {who}{tgt}: <b>{esc(x.action)}</b>"
                   + (f" {fmt_duration(x.duration)}" if x.duration else "") + (f" - {esc(x.reason[:60])}" if x.reason else "") + flag)
    await reply(m, "📒 <b>Moderation log</b>\n" + "\n".join(out))


# ------------------------------------------------------------------------------------------------------------ backup / restore
@command("modbackup", "backupmod", cat="report", usage="/modbackup", desc="Export this group's moderation settings, warnings, filters, approvals and tracked members as a file")
async def cmd_modbackup(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    data = await store.export_chat(r.chat_id)
    raw = json.dumps(data, ensure_ascii=False, indent=1).encode()
    await store.log_action(r.chat_id, r.actor, None, "modbackup", detail=f"{len(raw)} bytes")
    await m.reply_document(BufferedInputFile(raw, filename=f"moderation-backup-{abs(r.chat_id)}-{store.now()}.json"),
                           caption=f"💾 Backup: {len(data['warns'])} warnings · {len(data['filters'])} filters · {len(data['approved'])} approved · {len(data['members'])} tracked members.\n"
                                   "Restore with <code>/modrestore</code> (reply to this file).")


@command("modrestore", "restoremod", cat="report", usage="/modrestore (reply to a backup file)", desc="Restore from a backup file - merges missing records, replaces settings only after YES/NO")
async def cmd_modrestore(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    doc = m.reply_to_message.document if m.reply_to_message else None
    if doc is None or (doc.file_size or 0) > 8_000_000:
        raise ModError("↩️ Reply to a backup file (<code>moderation-backup-….json</code>, max 8 MB) with /modrestore.")
    data = await _load_backup(bot, doc.file_id)
    n = len(data["warns"]), len(data["filters"]), len(data["approved"]), len(data["members"])
    await confirm.request(m, bot, "modrestore", {"file_id": doc.file_id}, title="RESTORE moderation backup",
                          lines=[f"📦 Backup of chat <code>{data.get('chat_id')}</code> from {fmt_duration(store.now() - int(data.get('created_at', 0)))} ago.",
                                 f"Contains: {n[0]} warnings · {n[1]} filters · {n[2]} approved · {n[3]} tracked members.",
                                 "➕ Missing warnings / filters / approvals / members are <b>added</b>; existing ones are never overwritten or deleted.",
                                 "♻️ This group's moderation <b>settings are replaced</b> by the backup's."],
                          warning="Settings in the file overwrite the current ones.")


async def _load_backup(bot: Bot, file_id: str) -> dict:
    try:
        f = await tg(bot.get_file, file_id)
        buf = await bot.download_file(f.file_path)
        data = json.loads(buf.read().decode())
    except Exception:
        raise ModError("📄 I couldn't read that file as a moderation backup.")
    bad = store.describe_backup(data)
    if bad:
        raise ModError(f"📄 Can't restore: {esc(bad)}.")
    return data


async def _run_restore(ctx: confirm.Ctx) -> str:
    data = await _load_backup(ctx.bot, ctx.payload["file_id"])
    n = await store.import_chat(ctx.chat_id, data, replace_settings=True)
    await store.log_action(ctx.chat_id, ctx.issuer, None, "modrestore", detail=json.dumps(n))
    return ("✅ <b>Restore complete.</b>\n" f"Settings replaced · +{n['warns']} warnings · +{n['filters']} filters · +{n['approved']} approved · "
            f"+{n['members']} members · +{n['punishments']} scheduled punishments.")


confirm.register("modrestore", confirm.Spec(actor_right="info", bot_right=None, run=_run_restore))


# ------------------------------------------------------------------------------------------------------------ health & security report
@command("health", "communityhealth", cat="report", usage="/health", desc="Community health: activity, growth, warnings, reports, protection coverage")
async def cmd_health(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    ms, ws, rc = await store.member_stats(r.chat_id), await store.warn_stats(r.chat_id), await store.report_counts(r.chat_id)
    week = await store.action_counts(r.chat_id, store.now() - 7 * 86400)
    try:
        total = await tg(bot.get_chat_member_count, r.chat_id)
    except Exception:
        total = None
    on = sum(1 for k in ("flood", "spam", "antilink", "antibot", "antiraid", "captcha") if r.st[k]["on"])
    act_ratio = ms["active_7d"] / ms["tracked"] if ms["tracked"] else 0
    raids = week.get("raid", 0)
    score = max(0, min(100, int(40 + on * 7 + act_ratio * 20 - min(rc.get("pending", 0) * 3, 15) - min(raids * 8, 20) + (5 if ms["new_7d"] else 0))))
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    await reply(m, f"🩺 <b>Community health</b> - <code>{bar}</code> <b>{score}/100</b> <i>(heuristic)</i>\n"
                   f"👥 Members: {total if total is not None else '?'} (I track {ms['tracked']})\n"
                   f"💬 Active 24h: {ms['active_24h']} · 7d: {ms['active_7d']} · never spoke: {ms['lurkers']}\n"
                   f"🆕 New in 7d: {ms['new_7d']} · left: {ms['left']} · banned: {ms['banned']}\n"
                   f"⚠️ Active warnings: {ws['active']} ({ws['users']} members) · 🚩 pending reports: {rc.get('pending', 0)}\n"
                   f"🛡 Protections on: {on}/6 · level: <b>{r.st['protection']}</b>\n"
                   f"🔨 Actions in 7d: {sum(week.values())} · 🚨 raids: {raids}")


@command("securityreport", "secreport", cat="report", usage="/securityreport [days]", desc="Security report: what was caught, by which protection, and the most-punished members")
async def cmd_securityreport(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    days = int(r.args[0]) if r.args and r.args[0].isdigit() and 1 <= int(r.args[0]) <= 90 else 7
    since = store.now() - days * 86400
    cnt = await store.action_counts(r.chat_id, since)
    top = await store.top_targets(r.chat_id, since)
    auto = {k: v for k, v in cnt.items() if k.startswith(("auto-", "raid", "captcha", "suspicious", "flood", "spam"))}
    lines = [f"🛡 <b>Security report - last {days} day(s)</b>"]
    lines.append("<b>Actions</b>: " + (", ".join(f"{k} {v}" for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])[:12]) or "none"))
    lines.append("<b>Automatic protections</b>: " + (", ".join(f"{k} {v}" for k, v in sorted(auto.items(), key=lambda kv: -kv[1])) or "nothing triggered"))
    if top:
        lines.append("<b>Most actioned members</b>\n" + "\n".join(f"• {await perms.label(bot, r.chat_id, u)}: {n}" for u, n in top))
    lines.append(f"Raid mode: {'ACTIVE' if r.st['antiraid'].get('until', 0) > store.now() else 'off'} · lockdown: {'ACTIVE' if r.st['lockdown']['on'] else 'off'} · protection level: {r.st['protection']}")
    await reply(m, "\n".join(lines))


# ------------------------------------------------------------------------------------------------------------ help
CATS = {"member": "👤 Members", "mass": "📦 Mass actions", "admin": "👮 Admins", "message": "💬 Messages & locks", "settings": "⚙️ Group settings",
        "security": "🛡 Security", "report": "🚩 Reports, logs & more"}
NOTES = ("ℹ️ <b>Good to know</b>\n• Target = reply to a message, <code>@username</code> (members I have seen) or a numeric ID.\n"
         "• Durations: <code>30s 15m 2h 3d 1w 1h30m</code> (min 30s). Reasons are free text after them.\n"
         "• Serious actions ask <b>YES / NO</b> first; only you can answer, requests expire in 90s.\n"
         "• Admins, the bot owner, protected accounts and I are never moderated.\n"
         "• I must be an admin. Needed rights: Ban users, Delete messages, Pin messages, Add new admins (promote/demote), Change group info.\n"
         "• Telegram can't list all members: mass actions cover the members I have seen.\n"
         "• AI chat has no moderation powers - everything here is a dedicated command.\n"
         "• <code>/modpanel</code> opens the interactive dashboard.")


def _help_page(cat: str) -> str:
    items = [h for h in HELP if h["cat"] == cat]
    lines = [f"<b>{CATS[cat]}</b>"]
    for h in items:
        al = " ".join(f"/{n}" for n in h["names"][1:])
        lines.append(f"\n<code>{esc(h['usage'])}</code>" + (f"  <i>(aliases: {al})</i>" if al else "") + f"\n   {esc(h['desc'])}")
    return "\n".join(lines)[:3900]


def _help_kb() -> InlineKeyboardMarkup:
    rows = [[B(text=t, callback_data=f"mod:h:{k}")] for k, t in CATS.items()]
    rows = [[rows[i][0], rows[i + 1][0]] if i + 1 < len(rows) else [rows[i][0]] for i in range(0, len(rows), 2)]
    rows.append([B(text="ℹ️ Good to know", callback_data="mod:h:notes")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@command("modhelp", "moderation", cat="report", usage="/modhelp [command]", desc="This help: every moderation command with syntax", gate=False)
async def cmd_modhelp(m: Message, command: CommandObject):
    a = (command.args or "").strip().lstrip("/!").lower()
    if a:
        info = COMMANDS.get(a)
        h = next((x for x in HELP if a in x["names"]), None)
        if not h:
            raise ModError("❓ No such moderation command. Try <code>/modhelp</code>.")
        return await reply(m, f"<code>{esc(h['usage'])}</code>\n{esc(h['desc'])}" + ("\n<i>aliases: " + " ".join("/" + n for n in h["names"][1:]) + "</i>" if len(h["names"]) > 1 else ""))
    await reply(m, "🛡 <b>Moderation help</b>\nPick a category:", _help_kb())


@router.callback_query(F.data.startswith("mod:h:"))
async def help_cb(cb: CallbackQuery):
    key = cb.data.split(":", 2)[2]
    if cb.message is None:
        return await cb.answer()
    if key == "menu":
        text, kb = "🛡 <b>Moderation help</b>\nPick a category:", _help_kb()
    elif key == "notes" or key in CATS:
        text = NOTES if key == "notes" else _help_page(key)
        kb = InlineKeyboardMarkup(inline_keyboard=[[B(text="⬅️ Back", callback_data="mod:h:menu")]])
    else:
        return await cb.answer()
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await cb.answer()
