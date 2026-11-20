"""Moderation framework: a self-contained module that runs inside the bot but shares no game / economy / AI code.

Public surface (kept deliberately tiny):
    install(dp)          -> registers routers + enforcement middleware on the Dispatcher   (called from bot.build_dispatcher)
    run(bot)             -> background loops (temp-punishment scheduler, captcha / confirmation expiry, buffers)  (bot.main)
    legacy_welcome_suppressed(chat_id, kind) -> lets the old greeting step aside when a group configured its own
Importing this package does NOT import aiogram, so the pure helpers stay unit-testable anywhere.
"""
from __future__ import annotations


def install(dp):
    from .install import install as _install
    return _install(dp)


async def run(bot):
    from .scheduler import run as _run
    await _run(bot)


def set_flair_provider(fn) -> None:
    from .api import set_flair_provider as _f
    _f(fn)


def set_greeting_gate(fn) -> None:
    from .api import set_greeting_gate as _f
    _f(fn)


async def legacy_welcome_suppressed(chat_id: int, kind: str = "join") -> bool:
    from .api import legacy_welcome_suppressed as _f
    return await _f(chat_id, kind)
