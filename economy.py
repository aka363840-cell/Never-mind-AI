"""Diamond economy. Every balance change goes through apply() => atomic, validated, ledgered."""
from __future__ import annotations

import json
import random
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Transaction, User, Wallet, aware, utcnow
from database.session import db, insert_ignore
from game.rules import R
from services import cooldowns, progress, users
from services.errors import GameError, InsufficientFunds, OnCooldown
from utils import content

_rng = random.SystemRandom()


# ---------------------------------------------------------------- ledger core
async def apply(s: AsyncSession, uid: int, *, wallet: int = 0, bank: int = 0, kind: str,
                counterparty: int | None = None, ref: str | None = None, meta: dict | None = None) -> None:
    """Atomically change wallet/bank and write a ledger row.
    - Never goes negative (conditional UPDATE + DB CHECK constraints).
    - `ref` makes the operation idempotent: replaying it raises GameError and changes nothing."""
    inserted = await insert_ignore(s, Transaction, dict(
        user_id=uid, kind=kind, amount=wallet, bank_delta=bank, counterparty_id=counterparty,
        ref=ref, meta=json.dumps(meta) if meta else None, created_at=utcnow()))
    if not inserted:
        raise GameError("⚠️ That action was already processed.")
    res = await s.execute(
        update(Wallet).where(Wallet.user_id == uid, Wallet.balance + wallet >= 0, Wallet.bank + bank >= 0)
        .values(balance=Wallet.balance + wallet, bank=Wallet.bank + bank))
    if res.rowcount != 1:
        raise InsufficientFunds("❌ You don't have enough 💎 for that.")


async def wallet_of(s: AsyncSession, uid: int, lock: bool = False) -> Wallet:
    q = select(Wallet).where(Wallet.user_id == uid)
    if lock:
        q = q.with_for_update()
    w = (await s.execute(q)).scalar_one_or_none()
    if w is None:
        raise GameError("❌ Account not found.")
    await s.refresh(w)
    return w


async def transfer(s: AsyncSession, src: int, dst: int, amount: int, kind: str,
                   ref: str | None = None, meta: dict | None = None) -> None:
    if amount <= 0:
        raise GameError("❌ Amount must be positive.")
    await apply(s, src, wallet=-amount, kind=kind + "_out", counterparty=dst, ref=f"{ref}:out" if ref else None, meta=meta)
    await apply(s, dst, wallet=amount, kind=kind + "_in", counterparty=src, ref=f"{ref}:in" if ref else None, meta=meta)


async def transfer_upto(s: AsyncSession, src: int, dst: int, wanted: int, kind: str,
                        ref: str | None = None, meta: dict | None = None) -> int:
    """Move min(wanted, src wallet). Returns amount actually moved (never negative balances)."""
    if wanted <= 0:
        return 0
    w = await wallet_of(s, src, lock=True)
    amt = min(wanted, w.balance)
    if amt <= 0:
        return 0
    await transfer(s, src, dst, amt, kind, ref, meta)
    return amt


async def balance(uid: int) -> Wallet:
    async with db.tx() as s:
        return await wallet_of(s, uid)


# ---------------------------------------------------------------- rewards
async def claim_daily(uid: int) -> tuple[int, int, int, int]:
    """Returns (total reward, streak days, streak bonus, clan bonus). The atomic cooldown claim blocks double-claims."""
    from services import clans
    from database.models import AppState
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "daily", R.daily_cooldown_s)
        if rem:
            raise OnCooldown(rem, "Your daily reward")
        now = utcnow()
        key, streak = f"streak:{uid}", 1
        row = await s.get(AppState, key)
        if row:
            try:
                n, last = row.value.split("|")
                if (now.timestamp() - float(last)) <= R.streak_window_s:
                    streak = int(n) + 1
            except ValueError:
                pass
        await users.set_state(s, key, f"{streak}|{now.timestamp()}")
        bonus = min(streak - 1, R.streak_max_days) * R.streak_bonus
        clan_bonus = await clans.daily_bonus(s, uid)
        total = R.daily_reward + bonus + clan_bonus
        await apply(s, uid, wallet=total, kind="daily", ref=f"daily:{uid}:{now:%Y%m%d%H%M%S}")
        await progress.emit(s, uid, "daily_claim")
        await progress.emit(s, uid, "earn", total)
        return total, streak, bonus, clan_bonus


async def claim_weekly(uid: int) -> int:
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "weekly", R.weekly_cooldown_s)
        if rem:
            raise OnCooldown(rem, "Your weekly reward")
        await apply(s, uid, wallet=R.weekly_reward, kind="weekly", ref=f"weekly:{uid}:{utcnow():%Y%m%d%H%M%S}")
        await progress.emit(s, uid, "earn", R.weekly_reward)
        return R.weekly_reward


