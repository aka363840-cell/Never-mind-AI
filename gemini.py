"""Google Gemini (free tier) via the REST API. Keys come only from GEMINI_API_KEYS / GEMINI_API_KEY (up to 5).

Multi-key rotation: requests are spread round-robin over the keys. When a key hits its quota (429) it is parked
for a while and the request instantly retries on the next key, so the bot only pauses if ALL keys are exhausted.
Keys must belong to DIFFERENT Google projects (quota is per project, not per key).

Robustness rules:
* Models are tried in order; 404/401/403/400/5xx on one model just moves to the next one.
* 429 (quota) on every model => AIQuotaExceeded (the service then pauses AI and tells users to retry later).
* Every non-200 answer is logged with Google's own error text, so failures are never silent.
* If every configured model fails for a NON-quota reason (renamed/retired models), the provider asks Google
  which Flash / Flash-Lite models exist (models.list) and tries those. Pro/image/etc. are always refused.
* Nothing here can switch to a paid API: Pro-class names are rejected and billing is controlled by your Google project.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import aiohttp

from ai.base import AIError, AIQuotaExceeded, AIUnavailable, ChatTurn

log = logging.getLogger(__name__)
BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# Paid-only / non-chat families are refused outright so the bot can never drift onto paid usage.
_FORBIDDEN = re.compile(r"(pro|ultra|argon|omni|image|banana|veo|imagen|tts|live|embed|robotics|computer|audio|lyria)", re.I)
_BLOCKED_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")
MAX_KEYS = 5
BLOCKED_REPLY = "I can't help with that one, sorry! Let's talk about something else 🦋"


def _parts(t: ChatTurn) -> list[dict]:
    """Text part, plus the picture when the turn has one (Gemini can look at it)."""
    parts: list[dict] = [{"text": t.text}]
    if getattr(t, "image", None):
        import base64
        parts.insert(0, {"inline_data": {"mime_type": t.image_mime, "data": base64.b64encode(t.image).decode()}})
    return parts


def validate_models(models: tuple[str, ...]) -> tuple[str, ...]:
    ok = tuple(m for m in models if not _FORBIDDEN.search(m))
    if not ok:
        raise ValueError("GEMINI_MODELS must list at least one free-tier Flash / Flash-Lite model (Pro models are paid).")
    return ok


def _norm(name: str) -> str:
    return name.removeprefix("models/").strip()


def _version(name: str) -> tuple:
    """'gemini-3.6-flash' -> (3, 6); used to prefer newer discovered models."""
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def _retry_delay(data) -> float | None:
    if not isinstance(data, dict):
        return None
    for d in (data.get("error") or {}).get("details") or []:
        if isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo"):
            m = _DELAY.match(str(d.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return None


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str | list[str] | tuple[str, ...], models: tuple[str, ...], max_tokens: int = 700,
                 timeout: float = 8.0, discover: bool = False, budget: float = 14.0):
        keys = [api_key] if isinstance(api_key, str) else list(api_key)
        keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))[:MAX_KEYS]
        if not keys:
            raise ValueError("GEMINI_API_KEY is not set")
        self._keys = keys
        self._rest = [0.0] * len(keys)          # monotonic time until which a key is parked (quota hit)
        self._rr = 0
        self._models = [_norm(m) for m in validate_models(models)]
        self._max_tokens = max_tokens
        self._req_timeout = timeout             # one HTTP request never waits longer than this
        self._budget = budget                   # the WHOLE reply (all keys/models/retries) never takes longer than this
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._discover = discover
        self._found: tuple[float | None, list[str]] = (None, [])
        self.last_error = ""
        self.key_count = len(keys)
        self.budget = budget

    # ------------------------------------------------------------------ lifecycle
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    # ------------------------------------------------------------------ public API
    async def generate(self, system: str, turns: list[ChatTurn], *, temperature: float = 0.9, json_mode: bool = False) -> str:
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": t.role, "parts": _parts(t)} for t in turns],
            "generationConfig": {"maxOutputTokens": self._max_tokens, "temperature": temperature,
                                 **({"responseMimeType": "application/json"} if json_mode else {})},
        }
        t_end = time.monotonic() + self._budget
        order = self._order()
        if not order:                                   # every key is parked => don't even call Google
            raise AIQuotaExceeded(max(30, int(min(self._rest) - time.monotonic())))
        quota: AIQuotaExceeded | None = None
        last: Exception | None = None
        for idx in order:
            if time.monotonic() >= t_end:
                last = last or AIUnavailable("time budget used up")
                break
            try:
                text = await self._generate_one(body, self._keys[idx], t_end)
            except AIQuotaExceeded as e:
                self._rest[idx] = time.monotonic() + (1800 if e.retry_after >= 900 else max(e.retry_after, 60))
                log.warning("Gemini key #%d/%d quota exhausted -> switching to the next key", idx + 1, len(self._keys))
                quota = quota or e
                continue
            except AIError as e:
                last = e
                if getattr(e, "auth", False):            # invalid / blocked key: skip it for 10 min instead of retrying every message
                    self._rest[idx] = time.monotonic() + 600
                    log.error("Gemini key #%d/%d rejected (invalid/blocked) -> parked 10 min", idx + 1, len(self._keys))
                continue
            return text
        if quota is not None:
            raise quota
        raise last or AIUnavailable("no key available")

    def _order(self) -> list[int]:
        """Usable keys, round-robin starting after the one used last (spreads load + quota evenly)."""
        n, now = len(self._keys), time.monotonic()
        self._rr = (self._rr + 1) % n
        return [i for i in ((self._rr + k) % n for k in range(n)) if self._rest[i] <= now]

    async def _generate_one(self, body: dict, key: str, t_end: float | None = None) -> str:
        quota: AIQuotaExceeded | None = None
        last: Exception | None = None
        tried: list[str] = []

        async def run(models: list[str]) -> str | None:
            nonlocal quota, last
            for model in models:
                tried.append(model)
                text, err = await self._attempt(model, body, key, t_end)
                if text is not None:
                    if self._models[:1] != [model]:           # remember the first working model
                        if model in self._models:
                            self._models.remove(model)
                        self._models.insert(0, model)
                    return text
                last = err
                if isinstance(err, AIQuotaExceeded):
                    quota = quota or err
                if getattr(err, "auth", False):          # bad/blocked key: every model will fail the same way
                    return None
            return None

        text = await run(list(self._models))
        if (text is None and quota is None and self._discover and not getattr(last, "auth", False)
                and (t_end is None or t_end - time.monotonic() > 4)):
            extra = [m for m in await self._discovered(key) if m not in tried]
            if extra:
                log.warning("Configured Gemini models failed; trying models found via models.list: %s", extra)
                text = await run(extra)
        if text is not None:
            return text
        if quota is not None:
            raise quota
        raise last or AIUnavailable("no model available")

    async def self_test(self) -> tuple[bool, str]:
        """Tests EVERY key with one tiny request. Returns (at least one key works, per-key report). Never raises."""
        body = {"system_instruction": {"parts": [{"text": "Reply with the single word OK."}]},
                "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                "generationConfig": {"maxOutputTokens": self._max_tokens, "temperature": 0.0}}
        lines, ok_n = [], 0
        for i, key in enumerate(self._keys, 1):
            tag = f"Key {i} (…{key[-4:]})"
            try:
                await self._generate_one(body, key)
                ok_n += 1
                lines.append(f"{tag}: ✅ works (model: {self._models[0]})")
            except AIQuotaExceeded as e:
                lines.append(f"{tag}: ⏳ quota used up (retry in ~{e.retry_after}s)")
            except AIError as e:
                lines.append(f"{tag}: ❌ {e}. {self.last_error}".strip())
            except Exception as e:  # noqa: BLE001
                lines.append(f"{tag}: ❌ {type(e).__name__}: {e}")
        head = f"{ok_n}/{len(self._keys)} keys working. Keys rotate automatically when one runs out."
        return ok_n > 0, head + "\n" + "\n".join(lines)

    # ------------------------------------------------------------------ internals
    async def _attempt(self, model: str, body: dict, key: str, t_end: float | None = None) -> tuple[str | None, Exception | None]:
        err: Exception = AIUnavailable("time budget used up")
        for attempt in range(2):
            if t_end is not None and t_end - time.monotonic() < 1.0:
                return None, err
            try:
                status, data, headers = await self._post(model, body, key, t_end)
            except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as e:
                err = AIUnavailable(f"network error: {type(e).__name__}")
                if isinstance(e, (TimeoutError, asyncio.TimeoutError)) and len(self._models) > 1 and model in self._models:
                    self._models.remove(model)                             # slow model -> try the others first next time
                    self._models.append(model)
                    return None, err                                       # timed out: never wait twice on the same model
                err.transient = True                                       # type: ignore[attr-defined]
                self.last_error = f"{model}: {err}"
                log.warning("Gemini model=%s %s", model, err)
            else:
                if status == 200:
                    try:
                        return self._extract(data), None
                    except AIUnavailable as e:
                        err = e
                        self.last_error = f"{model}: {e}"
                        log.warning("Gemini model=%s %s", model, e)
                        if getattr(e, "truncated", False) and attempt == 0:      # thinking ate the token budget
                            body = {**body, "generationConfig": {**body["generationConfig"],
                                                                 "maxOutputTokens": min(self._max_tokens * 4, 4096)}}
                            continue
                else:
                    err = self._error(model, status, data, headers)
            if getattr(err, "transient", False) and attempt == 0 and (t_end is None or t_end - time.monotonic() > 3):
                await asyncio.sleep(0.5)
                continue
            return None, err
        return None, err

    def _error(self, model: str, status: int, data, headers) -> Exception:
        e = (data.get("error") if isinstance(data, dict) else None) or {}
        msg = str(e.get("message", ""))[:200]
        self.last_error = f"{model}: HTTP {status} {e.get('status', '')} {msg}".strip()
        log.warning("Gemini model=%s -> HTTP %s %s: %s", model, status, e.get("status", ""), msg)
        if status == 429:
            retry = _retry_delay(data)
            hdr = str(headers.get("Retry-After", "")) if headers else ""
            if retry is None and hdr.replace(".", "").isdigit():
                retry = float(hdr)
            if "perday" in json.dumps(data).lower().replace(" ", "").replace("_", ""):
                retry = max(retry or 0, 900)
            return AIQuotaExceeded(int(retry) if retry else 60)
        err = AIUnavailable(f"HTTP {status}")
        if status in (401, 403) or (status == 400 and "api key" in msg.lower()):
            err.auth = True                                                # type: ignore[attr-defined]
        if status >= 500:
            err.transient = True                                           # type: ignore[attr-defined]
        return err

    async def _post(self, model: str, body: dict, key: str, t_end: float | None = None):
        wait = self._req_timeout if t_end is None else max(1.0, min(self._req_timeout, t_end - time.monotonic()))
        async with self._sess().post(f"{BASE}/{model}:generateContent", json=body, timeout=aiohttp.ClientTimeout(total=wait),
                                     headers={"x-goog-api-key": key}) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:  # noqa: BLE001
                data = {}
            return r.status, data, r.headers

    async def _list_models(self, key: str) -> list[str]:
        async with self._sess().get(BASE, params={"pageSize": "200"}, headers={"x-goog-api-key": key},
                                    timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status != 200:
                log.warning("Gemini models.list -> HTTP %s", r.status)
                return []
            data = await r.json(content_type=None)
        names = []
        for m in (data or {}).get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                names.append(_norm(m.get("name", "")))
        return names

    async def _discovered(self, key: str) -> list[str]:
        ts, cached = self._found
        if ts is not None and time.monotonic() - ts < 600:
            return cached
        try:
            names = await self._list_models(key)
        except Exception as e:  # noqa: BLE001
            log.warning("Gemini models.list failed: %s", e)
            names = []
        flash = [n for n in names if "flash" in n and not _FORBIDDEN.search(n) and "preview" not in n and "exp" not in n]
        # newest first; plain Flash before Flash-Lite within a version
        flash.sort(key=lambda n: (_version(n), "lite" not in n), reverse=True)
        self._found = (time.monotonic(), flash[:6])
        return self._found[1]

    @staticmethod
    def _extract(data: dict) -> str:
        data = data or {}
        if (data.get("promptFeedback") or {}).get("blockReason"):
            return BLOCKED_REPLY
        try:
            cand = data["candidates"][0]
        except (KeyError, IndexError, TypeError):
            raise AIUnavailable("response had no candidates")
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()
        if text:
            return text
        finish = str(cand.get("finishReason") or "")
        if finish in _BLOCKED_FINISH:
            return BLOCKED_REPLY
        err = AIUnavailable(f"empty response (finishReason={finish or 'unknown'})")
        err.truncated = finish == "MAX_TOKENS"                              # type: ignore[attr-defined]
        raise err
