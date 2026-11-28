"""Mocked (no network, no real keys): key/model rotation on 429, image routing (Gemini first, then Groq WITHOUT the image),
Whisper -> Gemini fallback for voice notes, and the 60-second voice limit."""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

try:                                    # the real aiohttp is used when installed (CI); this tiny stand-in only lets the pure logic import without it
    import aiohttp  # noqa: F401
except ImportError:                     # pragma: no cover
    m = types.ModuleType("aiohttp")

    class _Err(Exception):
        pass

    class _Form:
        def __init__(self):
            self.fields = []

        def add_field(self, name, value, **kw):
            self.fields.append((name, value, kw))

    class _Sess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def close(self):
            pass

    m.ClientError, m.FormData, m.ClientSession, m.ClientTimeout = _Err, _Form, _Sess, lambda **k: None
    sys.modules["aiohttp"] = m

from ai import stt  # noqa: E402
from ai.base import AIQuotaExceeded, AIUnavailable, ChatTurn  # noqa: E402
from ai.multi import MultiProvider  # noqa: E402
from ai.openai_compat import OpenAICompatProvider  # noqa: E402


def run(c):
    return asyncio.run(c)


# ------------------------------------------------------------------------------------------------ voice note -> text
class Scripted(stt.Transcriber):
    """Transcriber whose HTTP layer is a script: replies[(provider, key-suffix, model)] = (status, json)."""

    def __init__(self, replies, **kw):
        super().__init__(**kw)
        self.replies, self.calls = replies, []

    async def _post(self, s, url, headers, **kw):
        if "groq.test" in url:
            key, model = headers["Authorization"].removeprefix("Bearer "), dict((f[0], f[1]) for f in kw["data"].fields)["model"]
            self.calls.append(("groq", key, model))
            st, data = self.replies.get(("groq", key, model), self.replies.get(("groq", key), (500, {})))
        else:
            key, model = headers["x-goog-api-key"], url.rsplit("/", 1)[1].split(":")[0]
            self.calls.append(("gemini", key, model, kw["json"]))
            st, data = self.replies.get(("gemini", key), (500, {}))
        return st, data, ({"Retry-After": "30"} if st == 429 else {})


def _tr(replies, groq=("g1", "g2"), gem=("m1", "m2")):
    return Scripted(replies, groq_keys=groq, gemini_keys=gem, gemini_models=("gemini-3.8-flash",), groq_url="http://groq.test/audio",
                    gemini_base="http://gem.test/models")


def test_whisper_rotates_keys_on_429():
    t = _tr({("groq", "g1"): (429, {}), ("groq", "g2"): (200, {"text": " namaste Ella "})})
    assert run(t.transcribe(b"ogg")) == "namaste Ella"
    assert t.last_source == "groq/whisper-large-v3-turbo"
    assert {c[1] for c in t.calls} == {"g1", "g2"} or len(t.calls) == 1          # (round-robin start may hit the good key first)
    assert all(c[2] == "whisper-large-v3-turbo" for c in t.calls)               # the first model is enough while any key works


def test_whisper_turbo_out_everywhere_goes_to_v3():
    t = _tr({("groq", "g1", "whisper-large-v3-turbo"): (429, {}), ("groq", "g2", "whisper-large-v3-turbo"): (429, {}),
             ("groq", "g1", "whisper-large-v3"): (200, {"text": "hello from v3"}), ("groq", "g2", "whisper-large-v3"): (200, {"text": "hello from v3"})})
    assert run(t.transcribe(b"ogg")) == "hello from v3"
    assert t.last_source == "groq/whisper-large-v3"
    assert [c[2] for c in t.calls][:2] == ["whisper-large-v3-turbo"] * 2


