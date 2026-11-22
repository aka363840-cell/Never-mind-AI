"""The optional headless-browser check must catch ads that only appear at runtime (invisible to the static scan),
and must not flag clean games."""
import asyncio
import time

from ella_server import scanner as sc


def test_static_scan_misses_runtime_ads_but_browser_check_catches_them(settings, upstream):
    import urllib.request
    urllib.request.urlopen(f"{upstream.url}/api/games.json").read()         # the fake registers its game kinds when the feed is served
    base = f"{upstream.url}"
    clean = sc.scan_static(settings, f"{base}/demo-001/")
    runtime = sc.scan_static(settings, f"{base}/runtime-ad/")
    assert clean["verdict"] == "clean" and runtime["verdict"] == "clean"        # documented limitation of static scanning
    res = asyncio.run(sc.scan_browser({"demo-001": f"{base}/demo-001/", "runtime-ad": f"{base}/runtime-ad/"}, hold_s=3))
    assert res["demo-001"]["ads"] == [] and res["demo-001"]["popups"] == 0 and not res["demo-001"]["error"]
    assert any("doubleclick" in a or "ad-dom" in a for a in res["runtime-ad"]["ads"]), res["runtime-ad"]


def test_full_scan_with_browser_flags_runtime_ad(settings, tmp_path):
    from dataclasses import replace
    from ella_server.catalogue import Catalogue
    from ella_server.db import Database
    st = replace(settings, scan_browser=True)
    async def go():
        db, cat = Database(tmp_path / "x.sqlite3"), Catalogue(st)
        assert await cat.refresh(force=True)
        s = sc.Scanner(st, cat, db)
        await s.scan_all()
        return cat, s
    cat, s = asyncio.run(go())
    assert not s.browser_note, s.browser_note
    assert cat.verdicts["runtime-ad"]["verdict"] == "ads" and not cat.is_eligible("runtime-ad")
    assert cat.verdicts["demo-001"]["verdict"] == "clean" and cat.is_eligible("demo-001")
    assert cat.report()["eligible_shown"] == 53                                 # 54 minus the runtime-ad game


# ---------------------------------------------------------------- review verdict: ad-LIKE markup is decided by what is rendered
def _prime(upstream):
    import urllib.request
    urllib.request.urlopen(f"{upstream.url}/api/games.json").read()


def test_label_only_pages_are_review_not_ads_statically(settings, upstream):
    _prime(upstream)
    for slug in ("label-visible", "label-hidden", "label-collapsed"):
        r = sc.scan_static(settings, f"{upstream.url}/{slug}/")
        assert r["verdict"] == "review" and "ad-label-text" in r["reasons"], (slug, r)
    assert sc.scan_static(settings, f"{upstream.url}/ad-static/")["verdict"] == "ads"      # real ad-network code is still a hard fail


def test_browser_check_decides_review_games_by_what_is_rendered(settings, upstream):
    _prime(upstream)
    urls = {s: f"{upstream.url}/{s}/" for s in ("label-visible", "label-hidden", "label-collapsed")}
    runtime = asyncio.run(sc.scan_browser(urls, hold_s=2))
    verdicts = {}
    for slug, info in runtime.items():
        verdicts[slug] = sc.merge_runtime(sc.scan_static(settings, urls[slug]), info)
    assert verdicts["label-visible"]["verdict"] == "ads" and any(r.startswith("visible-ad-label") for r in verdicts["label-visible"]["reasons"])
    assert verdicts["label-hidden"]["verdict"] == "clean" and "ad-markup-not-rendered" in verdicts["label-hidden"]["warnings"]
    assert verdicts["label-collapsed"]["verdict"] == "clean"


def test_merge_runtime_keeps_review_excluded_when_the_browser_could_not_check():
    base = {"verdict": "review", "reasons": ["ad-label-text"], "warnings": [], "status": 200, "signature": "x"}
    out = sc.merge_runtime(dict(base), {"ads": [], "popups": 0, "dialogs": 0, "error": "TimeoutError"})
    assert out["verdict"] == "review"                          # not eligible: unverified is never shown
    assert sc.merge_runtime(dict(base), {"ads": [], "popups": 1, "dialogs": 0, "error": ""})["verdict"] == "ads"
