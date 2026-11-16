"""Pure message-analysis helpers: links, blocklist matching, repeated characters, duplicate / flood tracking, suspicion score."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict, deque

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
_TLDS = ("com|net|org|io|me|co|in|ru|xyz|top|info|biz|tv|cc|gg|ly|to|link|click|site|online|shop|app|dev|ai|us|uk|de|fr|"
         "tk|ml|ga|cf|gq|pw|ws|vip|club|live|fun|icu|cn|jp|kr|br|ir|pk|bd|lk|np|id|ph|tr|ua|it|es|nl|pl|se|ch|at|be|cz|"
         "gl|ink|win|bid|loan|work|rest|cam|sbs|cfd|monster|buzz|cyou|today|world|space|store|tech|page|zip|mov")
URL_RE = re.compile(rf"(?i)(?:\b(?:https?://|ftp://|www\.)[^\s<>\"'()]+)|(?:\b(?:[a-z0-9][a-z0-9\-]{{0,62}}\.)+(?:{_TLDS})\b(?:/[^\s<>\"'()]*)?)")
SHORTENERS = frozenset({"bit.ly", "tinyurl.com", "goo.gl", "cutt.ly", "rebrand.ly", "is.gd", "shorturl.at", "t.ly", "ow.ly",
                        "buff.ly", "rb.gy", "tiny.cc", "v.gd", "s.id", "lnkd.in", "bl.ink", "clck.ru", "vk.cc"})
_IP = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH).lower()
    return re.sub(r"\s+", " ", t).strip()


def host_of(url: str) -> str:
    u = re.sub(r"(?i)^[a-z][a-z0-9+.\-]*://", "", url.strip())
    u = u.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    u = u.rsplit("@", 1)[-1]
    u = re.sub(r":\d+$", "", u).lower().rstrip(".")
    return u[4:] if u.startswith("www.") else u


def extract_hosts(text: str, entity_urls=()) -> list[str]:
    """Hosts of every link in the text (visible URLs + hidden text_link / url entities)."""
    seen: list[str] = []
    for u in list(entity_urls) + [m.group(0) for m in URL_RE.finditer(text or "")]:
        h = host_of(u.rstrip(".,;:!?)"))
        if h and h not in seen:
            seen.append(h)
    return seen


def domain_matches(host: str, domain: str) -> bool:
    d = domain.lower().lstrip(".")
    return host == d or host.endswith("." + d)


def link_flags(host: str, raw: str = "") -> list[str]:
    flags = []
    if host in SHORTENERS:
        flags.append("shortener")
    if _IP.match(host):
        flags.append("ip-address")
    if "xn--" in host:
        flags.append("punycode")
    if host.count(".") >= 4:
        flags.append("many-subdomains")
    r = raw.lower()
    if host in ("t.me", "telegram.me", "telegram.dog") and re.search(r"t(?:elegram)?\.(?:me|dog)/(?:\+|joinchat)", r):
        flags.append("telegram-invite")
    return flags


def has_repeated_chars(text: str, n: int) -> bool:
    if n < 3 or not text:
        return False
    return re.search(rf"([^\s])\1{{{n - 1},}}", text, re.S) is not None


def count_mentions(text: str) -> int:
    return len(re.findall(r"(?<!\w)@[A-Za-z][A-Za-z0-9_]{3,31}", text or ""))


def match_filters(filters: list[dict], text: str, hosts: list[str]) -> dict | None:
    """filters: [{'id','kind','pattern','action',...}]. kinds: word (whole word), phrase (substring), link (substring of a URL), domain."""
    norm = normalize(text)
    for f in filters:
        kind, pat = f["kind"], normalize(f["pattern"])
        if not pat:
            continue
        if kind == "word" and re.search(rf"(?<!\w){re.escape(pat)}(?!\w)", norm):
            return f
        if kind == "phrase" and pat in norm:
            return f
        if kind == "link" and (pat in norm or any(pat in h for h in hosts)):
            return f
        if kind == "domain" and any(domain_matches(h, pat) for h in hosts):
            return f
    return None


class SlidingCounter:
    """Counts events per key inside a time window. `now` is injectable so it is unit-testable."""
    def __init__(self, max_keys: int = 20000) -> None:
        self._d: dict = defaultdict(deque)
        self._max = max_keys

    def hit(self, key, now: float, window: float) -> int:
        q = self._d[key]
        q.append(now)
        while q and now - q[0] > window:
            q.popleft()
        if len(self._d) > self._max:
            self._gc(now, window)
        return len(q)

    def clear(self, key) -> None:
        self._d.pop(key, None)

    def _gc(self, now: float, window: float) -> None:
        for k in [k for k, q in self._d.items() if not q or now - q[-1] > window]:
            self._d.pop(k, None)


class DuplicateTracker:
    """Detects the same text repeated by one user, and the same text pushed by several different users (spam waves)."""
    def __init__(self, max_chats: int = 5000) -> None:
        self._own: dict = defaultdict(deque)       # (chat, uid) -> deque[(t, hash)]
        self._wave: dict = defaultdict(deque)      # chat -> deque[(t, hash, uid)]
        self._max = max_chats

    def check(self, chat: int, uid: int, text: str, now: float, window: float) -> tuple[int, int]:
        """-> (times this user sent this exact text in the window, distinct users who sent it in the window)."""
        h = hash(normalize(text))
        q = self._own[(chat, uid)]
        q.append((now, h))
        while q and now - q[0][0] > window:
            q.popleft()
        w = self._wave[chat]
        w.append((now, h, uid))
        while w and now - w[0][0] > window:
            w.popleft()
        if len(self._wave) > self._max:
            self._wave.clear(); self._own.clear()
        return sum(1 for _, x in q if x == h), len({u for _, x, u in w if x == h})


def suspicion(name: str, username: str | None, text: str, seconds_since_join: float | None, hosts: list[str],
              raw_urls: tuple[str, ...] = ()) -> tuple[int, list[str]]:
    """Heuristic 0..n score for 'suspicious activity monitoring'. Informational: it logs / flags, it never punishes by itself."""
    score, why = 0, []
    nm = normalize(f"{name} {username or ''}")
    if re.search(r"(t\.me/|https?://|www\.|\.com\b|bit\.ly)", nm):
        score += 3; why.append("link in profile name")
    if hosts and seconds_since_join is not None and seconds_since_join < 90:
        score += 3; why.append("link right after joining")
    for h, raw in zip(hosts, list(raw_urls) + [""] * len(hosts)):
        fl = link_flags(h, raw)
        if fl:
            score += 2; why.append("/".join(fl))
    if re.search(r"(?i)(earn|profit|investment|crypto|airdrop|giveaway|free\s+(?:money|usdt|btc)|dm me|click here|whatsapp)", text or ""):
        score += 1; why.append("scam wording")
    if len(re.findall(r"[\U0001F300-\U0001FAFF]", name or "")) >= 5:
        score += 1; why.append("emoji-heavy name")
    return score, why
