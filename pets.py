"""Pets + hunting. Pets add combat stats (see game.rules.pet_stats, applied in combat.load_fighter)."""
from __future__ import annotations

import random

from sqlalchemy import update

from database.models import InventoryItem, Pet, utcnow
from database.session import db, insert_ignore
from game.rules import PET_ROLL, PET_SPECIES, R
from services import cooldowns, economy, progress, users
from services.errors import GameError, OnCooldown

_rng = random.SystemRandom()
HUNT_TABLE = [("nothing", 12), ("diamonds", 42), ("potion", 8), ("scroll", 5), ("shard", 7), ("coin", 3),
              ("egg", 8), ("xp", 15)]
ITEM_FOR = {"potion": "health_potion", "scroll": "xp_scroll", "shard": "crystal_shard", "coin": "ancient_coin"}


def need_xp(level: int) -> int:
    return level * 80


def _gain(pet: Pet, amount: int) -> int:
    """Adds xp, returns levels gained."""
    pet.xp += amount
    gained = 0
    while pet.level < R.pet_max_level and pet.xp >= need_xp(pet.level):
        pet.xp -= need_xp(pet.level)
        pet.level += 1
        gained += 1
    if pet.level >= R.pet_max_level:
        pet.xp = 0
    return gained


def _roll_pet(rng, rarity: str | None = None) -> tuple[str, str]:
    rarity = rarity or rng.choices([r for r, _ in PET_ROLL], [w for _, w in PET_ROLL])[0]
    species = rng.choice([k for k, v in PET_SPECIES.items() if v[2] == rarity])
    return species, rarity


async def get(uid: int) -> Pet | None:
    async with db.tx() as s:
        return await s.get(Pet, uid)


async def adopt(uid: int, rng: random.Random | None = None) -> Pet:
    rng = rng or _rng
    async with db.tx() as s:
        await users.require(s, uid)
        if await s.get(Pet, uid):
            raise GameError("🐾 You already have a pet.")
        await economy.apply(s, uid, wallet=-R.pet_adopt_cost, kind="pet_adopt")
        species, rarity = _roll_pet(rng, "common")
        pet = Pet(user_id=uid, species=species, rarity=rarity, name=PET_SPECIES[species][1], level=1, xp=0, created_at=utcnow())
        s.add(pet)
        return pet


async def feed(uid: int) -> tuple[Pet, int]:
    async with db.tx() as s:
        pet = await s.get(Pet, uid)
        if not pet:
            raise GameError("🐾 You don't have a pet yet. Use /pet adopt or find an egg with /hunt.")
        if pet.level >= R.pet_max_level:
            raise GameError("🌟 Your pet is already at max level!")
        rem = await cooldowns.claim(s, uid, "petfeed", R.pet_feed_cooldown_s)
        if rem:
            raise OnCooldown(rem, "Feeding your pet")
        await economy.apply(s, uid, wallet=-R.pet_feed_cost, kind="pet_feed")
        return pet, _gain(pet, R.pet_feed_xp)


async def rename(uid: int, name: str) -> Pet:
    name = " ".join(name.split())
    if not (2 <= len(name) <= 20):
        raise GameError("❌ Pet names must be 2–20 characters.")
    async with db.tx() as s:
        pet = await s.get(Pet, uid)
        if not pet:
            raise GameError("🐾 You don't have a pet yet.")
        pet.name = name
        return pet


async def _grant(s, uid: int, key: str, qty: int = 1) -> None:
    r = await s.execute(update(InventoryItem).where(InventoryItem.user_id == uid, InventoryItem.item_key == key)
                        .values(qty=InventoryItem.qty + qty))
    if r.rowcount == 0:
        await insert_ignore(s, InventoryItem, dict(user_id=uid, item_key=key, qty=qty, level=1, equipped=False))


async def hunt(uid: int, rng: random.Random | None = None) -> dict:
    rng = rng or _rng
    async with db.tx() as s:
        await users.require(s, uid)
        rem = await cooldowns.claim(s, uid, "hunt", R.hunt_cooldown_s)
        if rem:
            raise OnCooldown(rem, "Hunting")
        kind = rng.choices([k for k, _ in HUNT_TABLE], [w for _, w in HUNT_TABLE])[0]
        out: dict = {"kind": kind}
        if kind == "diamonds":
            out["amount"] = rng.randint(50, 300)
            await economy.apply(s, uid, wallet=out["amount"], kind="hunt")
            await progress.emit(s, uid, "earn", out["amount"])
        elif kind in ITEM_FOR:
            await _grant(s, uid, ITEM_FOR[kind])
        elif kind == "xp":
            out["amount"] = 30
            await progress.add_xp(s, uid, 30)
        elif kind == "egg":
            pet = await s.get(Pet, uid)
            if pet is None:
                species, rarity = _roll_pet(rng)
                pet = Pet(user_id=uid, species=species, rarity=rarity, name=PET_SPECIES[species][1], level=1, xp=0, created_at=utcnow())
                s.add(pet)
                out["new_pet"] = pet
            else:
                out["levels"] = _gain(pet, 60)
                out["pet"] = pet
        return out
