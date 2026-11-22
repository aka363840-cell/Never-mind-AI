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

from . import premium_ai, tgstyle

from starlette.responses import JSONResponse
from starlette.routing import Route

log = logging.getLogger("ella.premium")
GAMES = {"uno": (2, 6, "UNO", "🃏"), "ludo": (2, 4, "LUDO", "🎲"), "chess": (2, 2, "CHESS", "♟️")}
ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TURN_S = 60
ROOMS: dict[str, "Room"] = {}
BOT_NAME: list[str] = []


# Rules the host can change in the lobby: key -> allowed values (the first one is the default)
OPT_DEFS = {"stack": [True, False], "block": [True, False], "callout": [True, False], "seven": [False, True], "jump": [False, True],
            "pts": [0, 100, 200, 500], "team": [False, True], "tc": ["off", "1+0", "3+2", "5+0", "10+0"], "lvl": ["normal", "easy"]}
BOTS = ["Ruby", "Nova", "Pixel", "Echo", "Zed"]


class Err(Exception):
    pass


class Room:
    def __init__(self, game, host):
        self.code = "".join(secrets.choice(ALPHA) for _ in range(5))
        self.game, self.players, self.status, self.g, self.winner = game, [host], "lobby", None, None
        self.v, self.ev, self.t = 1, asyncio.Event(), time.time()
        self.opts = {k: v[0] for k, v in OPT_DEFS.items()}
        self.match, self.bt = {}, None
        self.chat_id = self.msg_id = self.title = self.link = None
        self.sync = None

    @property
    def ids(self):
        return [p["id"] for p in self.players]

    def host(self):
        return next((i for i in self.ids if i >= 0), self.ids[0])

    def name(self, uid):
        return next((p["name"] for p in self.players if p["id"] == uid), "Player")


# ------------------------------------------------------------------------------------------------ UNO
CARD_PTS = lambda c: 50 if c[0] == "w" else 20 if c[1:] in ("s", "r", "d") else int(c[1:])


def uno_start(r):
    deck = [c + v for c in "rygb" for v in ["0"] + [x for x in "123456789sdr" for _ in (0, 1)]] + ["w"] * 4 + ["w4"] * 4
    random.shuffle(deck)
    n = len(r.ids)
    r.match.setdefault("score", {})
    r.match["round"] = r.match.get("round", 0) + 1
    first = (r.match["round"] - 1) % n
    g = {"hands": {i: [deck.pop() for _ in range(7)] for i in r.ids}, "deck": deck, "dir": 1, "turn": first, "drawn": None, "pend": 0, "pk": "",
         "vuln": None, "rdone": None, "last": ""}
    while deck[-1][0] == "w":
        random.shuffle(deck)
    top = deck.pop()
    g.update(top=top, color=top[0], tv=top[1:], pile=[top])
    r.g = g
    if top[1:] == "s":
        g["turn"] = (first + 1) % n
    elif top[1:] == "r":
        g["dir"] = -1
        g["turn"] = (first - 1) % n if n > 2 else (first + 1) % n
    elif top[1:] == "d":
        uno_draw(g, r.ids[first], 2)
        g["turn"] = (first + 1) % n


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


def uno_jump(r, g, c):
    """Jump-in: the exact same card (colour + value) as the top card may be played out of turn."""
    return bool(r.opts.get("jump")) and c == g["top"] and c[0] != "w" and not g["pend"] and not g["drawn"]


def uno_adv(r, k=1):
    g = r.g
    g["turn"] = (g["turn"] + g["dir"] * k) % len(r.ids)


def uno_end(r, uid):
    """Someone emptied their hand. Single round -> game over; otherwise points, next round or match winner."""
    g, target = r.g, int(r.opts.get("pts") or 0)
    if not target:
        return finish(r, uid)
    pts = sum(CARD_PTS(c) for i, h in g["hands"].items() if i != uid for c in h)
    sc = r.match["score"]
    sc[uid] = sc.get(uid, 0) + pts
    g["rdone"] = {"w": uid, "pts": pts}
    g["last"] = f"{r.name(uid)} won the round (+{pts})"
    if sc[uid] >= target:
        g["res"] = f"Reached {target} points"
        finish(r, uid)


