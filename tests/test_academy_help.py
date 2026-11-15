"""Part 7: /academyhelp - pages generated from the rules, buttons, owner check, the Academy help category."""
import re
from datetime import datetime, timezone

import pytest

from test_academy_adventures import Harness, _owner1, _setup, run, _detach_routers  # the same fake-Telegram dispatcher harness

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ACADEMY_BREAK_HOURS", raising=False)
    monkeypatch.delenv("ACADEMY_TZ", raising=False)
    monkeypatch.setenv("ACADEMY_ENABLED", "1")
    from utils import gifs
    monkeypatch.setattr(gifs, "ENABLED", False)
    yield
    _detach_routers()


def _all_pages():
    from services import academy_help as H
    return {k: H.render(k, T0 if k == "week" else None, "Season 1") for k in ["home", *H.PAGE_KEYS]}


def test_every_page_renders_short_and_valid_html():
    for k, t in _all_pages().items():
        assert 100 < len(t) < 4000, k
        assert "None" not in t and "{" not in t and "}" not in t, k
        for tag in ("b", "i", "code"):
            assert t.count(f"<{tag}>") == t.count(f"</{tag}>"), (k, tag)
        assert "&" not in re.sub(r"&(amp|lt|gt);", "", t), k      # a raw & would break HTML parse mode


def test_numbers_follow_the_rules(monkeypatch):
    from game import academy_rules as AR
    from services import academy_help as H
    monkeypatch.setattr(AR, "ENERGY_MAX", 137)
    monkeypatch.setattr(AR, "DAILY_DIAMOND_CAP", 777)
    monkeypatch.setattr(AR, "ROUND_SECONDS", 33)
    monkeypatch.setattr(AR, "SCORE_REF", 41)
    monkeypatch.setattr(AR, "PROBATION_GPA", 4.7)
    monkeypatch.setattr(AR, "RIVAL_WIN_DIAMONDS", 4321)
    monkeypatch.setattr(AR, "DATE_DIAMONDS", 9876)
    monkeypatch.setattr(AR, "ADV_EXPIRE_MINUTES", 59)
    assert "137" in H.render("start") and "777" in H.render("daily")
    ex = H.render("exams")
    assert "33 seconds" in ex and "× 41" in ex and "4.7" in ex
    assert "4321" in H.render("social") and "9876" in H.render("social")
    assert "59 minutes" in H.render("adv") and "× 41" in H.render("how")


def test_week_page_shows_real_times_in_the_academy_timezone(monkeypatch):
    from services import academy_help as H
    t = H.render("week", T0)
    assert "IST" in t and "14:30" in t                        # 09:00 UTC = 14:30 IST
    for d in range(1, 8):
        assert f"Day {d}" in t
    monkeypatch.setenv("ACADEMY_TZ", "UTC")
    t2 = H.render("week", T0)
    assert "UTC" in t2 and "09:00" in t2 and "IST" not in t2
    off = H.render("week", None)                              # no season yet: offsets, still every day
    assert "after the season opens" in off and all(f"Day {d}" in off for d in range(1, 8))


def test_resolve_and_nav():
    from services import academy_help as H
    assert H.resolve(None) == "home" and H.resolve("WEEK") == "week" and H.resolve("/privacy") == "how2"
    assert H.resolve("exam") == "exams" and H.resolve("nonsense") is None
    assert H.nav("start") == (None, "daily") and H.nav("how2") == ("how", None) and H.nav("home") == (None, None)
    for k in H.PAGE_KEYS:
        assert H.resolve(k) == k


def test_callback_data_is_short_even_for_the_biggest_telegram_id():
    from handlers import academy_help as AH
    from utils.content import academy_help_texts as T
    big = 9_999_999_999_999_999
    for page in ["home", *T.PAGES]:
        for row in AH.keyboard(page, big).inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode()) < 64


def test_help_is_in_the_help_category_but_not_in_the_slash_menu():
    from utils import helpcatalog as HC
    cat = dict(HC.CATEGORIES)
    assert "academyhelp" in cat["Academy 🎓"] if "Academy 🎓" in cat else any("academyhelp" in v for v in cat.values())
    assert "academyhelp" in dict(HC.DESC_LIST)
    assert "academyhelp" not in [c for c, *_ in HC.MENU] and "academyhelp" not in HC.GROUP_MENU_EXTRA


def test_academyhelp_through_the_dispatcher(monkeypatch):
    async def go():
        _owner1(monkeypatch)
        await _setup(3, season=False)
        h = Harness()
        out = await h.say(2, "/academyhelp")
        assert "Academy help" in out[0] and "ach:start:2" in h.buttons()
        out = await h.tap(2, "ach:exams:2")
        assert "strategy" not in out[0] or "Exams" in out[0]
        assert "do not need to know any subject" in out[0]
        assert {"ach:daily:2", "ach:home:2", "ach:adv:2"} <= set(h.buttons())
        out = await h.tap(3, "ach:exams:2")                          # someone else presses: popup only, nothing edited
        assert out == [] and h.alerts()
        out = await h.say(2, "/academyhelp week")                    # creates / reads the season, real times
        assert "Day 7" in out[0] and "Times are in" in out[0]
        out = await h.say(2, "/academyhelp privacy")
        assert "Privacy" in out[0] and "/academyremind off" in out[0]
        out = await h.tap(2, "ach:bogus:2")                          # junk data: ignored quietly
        assert out == []
        await h.say(2, "/academyhelp zzz")                           # an unknown word -> the menu
        monkeypatch.setenv("ACADEMY_ENABLED", "0")
        out = await h.say(2, "/academyhelp")                         # documentation still works when the Academy is off
        assert "closed" in out[0]
        from database.session import db
        await db.dispose()
    run(go())
