"""Text-to-speech for the owner's voice replies, using Microsoft Edge's free neural voices (`edge-tts`, no API key).
It never touches the Gemini chat keys. The result is MP3; with ffmpeg it becomes an OGG/Opus voice note, without ffmpeg it is
sent as an MP3 audio file. Any failure returns None and the caller just keeps the text reply.
Note: edge-tts uses an unofficial Microsoft endpoint, so it can change or be blocked without warning."""
from __future__ import annotations

import asyncio
import logging
import re
import shutil

log = logging.getLogger(__name__)
MAX_CHARS = 1500
VOICES = {   # short name -> (edge voice id, what it is)
    "neerja": ("en-IN-NeerjaNeural", "Indian English, woman (best for Hinglish text)"),
    "prabhat": ("en-IN-PrabhatNeural", "Indian English, man"),
    "swara": ("hi-IN-SwaraNeural", "Hindi, woman (best for Devanagari text)"),
    "madhur": ("hi-IN-MadhurNeural", "Hindi, man"),
    "aria": ("en-US-AriaNeural", "US English, woman"),
    "jenny": ("en-US-JennyNeural", "US English, friendly woman"),
    "guy": ("en-US-GuyNeural", "US English, man"),
    "sonia": ("en-GB-SoniaNeural", "British English, woman"),
    "ryan": ("en-GB-RyanNeural", "British English, man"),
}
DEFAULT = "neerja"
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]")
_MD = re.compile(r"[*_`#>~]|\[|\]|https?://\S+")


def voice_id(v: str) -> str:
    return VOICES.get((v or "").strip().lower(), VOICES[DEFAULT])[0]


def clean(text: str) -> str:
    t = _MD.sub(" ", _EMOJI.sub(" ", text or ""))
    return re.sub(r"\s+", " ", t).strip()


async def _opus(audio: bytes) -> bytes | None:
    if not shutil.which("ffmpeg"):
        return None
    try:
        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(audio), 20)
        return out if p.returncode == 0 and out else None
    except Exception:
        log.warning("ffmpeg opus conversion failed", exc_info=True)
        return None


async def _mp3(text: str, voice: str) -> bytes:
    import edge_tts
    buf = bytearray()
    async for ch in edge_tts.Communicate(text, voice).stream():
        if ch.get("type") == "audio":
            buf += ch["data"]
    return bytes(buf)


async def synth(text: str, voice: str = "") -> tuple[bytes, bool] | None:
    """Returns (audio, is_voice_note). is_voice_note False means MP3 (send as an audio file). None = could not speak."""
    text = clean(text)
    if not text or len(text) > MAX_CHARS:            # too long to speak in one go: caller keeps the text reply
        return None
    try:
        mp3 = await asyncio.wait_for(_mp3(text, voice_id(voice)), 30)
    except Exception:
        log.warning("edge-tts failed", exc_info=True)
        return None
    if not mp3:
        return None
    ogg = await _opus(mp3)
    return (ogg, True) if ogg else (mp3, False)
