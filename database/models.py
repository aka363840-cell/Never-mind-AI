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
    deleted_account: Mapped[bool] = mapped_column(Boolean, default=False)  # Telegram reported the account as deactivated
    # Has this person ever opened a private chat with the bot? True = yes (we can DM them), False = only ever seen in groups
    # (Telegram refuses to DM them), None = not checked yet (rows from before this column existed; /syncusers sorts them).
    started_dm: Mapped[bool | None] = mapped_column(Boolean, default=False)


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
    removed: Mapped[bool] = mapped_column(Boolean, default=False)  # bot was kicked/left - row is kept (history), just flagged
    removed_at: Mapped[datetime | None] = mapped_column(TS)


class GroupStat(Base):
    __tablename__ = "group_stats"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kills: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)


class GroupMember(Base):
    """Who the bot has seen in which group (written once per person per group per day by services/waifu.py, never per
    message). Telegram has no 'list members' call for bots, so this roster is what /waifu picks from."""
    __tablename__ = "group_members"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)


class Afk(Base):
    """/afk: one row per away player (removed when they write again). Part of the DB, so backups keep it."""
    __tablename__ = "afk"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    reason: Mapped[str] = mapped_column(String(120), default="")
    since: Mapped[datetime] = mapped_column(TS, default=utcnow)


class WhisperRecent(Base):
    """Who each person whispered to lately, so they can pick them from a list instead of typing the name / id again."""
    __tablename__ = "whisper_recent"
    sender_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    target_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AvatarCache(Base):
    """Last profile-photo file id the bot could see for a person. Telegram hides some photos (privacy) or a lookup fails at the
    wrong moment; the remembered one keeps /waifu (and others) working. Lives in the DB, so backups keep it."""
    __tablename__ = "avatar_cache"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    file_id: Mapped[str] = mapped_column(String(255))
    checked_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class WordGame(Base):
    """WordSeek: the game running in a chat right now (group, or a private chat). Kept in the DB so a redeploy doesn't lose it."""
    __tablename__ = "wordseek_games"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    word: Mapped[str] = mapped_column(String(8))
    guesses: Mapped[str] = mapped_column(Text, default="[]")          # JSON: [[word, user_id, name], ...]
    started_by: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class WordWin(Base):
    """WordSeek: one row per won game (scores and leaderboards are sums of these, so any time period works)."""
    __tablename__ = "wordseek_wins"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    points: Mapped[int] = mapped_column(Integer)
    guesses: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)


class WordHistory(Base):
    """WordSeek: words already used in a chat (so the same word does not come back soon)."""
    __tablename__ = "wordseek_history"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    word: Mapped[str] = mapped_column(String(8))
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class WaifuPick(Base):
    """/waifu: one row per player = their current pick. Entirely separate from marriages / relations / family."""
    __tablename__ = "waifu_picks"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    waifu_id: Mapped[int] = mapped_column(BigInteger)
    bond: Mapped[int] = mapped_column(Integer)
    picked_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


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


# ------------------------------------------------------------------ Academy Life (standalone school sim)
class AcademySeason(Base):
    """One season = 7 real days (start_at .. end_at), shared by all players. status: active | ended."""
    __tablename__ = "academy_seasons"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, unique=True)
    start_at: Mapped[datetime] = mapped_column(TS)
    end_at: Mapped[datetime] = mapped_column(TS)
    theme: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)


class AcademyStudent(Base):
    """A player enrolled in one season. Energy is stored lazily: `energy` as of `energy_at` (regen is computed on read)."""
    __tablename__ = "academy_students"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    stream: Mapped[str] = mapped_column(String(16))
    club: Mapped[str] = mapped_column(String(16))
    knowledge: Mapped[int] = mapped_column(Integer, default=10)
    charisma: Mapped[int] = mapped_column(Integer, default=10)
    fitness: Mapped[int] = mapped_column(Integer, default=10)
    creativity: Mapped[int] = mapped_column(Integer, default=10)
    energy: Mapped[int] = mapped_column(Integer, default=100)
    energy_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    late_join: Mapped[bool] = mapped_column(Boolean, default=False)
    enrolled_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("season_id", "user_id", name="uq_academy_student_season_user"),)


