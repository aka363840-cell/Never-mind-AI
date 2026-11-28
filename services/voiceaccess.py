"""Voice replies: each person's chosen voice, the owner's global on/off switch (/aivoice) and the per-person hourly limit.
Voice is open to everybody (no approval list). Choices and the switch are saved in a small JSON file in the data volume so
they survive restarts; the hourly counter is in memory (a restart only resets it)."""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)


def _is_owner(uid: int) -> bool:
    try:
        from config import get_settings
        return bool(uid) and uid == get_settings().owner_id
    except Exception:
        return False


LIMIT = int(os.getenv("AI_VOICE_OUT_PER_HOUR", "10") or 10)    # voice messages per person ...
WINDOW = 3600              # ... per hour
_path = Path(os.getenv("DATA_DIR", "/data")) / "voice_access.json"
_data: dict | None = None  # {"enabled": bool, "names": {"<uid>": "<voice>"}}
_used: dict[int, deque] = {}


def _load() -> dict:
    global _data
    if _data is None:
        try:
            _data = json.loads(_path.read_text("utf-8"))
        except Exception:
            _data = {}
        _data.setdefault("enabled", True)
        _data.setdefault("names", {})
    return _data


def _save() -> None:
    try:
        _path.parent.mkdir(parents=True, exist_ok=True)
        tmp = _path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_load()), "utf-8")
        tmp.replace(_path)
    except OSError:
        log.warning("could not save voice file %s", _path, exc_info=True)


def enabled() -> bool:
    return bool(_load()["enabled"])


def set_enabled(on: bool) -> None:
    _load()["enabled"] = bool(on)
    _save()


def get_name(uid: int) -> str:
    return _load()["names"].get(str(uid), "")


def set_name(uid: int, name: str) -> None:
    _load()["names"][str(uid)] = name
    _save()


def wait_secs(uid: int, now: float | None = None) -> int:
    """0 = this person may send another voice message now; otherwise seconds until the oldest one of the hour expires."""
    if _is_owner(uid):
        return 0
    now = time.time() if now is None else now
    q = _used.setdefault(uid, deque())
    while q and now - q[0] >= WINDOW:
        q.popleft()
    return 0 if len(q) < LIMIT else int(WINDOW - (now - q[0])) + 1


def count(uid: int, now: float | None = None) -> None:
    """Call once a voice message was really sent."""
    _used.setdefault(uid, deque()).append(time.time() if now is None else now)


# ---- voice notes people SEND to Ella (each one costs a Whisper request): a separate hourly limit per person
_in_used: dict[int, deque] = {}


def in_limit() -> int:
    try:
        return max(1, int(os.getenv("AI_VOICE_IN_PER_HOUR", "10")))
    except ValueError:
        return 10


def in_wait_secs(uid: int, now: float | None = None) -> int:
    if _is_owner(uid):
        return 0
    now = time.time() if now is None else now
    q = _in_used.setdefault(uid, deque())
    while q and now - q[0] >= WINDOW:
        q.popleft()
    return 0 if len(q) < in_limit() else int(WINDOW - (now - q[0])) + 1


def in_count(uid: int, now: float | None = None) -> None:
    """Call right before a voice note is transcribed (the request costs quota even if it fails)."""
    _in_used.setdefault(uid, deque()).append(time.time() if now is None else now)


# ---- /vtext (turn somebody's voice message into text; each one costs a transcription request): its own hourly limit per person
_vt_used: dict[int, deque] = {}


def vtext_limit() -> int:
    try:
        return max(1, int(os.getenv("AI_VTEXT_PER_HOUR", "10")))
    except ValueError:
        return 10


def vtext_wait_secs(uid: int, now: float | None = None) -> int:
    """0 = allowed. The owner has no limit."""
    if _is_owner(uid):
        return 0
    now = time.time() if now is None else now
    q = _vt_used.setdefault(uid, deque())
    while q and now - q[0] >= WINDOW:
        q.popleft()
    return 0 if len(q) < vtext_limit() else int(WINDOW - (now - q[0])) + 1


def vtext_count(uid: int, now: float | None = None) -> None:
    _vt_used.setdefault(uid, deque()).append(time.time() if now is None else now)
