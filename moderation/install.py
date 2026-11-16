"""Wires the moderation module into the Dispatcher. The only entry point the bot calls."""
from __future__ import annotations

import logging

from aiogram import F

log = logging.getLogger("moderation")


def install(dp):
    """-> (head_router, tail_router). The caller must include head_router BEFORE the game routers and tail_router just before the AI chat router."""
    from . import (base, commands_admin, commands_bulk, commands_member, commands_misc, commands_msg, commands_security,  # noqa: F401  (import = register)
                   commands_settings, confirm, dashboard, enforce, guard)               # noqa: F401
    base.set_dispatcher(dp)
    dp.message.outer_middleware(enforce.MessageEnforcer())
    dp.edited_message.outer_middleware(enforce.EditEnforcer())
    dp.chat_member.outer_middleware(enforce.MemberWatcher())
    dp.my_chat_member.outer_middleware(enforce.MemberWatcher())
    base.router.callback_query.register(confirm.handle_callback, F.data.startswith("mod:cf:"))
    # commands that only make sense in a group: answer politely in private chats (names the game also uses are left to the game)
    from aiogram.filters import Command
    own = sorted(n for n, i in base.COMMANDS.items() if not i["shared"])
    async def _private_notice(m):
        await m.reply("🛡 This is a group-moderation command - use it inside your group. <code>/modhelp</code> lists them all.")
    base.router.message.register(_private_notice, Command(*own, prefix="/!"), F.chat.type == "private")
    log.info("moderation installed: %d commands", len(base.COMMANDS))
    return base.router, base.tail_router
