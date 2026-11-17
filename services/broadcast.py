"""Broadcast ANY message (text, photo, video, sticker, voice, document, poll...) to users and/or groups.

* reply to a message with /broadcast  -> that exact message is copied (media + formatting kept)
* /broadcast <text>                   -> plain text
* scope word first: /broadcast users | groups | all  (default all)
Runs in the background, obeys Telegram flood limits (RetryAfter), marks users who blocked the bot, reports at the end.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (TelegramAPIError, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter)
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
    out: list[int] = []
    if scope in ("all", "users"):
        out += await adm.broadcast_targets()
    if scope in ("all", "groups"):
        out += await group_ids()
    return list(dict.fromkeys(out))


async def _send_one(bot: Bot, chat_id: int, p: dict) -> str:
    for _ in range(4):
        try:
            if p.get("src_msg"):
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
        except (TelegramNetworkError, asyncio.TimeoutError):
            await asyncio.sleep(2)
        except TelegramAPIError as e:
            log.info("broadcast to %s failed: %s", chat_id, e)
            return "fail"
    return "fail"


async def _run(bot: Bot, admin_id: int, p: dict) -> None:
    global _running
    try:
        ids = await targets(p.get("scope", "all"))
        c = {"ok": 0, "blocked": 0, "deleted": 0, "fail": 0}
        sem = asyncio.Semaphore(RATE)

        async def worker(cid: int) -> None:
            async with sem:
                c[await _send_one(bot, cid, p)] += 1
                await asyncio.sleep(1)    # hold this slot for 1s -> sustained rate stays at RATE/s, not RATE all at once

        await asyncio.gather(*(worker(cid) for cid in ids))
        await bot.send_message(admin_id, f"📣 Broadcast done: {c['ok']} delivered, {c['blocked']} blocked/left, "
                               f"{c['deleted']} deleted accounts, {c['fail']} failed (of {len(ids)}).", parse_mode=None)
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
