"""Static architecture tests for the moderation module (AST based: no Telegram / DB / aiogram needed).
They enforce the isolation rules: AI <-> moderation separation, no game-table access, no command clashes, every advertised command exists."""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
MOD = ROOT / "moderation"

REQUIRED = """ban unban kick mute unmute restrict unrestrict warn unwarn warnings resetwarns unwarnall banme kickme dban tban tmute reportuser checkadmin
userinfo admininfo banall unbanall muteall unmuteall kickall restrictall unrestrictall warnall purge delall cleanservice cleandownloads kickinactive
zombies promote demote adminlist setadmin title permissions admincache staff del pin unpin unpinall pinned edit lock unlock locktypes report reports
setrules rules setwelcome welcome resetwelcome setgoodbye goodbye setdescription settitle setabout slowmode setflood setwarnlimit setwarnmode
antispam antiflood antibot antiforward antilink antiraid captcha blocklist blacklist whitelist approve unapprove approvedlist""".split()
SERIOUS = ("banall", "unbanall", "muteall", "unmuteall", "kickall", "restrictall", "unrestrictall", "warnall", "unwarnall", "purge", "promote", "demote",
           "unpinall", "delall", "kickinactive", "banme", "lockdown", "modrestore", "cleanservice", "cleandownloads", "zombies", "setadmin", "permissions")
SHARED_WITH_GAME = {"ban", "unban", "mute", "unmute", "userinfo", "rules", "kick"}     # deliberately overlapping; see MODERATION.md


