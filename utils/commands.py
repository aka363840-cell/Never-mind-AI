"""Telegram '/' command menus.

* One short, curated menu of useful commands for EVERYONE, in DMs and in groups (groups also get /settings).
* Owner / moderator commands are never listed (they still work for the owner). The full A-Z list is /help.
* The descriptions live in utils/helpcatalog.py.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import (
    BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeChat,
    BotCommandScopeChatMember, BotCommandScopeDefault,
)

from config import get_settings
from services import admin as adm
from utils import brand, helpcatalog

log = logging.getLogger(__name__)


def public(group: bool = False) -> list[tuple[str, str]]:
    return helpcatalog.menu(brand.name(), brand.EMOJI, group)


def _cmds(*lists: list[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(command=c, description=d) for lst in lists for c, d in lst][:100]   # Telegram allows at most 100 per menu


def menu_for(role: str, group: bool) -> list[BotCommand]:
    """Same menu for everybody (the owner and moderators included): staff commands are not advertised."""
    return _cmds(public(group))


async def setup_commands(bot: Bot) -> None:
    """Publish the menus (DMs + groups) and overwrite any older owner-only menu. Never crashes startup."""
    st = get_settings()
    try:
        await bot.set_my_commands(_cmds(public()), scope=BotCommandScopeDefault())
        await bot.set_my_commands(_cmds(public()), scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands(_cmds(public(group=True)), scope=BotCommandScopeAllGroupChats())
    except Exception:
        log.exception("could not set public command menus")
    for uid in {st.owner_id}:
        await ensure_scope(bot, uid, uid, group=False)


_done: set[tuple[int, int]] = set()


async def ensure_scope(bot: Bot, chat_id: int, uid: int, group: bool) -> None:
    """Replace an older, extended owner / mod menu in this chat with the normal one (once per process)."""
    role = adm.role(uid)
    if role == "none" or (chat_id, uid) in _done:
        return
    _done.add((chat_id, uid))
    scope = BotCommandScopeChatMember(chat_id=chat_id, user_id=uid) if group else BotCommandScopeChat(chat_id=chat_id)
    try:
        await bot.set_my_commands(menu_for(role, group), scope=scope)
    except Exception as e:   # e.g. owner never opened the bot yet -> retried when they first write
        _done.discard((chat_id, uid))
        log.info("command menu for %s in %s not set yet: %s", uid, chat_id, e)


async def refresh_commands(bot: Bot) -> None:
    """Re-publish every menu + the bot's 'About' texts (called at boot and whenever the bot is renamed in BotFather)."""
    _done.clear()
    await setup_commands(bot)
    n = brand.name()
    try:
        await bot.set_my_short_description(f"{brand.EMOJI} {n} — AI chat, economy, duels, dungeons, pets, clans, family & fun!"[:120])
        await bot.set_my_description(
            f"👋 Hi, I'm {n}! {brand.EMOJI}\n\n💎 Earn diamonds · ⚔️ duel friends · 🗝 dungeons · 🐾 pets · 🏰 clans\n"
            "💍 marriage & 👨‍👩‍👧‍👦 family tree · 🎭 fun actions with GIFs · 🤖 AI chat in groups, replies in your language\n\nPress START to begin!")
    except Exception:
        log.exception("could not set the bot description")
