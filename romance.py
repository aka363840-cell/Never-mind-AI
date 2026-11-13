"""Romance layer on top of marriage: engagement, wedding ceremony, anniversary.

* /engage    - both sides accept; the ring (a sink) is paid by the proposer when the other side accepts; breaking costs a small penalty.
* /wedding   - an engaged couple (or a couple that married with /marry and never had a ceremony) picks a venue; the venue price is
               split 50/50, both sides confirm, a wedding card is issued and a one-time bonus is paid. Every bonus is smaller than what
               the couple paid, so weddings stay a net money sink.
* /anniversary - days together, one-time milestone rewards (30 days, 100 days, 1 year), plus a yearly bonus paid by a background loop.

Diamonds only move through economy.apply() with unique refs; one-time rewards are guarded by a unique couple_flags row, which is never
deleted on divorce (divorcing and re-marrying can't re-earn a reward). /marry and /divorce are untouched: marriage state stays in `marriages`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select

from database.models import CoupleFlag, Engagement, Marriage, User, Wedding, aware, utcnow
from database.session import db, insert_ignore
from game.rules import R
from services import economy, extras, family, progress, users
from services.errors import GameError
from utils.content import romance as texts
from utils.fmt import dur, esc, mention, num

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- helpers
def pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def other_of(row, uid: int) -> int:
    """The other side of an Engagement / Marriage / Wedding row."""
    return row.user_b if row.user_a == uid else row.user_a


async def _name(s, uid: int) -> str:
    return (await s.execute(select(User.first_name).where(User.id == uid))).scalar() or "Player"


async def _flag(s, lo: int, hi: int, key: str) -> bool:
    """True exactly once per (couple, key) - the caller that gets True pays the reward."""
    return await insert_ignore(s, CoupleFlag, dict(user_a=lo, user_b=hi, key=key, created_at=utcnow()))


def add_years(d: datetime, n: int) -> datetime:
    try:
        return d.replace(year=d.year + n)
    except ValueError:                                   # 29 Feb -> 1 Mar in non-leap years
        return d.replace(year=d.year + n, month=3, day=1)


def full_years(start: datetime, now: datetime) -> int:
    n = now.year - start.year - ((now.month, now.day) < (start.month, start.day))
    return max(0, n)


def venue_of(key: str) -> tuple | None:
    key = (key or "").strip().lower()
    return next((v for v in R.wedding_venues if v[0] == key), None)


async def _too_young(s, uid: int, who: str) -> None:
    """Same anti multi-account rule as /pay: brand-new accounts can't get engaged."""
    u = await users.require(s, uid)
    left = R.pay_sender_min_age_s - (utcnow() - aware(u.created_at)).total_seconds()
    if left > 0:
        raise GameError(f"🆕 {who} {'are' if who == 'You' else 'is'} too new to get engaged - try again in <b>{dur(int(left))}</b>.")


# ---------------------------------------------------------------- engagement
async def engagement_of(s, uid: int) -> Engagement | None:
    """The player's engagement. A row whose members have since married (each other with /marry, or anyone else) is stale and removed."""
    e = (await s.execute(select(Engagement).where(or_(Engagement.user_a == uid, Engagement.user_b == uid)))).scalars().first()
    if e is None:
        return None
    if await extras.partner_of(s, e.user_a) or await extras.partner_of(s, e.user_b):
        await s.delete(e)
        await s.flush()
        return None
    return e


