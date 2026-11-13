"""SQLite backups (daily + on demand via /backup). For PostgreSQL use pg_dump instead."""
from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "backups"))


def sqlite_file(url: str) -> str | None:
    if not url.startswith("sqlite") or ":///" not in url:
        return None
    path = url.split(":///", 1)[1]
    return None if path in ("", ":memory:") else path


def _do(src_path: str, keep: int) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / f"bot_{datetime.utcnow():%Y%m%d_%H%M%S}.db"
    src, dst = sqlite3.connect(src_path), sqlite3.connect(out)
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()
    for old in sorted(BACKUP_DIR.glob("bot_*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return out


async def make_backup(url: str, keep: int = 7) -> Path | None:
    path = sqlite_file(url)
    if path is None or not os.path.exists(path):
        return None
    return await asyncio.to_thread(_do, path, keep)


async def daily_backup(url: str) -> Path | None:
    """Creates one backup per UTC day."""
    if sqlite_file(url) is None:
        return None
    today = f"bot_{datetime.utcnow():%Y%m%d}"
    if BACKUP_DIR.exists() and any(p.name.startswith(today) for p in BACKUP_DIR.glob("bot_*.db")):
        return None
    return await make_backup(url)


def _restore(src_path: str, live_path: str) -> int:
    """Validate an uploaded SQLite backup and copy it over the live DB. Returns the number of users restored."""
    chk = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    try:
        if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("The file is corrupted (integrity check failed).")
        tables = {r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"users", "wallets"} <= tables:
            raise ValueError("This is not a valid bot database (users/wallets tables missing).")
        n = chk.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        chk.close()
    src, dst = sqlite3.connect(src_path), sqlite3.connect(live_path, timeout=30)
    try:
        src.backup(dst)               # overwrites the live DB contents in one consistent step
    finally:
        dst.close(); src.close()
    return n


# what a backup must contain (table -> label). Everything lives in the one SQLite file, so all of it is saved & restored together.
TRACKED = {"users": "players", "wallets": "wallets", "marriages": "marriages", "children": "kids", "relations": "relatives",
           "properties": "houses", "clans": "clans", "pets": "pets", "app_state": "settings"}


def _summary(path: str) -> str:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        parts = [f"{con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} {label}" for t, label in TRACKED.items() if t in have]
    finally:
        con.close()
    return "📦 " + " · ".join(parts)


async def summary(path: Path | str) -> str:
    try:
        return await asyncio.to_thread(_summary, str(path))
    except Exception:
        return ""


async def restore_backup(url: str, src_path: str) -> int:
    live = sqlite_file(url)
    if live is None:
        raise ValueError("Restore works only with SQLite.")
    if os.path.exists(live):
        await make_backup(url)        # safety copy of the current data first
    n = await asyncio.to_thread(_restore, src_path, live)
    from database.session import db
    await db.create_all()             # an older backup may lack newer tables (e.g. relations): create them empty
    return n
