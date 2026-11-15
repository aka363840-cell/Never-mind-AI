"""Academy adventures, Part 5: automatic validation of ALL adventure data (pure, no database, no Telegram).
Every node reachable, no dead or missing links, every ending reachable, no loops, rewards inside the caps of game/academy_rules.py.
Run: pytest tests/test_academy_adventure_data.py"""
import dataclasses
import random
from functools import lru_cache

import pytest

from game import academy_rules as AR
from services import academy_adventure_logic as L
from utils.content import academy_adventures as C

ADVS = list(C.ADVENTURES.values())
IDS = [a.key for a in ADVS]
BAD_WORDS = ("kill", "blood", "gun", "drunk", "beer", "drug", "sex", "die ", "dead", "suicide", "naked")


# ------------------------------------------------------------------ the validator says everything is fine
def test_all_adventure_data_is_valid():
    assert L.all_problems() == []


def test_the_five_adventures_exist_in_a_stable_order():
    assert C.ORDER == ("haunted_library", "canteen_heist", "missing_paper", "rooftop_concert", "sports_showdown")
    assert set(C.ORDER) == set(C.ADVENTURES)


@pytest.mark.parametrize("adv", ADVS, ids=IDS)
def test_size_and_variety(adv):
    endings = [n for n in adv.nodes.values() if n.ending]
    checks = [c.check for n in adv.nodes.values() for c in n.choices if c.check]
    assert 8 <= len(adv.nodes) <= 12, len(adv.nodes)                       # 8-12 nodes
    assert len(endings) >= 3                                               # several endings
    assert len({n.ending.mood for n in endings}) >= 3                      # that really differ
    assert len(checks) >= 5 and len({c.stat for c in checks}) >= 2         # real stat checks, more than one stat
    assert any(c.difficulty >= 3 for c in checks) and len({c.difficulty for c in checks}) >= 2     # easy and hard checks
    assert any(n.ending.mood == "bad" and n.ending.diamonds <= 5 for n in endings)
    assert any(n.ending.mood == "great" and n.ending.diamonds >= 25 for n in endings)


def test_the_brief_stats_are_used():
    stats = lambda key: {c.check.stat for n in C.ADVENTURES[key].nodes.values() for c in n.choices if c.check}
    assert {"charisma", "luck"} <= stats("canteen_heist")                  # Charisma and luck
    assert "creativity" in stats("rooftop_concert") and C.ADVENTURES["rooftop_concert"].stream_bonus == {"music": 5}
    assert "fitness" in stats("sports_showdown") and C.ADVENTURES["sports_showdown"].stream_bonus == {"sports": 5}


def test_the_mystery_has_clues_that_matter():
    adv = C.ADVENTURES["missing_paper"]
    set_flags = {f for n in adv.nodes.values() for c in n.choices for f in (c.check.win_sets if c.check else ())}
    used = {f for n in adv.nodes.values() for c in n.choices for f in c.needs}
    used |= {f for n in adv.nodes.values() for c in n.choices if c.check for f, _ in c.check.bonus}
    assert len(set_flags) >= 3 and set_flags <= used | {"alibi", "trail"}
    assert {"glitter", "tape"} <= set_flags
    nodes, buttons, endings = L.explore(adv)
    assert {"end_solved", "end_lucky", "end_partial", "end_wrong"} <= endings


# ------------------------------------------------------------------ schedule from the brief
def test_availability_matches_the_brief():
    lib, heist, paper = (C.ADVENTURES[k] for k in ("haunted_library", "canteen_heist", "missing_paper"))
    conc, sport = C.ADVENTURES["rooftop_concert"], C.ADVENTURES["sports_showdown"]
    assert lib.days == (2, 3, 4, 5, 6) and lib.night
    assert conc.days == (5,) and sport.days == (5,)
    for day in range(1, 8):
        assert not L.is_open(lib, day, 12)                                 # never in daylight
        assert L.is_open(lib, day, 22) == (day in (2, 3, 4, 5, 6))
        assert L.is_open(lib, day, 3) == (day in (2, 3, 4, 5, 6))          # after midnight still "night"
        assert L.is_open(conc, day, 12) == (day == 5) == L.is_open(sport, day, 12)
        assert not L.is_open(paper, 1, 12) and not L.is_open(heist, 1, 12)  # Day 1 is for enrolling
    assert L.is_open(lib, 2, AR.ADV_NIGHT_FROM_HOUR) and L.is_open(lib, 2, AR.ADV_NIGHT_TO_HOUR - 1)
    assert not L.is_open(lib, 2, AR.ADV_NIGHT_FROM_HOUR - 1) and not L.is_open(lib, 2, AR.ADV_NIGHT_TO_HOUR)
    assert [a.key for a in L.available(5, 12)] == ["canteen_heist", "missing_paper", "rooftop_concert", "sports_showdown"]
    assert [a.key for a in L.available(1, 22)] == []
    assert [a.key for a in L.available(7, 22)] == ["canteen_heist"]


