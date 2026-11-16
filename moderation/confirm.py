"""YES / NO confirmation for serious actions + the safe bulk runner.

Flow: request() stores a pending row (DB) and shows the YES/NO message -> on YES the callback atomically claims the row
(pending -> running, exactly once, only before it expires, only for the original issuer), re-checks permissions, runs the
executor with live progress and edits the message into a final summary. NO cancels. Expired rows are closed by the scheduler.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from . import perms, store
from .perms import ModError, explain
from .util import esc, fmt_duration

log = logging.getLogger("moderation.confirm")
CONFIRM_TTL = 90
THROTTLE = 0.12            # seconds between targets: ~8 Telegram admin calls per second, well under the limits
PROGRESS_EVERY = 2.5       # seconds between progress edits (Telegram edit limits)


@dataclass
class Spec:
    """How one confirmable action is authorised and executed."""
    actor_right: str | None                              # admin right the ISSUER needs (re-checked on YES)
    bot_right: str | None                                # admin right the BOT needs (re-checked on YES)
    run: Callable[["Ctx"], Awaitable[str]]               # executes; returns the final HTML summary
    bulk: bool = False
    any_member: bool = False                             # True: any group member may confirm (e.g. /banme on themselves)


@dataclass
class Ctx:
    bot: Bot
    chat_id: int
    issuer: int
    action: str
    payload: dict
    token: str
    message: Message | None = None
    progress: Callable[[int, int, int, int, int], Awaitable[None]] | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)


EXECUTORS: dict[str, Spec] = {}
_running_chats: set[int] = set()                         # one bulk operation per group at a time
_stops: dict[str, asyncio.Event] = {}


def register(action: str, spec: Spec) -> None:
    EXECUTORS[action] = spec


def _kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="✅ YES", callback_data=f"mod:cf:y:{token}"), B(text="❌ NO", callback_data=f"mod:cf:n:{token}")]])


async def request(m: Message, bot: Bot, action: str, payload: dict, *, title: str, lines: list[str], warning: str = "", issuer=None) -> None:
    """Show the confirmation prompt. Nothing is executed here."""
    if action not in EXECUTORS:
        raise ModError("⚠️ Internal error: unknown action.")
    if EXECUTORS[action].bulk and (m.chat.id in _running_chats or await store.running_pending(m.chat.id)):
        raise ModError("⏳ Another bulk operation is already running in this group. Wait for it to finish.")
    issuer = issuer or m.from_user
    token = await store.create_pending(m.chat.id, issuer.id, action, payload, CONFIRM_TTL)
    body = f"⚠️ <b>{esc(title)}</b>\n\n" + "\n".join(lines)
    if warning:
        body += f"\n\n❗ {warning}"
    body += f"\n\n<i>Only {esc(issuer.full_name)} can confirm · expires in {CONFIRM_TTL}s</i>"
    sent = await m.reply(body, reply_markup=_kb(token), disable_web_page_preview=True)
    await store.set_pending_message(token, sent.message_id)


async def _edit(msg: Message | None, text: str, kb=None) -> None:
    if msg is None:
        return
    try:
        await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest:
        pass
    except Exception:
        log.debug("edit failed", exc_info=True)


async def handle_callback(cb, bot: Bot) -> None:
    parts = (cb.data or "").split(":")
    if len(parts) != 4:
        return await cb.answer("Invalid button.", show_alert=True)
    _, _, verb, token = parts
    row = await store.get_pending(token)
    if row is None:
        return await cb.answer("This request is invalid or too old.", show_alert=True)
    if verb == "s":                                                      # Stop button during a bulk run
        if cb.from_user.id != row.issuer_id:
            return await cb.answer("Only the person who started this can stop it.", show_alert=True)
        ev = _stops.get(token)
        if ev:
            ev.set()
        return await cb.answer("Stopping after the current user…")
    if cb.from_user.id != row.issuer_id:
        return await cb.answer("⛔ Only the admin who ran the command can answer this.", show_alert=True)
    msg = cb.message if isinstance(cb.message, Message) else None
    if verb == "n":
        if await store.claim_pending(token, "cancelled"):
            await _edit(msg, "❌ <b>Cancelled.</b> Nothing was changed.")
            return await cb.answer("Cancelled.")
        return await cb.answer(_gone_text(row), show_alert=True)
    if verb != "y":
        return await cb.answer("Invalid button.", show_alert=True)
    spec = EXECUTORS.get(row.action)
    if spec is None:
        await store.claim_pending(token, "cancelled")
        return await cb.answer("This action is no longer available.", show_alert=True)
    # 1) re-check permissions BEFORE claiming, so a lost right doesn't burn the request silently
    try:
        if not spec.any_member:
            await perms.require_actor(bot, row.chat_id, cb.from_user.id, spec.actor_right, fresh=True)
        await perms.require_bot(bot, row.chat_id, spec.bot_right, force=True)
    except ModError as e:
        return await cb.answer(_strip(str(e)), show_alert=True)
    except Exception as e:
        return await cb.answer(_strip(explain(e)), show_alert=True)
    # 2) atomic claim: the ONLY way to run. A double click / second admin finds it already taken.
    if not await store.claim_pending(token, "running"):
        return await cb.answer(_gone_text(await store.get_pending(token) or row), show_alert=True)
    if spec.bulk:
        if row.chat_id in _running_chats:
            await store.finish_pending(token, "cancelled", "another bulk operation was running")
            await _edit(msg, "⏳ Another bulk operation is running in this group. Cancelled - try again later.")
            return await cb.answer()
        _running_chats.add(row.chat_id)
    await cb.answer("Working…")
    ctx = Ctx(bot=bot, chat_id=row.chat_id, issuer=row.issuer_id, action=row.action, payload=json.loads(row.payload or "{}"), token=token, message=msg)
    _stops[token] = ctx.stop
    try:
        await _edit(msg, "⏳ <b>Working…</b>", _stop_kb(token) if spec.bulk else None)
        summary = await spec.run(ctx)
        await store.finish_pending(token, "done", summary)
        await _edit(msg, summary)
    except ModError as e:
        await store.finish_pending(token, "done", str(e))
        await _edit(msg, str(e))
    except Exception as e:
        log.exception("confirmed action %s crashed", row.action)
        await store.finish_pending(token, "done", "crashed")
        await _edit(msg, "⚠️ Something went wrong while executing. Some steps may have completed - check /modlog.\n" + explain(e))
    finally:
        _stops.pop(token, None)
        _running_chats.discard(row.chat_id)


def _stop_kb(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="🛑 Stop", callback_data=f"mod:cf:s:{token}")]])


def _gone_text(row) -> str:
    return {"expired": "⌛ This request expired.", "running": "Already running.", "done": "Already executed.",
            "cancelled": "This request was cancelled.", "interrupted": "Interrupted by a restart; nothing re-ran."}.get(row.status, "This request is no longer valid.")


def _strip(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html)[:190]


# ---------------------------------------------------------------------------------------------------------- bulk runner
@dataclass
class Stats:
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    stopped: bool = False

    def note(self, key: str) -> None:
        self.reasons[key] = self.reasons.get(key, 0) + 1


async def run_bulk(ctx: Ctx, targets: list[int], op: Callable[[int], Awaitable[None]], *, st: dict, verb: str,
                   skip_protected: bool = True) -> Stats:
    """Sequentially apply op(uid) to every target.
    Per target: re-validate (never touch the owner / the bot / admins / protected ids), call Telegram, count ok / failed / skipped.
    One failure never aborts the run. Rate limits are waited out inside perms.tg()."""
    s = Stats()
    total, last_edit, t0 = len(targets), 0.0, time.monotonic()
    for i, uid in enumerate(targets, 1):
        if ctx.stop.is_set():
            s.stopped = True
            break
        try:
            if skip_protected:
                why, _ = await perms.protection_reason(ctx.bot, ctx.chat_id, uid, st)
                if why:
                    s.skipped += 1; s.note(f"protected: {why}")
                    continue
            await op(uid)
            s.ok += 1
        except ModError as e:
            r = _strip(str(e))
            if r.startswith("ℹ️") or "isn't" in r or "not in the group" in r:
                s.skipped += 1; s.note(r[:60])
            else:
                s.failed += 1; s.note(r[:60])
        except Exception as e:
            s.failed += 1; s.note(_strip(explain(e))[:60])
        await asyncio.sleep(THROTTLE)
        if time.monotonic() - last_edit >= PROGRESS_EVERY:
            last_edit = time.monotonic()
            await _edit(ctx.message, progress_text(verb, i, total, s), _stop_kb(ctx.token))
    s.reasons["_secs"] = int(time.monotonic() - t0)
    return s


def progress_text(verb: str, i: int, total: int, s: Stats) -> str:
    pct = int(i * 100 / total) if total else 100
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    return f"⏳ <b>{esc(verb)}…</b>\n<code>{bar}</code> {pct}%  ({i}/{total})\n✅ {s.ok}   ⚠️ {s.failed}   ⏭ {s.skipped}"


def summary_text(verb: str, total: int, s: Stats, *, note: str = "") -> str:
    secs = s.reasons.get("_secs", 0)
    out = [f"{'🛑 <b>Stopped</b>' if s.stopped else '✅ <b>Finished</b>'}: {esc(verb)}",
           f"Targets considered: <b>{total}</b>  ·  ✅ done <b>{s.ok}</b>  ·  ⚠️ failed <b>{s.failed}</b>  ·  ⏭ skipped <b>{s.skipped}</b>",
           f"Time: {fmt_duration(secs) if secs else '<1s'}"]
    why = sorted(((k, v) for k, v in s.reasons.items() if not k.startswith("_")), key=lambda kv: -kv[1])[:5]
    if why:
        out.append("\n<b>Why some were not done:</b>\n" + "\n".join(f"• {esc(k)}: {v}" for k, v in why))
    if note:
        out.append("\n" + note)
    return "\n".join(out)