async def _check_engage(s, a: int, b: int, final: bool) -> None:
    await users.require(s, a)
    await users.require(s, b)
    await _too_young(s, a, "You")
    await _too_young(s, b, "That player")
    if await extras.partner_of(s, a):
        raise GameError("💍 You are already married! Use /divorce first.")
    if await extras.partner_of(s, b):
        raise GameError("💔 That person is already married.")
    if await engagement_of(s, a):
        raise GameError("💍 You are already engaged! See /engage")
    if await engagement_of(s, b):
        raise GameError("💔 That person is already engaged.")
    if await family.blocked_for_marriage(s, a, b):
        raise GameError("🙅 You two are family — you can't get engaged! (/unfamily first if it was a mistake)")
    if (await economy.wallet_of(s, a)).balance < R.engage_ring_cost:
        if final:                                        # the accepter must not be told "you don't have enough"
            raise GameError(f"💔 The proposal fell through — the proposer can no longer afford the {num(R.engage_ring_cost)} 💎 ring.")
        raise GameError(f"💍 The ring costs <b>{num(R.engage_ring_cost)}</b> 💎 and you don't have enough in your wallet yet.")


async def can_engage(a: int, b: int) -> None:
    if a == b:
        raise GameError("❌ You can't get engaged to yourself 😅")
    async with db.tx() as s:
        await _check_engage(s, a, b, final=False)


async def engage(a: int, b: int, ref: str) -> None:
    """Called when b accepts a's proposal. a pays the ring."""
    if a == b:
        raise GameError("❌ You can't get engaged to yourself 😅")
    async with db.tx() as s:
        await users.lock(s, a, b)
        await _check_engage(s, a, b, final=True)
        await economy.apply(s, a, wallet=-R.engage_ring_cost, kind="engage_ring", counterparty=b, ref=ref)
        lo, hi = pair(a, b)
        s.add(Engagement(user_a=lo, user_b=hi, ring_cost=R.engage_ring_cost, engaged_at=utcnow()))


async def engagement_view(uid: int) -> tuple[int, str, int] | None:
    """(fiancé(e) id, name, days engaged) or None."""
    async with db.tx() as s:
        e = await engagement_of(s, uid)
        if not e:
            return None
        other = other_of(e, uid)
        return other, await _name(s, other), (utcnow() - aware(e.engaged_at)).days


async def break_engagement(uid: int) -> tuple[int, int]:
    """-> (the other player's id, penalty paid). The penalty is a sink (at most what the breaker holds, so nobody is trapped); the ring is not refunded."""
    async with db.tx() as s:
        e = await engagement_of(s, uid)
        if not e:
            raise GameError("💍 You are not engaged.")
        other = other_of(e, uid)
        await users.lock(s, uid, other)
        e = await engagement_of(s, uid)                  # re-check under the lock
        if not e:
            raise GameError("💍 You are not engaged.")
        w = await economy.wallet_of(s, uid, lock=True)
        pay = min(R.engage_break_penalty, w.balance)
        if pay > 0:
            await economy.apply(s, uid, wallet=-pay, kind="engage_break", counterparty=other,
                                ref=f"engbreak:{e.user_a}:{e.user_b}:{int(aware(e.engaged_at).timestamp())}")
        await s.delete(e)
        return other, pay


# ---------------------------------------------------------------- wedding
async def wedding_of(s, m: Marriage) -> Wedding | None:
    """The ceremony of THIS marriage (a ceremony older than the marriage belongs to an earlier marriage)."""
    w = await s.get(Wedding, m.user_a)
    if w is not None and w.user_b == m.user_b and aware(w.held_at) >= aware(m.married_at):
        return w
    return None


async def _wedding_context(s, uid: int) -> tuple[str, int]:
    """-> ('engaged' | 'ceremony', the other side)."""
    m = await extras.partner_of(s, uid)
    if m:
        if await wedding_of(s, m):
            raise GameError("💒 You two already had your wedding ceremony! See /anniversary")
        return "ceremony", other_of(m, uid)
    e = await engagement_of(s, uid)
    if e:
        return "engaged", other_of(e, uid)
    raise GameError("💍 You need to be engaged first: reply to someone with /engage")


async def wedding_options(uid: int) -> tuple[int, str]:
    """Checks that uid may plan a wedding now. -> (partner id, partner name)."""
    async with db.tx() as s:
        _, other = await _wedding_context(s, uid)
        return other, await _name(s, other)


