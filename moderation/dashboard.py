"""8. Interactive moderation dashboard (inline keyboards). Anyone can be shown it, but:
  * viewing needs to be an admin of that group (checked live on EVERY tap, never trusted from the button),
  * changing a setting needs the 'Change group info' right (or group owner / bot owner),
  * destructive / major changes go through the YES / NO confirmation."""
from __future__ import annotations

from aiogram import Bot, F
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from . import commands_msg, confirm, perms, store
from .base import command, prepare, reply, router
from .defaults import LEVELS, LOCKS, WARN_MODES, apply_preset
from .perms import ModError, esc
from .util import fmt_duration

FEATURES = (("flood", "🌊 Flood"), ("spam", "🧹 Spam"), ("antilink", "🔗 Links"), ("antiforward", "↪️ Forwards"),
            ("antibot", "🤖 Bots"), ("antiraid", "🚨 Raid"), ("captcha", "🧩 Captcha"))
DASH_LOCKS = ("text", "media", "photos", "videos", "stickers", "gifs", "documents", "links", "polls", "voice", "videonotes", "forwarded", "contact", "location")


def _b(text: str, data: str) -> B:
    return B(text=text, callback_data=data)


def _nav(chat_id: int, back: str = "home") -> list[B]:
    return [_b("⬅️ Back", f"mod:d:{back}:{chat_id}"), _b("🏠 Home", f"mod:d:home:{chat_id}")]


def _onoff(v: bool) -> str:
    return "🟢" if v else "🔴"


