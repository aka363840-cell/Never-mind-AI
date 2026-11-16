"""6. Anti-spam & security configuration commands (the enforcement itself lives in enforce.py)."""
from __future__ import annotations

from aiogram import Bot
from aiogram.filters import CommandObject
from aiogram.types import Message

from . import confirm, lockdown, perms, store
from .base import Req, command, prepare, reply
from .defaults import ACTIONS, LEVELS, PRESETS, apply_preset
from .matching import host_of
from .perms import ModError, esc
from .util import fmt_duration, parse_duration, strip_command

_BOOL = {"on": True, "yes": True, "true": True, "1": True, "off": False, "no": False, "false": False, "0": False}


def _int(lo, hi):
    def f(v):
        if not v.isdigit() or not lo <= int(v) <= hi:
            raise ValueError(f"a number from {lo} to {hi}")
        return int(v)
    return f


def _dur(v):
    d = parse_duration(v) if not v.isdigit() else int(v)
    if not d or d < 30:
        raise ValueError("a duration of at least 30s, like 10m or 1h")
    return d


def _choice(*opts):
    def f(v):
        if v.lower() not in opts:
            raise ValueError("one of: " + ", ".join(opts))
        return v.lower()
    return f


def _bool(v):
    if v.lower() not in _BOOL:
        raise ValueError("on or off")
    return _BOOL[v.lower()]


# feature -> {option: (settings field, validator)}
OPTIONS = {
    "spam": {"dup": ("dup_limit", _int(2, 10)), "window": ("dup_window", _int(10, 600)), "repeat": ("repeat_chars", _int(0, 100)),
             "mentions": ("mentions", _int(0, 100)), "links": ("suspicious_links", _bool), "cross": ("cross_user", _bool),
             "action": ("action", _choice(*ACTIONS)), "duration": ("duration", _dur)},
    "flood": {"limit": ("limit", _int(2, 50)), "window": ("window", _int(2, 120)), "action": ("action", _choice(*ACTIONS)), "duration": ("duration", _dur)},
    "antibot": {"action": ("action", _choice("kick", "ban"))},
    "antiforward": {"action": ("action", _choice(*ACTIONS)), "duration": ("duration", _dur)},
    "antilink": {"action": ("action", _choice(*ACTIONS)), "duration": ("duration", _dur)},
    "antiraid": {"joins": ("joins", _int(2, 100)), "window": ("window", _int(5, 600)), "lockdown": ("lockdown", _dur),
                 "action": ("action", _choice("kick", "ban", "mute"))},
    "captcha": {"mode": ("mode", _choice("button", "math")), "timeout": ("timeout", _int(30, 900)), "fail": ("fail", _choice("kick", "ban", "mute"))},
}
NEEDS_RESTRICT = {"antibot", "antiraid", "captcha"}


def _summary(key: str, sec: dict) -> str:
    bits = []
    for k, v in sec.items():
        if k in ("on", "until"):
            continue
        bits.append(f"{k}={fmt_duration(v) if k in ('duration', 'lockdown') else str(v).lower() if isinstance(v, bool) else v}")
    return f"{'🟢 ON' if sec['on'] else '🔴 OFF'}  ·  " + "  ".join(bits)


