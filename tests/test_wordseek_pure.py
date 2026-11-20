import random

from game import wordseek as W


def test_feedback_basic():
    assert W.feedback("crane", "crane") == list("ggggg")
    assert W.feedback("crane", "slate") == ["r", "r", "g", "r", "g"]
    assert W.feedback("crane", "nacre") == ["y", "y", "y", "y", "g"]


def test_feedback_repeated_letters():
    assert W.feedback("abbey", "babes") == ["y", "y", "g", "g", "r"]        # two b's in the word, both marked
    assert W.feedback("apple", "paper") == ["y", "y", "g", "y", "r"]
    assert W.feedback("crane", "eerie") == ["r", "r", "y", "r", "g"]        # only one e in the word: the green one wins
    assert W.feedback("hello", "llama") == ["y", "y", "r", "r", "r"]        # hello has two l's, both marked


def test_points_and_absent():
    assert W.points(1) == 35 and W.points(30) == 6 and W.points(99) == 5
    assert W.absent_letters("crane", ["slate", "pound"]) == ["d", "l", "o", "p", "s", "t", "u"]


def test_candidates_and_lists():
    assert W.is_candidate("Hello") and not W.is_candidate("hell") and not W.is_candidate("he llo") and not W.is_candidate("hello!")
    assert W.is_valid("CRANE") and not W.is_valid("zzzzz")
    assert all(w in W.VALID and len(w) == 5 and w.isalpha() for w in W.ANSWERS)
    assert len(W.ANSWERS) > 1000


def test_pick_avoids_recent():
    avoid = set(W.ANSWERS[:-1])
    assert W.pick_word(avoid, random.Random(1)) == W.ANSWERS[-1]
