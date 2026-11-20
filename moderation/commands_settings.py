"""5. Group settings: rules, welcome / goodbye, title / description, slow mode, flood and warning configuration."""
from __future__ import annotations

from aiogram import Bot
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.filters import CommandObject
from aiogram.types import Message

from . import greet, perms, store
from .base import command, prepare, reply
from .defaults import ACTIONS, WARN_MODES
from .perms import ModError, esc, tg
from .util import fmt_duration, parse_duration, strip_command

MAX_TEXT = 3500


def _body(m: Message, command: CommandObject) -> str:
    """The text after the command; or, if empty, the replied message's text - both with their formatting preserved."""
    txt = strip_command(m.html_text or "")
    if not txt and m.reply_to_message and (m.reply_to_message.text or m.reply_to_message.caption):
        txt = (m.reply_to_message.html_text or "").strip()
    return txt


# ------------------------------------------------------------------------------------------------------------ rules
@command("setrules", cat="settings", usage="/setrules <text> (or reply to a message)", desc="Set this group's rules (formatting kept)")
async def cmd_setrules(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    txt = _body(m, command)
    if not txt:
        raise ModError("✍️ Write the rules after the command, or reply to a message that contains them.")
    if len(txt) > MAX_TEXT:
        raise ModError(f"✂️ Too long ({len(txt)} chars). Keep it under {MAX_TEXT}.")
    await reply(m, "✅ <b>Rules saved.</b> Preview:\n\n" + txt)          # if the formatting is invalid this raises BEFORE anything is saved
    await store.save_settings(r.chat_id, lambda s: s.update(rules=txt))
    await store.log_action(r.chat_id, r.actor, None, "setrules")


@command("rules", "grouprules", cat="settings", usage="/rules", gate=False, shared=True,
         desc="Show this group's rules (if none are set, /rules still shows the game guide)")
async def cmd_rules(m: Message, command: CommandObject, bot: Bot):
    st = await store.get_settings(m.chat.id)
    if not st["rules"]:
        if command.command.lower() == "rules":
            return UNHANDLED                                 # no custom rules -> the game's own /rules keeps working exactly as before
        return await reply(m, "📜 No rules have been set yet. Admins: <code>/setrules …</code>")
    await reply(m, f"📜 <b>Rules of {esc(m.chat.title)}</b>\n\n{st['rules']}")


@command("resetrules", "clearrules", cat="settings", usage="/resetrules", desc="Delete the group's rules")
async def cmd_resetrules(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    await store.save_settings(r.chat_id, lambda s: s.update(rules=""))
    await store.log_action(r.chat_id, r.actor, None, "resetrules")
    await reply(m, "🧹 Rules cleared.")


# ------------------------------------------------------------------------------------------------------------ welcome / goodbye
async def _set_greeting(m: Message, command: CommandObject, bot: Bot, key: str):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    txt = _body(m, command)
    if not txt:
        raise ModError(f"✍️ Write the {key} text after the command. Placeholders: <code>{esc(greet.HELP_VARS)}</code>\n"
                       f"Buttons: <code>[Label](buttonurl://example.com)</code> (add <code>:same</code> to keep on the same row).")
    if len(txt) > 1500:
        raise ModError("✂️ Too long (max 1500 characters).")
    text, kb = greet.build(txt, m.from_user, m.chat.title, m.chat.id, None)
    await tg(bot.send_message, m.chat.id, "👁 <b>Preview</b>\n\n" + text, reply_markup=kb, disable_web_page_preview=True)   # invalid markup raises here: nothing saved
    await store.save_settings(r.chat_id, lambda s: s[key].update(text=txt, on=True))
    await store.log_action(r.chat_id, r.actor, None, f"set{key}")
    await reply(m, f"✅ {key.capitalize()} message saved and <b>enabled</b>.")


@command("setwelcome", cat="settings", usage="/setwelcome <text> (placeholders, buttons)", desc="Custom welcome message with {first} {mention} {chatname} {count}… and buttons")
async def cmd_setwelcome(m: Message, command: CommandObject, bot: Bot):
    await _set_greeting(m, command, bot, "welcome")


@command("setgoodbye", cat="settings", usage="/setgoodbye <text>", desc="Custom goodbye message when someone leaves")
async def cmd_setgoodbye(m: Message, command: CommandObject, bot: Bot):
    await _set_greeting(m, command, bot, "goodbye")


async def _greeting_cmd(m: Message, command: CommandObject, bot: Bot, key: str):
    st = await store.get_settings(m.chat.id)
    sec = st[key]
    arg = (command.args or "").strip().lower().split()
    if not arg:
        await perms.require_actor(bot, m.chat.id, m.from_user.id, None)
        lines = [f"👋 <b>{key.capitalize()}</b>: {'🟢 ON' if sec['on'] else '🔴 OFF'}" + (f" · clean: {'on' if sec.get('clean') else 'off'}" if key == 'welcome' else "")]
        lines.append("Text:\n" + (esc(sec["text"]) if sec["text"] else "<i>(not set - " + ("the bot's default greeting is used)" if key == "welcome" else "no goodbye is sent)") + "</i>"))
        lines.append(f"\n<code>/{key} on|off</code> · <code>/set{key} …</code> · <code>/reset{key}</code>")
        return await reply(m, "\n".join(lines))
    r = await prepare(m, command, bot, right="info", bot_right=None)
    if arg[0] in ("on", "off", "yes", "no"):
        on = arg[0] in ("on", "yes")
        if on and not sec["text"]:
            raise ModError(f"✍️ Set a text first with <code>/set{key} …</code>.")
        await store.save_settings(r.chat_id, lambda s: s[key].update(on=on))
        await store.log_action(r.chat_id, r.actor, None, f"{key}-{'on' if on else 'off'}")
        return await reply(m, f"✅ {key.capitalize()} is now <b>{'ON' if on else 'OFF'}</b>.")
    if key == "welcome" and arg[0] == "clean" and len(arg) > 1:
        on = arg[1] in ("on", "yes")
        await store.save_settings(r.chat_id, lambda s: s["welcome"].update(clean=on))
        return await reply(m, f"🧽 Deleting the previous welcome: <b>{'ON' if on else 'OFF'}</b>.")
    raise ModError(f"ℹ️ Usage: <code>/{key} on|off</code>" + (" or <code>/welcome clean on|off</code>" if key == "welcome" else ""))


@command("welcome", cat="settings", usage="/welcome [on|off|clean on|off]", desc="Show or toggle the welcome message", gate=False)
async def cmd_welcome(m: Message, command: CommandObject, bot: Bot):
    await _greeting_cmd(m, command, bot, "welcome")


@command("goodbye", cat="settings", usage="/goodbye [on|off]", desc="Show or toggle the goodbye message", gate=False)
async def cmd_goodbye(m: Message, command: CommandObject, bot: Bot):
    await _greeting_cmd(m, command, bot, "goodbye")


async def _reset_greeting(m, command, bot, key):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    await store.save_settings(r.chat_id, lambda s: s.update({key: {"on": False, "text": "", **({"clean": False} if key == "welcome" else {})}}))
    await store.log_action(r.chat_id, r.actor, None, f"reset{key}")
    await reply(m, f"🧹 {key.capitalize()} reset" + (" - the bot's default greeting is back." if key == "welcome" else "."))


@command("resetwelcome", cat="settings", usage="/resetwelcome", desc="Remove the custom welcome (the default greeting returns)")
async def cmd_resetwelcome(m: Message, command: CommandObject, bot: Bot):
    await _reset_greeting(m, command, bot, "welcome")


@command("resetgoodbye", cat="settings", usage="/resetgoodbye", desc="Remove the custom goodbye")
async def cmd_resetgoodbye(m: Message, command: CommandObject, bot: Bot):
    await _reset_greeting(m, command, bot, "goodbye")


# ------------------------------------------------------------------------------------------------------------ group info
@command("settitle", cat="settings", usage="/settitle <new title>", desc="Rename the group (needs 'Change group info' for me and you)")
async def cmd_settitle(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info")
    title = " ".join(r.args)[:128]
    if not title:
        raise ModError("✍️ Usage: <code>/settitle New Group Name</code>")
    try:
        await tg(bot.set_chat_title, r.chat_id, title)
    except Exception as e:
        await store.log_action(r.chat_id, r.actor, None, "settitle", result="failed", detail=str(e)[:200])
        raise ModError(perms.explain(e))
    await store.log_action(r.chat_id, r.actor, None, "settitle", reason=title)
    await reply(m, f"✅ Group renamed to <b>{esc(title)}</b>.")


@command("setdescription", "setabout", cat="settings", usage="/setdescription <text>  (alias /setabout)", desc="Set the group's description / about text (max 255)")
async def cmd_setdescription(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info")
    desc = " ".join(r.args)[:255]
    if not desc and not m.reply_to_message:
        raise ModError("✍️ Usage: <code>/setdescription text</code> (or reply to a message).")
    if not desc and m.reply_to_message:
        desc = (m.reply_to_message.text or m.reply_to_message.caption or "")[:255]
    try:
        await tg(bot.set_chat_description, r.chat_id, desc)
    except Exception as e:
        await store.log_action(r.chat_id, r.actor, None, "setdescription", result="failed", detail=str(e)[:200])
        raise ModError(perms.explain(e))
    await store.log_action(r.chat_id, r.actor, None, "setdescription")
    await reply(m, "✅ Group description updated.")


# ------------------------------------------------------------------------------------------------------------ slow mode / flood / warnings
@command("slowmode", cat="settings", usage="/slowmode <seconds|off>", desc="Minimum delay between a member's messages (I enforce it by deleting too-fast messages)")
async def cmd_slowmode(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    if not r.args:
        ch = await tg(bot.get_chat, r.chat_id)
        nat = getattr(ch, "slow_mode_delay", None)
        return await reply(m, f"🐌 Slow mode: <b>{fmt_duration(r.st['slowmode']) if r.st['slowmode'] else 'off'}</b>"
                              + (f"\nTelegram's own slow mode (set by an admin in the app): {nat}s" if nat else "")
                              + "\n<i>Telegram gives bots no way to switch ITS slow mode on, so I enforce mine by deleting messages sent too soon (needs 'Delete messages').</i>")
    a = r.args[0].lower()
    secs = 0 if a in ("off", "0", "no") else (parse_duration(a) or (int(a) if a.isdigit() else -1))
    if secs < 0 or secs > 3600:
        raise ModError("⏱ Use seconds from 1 to 3600 (e.g. <code>/slowmode 10</code>, <code>/slowmode 1m</code>) or <code>off</code>.")
    if secs:
        await perms.require_bot(bot, r.chat_id, "delete")
    await store.save_settings(r.chat_id, lambda s: s.update(slowmode=secs))
    await store.log_action(r.chat_id, r.actor, None, "slowmode", detail=str(secs))
    await reply(m, f"🐌 Slow mode <b>{'OFF' if not secs else 'ON: 1 message per ' + fmt_duration(secs)}</b> (admins and approved members are exempt).")


@command("setflood", cat="settings", usage="/setflood <messages|off> [seconds] [delete|warn|mute|kick|ban]", desc="Anti-flood: max messages per time window, and the punishment")
async def cmd_setflood(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    if not r.args:
        f = r.st["flood"]
        return await reply(m, f"🌊 Anti-flood: <b>{'ON' if f['on'] else 'OFF'}</b> - {f['limit']} messages / {f['window']}s → {f['action']}"
                              + (f" ({fmt_duration(f['duration'])})" if f["action"] == "mute" else "") + "\nSet: <code>/setflood 8 10 mute</code> · off: <code>/setflood off</code>")
    if r.args[0].lower() in ("off", "0", "no"):
        await store.save_settings(r.chat_id, lambda s: s["flood"].update(on=False))
        await store.log_action(r.chat_id, r.actor, None, "antiflood-off")
        return await reply(m, "🌊 Anti-flood <b>OFF</b>.")
    if not r.args[0].isdigit() or not 2 <= int(r.args[0]) <= 50:
        raise ModError("🔢 The message limit must be a number from 2 to 50.")
    limit, window, action = int(r.args[0]), r.st["flood"]["window"], r.st["flood"]["action"]
    for a in r.args[1:]:
        if a.isdigit() and 2 <= int(a) <= 120:
            window = int(a)
        elif a.lower() in ACTIONS:
            action = a.lower()
        else:
            raise ModError(f"❓ I don't understand <b>{esc(a)}</b>. Actions: {', '.join(ACTIONS)}.")
    if action != "delete":
        await perms.require_bot(bot, r.chat_id, "restrict")
    await perms.require_bot(bot, r.chat_id, "delete")
    await store.save_settings(r.chat_id, lambda s: s["flood"].update(on=True, limit=limit, window=window, action=action))
    await store.log_action(r.chat_id, r.actor, None, "setflood", detail=f"{limit}/{window}s {action}")
    await reply(m, f"🌊 Anti-flood <b>ON</b>: more than {limit} messages in {window}s → <b>{action}</b>.")


@command("setwarnlimit", cat="settings", usage="/setwarnlimit <1-20>", desc="How many warnings trigger the automatic punishment")
async def cmd_setwarnlimit(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    if not r.args or not r.args[0].isdigit() or not 1 <= int(r.args[0]) <= 20:
        raise ModError(f"🔢 Usage: <code>/setwarnlimit 3</code> (1-20). Now: <b>{r.st['warn']['limit']}</b>")
    n = int(r.args[0])
    await store.save_settings(r.chat_id, lambda s: s["warn"].update(limit=n))
    await store.log_action(r.chat_id, r.actor, None, "setwarnlimit", detail=str(n))
    await reply(m, f"⚠️ Warn limit is now <b>{n}</b>.")


@command("setwarnmode", cat="settings", usage="/setwarnmode <ban|kick|mute|tmute|tban> [duration]", desc="What happens at the warn limit")
async def cmd_setwarnmode(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    w = r.st["warn"]
    if not r.args or r.args[0].lower() not in WARN_MODES:
        raise ModError(f"ℹ️ Usage: <code>/setwarnmode tmute 1h</code>. Modes: {', '.join(WARN_MODES)}. Now: <b>{w['mode']}</b>"
                       + (f" ({fmt_duration(w['duration'])})" if w["mode"] in ("tmute", "tban") else ""))
    mode = r.args[0].lower()
    dur = parse_duration(r.args[1]) if len(r.args) > 1 else None
    if mode in ("tmute", "tban") and dur is None:
        dur = int(w.get("duration") or 3600)
    if dur is not None and dur < 30:
        raise ModError("⏱ The shortest duration is 30 seconds.")
    await perms.require_bot(bot, r.chat_id, "restrict")
    await store.save_settings(r.chat_id, lambda s: s["warn"].update(mode=mode, **({"duration": dur} if dur else {})))
    await store.log_action(r.chat_id, r.actor, None, "setwarnmode", detail=mode)
    await reply(m, f"⚠️ At {w['limit']} warnings: <b>{mode}</b>" + (f" for {fmt_duration(dur)}" if mode in ("tmute", "tban") else "") + ".")


@command("setwarnexpire", cat="settings", usage="/setwarnexpire <duration|off>", desc="Warnings older than this stop counting (e.g. 30d)")
async def cmd_setwarnexpire(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    a = (r.args[0].lower() if r.args else "")
    secs = 0 if a in ("off", "0", "never") else parse_duration(a)
    if secs is None:
        raise ModError(f"ℹ️ Usage: <code>/setwarnexpire 30d</code> or <code>off</code>. Now: <b>{fmt_duration(r.st['warn']['expire']) if r.st['warn']['expire'] else 'never'}</b>")
    await store.save_settings(r.chat_id, lambda s: s["warn"].update(expire=secs))
    await store.log_action(r.chat_id, r.actor, None, "setwarnexpire", detail=str(secs))
    await reply(m, "⚠️ Warnings now " + (f"expire after <b>{fmt_duration(secs)}</b>." if secs else "<b>never</b> expire."))
