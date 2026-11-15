"""Academy social, Part 4: PURE rules (no database): rival pairing, bot rivals, duel prizes, club-battle scoring, crush / date /
buddy rules, help catalog. Run: pytest tests/test_academy_social_logic.py"""
import itertools
import random
from datetime import datetime, timezone

import pytest

from game import academy_rules as AR
from services import academy_exam_logic as L
from services import academy_social_logic as S
from utils.content import academy_exam_rounds as CR, academy_social_texts as T

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def cands(n, seed=1):
    r = random.Random(seed)
    return [S.Candidate(i, round(r.uniform(0, 10), 2), r.randint(5, 70)) for i in range(1, n + 1)]


# ------------------------------------------------------------------ pairing
@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 7, 10, 25])
def test_everybody_is_paired_exactly_once_and_odd_gets_the_bot(n):
    pairs, odd = S.pair_students(cands(n))
    ids = [x for p in pairs for x in p]
    assert len(ids) == len(set(ids)) == 2 * (n // 2)
    assert (odd is None) == (n % 2 == 0)
    assert sorted(ids + ([odd] if odd else [])) == list(range(1, n + 1))
    assert all(a != b for a, b in pairs)


def test_pairing_is_deterministic_and_order_independent():
    c = cands(11)
    shuffled = c[:]
    random.Random(9).shuffle(shuffled)
    assert S.pair_students(c) == S.pair_students(shuffled)


def test_similar_students_are_paired():
    c = [S.Candidate(1, 9.0, 80), S.Candidate(2, 2.0, 10), S.Candidate(3, 9.1, 78), S.Candidate(4, 2.2, 12)]
    pairs, odd = S.pair_students(c)
    assert odd is None and {frozenset(p) for p in pairs} == {frozenset({1, 3}), frozenset({2, 4})}


def test_gpa_and_knowledge_both_count():
    a, b = S.Candidate(1, 5.0, 10), S.Candidate(2, 6.0, 10)
    assert S.pair_score(b) - S.pair_score(a) == pytest.approx(AR.RIVAL_GPA_WEIGHT)
    assert S.pair_score(S.Candidate(3, 5.0, 30)) - S.pair_score(a) == 20


def _cost(pairs, c):
    sc = {x.user_id: S.pair_score(x) for x in c}
    return sum(abs(sc[a] - sc[b]) for a, b in pairs)


def test_odd_player_out_is_the_best_choice_among_neighbour_pairings():
    for seed in range(6):
        c = cands(7, seed)
        pairs, odd = S.pair_students(c)
        ordered = sorted(c, key=lambda x: (S.pair_score(x), x.user_id))
        best = min(_cost([(r[i].user_id, r[i + 1].user_id) for i in range(0, 6, 2)], c)
                   for r in ([x for j, x in enumerate(ordered) if j != k] for k in range(7)))
        assert _cost(pairs, c) == pytest.approx(best)


# ------------------------------------------------------------------ bot rival
def test_bot_student_is_close_to_the_player_and_reproducible():
    stats = {"knowledge": 40, "charisma": 5, "fitness": 99, "creativity": 20}
    a = S.bot_student(stats, random.Random(3))
    b = S.bot_student(stats, random.Random(3))
    assert a == b
    for k in AR.STAT_KEYS:
        assert abs(getattr(a, k) - stats[k]) <= AR.RIVAL_BOT_STAT_SPREAD
        assert 0 <= getattr(a, k) <= 100
    assert a.stream in AR.STREAMS


def test_bot_name_is_stable():
    assert S.bot_name(5) == S.bot_name(5) and S.bot_name(5) in T.BOT_RIVAL_NAMES and S.bot_name(-5) in T.BOT_RIVAL_NAMES


def test_bot_picks_the_best_expected_move():
    st = L.ExamStudent("science", 80, 10, 10, 10)
    card = CR.BY_KEY[S.duel_cards(random.Random(1))[0]]
    idx = S.bot_choice(st, card)
    ev = []
    for b in card.buttons:
        p, _, _ = L.chance_of(st, card, b)
        w, lo = AR.ROUND_POINTS[b.risk]
        ev.append(p * w + (1 - p) * lo)
    assert ev[idx] == max(ev)


# ------------------------------------------------------------------ cards and rounds
def test_duel_cards_are_three_distinct_general_cards_and_no_real_subject_questions():
    for seed in range(20):
        keys = S.duel_cards(random.Random(seed))
        assert len(keys) == len(set(keys)) == AR.RIVAL_DUEL_ROUNDS
        assert all(CR.BY_KEY[k].stream is None for k in keys)


def test_both_players_get_the_same_cards_and_play_round_decides():
    keys = S.duel_cards(random.Random(2))
    me = L.ExamStudent("arts", 50, 50, 50, 50)
    res = [L.play_round(me, CR.BY_KEY[k], 0, random.Random(i)) for i, k in enumerate(keys)]
    assert [r.card_key for r in res] == keys


def test_battle_card_is_the_same_for_everybody_and_changes_with_the_season():
    assert S.battle_card("battle:1:x") == S.battle_card("battle:1:x")
    assert CR.BY_KEY[S.battle_card("battle:1:x")].difficulty == AR.BATTLE_DIFFICULTY
    assert len({S.battle_card(f"battle:{i}:x") for i in range(30)}) > 1


def test_club_focus_bonus_only_raises_the_focus_stat():
    st = L.ExamStudent("arts", 10, 10, 10, 10)
    b = S.battle_student(st, "coding")                   # coding focus = knowledge
    assert b.knowledge == 10 + AR.BATTLE_FOCUS_BONUS and (b.charisma, b.fitness, b.creativity) == (10, 10, 10)
    assert st.knowledge == 10                            # the original is untouched
    assert S.battle_student(st, "sports").fitness == 10 + AR.BATTLE_FOCUS_BONUS


# ------------------------------------------------------------------ duel result and prizes
def test_higher_total_wins_and_prizes():
    assert S.duel_outcome(30, 20) == "a" and S.duel_outcome(5, 9) == "b" and S.duel_outcome(7, 7) == "tie"
    assert S.duel_prize("win") == S.Prize(AR.RIVAL_WIN_DIAMONDS, AR.RIVAL_BADGE_POINTS)
    assert S.duel_prize("tie") == S.Prize(AR.RIVAL_TIE_DIAMONDS, 0)
    assert S.duel_prize("lose") == S.Prize(0, 0)
    w = S.duel_prize("win", walkover=True)
    assert w.diamonds == AR.RIVAL_WIN_DIAMONDS * AR.RIVAL_WALKOVER_PCT // 100 and w.badge == 0


def test_side_total_can_be_negative():
    assert S.side_total([22, -8, 0]) == 14 and S.side_total([-8, -8, 3]) == -13


# ------------------------------------------------------------------ club scoring
def test_club_total_is_practice_plus_battle_and_sorted():
    rows = S.club_rows({"sports": 40, "music": 10}, {"music": 50, "coding": 5}, {"music": 3, "coding": 1})
    assert [r.club for r in rows][:2] == ["music", "sports"]
    m = rows[0]
    assert (m.practice, m.battle, m.total, m.players) == (10, 50, 60, 3)
    assert {r.club for r in rows} == set(AR.CLUBS)


def test_negative_battle_points_count():
    rows = S.club_rows({"drama": 20}, {"drama": -16}, {"drama": 2})
    assert next(r for r in rows if r.club == "drama").total == 4


def test_tie_break_players_then_club_order():
    rows = S.club_rows({"sports": 10, "music": 10}, {}, {"music": 2})
    assert rows[0].club == "music"
    rows = S.club_rows({"sports": 10, "music": 10}, {}, {})
    assert rows[0].club == "sports"                      # AR.CLUBS order is the last tie-break


def test_nobody_scored_means_no_winner():
    assert S.winning_club(S.club_rows({}, {}, {})) is None
    assert S.winning_club(S.club_rows({"coding": 1}, {}, {})) == "coding"
    assert S.winning_club(S.club_rows({"coding": 5}, {"coding": -5}, {"coding": 1})) is None


# ------------------------------------------------------------------ crush rules and privacy
OK = dict(day=3, sender_enrolled=True, target_enrolled=True, same_person=False, target_is_bot=False, has_active=False,
          sender_in_couple=False, target_in_couple=False)


def block(**kw):
    return S.crush_block(**{**OK, **kw})


def test_crush_rules():
    assert block() is None
    assert block(sender_enrolled=False) == T.NEED_ENROLL
    assert block(target_enrolled=False) == T.TARGET_NOT_ENROLLED
    assert block(same_person=True) == T.SELF
    assert block(target_is_bot=True) == T.BOT_TARGET
    assert block(has_active=True) == T.CRUSH_ALREADY                 # one active crush each
    assert block(sender_in_couple=True) == T.CRUSH_IN_COUPLE
    assert block(target_in_couple=True) == T.CRUSH_TARGET_TAKEN
    assert block(day=2) == T.NOT_YET.format(day=AR.CRUSH_FROM_DAY) and block(day=0) is not None
    assert all(block(day=d) is None for d in (3, 4, 5, 6, 7))


def test_only_mutual_crushes_are_revealed():
    assert not S.is_mutual(1, 2, {1: 2})                              # one-sided
    assert not S.is_mutual(1, 2, {1: 2, 2: 3})                        # they crush somebody else
    assert not S.is_mutual(1, 2, {})
    assert S.is_mutual(1, 2, {1: 2, 2: 1}) and S.is_mutual(2, 1, {1: 2, 2: 1})
    assert not S.is_mutual(1, 1, {1: 1})
    assert S.couple_key(9, 4) == S.couple_key(4, 9) == (4, 9)


def test_no_refusal_text_says_anything_about_the_targets_feelings():
    texts = [T.CRUSH_SAVED, T.CRUSH_ALREADY, T.CRUSH_IN_COUPLE, T.CRUSH_TARGET_TAKEN, T.CRUSH_OFF, T.CRUSH_OFF_NONE,
             T.TARGET_NOT_ENROLLED, T.SELF, T.BOT_TARGET, T.NEED_ENROLL]
    for t in texts:
        low = t.lower()
        assert "crushes on you" not in low and "has a crush on you" not in low and "likes you" not in low


# ------------------------------------------------------------------ dates / buddies
def test_date_rules_and_gains():
    assert S.date_block(day=3, in_couple=False, done_today=False) == T.DATE_NEED_COUPLE
    assert S.date_block(day=3, in_couple=True, done_today=True) == T.DATE_ONCE
    assert S.date_block(day=2, in_couple=True, done_today=False) is not None
    assert S.date_block(day=7, in_couple=True, done_today=False) is None
    for seed in range(30):
        g = S.date_gains(random.Random(seed))
        assert g["charisma"] >= AR.DATE_STAT_GAIN[0] and set(g) <= set(AR.STAT_KEYS) and sum(g.values()) <= AR.DATE_STAT_GAIN[1] + 1


def test_buddy_rules():
    ok = dict(sender_enrolled=True, target_enrolled=True, same_person=False, target_is_bot=False, sender_has_buddy=False,
              target_has_buddy=False, sender_pending=0)
    assert S.buddy_block(**ok) is None
    assert S.buddy_block(**{**ok, "same_person": True}) == T.SELF
    assert S.buddy_block(**{**ok, "target_is_bot": True}) == T.BOT_TARGET
    assert S.buddy_block(**{**ok, "sender_has_buddy": True}) == T.BUDDY_ALREADY_HAS
    assert S.buddy_block(**{**ok, "target_has_buddy": True}) == T.BUDDY_TARGET_HAS
    assert S.buddy_block(**{**ok, "sender_pending": AR.BUDDY_PENDING_MAX}) == T.BUDDY_PENDING_LIMIT
    assert S.buddy_block(**{**ok, "target_enrolled": False}) == T.TARGET_NOT_ENROLLED


# ------------------------------------------------------------------ calendar helpers
def test_day_windows():
    assert [d for d in range(0, 9) if S.rival_pairing_due(d)] == [3, 4, 5]
    assert [d for d in range(0, 9) if S.duel_open(d)] == [3, 4, 5, 6]
    assert [d for d in range(0, 9) if S.battle_due(d)] == [3, 4, 5]
    lo, hi = S.battle_window(T0)
    assert lo == T0 + 2 * AR.DAY and hi == T0 + 5 * AR.DAY
    assert S.duel_deadline(T0) == T0 + 6 * AR.DAY


def test_diamond_budget_is_small():
    most = AR.RIVAL_WIN_DIAMONDS + AR.CLUB_WIN_DIAMONDS
    assert most <= 200 and AR.DATE_DIAMONDS <= 25


# ------------------------------------------------------------------ help catalog
def test_new_commands_are_in_help_but_not_in_the_slash_menu():
    from utils import helpcatalog as H
    new = {"rival", "rivalduel", "crush", "crushoff", "campuscouple", "campusdate", "clubs", "buddy"}
    assert new <= set(H.DESC)
    cat = dict(H.CATEGORIES)["Academy 🎓"]
    assert new <= set(cat)
    assert not new & set(H.MENU) and not new & set(H.GROUP_MENU_EXTRA)
    names = [c for c, _ in H.DESC_LIST]
    assert len(names) == len(set(names))                      # no command listed twice (no collisions)
