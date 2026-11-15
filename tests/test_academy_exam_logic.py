"""Academy exams, Part 3, PURE half: card content, play_round, luck cap, grades, ranking, drawing, time limit."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from game import academy_rules as AR
from services import academy_exam_logic as L
from utils.content import academy_exam_rounds as CR

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ content
def test_enough_cards_and_unique_keys():
    assert len(CR.CARDS) >= 40
    assert len({c.key for c in CR.CARDS}) == len(CR.CARDS)
    assert len({c.title for c in CR.CARDS}) == len(CR.CARDS)


def test_every_card_is_valid():
    for c in CR.CARDS:
        assert 3 <= len(c.buttons) <= 4, c.key
        assert c.difficulty in (1, 2, 3), c.key
        assert c.stream is None or c.stream in AR.STREAMS, c.key
        assert c.title.strip() and c.text.strip(), c.key
        for b in c.buttons:
            assert b.stat in CR.STATS, (c.key, b.stat)
            assert b.risk in CR.RISKS, (c.key, b.risk)
            assert b.label.strip() and b.win.strip() and b.lose.strip(), c.key
            assert len(b.label) <= 50, (c.key, b.label)          # fits a Telegram button next to the icon
        assert len({b.label for b in c.buttons}) == len(c.buttons), c.key


def test_every_stream_has_cards_of_each_difficulty_and_general_pool_is_big():
    for st in AR.STREAMS:
        cards = CR.stream_cards(st)
        assert len(cards) >= 4 and {c.difficulty for c in cards} == {1, 2, 3}, st
    assert len(CR.GENERAL) >= 20
    for d in (1, 2, 3):
        assert sum(1 for c in CR.GENERAL if c.difficulty == d) >= 6


def test_no_real_subject_questions():
    banned = ("capital of", "what is the", "solve", "calculate", "formula", "equation", "which of the following", "?")
    for c in CR.CARDS:
        blob = (c.text + " " + " ".join(b.label for b in c.buttons)).lower()
        assert not any(w in blob for w in banned), c.key


def test_callback_data_fits_telegram_limit():
    assert len(f"acx:c:{9_999_999_999}:{999_999_999}:{8}:{3}".encode()) < 64
    assert len(f"acx:f:{9_999_999_999}:{999_999_999}:{8}".encode()) < 64


# ------------------------------------------------------------------ deterministic play_round
def strong():
    return L.ExamStudent("science", 70, 70, 70, 70, streak=5, attendance=1.0)


def weak():
    return L.ExamStudent("science", 10, 10, 10, 10)


def test_play_round_is_deterministic_with_a_seed():
    card = CR.BY_KEY["late_bus"]
    a = L.play_round(strong(), card, 0, random.Random(42))
    b = L.play_round(strong(), card, 0, random.Random(42))
    assert a == b
    assert 1 <= a.roll <= 100 and a.card_key == "late_bus" and a.choice == 0
    assert a.line in (card.buttons[0].win, card.buttons[0].lose)


def test_play_round_accepts_orm_like_student_and_button_object():
    class Row:                                       # an AcademyStudent has no streak / attendance
        stream, knowledge, charisma, fitness, creativity = "arts", 30, 30, 30, 30
    card = CR.BY_KEY["late_bus"]
    r = L.play_round(Row(), card, card.buttons[1], random.Random(1))
    assert r.choice == 1 and r.parts["streak"] == 0 and r.parts["attendance"] == 0
    with pytest.raises(ValueError):
        L.play_round(Row(), card, 9, random.Random(1))


def test_points_follow_risk():
    for risk, (win, lose) in AR.ROUND_POINTS.items():
        assert L.round_points(risk, True) == win and L.round_points(risk, False) == lose
    assert AR.ROUND_POINTS["risky"][0] > AR.ROUND_POINTS["balanced"][0] > AR.ROUND_POINTS["safe"][0]
    assert AR.ROUND_POINTS["risky"][1] < 0 <= AR.ROUND_POINTS["safe"][1]          # risky can lose points, safe never does


def test_a_strong_student_always_wins_balanced_and_a_weak_one_always_loses_it():
    card = CR.BY_KEY["group_project"]                 # difficulty 2
    bal = next(i for i, b in enumerate(card.buttons) if b.risk == "balanced")
    assert all(L.play_round(strong(), card, bal, random.Random(i)).success for i in range(60))
    assert not any(L.play_round(weak(), card, bal, random.Random(i)).success for i in range(60))


def test_risky_gives_bigger_gains_and_losses():
    card = CR.BY_KEY["group_project"]
    risky = next(i for i, b in enumerate(card.buttons) if b.risk == "risky")
    mid = L.ExamStudent("science", 62, 62, 62, 62, streak=3, attendance=.5)       # chance ~0.5: inside the luck band
    pts = {L.play_round(mid, card, risky, random.Random(i)).points for i in range(300)}
    assert pts == {AR.ROUND_POINTS["risky"][0], AR.ROUND_POINTS["risky"][1]}


# ------------------------------------------------------------------ luck cap
def test_luck_never_moves_the_chance_by_more_than_the_cap():
    for roll in range(1, 101):
        assert abs(L.luck_of(roll)) <= AR.LUCK_CAP + 1e-9
    assert L.luck_of(1) == pytest.approx(-AR.LUCK_CAP) and L.luck_of(100) == pytest.approx(AR.LUCK_CAP)
    assert AR.LUCK_CAP <= 0.15


def test_outside_the_luck_band_the_dice_cannot_change_the_outcome():
    """Whatever the dice say, a round whose chance is clear of the line (by more than the cap) has ONE outcome."""
    for card in CR.CARDS:
        for i, b in enumerate(card.buttons):
            for stu in (weak(), strong()):
                p, _, _ = L.chance_of(stu, card, b)
                outcomes = {L.play_round(stu, card, i, _Fixed(r)).success for r in range(1, 101)}
                if abs(p - AR.SUCCESS_LINE) > AR.LUCK_CAP:
                    assert len(outcomes) == 1, (card.key, i, p)
                    assert outcomes == {p > AR.SUCCESS_LINE}


class _Fixed:
    def __init__(self, v):
        self.v = v

    def randint(self, a, b):
        return self.v


def _play_exam(stu, kind, seed, strategy):
    rng = random.Random(seed)
    pts = []
    for k in L.draw_cards(stu.stream, kind, rng):
        card = L.pick(k)
        if strategy == "random":
            idx = rng.randrange(len(card.buttons))
        else:                                         # best expected points for this student
            def ev(b):
                p, _, _ = L.chance_of(stu, card, b)
                s = sum(1 for r in range(1, 101) if p + L.luck_of(r) >= AR.SUCCESS_LINE) / 100
                w, l = AR.ROUND_POINTS[b.risk]
                return s * w + (1 - s) * l
            idx = max(range(len(card.buttons)), key=lambda j: ev(card.buttons[j]))
        pts.append(L.play_round(stu, card, idx, rng).points)
    return L.exam_score(pts, AR.EXAM_ROUNDS[kind])


def test_preparation_beats_luck():
    """Players who prepare daily get better grades: even the WORST luck of a prepared player beats the BEST of an idle one."""
    prepared = L.ExamStudent("science", 65, 65, 65, 65, streak=4, attendance=1.0)
    idle = L.ExamStudent("science", 12, 12, 12, 12)
    for kind in AR.EXAM_KINDS:
        p = [_play_exam(prepared, kind, i, "best") for i in range(60)]
        q = [_play_exam(idle, kind, i, "best") for i in range(60)]
        assert min(p) > max(q), kind
        assert sum(p) / 60 > 80 > 50 > sum(q) / 60


def test_each_prep_ingredient_helps():
    card = CR.BY_KEY["group_project"]
    b = card.buttons[0]
    base = L.ExamStudent("commerce", 40, 40, 40, 40)
    p0 = L.chance_of(base, card, b)[0]
    assert L.chance_of(L.ExamStudent("commerce", 40, 40, 40, 40, streak=5), card, b)[0] > p0
    assert L.chance_of(L.ExamStudent("commerce", 40, 40, 40, 40, attendance=1.0), card, b)[0] > p0
    assert L.chance_of(L.ExamStudent("commerce", 60, 60, 60, 60), card, b)[0] > p0
    s_card = CR.BY_KEY["com_pitch"]                   # own-stream card: bonus
    assert L.chance_of(base, s_card, s_card.buttons[0])[0] > L.chance_of(L.ExamStudent("arts", 40, 40, 40, 40), s_card, s_card.buttons[0])[0]
    assert L.streak_bonus(99) == AR.STREAK_BONUS_MAX and L.attendance_bonus(5) == AR.ATTENDANCE_BONUS_MAX


# ------------------------------------------------------------------ grades / GPA
@pytest.mark.parametrize("score,grade", [(100, "A+"), (90, "A+"), (89.9, "A"), (80, "A"), (79.9, "B+"), (70, "B+"), (69.9, "B"),
                                         (60, "B"), (59.9, "C"), (50, "C"), (49.9, "D"), (40, "D"), (39.9, "F"), (0, "F")])
def test_grade_boundaries(score, grade):
    assert L.grade_of(score) == grade


def test_gpa_is_a_ten_point_scale_and_probation_starts_below_four():
    assert L.gpa_of(100) == 10.0 and L.gpa_of(0) == 0.0 and L.gpa_of(85.5) == 8.55
    assert L.gpa_of(250) == 10.0 and L.gpa_of(-5) == 0.0
    assert L.on_probation(39.9) and not L.on_probation(40.0) and not L.on_probation(None)
    assert L.grade_of(39.9) == "F" and L.gpa_of(39.9) < AR.PROBATION_GPA          # F <=> probation


def test_exam_score_scale_and_clamp():
    assert L.exam_score([14] * 5, 5) == 100.0
    assert L.exam_score([22] * 8, 8) == 100.0                                       # clamped
    assert L.exam_score([-8] * 5, 5) == 0.0                                         # clamped
    assert L.exam_score([10, 10], 5) == round(100 * 20 / 70, 1)                     # missing rounds count 0
    assert L.exam_score([], 5) == 0.0


def test_midterm_and_finals_count_fifty_fifty():
    assert AR.EXAM_WEIGHT == {"midterm": 0.5, "finals": 0.5}
    assert L.combine(80, 60, True, True) == (70.0, False)
    assert L.combine(100, 0, True, True) == (50.0, False)


def test_combine_provisional_and_missing_exams():
    assert L.combine(None, None) == (None, True)
    assert L.combine(None, None, True, True) == (None, False)                        # never took an exam: unranked
    assert L.combine(80, None, True, False) == (80.0, True)                          # finals still to come: provisional
    assert L.combine(80, None, True, True) == (40.0, False)                          # finals window closed: counts 0
    assert L.combine(None, 70, True, True) == (35.0, False)


def test_rewards_per_grade():
    assert [L.reward_for(g) for g in AR.GRADE_ORDER] == sorted((L.reward_for(g) for g in AR.GRADE_ORDER), reverse=True)
    assert L.reward_for("F") == 0 and L.reward_for("A+") > L.reward_for("A") > L.reward_for("B+")
    assert 2 * L.reward_for("A+") <= 300                                            # small next to Part 2's 600/season


# ------------------------------------------------------------------ ranking ties
def _r(uid, score):
    return L.Ranked(0, uid, uid, f"u{uid}", score, L.gpa_of(score), L.grade_of(score))


def test_ranking_ties_share_a_rank():
    out = L.rank_rows([_r(5, 70.0), _r(2, 90.0), _r(9, 70.0), _r(1, 70.0), _r(7, 50.0)])
    assert [(r.user_id, r.rank) for r in out] == [(2, 1), (1, 2), (5, 2), (9, 2), (7, 5)]
    assert L.rank_rows([]) == []


# ------------------------------------------------------------------ time limit (pure)
def test_round_time_limit_is_enforced_by_timestamps():
    start = T0
    assert not L.round_expired(start, start + timedelta(seconds=AR.ROUND_SECONDS))
    assert not L.round_expired(start, start + timedelta(seconds=AR.ROUND_SECONDS + AR.ROUND_GRACE_SECONDS))
    assert L.round_expired(start, start + timedelta(seconds=AR.ROUND_SECONDS + AR.ROUND_GRACE_SECONDS + 1))
    assert L.seconds_left(start, start + timedelta(seconds=10)) == AR.ROUND_SECONDS - 10
    assert L.seconds_left(start, start + timedelta(seconds=99)) == 0
    assert AR.ROUND_SECONDS == 25


# ------------------------------------------------------------------ drawing / calendar
@pytest.mark.parametrize("stream", list(AR.STREAMS))
@pytest.mark.parametrize("kind", AR.EXAM_KINDS)
def test_draw_cards_counts_mix_and_difficulty(stream, kind):
    for seed in range(20):
        keys = L.draw_cards(stream, kind, random.Random(seed))
        assert len(keys) == AR.EXAM_ROUNDS[kind] and len(set(keys)) == len(keys)
        cards = [CR.BY_KEY[k] for k in keys]
        assert sum(1 for c in cards if c.stream == stream) == AR.EXAM_STREAM_CARDS[kind]
        assert all(c.stream in (None, stream) for c in cards)
        assert all(c.difficulty in AR.EXAM_DIFFICULTIES[kind] for c in cards)


def test_finals_avoid_cards_seen_in_the_midterm():
    for seed in range(30):
        mid = L.draw_cards("music", "midterm", random.Random(seed))
        fin = L.draw_cards("music", "finals", random.Random(seed), exclude=mid)
        assert not set(mid) & set(fin) and len(fin) == 8


def test_draw_is_deterministic():
    assert L.draw_cards("arts", "finals", random.Random(3)) == L.draw_cards("arts", "finals", random.Random(3))


def test_exam_windows_and_opening():
    assert AR.EXAM_ROUNDS == {"midterm": 5, "finals": 8} and AR.EXAM_DAY == {"midterm": 4, "finals": 6}
    D = timedelta(days=1)
    assert L.exam_open_now(T0, T0 + 3 * D) == "midterm"
    assert L.exam_open_now(T0, T0 + 4 * D - timedelta(seconds=1)) == "midterm"
    assert L.exam_open_now(T0, T0 + 4 * D) is None                                    # Day 5 (Fest)
    assert L.exam_open_now(T0, T0 + 5 * D + timedelta(hours=3)) == "finals"
    assert L.exam_open_now(T0, T0 + 6 * D) is None and L.exam_open_now(T0, T0 + timedelta(hours=1)) is None
    assert L.next_exam(T0, T0)[0] == "midterm" and L.next_exam(T0, T0 + 4 * D)[0] == "finals" and L.next_exam(T0, T0 + 6 * D) is None


def test_late_joiner_attendance_is_lighter():
    assert L.possible_class_days(1, 4) == 3 and L.possible_class_days(4, 4) == 1 and L.possible_class_days(3, 4) == 2
    assert L.attendance_ratio(1, 1) == 1.0 and L.attendance_ratio(0, 3) == 0.0 and L.attendance_ratio(2, 3) == pytest.approx(2 / 3)
