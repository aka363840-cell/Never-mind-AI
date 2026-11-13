from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.models import User
from services import admin as adm, events
from services.errors import GameError
from utils.fmt import esc, num
from utils.telegram import safe_answer, safe_edit

router = Router()


@router.message(Command("drop", "boss"), F.chat.type.in_({"group", "supergroup"}))
async def force_event(m: Message, bot: Bot):
    """Owner / mods can trigger an event right now in the current group."""
    if adm.role(m.from_user.id) == "none":
        return
    if (m.text or "").lower().startswith("/drop"):
        await events.spawn_drop(bot, m.chat.id)
    else:
        await events.spawn_boss(bot, m.chat.id)


@router.callback_query(F.data.startswith("drop:"))
async def drop_cb(cb: CallbackQuery, db_user: User):
    reward = await events.claim_drop(cb.data.split(":", 1)[1], cb.from_user.id)
    await safe_edit(cb, f"🎉 <b>{esc(cb.from_user.full_name)}</b> grabbed the crate and won <b>{num(reward)}</b> 💎!")
    await safe_answer(cb, f"You won {num(reward)} 💎!", alert=True)


@router.callback_query(F.data.startswith("boss:"))
async def boss_cb(cb: CallbackQuery, bot: Bot, db_user: User):
    dmg, _ = await events.hit_boss(bot, cb.data.split(":", 1)[1], cb.from_user.id, cb.from_user.first_name, db_user.level)
    await safe_answer(cb, f"⚔️ You hit for {dmg}!")
