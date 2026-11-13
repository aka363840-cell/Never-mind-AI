"""Gifts and love letters for your partner and your relatives (romance part B).

* /sendgift   - flowers, chocolate, teddy, cake. Costs diamonds (a pure sink) and raises LOVE points (partner, shared with /date and /gift)
                or BOND points (relatives and friends from /family). Each SENDER can create at most R.gift_daily_cap_points per UTC day.
* /loveletter - up to R.letter_max_chars characters, costs a few diamonds, 30 min cooldown, posted as a styled card.
Both need a standing relationship that both sides already accepted (marriage or an accepted /family relation); nobody can target themselves.
Diamonds only move through economy.apply(); the cooldown through cooldowns.claim()."""
from __future__ import annotations

import re

from sqlalchemy import or_, select

from database.models import Bond, BondDaily, CoupleFlag, User, utcnow
from database.session import db, insert_ignore
from game.rules import R
from services import cooldowns, economy, extras, family, life, progress, users
from services.errors import GameError, OnCooldown
from utils.fmt import esc, mention, num


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def gift_of(key: str) -> tuple | None:
    key = (key or "").strip().lower()
    return next((g for g in R.gift_shop if g[0] == key), None)


def bond_title(points: int) -> str:
    """'Close 🤝' / 'Inseparable 💞' ... or '' while the bond is still too small for a title."""
    for need, title, emoji in R.bond_titles:
        if points >= need:
            return f"{title} {emoji}"
    return ""


async def _relation(s, a: int, b: int) -> tuple[str, int | None]:
    """('partner', marriage key) | ('relative', None). Anything else -> GameError."""
    if a == b:
        raise GameError("❌ You can't send that to yourself 😅")
    m = await extras.partner_of(s, a)
    if m and b in (m.user_a, m.user_b):
        return "partner", m.user_a
    if await family._pair(s, a, b):
        return "relative", None
    raise GameError("💞 You can only send that to your partner or a relative / friend from /family. Add them first with /friend, /brother ... (see /family).")


async def _used_today(s, uid: int) -> int:
    row = await s.get(BondDaily, (uid, utcnow().date()))
    return row.points if row else 0


async def _add_daily(s, uid: int, pts: int) -> None:
    day = utcnow().date()
    row = await s.get(BondDaily, (uid, day))
    if row is None:
        if await insert_ignore(s, BondDaily, dict(user_id=uid, day=day, points=pts)):
            return
        row = await s.get(BondDaily, (uid, day))
    row.points = (row.points or 0) + pts


async def _add_bond(s, a: int, b: int, pts: int) -> int:
    lo, hi = _pair(a, b)
    row = await s.get(Bond, (lo, hi))
    if row is None:
        if await insert_ignore(s, Bond, dict(user_a=lo, user_b=hi, points=pts)):
            return pts
        row = await s.get(Bond, (lo, hi))
    row.points = (row.points or 0) + pts
    return row.points


async def _event(s, a: int, b: int, kind: str, sender: int) -> None:
    """Remember 'sender did <kind> today' for the couple quest (one row per day / kind / sender)."""
    lo, hi = _pair(a, b)
    await insert_ignore(s, CoupleFlag, dict(user_a=lo, user_b=hi, key=f"ev:{utcnow():%Y%m%d}:{kind}:{sender}", created_at=utcnow()))


async def partner_id(uid: int) -> int | None:
    async with db.tx() as s:
        m = await extras.partner_of(s, uid)
        return (m.user_b if m.user_a == uid else m.user_a) if m else None


# ---------------------------------------------------------------- gifts
async def gift_options(sender: int, target: int) -> tuple[str, str, int]:
    """Pre-check before the shop is shown. -> (kind 'partner'|'relative', target name, points still allowed today)."""
    async with db.tx() as s:
        await users.require(s, sender)
        await users.require(s, target)
        kind, _ = await _relation(s, sender, target)
        left = R.gift_daily_cap_points - await _used_today(s, sender)
        if left <= 0:
            raise GameError(f"🎁 You reached today's gift limit ({R.gift_daily_cap_points} love points). Come back tomorrow!")
        name = (await s.execute(select(User.first_name).where(User.id == target))).scalar() or "Player"
        return kind, name, left


