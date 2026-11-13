from __future__ import annotations

import json
import secrets

from sqlalchemy import select, update

from config import get_settings
from database.models import AdminLog, Transaction, User, utcnow
from database.session import db
from services import economy, users
from services.errors import GameError
from services.kv import kv

OWNER_ONLY = {"give", "take", "setbalance", "broadcast", "maintenance", "stats", "backup", "emojiid", "seticon", "ban", "unban", "mute", "unmute"}
DESTRUCTIVE = {"give", "take", "setbalance", "ban", "broadcast", "maintenance"}


def role(uid: int) -> str:
    st = get_settings()
    if uid == st.owner_id:
        return "owner"
    return "none"      # no moderators: the owner is the only one with admin powers


def authorize(uid: int, action: str) -> None:
    r = role(uid)
    if r == "none" or (r == "mod" and action in OWNER_ONLY):
        raise GameError("⛔ Not authorised.")


async def stage(admin_id: int, action: str, args: dict) -> str:
    """Store a pending destructive action; it only runs after an explicit confirm tap by the SAME admin."""
    authorize(admin_id, action)
    nonce = secrets.token_urlsafe(8)
    await kv.put(f"adm:{nonce}", json.dumps({"admin": admin_id, "action": action, "args": args}), 90)
    return nonce


async def confirm(admin_id: int, nonce: str) -> tuple[str, dict]:
    raw = await kv.take(f"adm:{nonce}")      # one-time: replays find nothing
    if not raw:
        raise GameError("⌛ Confirmation expired or already used.")
    data = json.loads(raw)
    if data["admin"] != admin_id:
        raise GameError("⛔ Not your confirmation.")
    return data["action"], data["args"]


async def cancel(nonce: str) -> None:
    await kv.take(f"adm:{nonce}")


async def _log(s, admin: int, action: str, target: int | None, details: dict) -> None:
    s.add(AdminLog(admin_id=admin, action=action, target_id=target, details=json.dumps(details), created_at=utcnow()))


async def execute(admin_id: int, action: str, args: dict) -> str:
    authorize(admin_id, action)
    async with db.tx() as s:
        t = args.get("target")
        if action in ("give", "take"):
            amt = int(args["amount"])
            if amt <= 0:
                raise GameError("❌ Amount must be positive.")
            await users.require(s, t) if action == "give" else await _exists(s, t)
            await economy.apply(s, t, wallet=amt if action == "give" else -amt, kind=f"admin_{action}",
                                counterparty=admin_id)
            msg = f"✅ {action} {amt:,} 💎 → {t}"
        elif action == "setbalance":
            await _exists(s, t)
            w = await economy.wallet_of(s, t, lock=True)
            new = int(args["amount"])
            if new < 0:
                raise GameError("❌ Negative balance.")
            await economy.apply(s, t, wallet=new - w.balance, kind="admin_set", counterparty=admin_id,
                                meta={"old": w.balance, "new": new})
            msg = f"✅ wallet of {t}: {w.balance:,} → {new:,}"
        elif action in ("ban", "unban"):
            await _exists(s, t)
            await s.execute(update(User).where(User.id == t).values(
                is_banned=(action == "ban"), ban_reason=args.get("reason") if action == "ban" else None,
                is_flagged=False if action == "unban" else User.is_flagged))
            msg = f"✅ {action} {t}"
        elif action in ("mute", "unmute"):
            await _exists(s, t)
            await s.execute(update(User).where(User.id == t).values(is_muted=(action == "mute")))
            msg = f"✅ {action} {t}"
        elif action == "maintenance":
            await users.set_state(s, "maintenance", "1" if args["on"] else "0")
            msg = f"✅ maintenance {'ON' if args['on'] else 'OFF'}"
        elif action == "broadcast":
            msg = "✅ broadcast queued"
        else:
            raise GameError("❌ Unknown admin action.")
        await _log(s, admin_id, action, t, {k: v for k, v in args.items() if k != "text"})
        return msg


async def _exists(s, uid: int) -> User:
    u = await s.get(User, uid)
    if u is None:
        raise GameError("❌ No such user.")
    return u


async def userinfo(uid: int) -> dict:
    async with db.tx() as s:
        u = await _exists(s, uid)
        w = await economy.wallet_of(s, uid)
        tx = (await s.execute(select(Transaction).where(Transaction.user_id == uid).order_by(Transaction.id.desc()).limit(10))).scalars().all()
        return dict(user=u, wallet=w, tx=list(tx))


async def recent_logs(n: int = 15) -> list[AdminLog]:
    async with db.tx() as s:
        return list((await s.execute(select(AdminLog).order_by(AdminLog.id.desc()).limit(min(n, 50)))).scalars())


async def broadcast_targets() -> list[int]:
    async with db.tx() as s:
        return list((await s.execute(select(User.id).where(User.is_banned == False, User.blocked_bot == False))).scalars())  # noqa: E712


async def server_stats() -> dict:
    from datetime import timedelta
    from sqlalchemy import func
    from database.models import Clan, GroupSetting, Pet
    from services import events
    day = utcnow() - timedelta(days=1)
    async with db.tx() as s:
        one = lambda q: s.execute(q)  # noqa: E731
        users_n = (await one(select(func.count()).select_from(User))).scalar()
        new_24h = (await one(select(func.count()).select_from(User).where(User.created_at >= day))).scalar()
        active_24h = (await one(select(func.count()).select_from(User).where(User.last_seen >= day))).scalar()
        banned = (await one(select(func.count()).select_from(User).where(User.is_banned == True))).scalar()  # noqa: E712
        groups = (await one(select(func.count()).select_from(GroupSetting))).scalar()
        from database.models import Wallet
        circ = (await one(select(func.coalesce(func.sum(Wallet.balance + Wallet.bank), 0)))).scalar()
        tx24 = (await one(select(func.count()).select_from(Transaction).where(Transaction.created_at >= day))).scalar()
        clans = (await one(select(func.count()).select_from(Clan))).scalar()
        pets = (await one(select(func.count()).select_from(Pet))).scalar()
        from database.models import Marriage, Relation
        marriages = (await one(select(func.count()).select_from(Marriage))).scalar()
        relations = (await one(select(func.count()).select_from(Relation))).scalar()
    return dict(users=users_n, new_24h=new_24h, active_24h=active_24h, banned=banned, groups=groups, circulation=int(circ),
                tx24=tx24, clans=clans, pets=pets, marriages=marriages, relations=relations, open_events=len(events.BOSSES) + len(events.DROPS))
