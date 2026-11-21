"""🍆 /growdick game: one random grow-or-shrink every 24 h, /pvp <cm> duels, group + overall leaderboards.

Every state change happens inside ONE db transaction (cooldown claim + size update, challenge accept + both sizes),
so a crash or a double tap can never grow someone twice or pay a duel out twice."""
from __future__ import annotations

import random
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update

from database.models import DickChallenge, DickChat, DickPlayer, User, aware, utcnow
from database.session import db, insert_ignore
from services import cooldowns
from services.errors import GameError

COOLDOWN_S = 24 * 3600
COOLDOWN_KEY = "dick"
FIRST_MIN, FIRST_MAX = 1, 20          # very first /growdick: always grows
GROW_CHANCE = 0.70                    # later rolls: 70 % grow, 30 % shrink
GROW_MIN, GROW_MAX = 1, 25
SHRINK_MIN, SHRINK_MAX = 1, 5
CHALLENGE_TTL_S = 600                 # an unanswered /pvp challenge dies after 10 minutes
PAGE = 10


def roll(size: int, first: bool, rng: random.Random | None = None) -> int:
    """Signed change in cm. A shrink never takes the size below 0."""
    r = rng or random
    if first:
        return r.randint(FIRST_MIN, FIRST_MAX)
    if r.random() < GROW_CHANCE:
        return r.randint(GROW_MIN, GROW_MAX)
    return -min(size, r.randint(SHRINK_MIN, SHRINK_MAX))


def hm(seconds: int) -> str:
    """'23h 59m' (never '1d': the wait is a day at most)."""
    h, r = divmod(max(0, int(seconds)), 3600)
    m = r // 60
    return f"{h}h {m}m" if h else f"{max(1, m)}m"


def _visible():
    return (User.is_banned == False) & (User.is_flagged == False)  # noqa: E712


async def _note_chat(s, chat_id: int | None, uid: int) -> None:
    if chat_id and chat_id < 0:
        await insert_ignore(s, DickChat, dict(chat_id=chat_id, user_id=uid))


async def _rank(s, uid: int, size: int, chat_id: int | None) -> int:
    q = select(func.count()).select_from(DickPlayer).join(User, User.id == DickPlayer.user_id).where(_visible(), DickPlayer.size > size)
    if chat_id:
        q = q.join(DickChat, (DickChat.user_id == DickPlayer.user_id) & (DickChat.chat_id == chat_id))
    return (await s.execute(q)).scalar() + 1


@dataclass
class Grow:
    old: int
    new: int
    delta: int
    first: bool
    rank_group: int | None
    rank_all: int


async def grow(uid: int, chat_id: int | None) -> Grow:
    """The daily /growdick. Raises OnCooldown (with the time left) if it was already used in the last 24 h."""
    async with db.tx() as s:
        left = await cooldowns.claim(s, uid, COOLDOWN_KEY, COOLDOWN_S)
        if left:
            raise GameError(f"⏳ Your dick already changed today. Come back in <b>{hm(left)}</b>.")
        p = await s.get(DickPlayer, uid)
        first = p is None
        if first:
            await insert_ignore(s, DickPlayer, dict(user_id=uid, size=0, wins=0, losses=0, last_delta=0, created_at=utcnow()))
            p = await s.get(DickPlayer, uid)
        old = p.size
        delta = roll(old, first)
        p.size = old + delta
        p.last_delta = delta
        await _note_chat(s, chat_id, uid)
        await s.flush()
        in_group = chat_id if chat_id and chat_id < 0 else None
        return Grow(old, p.size, delta, first,
                    await _rank(s, uid, p.size, in_group) if in_group else None,
                    await _rank(s, uid, p.size, None))


async def seed_owner(owner_id: int, size: int = 500) -> bool:
    """One-time: the owner starts at `size` cm. Done once (flag in app_state, so it is in every backup); later growth is untouched.
    Waits (returns False) until the owner exists as a user - it is then retried at the next start."""
    from database.models import AppState, DickPlayer as _P
    from services import users
    key = f"dick_owner_seed:{size}"
    async with db.tx() as s:
        if await s.get(AppState, key) is not None:
            return True
        if await s.get(User, owner_id) is None:
            return False
        p = await s.get(_P, owner_id)
        if p is None:
            await insert_ignore(s, _P, dict(user_id=owner_id, size=size, wins=0, losses=0, last_delta=0, created_at=utcnow()))
        else:
            p.size = size
        await users.set_state(s, key, "1")
        return True


async def size_of(uid: int) -> int | None:
    async with db.tx() as s:
        return (await s.execute(select(DickPlayer.size).where(DickPlayer.user_id == uid))).scalar()


