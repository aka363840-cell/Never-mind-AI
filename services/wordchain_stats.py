"""Word Chain stats: one row per player per finished game -> /wcstats and /wclb (any period, group or global).

The table is created on first use (CREATE TABLE IF NOT EXISTS), so installing this addon needs no migration and no
change to database/models.py or bot.py.  An aborted game (/end) is never counted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from database.models import BigPK, Base, TS, User, utcnow
from database.session import db

log = logging.getLogger(__name__)


class WordChainResult(Base):
    """One row per player per finished Word Chain game."""
    __tablename__ = "wordchain_results"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    mode: Mapped[str] = mapped_column(String(16), default="classic")
    won: Mapped[int] = mapped_column(Integer, default=0)            # 1 = this player won the game
    words: Mapped[int] = mapped_column(Integer, default=0)
    letters: Mapped[int] = mapped_column(Integer, default=0)
    longest: Mapped[str] = mapped_column(String(48), default="")    # this player's longest word in that game
    players: Mapped[int] = mapped_column(Integer, default=2)        # how many played
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)


_ready = False


async def _ensure() -> None:
    """Create the table the first time it is needed (no-op afterwards)."""
    global _ready
    if _ready:
        return
    assert db.engine is not None
    async with db.engine.begin() as c:
        await c.run_sync(lambda conn: WordChainResult.__table__.create(conn, checkfirst=True))
    _ready = True


async def record_game(g, winner) -> int:
    """Save a finished game (g = services.wordchain.Game). Returns the number of rows written."""
    if not g.log:
        return 0                                                    # nobody said a single word: not a real game
    best: dict[int, str] = {}
    for uid, w in g.log:
        if len(w) > len(best.get(uid, "")):
            best[uid] = w
    await _ensure()
    async with db.tx() as s:
        for p in g.players.values():
            s.add(WordChainResult(chat_id=g.chat_id, user_id=p.uid, mode=g.mode, won=1 if winner is not None and p.uid == winner.uid else 0,
                                  words=p.words, letters=p.letters, longest=best.get(p.uid, "")[:48], players=len(g.players)))
    return len(g.players)


def since(period: str) -> datetime | None:
    days = {"today": 1, "week": 7, "month": 30}.get(period)
    return utcnow() - timedelta(days=days) if days else None


async def board(chat_id: int | None, period: str = "all", limit: int = 10) -> list[tuple[int, str, int, int, int]]:
    """Top players: (user_id, name, wins, games, words), most wins first. chat_id None = global."""
    await _ensure()
    R = WordChainResult
    q = (select(R.user_id, func.coalesce(User.first_name, "Player"), func.sum(R.won), func.count(), func.sum(R.words))
         .select_from(R).join(User, User.id == R.user_id, isouter=True).group_by(R.user_id, User.first_name))
    if chat_id is not None:
        q = q.where(R.chat_id == chat_id)
    t = since(period)
    if t is not None:
        q = q.where(R.at >= t)
    async with db.tx() as s:
        rows = (await s.execute(q.order_by(func.sum(R.won).desc(), func.sum(R.words).desc()).limit(limit))).all()
    return [(r[0], r[1], int(r[2] or 0), int(r[3]), int(r[4] or 0)) for r in rows]


async def stats(uid: int, chat_id: int | None, period: str = "all") -> dict:
    """{games, wins, words, letters, longest, rank} for one player (rank = place on the same leaderboard, None if no games)."""
    await _ensure()
    R = WordChainResult
    q = select(func.count(), func.coalesce(func.sum(R.won), 0), func.coalesce(func.sum(R.words), 0), func.coalesce(func.sum(R.letters), 0)).where(R.user_id == uid)
    lq = select(R.longest).where(R.user_id == uid, R.longest != "")
    if chat_id is not None:
        q, lq = q.where(R.chat_id == chat_id), lq.where(R.chat_id == chat_id)
    t = since(period)
    if t is not None:
        q, lq = q.where(R.at >= t), lq.where(R.at >= t)
    async with db.tx() as s:
        games, wins, words, letters = (await s.execute(q)).one()
        longest = (await s.execute(lq.order_by(func.length(R.longest).desc(), R.longest).limit(1))).scalar() or ""
    rank = None
    if games:
        top = await board(chat_id, period, limit=10_000)
        rank = next((i + 1 for i, r in enumerate(top) if r[0] == uid), None)
    return dict(games=int(games), wins=int(wins), words=int(words), letters=int(letters), longest=longest, rank=rank)
