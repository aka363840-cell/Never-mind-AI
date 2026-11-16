"""4. Message moderation: del, pin family, edit, locks."""
from __future__ import annotations

from aiogram import Bot
from aiogram.filters import CommandObject
from aiogram.types import Message

from . import actions, confirm, perms, store
from .base import command, prepare, reply
from .defaults import LOCKS, compute_permissions, lock_name
from .perms import ModError, esc, tg
from .util import strip_command


@command("del", "delete", cat="message", usage="/del (reply)", desc="Delete the replied message (and your command)")
async def cmd_del(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="delete")
    if not m.reply_to_message:
        raise ModError("↩️ Reply to the message you want deleted.")
    ok = await actions.delete_message(bot, r.chat_id, m.reply_to_message.message_id)
    await actions.delete_message(bot, r.chat_id, m.message_id)
    await store.log_action(r.chat_id, r.actor, m.reply_to_message.from_user.id if m.reply_to_message.from_user else None, "delete",
                           result="ok" if ok else "failed", detail=f"msg {m.reply_to_message.message_id}")
    if not ok:
        await bot.send_message(r.chat_id, "🗑 I couldn't delete that message (already gone, or too old for Telegram).")


# ------------------------------------------------------------------------------------------------------------ pins
@command("pin", cat="message", usage="/pin (reply) [loud]", desc="Pin the replied message (silent by default; add 'loud' to notify everyone)")
async def cmd_pin(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="pin")
    if not m.reply_to_message:
        raise ModError("↩️ Reply to the message you want pinned.")
    loud = any(a.lower() in ("loud", "notify", "all") for a in r.args)
    try:
        await tg(bot.pin_chat_message, r.chat_id, m.reply_to_message.message_id, disable_notification=not loud)
    except Exception as e:
        await store.log_action(r.chat_id, r.actor, None, "pin", result="failed", detail=str(e)[:200])
        raise ModError(perms.explain(e))
    await store.log_action(r.chat_id, r.actor, None, "pin", detail=f"msg {m.reply_to_message.message_id}")
    await reply(m, "📌 Pinned" + (" (everyone notified)." if loud else "."))


@command("unpin", cat="message", usage="/unpin [reply]", desc="Unpin the replied message, or the most recent pin")
async def cmd_unpin(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="pin")
    try:
        if m.reply_to_message:
            await tg(bot.unpin_chat_message, r.chat_id, message_id=m.reply_to_message.message_id)
        else:
            await tg(bot.unpin_chat_message, r.chat_id)
    except Exception as e:
        await store.log_action(r.chat_id, r.actor, None, "unpin", result="failed", detail=str(e)[:200])
        raise ModError(perms.explain(e))
    await store.log_action(r.chat_id, r.actor, None, "unpin")
    await reply(m, "📍 Unpinned.")


@command("unpinall", cat="message", usage="/unpinall", desc="Remove EVERY pinned message (asks YES/NO)")
async def cmd_unpinall(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="pin")
    await confirm.request(m, bot, "unpinall", {}, title="Unpin ALL messages", lines=["Every pinned message in this group will be unpinned."])


async def _run_unpinall(ctx: confirm.Ctx) -> str:
    try:
        await tg(ctx.bot.unpin_all_chat_messages, ctx.chat_id)
    except Exception as e:
        await store.log_action(ctx.chat_id, ctx.issuer, None, "unpinall", result="failed", detail=str(e)[:200])
        raise ModError(perms.explain(e))
    await store.log_action(ctx.chat_id, ctx.issuer, None, "unpinall")
    return "✅ All pinned messages were removed."


confirm.register("unpinall", confirm.Spec(actor_right="pin", bot_right="pin", run=_run_unpinall))


@command("pinned", cat="message", usage="/pinned", desc="Jump to the current pinned message", gate=False)
async def cmd_pinned(m: Message, bot: Bot):
    ch = await tg(bot.get_chat, m.chat.id)
    p = getattr(ch, "pinned_message", None)
    if p is None:
        return await reply(m, "📌 Nothing is pinned.")
    link = f"https://t.me/{ch.username}/{p.message_id}" if ch.username else f"https://t.me/c/{str(ch.id)[4:]}/{p.message_id}"
    await reply(m, f'📌 <a href="{link}">Go to the pinned message</a>')


