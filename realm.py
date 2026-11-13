"""Realm layer: button-driven Dungeon runs, Pet Expeditions, co-op Heists, the cooldown board, lifetime
stat counters and the extended achievements.

No gambling mechanics here. Every diamond change goes through economy.apply() (atomic + ledgered, idempotent
refs), cooldowns through cooldowns.claim(). Dungeon runs are persisted in the DB (row-locked per action), so
restarts and double-taps cannot duplicate or lose rewards.
"""
from __future__ import annotations

import json
import random
import secrets
from datetime import timedelta

from sqlalchemy import delete, func, or_, select, update

from database.models import (Achievement, Child, ClanMember, Cooldown, DungeonRun, Expedition, Pet, Property, User,
                             UserStat, Wallet, aware, utcnow)
from database.session import db, insert_ignore
from game.engine import STRATEGIES, Fighter, resolve
from game.rules import ACHIEVEMENTS, PET_RARITY, R
from services import combat, cooldowns, economy, pets, progress, users
from services.errors import GameError, OnCooldown
from utils import content

_rng = random.SystemRandom()

# =============================================================== stat counters + achievements
STAT_ACH: dict[str, list[tuple[int, str]]] = {
    "dungeon_clear": [(1, "dungeon_first"), (10, "dungeon_10")],
    "dungeon_hard": [(1, "dungeon_hard")],
    "expedition": [(1, "explorer"), (10, "voyager")],
    "heist_win": [(1, "heist_first"), (5, "heist_5")],
    "heist_perfect": [(1, "heist_perfect")],
    "fish": [(50, "angler")],
    "mine": [(50, "prospector")],
    "whale": [(1, "whale")],
}
# (stat key, label) shown as progress in /achievements
STAT_LABELS = {"dungeon_clear": "Dungeon clears", "expedition": "Expeditions", "heist_win": "Heists pulled off",
               "fish": "Fishing trips", "mine": "Mining trips"}
GROUPS: list[tuple[str, list[str]]] = [
    ("⚔️ Combat & Wealth", ["first_blood", "duelist", "thief", "tycoon", "powered", "collector", "slayer", "mogul"]),
    ("🗺 Realm", ["dungeon_first", "dungeon_10", "dungeon_hard", "explorer", "voyager",
                  "heist_first", "heist_5", "heist_perfect"]),
    ("🏡 Life & Collection", ["pet_owner", "pet_legend", "married", "parent", "landlord", "clanner",
                              "angler", "prospector", "whale"]),
    ("⭐ Milestones", ["level_5", "level_10", "level_20", "completionist"]),
]


async def stat(s, uid: int, key: str) -> int:
    return (await s.execute(select(UserStat.value).where(UserStat.user_id == uid, UserStat.key == key))).scalar() or 0


async def stats_of(uid: int) -> dict[str, int]:
    async with db.tx() as s:
        rows = (await s.execute(select(UserStat.key, UserStat.value).where(UserStat.user_id == uid))).all()
    return {k: v for k, v in rows}


async def bump(s, uid: int, key: str, n: int = 1) -> list[str]:
    """Add to a lifetime counter inside the caller's transaction. Returns names of newly unlocked achievements."""
    res = await s.execute(update(UserStat).where(UserStat.user_id == uid, UserStat.key == key)
                          .values(value=UserStat.value + n))
    if res.rowcount == 0 and not await insert_ignore(s, UserStat, dict(user_id=uid, key=key, value=n)):
        await s.execute(update(UserStat).where(UserStat.user_id == uid, UserStat.key == key)
                        .values(value=UserStat.value + n))
    total = await stat(s, uid, key)
    new: list[str] = []
    for need, ach in STAT_ACH.get(key, []):
        if total >= need and await progress._unlock(s, uid, ach):
            new.append(ACHIEVEMENTS[ach][0])
    return new


