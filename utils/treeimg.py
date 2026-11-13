"""Draws the /familytree picture (PNG) with Pillow. Pure functions - no Telegram / database imports.

Layout = generations as horizontal lanes, the player in the middle lane:

    grandparents
    uncles/aunts · parents · parents-in-law
    cousins · siblings · YOU ♥ partner · siblings-in-law
    nephews/nieces · children · children-in-law
    grandchildren

Relations are only stored between the viewer and each relative, so connectors show the *structure*
(parents -> you and your siblings, you + partner -> your children ...). Solid line = direct link,
dashed = link to the nearest known relative when the usual one is missing.
"""
from __future__ import annotations

import io
import math
import os
import re
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
_FALLBACKS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
              "/Library/Fonts/Arial Unicode.ttf", "C:\\Windows\\Fonts\\arial.ttf"]
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE00-\uFE0F\u200D\u20E3\U000E0000-\U000E007F]")

R = max(1, min(4, int(os.getenv("TREE_SUPERSAMPLE", "3"))))   # supersampling factor (layout units -> pixels); 3 = original full quality (default); 2 = lighter on RAM
OUT_SCALE = 2               # final pixels per layout unit
CW, CH = 156, 92            # card size
AV = 25                     # avatar radius
GX, CG, COUPLE_GAP = 20, 52, 64   # card gap, cluster gap, gap between the two partners
LANE_GAP = 112
MARGIN, HEADER, FOOTER = 56, 118, 70

C = dict(bg1=(252, 247, 239), bg2=(243, 235, 224), ink=(43, 42, 51), muted=(138, 135, 148), line=(183, 175, 162),
         card=(255, 255, 255), dot=(226, 218, 206))
KIND = {"you": (79, 70, 229), "partner": (224, 69, 123), "blood": (15, 157, 138), "inlaw": (224, 138, 30)}
LANE_OF = {"grandparents": 0, "piblings": 1, "parents": 1, "pil": 1, "cousins": 2, "siblings": 2, "couple": 2, "sil": 2,
           "niblings": 3, "children": 3, "cil": 3, "grandchildren": 4}
ORDER = [["grandparents"], ["piblings", "parents", "pil"], ["cousins", "siblings", "couple", "sil"],
         ["niblings", "children", "cil"], ["grandchildren"]]


@dataclass
class Person:
    name: str
    role: str
    kind: str = "blood"          # you | partner | blood | inlaw
    photo: bytes | None = None   # profile picture (any image format); the initial letter is drawn when missing / unreadable


@dataclass
class Card:
    p: Person
    x: float = 0
    y: float = 0

    @property
    def cx(self): return self.x + CW / 2
    @property
    def bottom(self): return self.y + CH
    @property
    def right(self): return self.x + CW


_fonts: dict = {}


def _font(weight: str, size: float, path: str | None = None):
    key = (weight, size, path)
    if key not in _fonts:
        f = path or os.path.join(_FONT_DIR, {"r": "Inter-Regular.otf", "s": "Inter-SemiBold.otf", "b": "Inter-Bold.otf"}[weight])
        try:
            _fonts[key] = ImageFont.truetype(f, int(size * R))
        except OSError:
            _fonts[key] = ImageFont.load_default(int(size * R))
    return _fonts[key]


def _inter_ok(ch: str) -> bool:
    o = ord(ch)
    return o < 0x590 or 0x1E00 <= o <= 0x1EFF or 0x2000 <= o <= 0x206F


def _glyph(font, ch: str) -> bytes:
    im = Image.new("L", (font.size * 2, font.size * 2), 0)
    ImageDraw.Draw(im).text((4, 4), ch, font=font, fill=255)
    return im.tobytes()


def _covers(font, text: str) -> bool:
    if "Inter-" in str(getattr(font, "path", "")):          # bundled font: known Latin/Greek/Cyrillic coverage
        return all(_inter_ok(c) for c in text)
    try:
        missing = {_glyph(font, "\u0378"), _glyph(font, "\U0010fffd")}      # unassigned code points draw the .notdef box
        return all(c.isspace() or _glyph(font, c) not in missing for c in text)
    except Exception:
        return False


