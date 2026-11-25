"""Voice note -> text. Order: Groq whisper-large-v3-turbo, Groq whisper-large-v3, then Gemini Flash (audio as inline_data).
Several keys per provider: 429 -> next key, every key of a model out -> next model, Groq out -> Gemini. Returns None only when
EVERYTHING failed (the caller then says "couldn't hear that, please type it"); returns "" when the audio simply has no speech.
The audio is only held in memory for the call, never stored. Keys are never logged in full. Imports nothing from the game."""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time

import aiohttp

log = logging.getLogger(__name__)
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_MODELS = ("whisper-large-v3-turbo", "whisper-large-v3")
MAX_KEYS = 5
GEMINI_PROMPT = ("Transcribe this voice message faithfully, word for word, in the language and script it is spoken in "
                 "(Hinglish stays in Roman letters). Reply with ONLY the transcript, nothing else. If there is no speech, reply with nothing.")


def max_seconds() -> int:
    try:
        return max(1, int(os.getenv("AI_VOICE_MAX_SECONDS", "60")))
    except ValueError:
        return 60


def _clean(keys) -> list[str]:
    return list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))[:MAX_KEYS]


def env_keys(*names: str) -> list[str]:
    raw = ",".join(os.getenv(n, "") or "" for n in names)
    return _clean(raw.replace("\n", ",").replace(" ", ",").split(","))


_EXT = {"audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
        "audio/aac": "m4a", "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm", "audio/flac": "flac"}


def _mask(k: str) -> str:
    return f"…{k[-4:]}"


class Transcriber:
    def __init__(self, groq_keys, gemini_keys, gemini_models, *, timeout: float = 20.0, groq_url: str = GROQ_URL,
                 gemini_base: str = GEMINI_BASE):
        self.groq_keys, self.gemini_keys = _clean(groq_keys), _clean(gemini_keys)
        self.gemini_models = [m for m in dict.fromkeys(gemini_models) if m]
        self.timeout, self.groq_url, self.gemini_base = timeout, groq_url, gemini_base.rstrip("/")
        self._rest: dict[tuple, float] = {}              # ("g"|"m", key index, model) / ("key", provider, idx) -> parked until
        self._rr = 0
        self.last_error = ""
        self.last_source = ""

    # ------------------------------------------------------------------ helpers
    def _parked(self, *k) -> bool:
        return self._rest.get(k, 0.0) > time.monotonic()

    def _park(self, secs: float, *k) -> None:
        self._rest[k] = time.monotonic() + secs

    async def _post(self, s: aiohttp.ClientSession, url: str, headers: dict, **kw) -> tuple[int, dict, dict]:
        try:
            async with s.post(url, headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout), **kw) as r:
                try:
                    data = await r.json(content_type=None)
                except Exception:  # noqa: BLE001
                    data = {}
                return r.status, (data if isinstance(data, dict) else {}), dict(r.headers)
        except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError):
            return 0, {}, {}

    @staticmethod
    def _retry(hdr: dict) -> float:
        v = str(hdr.get("Retry-After", ""))
        return float(v) if v.replace(".", "").isdigit() else 60.0

    # ------------------------------------------------------------------ Groq Whisper
    async def _groq(self, s, audio: bytes, mime: str) -> str | None:
        n = len(self.groq_keys)
        if not n:
            return None
        self._rr = (self._rr + 1) % n
        for model in GROQ_MODELS:
            for j in range(n):
                i = (self._rr + j) % n
                if self._parked("groq-key", i) or self._parked("groq", i, model):
                    continue
                form = aiohttp.FormData()
                form.add_field("file", audio, filename="voice." + _EXT.get(mime.split(";")[0].strip().lower(), "ogg"), content_type=mime)
                form.add_field("model", model)
                form.add_field("response_format", "json")
                form.add_field("temperature", "0")
                status, data, hdr = await self._post(s, self.groq_url, {"Authorization": f"Bearer {self.groq_keys[i]}"}, data=form)
                if status == 200 and isinstance(data.get("text"), str):
                    self.last_source = f"groq/{model}"
                    return data["text"].strip()
                self.last_error = f"groq {model} key {_mask(self.groq_keys[i])}: HTTP {status}"
                log.warning("STT %s", self.last_error)
                if status == 429:
                    self._park(max(5, min(self._retry(hdr), 900)), "groq", i, model)       # this key+model is out: next key
                elif status in (401, 403):
                    self._park(600, "groq-key", i)
                elif status in (400, 404, 422):
                    break                                                                   # model/format problem: next model
                # 5xx / timeout / bad body: just try the next key
        return None

    # ------------------------------------------------------------------ Gemini (audio inline)
    async def _gemini(self, s, audio: bytes, mime: str) -> str | None:
        n = len(self.gemini_keys)
        if not n or not self.gemini_models:
            return None
        b64 = base64.b64encode(audio).decode()
        body = {"contents": [{"parts": [{"text": GEMINI_PROMPT}, {"inline_data": {"mime_type": mime, "data": b64}}]}],
                "generationConfig": {"temperature": 0.0}}
        self._rr = (self._rr + 1) % n
        for model in self.gemini_models:
            for j in range(n):
                i = (self._rr + j) % n
                if self._parked("gem-key", i) or self._parked("gem", i, model):
                    continue
                status, data, hdr = await self._post(s, f"{self.gemini_base}/{model}:generateContent",
                                                     {"x-goog-api-key": self.gemini_keys[i]}, json=body)
                if status == 200:
                    try:
                        parts = data["candidates"][0]["content"]["parts"]
                        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
                    except (KeyError, IndexError, TypeError):
                        text = ""                                  # blocked / no speech
                    self.last_source = f"gemini/{model}"
                    return text
                self.last_error = f"gemini {model} key {_mask(self.gemini_keys[i])}: HTTP {status}"
                log.warning("STT %s", self.last_error)
                if status == 429:
                    self._park(max(5, min(self._retry(hdr), 900)), "gem", i, model)
                elif status in (401, 403):
                    self._park(600, "gem-key", i)
                elif status in (400, 404):
                    break
        return None

    # ------------------------------------------------------------------ public
    async def transcribe(self, audio: bytes, mime: str = "audio/ogg") -> str | None:
        """Text of the voice note, "" if nobody spoke, None if every provider failed. Never raises."""
        try:
            async with aiohttp.ClientSession() as s:
                for step in (self._groq, self._gemini):
                    out = await step(s, audio, mime)
                    if out is not None:
                        return out
        except Exception:  # noqa: BLE001
            log.warning("STT failed unexpectedly", exc_info=True)
        return None


_cached: Transcriber | None = None


def get(settings) -> Transcriber:
    global _cached
    if _cached is None:
        _cached = Transcriber(env_keys("GROQ_API_KEYS", "GROQ_API_KEY"), settings.gemini_api_keys, settings.gemini_models)
    return _cached
