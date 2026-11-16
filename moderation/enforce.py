"""Automatic protection: member tracking, locks, slow mode, filters, anti-flood / spam / link / forward / bot / raid, captcha,
welcome / goodbye, suspicious-activity monitor, and logging of admin actions done outside the bot.

Runs as OUTER middleware on the dispatcher, wrapped in try/except: a bug here can never stop other features from receiving
the update. Everything is opt-in per group (defaults are all OFF), so merely having this module installed changes nothing."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from aiogram import BaseMiddleware, Bot, F
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from . import actions, greet, perms, store
from .base import GROUP, router
from .defaults import kinds_of, locked_by
from .matching import (DuplicateTracker, SlidingCounter, count_mentions, domain_matches, extract_hosts, has_repeated_chars,
                       link_flags, match_filters, suspicion)
from .perms import ModError, esc, tg
from .util import fmt_duration, mention

log = logging.getLogger("moderation.enforce")

SERVICE = {"new_chat_members": "join", "left_chat_member": "leave", "pinned_message": "pin", "new_chat_title": "title", "new_chat_photo": "photo",
           "delete_chat_photo": "photo", "group_chat_created": "other", "supergroup_chat_created": "other", "channel_chat_created": "other",
           "message_auto_delete_timer_changed": "other", "migrate_to_chat_id": "other", "migrate_from_chat_id": "other"}
_flood, _dups, _slow = SlidingCounter(), DuplicateTracker(), {}
_joins = SlidingCounter()
_recent_joiners: dict[int, deque] = defaultdict(lambda: deque(maxlen=200))
_join_seen: dict[tuple[int, int], float] = {}
_joined_at: dict[tuple[int, int], float] = {}
_once: dict[tuple, float] = {}
_tasks: set[asyncio.Task] = set()


def _cooldown(key: tuple, secs: float) -> bool:
    """True if `key` fired less than `secs` ago (else records it)."""
    now = time.monotonic()
    if len(_once) > 20000:
        for k in [k for k, t in _once.items() if now - t > 600]:
            _once.pop(k, None)
    if key in _once and now - _once[key] < secs:
        return True
    _once[key] = now
    return False


def gc() -> None:
    now = time.monotonic()
    for d, ttl in ((_join_seen, 60), (_joined_at, 3600), (_once, 900)):
        for k in [k for k, t in d.items() if now - t > ttl]:
            d.pop(k, None)
    for k in [k for k, t in _slow.items() if now - t > 3700]:
        _slow.pop(k, None)


def _later(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _say_temp(bot: Bot, chat_id: int, text: str, secs: int = 10, kb=None) -> None:
    try:
        sent = await bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        return

    async def rm():
        await asyncio.sleep(secs)
        await actions.delete_message(bot, chat_id, sent.message_id)
    _later(rm())


async def _alert(bot: Bot, chat_id: int, key: str, text: str) -> None:
    if not _cooldown(("alert", chat_id, key), 900):
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            pass


# ------------------------------------------------------------------------------------------------------------ middlewares
class MessageEnforcer(BaseMiddleware):
    edited = False

    async def __call__(self, handler, m: Message, data):
        try:
            if await process_message(data["bot"], m, self.edited):
                return None                      # message was removed by a protection: other features don't need to see it
        except Exception:
            log.exception("moderation enforcement error (ignored)")
        return await handler(m, data)


class EditEnforcer(MessageEnforcer):
    edited = True


class MemberWatcher(BaseMiddleware):
    async def __call__(self, handler, u: ChatMemberUpdated, data):
        try:
            await process_member_update(data["bot"], u)
        except Exception:
            log.exception("moderation member-update error (ignored)")
        return await handler(u, data)


@router.chat_member()
@router.my_chat_member()
async def _watch(_u: ChatMemberUpdated):
    return UNHANDLED          # registered only so Telegram is asked for these updates; real work is in MemberWatcher


@router.edited_message(GROUP)
async def _watch_edits(_m: Message):
    return UNHANDLED


# ------------------------------------------------------------------------------------------------------------ message pipeline
@dataclass
class Violation:
    feature: str
    reason: str
    action: str = "delete"
    duration: int | None = None
    silent: bool = False
    reply: str = ""
    keep: bool = False        # True: don't delete the message (auto-reply filters)


def _primary_kind(m: Message) -> str:
    k = kinds_of(m)
    for name in ("document", "audio", "video", "voice", "video_note", "gif", "photo", "sticker", "poll"):
        if name in k:
            return name
    return "text"


def _entity_urls(m: Message) -> list[str]:
    out = []
    text = m.text or m.caption or ""
    for e in (m.entities or m.caption_entities or []):
        if e.type == "text_link" and e.url:
            out.append(e.url)
        elif e.type == "url":
            try:
                out.append(e.extract_from(text))
            except Exception:
                pass
    return out


async def process_message(bot: Bot, m: Message, edited: bool) -> bool:
    if m.chat.type not in ("group", "supergroup"):
        return False
    ct = str(getattr(m.content_type, "value", m.content_type))
    st = await store.get_settings(m.chat.id)
    if ct in SERVICE or ct.startswith(("video_chat", "forum_topic", "general_forum")):
        if not edited:
            await on_service(bot, m, ct, st)
        return False
    user = m.from_user
    if user is None or (m.sender_chat and m.sender_chat.id == m.chat.id) or getattr(m, "is_automatic_forward", False):
        return False
    if not edited:
        store.note_member(m.chat.id, user, m.message_id, _primary_kind(m))
    if user.is_bot:
        return False
    v = await detect(bot, m, st, edited)
    if v is None:
        return False
    if await perms.is_exempt(bot, m.chat.id, user.id, st):
        return False
    return await apply(bot, m, v, st)


async def detect(bot: Bot, m: Message, st: dict, edited: bool) -> Violation | None:
    chat, uid, now = m.chat.id, m.from_user.id, time.monotonic()
    text = m.text or m.caption or ""
    urls = _entity_urls(m)
    hosts = extract_hosts(text, urls) if (text or urls) else []

    if st["slowmode"] and not edited:
        last = _slow.get((chat, uid))
        if last is not None and now - last < st["slowmode"]:
            return Violation("slowmode", f"slow mode ({fmt_duration(st['slowmode'])})", silent=True)
        _slow[(chat, uid)] = now

    if st["locks"]:
        lk = locked_by(st["locks"], kinds_of(m), bool(hosts))
        if lk:
            return Violation(f"lock-{lk}", f"{lk} is locked", silent=_cooldown(("locknote", chat, uid), 30))

    flt = await store.filters_of(chat)
    if flt and (text or hosts):
        f = match_filters(flt, text, hosts)
        if f:
            if f["action"] == "reply":
                if f["response"] and not _cooldown(("freply", chat, f["id"]), 30):
                    return Violation("filter-reply", f"filter #{f['id']}", action="reply", reply=f["response"], keep=True)
            else:
                dur = f.get("duration") if f["action"] in ("mute", "ban") else None
                return Violation("filter", f"{f['kind']} filter: {f['pattern'][:40]}", f["action"], dur, reply=f["response"] or "")

    if st["antiforward"]["on"] and (m.forward_origin or getattr(m, "forward_date", None)):
        a = st["antiforward"]
        return Violation("antiforward", "forwarded message", a["action"], a["duration"] if a["action"] in ("mute", "ban") else None)

    if st["antilink"]["on"] and hosts:
        wl = st["whitelist_domains"]
        bad = [h for h in hosts if not any(domain_matches(h, d) for d in wl)]
        if bad:
            a = st["antilink"]
            return Violation("antilink", f"link: {bad[0][:40]}", a["action"], a["duration"] if a["action"] in ("mute", "ban") else None)

    sp = st["spam"]
    if sp["on"] and not edited:
        act, dur = sp["action"], (sp["duration"] if sp["action"] in ("mute", "ban") else None)
        if len(text) >= 4:
            own, users = _dups.check(chat, uid, text, now, sp["dup_window"])
            if own >= sp["dup_limit"]:
                return Violation("spam-duplicate", f"same message {own}×", act, dur)
            if sp["cross_user"] and users >= 3 and len(text) >= 8:
                return Violation("spam-wave", f"same text from {users} accounts", act, dur)
        if sp["repeat_chars"] and has_repeated_chars(text, sp["repeat_chars"]):
            return Violation("spam-repeat", "repeated characters", act, dur)
        if sp["mentions"] and count_mentions(text) >= sp["mentions"]:
            return Violation("spam-mentions", "mass mentions", act, dur)
        if sp["suspicious_links"] and hosts:
            for h in hosts:
                fl = link_flags(h, next((u for u in urls if h in u), text))
                if fl:
                    return Violation("spam-link", f"suspicious link ({'/'.join(fl)})", act, dur)

    fl_ = st["flood"]
    if fl_["on"] and not edited:
        if _flood.hit((chat, uid), now, fl_["window"]) > fl_["limit"]:
            _flood.clear((chat, uid))
            return Violation("flood", f"flood (> {fl_['limit']} in {fl_['window']}s)", fl_["action"], fl_["duration"] if fl_["action"] in ("mute", "ban") else None)

    if st["suspicious"]["on"] and not edited and (hosts or m.from_user.full_name):
        joined = _joined_at.get((chat, uid))
        score, why = suspicion(m.from_user.full_name, m.from_user.username, text, (time.monotonic() - joined) if joined else None, hosts, tuple(urls))
        if score >= 3 and not _cooldown(("susp", chat, uid), 600):
            await store.log_action(chat, 0, uid, "suspicious", reason=", ".join(why)[:280], detail=f"score {score}", source="auto")
    return None


async def apply(bot: Bot, m: Message, v: Violation, st: dict) -> bool:
    chat, user = m.chat.id, m.from_user
    if v.action == "reply" and v.keep:
        try:
            await m.reply(v.reply, disable_web_page_preview=True)
        except Exception:
            pass
        return False
    can_del = await perms.bot_can(bot, chat, "delete")
    deleted = await actions.delete_message(bot, chat, m.message_id) if can_del else False
    if not can_del:
        await _alert(bot, chat, "nodel", "⚠️ A protection tried to remove a message but I don't have the <b>Delete messages</b> admin right. Please enable it.")
    await store.log_action(chat, bot.id, user.id, "auto-" + v.feature, reason=v.reason, result="ok" if deleted else "failed",
                           detail="" if deleted else "could not delete the message", source="auto")
    note = ""
    if v.action == "warn":
        try:
            note, _ = await actions.warn(bot, chat, user.id, actor=bot.id, reason=v.reason, st=st, source="auto")
        except ModError as e:
            note = str(e)
    elif v.action in ("mute", "ban", "kick"):
        try:
            if v.action == "mute":
                await actions.mute(bot, chat, user.id, actor=bot.id, reason=v.reason, duration=v.duration, source="auto")
            elif v.action == "ban":
                await actions.ban(bot, chat, user.id, actor=bot.id, reason=v.reason, duration=v.duration, source="auto")
            else:
                await actions.kick(bot, chat, user.id, actor=bot.id, reason=v.reason, source="auto")
            note = f"🚫 {v.action}{'ned' if v.action == 'ban' else 'd' if v.action in ('mute',) else 'ed'}" + (f" for {fmt_duration(v.duration)}" if v.duration else "")
        except ModError as e:
            await _alert(bot, chat, "punish-fail", f"⚠️ I couldn't {v.action} a member for «{esc(v.reason)}»:\n{e}")
    if v.reply and v.action != "reply":
        _later(_say_temp(bot, chat, v.reply, 20))
    if not v.silent and not _cooldown(("note", chat, user.id, v.feature), 20):
        text = f"🛡 {mention(user.id, user.full_name)}: <i>{esc(v.reason)}</i>" + (f"\n{note}" if note else "")
        _later(_say_temp(bot, chat, text, 10))
    return deleted


# ------------------------------------------------------------------------------------------------------------ service messages / joins
async def on_service(bot: Bot, m: Message, ct: str, st: dict) -> None:
    kind = SERVICE.get(ct, "other")
    store.note_service(m.chat.id, m.message_id)
    if ct == "new_chat_members":
        for u in m.new_chat_members or []:
            await on_join(bot, m.chat, u, m.from_user, st)
    elif ct == "left_chat_member" and m.left_chat_member:
        await on_leave(bot, m.chat, m.left_chat_member, st)
    cs = st["cleanservice"]
    if cs["on"] and kind in cs["types"] and await perms.bot_can(bot, m.chat.id, "delete"):
        await actions.delete_message(bot, m.chat.id, m.message_id)


async def _auto_action(bot: Bot, chat_id: int, uid: int, action: str, reason: str, duration: int | None = None) -> bool:
    try:
        if action == "ban":
            await actions.ban(bot, chat_id, uid, actor=bot.id, reason=reason, duration=duration, source="auto")
        elif action == "mute":
            await actions.mute(bot, chat_id, uid, actor=bot.id, reason=reason, duration=duration, source="auto")
        else:
            await actions.kick(bot, chat_id, uid, actor=bot.id, reason=reason, source="auto")
        return True
    except ModError as e:
        await _alert(bot, chat_id, "auto-fail", f"⚠️ A protection could not act ({esc(reason)}):\n{e}")
        return False


async def on_join(bot: Bot, chat, user, adder, st: dict) -> None:
    now = time.monotonic()
    key = (chat.id, user.id)
    if key in _join_seen and now - _join_seen[key] < 20:
        return                                              # same join reported twice (service message + chat_member update)
    _join_seen[key] = now
    if user.id == bot.id:
        perms.invalidate(chat.id)
        return
    store.note_member(chat.id, user, None, status="member", count_msg=False)
    _joined_at[key] = now
    exempt = await perms.is_exempt(bot, chat.id, user.id, st)
    if user.is_bot:
        ab = st["antibot"]
        if ab["on"] and not exempt and adder is not None and not await perms.is_exempt(bot, chat.id, adder.id, st):
            if await _auto_action(bot, chat.id, user.id, ab["action"], "bot added by a non-admin"):
                await store.log_action(chat.id, bot.id, user.id, "auto-antibot", reason=f"added by {adder.id}", source="auto")
                await _say_temp(bot, chat.id, f"🤖 Removed bot <b>{esc(user.full_name)}</b>: only admins may add bots.", 15)
        return
    if exempt:
        await greet.send(bot, chat, user, "welcome")
        return
    ar = st["antiraid"]
    if ar["on"]:
        n = _joins.hit(chat.id, now, ar["window"])
        _recent_joiners[chat.id].append((now, user.id))
        active = ar.get("until", 0) > store.now()
        if not active and n >= ar["joins"]:
            until = store.now() + ar["lockdown"]
            await store.save_settings(chat.id, lambda s: s["antiraid"].update(until=until))
            await store.log_action(chat.id, bot.id, None, "raid", reason=f"{n} joins in {ar['window']}s", duration=ar["lockdown"], source="auto")
            await _alert_raid(bot, chat.id, n, ar)
            swept = 0
            for t, uid in list(_recent_joiners[chat.id]):
                if now - t <= ar["window"] and not await perms.is_exempt(bot, chat.id, uid, st):
                    swept += 1 if await _auto_action(bot, chat.id, uid, ar["action"], "raid", ar["lockdown"] if ar["action"] == "mute" else None) else 0
            _recent_joiners[chat.id].clear()
            return
        if active:
            if await _auto_action(bot, chat.id, user.id, ar["action"], "raid mode", ar["lockdown"] if ar["action"] == "mute" else None):
                await store.log_action(chat.id, bot.id, user.id, "raid-action", reason="joined during raid mode", source="auto")
            return
    if st["captcha"]["on"]:
        if await start_captcha(bot, chat, user, st):
            return
    await greet.send(bot, chat, user, "welcome")


async def _alert_raid(bot: Bot, chat_id: int, n: int, ar: dict) -> None:
    try:
        await bot.send_message(chat_id, f"🚨 <b>Raid detected</b>: {n} joins within {ar['window']}s.\nRaid mode is ON for {fmt_duration(ar['lockdown'])}: new joiners will be {ar['action']}ed "
                                        f"and the recent ones were removed.\nAdmins: <code>/raidmode off</code> to stop · <code>/lockdown on</code> to silence the group.")
    except Exception:
        pass


async def on_leave(bot: Bot, chat, user, st: dict) -> None:
    if user.id == bot.id:
        return
    await store.set_member_status(chat.id, user.id, "left", user)
    _joined_at.pop((chat.id, user.id), None)
    cap = await store.take_captcha(chat.id, user.id)
    if cap and cap.message_id:
        await actions.delete_message(bot, chat.id, cap.message_id)
    if not user.is_bot:
        await greet.send(bot, chat, user, "goodbye")


# ------------------------------------------------------------------------------------------------------------ captcha
async def start_captcha(bot: Bot, chat, user, st: dict) -> bool:
    cfg = st["captcha"]
    try:
        await tg(bot.restrict_chat_member, chat.id, user.id, permissions=actions.perms_obj({}), use_independent_chat_permissions=True)
    except Exception:
        await _alert(bot, chat.id, "captcha-rights", "⚠️ Captcha is on but I can't restrict new members. Please give me the <b>Ban users</b> admin right.")
        return False
    if cfg["mode"] == "math":
        a, b = random.randint(2, 12), random.randint(2, 12)
        ans = a + b
        opts = list({ans, ans + random.randint(1, 3), ans - random.randint(1, 3), ans + random.randint(4, 6)})
        random.shuffle(opts)
        q = f"🧩 Solve to unlock chat: <b>{a} + {b} = ?</b>"
        kb = InlineKeyboardMarkup(inline_keyboard=[[B(text=str(o), callback_data=f"mod:cp:{chat.id}:{user.id}:{o}") for o in opts]])
        answer = str(ans)
    else:
        q, answer = "🧩 Tap the button to prove you're human.", "ok"
        kb = InlineKeyboardMarkup(inline_keyboard=[[B(text="✅ I'm human", callback_data=f"mod:cp:{chat.id}:{user.id}:ok")]])
    try:
        sent = await tg(bot.send_message, chat.id, f"👋 {mention(user.id, user.full_name)}\n{q}\n<i>You have {cfg['timeout']}s.</i>", reply_markup=kb)
    except Exception:
        return False
    await store.add_captcha(chat.id, user.id, sent.message_id, answer, cfg["timeout"])
    await store.set_member_status(chat.id, user.id, "restricted", user)
    return True


async def captcha_pass(bot: Bot, chat_id: int, user) -> None:
    restore = await actions.default_perm_dict(bot, chat_id)
    await tg(bot.restrict_chat_member, chat_id, user.id, permissions=actions.perms_obj(restore), use_independent_chat_permissions=True)
    await store.set_member_status(chat_id, user.id, "member", user)
    await store.log_action(chat_id, bot.id, user.id, "captcha-pass", source="auto")
    chat = await tg(bot.get_chat, chat_id)
    await greet.send(bot, chat, user, "welcome")


async def captcha_fail(bot: Bot, chat_id: int, uid: int, row, why: str) -> None:
    """`row` must come from store.take_captcha (atomic) so a timeout and a late click can't both act."""
    st = await store.get_settings(chat_id)
    if row.message_id:
        await actions.delete_message(bot, chat_id, row.message_id)
    fail = st["captcha"]["fail"]
    await store.log_action(chat_id, bot.id, uid, "captcha-fail", reason=why, source="auto")
    if fail in ("kick", "ban"):
        await _auto_action(bot, chat_id, uid, fail, "captcha " + why)
    # fail == "mute": they simply stay muted for an admin to deal with


