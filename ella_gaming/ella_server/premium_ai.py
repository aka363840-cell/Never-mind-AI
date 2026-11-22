"""Computer players for Premium Games. plan(r, uid) -> (action, data) for the bot's next single step.
Imports premium lazily (premium imports this module)."""
from __future__ import annotations

import random

VAL = {"p": 100, "n": 320, "b": 330, "r": 500, "q": 900, "k": 0}


def plan(r, uid):
    return {"uno": uno_plan, "ludo": ludo_plan, "chess": chess_plan}[r.game](r, uid)


# ------------------------------------------------------------------ UNO
def uno_plan(r, uid):
    from . import premium as P
    g, hand = r.g, r.g["hands"][uid]
    if r.opts.get("callout") and g["vuln"] not in (None, uid) and random.random() < 0.7:
        return "catch", {}
    ok = [c for c in hand if P.uno_can(g, c) and (not g["drawn"] or c == g["drawn"])]
    if not ok:
        return ("pass", {}) if g["drawn"] else ("draw", {})
    ids = r.ids
    nxt = ids[(g["turn"] + g["dir"]) % len(ids)]
    squeeze = len(g["hands"][nxt]) <= 2
    plain = [c for c in ok if c[0] != "w"]
    acts = [c for c in plain if c[1:] in ("s", "r", "d")]
    pick = (acts if squeeze and acts else plain or ok)
    c = random.choice(pick) if len(pick) == 1 or squeeze else max(pick, key=lambda x: (x[0] == g["color"], P.CARD_PTS(x)))
    if len(hand) > 2 and plain and c[0] == "w":
        c = random.choice(plain)
    d = {"card": c}
    if c[0] == "w":
        rest = [x[0] for x in hand if x != c and x[0] != "w"]
        d["color"] = max("rygb", key=lambda k: rest.count(k)) if rest else random.choice("rygb")
    if r.opts.get("seven") and c[1:] == "7":
        d["to"] = min((i for i in ids if i != uid), key=lambda i: len(g["hands"][i]))
    return "play", d


# ------------------------------------------------------------------ LUDO
def ludo_plan(r, uid):
    from . import premium as P
    g = r.g
    if not g["roll"]:
        return "roll", {}
    roll, seat = g["roll"], g["seat"][uid]

    def score(i):
        p = g["tok"][uid][i]
        new = 0 if p < 0 else p + roll
        s = new * 0.4
        if new == 56:
            return 100
        if p < 0:
            s += 55
        if new <= 50:
            pos = (P.OFF[seat] + new) % 52
            if pos in P.SAFE:
                s += 25
            else:
                for oid, ot in g["tok"].items():
                    if oid != uid and not P.ludo_mates(g, uid, oid):
                        if any(0 <= q <= 50 and (P.OFF[g["seat"][oid]] + q) % 52 == pos for q in ot):
                            s += 80
        if new > 50:
            s += 20
        return s + random.random()

    return "move", {"token": max(P.ludo_moves(g, uid), key=score)}


# ------------------------------------------------------------------ CHESS
def evalb(b):
    s = 0
    for r in range(8):
        for c in range(8):
            p = b[r][c]
            if p == ".":
                continue
            u = p.lower()
            v = VAL[u] + (int((7 - abs(2 * r - 7) - abs(2 * c - 7)) * 2) if u in "pnb" else 0)
            if u == "p":
                v += (6 - r if p == "P" else r - 1) * 4
            s += v if p.isupper() else -v
    return s


def material(b, white):
    t = sum(VAL[p.lower()] * (1 if p.isupper() else -1) for row in b for p in row if p != ".")
    return t if white else -t


def chess_accepts_draw(g, bot_white):
    return material(g["b"], bot_white) <= 50 or len(g["mv"]) > 120


def _search(g, depth, alpha, beta):
    from . import premium as P
    moves = P.c_legal(g)
    if not moves:
        return -99999 - depth if P.c_check(g) else 0
    if depth == 0:
        return evalb(g["b"]) * (1 if g["stm"] else -1)
    moves.sort(key=lambda m: -VAL.get(g["b"][m[2]][m[3]].lower(), 0))
    for m in moves:
        sc = -_search(P.c_next(g, m), depth - 1, -beta, -alpha)
        if sc >= beta:
            return beta
        alpha = max(alpha, sc)
    return alpha


def chess_plan(r, uid):
    from . import premium as P
    g = r.g
    moves = P.c_legal(g)
    easy = r.opts.get("lvl") == "easy"
    depth = 1 if easy else 2
    best, bs = [], -10 ** 9
    for m in moves:
        sc = -_search(P.c_next(g, m), depth - 1, -10 ** 9, 10 ** 9) + random.uniform(0, 150 if easy else 8)
        if sc > bs:
            best, bs = [m], sc
        elif sc == bs:
            best.append(m)
    if easy and random.random() < 0.2:
        best = moves
    m = random.choice(best)
    return "move", {"m": list(m), "promo": "Q"}