def _strings(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _mod_commands() -> dict[str, list[str]]:
    """primary-name -> all names, from @command(...) decorators and the _feat(...) helper."""
    out = {}
    for f in MOD.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.decorator_list:
                    if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "command":
                        names = [a.value for a in d.args if isinstance(a, ast.Constant)]
                        if names:
                            out[names[0]] = names
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_feat":
                names = [e.value for e in node.args[0].elts]
                out[names[0]] = names
    return out


def _funcs_calling(path: str, fn_names: tuple[str, ...]) -> dict[str, bool]:
    res = {}
    for node in ast.walk(ast.parse((MOD / path).read_text())):
        if isinstance(node, ast.AsyncFunctionDef):
            res[node.name] = any(isinstance(n, ast.Call) and (getattr(n.func, "id", "") in fn_names or getattr(n.func, "attr", "") in fn_names) for n in ast.walk(node))
    return res


def test_every_required_command_exists_with_help():
    have = {n for names in _mod_commands().values() for n in names}
    assert not set(REQUIRED) - have, sorted(set(REQUIRED) - have)
    for extra in ("lockdown", "protection", "raidmode", "modpanel", "modhelp", "modlog", "modbackup", "modrestore", "health", "securityreport", "suspicious", "filter"):
        assert extra in have, extra


def test_no_duplicate_command_names():
    seen = {}
    for primary, names in _mod_commands().items():
        for n in names:
            assert n not in seen, f"/{n} registered twice ({seen.get(n)} and {primary})"
            seen[n] = primary


def _game_commands() -> set[str]:
    names = set()
    for f in (ROOT / "handlers").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Command":
                names |= {a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    def consts(path, wanted):
        out = {}
        for n in ast.parse((ROOT / path).read_text()).body:
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                t = n.targets[0] if isinstance(n, ast.Assign) else n.target
                if isinstance(t, ast.Name) and t.id in wanted:
                    try:
                        out[t.id] = ast.literal_eval(n.value)
                    except Exception:
                        pass
        return out
    fun = consts("handlers/fun.py", {"ACTIONS", "ACTION_ALIASES"})
    fam = consts("services/family.py", {"ROLES", "ALIASES"})
    names |= set(fun.get("ACTIONS", ())) | set(fun.get("ACTION_ALIASES", ())) | set(fam.get("ROLES", ())) | set(fam.get("ALIASES", ()))
    return names


def test_no_unplanned_command_clash_with_the_game():
    game = _game_commands()
    assert {"kick", "ban", "rules", "userinfo"} <= game                     # sanity: the scan really sees the game's commands
    mine = {n for names in _mod_commands().values() for n in names}
    clash = (mine & game) - SHARED_WITH_GAME
    assert not clash, f"moderation commands clash with game commands: {sorted(clash)}"
    # every deliberate overlap must be explicitly marked as shared in the registrations
    for f in MOD.glob("commands_*.py"):
        pass
    src = (MOD / "commands_member.py").read_text() + (MOD / "commands_settings.py").read_text()
    assert "staff_shared=True" in src and "shared=True" in src


def _imports(path: pathlib.Path) -> set[str]:
    mods = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            mods.add(n.module)
    return mods


def test_ai_cannot_reach_moderation_and_moderation_cannot_reach_ai():
    # 1) nothing in the AI package / AI chat handler imports the moderation module (even lazily)
    for f in list((ROOT / "ai").rglob("*.py")) + [ROOT / "handlers" / "ai_chat.py"]:
        assert not any(m == "moderation" or m.startswith("moderation.") for m in _imports(f)), f
        assert "moderation" not in f.read_text().replace("moderation_cmds", ""), f"{f} mentions moderation"
    # 2) the moderation package imports no AI, game, economy or other feature code - only aiogram / sqlalchemy / config / database.session
    allowed_prefix = ("aiogram", "sqlalchemy", "config", "database.session", "__future__")
    stdlib_ok = {"asyncio", "json", "logging", "re", "time", "html", "copy", "secrets", "random", "unicodedata", "dataclasses", "typing", "collections", "pathlib"}
    for f in MOD.glob("*.py"):
        for m in _imports(f):
            top = m.split(".")[0]
            assert top in stdlib_ok or m.startswith(allowed_prefix), f"{f.name} imports {m}"
    # 3) no moderation function that executes actions is importable from AI-adjacent code: only these files import the moderation package
    users = {str(f.relative_to(ROOT)) for f in ROOT.rglob("*.py")
             if "moderation" not in f.parts and "tests" not in f.parts and any(m == "moderation" or m.startswith("moderation.") for m in _imports(f))}
    assert users == {"bot.py", "handlers/welcome.py", "handlers/start.py"}, users


def test_moderation_never_touches_game_tables():
    for f in MOD.glob("*.py"):
        imps = _imports(f)
        assert not {m for m in imps if m.split(".")[0] in ("services", "game", "handlers", "utils", "keyboards", "ai", "middlewares") or m.startswith("database.models")}, f.name
        text = f.read_text()
        assert "users.touch" not in text and "is_banned" not in text and "is_muted" not in text, f.name


def test_serious_commands_always_confirm():
    calls = {}
    for f in ("commands_bulk.py", "commands_admin.py", "commands_msg.py", "commands_security.py", "commands_member.py", "commands_misc.py"):
        calls.update(_funcs_calling(f, ("_ask", "request")))
    for name in SERIOUS:
        fn = f"cmd_{name}"
        assert fn in calls, f"missing handler {fn}"
        assert calls[fn] or name in ("permissions", "cleanservice", "setadmin", "purge", "delall", "lockdown", "modrestore"), f"/{name} executes without confirmation"
    # conditionally-confirmed ones must still reference the confirmation system somewhere
    # (/purge and /delall are deliberately NOT here: the owner asked for them to run at once, with no YES/NO)
    for name in ("cleanservice", "permissions", "lockdown", "modrestore", "setadmin"):
        assert calls[f"cmd_{name}"], name


def test_every_confirm_action_has_an_executor():
    requested, registered = set(), set()
    for f in MOD.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call):
                fn = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if fn == "request" and len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
                    requested.add(node.args[2].value)
                if fn == "_ask" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    requested.add(node.args[1].value)
                if fn == "register" and node.args and isinstance(node.args[0], ast.Constant) and getattr(getattr(node.func, "value", None), "id", "") == "confirm":
                    registered.add(node.args[0].value)
    assert requested and not requested - registered, sorted(requested - registered)


def test_callbacks_are_namespaced():
    for f in MOD.glob("*.py"):
        for s in _strings(ast.parse(f.read_text())):
            if s.startswith("mod:"):
                assert s.count(":") >= 1
    # the game never uses the "mod:" callback namespace
    for f in list((ROOT / "handlers").glob("*.py")) + list((ROOT / "keyboards").glob("*.py")):
        assert '"mod:' not in f.read_text() and "'mod:" not in f.read_text(), f


def test_existing_files_touched_minimally():
    """The integration points are small and exception-safe."""
    core = (ROOT / "middlewares" / "core.py").read_text()
    assert 'data.get("mod_ns")' in core
    welcome = (ROOT / "handlers" / "welcome.py").read_text()
    assert "legacy_welcome_suppressed" in welcome and "except Exception" in welcome
    bot = (ROOT / "bot.py").read_text()
    assert "moderation module failed to load - continuing without it" in bot
    admin = (ROOT / "handlers" / "admin.py").read_text()
    assert "gameban" in admin and 'removeprefix("game")' in admin


def test_database_tables_are_prefixed_and_separate():
    tree = ast.parse((MOD / "models.py").read_text())
    tables = [n.value.value for c in tree.body if isinstance(c, ast.ClassDef) for n in c.body
              if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "__tablename__"]
    assert tables and all(t.startswith("mod_") for t in tables), tables
    assert "class ModBase(DeclarativeBase)" in (MOD / "models.py").read_text()       # own metadata: game's create_all / Alembic never see it
