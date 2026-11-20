"""WordSeek service: the running game of each chat (memory cache + DB), guesses, wins, leaderboards."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, func, select

from database.models import User, WordGame, WordHistory, WordWin, utcnow
from database.session import db
from game import wordseek as W

IDLE_REPLACE_S = 6 * 3600          # a game nobody touched for 6 h may be replaced by /new
HISTORY = 150                      # words remembered per chat


@dataclass
class State:
    chat_id: int
    word: str
    guesses: list[list] = field(default_factory=list)      # [word, uid, name]
    started_by: int = 0
    updated: float = 0.0
    board_msg: int | None = None                            # last board message (deleted when the next one is sent)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_cache: dict[int, State | None] = {}                        # chat -> running game, or None (= checked, no game)


async def get(chat_id: int) -> State | None:
    if chat_id in _cache:
        return _cache[chat_id]
    async with db.tx() as s:
        row = await s.get(WordGame, chat_id)
    st = None
    if row is not None:
        st = State(chat_id, row.word, json.loads(row.guesses or "[]"), row.started_by, row.updated_at.timestamp() if row.updated_at else 0)
    _cache[chat_id] = st
    return st


async def _save(st: State) -> None:
    import time
    st.updated = time.time()
    async with db.tx() as s:
        row = await s.get(WordGame, st.chat_id)
        if row is None:
            s.add(WordGame(chat_id=st.chat_id, word=st.word, guesses=json.dumps(st.guesses), started_by=st.started_by))
        else:
            row.guesses, row.updated_at = json.dumps(st.guesses), utcnow()


async def start(chat_id: int, uid: int, length: int = W.DEFAULT_LEN) -> State:
    async with db.tx() as s:
        recent = set((await s.execute(select(WordHistory.word).where(WordHistory.chat_id == chat_id)
                                      .order_by(WordHistory.id.desc()).limit(HISTORY))).scalars())
    st = State(chat_id, W.pick_word(length, recent), [], uid)
    async with db.tx() as s:
        await s.execute(delete(WordGame).where(WordGame.chat_id == chat_id))
    _cache[chat_id] = st
    await _save(st)
    return st


async def finish(st: State, winner: tuple[int, str] | None = None) -> int:
    """Close the game. With a winner: store the win and return the points."""
    pts = 0
    async with db.tx() as s:
        await s.execute(delete(WordGame).where(WordGame.chat_id == st.chat_id))
        s.add(WordHistory(chat_id=st.chat_id, word=st.word))
        if winner:
            pts = W.points(len(st.guesses), len(st.word))
            s.add(WordWin(chat_id=st.chat_id, user_id=winner[0], points=pts, guesses=len(st.guesses)))
    _cache[st.chat_id] = None
    return pts


async def add_guess(st: State, word: str, uid: int, name: str) -> None:
    st.guesses.append([word.lower(), uid, name[:40]])
    await _save(st)


def idle_for(st: State) -> float:
    import time
    return time.time() - (st.updated or time.time())


def _since(period: str):
    days = {"today": 1, "week": 7, "month": 30}.get(period)
    return utcnow() - timedelta(days=days) if days else None


async def board(chat_id: int | None, period: str, limit: int = 10) -> list[tuple[int, str, int, int]]:
    """Top players: (user_id, name, points, wins). chat_id None = global."""
    q = (select(WordWin.user_id, func.coalesce(User.first_name, "Player"), func.sum(WordWin.points), func.count())
         .select_from(WordWin).join(User, User.id == WordWin.user_id, isouter=True).group_by(WordWin.user_id, User.first_name))
    if chat_id is not None:
        q = q.where(WordWin.chat_id == chat_id)
    since = _since(period)
    if since is not None:
        q = q.where(WordWin.at >= since)
    async with db.tx() as s:
        rows = (await s.execute(q.order_by(func.sum(WordWin.points).desc()).limit(limit))).all()
    return [(r[0], r[1], int(r[2] or 0), int(r[3])) for r in rows]


async def score(uid: int, chat_id: int | None, period: str) -> dict:
    q = select(func.coalesce(func.sum(WordWin.points), 0), func.count(), func.coalesce(func.avg(WordWin.guesses), 0)).where(WordWin.user_id == uid)
    if chat_id is not None:
        q = q.where(WordWin.chat_id == chat_id)
    since = _since(period)
    if since is not None:
        q = q.where(WordWin.at >= since)
    async with db.tx() as s:
        pts, wins, avg = (await s.execute(q)).one()
    rank = None
    if wins:
        top = await board(chat_id, period, limit=10_000)
        rank = next((i + 1 for i, r in enumerate(top) if r[0] == uid), None)
    return dict(points=int(pts), wins=int(wins), avg=float(avg), rank=rank)


async def totals() -> dict:
    async with db.tx() as s:
        games = (await s.execute(select(func.count()).select_from(WordGame))).scalar() or 0
        wins = (await s.execute(select(func.count()).select_from(WordWin))).scalar() or 0
        players = (await s.execute(select(func.count(func.distinct(WordWin.user_id))))).scalar() or 0
        chats = (await s.execute(select(func.count(func.distinct(WordWin.chat_id))))).scalar() or 0
        played = (await s.execute(select(func.count()).select_from(WordHistory))).scalar() or 0
    return dict(running=games, wins=wins, players=players, chats=chats, played=played)
