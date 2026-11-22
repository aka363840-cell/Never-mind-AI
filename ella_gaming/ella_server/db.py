"""Ella Gaming's own database (SQLite, WAL). It is a separate file from the bot's database and shares no tables.

All access goes through one connection guarded by a lock and executed in a worker thread (`asyncio.to_thread`),
so the event loop is never blocked. Every statement is parameterised."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS players(
  tg_id INTEGER PRIMARY KEY,
  first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '', username TEXT NOT NULL DEFAULT '',
  photo_url TEXT NOT NULL DEFAULT '', language_code TEXT NOT NULL DEFAULT '',
  nickname TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS favorites(
  tg_id INTEGER NOT NULL, slug TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(tg_id, slug));
CREATE TABLE IF NOT EXISTS recent_games(
  tg_id INTEGER NOT NULL, slug TEXT NOT NULL, last_played_at INTEGER NOT NULL, play_count INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(tg_id, slug));
CREATE INDEX IF NOT EXISTS ix_recent_user ON recent_games(tg_id, last_played_at DESC);
CREATE TABLE IF NOT EXISTS launches(
  id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL, slug TEXT NOT NULL,
  started_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL, ended_at INTEGER, duration_s INTEGER,
  platform TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_launch_user ON launches(tg_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_launch_slug ON launches(slug, started_at);
CREATE TABLE IF NOT EXISTS game_checks(
  slug TEXT PRIMARY KEY, verdict TEXT NOT NULL, reasons TEXT NOT NULL DEFAULT '[]', warnings TEXT NOT NULL DEFAULT '[]',
  http_status INTEGER, checked_at INTEGER NOT NULL, signature TEXT NOT NULL DEFAULT '', browser_checked INTEGER NOT NULL DEFAULT 0);
"""

MAX_FAVORITES = 500
MAX_RECENT = 60
MAX_LAUNCHES_PER_USER = 300
MAX_SESSION_SECONDS = 12 * 3600


