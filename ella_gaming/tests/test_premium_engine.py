"""Engine tests for Premium Games (no network): computer players finish games, rules work."""
import asyncio
import random

from ella_server import premium as P


def mk(game, n, **opts):
    r = P.Room(game, {"id": 1, "name": "A", "photo": ""})
    for i in range(1, n):
        r.players.append({"id": -i, "name": f"B{i}", "photo": "", "bot": True})
    r.opts.update(opts)
    r.status = "playing"
    P.START[game](r)
    return r


def run_bots(r, limit=6000, humans_too=True):
    from ella_server import premium_ai
    n = 0
    while r.status == "playing" and n < limit:
        n += 1
        g = r.g
        if g.get("rdone"):
            P.uno_start(r)
            continue
        uid = r.ids[g["turn"]]
        a, d = premium_ai.plan(r, uid)
        P.ACT[r.game](r, uid, a, d)
    return n


def test_uno_bots_finish():
    random.seed(3)
    for opts in ({}, {"seven": True, "jump": True}, {"pts": 100}, {"stack": False}):
        r = mk("uno", 4, **opts)
        assert run_bots(r) < 6000 and r.status == "done" and r.winner is not None, opts


def test_ludo_bots_finish_and_team():
    random.seed(5)
    r = mk("ludo", 3)
    assert run_bots(r, 20000) < 20000 and r.status == "done"
    r = mk("ludo", 4, team=True)
    assert r.g["team"]
    assert run_bots(r, 40000) < 40000 and r.status == "done" and r.g["wt"] in (0, 1)


def test_chess_bot_legal_and_san():
    random.seed(1)
    r = mk("chess", 2, lvl="easy")
    run_bots(r, 80)
    assert r.g["mv"] and all(isinstance(x, str) for x in r.g["mv"])
    P.chess_view(r, 1)


def test_chess_scholars_mate_san_and_undo():
    r = P.Room("chess", {"id": 1, "name": "A", "photo": ""})
    r.players.append({"id": 2, "name": "B", "photo": ""})
    r.status = "playing"
    P.chess_start(r)
    w = r.g["w"]
    b = 1 if w == 2 else 2
    seq = [(w, (6, 4, 4, 4)), (b, (1, 4, 3, 4)), (w, (7, 5, 4, 2)), (b, (0, 1, 2, 2)), (w, (7, 3, 3, 7)), (b, (0, 6, 2, 5)), (w, (3, 7, 1, 5))]
    for uid, m in seq:
        P.chess_act(r, uid, "move", {"m": list(m)})
    assert r.g["mv"] == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"] and r.status == "done" and r.winner == w
    r.status = "playing"
    P.chess_undo(r, w)                                   # white's last move was the one before black replied? -> 1 move back
    assert len(r.g["mv"]) == 6


def test_chess_undo_and_draw_offer():
    r = mk("chess", 2)
    r.players[1]["id"] = 2
    r.g["turn"] = 0
    r.g["w"] = r.ids[0]
    P.chess_act(r, 1, "move", {"m": [6, 4, 4, 4]})
    P.chess_act(r, 2, "draw", {})
    assert r.g["draw"] == 2
    P.chess_act(r, 1, "draw", {})
    assert r.status == "done" and r.winner is None


def test_chess_clock():
    r = mk("chess", 2, tc="3+2")
    assert r.g["tc"] == (180, 2) and set(r.g["clk"].values()) == {180}
    me = r.ids[r.g["turn"]]
    if me >= 0:
        P.chess_act(r, me, "move", {"m": [6, 4, 4, 4]})
        assert r.g["clk"][me] >= 180 + 2 - 1


def test_uno_callout_and_catch():
    r = mk("uno", 3, callout=True)
    g = r.g
    g["turn"] = 0
    g["hands"][1] = ["r5", "r6"]
    g.update(top="r1", color="r", tv="1", drawn=None, pend=0)
    P.uno_act(r, 1, "play", {"card": "r5"})
    assert g["vuln"] == 1
    P.uno_act(r, -1, "catch", {})
    assert len(g["hands"][1]) == 3 and g["vuln"] is None
