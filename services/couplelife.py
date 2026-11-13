"""Everyday couple life (romance part C): honeymoon, couple quest, love streak. All of them need a marriage.

* /honeymoon   - only after a /wedding, once per couple: both accept, the trip price (a sink) is split 50/50, the trip lasts 6 hours with story
                 chapters that unlock over time, then each partner gets a bonus (smaller than the trip price in total) plus love points.
* /couplequest - two shared tasks per day (picked per couple and day); when a task is done both partners are paid, once.
* /lovestreak  - both partners check in on the same UTC day; consecutive days build a streak; one-time rewards at 7 / 30 / 100 days.
Diamonds only via economy.apply() with unique refs; once-only rewards are guarded by couple_flags rows (never deleted, so no re-earning
after a divorce); the trip and the streak are stored in the database, so a restart loses nothing."""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, or_, select, update

from database.models import Cooldown, CoupleFlag, Honeymoon, LoveStreak, User, aware, utcnow
from database.session import db
from game.rules import R
from services import economy, extras, life, progress, romance, users
from services.errors import GameError
from utils.content import romance as texts
from utils.fmt import dur

_pair, _flag, other_of = romance.pair, romance._flag, romance.other_of


async def _name(s, uid: int) -> str:
    return (await s.execute(select(User.first_name).where(User.id == uid))).scalar() or "Player"


async def _marriage(s, uid: int):
    m = await extras.partner_of(s, uid)
    if not m:
        raise GameError("💍 You need to be married for that. Use /engage (or /marry) and /wedding first.")
    return m


# ================================================================== honeymoon
def destination(key: str) -> tuple | None:
    return texts.HONEYMOON_DESTINATIONS.get(key)


async def _wedded_marriage(s, uid: int):
    m = await _marriage(s, uid)
    if await romance.wedding_of(s, m) is None:
        raise GameError("🏝 Honeymoons are for newlyweds: hold your /wedding first.")
    return m


async def _trip(s, m) -> Honeymoon | None:
    """The honeymoon row that belongs to THIS marriage (an older one belongs to an earlier marriage)."""
    h = await s.get(Honeymoon, m.user_a)
    if h is not None and h.user_b == m.user_b and aware(h.started_at) >= aware(m.married_at):
        return h
    return None


def _chapters_unlocked(h: Honeymoon, now: datetime) -> int:
    if now >= aware(h.ends_at):
        return 3
    total = max(1, int((aware(h.ends_at) - aware(h.started_at)).total_seconds()))
    elapsed = max(0.0, (now - aware(h.started_at)).total_seconds())
    return min(3, 1 + int(3 * elapsed / total))


async def honeymoon_offer(uid: int) -> tuple[int, str, int]:
    """Pre-check for booking. -> (partner id, partner name, price per partner)."""
    half = R.honeymoon_cost // 2
    async with db.tx() as s:
        m = await _wedded_marriage(s, uid)
        other = other_of(m, uid)
        if await _trip(s, m) is not None or await s.get(CoupleFlag, (m.user_a, m.user_b, "honeymoon")):
            raise GameError("🏝 You two already had your honeymoon! Use /honeymoon to see it.")
        if (await economy.wallet_of(s, uid)).balance < half:
            raise GameError(f"💸 Your half of the trip is <b>{half:,}</b> 💎 and you don't have that much yet.")
        if (await economy.wallet_of(s, other)).balance < half:
            raise GameError(f"💸 Your partner can't afford their half of the trip ({half:,} 💎) yet.")
        return other, await _name(s, other), half


async def start_honeymoon(a: int, b: int, ref: str) -> dict:
    """b accepted a's booking. Both pay half; the trip starts now and ends after R.honeymoon_duration_s."""
    half = R.honeymoon_cost // 2
    async with db.tx() as s:
        await users.lock(s, a, b)
        m = await _wedded_marriage(s, a)
        if other_of(m, a) != b:
            raise GameError("💔 The plans changed — start again with /honeymoon.")
        if not await _flag(s, m.user_a, m.user_b, "honeymoon"):        # once per couple, ever
            raise GameError("🏝 You two already had your honeymoon!")
        for uid in (a, b):
            if (await economy.wallet_of(s, uid)).balance < half:
                raise GameError("💸 The trip fell through — one of you can no longer afford their half.")
        for uid, cp in ((a, b), (b, a)):
            await economy.apply(s, uid, wallet=-half, kind="honeymoon_trip", counterparty=cp, ref=f"{ref}:{uid}")
        key = random.choice(sorted(texts.HONEYMOON_DESTINATIONS))
        now = utcnow()
        await s.execute(delete(Honeymoon).where(or_(Honeymoon.user_a.in_((m.user_a, m.user_b)), Honeymoon.user_b.in_((m.user_a, m.user_b)))))
        s.add(Honeymoon(user_a=m.user_a, user_b=m.user_b, destination=key, started_at=now,
                        ends_at=now + timedelta(seconds=R.honeymoon_duration_s)))
        return dict(destination=key, ends_at=now + timedelta(seconds=R.honeymoon_duration_s), a_name=await _name(s, a), b_name=await _name(s, b))


