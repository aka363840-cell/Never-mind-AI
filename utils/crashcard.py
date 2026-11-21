"""Crash Count word picture: the secret word drawn letter by letter (random font, colour, tilt, height) over a noisy background,
so it can't be copy-pasted into a script or a search box. Pure drawing with Pillow - no Telegram calls here."""
from __future__ import annotations

import io
import os
import random

from PIL import Image, ImageDraw, ImageFont

_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
_FONTS = ("Poppins-Bold.ttf", "Inter-Bold.otf", "FreeSansBold.ttf", "DejaVuSans.ttf")
PALETTE = [(255, 107, 107), (255, 217, 61), (107, 203, 119), (77, 150, 255), (199, 125, 255), (255, 159, 67), (72, 219, 251), (255, 255, 255)]
_cache: dict = {}


def _font(name: str, size: int):
    k = (name, size)
    if k not in _cache:
        try:
            _cache[k] = ImageFont.truetype(os.path.join(_BASE, name), size)
        except Exception:
            try:
                _cache[k] = ImageFont.load_default(size=size)       # Pillow >= 10.1
            except Exception:
                _cache[k] = ImageFont.load_default()
    return _cache[k]


def _dim(c, k: float = 0.42):
    return tuple(int(v * k + 20) for v in c)


def render(word: str, rng: random.Random | None = None) -> bytes:
    """PNG bytes of the word (any length; the picture grows with it)."""
    rng = rng or random.Random()
    word = "".join(ch for ch in word.upper() if ch.isalpha()) or "?"
    size = 86
    tiles = []
    for ch in word:
        f = _font(rng.choice(_FONTS), size + rng.randint(-8, 10))
        box = f.getbbox(ch)
        w, h = max(1, box[2] - box[0]), max(1, box[3] - box[1])
        pad = size // 2
        tile = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((pad - box[0], pad - box[1]), ch, font=f, fill=rng.choice(PALETTE) + (255,))
        tile = tile.rotate(rng.randint(-22, 22), expand=True, resample=Image.BICUBIC)
        tiles.append((tile, w))
    adv = [int(w * 0.95) + rng.randint(4, 14) for _, w in tiles]
    width = max(520, sum(adv) + 120)
    height = 260
    img = Image.new("RGB", (width, height), (24, 23, 38))
    d = ImageDraw.Draw(img)
    for y in range(height):                                           # soft vertical gradient
        t = y / height
        d.line([(0, y), (width, y)], fill=(int(24 + 22 * t), int(23 + 12 * t), int(38 + 40 * t)))
    for _ in range(9):                                                # dim noise lines behind the letters (they must not hide a thin I or L)
        d.line([(rng.randint(0, width), rng.randint(0, height)), (rng.randint(0, width), rng.randint(0, height))],
               fill=_dim(rng.choice(PALETTE)), width=rng.randint(1, 2))
    x = (width - sum(adv)) // 2 + 10
    for (tile, _w), a in zip(tiles, adv):
        y = (height - tile.height) // 2 + rng.randint(-18, 18)
        img.paste(tile, (x - (tile.width - _w) // 2, y), tile)
        x += a
    for _ in range(260):                                              # speckles
        d.point((rng.randint(0, width - 1), rng.randint(0, height - 1)), fill=_dim(rng.choice(PALETTE), 0.6))
    for _ in range(3):
        d.arc([rng.randint(0, width // 2), rng.randint(0, height // 2), rng.randint(width // 2, width), rng.randint(height // 2, height)],
              rng.randint(0, 180), rng.randint(181, 360), fill=_dim(rng.choice(PALETTE)), width=1)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