def uno_act(r, uid, a, d):
    g, ids = r.g, r.ids
    if g.get("rdone"):
        if a == "next" and r.status == "playing":
            return uno_start(r)
        raise Err("Round finished - start the next one.")
    if a == "uno":                                      # call UNO on your last card (any time while you are exposed)
        if g["vuln"] != uid:
            raise Err("Nothing to call.")
        g["vuln"], g["last"] = None, f"{r.name(uid)}: UNO!"
        return
    if a == "catch":                                    # catch a rival who forgot to call UNO
        v = g["vuln"]
        if v is None or v == uid:
            raise Err("Nobody to catch.")
        uno_draw(g, v, 2)
        g["vuln"], g["last"] = None, f"{r.name(uid)} caught {r.name(v)} - +2 cards!"
        return
    me_turn = ids[g["turn"]] == uid
    if not me_turn:
        c = str(d.get("card", ""))
        if not (a == "play" and c in g["hands"].get(uid, []) and uno_jump(r, g, c)):
            raise Err("It's not your turn.")
        g["turn"] = ids.index(uid)                      # jump-in: the turn moves to you
        g["last"] = f"{r.name(uid)} jumped in!"
    if g["vuln"] is not None and g["vuln"] != uid:
        g["vuln"] = None                                # the chance to catch is over once someone else moves
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
        if not (uno_can(g, c) or (not me_turn)):
            raise Err("That card can't be played now.")
        if c[0] == "w" and col not in list("rygb"):
            raise Err("Pick a colour.")
        seven, to = r.opts["seven"] and c[1:] == "7", d.get("to")
        if seven and len(hand) > 1 and (to not in ids or to == uid):
            raise Err("Pick a player to swap hands with.")
        hand.remove(c)
        g["pile"].append(c)
        g.update(top=c, tv="" if c[0] == "w" else c[1:], color=col if c[0] == "w" else c[0], drawn=None, last=g["last"] if not me_turn else "")
        if not hand:
            return uno_end(r, uid)
        if r.opts["callout"] and len(hand) == 1 and uid >= 0:
            g["vuln"] = uid                             # must press UNO before somebody catches you
        nxt = ids[(g["turn"] + g["dir"]) % n]
        if seven:
            g["hands"][uid], g["hands"][to] = g["hands"][to], g["hands"][uid]
            g["last"] = f"{r.name(uid)} swapped hands with {r.name(to)}"
            if not g["hands"][uid]:
                return uno_end(r, uid)
        if r.opts["seven"] and c[1:] == "0" and n > 1:
            hs = [g["hands"][i] for i in ids]
            hs = hs[-g["dir"]:] + hs[:-g["dir"]] if g["dir"] == 1 else hs[1:] + hs[:1]
            for i, h in zip(ids, hs):
                g["hands"][i] = h
            g["last"] = "Everyone passed their hand along!"
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
    g, mine = r.g, r.ids[r.g["turn"]] == uid
    hand = g["hands"].get(uid, [])
    pl = [c for c in hand if uno_can(g, c) and (not g["drawn"] or c == g["drawn"])] if mine else [c for c in hand if uno_jump(r, g, c)]
    return {"hand": hand, "counts": {i: len(h) for i, h in g["hands"].items()}, "top": g["top"], "color": g["color"],
            "turn": r.ids[g["turn"]], "dir": g["dir"], "deck": len(g["deck"]), "drawn": g["drawn"], "pend": g.get("pend", 0),
            "playable": pl, "vuln": g["vuln"], "last": g["last"], "rdone": g["rdone"], "score": r.match.get("score", {}), "round": r.match.get("round", 1),
            "target": int(r.opts.get("pts") or 0)}


# ------------------------------------------------------------------------------------------------ LUDO
OFF, SAFE = (0, 13, 26, 39), {0, 8, 13, 21, 26, 34, 39, 47}


def ludo_start(r):
    seats = {2: [0, 2], 3: [0, 1, 2], 4: [0, 1, 2, 3]}[len(r.ids)]
    r.g = {"seat": dict(zip(r.ids, seats)), "tok": {i: [-1] * 4 for i in r.ids}, "turn": 0, "roll": None, "sixes": 0, "last": "", "block": r.opts["block"],
           "team": bool(r.opts["team"]) and len(r.ids) == 4, "extra": "", "wt": None, "rv": 0}


def ludo_mates(g, a, b):
    """Teammates (team mode: seats 0+2 against 1+3) never capture or block each other."""
    return g.get("team") and g["seat"][a] % 2 == g["seat"][b] % 2


