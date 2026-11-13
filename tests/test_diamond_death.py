"""One currency (diamonds) + death/revive rules. Run: pytest tests/test_diamond_death.py"""
import asyncio
import os
import tempfile

import pytest

DB = os.path.join(tempfile.gettempdir(), "diamond_death_test.db")
URL = f"sqlite+aiosqlite:///{DB}"          # module-local: test files must not share one database


def run(coro):
    return asyncio.run(coro)


async def _setup(funds: int = 10_000):
    from database.session import db
    from game.rules import R
    from services import economy, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in (1, 2, 3):
        await users.touch(uid, f"u{uid}", f"User{uid}")
        async with db.tx() as s:
            await economy.apply(s, uid, wallet=funds - R.start_balance, kind="test_seed", ref=f"seed:{uid}")  # new users already get start_balance
    return db


async def _level(db, uid, level):
    from sqlalchemy import update
    from database.models import User
    async with db.tx() as s:
        await s.execute(update(User).where(User.id == uid).values(level=level))


async def _clear_cd(db, uid, key):
    from services import cooldowns
    async with db.tx() as s:
        await cooldowns.clear(s, uid, key)


async def _bal(uid):
    from services import economy
    return (await economy.balance(uid)).balance


async def _dead(db, uid):
    from services import combat
    async with db.tx() as s:
        return await combat.is_dead(s, uid)


def test_kill_makes_victim_dead_and_killer_gets_diamonds():
    async def go():
        from services import combat
        db = await _setup()
        await _level(db, 1, 60)                       # overwhelming attacker -> wins the fight
        before1, before2 = await _bal(1), await _bal(2)
        o = await combat.attack(1, 2)
        assert o.result.winner == 1 and o.killed == 2
        assert await _dead(db, 2) and not await _dead(db, 1)
        assert o.reward > 0
        assert await _bal(1) > before1 and await _bal(2) < before2     # killer gains, victim loses
        await db.dispose()
    run(go())


def test_dead_player_cannot_kill_until_revived():
    async def go():
        from services import combat
        from services.errors import GameError, InsufficientFunds
        db = await _setup()
        await _level(db, 1, 60)
        await combat.attack(1, 2)                      # 2 is dead now (level 60 vs 1: certain win)
        await _level(db, 2, 60)                        # strong enough to win later fights
        with pytest.raises(GameError) as e:
            await combat.attack(2, 3)                  # dead cannot kill
        assert "dead" in str(e.value).lower() and not await _dead(db, 3)
        with pytest.raises(GameError):
            await combat.attack(1, 2)                  # cannot kill someone already dead
        before = await _bal(2)
        paid = await combat.revive(2)
        assert paid == combat.R.revive_cost and await _bal(2) == before - paid
        assert not await _dead(db, 2)
        await _clear_cd(db, 2, "kill")
        o = await combat.attack(2, 3)                  # alive again -> allowed
        assert o.killed == 3 and await _dead(db, 3)
        with pytest.raises(GameError):
            await combat.revive(2)                     # not dead
        await db.dispose()
    run(go())


def test_revive_needs_diamonds_and_charges_nothing_if_broke():
    async def go():
        from services import combat, economy
        from services.errors import GameError
        db = await _setup()
        await _level(db, 1, 60)
        await combat.attack(1, 2)
        bal = await _bal(2)
        async with db.tx() as s:                       # leave the victim with too little to revive
            await economy.apply(s, 2, wallet=-(bal - 5), kind="test_burn", ref="burn2")
        with pytest.raises(GameError):
            await combat.revive(2)
        assert await _dead(db, 2) and await _bal(2) == 5      # still dead, nothing charged
        await db.dispose()
    run(go())


def test_killer_takes_30_percent_of_free_diamonds_only():
    async def go():
        from services import combat, economy
        db = await _setup()
        await _level(db, 1, 60)
        async with db.tx() as s:                       # victim: 10,000 total -> 5,000 safe in bank
            await economy.apply(s, 2, wallet=-5_000, bank=5_000, kind="test_dep", ref="dep2")
        o = await combat.attack(1, 2)
        assert o.killed == 2 and o.reward == 1_500     # 30% of the 5,000 wallet, bank untouched
        w = await economy.balance(2)
        assert w.balance == 3_500 and w.bank == 5_000
        assert await _bal(1) == 11_500
        await db.dispose()
    run(go())