# ----------------------------------------------------------------------------------------------- /pvp
async def create_challenge(uid: int, chat_id: int, bet: int, target_id: int | None) -> int:
    if bet < 1:
        raise GameError("❌ The bet must be at least <b>1 cm</b>.")
    async with db.tx() as s:
        me = await s.get(DickPlayer, uid)
        if me is None or me.size < 1:
            raise GameError("🍆 Grow something first with /growdick.")
        if bet > me.size:
            raise GameError(f"❌ You only have <b>{me.size} cm</b> to bet.")
        if target_id == uid:
            raise GameError("❌ You can't fight yourself.")
        await s.execute(update(DickChallenge).where(DickChallenge.chat_id == chat_id, DickChallenge.challenger_id == uid,
                                                     DickChallenge.status == "open").values(status="cancelled"))   # one open challenge each
        ch = DickChallenge(chat_id=chat_id, challenger_id=uid, target_id=target_id, bet=bet, status="open", created_at=utcnow())
        s.add(ch)
        await s.flush()
        await _note_chat(s, chat_id, uid)
        return ch.id


@dataclass
class Fight:
    bet: int
    winner: int
    loser: int
    winner_old: int
    winner_new: int
    loser_old: int
    loser_new: int


async def accept(cid: int, acceptor: int) -> Fight:
    """Random winner takes the bet in cm from the loser. Atomic: the challenge flips open -> done exactly once."""
    async with db.tx() as s:
        ch = await s.get(DickChallenge, cid)
        if ch is None or ch.status != "open":
            raise GameError("⚔️ This challenge is already over.")
        if (utcnow() - aware(ch.created_at)).total_seconds() > CHALLENGE_TTL_S:
            raise GameError("⌛ This challenge expired. Start a new one with /pvp.")
        if acceptor == ch.challenger_id:
            raise GameError("❌ You can't accept your own challenge.")
        if ch.target_id and acceptor != ch.target_id:
            raise GameError("❌ This challenge is for someone else.")
        a, b = await s.get(DickPlayer, ch.challenger_id), await s.get(DickPlayer, acceptor)
        if b is None or b.size < 1:
            raise GameError("🍆 Grow something first with /growdick, then accept.")
        if a is None or a.size < ch.bet:
            raise GameError("❌ The challenger no longer has enough cm for this bet.")
        if b.size < ch.bet:
            raise GameError(f"❌ You need at least <b>{ch.bet} cm</b> to accept (you have {b.size} cm).")
        res = await s.execute(update(DickChallenge).where(DickChallenge.id == cid, DickChallenge.status == "open").values(status="done"))
        if res.rowcount != 1:
            raise GameError("⚔️ This challenge is already over.")
        win, lose = (a, b) if secrets.randbelow(2) == 0 else (b, a)
        wo, lo = win.size, lose.size
        win.size, lose.size = wo + ch.bet, lo - ch.bet
        win.wins += 1
        lose.losses += 1
        win.last_delta, lose.last_delta = ch.bet, -ch.bet
        await _note_chat(s, ch.chat_id, acceptor)
        return Fight(ch.bet, win.user_id, lose.user_id, wo, win.size, lo, lose.size)


async def cancel(cid: int, uid: int) -> None:
    async with db.tx() as s:
        ch = await s.get(DickChallenge, cid)
        if ch is None or ch.status != "open":
            raise GameError("⚔️ This challenge is already over.")
        if ch.challenger_id != uid:
            raise GameError("❌ Only the challenger can cancel it.")
        ch.status = "cancelled"


# ----------------------------------------------------------------------------------------------- leaderboard
async def board(chat_id: int | None, page: int) -> tuple[list[tuple[int, int, str, str | None, int]], int]:
    """([(rank, uid, name, username, cm)], total). chat_id -> that group only, None -> overall."""
    async with db.tx() as s:
        base = select(DickPlayer.user_id, User.first_name, User.username, DickPlayer.size) \
            .join(User, User.id == DickPlayer.user_id).where(_visible())
        cnt = select(func.count()).select_from(DickPlayer).join(User, User.id == DickPlayer.user_id).where(_visible())
        if chat_id:
            on = (DickChat.user_id == DickPlayer.user_id) & (DickChat.chat_id == chat_id)
            base, cnt = base.join(DickChat, on), cnt.join(DickChat, on)
        total = (await s.execute(cnt)).scalar()
        rows = (await s.execute(base.order_by(DickPlayer.size.desc(), DickPlayer.user_id).limit(PAGE).offset(page * PAGE))).all()
        return [(page * PAGE + i + 1, uid, name, un, size) for i, (uid, name, un, size) in enumerate(rows)], total


async def my_rank(uid: int, chat_id: int | None) -> tuple[int | None, int | None]:
    async with db.tx() as s:
        size = (await s.execute(select(DickPlayer.size).where(DickPlayer.user_id == uid))).scalar()
        if size is None:
            return None, None
        if chat_id and not (await s.execute(select(DickChat.user_id).where(DickChat.chat_id == chat_id, DickChat.user_id == uid))).first():
            return None, size                       # never played in this group: no rank there
        return await _rank(s, uid, size, chat_id), size
