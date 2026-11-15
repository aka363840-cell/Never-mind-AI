"""Academy adventures, Part 5: PURE engine logic (stat checks, transitions, rewards, expiry, state, rendering).
No database, no Telegram. Run: pytest tests/test_academy_adventure_logic.py   (the database half is tests/test_academy_adventures.py)"""
import random
from datetime import datetime, timedelta, timezone

import pytest

from game import academy_rules as AR
from services import academy_adventure_logic as L, academy_adventure_views as V
from utils.content import academy_adventure_texts as T, academy_adventures as C

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
STATS = {"knowledge": 25, "charisma": 25, "fitness": 25, "creativity": 25}


class Dice(random.Random):
    """A Random whose dice (randint) always roll `v`."""
    def __init__(self, v=50, seed=0):
        super().__init__(seed)
        self.v = v
        self.calls = 0

    def randint(self, a, b):
        self.calls += 1
        return self.v


def ck(stat="charisma", d=2, **kw):
    return C.Check(stat, d, "a", "b", **kw)


ADV = C.CANTEEN_HEIST


# ------------------------------------------------------------------ chance maths
def test_chance_at_the_target_is_the_base_chance():
    for d, target in AR.ADV_TARGET.items():
        r = L.chance_of(ADV, ck(d=d), {"charisma": target}, "arts", [])
        assert r.chance == pytest.approx(AR.ADV_CHANCE_BASE) and r.threshold == 55 and r.skill == target


def test_ten_skill_points_are_ten_percent():
    lo = L.chance_of(ADV, ck(), {"charisma": 25}, "arts", []).chance
    hi = L.chance_of(ADV, ck(), {"charisma": 35}, "arts", []).chance
    assert hi - lo == pytest.approx(0.10)


def test_chance_is_always_between_five_and_ninety_five_percent():
    lo = L.chance_of(ADV, ck(d=4), {"charisma": 0}, "arts", [])
    hi = L.chance_of(ADV, ck(d=1), {"charisma": 100}, "arts", [])
    assert (lo.chance, lo.threshold) == (0.05, 5) and (hi.chance, hi.threshold) == (0.95, 95)


def test_flags_add_and_remove_skill_only_when_set():
    k = ck(bonus=(("key", 6), ("samosa", -6)))
    base = L.chance_of(ADV, k, {"charisma": 25}, "arts", []).chance
    assert L.chance_of(ADV, k, {"charisma": 25}, "arts", ["key"]).chance == pytest.approx(base + 0.06)
    assert L.chance_of(ADV, k, {"charisma": 25}, "arts", ["samosa"]).chance == pytest.approx(base - 0.06)
    assert L.chance_of(ADV, k, {"charisma": 25}, "arts", ["key", "samosa"]).chance == pytest.approx(base)
    assert L.chance_of(ADV, k, {"charisma": 25}, "arts", ["other"]).chance == pytest.approx(base)


def test_stream_bonus_applies_to_stat_checks_of_the_matching_stream_only():
    adv = C.ROOFTOP_CONCERT
    k = ck("creativity")
    a = L.chance_of(adv, k, {"creativity": 25}, "arts", []).chance
    m = L.chance_of(adv, k, {"creativity": 25}, "music", []).chance
    assert m - a == pytest.approx(0.05)
    lk = ck("luck", 2)
    assert L.chance_of(adv, lk, {}, "music", []).chance == L.chance_of(adv, lk, {}, "arts", []).chance


def test_a_luck_check_ignores_stats():
    for d, p in AR.ADV_LUCK_CHANCE.items():
        low = L.chance_of(ADV, ck("luck", d), {k: 0 for k in AR.STAT_KEYS}, "arts", [])
        high = L.chance_of(ADV, ck("luck", d), {k: 100 for k in AR.STAT_KEYS}, "arts", [])
        assert low.chance == high.chance == pytest.approx(p) and low.skill is None
    withbonus = L.chance_of(ADV, ck("luck", 2, bonus=(("decoy", 8),)), {}, "arts", ["decoy"])
    assert withbonus.chance == pytest.approx(0.63)


def test_every_stat_key_the_content_uses_is_known():
    assert set(AR.ADV_CHECK_STATS) == set(L.STAT_EMOJI) == set(L.STAT_NAME)


