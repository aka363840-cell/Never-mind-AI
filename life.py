"""Life-sim layer on top of marriage: love, dates, gifts, children, family bonus and houses.
All diamond movement goes through economy.apply(); cooldowns through cooldowns.claim()."""
from __future__ import annotations

import html
import random

from sqlalchemy import delete, func, or_, select

from database.models import Child, CoupleLove, Marriage, Property, User, aware, utcnow
from database.session import db
from services import cooldowns, economy, extras, progress, users
from services.errors import GameError, OnCooldown
from utils import content

_rng = random.SystemRandom()

DATE_COST, DATE_CD_S = 100, 4 * 3600
BABY_COST, BABY_CD_S, BABY_MIN_LOVE = 1000, 12 * 3600, 50
MAX_KIDS = 6
FAMILY_BONUS_CD_S = 24 * 3600
RENT_CD_S = 24 * 3600

GIFTS = {   # key: (emoji, name, price, love)
    "flowers": ("💐", "Flowers", 50, 5),
    "chocolate": ("🍫", "Chocolates", 120, 12),
    "teddy": ("🧸", "Teddy bear", 300, 32),
    "perfume": ("🧴", "Perfume", 600, 70),
    "jewellery": ("💎", "Jewellery", 1500, 180),
}
HOUSES = {  # key: (emoji, name, price, rent per day)  (~2% per day)
    "tent": ("⛺", "Tent", 2_000, 40),
    "flat": ("🏢", "Apartment", 15_000, 300),
    "villa": ("🏡", "Villa", 80_000, 1_600),
    "mansion": ("🏰", "Mansion", 300_000, 6_000),
    "palace": ("👑", "Palace", 1_000_000, 20_000),
}
DATE_EVENTS = ["You had a candle-light dinner 🕯", "You watched a movie together 🎬", "You went for a long walk at sunset 🌇",
               "You shared ice-cream by the river 🍦", "You danced in the rain 🌧", "You went on a surprise road trip 🚗",
               "You cooked together and made a mess 🍳"]
DATE_EVENTS += content.DATE_MORE
BOY = ["Aarav", "Vihaan", "Reyansh", "Arjun", "Kabir", "Ishaan", "Ayaan", "Krish", "Rohan", "Dev"]
GIRL = ["Aadhya", "Anaya", "Diya", "Myra", "Saanvi", "Kiara", "Riya", "Meera", "Anvi", "Ira"]


def love_title(love: int) -> str:
    for lim, t in ((1500, "Eternal Soulmates 💫"), (800, "Madly in Love 😍"), (400, "Deeply in Love ❤️"),
                   (150, "Sweethearts 💕"), (50, "Getting Closer 💗"), (0, "Newlyweds 🌱")):
        if love >= lim:
            return t
    return "Newlyweds 🌱"


async def _marriage(s, uid: int) -> Marriage:
    m = await extras.partner_of(s, uid)
    if not m:
        raise GameError("💍 You need to be married for that. Propose with /marry (reply to someone).")
    return m


async def _add_love(s, a: int, amount: int) -> int:
    row = await s.get(CoupleLove, a)
    if row is None:
        row = CoupleLove(user_a=a, love=0)
        s.add(row)
    row.love = (row.love or 0) + amount
    return row.love


async def _love(s, a: int) -> int:
    row = await s.get(CoupleLove, a)
    return row.love if row else 0


# ---------------------------------------------------------------- love
async def go_on_date(uid: int) -> tuple[str, int, int, int]:
    """-> (event text, love gained, total love, partner id)"""
    async with db.tx() as s:
        m = await _marriage(s, uid)
        rem = await cooldowns.claim(s, uid, "date", DATE_CD_S)
        if rem:
            raise OnCooldown(rem, "Your next date")
        await economy.apply(s, uid, wallet=-DATE_COST, kind="date")
        gain = _rng.randint(8, 22)
        total = await _add_love(s, m.user_a, gain)
        partner = m.user_b if m.user_a == uid else m.user_a
        return _rng.choice(DATE_EVENTS), gain, total, partner


async def send_gift(uid: int, key: str) -> tuple[str, int, int, int]:
    if key not in GIFTS:
        raise GameError("🎁 Unknown gift. See /gifts")
    emoji, name, price, love = GIFTS[key]
    async with db.tx() as s:
        m = await _marriage(s, uid)
        await economy.apply(s, uid, wallet=-price, kind="gift_partner")
        total = await _add_love(s, m.user_a, love)
        partner = m.user_b if m.user_a == uid else m.user_a
        return f"{emoji} {name}", love, total, partner


# ---------------------------------------------------------------- children
def _kid_name(gender: str, wanted: str | None) -> str:
    n = (wanted or "").strip()[:20]
    return n.title() if n else _rng.choice(BOY if gender == "b" else GIRL)