async def render(page: str, chat_id: int, bot: Bot) -> tuple[str, InlineKeyboardMarkup]:
    st = await store.get_settings(chat_id)
    c = chat_id
    if page == "home":
        ms, ws, rc = await store.member_stats(c), await store.warn_stats(c), await store.report_counts(c)
        pun = await store.active_punishments(c, 200)
        on = sum(1 for k, _ in FEATURES if st[k]["on"])
        text = (f"🛡 <b>Moderation dashboard</b>\n"
                f"Protection level: <b>{st['protection']}</b> · protections on: <b>{on}/{len(FEATURES)}</b>\n"
                f"🚨 Raid mode: {'ACTIVE' if st['antiraid'].get('until', 0) > store.now() else 'off'} · lockdown: {'ACTIVE' if st['lockdown']['on'] else 'off'}\n"
                f"👥 Tracked members: {ms['tracked']} · ⚠️ active warnings: {ws['active']} · ⏳ temp punishments: {len(pun)} · 🚩 pending reports: {rc.get('pending', 0)}")
        rows = [[_b("🛡 Security", f"mod:d:sec:{c}"), _b("🧹 Anti-spam", f"mod:d:spam:{c}")],
                [_b("⚠️ Warnings", f"mod:d:warn:{c}"), _b("🔐 Locks", f"mod:d:locks:{c}")],
                [_b("👋 Welcome/Goodbye", f"mod:d:greet:{c}"), _b("🚫 Lists", f"mod:d:lists:{c}")],
                [_b("⏳ Temp punishments", f"mod:d:temp:{c}"), _b("📒 Recent actions", f"mod:d:logs:{c}")],
                [_b("🚩 Reports", f"mod:d:reports:{c}"), _b("👮 Admins", f"mod:d:admins:{c}")],
                [_b("🩺 Health", f"mod:d:health:{c}"), _b("♻️ Refresh", f"mod:d:home:{c}")]]
        return text, InlineKeyboardMarkup(inline_keyboard=rows)
    if page == "sec":
        rows = [[_b(f"{_onoff(st[k]['on'])} {t}", f"mod:t:{k}:{c}") for k, t in FEATURES[i:i + 2]] for i in range(0, len(FEATURES), 2)]
        rows.append([_b(f"Level: {lv}" + (" ✔" if st["protection"] == lv else ""), f"mod:t:prot-{lv}:{c}") for lv in LEVELS[1:]])
        rows.append([_b("🚨 Lockdown " + ("OFF" if st["lockdown"]["on"] else "ON"), f"mod:t:lockdown:{c}")])
        rows.append(_nav(c))
        a = st["antiraid"]
        return (f"🛡 <b>Security</b>\nTap a protection to switch it. Presets: low / medium / high.\n"
                f"Captcha: {st['captcha']['mode']} / {st['captcha']['timeout']}s → {st['captcha']['fail']}\n"
                f"Raid trigger: {a['joins']} joins / {a['window']}s → {a['action']} for {fmt_duration(a['lockdown'])}\n"
                f"Slow mode: {fmt_duration(st['slowmode']) if st['slowmode'] else 'off'}\n<i>Fine-tune with /antispam, /antilink, /captcha …</i>"), InlineKeyboardMarkup(inline_keyboard=rows)
    if page == "spam":
        f, s = st["flood"], st["spam"]
        rows = [[_b("➖", f"mod:t:floodlimit-:{c}"), _b(f"Flood {f['limit']}/{f['window']}s", f"mod:t:flood:{c}"), _b("➕", f"mod:t:floodlimit+:{c}")],
                [_b("➖", f"mod:t:dup-:{c}"), _b(f"Duplicates ≥ {s['dup_limit']}", f"mod:t:spam:{c}"), _b("➕", f"mod:t:dup+:{c}")],
                [_b(f"Suspicious links {_onoff(s['suspicious_links'])}", f"mod:t:suslinks:{c}"), _b(f"Cross-user waves {_onoff(s['cross_user'])}", f"mod:t:cross:{c}")],
                [_b(f"Flood action: {f['action']}", f"mod:t:floodact:{c}"), _b(f"Spam action: {s['action']}", f"mod:t:spamact:{c}")], _nav(c)]
        return (f"🧹 <b>Anti-spam &amp; flood</b>\nFlood {_onoff(f['on'])}: more than {f['limit']} msgs in {f['window']}s → {f['action']}\n"
                f"Spam {_onoff(s['on'])}: same text ×{s['dup_limit']} in {s['dup_window']}s, ≥{s['repeat_chars']} repeated chars, ≥{s['mentions']} mentions → {s['action']}"), InlineKeyboardMarkup(inline_keyboard=rows)
    if page == "warn":
        w = st["warn"]
        ws = await store.warn_stats(c)
        rows = [[_b("➖", f"mod:t:wlim-:{c}"), _b(f"Limit {w['limit']}", f"mod:d:warn:{c}"), _b("➕", f"mod:t:wlim+:{c}")],
                [_b(f"At limit: {w['mode']}  (tap to change)", f"mod:t:wmode:{c}")], _nav(c)]
        return (f"⚠️ <b>Warnings</b>\nLimit: <b>{w['limit']}</b> → <b>{w['mode']}</b>" + (f" ({fmt_duration(w['duration'])})" if w["mode"] in ("tmute", "tban") else "") +
                f"\nWarnings expire: {fmt_duration(w['expire']) if w['expire'] else 'never'}\nActive: <b>{ws['active']}</b> on <b>{ws['users']}</b> members · new in 7d: {ws['week']}"), InlineKeyboardMarkup(inline_keyboard=rows)
    if page == "locks":
        rows = [[_b(f"{'🔒' if st['locks'].get(n) else '▫️'} {n}", f"mod:t:lock-{n}:{c}") for n in DASH_LOCKS[i:i + 3]] for i in range(0, len(DASH_LOCKS), 3)]
        rows.append(_nav(c))
        return "🔐 <b>Locks</b>\nTap to lock / unlock a message type (admins are exempt). <code>/locktypes</code> shows which are native vs bot-side.", InlineKeyboardMarkup(inline_keyboard=rows)
    if page == "greet":
        wl, gb = dict(st["welcome"]), dict(st["goodbye"])
        from . import api
        wl["on"] = not await api.greeting_switched_off(c, "join")                 # the shown state is the real switch (off = nothing is sent)
        gb["on"] = not await api.greeting_switched_off(c, "leave")
        rows = [[_b(f"Welcome {_onoff(wl['on'])}", f"mod:t:welcome:{c}"), _b(f"Goodbye {_onoff(gb['on'])}", f"mod:t:goodbye:{c}")],
                [_b(f"Reports {_onoff(st['report']['on'])}", f"mod:t:reports:{c}")], _nav(c)]
        return (f"👋 <b>Welcome / goodbye</b>\nWelcome {_onoff(wl['on'])}: {esc((wl['text'] or '(default greeting)')[:200])}\nGoodbye {_onoff(gb['on'])}: {esc((gb['text'] or '(none)')[:200])}\n"
                f"<i>Edit with /setwelcome and /setgoodbye.</i>"), InlineKeyboardMarkup(inline_keyboard=rows)
    if page == "lists":
        flt = await store.filters_of(c)
        appr = await store.approved_set(c)
        rows = [[_b("🗑 Reset ALL moderation settings", f"mod:t:resetall:{c}")], _nav(c)]
        bl = [f for f in flt if f["action"] != "reply"]
        return (f"🚫 <b>Blocklist &amp; lists</b>\nBlocked entries: <b>{len(bl)}</b> · auto-replies: {len(flt) - len(bl)} · whitelisted domains: <b>{len(st['whitelist_domains'])}</b> · approved members: <b>{len(appr)}</b>\n"
                + "\n".join(f"• [{f['kind']}] {esc(f['pattern'])} → {f['action']}" for f in bl[:8]) + "\n<i>Manage with /blocklist, /whitelist, /filter, /approve.</i>"), InlineKeyboardMarkup(inline_keyboard=rows)
    if page == "temp":
        pun = await store.active_punishments(c, 12)
        lines = [f"• {await perms.label(bot, c, p.user_id)} - {p.kind}, {fmt_duration(max(0, p.expires_at - store.now()))} left" for p in pun]
        return "⏳ <b>Active temporary punishments</b>\n" + ("\n".join(lines) if lines else "None."), InlineKeyboardMarkup(inline_keyboard=[_nav(c)])
    if page == "logs":
        rows_ = await store.recent_actions(c, 8)
        lines = [f"• {fmt_duration(store.now() - x.created_at)} ago: <b>{esc(x.action)}</b>" + (f" → {await perms.label(bot, c, x.target_id)}" if x.target_id else "") + ("" if x.result == "ok" else f" ❌") for x in rows_]
        return "📒 <b>Recent actions</b>\n" + ("\n".join(lines) if lines else "Nothing yet.") + "\n<i>Full history: /modlog</i>", InlineKeyboardMarkup(inline_keyboard=[_nav(c)])
    if page == "reports":
        pend = await store.pending_reports(c, 8)
        lines = [f"<b>#{x.id}</b> {await perms.label(bot, c, x.target_id)} - {esc((x.reason or x.excerpt or '')[:60])}" for x in pend]
        rows = [[_b(f"✅ Resolve #{x.id}", f"mod:rp:ok:{x.id}") for x in pend[i:i + 3]] for i in range(0, len(pend), 3)] + [_nav(c)]
        return "🚩 <b>Pending reports</b>\n" + ("\n".join(lines) if lines else "None 🎉"), InlineKeyboardMarkup(inline_keyboard=rows)
    if page == "admins":
        ads = [a for a in (await perms.admins(bot, c, force=True)).values() if not a.user.is_bot]
        me = await perms.bot_member(bot, c)
        miss = [perms.RIGHT_LABEL[x] for x in ("restrict", "delete", "pin", "promote", "info") if not perms.has_right(me, x)]
        return (f"👮 <b>Admins</b> ({len(ads)})\n" + "\n".join(f"• {esc(a.user.full_name)}" + (f" - {esc(a.custom_title)}" if getattr(a, 'custom_title', None) else "") for a in ads[:20]) +
                f"\n\n🤖 My rights: {'all good ✅' if not miss else '⚠️ missing ' + ', '.join(miss)}"), InlineKeyboardMarkup(inline_keyboard=[_nav(c)])
    if page == "health":
        ms = await store.member_stats(c)
        return (f"🩺 <b>Health</b>\nTracked {ms['tracked']} · active 24h {ms['active_24h']} · 7d {ms['active_7d']} · new 7d {ms['new_7d']} · left {ms['left']}\n<i>Full: /health · /securityreport</i>"), InlineKeyboardMarkup(inline_keyboard=[_nav(c)])
    return "Unknown page.", InlineKeyboardMarkup(inline_keyboard=[_nav(c)])


