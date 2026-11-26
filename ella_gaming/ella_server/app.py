"""Ella Gaming HTTP API + static Mini App host (Starlette / ASGI)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
import os
import random
import time
from pathlib import Path
from datetime import datetime

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import auth, categories, premium
from .catalogue import SLUG_RX, Catalogue
from .config import Settings, load
from .db import Database
from .ratelimit import RateLimiter
from .scanner import Scanner

log = logging.getLogger("ella.app")
NICK_RX = re.compile(r"^[\w .\-'’·!?+*~()\[\]]{1,24}$", re.UNICODE)
PLATFORM_RX = re.compile(r"^[a-z0-9_.-]{0,24}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message or code, headers or {}


def jerr(e: ApiError) -> JSONResponse:
    return JSONResponse({"error": e.code, "message": e.message}, status_code=e.status, headers=e.headers)


# ----------------------------------------------------------------------------------------------- helpers
def client_ip(request: Request, st: Settings) -> str:
    if st.trust_proxy:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()[:64]          # last hop = the one our own proxy appended
    return request.client.host if request.client else "unknown"


def S(request: Request) -> Settings:
    return request.app.state.st


def limit_ip(request: Request, per_min: int | None = None, bucket: str = "ip") -> None:
    st = S(request)
    wait = request.app.state.rl.hit(f"{bucket}:{client_ip(request, st)}", per_min or st.rate_ip_per_min)
    if wait:
        raise ApiError(429, "rate_limited", "Too many requests - slow down a little.", {"Retry-After": str(int(wait) + 1)})


async def authed(request: Request, write: bool = False) -> int:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise ApiError(401, "unauthorized", "Missing token")
    try:
        uid = auth.read_token(h[7:].strip(), S(request).session_secret)
    except auth.AuthError as e:
        raise ApiError(401, e.code, "Session expired" if e.code == "token_expired" else "Invalid token")
    if write:
        wait = request.app.state.rl.hit(f"user:{uid}", S(request).rate_write_per_min)
        if wait:
            raise ApiError(429, "rate_limited", "Too many requests - slow down a little.", {"Retry-After": str(int(wait) + 1)})
    return uid


async def body(request: Request) -> dict:
    if int(request.headers.get("content-length") or 0) > 16384:
        raise ApiError(413, "too_large")
    try:
        data = await request.json()
    except Exception:
        raise ApiError(400, "bad_json", "Body must be JSON")
    if not isinstance(data, dict):
        raise ApiError(400, "bad_json", "Body must be a JSON object")
    return data


def int_param(request: Request, name: str, default: int, lo: int, hi: int) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        raise ApiError(400, "bad_param", f"{name} must be an integer")
    return max(lo, min(hi, v))


def slug_param(slug: str) -> str:
    if not SLUG_RX.match(slug or ""):
        raise ApiError(400, "bad_slug")
    return slug


def public_player(p: dict) -> dict:
    return {"id": p["tg_id"], "first_name": p["first_name"], "last_name": p["last_name"], "username": p["username"],
            "photo_url": p["photo_url"], "nickname": p["nickname"], "display_name": p["nickname"] or p["first_name"] or p["username"] or "Player",
            "created_at": p["created_at"]}


def cards(cat: Catalogue, slugs: list[str]) -> list[dict]:
    return [cat.card(s) for s in slugs if cat.is_eligible(s)]


# ----------------------------------------------------------------------------------------------- routes
async def ads_txt(request: Request):
    """/ads.txt for ad partners (GamePix verifies it). ELLA_ADS_TXT (if set) wins, else the bundled ella_server/ads.txt."""
    body = (os.getenv("ELLA_ADS_TXT", "") or "").replace("\\n", "\n").strip()
    if not body:
        try:
            body = (Path(__file__).with_name("ads.txt")).read_text("utf-8").strip()
        except OSError:
            body = ""
    if not body:
        return Response("# ads.txt not set\n", media_type="text/plain", status_code=404)
    return Response(body + "\n", media_type="text/plain", headers={"Cache-Control": "no-cache"})


async def healthz(request: Request):
    cat: Catalogue = request.app.state.cat
    sc = request.app.state.scanner
    rep = cat.report()
    return JSONResponse({"ok": True, "scan": sc.status(), "verdicts": rep["verdicts"], "exclusion_reasons": rep["exclusion_reasons"], "games_loaded": len(cat.games), "eligible": len(cat.eligible_slugs()),
                         "catalogue_error": cat.last_error, "feed_entries": cat.raw_count, "dropped": cat.invalid_count,
                         "dropped_examples": cat.invalid[:3], "fetched_at": int(cat.fetched_at)})


async def auth_telegram(request: Request):
    st = S(request)
    limit_ip(request, st.rate_auth_per_min, "auth")
    data = await body(request)
    try:
        info = auth.validate_init_data(data.get("initData", ""), st.bot_token, st.init_data_max_age)
    except auth.AuthError as e:
        status = 503 if e.code == "server_misconfigured" else 401
        raise ApiError(status, e.code, str(e))
    player = await request.app.state.db.upsert_player(info["user"])
    token = auth.make_token(player["tg_id"], st.session_secret, st.token_ttl)
    return JSONResponse({"token": token, "expires_in": st.token_ttl, "player": public_player(player)})


async def me(request: Request):
    limit_ip(request)
    uid = await authed(request)
    db, cat = request.app.state.db, request.app.state.cat
    p = await db.get_player(uid)
    if not p:
        raise ApiError(401, "unauthorized", "Unknown player")
    favs = await db.favorites(uid)
    hist = await db.history(uid, 20)
    recent = await db.recent(uid, 20)
    return JSONResponse({
        "player": public_player(p), "favorite_slugs": favs,
        "favorites": cards(cat, favs)[:120],
        "recent": cards(cat, [r["slug"] for r in recent])[:20],
        "history": [{"game": cat.card(h["slug"]) if cat.is_eligible(h["slug"]) else {"slug": h["slug"], "name": h["slug"], "emoji": "", "categories": [], "thumbnail": ""},
                    "started_at": h["started_at"], "duration_s": h["duration_s"], "available": cat.is_eligible(h["slug"])} for h in hist],
    })


async def patch_me(request: Request):
    limit_ip(request)
    uid = await authed(request, write=True)
    data = await body(request)
    nick = data.get("nickname")
    if not isinstance(nick, str):
        raise ApiError(400, "bad_param", "nickname must be a string")
    nick = nick.strip()
    if nick and not NICK_RX.match(nick):
        raise ApiError(400, "bad_nickname", "Nicknames can be 1-24 letters, numbers and simple punctuation")
    await request.app.state.db.set_nickname(uid, nick)
    return JSONResponse({"player": public_player(await request.app.state.db.get_player(uid))})


def _loaded(request: Request) -> Catalogue:
    cat: Catalogue = request.app.state.cat
    if not cat.games:
        raise ApiError(503, "catalogue_unavailable", "The game catalogue is loading or temporarily unavailable.", {"Retry-After": "5"})
    return cat


PREMIUM_OWN = 3                                          # our own tables: UNO, Ludo, Chess (counted in the totals)


async def home(request: Request):
    limit_ip(request)
    uid = await authed(request)
    cat, db = _loaded(request), request.app.state.db
    pop = await db.popularity()
    if sum(pop.values()) > 0:
        popular = cat.search(sort="popular", popularity=pop)[:20]
    else:
        popular = cat.featured(20, day=int(time.time() // 86400) + 1000)
    favs = await db.favorites(uid)
    recent = await db.recent(uid, 12)
    sc: Scanner = request.app.state.scanner
    counts = cat.counts()
    day_rng = random.Random(int(time.time() // 86400))                    # the same shuffle all day, a fresh one tomorrow
    rails = []
    for c in sorted(categories.CATEGORIES, key=lambda c: -counts[c["id"]]):
        slugs = cat.search(category=c["id"])
        if len(slugs) >= 6 and len(rails) < 8:
            rails.append({"id": c["id"], "name": c["name"], "count": counts[c["id"]], "items": [cat.card(x) for x in day_rng.sample(slugs, min(14, len(slugs)))]})
    return JSONResponse({
        "rails": rails,
        "featured": [cat.card(s) for s in cat.featured(12)],
        "popular": [cat.card(s) for s in popular],
        "recent": cards(cat, [r["slug"] for r in recent])[:12],
        "favorites": cards(cat, favs)[:12], "favorite_slugs": favs,
        "categories": [{**c, "count": counts[c["id"]]} for c in categories.CATEGORIES],
        "total_games": len(cat.eligible_slugs()) + PREMIUM_OWN, "premium_total": len(cat.top_slugs()) + PREMIUM_OWN, "verifying": sc.status()["pending"] if sc.running else 0,
    }, headers={"Cache-Control": "private, no-store"})


async def games(request: Request):
    limit_ip(request)
    await authed(request)
    cat = _loaded(request)
    q = request.query_params.get("q", "")[:60]
    category = request.query_params.get("category", "")
    sort = request.query_params.get("sort", "az")
    if category and category not in categories.IDS:
        raise ApiError(400, "bad_param", "unknown category")
    if sort not in ("az", "popular"):
        raise ApiError(400, "bad_param", "unknown sort")
    offset, limit = int_param(request, "offset", 0, 0, 100000), int_param(request, "limit", 24, 1, 48)
    pop = await request.app.state.db.popularity() if sort == "popular" else None
    found = cat.search(q, category, sort, pop)
    if request.query_params.get("top") == "1":           # Trending tab: only the best games, best first
        top = cat.top_slugs()
        rank = {s: i for i, s in enumerate(top)}
        found = sorted((s for s in found if s in rank), key=lambda s: rank[s]) if not q else [s for s in found if s in rank]
    sc: Scanner = request.app.state.scanner
    return JSONResponse({"items": [cat.card(s) for s in found[offset:offset + limit]], "total": len(found),
                         "next_offset": offset + limit if offset + limit < len(found) else None,
                         "verifying": sc.status()["pending"] if sc.running else 0},
                        headers={"Cache-Control": "private, max-age=30"})


async def game_detail(request: Request):
    limit_ip(request)
    uid = await authed(request)
    cat = _loaded(request)
    slug = slug_param(request.path_params["slug"])
    if not cat.is_eligible(slug):
        raise ApiError(404, "game_unavailable", "This game is not available.")
    return JSONResponse({**cat.detail(slug), "favorite": slug in set(await request.app.state.db.favorites(uid))})


async def favorite(request: Request):
    limit_ip(request)
    uid = await authed(request, write=True)
    cat = _loaded(request)
    slug = slug_param(request.path_params["slug"])
    on = request.method == "PUT"
    if on and not cat.is_eligible(slug):
        raise ApiError(404, "game_unavailable", "This game is not available.")
    ok = await request.app.state.db.set_favorite(uid, slug, on)
    if not ok:
        raise ApiError(409, "favorites_full", "You reached the favourites limit.")
    return JSONResponse({"slug": slug, "favorite": on})


async def session_start(request: Request):
    limit_ip(request)
    uid = await authed(request, write=True)
    cat = _loaded(request)
    data = await body(request)
    slug = slug_param(data.get("slug", ""))
    if not cat.is_eligible(slug):
        raise ApiError(404, "game_unavailable", "This game is not available.")
    platform = str(data.get("platform", "")).lower()
    sid = await request.app.state.db.start_session(uid, slug, platform if PLATFORM_RX.match(platform) else "")
    return JSONResponse({"session_id": sid, "play_url": cat.embed_url(slug), "game": cat.detail(slug)})


async def session_touch(request: Request):
    limit_ip(request)
    uid = await authed(request, write=True)
    try:
        sid = int(request.path_params["sid"])
    except ValueError:
        raise ApiError(400, "bad_param")
    ended = request.url.path.endswith("/end")
    if not await request.app.state.db.touch_session(uid, sid, ended):
        raise ApiError(404, "no_session")
    return JSONResponse({"ok": True})


async def admin_report(request: Request):
    limit_ip(request)
    uid = await authed(request)
    if uid not in S(request).admin_ids:
        raise ApiError(403, "forbidden")
    cat, sc = request.app.state.cat, request.app.state.scanner
    return JSONResponse({**cat.report(), "scanner": sc.status()})


async def admin_rescan(request: Request):
    limit_ip(request)
    uid = await authed(request, write=True)
    if uid not in S(request).admin_ids:
        raise ApiError(403, "forbidden")
    request.app.state.scanner.start(force=True)
    return JSONResponse({"started": True})


async def api_not_found(request: Request):
    return JSONResponse({"error": "not_found"}, status_code=404)


# ----------------------------------------------------------------------------------------------- middleware
def csp(st: Settings) -> str:
    frames = " ".join(f"https://{h}" if not st.dev else f"http://{h} https://{h}" for h in st.embed_hosts)
    api = " ".join(st.allowed_origins) if not st.serve_frontend else ""
    imgs = " ".join([f"https://{h}" if not st.dev else f"http://{h} https://{h}" for h in st.embed_hosts] + [f"https://{h}" for h in st.image_hosts])
    return "; ".join([
        "default-src 'none'",
        "script-src 'self' https://telegram.org",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        f"img-src 'self' data: https://t.me https://*.telegram.org https://*.telesco.pe {imgs}",
        f"connect-src 'self' {api}".strip(),
        f"frame-src {frames}",
        "manifest-src 'self'",
        "base-uri 'none'", "form-action 'none'", "object-src 'none'",
        "frame-ancestors https://web.telegram.org https://webk.telegram.org https://webz.telegram.org https://*.telegram.org",
    ])


class SecurityHeaders:
    """Pure ASGI middleware: security headers on every response + cache policy."""

    def __init__(self, app, st: Settings):
        self.app, self.st, self.csp = app, st, csp(st)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]

        async def wrapped(msg):
            if msg["type"] == "http.response.start":
                h = {k.lower(): v for k, v in msg.get("headers", [])}
                extra = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"strict-origin-when-cross-origin"),
                         (b"permissions-policy", b"geolocation=(), microphone=(), camera=(), payment=()"),
                         (b"content-security-policy", self.csp.encode())]
                if b"cache-control" not in h:
                    if path.startswith("/api/") or path == "/healthz":
                        extra.append((b"cache-control", b"no-store"))
                    elif path in ("/", "/index.html"):
                        extra.append((b"cache-control", b"no-cache"))
                    else:
                        extra.append((b"cache-control", b"public, max-age=300"))
                msg["headers"] = list(msg.get("headers", [])) + extra
            await send(msg)
        await self.app(scope, receive, wrapped)


async def api_error_handler(request: Request, exc: ApiError):
    return jerr(exc)


async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"error": "server_error", "message": "Something went wrong on our side."}, status_code=500)


# ----------------------------------------------------------------------------------------------- app factory
def create_app(st: Settings | None = None) -> Starlette:
    st = st or load()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        db = Database(st.db_path)
        cat = Catalogue(st)
        scanner = Scanner(st, cat, db)
        app.state.db, app.state.cat, app.state.scanner = db, cat, scanner
        cat.load_disk_cache()
        await scanner.load_verdicts()

        async def maintain():
            while True:
                try:
                    if cat.stale or not cat.games:
                        await cat.refresh(force=True)
                    if st.scan_on_start and cat.games and not scanner.running:
                        scanner.start()
                except Exception:
                    log.exception("maintenance loop error")
                await asyncio.sleep(60 if not cat.games else 900)

        task = asyncio.create_task(maintain())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
            db.close()

    routes = [
        Route("/healthz", healthz),
        Route("/ads.txt", ads_txt),
        Route("/api/auth/telegram", auth_telegram, methods=["POST"]),
        Route("/api/me", me, methods=["GET"]),
        Route("/api/me", patch_me, methods=["PATCH"]),
        Route("/api/home", home, methods=["GET"]),
        Route("/api/games", games, methods=["GET"]),
        Route("/api/games/{slug}", game_detail, methods=["GET"]),
        Route("/api/favorites/{slug}", favorite, methods=["PUT", "DELETE"]),
        Route("/api/sessions", session_start, methods=["POST"]),
        Route("/api/sessions/{sid}/ping", session_touch, methods=["POST"]),
        Route("/api/sessions/{sid}/end", session_touch, methods=["POST"]),
        Route("/api/admin/report", admin_report, methods=["GET"]),
        Route("/api/admin/rescan", admin_rescan, methods=["POST"]),
        *premium.build_routes(sys.modules[__name__]),
        Route("/api/{rest:path}", api_not_found),
    ]
    if st.serve_frontend:
        routes.append(Mount("/", StaticFiles(directory=str(st.web_dir), html=True), name="web"))
    middleware = [Middleware(SecurityHeaders, st=st)]
    if st.allowed_origins:
        middleware.append(Middleware(CORSMiddleware, allow_origins=list(st.allowed_origins), allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                                     allow_headers=["Authorization", "Content-Type"], max_age=600))
    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan,
                    exception_handlers={ApiError: api_error_handler, Exception: unhandled})
    app.state.st = st
    app.state.rl = RateLimiter()
    return app
