"""Telegram game / family / fun bot — entrypoint. Run:  python bot.py"""
from __future__ import annotations

import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))     # panels may start us from another folder: .env, bot.db, backups/ live next to this file

import asyncio
import gc
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import get_settings
from database.session import db
from middlewares.core import CoreMiddleware, ErrorMiddleware
from services import announce, backup, bounty, duel, events, shop
from services.kv import init_kv
from utils import brand
from utils.commands import refresh_commands

log = logging.getLogger("bot")


def _tune_memory() -> None:
    """Small hosts (128-512 MB): stop glibc from keeping one big memory arena per thread, and collect garbage more eagerly."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.mallopt(-8, int(os.getenv("MALLOC_ARENA_MAX", "2")))      # M_ARENA_MAX
    except Exception:
        pass
    gc.set_threshold(700, 10, 5)


def _trim_memory() -> None:
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)                         # hand freed pages back to the OS
    except Exception:
        pass


async def housekeeping_loop() -> None:
    """Every 10 minutes: drop stale in-memory entries and give freed memory back to the OS."""
    import time
    from services import events
    while True:
        await asyncio.sleep(600)
        try:
            now = time.time()
            for d in (events.ACTIVE, events.LAST_EVENT):
                for k in [k for k, t in d.items() if now - t > 86400]:
                    d.pop(k, None)
            for k in [k for k, b in events.BOSSES.items() if getattr(b, "done", False) or b.ends < now - 3600]:
                events.BOSSES.pop(k, None)
            ai_svc = getattr(housekeeping_loop, "ai", None)
            locks = getattr(ai_svc, "_locks", None)
            if locks is not None and len(locks) > 200:
                for k in [k for k, lk in locks.items() if not lk.locked()]:
                    locks.pop(k, None)
            _trim_memory()
        except Exception:
            log.exception("housekeeping error")


def build_dispatcher(ai=None) -> Dispatcher:
    from handlers import admin, ai_chat, casino, clan, combat, economy, events as events_h, fun, pets as pets_h, rank, shop as shop_h, start, welcome, extras as extras_h, life as life_h, realm as realm_h, family as family_h, arcade as arcade_h, romance as romance_h
    dp = Dispatcher()
    dp.workflow_data["ai"] = ai
    for obs in (dp.message, dp.callback_query):          # ErrorMiddleware first => outermost
        obs.middleware(ErrorMiddleware())
        obs.middleware(CoreMiddleware())
    # order matters: admin & game routers first, free-form AI chat last
    for r in (admin.router, start.router, economy.router, casino.router, clan.router, pets_h.router, events_h.router, realm_h.router, fun.router, combat.router, shop_h.router, rank.router, welcome.router, extras_h.router, life_h.router, family_h.router, arcade_h.router, romance_h.router, ai_chat.router):
        dp.include_router(r)
    return dp


async def romance_loop(bot: Bot) -> None:
    from handlers.romance import romance_loop as _loop
    await _loop(bot)


async def realm_loop(bot: Bot) -> None:
    from handlers.realm import expedition_loop
    await expedition_loop(bot)


HEARTBEAT = os.getenv("HEARTBEAT_FILE", "/tmp/bot.heartbeat")


async def maintenance_loop(bot: Bot | None = None) -> None:
    """Refund expired duels / bounties. Also covers work interrupted by a restart.
    Touches a heartbeat file every cycle; the Docker HEALTHCHECK restarts a frozen bot."""
    while True:
        try:
            with open(HEARTBEAT, "w") as fh:
                fh.write(str(int(asyncio.get_running_loop().time())))
        except OSError:
            pass
        try:
            path = await backup.daily_backup(get_settings().database_url)
            if path and bot is not None:           # keep a copy OFF the server: redeploys wipe local files
                try:
                    from aiogram.types import FSInputFile
                    await bot.send_document(get_settings().owner_id, FSInputFile(path),
                                            caption=f"💾 {brand.name()} daily backup {path.name}\n{await backup.summary(path)}\nAfter a redeploy: reply to this file with /restoredb")
                except Exception:
                    log.exception("could not DM the backup to the owner")
            d, b = await duel.expire_stale(), await bounty.expire_stale()
            if d or b:
                log.info("expired duels=%s bounties=%s", d, b)
        except Exception:
            log.exception("maintenance loop error")
        await asyncio.sleep(60)


async def brand_loop(bot: Bot) -> None:
    """Follow the bot's name in BotFather: rename it there and every screen / menu / AI persona follows within minutes."""
    from services import users
    while True:
        await asyncio.sleep(300)
        try:
            if brand.update(await bot.get_me()):
                log.info("Bot renamed to %r - refreshing menus", brand.name())
                async with db.tx() as s:
                    await users.set_state(s, "brand:name", brand.name())
                await refresh_commands(bot)
        except Exception:
            log.exception("brand refresh failed")


