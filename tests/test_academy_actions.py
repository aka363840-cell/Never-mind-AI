"""Academy Life, Part 2 (daily life), PURE half: gates, limits, streak, effects, diamond cap, GIF choice, message.
No database, no Telegram. Run: pytest tests/test_academy_actions.py"""
import random

import pytest

from game import academy_rules as AR
from services import academy_actions as A
from utils.content import academy_texts as T

SFW_GIFS = {"wave", "highfive", "dance", "blush", "cry", "facepalm", "laugh", "wink"}


class Fixed:
    """A 'random' that always rolls the top of every range (or the first item), for exact numbers."""
    def __init__(self, high=True, p=0.99):
        self.high, self.p = high, p

    def randint(self, a, b):
        return b if self.high else a

    def random(self):
        return self.p

    def choice(self, seq):
        return seq[0]


# ------------------------------------------------------------------ rules sanity
def test_rules_are_consistent():
    assert set(AR.ACTIONS) == {"study", "attend", "hangout", "rest", "club", "parttime"}
    for a in AR.ACTIONS.values():
        assert 0 <= a.energy <= AR.ENERGY_MAX and a.cooldown_s > 0 and a.daily_limit >= 1
        assert a.days and set(a.days) <= set(AR.ALL_DAYS)
    assert AR.ACTIONS["rest"].energy == 0 and all(AR.ACTIONS[k].energy > 0 for k in ("study", "attend", "hangout", "club", "parttime"))
    assert AR.ACTIONS["attend"].days == AR.CLASS_DAYS and set(AR.CLASS_DAYS) <= {2, 3, 4, 5, 6}
    assert AR.FINALS_DAY == 6 and AR.FINALS_DAY not in AR.ACTIONS["parttime"].days
    assert set(AR.ALL_DAYS) - {AR.FINALS_DAY} == set(AR.ACTIONS["parttime"].days)
    for k in ("study", "hangout", "rest", "club"):
        assert AR.ACTIONS[k].days == AR.ALL_DAYS
    assert set(AR.CLUB_STAT) == set(AR.CLUBS) and set(AR.CLUB_STAT.values()) <= {"fitness", "creativity"}
    assert set(AR.STREAM_PERKS) == set(AR.STREAMS)
    assert set(AR.GIF_MOMENTS.values()) <= SFW_GIFS
    assert AR.cooldown_key("study") == "academy:study" and len(AR.cooldown_key("parttime")) < 80
    assert all(AR.STREAK_MILESTONES[k] > 0 for k in AR.STREAK_MILESTONES) and set(AR.STREAK_MILESTONES) <= set(AR.ALL_DAYS)


def test_every_perk_has_a_flavour_note_and_valid_stats():
    for stream, perk in AR.STREAM_PERKS.items():
        for action, stats in perk.stats.items():
            assert action in AR.ACTIONS and set(stats) <= set(AR.STAT_KEYS)
        for action in AR.perk_actions(stream):
            assert (stream, action) in T.STREAM_NOTES, (stream, action)
    assert set(T.STREAM_NOTES) == {(s, a) for s in AR.STREAMS for a in AR.perk_actions(s)}     # no orphan notes either
    assert AR.STREAM_PERKS["commerce"].pay_pct > 0 and AR.STREAM_PERKS["sports"].rest_bonus > 0


def test_flavour_texts_are_plentiful_unique_and_html_safe():
    banks = {"study": T.STUDY_LINES, "attend": T.ATTEND_LINES, "hangout": T.HANGOUT_LINES, "rest": T.REST_LINES,
             "club": T.CLUB_LINES, "parttime": T.PARTTIME_LINES}
    assert set(banks) == set(AR.ACTIONS)
    for name, lines in banks.items():
        assert len(lines) >= 10 and len(set(lines)) == len(lines), name
    for club in AR.CLUBS:
        assert len(T.CLUB_EXTRA[club]) >= 3
    every = [x for b in banks.values() for x in b] + [x for v in T.CLUB_EXTRA.values() for x in v]
    for line in every:
        assert line.strip() and not any(ch in line for ch in "<>&"), line
    assert set(T.VERBS) == set(AR.ACTIONS) and set(T.STREAK_LINES) == set(AR.STREAK_MILESTONES)


