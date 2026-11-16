"""3. Administrator management."""
from __future__ import annotations

from aiogram import Bot
from aiogram.filters import CommandObject
from aiogram.types import Message

from . import actions, confirm, perms, store
from .base import Req, command, prepare, reply
from .perms import ModError, RIGHT_LABEL, esc, status_of, tg
from .util import mention

# Telegram admin right -> label, and short names an admin can type
RIGHTS = {"can_manage_chat": "Manage group", "can_delete_messages": "Delete messages", "can_restrict_members": "Ban users",
          "can_invite_users": "Invite users", "can_pin_messages": "Pin messages", "can_change_info": "Change group info",
          "can_promote_members": "Add new admins", "can_manage_video_chats": "Manage video chats", "can_manage_topics": "Manage topics"}
SHORT = {"delete": "can_delete_messages", "ban": "can_restrict_members", "restrict": "can_restrict_members", "pin": "can_pin_messages",
         "invite": "can_invite_users", "info": "can_change_info", "promote": "can_promote_members", "voice": "can_manage_video_chats",
         "calls": "can_manage_video_chats", "topics": "can_manage_topics", "manage": "can_manage_chat"}
PRESETS = {"basic": ["can_delete_messages", "can_restrict_members", "can_pin_messages", "can_invite_users", "can_manage_video_chats", "can_manage_chat"],
           "mod": ["can_delete_messages", "can_restrict_members", "can_pin_messages", "can_invite_users", "can_manage_video_chats", "can_manage_chat", "can_manage_topics"],
           "full": ["can_manage_chat", "can_delete_messages", "can_restrict_members", "can_invite_users", "can_pin_messages", "can_change_info",
                    "can_manage_video_chats", "can_manage_topics"],          # "full" deliberately does NOT include can_promote_members
           "none": []}


def parse_rights(args: list[str], default: str | None) -> tuple[list[str], str]:
    """['mod', '+info', '-pin', 'Head', 'Mod'] -> (rights, title)"""
    rights: list[str] | None = None
    i = 0
    while i < len(args):
        a = args[i].lower()
        if a in PRESETS:
            rights = list(PRESETS[a])
        elif a.lstrip("+-") in SHORT and a[0] in "+-":
            rights = rights if rights is not None else list(PRESETS[default or "none"])
            f = SHORT[a[1:]]
            if a[0] == "+" and f not in rights:
                rights.append(f)
            if a[0] == "-" and f in rights:
                rights.remove(f)
        elif a == "all":
            rights = list(PRESETS["full"]) + ["can_promote_members"]
        else:
            break
        i += 1
    if rights is None:
        rights = list(PRESETS[default or "none"])
    return rights, " ".join(args[i:])[:16]


async def _check_grantable(bot: Bot, chat_id: int, rights: list[str]) -> None:
    me = await perms.bot_member(bot, chat_id, force=True)
    missing = [RIGHTS[f] for f in rights if status_of(me) != "creator" and not getattr(me, f, False)]
    if missing:
        raise ModError("🔒 I can only hand out rights I have myself. I'm missing: <b>" + esc(", ".join(missing)) + "</b>.")


async def _apply_rights(bot: Bot, chat_id: int, uid: int, rights: list[str]) -> None:
    kw = {f: (f in rights) for f in RIGHTS}
    await tg(bot.promote_chat_member, chat_id, uid, is_anonymous=False, **kw)


def _rights_line(rights: list[str]) -> str:
    return ", ".join(RIGHTS[f] for f in rights) or "no rights"


async def _target_ok_for_admin_change(r: Req) -> None:
    if r.member is not None and status_of(r.member) == "creator":
        raise ModError("🛡 The group owner can't be changed by anyone.")


@command("promote", cat="admin", usage="/promote <target> [basic|mod|full|all] [+right -right …] [title]",
         desc="Make someone admin (rights presets: basic, mod, full; tune with +pin -info). Asks YES/NO first")