async def _kids_count(s, uid: int, partner: int | None) -> int:
    q = select(func.count()).select_from(Child).where(or_(Child.parent_a == uid, Child.parent_b == uid))
    if partner:
        q = select(func.count()).select_from(Child).where(or_(Child.parent_a.in_((uid, partner)), Child.parent_b.in_((uid, partner))))
    return (await s.execute(q)).scalar() or 0


async def have_baby(uid: int, wanted_name: str | None) -> tuple[bool, str, str]:
    """-> (born?, name, gender). A failed try still costs a cooldown but not diamonds."""
    async with db.tx() as s:
        m = await _marriage(s, uid)
        partner = m.user_b if m.user_a == uid else m.user_a
        if await _love(s, m.user_a) < BABY_MIN_LOVE:
            raise GameError(f"💞 Your love must be at least <b>{BABY_MIN_LOVE}</b> first. Use /date and /gift to grow it.")
        if await _kids_count(s, uid, partner) >= MAX_KIDS:
            raise GameError(f"👨‍👩‍👧‍👦 Your family is full ({MAX_KIDS} children max).")
        if (await economy.wallet_of(s, uid)).balance < BABY_COST:
            raise GameError(f"❌ You need <b>{BABY_COST:,}</b> 💎 in your wallet to try for a baby.")
        rem = await cooldowns.claim(s, m.user_a, "baby", BABY_CD_S)
        if rem:
            raise OnCooldown(rem, "Trying for a baby")
        if _rng.random() > 0.7:
            return False, "", ""
        await economy.apply(s, uid, wallet=-BABY_COST, kind="baby")
        g = _rng.choice("bg")
        name = _kid_name(g, wanted_name)
        s.add(Child(parent_a=m.user_a, parent_b=m.user_b, name=name, gender=g, adopted=False, born_at=utcnow()))
        await _add_love(s, m.user_a, 15)
        return True, name, g


async def purge_random_adopted() -> tuple[int, int]:
    """Owner maintenance: delete every ADOPTED child that carries one of the old auto-generated names (BOY / GIRL pools),
    for all players. Born children and custom-named adoptions are untouched. -> (children removed, parents affected)"""
    pool = {n.lower() for n in BOY + GIRL}
    async with db.tx() as s:
        rows = (await s.execute(select(Child).where(Child.adopted.is_(True)))).scalars().all()
        victims = [c for c in rows if (c.name or "").strip().lower() in pool]
        parents = {p for c in victims for p in (c.parent_a, c.parent_b) if p}
        for c in victims:
            await s.delete(c)
        return len(victims), len(parents)


async def disown(uid: int, name: str) -> str:
    async with db.tx() as s:
        c = (await s.execute(select(Child).where(or_(Child.parent_a == uid, Child.parent_b == uid),
                                                 func.lower(Child.name) == name.strip().lower()))).scalars().first()
        if not c:
            raise GameError("❌ You have no child with that name. See /family")
        await s.delete(c)
        return c.name