def test_whisper_all_down_falls_back_to_gemini_with_inline_audio():
    t = _tr({("groq", "g1"): (429, {}), ("groq", "g2"): (500, {}),
             ("gemini", "m1"): (429, {}), ("gemini", "m2"): (200, {"candidates": [{"content": {"parts": [{"text": "kal milte hain"}]}}]})})
    assert run(t.transcribe(b"AUDIO", "audio/ogg")) == "kal milte hain"
    assert t.last_source.startswith("gemini/")
    body = next(c[3] for c in t.calls if c[0] == "gemini")
    parts = body["contents"][0]["parts"]
    assert any(p.get("inline_data", {}).get("mime_type") == "audio/ogg" for p in parts)
    assert "transcript" in parts[0]["text"].lower()


def test_everything_down_returns_none_and_silence_returns_empty():
    t = _tr({("groq", "g1"): (500, {}), ("groq", "g2"): (500, {}), ("gemini", "m1"): (500, {}), ("gemini", "m2"): (429, {})})
    assert run(t.transcribe(b"x")) is None
    quiet = _tr({("groq", "g1"): (200, {"text": ""}), ("groq", "g2"): (200, {"text": ""})})
    assert run(quiet.transcribe(b"x")) == ""
    assert all(c[0] == "groq" for c in quiet.calls) and len(quiet.calls) == 1     # no speech: do not burn more quota


def test_voice_limit_is_60_seconds_and_configurable(monkeypatch):
    monkeypatch.delenv("AI_VOICE_MAX_SECONDS", raising=False)
    assert stt.max_seconds() == 60
    monkeypatch.setenv("AI_VOICE_MAX_SECONDS", "30")
    assert stt.max_seconds() == 30
    monkeypatch.setenv("AI_VOICE_MAX_SECONDS", "abc")
    assert stt.max_seconds() == 60


def test_handler_refuses_longer_voice_notes_and_never_stores_audio():
    src = open("handlers/ai_chat.py").read()
    seg = src[src.index("async def voice_in"):src.index("async def sticker_chat")]
    assert "media.duration or 0) > limit" in seg and "stt.max_seconds()" in seg
    assert "write(" not in seg and "open(" not in seg and "save" not in seg.lower().replace("saves", "")      # audio stays in memory


def test_keys_are_never_logged_in_full():
    t = _tr({("groq", "gsk_SECRETSECRET1234"): (429, {})}, groq=("gsk_SECRETSECRET1234",))
    import logging
    recs = []
    h = logging.Handler()
    h.emit = lambda r: recs.append(r.getMessage())
    logging.getLogger("ai.stt").addHandler(h)
    run(t.transcribe(b"x"))
    assert recs and not any("SECRETSECRET" in r for r in recs) and any("…1234" in r for r in recs)


# ------------------------------------------------------------------------------------------------ chat: rotation + image routing
class Compat(OpenAICompatProvider):
    """OpenAI-compatible provider with the HTTP call replaced by a script; records the exact messages it was given."""

    def __init__(self, script, **kw):
        super().__init__("groq", "http://x/v1", ["k1", "k2"], kw.pop("models", ["m1", "m2"]), timeout=1, budget=5)
        self.script, self.calls, self.msgs = script, [], []

    async def _call(self, model, idx, msgs, temperature, json_mode, t_end):
        self.calls.append((model, idx))
        self.msgs.append(msgs)
        r = self.script(model, idx)
        if isinstance(r, Exception):
            raise r
        return r


def test_groq_rotates_keys_then_models_on_429():
    p = Compat(lambda model, idx: AIQuotaExceeded(30) if model == "m1" else "ok from m2")
    assert run(p.generate("sys", [ChatTurn("user", "hi")])) == "ok from m2"
    assert [c[0] for c in p.calls] == ["m1", "m1", "m2"]                          # both keys of m1 tried, then the next model
    assert {c[1] for c in p.calls[:2]} == {0, 1}