def test_every_adventure_has_a_reason_text_when_closed():
    for a in ADVS:
        for day in range(1, 8):
            for hour in (3, 12, 22):
                assert (L.closed_reason(a, day, hour) == "") == L.is_open(a, day, hour)


# ------------------------------------------------------------------ rewards inside the caps
@pytest.mark.parametrize("adv", ADVS, ids=IDS)
def test_rewards_are_inside_the_caps(adv):
    for n in adv.nodes.values():
        e = n.ending
        if not e:
            continue
        assert 0 <= e.diamonds <= AR.ADV_MAX_DIAMONDS
        assert sum(e.stats.values()) <= AR.ADV_MAX_STAT_TOTAL
        for k, v in e.stats.items():
            assert k in AR.STAT_KEYS and 1 <= v <= AR.ADV_MAX_STAT_GAIN
        normal, fest = L.rewards_for(e, False), L.rewards_for(e, True)
        assert normal.diamonds == e.diamonds and normal.stats == e.stats
        assert e.diamonds <= fest.diamonds <= AR.ADV_FEST_MAX_DIAMONDS
        assert all(v <= AR.ADV_FEST_MAX_STAT_GAIN for v in fest.stats.values())
        if e.diamonds:
            assert fest.diamonds > e.diamonds                              # Fest Day is always a real bonus
        assert e.gif is None or e.gif in AR.ADV_GIFS_ALLOWED


def test_fest_day_is_a_bigger_bonus_for_every_adventure():
    for a in ADVS:
        best = max((n.ending for n in a.nodes.values() if n.ending), key=lambda e: e.diamonds)
        assert L.rewards_for(best, True).diamonds >= L.rewards_for(best, False).diamonds * 1.4


def test_the_numbers_from_the_brief():
    assert AR.ADV_MAX_PER_DAY == 2 and AR.ADV_EXPIRE_MINUTES == 30 and AR.ADV_FEST_DAY == 5 == AR.PHASE_BY_DAY[5].day
    assert AR.PHASE_BY_DAY[AR.ADV_FEST_DAY].key == "fest" and AR.ADV_FIRST_DAY == 2
    assert [a.energy for a in ADVS] == [20, 20, 15, 25, 25] and all(AR.ADV_ENERGY_MIN <= a.energy <= AR.ADV_ENERGY_MAX for a in ADVS)


def test_daily_diamond_budget_stays_small():
    assert AR.ADV_MAX_PER_DAY * AR.ADV_MAX_DIAMONDS <= AR.DAILY_DIAMOND_CAP          # never more than Part 2's job cap per day
    assert AR.ADV_MAX_PER_DAY * AR.ADV_FEST_MAX_DIAMONDS <= AR.DAILY_DIAMOND_CAP * 1.5


def test_gifs_are_all_sfw_and_known_to_the_gif_helper():
    ast_gifs = {n.ending.gif for a in ADVS for n in a.nodes.values() if n.ending and n.ending.gif}
    assert ast_gifs and ast_gifs <= set(AR.ADV_GIFS_ALLOWED)
    import re
    src = open("utils/gifs.py", encoding="utf-8").read()
    for g in AR.ADV_GIFS_ALLOWED:
        assert re.search(rf'"{g}":', src), g


def test_trophies_are_unique_and_typed():
    seen = {}
    for a in ADVS:
        for n in a.nodes.values():
            t = n.ending.trophy if n.ending else None
            if t:
                assert t.kind in AR.ADV_TROPHY_KINDS and t.key not in seen and 1 <= len(t.name) <= 40
                seen[t.key] = a.key
    assert len(seen) >= 8 and {t for t in ("title", "item")} == {
        n.ending.trophy.kind for a in ADVS for n in a.nodes.values() if n.ending and n.ending.trophy}


# ------------------------------------------------------------------ text hygiene
def test_texts_fit_telegram_and_stay_wholesome():
    for a in ADVS:
        for s in (a.name, a.blurb):
            assert s.strip() and not any(ch in s for ch in "<>&")
        for n in a.nodes.values():
            assert 20 <= len(n.text) <= 600 and not any(ch in n.text for ch in "<>&"), (a.key, n.key)
            low = (n.text + " " + " ".join(c.text for c in n.choices)).lower() + " "
            assert not any(w in low for w in BAD_WORDS), (a.key, n.key)
            for c in n.choices:
                assert 3 <= len(c.text) <= 40 and not any(ch in c.text for ch in "<>&"), (a.key, n.key, c.text)
            if n.ending:
                assert 3 <= len(n.ending.title) <= 40 and not any(ch in n.ending.title for ch in "<>&")