def test_victim_with_no_free_diamonds_is_already_dead():
    async def go():
        from services import combat, economy
        from services.errors import GameError
        db = await _setup()
        await _level(db, 1, 60)
        async with db.tx() as s:                       # everything in the bank
            await economy.apply(s, 2, wallet=-10_000, bank=10_000, kind="test_dep", ref="dep_all")
        b1 = await _bal(1)
        with pytest.raises(GameError) as e:
            await combat.attack(1, 2)
        assert "already dead" in str(e.value) and await _bal(1) == b1 and not await _dead(db, 2)
        async with db.tx() as s:                       # withdraw -> alive and killable again
            await economy.apply(s, 2, wallet=1, bank=-1, kind="test_wd", ref="wd1")
        o = await combat.attack(1, 2)
        assert o.killed == 2 and o.reward == 1          # tiny wallet still pays at least 1
        await db.dispose()
    run(go())


def test_revive_needs_wallet_not_bank():
    async def go():
        from services import combat, economy
        from services.errors import GameError
        db = await _setup()
        await _level(db, 1, 60)
        await combat.attack(1, 2)
        bal = await _bal(2)
        async with db.tx() as s:                       # move almost everything to the bank
            await economy.apply(s, 2, wallet=-(bal - 10), bank=bal - 10, kind="test_dep", ref="dep3")
        with pytest.raises(GameError) as e:
            await combat.revive(2)
        assert "/withdraw" in str(e.value) and await _dead(db, 2)
        async with db.tx() as s:
            await economy.apply(s, 2, wallet=bal - 10, bank=-(bal - 10), kind="test_wd", ref="wd3")
        await combat.revive(2)
        assert not await _dead(db, 2)
        await db.dispose()
    run(go())


def test_no_kill_cooldown():
    async def go():
        from services import combat
        db = await _setup()
        await _level(db, 1, 60)
        await combat.attack(1, 2)
        await combat.attack(1, 3)                      # immediately again: no cooldown
        assert await _dead(db, 3)
        await db.dispose()
    run(go())


def test_protect_blocks_kills_and_costs_per_hour():
    async def go():
        from services import combat
        from services.errors import GameError
        db = await _setup()
        await _level(db, 1, 60)
        b = await _bal(2)
        secs, price = await combat.protect(2, 2)
        assert secs == 7200 and price == 2 * combat.R.protect_cost_per_hour and await _bal(2) == b - price
        with pytest.raises(GameError):
            await combat.attack(1, 2)
        assert not await _dead(db, 2)
        with pytest.raises(GameError):
            await combat.protect(2, 1)                 # already protected: nothing charged
        assert await _bal(2) == b - price
        for bad in (0, -1, 25, 1000):
            with pytest.raises(GameError):
                await combat.protect(3, bad)
        assert await _bal(3) == 10_000
        await db.dispose()
    run(go())


def test_protect_needs_wallet_diamonds():
    async def go():
        from services import combat, economy
        from services.errors import GameError
        db = await _setup()
        async with db.tx() as s:
            await economy.apply(s, 2, wallet=-10_000, bank=10_000, kind="test_dep", ref="dep4")
        with pytest.raises(GameError):
            await combat.protect(2, 1)
        assert not await _prot(db, 2)
        await db.dispose()
    run(go())


async def _prot(db, uid):
    from services import combat
    async with db.tx() as s:
        return await combat.is_protected(s, uid)


def test_bank_holds_at_most_half():
    async def go():
        from services import economy
        from services.errors import GameError
        db = await _setup()                            # 10,000 wallet
        with pytest.raises(GameError):
            await economy.deposit(1, 5_001)            # over the limit: refused, nothing moves
        assert await _bal(1) == 10_000
        assert await economy.deposit(1, 3_000) == 3_000
        assert await economy.deposit(1, None) == 2_000  # 'all' = as much as allowed (bank -> 5,000)
        w = await economy.balance(1)
        assert (w.balance, w.bank) == (5_000, 5_000)
        with pytest.raises(GameError):
            await economy.deposit(1, 1)                # bank is at 50%
        with pytest.raises(GameError):
            await economy.deposit(1, 0)
        assert await economy.withdraw(1, 1_000) == 1_000
        assert await economy.deposit(1, None) == 1_000  # wallet 6,000 / bank 4,000 -> room 1,000
        await db.dispose()
    run(go())


