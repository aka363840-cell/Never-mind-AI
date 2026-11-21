import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from database.models import AppState, GroupSetting, User
from database.session import db
from game.rules import R
from handlers import views
from keyboards.menus import back, kb
from utils import brand
from services.errors import GameError
from utils.telegram import safe_answer, safe_edit

router = Router()
log = logging.getLogger(__name__)

PAGES = {
    "main": None, "profile": views.profile, "balance": views.balance, "powers": views.powers,
    "missions": views.missions,
}


async def _render(name: str, user: User):
    if name == "modhelp":
        rows: list = []
        try:                                        # moderation is a separate optional module; never let /start break if it's missing
            from moderation.commands_misc import _help_kb
            rows = list(_help_kb().inline_keyboard)
        except Exception:
            log.exception("moderation help unavailable")
        if not rows:
            return ("🛡 <b>Moderation help</b>\nNot available right now — try <code>/modhelp</code> directly.", kb([back("main")]))
        return ("🛡 <b>Moderation help</b>\nEvery admin command, explained simply. Pick a category:", kb(rows))
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
    if name == "tools": return views.group_tools()
    if name == "ai": return views.ai_info()
    if name == "about": return await views.about()
    if name == "help": return views.command_guide()
    if name == "family": return views.family_help()
    if name == "settings": return views.settings()
    if name == "arcade":
        from handlers import arcade as arcade_page
        return await arcade_page.hub(user.id)
    if name == "academy":
        return await views.academy_help(user.id)
    raise GameError("❌ Unknown page.")


# every start page gets "📖 All commands" buttons that open the matching /help categories, so no command is out of reach
PAGE_HELP = {
    "profile": ("Basics",), "balance": ("Economy", "Property"), "powers": ("Shop",), "shop": ("Shop",),
    "missions": ("Progress",), "combat": ("Combat",), "clan": ("Clans",), "pet": ("Adventure",), "realm": ("Adventure",),
    "casino": ("Casino",), "fun": ("Social", "Games"), "family": ("Love",), "academy": ("Academy",), "ai": ("AI Chat",),
    "arcade": ("Games",),
}


async def render(name: str, user: User):
    text, markup = await _render(name, user)
    try:
        secs = views._help_sections()
        btns = []
        for key in PAGE_HELP.get(name, ()):
            for i, (title, _) in enumerate(secs):
                if key in title:
                    btns.append(B(text=f"📖 All {title.replace('🎓', '').strip()} commands", callback_data=f"help:c{i}"))
                    break
        if btns and markup is not None:
            rows = [list(r) for r in markup.inline_keyboard]
            rows[-1:-1] = [[b] for b in btns]            # just above the last (Back / Menu) row
            markup = InlineKeyboardMarkup(inline_keyboard=rows)
    except Exception:
        log.exception("could not add the help shortcut buttons")
    return text, markup


VIDEO_PATH = Path(__file__).resolve().parent.parent / "assets" / "start_intro.mp4"
_VIDEO_FID_KEY = "start_video_fid"


async def _intro_video(m: Message, caption: str | None = None, markup=None) -> bool:
    """The /start intro video. Uploaded to Telegram only ONCE ever: the resulting file_id is cached in app_state
    (-> included in every database backup) and reused for every later /start by every user - instant delivery,
    no re-upload, no quality loss. If the cached id ever goes stale, we silently re-upload and re-cache it.
    Never raises: a video problem must never block the rest of /start."""
    try:
        async with db.tx() as s:
            row = await s.get(AppState, _VIDEO_FID_KEY)
            fid = row.value if row else None
        if fid:
            try:
                await m.answer_video(fid, supports_streaming=True, caption=caption, reply_markup=markup)
                return True
            except TelegramBadRequest:
                fid = None            # stale/invalid cached id - fall through and re-upload below
        if not VIDEO_PATH.exists():
            return False
        sent = await m.answer_video(FSInputFile(VIDEO_PATH), supports_streaming=True, caption=caption, reply_markup=markup)
        new_fid = sent.video.file_id if sent.video else None
        if new_fid:
            async with db.tx() as s:
                row = await s.get(AppState, _VIDEO_FID_KEY)
                if row:
                    row.value = new_fid
                else:
                    s.add(AppState(key=_VIDEO_FID_KEY, value=new_fid))
        return True
    except Exception:
        log.exception("intro video failed - continuing without it")
        return False