async def honeymoon_status(uid: int) -> dict:
    """phase: 'none' (can book) | 'away' | 'back' (collect the bonus) | 'done'."""
    async with db.tx() as s:
        m = await _wedded_marriage(s, uid)
        h = await _trip(s, m)
        other = other_of(m, uid)
        base = dict(other=other, name=await _name(s, other))
        if h is None:
            return dict(base, phase="done" if await s.get(CoupleFlag, (m.user_a, m.user_b, "honeymoon")) else "none")
        emoji, name, chapters, finale = texts.HONEYMOON_DESTINATIONS[h.destination]
        now = utcnow()
        left = int((aware(h.ends_at) - now).total_seconds())
        if h.collected:
            phase = "done"
        else:
            phase = "away" if left > 0 else "back"
        n = _chapters_unlocked(h, now)
        return dict(base, phase=phase, emoji=emoji, place=name, left=max(0, left), chapters=chapters[:n], finale=finale if left <= 0 else "")


async def collect_honeymoon(uid: int) -> dict:
    """Either partner collects once the trip is over; BOTH are paid in the same transaction, exactly once."""
    async with db.tx() as s:
        m = await _wedded_marriage(s, uid)
        await users.lock(s, m.user_a, m.user_b)
        h = await _trip(s, m)
        if h is None:
            raise GameError("🏝 You have no honeymoon to collect. Book one with /honeymoon.")
        left = int((aware(h.ends_at) - utcnow()).total_seconds())
        if left > 0:
            raise GameError(f"🏝 You are still away! Back in <b>{dur(left)}</b>.")
        res = await s.execute(update(Honeymoon).where(Honeymoon.user_a == h.user_a, Honeymoon.collected.is_(False)).values(collected=True))
        if res.rowcount != 1:
            raise GameError("🎁 The honeymoon bonus was already collected.")
        for u, cp in ((m.user_a, m.user_b), (m.user_b, m.user_a)):
            await economy.apply(s, u, wallet=R.honeymoon_bonus, kind="honeymoon_bonus", counterparty=cp, ref=f"honey:{m.user_a}:{m.user_b}:{u}")
            await progress.emit(s, u, "earn", R.honeymoon_bonus)
        await life._add_love(s, m.user_a, R.honeymoon_love)
        emoji, name, _chapters, finale = texts.HONEYMOON_DESTINATIONS[h.destination]
        return dict(emoji=emoji, place=name, finale=finale, bonus=R.honeymoon_bonus, love=R.honeymoon_love)


async def due_honeymoon_notices() -> list[dict]:
    """Loop job: trips that just ended and whose couple has not been told yet (each trip is announced once)."""
    out = []
    async with db.tx() as s:
        rows = (await s.execute(select(Honeymoon).where(Honeymoon.ends_at <= utcnow(), Honeymoon.notified.is_(False),
                                                         Honeymoon.collected.is_(False)))).scalars().all()
        for h in rows:
            res = await s.execute(update(Honeymoon).where(Honeymoon.user_a == h.user_a, Honeymoon.notified.is_(False)).values(notified=True))
            if res.rowcount == 1:
                out.append(dict(a=h.user_a, b=h.user_b, destination=h.destination))
    return out


# ================================================================== couple quest
def tasks_for(lo: int, hi: int, day: date) -> list[tuple]:
    """Two different tasks, the same for both partners all day (seeded by couple + date, nothing to store)."""
    return random.Random(f"{lo}:{hi}:{day:%Y%m%d}").sample(texts.COUPLE_TASKS, 2)


async def _event_today(s, lo: int, hi: int, day: date, kind: str, uid: int) -> bool:
    return await s.get(CoupleFlag, (lo, hi, f"ev:{day:%Y%m%d}:{kind}:{uid}")) is not None


async def _cooldown_claimed_today(s, uid: int, key: str, cd: int, day: date) -> bool:
    """/daily, /date and /work already keep cooldowns; the claim time is expires_at - cooldown, so 'did it today' needs no change to them."""
    exp = (await s.execute(select(Cooldown.expires_at).where(Cooldown.user_id == uid, Cooldown.key == key))).scalar()
    if exp is None:
        return False
    return aware(exp) - timedelta(seconds=cd) >= datetime.combine(day, time.min, tzinfo=timezone.utc)


