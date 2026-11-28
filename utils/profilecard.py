"""Shareable profile card (PNG). Pure Pillow, no bot/db imports so it is testable anywhere."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_BASE = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONTS = ("Poppins-Bold.ttf", "Inter-Bold.otf", "FreeSansBold.ttf", "DejaVuSans.ttf")
_cache: dict[int, ImageFont.ImageFont] = {}
W, H = 900, 500


def _font(size: int):
    if size in _cache:
        return _cache[size]
    for n in _FONTS:
        try:
            f = ImageFont.truetype(str(_BASE / n), size)
            break
        except Exception:
            continue
    else:
        f = ImageFont.load_default()
    _cache[size] = f
    return f


def _fit(d, text: str, size: int, maxw: int):
    text = text or ""
    while len(text) > 1 and d.textlength(text, font=_font(size)) > maxw:
        text = text[:-2] + "…" if not text.endswith("…") else text[:-2] + "…"
    return text


def _plain(s: str) -> str:
    """Strip characters the bundled fonts can't draw (emoji) so the card never shows boxes."""
    return "".join(c for c in (s or "") if ord(c) < 0x2000 or 0x2190 <= ord(c) <= 0x2BFF).strip() or "Player"


def render(*, name: str, level: int, xp: int, xp_need: int, diamonds: int, kills: int, deaths: int, wins: int,
           title: str = "", pet: str = "", clan: str = "", badges: int = 0, badges_total: int = 0,
           streak: int = 0) -> bytes:
    img = Image.new("RGB", (W, H), (24, 18, 44))
    d = ImageDraw.Draw(img)
    for y in range(H):                                   # vertical gradient
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(40 + 60 * t), int(18 + 10 * t), int(70 + 40 * (1 - t))))
    d.rounded_rectangle((24, 24, W - 24, H - 24), 28, outline=(255, 160, 200), width=3)
    d.text((54, 48), _fit(d, _plain(name), 52, 560), font=_font(52), fill=(255, 255, 255))
    sub = " · ".join(x for x in (_plain(title) if title else "", _plain(clan) if clan else "") if x and x != "Player")
    if sub:
        d.text((56, 112), _fit(d, sub, 24, 600), font=_font(24), fill=(255, 190, 220))
    # level badge
    d.ellipse((W - 214, 44, W - 54, 204), fill=(255, 120, 170), outline=(255, 255, 255), width=4)
    d.text((W - 134, 98), "LEVEL", font=_font(20), fill=(255, 255, 255), anchor="mm")
    d.text((W - 134, 144), str(level), font=_font(60), fill=(255, 255, 255), anchor="mm")
    # xp bar
    bx, by, bw = 56, 170, 560
    d.rounded_rectangle((bx, by, bx + bw, by + 26), 13, fill=(60, 45, 95))
    frac = 0 if xp_need <= 0 else max(0.0, min(1.0, xp / xp_need))
    if frac > 0:
        d.rounded_rectangle((bx, by, bx + max(26, int(bw * frac)), by + 26), 13, fill=(255, 120, 170))
    d.text((bx, by + 34), f"XP {xp:,} / {xp_need:,}", font=_font(20), fill=(220, 210, 245))
    # stat tiles
    tiles = [("Diamonds", f"{diamonds:,}"), ("Kills", f"{kills:,}"), ("Deaths", f"{deaths:,}"), ("Wins", f"{wins:,}")]
    tw, gap, ty = 190, 20, 262
    for i, (lab, val) in enumerate(tiles):
        x = 56 + i * (tw + gap)
        d.rounded_rectangle((x, ty, x + tw, ty + 104), 18, fill=(48, 36, 84))
        d.text((x + 16, ty + 12), lab, font=_font(20), fill=(200, 190, 230))
        d.text((x + 16, ty + 44), _fit(d, val, 36, tw - 28), font=_font(36), fill=(255, 255, 255))
    foot = [f"Pet: {_plain(pet)}" if pet else "Pet: none", f"Badges: {badges}/{badges_total}"]
    if streak:
        foot.append(f"Streak: {streak} days")
    d.text((56, 404), "   |   ".join(foot), font=_font(26), fill=(255, 220, 235))
    d.text((56, 446), "Ella  -  @ItzElla_AI_Bot", font=_font(20), fill=(170, 150, 210))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
