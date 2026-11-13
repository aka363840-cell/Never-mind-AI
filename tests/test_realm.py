"""Realm regression tests (dungeon / expeditions / heists / achievements). Run: pytest tests/test_realm.py"""
import asyncio
import os
import random
import tempfile
from datetime import timedelta

import pytest
from sqlalchemy import update

DB = os.path.join(tempfile.gettempdir(), "realm_test.db")
URL = f"sqlite+aiosqlite:///{DB}"          # module-local: test files must not share one database


def run(coro):
    return asyncio.run(coro)


async def _setup():
    from database.models import User, utcnow
    from database.session import db
    from services import economy, shop, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    await shop.seed_catalog()
    for uid in (1, 2, 3):
        await users.touch(uid, f"u{uid}", f"User{uid}")
        async with db.tx() as s:
            await s.execute(update(User).where(User.id == uid).values(level=5, created_at=utcnow() - timedelta(days=5)))
    return db


def test_dungeon_payout_matches_wallet_and_run_is_deleted():
    async def go():
        from handlers import realm as H
        from services import cooldowns, economy, realm
        from services.errors import GameError
        db = await _setup()
        rng = random.Random(1)
        for i in range(25):
            async with db.tx() as s:
                await cooldowns.clear(s, 1, "dungeon")
            before = (await economy.balance(1)).balance
            await realm.dungeon_enter(1, hard=False, rng=rng)
            res = None
            while res is None:
                st = await realm.dungeon_get(1)
                _, kbd = H.render_run(st)
                btns = [b.callback_data.split(":")[2] for r in kbd.inline_keyboard for b in r]
                st, res = await realm.dungeon_act(1, rng.choice(btns), rng=rng)   # every rendered button must be valid
            assert (await economy.balance(1)).balance - before == res["payout"]
            assert await realm.dungeon_get(1) is None
        await db.dispose()
    run(go())


def test_dungeon_blocks_double_entry_and_hard_below_level_5():
    async def go():
        from database.models import User
        from services import cooldowns, realm, users
        from services.errors import GameError
        db = await _setup()
        await realm.dungeon_enter(1, False)
        with pytest.raises(GameError):
            await realm.dungeon_enter(1, False)
        await users.touch(9, "n", "Newbie")
        with pytest.raises(GameError):
            await realm.dungeon_enter(9, True)
        await db.dispose()
    run(go())


def test_expedition_cannot_be_collected_early_or_twice():
    async def go():
        from database.models import Expedition, utcnow
        from services import economy, pets, realm
        from services.errors import GameError
        db = await _setup()
        async with db.tx() as s:
            await economy.apply(s, 2, wallet=5000, kind="test")
        await pets.adopt(2)
        await realm.expedition_start(2, "scout")
        with pytest.raises(GameError):
            await realm.expedition_start(2, "trek")
        with pytest.raises(GameError):
            await realm.expedition_collect(2)
        async with db.tx() as s:
            await s.execute(update(Expedition).where(Expedition.user_id == 2).values(ends_at=utcnow() - timedelta(seconds=1)))
        before = (await economy.balance(2)).balance
        r = await realm.expedition_collect(2)
        assert (await economy.balance(2)).balance - before == r["amount"] > 0
        with pytest.raises(GameError):
            await realm.expedition_collect(2)
        await db.dispose()
    run(go())


def test_heist_pays_once_and_roles_matter():
    async def go():
        from services import economy, realm
        from services.errors import GameError
        db = await _setup()
        assert await realm.heist_lock_in(3) == 0
        assert await realm.heist_lock_in(3) > 0           # personal cooldown holds
        b = (await economy.balance(3)).balance
        await realm.heist_pay(3, "tok", 4, 300)
        assert (await economy.balance(3)).balance - b == 300
        with pytest.raises(GameError):
            await realm.heist_pay(3, "tok", 4, 300)      # idempotent ledger ref
        rng = random.Random(0)
        full = {1: ("muscle", 5), 2: ("hacker", 5), 3: ("lookout", 5), 4: ("driver", 5)}
        dup = {1: ("muscle", 5), 2: ("muscle", 5), 3: ("muscle", 5), 4: ("muscle", 5)}
        avg = lambda crew: sum(sum(ok for *_, ok, _c in realm.heist_simulate(crew, rng)) for _ in range(2000)) / 2000
        assert avg(full) > avg(dup) + 0.5
        await db.dispose()
    run(go())


def test_counters_unlock_achievements():
    async def go():
        from database.models import Achievement
        from services import realm
        from sqlalchemy import select
        db = await _setup()
        async with db.tx() as s:
            new = await realm.bump(s, 1, "expedition")
        assert any("Explorer" in n for n in new)
        async with db.tx() as s:
            keys = set((await s.execute(select(Achievement.key).where(Achievement.user_id == 1))).scalars())
        assert "explorer" in keys
        await db.dispose()
    run(go())
