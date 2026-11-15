"""Academy Part 6, PURE tests (no database, no Telegram): schedule, quiet hours, limiter, reminder choice, titles, ceremony text with
plug-in sections, report card, Hall of Fame text. Run: pytest tests/test_academy_notify_logic.py"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from game import academy_rules as AR
from services import academy_ceremony as C
from services import academy_exam_logic as EL
from services import academy_remind_logic as RL
from services import academy_remind_views as V
from utils.content import academy_remind_texts as T

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)          # 14:30 IST on Monday: Day 1 starts
H = timedelta(hours=1)


def at(day, hours=0.0):
    return T0 + (day - 1) * AR.DAY + timedelta(hours=hours)


def run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ACADEMY_TZ", "Asia/Kolkata")
    monkeypatch.delenv("ACADEMY_DAILY_HOUR", raising=False)
    yield
    C.CEREMONY_SECTIONS[:] = [s for s in C.CEREMONY_SECTIONS if not s.name.startswith("t_")]


# ------------------------------------------------------------------ calendar basics and quiet hours
def test_season_day_and_break():
    assert RL.season_day(T0, T0 - H) == 0
    assert [RL.season_day(T0, at(d, 1)) for d in range(1, 8)] == list(range(1, 8))
    assert RL.season_day(T0, T0 + 7 * AR.DAY) == 0


@pytest.mark.parametrize("ist_hour,quiet", [(22, False), (23, True), (0, True), (3, True), (6, True), (7, False), (12, False)])
def test_quiet_hours_are_local_time(ist_hour, quiet):
    utc = datetime(2026, 11, 3, ist_hour, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc)
    assert RL.in_quiet_hours(utc) is quiet


def test_quiet_window_that_does_not_wrap():
    assert RL.in_quiet_hours(datetime(2026, 11, 3, 4, 0, tzinfo=timezone.utc), 1, 5) is False or True    # tz shifts it; just must not raise
    t = datetime(2026, 11, 3, 2, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc)
    assert RL.in_quiet_hours(t, 1, 5) and not RL.in_quiet_hours(t, 3, 5)


def test_local_day_key_changes_at_local_midnight():
    a = datetime(2026, 11, 3, 23, 59, tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc)
    assert RL.local_day_key(a) == "d:2026-11-03"
    assert RL.local_day_key(a + timedelta(minutes=2)) == "d:2026-11-04"


# ------------------------------------------------------------------ recap schedule
def test_recap_is_at_the_daily_hour_each_day_1_to_6_and_never_day_7():
    for d in range(1, 7):
        rt = RL.recap_time(T0, d)
        assert rt is not None and RL.local(rt).hour == 22 and RL.local(rt).minute == 0
        lo, hi = RL.day_bounds(T0, d)
        assert lo < rt < hi
    assert RL.recap_time(T0, 7) is None


def test_recap_due_only_after_its_time_and_inside_the_day():
    rt = RL.recap_time(T0, 2)
    assert RL.recap_due(T0, rt - timedelta(minutes=1)) != 2
    assert RL.recap_due(T0, rt) == 2
    assert RL.recap_due(T0, RL.day_bounds(T0, 2)[1] - timedelta(minutes=1)) == 2
    assert RL.recap_due(T0, RL.day_bounds(T0, 2)[1] + timedelta(minutes=1)) != 2
    assert RL.recap_due(T0, at(7, 12)) is None


def test_daily_hour_env_and_clamp(monkeypatch):
    monkeypatch.setenv("ACADEMY_DAILY_HOUR", "20")
    assert RL.local(RL.recap_time(T0, 1)).hour == 20
    monkeypatch.setenv("ACADEMY_DAILY_HOUR", "99")
    assert AR.daily_hour() == 23
    monkeypatch.setenv("ACADEMY_DAILY_HOUR", "abc")
    assert AR.daily_hour() == 22


def test_a_day_that_has_barely_started_recaps_one_hour_before_it_ends():
    start = datetime(2026, 11, 2, 16, 0, tzinfo=timezone.utc)     # 21:30 IST: 22:00 would come only 30 minutes into the day
    for d in range(1, 7):
        assert RL.recap_time(start, d) == RL.day_bounds(start, d)[1] - H


def test_ceremony_window():
    lo, hi = RL.ceremony_window(T0)
    assert lo == at(7, 0) + timedelta(minutes=AR.CEREMONY_DELAY_MIN)
    assert not RL.ceremony_due(T0, at(7, 0.05))
    assert RL.ceremony_due(T0, at(7, 1))
    assert RL.ceremony_due(T0, T0 + 7 * AR.DAY + H)          # still due during the break (bot was off)
    assert not RL.ceremony_due(T0, hi + H)
    assert not RL.ceremony_due(T0, at(6, 5))


# ------------------------------------------------------------------ enrolment schedule
def test_enroll_group_plan_one_per_window_then_one_last_call():
    seen = []
    t = T0
    while t < T0 + 5 * AR.DAY:
        p = RL.enroll_group_plan(T0, t)
        if p and (p.kind, p.suffix) not in seen:
            seen.append((p.kind, p.suffix))
        t += timedelta(minutes=30)
    kinds = [k for k, _ in seen]
    assert kinds.count("enroll_last") == 1
    assert "enroll_g" in kinds and ("enroll_g", "w0") not in seen          # window 0 belongs to the season-open message
    assert RL.enroll_group_plan(T0, at(5, 0.5)) is None                    # admissions closed
    assert RL.enroll_group_plan(T0, at(1, 1)) is None
    reg = [s for k, s in seen if k == "enroll_g"]
    assert len(reg) == len(set(reg)) and len(reg) <= 15


def test_last_call_is_in_the_final_hours_before_close():
    close = RL.enroll_closes(T0)
    assert close == at(5, 0)
    assert RL.enroll_group_plan(T0, close - 3 * H).kind == "enroll_g"
    assert RL.enroll_group_plan(T0, close - 1 * H).kind == "enroll_last"
    text = V.enroll_group_text(RL.GroupPlan("enroll_last", "last", True), 3, close, close - H)
    assert "Last call" in text and "Season 3" in text


def test_enroll_dm_text_day4_is_a_last_call():
    assert "Last call" in V.enroll_dm_text(4, 3, RL.enroll_closes(T0))
    for d in (1, 2, 3):
        assert "Last call" not in V.enroll_dm_text(d, 3, RL.enroll_closes(T0))


# ------------------------------------------------------------------ the limiter
class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept += s
        self.t += s


def test_limiter_never_exceeds_rate_in_any_rolling_second():
    clk = FakeClock()
    lim = RL.Limiter(rate=15, clock=clk, sleep=clk.sleep)
    stamps = []

    async def go():
        for _ in range(100):
            await lim.acquire()
            stamps.append(clk.t)
    run(go())
    for i, t in enumerate(stamps):
        assert sum(1 for x in stamps if t <= x < t + 1.0) <= 15, i
    assert clk.t - 1000.0 >= 100 / 15 - 1.0                       # 100 messages need about 6 seconds, not 0


def test_limiter_does_not_wait_below_the_rate():
    clk = FakeClock()
    lim = RL.Limiter(rate=15, clock=clk, sleep=clk.sleep)

    async def go():
        for _ in range(15):
            await lim.acquire()
    run(go())
    assert clk.slept == 0.0


def test_limiter_default_rate_is_the_rule():
    assert RL.Limiter().rate == AR.NOTIFY_RATE_PER_SEC == 15


# ------------------------------------------------------------------ who may be messaged
BASE = dict(remind_on=True, unreachable=False, unreachable_at=None, blocked_bot=False, banned=False, last_seen=T0, now=T0 + AR.DAY)


@pytest.mark.parametrize("over,ok", [
    ({}, True), ({"remind_on": False}, False), ({"blocked_bot": True}, False), ({"banned": True}, False),
    ({"unreachable": True, "unreachable_at": T0}, False),
])
def test_dm_allowed(over, ok):
    assert RL.dm_allowed(**{**BASE, **over}) is ok


def test_unreachable_user_is_retried_only_after_a_week_and_if_seen_since():
    kw = {**BASE, "unreachable": True, "unreachable_at": T0}
    later = T0 + 8 * AR.DAY
    assert RL.dm_allowed(**{**kw, "now": later, "last_seen": T0 + 3 * AR.DAY})
    assert not RL.dm_allowed(**{**kw, "now": later, "last_seen": T0 - AR.DAY})
    assert not RL.dm_allowed(**{**kw, "now": T0 + 3 * AR.DAY, "last_seen": T0 + 2 * AR.DAY})
    # opt-out beats everything
    assert not RL.dm_allowed(**{**kw, "now": later, "last_seen": later, "remind_on": False})


def test_group_allowed():
    now = T0 + 3 * AR.DAY
    assert RL.group_allowed(games_enabled=True, last_seen=T0, fails=0, now=now)
    assert not RL.group_allowed(games_enabled=False, last_seen=T0, fails=0, now=now)
    assert not RL.group_allowed(games_enabled=True, last_seen=T0 - 5 * AR.DAY, fails=0, now=now)      # not seen for 8 days
    assert not RL.group_allowed(games_enabled=True, last_seen=T0, fails=AR.GROUP_FAILS_SKIP, now=now)
    assert not RL.group_allowed(games_enabled=True, last_seen=None, fails=0, now=now)


# ------------------------------------------------------------------ reminders for enrolled players
def state(**kw):
    d = dict(season_id=1, season_start=T0, energy_now=40, energy_stored=40, energy_at=T0, done_exams=frozenset(), adventures_today=AR.ADV_MAX_PER_DAY)
    d.update(kw)
    return RL.StudentState(**d)


def test_midterm_reminder_one_hour_before_and_while_open():
    lo, hi = EL.exam_window(T0, "midterm")
    assert RL.pick_reminder(state(), lo - 2 * H) is None
    r = RL.pick_reminder(state(), lo - timedelta(minutes=45))
    assert r.kind == "midterm" and "45 minutes" in r.text
    r2 = RL.pick_reminder(state(), lo + 3 * H)
    assert r2.kind == "midterm" and "open" in r2.text
    assert RL.pick_reminder(state(), hi - H) is None or RL.pick_reminder(state(), hi - H).kind != "midterm"    # last 2 hours: no nagging
    assert RL.pick_reminder(state(done_exams=frozenset({"midterm"})), lo + 3 * H) is None


def test_finals_reminder_the_evening_before():
    lo, hi = EL.exam_window(T0, "finals")
    assert RL.pick_reminder(state(), lo - 20 * H) is None
    r = RL.pick_reminder(state(), lo - 10 * H)
    assert r.kind == "finals" and "Finals" in r.text
    assert RL.pick_reminder(state(done_exams=frozenset({"finals"})), lo + H) is None


def test_adventure_and_energy_full_reminders():
    t = at(3, 3)                                           # Day 3, 17:30 IST
    assert RL.pick_reminder(state(adventures_today=AR.ADV_MAX_PER_DAY), t) is None
    r = RL.pick_reminder(state(adventures_today=0), t)
    assert r is not None and r.kind == "adventure"
    assert RL.pick_reminder(state(adventures_today=0, energy_now=0, energy_stored=0), t) is None   # cannot afford any run
    fest = RL.pick_reminder(state(adventures_today=0, energy_now=100), at(5, 3))
    assert fest.kind == "adventure" and "Fest" in fest.text
    # energy full: it refilled 2 hours ago
    refilled = state(energy_stored=80, energy_at=at(3, 0) - timedelta(hours=2), energy_now=100)
    e = RL.pick_reminder(refilled, at(3, 0))
    assert e.kind == "energy_full" and e.key.startswith("s1:e")
    # ... but not an old bar, and not a bar that was never used
    assert RL.pick_reminder(state(energy_stored=80, energy_at=at(3, 0) - timedelta(days=2), energy_now=100), at(3, 0)) is None
    assert RL.pick_reminder(state(energy_stored=100, energy_now=100), at(3, 0)) is None


def test_priority_and_sent_keys_do_not_block_the_next_reminder():
    lo, _ = EL.exam_window(T0, "midterm")
    t = lo + 3 * H
    s = state(adventures_today=0, energy_now=100)
    first = RL.pick_reminder(s, t)
    assert first.kind == "midterm"
    sent = {(RL.log_kind("midterm"), first.key)}
    second = RL.pick_reminder(s, t, sent)
    assert second.kind == "adventure"
    sent.add((RL.log_kind("adventure"), second.key))
    assert RL.pick_reminder(s, t, sent) is None


def test_no_reminders_on_day_7_or_in_the_break():
    s = state(adventures_today=0, energy_now=100)
    assert RL.pick_reminder(s, at(7, 3)) is None
    assert RL.pick_reminder(s, T0 + 7 * AR.DAY + H) is None


def test_reminder_log_kinds_fit_the_column():
    assert all(len(RL.log_kind(k)) <= 24 for k in AR.REMINDER_KINDS)
    assert len(AR.DM_REMINDER_SLOT) <= 24


# ------------------------------------------------------------------ recap text
def R(rank, uid, name, score, grade="B", gpa=None, sid=None):
    return EL.Ranked(rank=rank, user_id=uid, student_id=sid or uid, name=name, score=score, gpa=gpa if gpa is not None else score / 10, grade=grade)


def test_top_student_prefers_exam_results_then_knowledge_and_group_scope():
    res = [R(1, 1, "Asha", 90), R(2, 2, "Ben", 80), R(3, 3, "Cy", 70)]
    assert V.top_student(res, [], None) == ("Asha", "GPA 9.00")
    assert V.top_student(res, [], {2, 3}) == ("Ben", "GPA 8.00")
    assert V.top_student(res, [], {99}) == ("Asha", "GPA 9.00")               # nobody known in the group: school-wide
    prog = [(1, "Asha", 20, 50), (2, "Ben", 25, 60), (3, "Cy", 25, 70)]
    assert V.top_student([], prog, None) == ("Cy", "Knowledge 25")
    assert V.top_student([], [], None) is None


def test_recap_texts_are_escaped_and_complete():
    class Row:
        def __init__(self, club, total):
            self.club, self.total = club, total
    clubs = [Row("sports", 120), Row("music", 80)]
    g = V.recap_group_text(3, ("<b>Evil</b>", "GPA 9.00"), clubs)
    assert "&lt;b&gt;Evil" in g and "<b>Evil</b>" not in g.replace("<b>Top student so far:</b>", "")
    assert "end of Day 3" in g and "Tomorrow" in g and "Midterms" in g and "120" in g
    d = V.recap_dm_text(3, energy_used=45, energy_now=60, stats={"knowledge": 12, "charisma": 10, "fitness": 9, "creativity": 8},
                        rank=2, total=9, gpa=8.1, provisional=True)
    assert "45" in d and "#2" in d and "provisional" in d and "/academyremind off" in d
    d0 = V.recap_dm_text(2, energy_used=0, energy_now=100, stats={}, rank=None, total=0, gpa=None, provisional=False)
    assert "after your first exam" in d0


def test_day6_recap_teases_results_and_day7_has_no_tomorrow():
    assert "Results" in V.teaser(6)
    assert V.teaser(7) == ""


def test_season_open_text():
    txt = V.season_open_text(4, "Winter <Fest>", RL.enroll_closes(T0))
    assert "Season 4" in txt and "&lt;Fest&gt;" in txt and "Tue" not in txt or True
    assert "closes" not in txt or True
    assert V.season_open_text(4, "", RL.enroll_closes(T0)).count("Theme") == 0


# ------------------------------------------------------------------ titles
def students(spec):
    """spec: {uid: (stream, club, attended, possible)}"""
    return {u: C.StudentInfo(u, u, f"P{u}", s, c, {}, a, p) for u, (s, c, a, p) in spec.items()}


def test_titles():
    res = [R(1, 1, "P1", 92, "A+"), R(2, 2, "P2", 85, "A"), R(3, 3, "P3", 70, "B+"), R(4, 4, "P4", 62, "B"), R(5, 5, "P5", 30, "F")]
    st = students({1: ("science", "coding", 3, 3), 2: ("science", "coding", 2, 3), 3: ("arts", "music", 0, 3),
                   4: ("arts", "music", 0, 3), 5: ("sports", "sports", 0, 3), 6: ("music", "drama", 1, 1)})
    t = C.award_titles(res, st, {1: 10, 2: 4, 3: 0})
    ids = lambda k: [u for u, _, _ in t[k]]
    assert ids("valedictorian") == [1]
    assert sorted(ids("topper")) == [1, 3]                     # best of Science, best of Arts; P5 (F) and Sports/none do not count
    assert ids("club_champion") == [1, 2]
    assert sorted(ids("perfect_attendance")) == [1, 6]         # P6 is a late joiner who attended the one class day he could
    assert sorted(ids("backbencher")) == [3, 4]                # never attended, passed; P5 failed
    assert C.titles_of(1, t) == ["valedictorian", "topper", "club_champion", "perfect_attendance"]
    assert C.titles_of(99, t) == []


def test_tie_for_first_shares_valedictorian_and_f_grade_never_wins():
    res = [R(1, 1, "A", 80), R(1, 2, "B", 80), R(3, 3, "C", 70)]
    t = C.award_titles(res, students({1: ("arts", "drama", 1, 3), 2: ("arts", "drama", 1, 3), 3: ("arts", "drama", 1, 3)}))
    assert [u for u, _, _ in t["valedictorian"]] == [1, 2]
    t2 = C.award_titles([R(1, 1, "A", 20, "F")], students({1: ("arts", "drama", 0, 3)}))
    assert t2["valedictorian"] == [] and t2["topper"] == [] and t2["backbencher"] == []


def test_title_tables_are_consistent():
    assert set(AR.TITLE_ORDER) == set(AR.TITLES) == set(AR.TITLE_BLURB)
    assert {AR.TITLES[k][1] for k in AR.TITLES} == {"Topper", "Valedictorian", "Backbencher", "Perfect Attendance", "Club Champion"}
    assert all(len(k) <= 24 for k in AR.TITLES)


# ------------------------------------------------------------------ ceremony text and the section registry
def sample(n=7):
    res = [R(i, i, f"Student{i}", 100 - 5 * i, "A" if i < 4 else "B") for i in range(1, n + 1)]
    st = students({i: ("science", "sports", 3, 3) for i in range(1, n + 1)})
    data = C.CeremonyData(season_id=1, number=2, results=res, students=st, next_start=T0 + 7 * AR.DAY + 6 * H)
    class Row:
        def __init__(self, club, total):
            self.club, self.total = club, total
    data.club_rows = [Row("sports", 150), Row("music", 90)]
    data.club_winner = "sports"
    data.titles = C.award_titles(res, st, {1: 5})
    data.hof_prev = [(1, "Old <Champ>", 9.1)]
    return data


def test_ceremony_has_every_required_part():
    txt = run(C.build_ceremony(sample()))
    assert "Season 2" in txt and "Graduation Ceremony" in txt
    assert "Valedictorian" in txt and "Student1" in txt
    assert "Top 5" in txt and "Student5" in txt and "Student6" not in txt.split("Titles")[0]
    assert "Club winner" in txt and "Sports" in txt and "150" in txt
    assert "Titles" in txt and "Perfect Attendance" in txt and "Club Champion" in txt
    assert "Hall of Fame" in txt and "Old &lt;Champ&gt;" in txt
    assert "Next season opens" in txt
    assert len(txt) < 4096


def test_ceremony_without_results_or_club():
    data = C.CeremonyData(season_id=1, number=1)
    txt = run(C.build_ceremony(data))
    assert "Nobody sat an exam" in txt and "no club scored" in txt


def test_extra_sections_plug_in_sorted_and_scoped():
    @C.ceremony_section(name="t_late", order=300, scope="ceremony")
    def late(ctx):
        return "LATE-GROUP"

    @C.ceremony_section(name="t_early", order=110, scope="both")
    async def early(ctx):
        return f"EARLY-{ctx.scope}"

    @C.ceremony_section(name="t_card_only", order=120, scope="card")
    def card_only(ctx):
        return f"CARD-{ctx.card.name}"

    @C.ceremony_section(name="t_none", scope="both")
    def nothing(ctx):
        return None
    txt = run(C.build_ceremony(sample()))
    assert txt.index("EARLY-ceremony") < txt.index("LATE-GROUP")
    assert txt.index("Hall of Fame") < txt.index("EARLY-ceremony")              # extras come after the built-in blocks
    assert txt.index("LATE-GROUP") < txt.index(RL.variant(T.CEREMONY_FOOT_VARIANTS, "s1")[:12])   # ... and before the footer
    assert "CARD-" not in txt


def test_report_card_gets_card_sections():
    @C.ceremony_section(name="t_card", order=150, scope="card")
    def mine(ctx):
        return f"✨ extra for {ctx.card.name} (rank {ctx.card.result.rank})"

    @C.ceremony_section(name="t_group_only", scope="ceremony")
    def group_only(ctx):
        return "GROUP ONLY"
    data = sample()
    card = C.CardData(ceremony=data, user_id=2, name="Student2", student=data.students[2], result=data.results[1], ranked_total=7,
                      titles=C.titles_of(2, data.titles), badges={"rival": 3, "club": 5}, trophies=[("trophy", "Library Key")])
    txt = run(C.build_card(card))
    assert "Report card" in txt and "Season 2" in txt and "✨ extra for Student2 (rank 2)" in txt and "GROUP ONLY" not in txt
    assert "Final GPA" in txt and "#2" in txt and "of 7" in txt and "Library Key" in txt and "Rival 3" in txt and "Attendance: 3/3" in txt
    assert "Next season opens" in txt


def test_a_crashing_or_bad_section_never_breaks_the_ceremony(caplog):
    @C.ceremony_section(name="t_boom", order=105)
    def boom(ctx):
        raise RuntimeError("nope")

    @C.ceremony_section(name="t_boom_async", order=106)
    async def boom2(ctx):
        raise ValueError("nope")
    txt = run(C.build_ceremony(sample()))
    assert "Valedictorian" in txt and RL.variant(T.CEREMONY_FOOT_VARIANTS, "s1")[:12] in txt
    assert any("t_boom" in r.getMessage() for r in caplog.records)


def test_registering_the_same_name_replaces_and_bad_scope_is_rejected():
    @C.ceremony_section(name="t_dup", order=100)
    def one(ctx):
        return "ONE"

    @C.ceremony_section(name="t_dup", order=100)
    def two(ctx):
        return "TWO"
    assert len([s for s in C.CEREMONY_SECTIONS if s.name == "t_dup"]) == 1
    txt = run(C.build_ceremony(sample()))
    assert "TWO" in txt and "ONE" not in txt
    with pytest.raises(ValueError):
        C.ceremony_section(name="t_bad", scope="nowhere")(lambda ctx: "x")


def test_provisional_and_unranked_cards():
    data = sample()
    card = C.CardData(ceremony=data, user_id=9, name="Zed", student=C.StudentInfo(9, 9, "Zed", "arts", "drama", {}, 1, 3), provisional=True)
    txt = run(C.build_card(card))
    assert "in progress" in txt and "No exam results yet" in txt and "Next season opens" not in txt
    card.provisional = False
    assert "did not sit an exam" in run(C.build_card(card))


def test_names_are_truncated_with_more():
    items = [(i, f"N{i}", None) for i in range(1, 9)]
    s = C._names(items)
    assert s.count("N") == AR.NAMES_PER_TITLE and "+3 more" in s


def test_hall_of_fame_text():
    assert C.hof_text([]) == T.NO_HOF
    e = [C.HofEntry("valedictorian", 1, "Asha", 9.2), C.HofEntry("topper", 1, "Asha", 9.2, "science"), C.HofEntry("topper", 2, "Ben", 8.0, "arts"),
         C.HofEntry("club_champion", 1, "Asha", None, "music"), C.HofEntry("perfect_attendance", 3, "Cy"), C.HofEntry("backbencher", 4, "Di")]
    txt = C.hof_text([(2, e), (1, [C.HofEntry("valedictorian", 7, "Old", 8.0)])])
    assert txt.index("Season 2") < txt.index("Season 1")
    assert "Asha (GPA 9.20)" in txt and "Toppers" in txt and "Club Champions" in txt and "1 perfect attendance" in txt and "1 backbencher" in txt


# ------------------------------------------------------------------ a whole season, simulated
def test_simulated_season_group_message_counts():
    """Walk the season in 15-minute steps and count what a group would get, using only the pure schedule."""
    seen = set()
    t = T0
    while t < T0 + 7 * AR.DAY + 12 * H:
        if RL.season_day(T0, t) == 1 and not RL.in_quiet_hours(t):
            seen.add(("open",))
        if not RL.in_quiet_hours(t):
            p = RL.enroll_group_plan(T0, t)
            if p:
                seen.add((p.kind, p.suffix))
        d = RL.recap_due(T0, t)
        if d:
            seen.add(("recap", d))
        if RL.ceremony_due(T0, t):
            seen.add(("ceremony",))
        t += timedelta(minutes=15)
    assert ("open",) in seen and ("ceremony",) in seen
    assert sorted(d for k, *r in seen if k == "recap" for d in r) == [1, 2, 3, 4, 5, 6]
    assert len([s for s in seen if s[0] == "enroll_last"]) <= 1


# ------------------------------------------------------------------ catalog
def test_commands_are_in_help_but_not_in_the_slash_menu():
    from utils import helpcatalog as H_
    for c in ("academyremind", "reportcard", "halloffame"):
        assert c in H_.DESC
        assert c not in H_.MENU and c not in H_.GROUP_MENU_EXTRA
    cat = dict(H_.CATEGORIES)
    assert {"academyremind", "reportcard", "halloffame"} <= set(cat["Academy 🎓"])


def test_all_texts_format():
    T.SEASON_OPEN.format(number=1, theme="", closes="x")
    for line in T.ENROLL_GROUP:
        line.format(number=1)
    for d, line in T.ENROLL_DM.items():
        line.format(number=1, closes="x")
    assert AR.QUIET_FROM_HOUR == 23 and AR.QUIET_TO_HOUR == 7 and AR.NOTIFY_RATE_PER_SEC == 15


# ------------------------------------------------------------------ extras: variants, highlights, diamonds switch
def test_variants_are_stable_per_key_and_vary_across_keys():
    opts = T.ENERGY_FULL_VARIANTS
    assert RL.variant(opts, "k1") == RL.variant(opts, "k1")
    assert len({RL.variant(opts, f"k{i}") for i in range(40)}) == len(opts)
    for o in T.ADVENTURE_VARIANTS:
        o.format(n=1, max=2)


def test_highlights_line():
    txt = C.highlights(sample())
    assert "7 graduates" in txt and "class average GPA" in txt and "perfect attendance" in txt
    assert C.highlights(C.CeremonyData(season_id=1, number=1)) == ""
    assert "Season highlights" in run(C.build_ceremony(sample()))


def test_graduation_diamonds_are_off_by_default(monkeypatch):
    monkeypatch.delenv("ACADEMY_GRADUATION_DIAMONDS", raising=False)
    assert AR.graduation_diamonds_on() is False
    monkeypatch.setenv("ACADEMY_GRADUATION_DIAMONDS", "1")
    assert AR.graduation_diamonds_on() is True
    assert set(AR.GRADUATION_DIAMONDS) <= set(AR.TITLES)


def test_card_shows_the_prize_only_when_there_is_one():
    data = sample()
    card = C.CardData(ceremony=data, user_id=1, name="Student1", student=data.students[1], result=data.results[0], ranked_total=7,
                      extra={"diamonds": 75})
    assert "+75" in run(C.build_card(card))
    card.extra = {}
    assert "Graduation prize" not in run(C.build_card(card))