async def cmd_promote(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="promote", target=True, allow_admin_target=True)
    await _target_ok_for_admin_change(r)
    if r.target == bot.id or r.target == perms.owner_id():
        raise ModError("🛡 No need - that account can't be promoted this way.")
    rights, title = parse_rights(r.args, "basic")
    await _check_grantable(bot, r.chat_id, rights)
    is_admin = perms.is_staff_status(status_of(r.member))
    await confirm.request(m, bot, "promote", {"uid": r.target, "rights": rights, "title": title}, title="Promote to admin",
                          lines=[f"👤 {await r.who()}" + (" <i>(already an admin: rights will be replaced)</i>" if is_admin else ""),
                                 f"🔑 Rights: {esc(_rights_line(rights))}", *([f"🏷 Title: {esc(title)}"] if title else [])],
                          warning="Admins can act on members. Only continue if you trust this person.")


@command("setadmin", cat="admin", usage="/setadmin <target> <basic|mod|full|none|+right -right …> [title]",
         desc="Change an admin's rights exactly (confirmed with YES/NO)")
async def cmd_setadmin(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="promote", target=True, allow_admin_target=True)
    await _target_ok_for_admin_change(r)
    if r.target in (bot.id, perms.owner_id()):
        raise ModError("🛡 That account's rights can't be changed here.")
    if not perms.is_staff_status(status_of(r.member)):
        raise ModError("ℹ️ They aren't an admin yet - use /promote.")
    if not r.args:
        raise ModError("ℹ️ Say what to set, e.g. <code>/setadmin @user mod +info</code> or <code>/setadmin @user none</code>.")
    rights, title = parse_rights(r.args, "none")
    await _check_grantable(bot, r.chat_id, rights)
    await confirm.request(m, bot, "promote", {"uid": r.target, "rights": rights, "title": title, "set": True}, title="Change admin rights",
                          lines=[f"👤 {await r.who()}", f"🔑 New rights: {esc(_rights_line(rights))}", *([f"🏷 Title: {esc(title)}"] if title else [])],
                          warning="Their current rights will be replaced. Telegram only lets me edit admins that I promoted.")


async def _run_promote(ctx: confirm.Ctx) -> str:
    p, bot = ctx.payload, ctx.bot
    uid, rights, title = int(p["uid"]), p["rights"], p.get("title", "")
    st = await store.get_settings(ctx.chat_id)
    why, member = await perms.protection_reason(bot, ctx.chat_id, uid, st)
    if status_of(member) == "creator" or uid in (bot.id, perms.owner_id()):
        raise ModError("🛡 Not possible: that account can't be changed.")
    await _check_grantable(bot, ctx.chat_id, rights)
    try:
        await _apply_rights(bot, ctx.chat_id, uid, rights)
    except Exception as e:
        await store.log_action(ctx.chat_id, ctx.issuer, uid, "set-admin" if p.get("set") else "promote", result="failed", detail=str(e)[:200])
        raise ModError(perms.explain(e))
    perms.invalidate(ctx.chat_id)
    note = ""
    if title:
        try:
            await tg(bot.set_chat_administrator_custom_title, ctx.chat_id, uid, title)
        except Exception as e:
            note = "\n⚠️ Rights applied, but the title failed: " + perms.explain(e)
    await store.log_action(ctx.chat_id, ctx.issuer, uid, "set-admin" if p.get("set") else "promote", reason=_rights_line(rights) + (f" | title: {title}" if title else ""))
    return f"✅ {await perms.label(bot, ctx.chat_id, uid)} is now an admin with: {esc(_rights_line(rights))}" + (f"\n🏷 {esc(title)}" if title and not note else "") + note


confirm.register("promote", confirm.Spec(actor_right="promote", bot_right="promote", run=_run_promote))


@command("demote", cat="admin", usage="/demote <target>", desc="Remove someone's admin rights (asks YES/NO). Telegram only allows it for admins I promoted")
async def cmd_demote(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="promote", target=True, allow_admin_target=True)
    await _target_ok_for_admin_change(r)
    if r.target in (bot.id, perms.owner_id()) or r.target in r.st.get("protected_ids", []):
        raise ModError("🛡 That admin is protected and can't be demoted by me.")
    if not perms.is_staff_status(status_of(r.member)):
        raise ModError("ℹ️ They aren't an admin.")
    await confirm.request(m, bot, "demote", {"uid": r.target}, title="Demote admin",
                          lines=[f"👤 {await r.who()}", "🔑 All admin rights will be removed."],
                          warning="Telegram only lets me demote admins that I promoted myself.")


