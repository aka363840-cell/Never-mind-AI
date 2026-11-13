from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database.models import User
from database.session import db
from handlers import views
from services import leaderboard, progress
from services.errors import GameError
from utils.telegram import safe_answer, safe_edit

router = Router()


@router.message(Command("top", "leaderboard", "lb"))
async def top(m: Message, db_user: User):
    if m.chat.type == "private":
        text, markup = await views.rankings("rich", 0, db_user.id)       # DM: one board at a time, with tab buttons
        await m.reply(text, reply_markup=markup)
    else:                                                                 # group: all boards at once, ◀️ ▶️ turn the page for everyone
        text, markup = await views.rankings_all(db_user.id, db_user.first_name)
        await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("lbg:"))
async def lbg_cb(cb: CallbackQuery):
    _, page, opener = cb.data.split(":")
    async with db.tx() as s:
        u = await s.get(User, int(opener))
    text, markup = await views.rankings_all(int(opener), u.first_name if u else "Player", int(page))
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.callback_query(F.data.startswith("lb:"))
async def lb_cb(cb: CallbackQuery, db_user: User):
    _, kind, page = cb.data.split(":")
    if kind not in leaderboard.KINDS:
        raise GameError("❌ Unknown ranking.")
    text, markup = await views.rankings(kind, max(0, int(page)), db_user.id)
    await safe_edit(cb, text, markup); await safe_answer(cb)


@router.message(Command("missions"))
async def missions_cmd(m: Message, db_user: User):
    text, markup = await views.missions(db_user.id)
    await m.reply(text, reply_markup=markup if m.chat.type == "private" else None)


@router.callback_query(F.data.startswith("ms:"))
async def ms_claim(cb: CallbackQuery, db_user: User):
    title, reward = await progress.claim_mission(db_user.id, int(cb.data.split(":")[1]))
    text, markup = await views.missions(db_user.id)
    await safe_edit(cb, text, markup)
    await safe_answer(cb, f"Mission complete! +{reward:,} 💎", alert=True)
