from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

BigPK = BigInteger().with_variant(Integer(), "sqlite")  # autoincrement-friendly in both DBs


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything we store is UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


TS = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    username_lc: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str] = mapped_column(String(128), default="Player")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(TS, default=utcnow)
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    kills: Mapped[int] = mapped_column(Integer, default=0)
    deaths: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    equipped_power: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(64))
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)  # excluded from rankings
    ban_reason: Mapped[str | None] = mapped_column(String(256))
    blocked_bot: Mapped[bool] = mapped_column(Boolean, default=False)


class Wallet(Base):
    __tablename__ = "wallets"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    bank: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_wallet_balance"),
                      CheckConstraint("bank >= 0", name="ck_wallet_bank"))


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    amount: Mapped[int] = mapped_column(BigInteger, default=0)       # wallet delta
    bank_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    counterparty_id: Mapped[int | None] = mapped_column(BigInteger)
    ref: Mapped[str | None] = mapped_column(String(128), unique=True)  # idempotency key
    meta: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (Index("ix_tx_user_created", "user_id", "created_at"),
                      Index("ix_tx_kind_created", "kind", "created_at"))


class Cooldown(Base):
    __tablename__ = "cooldowns"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(TS)


class CombatRecord(Base):
    __tablename__ = "combat_records"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    attacker_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    defender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16))  # kill | revenge | duel
    winner_id: Mapped[int | None] = mapped_column(BigInteger)
    reward: Mapped[int] = mapped_column(BigInteger, default=0)
    log: Mapped[str | None] = mapped_column(Text)
    revenged: Mapped[bool] = mapped_column(Boolean, default=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (Index("ix_cr_def_created", "defender_id", "created_at"),
                      Index("ix_cr_att_created", "attacker_id", "created_at"))


class Duel(Base):
    __tablename__ = "duels"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    challenger_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    wager: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    challenger_choice: Mapped[str | None] = mapped_column(String(1))
    target_choice: Mapped[str | None] = mapped_column(String(1))
    winner_id: Mapped[int | None] = mapped_column(BigInteger)
    escrowed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS)


class Power(Base):
    __tablename__ = "powers"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    price: Mapped[int] = mapped_column(BigInteger)
    activation_cost: Mapped[int] = mapped_column(BigInteger)
    cooldown_s: Mapped[int] = mapped_column(Integer)
    duration_s: Mapped[int] = mapped_column(Integer)
    max_level: Mapped[int] = mapped_column(Integer, default=5)
    description: Mapped[str] = mapped_column(Text)
    limitation: Mapped[str] = mapped_column(Text)


class UserPower(Base):
    __tablename__ = "user_powers"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    power_key: Mapped[str] = mapped_column(ForeignKey("powers.key"), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    active_until: Mapped[datetime | None] = mapped_column(TS)
    charges: Mapped[int] = mapped_column(Integer, default=0)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(TS)


class ShopItem(Base):
    __tablename__ = "shop_items"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    category: Mapped[str] = mapped_column(String(24), index=True)
    name: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8), default="")
    price: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(Text, default="")
    attack: Mapped[int] = mapped_column(Integer, default=0)
    defence: Mapped[int] = mapped_column(Integer, default=0)
    health: Mapped[int] = mapped_column(Integer, default=0)
    max_level: Mapped[int] = mapped_column(Integer, default=5)
    stackable: Mapped[bool] = mapped_column(Boolean, default=False)


class InventoryItem(Base):
    __tablename__ = "inventory"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_key: Mapped[str] = mapped_column(ForeignKey("shop_items.key"))
    qty: Mapped[int] = mapped_column(Integer, default=1)
    level: Mapped[int] = mapped_column(Integer, default=1)
    equipped: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("user_id", "item_key", name="uq_inventory_user_item"),
                      CheckConstraint("qty >= 0", name="ck_inventory_qty"))


class Protection(Base):
    __tablename__ = "protection"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(TS)
    last_activated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Bounty(Base):
    __tablename__ = "bounties"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    placer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|claimed|expired|cancelled
    claimed_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TS)
    __table_args__ = (CheckConstraint("amount > 0", name="ck_bounty_amount"),
                      Index("ix_bounty_target_status", "target_id", "status"))


class Mission(Base):
    __tablename__ = "missions"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mission_key: Mapped[str] = mapped_column(String(32))
    period: Mapped[str] = mapped_column(String(8))  # daily | weekly
    period_start: Mapped[date] = mapped_column(Date)
    event: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(80))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    goal: Mapped[int] = mapped_column(Integer)
    reward: Mapped[int] = mapped_column(Integer)
    claimed: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("user_id", "mission_key", "period_start", name="uq_mission_period"),)


class Achievement(Base):
    __tablename__ = "achievements"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    unlocked_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AIConversation(Base):
    __tablename__ = "ai_conversations"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)   # deliberately no FK: AI is independent of the game
    chat_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_ai_conv"),)


class AIMessage(Base):
    __tablename__ = "ai_messages"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("ai_conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(8))  # user | model
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AdminLog(Base):
    __tablename__ = "admin_logs"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class GroupSetting(Base):
    __tablename__ = "group_settings"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(String(256))
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    games_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class GroupStat(Base):
    __tablename__ = "group_stats"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kills: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)