class AcademyDaily(Base):
    """Part 2: one row per student and season day (1..7) with the counters of that day. Used by Part 2 itself (limits, the
    diamond cap, the study streak) and later by exams and awards. `attended` is 0/1. `diamonds` = diamonds earned today."""
    __tablename__ = "academy_daily"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("academy_students.id", ondelete="CASCADE"))
    day: Mapped[int] = mapped_column(Integer)
    study_count: Mapped[int] = mapped_column(Integer, default=0)
    attended: Mapped[int] = mapped_column(Integer, default=0)
    hangouts: Mapped[int] = mapped_column(Integer, default=0)
    club_count: Mapped[int] = mapped_column(Integer, default=0)
    parttime_count: Mapped[int] = mapped_column(Integer, default=0)
    energy_spent: Mapped[int] = mapped_column(Integer, default=0)
    rest_count: Mapped[int] = mapped_column(Integer, default=0)
    diamonds: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("student_id", "day", name="uq_academy_daily_student_day"),)


class AcademyClubPoints(Base):
    """Part 2: the season score of each club (all students of a club add to the same row). Key = (season_id, club)."""
    __tablename__ = "academy_club_points"
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"), primary_key=True)
    club: Mapped[str] = mapped_column(String(16), primary_key=True)
    points: Mapped[int] = mapped_column(Integer, default=0)


class AcademyExam(Base):
    """Part 3: the window of one exam in one season (midterm = Day 4, finals = Day 6). Created lazily by services/academy_exams."""
    __tablename__ = "academy_exams"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))                       # midterm | finals
    opens_at: Mapped[datetime] = mapped_column(TS)
    closes_at: Mapped[datetime] = mapped_column(TS)
    __table_args__ = (UniqueConstraint("season_id", "kind", name="uq_academy_exam_season_kind"),)


class AcademyAttempt(Base):
    """Part 3: a student's ONE attempt at an exam. state: active | done (finished by the player) | expired (the window closed
    first). `cards` = the drawn card keys in order (comma separated); `round_started_at` is set while a question is on screen
    (the 25 s are checked against it) and NULL while the player is looking at a result."""
    __tablename__ = "academy_attempts"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("academy_students.id", ondelete="CASCADE"), index=True)
    exam_kind: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    score: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(10), default="active", index=True)
    cards: Mapped[str] = mapped_column(String(512), default="")
    round_started_at: Mapped[datetime | None] = mapped_column(TS)
    focus_used: Mapped[int] = mapped_column(Integer, default=0)
    reward: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("student_id", "exam_kind", name="uq_academy_attempt_student_kind"),)


class AcademyRound(Base):
    """Part 3: one answered round of an attempt (n = 1..8). choice = button index as text, or 'timeout'."""
    __tablename__ = "academy_rounds"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("academy_attempts.id", ondelete="CASCADE"), index=True)
    n: Mapped[int] = mapped_column(Integer)
    card_key: Mapped[str] = mapped_column(String(40))
    choice: Mapped[str] = mapped_column(String(10))
    roll: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    focused: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("attempt_id", "n", name="uq_academy_round_attempt_n"),)


class AcademyChatMember(Base):
    """Part 3: which players used an exam command in which group (for the group /classrank). One row per pair, written
    once (insert-ignore) when a player uses /exam, /grades or /classrank in a group; never per message."""
    __tablename__ = "academy_chat_members"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)


# ---------------------------------------------------------------- Academy Life, Part 4: social (rivals, crushes, club battles, buddies)
class AcademyRival(Base):
    """Part 4: one row PER PLAYER and season (a pair = two mirrored rows). rival_id NULL = a bot rival (bot_name / bot_seed)."""
    __tablename__ = "academy_rivals"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rival_id: Mapped[int | None] = mapped_column(BigInteger)
    bot_name: Mapped[str] = mapped_column(String(40), default="")
    bot_seed: Mapped[int] = mapped_column(Integer, default=0)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("season_id", "user_id", name="uq_academy_rival_season_user"),)