def test_render_fight_is_short():
    from handlers.combat import render_fight
    from game.engine import FightResult
    from services.combat import FightOutcome
    r = FightResult(winner=1, hp={1: 55, 2: 0}, dealt={1: 1, 2: 1}, log=["x"])
    o = FightOutcome("kill", 1, 2, "Infinite", "Evil", r, 53, 0, 0, {}, 1, 2)
    txt = render_fight(o)
    assert txt.count("\n") == 1 and "tg://user?id=1" in txt and "tg://user?id=2" in txt
    assert " kills " in txt and "defeats" not in txt and "can't kill" not in txt
    assert "Pay 150 💎 with /revive to come back to life." in txt and "Spoils" not in txt and "❤️" not in txt and "Kill attempt" not in txt
    o2 = FightOutcome("kill", 1, 2, "Infinite", "Evil", FightResult(winner=2, hp={1: 0, 2: 5}, dealt={1: 0, 2: 0}, log=[]), 5, 0, 0, {}, 1, None)
    assert "/revive" not in render_fight(o2) and "defeats" in render_fight(o2)


def test_failed_attack_kills_nobody_and_costs_the_attacker():
    async def go():
        from services import combat
        db = await _setup()
        await _level(db, 2, 60)                        # strong defender beats the attacker
        before1, before2 = await _bal(1), await _bal(2)
        o = await combat.attack(1, 2)
        assert o.result.winner == 2 and o.killed is None
        assert not await _dead(db, 1) and not await _dead(db, 2)
        assert await _bal(1) < before1 and await _bal(2) > before2     # loser pays the winner
        await db.dispose()
    run(go())


def test_dead_players_cannot_duel_and_duels_always_have_a_stake():
    async def go():
        from services import combat, duel
        from services.errors import GameError
        db = await _setup()
        await _level(db, 1, 60)
        with pytest.raises(GameError):
            await duel.create(1, 3, 0, 100)            # no free duels any more
        await combat.attack(1, 2)                      # 2 is dead
        with pytest.raises(GameError):
            await duel.create(2, 3, 50, 100)           # dead challenger
        with pytest.raises(GameError):
            await duel.create(3, 2, 50, 100)           # dead target
        d = await duel.create(1, 3, 50, 100)           # alive players can
        assert d.wager == 50
        await db.dispose()
    run(go())


def test_ttt_winner_takes_diamonds_from_loser():
    async def go():
        from services import arcade
        db = await _setup()
        b1, b2 = await _bal(1), await _bal(2)
        moved = await arcade.ttt_record(1, 1, 2)
        assert moved == arcade.TTT_STAKE
        assert await _bal(1) == b1 + moved and await _bal(2) == b2 - moved
        assert await arcade.ttt_record(None, 1, 2) == 0               # draw: nothing moves
        await db.dispose()
    run(go())


def test_ttt_pair_daily_limit_stops_alt_farming():
    async def go():
        from services import arcade
        db = await _setup()
        for _ in range(arcade.TTT_PAIR_DAILY):
            assert await arcade.ttt_record(1, 1, 2) == arcade.TTT_STAKE
        b1 = await _bal(1)
        assert await arcade.ttt_record(1, 1, 2) == 0                  # limit reached: game counts, no diamonds
        assert await arcade.ttt_record(2, 1, 2) == 0                  # the limit is per pair, in both directions
        assert await _bal(1) == b1
        await db.dispose()
    run(go())


def test_ttt_needs_both_players_to_afford_the_stake():
    async def go():
        from services import arcade, economy
        from services.errors import GameError
        db = await _setup()
        await arcade.ttt_check_funds(1, "User1")                      # rich enough -> fine
        async with db.tx() as s:
            await economy.apply(s, 2, wallet=-9_990, kind="test_burn", ref="burn_ttt")
        with pytest.raises(GameError):
            await arcade.ttt_check_funds(2, "User2")                  # 10 💎 < stake
        await db.dispose()
    run(go())


