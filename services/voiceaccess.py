"""Who may use voice replies, and each person's voice settings. The owner is always allowed; the owner can approve others
(/approvevoice) and remove them (/removevoice). Saved in a small JSON file in the data volume so it survives restarts."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)
_path = Path(os.getenv("DATA_DIR", "/data")) / "voice_access.json"
_data: dict | None = None       # {"approved": [ids], "prefs": {"<uid>": {"on": bool, "name": str}}}


def _load() -> dict:
    global _data
    if _data is None:
        try:
            _data = json.loads(_path.read_text("utf-8"))
        except Exception:
            _data = {}
        _data.setdefault("approved", [])
        _data.setdefault("prefs", {})
    return _data


def _save() -> None:
    try:
        _path.parent.mkdir(parents=True, exist_ok=True)
        tmp = _path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_load()), "utf-8")
        tmp.replace(_path)
    except OSError:
        log.warning("could not save voice access file %s", _path, exc_info=True)


def allowed(uid: int, owner_id: int) -> bool:
    return bool(uid) and (uid == owner_id or uid in _load()["approved"])


def approved_ids() -> list[int]:
    return list(_load()["approved"])


def approve(uid: int) -> bool:
    d = _load()
    if uid in d["approved"]:
        return False
    d["approved"].append(uid)
    _save()
    return True


def remove(uid: int) -> bool:
    d = _load()
    if uid not in d["approved"]:
        return False
    d["approved"].remove(uid)
    d["prefs"].pop(str(uid), None)
    _save()
    return True


def prefs(uid: int) -> tuple[bool, str]:
    p = _load()["prefs"].get(str(uid), {})
    return bool(p.get("on")), p.get("name", "")


def set_prefs(uid: int, *, on: bool | None = None, name: str | None = None) -> None:
    p = _load()["prefs"].setdefault(str(uid), {})
    if on is not None:
        p["on"] = on
    if name is not None:
        p["name"] = name
    _save()