async def sweep(s, uid: int) -> list[str]:
    """Evaluate every state-based achievement (pet, marriage, houses, ...). Idempotent."""
    from services import extras
    u = await s.get(User, uid)
    if u is None:
        return []
    await s.refresh(u)
    facts: dict[str, bool] = {}
    pet = await s.get(Pet, uid)
    facts["pet_owner"] = pet is not None
    facts["pet_legend"] = pet is not None and pet.rarity == "legendary"
    facts["married"] = (await extras.partner_of(s, uid)) is not None
    facts["parent"] = ((await s.execute(select(func.count()).select_from(Child).where(
        or_(Child.parent_a == uid, Child.parent_b == uid)))).scalar() or 0) > 0
    facts["landlord"] = ((await s.execute(select(func.count()).select_from(Property).where(
        Property.user_id == uid))).scalar() or 0) > 0
    facts["clanner"] = (await s.get(ClanMember, uid)) is not None
    w = (await s.execute(select(Wallet).where(Wallet.user_id == uid))).scalar_one_or_none()
    if w is not None:
        await s.refresh(w)
        facts["mogul"] = w.balance + w.bank >= 100_000
    facts["level_20"] = u.level >= 20
    facts["slayer"] = u.kills >= 50
    new: list[str] = []
    for key, ok in facts.items():
        if ok and await progress._unlock(s, uid, key):
            new.append(ACHIEVEMENTS[key][0])
    have = set((await s.execute(select(Achievement.key).where(Achievement.user_id == uid))).scalars())
    if all(k in have for k in ACHIEVEMENTS if k != "completionist") and await progress._unlock(s, uid, "completionist"):
        new.append(ACHIEVEMENTS["completionist"][0])
    return new


async def sweep_and_progress(uid: int) -> tuple[list[str], set[str], dict[str, int]]:
    """For /achievements: unlock what is earned, then return (newly unlocked names, unlocked keys, counters)."""
    async with db.tx() as s:
        new = await sweep(s, uid)
        have = set((await s.execute(select(Achievement.key).where(Achievement.user_id == uid))).scalars())
        counters = {k: await stat(s, uid, k) for k in STAT_LABELS}
    return new, have, counters


# =============================================================== cooldown board
CD_ROWS = [("daily", "🎁 Daily"), ("weekly", "📅 Weekly"), ("work", "💼 Work"), ("crime", "🕶 Crime"),
           ("rob", "🥷 Rob"), ("hunt", "🏹 Hunt"), ("fish", "🎣 Fish"), ("mine", "⛏ Mine"),
           ("trivia", "🧠 Trivia"), ("spin", "🎡 Lucky wheel"), ("petfeed", "🍖 Feed pet"),
           ("dungeon", "🗝 Dungeon"), ("heist", "🎭 Heist"), ("date", "💑 Date"),
           ("familybonus", "💰 Family bonus"), ("rent", "🏦 House rent"), ("protect", "🛡 Protection"),
           ("jail", "🚔 Jail")]


async def cooldown_board(uid: int) -> dict:
    now = utcnow()
    async with db.tx() as s:
        rows = (await s.execute(select(Cooldown.key, Cooldown.expires_at).where(Cooldown.user_id == uid))).all()
        left = {k: int((aware(e) - now).total_seconds()) for k, e in rows}
        exp = await s.get(Expedition, uid)
        run = await s.get(DungeonRun, uid)
        pet = await s.get(Pet, uid)
        married = await _married(s, uid)
        rent_owned = list((await s.execute(select(Property.key).where(Property.user_id == uid))).scalars())
    from services.life import HOUSES
    items = []
    for key, label in CD_ROWS:
        t = max(0, left.get(key, 0))
        if key == "rent":   # per-property timers: show the soonest one that is ready / coming up
            owned = [k for k in rent_owned if k in HOUSES]
            if not owned:
                continue
            t = min((max(t, left.get(f"rent:{k}", 0), 0) for k in owned), default=t)
        if key == "jail" and not t:
            continue
        if key in ("date", "familybonus") and not married:
            continue
        items.append((label, t))
    return {"items": items, "expedition": (exp.kind, int((aware(exp.ends_at) - now).total_seconds())) if exp else None,
            "dungeon": run is not None, "pet": pet is not None}


async def _married(s, uid: int) -> bool:
    from services import extras
    return (await extras.partner_of(s, uid)) is not None


