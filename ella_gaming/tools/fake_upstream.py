"""A LOCAL STAND-IN for gamezipper.com used only by the test-suite (it is not GameZipper and says nothing about it).
Serves a feed in the documented shape plus game pages of several kinds so the scanner / player can be tested:
  clean            - a tiny touch game on a <canvas>
  ads-static       - AdSense script + 'Sponsored · Advertisement' slot in the HTML
  ads-script       - first-party script that references an ad network
  framed-blocked   - X-Frame-Options: DENY
  runtime-ads      - looks clean statically; injects an ad iframe + opens a popup after load (browser check only)
  missing          - 404
  slow             - takes 6 s to answer (loading state)
Run:  python tools/fake_upstream.py 9001"""
from __future__ import annotations

import sys

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

KINDS = {}
EXTRA_KINDS = {            # served but NOT in the feed: used to test the 'review' verdict and the rendered-ads check
    "label-visible": "label-visible", "label-hidden": "label-hidden", "label-collapsed": "label-collapsed",
}
FEED = []
_CATS = ["puzzle", "arcade", "skill", "idle", "card", "board", "casual", "sports", "racing", "word", "simulation", "puzzle"]
_TAGS = {0: ["logic puzzle"], 1: ["arcade", "shooter"], 2: ["reflex"], 3: ["idle clicker", "tycoon"], 4: ["solitaire"], 5: ["chess", "2 player"],
         6: ["relaxing"], 7: ["football"], 8: ["car racing"], 9: ["wordle"], 10: ["tower defense", "strategy"], 11: ["dungeon", "adventure"]}


def _build(n_clean: int = 52):
    FEED.clear(); KINDS.clear()
    for i in range(n_clean):
        FEED.append((f"demo-{i:03d}", "clean", i))
    FEED.extend([("ad-static", "ads-static", 1), ("ad-script", "ads-script", 2), ("blocked-frame", "framed-blocked", 3),
                 ("runtime-ad", "runtime-ads", 4), ("gone-404", "missing", 5), ("slow-game", "slow", 6)])
    return FEED


def feed_json(base: str) -> list:
    out = []
    for slug, kind, i in FEED:
        KINDS[slug] = kind
        words = ["Star", "Block", "Dash", "Loom", "Quest", "Garden", "Rocket", "Cipher", "Maze", "Pixel", "Orbit", "Tango", "Nimbus"]
        special = {"ad-static": "Static Ads Game", "ad-script": "Script Ads Game", "blocked-frame": "Frame Blocked Game",
                   "runtime-ad": "Runtime Ads Game", "gone-404": "Missing Game", "slow-game": "Slow Burner"}
        name = special.get(slug) or f"{words[i % len(words)]} {words[(i * 7 + 3) % len(words)]} {i}"
        out.append({"slug": slug, "name": name, "emoji": "🎮", "category": _CATS[i % len(_CATS)], "tags": [name.lower()] + _TAGS[i % 12],
                    "embedUrl": f"{base}/{slug}/", "thumbnail": f"{base}/thumbs/{slug}.svg"})
    # entries the backend must reject
    out.append({"slug": "evil-host", "name": "Evil", "emoji": "x", "category": "arcade", "tags": [], "embedUrl": "https://evil.example/x/", "thumbnail": ""})
    out.append({"slug": "Bad Slug!", "name": "Bad", "emoji": "x", "category": "arcade", "tags": [], "embedUrl": f"{base}/x/", "thumbnail": ""})
    out.append({"slug": "demo-000", "name": "Duplicate", "emoji": "x", "category": "arcade", "tags": [], "embedUrl": f"{base}/demo-000/", "thumbnail": ""})
    out.append("not-an-object")
    return out


CLEAN = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1,user-scalable=no">
<title>{title}</title><style>html,body{{margin:0;height:100%;background:#123;color:#fff;font-family:sans-serif;touch-action:manipulation}}
#c{{width:100%;height:100%;display:block}}</style></head><body><canvas id=c></canvas><div id=score style="position:fixed;top:8px;left:8px">score: 0</div>
<script src="game.js"></script></body></html>"""
CLEAN_JS = """const c=document.getElementById('c'),x=c.getContext('2d');let s=0;c.width=innerWidth;c.height=innerHeight;
function tap(e){s++;document.getElementById('score').textContent='score: '+s;x.fillStyle='hsl('+(s*40)+',80%,60%)';x.beginPath();x.arc(innerWidth/2,innerHeight/2,20+s,0,7);x.fill();}
c.addEventListener('pointerdown',tap);window.__tapCount=()=>s;"""


async def feed(request):
    base = f"http://{request.headers['host']}"
    return JSONResponse(feed_json(base))


async def thumb(request):
    return Response('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200"><rect width="320" height="200" fill="#3a2a7a"/></svg>', media_type="image/svg+xml")


async def game(request):
    import asyncio
    slug = request.path_params["slug"]
    kind = KINDS.get(slug) or EXTRA_KINDS.get(slug)
    if kind is None or kind == "missing":
        return PlainTextResponse("nope", status_code=404)
    if kind == "slow":
        await asyncio.sleep(6)
    html = CLEAN.format(title=slug)
    headers = {}
    if kind == "ads-static":
        html = html.replace("</head>", '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"></script></head>').replace(
            "<body>", '<body><div class="ad-slot">Sponsored · Advertisement</div>')
    if kind == "label-visible":
        html = html.replace("<body>", '<body><div class="ad-slot" style="height:60px">Sponsored · Advertisement</div>')
    if kind == "label-hidden":
        html = html.replace("<body>", '<body><div class="ad-slot" style="display:none">Sponsored · Advertisement</div>')
    if kind == "label-collapsed":
        html = html.replace("<body>", '<body><div style="height:0;overflow:hidden"><div class="ad-slot">Sponsored · Advertisement</div></div>')
    if kind == "framed-blocked":
        headers["X-Frame-Options"] = "DENY"
    if kind == "runtime-ads":
        html = html.replace("</body>", '<script src="rt.js"></script></body>')
    if kind == "ads-script":
        html = html.replace("game.js", "bad.js")
    return HTMLResponse(html, headers=headers)


async def js(request):
    slug, name = request.path_params["slug"], request.path_params["name"]
    kind = KINDS.get(slug)
    if name == "game.js":
        return Response(CLEAN_JS, media_type="application/javascript")
    if name == "bad.js":
        return Response(CLEAN_JS + "\nfetch('https://ads.exoclick.com/x');", media_type="application/javascript")
    if name == "rt.js" and kind == "runtime-ads":
        # built at runtime so no static scanner can see the domain
        return Response("setTimeout(()=>{const d=['doub','leclick'].join('');const f=document.createElement('iframe');"
                        "f.src='https://ad.'+d+'.net/x';f.className='zz';document.body.appendChild(f);},300);", media_type="application/javascript")
    return PlainTextResponse("", status_code=404)


def make_app() -> Starlette:
    _build()
    return Starlette(routes=[
        Route("/api/games.json", feed), Route("/thumbs/{n}", thumb),
        Route("/{slug}/", game), Route("/{slug}/{name}", js),
    ])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(make_app(), host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 9001, log_level="warning")
