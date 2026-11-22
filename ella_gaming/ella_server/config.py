"""Ella Gaming configuration. Everything comes from environment variables (see .env.example).

This service is completely separate from the Ella AI bot: it has its own settings, its own database file and
never imports anything from the bot. The only value it shares with the bot is the Telegram bot *token*,
because Telegram signs Mini App `initData` with it (that is how Telegram defines the login)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

try:  # optional: .env next to the service
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return default if not raw else raw in {"1", "true", "yes", "on"}


def _list(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(x.strip() for x in os.getenv(name, default).split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    bot_token: str                       # ELLA_BOT_TOKEN - only used to verify Telegram's initData signature
    session_secret: str                  # ELLA_SESSION_SECRET - signs our own session tokens (>= 32 chars)
    public_url: str                      # ELLA_PUBLIC_URL - https origin the Mini App is served from
    data_dir: Path                       # ELLA_DATA_DIR - sqlite file + catalogue cache live here
    web_dir: Path                        # frontend files (served by this service unless hosted elsewhere)
    serve_frontend: bool                 # ELLA_SERVE_FRONTEND
    allowed_origins: tuple[str, ...]     # ELLA_ALLOWED_ORIGINS - only needed when the frontend is hosted elsewhere
    catalogue_url: str                   # ELLA_CATALOGUE_URL
    embed_hosts: tuple[str, ...]         # ELLA_EMBED_HOSTS - the ONLY hosts a game may be loaded from
    catalogue_ttl: int                   # seconds (GameZipper asks for <= 6 h client-side caching)
    ads_policy: str                      # verified_only (default) | trust_provider
    scan_ttl: int                        # seconds before a verdict is re-checked
    scan_concurrency: int
    scan_on_start: bool
    scan_browser: bool                   # runtime check in a headless browser (needs playwright). ON by default: it is what decides `review` games
    init_data_max_age: int               # seconds Telegram's auth_date may be old
    token_ttl: int                       # seconds our session token lives
    trust_proxy: bool                    # ELLA_TRUST_PROXY - read client IP from X-Forwarded-For (only behind a proxy you control)
    rate_ip_per_min: int
    rate_auth_per_min: int
    rate_write_per_min: int
    utm_source: str                      # attribution host sent to GameZipper (publisher docs ask for it)
    admin_ids: tuple[int, ...] = field(default_factory=tuple)   # ELLA_ADMIN_IDS - may read /api/admin/report
    dev: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ella_gaming.sqlite3"

    @property
    def cache_path(self) -> Path:
        return self.data_dir / "catalogue_cache.json"

    @property
    def public_origin(self) -> str:
        p = urlparse(self.public_url)
        return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


def load() -> Settings:
    public_url = os.getenv("ELLA_PUBLIC_URL", "").strip().rstrip("/")
    host = urlparse(public_url).netloc or "ella-gaming.local"
    secret = os.getenv("ELLA_SESSION_SECRET", "").strip()
    dev = _bool("ELLA_DEV")
    if len(secret) < 32:
        if not dev:
            raise RuntimeError("ELLA_SESSION_SECRET must be set to a random string of at least 32 characters "
                               "(generate one with: python -c \"import secrets;print(secrets.token_urlsafe(48))\")")
        secret = secrets.token_urlsafe(48)           # dev only: tokens die on restart
    token = os.getenv("ELLA_BOT_TOKEN", "").strip()
    if not token and not dev:
        raise RuntimeError("ELLA_BOT_TOKEN is required to verify Telegram Mini App logins")
    data_dir = Path(os.getenv("ELLA_DATA_DIR", str(ROOT / "data"))).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        bot_token=token,
        session_secret=secret,
        public_url=public_url,
        data_dir=data_dir,
        web_dir=Path(os.getenv("ELLA_WEB_DIR", str(ROOT / "web"))).resolve(),
        serve_frontend=_bool("ELLA_SERVE_FRONTEND", True),
        allowed_origins=_list("ELLA_ALLOWED_ORIGINS"),
        catalogue_url=os.getenv("ELLA_CATALOGUE_URL", "https://gamezipper.com/api/games.json").strip(),
        embed_hosts=tuple(h.lower() for h in _list("ELLA_EMBED_HOSTS", "gamezipper.com")),
        catalogue_ttl=_int("ELLA_CATALOGUE_TTL", 6 * 3600),
        ads_policy=os.getenv("ELLA_ADS_POLICY", "verified_only").strip().lower(),
        scan_ttl=_int("ELLA_SCAN_TTL", 7 * 86400),
        scan_concurrency=max(1, min(_int("ELLA_SCAN_CONCURRENCY", 6), 20)),
        scan_on_start=_bool("ELLA_SCAN_ON_START", True),
        scan_browser=_bool("ELLA_SCAN_BROWSER", True),
        init_data_max_age=_int("ELLA_INITDATA_MAX_AGE", 86400),
        token_ttl=_int("ELLA_TOKEN_TTL", 6 * 3600),
        trust_proxy=_bool("ELLA_TRUST_PROXY"),
        rate_ip_per_min=_int("ELLA_RATE_IP_PER_MIN", 240),
        rate_auth_per_min=_int("ELLA_RATE_AUTH_PER_MIN", 20),
        rate_write_per_min=_int("ELLA_RATE_WRITE_PER_MIN", 90),
        utm_source=os.getenv("ELLA_UTM_SOURCE", host).strip() or host,
        admin_ids=tuple(int(x) for x in _list("ELLA_ADMIN_IDS") if x.isdigit()),
        dev=dev,
    )