def test_no_real_subject_quiz_questions():
    """Adventures are stat + luck strategy only: no 'what is the capital / solve' style questions in any text."""
    quiz = ("capital of", "solve ", "formula", "equation", "which planet", "what is the answer", "multiple choice", "correct answer")
    for a in ADVS:
        for n in a.nodes.values():
            text = (n.text + " " + " ".join(c.text for c in n.choices)).lower()
            assert not any(q in text for q in quiz), (a.key, n.key)


def test_callback_data_of_every_button_is_short():
    big_uid, big_run = 9_999_999_999_999, 99_999_999
    for a in ADVS:
        assert len(f"acv:s:{big_uid}:{a.key}".encode()) < 64
    assert len(f"acv:c:{big_uid}:{big_run}:99:3".encode()) < 64
    assert len(f"acv:q:{big_uid}:{big_run}".encode()) < 64


# ------------------------------------------------------------------ the validator really catches mistakes
def _mutate(adv, key, **changes):
    nodes = dict(adv.nodes)
    nodes[key] = dataclasses.replace(nodes[key], **changes)
    return dataclasses.replace(adv, nodes=nodes)


def _find(problem_list, text):
    return any(text in p for p in problem_list)


def test_validator_catches_a_missing_link():
    adv = C.ADVENTURES["canteen_heist"]
    bad = _mutate(adv, "plan", choices=(C.Choice("x", to="nowhere"),) + adv.nodes["plan"].choices[1:])
    assert _find(L.problems(bad), "missing node 'nowhere'")


def test_validator_catches_a_dead_end():
    adv = C.ADVENTURES["canteen_heist"]
    assert _find(L.problems(_mutate(adv, "hide", choices=())), "dead end")


def test_validator_catches_an_unreachable_node_and_ending():
    adv = C.ADVENTURES["haunted_library"]
    nodes = dict(adv.nodes)
    nodes["lonely"] = C.Node("lonely", "Nobody comes here at all, ever.", (C.Choice("Back", to="gates"),))
    nodes["lonely_end"] = C.Node("lonely_end", "A lonely ending that nobody reaches.", (), C.Ending("Lonely", "ok", 5))
    p = L.problems(dataclasses.replace(adv, nodes=nodes))
    assert _find(p, "lonely: unreachable node") and _find(p, "lonely_end: unreachable ending")


def test_validator_catches_a_loop():
    adv = C.ADVENTURES["haunted_library"]
    bad = _mutate(adv, "talk", choices=adv.nodes["talk"].choices + (C.Choice("Again", to="gates"),))
    assert _find(L.problems(bad), "loop")


def test_validator_catches_rewards_over_the_caps():
    adv = C.ADVENTURES["haunted_library"]
    e = adv.nodes["end_encore"].ending
    assert _find(L.problems(_mutate(adv, "end_encore", ending=dataclasses.replace(e, diamonds=AR.ADV_MAX_DIAMONDS + 1))), "over the cap")
    assert _find(L.problems(_mutate(adv, "end_encore", ending=dataclasses.replace(e, stats={"creativity": 9}))), "outside 1..")
    assert _find(L.problems(_mutate(adv, "end_encore", ending=dataclasses.replace(e, stats={"knowledge": 3, "charisma": 3}))), "total over")
    assert _find(L.problems(_mutate(adv, "end_encore", ending=dataclasses.replace(e, stats={"magic": 1}))), "unknown stat")
    assert _find(L.problems(_mutate(adv, "end_encore", ending=dataclasses.replace(e, gif="kiss"))), "not an allowed SFW")


def test_validator_catches_flags_that_are_never_set_and_hidden_dead_ends():
    adv = C.ADVENTURES["haunted_library"]
    ch = adv.nodes["gates"].choices
    bad = _mutate(adv, "gates", choices=(dataclasses.replace(ch[0], needs=("ghostflag",)), ch[1]))
    p = L.problems(bad)
    assert _find(p, "ghostflag")
    only_flagged = _mutate(adv, "notice", choices=tuple(dataclasses.replace(c, needs=("map",)) for c in adv.nodes["notice"].choices))
    assert _find(L.problems(only_flagged), "no always-visible choice")


