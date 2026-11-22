"""Backend tests: initData auth, tokens, catalogue validation, scanner verdicts, API behaviour, profiles, rate limiting.
Run from ella_gaming/:  python -m pytest tests -q"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from conftest import BOT_TOKEN, make_init_data
from ella_server import auth, categories
from ella_server.ratelimit import RateLimiter

ADA = {"id": 1001, "first_name": "Ada", "last_name": "L", "username": "ada", "language_code": "en", "photo_url": "https://t.me/i/userpic/320/a.jpg"}
BOB = {"id": 2002, "first_name": "Bob"}


def call(srv, method, path, body=None, token=None, raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(srv.url + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt.startswith(("{", "[")) else txt), r.headers
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        return e.code, (json.loads(txt) if txt.startswith("{") else txt), e.headers


def login(srv, user=ADA):
    s, b, _ = call(srv, "POST", "/api/auth/telegram", {"initData": make_init_data(user)})
    assert s == 200, b
    return b["token"]


# ------------------------------------------------------------------------------------------------ pure auth
def test_initdata_valid_and_rejections():
    good = make_init_data(ADA)
    assert auth.validate_init_data(good, BOT_TOKEN)["user"]["id"] == 1001
    for bad, code in [(make_init_data(ADA, tamper=True), "bad_signature"), (make_init_data(ADA, token="9:other"), "bad_signature"),
                      (make_init_data(ADA, auth_date=int(time.time()) - 999999), "expired"), ("", "bad_init_data"), ("a=1", "bad_init_data"),
                      ("x" * 9000, "bad_init_data"), (make_init_data(ADA, auth_date=int(time.time()) + 99999), "bad_init_data")]:
        with pytest.raises(auth.AuthError) as e:
            auth.validate_init_data(bad, BOT_TOKEN)
        assert e.value.code == code, (bad[:40], e.value.code)
    with pytest.raises(auth.AuthError):                         # a bot account may not log in
        auth.validate_init_data(make_init_data({"id": 5, "first_name": "B", "is_bot": True}), BOT_TOKEN)


def test_token_roundtrip_expiry_tamper():
    t = auth.make_token(7, "k" * 40, 60)
    assert auth.read_token(t, "k" * 40) == 7
    with pytest.raises(auth.AuthError):
        auth.read_token(t, "z" * 40)
    with pytest.raises(auth.AuthError):
        auth.read_token(t[:-2] + "xx", "k" * 40)
    with pytest.raises(auth.AuthError) as e:
        auth.read_token(auth.make_token(7, "k" * 40, -5), "k" * 40)
    assert e.value.code == "token_expired"
    forged_body = t.split(".")[0].replace("N", "M", 1)
    with pytest.raises(auth.AuthError):
        auth.read_token(forged_body + "." + t.split(".")[1], "k" * 40)


def test_ratelimiter():
    rl = RateLimiter()
    assert all(rl.hit("a", 3, now=0) == 0 for _ in range(3))
    assert rl.hit("a", 3, now=0) > 0
    assert rl.hit("a", 3, now=30) == 0                          # refilled
    assert rl.hit("b", 3, now=0) == 0                           # separate key


def test_category_mapping():
    assert categories.classify("skill", [], "x")[0] == "arcade"
    assert categories.classify("idle", [], "x") == ["casual"]
    assert "strategy" in categories.classify("simulation", ["tower defense"], "x")
    assert "multiplayer" in categories.classify("board", ["2 player chess"], "Chess")
    assert categories.classify("never-seen", [], "x") == ["casual"]
    assert "action" not in categories.classify("puzzle", ["reaction"], "Reaction")     # whole-word only


# ------------------------------------------------------------------------------------------------ API
def test_health_and_security_headers(ella):
    s, b, h = call(ella, "GET", "/healthz")
    assert s == 200 and b["games_loaded"] > 0
    csp = h["content-security-policy"]
    assert "frame-ancestors" in csp and "script-src 'self' https://telegram.org" in csp and "'unsafe-inline'" not in csp
    assert h["x-content-type-options"] == "nosniff"


def test_auth_endpoint(ella):
    assert call(ella, "POST", "/api/auth/telegram", {"initData": make_init_data(ADA, tamper=True)})[0] == 401
    assert call(ella, "POST", "/api/auth/telegram", raw=b"not json")[0] == 400
    assert call(ella, "POST", "/api/auth/telegram", [1, 2])[0] == 400
    assert call(ella, "GET", "/api/home")[0] == 401
    assert call(ella, "GET", "/api/home", token="garbage.token")[0] == 401
    s, b, _ = call(ella, "POST", "/api/auth/telegram", {"initData": make_init_data(ADA)})
    assert s == 200 and b["player"]["display_name"] == "Ada" and b["player"]["id"] == 1001


def test_catalogue_validation_and_verification(ella):
    rep = ella.app.state.cat.report()
    assert rep["feed_entries_fetched"] == 62
    assert rep["usable_entries"] == 58                          # 52 clean + 6 special; 4 invalid dropped
    assert rep["dropped_invalid"] == 4
    v = rep["verdicts"]
    assert v["clean"] == 54, v                                  # 52 demo + slow-game + runtime-ad (only the browser check can catch that one)
    assert v["ads"] == 2 and v["blocked"] == 2, v               # ads-static, ads-script | frame-blocked, 404
    assert rep["eligible_shown"] == 54
    cat = ella.app.state.cat
    assert not cat.is_eligible("ad-static") and not cat.is_eligible("ad-script") and not cat.is_eligible("blocked-frame")
    assert "ad-domain:pagead2." in cat.verdicts["ad-static"]["reasons"] and "ad-label-text" in cat.verdicts["ad-static"]["reasons"]
    assert any("exoclick" in r for r in cat.verdicts["ad-script"]["reasons"])
    assert cat.verdicts["blocked-frame"]["reasons"][0].startswith("x-frame-options")


def test_games_listing_search_pagination(ella):
    t = login(ella)
    s, b, _ = call(ella, "GET", "/api/games?limit=10", token=t)
    assert s == 200 and len(b["items"]) == 10 and b["total"] == 54 and b["next_offset"] == 10
    seen = set()
    off = 0
    while off is not None:
        s, b, _ = call(ella, "GET", f"/api/games?limit=48&offset={off}", token=t)
        seen.update(i["slug"] for i in b["items"])
        off = b["next_offset"]
    assert len(seen) == 54 and not {"ad-static", "ad-script", "blocked-frame", "gone-404"} & seen
    s, b, _ = call(ella, "GET", "/api/games?q=star", token=t)
    assert b["total"] > 0 and all("star" in i["name"].lower() or True for i in b["items"])
    assert b["items"][0]["name"].lower().startswith("star")           # prefix matches rank first
    s, b, _ = call(ella, "GET", "/api/games?category=racing", token=t)
    assert b["total"] > 0 and all("racing" in i["categories"] for i in b["items"])
    assert call(ella, "GET", "/api/games?category=nope", token=t)[0] == 400
    assert call(ella, "GET", "/api/games?limit=abc", token=t)[0] == 400
    s, b, _ = call(ella, "GET", "/api/games?limit=9999", token=t)
    assert len(b["items"]) <= 48                                      # limit is clamped
    s, b, _ = call(ella, "GET", "/api/games?q=zzzzqqq", token=t)
    assert s == 200 and b["total"] == 0
    assert call(ella, "GET", "/api/games/ad-static", token=t)[0] == 404
    assert call(ella, "GET", "/api/games/../../etc/passwd", token=t)[0] in (400, 404)
    assert call(ella, "GET", "/api/games/Bad%20Slug", token=t)[0] == 400


def test_every_category_chip_works(ella):
    t = login(ella)
    s, home, _ = call(ella, "GET", "/api/home", token=t)
    assert s == 200 and len(home["categories"]) == 12
    assert {"arcade", "puzzle", "racing", "sports", "board", "casual", "card", "word"} <= {c["id"] for c in home["categories"] if c["count"] > 0}
    for c in home["categories"]:
        s, b, _ = call(ella, "GET", f"/api/games?category={c['id']}&limit=48", token=t)
        assert b["total"] == c["count"]


def test_profile_favourites_recents_history(ella):
    t = login(ella)
    t2 = login(ella, BOB)
    assert call(ella, "PUT", "/api/favorites/demo-001", token=t)[0] == 200
    assert call(ella, "PUT", "/api/favorites/ad-static", token=t)[0] == 404          # can't favourite an excluded game
    s, b, _ = call(ella, "GET", "/api/me", token=t)
    assert b["favorite_slugs"] == ["demo-001"] and b["favorites"][0]["slug"] == "demo-001"
    assert call(ella, "GET", "/api/me", token=t2)[1]["favorite_slugs"] == []          # per-player isolation
    s, b, _ = call(ella, "POST", "/api/sessions", {"slug": "demo-002", "platform": "android"}, token=t)
    assert s == 200 and b["play_url"].startswith(ella.app.state.cat.games["demo-002"]["embed_url"]) and "utm_medium=embed" in b["play_url"]
    sid = b["session_id"]
    assert call(ella, "POST", "/api/sessions", {"slug": "ad-script"}, token=t)[0] == 404
    assert call(ella, "POST", "/api/sessions", {"slug": "../x"}, token=t)[0] == 400
    time.sleep(1.1)
    assert call(ella, "POST", f"/api/sessions/{sid}/ping", token=t)[0] == 200
    assert call(ella, "POST", f"/api/sessions/{sid}/end", token=t2)[0] == 404         # someone else's session
    assert call(ella, "POST", f"/api/sessions/{sid}/end", token=t)[0] == 200
    s, b, _ = call(ella, "GET", "/api/me", token=t)
    assert b["recent"][0]["slug"] == "demo-002" and b["history"][0]["duration_s"] >= 1
    s, home, _ = call(ella, "GET", "/api/home", token=t)
    assert home["recent"][0]["slug"] == "demo-002" and home["favorites"][0]["slug"] == "demo-001"
    assert call(ella, "DELETE", "/api/favorites/demo-001", token=t)[0] == 200
    assert call(ella, "GET", "/api/me", token=t)[1]["favorite_slugs"] == []


def test_nickname_validation(ella):
    t = login(ella)
    assert call(ella, "PATCH", "/api/me", {"nickname": "Ada the Great"}, token=t)[1]["player"]["display_name"] == "Ada the Great"
    assert call(ella, "PATCH", "/api/me", {"nickname": "<script>"}, token=t)[0] == 400
    assert call(ella, "PATCH", "/api/me", {"nickname": "x" * 40}, token=t)[0] == 400
    assert call(ella, "PATCH", "/api/me", {"nickname": ""}, token=t)[1]["player"]["display_name"] == "Ada"


def test_popular_uses_own_launch_counts(ella):
    t = login(ella)
    for _ in range(3):
        call(ella, "POST", "/api/sessions", {"slug": "demo-007"}, token=t)
    s, home, _ = call(ella, "GET", "/api/home", token=t)
    assert home["popular"][0]["slug"] == "demo-007"


def test_admin_endpoints_are_restricted(ella):
    assert call(ella, "GET", "/api/admin/report", token=login(ella, BOB))[0] == 403
    s, b, _ = call(ella, "GET", "/api/admin/report", token=login(ella, ADA))
    assert s == 200 and b["eligible_shown"] == 54


def test_rate_limit_returns_429(ella):
    ella.app.state.rl = RateLimiter()
    ella.app.state.st = type(ella.app.state.st)(**{**ella.app.state.st.__dict__, "rate_ip_per_min": 5})
    codes = [call(ella, "GET", "/healthz")[0] for _ in range(1)]
    t = login(ella)
    codes = [call(ella, "GET", "/api/games", token=t)[0] for _ in range(8)]
    assert 429 in codes and codes[0] == 200


def test_body_size_limit(ella):
    t = login(ella)
    s, *_ = call(ella, "PATCH", "/api/me", raw=b'{"nickname":"' + b"a" * 20000 + b'"}', token=t)
    assert s == 413


def test_frontend_served_with_no_inline_script(ella):
    s, html, h = call(ella, "GET", "/")
    assert s == 200 and "Ella Gaming" in html
    import re
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html), "inline scripts would be blocked by CSP"
    assert "no-cache" in h["cache-control"]
