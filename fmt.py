from __future__ import annotations

import html


def esc(t: object) -> str:
    return html.escape(str(t), quote=False)


def num(n: int) -> str:
    return f"{int(n):,}"


def dur(seconds: int) -> str:
    seconds = max(0, int(seconds))
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m and not d: parts.append(f"{m}m")
    if s and not (d or h): parts.append(f"{s}s")
    return " ".join(parts) or "0s"


def mention(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{esc(name)}</a>'


def parse_amount(raw: str, maximum: int | None = None) -> int | None:
    """'500', '1,000', '2k', '1.5m', 'all' -> int. None if invalid."""
    raw = raw.strip().lower().replace(",", "").replace("_", "")
    if raw in ("all", "max") and maximum is not None:
        return maximum
    mult = 1
    if raw.endswith("k"): mult, raw = 1_000, raw[:-1]
    elif raw.endswith("m"): mult, raw = 1_000_000, raw[:-1]
    try:
        val = float(raw) * mult
    except ValueError:
        return None
    if val != val or val <= 0 or val > 10**12 or int(val) != val:
        return None
    return int(val)


def bar(cur: float, mx: float, n: int = 10) -> str:
    """▰▰▰▱▱ style progress bar."""
    mx = max(1, mx)
    full = max(0, min(n, round(n * max(0, cur) / mx)))
    if cur > 0 and full == 0:
        full = 1
    return "▰" * full + "▱" * (n - full)
