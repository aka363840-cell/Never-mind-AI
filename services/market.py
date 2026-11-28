"""Player marketplace for shop items (not powers): escrow listings, 5% fee, atomic buy.

Listings live in AppState rows `mkt:<id>` (JSON) so no new table/migration is needed. The listing row is the
escrow: the item leaves the seller's inventory when listed and a buyer claims it by DELETING the row
(rowcount == 1 is the atomic claim), all in one transaction with the diamond transfer."""
from __future__ import annotations

import json
import secrets
from datetime import timedelta

from sqlalchemy import delete, select, update

from database.models import AppState, InventoryItem, ShopItem, aware, utcnow
from database.session import db, insert_ignore
from services import economy, users
from services.errors import GameError

FEE = 0.05
MAX_LISTINGS = 5
MAX_MARKUP = 3          # unit price may be at most 3x the shop price (stops alt-account laundering)
PREFIX = "mkt:"


def _parse(row) -> dict | None:
    try:
        d = json.loads(row.value)
        d["id"] = row.key[len(PREFIX):]
        return d
    except Exception:
        return None


def net_after_fee(price: int) -> int:
    return price - int(price * FEE)


async def _give(s, uid: int, o: ShopItem, qty: int, level: int) -> None:
    if o.stackable:
        r = await s.execute(update(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key)
                            .values(qty=InventoryItem.qty + qty))
        if r.rowcount == 0:
            await insert_ignore(s, InventoryItem, dict(user_id=uid, item_key=o.key, qty=qty, level=1, equipped=False))
            await s.execute(update(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key,
                                                        InventoryItem.qty < qty).values(qty=qty))
    elif not await insert_ignore(s, InventoryItem, dict(user_id=uid, item_key=o.key, qty=1, level=level, equipped=False)):
        raise GameError("❌ You already own that item (it can only be owned once).")


async def listings(limit: int = 15) -> list[dict]:
    async with db.tx() as s:
        rows = (await s.execute(select(AppState).where(AppState.key.like(PREFIX + "%")))).scalars().all()
    out = [d for d in (_parse(r) for r in rows) if d]
    out.sort(key=lambda d: d.get("t", 0), reverse=True)
    return out[:limit]


async def mine(uid: int) -> list[dict]:
    return [d for d in await listings(10_000) if d["s"] == uid]


async def create(uid: int, key: str, qty: int, price: int) -> dict:
    key = (key or "").strip().lower()
    if not isinstance(qty, int) or qty < 1 or qty > 99:
        raise GameError("❌ Quantity must be 1–99.")
    if not isinstance(price, int) or price < 1:
        raise GameError("❌ Price must be at least 1 💎 (total for the whole stack).")
    async with db.tx() as s:
        await users.require(s, uid)
        o = await s.get(ShopItem, key)
        if o is None:
            raise GameError("❌ Unknown item. Use the item key from /inv.")
        if not o.stackable and qty != 1:
            raise GameError("❌ That item is sold one at a time.")
        if price > o.price * qty * MAX_MARKUP:
            raise GameError(f"❌ Too expensive: max {o.price * qty * MAX_MARKUP:,} 💎 for this ({MAX_MARKUP}x shop price).")
        rows = (await s.execute(select(AppState).where(AppState.key.like(PREFIX + "%")))).scalars().all()
        if sum(1 for r in rows if (_parse(r) or {}).get("s") == uid) >= MAX_LISTINGS:
            raise GameError(f"❌ You can have at most {MAX_LISTINGS} listings. Cancel one with /mcancel.")
        inv = (await s.execute(select(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key))).scalar_one_or_none()
        level = inv.level if inv else 1
        r = await s.execute(update(InventoryItem).where(
            InventoryItem.user_id == uid, InventoryItem.item_key == o.key, InventoryItem.qty >= qty,
            InventoryItem.equipped == False).values(qty=InventoryItem.qty - qty))  # noqa: E712
        if r.rowcount != 1:
            raise GameError("❌ You don't have that many unequipped. (Unequip first with /equip.)")
        await s.execute(delete(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == o.key,
                                                    InventoryItem.qty == 0))
        lid = secrets.token_hex(3)
        d = {"s": uid, "k": o.key, "q": qty, "l": level, "p": price, "n": o.name, "e": o.emoji,
             "t": int(utcnow().timestamp())}
        await insert_ignore(s, AppState, dict(key=PREFIX + lid, value=json.dumps(d, separators=(",", ":"))))
        d["id"] = lid
        return d


async def cancel(uid: int, lid: str) -> dict:
    async with db.tx() as s:
        row = await s.get(AppState, PREFIX + (lid or "").strip().lower())
        d = _parse(row) if row else None
        if not d or d["s"] != uid:
            raise GameError("❌ You have no such listing.")
        r = await s.execute(delete(AppState).where(AppState.key == row.key))
        if r.rowcount != 1:
            raise GameError("❌ That listing is already gone.")
        o = await s.get(ShopItem, d["k"])
        await _give(s, uid, o, d["q"], d["l"])
        return d


async def buy(uid: int, lid: str) -> tuple[dict, int]:
    """Returns (listing, fee). Raises GameError (nothing changes) on any problem."""
    async with db.tx() as s:
        u = await users.require(s, uid)
        if (utcnow() - aware(u.created_at)).total_seconds() < 24 * 3600:
            raise GameError("🔒 New accounts can't use the market for the first 24 hours (anti-abuse).")
        row = await s.get(AppState, PREFIX + (lid or "").strip().lower())
        d = _parse(row) if row else None
        if not d:
            raise GameError("❌ That listing doesn't exist (maybe sold already).")
        if d["s"] == uid:
            raise GameError("❌ That's your own listing — use /mcancel to take it back.")
        r = await s.execute(delete(AppState).where(AppState.key == row.key))
        if r.rowcount != 1:
            raise GameError("❌ Someone just bought it.")
        o = await s.get(ShopItem, d["k"])
        await _give(s, uid, o, d["q"], d["l"])
        net = net_after_fee(d["p"])
        await economy.apply(s, uid, wallet=-d["p"], kind="market_buy", counterparty=d["s"], meta={"item": d["k"], "id": d["id"]})
        await economy.apply(s, d["s"], wallet=net, kind="market_sell", counterparty=uid, meta={"item": d["k"], "id": d["id"]})
        return d, d["p"] - net