async def _jail_check(s: AsyncSession, uid: int) -> None:
    j = await cooldowns.remaining(s, uid, "jail")
    if j:
        raise GameError(f"🚔 You're in jail for another <b>{__import__('utils.fmt', fromlist=['dur']).dur(j)}</b>.")


WORK_TEXTS = ["You mined diamonds in the Ember Caves", "You delivered parcels across Crystal City",
              "You repaired airships at the docks", "You guarded a merchant caravan",
              "You brewed potions at the alchemist's", "You tutored a young mage"]
CRIME_WIN = ["You picked the pocket of a noble", "You smuggled moonstone past the guards",
             "You cracked a vault in the old bank"]
CRIME_LOSE = ["The guards caught you red-handed", "Your getaway cart broke down", "A rival crew tipped off the watch"]
WORK_TEXTS += content.WORK_MORE
CRIME_WIN += content.CRIME_WIN_MORE
CRIME_LOSE += content.CRIME_LOSE_MORE


async def work(uid: int, rng: random.Random | None = None) -> tuple[str, int]:
    rng = rng or _rng
    async with db.tx() as s:
        await _jail_check(s, uid)
        rem = await cooldowns.claim(s, uid, "work", R.work_cooldown_s)
        if rem:
            raise OnCooldown(rem, "Work")
        amount = rng.randint(R.work_min, R.work_max)
        await apply(s, uid, wallet=amount, kind="work")
        await progress.emit(s, uid, "earn", amount)
        return rng.choice(WORK_TEXTS), amount


async def crime(uid: int, rng: random.Random | None = None) -> tuple[bool, str, int]:
    rng = rng or _rng
    async with db.tx() as s:
        await _jail_check(s, uid)
        rem = await cooldowns.claim(s, uid, "crime", R.crime_cooldown_s)
        if rem:
            raise OnCooldown(rem, "Crime")
        if rng.random() < R.crime_success_chance:
            amount = rng.randint(R.crime_reward_min, R.crime_reward_max)
            await apply(s, uid, wallet=amount, kind="crime_win")
            await progress.emit(s, uid, "earn", amount)
            return True, rng.choice(CRIME_WIN), amount
        w = await wallet_of(s, uid, lock=True)
        fine = min(rng.randint(R.crime_fine_min, R.crime_fine_max), w.balance)
        if fine:
            await apply(s, uid, wallet=-fine, kind="crime_fine")   # sink: fine is burned
        await cooldowns.set_(s, uid, "jail", R.crime_jail_s)
        return False, rng.choice(CRIME_LOSE), fine


# ---------------------------------------------------------------- rob
async def rob(robber: int, target: int, amount: int, rng: random.Random | None = None) -> tuple[bool, int, int]:
    """Try to steal exactly `amount` 💎 from the target's wallet. Returns (success, amount_moved, jail_seconds).

    The amount is mandatory. It must be at least R.rob_min_steal and at most what the target's wallet allows
    (R.rob_steal_pct_max of it, capped by R.rob_max_steal). Every refusal happens BEFORE anything is charged."""
    rng = rng or _rng
    if not isinstance(amount, int) or amount < R.rob_min_steal:
        raise GameError(f"❌ The minimum amount to rob is <b>{R.rob_min_steal:,}</b> 💎.")
    async with db.tx() as s:
        if robber == target:
            raise GameError("❌ You can't rob yourself.")
        await users.lock(s, robber, target)
        await users.require(s, target)
        from services.combat import is_dead, is_protected
        await _jail_check(s, robber)
        if await is_dead(s, robber):
            raise GameError(f"💀 You're dead! Pay <b>{R.revive_cost:,}</b> 💎 with /revive before you can rob anyone.")
        if await is_dead(s, target):
            raise GameError("💀 That player is dead and can't be robbed.")
        if await is_protected(s, target):
            raise GameError("🛡 That player is protected.")
        if (r := await cooldowns.remaining(s, target, "robbed")):
            raise GameError("🛡 That player was robbed recently and is shielded for a while.")
        if (r := await cooldowns.remaining(s, robber, f"robpair:{robber}:{target}")):
            raise OnCooldown(r, "Robbing that same player")
        rem = await cooldowns.claim(s, robber, "rob", R.rob_cooldown_s)
        if rem:
            raise OnCooldown(rem, "Rob")
        wr, wt = await wallet_of(s, robber, lock=True), await wallet_of(s, target, lock=True)
        if wr.balance < R.rob_robber_min_wallet:
            raise GameError(f"❌ You need at least <b>{R.rob_robber_min_wallet}</b> 💎 in your wallet to risk a robbery.")
        if wt.balance < R.rob_target_min_wallet:
            raise GameError(f"❌ That player only has <b>{wt.balance:,}</b> 💎 in their wallet. A wallet needs at least "
                            f"<b>{R.rob_target_min_wallet:,}</b> 💎 to be worth robbing. (Bank deposits are safe.)")
        max_take = min(wt.balance, R.rob_max_steal, max(R.rob_min_steal, int(wt.balance * R.rob_steal_pct_max)))
        if amount > max_take:
            raise GameError(f"❌ You can rob at most <b>{max_take:,}</b> 💎 from that player right now "
                            f"(their wallet: <b>{wt.balance:,}</b> 💎).")
        await cooldowns.set_(s, robber, f"robpair:{robber}:{target}", R.rob_pair_cooldown_s)
        if rng.random() < R.rob_success_chance:
            await transfer(s, target, robber, amount, "rob")
            await cooldowns.set_(s, target, "robbed", R.rob_victim_immunity_s)
            await progress.emit(s, robber, "rob_success")
            await progress.emit(s, robber, "earn", amount)
            return True, amount, 0
        # caught: the fine grows with the amount you tried to take, so a greedy attempt is a bigger gamble
        fine = min(max(R.rob_fine_min, min(int(amount * R.rob_fine_ratio), R.rob_fine_max)), wr.balance)
        await transfer(s, robber, target, fine, "rob_fine")
        await cooldowns.set_(s, robber, "jail", R.rob_fail_jail_s)
        return False, fine, R.rob_fail_jail_s