class Marriage(Base):
    """One row per couple (user_a is the smaller id). A player can be in at most one marriage (enforced under row locks)."""
    __tablename__ = "marriages"
    user_a: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_b: Mapped[int] = mapped_column(BigInteger, unique=True)
    married_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class CoupleLove(Base):
    """Love points of a marriage (key = the smaller user id of the couple)."""
    __tablename__ = "couple_love"
    user_a: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    love: Mapped[int] = mapped_column(Integer, default=0)


class Child(Base):
    """A child born to / adopted by one or two players. Both parents see it (joint custody after a divorce)."""
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    parent_a: Mapped[int] = mapped_column(BigInteger, index=True)
    parent_b: Mapped[int | None] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(32))
    gender: Mapped[str] = mapped_column(String(1))      # b | g
    adopted: Mapped[bool] = mapped_column(Boolean, default=False)
    born_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Relation(Base):
    """Extended family & friends (papa, bhai, dada, chacha, bestie ...). One row per pair, whichever side asked.
    `kind` = the role user_b has for user_a (e.g. kind='papa' -> user_b is user_a's father)."""
    __tablename__ = "relations"
    __table_args__ = (UniqueConstraint("user_a", "user_b", name="uq_relation_pair"),)
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    user_a: Mapped[int] = mapped_column(BigInteger, index=True)
    user_b: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Property(Base):
    __tablename__ = "properties"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    key: Mapped[str] = mapped_column(String(16), primary_key=True)
    bought_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AppState(Base):
    """Tiny persistent key/value (maintenance flag survives restarts)."""
    __tablename__ = "app_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))


class Clan(Base):
    __tablename__ = "clans"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(24))
    name_lc: Mapped[str] = mapped_column(String(24), unique=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    bank: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (CheckConstraint("bank >= 0", name="ck_clan_bank"),)


class ClanMember(Base):
    __tablename__ = "clan_members"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    clan_id: Mapped[int] = mapped_column(ForeignKey("clans.id", ondelete="CASCADE"), index=True)
    joined_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Pet(Base):
    __tablename__ = "pets"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    species: Mapped[str] = mapped_column(String(16))
    rarity: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(24))
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class UserStat(Base):
    """Lifetime counters (dungeon clears, expeditions, heists, ...) that drive achievements."""
    __tablename__ = "user_stats"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, default=0)


class Expedition(Base):
    """A pet that is away on an expedition. One row per player; deleted atomically when collected."""
    __tablename__ = "expeditions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(TS, index=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class DungeonRun(Base):
    """Live dungeon run (JSON state). Persisted so a restart never eats someone's run."""
    __tablename__ = "dungeon_runs"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ---------------------------------------------------------------- romance (engagement / wedding / anniversary)
class Engagement(Base):
    """A pending-wedding engagement. One row per couple (user_a is the smaller id); at most one per player (enforced under row locks)."""
    __tablename__ = "engagements"
    user_a: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_b: Mapped[int] = mapped_column(BigInteger, unique=True)
    ring_cost: Mapped[int] = mapped_column(Integer, default=0)
    engaged_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Wedding(Base):
    """The wedding ceremony of the CURRENT marriage (user_a is the smaller id). It counts only while held_at >= marriages.married_at,
    so a divorce needs no cleanup: a later marriage of the same couple simply has no ceremony yet."""
    __tablename__ = "weddings"
    user_a: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_b: Mapped[int] = mapped_column(BigInteger, unique=True)
    venue: Mapped[str] = mapped_column(String(16))
    cost: Mapped[int] = mapped_column(Integer, default=0)
    held_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class CoupleFlag(Base):
    """Once-only markers per couple (user_a < user_b): 'wedding' bonus, 'ms30:<uid>' milestone, 'yr2' anniversary, ...
    The unique primary key is the guard: INSERT .. ON CONFLICT DO NOTHING decides who gets to pay a one-time reward. Never deleted on divorce,
    so divorcing and re-marrying cannot re-earn a reward."""
    __tablename__ = "couple_flags"
    user_a: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_b: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ---------------------------------------------------------------- romance B / C (gifts, bonds, honeymoon, love streak)
class Bond(Base):
    """Bond points between two players who are relatives / friends in /family (user_a is the smaller id)."""
    __tablename__ = "bonds"
    user_a: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_b: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    points: Mapped[int] = mapped_column(Integer, default=0)


class BondDaily(Base):
    """Love / bond points a SENDER has created today (UTC) - enforces the daily cap per sender."""
    __tablename__ = "bond_daily"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    points: Mapped[int] = mapped_column(Integer, default=0)


class Honeymoon(Base):
    """The honeymoon trip of a couple (user_a is the smaller id). collected=True once the bonus was paid."""
    __tablename__ = "honeymoons"
    user_a: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_b: Mapped[int] = mapped_column(BigInteger, unique=True)
    destination: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(TS, index=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    collected: Mapped[bool] = mapped_column(Boolean, default=False)


class LoveStreak(Base):
    """Consecutive days on which BOTH partners checked in (user_a is the smaller id)."""
    __tablename__ = "love_streaks"
    user_a: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_b: Mapped[int] = mapped_column(BigInteger, unique=True)
    streak: Mapped[int] = mapped_column(Integer, default=0)
    best: Mapped[int] = mapped_column(Integer, default=0)
    last_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    a_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    b_day: Mapped[date | None] = mapped_column(Date, nullable=True)
