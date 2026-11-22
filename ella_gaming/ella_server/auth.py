"""Telegram Mini App authentication.

1. The Mini App sends `Telegram.WebApp.initData` (a query string) to POST /api/auth/telegram.
2. We verify it exactly as Telegram documents: secret = HMAC_SHA256(key="WebAppData", msg=bot_token),
   hash = HMAC_SHA256(key=secret, msg=data_check_string). Compared in constant time. auth_date must be fresh.
3. We answer with our own short-lived HMAC-signed bearer token; every other endpoint only trusts that token,
   never anything the frontend merely claims about the user.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class AuthError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


MAX_INIT_DATA = 8192


def validate_init_data(init_data: str, bot_token: str, max_age: int = 86400, now: float | None = None) -> dict:
    """Return {'user': {...}, 'auth_date': int, ...} or raise AuthError."""
    if not isinstance(init_data, str) or not init_data or len(init_data) > MAX_INIT_DATA:
        raise AuthError("bad_init_data", "initData missing or too large")
    if not bot_token:
        raise AuthError("server_misconfigured", "bot token not configured")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise AuthError("bad_init_data", "initData is not a valid query string")
    fields: dict[str, str] = {}
    for k, v in pairs:
        if k in fields:                                   # duplicated keys are never produced by Telegram
            raise AuthError("bad_init_data", "duplicate field")
        fields[k] = v
    received = fields.pop("hash", None)
    if not received:
        raise AuthError("bad_init_data", "hash missing")
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        raise AuthError("bad_signature", "initData signature mismatch")
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        raise AuthError("bad_init_data", "auth_date invalid")
    t = time.time() if now is None else now
    if auth_date <= 0 or t - auth_date > max_age:
        raise AuthError("expired", "initData is too old - reopen the app from Telegram")
    if auth_date - t > 300:
        raise AuthError("bad_init_data", "auth_date is in the future")
    try:
        user = json.loads(fields.get("user", ""))
    except ValueError:
        raise AuthError("bad_init_data", "user missing")
    if not isinstance(user, dict) or not isinstance(user.get("id"), int) or user["id"] <= 0 or user.get("is_bot"):
        raise AuthError("bad_init_data", "user invalid")
    out = dict(fields)
    out["user"] = user
    out["auth_date"] = auth_date
    return out


# --------------------------------------------------------------------------------------------- session tokens
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(uid: int, secret: str, ttl: int, now: float | None = None) -> str:
    exp = int((time.time() if now is None else now) + ttl)
    body = _b64(json.dumps({"u": uid, "e": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str, secret: str, now: float | None = None) -> int:
    """Return the Telegram user id or raise AuthError."""
    try:
        body, sig = token.split(".", 1)
        good = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(good, sig):
            raise AuthError("bad_token")
        data = json.loads(_unb64(body))
        uid, exp = int(data["u"]), int(data["e"])
    except AuthError:
        raise
    except Exception:
        raise AuthError("bad_token")
    if (time.time() if now is None else now) > exp:
        raise AuthError("token_expired")
    return uid
