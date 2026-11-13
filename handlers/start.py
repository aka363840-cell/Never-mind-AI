import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from database.models import AppState, GroupSetting, User
from database.session import db
from game.rules import R
from handlers import views
from keyboards.menus import kb
from utils import brand
from services.errors import GameError
from utils.telegram import safe_answer, safe_edit

router = Router()
log = logging.getLogger(__name__)

PAGES = {
    "main": None, "profile": views.profile, "balance": views.balance, "powers": views.powers,
    "missions": views.missions,
}


async def render(name: str, user: User):
    if name == "main":
        return await views.main(user.first_name)
    if name in ("profile", "balance", "powers", "missions"):
        return await PAGES[name](user.id)
    if name == "combat": return views.combat_help()
    if name == "shop": return views.shop_home()
    if name == "clan": return views.clan_help()
    if name == "pet": return views.pet_help()
    if name == "realm": return views.realm_help()
    if name == "casino": return views.casino_help()
    if name == "fun": return views.fun_help()
    if name == "ai": return views.ai_info()
    if name == "about": return await views.about()
    if name == "help": return views.command_guide()
    if name == "family": return views.family_help()
    if name == "settings": return views.settings()
    if name == "arcade":
        from handlers import arcade as arcade_page
        return await arcade_page.hub(user.id)
    raise GameError("❌ Unknown page.")


async def _first_time(uid: int) -> bool:
    """True exactly once per user (flag lives in app_state -> included in every database backup)."""
    async with db.tx() as s:
        key = f"welcomed:{uid}"
        if await s.get(AppState, key):
            return False
        s.add(AppState(key=key, value="1"))
        return True


@router.message(Command("rules", "rule", "guide"))
async def rules_cmd(m: Message, command: CommandObject):
    text, markup = views.rules((command.args or "").strip().lower() or None)
    await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("rules:"))
async def rules_cb(cb: CallbackQuery):
    topic = cb.data.split(":", 1)[1]
    text, markup = views.rules(None if topic == "home" else topic)
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.message(Command("start", "menu"))
async def start(m: Message, command: CommandObject, bot: Bot, db_user: User):
    if m.chat.type != "private":
        await m.reply(f"{brand.line()}\nUse commands here (/daily /kill /duel /family /hug …) or open the full menu in private 👇",
                      reply_markup=kb([[B(text=f"Open {brand.name()} menu", url=brand.link("start=menu"))]]))
        return
    payload = (command.args or "").strip().lower()
    if payload in ("about", "help"):
        text, markup = await views.about() if payload == "about" else views.command_guide()
        await m.answer(text, reply_markup=markup)
        return
    if m.text and m.text.lstrip().lower().startswith("/start") and await _first_time(db_user.id):
        try:
            text, markup = await views.welcome(db_user.first_name)   # brand-new user: greeting + about + quick start
            await m.answer(text, reply_markup=markup)
            return
        except Exception:                                            # /start must never fail: fall back to the plain menu
            log.exception("welcome screen failed")
    text, markup = await views.main(db_user.first_name)
    await m.answer(text, reply_markup=markup)


@router.message(Command("help"))
async def help_cmd(m: Message):
    text, markup = views.command_guide(compact=m.chat.type != "private")
    await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.regexp(r"^help:(home|\d+|c\d+(:\d+)?)$"))
async def help_page(cb: CallbackQuery):
    in_group = cb.message is not None and cb.message.chat.type != "private"
    part = cb.data.split(":")[1:]                      # help:home | help:c3 | help:c3:1 | help:2 (old A-Z message -> home)
    cat = page = None
    if part[0].startswith("c"):
        cat, page = int(part[0][1:]), int(part[1]) if len(part) > 1 else 0
    text, markup = views.command_guide(cat, compact=in_group, page=page or 0)
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.message(Command("about", "info"))
async def about_cmd(m: Message):
    text, markup = await views.about()
    await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("nav:"))
async def nav(cb: CallbackQuery, db_user: User):
    text, markup = await render(cb.data.split(":")[1], db_user)
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.message(Command("profile", "me"))
async def profile_cmd(m: Message, db_user: User):
    text, markup = await views.profile(db_user.id)
    await m.reply(text, reply_markup=markup if m.chat.type == "private" else None)


# ------------------------------------------------------------ group settings (Telegram group admins)
async def _is_group_admin(bot: Bot, chat_id: int, uid: int) -> bool:
    mem = await bot.get_chat_member(chat_id, uid)
    return mem.status in ("administrator", "creator")


def _gs_kb(g: GroupSetting, welcome: bool = True) -> InlineKeyboardMarkup:
    # welcome / leave messages are always on, so there is no button for them
    return kb([[B(text=f"🤖 AI replies: {'ON' if g.ai_enabled else 'OFF'}", callback_data="gs:ai")],
               [B(text=f"🎮 Game commands: {'ON' if g.games_enabled else 'OFF'}", callback_data="gs:games")]])


async def _welcome_flag(chat_id: int) -> bool:
    from handlers.welcome import welcome_on
    return await welcome_on(chat_id)


@router.message(Command("settings"), F.chat.type.in_({"group", "supergroup"}))
async def group_settings(m: Message, bot: Bot, group: GroupSetting):
    if not await _is_group_admin(bot, m.chat.id, m.from_user.id):
        raise GameError("⛔ Only group admins can change settings.")
    await m.reply("⚙️ <b>Group settings</b>", reply_markup=_gs_kb(group))


@router.callback_query(F.data.startswith("gs:"))
async def group_toggle(cb: CallbackQuery, bot: Bot):
    if cb.message.chat.type not in ("group", "supergroup") or not await _is_group_admin(bot, cb.message.chat.id, cb.from_user.id):
        raise GameError("⛔ Group admins only.")
    chat_id = cb.message.chat.id
    if cb.data == "gs:welcome":                      # old settings message still showing the removed button
        await safe_answer(cb, "👋 Welcome messages are always on.")
        return
    async with db.tx() as s:
        g = await s.get(GroupSetting, chat_id)
        field = "ai_enabled" if cb.data == "gs:ai" else "games_enabled"
        setattr(g, field, not getattr(g, field))
        await s.flush()
        markup = _gs_kb(g)
    await safe_edit(cb, "⚙️ <b>Group settings</b>", markup)
    await safe_answer(cb, "Saved")
