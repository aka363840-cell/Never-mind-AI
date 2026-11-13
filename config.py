"""Runtime configuration (secrets & infrastructure). Game balance lives in game/rules.py."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _ids(name: str) -> frozenset[int]:
    return frozenset(int(x) for x in os.getenv(name, "").replace(" ", "").split(",") if x)


def _db_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_id: int
    admin_ids: frozenset[int]
    bot_names: tuple[str, ...]
    database_url: str
    redis_url: str | None
    log_level: str
    # AI
    ai_provider: str
    gemini_api_key: str
    gemini_api_keys: tuple[str, ...]
    gemini_models: tuple[str, ...]
    ai_context_messages: int
    ai_conversation_ttl_min: int
    ai_user_rpm: int
    ai_user_daily: int
    ai_global_rpm: int
    ai_max_input_chars: int
    ai_max_output_tokens: int


def _keys() -> tuple[str, ...]:
    """GEMINI_API_KEYS (comma / space / newline separated) + the legacy GEMINI_API_KEY, deduplicated, max 5."""
    raw = os.getenv("GEMINI_API_KEYS", "") + "," + os.getenv("GEMINI_API_KEY", "")
    keys = [k for k in raw.replace("\n", ",").replace(" ", ",").split(",") if k.strip()]
    return tuple(dict.fromkeys(keys))[:5]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    models = tuple(
        m.strip()
        for m in os.getenv("GEMINI_MODELS", "gemini-3.6-flash,gemini-3.5-flash,gemini-3.1-flash-lite,gemini-flash-latest,gemini-flash-lite-latest").split(",")
        if m.strip()
    )
    return Settings(
        bot_token=os.getenv("BOT_TOKEN", ""),
        owner_id=_int("OWNER_ID", 0),
        admin_ids=_ids("ADMIN_IDS"),
        bot_names=tuple(n.strip() for n in os.getenv("BOT_NAMES", "").split(",") if n.strip()),
        database_url=_db_url(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot.db")),
        redis_url=os.getenv("REDIS_URL") or None,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ai_provider=os.getenv("AI_PROVIDER", "gemini").lower(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_api_keys=_keys(),
        gemini_models=models,
        ai_context_messages=_int("AI_CONTEXT_MESSAGES", 6),
        ai_conversation_ttl_min=_int("AI_CONVERSATION_TTL_MIN", 60),
        ai_user_rpm=_int("AI_USER_RPM", 6),
        ai_user_daily=_int("AI_USER_DAILY", 120),
        ai_global_rpm=_int("AI_GLOBAL_RPM", 10),
        ai_max_input_chars=_int("AI_MAX_INPUT_CHARS", 800),
        ai_max_output_tokens=_int("AI_MAX_OUTPUT_TOKENS", 450),
    )
