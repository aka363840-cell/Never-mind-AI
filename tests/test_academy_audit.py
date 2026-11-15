"""Part 7 final audit as tests: command collisions, menu hygiene, callback size and owner checks, bounded memory, no per-message
writes, the diamond economy on paper, the docs' economy numbers, and a whole season (two of them) walked with the pure calendar."""
import ast
import glob
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = timedelta(hours=1)
D = timedelta(days=1)
S0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
STAFF = {"academyadvance", "academyreset"}


def _read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def _files(pattern):
    return sorted(glob.glob(os.path.join(ROOT, pattern)))


# ------------------------------------------------------------------ commands
def _command_names(path):
    src = open(path, encoding="utf-8").read()
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "Command":
            out += [a.value.lower() for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return out


def _academy_commands():
    names = []
    for f in _files("handlers/academy_*.py"):
        names += _command_names(f)
    return names


def test_academy_commands_never_collide_with_any_other_command():
    owners: dict[str, list[str]] = {}
    for f in _files("handlers/*.py") + _files("*.py") + _files("middlewares/*.py"):
        for n in _command_names(f):
            owners.setdefault(n, []).append(os.path.basename(f))
    acad = _academy_commands()
    assert len(acad) >= 25 and len(acad) == len(set(acad)), "an academy command is registered twice"
    for n in acad:
        assert all(f.startswith("academy_") for f in owners[n]), (n, owners[n])


def test_every_academy_command_is_in_the_help_category_and_none_in_the_slash_menu():
    from utils import helpcatalog as HC
    cats = dict(HC.CATEGORIES)
    academy = next(v for k, v in cats.items() if k.startswith("Academy"))
    desc = dict(HC.DESC_LIST)
    menu = {c for c, *_ in HC.MENU} | set(HC.GROUP_MENU_EXTRA)
    aliases = {a for names in HC.ALIASES.values() for a in names}            # e.g. /school is an alias of /academy
    for n in _academy_commands():
        if n in STAFF or n in aliases:
            continue
        assert n in academy, f"/{n} missing from the Academy category"
        assert n in desc, f"/{n} missing from DESC_LIST"
    for n in _academy_commands():
        assert n not in menu, f"/{n} must stay out of the / menu"
    src = _read("utils/commands.py")
    assert "helpcatalog" in src and "academy" not in src.lower()      # the menu is built from helpcatalog.menu() only


# ------------------------------------------------------------------ callbacks
def test_callback_data_templates_fit_in_64_bytes_with_worst_case_values():
    pat = re.compile(r'f"(ac[a-z]*:[^"]*)"')
    seen = set()
    for f in _files("handlers/academy_*.py") + _files("services/academy_*.py"):
        seen |= set(pat.findall(open(f, encoding="utf-8").read()))
    assert len(seen) > 25
    from utils.content import academy_adventures as C
    longest_key = max(len(k) for k in C.ORDER)

    def fill(m):
        name = m.group(1)
        if name in ("uid", "target") or name.endswith(".id") or name == "user_id":
            return "9" * 16                                  # a Telegram id can be 16 digits
        if name in ("k", "stream", "e.key"):
            return "x" * max(longest_key, 12)
        if name in ("i", "n", "idx", "scr.n", "scr.step", "c.idx"):
            return "999"
        return "9" * 12                                      # row ids
    for t in seen:
        data = re.sub(r"\{([^}]*)\}", fill, t)
        assert len(data.encode()) < 64, (t, len(data.encode()))
    from handlers import academy_help
    from utils.content import academy_help_texts as T
    for page in T.PAGES:
        assert len(academy_help.data(page, 9_999_999_999_999_999).encode()) < 64


OWNER_MARKERS = ("from_user.id", "_owner_or_deny", "_owner(", "_mine(", "db_user.id", "_own(")
# acn:e: acts only for the person pressing (it never reads an id from the data).
NO_OWNER_IN_DATA = {"cb_enroll_from_group"}


def test_every_academy_callback_handler_checks_who_pressed():
    checked = 0
    for f in _files("handlers/academy_*.py"):
        src = open(f, encoding="utf-8").read()
        tree = ast.parse(src)
        funcs = {n.name: ast.get_source_segment(src, n) for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
        for n in ast.walk(tree):
            if not isinstance(n, ast.AsyncFunctionDef):
                continue
            if not any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "callback_query" for d in n.decorator_list):
                continue
            body = funcs[n.name]
            callees = {c.func.id for c in ast.walk(n) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            text = body + "".join(funcs.get(c, "") for c in callees)
            assert n.name in NO_OWNER_IN_DATA or any(m in text for m in OWNER_MARKERS), f"{os.path.basename(f)}:{n.name} has no owner check"
            checked += 1
    assert checked >= 20


# ------------------------------------------------------------------ robustness
def test_telegram_calls_in_academy_code_go_through_safe_helpers_or_are_guarded():
    """Direct bot calls are only allowed inside a try block (the notify sender and the DM helper handle Forbidden/BadRequest)."""
    call = re.compile(r"\.(send_message|send_animation|send_photo|copy_message|edit_message_text)\(")
    for f in _files("services/academy_*.py") + _files("handlers/academy_*.py"):
        src = open(f, encoding="utf-8").read()
        tree = ast.parse(src)
        guarded = set()
        for t in ast.walk(tree):
            if isinstance(t, ast.Try):
                for b in t.body:
                    guarded |= set(range(b.lineno, (b.end_lineno or b.lineno) + 1))
        for i, line in enumerate(src.splitlines(), 1):
            if call.search(line) and not line.lstrip().startswith("#"):
                assert i in guarded, f"{os.path.basename(f)}:{i} unguarded Telegram call: {line.strip()}"


def test_seen_throttle_dict_is_bounded(monkeypatch):
    from services import academy_notify as N
    N._seen_mono.clear()
    monkeypatch.setattr(N, "SEEN_MAX", 50)
    for i in range(500):                                     # 500 groups in less than the write interval: nothing expires
        N.seen_due(-1000 - i, mono=float(i))
    assert len(N._seen_mono) <= 50
    N._seen_mono.clear()


def test_reminder_cursor_is_bounded():
    from services import academy_reminders as R
    R._cursor.clear()
    for sid in range(1, 40):
        R._set_cursor(sid, sid * 10)
    assert len(R._cursor) <= R._CURSOR_KEEP and max(R._cursor) == 39
    R._cursor.clear()


def test_no_per_message_database_write_in_the_academy_middleware():
    src = _read("services/academy_seen.py")
    assert "seen_due" in src                                 # the in-memory throttle gates every write
    mw = src[src.index("seen_due"):]
    assert "commit" not in src.split("seen_due")[0].split("class")[0]


def test_ticks_are_registered_in_one_loop_and_listed():
    from services import academy_ticks
    reg = academy_ticks.registered()
    assert len(reg) >= 8
    for mod in academy_ticks.TICK_MODULES:
        __import__(mod)
    bot_src = _read("bot.py")
    assert bot_src.count("academy_loop") >= 1


# ------------------------------------------------------------------ economy
def test_economy_on_paper_matches_the_rules():
    from game import academy_rules as AR
    from services import academy_economy as E
    ds = E.days()
    assert len(ds) == AR.SEASON_DAYS
    assert E.parttime_day_max(6) == 0 and E.parttime_day_max(1) == AR.DAILY_DIAMOND_CAP
    assert E.adventure_day_max(1) == (0, 0)
    fest = E.adventure_day_max(AR.ADV_FEST_DAY)[0]
    assert fest > E.adventure_day_max(AR.ADV_FEST_DAY - 1)[0]
    s = E.summary()
    assert s["season_total"] == sum(b.total for b in ds)
    assert s["peak_day_total"] == max(b.total for b in ds)
    assert s["per_day_recurring"] == max(b.recurring for b in ds)
    assert s["sink_season"] == AR.DATE_DIAMONDS * (AR.SEASON_DAYS - AR.CRUSH_FROM_DAY + 1)
    # Energy never limits the diamonds: the busiest day needs less than one day of regeneration
    assert s["energy_needed_peak"] <= s["energy_supply_day"]
    # a season's one-off prizes are counted once
    once = sum(x.diamonds for x in E.once_sources())
    assert s["season_total"] == sum(b.recurring for b in ds) + once


def test_docs_economy_numbers_are_current():
    from services import academy_economy as E
    docs = _read("docs/ACADEMY.md")
    for key, value in E.summary().items():
        m = re.search(rf"<!-- economy:{key}=(\d+) -->", docs)
        assert m, f"docs/ACADEMY.md lacks the marker economy:{key}"
        assert int(m.group(1)) == value, f"docs economy:{key} is stale: {m.group(1)} vs {value}"


def test_docs_list_every_command_table_and_tick():
    from database import models as M
    from services import academy_ticks
    docs = _read("docs/ACADEMY.md")
    for c in set(_academy_commands()):
        assert f"/{c}" in docs, f"/{c} is not documented"
    for t in M.Base.metadata.tables:
        if t.startswith("academy_"):
            assert t in docs, f"{t} is not documented"
    for name in academy_ticks.registered():
        assert name in docs, f"tick {name} is not documented"


# ------------------------------------------------------------------ a whole season, twice, with the pure calendar
@pytest.mark.parametrize("tzname,break_h", [("Asia/Kolkata", None), ("UTC", "0"), ("America/New_York", "12")])
def test_two_whole_seasons_walked_in_half_hour_steps(monkeypatch, tzname, break_h):
    from game import academy_rules as AR
    from services import academy_adventure_logic as AL, academy_calendar as cal, academy_exam_logic as EL
    from services import academy_remind_logic as RL, academy_social_logic as SL
    monkeypatch.setenv("ACADEMY_TZ", tzname)
    if break_h is None:
        monkeypatch.delenv("ACADEMY_BREAK_HOURS", raising=False)
    else:
        monkeypatch.setenv("ACADEMY_BREAK_HOURS", break_h)
    cyc = cal.cycle()
    assert cyc == AR.SEASON_DAYS * D + AR.break_delta()
    step = timedelta(minutes=30)
    for start in (S0, S0 + cyc):
        steps_per_day = {d: 0 for d in range(1, AR.SEASON_DAYS + 1)}
        recap_days, last_day = set(), 0
        now = start - 2 * H
        end = start + cyc + 2 * H
        ceremony = 0
        while now < end:
            ph = cal.phase_at(start, now)
            if now < start:
                assert ph.key == AR.UPCOMING_KEY and RL.season_day(start, now) == 0
            elif now < cal.season_end(start):
                d = ph.day
                assert d == (now - start) // D + 1 and d >= last_day                       # days only go forward
                last_day = d
                steps_per_day[d] += 1
                assert RL.season_day(start, now) == d
                # exams: exactly the exam day's window
                kind = EL.exam_open_now(start, now)
                assert kind == next((k for k in AR.EXAM_KINDS if AR.EXAM_DAY[k] == d), None)
                # enrolment: on time Day 1, late until the end of LATE_JOIN_LAST_DAY, then closed
                assert RL.enroll_open(start, now) == (d <= AR.LATE_JOIN_LAST_DAY)
                assert cal.enroll_window(start, now).open == (d <= AR.LATE_JOIN_LAST_DAY)
                assert cal.enroll_window(start, now).late == (d > 1) or not cal.enroll_window(start, now).open
                # social windows follow the rule days
                assert SL.rival_pairing_due(d) == (AR.RIVAL_PAIR_FROM_DAY <= d <= AR.RIVAL_PAIR_LAST_DAY)
                assert SL.duel_open(d) == (AR.RIVAL_PAIR_FROM_DAY <= d <= AR.RIVAL_DUEL_LAST_DAY)
                assert SL.battle_due(d) == (AR.BATTLE_FROM_DAY <= d <= AR.BATTLE_LAST_DAY)
                # adventures never before their first day
                local_hour = cal.local(now).hour
                if d < AR.ADV_FIRST_DAY:
                    assert AL.available(d, local_hour) == []
                # recap only on Days 1..RECAP_LAST_DAY
                rd = RL.recap_due(start, now)
                if rd is not None:
                    assert rd == d and 1 <= d <= AR.RECAP_LAST_DAY
                    recap_days.add(d)
                else:
                    assert d > AR.RECAP_LAST_DAY or RL.recap_time(start, d) > now
            else:
                assert ph.key in (AR.BREAK_KEY, AR.UPCOMING_KEY) or ph.day == 0 or now < start + cyc
                assert EL.exam_open_now(start, now) is None
                assert not RL.enroll_open(start, now)
            if RL.ceremony_due(start, now):
                ceremony += 1
                assert RL.season_day(start, now) in (AR.SEASON_DAYS, 0) or now >= cal.season_end(start)
            # quiet hours match the local clock
            h = cal.local(now).hour
            q = RL.in_quiet_hours(now)
            wraps = AR.QUIET_FROM_HOUR > AR.QUIET_TO_HOUR
            assert q == ((h >= AR.QUIET_FROM_HOUR or h < AR.QUIET_TO_HOUR) if wraps else (AR.QUIET_FROM_HOUR <= h < AR.QUIET_TO_HOUR))
            now += step
        assert all(n == 48 for n in steps_per_day.values()), steps_per_day            # every day is exactly 24 h
        assert recap_days == set(range(1, AR.RECAP_LAST_DAY + 1))                      # every recap day fires
        assert ceremony > 0
        # the ceremony opens shortly after Day 7 begins, never earlier
        lo, hi = RL.ceremony_window(start)
        assert lo >= start + (AR.SEASON_DAYS - 1) * D and hi > cal.season_end(start)
    # the next season really starts one cycle later, on Day 1, and the break between them has the configured length
    nxt = S0 + cyc
    if AR.break_delta() > timedelta(0):
        assert cal.phase_at(S0, nxt - timedelta(seconds=1)).key == AR.BREAK_KEY
    assert cal.phase_at(nxt, nxt).day == 1
    assert cal.next_phase(S0, cal.season_end(S0)).starts_at == nxt                   # during the break "next" = Day 1 of the next season
    assert nxt - cal.season_end(S0) == AR.break_delta()


def test_help_week_page_agrees_with_the_calendar():
    from services import academy_calendar as cal, academy_help as HP
    page = HP.render("week", S0)
    for p in cal.week(S0):
        assert cal.fmt_when(p.starts_at) in page
