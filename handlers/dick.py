"""🍆 /growdick (random grow/shrink, once per 24 h) · /pvp <cm> (random duel for cm) · /dicktop (group board + overall)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from database.models import User
from keyboards.menus import kb
from services import dick
from services.errors import GameError
from utils import paging
from utils.fmt import dur, esc
from utils.targets import resolve_target
from utils.telegram import safe_answer, safe_edit

router = Router()
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def link(uid: int, name: str, username: str | None = None) -> str:
    """Clickable name. A public t.me link always opens the profile; tg://user can be refused by privacy settings."""
    name = esc((name or "Player")[:24])
    return f'<a href="https://t.me/{username}">{name}</a>' if username else f'<a href="tg://user?id={uid}">{name}</a>'


def _in_group(m: Message) -> bool:
    return m.chat.type in ("group", "supergroup")


# ------------------------------------------------------------------------------------------------ /growdick
@router.message(Command("growdick", "dick"))
async def growdick_cmd(m: Message, db_user: User):
    g = await dick.grow(db_user.id, m.chat.id if _in_group(m) else None)
    who = link(db_user.id, db_user.first_name, db_user.username)
    if g.delta > 0:
        change = f"has grown by <b>{g.delta} cm</b> and now it is <b>{g.new} cm</b> long !!"
    elif g.delta < 0:
        change = f"has shrunk by <b>{-g.delta} cm</b> and now it is <b>{g.new} cm</b> long !!"
    else:
        change = f"has not changed, it is still <b>{g.new} cm</b> long !!"
    await m.reply(f"🍆{who} Your dick {change}\n🏆Your position in the top is <b>{g.rank_all}</b> overall.\n\n"
                  f"Next attempt in <b>{dick.hm(dick.COOLDOWN_S)}</b>.\n\n– /pvp\n– /dicktop")


# ------------------------------------------------------------------------------------------------ /pvp
def _challenge_kb(cid: int) -> InlineKeyboardMarkup:
    return kb([[B(text="⚔️ Accept", callback_data=f"dkp:a:{cid}"), B(text="✖️ Cancel", callback_data=f"dkp:c:{cid}")]])


@router.message(Command("pvp"))
async def pvp_cmd(m: Message, command: CommandObject, db_user: User):
    if not _in_group(m):
        raise GameError("⚔️ /pvp works in groups only: use it where your friends are.")
    args = (command.args or "").split()
    target, rest = await resolve_target(m, args)
    if not rest or not rest[0].isdigit() or len(rest[0]) > 6:
        mine = await dick.size_of(db_user.id)
        have = f" You have <b>{mine} cm</b>." if mine else " Grow something first with /growdick."
        raise GameError(f"⚔️ Usage: <code>/pvp 5</code> - bet 5 cm, anyone can accept; reply to someone to challenge only them.{have}")
    bet = int(rest[0])
    cid = await dick.create_challenge(db_user.id, m.chat.id, bet, target)
    who = link(db_user.id, db_user.first_name, db_user.username)
    vs = "<a href=\"tg://user?id=%d\">you</a>" % target if target else "anyone"
    await m.reply(f"⚔️ {who} bets <b>{bet} cm</b>!\nWho dares to accept? ({vs}) - random winner takes {bet} cm from the loser.\n"
                  f"<i>Expires in {dur(dick.CHALLENGE_TTL_S)}.</i>", reply_markup=_challenge_kb(cid))


async def _names(*uids: int) -> dict[int, tuple[str, str | None]]:
    from sqlalchemy import select
    from database.session import db
    async with db.tx() as s:
        rows = (await s.execute(select(User.id, User.first_name, User.username).where(User.id.in_(uids)))).all()
    return {u: (n, un) for u, n, un in rows}


@router.callback_query(F.data.startswith("dkp:"))
async def pvp_cb(cb: CallbackQuery, db_user: User):
    _, act, raw = cb.data.split(":")
    cid = int(raw)
    if act == "c":
        await dick.cancel(cid, db_user.id)
        await safe_edit(cb, "✖️ Challenge cancelled.", None)
        return await safe_answer(cb)
    f = await dick.accept(cid, db_user.id)
    nm = await _names(f.winner, f.loser)
    w, l = (link(u, *nm.get(u, ("Player", None))) for u in (f.winner, f.loser))
    await safe_edit(cb, f"⚔️ <b>Dick fight!</b>\n\n🏆 {w} wins and takes <b>{f.bet} cm</b> from {l}!\n\n"
                        f"{w}: {f.winner_old} → <b>{f.winner_new} cm</b> 📈\n{l}: {f.loser_old} → <b>{f.loser_new} cm</b> 📉", None)
    await safe_answer(cb)


# ------------------------------------------------------------------------------------------------ /dicktop
async def board_view(chat_id: int | None, page: int, uid: int, in_group: bool) -> tuple[str, InlineKeyboardMarkup | None]:
    """chat_id None = overall. In a group the two buttons switch between this group and overall."""
    _, total = await dick.board(chat_id, 0)
    page = paging.clamp(page, total, dick.PAGE)
    rows, _ = await dick.board(chat_id, page)
    title = "🌍 Overall" if chat_id is None else "👥 This group"
    lines = [f"🍆 <b>Dick leaderboard — {title}</b>\n"]
    for rank, u, name, un, size in rows:
        lines.append(f"{MEDALS.get(rank, f'{rank}.')} {link(u, name, un)} — <b>{size} cm</b>")
    if not rows:
        lines.append("Nobody yet. Be the first: /growdick")
    r, size = await dick.my_rank(uid, chat_id)
    if r:
        lines.append(f"\n📍 You: <b>#{r}</b> ({size} cm)")
    lines.append(paging.footer(page, paging.total_pages(total, dick.PAGE)))
    scope = "o" if chat_id is None else "g"
    cid = chat_id or 0
    pages = paging.total_pages(total, dick.PAGE)
    rows_kb = []
    if in_group:
        rows_kb.append([B(text=("• " if chat_id else "") + "👥 This group", callback_data=f"dk:g:{cid or 0}:0"),
                        B(text=("• " if chat_id is None else "") + "🌍 Overall", callback_data="dk:o:0:0")])
    nav = paging.nav(lambda p: f"dk:{scope}:{cid}:{p}", page, pages)
    if nav:
        rows_kb.append(nav)
    return "\n".join(lines), (kb(rows_kb) if rows_kb else None)


@router.message(Command("dicktop", "dtop"))
async def dicktop_cmd(m: Message, db_user: User):
    grp = _in_group(m)
    text, markup = await board_view(m.chat.id if grp else None, 0, db_user.id, grp)
    await m.reply(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data.startswith("dk:"))
async def dicktop_cb(cb: CallbackQuery, db_user: User):
    _, scope, chat_raw, page = cb.data.split(":")
    here = cb.message.chat.id if cb.message else 0
    in_group = here < 0
    chat_id = here if (scope == "g" and in_group) else None          # never trust a chat id from the button: use the message's own chat
    text, markup = await board_view(chat_id, max(0, int(page)), db_user.id, in_group)
    await safe_edit(cb, text, markup)
    await safe_answer(cb)
