from __future__ import annotations

import asyncio
import logging
import os
import re

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton as B, Message

from keyboards.menus import kb
from config import get_settings
from services import admin as adm, backup as bk, users
from utils import brand
from services.errors import GameError
from services.kv import kv
from utils.fmt import esc, num, parse_amount
from utils.targets import resolve_target
from utils.telegram import safe_answer, safe_edit, safe_send

log = logging.getLogger(__name__)
router = Router()


class IsAdmin(BaseFilter):
    """Non-admins never match, so admin commands are invisible to them (no 'not authorised' leak)."""
    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u and adm.role(u.id) != "none")


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _target_and_args(m: Message, command: CommandObject):
    target, rest = await resolve_target(m, (command.args or "").split())
    if target is None:
        raise GameError("❌ Give a target: reply, @username or numeric ID.")
    return target, rest


async def _ask(m: Message, action: str, args: dict, summary: str):
    nonce = await adm.stage(m.from_user.id, action, args)
    await m.reply(f"⚠️ <b>Confirm</b>: {summary}\n<i>Expires in 90s.</i>",
                  reply_markup=kb([[B(text="✅ Confirm", callback_data=f"adm:ok:{nonce}"),
                                    B(text="✖️ Cancel", callback_data=f"adm:no:{nonce}")]]))


@router.message(Command("admin"))
async def admin_help(m: Message):
    await m.reply("🔧 <b>Admin</b> (" + adm.role(m.from_user.id) + ")\n/give /take /setbalance &lt;target&gt; &lt;amount&gt;\n/resetdiamonds &lt;amount&gt; (everyone's wallet = amount, owner)\n"
                  "/ban &lt;target&gt; [reason] · /unban · /mute · /unmute  (owner: no slash needed — reply with \"ban\" / \"mute\" / \"unban\")\n/userinfo &lt;target&gt; · /logs [n]\n"
                  "/broadcast [forward] &lt;text&gt; · /syncusers · /maintenance on|off · /stats · /backupdb · /restoredb · /emojiid · /seticon · /delicon · /setstart dm|gc · /aitest (owner) · /drop · /boss (in groups)\n\nTarget = reply, @user or ID. Works in DM and in groups. State changes need a confirm tap.")


@router.message(Command("stats"))
async def stats(m: Message):
    adm.authorize(m.from_user.id, "stats")
    d = await adm.server_stats()
    await m.reply(f"📊 <b>{esc(brand.name())} stats</b>\n"
                  f"👥 Known people: <b>{num(d['users'])}</b> (+{d['new_24h']} today, {d['active_24h']} active 24h)\n"
                  f"✅ Started the bot in DM: <b>{num(d['started'])}</b> · 👀 Only seen in groups: {num(d['group_only'])}"
                  + (f" · ❔ Not checked yet: {num(d['unverified'])} (run /syncusers)" if d['unverified'] else "") + "\n"
                  f"🚫 {d['banned']} banned · 🔇 {d['blocked']} blocked the bot · 👻 {d['deleted']} deleted accounts\n"
                  f"💬 Groups: <b>{d['groups']}</b> (+{d['groups_removed']} removed, kept for history)\n"
                  f"💎 Diamonds in circulation: <b>{num(d['circulation'])}</b>\n"
                  f"🧾 Transactions (24h): <b>{num(d['tx24'])}</b>\n🏰 Clans: {d['clans']} · 🐾 Pets: {d['pets']}\n"
                  f"💍 Marriages: {d['marriages']} · 👨‍👩‍👧 Relations: {d['relations']}\n"
                  f"🎁 Open group events: {d['open_events']}")


@router.message(Command("backup", "backupdb"))
async def backup_cmd(m: Message):
    adm.authorize(m.from_user.id, "backup")
    if m.chat.type != "private":
        raise GameError("🔒 Use /backup in a private chat with me.")
    path = await bk.make_backup(get_settings().database_url)
    if path is None:
        raise GameError("ℹ️ Automatic backup only works with SQLite. For PostgreSQL use <code>pg_dump</code>.")
    await m.reply_document(FSInputFile(path), caption=f"💾 Backup {path.name}\n{await bk.summary(path)}")