@command("modpanel", "dashboard", "mod", cat="report", usage="/modpanel", desc="Interactive moderation dashboard (admins)", gate=False)
async def cmd_modpanel(m: Message, command: CommandObject, bot: Bot):
    r = await prepare(m, command, bot, right=None, bot_right=None)
    text, kb = await render("home", r.chat_id, bot)
    await reply(m, text, kb)


async def _edit(cb: CallbackQuery, text: str, kb) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass


@router.callback_query(F.data.startswith("mod:d:"))
async def dash_nav(cb: CallbackQuery, bot: Bot):
    _, _, page, cid = cb.data.split(":")
    chat_id = int(cid)
    await perms.require_actor(bot, chat_id, cb.from_user.id, None)
    text, kb = await render(page, chat_id, bot)
    await _edit(cb, text, kb)
    await cb.answer()


_CYCLE_ACT = ("delete", "warn", "mute", "kick", "ban")


@router.callback_query(F.data.startswith("mod:t:"))
async def dash_toggle(cb: CallbackQuery, bot: Bot):
    _, _, key, cid = cb.data.split(":")
    chat_id, actor = int(cid), cb.from_user.id
    await perms.require_actor(bot, chat_id, actor, "info")            # changing anything needs 'Change group info'
    st = await store.get_settings(chat_id)
    page = "sec"

    async def need(*rights):
        for rt in rights:
            await perms.require_bot(bot, chat_id, rt)

    if key in dict(FEATURES):
        on = not st[key]["on"]
        if on:
            await need("delete") if key not in ("antibot", "captcha", "antiraid") else await need("restrict")
        await store.save_settings(chat_id, lambda s: s[key].update(on=on))
        await store.log_action(chat_id, actor, None, f"{key}-{'on' if on else 'off'}", source="dashboard")
        page = "spam" if key in ("flood", "spam") else "sec"
    elif key.startswith("prot-"):
        lv = key[5:]
        if lv not in LEVELS:
            return await cb.answer()
        await need("delete", "restrict")
        await store.save_settings(chat_id, lambda s: apply_preset(s, lv))
        await store.log_action(chat_id, actor, None, "protection", reason=lv, source="dashboard")
    elif key == "lockdown":
        if st["lockdown"]["on"]:
            from . import lockdown
            await lockdown.lift(bot, chat_id, actor)
        else:
            await perms.require_actor(bot, chat_id, actor, "restrict")
            await need("restrict")
            await confirm.request(cb.message, bot, "lockdown", {"dur": None}, title="Start EMERGENCY LOCKDOWN", issuer=cb.from_user,
                                  lines=["Nobody except admins will be able to send messages until lockdown is lifted."], warning="This changes what every member of the group can do.")
            return await cb.answer("Confirm below ⬇️")
    elif key in ("floodlimit-", "floodlimit+"):
        d = -1 if key.endswith("-") else 1
        await store.save_settings(chat_id, lambda s: s["flood"].update(limit=max(2, min(50, s["flood"]["limit"] + d))))
        page = "spam"
    elif key in ("dup-", "dup+"):
        d = -1 if key.endswith("-") else 1
        await store.save_settings(chat_id, lambda s: s["spam"].update(dup_limit=max(2, min(10, s["spam"]["dup_limit"] + d))))
        page = "spam"
    elif key in ("suslinks", "cross"):
        f = "suspicious_links" if key == "suslinks" else "cross_user"
        await store.save_settings(chat_id, lambda s: s["spam"].update({f: not s["spam"][f]}))
        page = "spam"
    elif key in ("floodact", "spamact"):
        sec = "flood" if key == "floodact" else "spam"
        nxt = _CYCLE_ACT[(_CYCLE_ACT.index(st[sec]["action"]) + 1) % len(_CYCLE_ACT)] if st[sec]["action"] in _CYCLE_ACT else "delete"
        if nxt != "delete":
            await need("restrict")
        await store.save_settings(chat_id, lambda s: s[sec].update(action=nxt))
        page = "spam"
    elif key in ("wlim-", "wlim+"):
        d = -1 if key.endswith("-") else 1
        await store.save_settings(chat_id, lambda s: s["warn"].update(limit=max(1, min(20, s["warn"]["limit"] + d))))
        page = "warn"
    elif key == "wmode":
        nxt = WARN_MODES[(WARN_MODES.index(st["warn"]["mode"]) + 1) % len(WARN_MODES)] if st["warn"]["mode"] in WARN_MODES else "mute"
        await need("restrict")
        await store.save_settings(chat_id, lambda s: s["warn"].update(mode=nxt))
        page = "warn"
    elif key.startswith("lock-"):
        name = key[5:]
        if name not in LOCKS or name == "all":
            return await cb.answer()
        await commands_msg.apply_locks(bot, chat_id, [name], not st["locks"].get(name), actor)
        page = "locks"
    elif key in ("welcome", "goodbye"):
        from . import api
        kind = "join" if key == "welcome" else "leave"
        on = await api.greeting_switched_off(chat_id, kind)                      # it is OFF now -> turn it ON (and the other way round)
        await api.set_greeting_switch(chat_id, kind, not on)
        if st[key]["text"]:
            await store.save_settings(chat_id, lambda s: s[key].update(on=on))
        page = "greet"
    elif key == "reports":
        await store.save_settings(chat_id, lambda s: s["report"].update(on=not s["report"]["on"]))
        page = "greet"
    elif key == "resetall":
        await confirm.request(cb.message, bot, "resetall", {}, title="RESET all moderation settings", issuer=cb.from_user,
                              lines=["Every moderation setting of this group goes back to its default: locks (Telegram permissions are NOT changed), protections, welcome / goodbye, rules, warn config, whitelist.",
                                     "Warnings, filters, approvals and logs are kept."], warning="Use /modbackup first if you may want this back.")
        return await cb.answer("Confirm below ⬇️")
    else:
        return await cb.answer()
    text, kb = await render(page, chat_id, bot)
    await _edit(cb, text, kb)
    await cb.answer("Saved ✔")


async def _run_resetall(ctx: confirm.Ctx) -> str:
    await store.reset_settings(ctx.chat_id)
    await store.log_action(ctx.chat_id, ctx.issuer, None, "reset-settings", source="dashboard")
    return "✅ All moderation settings were reset to defaults."


confirm.register("resetall", confirm.Spec(actor_right="info", bot_right=None, run=_run_resetall))
