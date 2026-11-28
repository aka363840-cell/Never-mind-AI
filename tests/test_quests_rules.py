"""Daily quests: the 'finish every daily quest' goal matches the other quests, and every quest event is really emitted."""
from game.rules import R


def test_all_daily_goal_is_the_number_of_other_daily_quests():
    others = [m for m in R.daily_missions if m[0] != "all_daily"]
    allq = next(m for m in R.daily_missions if m[0] == "all_daily")
    assert allq[3] == len(others)


def test_quest_keys_are_unique_and_rewards_are_small():
    keys = [m[0] for m in R.daily_missions + R.weekly_missions]
    assert len(keys) == len(set(keys))
    assert sum(m[4] for m in R.daily_missions) <= 2500          # a full day of quests is a bonus, not the main income


def test_every_daily_quest_event_is_emitted_somewhere():
    import pathlib
    src = "".join(p.read_text("utf-8") for d in ("services", "handlers") for p in pathlib.Path(d).glob("*.py"))
    for key, _t, event, _g, _r in R.daily_missions:
        assert (f'"{event}"' in src) or event in ("fish", "mine"), (key, event)
    assert 'progress.emit(s, uid, "work")' in src and 'progress.emit(s, uid, "hunt")' in src and 'progress.emit(s, uid, game)' in src
    assert '"daily_all"' in src
