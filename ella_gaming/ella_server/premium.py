"""Premium games for Ella Gaming: UNO + LUDO, live multiplayer tables (in-memory, long-poll, no extra deps).

Tables can be created in the Mini App (invite by link) or from a group with /uno /ludo (the server posts a live lobby message
in the group: who is waiting, who is playing, whose turn). Nothing here touches the free-games catalogue."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import random
import secrets
import time
import urllib.parse
import urllib.request

from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger("ella.premium")
GAMES = {"uno": (2, 6, "UNO", "🃏"), "ludo": (2, 4, "LUDO", "🎲"), "chess": (2, 2, "CHESS", "♟️")}
ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TURN_S = 60
ROOMS: dict[str, "Room"] = {}
BOT_NAME: list[str] = []


class Err(Exception):
    pass


class Room:
    def __init__(self, game, host):
        self.code = "".join(secrets.choice(ALPHA) for _ in range(5))
        self.game, self.players, self.status, self.g, self.winner = game, [host], "lobby", None, None
        self.v, self.ev, self.t = 1, asyncio.Event(), time.time()
        self.opts = {"stack": True, "block": True}
        self.chat_id = self.msg_id = self.title = self.link = None
        self.sync = None

    @property
    def ids(self):
        return [p["id"] for p in self.players]

    def name(self, uid):
        return next((p["name"] for p in self.players if p["id"] == uid), "Player")


# ------------------------------------------------------------------------------------------------ UNO
def uno_start(r):
    deck = [c + v for c in "rygb" for v in ["0"] + [x for x in "123456789sdr" for _ in (0, 1)]] + ["w"] * 4 + ["w4"] * 4
    random.shuffle(deck)
    g = {"hands": {i: [deck.pop() for _ in range(7)] for i in r.ids}, "deck": deck, "dir": 1, "turn": 0, "drawn": None, "pend": 0, "pk": ""}
    while deck[-1][0] == "w":
        random.shuffle(deck)
    top = deck.pop()
    g.update(top=top, color=top[0], tv=top[1:], pile=[top])
    r.g = g
    n = len(r.ids)
    if top[1:] == "s":
        g["turn"] = 1 % n
    elif top[1:] == "r":
        g["dir"] = -1
        g["turn"] = (n - 1) if n > 2 else 1 % n
    elif top[1:] == "d":
        uno_draw(g, r.ids[0], 2)
        g["turn"] = 1 % n


def uno_draw(g, pid, k):
    for _ in range(k):
        if not g["deck"] and len(g["pile"]) > 1:
            top = g["pile"].pop()
            g["deck"], g["pile"] = g["pile"], [top]
            random.shuffle(g["deck"])
        if g["deck"]:
            g["hands"][pid].append(g["deck"].pop())


def uno_can(g, c):
    if g.get("pend"):                                   # stacking: answer a draw card with another one, or take the cards
        return c == "w4" or (c[1:] == "d" and g["pk"] == "d")
    return c[0] == "w" or c[0] == g["color"] or (g["tv"] != "" and c[1:] == g["tv"])


def uno_adv(r, k=1):
    g = r.g
    g["turn"] = (g["turn"] + g["dir"] * k) % len(r.ids)


def uno_act(r, uid, a, d):
    g, ids = r.g, r.ids
    if ids[g["turn"]] != uid:
        raise Err("It's not your turn.")
    hand, n = g["hands"][uid], len(ids)
    if a == "draw":
        if g["drawn"]:
            raise Err("You already drew - play that card or pass.")
        if g["pend"]:
            uno_draw(g, uid, g["pend"])
            g["pend"], g["pk"] = 0, ""
            return uno_adv(r)
        uno_draw(g, uid, 1)
        c = hand[-1] if hand else None
        if c and uno_can(g, c):
            g["drawn"] = c
        else:
            uno_adv(r)
    elif a == "pass":
        if not g["drawn"]:
            raise Err("Draw a card first.")
        g["drawn"] = None
        uno_adv(r)
    elif a == "play":
        c, col = str(d.get("card", "")), str(d.get("color", ""))
        if c not in hand:
            raise Err("That card isn't in your hand.")
        if g["drawn"] and g["drawn"] != c:
            raise Err("Play the card you drew, or pass.")
        if not uno_can(g, c):
            raise Err("That card can't be played now.")
        if c[0] == "w" and col not in list("rygb"):
            raise Err("Pick a colour.")
        hand.remove(c)
        g["pile"].append(c)
        g.update(top=c, tv="" if c[0] == "w" else c[1:], color=col if c[0] == "w" else c[0], drawn=None)
        if not hand:
            return finish(r, uid)
        nxt = ids[(g["turn"] + g["dir"]) % n]
        if c[1:] == "r":
            g["dir"] *= -1
            uno_adv(r, 2 if n == 2 else 1)
        elif c[1:] == "s":
            uno_adv(r, 2)
        elif (c[1:] == "d" or c == "w4") and r.opts["stack"]:
            g["pend"] += 2 if c[1:] == "d" else 4
            g["pk"] = "d" if c[1:] == "d" else "4"
            uno_adv(r)
        elif c[1:] == "d":
            uno_draw(g, nxt, 2)
            uno_adv(r, 2)
        elif c == "w4":
            uno_draw(g, nxt, 4)
            uno_adv(r, 2)
        else:
            uno_adv(r)
    else:
        raise Err("Unknown move.")


def uno_view(r, uid):
    g = r.g
    return {"hand": g["hands"].get(uid, []), "counts": {i: len(h) for i, h in g["hands"].items()}, "top": g["top"], "color": g["color"],
            "turn": r.ids[g["turn"]], "dir": g["dir"], "deck": len(g["deck"]), "drawn": g["drawn"], "pend": g.get("pend", 0),
            "playable": [c for c in g["hands"].get(uid, []) if uno_can(g, c) and (not g["drawn"] or c == g["drawn"])]}


# ------------------------------------------------------------------------------------------------ LUDO
OFF, SAFE = (0, 13, 26, 39), {0, 8, 13, 21, 26, 34, 39, 47}


def ludo_start(r):
    seats = {2: [0, 2], 3: [0, 1, 2], 4: [0, 1, 2, 3]}[len(r.ids)]
    r.g = {"seat": dict(zip(r.ids, seats)), "tok": {i: [-1] * 4 for i in r.ids}, "turn": 0, "roll": None, "sixes": 0, "last": "", "block": r.opts["block"]}


def ludo_blocked(g, uid, p, new):
    """A rival pair (2+ tokens on one track cell) is a wall: nobody else may land on it or jump over it."""
    seat = g["seat"][uid]
    for q in ([0] if p < 0 else range(p + 1, min(new, 50) + 1)):
        a = (OFF[seat] + q) % 52
        for oid, ot in g["tok"].items():
            if oid != uid and sum(1 for x in ot if 0 <= x <= 50 and (OFF[g["seat"][oid]] + x) % 52 == a) >= 2:
                return True
    return False


def ludo_moves(g, uid, block=None):
    roll = g["roll"]
    block = g.get("block", False) if block is None else block
    out = [i for i, p in enumerate(g["tok"][uid]) if (roll == 6 if p < 0 else p + roll <= 56)] if roll else []
    return [i for i in out if not (block and ludo_blocked(g, uid, g["tok"][uid][i], 0 if g["tok"][uid][i] < 0 else g["tok"][uid][i] + roll))]


def ludo_next(r):
    g = r.g
    g.update(turn=(g["turn"] + 1) % len(r.ids), roll=None, sixes=0)


def ludo_act(r, uid, a, d):
    g = r.g
    if r.ids[g["turn"]] != uid:
        raise Err("It's not your turn.")
    if a == "roll":
        if g["roll"]:
            raise Err("Move a token first.")
        g["roll"] = random.randint(1, 6)
        g["sixes"] = g["sixes"] + 1 if g["roll"] == 6 else 0
        g["last"] = f"{r.name(uid)} rolled {g['roll']}"
        if g["sixes"] >= 3:
            g["last"] += " - third six, turn lost"
            ludo_next(r)
        elif not ludo_moves(g, uid):
            ludo_next(r) if g["roll"] != 6 else g.update(roll=None)
    elif a == "move":
        i = d.get("token")
        if not g["roll"] or i not in ludo_moves(g, uid):
            raise Err("That token can't move.")
        roll, toks, seat = g["roll"], g["tok"][uid], g["seat"][uid]
        new = 0 if toks[i] < 0 else toks[i] + roll
        toks[i], hit = new, False
        a_pos = (OFF[seat] + new) % 52 if new <= 50 else None
        if a_pos is not None and a_pos not in SAFE:
            for oid, ot in g["tok"].items():
                for j, q in enumerate(ot):
                    if oid != uid and 0 <= q <= 50 and (OFF[g["seat"][oid]] + q) % 52 == a_pos:
                        ot[j], hit = -1, True
        if all(t == 56 for t in toks):
            return finish(r, uid)
        if hit:
            g["last"] = f"{r.name(uid)} captured a token!"
        if roll == 6 or hit or new == 56:
            g["roll"] = None
        else:
            ludo_next(r)
    else:
        raise Err("Unknown move.")


def ludo_view(r, uid):
    g = r.g
    return {"tok": g["tok"], "seat": g["seat"], "turn": r.ids[g["turn"]], "roll": g["roll"], "last": g["last"],
            "block": g["block"], "movable": ludo_moves(g, uid) if r.ids[g["turn"]] == uid else []}



# ------------------------------------------------------------------------------------------------ CHESS (full rules, server-authoritative)
KN = [(1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)]
KG = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
DIAG, ORTH = [(1, 1), (1, -1), (-1, 1), (-1, -1)], [(1, 0), (-1, 0), (0, 1), (0, -1)]
ok8 = lambda r, c: 0 <= r < 8 and 0 <= c < 8
own = lambda p, w: p != "." and p.isupper() == w


def c_attacked(b, r, c, byw):
    pr = r + 1 if byw else r - 1
    if any(ok8(pr, c + dc) and b[pr][c + dc] == ("P" if byw else "p") for dc in (-1, 1)):
        return True
    for dirs, ch in ((KN, "N"), (KG, "K")):
        if any(ok8(r + a, c + d) and b[r + a][c + d] == (ch if byw else ch.lower()) for a, d in dirs):
            return True
    for dirs, pcs in ((DIAG, "BQ"), (ORTH, "RQ")):
        for a, d in dirs:
            y, x = r + a, c + d
            while ok8(y, x) and b[y][x] == ".":
                y, x = y + a, x + d
            if ok8(y, x) and b[y][x].isupper() == byw and b[y][x].upper() in pcs:
                return True
    return False


def c_gen(g, w):
    b, out = g["b"], []
    for r in range(8):
        for c in range(8):
            p = b[r][c]
            if not own(p, w):
                continue
            u = p.upper()
            if u == "P":
                d, st = (-1, 6) if w else (1, 1)
                if ok8(r + d, c) and b[r + d][c] == ".":
                    out.append((r, c, r + d, c))
                    if r == st and b[r + 2 * d][c] == ".":
                        out.append((r, c, r + 2 * d, c))
                for dc in (-1, 1):
                    if ok8(r + d, c + dc) and ((b[r + d][c + dc] != "." and not own(b[r + d][c + dc], w)) or g["ep"] == (r + d, c + dc)):
                        out.append((r, c, r + d, c + dc))
            elif u in "NK":
                out += [(r, c, r + a, c + d) for a, d in (KN if u == "N" else KG) if ok8(r + a, c + d) and not own(b[r + a][c + d], w)]
                if u == "K" and r == (7 if w else 0) and c == 4:
                    k, q = ("K", "Q") if w else ("k", "q")
                    rk = "R" if w else "r"
                    if k in g["cr"] and b[r][5] == b[r][6] == "." and b[r][7] == rk and not any(c_attacked(b, r, x, not w) for x in (4, 5, 6)):
                        out.append((r, 4, r, 6))
                    if q in g["cr"] and b[r][1] == b[r][2] == b[r][3] == "." and b[r][0] == rk and not any(c_attacked(b, r, x, not w) for x in (4, 3, 2)):
                        out.append((r, 4, r, 2))
            else:
                for a, d in (DIAG if u == "B" else ORTH if u == "R" else DIAG + ORTH):
                    y, x = r + a, c + d
                    while ok8(y, x) and b[y][x] == ".":
                        out.append((r, c, y, x))
                        y, x = y + a, x + d
                    if ok8(y, x) and not own(b[y][x], w):
                        out.append((r, c, y, x))
    return out


def c_make(b, ep, m, promo="Q"):
    r, c, r2, c2 = m
    nb = [row[:] for row in b]
    p, cap = nb[r][c], nb[r2][c2] != "."
    nb[r][c] = "."
    if p.upper() == "P" and (r2, c2) == ep and c != c2 and not cap:
        nb[r][c2], cap = ".", True
    if p.upper() == "K" and abs(c2 - c) == 2:
        if c2 > c:
            nb[r][5], nb[r][7] = nb[r][7], "."
        else:
            nb[r][3], nb[r][0] = nb[r][0], "."
    if p.upper() == "P" and r2 in (0, 7):
        p = promo.upper() if p.isupper() else promo.lower()
    nb[r2][c2] = p
    return nb, cap


def c_king(b, w):
    k = "K" if w else "k"
    return next((r, c) for r in range(8) for c in range(8) if b[r][c] == k)


def c_check(g):
    return c_attacked(g["b"], *c_king(g["b"], g["stm"]), not g["stm"])


def c_legal(g):
    w = g["stm"]
    return [m for m in c_gen(g, w) if not c_attacked((nb := c_make(g["b"], g["ep"], m)[0]), *c_king(nb, w), not w)]


def c_next(g, m, promo="Q"):
    r, c, r2, c2 = m
    p = g["b"][r][c]
    nb, cap = c_make(g["b"], g["ep"], m, promo)
    cr = g["cr"]
    for sq, ch in (((7, 4), "KQ"), ((0, 4), "kq"), ((7, 0), "Q"), ((7, 7), "K"), ((0, 0), "q"), ((0, 7), "k")):
        if (r, c) == sq or (r2, c2) == sq:
            cr = "".join(x for x in cr if x not in ch)
    return {"b": nb, "stm": not g["stm"], "cr": cr, "ep": ((r + r2) // 2, c) if p.upper() == "P" and abs(r2 - r) == 2 else None,
            "half": 0 if cap or p.upper() == "P" else g["half"] + 1, "full": g["full"] + (0 if g["stm"] else 1)}


def c_end(g):
    if not c_legal(g):
        return (not g["stm"], "Checkmate") if c_check(g) else (None, "Stalemate")
    if g["half"] >= 100:
        return None, "50-move rule"
    left = [p for row in g["b"] for p in row if p not in ".kK"]
    if not left or (len(left) == 1 and left[0].upper() in "NB"):
        return None, "Insufficient material"


def chess_start(r):
    b = [list("rnbqkbnr"), list("pppppppp")] + [list("." * 8) for _ in range(4)] + [list("PPPPPPPP"), list("RNBQKBNR")]
    w = random.choice(r.ids)
    r.g = {"b": b, "stm": True, "cr": "KQkq", "ep": None, "half": 0, "full": 1, "w": w, "turn": r.ids.index(w), "last": None, "res": "", "rep": {}}


def chess_act(r, uid, a, d):
    g = r.g
    if a == "resign":
        g["res"] = "Resignation"
        return finish(r, next(i for i in r.ids if i != uid))
    if r.ids[g["turn"]] != uid:
        raise Err("It's not your turn.")
    m = d.get("m")
    if a != "move" or not (isinstance(m, list) and len(m) == 4 and all(isinstance(x, int) for x in m)) or tuple(m) not in c_legal(g):
        raise Err("Illegal move.")
    promo = str(d.get("promo", "Q")).upper()
    g.update(c_next(g, tuple(m), promo if promo in ("Q", "R", "B", "N") else "Q"))
    g["turn"], g["last"] = (g["turn"] + 1) % 2, m
    key = ("".join("".join(x) for x in g["b"]), g["stm"], g["cr"], g["ep"])
    g["rep"][key] = g["rep"].get(key, 0) + 1
    end = (None, "Threefold repetition") if g["rep"][key] >= 3 else c_end(g)
    if end:
        g["res"] = end[1]
        r.status, r.winner = "done", (None if end[0] is None else (g["w"] if end[0] else next(i for i in r.ids if i != g["w"])))


def chess_view(r, uid):
    g = r.g
    return {"b": ["".join(x) for x in g["b"]], "turn": r.ids[g["turn"]], "w": g["w"], "last": g["last"], "res": g["res"], "check": c_check(g) if r.status == "playing" else False,
            "legal": [list(m) for m in c_legal(g)] if r.status == "playing" and r.ids[g["turn"]] == uid else []}


START = {"uno": uno_start, "ludo": ludo_start, "chess": chess_start}
ACT = {"uno": uno_act, "ludo": ludo_act, "chess": chess_act}
VIEW = {"uno": uno_view, "ludo": ludo_view, "chess": chess_view}

# ------------------------------------------------------------------------------------------------ rooms
def finish(r, uid):
    r.status, r.winner = "done", uid


def view(r, uid):
    g = r.g and VIEW[r.game](r, uid)
    return {"v": r.v, "code": r.code, "game": r.game, "status": r.status, "host": r.ids[0], "players": r.players, "me": uid,
            "min": GAMES[r.game][0], "max": GAMES[r.game][1], "winner": r.winner, "link": invite_link(r), "g": g,
            "deadline": int(r.g["dl"]) if r.g and r.status == "playing" else 0, "group": r.title, "opts": r.opts}


def bump(r):
    r.v += 1
    r.t = time.time()
    if r.status == "playing" and r.g:
        r.g["dl"] = time.time() + (150 if r.game == "chess" else TURN_S)
    ev, r.ev = r.ev, asyncio.Event()
    ev.set()
    if r.chat_id and not r.sync:
        r.sync = asyncio.create_task(push_msg(r))


def tick(r):
    """A player who stays idle for TURN_S seconds is auto-played so the table never freezes."""
    if r.status != "playing" or time.time() < r.g.get("dl", 1e18):
        return
    uid = r.ids[r.g["turn"]]
    try:
        if r.game == "chess":                              # chess: running out of time loses the game
            r.g["res"] = "Timeout"
            finish(r, next(i for i in r.ids if i != uid))
        elif r.game == "uno":
            uno_act(r, uid, "draw", {})
            if r.status == "playing" and r.g["drawn"]:
                uno_act(r, uid, "pass", {})
        else:
            if not r.g["roll"]:
                ludo_act(r, uid, "roll", {})
            mv = ludo_moves(r.g, uid) if r.status == "playing" and r.g["turn"] == r.ids.index(uid) else []
            if mv:
                ludo_act(r, uid, "move", {"token": max(mv, key=lambda i: r.g["tok"][uid][i])})
    except Err:
        pass
    bump(r)


def drop(r, uid):
    i = r.ids.index(uid)
    r.players.pop(i)
    if r.status == "playing":
        g = r.g
        g.get("hands" if r.game == "uno" else "tok", {}).pop(uid, None)
        if len(r.players) < 2:
            g["res"] = "Opponent left"
            return finish(r, r.ids[0])
        if i < g["turn"]:
            g["turn"] -= 1
        g["turn"] %= len(r.players)
        g["drawn"] = g.get("drawn") and None
        g["roll"] = g.get("roll") and None


def purge():
    now = time.time()
    for c in [c for c, r in ROOMS.items() if now - r.t > (3600 if r.status == "done" else 6 * 3600)]:
        ROOMS.pop(c, None)


# ------------------------------------------------------------------------------------------------ Telegram group lobby
def invite_link(r):
    base = r.link or os.getenv("ELLA_GAMING_LINK", "").strip() or (f"https://t.me/{BOT_NAME[0]}" if BOT_NAME else "")
    return f"{base}?startapp=pg_{r.code}" if base else ""


def lobby_text(r):
    _, mx, title, ico = GAMES[r.game]
    e = html.escape
    if r.status == "lobby":
        st = f"🟡 <b>Waiting for players</b> ({len(r.players)}/{mx})"
    elif r.status == "playing":
        st = f"🟢 <b>Game in progress</b>\n🎯 Turn: <b>{e(r.name(r.ids[r.g['turn']]))}</b>"
    else:
        st = "🤝 <b>Draw</b>" if r.winner is None else f"🏆 <b>{e(r.name(r.winner))}</b> won the table!"
    rows = "\n".join(("👑 " if i == 0 else "👤 ") + e(p["name"]) for i, p in enumerate(r.players))
    return f"{ico} <b>{title} · Premium</b>  <code>{r.code}</code>\n\n{st}\n\n{rows}"


def lobby_markup(r):
    link = invite_link(r)
    if not link:
        return None
    label = {"lobby": "🎮 Join & play", "playing": "👀 Open table", "done": "🔁 Play again"}[r.status]
    row = [{"text": label, "url": link}]
    if r.status == "lobby":
        row.append({"text": "📤 Invite", "url": "https://t.me/share/url?" + urllib.parse.urlencode({"url": link, "text": f"Join my {GAMES[r.game][2]} table!"})})
    return {"inline_keyboard": [row]}


def _tg(token, method, **p):
    data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in p.items()}).encode()
    with urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data), timeout=10) as f:
        return json.load(f)


async def tg(token, method, **p):
    return await asyncio.to_thread(_tg, token, method, **p)


async def post_msg(r, token):
    try:
        res = await tg(token, "sendMessage", chat_id=r.chat_id, text=lobby_text(r), parse_mode="HTML", reply_markup=lobby_markup(r) or {"inline_keyboard": []})
        r.msg_id = res["result"]["message_id"]
    except Exception:
        log.exception("premium: could not post the lobby message")


async def push_msg(r):
    await asyncio.sleep(1.2)                                   # debounce bursts of moves into one edit
    r.sync = None
    token = os.getenv("ELLA_BOT_TOKEN", "").strip()
    if not (token and r.msg_id):
        return
    try:
        await tg(token, "editMessageText", chat_id=r.chat_id, message_id=r.msg_id, text=lobby_text(r), parse_mode="HTML", reply_markup=lobby_markup(r) or {"inline_keyboard": []})
    except Exception as ex:
        if "not modified" not in str(ex).lower():
            log.warning("premium: lobby edit failed: %s", ex)


# ------------------------------------------------------------------------------------------------ HTTP
def build_routes(m):
    ApiError = m.ApiError

    async def who(request, write=False):
        uid = await m.authed(request)
        if write and request.app.state.rl.hit(f"pg:{uid}", 120):
            raise ApiError(429, "rate_limited", "Slow down a little.")
        p = await request.app.state.db.get_player(uid)
        if not p:
            raise ApiError(401, "unauthorized", "Unknown player")
        pp = m.public_player(p)
        if not BOT_NAME and m.S(request).bot_token:
            try:
                BOT_NAME.append((await tg(m.S(request).bot_token, "getMe"))["result"]["username"])
            except Exception:
                pass
        return {"id": uid, "name": pp["display_name"][:24], "photo": pp["photo_url"] or ""}

    def room(request):
        r = ROOMS.get(str(request.path_params["code"]).upper())
        if not r:
            raise ApiError(404, "no_table", "This table no longer exists.")
        return r

    async def create(request):
        me, data = await who(request, True), await m.body(request)
        game = data.get("game")
        if game not in GAMES:
            raise ApiError(400, "bad_game")
        purge()
        if sum(1 for r in ROOMS.values() if r.ids[0] == me["id"] and r.status != "done") >= 3:
            raise ApiError(409, "too_many", "You already host 3 open tables.")
        r = Room(game, me)
        ROOMS[r.code] = r
        return JSONResponse(view(r, me["id"]))

    async def mine(request):
        me = await who(request)
        out = [{"code": r.code, "game": r.game, "status": r.status, "players": len(r.players), "group": r.title}
               for r in ROOMS.values() if me["id"] in r.ids and r.status != "done"]
        return JSONResponse({"rooms": out})

    async def get(request):
        me, r = await who(request), room(request)
        try:
            v = int(request.query_params.get("v", "0"))
        except ValueError:
            v = 0
        tick(r)
        if v == r.v and request.query_params.get("wait"):
            wait = 15 if r.status != "playing" else min(15, max(0.2, r.g.get("dl", 0) - time.time() + 0.2))
            try:
                await asyncio.wait_for(r.ev.wait(), wait)
            except asyncio.TimeoutError:
                pass
            tick(r)
        return JSONResponse({"same": True, "v": r.v} if v == r.v else view(r, me["id"]))

    async def act(request):
        me, r, d = await who(request, True), room(request), await m.body(request)
        a, uid = str(d.get("a", "")), me["id"]
        tick(r)
        try:
            if a == "join":
                if uid not in r.ids:
                    if r.status != "lobby":
                        raise Err("This game has already started.")
                    if len(r.players) >= GAMES[r.game][1]:
                        raise Err("This table is full.")
                    r.players.append(me)
            elif uid not in r.ids:
                raise Err("Join the table first.")
            elif a == "leave":
                drop(r, uid)
                if not r.players:
                    ROOMS.pop(r.code, None)
                    return JSONResponse({"left": True})
            elif a == "opt":
                if uid != r.ids[0] or r.status != "lobby":
                    raise Err("Only the host can change rules before the game starts.")
                if d.get("k") in r.opts:
                    r.opts[d["k"]] = bool(d.get("v"))
            elif a in ("start", "again"):
                if uid != r.ids[0]:
                    raise Err("Only the host can do that.")
                if a == "start" and r.status == "lobby":
                    if len(r.players) < GAMES[r.game][0]:
                        raise Err(f"Need at least {GAMES[r.game][0]} players.")
                    r.status = "playing"
                    START[r.game](r)
                elif a == "again" and r.status == "done":
                    r.status, r.g, r.winner = "lobby", None, None
                else:
                    raise Err("Nothing to do.")
            elif r.status != "playing":
                raise Err("The game isn't running.")
            else:
                ACT[r.game](r, uid, a, d)
        except Err as e:
            raise ApiError(400, "bad_move", str(e))
        bump(r)
        return JSONResponse(view(r, uid))

    async def bot_create(request):
        st = m.S(request)
        key = hmac.new(st.bot_token.encode(), b"premium-bot", hashlib.sha256).hexdigest()
        if not st.bot_token or not hmac.compare_digest(key, request.headers.get("x-ella-key", "")):
            raise ApiError(403, "forbidden")
        d = await m.body(request)
        game, u = d.get("game"), d.get("user") or {}
        if game not in GAMES or not isinstance(u.get("id"), int) or not isinstance(d.get("chat_id"), int):
            raise ApiError(400, "bad_param")
        db = request.app.state.db
        row = await db.get_player(u["id"]) or await db.upsert_player({k: u.get(k) for k in ("id", "first_name", "last_name", "username") if k in u})
        p = m.public_player(row)
        me = {"id": p["id"], "name": p["display_name"][:24], "photo": p["photo_url"] or ""}
        purge()
        r = next((x for x in ROOMS.values() if x.chat_id == d["chat_id"] and x.game == game and x.status != "done"), None)
        if r is None:
            r = Room(game, me)
            ROOMS[r.code] = r
            r.chat_id, r.title = d["chat_id"], str(d.get("title") or "")[:60]
            link = str(d.get("link") or "")
            r.link = link if link.startswith("https://t.me/") else None
        elif me["id"] not in r.ids and r.status == "lobby" and len(r.players) < GAMES[game][1]:
            r.players.append(me)
            bump(r)
        await post_msg(r, st.bot_token)                   # always a fresh, bottom-of-chat lobby card
        return JSONResponse({"code": r.code})

    P = "/api/premium"
    return [Route(P + "/rooms", create, methods=["POST"]), Route(P + "/mine", mine), Route(P + "/rooms/{code}", get),
            Route(P + "/rooms/{code}/act", act, methods=["POST"]), Route(P + "/bot/create", bot_create, methods=["POST"])]
