from __future__ import annotations

from sqlalchemy import func, select

from database.models import User, Wallet
from database.session import db

KINDS = {
    "rich": ("💎 Richest Players", lambda: Wallet.balance + Wallet.bank),
    "kills": ("⚔️ Most Kills", lambda: User.kills),
    "level": ("⭐ Highest Level", lambda: User.level * 1_000_000 + User.xp),
    "wins": ("🏆 Most Wins", lambda: User.wins),
}
PAGE = 10


def _visible():
    return (User.is_banned == False) & (User.is_flagged == False)  # noqa: E712


async def page(kind: str, page_no: int) -> tuple[list[tuple[int, int, str, int]], int]:
    """Returns ([(rank, uid, name, value)], total_users). Ranks computed from live DB, never cached."""
    score = KINDS[kind][1]()
    async with db.tx() as s:
        total = (await s.execute(select(func.count()).select_from(User).join(Wallet, Wallet.user_id == User.id).where(_visible()))).scalar()
        rows = (await s.execute(select(User.id, User.first_name, score.label("v")).join(Wallet, Wallet.user_id == User.id)
                                .where(_visible()).order_by(score.desc(), User.id).limit(PAGE).offset(page_no * PAGE))).all()
        out = []
        for i, (uid, name, v) in enumerate(rows):
            out.append((page_no * PAGE + i + 1, uid, name, int(v)))
        return out, total


async def rank_of(kind: str, uid: int) -> tuple[int | None, int]:
    score = KINDS[kind][1]()
    async with db.tx() as s:
        me = (await s.execute(select(score).select_from(User).join(Wallet, Wallet.user_id == User.id)
                              .where(User.id == uid, _visible()))).scalar()
        if me is None:
            return None, 0
        better = (await s.execute(select(func.count()).select_from(User).join(Wallet, Wallet.user_id == User.id)
                                  .where(_visible(), score > me))).scalar()
        return better + 1, int(me)