def test_validator_catches_a_choice_that_can_never_be_shown():
    adv = C.ADVENTURES["haunted_library"]
    # 'map' is only set on the way to 'notice'; a button needing it on the very first node can never appear
    ch = adv.nodes["gates"].choices
    bad = _mutate(adv, "gates", choices=ch + (C.Choice("Secret door", to="talk", needs=("map",)),))
    assert _find(L.problems(bad), "can never be shown")


def test_validator_catches_bad_checks_and_numbers():
    adv = C.ADVENTURES["canteen_heist"]
    k = C.Check("charm", 2, "door", "window")
    assert _find(L.problems(_mutate(adv, "plan", choices=(C.Choice("x", check=k),) + adv.nodes["plan"].choices[1:])), "unknown check stat")
    k = C.Check("charisma", 9, "door", "window")
    assert _find(L.problems(_mutate(adv, "plan", choices=(C.Choice("x", check=k),) + adv.nodes["plan"].choices[1:])), "unknown difficulty")
    k = C.Check("charisma", 2, "door", "window", bonus=(("decoy", 50),))
    assert _find(L.problems(_mutate(adv, "plan", choices=(C.Choice("x", check=k),) + adv.nodes["plan"].choices[1:])), "flag bonus")
    both = C.Choice("x", to="door", check=C.Check("luck", 1, "door", "window"))
    assert _find(L.problems(_mutate(adv, "plan", choices=(both,))), "exactly one of")
    assert _find(L.problems(dataclasses.replace(adv, energy=AR.ADV_ENERGY_MAX + 1)), "energy")
    assert _find(L.problems(dataclasses.replace(adv, days=(0, 9))), "bad days")
    assert _find(L.problems(dataclasses.replace(adv, start="nope")), "start node")
    assert _find(L.problems(dataclasses.replace(adv, stream_bonus={"music": 99})), "stream bonus")


# ------------------------------------------------------------------ simulation: every ending is really reachable and runs always end
@pytest.mark.parametrize("adv", ADVS, ids=IDS)
def test_random_play_always_ends_and_finds_every_ending(adv):
    stats = {k: 25 for k in AR.STAT_KEYS}
    found = set()
    for seed in range(600):
        rng = random.Random(seed)
        st, node, steps = L.new_state(3), adv.start, 0
        while adv.nodes[node].ending is None:
            vis = L.visible(adv.nodes[node], st["flags"])
            assert vis, (adv.key, node)                                    # never a dead end, whatever the flags
            mv = L.advance(adv, node, st, rng.choice(vis)[0], stats, "arts", rng)
            node, st, steps = mv.node, mv.state, steps + 1
            assert steps <= len(adv.nodes)                                 # no loops: a run is shorter than the story
        found.add(node)
    ends = {k for k, n in adv.nodes.items() if n.ending}
    assert found == ends, (adv.key, ends - found)


@lru_cache(maxsize=None)
def _best_expected_diamonds(key: str, level: int, stream: str = "arts") -> float:
    adv = C.ADVENTURES[key]
    stats = {k: level for k in AR.STAT_KEYS}

    @lru_cache(maxsize=None)
    def ev(node, flags):
        n = adv.nodes[node]
        if n.ending:
            return float(n.ending.diamonds)
        best = 0.0
        for _, c in L.visible(n, flags):
            fl = frozenset(flags | set(c.sets))
            if c.check is None:
                v = ev(c.to, fl)
            else:
                p = L.chance_of(adv, c.check, stats, stream, fl).threshold / 100
                v = p * ev(c.check.success, fl | frozenset(c.check.win_sets)) + (1 - p) * ev(c.check.fail, fl)
            best = max(best, v)
        return best
    return ev(adv.start, frozenset())


@pytest.mark.parametrize("adv", ADVS, ids=IDS)
def test_stats_matter_but_luck_always_has_a_say(adv):
    """With the best play, higher stats are clearly better (monotonic), and even a weak student has a real chance."""
    vals = [_best_expected_diamonds(adv.key, lv) for lv in (10, 25, 40, 60)]
    assert vals == sorted(vals) and vals[-1] - vals[0] >= 4, vals
    assert vals[0] >= 10                                                   # a fresh student still earns something decent
    assert vals[-1] <= AR.ADV_MAX_DIAMONDS


def test_a_streams_bonus_helps_only_its_adventure():
    assert _best_expected_diamonds("rooftop_concert", 25, "music") > _best_expected_diamonds("rooftop_concert", 25, "arts")
    assert _best_expected_diamonds("sports_showdown", 25, "sports") > _best_expected_diamonds("sports_showdown", 25, "arts")
    assert _best_expected_diamonds("missing_paper", 25, "music") == _best_expected_diamonds("missing_paper", 25, "arts")