def ludo_blocked(g, uid, p, new):
    """A rival pair (2+ tokens on one track cell) is a wall: nobody else may land on it or jump over it."""
    seat = g["seat"][uid]
    for q in ([0] if p < 0 else range(p + 1, min(new, 50) + 1)):
        a = (OFF[seat] + q) % 52
        for oid, ot in g["tok"].items():
            if oid != uid and not ludo_mates(g, uid, oid) and sum(1 for x in ot if 0 <= x <= 50 and (OFF[g["seat"][oid]] + x) % 52 == a) >= 2:
                return True
    return False


def ludo_moves(g, uid, block=None):
    roll = g["roll"]
    block = g.get("block", False) if block is None else block
    out = [i for i, p in enumerate(g["tok"][uid]) if (roll == 6 if p < 0 else p + roll <= 56)] if roll else []
    return [i for i in out if not (block and ludo_blocked(g, uid, g["tok"][uid][i], 0 if g["tok"][uid][i] < 0 else g["tok"][uid][i] + roll))]


def ludo_done(g, uid):
    return all(t == 56 for t in g["tok"][uid])


def ludo_next(r):
    g = r.g
    g.update(turn=(g["turn"] + 1) % len(r.ids), roll=None, sixes=0, extra="")
    for _ in range(len(r.ids)):                         # team mode: a finished player just waits for the partner
        if g["team"] and ludo_done(g, r.ids[g["turn"]]):
            g["turn"] = (g["turn"] + 1) % len(r.ids)
        else:
            break


def ludo_act(r, uid, a, d):
    g = r.g
    if r.ids[g["turn"]] != uid:
        raise Err("It's not your turn.")
    if a == "roll":
        if g["roll"]:
            raise Err("Move a token first.")
        g["roll"] = random.randint(1, 6)
        g["sixes"] = g["sixes"] + 1 if g["roll"] == 6 else 0
        g["extra"], g["rv"] = "", g["roll"]
        g["last"] = f"{r.name(uid)} rolled {g['roll']}"
        if g["sixes"] >= 3:
            g["last"] += " - third six, turn lost"
            ludo_next(r)
        elif not ludo_moves(g, uid):
            if g["roll"] != 6:
                ludo_next(r)
            else:
                g.update(roll=None, extra="six")
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
                    if oid != uid and not ludo_mates(g, uid, oid) and 0 <= q <= 50 and (OFF[g["seat"][oid]] + q) % 52 == a_pos:
                        ot[j], hit = -1, True
        if ludo_done(g, uid) and (not g["team"] or all(ludo_done(g, o) for o in g["tok"] if ludo_mates(g, uid, o))):
            g["wt"] = seat % 2
            if g["team"]:
                g["res"] = "Team win"
            return finish(r, uid)
        if hit:
            g["last"] = f"{r.name(uid)} captured a token!"
        if roll == 6 or hit or new == 56:
            g["roll"], g["extra"] = None, "six" if roll == 6 else "capture" if hit else "home"
        else:
            ludo_next(r)
    else:
        raise Err("Unknown move.")


