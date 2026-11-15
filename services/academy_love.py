"""Academy social, DATABASE half 2 (Part 4): secret crushes, Campus Couples, /campusdate and study buddies.
Standalone: no link to the marriage / family tables.

PRIVACY (the rule everything here is built around): a crush row is read by exactly two things - the sender (to show / remove their
own crush) and the mutual check. A crush is revealed ONLY when the other player has an active crush on the sender too (same
season); then both rows are consumed and a couple is created. No function, text or event ever tells a target that somebody
has a crush on them, and nothing is emitted for a crush that is not mutual.

    async with db.tx() as s:
        res = await love.send_crush(s, user_id, target_id, chat_id=...)
    await soc.deliver(bot, res.out)                                 # AFTER the commit
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update

from database.models import AcademyBuddy, AcademyBuddyBonus, AcademyCouple, AcademyCoupleDate, AcademyCrush, aware, utcnow
from database.session import db, insert_ignore
from game import academy_rules as AR
from services import academy_calendar as cal, academy_events as ev, academy_social as soc, academy_social_logic as S, academy_social_views as V, academy_students as stu, users
from services.errors import GameError, InsufficientFunds
from utils.content import academy_social_texts as T
from utils.fmt import esc

log = logging.getLogger(__name__)
SocialBlocked = soc.SocialBlocked


async def charge_diamonds(s, user_id: int, amount: int, ref: str, *, kind: str, meta: dict | None = None) -> None:
    """Take `amount` 💎 through the economy ledger with a unique ref (a replay changes nothing: GameError; too poor: InsufficientFunds)."""
    from services import economy
    if amount <= 0:
        raise ValueError("amount must be positive")
    ref = ref if ref.startswith("academy:") else f"academy:{ref}"
    await economy.apply(s, user_id, wallet=-amount, kind=kind, ref=ref, meta=meta)


# ================================================================== crushes
async def _crush_of(s, season_id: int, from_user: int) -> AcademyCrush | None:
    return (await s.execute(select(AcademyCrush).where(AcademyCrush.season_id == season_id,
                                                       AcademyCrush.from_user == from_user))).scalar_one_or_none()


async def couple_of(s, season_id: int, user_id: int) -> AcademyCouple | None:
    return (await s.execute(select(AcademyCouple).where(AcademyCouple.season_id == season_id,
                                                        or_(AcademyCouple.user_a == user_id,
                                                            AcademyCouple.user_b == user_id)))).scalars().first()


@dataclass
class CrushResult:
    status: str                              # saved | mutual
    text: str = ""                           # for the sender (only 'saved'; a mutual reveal is delivered by DM through `out`)
    partner_name: str = ""
    couple_id: int = 0
    out: soc.Out = field(default_factory=soc.Out)


async def send_crush(s, user_id: int, target_id: int, *, chat_id: int | None = None, target_is_bot: bool = False,
                     now: datetime | None = None) -> CrushResult:
    """Send a SECRET crush. Raises SocialBlocked (nothing changed) when it is not allowed. The users are row-locked in a fixed order,
    so two players who crush each other at the same moment cannot miss each other's crush. `chat_id` = the group of the command (only
    ever used for the mutual announcement)."""
    now = aware(now) if now else utcnow()
    await users.lock(s, user_id, target_id)
    season, ph, st = await soc.ctx(s, user_id, now)
    if ph.key == AR.BREAK_KEY:
        raise SocialBlocked(T.BREAK)
    same = user_id == target_id
    tst = None if (same or target_is_bot) else await stu.get_student(s, target_id, season.id)
    mine = await _crush_of(s, season.id, user_id)
    msg = S.crush_block(day=ph.day, sender_enrolled=st is not None, target_enrolled=tst is not None, same_person=same,
                        target_is_bot=target_is_bot, has_active=mine is not None,
                        sender_in_couple=(await couple_of(s, season.id, user_id)) is not None,
                        target_in_couple=(not same and (await couple_of(s, season.id, target_id)) is not None))
    if msg:
        raise SocialBlocked(msg)
    if not await insert_ignore(s, AcademyCrush, dict(season_id=season.id, from_user=user_id, to_user=target_id, chat_id=chat_id,
                                                     created_at=now)):
        raise SocialBlocked(T.CRUSH_ALREADY)
    back = await _crush_of(s, season.id, target_id)
    if back is None or back.to_user != user_id:                       # not mutual: nobody but the sender knows anything
        return CrushResult("saved", T.CRUSH_SAVED)
    # ---- mutual: the ONLY moment a crush is revealed
    a, b = S.couple_key(user_id, target_id)
    await insert_ignore(s, AcademyCouple, dict(season_id=season.id, user_a=a, user_b=b, points=0, created_at=now))
    cp = await couple_of(s, season.id, user_id)
    if cp is None:
        raise SocialBlocked(T.CRUSH_TARGET_TAKEN)
    await s.execute(delete(AcademyCrush).where(AcademyCrush.season_id == season.id,
                                               AcademyCrush.from_user.in_([user_id, target_id])))
    nm = await soc.names(s, [user_id, target_id])
    me, other = nm.get(user_id, "Player"), nm.get(target_id, "Player")
    group = chat_id or back.chat_id
    out = soc.Out()
    out.dm.append((user_id, T.CRUSH_MUTUAL_DM.format(name=esc(other)), AR.SOCIAL_GIFS["mutual"]))
    out.dm.append((target_id, T.CRUSH_MUTUAL_DM.format(name=esc(me)), AR.SOCIAL_GIFS["mutual"]))
    if group:
        out.announce = (group, T.CRUSH_MUTUAL_GROUP.format(a=esc(me), b=esc(other)))
    out.events.append((soc.CRUSH_MUTUAL, user_id, dict(season_id=season.id, partner_id=target_id, couple_id=cp.id)))
    return CrushResult("mutual", "", other, cp.id, out)


async def crush_off(s, user_id: int, now: datetime | None = None) -> bool:
    """Remove your own active crush (this season). True if there was one. Nobody is told."""
    now = aware(now) if now else utcnow()
    season = await cal.ensure_season(s, now)
    res = await s.execute(delete(AcademyCrush).where(AcademyCrush.season_id == season.id, AcademyCrush.from_user == user_id))
    return res.rowcount > 0


async def my_crush_name(s, user_id: int, season_id: int) -> str | None:
    """The name of the player YOU have a crush on (your own secret; only ever shown to you in a private chat)."""
    row = await _crush_of(s, season_id, user_id)
    if row is None:
        return None
    return (await soc.names(s, [row.to_user])).get(row.to_user, "Player")


# ================================================================== campus couple
async def couple_status(s, user_id: int, now: datetime | None = None, private: bool = False) -> dict:
    """Everything /campuscouple shows. `private` (a DM) also lets the owner see THEIR OWN pending crush; incoming crushes are
    never shown to anyone."""
    now = aware(now) if now else utcnow()
    season, ph, st = await soc.ctx(s, user_id, now, lock=False)
    soc.gate(season, ph, st, now)
    cp = await couple_of(s, season.id, user_id)
    pending = await my_crush_name(s, user_id, season.id) if (private and cp is None) else None
    if cp is None:
        return dict(couple=None, pending=pending, day=ph.day)
    partner = cp.user_b if cp.user_a == user_id else cp.user_a
    nm = await soc.names(s, [partner])
    dates = int((await s.execute(select(func.count()).select_from(AcademyCoupleDate)
                                 .where(AcademyCoupleDate.couple_id == cp.id))).scalar_one())
    done = (await s.get(AcademyCoupleDate, (cp.id, ph.day))) is not None if ph.day else False
    return dict(couple=cp, partner_id=partner, partner=nm.get(partner, "Player"), points=cp.points, dates=dates,
                done_today=done, day=ph.day, pending=None)


@dataclass
class DateResult:
    partner_name: str
    text: str
    gif: str | None
    energy: int
    total_points: int
    out: soc.Out = field(default_factory=soc.Out)


async def campus_date(s, user_id: int, now: datetime | None = None, rng: random.Random | None = None) -> DateResult:
    """/campusdate: once per couple and season day. Costs the player AR.DATE_ENERGY Energy and AR.DATE_DIAMONDS 💎 (ledger, unique
    ref). Both partners get a stat boost and the couple gets AR.DATE_POINTS. Too tired / too poor / already done today -> SocialBlocked
    or GameError and EVERYTHING (the once-a-day claim included) is rolled back."""
    now = aware(now) if now else utcnow()
    rng = rng or random.Random()
    season, ph, st = await soc.ctx(s, user_id, now)
    soc.gate(season, ph, st, now)
    cp = await couple_of(s, season.id, user_id)
    done = cp is not None and ph.day > 0 and (await s.get(AcademyCoupleDate, (cp.id, ph.day))) is not None
    msg = S.date_block(day=ph.day, in_couple=cp is not None, done_today=done)
    if msg:
        raise SocialBlocked(msg)
    partner = cp.user_b if cp.user_a == user_id else cp.user_a
    if await stu.get_student(s, partner, season.id, lock=True) is None:
        raise SocialBlocked(T.TARGET_NOT_ENROLLED)
    if not await insert_ignore(s, AcademyCoupleDate, dict(couple_id=cp.id, day=ph.day, by_user=user_id, created_at=now)):
        raise SocialBlocked(T.DATE_ONCE)                                   # the primary key is the once-a-day lock
    left = await stu.spend_energy(s, user_id, season.id, AR.DATE_ENERGY, now)    # GameError if too tired
    try:
        await charge_diamonds(s, user_id, AR.DATE_DIAMONDS, f"date:{season.id}:{cp.id}:{soc._us(cp.created_at)}:d{ph.day}",
                              kind="academy_date", meta={"season": season.id, "day": ph.day})
    except InsufficientFunds:
        raise SocialBlocked(T.DATE_NO_COST.format(cost=AR.DATE_DIAMONDS))
    mine, theirs = S.date_gains(rng), S.date_gains(rng)
    await stu.add_stats(s, user_id, season.id, mine)
    await stu.add_stats(s, partner, season.id, theirs)
    await s.execute(update(AcademyCouple).where(AcademyCouple.id == cp.id).values(points=AcademyCouple.points + AR.DATE_POINTS))
    total = int((await s.execute(select(AcademyCouple.points).where(AcademyCouple.id == cp.id))).scalar_one())
    nm = await soc.names(s, [user_id, partner])
    pname = nm.get(partner, "Player")
    text = V.date_text(partner=pname, line=S.date_line(rng), points=AR.DATE_POINTS, total=total, mine=mine, theirs=theirs,
                       me=nm.get(user_id, "You"))
    gif = AR.SOCIAL_GIFS["date"] if rng.random() < AR.DATE_GIF_CHANCE else None
    res = DateResult(pname, text, gif, left, total)
    res.out.events.append((soc.DATE, user_id, dict(season_id=season.id, day=ph.day, partner_id=partner, couple_id=cp.id,
                                                   points=AR.DATE_POINTS, total=total)))
    return res


# ================================================================== study buddies
async def active_buddy(s, season_id: int, user_id: int) -> AcademyBuddy | None:
    return (await s.execute(select(AcademyBuddy).where(AcademyBuddy.season_id == season_id, AcademyBuddy.state == "active",
                                                       or_(AcademyBuddy.user_a == user_id,
                                                           AcademyBuddy.user_b == user_id)))).scalars().first()


@dataclass
class BuddyRequest:
    status: str                  # pending | active
    request_id: int
    name_me: str
    name_target: str


async def request_buddy(s, user_id: int, target_id: int, *, chat_id: int | None = None, target_is_bot: bool = False,
                        now: datetime | None = None) -> BuddyRequest:
    """Ask `target_id` to be study buddies. If THEY already asked you, this accepts it. One open request per player."""
    now = aware(now) if now else utcnow()
    await users.lock(s, user_id, target_id)
    season, ph, st = await soc.ctx(s, user_id, now)
    if ph.key == AR.BREAK_KEY:
        raise SocialBlocked(T.BREAK)
    same = user_id == target_id
    tst = None if (same or target_is_bot) else await stu.get_student(s, target_id, season.id)
    pending = int((await s.execute(select(func.count()).select_from(AcademyBuddy)
                                   .where(AcademyBuddy.season_id == season.id, AcademyBuddy.user_a == user_id,
                                          AcademyBuddy.state == "pending"))).scalar_one())
    sender_has = (await active_buddy(s, season.id, user_id)) is not None
    target_has = (not same) and (await active_buddy(s, season.id, target_id)) is not None
    nm = await soc.names(s, [user_id, target_id])
    me, other = nm.get(user_id, "Player"), nm.get(target_id, "Player")
    rev = None if same else (await s.execute(select(AcademyBuddy).where(
        AcademyBuddy.season_id == season.id, AcademyBuddy.user_a == target_id, AcademyBuddy.user_b == user_id,
        AcademyBuddy.state == "pending"))).scalar_one_or_none()
    if rev is not None and st is not None and tst is not None and not sender_has and not target_has:
        rev.state = "active"                                               # they asked you first: that is a mutual accept
        return BuddyRequest("active", rev.id, me, other)
    msg = S.buddy_block(sender_enrolled=st is not None, target_enrolled=tst is not None, same_person=same, target_is_bot=target_is_bot,
                        sender_has_buddy=sender_has, target_has_buddy=target_has, sender_pending=pending)
    if msg:
        raise SocialBlocked(msg)
    await insert_ignore(s, AcademyBuddy, dict(season_id=season.id, user_a=user_id, user_b=target_id, state="pending", chat_id=chat_id,
                                              created_at=now))
    row = (await s.execute(select(AcademyBuddy).where(AcademyBuddy.season_id == season.id, AcademyBuddy.user_a == user_id,
                                                      AcademyBuddy.user_b == target_id))).scalar_one()
    return BuddyRequest("pending", row.id, me, other)


async def answer_buddy(s, user_id: int, request_id: int, accept: bool) -> tuple[str, str, str]:
    """The TARGET answers a request. Returns (status, requester name, target name); status = active | declined.
    Only the player the request was sent to may answer; a stale request is refused."""
    row = (await s.execute(select(AcademyBuddy).where(AcademyBuddy.id == request_id).with_for_update())).scalar_one_or_none()
    if row is None or row.state != "pending":
        raise SocialBlocked(T.BUDDY_GONE)
    if row.user_b != user_id:
        raise SocialBlocked(T.BUDDY_NOT_FOR_YOU)
    nm = await soc.names(s, [row.user_a, row.user_b])
    a, b = nm.get(row.user_a, "Player"), nm.get(row.user_b, "Player")
    if not accept:
        await s.delete(row)
        return "declined", a, b
    if (await active_buddy(s, row.season_id, row.user_a)) is not None or (await active_buddy(s, row.season_id, row.user_b)) is not None:
        raise SocialBlocked(T.BUDDY_TARGET_HAS)
    row.state = "active"
    return "active", a, b


async def end_buddy(s, user_id: int, now: datetime | None = None) -> str | None:
    """/buddy off: end your active buddy pair, or cancel your own open request. Returns 'active' | 'pending' | None."""
    now = aware(now) if now else utcnow()
    season = await cal.ensure_season(s, now)
    act = await active_buddy(s, season.id, user_id)
    if act is not None:
        await s.delete(act)
        return "active"
    res = await s.execute(delete(AcademyBuddy).where(AcademyBuddy.season_id == season.id, AcademyBuddy.user_a == user_id,
                                                     AcademyBuddy.state == "pending"))
    return "pending" if res.rowcount > 0 else None


async def buddy_status(s, user_id: int, now: datetime | None = None) -> dict:
    now = aware(now) if now else utcnow()
    season, ph, st = await soc.ctx(s, user_id, now, lock=False)
    soc.gate(season, ph, st, now)
    act = await active_buddy(s, season.id, user_id)
    if act is not None:
        partner = act.user_b if act.user_a == user_id else act.user_a
        nm = await soc.names(s, [partner])
        claimed = (await s.get(AcademyBuddyBonus, (season.id, user_id, ph.day))) is not None if ph.day else False
        return dict(kind="active", partner=nm.get(partner, "Player"), claimed_today=claimed)
    out = (await s.execute(select(AcademyBuddy).where(AcademyBuddy.season_id == season.id, AcademyBuddy.user_a == user_id,
                                                      AcademyBuddy.state == "pending"))).scalars().first()
    if out is not None:
        nm = await soc.names(s, [out.user_b])
        return dict(kind="pending", partner=nm.get(out.user_b, "Player"))
    return dict(kind="none")


@ev.subscribe(ev.STUDIED)
async def _buddy_bonus_on_study(user_id, **data) -> None:
    """Part 2 emits `studied` after every /study. With an active buddy, the FIRST study of a season day gives +AR.BUDDY_KNOWLEDGE_BONUS
    Knowledge (the primary key of academy_buddy_bonus is the once-a-day lock). Uses the event, so Part 2 is untouched."""
    season_id, day = data.get("season_id"), data.get("day")
    if not user_id or not season_id or not day:
        return
    async with db.tx() as s:
        if await active_buddy(s, season_id, user_id) is None:
            return
        if not await insert_ignore(s, AcademyBuddyBonus, dict(season_id=season_id, user_id=user_id, day=day)):
            return
        await stu.add_stats(s, user_id, season_id, {"knowledge": AR.BUDDY_KNOWLEDGE_BONUS})