async def main() -> None:
    st = get_settings()
    logging.basicConfig(level=st.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not st.bot_token or not st.owner_id:
        raise SystemExit("BOT_TOKEN and OWNER_ID must be set (see .env.example)")
    db.init(st.database_url)
    await db.create_all()          # idempotent: only creates tables that don't exist yet (clans, pets, ...)
    init_kv(st.redis_url)
    from keyboards import style as btn_style
    try:
        await btn_style.load_icons()
    except Exception:
        log.exception("could not load premium button icons")
    await shop.seed_catalog()

    ai = provider = None
    try:
        from ai.service import ChatService, build_provider
        provider = build_provider(st)
        ai = ChatService(provider, context_messages=st.ai_context_messages, ttl_min=st.ai_conversation_ttl_min,
                         user_rpm=st.ai_user_rpm, user_daily=st.ai_user_daily, global_rpm=st.ai_global_rpm * max(1, getattr(provider, "key_count", len(st.gemini_api_keys))),
                         max_input=st.ai_max_input_chars)
    except ValueError as e:
        log.warning("AI disabled: %s (games still work)", e)

    housekeeping_loop.ai = ai

    async def _ai_selftest() -> None:                  # runs in the background: the bot answers commands immediately
        try:
            ok, why = await asyncio.wait_for(ai.diagnose(), 45)
            (log.info if ok else log.error)("AI self-test: %s", why)
        except Exception as e:
            log.error("AI self-test crashed: %s", e)

    bot = Bot(st.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(btn_style.ButtonStyleMiddleware())     # colours + premium icons on every inline keyboard
    try:
        await btn_style.load_packs(bot)                           # default premium emoji packs
    except Exception:
        log.exception("could not load default emoji packs")
    dp = build_dispatcher(ai)
    try:                                                          # a leftover webhook makes polling fail with a 'conflict' error
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        log.exception("could not clear the webhook (continuing)")
    brand.update(await bot.get_me())                              # live display name, used everywhere
    tasks = [asyncio.create_task(brand_loop(bot)), asyncio.create_task(maintenance_loop(bot)), asyncio.create_task(events.events_loop(bot)),
             asyncio.create_task(announce.levelup_loop(bot)),
             asyncio.create_task(realm_loop(bot)), asyncio.create_task(housekeeping_loop()),
             asyncio.create_task(romance_loop(bot))]
    if ai is not None:
        tasks.append(asyncio.create_task(_ai_selftest()))
    try:
        from services import users
        async with db.tx() as s:                                  # remembered in the DB -> part of every backup
            await users.set_state(s, "brand:name", brand.name())
        await refresh_commands(bot)
        me = await bot.me()
        log.info("Started as %s (@%s)", brand.name(), me.username)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for t in tasks:
            t.cancel()
        if provider:
            await provider.close()
        from utils import gifs
        await gifs.close()
        await bot.session.close()
        await db.dispose()


if __name__ == "__main__":
    _tune_memory()
    asyncio.run(main())
