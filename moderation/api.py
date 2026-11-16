"""Tiny read-only interface for other modules (kept separate so importing it is cheap and cannot trigger moderation actions)."""
from __future__ import annotations

from . import store
from .defaults import legacy_suppressed


async def legacy_welcome_suppressed(chat_id: int, kind: str = "join") -> bool:
    """True when this group configured its OWN welcome / goodbye text,
    so the bot's original greeting must stay quiet to avoid a double message. Never raises."""
    try:
        st = await store.get_settings(chat_id)
        return legacy_suppressed(st, kind)
    except Exception:
        return False


# ---- optional "reaction flair" (e.g. the game's kick GIF) injected by the host bot; moderation never imports game code
_flair_provider = None


def set_flair_provider(fn) -> None:
    """fn(message, flair_name) -> awaitable. Called best-effort BEFORE a moderation action; any error is ignored."""
    global _flair_provider
    _flair_provider = fn


async def flair(message, name: str) -> None:
    if _flair_provider is None:
        return
    try:
        await _flair_provider(message, name)
    except Exception:
        pass
