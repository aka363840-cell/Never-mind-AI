"""Provider chain: fallback, parallel backup for slow providers, OpenAI-compatible rotation. No network (local fake server)."""
from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import web

from ai.base import AIQuotaExceeded, AIUnavailable, ChatTurn
from ai.multi import MultiProvider
from ai.openai_compat import OpenAICompatProvider

T = [ChatTurn("user", "hi")]


class Fake:
    def __init__(self, name, result=None, delay=0.0, exc=None):
        self.name, self.result, self.delay, self.exc, self.calls = name, result, delay, exc, 0

    async def generate(self, system, turns, **kw):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.result


def run(c):
    return asyncio.run(c)


def test_fast_failure_switches_instantly():
    a, b = Fake("a", exc=AIQuotaExceeded(60)), Fake("b", "from b")
    t = time.monotonic()
    assert run(MultiProvider([a, b], hedge=2.5, budget=5).generate("s", T)) == "from b"
    assert time.monotonic() - t < 0.5


def test_slow_primary_is_raced_by_backup():
    a, b = Fake("a", "from a", delay=3), Fake("b", "from b", delay=0.1)
    t = time.monotonic()
    assert run(MultiProvider([a, b], hedge=0.3, budget=5).generate("s", T)) == "from b"
    assert time.monotonic() - t < 1.0


def test_failed_provider_is_skipped_next_time():
    a, b = Fake("a", exc=AIUnavailable("down")), Fake("b", "ok")

    async def go():
        m = MultiProvider([a, b], hedge=1, budget=5)
        await m.generate("s", T)
        await m.generate("s", T)
        return a.calls

    assert run(go()) == 1


def test_all_fail_raises():
    m = MultiProvider([Fake("a", exc=AIQuotaExceeded(30)), Fake("b", exc=AIQuotaExceeded(30))], budget=3)
    with pytest.raises(AIQuotaExceeded):
        run(m.generate("s", T))


def test_openai_compat_rotates_keys_and_models():
    seen = []

    async def handler(req):
        auth, body = req.headers["Authorization"], await req.json()
        seen.append((auth, body["model"]))
        if auth.endswith("k1"):
            return web.json_response({"error": {"message": "rate"}}, status=429)
        if body["model"] == "dead":
            return web.json_response({"error": {"message": "model not found"}}, status=404)
        return web.json_response({"choices": [{"message": {"content": "<think>x</think> hello"}}]})

    async def go():
        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        p = OpenAICompatProvider("x", f"http://127.0.0.1:{port}/v1", ["k1", "k2"], ["dead", "good"], timeout=3, budget=6)
        try:
            return await p.generate("s", T)
        finally:
            await p.close()
            await runner.cleanup()

    assert run(go()) == "hello"
