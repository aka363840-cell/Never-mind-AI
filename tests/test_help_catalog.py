"""/help lists every public command grouped by category, the '/' menu is short, owner/mod commands are never advertised.
Pure Python (parses the handler sources), so it runs without Telegram or a database. Run: pytest tests/test_help_catalog.py"""
import ast
import glob
import os
import re

from utils import helpcatalog as H

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _consts(path, names):
    out = {}
    for n in ast.parse(open(os.path.join(ROOT, path)).read()).body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            tgt = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if isinstance(tgt, ast.Name) and tgt.id in names:
                out[tgt.id] = ast.literal_eval(n.value)
    return out


def _registered():
    """{file: [[names of one handler], ...]} for every @router.message(Command("a", "b")) in handlers/."""
    out = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "handlers", "*.py"))):
        for n in ast.walk(ast.parse(open(f).read())):
            if isinstance(n, ast.AsyncFunctionDef):
                for d in n.decorator_list:
                    if (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "message" and d.args
                            and isinstance(d.args[0], ast.Call) and getattr(d.args[0].func, "id", "") == "Command"):
                        names = [a.value for a in d.args[0].args if isinstance(a, ast.Constant)]
                        if names:
                            out.setdefault(os.path.basename(f), []).append(names)
    return out


def _entries():
    fun = _consts("handlers/fun.py", {"ACTIONS", "ACTION_ALIASES"})
    fam = _consts("services/family.py", {"ROLES", "ALIASES"})
    return H.entries("Bot", "*", fun["ACTIONS"], fun["ACTION_ALIASES"], fam["ROLES"], fam["ALIASES"]), fun, fam


def test_every_public_command_is_in_help():
    items, fun, fam = _entries()
    listed = {c for c, _, _ in items} | {a for _, _, al in items for a in al}
    wanted = set(fun["ACTIONS"]) | set(fun["ACTION_ALIASES"]) | set(fam["ROLES"]) | {a for a in fam["ALIASES"] if "-" not in a}
    for names_list in _registered().values():
        for names in names_list:
            wanted |= set(names)
    wanted -= H.STAFF
    assert not wanted - listed, f"missing from /help: {sorted(wanted - listed)}"
    names = [c for c, _, _ in items]
    assert len(names) == len(set(names))
    assert all(d.strip() for _, d, _ in items)


def test_staff_commands_are_hidden_everywhere():
    items, _, _ = _entries()
    shown = {c for c, _, _ in items} | {a for _, _, al in items for a in al}
    assert not shown & H.STAFF
    for group in (False, True):
        assert not {c for c, _ in H.menu("Bot", "*", group)} & H.STAFF
    # anything registered in handlers/admin.py must be on the hidden list, so new admin commands can't leak
    admin_cmds = {n for names in _registered()["admin.py"] for n in names}
    assert admin_cmds <= H.STAFF, sorted(admin_cmds - H.STAFF)
    assert {"give", "take", "setbalance", "broadcast", "maintenance", "restoredb", "aitest"} <= H.STAFF


def test_menu_is_short_valid_and_same_in_dm_and_group():
    dm, grp = H.menu("Bot", "*", False), H.menu("Bot", "*", True)
    assert 20 <= len(dm) <= 40 and len(grp) <= 100
    assert [c for c, _ in grp][: len(dm)] == [c for c, _ in dm] and grp[-1][0] == "settings"
    assert "ask" not in {c for c, _ in dm} and "ask" in {c for c, _ in grp}    # AI chat only works in groups
    for c, d in grp:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", c) and 1 <= len(d) <= 256, c
    assert {"help", "bal", "daily", "rob", "kill", "revive"} <= {c for c, _ in dm}


def test_help_is_grouped_by_category_and_complete():
    items, fun, fam = _entries()
    secs = H.sections(items, fun["ACTIONS"], fam["ROLES"])
    titles = [t for t, _ in secs]
    assert len(titles) >= 8 and len(titles) == len(set(titles))
    assert titles[0].endswith("Basics") and "💰 Economy" in titles and "⚔️ Combat & PvP" in titles
    joined = "\n".join(p for _, pages in secs for p in pages)
    # every public command (generated fun actions / family roles included) appears somewhere in the categories
    for c, _, _ in items:
        assert f"/{c} " in joined or f"/{c}\n" in joined or joined.endswith(f"/{c}"), c
    # a command is listed in exactly one category (no duplicates across screens)
    own = [c for c, _, _ in items if c not in fun["ACTIONS"] and c not in fam["ROLES"]]
    for c in own:
        assert sum(f"/{c} " in p for _, pages in secs for p in pages) == 1, c
    # nothing was forgotten in CATEGORIES: the catch-all stays empty
    assert H.OTHER not in titles, dict(secs).get(H.OTHER)
    # every screen fits in a Telegram message (with header / footer / buttons)
    assert all(len(p) + 400 < 4096 for _, pages in secs for p in pages)
    assert "&lt;amount&gt;" in joined and "<amount>" not in joined   # descriptions are HTML-escaped