# =============================================================== pet expeditions
EXPEDITIONS = {  # key: (emoji, name, seconds, (min, max) diamonds, pet xp, bonus item chance)
    "scout": ("🌲", "Forest Scout", 3600, (90, 170), 25, 0.08),
    "trek": ("⛰", "Mountain Trek", 4 * 3600, (380, 640), 70, 0.20),
    "voyage": ("🌊", "Sea Voyage", 8 * 3600, (850, 1350), 140, 0.35),
}
EXP_TALES = {
    "scout": ["{p} sniffed out a hidden stash under an old oak.", "{p} chased a squirrel and tripped over treasure.",
              "{p} befriended a talking mushroom who paid in diamonds."],
    "trek": ["{p} climbed a frozen ridge and dug a cache out of the snow.", "{p} outran an avalanche with a pouch in its mouth.",
             "{p} found an abandoned dwarven camp."],
    "voyage": ["{p} sailed past the Storm Reef and came home with a sunken chest.", "{p} out-bartered a pirate captain.",
               "{p} discovered an island where diamonds grow like coconuts."],
}
for _k, _more in content.EXP_TALES_MORE.items():       # more adventure tales per expedition (text only)
    EXP_TALES[_k] += _more
EXP_ITEMS = [("health_potion", 40), ("xp_scroll", 30), ("crystal_shard", 22), ("ancient_coin", 8)]


def exp_multiplier(pet: Pet) -> float:
    return min(2.0, (1 + pet.level * 0.04) * PET_RARITY.get(pet.rarity, (1.0, ""))[0] ** 0.5)


async def expedition_status(uid: int) -> dict:
    async with db.tx() as s:
        pet = await s.get(Pet, uid)
        exp = await s.get(Expedition, uid)
        left = int((aware(exp.ends_at) - utcnow()).total_seconds()) if exp else 0
        return {"pet": pet, "kind": exp.kind if exp else None, "left": max(0, left), "active": exp is not None}


async def expedition_start(uid: int, kind: str) -> Pet:
    if kind not in EXPEDITIONS:
        raise GameError("❌ Unknown expedition.")
    async with db.tx() as s:
        await users.require(s, uid)
        pet = await s.get(Pet, uid)
        if pet is None:
            raise GameError("🐾 You need a pet first. Use /pet adopt or find an egg with /hunt.")
        if await s.get(Expedition, uid):
            raise GameError("🧭 Your pet is already out exploring. Check /expedition.")
        now = utcnow()
        if not await insert_ignore(s, Expedition, dict(user_id=uid, kind=kind, started_at=now,
                                                       ends_at=now + timedelta(seconds=EXPEDITIONS[kind][2]),
                                                       notified=False)):
            raise GameError("🧭 Your pet is already out exploring.")
        return pet