def _round_photo(data: bytes | None, radius: float) -> Image.Image | None:
    """Square-crop a profile picture and cut it to a circle of `radius` layout units (supersampled). None if unreadable."""
    if not data:
        return None
    try:
        d = int(radius * 2 * R)
        im = Image.open(io.BytesIO(data))
        im.load()
        im = ImageOps.exif_transpose(im).convert("RGBA")
        im = ImageOps.fit(im, (d, d), Image.LANCZOS, centering=(0.5, 0.4))     # 0.4: keep faces a little higher in the circle
        mask = Image.new("L", (d * 2, d * 2), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, d * 2 - 1, d * 2 - 1], fill=255)
        im.putalpha(mask.resize((d, d), Image.LANCZOS))
        return im
    except Exception:
        return None


def clean_name(name: str, limit: int = 40) -> str:
    n = _EMOJI.sub("", name or "").strip()
    return (n or "Player")[:limit]


def _pick(weight: str, size: float, text: str):
    f = _font(weight, size)
    if _covers(f, text):
        return f
    bundled = [os.path.join(_FONT_DIR, {"r": "Poppins-Regular.ttf", "s": "Poppins-Medium.ttf", "b": "Poppins-Bold.ttf"}[weight]),
               os.path.join(_FONT_DIR, "FreeSansBold.ttf"), os.path.join(_FONT_DIR, "FreeSans.ttf")]
    for p in bundled + _FALLBACKS:
        if os.path.exists(p):
            g = _font(weight, size, p)
            if _covers(g, text):
                return g
    return f


def _fit(text: str, weight: str, size: float, width: float):
    f = _pick(weight, size, text)
    if not _covers(f, text):
        text = " ".join("".join(c for c in text if _covers(f, c)).split()) or "Player"
    if f.getlength(text) <= width * R:
        return text, f
    while len(text) > 1 and f.getlength(text + "…") > width * R:
        text = text[:-1]
    return text.rstrip() + "…", f


def _mix(c, t, a):                      # blend colour c towards t by a
    return tuple(int(c[i] + (t[i] - c[i]) * a) for i in range(3))