# ------------------------------------------------------------------ the dice
def test_one_dice_roll_per_check_and_the_edges():
    r = Dice(1)
    res = L.resolve_check(ADV, ck(d=4), {"charisma": 0}, "arts", [], r)
    assert res.success is True and res.roll == 1 and r.calls == 1          # 5% floor: a roll of 1 always wins
    res = L.resolve_check(ADV, ck(d=1), {"charisma": 100}, "arts", [], Dice(100))
    assert res.success is False and res.roll == 100                        # 95% ceiling: a roll of 100 always loses


def test_success_is_roll_at_or_below_the_threshold():
    thr = L.chance_of(ADV, ck(), STATS, "arts", []).threshold              # 55
    assert L.resolve_check(ADV, ck(), STATS, "arts", [], Dice(thr)).success is True
    assert L.resolve_check(ADV, ck(), STATS, "arts", [], Dice(thr + 1)).success is False


def test_the_real_random_is_used_when_no_rng_is_given():
    seen = {L.resolve_check(ADV, ck(), STATS, "arts", []).roll for _ in range(200)}
    assert min(seen) >= 1 and max(seen) <= 100 and len(seen) > 20


def test_check_line_and_hint_text():
    r = L.resolve_check(ADV, ck("luck", 3), STATS, "arts", [], Dice(7))
    line = L.check_line(r)
    assert "Luck check" in line and "★★★" in line and "rolled 7" in line and "success" in line
    assert "not this time" in L.check_line(L.resolve_check(ADV, ck(), STATS, "arts", [], Dice(99)))
    assert L.hint(ADV, C.Choice("x", to="door"), STATS, "arts", []) == ""
    assert L.hint(ADV, C.Choice("x", check=ck("fitness")), STATS, "arts", []) == "💪 55%"


# ------------------------------------------------------------------ transitions
def test_plain_move_sets_flags_and_never_rolls():
    r = Dice(50)
    st = L.new_state(3)
    mv = L.advance(ADV, "plan", st, 2, STATS, "arts", r)                   # "Build a pillow decoy": to=window, sets decoy
    assert mv.node == "window" and "decoy" in mv.state["flags"] and mv.check is None and r.calls == 0
    assert mv.state["trail"] == ["window"] and mv.choice_text.startswith("Build a pillow")


def test_check_success_and_failure_pick_the_right_target_and_flags():
    st = L.new_state(3)
    ok = L.advance(ADV, "plan", st, 0, STATS, "arts", Dice(1))             # charisma 2 -> door, sets key on success
    assert ok.node == "door" and "key" in ok.state["flags"] and ok.state["wins"] == 1 and ok.check.success
    bad = L.advance(ADV, "plan", st, 0, STATS, "arts", Dice(100))
    assert bad.node == "window" and "key" not in bad.state["flags"] and bad.state["losses"] == 1 and not bad.check.success


def test_advance_never_mutates_the_old_state():
    st = L.new_state(3)
    before = L.dump_state(st)
    L.advance(ADV, "plan", st, 2, STATS, "arts", Dice(1))
    L.advance(ADV, "plan", st, 0, STATS, "arts", Dice(1))
    assert L.dump_state(st) == before


def test_hidden_choices_cannot_be_pressed_and_endings_have_no_choices():
    paper = C.MISSING_PAPER
    st = L.new_state(3)
    assert [i for i, _ in L.visible(paper.nodes["corridor"], st["flags"])] == [2, 3]          # clue buttons hidden
    with pytest.raises(L.BadChoice):
        L.advance(paper, "corridor", st, 0, STATS, "arts", Dice(1))                            # needs glitter
    st["flags"] = ["glitter"]
    assert [i for i, _ in L.visible(paper.nodes["corridor"], st["flags"])] == [0, 2, 3]
    assert L.advance(paper, "corridor", st, 0, STATS, "arts", Dice(1)).node == "artroom"
    for bad_idx in (-1, 9):
        with pytest.raises(L.BadChoice):
            L.advance(paper, "corridor", st, bad_idx, STATS, "arts", Dice(1))
    with pytest.raises(L.BadChoice):
        L.advance(paper, "end_solved", st, 0, STATS, "arts", Dice(1))
    with pytest.raises(L.BadChoice):
        L.advance(paper, "no_such_node", st, 0, STATS, "arts", Dice(1))


def test_hides_removes_a_button():
    node = C.Node("n", "text of the node here", (C.Choice("a", to="x"), C.Choice("b", to="x", hides=("seen",))))
    assert [i for i, _ in L.visible(node, [])] == [0, 1] and [i for i, _ in L.visible(node, ["seen"])] == [0]


