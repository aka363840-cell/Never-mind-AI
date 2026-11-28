from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from database.models import User, utcnow
from keyboards.menus import back, kb
from services import seasons as S
from utils.fmt import dur, esc, num
from utils.telegram import safe_answer, safe_edit

router = Router()
_MEDAL = ("🥇", "🥈", "🥉")


async def page(uid: int, period: str = "week") -> tuple[str, InlineKeyboardMarkup]:
    now = utcnow()
    start = S.week_start(now) if period == "week" else S.month_start(now)
    prizes = S.WEEKLY_PRIZES if period == "week" else S.MONTHLY_PRIZES
    rows = await S.standings(start, None, 10)
    left = int((S.next_reset(period, now) - now).total_seconds())
    lines = [f"🏆 <b>{'Weekly' if period == 'week' else 'Monthly'} Top Earners</b>",
             f"Ends in <b>{dur(left)}</b>. Prizes: " + " · ".join(f"{_MEDAL[i]} {num(p)}💎" for i, p in enumerate(prizes)),
             "<i>Score = 💎 earned from work, hunts, dungeons, expeditions, missions, trivia, hack & heists. "
             "Gambling, /pay and market sales don't count.</i>\n"]
    if rows:
        for i, (u, n, sc) in enumerate(rows, 1):
            lines.append(f"{_MEDAL[i - 1] if i <= 3 else str(i) + '.'} {esc(n)} — <b>{num(sc)}</b>")
    else:
        lines.append("<i>No scores yet — start earning with /work, /hunt, /dungeon!</i>")
    me = await S.rank_of(uid, start)
    lines.append(f"\n📍 You: <b>#{me[0]}</b> · {num(me[1])} 💎" if me else "\n📍 You: no score yet this period")
    win = await S.last_winners(period)
    if win:
        lines.append("\n🎖 Last winners: " + ", ".join(f"{esc(n)} (+{num(p)})" for _, n, p in win))
    other = "month" if period == "week" else "week"
    rk = kb([[B(text=f"🔁 Show {other}ly", callback_data=f"season:{other}"), B(text="🔄 Refresh", callback_data=f"season:{period}")],
             back("missions")])
    return "\n".join(lines), rk


@router.message(Command("season", "weeklytop"))
async def season_cmd(m: Message, db_user: User):
    text, markup = await page(db_user.id)
    await m.reply(text, reply_markup=markup if m.chat.type == "private" else None)


@router.callback_query(F.data.startswith("season:"))
async def season_cb(cb: CallbackQuery, db_user: User):
    period = cb.data.split(":", 1)[1]
    text, markup = await page(db_user.id, "month" if period == "month" else "week")
    await safe_edit(cb, text, markup)
    await safe_answer(cb)
