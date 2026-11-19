"""Broadcast ANY message (text, photo, video, sticker, voice, document, poll...) to users and/or groups.

* reply to a message with /broadcast  -> that exact message is copied (media + formatting kept)
* /broadcast <text>                   -> plain text
* scope word first: /broadcast users | groups | all  (default all)
Runs in the background, obeys Telegram flood limits (RetryAfter), marks users who blocked the bot, reports at the end.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import logging

from aiogram import Bot
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError,
                                TelegramRetryAfter)
from sqlalchemy import select

from database.models import GroupSetting
from database.session import db
from services import admin as adm, users

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()
_running = False

RATE = 25  # sustained messages/second. Telegram's own bulk-notification guideline is ~30/s globally; this leaves headroom.


async def group_ids() -> list[int]:
    async with db.tx() as s:
        return list((await s.execute(select(GroupSetting.chat_id).where(GroupSetting.removed == False))).scalars())  # noqa: E712


async def targets(scope: str) -> list[int]:
    if scope == "sync":
        return await adm.sync_targets()
    out: list[int] = []
    if scope in ("all", "users"):
        out += await adm.broadcast_targets()
    if scope in ("all", "groups"):
        out += await group_ids()
    return list(dict.fromkeys(out))


_NEVER = ("chat not found", "user not found", "peer_id_invalid", "bot can't initiate")     # Telegram: this person never opened a chat with the bot


async def _send_one(bot: Bot, chat_id: int, p: dict) -> str:
    """-> ok | blocked | deleted | never (never started the bot in DM) | fail"""
    why: Counter = p.setdefault("_why", Counter())
    for _ in range(4):
        try:
            if p.get("probe"):
                await bot.send_chat_action(chat_id, "typing")      # invisible: nothing appears in their chat
            elif p.get("src_msg"):
                await bot.copy_message(chat_id, p["src_chat"], p["src_msg"])
            else:
                await bot.send_message(chat_id, p["text"], parse_mode=None, disable_web_page_preview=True)
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError as e:
            if chat_id > 0:
                if "deactivated" in str(e).lower():        # Telegram's own wording for a deleted/deactivated account
                    await users.mark_deleted(chat_id)
                    return "deleted"
                await users.mark_blocked(chat_id)
                return "blocked"
            await users.mark_group_removed(chat_id)        # bot was kicked from / left this group
            return "blocked"
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if chat_id > 0 and any(k in msg for k in _NEVER):
                return "never"
            why[str(e)[:70]] += 1
            return "fail"
        except (TelegramNetworkError, asyncio.TimeoutError):
            await asyncio.sleep(2)
        except TelegramAPIError as e:
            why[str(e)[:70]] += 1
            log.info("broadcast to %s failed: %s", chat_id, e)
            return "fail"
    why["gave up after 4 tries (rate limit / network)"] += 1
    return "fail"


async def _run(bot: Bot, admin_id: int, p: dict) -> None:
    global _running
    try:
        ids = await targets(p.get("scope", "all"))
        c = {"ok": 0, "blocked": 0, "deleted": 0, "never": 0, "fail": 0}
        reached: list[int] = []
        never: list[int] = []
        sem = asyncio.Semaphore(RATE)

        async def worker(cid: int) -> None:
            async with sem:
                r = await _send_one(bot, cid, p)
                c[r] += 1
                if cid > 0 and r == "ok":
                    reached.append(cid)
                elif cid > 0 and r == "never":
                    never.append(cid)
                await asyncio.sleep(1)    # hold this slot for 1s -> sustained rate stays at RATE/s, not RATE all at once

        await asyncio.gather(*(worker(cid) for cid in ids))
        await users.set_dm_status(reached, True)       # a message got through -> they have started the bot (and aren't blocked)
        await users.set_dm_status(never, False)        # Telegram: no such chat -> only ever seen in groups, skipped from now on
        top = "".join(f"\n  • {n}× {w}" for w, n in p.get("_why", Counter()).most_common(3))
        head = "🔎 Sync done" if p.get("probe") else "📣 Broadcast done"
        ok = "reachable" if p.get("probe") else "delivered"
        await bot.send_message(admin_id, f"{head}: {c['ok']} {ok}, {c['blocked']} blocked/left, {c['deleted']} deleted accounts, "
                               f"{c['never']} never started the bot in DM (now skipped), {c['fail']} failed (of {len(ids)})."
                               + (f"\nWhy the failures:{top}" if top else ""), parse_mode=None)
    except Exception:
        log.exception("broadcast crashed")
        try:
            await bot.send_message(admin_id, "⚠️ Broadcast stopped because of an error (see logs).", parse_mode=None)
        except Exception:
            pass
    finally:
        _running = False


def start(bot: Bot, admin_id: int, payload: dict) -> bool:
    """False if another broadcast is still running."""
    global _running
    if _running:
        return False
    _running = True
    t = asyncio.create_task(_run(bot, admin_id, payload))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return True


def sync(bot: Bot, admin_id: int) -> bool:
    """/syncusers: quietly check everybody (an invisible 'typing' signal, no message) and record who has really started
    the bot, who blocked it, who deleted their account and who was only ever seen in a group."""
    return start(bot, admin_id, {"probe": True, "scope": "sync"})
