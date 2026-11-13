"""Casino + lucky wheel. All wallet changes go through economy.apply (atomic, ledgered).
Slots and coin flip have a house edge (RTP ~97-98%), so they are net diamond sinks over time."""
from __future__ import annotations

import random

from database.session import db
from game.rules import R
from services import cooldowns, economy, progress
from services.errors import GameError, OnCooldown

_rng = random.SystemRandom()

SYMBOLS = ["🍒", "🍋", "🍇", "🔔", "💎", "7️⃣"]
WEIGHTS = [30, 25, 20, 13, 8, 4]
TRIPLE = {"🍒": 3, "🍋": 4, "🍇": 6, "🔔": 10, "💎": 25, "7️⃣": 77}
PAIR_MULT = 1.5

WHEEL = [  # (weight, min, max, label)
    (450, 30, 100, "🪙 Small change"),
    (300, 100, 250, "💰 Nice pouch"),
    (150, 250, 500, "💎 Shiny haul"),
    (70, 500, 1000, "🌟 Lucky strike"),
    (25, 2000, 2000, "🎉 JACKPOT"),
    (5, 5000, 5000, "👑 MEGA JACKPOT"),
]


def _check_bet(bet: int) -> None:
    if bet < R.casino_min_bet:
        raise GameError(f"❌ Minimum bet is <b>{R.casino_min_bet}</b> 💎.")
    if bet > R.casino_max_bet:
        raise GameError(f"❌ Maximum bet is <b>{R.casino_max_bet:,}</b> 💎.")


async def slots(uid: int, bet: int, rng: random.Random | None = None) -> tuple[list[str], int, str]:
    """Returns (reels, payout, result label). Net change = payout - bet."""
    rng = rng or _rng
    _check_bet(bet)
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "casino", R.casino_cooldown_s)
        if rem:
            raise OnCooldown(rem, "The casino")
        reels = rng.choices(SYMBOLS, WEIGHTS, k=3)
        await economy.apply(s, uid, wallet=-bet, kind="slots_bet")
        if len(set(reels)) == 1:
            pay, label = bet * TRIPLE[reels[0]], f"🎰 JACKPOT x{TRIPLE[reels[0]]}!"
        elif len(set(reels)) == 2:
            pay, label = int(bet * PAIR_MULT), "✨ Pair! x1.5"
        else:
            pay, label = 0, "💨 No match"
        if pay:
            await economy.apply(s, uid, wallet=pay, kind="slots_win")
        return reels, pay, label


async def flip(uid: int, bet: int, side: str, rng: random.Random | None = None) -> tuple[str, bool]:
    """Returns (coin side that landed, won). 49% to win, pays 2x (RTP 98%)."""
    rng = rng or _rng
    _check_bet(bet)
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "casino", R.casino_cooldown_s)
        if rem:
            raise OnCooldown(rem, "The casino")
        won = rng.random() < 0.49
        landed = side if won else ("tails" if side == "heads" else "heads")
        await economy.apply(s, uid, wallet=-bet, kind="flip_bet")
        if won:
            await economy.apply(s, uid, wallet=bet * 2, kind="flip_win")
        return landed, won


async def spin(uid: int, rng: random.Random | None = None) -> tuple[str, int]:
    """Free lucky wheel on a cooldown. Mints diamonds like daily/work."""
    rng = rng or _rng
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "spin", R.spin_cooldown_s)
        if rem:
            raise OnCooldown(rem, "The lucky wheel")
        _, lo, hi, label = rng.choices(WHEEL, [w[0] for w in WHEEL], k=1)[0]
        amount = rng.randint(lo, hi)
        await economy.apply(s, uid, wallet=amount, kind="spin")
        await progress.emit(s, uid, "earn", amount)
        return label, amount
