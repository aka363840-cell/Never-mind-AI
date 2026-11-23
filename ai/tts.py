"""Text-to-speech for the owner's voice replies (Gemini TTS, REST). Separate from the chat provider on purpose: the chat
provider refuses tts/audio model names so it can never drift onto paid usage; this module has its own model setting.

Gemini returns raw 24 kHz 16-bit mono PCM. With ffmpeg it is turned into an OGG/Opus voice note; without ffmpeg it is
wrapped as WAV and sent as an audio file. Any failure returns None and the caller just keeps the text reply."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
import shutil
import wave

import aiohttp

log = logging.getLogger(__name__)
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts").strip()
VOICE = os.getenv("GEMINI_TTS_VOICE", "Kore").strip()
MAX_CHARS = 1200
VOICES = {   # Gemini prebuilt voices (name -> feel). All work with Hindi / Hinglish text.
    "kore": "firm", "puck": "upbeat", "charon": "calm, informative", "zephyr": "bright", "aoede": "breezy",
    "leda": "youthful", "fenrir": "excitable", "orus": "firm, deep", "achird": "friendly", "sulafat": "warm",
    "vindemiatrix": "gentle", "algieba": "smooth", "gacrux": "mature", "zubenelgenubi": "casual",
}
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_MD = re.compile(r"[*_`#>~]|\[|\]|https?://\S+")


def voice_name(v: str) -> str:
    v = (v or "").strip().lower()
    return v.capitalize() if v in VOICES else VOICE


def clean_full(text: str) -> str:
    t = _MD.sub(" ", _EMOJI.sub(" ", text or ""))
    return re.sub(r"\s+", " ", t).strip()


def clean(text: str) -> str:
    return clean_full(text)[:MAX_CHARS]


def wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def _opus(wav_bytes: bytes) -> bytes | None:
    if not shutil.which("ffmpeg"):
        return None
    try:
        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(wav_bytes), 20)
        return out if p.returncode == 0 and out else None
    except Exception:
        log.warning("ffmpeg opus conversion failed", exc_info=True)
        return None


async def synth(text: str, keys: tuple[str, ...], voice: str = "") -> tuple[bytes, bool] | None:
    """Returns (audio, is_voice_note). is_voice_note False means WAV (send as audio file). None = could not speak."""
    if len(clean_full(text)) > MAX_CHARS:     # too long to speak in one go: caller keeps the text reply
        return None
    text = clean(text)
    if not text or not keys:
        return None
    body = {"contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name(voice)}}}}}
    pcm = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
        for k in keys:
            try:
                async with s.post(URL.format(model=MODEL), json=body, headers={"x-goog-api-key": k}) as r:
                    if r.status != 200:
                        log.warning("Gemini TTS HTTP %s: %s", r.status, (await r.text())[:200])
                        continue
                    data = await r.json()
                part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
                pcm = base64.b64decode(part["data"])
                break
            except Exception:
                log.warning("Gemini TTS failed", exc_info=True)
    if not pcm:
        return None
    w = wav(pcm)
    ogg = await _opus(w)
    return (ogg, True) if ogg else (w, False)
