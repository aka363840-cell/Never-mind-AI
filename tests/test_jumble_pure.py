"""Word Jumble: pure rules, stats helpers and /help wiring (no Telegram, no database). Run: pytest tests/test_jumble_pure.py"""
import random
from datetime import date, datetime, timedelta

from game import jumble as D, jumble_stats as S, jumble_words as W
from services import jumble as svc
from utils import helpcatalog as C


# ------------------------------------------------------------------ word data
def test_word_packs_are_clean():
    for theme, by_len in W.THEMES.items():
        assert theme in D.THEME_LABELS
        for n, words in by_len.items():
            assert len(words) >= 10, (theme, n)
            assert len(set(words)) == len(words), (theme, n)
            assert all(len(w) == n and w.isalpha() and w.islower() for w in words), (theme, n)
    for n, words in W.EXTRA.items():
        assert n in (7, 8) and len(words) >= 50 and len(set(words)) == len(words)
        assert all(len(w) == n and w.isalpha() and w.islower() for w in words)
    assert set(D.THEME_LABELS) == set(W.THEMES) | {"all"}


def test_every_difficulty_and_theme_has_words():
    for key, d in D.DIFFS.items():
        assert D.diff(key) is d and d.time_s > 0 and d.base > 0
        assert all(D._pool(n) for n in d.lengths), key
    for theme in W.THEMES:
        for n in (4, 5, 6):
            assert D._pool(n, theme), (theme, n)
    assert D.diff("mixed") is D.DIFFS[D.DEFAULT_DIFF] and D.diff("nonsense") is D.DIFFS[D.DEFAULT_DIFF]
    assert set(D.CHOICES) == set(D.DIFFS) | {"mixed"}
    assert D.DEFAULT_ROUNDS in D.ROUND_CHOICES and D.DEFAULT_TARGET in D.TARGET_CHOICES


# ------------------------------------------------------------------ puzzles
def test_scramble_is_a_different_arrangement():
    rng = random.Random(11)
    for key in D.DIFFS:
        for _ in range(60):
            word, scr, ans = D.new_puzzle(key, rng=rng)
            assert len(word) in D.DIFFS[key].lengths
            assert len(scr) == len(word) and sorted(scr) == sorted(word) and scr != word
            assert word in ans and all(sorted(a) == sorted(word) for a in ans)


def test_themed_puzzles_use_the_theme():
    rng = random.Random(2)
    for theme in W.THEMES:
        for key in ("easy", "medium", "hard"):
            for _ in range(15):
                word, _, _ = D.new_puzzle(key, theme, rng=rng)
                assert word in W.THEMES[theme][len(word)]
    word, _, _ = D.new_puzzle("extreme", "animals", rng=rng)      # no animal words of 7-8 letters: falls back to any words
    assert len(word) in (7, 8)


def test_anagrams_are_accepted():
    for w in list(D._pool(5))[:200] + list(D._pool(7))[:30]:
        ans = D.anagrams(w)
        assert w in ans and all(len(a) == len(w) and sorted(a) == sorted(w) for a in ans)


def test_words_are_not_repeated_inside_a_game():
    rng, used = random.Random(5), set()
    for _ in range(10):
        word = D.pick_word((5,), "all", used, rng)
        assert word not in used
        used.add(word)
    assert D.pick_word((5,), "all", set(D._pool(5)), rng)          # everything used up: starts over instead of failing


def test_answer_format():
    assert D.is_guess("tree", (4,)) and D.is_guess(" Tree ", (4,)) and not D.is_guess("trees", (4,))
    assert D.is_guess("balance", (7, 8)) and D.is_guess("absolute", (7, 8)) and not D.is_guess("six", (7, 8))
    assert not D.is_guess("tre3", (4,)) and not D.is_guess("tr ee", (4,)) and not D.is_guess("", (4,)) and not D.is_guess("tréé", (4,))


