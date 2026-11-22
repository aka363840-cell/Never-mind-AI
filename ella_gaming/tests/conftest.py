"""Shared fixtures: runs the fake upstream + the real Ella server as real HTTP servers (uvicorn in threads)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

BOT_TOKEN = "123456:TEST-TOKEN-not-real"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_init_data(user: dict, token: str = BOT_TOKEN, auth_date: int | None = None, tamper: bool = False) -> str:
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAH-test", "user": json.dumps(user, separators=(",", ":"))}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields["user"] = fields["user"].replace("Ada", "Eve")
    return urlencode(fields)


class Srv:
    def __init__(self, app, port):
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture(scope="session")
def upstream():
    import fake_upstream
    s = Srv(fake_upstream.make_app(), free_port()).start()
    yield s
    s.stop()


@pytest.fixture()
def settings(upstream, tmp_path):
    from ella_server import config
    os.environ.update({
        "ELLA_DEV": "1", "ELLA_BOT_TOKEN": BOT_TOKEN, "ELLA_SESSION_SECRET": "s" * 48, "ELLA_DATA_DIR": str(tmp_path),
        "ELLA_CATALOGUE_URL": f"{upstream.url}/api/games.json", "ELLA_EMBED_HOSTS": f"127.0.0.1:{upstream.port}",
        "ELLA_SCAN_ON_START": "1", "ELLA_SCAN_BROWSER": "0", "ELLA_ADMIN_IDS": "1001", "ELLA_RATE_AUTH_PER_MIN": "1000",
        "ELLA_RATE_IP_PER_MIN": "100000", "ELLA_PUBLIC_URL": "http://127.0.0.1",
    })
    return config.load()


@pytest.fixture()
def ella(settings):
    from ella_server.app import create_app
    app = create_app(settings)
    s = Srv(app, free_port()).start()
    # wait for catalogue + scan to settle
    import urllib.request
    for _ in range(200):
        sc = app.state.scanner
        if app.state.cat.games and not sc.running and sc.total and sc.done >= sc.total:
            break
        time.sleep(0.1)
    s.app = app
    yield s
    s.stop()
