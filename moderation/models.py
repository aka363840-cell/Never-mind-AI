"""Moderation tables. Own declarative Base => independent from the game's metadata (Alembic / create_all of the game never see them).
All timestamps are integer UNIX seconds (UTC): no timezone surprises between SQLite and PostgreSQL."""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

BigPK = BigInteger().with_variant(Integer(), "sqlite")


class ModBase(DeclarativeBase):
    pass


class ModMeta(ModBase):
    __tablename__ = "mod_meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), default="")


class ModSettings(ModBase):
    __tablename__ = "mod_settings"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    data: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[int] = mapped_column(BigInteger, default=0)


class ModWarn(ModBase):
    __tablename__ = "mod_warns"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    by_id: Mapped[int] = mapped_column(BigInteger, default=0)
    reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (Index("ix_mod_warns_chat_user", "chat_id", "user_id"),)


class ModAction(ModBase):
    __tablename__ = "mod_actions"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    actor_id: Mapped[int] = mapped_column(BigInteger, default=0)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(300), default="")
    duration: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result: Mapped[str] = mapped_column(String(12), default="ok")           # ok | failed | skipped
    detail: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(24), default="manual")      # manual | auto | bulk | telegram | scheduler
    created_at: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (Index("ix_mod_actions_chat_time", "chat_id", "created_at"),
                      Index("ix_mod_actions_target", "chat_id", "target_id"))


class ModPunishment(ModBase):
    __tablename__ = "mod_punishments"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(12))                           # ban | mute | restrict
    expires_at: Mapped[int] = mapped_column(BigInteger)
    prev_perms: Mapped[str | None] = mapped_column(Text, nullable=True)     # JSON of the permissions to restore
    status: Mapped[str] = mapped_column(String(12), default="active")       # active | expiring | done | cancelled | failed
    retries: Mapped[int] = mapped_column(Integer, default=0)
    by_id: Mapped[int] = mapped_column(BigInteger, default=0)
    reason: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[int] = mapped_column(BigInteger)
    claimed_at: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (Index("ix_mod_punish_due", "status", "expires_at"), Index("ix_mod_punish_chat_user", "chat_id", "user_id"))


class ModMember(ModBase):
    __tablename__ = "mod_members"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    username_lc: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="member")      # member | restricted | left | banned | admin
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[int] = mapped_column(BigInteger, default=0)
    last_seen: Mapped[int] = mapped_column(BigInteger, default=0)
    msgs: Mapped[int] = mapped_column(Integer, default=0)


class ModMsg(ModBase):
    """Recent message ids (kept ~48 h): the only way to 'delete everything from X' / clean downloads, since Bot API has no such call."""
    __tablename__ = "mod_msgs"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="text")
    ts: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (Index("ix_mod_msgs_user", "chat_id", "user_id"), Index("ix_mod_msgs_ts", "ts"))


class ModFilter(ModBase):
    __tablename__ = "mod_filters"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(10))                           # word | phrase | link | domain
    pattern: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(10), default="delete")       # delete | warn | mute | kick | ban | reply
    duration: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    response: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, default=0)


class ModApproved(ModBase):
    __tablename__ = "mod_approved"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    by_id: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, default=0)


class ModReport(ModBase):
    __tablename__ = "mod_reports"
    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reporter_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    excerpt: Mapped[str] = mapped_column(String(300), default="")
    reason: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(10), default="pending")      # pending | resolved | dismissed
    created_at: Mapped[int] = mapped_column(BigInteger)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolution: Mapped[str] = mapped_column(String(24), default="")


class ModPending(ModBase):
    """A confirmation request (YES / NO). Persisted so it survives restarts and can be claimed exactly once."""
    __tablename__ = "mod_pending"
    token: Mapped[str] = mapped_column(String(24), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    issuer_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(12), default="pending")      # pending | running | done | cancelled | expired | interrupted
    created_at: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result: Mapped[str] = mapped_column(Text, default="")


class ModCaptcha(ModBase):
    __tablename__ = "mod_captcha"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    answer: Mapped[str] = mapped_column(String(16), default="ok")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger)
