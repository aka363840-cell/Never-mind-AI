"""Per-game verification: is the game embeddable, and is it free of advertising?

Ella shows a game only when its verdict is `clean` (policy `verified_only`, the default).
Verdicts: clean | ads (ad-network code / rendered ads / pop-ups) | blocked (not embeddable) | error (could not check, retried)
          | review (ad-LIKE markup or a "Sponsored / Advertisement" label in the HTML, but no ad-network code: excluded until the
            browser check proves nothing ad-related is actually rendered - then it becomes clean, otherwise ads).

STATIC CHECK (always on) - fetches the exact embed URL a player would load and
  * requires HTTP 200 + HTML, and the final URL must stay on an allowed host (no redirects away);
  * rejects games that forbid framing (X-Frame-Options DENY/SAMEORIGIN, or a CSP frame-ancestors that excludes us);
    we never try to bypass those;
  * scans the HTML and the game's own first-party scripts for advertising: known ad-network domains / SDKs,
    AdSense/GPT/IMA markers, ad-slot elements, "Sponsored / Advertisement" labels  -> verdict `ads`.
  Soft findings (e.g. an AdSense account <meta> without any ad script/slot, window.open usage) are kept as warnings.

BROWSER CHECK (optional, ELLA_SCAN_BROWSER=1, needs `playwright` + Chromium) - actually loads the game, taps it, and
  records requests to ad hosts, popups, dialogs and ad elements that appear at runtime. This catches ads injected by
  JavaScript, which a static scan cannot see.

Limits (also listed in the README): a scan is a snapshot - a provider can change a game later, so verdicts expire
(ELLA_SCAN_TTL) and are re-checked; ads that only appear after long play or specific levels may be missed. The player
iframe is additionally sandboxed without allow-popups / allow-top-navigation, so a game cannot open pop-ups or
redirect the Mini App even if it tries."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from .catalogue import UA, Catalogue
from .config import Settings
from .db import Database

log = logging.getLogger("ella.scanner")

AD_DOMAINS = (
    "googlesyndication.com", "doubleclick.net", "googleadservices.com", "googletagservices.com", "adservice.google",
    "pagead2.", "adsbygoogle", "imasdk.googleapis.com", "ima3.js", "securepubads", "amazon-adsystem.com", "adsterra",
    "highperformanceformat", "profitableratecpm", "propellerads", "propeller-tracking", "popads.net", "popcash.net",
    "exoclick.com", "exosrv.com", "juicyads", "trafficjunky", "clickadu", "hilltopads", "monetag", "richads",
    "taboola.com", "outbrain.com", "mgid.com", "revcontent.com", "media.net", "criteo.", "pubmatic.com", "rubiconproject.com",
    "openx.net", "adnxs.com", "appnexus.com", "smartadserver", "adform.net", "yandex.ru/ads", "an.yandex", "yandexadexchange",
    "gamemonetize.com", "gamedistribution.com", "html5.gamedistribution", "api.adinplay", "adinplay.com", "venatus.com",
    "applixir", "a-ads.com", "coinzilla", "bidvertiser", "adcash", "adsco.re", "ad-maven", "admaven", "poki-gdn", "sdk.crazygames.com",
    "afg.google", "gpt.js", "adsbygoogle.js", "adskeeper", "lkqd", "spotxchange", "vidazoo", "minuteburst",
)
AD_TEXT_RX = re.compile(r">\s*[^<>]{0,24}\b(?:sponsored|advertisement|advertisment)\b[^<>]{0,24}<", re.I)
AD_ELEMENT_RX = re.compile(
    r"""<ins[^>]+class=["'][^"']*adsbygoogle|class=["'][^"']*\b(?:ad-slot|adslot|ad-container|ad-banner|ad-unit|advert|advertisement|ad-wrapper|ad-label|sponsored)\b"""
    r"""|id=["'](?:ad-slot|adslot|ad-container|ad-banner|ads|adbox|div-gpt-ad)[^"']*""", re.I)
