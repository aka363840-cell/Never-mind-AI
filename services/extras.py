"""Extra mini-games & social features: fishing, mining, trivia rewards, marriage.
Every diamond change goes through economy.apply(); cooldowns through cooldowns.claim()."""
from __future__ import annotations

import random
from datetime import datetime

from sqlalchemy import delete, or_, select

from database.models import CoupleLove, Marriage, User, aware, utcnow
from database.session import db
from services import cooldowns, economy, progress, users
from services.errors import GameError, OnCooldown
from utils import content

_rng = random.SystemRandom()

RING_PRICE = 500            # diamonds the proposer pays when the proposal is accepted (a sink)
TRIVIA_COOLDOWN_S = 90
TRIVIA_REWARD = (60, 140)

# (weight, min, max, text)
FISH_TABLE = [
    (28, 0, 0, "👢 You reeled in an old boot. Better luck next time!"),
    (34, 40, 90, "🐟 You caught a nice fish"),
    (20, 90, 160, "🐠 You caught a colourful tropical fish"),
    (11, 160, 300, "🦈 You wrestled a shark"),
    (6, 350, 650, "🐙 You landed a giant octopus"),
    (1, 1500, 2500, "🐋 You caught the DIAMOND WHALE 🏆"),
]
MINE_TABLE = [
    (25, 0, 0, "🪨 Only plain rocks this time."),
    (35, 60, 130, "⛏ You dug up some iron ore"),
    (22, 130, 240, "💎 You found a diamond vein"),
    (12, 240, 450, "💠 You struck a sapphire"),
    (5, 500, 900, "💎 You unearthed a raw diamond"),
    (1, 2000, 3500, "👑 You found the ROYAL DIAMOND 🏆"),
]


def _spread(table, extra):
    """Share each row's chance evenly between its original text and the extra variants. Reward ranges are untouched,
    so the odds of every outcome tier (and the economy) stay exactly the same -- only the wording varies."""
    out = []
    for weight, lo, hi, text in table:
        texts = [text] + extra.get(text, [])
        out += [(weight / len(texts), lo, hi, t) for t in texts]
    return out


FISH_TABLE = _spread(FISH_TABLE, content.FISH_EXTRA)
MINE_TABLE = _spread(MINE_TABLE, content.MINE_EXTRA)
GAMES = {
    "fish": ("fish", 1800, FISH_TABLE, "Fishing"),
    "mine": ("mine", 3600, MINE_TABLE, "Mining"),
}


async def gather(uid: int, game: str) -> tuple[str, int]:
    key, cd, table, label = GAMES[game]
    async with db.tx() as s:
        await economy._jail_check(s, uid)
        rem = await cooldowns.claim(s, uid, key, cd)
        if rem:
            raise OnCooldown(rem, label)
        _, lo, hi, text = _rng.choices(table, [t[0] for t in table], k=1)[0]
        amount = _rng.randint(lo, hi)
        if amount:
            await economy.apply(s, uid, wallet=amount, kind=key)
            await progress.emit(s, uid, "earn", amount)
        from services import realm          # lifetime counters -> Angler / Prospector / Whale Watcher
        await realm.bump(s, uid, game)
        if game == "fish" and text.startswith("🐋"):
            await realm.bump(s, uid, "whale")
        return text, amount


async def trivia_start(uid: int) -> None:
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "trivia", TRIVIA_COOLDOWN_S)
        if rem:
            raise OnCooldown(rem, "Trivia")


async def trivia_reward(uid: int, ref: str) -> int:
    amount = _rng.randint(*TRIVIA_REWARD)
    async with db.tx() as s:
        await economy.apply(s, uid, wallet=amount, kind="trivia", ref=ref)
        await progress.emit(s, uid, "earn", amount)
    return amount


# ---------------------------------------------------------------- marriage
async def partner_of(s, uid: int) -> Marriage | None:
    return (await s.execute(select(Marriage).where(or_(Marriage.user_a == uid, Marriage.user_b == uid)))).scalar_one_or_none()


async def can_propose(a: int, b: int) -> None:
    if a == b:
        raise GameError("❌ You can't marry yourself 😅")
    async with db.tx() as s:
        await users.require(s, a)
        await users.require(s, b)
        if await partner_of(s, a):
            raise GameError("💍 You are already married! Use /divorce first.")
        if await partner_of(s, b):
            raise GameError("💔 That person is already married.")
        from services import family
        if await family.blocked_for_marriage(s, a, b):
            raise GameError("🙅 You two are family — you can't marry each other! (/unfamily first if it was a mistake)")
        if (await economy.wallet_of(s, a)).balance < RING_PRICE:
            raise GameError(f"💍 The ring costs <b>{RING_PRICE}</b> 💎 and you don't have enough in your wallet yet.")


async def marry(a: int, b: int, ref: str) -> None:
    async with db.tx() as s:
        await users.lock(s, a, b)
        await users.require(s, a)
        await users.require(s, b)
        if await partner_of(s, a) or await partner_of(s, b):
            raise GameError("💔 One of you is already married.")
        from services import family
        if await family.blocked_for_marriage(s, a, b):
            raise GameError("🙅 You two are family — you can't marry each other!")
        if (await economy.wallet_of(s, a)).balance < RING_PRICE:      # the accepter must not see "you don't have enough"
            raise GameError(f"💔 The proposal fell through — the proposer can no longer afford the {RING_PRICE} 💎 ring.")
        await economy.apply(s, a, wallet=-RING_PRICE, kind="marry_ring", counterparty=b, ref=ref)
        s.add(Marriage(user_a=min(a, b), user_b=max(a, b), married_at=utcnow()))


async def divorce(uid: int) -> int:
    async with db.tx() as s:
        m = await partner_of(s, uid)
        if not m:
            raise GameError("You are not married.")
        other = m.user_b if m.user_a == uid else m.user_a
        await s.execute(delete(Marriage).where(Marriage.user_a == m.user_a))
        await s.execute(delete(CoupleLove).where(CoupleLove.user_a == m.user_a))
        return other


async def couple(uid: int) -> tuple[int, str, int] | None:
    """(partner id, partner name, days married) or None."""
    async with db.tx() as s:
        m = await partner_of(s, uid)
        if not m:
            return None
        other = m.user_b if m.user_a == uid else m.user_a
        name = (await s.execute(select(User.first_name).where(User.id == other))).scalar() or "Partner"
        days = (utcnow() - aware(m.married_at)).days
        return other, name, days