@router.message(Command("restoredb"))
async def restoredb_cmd(m: Message, bot: Bot):
    """Owner: reply to a backup .db file (sent by /backupdb) with /restoredb to bring all players' data back."""
    adm.authorize(m.from_user.id, "backup")
    if m.chat.type != "private":
        raise GameError("🔒 Use /restoredb in a private chat with me.")
    doc = m.reply_to_message.document if m.reply_to_message else None
    if doc is None:
        raise GameError("↩️ Reply to the backup <b>.db</b> file with /restoredb.")
    if (doc.file_size or 0) > 20 * 1024 * 1024:
        raise GameError("❌ Telegram lets bots download files up to 20 MB only.")
    bk.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = bk.BACKUP_DIR / f"upload_{m.from_user.id}.db"
    await bot.download(doc, destination=tmp)
    await kv.put(f"dbr:{m.from_user.id}", str(tmp), 120)
    await m.reply("⚠️ <b>Restore this backup?</b>\nCurrent data will be replaced (a safety copy is saved first).\n<i>Expires in 2 min.</i>",
                  reply_markup=kb([[B(text="✅ Restore", callback_data="dbr:ok"), B(text="✖️ Cancel", callback_data="dbr:no")]]))


@router.callback_query(F.data.startswith("dbr:"))
async def restoredb_cb(cb: CallbackQuery):
    adm.authorize(cb.from_user.id, "backup")
    path = await kv.take(f"dbr:{cb.from_user.id}")
    if cb.data == "dbr:no" or not path:
        await safe_edit(cb, "✖️ Cancelled or expired."); await safe_answer(cb); return
    try:
        n = await bk.restore_backup(get_settings().database_url, path)
    except Exception as e:
        await safe_edit(cb, f"❌ Restore failed: {esc(str(e))}"); await safe_answer(cb); return
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    await safe_edit(cb, f"✅ Restored. <b>{num(n)}</b> players are back. {brand.EMOJI}")
    await safe_answer(cb)


@router.message(Command("give", "take", "setbalance"))
async def balance_cmds(m: Message, command: CommandObject):
    action = command.command.lower()
    target, rest = await _target_and_args(m, command)
    amt = parse_amount(rest[0]) if rest else None
    if amt is None and not (action == "setbalance" and rest and rest[0] == "0"):
        raise GameError(f"Usage: /{action} &lt;target&gt; &lt;amount&gt;")
    amt = amt or 0
    await _ask(m, action, {"target": target, "amount": amt}, f"{action} <b>{num(amt)}</b> 💎 for <code>{target}</code>")


@router.message(Command("resetdiamonds"))
async def resetdiamonds_cmd(m: Message, command: CommandObject):
    """Owner: /resetdiamonds <amount> - EVERY player's wallet becomes <amount> and the bank is emptied. Confirm tap + automatic backup first."""
    adm.authorize(m.from_user.id, "resetdiamonds")
    arg = (command.args or "").split()
    amt = parse_amount(arg[0]) if arg else None
    if amt is None and not (arg and arg[0] == "0"):
        raise GameError("Usage: /resetdiamonds &lt;amount&gt;  (e.g. /resetdiamonds 1000)\nEvery player's wallet becomes that amount and the bank becomes 0.")
    amt = amt or 0
    await _ask(m, "resetdiamonds", {"amount": amt}, f"set <b>EVERY player's</b> wallet to <b>{num(amt)}</b> 💎 and empty all banks.\n"
               "A backup file is sent to you first, so /restoredb can undo this.")