# ---------------------------------------------------------------- pay / bank
async def pay(sender: int, receiver: int, amount: int, ref: str | None = None) -> None:
    if not isinstance(amount, int) or amount < R.pay_min or amount > R.pay_max:
        raise GameError(f"❌ Amount must be between {R.pay_min} and {R.pay_max:,}.")
    if sender == receiver:
        raise GameError("❌ You can't pay yourself.")
    async with db.tx() as s:
        su = await users.require(s, sender)
        await users.require(s, receiver)
        if (utcnow() - aware(su.created_at)).total_seconds() < R.pay_sender_min_age_s:
            raise GameError("🔒 New accounts can't send diamonds for the first 24 hours (anti-abuse).")
        day = utcnow() - timedelta(days=1)
        n = (await s.execute(select(func.count()).select_from(Transaction).where(
            Transaction.user_id == sender, Transaction.kind == "pay_out",
            Transaction.counterparty_id == receiver, Transaction.created_at >= day))).scalar()
        if n >= R.pay_pair_daily_count:
            raise GameError("🚫 Too many transfers to this player today.")
        sent = (await s.execute(select(func.coalesce(func.sum(-Transaction.amount), 0)).where(
            Transaction.user_id == sender, Transaction.kind == "pay_out", Transaction.created_at >= day))).scalar()
        if sent + amount > R.pay_sender_daily_cap:
            raise GameError(f"🚫 Daily transfer cap ({R.pay_sender_daily_cap:,} 💎) reached.")
        await transfer(s, sender, receiver, amount, "pay", ref=ref)


def bank_room(balance: int, bank: int) -> int:
    """How many more 💎 may be deposited: the bank may hold at most bank_max_pct of wallet + bank."""
    return max(0, int((balance + bank) * R.bank_max_pct) - bank)


async def deposit(uid: int, amount: int | None) -> int:
    """amount=None -> deposit as much as allowed. An explicit amount above the limit is refused (nothing moves)."""
    async with db.tx() as s:
        w = await wallet_of(s, uid, lock=True)
        room = bank_room(w.balance, w.bank)
        if amount is not None and amount <= 0:
            raise GameError("❌ Nothing to deposit.")
        if room <= 0:
            raise GameError(f"🏦 Your bank is full: it can hold at most <b>{int(R.bank_max_pct * 100)}%</b> of your diamonds.")
        if amount is not None and amount > w.balance:
            raise GameError("❌ You don't have enough 💎 for that.")
        if amount is not None and amount > room:
            raise GameError(f"🏦 You can only deposit <b>{room:,}</b> 💎 more (max {int(R.bank_max_pct * 100)}% of your diamonds).")
        amt = min(room, w.balance) if amount is None else amount
        if amt <= 0:
            raise GameError("❌ Nothing to deposit.")
        await apply(s, uid, wallet=-amt, bank=amt, kind="deposit")
        return amt


async def withdraw(uid: int, amount: int | None) -> int:
    async with db.tx() as s:
        w = await wallet_of(s, uid, lock=True)
        amt = w.bank if amount is None else amount
        if amt <= 0:
            raise GameError("❌ Nothing to withdraw.")
        await apply(s, uid, wallet=amt, bank=-amt, kind="withdraw")
        return amt