def ludo_view(r, uid):
    g = r.g
    return {"tok": g["tok"], "seat": g["seat"], "turn": r.ids[g["turn"]], "roll": g["roll"], "last": g["last"], "extra": g["extra"], "rv": g["rv"], "team": g["team"], "wt": g["wt"],
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


def tc_parse(tc):
    """'3+2' -> (180 seconds, 2 seconds increment); 'off' -> None."""
    if tc == "off" or "+" not in str(tc):
        return None
    m, i = str(tc).split("+")
    return int(m) * 60, int(i)


def chess_start(r):
    b = [list("rnbqkbnr"), list("pppppppp")] + [list("." * 8) for _ in range(4)] + [list("PPPPPPPP"), list("RNBQKBNR")]
    w = random.choice(r.ids)
    tc = tc_parse(r.opts["tc"])
    r.g = {"b": b, "stm": True, "cr": "KQkq", "ep": None, "half": 0, "full": 1, "w": w, "turn": r.ids.index(w), "last": None, "res": "", "rep": {},
           "mv": [], "hist": [], "draw": None, "undo": None, "tc": tc, "inc": tc[1] if tc else 0, "clk": {i: tc[0] for i in r.ids} if tc else {}, "ts": time.time()}


def c_san(g, m, promo, legal):
    r, c, r2, c2 = m
    b, p = g["b"], g["b"][r][c]
    u, f = p.upper(), "abcdefgh"
    sq, cap = f[c2] + str(8 - r2), b[r2][c2] != "." or (u == "P" and c != c2)
    if u == "K" and abs(c2 - c) == 2:
        return "O-O" if c2 > c else "O-O-O"
    if u == "P":
        return (f[c] + "x" if cap else "") + sq + ("=" + promo if r2 in (0, 7) else "")
    amb = [x for x in legal if x != m and b[x[0]][x[1]] == p and x[2] == r2 and x[3] == c2]
    dis = ""
    if amb:
        dis = f[c] if all(x[1] != c for x in amb) else str(8 - r) if all(x[0] != r for x in amb) else f[c] + str(8 - r)
    return u + dis + ("x" if cap else "") + sq


def chess_snap(g):
    return {"b": [row[:] for row in g["b"]], "stm": g["stm"], "cr": g["cr"], "ep": g["ep"], "half": g["half"], "full": g["full"], "turn": g["turn"],
            "last": g["last"], "rep": dict(g["rep"]), "clk": dict(g["clk"])}


def chess_draw_end(r, why):
    r.g["res"] = why
    r.status, r.winner = "done", None


def chess_act(r, uid, a, d):
    g = r.g
    opp = next(i for i in r.ids if i != uid)
    if a == "resign":
        g["res"] = "Resignation"
        return finish(r, opp)
    if a == "draw":                                     # offer a draw, or accept the one on the table
        if g["draw"] == opp:
            return chess_draw_end(r, "Draw agreed")
        g["draw"] = uid
        if opp < 0 and premium_ai.chess_accepts_draw(g, opp == g["w"]):
            return chess_draw_end(r, "Draw agreed")
        return
    if a == "draw_no":
        g["draw"] = None
        return
    if a == "undo":                                     # ask to take back your last move, or accept the request
        if g["undo"] == opp or opp < 0:
            return chess_undo(r, uid)
        if not g["hist"]:
            raise Err("Nothing to take back yet.")
        g["undo"] = uid
        return
    if a == "undo_no":
        g["undo"] = None
        return
    if r.ids[g["turn"]] != uid:
        raise Err("It's not your turn.")
    m = d.get("m")
    if a != "move" or not (isinstance(m, list) and len(m) == 4 and all(isinstance(x, int) for x in m)):
        raise Err("Illegal move.")
    legal = c_legal(g)
    if tuple(m) not in legal:
        raise Err("Illegal move.")
    promo = str(d.get("promo", "Q")).upper()
    promo = promo if promo in ("Q", "R", "B", "N") else "Q"
    now = time.time()
    if g["tc"]:
        g["clk"][uid] = g["clk"][uid] - (now - g["ts"]) + g["inc"]
    snap, san = chess_snap(g), c_san(g, tuple(m), promo, legal)
    g["hist"].append(snap)
    g.update(c_next(g, tuple(m), promo))
    g["turn"], g["last"], g["ts"], g["draw"], g["undo"] = (g["turn"] + 1) % 2, m, now, None, None
    chk = c_check(g)
    mate = chk and not c_legal(g)
    g["mv"].append(san + ("#" if mate else "+" if chk else ""))
    key = ("".join("".join(x) for x in g["b"]), g["stm"], g["cr"], g["ep"])
    g["rep"][key] = g["rep"].get(key, 0) + 1
    end = (None, "Threefold repetition") if g["rep"][key] >= 3 else c_end(g)
    if end:
        g["res"] = end[1]
        r.status, r.winner = "done", (None if end[0] is None else (g["w"] if end[0] else next(i for i in r.ids if i != g["w"])))


def chess_undo(r, uid):
    g = r.g
    k = 2 if r.ids[g["turn"]] == uid else 1               # you moved last -> 1 move back, otherwise take back the rival's reply too
    if len(g["hist"]) < k:
        raise Err("Nothing to take back yet.")
    snap = None
    for _ in range(k):
        snap = g["hist"].pop()
        g["mv"].pop()
    g.update({x: snap[x] for x in snap})
    g["ts"], g["draw"], g["undo"] = time.time(), None, None


def chess_view(r, uid):
    g = r.g
    full = {"p": 8, "n": 2, "b": 2, "r": 2, "q": 1}
    have = {}
    for row in g["b"]:
        for p in row:
            have[p] = have.get(p, 0) + 1
    lost = lambda white: "".join(k * max(0, n - have.get(k.upper() if white else k, 0)) for k, n in full.items())
    mover = r.ids[g["turn"]]
    return {"b": ["".join(x) for x in g["b"]], "turn": mover, "w": g["w"], "last": g["last"], "res": g["res"], "check": c_check(g) if r.status == "playing" else False,
            "legal": [list(m) for m in c_legal(g)] if r.status == "playing" and mover == uid else [], "mv": g["mv"], "lost": {"w": lost(True), "b": lost(False)},
            "draw": g["draw"], "undo": g["undo"], "clk": {i: max(0, int(c)) for i, c in g["clk"].items()}, "ts": int(g["ts"]), "tc": bool(g["tc"]),
            "can_undo": bool(g["hist"]), "rem": max(0, int(g["clk"][mover] - (time.time() - g["ts"]))) if g["tc"] else 0}


START = {"uno": uno_start, "ludo": ludo_start, "chess": chess_start}
ACT = {"uno": uno_act, "ludo": ludo_act, "chess": chess_act}
VIEW = {"uno": uno_view, "ludo": ludo_view, "chess": chess_view}

# ------------------------------------------------------------------------------------------------ rooms
def finish(r, uid):
    r.status, r.winner = "done", uid


def view(r, uid):
    g = r.g and VIEW[r.game](r, uid)
    return {"v": r.v, "code": r.code, "game": r.game, "status": r.status, "host": r.host(), "players": r.players, "me": uid,
            "min": GAMES[r.game][0], "max": GAMES[r.game][1], "winner": r.winner, "link": invite_link(r), "g": g,
            "deadline": int(r.g["dl"]) if r.g and r.status == "playing" else 0, "group": r.title, "opts": r.opts}


def actor(r):
    g = r.g
    return None if not g or r.status != "playing" or g.get("rdone") else r.ids[g["turn"]]


def bump(r):
    r.v += 1
    r.t = time.time()
    if r.status == "playing" and r.g:
        g = r.g
        if r.game == "chess" and g["tc"]:
            g["dl"] = g["ts"] + g["clk"][r.ids[g["turn"]]]
        else:
            g["dl"] = time.time() + (150 if r.game == "chess" else 20 if g.get("rdone") else TURN_S)
    ev, r.ev = r.ev, asyncio.Event()
    ev.set()
    if r.chat_id and not r.sync:
        r.sync = asyncio.create_task(push_msg(r))
    if (actor(r) or 0) < 0 and not r.bt:
        r.bt = asyncio.create_task(bot_run(r))


async def bot_run(r):
    """Computer players: wait a moment (so people can follow), then play one step at a time until a human is up."""
    try:
        while True:
            uid = actor(r)
            if uid is None or uid >= 0:
                return
            await asyncio.sleep(random.uniform(0.9, 1.7))
            if actor(r) != uid:
                continue
            a, d = await asyncio.to_thread(premium_ai.plan, r, uid)
            try:
                ACT[r.game](r, uid, a, d)
            except Err as e:
                log.warning("premium bot move refused: %s", e)
                if r.game == "uno":                     # never get stuck: fall back to a plain draw/pass
                    try:
                        ACT["uno"](r, uid, "pass" if r.g["drawn"] else "draw", {})
                    except Err:
                        return
                else:
                    return
            bump(r)
    except Exception:
        log.exception("premium bot crashed")
    finally:
        r.bt = None


def tick(r):
    """A player who stays idle is auto-played so the table never freezes (chess: the clock runs out)."""
    if r.status != "playing" or time.time() < r.g.get("dl", 1e18):
        return
    if r.g.get("rdone"):
        if r.game == "uno":
            uno_start(r)
        return bump(r)
    uid = r.ids[r.g["turn"]]
    if uid < 0:
        return
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
    if not any(x >= 0 for x in r.ids):
        return
    if r.status == "playing":
        g = r.g
        g.get("hands" if r.game == "uno" else "tok", {}).pop(uid, None)
        if len(r.players) < 2 or (r.game == "chess"):
            g["res"] = "Opponent left"
            return finish(r, next((x for x in r.ids if x >= 0), r.ids[0]))
        if i < g["turn"]:
            g["turn"] -= 1
        g["turn"] %= len(r.players)
        g["drawn"] = g.get("drawn") and None
        g["roll"] = g.get("roll") and None
        if g.get("vuln") == uid:
            g["vuln"] = None
        if r.game == "ludo" and g.get("team"):
            g["team"] = False


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
    rows = "\n".join(("👑 " if p["id"] == r.host() else "🤖 " if p["id"] < 0 else "👤 ") + e(p["name"]) for p in r.players)
    return f"{ico} <b>{title} · Premium</b>  <code>{r.code}</code>\n\n{st}\n\n{rows}"


async def ensure_bot_name(token):
    if not BOT_NAME and token:
        try:
            BOT_NAME.append((await tg(token, "getMe"))["result"]["username"])
        except Exception:
            log.warning("premium: getMe failed, group cards may have no buttons")


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


CARD_STYLES = {"🎮": "success", "🔁": "success", "📤": "primary", "👀": "primary"}


async def card(token, method, r, **p):
    """Sends/edits the group card with premium emoji + coloured buttons; falls back to plain if Telegram refuses them."""
    await tgstyle.load(token, tg)
    text, mk = lobby_text(r), lobby_markup(r) or {"inline_keyboard": []}
    try:
        return await tg(token, method, text=tgstyle.html_text(text), parse_mode="HTML", reply_markup=tgstyle.markup(mk, CARD_STYLES), **p)
    except Exception as ex:
        if "not modified" in str(ex).lower():
            raise
        tgstyle.refused()
        log.info("premium: plain card fallback (%s)", ex)
        return await tg(token, method, text=text, parse_mode="HTML", reply_markup=mk, **p)


async def post_msg(r, token):
    try:
        res = await card(token, "sendMessage", r, chat_id=r.chat_id)
        r.msg_id = res["result"]["message_id"]
        return True
    except Exception:
        log.exception("premium: could not post the lobby message")
        return False


async def push_msg(r):
    await asyncio.sleep(1.2)                                   # debounce bursts of moves into one edit
    r.sync = None
    token = os.getenv("ELLA_BOT_TOKEN", "").strip()
    if not (token and r.msg_id):
        return
    try:
        await card(token, "editMessageText", r, chat_id=r.chat_id, message_id=r.msg_id)
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
        await ensure_bot_name(m.S(request).bot_token)
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
        if sum(1 for r in ROOMS.values() if r.host() == me["id"] and r.status != "done") >= 3:
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
                if not any(i >= 0 for i in r.ids):
                    ROOMS.pop(r.code, None)
                    return JSONResponse({"left": True})
            elif a == "opt":
                if uid != r.host() or r.status != "lobby":
                    raise Err("Only the host can change rules before the game starts.")
                if d.get("k") in OPT_DEFS and d.get("v") in OPT_DEFS[d["k"]]:
                    r.opts[d["k"]] = d["v"]
            elif a in ("addbot", "rmbot"):
                if uid != r.host() or r.status != "lobby":
                    raise Err("Only the host can change the seats before the game starts.")
                if a == "addbot":
                    if len(r.players) >= GAMES[r.game][1]:
                        raise Err("This table is full.")
                    n = sum(1 for i in r.ids if i < 0) + 1
                    used = {p["name"] for p in r.players}
                    nm = next((f"🤖 {b}" for b in BOTS if f"🤖 {b}" not in used), f"🤖 Bot {n}")
                    r.players.append({"id": -n, "name": nm, "photo": "", "bot": True})
                else:
                    bots = [p for p in r.players if p["id"] < 0]
                    if not bots:
                        raise Err("No computer players to remove.")
                    r.players.remove(bots[-1])
            elif a in ("start", "again"):
                if uid != r.host():
                    raise Err("Only the host can do that.")
                if a == "start" and r.status == "lobby":
                    if len(r.players) < GAMES[r.game][0]:
                        raise Err(f"Need at least {GAMES[r.game][0]} players.")
                    r.status, r.match = "playing", {}
                    START[r.game](r)
                elif a == "again" and r.status == "done":
                    r.status, r.g, r.winner, r.match = "lobby", None, None, {}
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
        await ensure_bot_name(st.bot_token)
        if not await post_msg(r, st.bot_token):           # always a fresh, bottom-of-chat lobby card
            raise ApiError(502, "telegram_failed", "Could not post the table card in this chat.")
        return JSONResponse({"code": r.code})

    P = "/api/premium"
    return [Route(P + "/rooms", create, methods=["POST"]), Route(P + "/mine", mine), Route(P + "/rooms/{code}", get),
            Route(P + "/rooms/{code}/act", act, methods=["POST"]), Route(P + "/bot/create", bot_create, methods=["POST"])]
