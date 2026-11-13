from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import CombatRecord, Duel, GroupStat, User, aware, utcnow
from database.session import db, insert_ignore
from game.engine import STRATEGIES, FightResult, resolve
from game.rules import R
from services import combat, cooldowns, economy, progress, users
from services.errors import GameError, OnCooldown

_rng = random.SystemRandom()


async def create(challenger: int, target: int, wager: int, chat_id: int) -> Duel:
    if challenger == target:
        raise GameError("❌ You can't duel yourself.")
    if not isinstance(wager, int) or wager < R.duel_min_wager or wager > R.duel_max_wager:
        raise GameError(f"❌ Wager must be between {R.duel_min_wager:,} and {R.duel_max_wager:,} 💎.")
    async with db.tx() as s:
        await users.require(s, challenger)
        await users.require(s, target)
        await combat.require_alive(s, challenger)
        if await combat.is_dead(s, target):
            raise GameError("💀 That player is dead and can't duel until they /revive.")
        day = utcnow() - timedelta(days=1)
        decided = (await s.execute(select(func.count()).select_from(CombatRecord).where(
            CombatRecord.kind == "duel", CombatRecord.winner_id.is_not(None), CombatRecord.created_at >= day,
            ((CombatRecord.attacker_id == challenger) & (CombatRecord.defender_id == target)) |
            ((CombatRecord.attacker_id == target) & (CombatRecord.defender_id == challenger))))).scalar()
        if decided >= R.duel_pair_daily_credit:      # anti alt-farming: diamonds can't be fed back and forth by duelling
            raise GameError("🚫 You two have duelled too much today. Find someone else!")
        pend = (await s.execute(select(func.count()).select_from(Duel).where(
            Duel.challenger_id == challenger, Duel.status.in_(("pending", "active")), Duel.expires_at > utcnow()))).scalar()
        if pend:
            raise GameError("⚔️ You already have an open duel.")
        if wager and (await economy.wallet_of(s, challenger)).balance < wager:
            raise GameError("❌ You can't afford that wager.")
        rem = await cooldowns.claim(s, challenger, "duel_invite", R.duel_invite_cooldown_s)
        if rem:
            raise OnCooldown(rem, "Sending duel invites")
        d = Duel(chat_id=chat_id, challenger_id=challenger, target_id=target, wager=wager,
                 expires_at=utcnow() + timedelta(seconds=R.duel_invite_ttl_s))
        s.add(d)
        await s.flush()
        return d


async def set_message(duel_id: int, message_id: int) -> None:
    async with db.tx() as s:
        await s.execute(update(Duel).where(Duel.id == duel_id).values(message_id=message_id))


async def _refund(s: AsyncSession, d: Duel) -> None:
    if d.escrowed and d.wager:
        for uid in (d.challenger_id, d.target_id):
            try:
                await economy.apply(s, uid, wallet=d.wager, kind="duel_refund", ref=f"duel:{d.id}:refund:{uid}")
            except GameError:
                pass  # already refunded (idempotent)


async def accept(duel_id: int, user_id: int) -> Duel:
    async with db.tx() as s:
        d = await s.get(Duel, duel_id)
        if d is None or d.target_id != user_id:
            raise GameError("❌ This challenge isn't for you.")
        if d.status != "pending":
            raise GameError("❌ This duel is no longer open.")
        if aware(d.expires_at) <= utcnow():
            await s.execute(update(Duel).where(Duel.id == duel_id, Duel.status == "pending").values(status="expired"))
            raise GameError("⌛ This challenge expired.")
        await combat.require_alive(s, user_id)
        if await combat.is_dead(s, d.challenger_id):
            raise GameError("💀 The challenger is dead and can't duel until they /revive.")
        r = await s.execute(update(Duel).where(Duel.id == duel_id, Duel.status == "pending")
                            .values(status="active", escrowed=bool(d.wager),
                                    expires_at=utcnow() + timedelta(seconds=R.duel_play_ttl_s)))
        if r.rowcount != 1:
            raise GameError("❌ This duel is no longer open.")
        if d.wager:   # both stakes escrowed atomically; failure rolls the status change back too
            for uid in (d.challenger_id, d.target_id):
                await economy.apply(s, uid, wallet=-d.wager, kind="duel_escrow", ref=f"duel:{duel_id}:escrow:{uid}")
        await s.refresh(d)
        return d


async def decline(duel_id: int, user_id: int) -> Duel:
    async with db.tx() as s:
        d = await s.get(Duel, duel_id)
        if d is None or user_id not in (d.target_id, d.challenger_id):
            raise GameError("❌ Not your duel.")
        r = await s.execute(update(Duel).where(Duel.id == duel_id, Duel.status == "pending").values(status="declined"))
        if r.rowcount != 1:
            raise GameError("❌ This duel is no longer pending.")
        await s.refresh(d)
        return d


@dataclass
class DuelResult:
    duel: Duel
    result: FightResult | None
    names: dict[int, str]
    winner: int | None
    pot: int
    credited: bool
    levelups: dict[int, int]


