"""Any OpenAI-compatible chat API (Groq, Cerebras, OpenRouter, Mistral, SambaNova, NVIDIA, GitHub Models ...).

Same idea as the Gemini provider: several keys, several models, instant switch on failure.
* 429 on a (key, model) pair  -> that pair is parked for the time the server asks, the next combination is tried at once.
* 401/403                     -> the key is parked 10 min.
* 404 / 400 'model' errors    -> the model is parked 10 min (renamed or retired models never block the others).
* 5xx / timeout / empty reply -> next combination, nothing is parked for long.
The whole call never takes longer than `budget` seconds, one HTTP request never longer than `timeout`.
Imports nothing from the game (like the rest of the ai package).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import aiohttp

from ai.base import AIError, AIQuotaExceeded, AIUnavailable, ChatTurn

log = logging.getLogger(__name__)
MAX_KEYS = 10
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, keys: list[str] | tuple[str, ...], models: list[str] | tuple[str, ...],
                 max_tokens: int = 450, timeout: float = 6.0, budget: float = 10.0, extra_headers: dict | None = None):
        keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))[:MAX_KEYS]
        models = list(dict.fromkeys(m.strip() for m in models if m and m.strip()))
        if not keys:
            raise ValueError(f"{name}: no API key")
        if not models:
            raise ValueError(f"{name}: no model")
        self.name = name
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._keys, self._models = keys, models
        self._max_tokens, self._req_timeout, self.budget = max_tokens, timeout, budget
        self._headers = dict(extra_headers or {})
        self._pair_rest: dict[tuple[int, str], float] = {}     # (key index, model) -> parked until (monotonic)
        self._key_rest = [0.0] * len(keys)
        self._model_rest: dict[str, float] = {}
        self._rr = 0
        self._no_json = False                                  # provider rejected response_format -> stop sending it
        self._session: aiohttp.ClientSession | None = None
        self.key_count = len(keys)
        self.last_error = ""

    # ------------------------------------------------------------------ lifecycle
    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    # ------------------------------------------------------------------ public API
    def retry_in(self) -> float:
        """Seconds until at least one (key, model) combination is usable again (0 = usable now)."""
        now = time.monotonic()
        best = None
        for m in self._models:
            for i in range(len(self._keys)):
                free_at = max(self._pair_rest.get((i, m), 0.0), self._key_rest[i], self._model_rest.get(m, 0.0))
                best = free_at if best is None else min(best, free_at)
        return max(0.0, (best or 0.0) - now)

    async def generate(self, system: str, turns: list[ChatTurn], *, temperature: float = 0.9, json_mode: bool = False) -> str:
        msgs = [{"role": "system", "content": system}] + [
            {"role": "assistant" if t.role == "model" else "user", "content": t.text} for t in turns]
        t_end = time.monotonic() + self.budget
        quota: AIQuotaExceeded | None = None
        last: Exception | None = None
        tried = 0
        n = len(self._keys)
        self._rr = (self._rr + 1) % n
        for model in list(self._models):
            for k in range(n):
                idx = (self._rr + k) % n
                now = time.monotonic()
                if now >= t_end:
                    raise last or quota or AIUnavailable(f"{self.name}: time budget used up")
                if max(self._pair_rest.get((idx, model), 0.0), self._key_rest[idx], self._model_rest.get(model, 0.0)) > now:
                    continue
                tried += 1
                try:
                    text = await self._call(model, idx, msgs, temperature, json_mode, t_end)
                except AIQuotaExceeded as e:
                    self._pair_rest[(idx, model)] = time.monotonic() + max(5, min(e.retry_after, 1800))
                    quota = quota or e
                    continue
                except AIError as e:
                    last = e
                    if getattr(e, "auth", False):
                        self._key_rest[idx] = time.monotonic() + 600
                        log.error("%s key #%d/%d rejected (invalid/blocked) -> parked 10 min", self.name, idx + 1, n)
                    elif getattr(e, "bad_model", False):
                        self._model_rest[model] = time.monotonic() + 600
                        log.warning("%s model %s not usable (%s) -> parked 10 min", self.name, model, e)
                        break                                  # no point trying this model with the other keys
                    continue
                if self._models[0] != model and model in self._models:   # remember the first working model
                    self._models.remove(model)
                    self._models.insert(0, model)
                return text
        if quota is not None:
            raise quota
        if tried == 0:
            raise AIQuotaExceeded(max(10, int(self.retry_in())))
        raise last or AIUnavailable(f"{self.name}: no key/model available")

    async def self_test(self) -> tuple[bool, str]:
        msgs = [{"role": "system", "content": "Reply with the single word OK."}, {"role": "user", "content": "ping"}]
        lines, ok_n = [], 0
        for i in range(len(self._keys)):
            tag = f"{self.name} key {i + 1} (…{self._keys[i][-4:]})"
            result = None
            for model in self._models:
                try:
                    await self._call(model, i, msgs, 0.0, False, time.monotonic() + 12)
                    result = f"✅ works (model: {model})"
                    break
                except AIQuotaExceeded as e:
                    result = f"⏳ quota used up (retry in ~{e.retry_after}s)"
                    break
                except AIError as e:
                    result = f"❌ {model}: {e}"
                    if getattr(e, "auth", False):
                        break
                except Exception as e:  # noqa: BLE001
                    result = f"❌ {model}: {type(e).__name__}"
            ok_n += result.startswith("✅")
            lines.append(f"{tag}: {result}")
        return ok_n > 0, f"{self.name}: {ok_n}/{len(self._keys)} keys working\n" + "\n".join(lines)

    # ------------------------------------------------------------------ internals
    async def _call(self, model: str, idx: int, msgs: list[dict], temperature: float, json_mode: bool, t_end: float) -> str:
        body = {"model": model, "messages": msgs, "max_tokens": self._max_tokens, "temperature": min(max(temperature, 0.0), 1.0)}
        if self.name == "groq" and "gpt-oss" in model:
            body["reasoning_effort"] = "low"                   # reasoning tokens would otherwise eat the small max_tokens budget
        if json_mode and not self._no_json:
            body["response_format"] = {"type": "json_object"}
        for attempt in range(2):
            wait = max(1.0, min(self._req_timeout, t_end - time.monotonic()))
            headers = {"Authorization": f"Bearer {self._keys[idx]}", "Content-Type": "application/json", **self._headers}
            try:
                async with self._sess().post(self._url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=wait)) as r:
                    status, hdr = r.status, r.headers
                    try:
                        data = await r.json(content_type=None)
                    except Exception:  # noqa: BLE001
                        data = {}
            except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as e:
                raise AIUnavailable(f"{self.name} network error: {type(e).__name__}")
            if status == 200:
                return self._extract(data)
            err = (data.get("error") if isinstance(data, dict) else None) or {}
            msg = str(err.get("message", "") if isinstance(err, dict) else err)[:200]
            self.last_error = f"{self.name} {model}: HTTP {status} {msg}".strip()
            log.warning("%s model=%s -> HTTP %s %s", self.name, model, status, msg)
            low = msg.lower()
            if status == 400 and "response_format" in body and ("response_format" in low or "json" in low):
                self._no_json = True                                  # this provider has no JSON mode: retry once without it
                body.pop("response_format", None)
                continue
            if status == 429 or (status == 402):
                retry = hdr.get("Retry-After", "") if hdr else ""
                secs = int(float(retry)) if retry.replace(".", "").isdigit() else 60
                raise AIQuotaExceeded(max(secs, 900) if status == 402 else secs)
            e = AIUnavailable(f"HTTP {status}")
            if status in (401, 403):
                e.auth = True                                         # type: ignore[attr-defined]
            elif status in (400, 404, 422) and ("model" in low or status == 404):
                e.bad_model = True                                    # type: ignore[attr-defined]
            raise e
        raise AIUnavailable(f"{self.name}: bad request")

    def _extract(self, data: dict) -> str:
        try:
            content = data["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise AIUnavailable(f"{self.name}: response had no choices")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = _THINK.sub("", content or "").strip()
        if not text:
            raise AIUnavailable(f"{self.name}: empty response")
        return text