async def _feature(m: Message, command: CommandObject, bot: Bot, key: str, title: str, what: str):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    sec_now = r.st[key]
    if not r.args or r.args[0].lower() == "status":
        opts = " ".join(f"<code>{o}=…</code>" for o in OPTIONS[key])
        return await reply(m, f"{title}\n{what}\n\n{_summary(key, sec_now)}\n\n<code>/{command.command} on|off {''}</code> {opts}")
    first = r.args[0].lower()
    patch: dict = {}
    on = None
    rest = r.args
    if first in _BOOL and first not in ("1", "0"):
        on, rest = _BOOL[first], r.args[1:]
    for tok in rest:
        if "=" not in tok:
            raise ModError(f"ℹ️ Options look like <code>name=value</code> (got <b>{esc(tok)}</b>). Available: {', '.join(OPTIONS[key])}")
        name, val = tok.split("=", 1)
        if name.lower() not in OPTIONS[key]:
            raise ModError(f"❓ Unknown option <b>{esc(name)}</b>. Available: {', '.join(OPTIONS[key])}")
        field, check = OPTIONS[key][name.lower()]
        try:
            patch[field] = check(val)
        except ValueError as e:
            raise ModError(f"❌ <b>{esc(name)}</b> must be {e}.")
    if on is None and not patch:
        raise ModError("ℹ️ Say <code>on</code>, <code>off</code>, or set options.")
    if on is not False:
        eff = {**sec_now, **patch}
        if key not in ("antibot", "captcha", "antiraid"):
            await perms.require_bot(bot, r.chat_id, "delete")
        if key in NEEDS_RESTRICT or eff.get("action", "delete") != "delete":
            await perms.require_bot(bot, r.chat_id, "restrict")
    if on is not None:
        patch["on"] = on
    await store.save_settings(r.chat_id, lambda s: s[key].update(patch))
    await store.log_action(r.chat_id, r.actor, None, f"{key}-config", detail=" ".join(f"{k}={v}" for k, v in patch.items())[:200])
    new = (await store.get_settings(r.chat_id))[key]
    await reply(m, f"{title} updated\n{_summary(key, new)}")


def _feat(names, key, title, what, usage, desc):
    @command(*names, cat="security", usage=usage, desc=desc)
    async def handler(m: Message, command: CommandObject, bot: Bot):
        await _feature(m, command, bot, key, title, what)
    handler.__name__ = f"cmd_{names[0]}"
    return handler


_feat(("antispam",), "spam", "🧹 <b>Anti-spam</b>", "Duplicate messages, repeated characters, mention floods, spam waves from several accounts, suspicious links.",
      "/antispam on|off [dup=3 window=60 repeat=15 mentions=10 links=on cross=on action=delete duration=1h]", "Duplicate / repeated-character / mention / link-spam protection")
_feat(("antiflood",), "flood", "🌊 <b>Anti-flood</b>", "Too many messages in a short time.",
      "/antiflood on|off [limit=8 window=10 action=mute duration=10m]", "Message-frequency limit (also see /setflood)")
_feat(("antibot",), "antibot", "🤖 <b>Anti-bot</b>", "Non-admins who add bots to the group get the bot removed.",
      "/antibot on|off [action=kick|ban]", "Remove bots added by non-admins")
_feat(("antiforward",), "antiforward", "↪️ <b>Anti-forward</b>", "Forwarded messages from non-admins are deleted.",
      "/antiforward on|off [action=delete duration=1h]", "Block forwarded messages")
_feat(("antilink",), "antilink", "🔗 <b>Anti-link</b>", "Links from non-admins are deleted unless the domain is whitelisted (/whitelist).",
      "/antilink on|off [action=delete duration=1h]", "Block links except whitelisted domains")
_feat(("antiraid",), "antiraid", "🚨 <b>Anti-raid</b>", "Too many joins in a short time starts raid mode: new joiners are removed and admins are alerted.",
      "/antiraid on|off [joins=8 window=30 lockdown=10m action=kick|ban|mute]", "Detect join floods automatically")
_feat(("captcha",), "captcha", "🧩 <b>Captcha</b>", "New members are muted until they pass a quick check.",
      "/captcha on|off [mode=button|math timeout=120 fail=kick|ban|mute]", "Verify new members before they can talk")