@router.callback_query(F.data.startswith("mod:cp:"))
async def captcha_cb(cb: CallbackQuery, bot: Bot):
    _, _, cid, uid_s, ans = cb.data.split(":")
    chat_id, uid = int(cid), int(uid_s)
    if cb.from_user.id != uid:
        return await cb.answer("This check is for the new member only.", show_alert=True)
    row = await store.get_captcha(chat_id, uid)
    if row is None or row.expires_at <= store.now():
        return await cb.answer("This check expired.", show_alert=True)
    if ans == row.answer:
        taken = await store.take_captcha(chat_id, uid)
        if taken is None:
            return await cb.answer()
        if taken.message_id:
            await actions.delete_message(bot, chat_id, taken.message_id)
        await cb.answer("✅ Verified - welcome!")
        await captcha_pass(bot, chat_id, cb.from_user)
        return
    if await store.bump_captcha(chat_id, uid) >= 2:
        taken = await store.take_captcha(chat_id, uid)
        if taken:
            await cb.answer("❌ Wrong answer.", show_alert=True)
            await captcha_fail(bot, chat_id, uid, taken, "wrong answer")
        return
    await cb.answer("❌ Wrong - one more try.", show_alert=True)


# ------------------------------------------------------------------------------------------------------------ chat_member updates
_MAP = {"creator": "admin", "administrator": "admin", "member": "member", "restricted": "restricted", "left": "left", "kicked": "banned"}


