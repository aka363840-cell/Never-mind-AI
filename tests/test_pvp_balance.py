"""PvP (/kill, /revenge, wagered /duel) is decided by level, gear and luck - not by powers, potions or one big item."""
import random

from game.engine import Fighter, resolve_pvp
from game.rules import R


def _f(uid, lv, atk=0, dfn=0, hp=0, power=None):
    return Fighter(uid, f"p{uid}", R.base_hp + lv * R.hp_per_level + hp, R.base_attack + lv * R.attack_per_level + atk,
                   R.base_defence + lv * R.defence_per_level + dfn, power, 1.0, 3 if power else 0)


def _win_rate(make_a, make_b, n=3000):
    rng, w = random.Random(11), 0
    for _ in range(n):
        r = resolve_pvp(make_a(), make_b(), rng, a_first=True)
        w += 1 if r.winner == 1 else 0.5 if r.winner is None else 0
    return w / n


def test_equal_fighters_are_a_coin_flip():
    assert 0.44 <= _win_rate(lambda: _f(1, 5), lambda: _f(2, 5)) <= 0.56


def test_one_level_or_one_item_helps_but_does_not_decide():
    assert 0.52 <= _win_rate(lambda: _f(1, 6), lambda: _f(2, 5)) <= 0.72           # +1 level
    assert _win_rate(lambda: _f(1, 5), lambda: _f(2, 5, dfn=10)) >= 0.25           # a +10 defence item is no wall


def test_powers_do_nothing_in_pvp():
    base = _win_rate(lambda: _f(1, 5), lambda: _f(2, 5))
    for p in ("PHANTOM", "MIRROR", "REAPER", "PHOENIX"):
        assert abs(_win_rate(lambda: _f(1, 5), lambda: _f(2, 5, power=p)) - base) < 0.04, p


def test_a_big_level_gap_still_wins_almost_always():
    assert _win_rate(lambda: _f(1, 10), lambda: _f(2, 1)) >= 0.9


def test_kill_reward_is_capped_like_rob():
    assert R.kill_reward_max == R.rob_max_steal and R.kill_loss_pct >= 0.10


def test_kills_have_a_short_cooldown():
    assert 0 < R.kill_cooldown_s <= 120