class AcademyRivalDuel(Base):
    """Part 4: ONE duel per rival pair and season. user_a = the smaller id of the pair (or the player of a bot duel), user_b NULL = bot.
    state open | done; result a | b | tie (NULL while open); walkover = one side never played."""
    __tablename__ = "academy_rival_duels"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"), index=True)
    user_a: Mapped[int] = mapped_column(BigInteger)
    user_b: Mapped[int | None] = mapped_column(BigInteger)
    cards: Mapped[str] = mapped_column(String(128), default="")
    state: Mapped[str] = mapped_column(String(8), default="open", index=True)
    result: Mapped[str | None] = mapped_column(String(8))
    walkover: Mapped[bool] = mapped_column(Boolean, default=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)         # the group where it was started (for the ONE result announcement)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (UniqueConstraint("season_id", "user_a", name="uq_academy_duel_season_a"),)


class AcademyRivalRound(Base):
    """Part 4: one played card of a duel. user_id 0 = the bot side."""
    __tablename__ = "academy_rival_rounds"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    duel_id: Mapped[int] = mapped_column(ForeignKey("academy_rival_duels.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    n: Mapped[int] = mapped_column(Integer)
    card_key: Mapped[str] = mapped_column(String(40))
    choice: Mapped[int] = mapped_column(Integer, default=0)
    roll: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("duel_id", "user_id", "n", name="uq_academy_duel_round"),)


class AcademyBadge(Base):
    """Part 4: season badges and badge points. kind = rival | club. One row per season, player and kind."""
    __tablename__ = "academy_badges"
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    points: Mapped[int] = mapped_column(Integer, default=0)


class AcademyCrush(Base):
    """Part 4: a SECRET crush. Private by design: nothing but the sender (and the mutual reveal) ever reads it. One active crush
    per player and season (UNIQUE); /crushoff deletes the row; a mutual crush turns into a couple and both rows are deleted."""
    __tablename__ = "academy_crushes"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"), index=True)
    from_user: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    to_user: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)         # group of the command, ONLY used for the mutual announcement
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("season_id", "from_user", name="uq_academy_crush_season_from"),)


class AcademyCouple(Base):
    """Part 4: a Campus Couple (user_a < user_b), created only by a mutual crush. Standalone: no link to the marriage tables."""
    __tablename__ = "academy_couples"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"), index=True)
    user_a: Mapped[int] = mapped_column(BigInteger, index=True)
    user_b: Mapped[int] = mapped_column(BigInteger, index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("season_id", "user_a", "user_b", name="uq_academy_couple_pair"),)