async def process_member_update(bot: Bot, u: ChatMemberUpdated) -> None:
    if u.chat.type not in ("group", "supergroup"):
        return
    old, new = perms.status_of(u.old_chat_member), perms.status_of(u.new_chat_member)
    user = u.new_chat_member.user
    if old in ("creator", "administrator") or new in ("creator", "administrator") or user.id == bot.id:
        perms.invalidate(u.chat.id)
    if user.id == bot.id or old == new and old not in ("administrator", "restricted"):
        return
    st = await store.get_settings(u.chat.id)
    await store.set_member_status(u.chat.id, user.id, _MAP.get(new, "member"), user)
    by = u.from_user
    if old in ("left", "kicked") and new in ("member", "restricted"):
        await on_join(bot, u.chat, user, by if by and by.id != user.id else None, st)
    elif old in ("member", "restricted", "administrator", "creator") and new == "left" and (by is None or by.id == user.id):
        await on_leave(bot, u.chat, user, st)
    # actions done by humans directly in Telegram: keep the history complete (the bot's own actions are logged where they happen)
    if by is None or by.id in (bot.id, user.id):
        return
    act = None
    if new == "kicked":
        act = "ban"
    elif old == "kicked" and new in ("left", "member"):
        act = "unban"
    elif new == "left" and old in ("member", "restricted"):
        act = "kick"
    elif new == "restricted" and old == "member":
        act = "restrict"
    elif old == "restricted" and new == "member":
        act = "unrestrict"
    elif new == "administrator" and old != "administrator":
        act = "promote"
    elif old == "administrator" and new == "member":
        act = "demote"
    elif old == "administrator" and new == "administrator":
        act = "admin-rights"
    if act:
        await store.log_action(u.chat.id, by.id, user.id, act, detail="done directly in Telegram", source="telegram")