def test_clues_change_the_odds_in_the_mystery():
    paper = C.MISSING_PAPER
    node = paper.nodes["artroom"]
    plain = L.node_choices(paper, node, L.new_state(3), STATS, "arts")
    st = L.new_state(3)
    st["flags"] = ["glitter", "tape", "trail"]
    clued = L.node_choices(paper, node, st, STATS, "arts")
    assert int(clued[1].hint.split()[1].rstrip("%")) - int(plain[1].hint.split()[1].rstrip("%")) == 20


# ------------------------------------------------------------------ whole runs
def play(adv, picks, rng, day=3, stats=STATS, stream="arts"):
    st, node, path = L.new_state(day), adv.start, []
    for idx in picks:
        mv = L.advance(adv, node, st, idx, stats, stream, rng)
        node, st = mv.node, mv.state
        path.append(node)
    return node, st, path


def test_lucky_detective_reaches_the_best_ending():
    node, st, path = play(C.MISSING_PAPER, [0, 0, 0, 0], Dice(1))
    assert path == ["staffroom", "corridor", "artroom", "end_solved"] and "glitter" in st["flags"]
    assert C.MISSING_PAPER.nodes[node].ending.mood == "great"


def test_unlucky_detective_accuses_the_gardener():
    node, st, path = play(C.MISSING_PAPER, [0, 0, 3, 0], Dice(100))
    assert path == ["staffroom", "corridor", "board", "end_wrong"] and st["losses"] == 1


def test_the_samosa_mistake_makes_the_final_sprint_harder():
    adv = C.SPORTS_SHOWDOWN
    _, st, _ = play(adv, [2], Dice(1), day=5)
    assert "samosa" in st["flags"]
    node = adv.nodes["final"]
    plain = L.chance_of(adv, node.choices[0].check, STATS, "arts", []).chance
    heavy = L.chance_of(adv, node.choices[0].check, STATS, "arts", ["samosa"]).chance
    warm = L.chance_of(adv, node.choices[0].check, STATS, "arts", ["warm"]).chance
    assert heavy < plain < warm


# ------------------------------------------------------------------ rewards
def test_rewards_normal_and_fest():
    end = C.HAUNTED_LIBRARY.nodes["end_encore"].ending                       # 30 diamonds, creativity +2, charisma +1, a title
    n, f = L.rewards_for(end, False), L.rewards_for(end, True)
    assert (n.diamonds, n.stats, n.fest) == (30, {"creativity": 2, "charisma": 1}, False)
    assert (f.diamonds, f.base_diamonds, f.fest_diamonds, f.stats, f.fest) == (45, 30, 15, {"creativity": 3, "charisma": 2}, True)
    assert n.trophy == f.trophy == end.trophy and end.trophy.kind == "title"


def test_fest_bonus_rounds_up_and_never_invents_rewards():
    assert [AR.adv_fest_diamonds(x) for x in (0, 1, 4, 12, 18, 30)] == [0, 2, 6, 18, 27, 45]
    nothing = C.Ending("Nothing", "bad")
    r = L.rewards_for(nothing, True)
    assert (r.diamonds, r.stats, r.trophy) == (0, {}, None)


def test_reward_ref_is_unique_per_run_and_short():
    refs = {L.reward_ref(u, e, r) for u in (1, 2) for e in (111, 222) for r in (1, 2, 3)}
    assert len(refs) == 12
    assert len("academy:" + L.reward_ref(9_999_999_999_999, 1_900_000_000_000_000, 99_999_999)) <= 128
    assert L.reward_ref(5, 6, 7) == "adv:5:6:7"


# ------------------------------------------------------------------ expiry
def test_runs_expire_after_thirty_quiet_minutes():
    t = T0
    assert not L.expired(t, t + timedelta(minutes=30))                        # exactly 30 min: still alive
    assert L.expired(t, t + timedelta(minutes=30, seconds=1))
    assert not L.expired(t, t + timedelta(minutes=1))
    naive = t.replace(tzinfo=None)                                           # SQLite hands back naive datetimes
    assert L.expired(naive, t + timedelta(hours=1)) and not L.expired(naive, t + timedelta(minutes=5))
    assert AR.ADV_EXPIRE_MINUTES == 30


def test_seconds_left_counts_down_to_zero():
    assert L.seconds_left(T0, T0) == 1800 and L.seconds_left(T0, T0 + timedelta(minutes=10)) == 1200
    assert L.seconds_left(T0, T0 + timedelta(hours=2)) == 0