class AcademyCoupleDate(Base):
    """Part 4: one /campusdate per couple and season day (the PRIMARY KEY is the once-a-day lock)."""
    __tablename__ = "academy_couple_dates"
    couple_id: Mapped[int] = mapped_column(ForeignKey("academy_couples.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[int] = mapped_column(Integer, primary_key=True)
    by_user: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AcademyBattle(Base):
    """Part 4: the club battle of a season (UNIQUE per season, opened by a tick on Day 3). state open | closed; winner = club key."""
    __tablename__ = "academy_battles"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"), unique=True)
    card_key: Mapped[str] = mapped_column(String(40))
    opens_at: Mapped[datetime] = mapped_column(TS)
    closes_at: Mapped[datetime] = mapped_column(TS)
    state: Mapped[str] = mapped_column(String(8), default="open", index=True)
    winner: Mapped[str | None] = mapped_column(String(16))


class AcademyBattlePlay(Base):
    """Part 4: a member's ONE battle round (PRIMARY KEY = once per battle). points = play_round points (can be negative)."""
    __tablename__ = "academy_battle_plays"
    battle_id: Mapped[int] = mapped_column(ForeignKey("academy_battles.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    club: Mapped[str] = mapped_column(String(16))
    choice: Mapped[int] = mapped_column(Integer, default=0)
    roll: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    played_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AcademyBuddy(Base):
    """Part 4: a study-buddy request (state pending) or pair (state active). user_a = who asked, user_b = who must accept."""
    __tablename__ = "academy_buddies"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("academy_seasons.id", ondelete="CASCADE"), index=True)
    user_a: Mapped[int] = mapped_column(BigInteger, index=True)
    user_b: Mapped[int] = mapped_column(BigInteger, index=True)
    state: Mapped[str] = mapped_column(String(8), default="pending")
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("season_id", "user_a", "user_b", name="uq_academy_buddy_pair"),)


class AcademyBuddyBonus(Base):
    """Part 4: the buddy study bonus was given to this player on this season day (PRIMARY KEY = once a day)."""
    __tablename__ = "academy_buddy_bonus"
    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    day: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class AcademySocialRun(Base):
    """Part 4: 'this one-off job already ran for this season' (kind = rivals). The PRIMARY KEY makes a tick idempotent."""
    __tablename__ = "academy_social_runs"
    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AcademyAdventureRun(Base):
    """Part 5: one adventure run of a student. `node` = where the story is now, `state_json` = flags / counters (see
    services/academy_adventure_logic.py), `step` = how many buttons were pressed (the optimistic-lock token: a stale or
    double press loses the UPDATE ... WHERE step = :step), `finished` + `ending`: an ending key, or 'expired' / 'quit' (no reward).
    A run that has had no press for 30 minutes expires."""
    __tablename__ = "academy_adventure_runs"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("academy_students.id", ondelete="CASCADE"), index=True)
    adventure_key: Mapped[str] = mapped_column(String(32))
    node: Mapped[str] = mapped_column(String(48))
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ending: Mapped[str | None] = mapped_column(String(48))
    step: Mapped[int] = mapped_column(Integer, default=0)


class AcademyAdventureTrophy(Base):
    """Part 5: a title or item earned from an adventure ending. PRIMARY KEY = once per season, player and trophy."""
    __tablename__ = "academy_adventure_trophies"
    season_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(64))
    adventure_key: Mapped[str] = mapped_column(String(32))
    earned_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


# ------------------------------------------------------------------ Academy Life, Part 6: reminders, recaps, graduation
class AcademyNotifyLog(Base):
    """Part 6: one row per message that was (or is being) sent. UNIQUE(kind, key, target_id) is the 'send once' lock: a sender
    INSERTs the row first and only sends if the insert won, so a restart, a retry or two overlapping ticks never send twice.
    target_id = chat id (groups) or user id (DMs); season_id is only for housekeeping."""
    __tablename__ = "academy_notify_log"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(24))
    key: Mapped[str] = mapped_column(String(64))
    season_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    target_id: Mapped[int] = mapped_column(BigInteger)
    sent_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("kind", "key", "target_id", name="uq_academy_notify"),)


class AcademyUserPrefs(Base):
    """Part 6: per-user notification settings. remind_on = /academyremind on|off (off = no unsolicited Academy DMs at all).
    unreachable = the bot could not DM this user (blocked it / never started it); unreachable_at = when we noticed."""
    __tablename__ = "academy_user_prefs"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    remind_on: Mapped[bool] = mapped_column(Boolean, default=True)
    unreachable: Mapped[bool] = mapped_column(Boolean, default=False)
    unreachable_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class AcademyGroupSeen(Base):
    """Part 6: when the bot last saw a message in a group (written at most every 6 hours per group by services/academy_seen.py,
    NEVER per message). fails = how many Academy messages in a row the group refused (reset when someone writes there)."""
    __tablename__ = "academy_group_seen"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_seen: Mapped[datetime] = mapped_column(TS, default=utcnow)
    fails: Mapped[int] = mapped_column(Integer, default=0)


class AcademyHallOfFame(Base):
    """Part 6: every title awarded at a graduation ceremony (valedictorian, topper, backbencher, perfect_attendance, club_champion).
    UNIQUE(season_id, user_id, title) = awarded once. name is copied at award time. detail = stream / club key where it applies.
    season_id and user_id are plain integers (no FK). A staff /academyreset deletes the hall with everything else."""
    __tablename__ = "academy_hall_of_fame"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    season_id: Mapped[int] = mapped_column(Integer, index=True)
    season_number: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128), default="Player")
    title: Mapped[str] = mapped_column(String(24))
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (UniqueConstraint("season_id", "user_id", "title", name="uq_academy_hof"),)