# ------------------------------------------------------------------------------------------------------------ approve
@command("approve", cat="security", usage="/approve <target>", desc="Exempt a member from every automatic filter (anti-spam, locks, flood…)")
async def cmd_approve(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", bot_right=None, target=True, allow_admin_target=True)
    added = await store.approve(r.chat_id, r.target, r.actor)
    await store.log_action(r.chat_id, r.actor, r.target, "approve")
    await reply(m, f"✅ {await r.who()} " + ("is now <b>approved</b>: automatic filters ignore them." if added else "was already approved."))


@command("unapprove", cat="security", usage="/unapprove <target>", desc="Remove a member's exemption")
async def cmd_unapprove(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict", bot_right=None, target=True, allow_admin_target=True)
    gone = await store.unapprove(r.chat_id, r.target)
    await store.log_action(r.chat_id, r.actor, r.target, "unapprove")
    await reply(m, f"{'🚫' if gone else 'ℹ️'} {await r.who()} " + ("is no longer approved." if gone else "wasn't approved."))


@command("approvedlist", "approved", cat="security", usage="/approvedlist", desc="List approved members")
async def cmd_approvedlist(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    ids = sorted(await store.approved_set(r.chat_id))
    if not ids:
        return await reply(m, "ℹ️ Nobody is approved.")
    await reply(m, f"✅ <b>Approved members</b> ({len(ids)})\n" + "\n".join(f"• {await perms.label(bot, r.chat_id, u)}" for u in ids[:60]))


# ------------------------------------------------------------------------------------------------------------ blocklist / whitelist / filters
def _kind_of(text: str) -> str:
    if " " in text.strip():
        return "phrase"
    if "." in text and "/" not in text and all(p for p in text.split(".")):
        return "domain"
    if "/" in text or text.startswith(("http://", "https://")):
        return "link"
    return "word"


def _fmt_filter(f: dict) -> str:
    extra = f"{f['action']}" + (f":{fmt_duration(f['duration'])}" if f.get("duration") else "")
    return f"<code>#{f['id']}</code> [{f['kind']}] <b>{esc(f['pattern'])}</b> → {extra}" + (" 💬" if f.get("response") else "")


def _act(token: str) -> tuple[str, int | None]:
    a, _, d = token.lower().partition(":")
    if a not in ACTIONS and a != "reply":
        raise ModError(f"❓ Unknown action <b>{esc(a)}</b>. Use: {', '.join(ACTIONS)} or reply.")
    dur = parse_duration(d) if d else None
    if d and dur is None:
        raise ModError("⏱ Bad duration after the action, e.g. <code>mute:1h</code>.")
    return a, dur


async def _blocklist(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    args = r.args
    sub = args[0].lower() if args else "list"
    flt = [f for f in await store.filters_of(r.chat_id) if f["action"] != "reply"]
    if sub == "list":
        if not flt:
            return await reply(m, "🚫 The blocklist is empty.\nAdd: <code>/blocklist add badword</code> · <code>/blocklist add free money</code> · <code>/blocklist add scam.com</code>")
        return await reply(m, f"🚫 <b>Blocklist</b> ({len(flt)})\n" + "\n".join(_fmt_filter(f) for f in flt[:60]))
    if sub in ("add", "block"):
        words = args[1:]
        if not words:
            raise ModError("ℹ️ Usage: <code>/blocklist add [word|phrase|link|domain] &lt;text&gt; [action=warn]</code>")
        action, dur = "delete", None
        if "=" in words[-1] and words[-1].lower().startswith("action="):
            action, dur = _act(words.pop().split("=", 1)[1])
        kind = None
        if words and words[0].lower() in ("word", "phrase", "link", "domain"):
            kind = words.pop(0).lower()
        text = " ".join(words).strip().lower()[:200]
        if not text:
            raise ModError("ℹ️ What should I block?")
        kind = kind or _kind_of(text)
        if kind in ("domain", "link"):
            text = host_of(text) if kind == "domain" else text
        await perms.require_bot(bot, r.chat_id, "delete")
        if action != "delete":
            await perms.require_bot(bot, r.chat_id, "restrict")
        fid = await store.add_filter(r.chat_id, kind, text, action, r.actor, dur)
        if fid is None:
            raise ModError(f"📦 The group already has {store.MAX_FILTERS} filters (the maximum).")
        await store.log_action(r.chat_id, r.actor, None, "blocklist-add", reason=f"{kind}:{text}")
        return await reply(m, f"🚫 Blocked {kind} <b>{esc(text)}</b> → {action} (id #{fid}).")
    if sub in ("remove", "rm", "del", "unblock"):
        ref = " ".join(args[1:]).strip().lower()
        if not ref:
            raise ModError("ℹ️ Usage: <code>/blocklist remove &lt;text or #id&gt;</code>")
        n = await store.remove_filter(r.chat_id, ref.lstrip("#"))
        await store.log_action(r.chat_id, r.actor, None, "blocklist-remove", reason=ref)
        return await reply(m, "✅ Removed." if n else "ℹ️ I couldn't find that entry.")
    if sub == "clear":
        await confirm.request(m, bot, "blocklist_clear", {}, title="Clear the whole blocklist", lines=[f"<b>{len(flt)}</b> blocklist entries will be deleted."])
        return
    raise ModError("ℹ️ Usage: <code>/blocklist [list|add|remove|clear]</code>")


@command("blocklist", "blacklist", cat="security", usage="/blocklist [list | add [word|phrase|link|domain] <text> [action=warn] | remove <text|#id> | clear]",
         desc="Blocked words, phrases, links and domains (alias /blacklist)")
async def cmd_blocklist(m: Message, command: CommandObject, bot: Bot):
    await _blocklist(m, command, bot)


async def _run_blocklist_clear(ctx: confirm.Ctx) -> str:
    n = await store.clear_filters(ctx.chat_id, actions=("delete", "warn", "mute", "kick", "ban"))
    await store.log_action(ctx.chat_id, ctx.issuer, None, "blocklist-clear", detail=f"{n} removed")
    return f"✅ Blocklist cleared ({n} entries)."


confirm.register("blocklist_clear", confirm.Spec(actor_right="info", bot_right=None, run=_run_blocklist_clear))


@command("whitelist", cat="security", usage="/whitelist [list | add <domain> | remove <domain>]", desc="Domains that anti-link always allows")
async def cmd_whitelist(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    sub = r.args[0].lower() if r.args else "list"
    cur = list(r.st["whitelist_domains"])
    if sub == "list":
        return await reply(m, "✅ <b>Whitelisted domains</b>\n" + ("\n".join(f"• {esc(d)}" for d in cur) if cur else "<i>none</i>") +
                           "\nAdd: <code>/whitelist add example.com</code>")
    if sub not in ("add", "remove", "rm") or len(r.args) < 2:
        raise ModError("ℹ️ Usage: <code>/whitelist add example.com</code> · <code>/whitelist remove example.com</code>")
    d = host_of(r.args[1])
    if "." not in d:
        raise ModError("❌ That doesn't look like a domain.")
    if sub == "add":
        if len(cur) >= 100:
            raise ModError("📦 100 domains is the maximum.")
        if d not in cur:
            cur.append(d)
    else:
        cur = [x for x in cur if x != d]
    await store.save_settings(r.chat_id, lambda s: s.update(whitelist_domains=cur))
    await store.log_action(r.chat_id, r.actor, None, f"whitelist-{sub}", reason=d)
    await reply(m, f"✅ {'Added' if sub == 'add' else 'Removed'} <b>{esc(d)}</b>.")


@command("filter", cat="security", usage="/filter <word|phrase|link|domain> <delete|warn|mute|kick|ban|reply>[:duration] <text> [=> reply text]",
         desc="Custom filter: match text and react (delete / warn / mute / kick / ban) and/or auto-reply")
async def cmd_filter(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    plain = " ".join(r.args)
    if len(r.args) < 3 or r.args[0].lower() not in ("word", "phrase", "link", "domain"):
        raise ModError("ℹ️ Usage: <code>/filter phrase warn free money =&gt; Please don't advertise.</code>\n"
                       "Kinds: word · phrase · link · domain. Actions: delete · warn · mute[:1h] · kick · ban · reply.\n"
                       "Everything after <code>=&gt;</code> is posted as an automatic reply.")
    kind = r.args[0].lower()
    action, dur = _act(r.args[1])
    left = plain.split(None, 2)[2] if len(plain.split(None, 2)) > 2 else ""
    pattern = left.split("=>", 1)[0].strip().lower()[:200]
    html = strip_command(m.html_text or "")
    response = ""
    for sep in ("=&gt;", "=>"):
        if sep in html:
            response = html.split(sep, 1)[1].strip()
            break
    if not pattern:
        raise ModError("ℹ️ What text should trigger the filter?")
    if action == "reply" and not response:
        raise ModError("💬 The <b>reply</b> action needs a reply text after <code>=&gt;</code>.")
    if kind == "domain":
        pattern = host_of(pattern)
    if action not in ("reply",):
        await perms.require_bot(bot, r.chat_id, "delete")
        if action != "delete":
            await perms.require_bot(bot, r.chat_id, "restrict")
    fid = await store.add_filter(r.chat_id, kind, pattern, action, r.actor, dur, response[:1500])
    if fid is None:
        raise ModError(f"📦 The group already has {store.MAX_FILTERS} filters (the maximum).")
    await store.log_action(r.chat_id, r.actor, None, "filter-add", reason=f"{kind}:{pattern} -> {action}")
    await reply(m, f"✅ Filter #{fid}: {kind} <b>{esc(pattern)}</b> → {action}" + (" + auto-reply" if response and action != "reply" else ""))


@command("filters", cat="security", usage="/filters", desc="List every custom filter (blocklist entries too)")
async def cmd_filters(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    flt = await store.filters_of(r.chat_id)
    if not flt:
        return await reply(m, "ℹ️ No filters yet. See <code>/filter</code> and <code>/blocklist</code>.")
    await reply(m, f"🧰 <b>Filters</b> ({len(flt)})\n" + "\n".join(_fmt_filter(f) for f in flt[:60]) + "\n\nRemove: <code>/stopfilter &lt;#id or text&gt;</code>")


@command("stopfilter", "unfilter", cat="security", usage="/stopfilter <#id | text>", desc="Delete a filter")
async def cmd_stopfilter(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    ref = " ".join(r.args).strip().lower().lstrip("#")
    if not ref:
        raise ModError("ℹ️ Usage: <code>/stopfilter 12</code> or <code>/stopfilter free money</code>")
    n = await store.remove_filter(r.chat_id, ref)
    await store.log_action(r.chat_id, r.actor, None, "filter-remove", reason=ref)
    await reply(m, "✅ Filter removed." if n else "ℹ️ No such filter.")


# ------------------------------------------------------------------------------------------------------------ protection levels / raid / lockdown
@command("protection", "protectionlevel", cat="security", usage="/protection [off|low|medium|high]",
         desc="One-step community protection level that configures flood / spam / links / captcha / raid / slow mode together")
async def cmd_protection(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="info", bot_right=None)
    lv = (r.args[0].lower() if r.args else "")
    if lv not in LEVELS:
        desc = {"off": "everything automatic is off", "low": "flood + spam (delete) + anti-bot", "medium": "low + warnings, anti-link, button captcha, anti-raid",
                "high": "medium + anti-forward, math captcha, stricter flood / raid limits, 3s slow mode"}
        return await reply(m, f"🛡 <b>Protection level: {r.st['protection']}</b>\n" + "\n".join(f"• <code>{k}</code> - {v}" for k, v in desc.items()) +
                           "\n\nApply: <code>/protection medium</code>  (fine-tune afterwards with /antispam, /antilink …)")
    if lv != "off":
        await perms.require_bot(bot, r.chat_id, "delete")
        await perms.require_bot(bot, r.chat_id, "restrict")
    await store.save_settings(r.chat_id, lambda s: apply_preset(s, lv))
    await store.log_action(r.chat_id, r.actor, None, "protection", reason=lv)
    await reply(m, f"🛡 Protection level set to <b>{lv}</b>.\nSee the result with <code>/modpanel</code>. Admins and approved members are never affected.")


@command("raidmode", cat="security", usage="/raidmode on [duration] | off", desc="Manually start / stop raid mode (new joiners are removed)")
async def cmd_raidmode(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict")
    a = (r.args[0].lower() if r.args else "status")
    until = r.st["antiraid"].get("until", 0)
    if a == "status":
        return await reply(m, "🚨 Raid mode is <b>" + (f"ACTIVE for another {fmt_duration(until - store.now())}" if until > store.now() else "off") + "</b>.")
    if a == "on":
        dur = parse_duration(r.args[1]) if len(r.args) > 1 else r.st["antiraid"]["lockdown"]
        dur = dur or r.st["antiraid"]["lockdown"]
        await store.save_settings(r.chat_id, lambda s: s["antiraid"].update(until=store.now() + dur))
        await store.log_action(r.chat_id, r.actor, None, "raidmode-on", duration=dur)
        return await reply(m, f"🚨 Raid mode <b>ON</b> for {fmt_duration(dur)}: new joiners will be {r.st['antiraid']['action']}ed.")
    if a == "off":
        await store.save_settings(r.chat_id, lambda s: s["antiraid"].update(until=0))
        await store.log_action(r.chat_id, r.actor, None, "raidmode-off")
        return await reply(m, "✅ Raid mode <b>OFF</b>.")
    raise ModError("ℹ️ Usage: <code>/raidmode on [10m]</code> · <code>/raidmode off</code>")


@command("lockdown", "emergency", cat="security", usage="/lockdown on [duration] | off | status",
         desc="Emergency mode: silence the whole group at once (asks YES/NO to start)")
async def cmd_lockdown(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right="restrict")
    a = (r.args[0].lower() if r.args else "status")
    ld = r.st["lockdown"]
    if a == "status":
        return await reply(m, "🚨 Lockdown is <b>" + (("ACTIVE" + (f" for another {fmt_duration(ld['until'] - store.now())}" if ld["until"] else "")) if ld["on"] else "off") + "</b>.")
    if a == "off":
        return await reply(m, await lockdown.lift(bot, r.chat_id, r.actor))
    if a == "on":
        dur = parse_duration(r.args[1]) if len(r.args) > 1 else None
        if ld["on"]:
            raise ModError("🚨 Lockdown is already active.")
        await confirm.request(m, bot, "lockdown", {"dur": dur}, title="Start EMERGENCY LOCKDOWN",
                              lines=["Nobody except admins will be able to send messages" + (f" for {fmt_duration(dur)}." if dur else " until you run <code>/lockdown off</code>.")],
                              warning="This changes what every member of the group can do.")
        return
    raise ModError("ℹ️ Usage: <code>/lockdown on [30m]</code> · <code>/lockdown off</code>")


async def _run_lockdown(ctx: confirm.Ctx) -> str:
    return await lockdown.engage(ctx.bot, ctx.chat_id, ctx.payload.get("dur"), ctx.issuer)


confirm.register("lockdown", confirm.Spec(actor_right="restrict", bot_right="restrict", run=_run_lockdown))


@command("suspicious", cat="security", usage="/suspicious", desc="Recent suspicious-activity alerts the monitor recorded")
async def cmd_suspicious(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    rows = await store.recent_actions(r.chat_id, 10, actions=("suspicious",))
    if not rows:
        return await reply(m, "🕵️ Nothing suspicious recorded recently.")
    lines = [f"• {await perms.label(bot, r.chat_id, x.target_id)} - {esc(x.reason)} ({fmt_duration(store.now() - x.created_at)} ago)" for x in rows]
    await reply(m, "🕵️ <b>Suspicious activity</b>\n" + "\n".join(lines))
