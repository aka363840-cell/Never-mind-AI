"""GameZipper catalogue: fetch, validate, cache, search.

Feed documented at https://gamezipper.com/publisher-partner.html:  GET /api/games.json -> JSON array of
{slug, name, emoji, category, tags[], embedUrl, thumbnail}; the publisher asks for <= 6 h client-side caching.
Nothing is hardcoded: the whole list comes from the feed. Entries that fail validation (bad slug, embed URL
outside the allowed hosts, ...) are counted and dropped, never shown."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

from . import categories
from .config import Settings

log = logging.getLogger("ella.catalogue")

SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")
MAX_FEED_BYTES = 15 * 1024 * 1024
UA = "EllaGaming/1.0 (Telegram Mini App; publisher-embed)"


class CatalogueError(Exception):
    pass


def http_get(url: str, headers: dict | None = None, timeout: float = 20, max_bytes: int = MAX_FEED_BYTES):
    """Blocking GET (run it with asyncio.to_thread). Returns (status, headers(dict, lower-case keys), body bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise CatalogueError("response too large")
            return r.status, {k.lower(): v for k, v in r.headers.items()}, body
    except urllib.error.HTTPError as e:
        body = e.read(65536) if e.fp else b""
        return e.code, {k.lower(): v for k, v in e.headers.items()}, body


class Catalogue:
    def __init__(self, st: Settings):
        self.st = st
        self.games: dict[str, dict] = {}
        self.verdicts: dict[str, dict] = {}          # slug -> {verdict, reasons, warnings, ...} (filled by the scanner)
        self.fetched_at = 0.0
        self.etag = ""
        self.raw_count = 0                           # entries in the feed, valid or not
        self.invalid: list[str] = []                 # first few reasons entries were dropped
        self.invalid_count = 0
        self.last_error = ""
        self._lock = asyncio.Lock()
        self._rev = 0                                # bumps on every change -> cache key for derived data
        self._search_cache: dict[tuple, list[str]] = {}

    # ------------------------------------------------------------------------------------------------ validation
    def _url_ok(self, url: object) -> str | None:
        if not isinstance(url, str) or len(url) > 400:
            return None
        p = urlparse(url)
        if p.scheme != "https" and not (self.st.dev and p.scheme == "http"):
            return None
        if (p.netloc or "").lower() not in self.st.embed_hosts or p.username or p.password:
            return None
        return url

    def normalise(self, raw: object) -> tuple[dict | None, str]:
        if not isinstance(raw, dict):
            return None, "not an object"
        slug = raw.get("slug")
        if not isinstance(slug, str) or not SLUG_RX.match(slug):
            return None, "bad slug"
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            return None, "no name"
        embed = self._url_ok(raw.get("embedUrl"))
        if not embed:
            return None, "embedUrl missing or not on an allowed host"
        thumb = self._url_ok(raw.get("thumbnail"))
        tags = [t.strip()[:40] for t in raw.get("tags", []) if isinstance(t, str) and t.strip()][:20] if isinstance(raw.get("tags"), list) else []
        pc = raw.get("category") if isinstance(raw.get("category"), str) else ""
        emoji = raw.get("emoji") if isinstance(raw.get("emoji"), str) else ""
        return {
            "slug": slug, "name": name.strip()[:80], "emoji": emoji[:8], "provider_category": pc.lower()[:30],
            "categories": categories.classify(pc, tags, name), "tags": tags, "embed_url": embed, "thumbnail": thumb or "",
        }, ""

    def load_list(self, data: object) -> None:
        if not isinstance(data, list):
            raise CatalogueError("feed is not a JSON array")
        games: dict[str, dict] = {}
        bad: list[str] = []
        n_bad = 0
        for i, raw in enumerate(data):
            g, why = self.normalise(raw)
            if g is None or g["slug"] in games:
                n_bad += 1
                if len(bad) < 15:
                    bad.append(f"#{i}: {why or 'duplicate slug'}")
                continue
            games[g["slug"]] = g
        if not games:
            raise CatalogueError("feed contained no usable games")
        self.games, self.raw_count, self.invalid, self.invalid_count = games, len(data), bad, n_bad
        self._bump()

    def _bump(self) -> None:
        self._rev += 1
        self._search_cache.clear()

    # ------------------------------------------------------------------------------------------------ fetching
    def load_disk_cache(self) -> bool:
        try:
            blob = json.loads(self.st.cache_path.read_text("utf-8"))
            self.load_list(blob["games"])
            self.fetched_at, self.etag = float(blob.get("fetched_at", 0)), blob.get("etag", "")
            return True
        except Exception as e:                       # missing/corrupt cache is fine
            log.info("no usable catalogue disk cache (%s)", e)
            return False

    def _save_disk_cache(self, data: list) -> None:
        try:
            tmp = self.st.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"fetched_at": self.fetched_at, "etag": self.etag, "games": data}), "utf-8")
            tmp.replace(self.st.cache_path)
        except OSError as e:
            log.warning("could not write catalogue cache: %s", e)

    @property
    def stale(self) -> bool:
        return time.time() - self.fetched_at > self.st.catalogue_ttl

    async def refresh(self, force: bool = False) -> bool:
        """Fetch the feed if the cache is stale. On failure the previous catalogue keeps serving. Single-flight."""
        async with self._lock:
            if not force and self.games and not self.stale:
                return True
            try:
                headers = {"Accept": "application/json"}
                if self.etag and self.games:
                    headers["If-None-Match"] = self.etag
                status, hdrs, body = await asyncio.to_thread(http_get, self.st.catalogue_url, headers)
                if status == 304 and self.games:
                    self.fetched_at = time.time()
                    return True
                if status != 200:
                    raise CatalogueError(f"feed returned HTTP {status}")
                data = json.loads(body.decode("utf-8"))
                self.load_list(data)
                self.fetched_at, self.etag, self.last_error = time.time(), hdrs.get("etag", ""), ""
                await asyncio.to_thread(self._save_disk_cache, data)
                log.info("catalogue refreshed: %d feed entries, %d usable, %d dropped", self.raw_count, len(self.games), self.invalid_count)
                return True
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                log.warning("catalogue refresh failed: %s", self.last_error)
                return False

    # ------------------------------------------------------------------------------------------------ eligibility
    def set_verdicts(self, rows: dict[str, dict]) -> None:
        self.verdicts = rows
        self._bump()

    def is_eligible(self, slug: str) -> bool:
        if slug not in self.games:
            return False
        v = self.verdicts.get(slug, {}).get("verdict")
        if self.st.ads_policy == "show_all":      # list every embeddable game, ads or not (games may show provider ads)
            return v != "blocked"
        if self.st.ads_policy == "trust_provider":
            return v not in ("ads", "blocked")
        return v == "clean"

    def eligible_slugs(self) -> list[str]:
        return [s for s in self.games if self.is_eligible(s)]

    def report(self) -> dict:
        by_verdict: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for s in self.games:
            v = self.verdicts.get(s)
            key = v["verdict"] if v else "unchecked"
            by_verdict[key] = by_verdict.get(key, 0) + 1
            for r in (v or {}).get("reasons", []):
                reasons[r.split(":")[0]] = reasons.get(r.split(":")[0], 0) + 1
        per_cat = {c: 0 for c in categories.IDS}
        for s in self.eligible_slugs():
            for c in self.games[s]["categories"]:
                per_cat[c] += 1
        return {
            "feed_entries_fetched": self.raw_count, "usable_entries": len(self.games), "dropped_invalid": self.invalid_count,
            "dropped_examples": self.invalid, "verdicts": by_verdict, "exclusion_reasons": reasons,
            "eligible_shown": len(self.eligible_slugs()), "ads_policy": self.st.ads_policy,
            "eligible_per_category": per_cat, "fetched_at": int(self.fetched_at), "last_error": self.last_error,
        }

    # ------------------------------------------------------------------------------------------------ public views
    def embed_url(self, slug: str) -> str:
        g = self.games[slug]
        sep = "&" if "?" in g["embed_url"] else "?"
        return g["embed_url"] + sep + urlencode({"utm_source": self.st.utm_source, "utm_medium": "embed", "utm_campaign": "ella-gaming"})

    def card(self, slug: str) -> dict:
        g = self.games[slug]
        return {"slug": slug, "name": g["name"], "emoji": g["emoji"], "categories": g["categories"], "thumbnail": g["thumbnail"]}

    def detail(self, slug: str) -> dict:
        g = self.games[slug]
        return {**self.card(slug), "tags": g["tags"][:8]}

    def counts(self) -> dict[str, int]:
        out = {c: 0 for c in categories.IDS}
        for s in self.eligible_slugs():
            for c in self.games[s]["categories"]:
                out[c] += 1
        return out

    def search(self, q: str = "", category: str = "", sort: str = "az", popularity: dict[str, int] | None = None) -> list[str]:
        key = (self._rev, q, category, sort, bool(popularity))
        if sort != "popular" and key in self._search_cache:
            return self._search_cache[key]
        tokens = [t for t in re.split(r"[^a-z0-9]+", q.lower()) if t][:6]
        scored: list[tuple[float, str, str]] = []
        for s in self.eligible_slugs():
            g = self.games[s]
            if category and category not in g["categories"]:
                continue
            score = 0.0
            if tokens:
                name = g["name"].lower()
                words = re.split(r"[^a-z0-9]+", name)
                tagtxt = " ".join(g["tags"]).lower()
                for t in tokens:
                    if name.startswith(t):
                        score += 3
                    elif any(w.startswith(t) for w in words):
                        score += 2
                    elif t in name:
                        score += 1
                    elif t in tagtxt:
                        score += 0.5
                    else:
                        score = -1
                        break
                if score < 0:
                    continue
            scored.append((score, g["name"].lower(), s))
        if tokens:
            scored.sort(key=lambda x: (-x[0], x[1]))
        elif sort == "popular" and popularity is not None:
            scored.sort(key=lambda x: (-popularity.get(x[2], 0), x[1]))
        else:
            scored.sort(key=lambda x: x[1])
        res = [s for _, _, s in scored]
        if sort != "popular":
            self._search_cache[key] = res
        return res

    def featured(self, n: int = 8, day: int | None = None) -> list[str]:
        """Editorial rotation: a stable daily shuffle of games that have a thumbnail."""
        import random
        pool = [s for s in self.eligible_slugs() if self.games[s]["thumbnail"]]
        pool.sort()
        random.Random(day if day is not None else int(time.time() // 86400)).shuffle(pool)
        return pool[:n]
