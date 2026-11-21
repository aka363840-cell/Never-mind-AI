from game import charades as C


def test_matching():
    assert C.matches("is it an Elephant?", "elephant") and C.matches("elephants!!", "elephant")
    assert C.matches("ICE-CREAM", "ice cream") and C.matches("I think ice   cream", "ice cream")
    assert not C.matches("elephan", "elephant") and not C.matches("the cat sat", "cats a") and not C.matches("catalog", "cat")


def test_words_and_pick():
    assert len(C.WORDS) > 150 and len(set(C.WORDS)) == len(C.WORDS)
    assert all(w == w.lower() and C.norm(w) == w for w in C.WORDS)
    assert C.pick(set(C.WORDS[:-1])) == C.WORDS[-1]