async def send_gift(sender: int, target: int, key: str, ref: str) -> dict:
    g = gift_of(key)
    if g is None:
        raise GameError("🎁 Unknown gift. Pick one of the buttons.")
    _, emoji, name, price, pts = g
    async with db.tx() as s:
        await users.lock(s, sender, target)
        await users.require(s, sender)
        await users.require(s, target)
        kind, mkey = await _relation(s, sender, target)
        left = R.gift_daily_cap_points - await _used_today(s, sender)
        if left <= 0:
            raise GameError(f"🎁 You reached today's gift limit ({R.gift_daily_cap_points} love points). Come back tomorrow!")
        if left < pts:                                   # never charge for points that would be cut off
            raise GameError(f"🎁 Only <b>{left}</b> love points left for today — pick a smaller gift.")
        await economy.apply(s, sender, wallet=-price, kind="gift_send", counterparty=target, ref=ref)
        if kind == "partner":
            total = await life._add_love(s, mkey, pts)
            title = life.love_title(total)
        else:
            total = await _add_bond(s, sender, target, pts)
            title = bond_title(total)
        await _add_daily(s, sender, pts)
        from services import realm
        await realm.bump(s, sender, "gifts_sent")
        await progress.emit(s, sender, "gift_sent")
        if kind == "partner":
            await _event(s, sender, target, "gift", sender)
        tname = (await s.execute(select(User.first_name).where(User.id == target))).scalar() or "Player"
        return dict(kind=kind, emoji=emoji, name=name, price=price, points=pts, total=total, title=title, left=left - pts, target_name=tname)


# ---------------------------------------------------------------- bonds (for /family)
async def bond_points_map(uid: int) -> dict[int, int]:
    """{other player id: bond points} for everybody uid has a bond with."""
    async with db.tx() as s:
        rows = (await s.execute(select(Bond).where(or_(Bond.user_a == uid, Bond.user_b == uid)))).scalars().all()
        return {(r.user_b if r.user_a == uid else r.user_a): r.points for r in rows}


# ---------------------------------------------------------------- love letters
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]")


def clean_letter(raw: str) -> str:
    """Trim, drop control / bidi-override characters, collapse blank-line runs, and defuse pings and links
    (a zero-width space after '@' and inside '://' keeps a letter from tagging strangers or auto-linking)."""
    t = _CTRL.sub("", raw or "").replace("\r\n", "\n").strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


def defuse(t: str) -> str:
    return t.replace("@", "@\u200b").replace("://", ":\u200b//")


def letter_card(sender_id: int, sender_name: str, target_id: int, target_name: str, text: str, opener: str = "With all my heart") -> str:
    return (f"💌 ✦ ━━ <b>Love Letter</b> ━━ ✦ 💌\n\n"
            f"To {mention(target_id, target_name)},\n\n"
            f"<i>“{esc(defuse(text))}”</i>\n\n"
            f"— {esc(opener)}, {mention(sender_id, sender_name)} 💕")


async def send_letter(sender: int, target: int, raw_text: str) -> dict:
    text = clean_letter(raw_text)
    if not text:
        raise GameError("💌 Write something! Example: <code>/loveletter You make every day better</code>")
    if len(text) > R.letter_max_chars:
        raise GameError(f"💌 Too long: <b>{len(text)}</b> characters (max {R.letter_max_chars}).")
    async with db.tx() as s:
        await users.require(s, sender)
        await users.require(s, target)
        kind, mkey = await _relation(s, sender, target)
        rem = await cooldowns.claim(s, sender, "loveletter", R.letter_cooldown_s)
        if rem:
            raise OnCooldown(rem, "Your next love letter")
        await economy.apply(s, sender, wallet=-R.letter_cost, kind="love_letter", counterparty=target)
        left = R.gift_daily_cap_points - await _used_today(s, sender)
        pts = max(0, min(R.letter_points, left))         # a letter is always sent; it just stops giving points at the daily cap
        if pts:
            if kind == "partner":
                await life._add_love(s, mkey, pts)
            else:
                await _add_bond(s, sender, target, pts)
            await _add_daily(s, sender, pts)
        from services import realm
        await realm.bump(s, sender, "letters_sent")
        await progress.emit(s, sender, "letter_sent")
        if kind == "partner":
            await _event(s, sender, target, "letter", sender)
        sname = (await s.execute(select(User.first_name).where(User.id == sender))).scalar() or "Player"
        tname = (await s.execute(select(User.first_name).where(User.id == target))).scalar() or "Player"
        return dict(text=text, points=pts, cost=R.letter_cost, sender_name=sname, target_name=tname, kind=kind)