def test_duel_pair_daily_limit():
    async def go():
        from database.models import CombatRecord, utcnow
        from services import duel
        from services.errors import GameError
        db = await _setup()
        async with db.tx() as s:
            for _ in range(duel.R.duel_pair_daily_credit):
                s.add(CombatRecord(attacker_id=1, defender_id=2, kind="duel", winner_id=1, reward=20, created_at=utcnow()))
        with pytest.raises(GameError):
            await duel.create(1, 2, 50, 100)                          # same pair, too many decided duels today
        d = await duel.create(1, 3, 50, 100)                          # a different opponent is fine
        assert d.wager == 50
        await db.dispose()
    run(go())


def test_duel_is_cancelled_and_refunded_if_a_player_dies():
    async def go():
        from database.models import Duel
        from services import combat, duel
        from services.errors import GameError
        db = await _setup()
        await _level(db, 1, 60)
        d = await duel.create(1, 3, 50, 100)
        await duel.accept(d.id, 3)                                    # both stakes escrowed
        b3_escrowed = await _bal(3)
        await combat.attack(1, 3)                                     # 3 gets killed mid-duel
        b3_after_kill = await _bal(3)
        with pytest.raises(GameError):
            await duel.choose(d.id, 1, "b")
        async with db.tx() as s:
            assert (await s.get(Duel, d.id)).status == "expired"
        assert await _bal(3) == b3_after_kill + 50 and b3_escrowed > b3_after_kill     # stake came back
        await db.dispose()
    run(go())


def test_rob_requires_an_amount_and_steals_exactly_that():
    async def go():
        import random
        from services import economy
        from services.errors import GameError
        db = await _setup(5_000)
        with pytest.raises(GameError):                          # below the minimum
            await economy.rob(1, 2, 1)
        with pytest.raises(GameError) as e:                     # more than the wallet allows
            await economy.rob(1, 2, 4_000)
        assert "at most" in str(e.value)
        await _clear_cd(db, 1, "rob")
        class Win(random.Random):
            def random(self): return 0.0                        # always succeeds
        ok, amt, jail = await economy.rob(1, 2, 500, rng=Win())
        assert (ok, amt, jail) == (True, 500, 0)
        assert (await _bal(1)) == 5_500 and (await _bal(2)) == 4_500
        await db.dispose()
    run(go())


def test_rob_poor_target_message_shows_the_numbers():
    async def go():
        from services import economy
        from services.errors import GameError
        db = await _setup(5_000)
        async with db.tx() as s:                                # the target is left with only 100
            await economy.apply(s, 2, wallet=-4_900, kind="test_drain", ref="drain:2")
        with pytest.raises(GameError) as e:
            await economy.rob(1, 2, 50)
        assert "only has <b>100</b>" in str(e.value) and "300" in str(e.value)
        await db.dispose()
    run(go())


def test_rob_failure_fine_scales_with_the_amount():
    async def go():
        import random
        from services import economy
        db = await _setup(5_000)
        class Lose(random.Random):
            def random(self): return 0.99                       # always caught
        ok, fine, jail = await economy.rob(1, 2, 400, rng=Lose())
        assert ok is False and fine == 200 and jail > 0         # 50% of the 400 attempted
        assert (await _bal(1)) == 4_800 and (await _bal(2)) == 5_200
        await db.dispose()
    run(go())


def test_dead_players_cannot_rob_or_be_robbed():
    async def go():
        from services import combat, economy
        from services.errors import GameError
        db = await _setup(5_000)
        await _level(db, 1, 60)
        await combat.attack(1, 2)                               # user 2 dies
        assert await _dead(db, 2)
        before = (await _bal(1), await _bal(2))
        with pytest.raises(GameError) as e:                     # robbing the dead
            await economy.rob(1, 2, 100)
        assert "dead" in str(e.value)
        with pytest.raises(GameError) as e:                     # the dead robbing someone
            await economy.rob(2, 3, 100)
        assert "dead" in str(e.value) and "/revive" in str(e.value)
        assert (await _bal(1), await _bal(2)) == before         # nothing moved, nothing charged
        await db.dispose()
    run(go())