async def _did(s, kind: str, uid: int, lo: int, hi: int, day: date) -> bool:
    if kind in ("letter", "gift"):
        return await _event_today(s, lo, hi, day, kind, uid)
    if kind == "daily":
        return await _cooldown_claimed_today(s, uid, "daily", R.daily_cooldown_s, day)
    if kind == "date":
        return await _cooldown_claimed_today(s, uid, "date", life.DATE_CD_S, day)
    if kind == "work":
        return await _cooldown_claimed_today(s, uid, "work", R.work_cooldown_s, day)
    return False


async def _progress(s, uid: int) -> dict:
    m = await _marriage(s, uid)
    lo, hi = m.user_a, m.user_b
    day = utcnow().date()
    out = []
    for key, emoji, text, mode in tasks_for(lo, hi, day):
        da, db_ = await _did(s, key, lo, lo, hi, day), await _did(s, key, hi, lo, hi, day)
        done = (da and db_) if mode == "both" else (da or db_)
        paid = await s.get(CoupleFlag, (lo, hi, f"cq:{day:%Y%m%d}:{key}")) is not None
        out.append(dict(key=key, emoji=emoji, text=text, mode=mode, done=done, paid=paid, mine=da if uid == lo else db_,
                        theirs=db_ if uid == lo else da))
    return dict(m=m, day=day, tasks=out)


async def couple_quest(uid: int) -> dict:
    """Pays every finished, not yet paid task to BOTH partners (once), then returns the board."""
    paid_now = []
    async with db.tx() as s:
        pr = await _progress(s, uid)
        m, day = pr["m"], pr["day"]
        for t in pr["tasks"]:
            if t["done"] and not t["paid"] and await _flag(s, m.user_a, m.user_b, f"cq:{day:%Y%m%d}:{t['key']}"):
                for u, cp in ((m.user_a, m.user_b), (m.user_b, m.user_a)):
                    await economy.apply(s, u, wallet=R.couple_quest_reward, kind="couple_quest", counterparty=cp,
                                        ref=f"cq:{m.user_a}:{m.user_b}:{day:%Y%m%d}:{t['key']}:{u}")
                    await progress.emit(s, u, "earn", R.couple_quest_reward)
                    await progress.emit(s, u, "couple_quest")
                await life._add_love(s, m.user_a, R.couple_quest_love)
                t["paid"] = True
                paid_now.append(t["key"])
        other = other_of(m, uid)
        return dict(tasks=pr["tasks"], paid_now=paid_now, other=other, name=await _name(s, other))


# ================================================================== love streak
async def love_checkin(uid: int) -> dict:
    """Check in for today. The day only counts when BOTH partners have checked in (UTC date)."""
    async with db.tx() as s:
        m = await _marriage(s, uid)
        lo, hi = m.user_a, m.user_b
        await users.lock(s, lo, hi)
        today = utcnow().date()
        row = await s.get(LoveStreak, lo)
        if row is None or row.user_b != hi:
            await s.execute(delete(LoveStreak).where(or_(LoveStreak.user_a.in_((lo, hi)), LoveStreak.user_b.in_((lo, hi)))))
            row = LoveStreak(user_a=lo, user_b=hi, streak=0, best=0)
            s.add(row)
            await s.flush()
        other = other_of(m, uid)
        mine = row.a_day if uid == lo else row.b_day
        already = mine == today
        rewards: list[tuple[int, int]] = []
        if not already:
            if uid == lo:
                row.a_day = today
            else:
                row.b_day = today
            if row.a_day == today and row.b_day == today and row.last_day != today:
                row.streak = (row.streak + 1) if row.last_day == today - timedelta(days=1) else 1
                row.last_day = today
                row.best = max(row.best or 0, row.streak)
                await life._add_love(s, lo, R.streak_love_per_day)
                for n, reward in R.streak_milestones:
                    if row.streak >= n and await _flag(s, lo, hi, f"ls{n}"):          # once per couple, ever
                        for u, cp in ((lo, hi), (hi, lo)):
                            await economy.apply(s, u, wallet=reward, kind="love_streak", counterparty=cp, ref=f"lstreak:{lo}:{hi}:{n}:{u}")
                            await progress.emit(s, u, "earn", reward)
                        rewards.append((n, reward))
                await progress.emit(s, lo, "love_streak")
                await progress.emit(s, hi, "love_streak")
        alive = row.last_day is not None and row.last_day >= today - timedelta(days=1)
        both = row.a_day == today and row.b_day == today
        nxt = next(((n, r) for n, r in R.streak_milestones if (row.streak if alive else 0) < n), None)
        return dict(other=other, name=await _name(s, other), already=already, both=both, streak=row.streak if alive else 0,
                    best=row.best or 0, rewards=rewards, next=nxt)