class _Canvas:
    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        top, bot = C["bg1"], C["bg2"]
        g = Image.new("RGB", (1, h * R))
        for y in range(h * R):
            g.putpixel((0, y), _mix(top, bot, y / (h * R)))
        self.im = g.resize((w * R, h * R)).convert("RGBA")
        d = ImageDraw.Draw(self.im)
        for x in range(24, w, 28):                       # faint dot grid
            for y in range(24, h, 28):
                d.ellipse([x * R - 1.5, y * R - 1.5, x * R + 1.5, y * R + 1.5], fill=C["dot"])
        self.d = ImageDraw.Draw(self.im)

    # primitives in layout units
    def line(self, pts, color, width=2.2, dash=False):
        pts = [(x * R, y * R) for x, y in pts]
        if not dash:
            self.d.line(pts, fill=color, width=int(width * R), joint="curve")
            for x, y in (pts[0], pts[-1]):
                self.d.ellipse([x - width * R / 2, y - width * R / 2, x + width * R / 2, y + width * R / 2], fill=color)
            return
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            L = math.hypot(x1 - x0, y1 - y0)
            n = int(L // (9 * R))
            for i in range(n + 1):
                a, b = i * 9 * R / L, min(i * 9 * R + 5 * R, L) / L
                if a >= 1:
                    break
                self.d.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=color, width=int(width * R))

    def rrect(self, box, r, fill, outline=None, width=1.5):
        self.d.rounded_rectangle([v * R for v in box], r * R, fill=fill, outline=outline, width=int(width * R))

    def shadow(self, box, r, blur=10, dy=5, alpha=46):
        pad = blur * 3
        x0, y0, x1, y1 = box
        w, h = int((x1 - x0 + 2 * pad) * R), int((y1 - y0 + 2 * pad) * R)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle([pad * R, pad * R, w - pad * R, h - pad * R], r * R, fill=(70, 55, 40, alpha))
        layer = layer.filter(ImageFilter.GaussianBlur(blur * R / 2))
        self.im.alpha_composite(layer, (int((x0 - pad) * R), int((y0 - pad + dy) * R)))
        self.d = ImageDraw.Draw(self.im)

    def text(self, xy, s, font, fill, anchor="la"):
        self.d.text((xy[0] * R, xy[1] * R), s, font=font, fill=fill, anchor=anchor)

    def heart(self, cx, cy, size, fill):
        pts = []
        for i in range(60):
            t = i / 60 * 2 * math.pi
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            pts.append(((cx + x * size / 34) * R, (cy - y * size / 34) * R))
        self.d.polygon(pts, fill=fill)

    def card(self, c: Card):
        col = KIND[c.p.kind]
        you = c.p.kind == "you"
        box = (c.x, c.y, c.x + CW, c.y + CH)
        self.shadow(box, 18)
        self.rrect(box, 18, fill=col if you else C["card"], outline=None if you else _mix(col, (255, 255, 255), .55), width=1.6)
        cx = c.cx
        # avatar straddles the top edge
        self.d.ellipse([(cx - AV - 4) * R, (c.y - AV - 4) * R, (cx + AV + 4) * R, (c.y + AV + 4) * R], fill=(255, 255, 255) if not you else _mix(col, (255, 255, 255), .0))
        self.d.ellipse([(cx - AV) * R, (c.y - AV) * R, (cx + AV) * R, (c.y + AV) * R], fill=_mix(col, (255, 255, 255), .0) if you else _mix(col, (255, 255, 255), .84),
                       outline=(255, 255, 255) if you else col, width=int(2 * R))
        name = clean_name(c.p.name)
        t, f = _fit(name, "s", 16, CW - 20)
        pic = _round_photo(c.p.photo, AV)
        if pic is not None:                              # profile picture inside the coloured ring
            self.im.alpha_composite(pic, (int((cx - AV) * R), int((c.y - AV) * R)))
            self.d = ImageDraw.Draw(self.im)
            self.d.ellipse([(cx - AV) * R, (c.y - AV) * R, (cx + AV) * R, (c.y + AV) * R],
                           outline=(255, 255, 255) if you else col, width=int(2 * R))
        else:                                            # no picture: initial letter
            ini = next((ch for ch in t if ch.isalnum()), "?").upper()
            _, fi = _fit(ini, "b", 22, 40)
            self.text((cx, c.y), ini, fi, (255, 255, 255) if you else col, "mm")
        self.text((cx, c.y + 44), t, f, (255, 255, 255) if you else C["ink"], "mm")
        role = c.p.role.upper()
        f = _font("s", 10.5)
        role = " ".join(role) if False else role
        w = f.getlength(role) / R + 20
        w = min(w, CW - 14)
        px0, py0 = cx - w / 2, c.y + 62
        self.rrect((px0, py0, px0 + w, py0 + 20), 10, fill=_mix(col, (255, 255, 255), .22) if you else _mix(col, (255, 255, 255), .88))
        t, f = _fit(role, "s", 10.5, w - 12)
        self.text((cx, py0 + 10.3), t, f, (255, 255, 255) if you else col, "mm")


def _lane_layout(clusters: dict[str, list[Card]], keys: list[str]):
    """-> total width, list of (card, dx) positions relative to lane left edge"""
    x, out, first = 0.0, [], True
    for k in keys:
        cards = clusters.get(k)
        if not cards:
            continue
        if not first:
            x += CG
        first = False
        for i, c in enumerate(cards):
            if i:
                x += COUPLE_GAP if k == "couple" else GX
            out.append((c, x))
            x += CW
    return x, out