async def _moderate(m: Message, action: str, target: int, rest: list[str]):
    if target == m.from_user.id or adm.role(target) == "owner":
        raise GameError("❌ Not allowed on that account.")
    args = {"target": target, "reason": " ".join(rest)[:200]}
    if action == "ban":
        await _ask(m, action, args, f"ban <code>{target}</code> {esc(args['reason'])}")
    else:   # reversible, low-risk: still logged, no confirm needed
        adm.authorize(m.from_user.id, action)
        await m.reply(await adm.execute(m.from_user.id, action, args))


@router.message(Command("ban", "unban", "mute", "unmute", "gameban", "gameunban", "gamemute", "gameunmute", "botban", "botunban", "botmute", "botunmute"))
async def moderation_cmds(m: Message, command: CommandObject):
    target, rest = await _target_and_args(m, command)
    await _moderate(m, command.command.lower().removeprefix("game").removeprefix("bot"), target, rest)


# Plain-text moderation (no slash): reply to someone (or add @user / ID) and write "ban", "ban karo", "isko mute kardo", "unban"...
_PLAIN_MOD = re.compile(
    r"^\s*(?:(?:isko|ise|usko|inko|unko)\s+)?(un)?(ban|mute|बैन|म्यूट)\b"
    r"(?:\s+(?:kar\s*do|kar\s*de|kardo|karde|karo|kr\s*do|kro|dalo|daldo|kar|करो|कर\s*दो|दो))?[\s.!]*(.*)$", re.I | re.S)


class PlainModeration(BaseFilter):
    """Matches only OWNER messages like 'ban karo' that ALSO carry a target; everything else falls through to normal handlers."""
    async def __call__(self, m: Message):
        txt = m.text or ""
        if not m.from_user or adm.role(m.from_user.id) != "owner":     # plain-text moderation: OWNER only
            return False
        if len(txt) > 120 or txt.startswith("/"):
            return False
        mt = _PLAIN_MOD.match(txt)
        if not mt:
            return False
        target, rest = await resolve_target(m, mt.group(3).split())
        if target is None:
            return False
        word = {"बैन": "ban", "म्यूट": "mute"}.get(mt.group(2).lower(), mt.group(2).lower())
        return {"plain_mod": (("un" if mt.group(1) else "") + word, target, rest)}


@router.message(PlainModeration())
async def plain_moderation(m: Message, plain_mod: tuple):
    action, target, rest = plain_mod
    await _moderate(m, action, target, rest)


@router.message(Command("userinfo"))
async def userinfo(m: Message, command: CommandObject):
    adm.authorize(m.from_user.id, "userinfo")
    target, _ = await _target_and_args(m, command)
    await send_userinfo(m, target)


async def send_userinfo(m: Message, target: int):
    d = await adm.userinfo(target)
    u, w = d["user"], d["wallet"]
    tx = "\n".join(f"#{t.id} {t.kind} {t.amount:+} / bank {t.bank_delta:+} {t.created_at:%m-%d %H:%M}" for t in d["tx"]) or "—"
    await m.reply(f"👤 <b>{esc(u.first_name)}</b> <code>{u.id}</code> @{esc(u.username or '-')}\nLv {u.level} · 💎 {num(w.balance)} · 🏦 {num(w.bank)}\n"
                  f"K/D {u.kills}/{u.deaths} · W/L {u.wins}/{u.losses}\nbanned={u.is_banned} muted={u.is_muted} flagged={u.is_flagged}\n\n<b>Last transactions</b>\n<code>{esc(tx)}</code>")


@router.message(Command("logs"))
async def logs(m: Message, command: CommandObject):
    adm.authorize(m.from_user.id, "logs")
    n = int(command.args) if command.args and command.args.isdigit() else 15
    rows = await adm.recent_logs(n)
    await m.reply("<b>Admin logs</b>\n<code>" + esc("\n".join(f"#{r.id} {r.created_at:%m-%d %H:%M} {r.admin_id} {r.action} {r.target_id or ''}" for r in rows) or "empty") + "</code>")


@router.message(Command("maintenance"))
async def maintenance(m: Message, command: CommandObject):
    arg = (command.args or "").lower()
    if arg not in ("on", "off"):
        raise GameError("Usage: /maintenance on|off")
    await _ask(m, "maintenance", {"on": arg == "on"}, f"maintenance <b>{arg.upper()}</b>")