class Database:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON", "busy_timeout=5000"):
                self._conn.execute(f"PRAGMA {pragma}")
            self._conn.executescript(SCHEMA)

    # -- plumbing -------------------------------------------------------------------------------------------
    def _run(self, fn):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                out = fn(self._conn)
                self._conn.execute("COMMIT")
                return out
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    async def run(self, fn):
        return await asyncio.to_thread(self._run, fn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- players --------------------------------------------------------------------------------------------
    async def upsert_player(self, u: dict) -> dict:
        now = int(time.time())

        def f(c: sqlite3.Connection):
            c.execute(
                """INSERT INTO players(tg_id,first_name,last_name,username,photo_url,language_code,created_at,last_seen_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(tg_id) DO UPDATE SET first_name=excluded.first_name,last_name=excluded.last_name,
                   username=excluded.username,photo_url=excluded.photo_url,language_code=excluded.language_code,
                   last_seen_at=excluded.last_seen_at""",
                (u["id"], str(u.get("first_name", ""))[:64], str(u.get("last_name", ""))[:64],
                 str(u.get("username", ""))[:64], str(u.get("photo_url", ""))[:300],
                 str(u.get("language_code", ""))[:12], now, now))
            return dict(c.execute("SELECT * FROM players WHERE tg_id=?", (u["id"],)).fetchone())

        return await self.run(f)

    async def get_player(self, uid: int) -> dict | None:
        def f(c):
            r = c.execute("SELECT * FROM players WHERE tg_id=?", (uid,)).fetchone()
            return dict(r) if r else None
        return await self.run(f)

    async def set_nickname(self, uid: int, nick: str) -> None:
        await self.run(lambda c: c.execute("UPDATE players SET nickname=? WHERE tg_id=?", (nick, uid)))

    # -- favourites -----------------------------------------------------------------------------------------
    async def favorites(self, uid: int) -> list[str]:
        def f(c):
            return [r["slug"] for r in c.execute("SELECT slug FROM favorites WHERE tg_id=? ORDER BY created_at DESC", (uid,))]
        return await self.run(f)

    async def set_favorite(self, uid: int, slug: str, on: bool) -> bool:
        """Returns False if the favourites limit is reached."""
        def f(c):
            if on:
                n = c.execute("SELECT COUNT(*) FROM favorites WHERE tg_id=?", (uid,)).fetchone()[0]
                exists = c.execute("SELECT 1 FROM favorites WHERE tg_id=? AND slug=?", (uid, slug)).fetchone()
                if n >= MAX_FAVORITES and not exists:
                    return False
                c.execute("INSERT OR IGNORE INTO favorites(tg_id,slug,created_at) VALUES(?,?,?)", (uid, slug, int(time.time())))
            else:
                c.execute("DELETE FROM favorites WHERE tg_id=? AND slug=?", (uid, slug))
            return True
        return await self.run(f)

    # -- recents / launches / sessions ----------------------------------------------------------------------
    async def recent(self, uid: int, limit: int = 20) -> list[dict]:
        def f(c):
            return [dict(r) for r in c.execute(
                "SELECT slug,last_played_at,play_count FROM recent_games WHERE tg_id=? ORDER BY last_played_at DESC LIMIT ?", (uid, limit))]
        return await self.run(f)

    async def start_session(self, uid: int, slug: str, platform: str) -> int:
        now = int(time.time())

        def f(c):
            cur = c.execute("INSERT INTO launches(tg_id,slug,started_at,last_seen_at,platform) VALUES(?,?,?,?,?)",
                            (uid, slug, now, now, platform[:24]))
            c.execute("""INSERT INTO recent_games(tg_id,slug,last_played_at,play_count) VALUES(?,?,?,1)
                         ON CONFLICT(tg_id,slug) DO UPDATE SET last_played_at=excluded.last_played_at, play_count=play_count+1""",
                      (uid, slug, now))
            c.execute("""DELETE FROM recent_games WHERE tg_id=? AND slug NOT IN
                         (SELECT slug FROM recent_games WHERE tg_id=? ORDER BY last_played_at DESC LIMIT ?)""", (uid, uid, MAX_RECENT))
            c.execute("""DELETE FROM launches WHERE tg_id=? AND id NOT IN
                         (SELECT id FROM launches WHERE tg_id=? ORDER BY id DESC LIMIT ?)""", (uid, uid, MAX_LAUNCHES_PER_USER))
            return cur.lastrowid
        return await self.run(f)

    async def touch_session(self, uid: int, sid: int, ended: bool) -> bool:
        """Heartbeat / end. Duration = last heartbeat - start (measurable only while the app is open)."""
        now = int(time.time())

        def f(c):
            r = c.execute("SELECT started_at,ended_at FROM launches WHERE id=? AND tg_id=?", (sid, uid)).fetchone()
            if not r:
                return False
            if r["ended_at"] is not None:
                return True
            dur = max(0, min(now - r["started_at"], MAX_SESSION_SECONDS))
            if ended:
                c.execute("UPDATE launches SET last_seen_at=?,ended_at=?,duration_s=? WHERE id=?", (now, now, dur, sid))
            else:
                c.execute("UPDATE launches SET last_seen_at=?,duration_s=? WHERE id=?", (now, dur, sid))
            return True
        return await self.run(f)

    async def history(self, uid: int, limit: int = 30) -> list[dict]:
        def f(c):
            return [dict(r) for r in c.execute(
                "SELECT slug,started_at,duration_s FROM launches WHERE tg_id=? ORDER BY id DESC LIMIT ?", (uid, limit))]
        return await self.run(f)

    async def popularity(self, days: int = 30) -> dict[str, int]:
        since = int(time.time()) - days * 86400

        def f(c):
            return {r["slug"]: r["n"] for r in c.execute(
                "SELECT slug,COUNT(*) n FROM launches WHERE started_at>=? GROUP BY slug", (since,))}
        return await self.run(f)

    # -- scan verdicts --------------------------------------------------------------------------------------
    async def save_check(self, slug: str, verdict: str, reasons: list[str], warnings: list[str], status: int | None,
                         signature: str, browser: bool) -> None:
        await self.run(lambda c: c.execute(
            """INSERT INTO game_checks(slug,verdict,reasons,warnings,http_status,checked_at,signature,browser_checked)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET verdict=excluded.verdict,reasons=excluded.reasons,
               warnings=excluded.warnings,http_status=excluded.http_status,checked_at=excluded.checked_at,
               signature=excluded.signature,browser_checked=excluded.browser_checked""",
            (slug, verdict, json.dumps(reasons), json.dumps(warnings), status, int(time.time()), signature, int(browser))))

    async def checks(self) -> dict[str, dict]:
        def f(c):
            out = {}
            for r in c.execute("SELECT * FROM game_checks"):
                d = dict(r)
                d["reasons"] = json.loads(d["reasons"])
                d["warnings"] = json.loads(d["warnings"])
                out[d["slug"]] = d
            return out
        return await self.run(f)
