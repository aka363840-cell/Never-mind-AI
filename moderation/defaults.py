"""Per-group configuration defaults, lock table and protection presets. Pure Python (testable without Telegram)."""
from __future__ import annotations

import copy
from typing import Any

# Everything security-related is OFF by default: adding the bot to a group changes nothing until an admin opts in.
DEFAULTS: dict[str, Any] = {
    "rules": "",
    "welcome": {"on": False, "text": "", "clean": False, "mute": False},
    "goodbye": {"on": False, "text": "", "mute": False},
    "warn": {"limit": 3, "mode": "mute", "duration": 3600, "expire": 0},       # expire: seconds a warning stays active (0 = forever)
    "locks": {},
    "flood": {"on": False, "limit": 8, "window": 10, "action": "mute", "duration": 600},
    "spam": {"on": False, "dup_limit": 3, "dup_window": 60, "repeat_chars": 15, "mentions": 10,
             "suspicious_links": True, "cross_user": True, "action": "delete", "duration": 3600},
    "antibot": {"on": False, "action": "kick"},
    "antiforward": {"on": False, "action": "delete", "duration": 3600},
    "antilink": {"on": False, "action": "delete", "duration": 3600},
    "antiraid": {"on": False, "joins": 8, "window": 30, "lockdown": 600, "action": "kick", "until": 0},
    "captcha": {"on": False, "mode": "button", "timeout": 120, "fail": "kick"},
    "slowmode": 0,
    "cleanservice": {"on": False, "types": ["join", "leave"]},
    "whitelist_domains": [],
    "protected_ids": [],
    "report": {"on": True, "cooldown": 60},
    "protection": "off",
    "lockdown": {"on": False, "until": 0, "prev": None},
    "suspicious": {"on": True},
}

ACTIONS = ("delete", "warn", "mute", "kick", "ban")          # what a security feature does after deleting the message
WARN_MODES = ("ban", "kick", "mute", "tmute", "tban")
SERVICE_TYPES = ("join", "leave", "pin", "title", "photo", "other")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            # a default of {} is a free-form map (e.g. "locks": {name: bool}): keep whatever was stored
            out[k] = deep_merge(out[k], v) if out[k] else copy.deepcopy(v)
        elif k in out:
            out[k] = v
    return out


def with_defaults(stored: dict | None) -> dict:
    return deep_merge(DEFAULTS, stored or {})


# ---------------------------------------------------------------------------------------------- locks
SEND_FIELDS = ("can_send_messages", "can_send_audios", "can_send_documents", "can_send_photos", "can_send_videos",
               "can_send_video_notes", "can_send_voice_notes", "can_send_polls", "can_send_other_messages",
               "can_add_web_page_previews")
ALL_PERM_FIELDS = SEND_FIELDS + ("can_change_info", "can_invite_users", "can_pin_messages", "can_manage_topics")
MEDIA_FIELDS = ("can_send_photos", "can_send_videos", "can_send_audios", "can_send_documents",
                "can_send_video_notes", "can_send_voice_notes")

# lock name -> (Telegram permission fields it switches off natively, message kinds the bot deletes, description)
# "native" = enforced by Telegram itself for every member; "bot-side" = enforced by deleting the message.
LOCKS: dict[str, tuple[tuple[str, ...], frozenset[str], str]] = {
    "all":        (SEND_FIELDS, frozenset({"*"}), "everything: nobody but admins can send anything"),
    "text":       (("can_send_messages",), frozenset({"text"}), "text messages"),
    "media":      (MEDIA_FIELDS, frozenset({"photo", "video", "audio", "document", "voice", "video_note", "gif"}), "photos, videos, audio, files, voice, video notes, GIFs"),
    "photos":     (("can_send_photos",), frozenset({"photo"}), "photos"),
    "videos":     (("can_send_videos",), frozenset({"video"}), "videos"),
    "audios":     (("can_send_audios",), frozenset({"audio"}), "music / audio files"),
    "documents":  (("can_send_documents",), frozenset({"document"}), "files / documents"),
    "voice":      (("can_send_voice_notes",), frozenset({"voice"}), "voice messages"),
    "videonotes": (("can_send_video_notes",), frozenset({"video_note"}), "round video messages"),
    "polls":      (("can_send_polls",), frozenset({"poll"}), "polls"),
    "gifs":       ((), frozenset({"gif"}), "GIFs (bot-side: Telegram has no separate GIF permission)"),
    "stickers":   ((), frozenset({"sticker"}), "stickers (bot-side)"),
    "animations": ((), frozenset({"gif", "animated_sticker", "dice"}), "animated / video stickers, GIFs, dice (bot-side)"),
    "other":      (("can_send_other_messages",), frozenset({"sticker", "gif", "dice", "game"}), "stickers + GIFs + games + inline bots (native, all-or-nothing)"),
    "links":      ((), frozenset({"link"}), "links (bot-side)"),
    "previews":   (("can_add_web_page_previews",), frozenset(), "link previews (native)"),
    "forwarded":  ((), frozenset({"forward"}), "forwarded messages (bot-side)"),
    "contact":    ((), frozenset({"contact"}), "shared contacts (bot-side)"),
    "location":   ((), frozenset({"location"}), "shared locations / venues (bot-side)"),
    "invite":     (("can_invite_users",), frozenset(), "members inviting others (native)"),
    "pin":        (("can_pin_messages",), frozenset(), "members pinning messages (native)"),
    "info":       (("can_change_info",), frozenset(), "members changing group info (native)"),
}
LOCK_ALIASES = {"gif": "gifs", "sticker": "stickers", "photo": "photos", "video": "videos", "audio": "audios",
                "document": "documents", "doc": "documents", "docs": "documents", "file": "documents", "files": "documents",
                "voicemessages": "voice", "voicenotes": "voice", "videonote": "videonotes", "poll": "polls",
                "link": "links", "url": "links", "urls": "links", "forward": "forwarded", "forwards": "forwarded",
                "contacts": "contact", "locations": "location", "animation": "animations", "everything": "all",
                "messages": "text", "preview": "previews"}