def test_day_hints_point_at_the_new_commands():
    text = " ".join(h for hints in T.DAY_HINTS.values() for h in hints)
    for cmd in ("/study", "/attend", "/hangout", "/club", "/rest", "/parttime"):
        assert cmd in text, cmd
    assert set(T.DAY_HINTS) == set(AR.ALL_DAYS)


# ------------------------------------------------------------------ phase gating (pure)
def test_attend_only_on_class_days():
    for day in AR.ALL_DAYS:
        msg = A.day_block("attend", day)
        assert (msg is None) == (day in (2, 3, 4)), day
    assert "class days" in A.day_block("attend", 5) and "2, 3, 4" in A.day_block("attend", 1)


def test_parttime_closed_on_finals_day_only():
    for day in AR.ALL_DAYS:
        assert (A.day_block("parttime", day) is None) == (day != 6), day
    assert "Finals" in A.day_block("parttime", 6)
    for key in ("study", "hangout", "rest", "club"):
        assert all(A.day_block(key, d) is None for d in AR.ALL_DAYS)


# ------------------------------------------------------------------ daily limits and the diamond cap
def test_daily_limits():
    c = A.DailyCounts()
    for key, act in AR.ACTIONS.items():
        assert A.limit_block(key, c) is None
        setattr(c, key, act.daily_limit - 1)
        assert A.limit_block(key, c) is None
        setattr(c, key, act.daily_limit)
        msg = A.limit_block(key, c, "5h")
        assert msg and ("5h" in msg or key == "attend"), key
    assert "already attended" in A.limit_block("attend", A.DailyCounts(attend=1))


def test_cap_pay_never_exceeds_the_daily_cap():
    cap = AR.DAILY_DIAMOND_CAP
    assert A.cap_pay(30, 0) == 30
    assert A.cap_pay(30, cap - 10) == 10                  # partial payout at the cap
    assert A.cap_pay(30, cap) == 0 and A.cap_pay(30, cap + 50) == 0
    assert A.diamonds_left(A.DailyCounts(diamonds=cap - 1)) == 1 and A.diamonds_left(A.DailyCounts(diamonds=cap + 5)) == 0


def test_max_diamonds_per_day_for_every_stream():
    """Simulate a player who does every allowed shift with the best possible rolls: never more than the cap per day."""
    limit = AR.ACTIONS["parttime"].daily_limit
    for stream in AR.STREAMS:
        counts = A.DailyCounts()
        for _ in range(limit + 5):                        # even if the shift limit did not exist
            if A.diamonds_left(counts) <= 0:
                break
            p = A.plan("parttime", stream=stream, club="coding", rng=Fixed(high=True))
            counts.diamonds += A.cap_pay(p.pay_roll, counts.diamonds)
        assert counts.diamonds <= AR.DAILY_DIAMOND_CAP, stream
    assert AR.DAILY_DIAMOND_CAP == 100                    # the number written in docs/ACADEMY_PROGRESS.md
    best_week = AR.DAILY_DIAMOND_CAP * len(AR.ACTIONS["parttime"].days)
    assert best_week == 600                               # most a player can ever earn in a season


# ------------------------------------------------------------------ streak
def test_study_streak():
    assert A.study_streak([], 3) == 0 and A.study_streak([], 1) == 0
    assert A.study_streak({1, 2, 3}, 3) == 3
    assert A.study_streak({1, 2, 3}, 4) == 0              # not studied today (4) -> no streak yet
    assert A.study_streak({1, 2, 4}, 4) == 1              # a gap breaks it
    assert A.study_streak({1, 2, 3, 4, 5, 6, 7}, 7) == 7
    assert A.study_streak({2, 3} | {4}, 4) == 3           # callers add 'today' before studying
    assert A.study_streak({1, 2, 3, 5}, 5) == 1


def test_best_streak_and_milestones():
    assert A.best_streak([]) == 0 and A.best_streak([2]) == 1
    assert A.best_streak([1, 2, 3, 5, 6]) == 3 and A.best_streak({7, 5, 6, 1}) == 3
    assert [A.streak_bonus(n) for n in range(0, 9)] == [0, 0, 0, 1, 0, 2, 0, 3, 0]


