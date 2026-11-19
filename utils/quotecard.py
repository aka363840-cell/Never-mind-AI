"""Quote stickers (/q): chat bubbles drawn with Pillow, exported as a WebP sticker. Pure drawing - no Telegram calls here."""
from __future__ import annotations

import colorsys
import io
import os
import random
import re

from PIL import Image, ImageDraw, ImageFont

_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
COLORS = {"red": "#e5393f", "blue": "#2f80ed", "green": "#27ae60", "yellow": "#f2c94c", "orange": "#f2994a", "purple": "#9b51e0",
          "pink": "#ff7eb6", "black": "#17171b", "white": "#f5f5f7", "gray": "#6b7280", "grey": "#6b7280", "teal": "#14b8a6",
          "cyan": "#22d3ee", "brown": "#8d6e63", "lavender": "#cbafff", "mint": "#7ee0b0", "dark": "#1c1b29"}
DEFAULT_BG = "#1c1b29"
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")
_cache: dict = {}


def parse_color(tok: str) -> tuple[int, int, int] | None:
    t = tok.lower().strip()
    if t == "random":
        h, s, v = random.random(), random.uniform(.45, .75), random.uniform(.55, .85)
        return tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))
    hx = COLORS.get(t) or (t if re.fullmatch(r"#?[0-9a-f]{6}|#?[0-9a-f]{3}", t) else None)
    if not hx:
        return None
    hx = hx.lstrip("#")
    if len(hx) == 3:
        hx = "".join(c * 2 for c in hx)
    return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))


def _font(name: str, size: int):
    k = (name, size)
    if k not in _cache:
        try:
            _cache[k] = ImageFont.truetype(os.path.join(_BASE, name), size)
        except Exception:
            _cache[k] = ImageFont.load_default()
    return _cache[k]


def _lum(c) -> float:
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255


import unicodedata

_have: dict = {}


def _has(font, ch: str) -> bool:
    """True when this font really draws ch (a missing glyph renders the same as the 'not defined' box)."""
    k = (id(font), ch)
    if k not in _have:
        try:
            _have[k] = ch.isspace() or bytes(font.getmask(ch)) != bytes(font.getmask("\uffff"))
        except Exception:
            _have[k] = False
    return _have[k]


class Stack:
    """Inter first, then FreeSans (Hindi, Cyrillic, Greek...). Characters no font can draw are dropped instead of showing boxes."""

    def __init__(self, main: str, size: int):
        # Inter first; then fonts that carry fancy letters (𝑷𝒊𝒚𝒖𝒔𝒉 𝕬 𝓐 ♡ ✨ ★), Hindi, Cyrillic, Greek, symbols
        self.fonts = [_font(main, size)] + [_font(f, size) for f in ("FreeSans.ttf", "FreeSerif.ttf", "DejaVuMathTeXGyre.ttf", "DejaVuSans.ttf")]

    def clean(self, text: str) -> str:
        text = text.replace("\ufe0f", "").replace("\u200d", "").replace("\u20e3", "")        # invisible emoji glue only; the text stays EXACTLY as written
        return "".join(c for c in text if c == "\n" or (c.isprintable() and any(_has(f, c) for f in self.fonts)))

    def _f(self, ch: str):
        return next((f for f in self.fonts if _has(f, ch)), self.fonts[0])

    def _runs(self, text: str):
        """Consecutive characters that the same font draws, kept together so Hindi etc. joins its marks properly."""
        out: list[list] = []
        for c in text:
            f = self._f(c) if not c.isspace() or not out else out[-1][0]
            if out and out[-1][0] is f:
                out[-1][1] += c
            else:
                out.append([f, c])
        return out

    def length(self, text: str) -> float:
        return sum(f.getlength(t) for f, t in self._runs(text))

    def draw(self, d, xy, text: str, fill, anchor_baseline: bool = False) -> None:
        x, y = xy
        for f, t in self._runs(text):
            d.text((x, y), t, font=f, fill=fill)
            x += f.getlength(t)


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            while font.length(word) > width:         # a single huge word: break it
                k = max(1, int(len(word) * width / max(1, font.length(word))))
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(word[:k])
                word = word[k:]
            trial = (cur + " " + word).strip()
            if font.length(trial) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def _circle(av: bytes | None, name: str, size: int, tint) -> Image.Image:
    if av:
        try:
            im = Image.open(io.BytesIO(av)).convert("RGB").resize((size, size))
        except Exception:
            im = None
    else:
        im = None
    if im is None:
        im = Image.new("RGB", (size, size), tint)
        d = ImageDraw.Draw(im)
        ch = next((c for c in unicodedata.normalize("NFKC", name) if c.isalnum() and _has(_font("Poppins-Bold.ttf", 40), c)), "?").upper()   # (only the avatar letter is simplified)
        d.text((size / 2, size / 2), ch, font=_font("Poppins-Bold.ttf", int(size * .5)), fill=(255, 255, 255), anchor="mm")
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask.resize((size, size), Image.LANCZOS))
    return out


