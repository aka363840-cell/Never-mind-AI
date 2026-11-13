"""Content banks: enough variety, no duplicates, safe for Telegram HTML mode, odds unchanged."""
import re
from collections import defaultdict

BAD_HTML = re.compile(r"[<>]|&(?!(amp|lt|gt|quot);)")


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)


def test_banks_are_big_unique_and_html_safe():
    from services import arcade
    from utils import content
    assert len(arcade.FACTS) >= 200 and len(arcade.RIDDLES) >= 100 and len(arcade.WYR) >= 100
    assert len(arcade._FORTUNES) >= 60
    for pool in (arcade.FACTS, arcade._FORTUNES, arcade._MOOD, arcade._LOVE, arcade._MONEY, arcade._TIPS, arcade._COLORS):
        assert len(pool) == len(set(pool))
    assert len({q for q, _ in arcade.RIDDLES}) == len(arcade.RIDDLES)
    assert len(set(arcade.WYR)) == len(arcade.WYR)
    for name in dir(content):
        if name.startswith("_") or not isinstance(getattr(content, name), (list, dict)):
            continue
        for s in _strings(getattr(content, name)):
            assert not BAD_HTML.search(re.sub(r"</?b>", "", s)), (name, s)


def test_format_placeholders_are_safe():
    from utils import content
    for s in content.DARE_MORE:
        s.format(bot="Bot")
    for s in content.WELCOME_MORE + content.GOODBYE_MORE:
        s.format(n="A", g="B", e="C")
    for tales in content.EXP_TALES_MORE.values():
        for s in tales:
            s.format(p="Rex")


def test_trivia_questions_are_valid():
    from handlers import extras
    qs = [q for q, _, _ in extras.QUESTIONS]
    assert len(qs) == len(set(qs)) and len(qs) >= 100
    for q, opts, right in extras.QUESTIONS:
        assert len(opts) >= 2 and len(set(opts)) == len(opts) and 0 <= right < len(opts)


def test_every_sign_has_flavour_lines_and_horoscope_is_stable():
    from datetime import date
    from services import arcade
    assert set(arcade._SIGN_VIBE) == {n for _, n in arcade.SIGNS}
    d = date(2026, 11, 13)
    for _, n in arcade.SIGNS:
        assert arcade.horoscope(n, d) == arcade.horoscope(n.lower(), d)
        assert "Lucky time" in arcade.horoscope(n, d)


def test_fish_and_mine_odds_and_rewards_unchanged():
    from services import extras
    for table, expected in ((extras.FISH_TABLE, [28, 34, 20, 11, 6, 1]), (extras.MINE_TABLE, [25, 35, 22, 12, 5, 1])):
        tiers = defaultdict(float)
        for w, lo, hi, _ in table:
            tiers[(lo, hi)] += w
        assert [round(v) for v in tiers.values()] == expected
    assert sum(1 for *_, t in extras.FISH_TABLE if t.startswith("🐋")) == 1   # the whale achievement hook still matches


def test_deck_never_repeats_until_exhausted():
    from utils import deck
    first = [deck.draw("unit", 7, 123) for _ in range(7)]
    assert sorted(first) == list(range(7))
    prev = first[-1]
    for _ in range(500):
        nxt = deck.draw("unit", 7, 123)
        assert nxt != prev
        prev = nxt
