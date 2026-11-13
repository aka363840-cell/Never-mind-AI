"""PvP: /kill, /revenge, /protect. All outcomes computed server-side inside ONE transaction."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (Pet, CombatRecord, GroupStat, InventoryItem, Protection, ShopItem, User, UserPower,
                             UserStat, aware, utcnow)
from database.session import db, insert_ignore
from game.engine import Fighter, FightResult, resolve
from game.rules import R, pet_stats
from services import cooldowns, economy, bounty, progress, users
from services.errors import GameError, OnCooldown

_rng = random.SystemRandom()


# ------------------------------------------------------------ dead / revive
# The dead flag lives in the existing `user_stats` table (key "dead", 1 = dead): no schema change, so
# existing databases and backups keep working untouched.
DEAD_KEY = "dead"


async def is_dead(s: AsyncSession, uid: int) -> bool:
    return bool((await s.execute(select(UserStat.value).where(
        UserStat.user_id == uid, UserStat.key == DEAD_KEY))).scalar() or 0)


async def _set_dead(s: AsyncSession, uid: int) -> None:
    res = await s.execute(update(UserStat).where(UserStat.user_id == uid, UserStat.key == DEAD_KEY).values(value=1))
    if res.rowcount == 0 and not await insert_ignore(s, UserStat, dict(user_id=uid, key=DEAD_KEY, value=1)):
        await s.execute(update(UserStat).where(UserStat.user_id == uid, UserStat.key == DEAD_KEY).values(value=1))


async def revive(uid: int) -> int:
    """Pay R.revive_cost 💎 to come back to life. Returns the diamonds paid.
    One transaction: if the player can't afford it, they stay dead and nothing is charged."""
    async with db.tx() as s:
        await users.require(s, uid)
        if not await is_dead(s, uid):
            raise GameError("😇 You're not dead.")
        w = await economy.wallet_of(s, uid)
        if w.balance < R.revive_cost:
            need = R.revive_cost - w.balance
            if w.bank >= need:
                hint = " Use /withdraw to take diamonds out of your bank first."
            else:
                hint = " You stay dead and nothing was charged. Earn more with /daily, /work or /crime (the dead can still use them), then /revive."
            raise GameError(f"❌ Reviving costs <b>{R.revive_cost:,}</b> 💎 from your wallet; you have <b>{w.balance:,}</b>.{hint}")
        res = await s.execute(update(UserStat).where(UserStat.user_id == uid, UserStat.key == DEAD_KEY,
                                                     UserStat.value > 0).values(value=0))
        if res.rowcount != 1:
            raise GameError("😇 You're not dead.")
        if R.revive_cost:
            await economy.apply(s, uid, wallet=-R.revive_cost, kind="revive")   # sink
        await cooldowns.set_(s, uid, "immune", R.kill_victim_immunity_s)        # brief respawn immunity
        return R.revive_cost


async def require_alive(s: AsyncSession, uid: int) -> None:
    if await is_dead(s, uid):
        raise GameError(f"💀 You're dead! Pay <b>{R.revive_cost:,}</b> 💎 with /revive before you can fight again.")


# ------------------------------------------------------------ protection
async def is_protected(s: AsyncSession, uid: int) -> bool:
    exp = (await s.execute(select(Protection.expires_at).where(Protection.user_id == uid))).scalar()
    return exp is not None and aware(exp) > utcnow()


async def protection_left(s: AsyncSession, uid: int) -> int:
    exp = (await s.execute(select(Protection.expires_at).where(Protection.user_id == uid))).scalar()
    return max(0, int((aware(exp) - utcnow()).total_seconds())) if exp else 0


async def grant_protection(s: AsyncSession, uid: int, seconds: int) -> None:
    exp = utcnow() + timedelta(seconds=seconds)
    res = await s.execute(update(Protection).where(Protection.user_id == uid).values(expires_at=exp, last_activated_at=utcnow()))
    if res.rowcount == 0:
        await insert_ignore(s, Protection, dict(user_id=uid, expires_at=exp, last_activated_at=utcnow()))


def protect_price(hours: int) -> int:
    return hours * R.protect_cost_per_hour


async def protect(uid: int, hours: int) -> tuple[int, int]:
    """Buy `hours` of protection. Returns (seconds, price). Nothing is charged on any error."""
    if not isinstance(hours, int) or not 1 <= hours <= R.protect_max_hours:
        raise GameError(f"❌ Choose between 1h and {R.protect_max_hours}h, e.g. <code>/protect 2h</code>.")
    price = protect_price(hours)
    async with db.tx() as s:
        await users.require(s, uid)
        if await is_protected(s, uid):
            from utils.fmt import dur
            raise GameError(f"🛡 You're already protected for another <b>{dur(await protection_left(s, uid))}</b>.")
        w = await economy.wallet_of(s, uid)
        if w.balance < price:
            hint = " Use /withdraw to take diamonds out of your bank." if w.bank else ""
            raise GameError(f"❌ {hours}h of protection costs <b>{price:,}</b> 💎 from your wallet; you have <b>{w.balance:,}</b>.{hint}")
        await economy.apply(s, uid, wallet=-price, kind="protect", meta={"hours": hours})  # sink
        await grant_protection(s, uid, hours * 3600)
        return hours * 3600, price