def test_milestone_bonus_only_on_the_first_study_of_a_day():
    first = A.plan("study", stream="music", club="music", rng=Fixed(), streak=3, first_study_today=True)
    again = A.plan("study", stream="music", club="music", rng=Fixed(), streak=3, first_study_today=False)
    assert first.milestone == 3 and first.stats == {"knowledge": 3 + 1}
    assert again.milestone == 0 and again.stats == {"knowledge": 3}                      # repeating /study never pays it twice
    none = A.plan("study", stream="music", club="music", rng=Fixed(), streak=4, first_study_today=True)
    assert none.milestone == 0 and none.stats == {"knowledge": 3}
    seven = A.plan("study", stream="music", club="music", rng=Fixed(), streak=7, first_study_today=True)
    assert seven.stats == {"knowledge": 3 + 3} and seven.milestone == 7


# ------------------------------------------------------------------ effects
def test_study_gain_range_and_stream_perk():
    for seed in range(60):
        rng = random.Random(seed)
        p = A.plan("study", stream="music", club="coding", rng=rng)
        assert AR.STUDY_GAIN[0] <= p.stats["knowledge"] <= AR.STUDY_GAIN[1] and set(p.stats) == {"knowledge"} and p.perk is None
        s = A.plan("study", stream="science", club="coding", rng=random.Random(seed))
        assert s.stats["knowledge"] == p.stats["knowledge"] + 1 and s.perk == T.STREAM_NOTES[("science", "study")]
    assert A.plan("study", stream="arts", club="drama", rng=Fixed()).perk is None


def test_attend_gives_small_knowledge():
    p = A.plan("attend", stream="music", club="music", rng=Fixed())
    assert p.stats == {"knowledge": AR.ATTEND_GAIN} and p.line in T.ATTEND_LINES
    assert A.plan("attend", stream="science", club="music", rng=Fixed()).stats == {"knowledge": AR.ATTEND_GAIN + 1}
    assert A.plan("attend", stream="arts", club="music", rng=Fixed()).stats == {"knowledge": 1, "creativity": 1}


def test_hangout_raises_charisma_and_sometimes_creativity():
    p = A.plan("hangout", stream="sports", club="sports", rng=Fixed(p=0.99))
    assert p.stats == {"charisma": AR.HANGOUT_CHARISMA[1]}
    p = A.plan("hangout", stream="sports", club="sports", rng=Fixed(p=0.0))
    assert p.stats == {"charisma": AR.HANGOUT_CHARISMA[1], "creativity": 1}
    seen = {"creativity" in A.plan("hangout", stream="sports", club="sports", rng=random.Random(i)).stats for i in range(80)}
    assert seen == {True, False}                          # 'sometimes'
    assert A.plan("hangout", stream="commerce", club="coding", rng=Fixed(p=0.99)).stats["charisma"] == AR.HANGOUT_CHARISMA[1] + 1


def test_club_stat_depends_on_the_club():
    for club, stat in AR.CLUB_STAT.items():
        p = A.plan("club", stream="commerce", club=club, rng=Fixed())
        assert p.club_points == AR.CLUB_POINTS[1] and p.stats == {stat: AR.CLUB_STAT_GAIN[1]}, club
    assert A.plan("club", stream="sports", club="sports", rng=Fixed()).stats == {"fitness": AR.CLUB_STAT_GAIN[1] + 1}
    for seed in range(40):
        p = A.plan("club", stream="arts", club="music", rng=random.Random(seed))
        assert AR.CLUB_POINTS[0] <= p.club_points <= AR.CLUB_POINTS[1]
        assert p.line in T.CLUB_LINES + T.CLUB_EXTRA["music"]


def test_rest_restores_energy_and_sports_bonus():
    assert A.plan("rest", stream="music", club="music", rng=Fixed()).energy_restore == AR.REST_ENERGY
    sp = A.plan("rest", stream="sports", club="music", rng=Fixed())
    assert sp.energy_restore == AR.REST_ENERGY + AR.STREAM_PERKS["sports"].rest_bonus and sp.perk


def test_parttime_pay_range_and_commerce_bonus():
    for seed in range(40):
        p = A.plan("parttime", stream="arts", club="drama", rng=random.Random(seed))
        assert AR.PARTTIME_PAY[0] <= p.pay_roll <= AR.PARTTIME_PAY[1] and p.perk is None
    hi = A.plan("parttime", stream="commerce", club="drama", rng=Fixed(high=True))
    assert hi.pay_roll == AR.PARTTIME_PAY[1] * 120 // 100 and hi.perk == T.STREAM_NOTES[("commerce", "parttime")]


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError):
        A.plan("nap", stream="arts", club="drama")


