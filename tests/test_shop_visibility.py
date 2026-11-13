"""Bought gear must be visible (profile / inventory / purchase message) and must really change fights."""
import asyncio
import os
import tempfile

import pytest

DB = os.path.join(tempfile.gettempdir(), "shopvis_test.db")


def run(c):
    return asyncio.run(c)


async def _setup(funds: int = 100_000):
    from database.session import db
    from services import economy, shop, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(f"sqlite+aiosqlite:///{DB}")
    await db.create_all()
    await shop.seed_catalog()
    await users.touch(1, "u1", "User1")
    async with db.tx() as s:
        await economy.apply(s, 1, wallet=funds, kind="test")
    return db


def test_gear_is_visible_and_counts_in_fights():
    async def go():
        from handlers import views
        from services import combat, shop
        from database.models import User
        db = await _setup()
        async with db.tx() as s:
            base = await combat.load_fighter(s, await s.get(User, 1))
        await shop.buy(1, "iron_sword")
        await shop.buy(1, "chainmail")
        text, _ = await views.profile(1)
        assert "Weapon: — none" in text and "Armour: — none" in text          # bought but not worn yet
        inv, _ = await views.inventory(1)
        assert inv.count("not equipped") == 2
        await shop.equip(1, "iron_sword"); await shop.equip(1, "chainmail")
        text, _ = await views.profile(1)
        assert "Iron Sword" in text and "Chainmail" in text and "⚔️ +8" in text and "❤️ +25" in text
        async with db.tx() as s:
            f = await combat.load_fighter(s, await s.get(User, 1))
        assert f.atk == pytest.approx(base.atk + 8) and f.dfn == pytest.approx(base.dfn + 5) and f.hp == base.hp + 25
        await shop.upgrade(1, "iron_sword")                                     # +20% of the item's stats
        lo = await shop.loadout(1)
        assert lo["atk"] == pytest.approx(8 * 1.2)
    run(go())


def test_one_item_per_slot_and_next_step_text():
    async def go():
        from handlers import views
        from services import shop
        await _setup()
        for k in ("wooden_sword", "iron_sword"):
            await shop.buy(1, k)
        await shop.equip(1, "wooden_sword"); await shop.equip(1, "iron_sword")
        lo = await shop.loadout(1)
        assert lo["weapon"]["key"] == "iron_sword" and lo["atk"] == 8          # swapped, not stacked
        q = await shop.quote("iron_sword")
        assert q["category"] == "weapons" and "/equip iron_sword" in views.purchase_next_step(q["category"], q["key"])
        assert "no fight bonus" in views.CATEGORY_ROLE["collectibles"]
    run(go())


def test_profile_shows_protection_potion_clan_and_property():
    async def go():
        from handlers import views
        from services import clans, combat, life, shop
        await _setup()
        await shop.buy(1, "health_potion"); await shop.use_item(1, "health_potion")
        await combat.protect(1, 2)
        await life.buy_house(1, "tent")
        await clans.create(1, "Test Clan")
        text, _ = await views.profile(1)
        assert "Protected for" in text and "Health Potion active" in text
        assert "Test Clan" in text and "rent 40" in text
    run(go())


def test_profile_shows_partner_and_reputation():
    async def go():
        from handlers import views
        from services import arcade, extras, users
        from database.session import db
        from services import economy
        await _setup()
        await users.touch(2, "u2", "User2")
        async with db.tx() as s:
            await economy.apply(s, 2, wallet=5000, kind="test")
        await extras.marry(1, 2, "m1")
        await arcade.give_rep(2, 1)
        text, _ = await views.profile(1)
        assert "Married to" in text and "User2" in text and "Reputation" in text
    run(go())


def test_inventory_buttons_name_their_item_and_show_cost():
    async def go():
        from handlers import views
        from services import shop
        await _setup()
        for k in ("iron_sword", "chainmail", "golden_idol", "health_potion"):
            await shop.buy(1, k)
        text, markup = await views.inventory(1)
        labels = [b.text for row in markup.inline_keyboard for b in row]
        assert "✅ Equip Iron Sword" in labels and "✅ Equip Chainmail" in labels and "🧪 Use Health Potion" in labels
        assert any(l.startswith("⬆️ Iron Sword → Lv2 ·") and "💎" in l for l in labels)    # cost visible before paying
        assert "Equip" not in [l for l in labels if len(l) < 8]                                # no anonymous "Equip" buttons
        idol = next(l for l in text.splitlines() if "Diamond Idol" in l)
        assert "Lv" not in idol and "trophy" in text                                           # no meaningless Lv1
        assert "Lv1/" in next(l for l in text.splitlines() if "Iron Sword" in l)
        for _ in range(4):
            await shop.upgrade(1, "iron_sword")
        text, markup = await views.inventory(1)
        assert not any(b.text.startswith("⬆️ Iron Sword") for row in markup.inline_keyboard for b in row)   # max level -> no button
    run(go())


def test_inventory_lists_gear_first_and_labels_every_button():
    async def go():
        from handlers import views
        from services import shop
        await _setup()
        for k in ("golden_idol", "health_potion", "title_legend", "shadow_dagger"):
            await shop.buy(1, k)
        await shop.equip(1, "shadow_dagger")
        text, markup = await views.inventory(1)
        assert text.index("Shadow Dagger") < text.index("Legend") < text.index("Health Potion") < text.index("Diamond Idol")
        assert "not equipped" in text and "✅ equipped" in text
        idol_line = next(l for l in text.splitlines() if "Diamond Idol" in l)
        assert "Lv" not in idol_line                                                      # no meaningless "Lv1" on trophies
        for row in markup.inline_keyboard:
            for b in row:
                assert len(b.text) > 3                                                    # every button says what it does
    run(go())


def test_rules_page_is_complete_and_jail_text_is_true():
    async def go():
        from handlers import views
        from services import economy, cooldowns
        from services.errors import GameError
        from database.session import db
        await _setup()
        home, _ = views.rules()
        for word in ("Kill", "Rob", "Protection", "Duels", "Bounties", "Casino"):
            assert word in home or any(word in t for _, t, _ in views.RULE_TOPICS)
        kill, _ = views.rules("kill"); rob, _ = views.rules("rob")
        assert "30%" in kill and "4%" in kill and "revive" in kill.lower() and "45%" in rob and "jail" in rob.lower()
        async with db.tx() as s:
            await cooldowns.set_(s, 1, "jail", 300)
        for act in (economy.work(1), economy.crime(1)):
            try:
                await act
                raise AssertionError("jail should block this")
            except GameError as e:
                assert "jail" in str(e).lower()
        assert "working" in rob                                                           # the page names /work as blocked
    run(go())