@router.message(Command("broadcast"))
async def broadcast(m: Message, command: CommandObject):
    """/broadcast <text> · or REPLY to any message (photo, video, sticker, voice, file, poll...) with /broadcast.
    Optional words: users | groups | all (default all) and 'forward' (reply mode: keeps the tappable "Forwarded from" header)."""
    from services import broadcast as bc
    args, scope = (command.args or "").strip(), "all"
    fwd = False
    for _ in range(2):                                   # options in any order: users|groups|all  and  forward
        first, _, rest = args.partition(" ")
        if first.lower() in ("users", "groups", "all"):
            scope, args = first.lower(), rest.strip()
        elif first.lower() in ("forward", "fwd"):
            fwd, args = True, rest.strip()
    rep = m.reply_to_message
    if rep is not None:
        payload = {"src_chat": rep.chat.id, "src_msg": rep.message_id, **({"forward": True} if fwd else {})}
        what = f"that <b>{esc(str(rep.content_type.value if hasattr(rep.content_type, 'value') else rep.content_type))}</b> message"
    elif args:
        payload = {"text": args[:4000]}
        what = f"text:\n\n{esc(args[:500])}"
    else:
        raise GameError("Usage: /broadcast [users|groups|all] &lt;text&gt;  — or reply to ANY message (photo, video, sticker, file…) with /broadcast")
    n = len(await bc.targets(scope))
    await _ask(m, "broadcast", {**payload, "scope": scope}, f"send {what}\nto <b>{n}</b> chats (<i>{scope}</i>)")


@router.message(Command("syncusers"))
async def syncusers(m: Message, bot: Bot):
    """Owner: quietly check every known person once (invisible, no messages) so /stats and broadcasts know who really started the bot."""
    adm.authorize(m.from_user.id, "syncusers")
    from services import broadcast as bc
    if not bc.sync(bot, m.from_user.id):
        raise GameError("⏳ A broadcast / sync is already running.")
    await m.reply("🔎 Sync started in the background (nothing is sent to anyone). I'll message you when it's done.")


@router.message(Command("emojiid"))
async def emojiid_cmd(m: Message):
    """Owner: send/reply with premium emoji to get their custom_emoji_id (used by /seticon for premium button icons)."""
    adm.authorize(m.from_user.id, "emojiid")
    found = []
    for msg in (m, m.reply_to_message):
        for ent in ((msg.entities or []) if msg else []):
            if ent.type == "custom_emoji" and ent.custom_emoji_id:
                found.append(f"{esc(ent.extract_from(msg.text or ''))} = <code>{ent.custom_emoji_id}</code>")
    if not found:
        raise GameError("Send this command together with a premium emoji (or reply to a message that has one).")
    await m.reply("\n".join(found) + "\n\nUse: /seticon &lt;emoji at the start of a button&gt; &lt;id&gt;")


@router.message(Command("seticon", "delicon"))
async def seticon_cmd(m: Message, command: CommandObject):
    """Owner: /seticon 💎 <custom_emoji_id>  ·  /delicon 💎  ·  /seticon (list). Needs Telegram Premium on the OWNER's account."""
    from keyboards import style as st
    adm.authorize(m.from_user.id, "seticon")
    parts = (command.args or "").split()
    if command.command.lower() == "delicon":
        if not parts:
            raise GameError("Usage: /delicon &lt;emoji&gt;")
        await st.set_icon(parts[0], None)
        await m.reply(f"🗑 Icon for {esc(parts[0])} removed."); return
    if not parts:
        body = "\n".join(f"{esc(k)} → <code>{v}</code>" for k, v in st.ICONS.items()) or "none yet"
        await m.reply(f"🎨 <b>Premium button icons</b>\n{body}\n\nSet: /seticon 💎 &lt;custom_emoji_id&gt;  (get ids with /emojiid)\n"
                      "Works only if the bot OWNER has Telegram Premium."); return
    if len(parts) != 2 or not parts[1].isdigit():
        raise GameError("Usage: /seticon &lt;emoji&gt; &lt;custom_emoji_id (digits)&gt;")
    await st.set_icon(parts[0], parts[1])
    await m.reply(f"✅ {esc(parts[0])} buttons will now show that premium icon.")