@command("edit", cat="message", usage="/edit <new text> (reply to MY message)", desc="Edit one of my own messages (Telegram doesn't let bots edit anyone else's)")
async def cmd_edit(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    t = m.reply_to_message
    if t is None or t.from_user is None or t.from_user.id != bot.id:
        raise ModError("ℹ️ Reply to one of <b>my</b> messages. Telegram only lets a bot edit its own messages.")
    new = strip_command(m.html_text or "")
    if not new:
        raise ModError("✍️ Give the new text: <code>/edit new text</code> (formatting is kept).")
    try:
        await tg(bot.edit_message_text, new, chat_id=r.chat_id, message_id=t.message_id, disable_web_page_preview=True)
    except Exception as e:
        raise ModError(perms.explain(e))
    await store.log_action(r.chat_id, r.actor, None, "edit", detail=f"msg {t.message_id}")
    await actions.delete_message(bot, r.chat_id, m.message_id)


# ------------------------------------------------------------------------------------------------------------ locks
async def apply_locks(bot: Bot, chat_id: int, names: list[str], value: bool, actor: int) -> str:
    """Native locks change the group's default permissions in Telegram (works for everyone, even offline); bot-side locks delete
    the offending messages. Telegram is changed FIRST - the stored setting is only updated once Telegram confirmed."""
    st = await store.get_settings(chat_id)
    new = {k: v for k, v in {**st["locks"], **{n: value for n in names}}.items() if v}
    native = any(LOCKS[n][0] for n in names)
    needs_delete = value and any(not LOCKS[n][0] and LOCKS[n][1] for n in names)
    if native:
        await perms.require_bot(bot, chat_id, "restrict", force=True)
    if needs_delete:
        await perms.require_bot(bot, chat_id, "delete", force=True)
    if native:
        cur = await actions.default_perm_dict(bot, chat_id)
        try:
            await tg(bot.set_chat_permissions, chat_id, actions.perms_obj(compute_permissions(cur, new, st["locks"])), use_independent_chat_permissions=True)
        except Exception as e:
            await store.log_action(chat_id, actor, None, "lock" if value else "unlock", reason=",".join(names), result="failed", detail=str(e)[:200])
            raise ModError(perms.explain(e))
    await store.save_settings(chat_id, lambda s: s.update(locks=new))
    await store.log_action(chat_id, actor, None, "lock" if value else "unlock", reason=",".join(names))
    return ("🔒 Locked" if value else "🔓 Unlocked") + f": <b>{esc(', '.join(names))}</b>"


def _parse_locks(args: list[str]) -> list[str]:
    names, bad = [], []
    for a in args:
        n = lock_name(a)
        (names if n else bad).append(n or a)
    if bad or not names:
        raise ModError(("❓ Unknown lock type: <b>" + esc(", ".join(bad)) + "</b>\n" if bad else "") + "See <code>/locktypes</code> for the list.")
    return list(dict.fromkeys(names))


async def _lock_cmd(m: Message, command: CommandObject, bot: Bot, value: bool):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    names = _parse_locks(r.args)
    if "all" in names:
        await confirm.request(m, bot, "locks", {"names": ["all"], "value": value},
                              title=("Lock EVERYTHING" if value else "Unlock EVERYTHING"),
                              lines=["Nobody except admins will be able to send anything." if value else "The 'all' lock will be removed."],
                              warning="This changes what every member of the group can do.")
        return
    await reply(m, await apply_locks(bot, r.chat_id, names, value, r.actor))


@command("lock", cat="message", usage="/lock <types…>", desc="Forbid message types for everyone but admins (see /locktypes). 'all' asks YES/NO")
async def cmd_lock(m: Message, command: CommandObject, bot: Bot):
    await _lock_cmd(m, command, bot, True)


@command("unlock", cat="message", usage="/unlock <types…>", desc="Allow message types again")
async def cmd_unlock(m: Message, command: CommandObject, bot: Bot):
    await _lock_cmd(m, command, bot, False)


async def _run_locks(ctx: confirm.Ctx) -> str:
    return await apply_locks(ctx.bot, ctx.chat_id, ctx.payload["names"], bool(ctx.payload["value"]), ctx.issuer)


confirm.register("locks", confirm.Spec(actor_right="info", bot_right=None, run=_run_locks))


@command("locktypes", "locks", cat="message", usage="/locktypes", desc="All lock types and which are currently on", gate=False)
async def cmd_locktypes(m: Message, bot: Bot):
    st = await store.get_settings(m.chat.id)
    lines = ["🔐 <b>Lock types</b>  <i>(native = Telegram enforces it · bot = I delete the message)</i>"]
    for n, (fields, kinds, desc) in LOCKS.items():
        mode = "native" if fields and not kinds - {"*"} and n not in ("all",) else "native+bot" if fields else "bot"
        lines.append(f"{'🔒' if st['locks'].get(n) else '▫️'} <code>{n}</code> - {esc(desc)} <i>[{mode}]</i>")
    await reply(m, "\n".join(lines))
