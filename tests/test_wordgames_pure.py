"""Wordly + Hack: pure rules and /help wiring (no Telegram, no database). Run: pytest tests/test_wordgames_pure.py"""
import random

from game import hack as H, wordly as WL
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
    assert not names & {"wordly", "wl", "hack3", "hack4", "hack5", "hack6", "joinhack", "end"}
    desc = dict(C.DESC_LIST)
    for c in ("wordly", "hack3", "hack4", "hack5", "hack6", "joinhack"):
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
