"""Emergency lockdown: silence the whole group natively (Telegram enforces it), remember the previous permissions, restore on lift / expiry."""
from __future__ import annotations

from aiogram import Bot

from . import actions, perms, store
from .defaults import SEND_FIELDS
from .perms import ModError, tg
from .util import fmt_duration


async def engage(bot: Bot, chat_id: int, duration: int | None, actor: int, source: str = "manual") -> str:
    st = await store.get_settings(chat_id)
    if st["lockdown"]["on"]:
        raise ModError("🚨 Lockdown is already active.")
    await perms.require_bot(bot, chat_id, "restrict", force=True)
    cur = await actions.default_perm_dict(bot, chat_id)
    locked = {**cur, **{f: False for f in SEND_FIELDS}}
    try:
        await tg(bot.set_chat_permissions, chat_id, actions.perms_obj(locked), use_independent_chat_permissions=True)
    except Exception as e:
        await store.log_action(chat_id, actor, None, "lockdown", result="failed", detail=str(e)[:200], source=source)
        raise ModError(perms.explain(e))
    until = store.now() + duration if duration else 0
    await store.save_settings(chat_id, lambda s: s["lockdown"].update(on=True, until=until, prev=cur))
    await store.log_action(chat_id, actor, None, "lockdown", duration=duration, source=source)
    return "🚨 <b>LOCKDOWN ON</b> - only admins can talk" + (f" for {fmt_duration(duration)}." if duration else " until an admin runs <code>/lockdown off</code>.")


async def lift(bot: Bot, chat_id: int, actor: int, source: str = "manual") -> str:
    st = await store.get_settings(chat_id)
    if not st["lockdown"]["on"]:
        raise ModError("ℹ️ There is no active lockdown.")
    prev = st["lockdown"].get("prev") or {}
    try:
        await tg(bot.set_chat_permissions, chat_id, actions.perms_obj({**{f: True for f in actions.ALL_PERM_FIELDS}, **prev}), use_independent_chat_permissions=True)
    except Exception as e:
        await store.log_action(chat_id, actor, None, "lockdown-lift", result="failed", detail=str(e)[:200], source=source)
        raise ModError(perms.explain(e))
    await store.save_settings(chat_id, lambda s: s["lockdown"].update(on=False, until=0, prev=None))
    await store.log_action(chat_id, actor, None, "lockdown-lift", source=source)
    return "✅ Lockdown lifted - the previous permissions are back."