# ------------------------------------------------------------ fighter loading
async def load_fighter(s: AsyncSession, u: User, *, attack_bonus: float = 0.0, now=None) -> Fighter:
    now = now or utcnow()
    hp = R.base_hp + u.level * R.hp_per_level
    atk = R.base_attack + u.level * R.attack_per_level
    dfn = R.base_defence + u.level * R.defence_per_level
    rows = (await s.execute(select(InventoryItem, ShopItem).join(ShopItem, ShopItem.key == InventoryItem.item_key)
                            .where(InventoryItem.user_id == u.id, InventoryItem.equipped == True))).all()  # noqa: E712
    for inv, item in rows:
        m = 1 + R.upgrade_stat_bonus * (inv.level - 1)
        atk += item.attack * m; dfn += item.defence * m; hp += item.health * m
    pet = await s.get(Pet, u.id)
    if pet:
        pa, pd, ph = pet_stats(pet.level, pet.rarity)
        atk += pa; dfn += pd; hp += ph
    if await cooldowns.remaining(s, u.id, "buff_potion"):
        hp += R.potion_hp
    power, pm, charges = None, 1.0, 0
    if u.equipped_power:
        up = await s.get(UserPower, (u.id, u.equipped_power))
        if up and up.active_until and aware(up.active_until) > now and u.equipped_power in (
                "PHANTOM", "REAPER", "MIRROR", "PHOENIX", "PARASITE"):
            if u.equipped_power not in ("PHANTOM", "PHOENIX") or up.charges > 0:
                power, pm, charges = u.equipped_power, 1 + R.power_level_bonus * (up.level - 1), up.charges
    return Fighter(u.id, u.first_name, int(hp), atk * (1 + attack_bonus), dfn, power, pm, charges)


async def persist_consumed(s: AsyncSession, f: Fighter) -> None:
    for key in f.consumed:
        await s.execute(update(UserPower).where(UserPower.user_id == f.uid, UserPower.power_key == key,
                                                UserPower.charges > 0).values(charges=UserPower.charges - 1))


# ------------------------------------------------------------ settlement shared by kill/revenge
@dataclass
class FightOutcome:
    kind: str
    attacker: int
    defender: int
    attacker_name: str
    defender_name: str
    result: FightResult
    reward: int
    parasite: int
    bounty: int
    levelups: dict[int, int]
    record_id: int
    killed: int | None = None      # id of the player who died in this fight (None = nobody died)


async def _settle(s: AsyncSession, kind: str, a: User, d: User, fa: Fighter, fd: Fighter,
                  res: FightResult, chat_id: int | None) -> FightOutcome:
    winner_id = res.winner
    reward = parasite = bounty_paid = 0
    killed_id: int | None = None
    levelups: dict[int, int] = {}
    await persist_consumed(s, fa); await persist_consumed(s, fd)
    if winner_id is not None:
        w, l = (a, d) if winner_id == a.id else (d, a)
        if winner_id == a.id:
            # 30% of the victim's FREE diamonds (wallet only); at least 1 so a tiny wallet still pays
            wanted = max(1, int((await economy.wallet_of(s, l.id)).balance * R.kill_reward_pct))
        else:
            wallet = (await economy.wallet_of(s, l.id)).balance
            wanted = min(int(wallet * R.kill_loss_pct), R.kill_loss_max)
        reward = await economy.transfer_upto(s, l.id, w.id, wanted, kind, ref=None, meta={"fight": kind})
        killed = winner_id == a.id          # the attacker won: the target is killed
        if killed:
            await _set_dead(s, l.id)
            killed_id = l.id
        winner_f = fa if winner_id == a.id else fd
        if winner_f.power == "PARASITE":
            pct = 0.15 + 0.03 * round((winner_f.power_mult - 1) / R.power_level_bonus)
            left = (await economy.wallet_of(s, l.id)).balance
            parasite = await economy.transfer_upto(s, l.id, w.id, min(int(left * pct), 150), "parasite")
        await s.execute(update(User).where(User.id == w.id).values(kills=User.kills + (1 if killed else 0), wins=User.wins + 1))
        await s.execute(update(User).where(User.id == l.id).values(deaths=User.deaths + (1 if killed else 0),
                                                                   losses=User.losses + 1))
        levelups[w.id] = await progress.add_xp(s, w.id, R.xp_win)
        levelups[l.id] = await progress.add_xp(s, l.id, R.xp_loss)
        # (respawn immunity is granted when the victim pays for /revive)
        bounty_paid = await bounty.claim_for_kill(s, winner=w.id, loser=l.id) if killed else 0
        if chat_id:
            await insert_ignore(s, GroupStat, dict(chat_id=chat_id, user_id=w.id, kills=0, wins=0))
            await s.execute(update(GroupStat).where(GroupStat.chat_id == chat_id, GroupStat.user_id == w.id)
                            .values(kills=GroupStat.kills + (1 if killed else 0), wins=GroupStat.wins + 1))
        if killed:
            await progress.emit(s, w.id, "kill")
        if reward + parasite + bounty_paid:
            await progress.emit(s, w.id, "earn", 0)  # achievements check only (no mission credit: PvP moves diamonds)
    rec = CombatRecord(attacker_id=a.id, defender_id=d.id, kind=kind, winner_id=winner_id, reward=reward + parasite,
                       log=json.dumps(res.log[-30:]), chat_id=chat_id, created_at=utcnow())
    s.add(rec)
    await s.flush()
    return FightOutcome(kind, a.id, d.id, a.first_name, d.first_name, res, reward, parasite, bounty_paid, levelups, rec.id,
                        killed_id)