# ------------------------------------------------------------------ modes, speed, hints, points
def test_mixed_difficulty_climbs():
    for total in (3, 5, 10):
        seq = [D.round_diff("mixed", i, total) for i in range(1, total + 1)]
        assert seq[0] == "easy" and seq[-1] == "hard" and seq == sorted(seq, key=["easy", "medium", "hard"].index)
        assert "medium" in seq
    race = [D.round_diff("mixed", i, 40, race=True) for i in (1, 3, 4, 7, 8, 40)]
    assert race == ["easy", "easy", "medium", "medium", "hard", "hard"]
    assert D.round_diff("hard", 1, 5) == "hard" and D.round_diff("zzz", 1, 5) == D.DEFAULT_DIFF


def test_speed_changes_the_time():
    assert D.time_for("medium", "fast") < D.time_for("medium", "normal") < D.time_for("medium", "relaxed")
    assert D.time_for("medium", "normal") == D.DIFFS["medium"].time_s and D.time_for("easy", "bogus") == D.DIFFS["easy"].time_s


def test_hints_and_points():
    t = D.time_for("medium")
    h1, h2 = D.hint_times(t)
    assert 0 < h1 < h2 < t
    assert D.hint("grape", 1) == "G _ _ _ _" and D.hint("grape", 2) == "G R _ _ _" and D.hint("grape", 99) == "G R A P _"
    for key, d in D.DIFFS.items():
        fast, mid, slow = (D.points(key, d.time_s * f, d.time_s) for f in (0.1, 0.5, 0.9))
        assert fast == d.base + 2 and mid == d.base + 1 and slow == d.base
        assert D.points(key, d.time_s * 0.5, d.time_s, hints=1) == d.base          # a hint costs 1 point
        assert D.points(key, d.time_s * 0.9, d.time_s, hints=9) == 1                # never below 1
    assert D.points("extreme", 1) > D.points("hard", 1) > D.points("easy", 1)
    assert D.show("abc") == "A  B  C"


# ------------------------------------------------------------------ stats, streaks, rewards
def test_ledger_ref_roundtrip():
    ref = S.make_ref(-1001234567890, "ab12cd34", 42, 17, 5, 2)
    assert S.parse_ref(ref) == (-1001234567890, "ab12cd34", 42, 17, 5, 2)
    for bad in ("", None, "hack:1:b:2", "jumblebet:ab:b:1", "jumble:x:y:z", "jumble:1:g:u:1:2:3"):
        assert S.parse_ref(bad) is None
    assert not (ref.startswith("jumblebet:"))                                      # a stats ref never matches the bet refs


def test_aggregate_and_top():
    rows = [(1, S.make_ref(-1, "a", 1, 10, 3, 1)), (2, S.make_ref(-1, "a", 2, 4, 1, 2)),
            (1, S.make_ref(-1, "b", 1, 6, 2, 2)), (2, S.make_ref(-1, "b", 2, 9, 3, 1)), (3, "garbage")]
    st = S.aggregate(rows)
    assert (st[1].points, st[1].words, st[1].games, st[1].wins) == (16, 5, 2, 1) and 3 not in st
    assert [u for u, _ in S.top(st)] == [1, 2] and len(S.top(st, 1)) == 1


def test_periods():
    now = datetime(2026, 11, 21, 15, 30)
    assert S.since("today", now) == datetime(2026, 11, 21) and S.since("week", now) == now - timedelta(days=7)
    assert S.since("month", now) == now - timedelta(days=30) and S.since("all", now) is None
    assert set(S.PERIODS) == {"today", "week", "month", "all"}


def test_streaks():
    t = date(2026, 11, 21)
    d = lambda n: t - timedelta(days=n)
    assert S.streak_from_days(set(), t) == 0
    assert S.streak_from_days({t}, t) == 1 and S.streak_from_days({d(1)}, t) == 1          # yesterday still counts
    assert S.streak_from_days({t, d(1), d(2)}, t) == 3 and S.streak_from_days({d(1), d(2), d(3)}, t) == 3
    assert S.streak_from_days({t, d(2)}, t) == 1 and S.streak_from_days({d(2), d(3)}, t) == 0