# ------------------------------------------------------------------ GIF moments
def test_gif_moments_and_priority():
    g = AR.GIF_MOMENTS
    assert A.choose_gif("study", milestone=3, energy_left=80) == g["streak"] == "highfive"
    assert A.choose_gif("study", milestone=0, energy_left=80) is None
    assert A.choose_gif("attend", first_attend=True, energy_left=80) == "wave"
    assert A.choose_gif("attend", first_attend=False, energy_left=80) is None
    assert A.choose_gif("club", energy_left=AR.LOW_ENERGY_GIF) == "facepalm"
    assert A.choose_gif("club", energy_left=AR.LOW_ENERGY_GIF + 1) is None
    assert A.choose_gif("rest", energy_left=5) is None                              # resting is never a facepalm moment
    assert A.choose_gif("hangout", energy_left=80, rng=Fixed(p=0.0)) == "laugh"
    assert A.choose_gif("hangout", energy_left=80, rng=Fixed(p=0.999)) is None      # 50% by default
    assert A.choose_gif("study", milestone=5, energy_left=3) == "highfive"          # milestone beats low Energy
    assert A.choose_gif("attend", first_attend=True, energy_left=3) == "wave"
    assert A.choose_gif("hangout", energy_left=3, rng=Fixed(p=0.0)) == "facepalm"  # low Energy beats laugh
    for key in AR.ACTIONS:
        for rng in (Fixed(p=0.0), Fixed(p=0.999)):
            assert A.choose_gif(key, milestone=3, first_attend=True, energy_left=1, rng=rng) in SFW_GIFS | {None}


# ------------------------------------------------------------------ message
def _result(**kw):
    base = dict(key="study", day=2, line="You read <b>fast</b> & well.", stream="science", club="coding",
                gains={"knowledge": 3}, stats={"knowledge": 18, "charisma": 10, "fitness": 10, "creativity": 12},
                energy=85, energy_bar="▰▰▰▰▰▰▰▰▱▱", energy_delta=-15, used=1, limit=6)
    base.update(kw)
    return A.ActionResult(**base)


def test_render_study_message():
    txt = A.render(_result(streak=3, milestone=True, perk="🔬 Science perk."))
    assert "📚 <b>Study</b>" in txt and "Knowledge" in txt and "+3" in txt and "→ 18" in txt
    assert "3-day study streak" in txt and "🔬 Science perk." in txt
    assert "85/100" in txt and "−15" in txt and "Today: Study 1/6" in txt
    assert "&lt;b&gt;fast&lt;/b&gt; &amp; well" in txt and "<b>fast</b>" not in txt        # flavour text is escaped
    assert len(txt) < 900                                                                  # fits a GIF caption (1000)
    plain = A.render(_result(streak=2))
    assert "Study streak: <b>2</b>" in plain and "3-day" not in plain


def test_render_other_actions():
    club = A.render(_result(key="club", gains={"fitness": 2}, club="sports", club_points=4, club_total=21, club_rank=2,
                            club_count=4, used=1, limit=4))
    assert "Sports Club" in club and "+4" in club and "#2 of 4" in club and "Fitness" in club
    job = A.render(_result(key="parttime", gains={}, paid=40, diamonds_today=90, energy_delta=-20, energy=60))
    assert "40</b> diamonds" in job and f"90/{AR.DAILY_DIAMOND_CAP}" in job and "cap reached" not in job
    assert "cap reached" in A.render(_result(key="parttime", gains={}, paid=10, capped=True, diamonds_today=100))
    rest = A.render(_result(key="rest", gains={}, energy=100, energy_delta=20, used=1, limit=3))
    assert "+20" in rest and "maxed" not in rest
    att = A.render(_result(key="attend", first_attend=True, gains={"knowledge": 1}, limit=1))
    assert "First attendance mark" in att and "Attendance marked" not in att
    assert "Attendance marked" in A.render(_result(key="attend", gains={"knowledge": 1}, limit=1))
    maxed = A.render(_result(key="study", gains={"knowledge": 0}))
    assert "maxed" in maxed


def test_action_blocked_is_a_game_error_with_enroll_flag():
    from services.errors import GameError
    e = A.ActionBlocked("hello", enroll=True)
    assert isinstance(e, GameError) and e.text == "hello" and e.enroll is True and str(e) == "hello"
    assert A.ActionBlocked("x").enroll is False
