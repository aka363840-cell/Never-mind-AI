"""Letter tiles for Word Jumble as a picture (pure Pillow, no Telegram).

A scrambled word typed as text can be copied straight into an anagram solver; as a picture it can't. Each tile is slightly
rotated and the background has faint noise, so the letters stay easy for a person and annoying for a copy-paste solver.
render_tiles() returns PNG bytes; the handler falls back to plain text if Pillow or the font is missing."""
from __future__ import annotations

import random
from io import BytesIO
from pathlib import Path

FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Poppins-Bold.ttf"
BG = (24, 26, 38)
TILE_COLORS = ((99, 102, 241), (236, 72, 153), (16, 185, 129), (245, 158, 11), (14, 165, 233), (168, 85, 247))


def render_tiles(letters: str, rng: random.Random | None = None) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    rng = rng or random.SystemRandom()
    text = letters.upper()
    n = len(text)
    size = 132 if n <= 5 else 112 if n <= 6 else 96             # keeps 7-8 letters under ~900 px wide
    gap, pad = size // 8, size // 2
    width, height = pad * 2 + n * size + (n - 1) * gap, pad * 2 + size
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    for _ in range(14):                                          # faint noise lines behind the tiles
        x1, y1, x2, y2 = (rng.randint(0, width), rng.randint(0, height), rng.randint(0, width), rng.randint(0, height))
        d.line((x1, y1, x2, y2), fill=(44, 47, 66), width=2)
    font = ImageFont.truetype(str(FONT_PATH), int(size * 0.62))
    for i, ch in enumerate(text):
        tile = Image.new("RGBA", (size + 40, size + 40), (0, 0, 0, 0))
        td = ImageDraw.Draw(tile)
        color = TILE_COLORS[i % len(TILE_COLORS)]
        td.rounded_rectangle((20, 20, 20 + size, 20 + size), radius=size // 5, fill=color)
        td.rounded_rectangle((20, 20, 20 + size, 20 + size // 2), radius=size // 5, fill=tuple(min(255, c + 28) for c in color))
        td.rounded_rectangle((20, 20, 20 + size, 20 + size), radius=size // 5, outline=(255, 255, 255, 70), width=3)
        td.text((20 + size / 2, 20 + size / 2 + size * 0.03), ch, font=font, fill=(255, 255, 255), anchor="mm")
        tile = tile.rotate(rng.uniform(-7, 7), resample=Image.BICUBIC)
        x = pad + i * (size + gap) - 20
        y = pad - 20 + rng.randint(-size // 14, size // 14)
        img.paste(tile, (x, y), tile)
    out = BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