def test_rewards():
    base, bonus = S.reward(10, 0, 0)
    assert (base, bonus) == (10 * S.REWARD_PER_POINT, 0)
    r3, b3 = S.reward(10, 3, 0)
    assert b3 == 3 * S.STREAK_STEP_PCT and r3 > base
    assert S.reward(10, 99, 0)[1] == S.STREAK_MAX_DAYS * S.STREAK_STEP_PCT                  # streak bonus is capped
    assert S.reward(100, 0, S.DAILY_REWARD_CAP - 7)[0] == 7                                  # only the room left under the daily cap
    assert S.reward(100, 5, S.DAILY_REWARD_CAP)[0] == S.MIN_REWARD                          # cap reached: a token reward keeps the stats row


def test_pot_split():
    assert S.split_pot(1000, 1) == [1000] and S.split_pot(1000, 3) == [334, 333, 333] and S.split_pot(100, 0) == []
    assert sum(S.split_pot(997, 4)) == 997 and S.split_pot(1000, 1, fee_pct=10) == [900]


# ------------------------------------------------------------------ service state + help
def test_game_state():
    g = svc.JumbleGame(1, "abcd", 2)
    assert g.phase == "setup" and g.rounds == D.DEFAULT_ROUNDS and g.diff == D.DEFAULT_DIFF and not g.open and g.pot == 0
    g.entry, g.players = 500, {1: "a", 2: "b", 3: "c"}
    assert g.pot == 1500
    svc.GAMES[1] = g
    try:
        assert svc.running(1) == "jumble" and svc.running(2) is None
    finally:
        svc.GAMES.pop(1, None)


def test_jumble_is_in_help_but_not_in_the_slash_menu():
    names = {c for c, _ in C.menu("Bot", "*", True)}
    assert not names & {"jumble", "jum", "jlb", "jstats", "jskip"}
    desc = dict(C.DESC_LIST)
    for c in ("jumble", "jlb", "jstats", "jskip"):
        assert c in desc, c
    assert "/jum" in desc["jumble"] and "Jumble" in desc["endgame"]
    assert "jum" in C.ALIASES["jumble"]
    games = dict(C.CATEGORIES)["🕹 Games"]
    assert all(c in games for c in ("jumble", "jlb", "jstats", "jskip"))


def test_scramble_is_really_mixed_up():
    rng = random.Random(5)
    for key in ("easy", "medium", "hard", "extreme"):
        for _ in range(150):
            word, scr, _ans = D.new_puzzle(key, rng=rng)
            assert sorted(scr) == sorted(word) and scr != word
            assert D._fixed(word, scr) <= max(1, len(word) // 4), (word, scr)


def test_is_word_for_near_miss():
    assert D.is_word("house") and D.is_word(" House ") and not D.is_word("hoose") and not D.is_word("ab") and not D.is_word("h0use")


def test_tile_picture():
    from game.jumble_img import render_tiles
    for w in ("cats", "house", "planet", "balance", "absolute"):
        png = render_tiles(D.scramble(w), random.Random(1))
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and 5_000 < len(png) < 400_000


def test_solo_summary():
    assert S.solo_summary({}) is None and S.solo_summary({S.SOLO_BEST: 9}) is None        # no finished solo game yet
    sm = S.solo_summary({S.SOLO_GAMES: 3, S.SOLO_WORDS: 12, S.SOLO_POINTS: 40, S.SOLO_BEST: 18})
    assert sm == {"games": 3, "words": 12, "points": 40, "best": 18}
    assert all(len(k) <= 32 for k in (S.SOLO_GAMES, S.SOLO_WORDS, S.SOLO_POINTS, S.SOLO_BEST))   # user_stats.key is String(32)
