"""Catalogue, purchases, sales, equipment, upgrades, consumables and powers."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import InventoryItem, Power, ShopItem, User, UserPower, aware, utcnow
from database.session import db, insert_ignore
from game.rules import EQUIP_SLOTS, ITEMS, POWERS, R
from services import combat, cooldowns, economy, progress, users
from services.errors import GameError, OnCooldown


async def seed_catalog() -> None:
    """Sync code catalogue -> DB (price changes in rules.py apply on next boot)."""
    async with db.tx() as s:
        for p in POWERS.values():
            row = await s.get(Power, p.key)
            vals = dict(name=p.name, price=p.price, activation_cost=p.activation_cost, cooldown_s=p.cooldown_s,
                        duration_s=p.duration_s, max_level=p.max_level, description=p.description, limitation=p.limitation)
            if row is None:
                s.add(Power(key=p.key, **vals))
            else:
                for k, v in vals.items(): setattr(row, k, v)
        for i in ITEMS.values():
            row = await s.get(ShopItem, i.key)
            vals = dict(category=i.category, name=i.name, emoji=i.emoji, price=i.price, description=i.description,
                        attack=i.attack, defence=i.defence, health=i.health, max_level=i.max_level, stackable=i.stackable)
            if row is None:
                s.add(ShopItem(key=i.key, **vals))
            else:
                for k, v in vals.items(): setattr(row, k, v)


async def catalog(category: str) -> list[dict]:
    async with db.tx() as s:
        if category == "powers":
            rows = (await s.execute(select(Power).order_by(Power.price))).scalars().all()
            return [dict(key=r.key, name=POWERS[r.key].emoji + " " + r.name, price=r.price, desc=r.description) for r in rows]
        rows = (await s.execute(select(ShopItem).where(ShopItem.category == category).order_by(ShopItem.price))).scalars().all()
        return [dict(key=r.key, name=f"{r.emoji} {r.name}", price=r.price, desc=r.description,
                     atk=r.attack, dfn=r.defence, hp=r.health) for r in rows]


async def _lookup(s: AsyncSession, key: str):
    key = key.strip()
    p = await s.get(Power, key.upper())
    if p:
        return "power", p
    i = await s.get(ShopItem, key.lower())
    if i:
        return "item", i
    raise GameError("❌ Unknown item. Check /shop for item keys.")


async def quote(key: str) -> dict:
    async with db.tx() as s:
        kind, o = await _lookup(s, key)
        return dict(kind=kind, key=o.key, name=o.name, price=o.price, desc=o.description,
                    category="powers" if kind == "power" else o.category)


async def buy(uid: int, key: str, qty: int = 1) -> tuple[str, int]:
    if not isinstance(qty, int) or not (1 <= qty <= 99):
        raise GameError("❌ Quantity must be 1–99.")
    async with db.tx() as s:
        kind, o = await _lookup(s, key)
        if kind == "power":
            if qty != 1:
                raise GameError("❌ Powers are bought one at a time.")
            if not await insert_ignore(s, UserPower, dict(user_id=uid, power_key=o.key, level=1, charges=0, uses=0)):
                raise GameError("✅ You already own that power.")
        else:
            if not o.stackable and qty != 1:
                raise GameError("❌ That item can only be bought once.")
            if o.stackable:
                r = await s.execute(update(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key)
                                    .values(qty=InventoryItem.qty + qty))
                if r.rowcount == 0:
                    await insert_ignore(s, InventoryItem, dict(user_id=uid, item_key=o.key, qty=qty, level=1, equipped=False))
                    # lost a race with a concurrent first purchase -> add to it instead
                    await s.execute(update(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key,
                                                                InventoryItem.qty < qty).values(qty=qty))
            elif not await insert_ignore(s, InventoryItem, dict(user_id=uid, item_key=o.key, qty=1, level=1, equipped=False)):
                raise GameError("✅ You already own that item.")
        total = o.price * qty
        await economy.apply(s, uid, wallet=-total, kind="shop_buy", meta={"item": o.key, "qty": qty})
        await progress.emit(s, uid, "shop_buy")
        return o.name, total


async def sell(uid: int, key: str, qty: int = 1) -> tuple[str, int]:
    if not isinstance(qty, int) or qty < 1:
        raise GameError("❌ Invalid quantity.")
    async with db.tx() as s:
        kind, o = await _lookup(s, key)
        if kind == "power":
            raise GameError("❌ Powers can't be sold.")
        r = await s.execute(update(InventoryItem).where(
            InventoryItem.user_id == uid, InventoryItem.item_key == o.key, InventoryItem.qty >= qty,
            InventoryItem.equipped == False).values(qty=InventoryItem.qty - qty))  # noqa: E712
        if r.rowcount != 1:
            raise GameError("❌ You don't have that many unequipped. (Unequip first with /equip.)")
        await s.execute(delete(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key, InventoryItem.qty == 0))
        gain = int(o.price * R.sell_ratio) * qty
        await economy.apply(s, uid, wallet=gain, kind="shop_sell", meta={"item": o.key, "qty": qty})
        return o.name, gain


async def equip(uid: int, key: str) -> tuple[str, bool]:
    """Toggle equip. Returns (name, now_equipped). Limits: 1 power, 1 weapon, 1 armour, 1 shield, 1 title."""
    async with db.tx() as s:
        kind, o = await _lookup(s, key)
        if kind == "power":
            if await s.get(UserPower, (uid, o.key)) is None:
                raise GameError("❌ You don't own that power.")
            u = await s.get(User, uid)
            new = None if u.equipped_power == o.key else o.key
            await s.execute(update(User).where(User.id == uid).values(equipped_power=new))
            return o.name, new is not None
        slot = EQUIP_SLOTS.get(o.category)
        if not slot:
            raise GameError("❌ That item can't be equipped.")
        inv = (await s.execute(select(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key))).scalar_one_or_none()
        if inv is None:
            raise GameError("❌ You don't own that item.")
        if inv.equipped:
            await s.execute(update(InventoryItem).where(InventoryItem.id == inv.id).values(equipped=False))
            if slot == "cosmetic":
                await s.execute(update(User).where(User.id == uid).values(title=None))
            return o.name, False
        same = select(ShopItem.key).where(ShopItem.category == o.category)
        await s.execute(update(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key.in_(same)).values(equipped=False))
        await s.execute(update(InventoryItem).where(InventoryItem.id == inv.id).values(equipped=True))
        if slot == "cosmetic":
            await s.execute(update(User).where(User.id == uid).values(title=o.name))
        return o.name, True


async def upgrade(uid: int, key: str) -> tuple[str, int, int]:
    """Returns (name, new_level, cost). Optimistic-locked on current level (no double upgrade)."""
    async with db.tx() as s:
        kind, o = await _lookup(s, key)
        if kind == "power":
            row = await s.get(UserPower, (uid, o.key))
            if row is None:
                raise GameError("❌ You don't own that power.")
            cur, mx, base = row.level, o.max_level, int(o.price * R.power_upgrade_cost_ratio)
            tbl, where = UserPower, (UserPower.user_id == uid, UserPower.power_key == o.key, UserPower.level == cur)
        else:
            row = (await s.execute(select(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key))).scalar_one_or_none()
            if row is None:
                raise GameError("❌ You don't own that item.")
            cur, mx, base = row.level, o.max_level, int(o.price * 0.5)
            tbl, where = InventoryItem, (InventoryItem.id == row.id, InventoryItem.level == cur)
        if cur >= mx:
            raise GameError(f"✅ {o.name} is already at max level ({mx}).")
        cost = base * cur
        r = await s.execute(update(tbl).where(*where).values(level=cur + 1))
        if r.rowcount != 1:
            raise GameError("⚠️ Upgrade already processed.")
        await economy.apply(s, uid, wallet=-cost, kind="upgrade", meta={"item": o.key, "to": cur + 1})
        return o.name, cur + 1, cost


async def use_item(uid: int, key: str) -> str:
    async with db.tx() as s:
        kind, o = await _lookup(s, key)
        if kind != "item" or o.category != "consumables":
            raise GameError("❌ That isn't a consumable.")
        if o.key == "health_potion" and await cooldowns.remaining(s, uid, "buff_potion"):
            raise GameError("🧪 A potion is already active.")
        r = await s.execute(update(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key,
                                                        InventoryItem.qty >= 1).values(qty=InventoryItem.qty - 1))
        if r.rowcount != 1:
            raise GameError("❌ You don't have one.")
        await s.execute(delete(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key, InventoryItem.qty == 0))
        if o.key == "health_potion":
            await cooldowns.set_(s, uid, "buff_potion", R.potion_duration_s)
            return f"🧪 +{R.potion_hp} HP in your fights for {R.potion_duration_s // 60} minutes."
        if o.key == "xp_scroll":
            lvl = await progress.add_xp(s, uid, 100)
            return f"📜 +100 XP (level {lvl})."
        raise GameError("❌ Nothing happens.")


async def inventory(uid: int) -> list[dict]:
    async with db.tx() as s:
        rows = (await s.execute(select(InventoryItem, ShopItem).join(ShopItem, ShopItem.key == InventoryItem.item_key)
                                .where(InventoryItem.user_id == uid).order_by(ShopItem.category, ShopItem.price))).all()
        return [dict(key=i.item_key, name=f"{it.emoji} {it.name}", plain=it.name, qty=i.qty, level=i.level, equipped=i.equipped,
                     cat=it.category, max=it.max_level, price=it.price,
                     atk=it.attack * (1 + R.upgrade_stat_bonus * (i.level - 1)),
                     dfn=it.defence * (1 + R.upgrade_stat_bonus * (i.level - 1)),
                     hp=it.health * (1 + R.upgrade_stat_bonus * (i.level - 1)),
                     upgrade_cost=int(it.price * 0.5) * i.level if i.level < it.max_level else 0,
                     sell_value=int(it.price * R.sell_ratio)) for i, it in rows]


async def loadout(uid: int) -> dict:
    """Equipped gear (weapon / armour / shield / title) and the combat bonus it adds, upgrade levels included.
    Same maths as combat.load_fighter, so what /profile shows is what a fight really uses."""
    out: dict = dict(weapon=None, armour=None, shield=None, title=None, atk=0.0, dfn=0.0, hp=0.0)
    slot_of = {"weapons": "weapon", "armour": "armour", "shields": "shield", "cosmetics": "title"}
    async with db.tx() as s:
        rows = (await s.execute(select(InventoryItem, ShopItem).join(ShopItem, ShopItem.key == InventoryItem.item_key)
                                .where(InventoryItem.user_id == uid, InventoryItem.equipped == True))).all()  # noqa: E712
    for inv, it in rows:
        m = 1 + R.upgrade_stat_bonus * (inv.level - 1)
        out["atk"] += it.attack * m; out["dfn"] += it.defence * m; out["hp"] += it.health * m
        slot = slot_of.get(it.category)
        if slot:
            out[slot] = dict(key=it.key, name=f"{it.emoji} {it.name}", level=inv.level, max=it.max_level)
    return out


async def owned_powers(uid: int) -> dict[str, UserPower]:
    async with db.tx() as s:
        rows = (await s.execute(select(UserPower).where(UserPower.user_id == uid))).scalars().all()
        return {r.power_key: r for r in rows}


# ---------------------------------------------------------------- powers
def _mult(level: int) -> float:
    return 1 + R.power_level_bonus * (level - 1)


async def activate(uid: int, target: int | None = None) -> dict:
    """Activate the equipped power. Pays activation cost (burned), starts cooldown, records usage."""
    async with db.tx() as s:
        u = await s.get(User, uid)
        if not u.equipped_power:
            raise GameError("❌ Equip a power first (/powers).")
        p = POWERS[u.equipped_power]
        up = await s.get(UserPower, (uid, p.key))
        m = _mult(up.level)
        info: dict = dict(power=p.key, name=p.name)
        if p.key in ("GUARDIAN", "ORACLE"):
            if target is None:
                raise GameError(f"❌ {p.name} needs a target — reply to a player with /activate.")
            if target == uid:
                raise GameError("❌ You can't target yourself.")
            t = await users.require(s, target)
            if p.key == "GUARDIAN" and await combat.is_protected(s, target):
                raise GameError("🛡 That player is already protected.")
        cd = max(60, int(p.cooldown_s * (1 - 0.05 * (up.level - 1))))
        rem = await cooldowns.claim(s, uid, f"power:{p.key}", cd)
        if rem:
            raise OnCooldown(rem, p.name)
        if p.activation_cost:
            await economy.apply(s, uid, wallet=-p.activation_cost, kind="power_activate", meta={"power": p.key})
        now = utcnow()
        vals = dict(uses=UserPower.uses + 1, last_used_at=now)
        if p.key in ("PHANTOM", "PHOENIX"):
            vals["charges"] = 1
        if p.key in ("PHANTOM", "PHOENIX", "REAPER", "MIRROR", "PARASITE"):
            vals["active_until"] = now + timedelta(seconds=int(p.duration_s * m))
            info["seconds"] = int(p.duration_s * m)
        if p.key == "GUARDIAN":
            secs = int(p.duration_s * m)
            await combat.grant_protection(s, target, secs)
            info.update(seconds=secs, target=t.first_name)
        if p.key == "ORACLE":
            f = await combat.load_fighter(s, t)
            info.update(target=t.first_name, level=t.level, hp=f.start_hp, atk=round(f.atk), dfn=round(f.dfn),
                        their_power=t.equipped_power or "—", protected=await combat.protection_left(s, target))
        await s.execute(update(UserPower).where(UserPower.user_id == uid, UserPower.power_key == p.key).values(**vals))
        await progress.emit(s, uid, "power_use")
        return info