def render(owner: str, clusters: dict[str, list[Person]], partner_love: str = "", brand: str = "") -> bytes:
    """clusters: key from ORDER -> people ('couple' holds you [+ partner], in that order). Returns PNG bytes."""
    cc: dict[str, list[Card]] = {k: [Card(p) for p in v] for k, v in clusters.items() if v}
    lanes = [i for i, ks in enumerate(ORDER) if any(cc.get(k) for k in ks)]
    widths, laid = {}, {}
    for i in lanes:
        widths[i], laid[i] = _lane_layout(cc, ORDER[i])
    inner = max(widths.values())
    W = int(max(inner + 2 * MARGIN, 700))
    top = HEADER + 40
    lane_y = {}
    for n, i in enumerate(lanes):
        lane_y[i] = top + n * (CH + LANE_GAP)
    H = int(lane_y[lanes[-1]] + CH + FOOTER + 34)
    # centre the lane that holds the player on the canvas; align the other lanes on it
    for i in lanes:
        left = (W - widths[i]) / 2
        for c, dx in laid[i]:
            c.x, c.y = left + dx, lane_y[i]

    cv = _Canvas(W, H)
    # ----- header
    f = _font("b", 30)
    title = f"{clean_name(owner, 24)}'s Family Tree"
    t, f = _fit(title, "b", 30, W - 120)
    cv.text((W / 2, 52), t, f, C["ink"], "mm")
    n_rel = sum(len(v) for k, v in cc.items() if k != "couple") + (len(cc.get("couple", [])) - 1)
    sub = f"{n_rel} family member{'s' if n_rel != 1 else ''} · {len(lanes)} generation{'s' if len(lanes) != 1 else ''}"
    cv.text((W / 2, 86), sub, _font("r", 14), C["muted"], "mm")
    cv.line([(W / 2 - 22, 108), (W / 2 + 22, 108)], KIND["you"], 3)

    # ----- connectors (drawn before cards so avatars sit on top)
    slots: dict[int, int] = {}

    def gap_y(lane: int) -> float:
        k = slots.get(lane, 0)
        slots[lane] = k + 1
        return lane_y[lane] + CH + 40 + (k % 3) * 17

    def span(cards): return min(c.cx for c in cards), max(c.cx for c in cards)

    def anchor(cards: list[Card], kind: str, draw_bracket=True):
        """point where children hang from"""
        col = _mix(KIND[kind], C["line"], .55)
        if len(cards) == 1:
            return cards[0].cx, cards[0].bottom, col
        if cards[0].p.kind == "you" and cards[1].p.kind == "partner":
            mx = (cards[0].right + cards[1].x) / 2
            return mx, cards[0].y + CH / 2 + 14, KIND["partner"]
        y = max(c.bottom for c in cards) + 16
        a, b = span(cards)
        for c in cards:
            cv.line([(c.cx, c.bottom), (c.cx, y)], col, 2.2)
        cv.line([(a, y), (b, y)], col, 2.2)
        return (a + b) / 2, y, col

    def bus(src: list[Card], dst: list[Card], kind: str, dashed=False):
        if not src or not dst:
            return
        sl, dl = next(i for i in lanes if cc_lane(src[0]) == i), next(i for i in lanes if cc_lane(dst[0]) == i)
        ax, ay, col = anchor(src, kind)
        if dashed:
            col = _mix(KIND["inlaw"], C["line"], .35) if kind == "inlaw" else C["line"]
        my = gap_y(sl)
        top_y = dst[0].y
        xs = [ax] + [c.cx for c in dst]
        cv.line([(ax, ay), (ax, my)], col, 2.2, dashed)
        cv.line([(min(xs), my), (max(xs), my)], col, 2.2, dashed)
        for c in dst:
            cv.line([(c.cx, my), (c.cx, top_y)], col, 2.2, dashed)

    def cc_lane(card: Card) -> int:
        return next(i for i in lanes if abs(lane_y[i] - card.y) < 1)

    def bar(cards: list[Card], kind: str, dashed=True):
        """sibling-style bar above a row of cards that share no known parent"""
        if len(cards) < 2:
            return
        y = cards[0].y - 40
        col = C["line"]
        a, b = span(cards)
        cv.line([(a, y), (b, y)], col, 2.2, dashed)
        for c in cards:
            cv.line([(c.cx, y), (c.cx, c.y)], col, 2.2, dashed)

    def chain(a: Card, b: Card, kind: str):
        y = a.y + CH / 2
        cv.line([(a.right, y), (b.x, y)], _mix(KIND[kind], C["line"], .3), 2.2, True)

    g = lambda k: cc.get(k, [])
    couple = g("couple")
    you = couple[0]
    kids_src = couple if len(couple) == 2 else [you]
    present = lambda *ks: [k for k in ks if g(k)]

    # parents / grandparents / uncles & aunts
    top_siblings = g("siblings") + [you]
    if g("parents"):
        bus(g("parents"), top_siblings, "blood")
        if g("grandparents"):
            bus(g("grandparents"), g("parents") + g("piblings"), "blood")
        elif g("piblings"):
            bar(g("piblings") + g("parents"), "blood")
    else:
        if g("grandparents") and g("piblings"):
            bus(g("grandparents"), g("piblings") + [you], "blood", dashed=True)
        elif g("grandparents"):
            bus(g("grandparents"), g("cousins") + top_siblings, "blood", dashed=True)
        elif len(top_siblings) > 1:
            bar(top_siblings, "blood")
        if g("piblings") and not g("grandparents"):
            bus(g("piblings"), [you], "blood", dashed=True)
    # cousins
    if g("cousins"):
        if g("piblings"):
            bus(g("piblings"), g("cousins"), "blood")
        elif not g("grandparents") or g("parents"):
            nxt = (g("siblings") or couple)[0]
            chain(g("cousins")[-1], nxt, "blood")
    # in-laws
    partner = couple[1] if len(couple) == 2 else you
    if g("pil"):
        bus(g("pil"), [partner] + g("sil"), "inlaw")
    elif g("sil"):
        chain(partner, g("sil")[0], "inlaw")
    if g("sil") and not g("pil"):
        for a, b in zip(g("sil"), g("sil")[1:]):
            chain(a, b, "inlaw")
    # next generation
    if g("children"):
        bus(kids_src, g("children"), "blood")
    if g("niblings"):
        if g("siblings"):
            bus(g("siblings"), g("niblings"), "blood")
        else:
            bus([you], g("niblings"), "blood", dashed=True)
    if g("cil"):
        if g("children"):
            chain(g("children")[-1], g("cil")[0], "inlaw")
        else:
            bus(kids_src, g("cil"), "inlaw", dashed=True)
        for a, b in zip(g("cil"), g("cil")[1:]):
            chain(a, b, "inlaw")
    if g("grandchildren"):
        src = g("children") + g("cil") if g("children") else kids_src
        if g("children"):
            bus(g("children"), g("grandchildren"), "blood")
        else:
            bus(kids_src, g("grandchildren"), "blood", dashed=True)
    # couple link (drawn last so it sits on top of any bus)
    if len(couple) == 2:
        y = you.y + CH / 2
        cv.line([(you.right, y), (couple[1].x, y)], KIND["partner"], 3)
        mx = (you.right + couple[1].x) / 2
        cv.d.ellipse([(mx - 15) * R, (y - 15) * R, (mx + 15) * R, (y + 15) * R], fill=(255, 255, 255), outline=KIND["partner"], width=int(2 * R))
        cv.heart(mx, y + .5, 17, KIND["partner"])
        if partner_love:
            f = _font("s", 10.5)
            tw = f.getlength(partner_love) / R + 16
            cv.rrect((mx - tw / 2, y + 22, mx + tw / 2, y + 40), 9, fill=_mix(KIND["partner"], (255, 255, 255), .88))
            cv.text((mx, y + 31.3), partner_love, f, KIND["partner"], "mm")

    for k in ORDER[0] + ORDER[1] + ORDER[2] + ORDER[3] + ORDER[4]:
        for c in g(k):
            cv.card(c)

    # ----- footer legend
    items = [("You", "you"), ("Partner", "partner"), ("Family", "blood"), ("In-laws", "inlaw")]
    fnt = _font("s", 12)
    total = sum(fnt.getlength(n) / R + 34 for n, _ in items)
    x, y = W / 2 - total / 2 - 40, H - 44
    for n, k in items:
        cv.d.ellipse([x * R, (y - 5) * R, (x + 10) * R, (y + 5) * R], fill=KIND[k])
        cv.text((x + 16, y), n, fnt, C["muted"], "lm")
        x += fnt.getlength(n) / R + 34
    cv.line([(x + 4, y), (x + 30, y)], C["line"], 2, True)
    cv.text((x + 38, y), "indirect", fnt, C["muted"], "lm")
    if brand:
        cv.text((W / 2, H - 18), clean_name(brand, 30), _font("r", 11), C["line"], "mm")

    scale = OUT_SCALE
    while (W * scale) + (H * scale) > 9000 and scale > 1:
        scale -= 1
    out = cv.im.convert("RGB").resize((W * scale, H * scale), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()