async def family_bonus(uid: int) -> int:
    async with db.tx() as s:
        m = await extras.partner_of(s, uid)
        partner = (m.user_b if m.user_a == uid else m.user_a) if m else None
        kids = await _kids_count(s, uid, partner)
        love = await _love(s, m.user_a) if m else 0
        from services import family
        extra = await family.bonus_part(s, uid)
        if not kids and not m and not extra:
            raise GameError("👨‍👩‍👧 You have no family yet. Try /marry, /baby, /adopt or /addfamily (reply to someone).")
        amount = kids * 60 + (40 + love // 10 if m else 0) + extra
        rem = await cooldowns.claim(s, uid, "familybonus", FAMILY_BONUS_CD_S)
        if rem:
            raise OnCooldown(rem, "Your family bonus")
        await economy.apply(s, uid, wallet=amount, kind="family_bonus")
        await progress.emit(s, uid, "earn", amount)
        return amount


async def family_view(uid: int) -> dict:
    async with db.tx() as s:
        m = await extras.partner_of(s, uid)
        partner = None
        love = 0
        days = 0
        if m:
            pid = m.user_b if m.user_a == uid else m.user_a
            pname = (await s.execute(select(User.first_name).where(User.id == pid))).scalar() or "Partner"
            partner = (pid, pname)
            love = await _love(s, m.user_a)
            days = (utcnow() - aware(m.married_at)).days
        ids = (uid, partner[0]) if partner else (uid,)
        kids = (await s.execute(select(Child).where(or_(Child.parent_a.in_(ids), Child.parent_b.in_(ids))).order_by(Child.born_at))).scalars().all()
        kid_rows = [(c.name, c.gender, c.adopted, (utcnow() - aware(c.born_at)).days) for c in kids]
        houses = [r for r in (await s.execute(select(Property.key).where(Property.user_id == uid))).scalars()]
        return dict(partner=partner, love=love, days=days, kids=kid_rows, houses=houses)


# ---------------------------------------------------------------- houses
async def buy_house(uid: int, key: str) -> tuple[str, int]:
    if key not in HOUSES:
        raise GameError("🏠 Unknown property. See /houses")
    emoji, name, price, _ = HOUSES[key]
    async with db.tx() as s:
        await users.lock(s, uid)
        if await s.get(Property, (uid, key)):
            raise GameError(f"🏠 You already own a {name}.")
        await economy.apply(s, uid, wallet=-price, kind="house", meta={"house": key})
        s.add(Property(user_id=uid, key=key, bought_at=utcnow()))
        return f"{emoji} {name}", price


async def sell_house(uid: int, key: str) -> tuple[str, int]:
    if key not in HOUSES:
        raise GameError("🏠 Unknown property. See /houses")
    emoji, name, price, _ = HOUSES[key]
    refund = price * 6 // 10
    async with db.tx() as s:
        await users.lock(s, uid)
        row = await s.get(Property, (uid, key))
        if not row:
            raise GameError(f"🏠 You don't own a {name}.")
        await s.delete(row)
        await economy.apply(s, uid, wallet=refund, kind="house_sale", meta={"house": key})
        return f"{emoji} {name}", refund


def resolve_house(raw: str) -> str:
    """'flat' / 'Apartment' / 'apartment' -> 'flat'. Raises GameError for unknown names."""
    w = (raw or "").strip().lower().lstrip("/")
    if w in HOUSES:
        return w
    for k, (_e, name, _p, _r) in HOUSES.items():
        if w == name.lower():
            return k
    names = ", ".join(f"<code>{k}</code>" for k in HOUSES)
    raise GameError(f"🏠 Unknown property <b>{html.escape(w[:30])}</b>. Choose from: {names}")


async def _rent_left(s, uid: int, key: str, legacy: int) -> int:
    """Seconds until this property's rent is ready (honours the old single 'rent' cooldown)."""
    return max(legacy, await cooldowns.remaining(s, uid, f"rent:{key}"))


async def rent_waiting(uid: int) -> list[tuple[str, int]]:
    """[(label, seconds)] for owned properties whose rent is still cooling down (soonest first)."""
    async with db.tx() as s:
        owned = [k for k in (await s.execute(select(Property.key).where(Property.user_id == uid))).scalars() if k in HOUSES]
        legacy = await cooldowns.remaining(s, uid, "rent")
        out = [(f"{HOUSES[k][0]} {HOUSES[k][1]}", await _rent_left(s, uid, k, legacy)) for k in owned]
    return sorted([x for x in out if x[1] > 0], key=lambda x: x[1])


async def collect_rent(uid: int, which: str | None = None) -> tuple[int, list[str]]:
    """Collect rent. No argument -> every property whose rent is ready. With a property name -> only that one.
    Each property has its own 24h timer. -> (total, ["🏢 Apartment", ...])"""
    async with db.tx() as s:
        owned = list((await s.execute(select(Property.key).where(Property.user_id == uid))).scalars())
        owned = [k for k in owned if k in HOUSES]
        if not owned:
            raise GameError("🏠 You own no property yet. Buy one from /houses")
        legacy = await cooldowns.remaining(s, uid, "rent")   # cooldown started before per-property timers existed
        if which and which.strip():
            k = resolve_house(which)
            if k not in owned:
                raise GameError(f"🏠 You don't own a {HOUSES[k][1]}. See /houses")
            targets, what = [k], f"{HOUSES[k][1]} rent"
        else:
            targets, what = owned, "Rent collection"
        ready, waits = [], {}
        for k in targets:
            left = await _rent_left(s, uid, k, legacy)
            if left:
                waits[k] = left
            else:
                ready.append(k)
        if not ready:
            if which and which.strip():      # one named property: say exactly which one and when it pays again
                e, n = HOUSES[targets[0]][0], HOUSES[targets[0]][1]
                raise OnCooldown(waits[targets[0]], f"{e} {n} rent")
            from utils.fmt import dur
            rows = "\n".join(f"{HOUSES[k][0]} {HOUSES[k][1]} — <b>{dur(t)}</b>" for k, t in sorted(waits.items(), key=lambda x: x[1]))
            raise GameError("⏳ All your rent is already collected. Next payouts:\n" + rows)
        for k in ready:
            if await cooldowns.claim(s, uid, f"rent:{k}", RENT_CD_S):
                raise OnCooldown(1, what)   # lost a race with a concurrent /rent
        total = sum(HOUSES[k][3] for k in ready)
        await economy.apply(s, uid, wallet=total, kind="rent")
        await progress.emit(s, uid, "earn", total)
        return total, [f"{HOUSES[k][0]} {HOUSES[k][1]}" for k in ready]