def render(items: list[dict], bg: tuple[int, int, int] | None = None, reply: dict | None = None, max_lines: int = 16) -> bytes:
    """items: [{name, text, avatar: bytes|None}]; reply: {name, text} shown above the first bubble. -> WebP bytes (<=512px)."""
    bg = bg or parse_color(DEFAULT_BG)
    fg = (20, 20, 24) if _lum(bg) > .6 else (245, 245, 250)
    sub = (90, 90, 100) if _lum(bg) > .6 else (190, 190, 205)
    W, PAD, AV = 880, 34, 84
    f_t, f_n, f_s = Stack("Inter-Regular.otf", 40), Stack("Inter-SemiBold.otf", 34), Stack("Inter-Regular.otf", 30)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    tw = W - AV - PAD * 3 - 12
    blocks = []
    budget = max_lines
    prev_name = None
    for it in items:
        txt = f_t.clean(it["text"]).strip() or "…"
        it = dict(it, name=f_n.clean(it["name"]).strip() or "User")
        lines = _wrap(probe, txt, f_t, tw - 2 * 26)
        if len(lines) > budget:
            lines = lines[:max(1, budget)]
            lines[-1] = lines[-1][:-1].rstrip() + "…"
        budget = max(1, budget - len(lines))
        nlines = _wrap(probe, it["name"], f_n, tw - 2 * 26)[:3]
        blocks.append((it, lines, it["name"] != prev_name, nlines))
        prev_name = it["name"]
    rep_lines = []
    if reply:
        rep_lines = _wrap(probe, f_s.clean(reply["text"]).strip() or "…", f_s, tw - 60)[:2]
    h = PAD
    layout = []
    if reply:
        rh = 34 + 38 * len(rep_lines) + 22
        layout.append(("reply", h, rh))
        h += rh + 14
    for it, lines, head, nlines in blocks:
        bh = 26 * 2 + 52 * len(lines) + (46 * len(nlines) if head else 0)
        layout.append(("msg", h, bh, it, lines, head, nlines))
        h += bh + 14
    h += PAD - 14
    bubble = tuple(min(255, int(c * 1.0)) for c in bg)
    im = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pal = [(236, 114, 113), (118, 173, 255), (126, 217, 160), (242, 190, 90), (190, 150, 255), (255, 140, 190)]
    x0 = PAD + AV + 14
    for ent in layout:
        if ent[0] == "reply":
            _, y, rh = ent
            d.rounded_rectangle((x0, y, W - PAD, y + rh), radius=26, fill=bubble + (150,))
            d.rectangle((x0 + 14, y + 16, x0 + 20, y + rh - 16), fill=sub)
            f_s.draw(d, (x0 + 38, y + 14), (f_s.clean(reply["name"]) or "User")[:40], fg)
            for i, ln in enumerate(rep_lines):
                f_s.draw(d, (x0 + 38, y + 14 + 38 * (i + 1)), ln, sub)
            continue
        _, y, bh, it, lines, head, nlines = ent
        d.rounded_rectangle((x0, y, W - PAD, y + bh), radius=34, fill=bubble + (255,))
        ty = y + 26
        if head:
            col = pal[sum(map(ord, it["name"])) % len(pal)]
            if _lum(bg) > .6:
                col = tuple(int(c * .6) for c in col)
            if abs(_lum(col) - _lum(bg)) < .28:          # name colour too close to the bubble colour: use the plain text colour
                col = fg
            for nl in nlines:
                f_n.draw(d, (x0 + 26, ty), nl, col)
                ty += 46
            im.alpha_composite(_circle(it.get("avatar"), it["name"], AV, col), (PAD, y))
        for ln in lines:
            f_t.draw(d, (x0 + 26, ty), ln, fg)
            ty += 52
    scale = min(512 / W, 512 / h)
    im = im.resize((max(1, int(W * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=92)
    return buf.getvalue()
