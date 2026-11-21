"""Wordly + Hack: pure rules and /help wiring (no Telegram, no database). Run: pytest tests/test_wordgames_pure.py"""
import random

from game import hack as H, hack_stats as HS, wordly as WL
from utils import helpcatalog as C


def test_hack_score():
    assert H.score("1234", "1234") == (4, 0)
    assert H.score("1234", "4321") == (0, 4)
    assert H.score("1234", "1243") == (2, 2)
    assert H.score("1234", "5678") == (0, 0)
    assert H.score("1234", "1111") == (1, 0)          # a repeated digit in a guess is matched only once
    assert H.score("012", "210") == (1, 2)


def test_hack_codes_and_guess_format():
    rng = random.Random(7)
    for n in H.LENGTHS:
        for _ in range(50):
            c = H.new_code(n, rng)
            assert len(c) == n and len(set(c)) == n and c.isdigit()
        assert H.max_guesses(n) >= 8
    assert H.is_guess("0123", 4) and H.is_guess(" 4821 ", 4) and not H.is_guess("123", 4) and not H.is_guess("12345", 4)
    assert not H.is_guess("12a4", 4) and not H.is_guess("12 4", 4)


def test_wordly_rack_and_words():
    rng = random.Random(3)
    for _ in range(5):
        rack, sols = WL.make_rack(rng)
        assert len(rack) == WL.RACK and rack.isalpha() and len(sols) >= WL.MIN_WORDS
        assert all(WL.can_make(w, rack) and WL.MIN_LEN <= len(w) <= WL.MAX_LEN for w in sols)
        assert any(len(w) == 3 for w in sols)
        assert len(WL.solutions(rack, common=True)) >= WL.MIN_COMMON
    assert WL.can_make("tree", "treasure"[:8]) and not WL.can_make("eeee", "treasure"[:8])
    assert not WL.can_make("tree", "trxsabcd")         # needs two e's
    assert WL.points("cat") == 1 and WL.points("tree") == 2 and WL.points("trees") == 4 and WL.points("treads") == 7 and WL.points("trained") == 11 and WL.points("training") == 16
    assert WL.is_candidate("Tree") and WL.is_candidate("cat") and WL.is_candidate("balanced") and not WL.is_candidate("ab") and not WL.is_candidate("ninelette") and not WL.is_candidate("seven77")


def test_games_are_in_help_but_not_in_the_slash_menu():
    names = {c for c, _ in C.menu("Bot", "*", True)}
    assert not names & {"wordly", "wl", "hack3", "hack4", "hack5", "hack6", "joinhack", "hackdaily", "hstats", "hlb", "end"}
    desc = dict(C.DESC_LIST)
    for c in ("wordly", "hack3", "hack4", "hack5", "hack6", "joinhack", "hackdaily", "hstats", "hlb"):
        assert c in desc, c
    assert "wl" in C.ALIASES["wordly"]


def test_extra_words():
    from game.wordly_data import EXTRA
    assert set(EXTRA) == {3, 7, 8} and len(EXTRA[3]) > 300 and len(EXTRA[7]) > 400 and len(EXTRA[8]) > 400
    for n, ws in EXTRA.items():
        assert all(len(w) == n and w.isalpha() and w.islower() for w in ws)
    assert "cat" in WL.solutions("catsmore")
    # an 8-letter rack made from an 8-letter word allows that word
    rng = random.Random(11)
    full = [r for r, s in (WL.make_rack(rng) for _ in range(30)) if any(len(w) == 8 for w in s)]
    assert full


def test_hack_repeats_and_tracker():
    assert H.has_repeats("1123") and not H.has_repeats("1234")
    hist = [("a", "1234", 0, 0), ("b", "1567", 1, 0), ("c", "5678", 0, 0)]
    assert H.eliminated(hist) == list("12345678")             # every digit of a 0/0 guess is certainly not in the code
    assert H.eliminated([("a", "1234", 1, 0)]) == []



def test_hack_ranks():
    assert HS.rank_for(0) == ("🐣", "Script Kiddie") and HS.rank_for(2)[1] == "Script Kiddie"
    assert HS.rank_for(3)[1] == "Packet Sniffer" and HS.rank_for(25)[1] == "Cipher Breaker" and HS.rank_for(100)[1] == "Elite Hacker"
    assert HS.rank_for(10_000)[1] == "Elite Hacker"
    assert HS.next_rank(0) == (3, "📡 Packet Sniffer") and HS.next_rank(9) == (1, "🎩 Black Hat") and HS.next_rank(100) is None
    wins = [r[0] for r in HS.RANKS]
    assert wins == sorted(wins) and wins[0] == 0


def test_hack_summary():
    sm = HS.summarize({"hack_play": 10, "hack_win": 4, "hack_crack": 3, "hack_tries": 15, "hack_fast": 3})
    assert (sm["play"], sm["win"], sm["rate"], sm["avg_tries"], sm["fast"]) == (10, 4, 40, 5.0, 3)
    assert sm["rank"][1] == "Packet Sniffer" and sm["next"] == (6, "🎩 Black Hat")
    empty = HS.summarize({})
    assert empty["rate"] == 0 and empty["avg_tries"] is None and empty["fast"] is None
    assert all(len(k) <= 32 for k in (HS.K_PLAY, HS.K_WIN, HS.K_CRACK, HS.K_TRIES, HS.K_FAST))   # user_stats.key is String(32)


def test_daily_code():
    from datetime import date
    d = date(2026, 11, 21)
    c = HS.daily_code(d, "secret")
    assert c == HS.daily_code(d, "secret")                      # same for everybody, stable across restarts
    assert len(c) == HS.DAILY_LEN and len(set(c)) == len(c) and c.isdigit()
    assert any(HS.daily_code(date(2026, 11, 22 + i % 5), "secret") != c for i in range(5))   # changes from day to day
    assert any(HS.daily_code(d, f"other{i}") != c for i in range(5))                         # depends on the secret
    for n in H.LENGTHS:
        x = HS.daily_code(d, "secret", n)
        assert len(x) == n and len(set(x)) == n
    assert HS.daily_ref(d, 42) == "hackdaily:20261121:42" and HS.daily_ref(d, 42).startswith(HS.daily_ref_prefix(d))
    assert len(HS.daily_ref(d, 2 ** 40)) <= 128


def test_daily_reward():
    top = HS.DAILY_BASE + HS.DAILY_PER_TRY * (H.max_guesses(4) - 1)
    assert HS.daily_reward(1, 0) == (top, 0)
    assert HS.daily_reward(H.max_guesses(4), 0) == (HS.DAILY_BASE, 0)          # solved on the very last try
    assert HS.daily_reward(1, 4) == (top + top * 20 // 100, 20)                # +5 % per streak day
    assert HS.daily_reward(1, 99)[1] == 50                                     # capped at +50 %
    assert HS.daily_reward(3, 0)[0] > HS.daily_reward(4, 0)[0]                 # fewer tries pay more


def test_daily_history_rows():
    rows = HS.history_rows("1234", ["1243", "5678"])
    assert rows == [("", "1243", 2, 2), ("", "5678", 0, 0)]
    assert H.eliminated(rows) == list("5678")