def lock_name(raw: str) -> str | None:
    r = (raw or "").lower().strip()
    r = LOCK_ALIASES.get(r, r)
    return r if r in LOCKS else None


def managed_fields() -> set[str]:
    return {f for fields, _, _ in LOCKS.values() for f in fields}


def compute_permissions(current: dict[str, bool | None], locks: dict[str, bool], previous: dict[str, bool] | None = None) -> dict[str, bool]:
    """Merge the active locks into the chat's current default permissions.
    * a field covered by an active lock            -> False
    * a field covered by a lock that was ON before (`previous`) and is OFF now -> True (the lock gives it back)
    * every other field                            -> left exactly as the admins set it in Telegram"""
    out = {f: bool(current.get(f)) if current.get(f) is not None else True for f in ALL_PERM_FIELDS}

    def covered(lk: dict[str, bool]) -> set[str]:
        c: set[str] = set()
        for name, on in (lk or {}).items():
            if on and name in LOCKS:
                c.update(LOCKS[name][0])
        return c
    now, before = covered(locks), covered(previous or {})
    for f in before - now:
        out[f] = True
    for f in now:
        out[f] = False
    return out


def kinds_of(m) -> set[str]:
    """Message kinds present in a Telegram message (duck-typed: works with aiogram Message or a test stub)."""
    g = lambda a: getattr(m, a, None)  # noqa: E731
    k: set[str] = set()
    if g("text"):
        k.add("text")
    if g("photo"):
        k.add("photo")
    if g("video"):
        k.add("video")
    if g("audio"):
        k.add("audio")
    if g("document") and not g("animation"):
        k.add("document")
    if g("animation"):
        k.add("gif")
    st = g("sticker")
    if st:
        k.add("sticker")
        if getattr(st, "is_animated", False) or getattr(st, "is_video", False):
            k.add("animated_sticker")
    if g("voice"):
        k.add("voice")
    if g("video_note"):
        k.add("video_note")
    if g("poll"):
        k.add("poll")
    if g("contact"):
        k.add("contact")
    if g("location") or g("venue"):
        k.add("location")
    if g("dice"):
        k.add("dice")
    if g("game"):
        k.add("game")
    if g("forward_origin") or g("forward_date"):
        k.add("forward")
    return k


def locked_by(locks: dict[str, bool], kinds: set[str], has_link: bool) -> str | None:
    """Name of the first active lock that forbids this message (bot-side enforcement), else None."""
    kinds = set(kinds) | ({"link"} if has_link else set())
    for name, on in locks.items():
        if not on or name not in LOCKS:
            continue
        blocked = LOCKS[name][1]
        if "*" in blocked:
            if kinds:
                return name
        elif blocked & kinds:
            return name
    return None


# ---------------------------------------------------------------------------------------------- protection levels
PRESETS: dict[str, dict[str, Any]] = {
    "off": {"flood": {"on": False}, "spam": {"on": False}, "antibot": {"on": False}, "antiforward": {"on": False},
            "antilink": {"on": False}, "antiraid": {"on": False}, "captcha": {"on": False}, "slowmode": 0},
    "low": {"flood": {"on": True, "limit": 10, "window": 10, "action": "mute", "duration": 300},
            "spam": {"on": True, "action": "delete"}, "antibot": {"on": True}},
    "medium": {"flood": {"on": True, "limit": 8, "window": 10, "action": "mute", "duration": 600},
               "spam": {"on": True, "action": "warn"}, "antibot": {"on": True}, "antilink": {"on": True, "action": "delete"},
               "captcha": {"on": True, "mode": "button"}, "antiraid": {"on": True, "joins": 8, "window": 30}},
    "high": {"flood": {"on": True, "limit": 6, "window": 10, "action": "mute", "duration": 1800},
             "spam": {"on": True, "dup_limit": 2, "action": "warn"}, "antibot": {"on": True},
             "antilink": {"on": True, "action": "warn"}, "antiforward": {"on": True, "action": "delete"},
             "captcha": {"on": True, "mode": "math"}, "antiraid": {"on": True, "joins": 5, "window": 30},
             "slowmode": 3},
}
LEVELS = tuple(PRESETS)


def legacy_suppressed(st: dict, kind: str) -> bool:
    """True when the group muted the greeting (/welcomeoff, /goodbyeoff) or wrote its OWN welcome / goodbye text and enabled it. Otherwise the bot's original greeting stays."""
    sec = st["goodbye" if kind == "leave" else "welcome"]
    return bool(sec.get("mute") or (sec["on"] and sec["text"]))


def apply_preset(settings: dict, level: str) -> dict:
    out = deep_merge(settings, PRESETS[level])
    out["protection"] = level
    return out