async def attack(attacker_id: int, target_id: int, chat_id: int | None = None, *, kind: str = "kill",
                 rng: random.Random | None = None) -> FightOutcome:
    rng = rng or _rng
    async with db.tx() as s:
        if attacker_id == target_id:
            raise GameError("❌ You can't attack yourself.")
        await users.lock(s, attacker_id, target_id)
        a = await users.require(s, attacker_id)
        d = await users.require(s, target_id)
        await require_alive(s, attacker_id)            # the dead cannot kill: they must /revive first
        if await is_dead(s, target_id):
            raise GameError("💀 That player is already dead.")
        if (await economy.wallet_of(s, target_id)).balance <= 0:
            # no free diamonds left (bank deposits are untouchable): nothing to take, so they count as already dead
            raise GameError("💀 That player is already dead.")
        record = None
        if kind == "revenge":
            since = utcnow() - timedelta(seconds=R.revenge_window_s)
            record = (await s.execute(select(CombatRecord).where(
                CombatRecord.defender_id == attacker_id, CombatRecord.attacker_id == target_id,
                CombatRecord.winner_id == target_id, CombatRecord.kind.in_(("kill", "revenge")),
                CombatRecord.revenged == False, CombatRecord.created_at >= since)  # noqa: E712
                .order_by(CombatRecord.id.desc()).limit(1))).scalar_one_or_none()
            if record is None:
                raise GameError("❌ No eligible attacker to take revenge on (window: "
                                f"{R.revenge_window_s // 60} min, once per attack).")
        if await is_protected(s, target_id):
            raise GameError("🛡 That player is protected.")
        if kind == "kill" and (left := await cooldowns.remaining(s, target_id, "immune")):
            raise GameError("😮‍💨 That player just respawned and is immune for a while.")
        day = utcnow() - timedelta(days=1)
        n = (await s.execute(select(func.count()).select_from(CombatRecord).where(
            CombatRecord.kind.in_(("kill", "revenge")), CombatRecord.created_at >= day,
            ((CombatRecord.attacker_id == attacker_id) & (CombatRecord.defender_id == target_id)) |
            ((CombatRecord.attacker_id == target_id) & (CombatRecord.defender_id == attacker_id))))).scalar()
        if n >= R.kill_pair_daily_limit:
            raise GameError("🚫 You two have fought too much today. Find someone else!")
        key, cd = ("revenge", R.revenge_cooldown_s) if kind == "revenge" else ("kill", R.kill_cooldown_s)
        if cd > 0:                                   # kill/revenge have no cooldown by default
            rem = await cooldowns.claim(s, attacker_id, key, cd)
            if rem:
                raise OnCooldown(rem, "Attack")
        if record is not None:
            r = await s.execute(update(CombatRecord).where(CombatRecord.id == record.id, CombatRecord.revenged == False)  # noqa: E712
                                .values(revenged=True))
            if r.rowcount != 1:
                raise GameError("❌ Revenge already taken.")
        if await is_protected(s, attacker_id):   # attacking drops your own shield
            await s.execute(delete(Protection).where(Protection.user_id == attacker_id))
        fa = await load_fighter(s, a, attack_bonus=R.revenge_attack_bonus if kind == "revenge" else 0)
        fd = await load_fighter(s, d)
        res = resolve(fa, fd, rng, a_first=True)
        return await _settle(s, kind, a, d, fa, fd, res, chat_id)
