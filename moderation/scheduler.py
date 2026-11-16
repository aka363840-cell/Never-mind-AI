"""Background loops: temporary-punishment expiry, captcha timeouts, confirmation expiry, lockdown expiry, buffer flushing, pruning.
Every loop is independent and swallows its own errors - a failure here never touches any other part of the bot."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from . import actions, enforce, lockdown, store
from .perms import explain

log = logging.getLogger("moderation.scheduler")


async def _loop(name: str, every: float, fn) -> None:
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("moderation loop '%s' error (continuing)", name)
        await asyncio.sleep(every)


async def punish_tick(bot: Bot) -> None:
    for pid in await store.due_punishments():
        row = await store.claim_punishment(pid)            # atomic: an expiry can never run twice
        if row is None:
            continue
        try:
            res = await actions.expire_punishment(bot, row)
            await store.finish_punishment(pid, "done")
            log.info("punishment %s (%s) expired: %s", pid, row.kind, res)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            # permanent problem (bot lost rights / removed): retrying won't help - record it and tell the admins
            await store.finish_punishment(pid, "failed")
            await store.log_action(row.chat_id, 0, row.user_id, "expire-" + row.kind, result="failed", detail=str(e)[:200], source="scheduler")
            try:
                await bot.send_message(row.chat_id, f"⚠️ I couldn't lift the temporary {row.kind} of user <code>{row.user_id}</code>:\n{explain(e)}\nPlease do it by hand.")
            except Exception:
                pass
        except Exception:
            outcome = await store.release_punishment(pid)            # network / rate limit: try again later
            log.warning("punishment %s expiry deferred (%s)", pid, outcome, exc_info=True)


async def housekeeping(bot: Bot) -> None:
    await store.flush()
    for row in await store.expire_pending():
        if row.message_id:
            try:
                await bot.edit_message_text("⌛ <b>Expired.</b> Nothing was changed.", chat_id=row.chat_id, message_id=row.message_id)
            except Exception:
                pass
    for chat_id, uid in await store.expired_captchas():
        row = await store.take_captcha(chat_id, uid)       # atomic vs. a late button tap
        if row is not None:
            try:
                await enforce.captcha_fail(bot, chat_id, uid, row, "timeout")
            except Exception:
                log.exception("captcha timeout handling failed")
    now = store.now()
    for chat_id, st in list(store._settings.items()):
        ld = st.get("lockdown", {})
        if ld.get("on") and ld.get("until") and ld["until"] <= now:
            try:
                await lockdown.lift(bot, chat_id, 0, source="scheduler")
                await bot.send_message(chat_id, "✅ Lockdown time is over - the previous permissions are back.")
            except Exception:
                log.warning("could not lift expired lockdown in %s", chat_id, exc_info=True)


_tick = {"n": 0}


async def slow_tasks() -> None:
    _tick["n"] += 1
    enforce.gc()
    if _tick["n"] % 30 == 0:
        n = await store.prune()
        log.debug("pruned %s old tracked messages", n)


async def run(bot: Bot) -> None:
    try:
        rec = await store.recover()
        if rec["punishments"] or rec["interrupted"]:
            log.warning("moderation recovery: %s", rec)
        for cid in await store.all_chat_ids():             # warm the settings cache so lockdown expiry survives restarts
            await store.get_settings(cid)
    except Exception:
        log.exception("moderation startup recovery failed (continuing)")
    await asyncio.gather(_loop("punishments", 5, lambda: punish_tick(bot)), _loop("housekeeping", 15, lambda: housekeeping(bot)),
                         _loop("slow", 60, slow_tasks))