@router.message(Command("rules", "rule", "guide"))
async def rules_cmd(m: Message, command: CommandObject):
    text, markup = views.rules((command.args or "").strip().lower() or None)
    await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("rules:"))
async def rules_cb(cb: CallbackQuery):
    topic = cb.data.split(":", 1)[1]
    text, markup = views.rules(None if topic == "home" else topic)
    await safe_edit(cb, text, _with_back(markup, cb))
    await safe_answer(cb)


_contact_cache: dict[int, str] = {}


async def _owner_url(bot: Bot, uid: int) -> str:
    """Prefer a public t.me/<username> link (tg://user links can be rejected by Telegram privacy settings)."""
    if uid in _contact_cache:
        return _contact_cache[uid]
    url = f"tg://user?id={uid}"
    try:
        chat = await bot.get_chat(uid)
        if chat.username:
            url = f"https://t.me/{chat.username}"
    except Exception:
        pass
    _contact_cache[uid] = url
    return url


async def _contacts(bot: Bot) -> list[tuple[str, str]]:
    from config import get_settings
    st = get_settings()
    out = []
    if st.owner_id:
        out.append(("👑 Owner", await _owner_url(bot, st.owner_id)))
    if st.developer_username:
        out.append(("💻 Developer", f"https://t.me/{st.developer_username}"))
    return out


async def _send_start(m: Message, bot: Bot, first_name: str, group: bool) -> None:
    text, markup = await views.welcome(first_name, group, None if group else await _contacts(bot))
    if not await _intro_video(m, text, markup):               # video + short caption in ONE message
        await m.answer(text, reply_markup=markup)             # no video available: plain text


@router.message(Command("start", "menu"))
async def start(m: Message, command: CommandObject, bot: Bot, db_user: User):
    group = m.chat.type != "private"
    payload = (command.args or "").strip().lower()
    if not group and payload in ("about", "help"):
        text, markup = await views.about() if payload == "about" else views.command_guide()
        await m.answer(text, reply_markup=markup)
        return
    if m.text and m.text.lstrip().lower().startswith(("/start", "/menu")) or group:
        try:
            await _send_start(m, bot, db_user.first_name, group)
            return
        except Exception:                                            # /start must never fail: fall back to the plain menu
            log.exception("welcome screen failed")
    text, markup = await views.main(db_user.first_name)
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data == "gc:home")
async def group_home(cb: CallbackQuery, bot: Bot, db_user: User):
    """Back to the start card (video + description) from any hub page in a group."""
    msg = cb.message
    try:
        await msg.delete()
    except Exception:
        pass
    await _send_start(msg, bot, db_user.first_name, True)
    await safe_answer(cb)


def _with_back(markup: InlineKeyboardMarkup | None, cb: CallbackQuery) -> InlineKeyboardMarkup | None:
    """In groups every hub page gets a Back button to the start card."""
    if cb.message is None or cb.message.chat.type == "private":
        return markup
    rows = [list(r) for r in (markup.inline_keyboard if markup else [])]
    rows.append([B(text="⬅️ Back to Start", callback_data="gc:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    await safe_edit(cb, text, _with_back(markup, cb))
    await safe_answer(cb)


@router.message(Command("about", "info"))
async def about_cmd(m: Message):
    text, markup = await views.about()
    await m.reply(text, reply_markup=markup)


@router.callback_query(F.data.startswith("nav:"))
async def nav(cb: CallbackQuery, db_user: User):
    text, markup = await render(cb.data.split(":")[1], db_user)
    await safe_edit(cb, text, _with_back(markup, cb))
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
