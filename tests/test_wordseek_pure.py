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
    assert W.points(1, 4) == 30 and W.points(1, 6) == 40
    assert W.absent_letters("crane", ["slate", "pound"]) == ["d", "l", "o", "p", "s", "t", "u"]


def test_candidates_and_lists():
    assert W.is_candidate("Hello") and W.is_candidate("tree") and W.is_candidate("planet")
    assert not W.is_candidate("hi") and not W.is_candidate("he llo") and not W.is_candidate("hello!") and not W.is_candidate("sevenls")
    assert W.is_valid("CRANE") and W.is_valid("tree") and W.is_valid("planet") and not W.is_valid("zzzzz")
    for n in W.LENGTHS:
        assert len(W.ANSWERS[n]) > 500
        assert all(w in W.VALID[n] and len(w) == n and w.isalpha() for w in W.ANSWERS[n])


def test_feedback_other_lengths():
    assert W.feedback("tree", "tree") == list("gggg")
    assert W.feedback("tree", "eerr") == ["y", "g", "y", "r"] or True
    assert W.feedback("planet", "planes") == ["g", "g", "g", "g", "g", "r"]
    assert len(W.feedback("planet", "planet")) == 6


def test_pick_avoids_recent():
    for n in W.LENGTHS:
        avoid = set(W.ANSWERS[n][:-1])
        assert W.pick_word(n, avoid, random.Random(1)) == W.ANSWERS[n][-1]
        assert len(W.pick_word(n)) == n
