"""Clans: join together, fill the clan bank, level the clan up for a /daily bonus. All money via economy.apply."""
from __future__ import annotations

import re

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from database.models import Clan, ClanMember, User, utcnow
from database.session import db
from game.rules import R
from services import economy, users
from services.errors import GameError

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{1,22}[A-Za-z0-9]$")


def level_of(bank: int) -> int:
    return min(10, 1 + int(bank) // R.clan_level_step)


def capacity(level: int) -> int:
    return R.clan_max_members + 2 * (level - 1)


def _clean(name: str) -> str:
    return " ".join(name.split())


async def _member(s, uid: int):
    return (await s.execute(select(ClanMember, Clan).join(Clan, Clan.id == ClanMember.clan_id)
                            .where(ClanMember.user_id == uid))).first()


async def _count(s, clan_id: int) -> int:
    return (await s.execute(select(func.count()).select_from(ClanMember).where(ClanMember.clan_id == clan_id))).scalar()


async def create(uid: int, name: str) -> str:
    name = _clean(name)
    if not NAME_RE.match(name):
        raise GameError("❌ Clan names: 3–24 characters — letters, numbers, spaces, - or _.")
    try:
        async with db.tx() as s:
            await users.require(s, uid)
            if await _member(s, uid):
                raise GameError("❌ You're already in a clan. Use /clan leave first.")
            await economy.apply(s, uid, wallet=-R.clan_create_cost, kind="clan_create")
            c = Clan(name=name, name_lc=name.lower(), owner_id=uid, bank=0, created_at=utcnow())
            s.add(c)
            await s.flush()
            s.add(ClanMember(user_id=uid, clan_id=c.id, joined_at=utcnow()))
            return c.name
    except IntegrityError:
        raise GameError("❌ That clan name is already taken.")


async def join(uid: int, name: str) -> str:
    async with db.tx() as s:
        await users.require(s, uid)
        if await _member(s, uid):
            raise GameError("❌ You're already in a clan. Use /clan leave first.")
        c = (await s.execute(select(Clan).where(Clan.name_lc == _clean(name).lower()).with_for_update())).scalar_one_or_none()
        if c is None:
            raise GameError("❌ No clan with that name. See /clan top.")
        if await _count(s, c.id) >= capacity(level_of(c.bank)):
            raise GameError("❌ That clan is full.")
        s.add(ClanMember(user_id=uid, clan_id=c.id, joined_at=utcnow()))
        return c.name


async def leave(uid: int) -> str:
    async with db.tx() as s:
        row = await _member(s, uid)
        if not row:
            raise GameError("❌ You're not in a clan.")
        _, c = row
        if c.owner_id == uid and await _count(s, c.id) > 1:
            raise GameError("👑 You own this clan. Kick the members first or use /clan disband.")
        await s.execute(delete(ClanMember).where(ClanMember.user_id == uid))
        if c.owner_id == uid:
            await s.execute(delete(Clan).where(Clan.id == c.id))
        return c.name


async def kick(owner: int, target: int) -> str:
    async with db.tx() as s:
        row = await _member(s, owner)
        if not row or row[1].owner_id != owner:
            raise GameError("⛔ Only the clan owner can kick members.")
        if target == owner:
            raise GameError("❌ You can't kick yourself.")
        res = await s.execute(delete(ClanMember).where(ClanMember.user_id == target, ClanMember.clan_id == row[1].id))
        if res.rowcount != 1:
            raise GameError("❌ That player isn't in your clan.")
        return row[1].name


async def disband(owner: int) -> str:
    async with db.tx() as s:
        row = await _member(s, owner)
        if not row or row[1].owner_id != owner:
            raise GameError("⛔ Only the clan owner can disband the clan.")
        c = row[1]
        await s.execute(delete(ClanMember).where(ClanMember.clan_id == c.id))
        await s.execute(delete(Clan).where(Clan.id == c.id))
        return c.name


async def deposit(uid: int, amount: int) -> tuple[str, int, int, int]:
    """Returns (clan name, new bank, old level, new level). Deposits stay in the clan bank (a diamond sink)."""
    if amount < 100:
        raise GameError("❌ Minimum clan deposit is <b>100</b> 💎.")
    async with db.tx() as s:
        row = await _member(s, uid)
        if not row:
            raise GameError("❌ You're not in a clan.")
        c = row[1]
        await economy.apply(s, uid, wallet=-amount, kind="clan_deposit", meta={"clan": c.id})
        await s.execute(update(Clan).where(Clan.id == c.id).values(bank=Clan.bank + amount))
        await s.refresh(c)
        return c.name, c.bank, level_of(c.bank - amount), level_of(c.bank)


async def daily_bonus(s, uid: int) -> int:
    row = await _member(s, uid)
    return level_of(row[1].bank) * R.clan_daily_bonus if row else 0


ROSTER_PER = 15


async def info(name: str | None = None, uid: int | None = None, clan_id: int | None = None, page: int = 0) -> dict:
    """Clan card. The member list is paged (ROSTER_PER per page): d["page"] / d["pages"] / d["id"] drive the arrows."""
    async with db.tx() as s:
        if clan_id is not None:
            c = await s.get(Clan, clan_id)
        elif name:
            c = (await s.execute(select(Clan).where(Clan.name_lc == _clean(name).lower()))).scalar_one_or_none()
        else:
            row = await _member(s, uid)
            c = row[1] if row else None
        if c is None:
            raise GameError("❌ Clan not found." if (name or clan_id is not None) else "NOCLAN")
        total = await _count(s, c.id)
        pages = max(1, -(-total // ROSTER_PER))
        page = min(max(0, page), pages - 1)
        rows = (await s.execute(select(User.id, User.first_name, User.level).join(ClanMember, ClanMember.user_id == User.id)
                                .where(ClanMember.clan_id == c.id).order_by(User.level.desc(), User.id)
                                .limit(ROSTER_PER).offset(page * ROSTER_PER))).all()
        lvl = level_of(c.bank)
        return dict(id=c.id, name=c.name, owner=c.owner_id, bank=c.bank, level=lvl, cap=capacity(lvl), members=total,
                    next_level_at=None if lvl >= 10 else lvl * R.clan_level_step, roster=rows, page=page, pages=pages)


async def top_page(page: int = 0, per: int = 10) -> tuple[list[tuple[str, int, int, int]], int]:
    """One page of the clan board -> (rows, total clans)."""
    async with db.tx() as s:
        total = (await s.execute(select(func.count()).select_from(Clan))).scalar() or 0
        rows = (await s.execute(
            select(Clan.name, Clan.bank, func.count(ClanMember.user_id))
            .join(ClanMember, ClanMember.clan_id == Clan.id, isouter=True)
            .group_by(Clan.id).order_by(Clan.bank.desc(), Clan.id).limit(per).offset(max(0, page) * per))).all()
        return [(n, int(b), int(c), level_of(b)) for n, b, c in rows], int(total)


async def top(limit: int = 10) -> list[tuple[str, int, int, int]]:
    async with db.tx() as s:
        rows = (await s.execute(
            select(Clan.name, Clan.bank, func.count(ClanMember.user_id))
            .join(ClanMember, ClanMember.clan_id == Clan.id, isouter=True)
            .group_by(Clan.id).order_by(Clan.bank.desc(), Clan.id).limit(limit))).all()
        return [(n, int(b), int(c), level_of(b)) for n, b, c in rows]