async def can_wed(uid: int, venue_key: str) -> tuple[int, str]:
    """Pre-check for one venue (no money moves): both wallets must cover their half. -> (partner id, partner name)."""
    v = venue_of(venue_key)
    if v is None:
        raise GameError("🏛 Unknown venue. Pick one of the buttons.")
    half = v[3] // 2
    async with db.tx() as s:
        _, other = await _wedding_context(s, uid)
        if (await economy.wallet_of(s, uid)).balance < half:
            raise GameError(f"💸 Your half of the <b>{esc(v[2])}</b> is <b>{num(half)}</b> 💎 and you don't have that much yet.")
        if (await economy.wallet_of(s, other)).balance < half:
            raise GameError(f"💸 Your partner can't afford their half of the <b>{esc(v[2])}</b> ({num(half)} 💎) yet. Try a cheaper venue.")
        return other, await _name(s, other)


async def hold_wedding(a: int, b: int, venue_key: str, ref: str) -> dict:
    """Both sides have agreed (a chose the venue, b pressed accept). Charges half each, marries an engaged couple, pays the once-only bonus."""
    v = venue_of(venue_key)
    if v is None:
        raise GameError("🏛 Unknown venue.")
    key, _emoji, _vname, cost, bonus = v
    half = cost // 2
    async with db.tx() as s:
        await users.lock(s, a, b)
        mode, other = await _wedding_context(s, a)
        if other != b:
            raise GameError("💔 The wedding plans changed — start again with /wedding.")
        if mode == "engaged" and await family.blocked_for_marriage(s, a, b):
            raise GameError("🙅 You two are family — you can't marry each other!")
        for uid in (a, b):
            if (await economy.wallet_of(s, uid)).balance < half:
                raise GameError("💸 The wedding fell through — one of you can no longer afford their half of the venue.")
        for uid, cp in ((a, b), (b, a)):
            await economy.apply(s, uid, wallet=-half, kind="wedding_venue", counterparty=cp, ref=f"{ref}:{uid}")
        lo, hi = pair(a, b)
        now = utcnow()
        if mode == "engaged":
            e = await engagement_of(s, a)
            if e is not None:
                await s.delete(e)
                await s.flush()
            s.add(Marriage(user_a=lo, user_b=hi, married_at=now))
        await s.execute(delete(Wedding).where(or_(Wedding.user_a.in_((lo, hi)), Wedding.user_b.in_((lo, hi)))))
        s.add(Wedding(user_a=lo, user_b=hi, venue=key, cost=cost, held_at=now))
        paid = 0
        if await _flag(s, lo, hi, "wedding"):            # once per couple, ever
            for uid, cp in ((a, b), (b, a)):
                await economy.apply(s, uid, wallet=bonus, kind="wedding_bonus", counterparty=cp, ref=f"wedding_bonus:{lo}:{hi}:{uid}")
                await progress.emit(s, uid, "earn", bonus)
            paid = bonus
        return dict(a=a, b=b, a_name=await _name(s, a), b_name=await _name(s, b), venue=key, cost=cost, bonus=paid,
                    newly_married=(mode == "engaged"), held_at=now)


def wedding_card(info: dict, vow: str | None = None) -> str:
    """The wedding card (Telegram HTML). All names are escaped."""
    v = venue_of(info["venue"]) or R.wedding_venues[0]
    scene, guests = texts.VENUE_SCENES.get(v[0], ("", ""))
    vow = vow or texts.VOWS[(info["a"] + info["b"]) % len(texts.VOWS)]
    bonus = (f"🎁 Wedding gift: <b>+{num(info['bonus'])}</b> 💎 each" if info.get("bonus")
             else "<i>You already received this couple's wedding gift once — no second bonus.</i>")
    held = info["held_at"]
    return (f"💒 ✦ ━━ <b>Wedding Card</b> ━━ ✦ 💒\n\n"
            f"{mention(info['a'], info['a_name'])}  💞  {mention(info['b'], info['b_name'])}\n\n"
            f"{v[1]} <b>{esc(v[2])}</b> · {held:%d %b %Y}\n<i>{esc(scene)}</i>\n{esc(guests)}\n\n"
            f"“{esc(vow)}”\n\n{bonus}")