async def _run_demote(ctx: confirm.Ctx) -> str:
    uid = int(ctx.payload["uid"])
    st = await store.get_settings(ctx.chat_id)
    why, member = await perms.protection_reason(ctx.bot, ctx.chat_id, uid, st)
    if uid in (ctx.bot.id, perms.owner_id()) or uid in st.get("protected_ids", []) or status_of(member) == "creator":
        raise ModError("🛡 Not possible: that admin is protected.")
    try:
        await _apply_rights(ctx.bot, ctx.chat_id, uid, [])
    except Exception as e:
        await store.log_action(ctx.chat_id, ctx.issuer, uid, "demote", result="failed", detail=str(e)[:200])
        raise ModError(perms.explain(e))
    perms.invalidate(ctx.chat_id)
    await store.log_action(ctx.chat_id, ctx.issuer, uid, "demote")
    return f"✅ {await perms.label(ctx.bot, ctx.chat_id, uid)} is no longer an admin."


confirm.register("demote", confirm.Spec(actor_right="promote", bot_right="promote", run=_run_demote))


@command("title", cat="admin", usage="/title <target> <text>", desc="Set an admin's custom title (max 16 chars, no emoji; only admins I promoted)")
async def cmd_title(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="promote", target=True, allow_self=True, allow_admin_target=True)
    text = " ".join(r.args)[:16]
    if not text:
        raise ModError("ℹ️ Usage: <code>/title @admin Head Mod</code>")
    if not perms.is_staff_status(status_of(r.member)):
        raise ModError("ℹ️ Titles only exist for admins.")
    try:
        await tg(bot.set_chat_administrator_custom_title, r.chat_id, r.target, text)
    except Exception as e:
        await store.log_action(r.chat_id, r.actor, r.target, "title", result="failed", detail=str(e)[:200])
        raise ModError(perms.explain(e))
    perms.invalidate(r.chat_id)
    await store.log_action(r.chat_id, r.actor, r.target, "title", reason=text)
    await reply(m, f"🏷 {await r.who()} is now <b>{esc(text)}</b>.")


def _admin_line(a) -> str:
    badge = "👑" if status_of(a) == "creator" else "🤖" if a.user.is_bot else "👮"
    t = f" · <i>{esc(a.custom_title)}</i>" if getattr(a, "custom_title", None) else ""
    anon = " 🕶" if getattr(a, "is_anonymous", False) else ""
    return f"{badge} {mention(a.user.id, a.user.full_name)}{t}{anon}"


@command("adminlist", "admins", cat="admin", usage="/adminlist", desc="List the group's admins with their titles", gate=False)
async def cmd_adminlist(m: Message, bot: Bot):
    ads = list((await perms.admins(bot, m.chat.id, force=True)).values())
    ads.sort(key=lambda a: (status_of(a) != "creator", a.user.is_bot, a.user.full_name.lower()))
    await reply(m, f"👮 <b>Admins of {esc(m.chat.title)}</b> ({len(ads)})\n" + "\n".join(_admin_line(a) for a in ads))


@command("staff", cat="admin", usage="/staff", desc="Admins plus the protected accounts the moderation system will never touch", gate=False)
async def cmd_staff(m: Message, bot: Bot):
    st = await store.get_settings(m.chat.id)
    ads = list((await perms.admins(bot, m.chat.id, force=True)).values())
    humans = [a for a in ads if not a.user.is_bot]
    lines = [f"🏛 <b>Staff of {esc(m.chat.title)}</b>", *(_admin_line(a) for a in humans)]
    prot = st.get("protected_ids", [])
    lines.append("\n🛡 <b>Protected</b> (never moderated): all admins, the bot owner, me" + (f", plus {len(prot)} configured account(s)" if prot else ""))
    for uid in prot:
        lines.append(f"• {await perms.label(bot, m.chat.id, uid)}")
    await reply(m, "\n".join(lines))