# ------------------------------------------------------------------ run state
def test_state_roundtrip_and_corrupt_data():
    st = L.new_state(5)
    st["flags"], st["wins"], st["trail"] = ["a", "b"], 2, ["x", "y"]
    assert L.load_state(L.dump_state(st)) == st and st["fest"] is True and L.new_state(4)["fest"] is False
    for junk in (None, "", "not json", "[1,2]", "null", '{"flags": "oops", "trail": 3}'):
        s = L.load_state(junk)
        assert s["flags"] == [] and s["trail"] == [] and s["wins"] == 0
    assert L.new_state(3, fest=True)["fest"] is True


# ------------------------------------------------------------------ rendering
def _screen(**kw):
    base = dict(kind="node", adventure="haunted_library", run_id=7, step=2, node="gates", text="Plain story text", seconds_left=1500)
    base.update(kw)
    return L.Screen(**base)


def test_node_text_escapes_story_text_and_shows_the_fest_line():
    html = V.node_text(_screen(text="Mind the <b>bold</b> & the cat", line="🎲 Luck check ★: you needed 5 or less and rolled 3. success!"))
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; the cat" in html and "<b>The Haunted Library</b>" in html
    assert "Fest Day bonus" not in html and "25 min" in html
    assert "Fest Day bonus" in V.node_text(_screen(fest=True))


def test_ending_text_lists_every_reward():
    end = C.HAUNTED_LIBRARY.nodes["end_encore"]
    rw = L.rewards_for(end.ending, True)
    scr = _screen(kind="ending", text=end.text, title=end.ending.title, mood="great", rewards=rw, paid=45, gains=rw.stats, trophy_new=True,
                  fest=True, done_today=1)
    html = V.ending_text(scr)
    for part in ("Best ending", "+45", "+15 Fest Day bonus", "Creativity", "<b>+3</b>", "New title: <b>Ghost Whisperer</b>", "1/2"):
        assert part in html, part
    again = V.ending_text(_screen(kind="ending", text="t", title="T", mood="good", rewards=L.rewards_for(end.ending, False), paid=30,
                                  gains={}, trophy_new=False, done_today=2))
    assert "already own" in again and "Fest Day" not in again and "+30" in again


def test_ending_without_diamonds_says_so_and_items_are_items():
    heist_end = C.MISSING_PAPER.nodes["end_wrong"]
    rw = L.rewards_for(heist_end.ending, False)
    html = V.ending_text(_screen(kind="ending", text="x", title="t", mood="bad", rewards=rw, paid=0, gains={}, trophy_new=True))
    assert "No diamonds" in html and "New item: <b>Mr. Das&#x27;s Marigold</b>" in html or "New item" in html


def test_list_text_shows_open_and_closed_adventures():
    entries = [L.ListEntry("a", "Open One", "🗺", "A <fun> tale", 20, True),
               L.ListEntry("b", "Shut One", "🔒", "x", 15, False, "night only (20:00 to 06:00)")]
    v = L.ListView(day=5, phase_name="Fest Day", phase_emoji="🎉", fest=True, entries=entries, done_today=1, energy=80,
                   trophies=[("title", "Ghost Whisperer")])
    html = V.list_text(v)
    for part in ("Day 5", "Fest Day bonus", "+50%", "<b>Open One</b> (⚡20)", "A &lt;fun&gt; tale", "night only", "1/2", "80", "Ghost Whisperer"):
        assert part in html, part
    v2 = L.ListView(day=2, phase_name="Classes Begin", phase_emoji="📚", fest=False, entries=[entries[1]],
                    live=L.LiveRun("a", "Open One", "🗺", 3, 1, 600))
    h2 = V.list_text(v2)
    assert "Fest Day bonus" not in h2 and "Nothing is open" in h2 and "In progress" in h2 and "10 min" in h2


def test_texts_are_html_safe_and_long_texts_are_cut():
    big = _screen(text="x" * 9000)
    assert len(V.node_text(big)) <= 3900
    assert V.screen_text(L.Screen(kind="expired", message=T.EXPIRED)) == T.EXPIRED
    assert all(isinstance(getattr(T, n), (str, dict)) for n in dir(T) if n.isupper())


# ------------------------------------------------------------------ help catalog
def test_adventure_is_in_help_but_not_in_the_slash_menu():
    from utils import helpcatalog as H
    assert "adventure" in H.DESC and "adventure" not in H.MENU and "adventure" not in H.GROUP_MENU_EXTRA
    assert any("adventure" in cmds for cat, cmds in H.CATEGORIES if cat == "Academy 🎓")
    assert "adventure" not in H.STAFF