async def expedition_collect(uid: int) -> dict:
    async with db.tx() as s:
        exp = await s.get(Expedition, uid)
        if exp is None:
            raise GameError("🧭 No expedition to collect. Send your pet out with /expedition.")
        now = utcnow()
        if aware(exp.ends_at) > now:
            from utils.fmt import dur
            raise GameError(f"⏳ Your pet is still away — back in <b>{dur(int((aware(exp.ends_at) - now).total_seconds()))}</b>.")
        kind, started = exp.kind, aware(exp.started_at)
        res = await s.execute(delete(Expedition).where(Expedition.user_id == uid, Expedition.ends_at <= now)
                              .execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise GameError("🧭 Already collected.")
        pet = await s.get(Pet, uid)
        if pet is None:
            raise GameError("🐾 Your pet is gone.")
        emoji, name, _, (lo, hi), pxp, item_chance = EXPEDITIONS[kind]
        mult = exp_multiplier(pet)
        amount = int(_rng.randint(lo, hi) * mult)
        await economy.apply(s, uid, wallet=amount, kind="expedition", ref=f"exp:{uid}:{int(started.timestamp())}")
        await progress.emit(s, uid, "earn", amount)
        levels = pets._gain(pet, pxp)
        item = None
        if _rng.random() < item_chance:
            item = _rng.choices([k for k, _ in EXP_ITEMS], [w for _, w in EXP_ITEMS])[0]
            await pets._grant(s, uid, item)
        await progress.emit(s, uid, "expedition")
        unlocked = await bump(s, uid, "expedition")
        unlocked += await sweep(s, uid)
        return {"kind": kind, "amount": amount, "levels": levels, "item": item, "pet": pet, "mult": mult,
                "tale": _rng.choice(EXP_TALES[kind]).format(p=pet.name), "unlocked": unlocked}


async def expeditions_due() -> list[tuple[int, str, str]]:
    """Expeditions that just finished and were not announced yet -> [(uid, kind, pet name)]. At-most-once."""
    out = []
    async with db.tx() as s:
        rows = (await s.execute(select(Expedition).where(Expedition.ends_at <= utcnow(),
                                                         Expedition.notified == False))).scalars().all()  # noqa: E712
        for e in rows:
            r = await s.execute(update(Expedition).where(Expedition.user_id == e.user_id,
                                                         Expedition.notified == False).values(notified=True))  # noqa: E712
            if r.rowcount == 1:
                pet = await s.get(Pet, e.user_id)
                out.append((e.user_id, e.kind, pet.name if pet else "Your pet"))
    return out


# =============================================================== dungeon
DUNGEON_CD_S = 90 * 60
DUNGEON_FLOORS = 5
STALE_RUN_S = 2 * 3600
HARD_GOLD = 1.4
GOLD_SCALE = 1.7                      # all diamonds found in rooms (diamonds are paid when the run ends)
CLEAR_BONUS = {False: 200, True: 350}
CLEAR_XP = {False: 60, True: 100}

MONSTERS = {
    1: [("🟢", "Cave Slime"), ("🦇", "Giant Bat"), ("🐀", "Sewer Rat King")],
    2: [("👺", "Goblin Raider"), ("🕷", "Web Spinner"), ("🐺", "Dire Wolf")],
    3: [("💀", "Skeleton Knight"), ("🧟", "Crypt Zombie"), ("🦂", "Giant Scorpion")],
    4: [("👹", "Ogre Brute"), ("👻", "Wailing Wraith"), ("🧌", "Stone Troll")],
}
BOSSES = [("🐉", "Ashen Drake"), ("👑", "Bone Tyrant"), ("🕸", "Void Spider Queen"), ("🗿", "Stone Colossus")]
LOOT_TABLE = [("health_potion", 38), ("xp_scroll", 32), ("crystal_shard", 22), ("ancient_coin", 8)]
ROOM_KINDS = [("monster", 46), ("chest", 14), ("shrine", 9), ("trap", 10), ("merchant", 9), ("camp", 12)]
ROOM_FLAVOR = {
    "chest": "A dusty chest sits in the corner. Something about it feels… too perfect.",
    "shrine": "A glowing shrine hums softly. The air smells of rain.",
    "trap": "Click. A pressure plate under your boot. Darts glint in the walls.",
    "merchant": "A hooded merchant waves you over. “Diamonds for goods, friend.”",
    "camp": "A forgotten campfire still crackles. A safe place to catch your breath.",
}


def monster_stats(base: list[float], floor: int, boss: bool, hard: bool) -> tuple[int, float, float]:
    """Scaled from LEVEL-only base stats, so gear / pets / powers give a real edge. -> (hp, attack, defence)"""
    bhp, batk, bdef = base
    k = 1.25 if hard else 1.0
    mdef = bdef * (0.8 + 0.04 * floor)
    base_dmg = max(2.0, batk - 0.6 * mdef)
    mhp = base_dmg * (2.0 + 0.8 * floor) * (1.5 if boss else 1.0) * k
    matk = (0.6 * bdef + bhp * (0.031 + 0.006 * floor)) * (1.3 if boss else 1.0) * (1 + (k - 1) * 0.6)
    return max(10, int(mhp)), matk, mdef


def _make_monster(st: dict, rng: random.Random) -> dict:
    floor, boss = st["floor"], st["floor"] == DUNGEON_FLOORS
    emoji, name = rng.choice(BOSSES) if boss else rng.choice(MONSTERS[min(4, floor)])
    hp, atk, dfn = monster_stats(st["base"], floor, boss, st["hard"])
    return {"t": "monster", "emoji": emoji, "name": name, "hp": hp, "max": hp, "atk": atk, "def": dfn,
            "boss": boss, "sneak": not boss}


def _gen_room(st: dict, rng: random.Random) -> dict:
    if st["floor"] == 1 or st["floor"] == DUNGEON_FLOORS:
        return _make_monster(st, rng)
    kinds = [(k, w) for k, w in ROOM_KINDS if k == "monster" or k != st.get("last")]
    kind = rng.choices([k for k, _ in kinds], [w for _, w in kinds])[0]
    if kind == "monster":
        return _make_monster(st, rng)
    return {"t": kind}


def _gold(st: dict, base: int) -> int:
    return int(base * GOLD_SCALE * (HARD_GOLD if st["hard"] else 1.0))


def _drop(rng: random.Random) -> str:
    return rng.choices([k for k, _ in LOOT_TABLE], [w for _, w in LOOT_TABLE])[0]


def _heal(st: dict, pct: float) -> int:
    gain = min(st["mhp"] - st["hp"], int(st["mhp"] * pct))
    st["hp"] += gain
    return gain


def _hurt_safe(st: dict, pct: float) -> int:
    """Non-lethal environmental damage (traps / mimics never kill; only fights do)."""
    dmg = max(1, min(int(st["mhp"] * pct), st["hp"] - 1))
    st["hp"] -= dmg
    return dmg


def _finish_room(st: dict, note: str) -> None:
    st["last"] = st["room"]["t"] if st["room"] else None
    st["room"], st["note"] = None, note


def _exchange(st: dict, strat: str, rng: random.Random) -> tuple[str, str]:
    """One 6-round exchange vs the room's monster. -> (outcome: 'win'|'dead'|'go_on', note)"""
    room = st["room"]
    _, sa, sd = STRATEGIES[strat]
    p = Fighter(st["uid"], "You", st["hp"], st["atk"] * (1 + st["buff"]) * sa, st["dfn"] * sd,
                st["power"], st["pm"], st["charges"])
    m = Fighter(-1, room["name"], room["hp"], room["atk"], room["def"])
    res = resolve(p, m, rng)
    st["hp"], room["hp"], st["charges"] = max(0, p.hp), max(0, m.hp), p.charges
    st["consumed"] = sorted(set(st["consumed"]) | p.consumed)
    dealt, taken = res.dealt[st["uid"]], res.dealt[-1]
    notes = [ln for ln in res.log if any(t in ln for t in ("CRIT", "phases", "rises", "reflects"))][:3]
    head = f"{STRATEGIES[strat][0]} — you dealt <b>{dealt}</b>, took <b>{taken}</b>."
    extra = ("\n" + "\n".join(notes)) if notes else ""
    if st["hp"] <= 0:
        return "dead", f"{head}{extra}\n💀 <b>{room['name']}</b> was too strong…"
    if room["hp"] <= 0:
        gold = _gold(st, _rng_int(rng, 120, 180) if room["boss"] else _rng_int(rng, 22, 42) + st["floor"] * 7)
        xp = 40 if room["boss"] else 6 + st["floor"] * 3
        st["gold"] += gold
        st["xp"] += xp
        drop = ""
        if room["boss"] or rng.random() < 0.12:
            key = _drop(rng)
            if room["boss"] and st["hard"] and rng.random() < 0.02:
                key = "golden_idol"
            st["loot"].append(key)
            drop = f" and found {_loot_name(key)}"
        return "win", f"{head}{extra}\n🏆 <b>{room['name']}</b> defeated! +{gold} 💎{drop}."
    return "go_on", f"{head}{extra}\n⚔️ Neither of you has fallen yet — the fight continues!"


def _rng_int(rng: random.Random, a: int, b: int) -> int:
    return rng.randint(a, b)


def _loot_name(key: str) -> str:
    from game.rules import ITEMS
    it = ITEMS.get(key)
    return f"{it.emoji} {it.name}" if it else key


def _act_room(st: dict, action: str, rng: random.Random) -> tuple[str, str]:
    """Apply a player's choice inside the current room. -> (state tag, note). Tags: ok | dead | stay."""
    room, t = st["room"], st["room"]["t"]
    if t == "monster":
        if action in ("fa", "fb", "fd"):
            strat = {"fa": "a", "fb": "b", "fd": "d"}[action]
            out, note = _exchange(st, strat, rng)
            if out == "dead":
                return "dead", note
            if out == "win":
                _finish_room(st, note)
                return "ok", note
            room["sneak"] = False
            st["note"] = note
            return "stay", note
        if action == "sn" and room.get("sneak"):
            if rng.random() < 0.55:
                _finish_room(st, "🥷 You slipped past the monster unnoticed.")
                return "ok", st["note"]
            dmg = max(1, int((room["atk"] - 0.6 * st["dfn"]) * 1.2))
            st["hp"] -= dmg
            room["sneak"] = False
            if st["hp"] <= 0:
                st["hp"] = 0
                return "dead", f"🥷 You were spotted and took a deadly blow of <b>{dmg}</b>!"
            st["note"] = f"🥷 Spotted! The monster hits you for <b>{dmg}</b> as you stumble back. Fight!"
            return "stay", st["note"]
    elif t == "chest":
        if action == "open":
            if rng.random() < 0.78:
                g = _gold(st, rng.randint(35, 95) + st["floor"] * 8)
                st["gold"] += g
                _finish_room(st, f"📦 The chest held <b>{g}</b> 💎!")
            else:
                d = _hurt_safe(st, 0.14)
                _finish_room(st, f"👅 It was a MIMIC! It bites you for <b>{d}</b> and scuttles off.")
            return "ok", st["note"]
        if action == "leave":
            _finish_room(st, "🚶 You leave the chest alone. Wise… or boring.")
            return "ok", st["note"]
    elif t == "shrine":
        if action == "pray":
            g = _heal(st, 0.30)
            _finish_room(st, f"🙏 Warm light washes over you. +<b>{g}</b> HP.")
            return "ok", st["note"]
        if action == "offer" and st["gold"] >= 40:
            st["gold"] -= 40
            st["buff"] += 0.12
            g = _heal(st, 0.10)
            _finish_room(st, f"💰 The shrine accepts your offering: <b>+12% attack</b> for the run, +{g} HP.")
            return "ok", st["note"]
    elif t == "trap":
        if action == "disarm":
            if rng.random() < 0.55:
                g = _gold(st, rng.randint(30, 70))
                st["gold"] += g
                _finish_room(st, f"🧠 Click… disarmed! The mechanism hid <b>{g}</b> 💎.")
            else:
                d = _hurt_safe(st, 0.12)
                _finish_room(st, f"💥 You fumbled it! Darts hit you for <b>{d}</b>.")
            return "ok", st["note"]
        if action == "rush":
            d = _hurt_safe(st, 0.07)
            _finish_room(st, f"🏃 You dash through, scraped for <b>{d}</b> damage.")
            return "ok", st["note"]
    elif t == "merchant":
        if action == "potion" and st["gold"] >= 45:
            st["gold"] -= 45
            g = _heal(st, 0.35)
            _finish_room(st, f"🧪 Bought a tonic for 45 💎. +<b>{g}</b> HP.")
            return "ok", st["note"]
        if action == "stone" and st["gold"] >= 70:
            st["gold"] -= 70
            st["buff"] += 0.10
            _finish_room(st, "🗡 Bought a whetstone for 70 💎. <b>+10% attack</b> for the run.")
            return "ok", st["note"]
        if action == "leave":
            _finish_room(st, "🚶 You nod to the merchant and move on.")
            return "ok", st["note"]
    elif t == "camp":
        if action == "rest":
            g = _heal(st, 0.28)
            _finish_room(st, f"🔥 You rest by the fire. +<b>{g}</b> HP.")
            return "ok", st["note"]
        if action == "sharpen":
            st["buff"] += 0.08
            _finish_room(st, "🪨 You sharpen your weapon. <b>+8% attack</b> for the run.")
            return "ok", st["note"]
    raise GameError("❌ That option isn't available right now.")


async def _settle_run(s, uid: int, st: dict, how: str) -> dict:
    """Pay out and delete the run (same transaction => exactly once). how: clear | retreat | death | abandon"""
    gold, xp, loot = st["gold"], st["xp"], list(st["loot"])
    bonus = 0
    if how == "clear":
        bonus = CLEAR_BONUS[st["hard"]]
        xp += CLEAR_XP[st["hard"]]
    elif how in ("death", "abandon"):
        gold, xp, loot = gold // 2, xp // 2, []
    payout = gold + bonus
    if payout:
        await economy.apply(s, uid, wallet=payout, kind="dungeon", ref=f"dg:{uid}:{st['id']}")
        await progress.emit(s, uid, "earn", payout)
    if xp:
        await progress.add_xp(s, uid, xp)
    for key in loot:
        await pets._grant(s, uid, key)
    shim = Fighter(uid, "", 1, 1.0, 1.0)
    shim.consumed = set(st["consumed"])
    await combat.persist_consumed(s, shim)
    pet_levels = 0
    unlocked: list[str] = []
    if how == "clear":
        pet = await s.get(Pet, uid)
        if pet:
            pet_levels = pets._gain(pet, 40)
        await progress.emit(s, uid, "dungeon_clear")
        unlocked += await bump(s, uid, "dungeon_clear")
        if st["hard"]:
            unlocked += await bump(s, uid, "dungeon_hard")
    unlocked += await sweep(s, uid)
    await s.execute(delete(DungeonRun).where(DungeonRun.user_id == uid))
    return {"how": how, "gold": gold, "bonus": bonus, "payout": payout, "xp": xp, "loot": loot,
            "pet_levels": pet_levels, "unlocked": unlocked, "hard": st["hard"], "floor": st["floor"]}


async def dungeon_get(uid: int) -> dict | None:
    async with db.tx() as s:
        run = await s.get(DungeonRun, uid)
        return json.loads(run.state) if run else None


async def dungeon_enter(uid: int, hard: bool, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    async with db.tx() as s:
        u = await users.require(s, uid)
        await economy._jail_check(s, uid)
        run = await s.get(DungeonRun, uid)
        if run is not None:
            if (utcnow() - aware(run.updated_at)).total_seconds() < STALE_RUN_S:
                raise GameError("🗝 You're already inside the dungeon! Use /dungeon to continue your run.")
            await _settle_run(s, uid, json.loads(run.state), "abandon")   # forfeited run: half rewards
        if hard and u.level < 5:
            raise GameError("☠️ Hard mode unlocks at <b>level 5</b>. Try Normal first!")
        rem = await cooldowns.claim(s, uid, "dungeon", DUNGEON_CD_S)
        if rem:
            raise OnCooldown(rem, "The dungeon")
        f = await combat.load_fighter(s, u)
        st = {"id": secrets.token_hex(4), "uid": uid, "hard": hard, "floor": 1, "hp": f.hp, "mhp": f.hp,
              "atk": f.atk, "dfn": f.dfn, "pm": f.power_mult, "power": f.power, "charges": f.charges,
              "consumed": [], "base": [R.base_hp + u.level * R.hp_per_level, R.base_attack + u.level * R.attack_per_level,
                                       R.base_defence + u.level * R.defence_per_level],
              "buff": 0.0, "gold": 0, "xp": 0, "loot": [], "room": None, "last": None,
              "note": "The gate groans shut behind you. There is no turning back… except by cashing out between floors."}
        st["room"] = _gen_room(st, rng)
        s.add(DungeonRun(user_id=uid, state=json.dumps(st), updated_at=utcnow()))
        return st


async def dungeon_act(uid: int, action: str, rng: random.Random | None = None) -> tuple[dict, dict | None]:
    """Apply one button press. -> (state, settlement-or-None). Row-locked: concurrent taps are serialised."""
    rng = rng or random.Random()
    async with db.tx() as s:
        run = (await s.execute(select(DungeonRun).where(DungeonRun.user_id == uid).with_for_update())).scalar_one_or_none()
        if run is None:
            raise GameError("🗝 You're not in a dungeon. Start one with /dungeon.")
        st = json.loads(run.state)
        result: dict | None = None
        if st["room"] is None:                         # between floors
            if action == "cash":
                result = await _settle_run(s, uid, st, "retreat")
            elif action == "go":
                if st["floor"] >= DUNGEON_FLOORS:
                    raise GameError("❌ Nothing deeper than the boss.")
                st["floor"] += 1
                st["room"] = _gen_room(st, rng)
                st["note"] = f"You descend to floor <b>{st['floor']}</b>…"
            else:
                raise GameError("❌ That option isn't available right now.")
        else:
            tag, note = _act_room(st, action, rng)
            if tag == "dead":
                st["note"] = note
                result = await _settle_run(s, uid, st, "death")
                result["last_note"] = note
            elif tag == "ok" and st["floor"] >= DUNGEON_FLOORS and st["room"] is None:
                result = await _settle_run(s, uid, st, "clear")     # boss defeated
                result["last_note"] = note
        if result is None:
            run.state, run.updated_at = json.dumps(st), utcnow()
        return st, result


# =============================================================== heists (co-op, groups)
HEIST_CD_S = 3 * 3600
HEIST_GROUP_CD_S = 20 * 60
HEIST_MIN, HEIST_MAX, HEIST_LOBBY_S = 2, 6, 75
HEIST_ROLES = {"muscle": ("💪", "Muscle"), "hacker": ("💻", "Hacker"), "lookout": ("🔭", "Lookout"),
               "driver": ("🚗", "Driver")}
HEIST_STAGES = [("🚪", "Break through the vault door", "muscle"), ("🔐", "Crack the security system", "hacker"),
                ("👮", "Slip past the guards", "lookout"), ("🏎", "Make the getaway", "driver")]
HEIST_TARGETS = ["the Royal Mint", "Baron Voss's mansion", "the Skyport Vault", "the Obsidian Museum",
                 "the Crimson Casino cellar", "the Merchant Guild bank"]
HEIST_TARGETS += content.HEIST_TARGETS_MORE


def heist_simulate(crew: dict[int, tuple[str, int]], rng: random.Random | None = None) -> list[tuple[str, str, bool, float]]:
    """crew: uid -> (role, level). -> [(emoji, text, success, chance)] — every role matters."""
    rng = rng or random.Random()
    avg_level = sum(lv for _, lv in crew.values()) / max(1, len(crew))
    bonus = min(0.10, avg_level * 0.004)
    out = []
    for emoji, text, role in HEIST_STAGES:
        n = sum(1 for r, _ in crew.values() if r == role)
        chance = 0.40 + (0.33 if n >= 1 else 0) + (0.06 if n >= 2 else 0) + bonus
        chance = min(0.93, chance)
        out.append((emoji, text, rng.random() < chance, chance))
    return out


def heist_payout(passed: int, rng: random.Random | None = None) -> int:
    rng = rng or random.Random()
    pay = sum(rng.randint(55, 105) for _ in range(passed))
    return pay + (90 if passed == len(HEIST_STAGES) else 0)


async def heist_check(uid: int) -> User:
    """Raises GameError if this player can't join a heist right now."""
    async with db.tx() as s:
        u = await users.require(s, uid)
        if (utcnow() - aware(u.created_at)).total_seconds() < R.pay_sender_min_age_s:
            raise GameError("🎭 Heist crews only take accounts that are at least 24 hours old.")
        await economy._jail_check(s, uid)
        rem = await cooldowns.remaining(s, uid, "heist")
        if rem:
            raise OnCooldown(rem, "Your next heist")
        return u


async def heist_lock_in(uid: int) -> int:
    """Start the personal cooldown right before the job. Returns 0 on success, else seconds remaining."""
    async with db.tx() as s:
        return await cooldowns.claim(s, uid, "heist", HEIST_CD_S)


async def heist_release(uid: int) -> None:
    async with db.tx() as s:
        await cooldowns.clear(s, uid, "heist")


async def heist_pay(uid: int, token: str, passed: int, payout: int) -> list[str]:
    unlocked: list[str] = []
    async with db.tx() as s:
        if payout:
            await economy.apply(s, uid, wallet=payout, kind="heist", ref=f"heist:{token}:{uid}")
            await progress.emit(s, uid, "earn", payout)
        if passed >= 2:
            await progress.emit(s, uid, "heist_win")
            unlocked += await bump(s, uid, "heist_win")
        if passed == len(HEIST_STAGES):
            unlocked += await bump(s, uid, "heist_perfect")
        unlocked += await sweep(s, uid)
        await progress.add_xp(s, uid, 10 + 8 * passed)
    return unlocked