SOFT_RX = {
    "adsense-account-meta": re.compile(r"google-adsense-(?:platform-)?account", re.I),
    "window.open": re.compile(r"\bwindow\.open\s*\(", re.I),
}
SCRIPT_SRC_RX = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.I)
IFRAME_SRC_RX = re.compile(r"""<iframe[^>]+src=["']([^"']+)["']""", re.I)

MAX_HTML = 1_500_000
MAX_SCRIPT = 800_000
MAX_SCRIPTS = 8


class _NoCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str, timeout: float, max_bytes: int):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"})
    opener = urllib.request.build_opener(_NoCrossHostRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(max_bytes), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, b"", url


def find_ads(text: str) -> list[str]:
    low = text.lower()
    found = [f"ad-domain:{d}" for d in AD_DOMAINS if d in low]
    if AD_ELEMENT_RX.search(text):
        found.append("ad-element")
    if AD_TEXT_RX.search(text):
        found.append("ad-label-text")
    return found


def frame_blocked(headers: dict, our_origin: str) -> str:
    xfo = headers.get("x-frame-options", "").strip().lower()
    if xfo and ("deny" in xfo or "sameorigin" in xfo or "allow-from" in xfo):
        return f"x-frame-options:{xfo}"
    csp = headers.get("content-security-policy", "")
    m = re.search(r"frame-ancestors([^;]*)", csp, re.I)
    if m:
        anc = m.group(1).strip().lower().split()
        if anc and "*" not in anc and "https:" not in anc and not (our_origin and our_origin.lower() in anc):
            return f"csp-frame-ancestors:{' '.join(anc)[:80]}"
    return ""


def scan_static(st: Settings, url: str) -> dict:
    """Blocking. Returns {verdict, reasons, warnings, status, signature}."""
    reasons: list[str] = []
    warnings: list[str] = []
    try:
        status, headers, body, final = _fetch(url, 15, MAX_HTML)
    except Exception as e:
        return {"verdict": "error", "reasons": [f"fetch-failed:{type(e).__name__}"], "warnings": [], "status": None, "signature": ""}
    if status >= 500 or status == 429:
        return {"verdict": "error", "reasons": [f"http-{status}"], "warnings": [], "status": status, "signature": ""}
    if status != 200:
        return {"verdict": "blocked", "reasons": [f"http-{status}"], "warnings": [], "status": status, "signature": ""}
    if (urlparse(final).netloc or "").lower() not in st.embed_hosts:
        return {"verdict": "blocked", "reasons": [f"redirected-off-host:{urlparse(final).netloc}"], "warnings": [], "status": status, "signature": ""}
    if "html" not in headers.get("content-type", "html").lower():
        return {"verdict": "blocked", "reasons": ["not-html"], "warnings": [], "status": status, "signature": ""}
    fb = frame_blocked(headers, st.public_origin)
    if fb:
        return {"verdict": "blocked", "reasons": [fb], "warnings": [], "status": status, "signature": ""}

    html = body.decode("utf-8", "replace")
    digest = hashlib.sha256(body)
    reasons += find_ads(html)
    for name, rx in SOFT_RX.items():
        if rx.search(html):
            warnings.append(name)
    for src in IFRAME_SRC_RX.findall(html):
        host = (urlparse(urljoin(final, src)).netloc or "").lower()
        if host and host not in st.embed_hosts:
            warnings.append(f"third-party-iframe:{host}")
    # first-party scripts only (the game code); third-party ad scripts were already caught in the HTML
    fetched = 0
    for src in SCRIPT_SRC_RX.findall(html):
        full = urljoin(final, src)
        if (urlparse(full).netloc or "").lower() not in st.embed_hosts:
            continue
        if fetched >= MAX_SCRIPTS:
            warnings.append("more-scripts-not-scanned")
            break
        fetched += 1
        try:
            s_status, _, s_body, _ = _fetch(full, 15, MAX_SCRIPT)
        except Exception:
            warnings.append(f"script-unreadable:{src[:60]}")
            continue
        if s_status == 200:
            digest.update(s_body)
            txt = s_body.decode("utf-8", "replace")
            reasons += [f"{r}@script" for r in find_ads(txt) if not r.startswith("ad-label-text") and not r.startswith("ad-element")]
            for name, rx in SOFT_RX.items():
                if rx.search(txt) and name not in warnings:
                    warnings.append(name)
    # a lone adsense meta stays a warning. Ad-network code is hard evidence (-> ads). Ad-LIKE markup or label text alone
    # (a hidden placeholder, a CSS class name) proves nothing about what a player sees -> `review`: excluded until the
    # browser check confirms nothing ad-related is actually rendered.
    reasons = sorted(set(reasons))
    hard = any(r.startswith("ad-domain:") for r in reasons)
    verdict = "ads" if hard else ("review" if reasons else "clean")
    return {"verdict": verdict, "reasons": reasons, "warnings": sorted(set(warnings)),
            "status": status, "signature": digest.hexdigest()[:24]}


def merge_runtime(res: dict, info: dict) -> dict:
    """Combine a static result with the browser check of the same game (mutates and returns `res`).
    ads/popups/dialogs seen at runtime -> ads. Browser could not run the page -> unchanged (a `review` game stays excluded).
    Browser ran clean -> a `review` game becomes clean, its static findings are kept as warnings."""
    if info["ads"] or info["popups"] or info["dialogs"]:
        res["verdict"] = "ads"
        res["reasons"] = sorted(set(res["reasons"] + info["ads"] + (["runtime-popup"] if info["popups"] else [])
                                    + (["runtime-dialog"] if info["dialogs"] else [])))
    elif info["error"]:
        res["warnings"] = sorted(set(res["warnings"] + [f"browser-check-failed:{info['error']}"]))
    elif res["verdict"] == "review":
        res["warnings"] = sorted(set(res["warnings"] + [f"static:{r}" for r in res["reasons"]] + ["ad-markup-not-rendered"]))
        res["reasons"], res["verdict"] = [], "clean"
    return res


# --------------------------------------------------------------------------------------------- runtime (browser)
# Runs in the page: counts ad-looking things that are actually RENDERED (non-zero size, not display:none / visibility:hidden /
# opacity:0, not inside a collapsed overflow:hidden parent). Ad iframes always count (an ad frame being loaded is an ad).
VISIBLE_AD_JS = """() => {
  const rx = /\\b(sponsored|advertisement|advertisment)\\b/i;
  const clipped = (el) => { for (let p = el, i = 0; p && p !== document.body && i < 8; p = p.parentElement, i++) {
      const r = p.getBoundingClientRect(), cs = getComputedStyle(p);
      if ((r.width < 2 || r.height < 2) && cs.overflow !== 'visible') return true; } return false; };
  const vis = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false;
      if (el.checkVisibility && !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false; return !clipped(el); };
  let labels = 0;
  const w = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { const n = w.currentNode; if (n.nodeValue.length < 80 && rx.test(n.nodeValue) && vis(n.parentElement)) labels++; }
  let elements = 0;
  document.querySelectorAll('ins.adsbygoogle, [id^="div-gpt-ad"], [class*="ad-slot"], [class*="ad-banner"], [class*="ad-container"], [class*="adsbygoogle"]')
    .forEach((e) => { if (vis(e)) elements++; });
  elements += document.querySelectorAll('iframe[src*="doubleclick"], iframe[src*="googlesyndication"]').length;
  return {labels, elements};
}"""


async def scan_browser(urls: dict[str, str], hold_s: float = 5.0, concurrency: int = 3) -> dict[str, dict]:
    """slug -> {ads: [...], popups: n, dialogs: n}. Needs playwright; raises ImportError if it is not installed."""
    from playwright.async_api import async_playwright   # optional dependency

    ad_hosts = tuple(d for d in AD_DOMAINS if "." in d and "/" not in d)
    out: dict[str, dict] = {}
    sem = asyncio.Semaphore(concurrency)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()

        async def one(slug: str, url: str):
            async with sem:
                ctx = await browser.new_context(viewport={"width": 390, "height": 760}, has_touch=True, is_mobile=True)
                page = await ctx.new_page()
                hits: set[str] = set()
                info = {"ads": [], "popups": 0, "dialogs": 0, "error": ""}
                page.on("request", lambda r: hits.update(f"ad-request:{h}" for h in ad_hosts if h in r.url.lower()))
                ctx.on("page", lambda p: info.__setitem__("popups", info["popups"] + 1))
                page.on("dialog", lambda d: (info.__setitem__("dialogs", info["dialogs"] + 1), asyncio.ensure_future(d.dismiss())))
                try:
                    await page.goto(url, wait_until="load", timeout=25000)
                    await page.wait_for_timeout(int(hold_s * 1000 / 2))
                    await page.touchscreen.tap(195, 380)
                    await page.wait_for_timeout(int(hold_s * 1000 / 2))
                    seen = await page.evaluate(VISIBLE_AD_JS)
                    if seen["labels"]:
                        hits.add(f"visible-ad-label:{seen['labels']}")
                    if seen["elements"]:
                        hits.add(f"ad-dom-elements:{seen['elements']}")
                except Exception as e:
                    info["error"] = f"{type(e).__name__}"
                info["ads"] = sorted(hits)
                out[slug] = info
                await ctx.close()

        await asyncio.gather(*(one(s, u) for s, u in urls.items()))
        await browser.close()
    return out


# --------------------------------------------------------------------------------------------- orchestrator
class Scanner:
    def __init__(self, st: Settings, cat: Catalogue, db: Database):
        self.st, self.cat, self.db = st, cat, db
        self.running = False
        self.total = 0
        self.done = 0
        self.browser_note = ""
        self._task: asyncio.Task | None = None

    async def load_verdicts(self) -> None:
        self.cat.set_verdicts(await self.db.checks())

    def _due(self, checks: dict[str, dict]) -> list[str]:
        now = time.time()
        due = []
        for slug in self.cat.games:
            c = checks.get(slug)
            if not c:
                due.append(slug)
            elif c["verdict"] == "error" and now - c["checked_at"] > 3600:
                due.append(slug)
            elif now - c["checked_at"] > self.st.scan_ttl:
                due.append(slug)
            elif (self.st.scan_browser and not self.browser_note.startswith("playwright not installed")
                  and not c.get("browser_checked") and c["verdict"] in ("clean", "review")):
                due.append(slug)
        return due

    def start(self, force: bool = False) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.scan_all(force))

    async def scan_all(self, force: bool = False) -> dict:
        if self.running:
            return {"started": False}
        self.running = True
        try:
            checks = await self.db.checks()
            slugs = list(self.cat.games) if force else self._due(checks)
            self.total, self.done = len(slugs), 0
            sem = asyncio.Semaphore(self.st.scan_concurrency)
            results: dict[str, dict] = {}

            async def one(slug: str):
                async with sem:
                    res = await asyncio.to_thread(scan_static, self.st, self.cat.embed_url(slug))
                    results[slug] = res
                    self.done += 1
                    if self.done % 25 == 0:
                        await self._publish(results)

            await asyncio.gather(*(one(s) for s in slugs))
            browser_done = False
            if self.st.scan_browser:
                clean = {s: self.cat.embed_url(s) for s, r in results.items() if r["verdict"] in ("clean", "review")}
                try:
                    runtime = await scan_browser(clean)
                    browser_done = True
                    for slug, info in runtime.items():
                        merge_runtime(results[slug], info)
                    self.browser_note = ""
                except ImportError:
                    self.browser_note = "playwright not installed - runtime browser check skipped"
                    log.warning(self.browser_note)
                except Exception as e:
                    self.browser_note = f"browser check failed: {type(e).__name__}: {e}"
                    log.warning(self.browser_note)
            await self._publish(results, browser_done)
            log.info("scan finished: %d games checked", len(results))
            return {"started": True, "checked": len(results)}
        finally:
            self.running = False

    async def _publish(self, results: dict[str, dict], browser: bool = False) -> None:
        for slug, r in list(results.items()):
            await self.db.save_check(slug, r["verdict"], r["reasons"], r["warnings"], r["status"], r["signature"], browser and r["verdict"] != "error")
        await self.load_verdicts()

    def status(self) -> dict:
        unchecked = sum(1 for s in self.cat.games if s not in self.cat.verdicts)
        return {"running": self.running, "pending": max(unchecked, self.total - self.done if self.running else 0),
                "browser_note": self.browser_note}