@router.message(Command("setstart"))
async def setstart_cmd(m: Message, command: CommandObject):
    """Owner: /setstart dm|gc <text> (or reply to a text with /setstart dm|gc) · /setstart dm|gc reset · /setstart (show).
    Changes only the /start message text; the buttons never change. {name} = user's name, {bot} = bot name."""
    from database.models import AppState
    from database.session import db
    from handlers import views
    adm.authorize(m.from_user.id, "setstart")
    usage = ("Usage: /setstart dm|gc &lt;text&gt; (or reply to a text)\n/setstart dm|gc reset\n"
             "<code>{name}</code> = user's name · <code>{bot}</code> = bot name. Buttons stay unchanged. Max 1000 chars.")
    raw = (command.args or "").strip()
    which, _, rest = raw.partition(" ")
    which, rest = which.lower(), rest.strip()
    if which not in ("dm", "gc"):
        cur = []
        for k, label in (("dm", "DM"), ("gc", "Group")):
            cur.append(f"{label}: {'custom' if await views.start_text(k == 'gc') else 'default'}")
        raise GameError("ℹ️ " + " · ".join(cur) + "\n" + usage)
    group = which == "gc"
    key = views.start_key(group)
    if rest.lower() == "reset":
        async with db.tx() as s:
            row = await s.get(AppState, key)
            if row:
                await s.delete(row)
        await m.reply(f"♻️ {which.upper()} start message reset to default."); return
    src = m.reply_to_message
    if rest:
        html = (m.html_text or "").split(None, 2)
        text = html[2] if len(html) > 2 else rest
    elif src is not None and (src.text or src.caption):
        text = src.html_text if src.text else (src.caption or "")
    else:
        raise GameError(usage)
    plain = re.sub(r"<[^>]+>", "", text)
    if len(plain) > 1000:
        raise GameError(f"❌ Too long ({len(plain)} chars, max 1000 - the DM start card is a video caption).")
    async with db.tx() as s:
        row = await s.get(AppState, key)
        if row:
            row.value = text
        else:
            s.add(AppState(key=key, value=text))
    await m.reply(f"✅ {which.upper()} start message saved. Send /start to preview.")


@router.callback_query(F.data.startswith("adm:"))
async def confirm_cb(cb: CallbackQuery, bot: Bot):
    _, verb, nonce = cb.data.split(":")
    if verb == "no":
        await adm.cancel(nonce)
        await safe_edit(cb, "✖️ Cancelled."); await safe_answer(cb); return
    action, args = await adm.confirm(cb.from_user.id, nonce)
    if action == "resetdiamonds":                       # safety copy first - if it can't be made, nothing is reset
        path = await bk.make_backup(get_settings().database_url)
        if path is None:
            raise GameError("❌ I couldn't make the safety backup (SQLite only), so I did not reset anything.")
        await bot.send_document(cb.from_user.id, FSInputFile(path), caption=f"💾 Safety backup before /resetdiamonds ({path.name}). Reply with /restoredb to undo.")
    result = await adm.execute(cb.from_user.id, action, args)
    if action == "maintenance":
        await kv.put("maint", "1" if args["on"] else "0", 5)
    if action == "broadcast":
        from services import broadcast as bc
        if bc.start(bot, cb.from_user.id, args):
            await safe_edit(cb, "📣 Broadcast started in the background. I'll message you when it's done.")
        else:
            await safe_edit(cb, "⏳ Another broadcast is still running. Try again after it finishes.")
        await safe_answer(cb)
        return
    await safe_edit(cb, result); await safe_answer(cb)