# ---------------------------------------------------------------- anniversary
async def claim_milestones(uid: int) -> list[tuple[str, int]]:
    """Pays every milestone the couple has reached that this player has not received yet. -> [(label, reward)]."""
    got: list[tuple[str, int]] = []
    async with db.tx() as s:
        m = await extras.partner_of(s, uid)
        if not m:
            raise GameError("💍 You are not married. Use /engage (or /marry) first.")
        days = (utcnow() - aware(m.married_at)).days
        for d, label, reward in R.anniv_milestones:
            if days >= d and await _flag(s, m.user_a, m.user_b, f"ms{d}:{uid}"):
                await economy.apply(s, uid, wallet=reward, kind="anniv_milestone", counterparty=other_of(m, uid),
                                    ref=f"anniv_ms:{m.user_a}:{m.user_b}:{d}:{uid}")
                await progress.emit(s, uid, "earn", reward)
                got.append((label, reward))
    return got


async def anniversary_view(uid: int) -> dict:
    async with db.tx() as s:
        m = await extras.partner_of(s, uid)
        if not m:
            raise GameError("💍 You are not married. Use /engage (or /marry) first.")
        other = other_of(m, uid)
        start, now = aware(m.married_at), utcnow()
        days = (now - start).days
        years = full_years(start, now)
        w = await wedding_of(s, m)
        mine = {k for k in (await s.execute(select(CoupleFlag.key).where(CoupleFlag.user_a == m.user_a, CoupleFlag.user_b == m.user_b))).scalars()}
        ms = [dict(days=d, label=label, reward=reward, reached=days >= d, claimed=f"ms{d}:{uid}" in mine) for d, label, reward in R.anniv_milestones]
        nxt = add_years(start, years + 1)
        return dict(other=other, name=await _name(s, other), days=days, years=years, start=start, milestones=ms,
                    next_anniversary=nxt, days_to_anniversary=max(0, (nxt.date() - now.date()).days),
                    venue=(venue_of(w.venue) if w else None))


async def pay_yearly_bonuses() -> list[dict]:
    """Loop job: every married couple that reached a new wedding anniversary gets the yearly bonus ONCE (unique flag 'yr<n>').
    -> one dict per couple that was just paid (for the greeting DMs). Idempotent, safe to run every few minutes."""
    now = utcnow()
    async with db.tx() as s:
        rows = (await s.execute(select(Marriage.user_a, Marriage.user_b, Marriage.married_at)
                                .where(Marriage.married_at <= now - timedelta(days=364)))).all()
    out: list[dict] = []
    for a, b, started in rows:
        n = full_years(aware(started), now)
        if n < 1:
            continue
        try:
            async with db.tx() as s:
                m = await extras.partner_of(s, a)
                if not m or m.user_b != b or aware(m.married_at) != aware(started):
                    continue                              # divorced / re-married meanwhile
                if not await _flag(s, a, b, f"yr{n}"):
                    continue
                for uid, cp in ((a, b), (b, a)):
                    await economy.apply(s, uid, wallet=R.anniv_yearly_bonus, kind="anniv_yearly", counterparty=cp,
                                        ref=f"anniv_yr:{a}:{b}:{n}:{uid}")
                    await progress.emit(s, uid, "earn", R.anniv_yearly_bonus)
                out.append(dict(a=a, b=b, a_name=await _name(s, a), b_name=await _name(s, b), years=n, bonus=R.anniv_yearly_bonus))
        except Exception:
            log.exception("yearly anniversary bonus failed for %s/%s", a, b)
    return out