@command("admincache", cat="admin", usage="/admincache", desc="Refresh my cached list of admins and re-check my own rights")
async def cmd_admincache(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    perms.invalidate(r.chat_id)
    ads = await perms.admins(bot, r.chat_id, force=True)
    me = ads.get(bot.id)
    miss = [RIGHT_LABEL[x] for x in ("restrict", "delete", "pin", "promote", "info") if not perms.has_right(me, x)]
    await reply(m, f"♻️ Admin cache refreshed: <b>{len(ads)}</b> admins." + ("\n⚠️ I'm missing: " + ", ".join(miss) if miss else "\n✅ I have all the rights I need."))


@command("admininfo", "ainfo", cat="admin", usage="/admininfo [target]", desc="An admin's exact rights, title and whether I can edit them", gate=False)
async def cmd_admininfo(m: Message, command: CommandObject, bot: Bot):
    uid, _, _ = await perms.resolve_target(bot, m, (command.args or "").split())
    uid = uid or m.from_user.id
    a = await perms.get_member(bot, m.chat.id, uid)
    if not perms.is_staff_status(status_of(a)):
        return await reply(m, f"ℹ️ {await perms.label(bot, m.chat.id, uid)} is not an admin.")
    owner = status_of(a) == "creator"
    lines = [f"{_admin_line(a)}", f"Role: <b>{'group owner' if owner else 'admin'}</b>" + (" · 🧾 bot owner" if uid == perms.owner_id() else "")]
    for f, lab in RIGHTS.items():
        lines.append(f"{'✅' if owner or getattr(a, f, False) else '❌'} {lab}")
    if not owner:
        lines.append(f"\n✏️ I {'can' if getattr(a, 'can_be_edited', False) else 'cannot'} change this admin (Telegram: only the promoter can).")
    await reply(m, "\n".join(lines))


@command("permissions", "perms", cat="admin", usage="/permissions [reset]",
         desc="Show the group's member permissions and active locks; 'reset' lifts every lock (asks YES/NO)")
async def cmd_permissions(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    cur = await actions.default_perm_dict(bot, r.chat_id)
    if (command.args or "").strip().lower() == "reset":
        await perms.require_bot(bot, r.chat_id, "restrict")
        await confirm.request(m, bot, "permreset", {}, title="Reset ALL permission locks",
                              lines=["Every <code>/lock</code> will be switched off and members get <b>all</b> permissions back (send, media, polls, links, invite, pin, info)."],
                              warning="This changes what every member of the group can do.")
        return
    nice = {"can_send_messages": "Send messages", "can_send_photos": "Photos", "can_send_videos": "Videos", "can_send_audios": "Audio",
            "can_send_documents": "Files", "can_send_voice_notes": "Voice", "can_send_video_notes": "Video notes", "can_send_polls": "Polls",
            "can_send_other_messages": "Stickers/GIFs/games", "can_add_web_page_previews": "Link previews", "can_invite_users": "Invite users",
            "can_pin_messages": "Pin messages", "can_change_info": "Change info"}
    locks = [k for k, v in r.st["locks"].items() if v]
    await reply(m, "🔐 <b>Default member permissions</b>\n" + "\n".join(f"{'✅' if cur.get(k) else '🚫'} {v}" for k, v in nice.items())
                + f"\n\n🔒 Active locks: {esc(', '.join(locks)) if locks else 'none'}")


async def _run_permreset(ctx: confirm.Ctx) -> str:
    from .commands_msg import apply_locks
    st = await store.get_settings(ctx.chat_id)
    names = [k for k, v in st["locks"].items() if v]
    cur = await actions.default_perm_dict(ctx.bot, ctx.chat_id)
    full = {f: True for f in cur}
    try:
        await tg(ctx.bot.set_chat_permissions, ctx.chat_id, actions.perms_obj(full), use_independent_chat_permissions=True)
    except Exception as e:
        await store.log_action(ctx.chat_id, ctx.issuer, None, "permreset", result="failed", detail=str(e)[:200])
        raise ModError(perms.explain(e))
    await store.save_settings(ctx.chat_id, lambda s: s.update(locks={}))
    await store.log_action(ctx.chat_id, ctx.issuer, None, "permreset", detail=f"{len(names)} locks cleared")
    return "✅ All permission locks are off and members have every permission back."


confirm.register("permreset", confirm.Spec(actor_right="info", bot_right="restrict", run=_run_permreset))