def test_gemini_first_then_groq_without_the_image():
    class Gem:
        name = "gemini"
        seen = None

        async def generate(self, system, turns, **kw):
            Gem.seen = turns
            raise AIQuotaExceeded(60)

    groq = Compat(lambda model, idx: "groq text reaction")
    img = ChatTurn("user", "[sticker: emoji 😂; pack x; static] React to this sticker.", b"\x89IMAGEBYTES", "image/webp")
    out = run(MultiProvider([Gem(), groq], hedge=2.5, budget=5).generate("sys", [img]))
    assert out == "groq text reaction"
    assert Gem.seen[0].image == b"\x89IMAGEBYTES"                                # Gemini got the picture
    sent = groq.msgs[0]
    assert sent[-1] == {"role": "user", "content": "[sticker: emoji 😂; pack x; static] React to this sticker."}
    assert "IMAGEBYTES" not in repr(sent) and "image" not in repr(sent).replace("React to this sticker", "").lower().replace("image/webp", "")


def test_everything_failed_raises_so_the_handler_can_show_busy():
    class Down:
        name = "gemini"

        async def generate(self, *a, **k):
            raise AIUnavailable("down")

    groq = Compat(lambda model, idx: AIUnavailable("down"))
    with pytest.raises(Exception) as e:
        run(MultiProvider([Down(), groq], hedge=0.5, budget=5).generate("s", [ChatTurn("user", "hi")]))
    assert isinstance(e.value, (AIUnavailable, AIQuotaExceeded))


def test_default_providers_are_only_gemini_and_groq_and_groq_models_are_in_order():
    src = open("ai/service.py").read()
    assert 'DEFAULT_ORDER = "gemini,groq"' in src
    assert '"qwen/qwen3.8-27b,openai/gpt-oss-120b,openai/gpt-oss-20b"' in src


def test_voice_note_input_limit_is_10_per_hour_per_person(monkeypatch):
    from services import voiceaccess as v
    monkeypatch.delenv("AI_VOICE_IN_PER_HOUR", raising=False)
    v._in_used.clear()
    for i in range(10):
        assert v.in_wait_secs(1, 100.0 + i) == 0
        v.in_count(1, 100.0 + i)
    assert v.in_wait_secs(1, 200.0) > 3000            # the 11th note within the hour is refused
    assert v.in_wait_secs(2, 200.0) == 0              # somebody else is not affected
    assert v.in_wait_secs(1, 100.0 + 3601) == 0       # frees up after an hour
    monkeypatch.setenv("AI_VOICE_IN_PER_HOUR", "3")
    assert v.in_limit() == 3


def test_v_voice_answer_limit_is_10_per_hour():
    from services import voiceaccess as v
    assert v.LIMIT == 10
    v._used.clear()
    for i in range(10):
        assert v.wait_secs(5, 100.0 + i) == 0
        v.count(5, 100.0 + i)
    assert v.wait_secs(5, 200.0) > 3000 and v.wait_secs(6, 200.0) == 0


def test_vtext_limit_is_10_per_hour_and_owner_is_unlimited(monkeypatch):
    from services import voiceaccess as v
    monkeypatch.delenv("AI_VTEXT_PER_HOUR", raising=False)
    v._vt_used.clear()
    for i in range(10):
        assert v.vtext_wait_secs(7, 100.0 + i) == 0
        v.vtext_count(7, 100.0 + i)
    assert v.vtext_wait_secs(7, 200.0) > 3000 and v.vtext_wait_secs(8, 200.0) == 0
    monkeypatch.setattr(v, "_is_owner", lambda uid: uid == 7)
    assert v.vtext_wait_secs(7, 200.0) == 0             # owner: no limit


def test_vtext_handler_is_group_only_reply_based_and_keeps_audio_in_memory():
    src = open("handlers/ai_chat.py").read()
    seg = src[src.index("async def vtext_cmd"):src.index("async def chat(")]
    assert 'm.chat.type == "private"' in seg and "reply_to_message" in seg and "rep.voice or rep.audio" in seg
    assert "write(" not in seg and "open(" not in seg
