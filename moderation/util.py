"""Pure helpers (no aiogram / database imports): durations, HTML escaping, templates, welcome buttons."""
from __future__ import annotations

import html
import re

MAX_DURATION = 3 * 365 * 86400            # 3 years: longer than that is "permanent" in practice
_UNITS = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400, "w": 604800, "mo": 2592000, "y": 31536000}
_DUR_FULL = re.compile(r"(?:\d{1,6}(?:mo|min|sec|hr|s|m|h|d|w|y))+")
_DUR_PART = re.compile(r"(\d{1,6})(mo|min|sec|hr|s|m|h|d|w|y)")


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def parse_duration(tok: str) -> int | None:
    """'30m' / '2h' / '1d12h' / '1w' -> seconds. Anything else (plain numbers, words) -> None."""
    tok = (tok or "").strip().lower()
    if not tok or not _DUR_FULL.fullmatch(tok):
        return None
    total = sum(int(n) * _UNITS[u] for n, u in _DUR_PART.findall(tok))
    return min(total, MAX_DURATION) if total > 0 else None


def fmt_duration(sec: int | float | None) -> str:
    if sec is None:
        return "permanent"
    sec = int(sec)
    if sec <= 0:
        return "0s"
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        q, sec = divmod(sec, size)
        if q:
            parts.append(f"{q}{unit}")
    return " ".join(parts[:3])


def mention(uid: int, name: str | None) -> str:
    return f'<a href="tg://user?id={int(uid)}">{esc((name or str(uid))[:64])}</a>'


def clip(s: str | None, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


_VAR = re.compile(r"\{(\w+)\}")


def render(tpl: str, **vals: str) -> str:
    """{first} {last} {fullname} {username} {mention} {id} {chatname} {count} ... Unknown {words} stay as typed.
    Values must already be HTML-escaped by the caller. Deliberately NOT str.format (admin text may contain braces)."""
    return _VAR.sub(lambda m: str(vals[m.group(1)]) if m.group(1) in vals else m.group(0), tpl or "")


_BTN = re.compile(r"\[([^\]\n]{1,40})\]\(buttonurl://([^)\s]{3,300}?)(:same)?\)")


def extract_buttons(text: str) -> tuple[str, list[list[tuple[str, str]]]]:
    """Rose-style buttons: [Label](buttonurl://example.com) ; add ':same' to put it on the previous row."""
    rows: list[list[tuple[str, str]]] = []

    def repl(m: re.Match) -> str:
        label, url, same = m.group(1), m.group(2), bool(m.group(3))
        if not re.match(r"(?i)^(https?://|tg://)", url):
            url = "https://" + url
        if same and rows and len(rows[-1]) < 3:
            rows[-1].append((label, url))
        else:
            rows.append([(label, url)])
        return ""

    clean = _BTN.sub(repl, text or "")
    return clean.strip(), rows[:8]


def strip_command(html_text: str) -> str:
    """Remove the leading '/cmd@bot ' from a message's html_text."""
    return re.sub(r"^\s*[/!]\w+(?:@\w+)?\s*", "", html_text or "", count=1).strip()


def short_id(n: int) -> str:
    return f"{int(n):d}"
