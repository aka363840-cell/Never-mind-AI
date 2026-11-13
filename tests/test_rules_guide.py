"""/rules must state the real numbers from game.rules (never hard-coded) and every topic must render as valid HTML."""
import re


def _plain(t):
    return re.sub(r"<[^>]+>", "", t)


def test_every_rule_page_renders_with_real_numbers():
    from game.rules import R
    from handlers import views
    home, kbd = views.rules()
    assert len(kbd.inline_keyboard[0]) == 2 and "Pick a topic" in home
    for key, _, _ in views.RULE_TOPICS:
        text, markup = views.rules(key)
        assert len(_plain(text)) < 4000
        assert not re.search(r"&(?!(amp|lt|gt);)", text)            # Telegram HTML safe
        assert markup.inline_keyboard[-1][0].callback_data == "rules:home"
    kill = _plain(views.rules("kill")[0]); rob = _plain(views.rules("rob")[0])
    assert f"{R.kill_reward_pct * 100:g}%" in kill and f"{R.revive_cost}" in kill and f"{R.kill_pair_daily_limit}×" in kill
    assert f"{R.rob_min_steal}" in rob and f"{R.rob_success_chance * 100:g}%" in rob and f"{R.rob_target_min_wallet}" in rob
    assert "Pick a topic" in _plain(views.rules("nope")[0])          # unknown topic falls back to the menu


def test_rules_command_is_in_help_menu_and_catalog():
    from utils import helpcatalog
    assert "rules" in helpcatalog.DESC and "rules" in helpcatalog.MENU
    assert any("rules" in cmds for _, cmds in helpcatalog.CATEGORIES)


def test_stances_are_explained_with_the_real_multipliers():
    from game.engine import STRATEGIES, stance_help
    h = stance_help()
    for lbl, a, d in STRATEGIES.values():
        assert lbl in h
    assert "+25% attack" in h and "+30% defence" in h
    from handlers import views
    assert "stance" in views.rules("duel")[0]


def test_leaderboard_tabs_have_words():
    import asyncio
    from handlers import views
    from tests.test_shop_visibility import _setup
    asyncio.run(_setup())
    _, markup = asyncio.run(views.rankings("rich", 0, 1))
    labels = [b.text for b in markup.inline_keyboard[0]]
    assert labels == ["• 💎 Rich", "⚔️ Kills", "⭐ Level", "🏆 Wins"]