async def choose(duel_id: int, user_id: int, strat: str, rng: random.Random | None = None) -> DuelResult | None:
    """Store a private choice. Returns DuelResult when both have chosen (resolved exactly once)."""
    rng = rng or _rng
    if strat not in STRATEGIES:
        raise GameError("❌ Invalid strategy.")
    cancelled = False
    async with db.tx() as s:       # a player who got killed mid-duel cancels it: stakes go back (committed before we raise)
        d0 = await s.get(Duel, duel_id)
        if (d0 is not None and d0.status == "active" and user_id in (d0.challenger_id, d0.target_id)
                and (await combat.is_dead(s, d0.challenger_id) or await combat.is_dead(s, d0.target_id))):
            await _expire_one(s, d0)
            cancelled = True
    if cancelled:
        raise GameError("💀 A player died, so the duel was cancelled and both stakes were refunded.")
    async with db.tx() as s:
        d = await s.get(Duel, duel_id)
        if d is None or user_id not in (d.challenger_id, d.target_id):
            raise GameError("❌ You're not in this duel.")
        if d.status != "active":
            raise GameError("❌ This duel isn't active.")
        if aware(d.expires_at) <= utcnow():
            await _expire_one(s, d)
            raise GameError("⌛ The duel timed out. Stakes refunded.")
        col = Duel.challenger_choice if user_id == d.challenger_id else Duel.target_choice
        field = "challenger_choice" if user_id == d.challenger_id else "target_choice"
        r = await s.execute(update(Duel).where(Duel.id == duel_id, Duel.status == "active", col.is_(None))
                            .values(**{field: strat}))
        if r.rowcount != 1:
            raise GameError("✅ You've already locked in your strategy.")
        await s.refresh(d)
        if not (d.challenger_choice and d.target_choice):
            return None
        r = await s.execute(update(Duel).where(Duel.id == duel_id, Duel.status == "active").values(status="resolved"))
        if r.rowcount != 1:
            return None
        return await _resolve(s, d, rng)


async def _resolve(s: AsyncSession, d: Duel, rng: random.Random) -> DuelResult:
    cu, tu = await s.get(User, d.challenger_id), await s.get(User, d.target_id)
    fc, ft = await combat.load_fighter(s, cu), await combat.load_fighter(s, tu)
    for f, ch in ((fc, d.challenger_choice), (ft, d.target_choice)):
        _, am, dm = STRATEGIES[ch]
        f.atk *= am; f.dfn *= dm
    res = resolve(fc, ft, rng, a_first=rng.random() < 0.5)
    await combat.persist_consumed(s, fc); await combat.persist_consumed(s, ft)
    winner = res.winner
    pot = d.wager * 2 if d.wager else 0
    if d.wager:
        if winner is None:
            for uid in (cu.id, tu.id):
                await economy.apply(s, uid, wallet=d.wager, kind="duel_refund", ref=f"duel:{d.id}:refund:{uid}")
        else:
            await economy.apply(s, winner, wallet=pot, kind="duel_win", ref=f"duel:{d.id}:payout")
    day = utcnow() - timedelta(days=1)
    prior = (await s.execute(select(func.count()).select_from(CombatRecord).where(
        CombatRecord.kind == "duel", CombatRecord.created_at >= day,
        ((CombatRecord.attacker_id == cu.id) & (CombatRecord.defender_id == tu.id)) |
        ((CombatRecord.attacker_id == tu.id) & (CombatRecord.defender_id == cu.id))))).scalar()
    credited = prior < R.duel_pair_daily_credit
    levelups: dict[int, int] = {}
    s.add(CombatRecord(attacker_id=cu.id, defender_id=tu.id, kind="duel", winner_id=winner, reward=pot,
                       log=json.dumps(res.log[-30:]), chat_id=d.chat_id, created_at=utcnow()))
    await s.execute(update(Duel).where(Duel.id == d.id).values(winner_id=winner))
    if winner is not None and credited:
        loser = tu.id if winner == cu.id else cu.id
        await s.execute(update(User).where(User.id == winner).values(wins=User.wins + 1))
        await s.execute(update(User).where(User.id == loser).values(losses=User.losses + 1))
        levelups[winner] = await progress.add_xp(s, winner, R.xp_win)
        levelups[loser] = await progress.add_xp(s, loser, R.xp_loss)
        await insert_ignore(s, GroupStat, dict(chat_id=d.chat_id, user_id=winner, kills=0, wins=0))
        await s.execute(update(GroupStat).where(GroupStat.chat_id == d.chat_id, GroupStat.user_id == winner)
                        .values(wins=GroupStat.wins + 1))
        await progress.emit(s, winner, "duel_win")
    return DuelResult(d, res, {cu.id: cu.first_name, tu.id: tu.first_name}, winner, pot, credited, levelups)


async def _expire_one(s: AsyncSession, d: Duel) -> None:
    r = await s.execute(update(Duel).where(Duel.id == d.id, Duel.status.in_(("pending", "active"))).values(status="expired"))
    if r.rowcount == 1:
        await _refund(s, d)


async def expire_stale() -> int:
    """Run on startup and periodically: refunds escrow for duels abandoned or interrupted by a restart."""
    async with db.tx() as s:
        ids = (await s.execute(select(Duel.id).where(Duel.status.in_(("pending", "active")), Duel.expires_at <= utcnow()))).scalars().all()
    for i in ids:
        async with db.tx() as s:
            d = await s.get(Duel, i)
            await _expire_one(s, d)
    return len(ids)
