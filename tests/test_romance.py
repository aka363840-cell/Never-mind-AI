"""Romance tests: engagement, wedding, anniversary. Run: pytest tests/test_romance.py   (needs the packages from requirements*.txt)

Part A of the romance feature. Parts B (gifts / love letters) and C (honeymoon / quests / streaks) append their tests below."""
import asyncio
import os
import re
import tempfile
from datetime import timedelta

import pytest

DB = os.path.join(tempfile.gettempdir(), "romance_test.db")
URL = f"sqlite+aiosqlite:///{DB}"          # module-local: test files must not share one database


def run(c):
    return asyncio.run(c)


async def _setup(n: int = 4):
    """Users 1..n, all with accounts older than the anti-alt age gate, 100 diamonds each (the start balance)."""
    from sqlalchemy import update
    from database.models import User, utcnow
    from database.session import db
    from services import users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in range(1, n + 1):
        await users.touch(uid, f"u{uid}", f"User{uid}")
    async with db.tx() as s:
        await s.execute(update(User).values(created_at=utcnow() - timedelta(days=3)))
    return db


async def _fund(db, uid: int, amount: int):
    from services import economy
    async with db.tx() as s:
        await economy.apply(s, uid, wallet=amount, kind="test")


async def _bal(uid: int) -> int:
    from services import economy
    return (await economy.balance(uid)).balance


async def _married_since(db, a: int, b: int, days: int):
    from sqlalchemy import update
    from database.models import Marriage, utcnow
    async with db.tx() as s:
        await s.execute(update(Marriage).where(Marriage.user_a == min(a, b)).values(married_at=utcnow() - timedelta(days=days)))


async def _engaged_couple(db, a=1, b=2, money=6000):
    from services import romance
    await _fund(db, a, money)
    await _fund(db, b, money)
    await romance.engage(a, b, ref=f"engage:{a}:{b}")


# ------------------------------------------------------------------ engagement
def test_engage_charges_the_ring_to_the_proposer_only():
    async def go():
        from services import romance
        from game.rules import R
        db = await _setup()
        await _fund(db, 1, 2000)
        await romance.can_engage(1, 2)
        await romance.engage(1, 2, ref="engage:t1")
        assert await _bal(1) == 100 + 2000 - R.engage_ring_cost and await _bal(2) == 100
        pid, pname, days = await romance.engagement_view(2)
        assert pid == 1 and pname == "User1" and days == 0
        assert (await romance.engagement_view(1))[0] == 2
        await db.dispose()
    run(go())


def test_engage_rules_no_self_no_double_no_relatives_no_new_accounts_no_broke_proposer():
    async def go():
        from sqlalchemy import update
        from database.models import User, utcnow
        from services import family, romance
        from services.errors import GameError
        db = await _setup()
        await _fund(db, 1, 5000)
        with pytest.raises(GameError):
            await romance.can_engage(1, 1)                       # no self-targeting
        with pytest.raises(GameError):
            await romance.can_engage(3, 2)                       # 3 only has the 100 start diamonds: can't pay the ring
        await family.create(1, 3, "brother")
        with pytest.raises(GameError):
            await romance.can_engage(1, 3)                       # relatives can't get engaged
        async with db.tx() as s:                                 # user 4 registered just now
            await s.execute(update(User).where(User.id == 4).values(created_at=utcnow()))
        with pytest.raises(GameError):
            await romance.can_engage(1, 4)                       # new accounts are blocked (anti multi-account)
        await romance.engage(1, 2, ref="engage:t2")
        await _fund(db, 3, 5000)
        with pytest.raises(GameError):
            await romance.can_engage(3, 2)                       # 2 is already engaged
        async with db.tx() as s:
            await s.execute(update(User).where(User.id == 4).values(created_at=utcnow() - timedelta(days=3)))
        with pytest.raises(GameError):
            await romance.can_engage(1, 4)                       # 1 is already engaged
        await db.dispose()
    run(go())


def test_engage_replay_with_same_ref_cannot_double_charge():
    async def go():
        from services import romance
        from services.errors import GameError
        db = await _setup()
        await _fund(db, 1, 5000)
        await romance.engage(1, 2, ref="engage:dup")
        before = await _bal(1)
        with pytest.raises(GameError):
            await romance.engage(1, 2, ref="engage:dup")
        assert await _bal(1) == before
        await db.dispose()
    run(go())


def test_break_engagement_costs_a_penalty_and_the_ring_is_not_refunded():
    async def go():
        from services import romance
        from services.errors import GameError
        from game.rules import R
        db = await _setup()
        await _fund(db, 1, 5000)
        await _fund(db, 2, 1000)
        await romance.engage(1, 2, ref="engage:b1")
        b1, b2 = await _bal(1), await _bal(2)
        other, paid = await romance.break_engagement(2)          # either side may break; the breaker pays
        assert other == 1 and paid == R.engage_break_penalty
        assert await _bal(2) == b2 - paid and await _bal(1) == b1   # fiancé(e) not charged, ring not refunded
        assert await romance.engagement_view(1) is None
        with pytest.raises(GameError):
            await romance.break_engagement(2)
        await romance.can_engage(1, 2)                           # free to engage again
        await db.dispose()
    run(go())


def test_break_engagement_never_traps_a_broke_player():
    async def go():
        from services import economy, romance
        db = await _setup()
        await _fund(db, 1, 5000)
        await romance.engage(1, 2, ref="engage:b2")
        async with db.tx() as s:                                 # 2 spends everything but 40
            await economy.apply(s, 2, wallet=-60, kind="test")
        other, paid = await romance.break_engagement(2)
        assert paid == 40 and await _bal(2) == 0 and await romance.engagement_view(1) is None
        await db.dispose()
    run(go())


def test_marrying_with_marry_voids_a_stale_engagement_and_marry_divorce_still_work():
    async def go():
        from services import extras, romance
        db = await _setup()
        await _fund(db, 1, 5000)
        await romance.engage(1, 2, ref="engage:m1")
        await extras.can_propose(1, 2)
        await extras.marry(1, 2, ref="marry:m1")                 # the old command is untouched
        assert await romance.engagement_view(1) is None          # engagement is consumed, no ghost row blocks anything
        assert (await extras.couple(1))[0] == 2
        assert await extras.divorce(1) == 2
        assert await extras.couple(1) is None
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ wedding
def test_wedding_marries_an_engaged_couple_splits_the_cost_and_pays_the_bonus_once():
    async def go():
        from services import extras, romance
        from game.rules import R
        db = await _setup()
        await _engaged_couple(db)
        b1, b2 = await _bal(1), await _bal(2)
        pid, _ = await romance.can_wed(1, "simple")
        assert pid == 2
        info = await romance.hold_wedding(1, 2, "simple", ref="wedding:w1")
        _, _, _, cost, bonus = romance.venue_of("simple")
        assert info["newly_married"] and info["bonus"] == bonus
        assert await _bal(1) == b1 - cost // 2 + bonus and await _bal(2) == b2 - cost // 2 + bonus
        assert await romance.engagement_view(1) is None
        assert (await extras.couple(1))[0] == 2 and (await extras.couple(2))[0] == 1
        async with db.tx() as s:
            m = await extras.partner_of(s, 1)
            assert await romance.wedding_of(s, m) is not None
        assert "Wedding Card" in romance.wedding_card(info)
        await db.dispose()
    run(go())


def test_wedding_is_a_money_sink_for_every_venue_and_milestones_are_small():
    from game.rules import R
    for key, _e, _n, cost, bonus in R.wedding_venues:
        assert cost % 2 == 0, key                                # halves are whole diamonds
        assert 2 * bonus < R.engage_ring_cost + cost, key        # what the couple gets back is less than what it paid
    assert [d for d, _, _ in R.anniv_milestones] == [30, 100, 365]
    assert len({k for k, *_ in R.wedding_venues}) == len(R.wedding_venues) >= 3


def test_wedding_needs_both_wallets_and_is_atomic():
    async def go():
        from services import economy, extras, romance
        from services.errors import GameError
        db = await _setup()
        await _engaged_couple(db, money=6000)
        everything = await _bal(2)
        async with db.tx() as s:                                 # partner spends everything
            await economy.apply(s, 2, wallet=-everything, kind="test")
        b1 = await _bal(1)
        with pytest.raises(GameError):
            await romance.can_wed(1, "garden")
        with pytest.raises(GameError):
            await romance.hold_wedding(1, 2, "garden", ref="wedding:w2")
        assert await _bal(1) == b1 and await _bal(2) == 0       # nobody was charged
        assert await extras.couple(1) is None                    # and nobody got married
        assert (await romance.engagement_view(1)) is not None
        await db.dispose()
    run(go())


def test_wedding_requires_an_engagement_or_a_marriage_and_the_right_partner():
    async def go():
        from services import romance
        from services.errors import GameError
        db = await _setup()
        await _fund(db, 1, 9000)
        with pytest.raises(GameError):
            await romance.wedding_options(1)                     # not engaged, not married
        with pytest.raises(GameError):
            await romance.can_wed(1, "nope")                     # unknown venue
        await romance.engage(1, 2, ref="engage:w3")
        await _fund(db, 2, 9000)
        with pytest.raises(GameError):
            await romance.hold_wedding(1, 3, "simple", ref="wedding:w3")   # 3 is not the fiancé(e)
        await db.dispose()
    run(go())


def test_a_marry_couple_can_still_hold_a_ceremony_once_without_a_second_marriage():
    async def go():
        from services import extras, romance
        from services.errors import GameError
        db = await _setup()
        await _fund(db, 1, 9000)
        await _fund(db, 2, 9000)
        await extras.marry(1, 2, ref="marry:c1")
        await _married_since(db, 1, 2, 10)
        info = await romance.hold_wedding(2, 1, "garden", ref="wedding:c1")
        assert not info["newly_married"] and info["bonus"] > 0
        v = await romance.anniversary_view(1)
        assert v["days"] == 10 and v["venue"][0] == "garden"      # the anniversary still counts from the marriage
        with pytest.raises(GameError):
            await romance.wedding_options(1)                     # one ceremony per marriage
        await db.dispose()
    run(go())


def test_wedding_bonus_is_once_per_couple_even_after_divorce_and_remarriage():
    async def go():
        from services import extras, romance
        db = await _setup()
        await _engaged_couple(db, money=20000)
        first = await romance.hold_wedding(1, 2, "simple", ref="wedding:r1")
        assert first["bonus"] > 0
        await extras.divorce(1)
        await romance.can_engage(1, 2)
        await romance.engage(1, 2, ref="engage:r2")
        second = await romance.hold_wedding(1, 2, "simple", ref="wedding:r2")
        assert second["newly_married"] and second["bonus"] == 0  # the venue was paid again, the one-time gift was not
        assert "no second bonus" in romance.wedding_card(second)
        await db.dispose()
    run(go())


# ------------------------------------------------------------------ anniversary
def test_milestones_pay_each_partner_once_and_survive_divorce():
    async def go():
        from services import extras, romance
        from services.errors import GameError
        from game.rules import R
        db = await _setup()
        await _engaged_couple(db)
        await romance.hold_wedding(1, 2, "simple", ref="wedding:a1")
        assert await romance.claim_milestones(1) == []           # day 0: nothing reached yet
        await _married_since(db, 1, 2, 31)
        b1 = await _bal(1)
        assert await romance.claim_milestones(1) == [("1 month", 150)]
        assert await _bal(1) == b1 + 150
        assert await romance.claim_milestones(1) == []           # one-time
        assert await romance.claim_milestones(2) == [("1 month", 150)]   # the partner claims their own share
        await _married_since(db, 1, 2, 400)
        assert [x[0] for x in await romance.claim_milestones(1)] == ["100 days", "1 year"]
        v = await romance.anniversary_view(1)
        assert all(m["claimed"] for m in v["milestones"]) and v["years"] == 1
        assert v["name"] == "User2" and v["days_to_anniversary"] >= 0
        await extras.divorce(1)                                  # divorce + remarry must not reset the rewards
        await _fund(db, 1, 5000)
        await extras.marry(1, 2, ref="marry:a2")
        await _married_since(db, 1, 2, 400)
        assert await romance.claim_milestones(1) == [] and await romance.claim_milestones(2) == [("100 days", R.anniv_milestones[1][2]), ("1 year", R.anniv_milestones[2][2])]
        await extras.divorce(1)
        with pytest.raises(GameError):
            await romance.claim_milestones(1)                    # single players have no anniversary
        await db.dispose()
    run(go())


def test_yearly_bonus_is_paid_once_per_year_by_the_loop_job():
    async def go():
        from services import extras, romance
        from game.rules import R
        db = await _setup()
        await _engaged_couple(db)
        await romance.hold_wedding(1, 2, "simple", ref="wedding:y1")
        assert await romance.pay_yearly_bonuses() == []          # newlyweds: nothing due
        await _married_since(db, 1, 2, 100)
        assert await romance.pay_yearly_bonuses() == []
        await _married_since(db, 1, 2, 366)
        b1, b2 = await _bal(1), await _bal(2)
        paid = await romance.pay_yearly_bonuses()
        assert len(paid) == 1 and paid[0]["years"] == 1 and paid[0]["bonus"] == R.anniv_yearly_bonus
        assert await _bal(1) == b1 + R.anniv_yearly_bonus and await _bal(2) == b2 + R.anniv_yearly_bonus
        assert await romance.pay_yearly_bonuses() == []          # idempotent: the loop can run every few minutes
        assert await _bal(1) == b1 + R.anniv_yearly_bonus
        await _married_since(db, 1, 2, 800)                      # year 2 is a new bonus
        assert [p["years"] for p in await romance.pay_yearly_bonuses()] == [2]
        await db.dispose()
    run(go())


def test_concurrent_loop_runs_pay_the_yearly_bonus_once():
    async def go():
        from services import romance
        from game.rules import R
        db = await _setup()
        await _engaged_couple(db)
        await romance.hold_wedding(1, 2, "simple", ref="wedding:y2")
        await _married_since(db, 1, 2, 400)
        b1 = await _bal(1)
        await asyncio.gather(romance.pay_yearly_bonuses(), romance.pay_yearly_bonuses())
        assert await _bal(1) == b1 + R.anniv_yearly_bonus
        await db.dispose()
    run(go())


def test_year_maths_handles_leap_days():
    from datetime import datetime, timezone
    from services import romance
    d = lambda y, m, dd: datetime(y, m, dd, tzinfo=timezone.utc)
    assert romance.full_years(d(2025, 11, 13), d(2026, 11, 13)) == 1
    assert romance.full_years(d(2025, 11, 14), d(2026, 11, 13)) == 0
    assert romance.full_years(d(2024, 2, 29), d(2025, 2, 28)) == 0
    assert romance.full_years(d(2024, 2, 29), d(2025, 3, 1)) == 1
    assert romance.add_years(d(2024, 2, 29), 1) == d(2025, 3, 1)


# ------------------------------------------------------------------ texts, wiring, safety
def test_wedding_card_escapes_user_names():
    from datetime import datetime, timezone
    from services import romance
    card = romance.wedding_card(dict(a=1, b=2, a_name="<b>Eve</b>", b_name="A&B", venue="royal", bonus=0,
                                     held_at=datetime(2026, 1, 2, tzinfo=timezone.utc)))
    assert "&lt;b&gt;Eve&lt;/b&gt;" in card and "A&amp;B" in card and "<b>Eve</b>" not in card


def test_romance_texts_are_html_safe():
    from utils.content import romance as t
    bad = re.compile(r"[<>&]")
    pools = [t.VOWS, t.ENGAGE_PROPOSAL, t.ENGAGED_ACCEPT, t.ANNIV_GREETINGS, [x for v in t.VENUE_SCENES.values() for x in v]]
    for pool in pools:
        assert len(pool) == len(set(pool)) >= 2
        assert not any(bad.search(x) for x in pool)
    for g in t.ANNIV_GREETINGS:
        g.format(years=1, plural="", ordinal=t.ordinal(1))
    assert [t.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22)] == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd"]


def test_callback_data_fits_telegram_limit_and_is_owner_bound():
    from game.rules import R
    biggest = 9_999_999_999
    for key, *_ in R.wedding_venues:
        assert len(f"wd:v:{key}:{biggest}".encode()) < 64
    for cb in (f"eg:brk:{biggest}", f"eg:cx:{biggest}", "eg:ok:" + "x" * 8, "wd:ok:" + "x" * 8, "wd:no:" + "x" * 8):
        assert len(cb.encode()) < 64


def test_new_commands_are_in_help_and_router_is_before_ai_chat():
    import inspect
    import bot
    from utils import helpcatalog as H
    for c in ("engage", "wedding", "anniversary"):
        assert c in H.DESC and any(c in cmds for _, cmds in H.CATEGORIES)
        assert c not in H.MENU and c not in H.STAFF
    src = inspect.getsource(bot.build_dispatcher)
    assert "romance as romance_h" in src and src.index("romance_h.router") < src.index("ai_chat.router")
    assert "romance_loop" in inspect.getsource(bot.main)


# ================================================================== Part B: gifts, bonds, love letters
async def _wed(db, a=1, b=2, money=20000):
    from services import romance
    await _engaged_couple(db, a, b, money)
    return await romance.hold_wedding(a, b, "simple", ref=f"wedding:{a}:{b}")


async def _love(db, uid=1) -> int:
    from services import life
    async with db.tx() as s:
        return await life._love(s, uid)


def test_gift_to_partner_costs_diamonds_raises_love_and_replay_is_refused():
    async def go():
        from services import gifting
        from services.errors import GameError
        db = await _setup()
        await _wed(db)
        b = await _bal(1)
        r = await gifting.send_gift(1, 2, "flowers", ref="sg:t1")
        assert r["kind"] == "partner" and r["price"] == 50 and r["points"] == 6
        assert await _bal(1) == b - 50 and await _love(db) == 6
        with pytest.raises(GameError):
            await gifting.send_gift(1, 2, "flowers", ref="sg:t1")        # double tap / replay
        assert await _bal(1) == b - 50 and await _love(db) == 6
        await db.dispose()
    run(go())


def test_gifts_have_a_daily_cap_per_sender_and_never_charge_for_cut_off_points():
    async def go():
        from services import gifting
        from services.errors import GameError
        from game.rules import R
        db = await _setup()
        await _wed(db)
        await gifting.send_gift(1, 2, "cake", ref="sg:c0")                # +75
        sent, used = 0, 75
        while True:
            b = await _bal(1)
            try:
                await gifting.send_gift(1, 2, "flowers", ref=f"sg:c{sent + 1}")
            except GameError:
                assert await _bal(1) == b                                # the refused gift cost nothing
                break
            sent += 1
            used += 6
        assert used <= R.gift_daily_cap_points and R.gift_daily_cap_points - used < 6
        assert (await gifting.gift_options(1, 2))[2] == R.gift_daily_cap_points - used
        assert (await gifting.send_gift(2, 1, "flowers", ref="sg:other"))["points"] == 6   # the cap is per SENDER
        await db.dispose()
    run(go())


def test_gift_to_relative_builds_bond_points_and_titles():
    async def go():
        from services import family, gifting
        db = await _setup()
        await _fund(db, 1, 5000)
        await family.create(1, 3, "brother")
        await gifting.send_gift(1, 3, "cake", ref="sg:b1")                # +75
        assert await gifting.bond_points_map(1) == {3: 75} and await gifting.bond_points_map(3) == {1: 75}
        await gifting.send_gift(1, 3, "teddy", ref="sg:b2")               # +35 -> 110
        assert (await gifting.bond_points_map(1))[3] == 110 and gifting.bond_title(110) == "Close 🤝"
        assert await _love(db, 1) == 0                                    # relatives never touch couple love
        await db.dispose()
    run(go())


def test_bond_titles_thresholds():
    from services import gifting
    assert [gifting.bond_title(p) for p in (0, 29, 30, 99, 100, 299, 300, 5000)] == \
        ["", "", "Friendly 🌼", "Friendly 🌼", "Close 🤝", "Close 🤝", "Inseparable 💞", "Inseparable 💞"]


def test_gift_rules_partner_or_relatives_only_no_self_no_unknown_no_broke_sender():
    async def go():
        from services import family, gifting
        from services.errors import GameError
        db = await _setup()
        await _wed(db)
        await family.create(1, 3, "friend")
        for bad in ((1, 1, "flowers"), (1, 4, "flowers"), (1, 3, "diamond-ring"), (2, 3, "flowers")):
            with pytest.raises(GameError):
                await gifting.send_gift(*bad, ref=f"sg:bad{bad[1]}{bad[2]}")
        with pytest.raises(GameError):
            await gifting.gift_options(1, 4)
        b = await _bal(3)
        with pytest.raises(GameError):
            await gifting.send_gift(3, 1, "cake", ref="sg:broke")         # 3 holds only 100 diamonds
        assert await _bal(3) == b and await gifting.bond_points_map(3) == {}   # nothing charged, no points
        await db.dispose()
    run(go())


def test_family_page_shows_bond_titles():
    async def go():
        from services import family, gifting
        import handlers.family as hf
        db = await _setup()
        await _fund(db, 1, 5000)
        await family.create(1, 3, "brother")
        text, _ = await hf.render_tree_page(1, "Aman", 0)
        assert "Close" not in text and "Friendly" not in text
        await gifting.send_gift(1, 3, "cake", ref="sg:f1")
        await gifting.send_gift(1, 3, "teddy", ref="sg:f2")
        text, _ = await hf.render_tree_page(1, "Aman", 0)
        assert "Close 🤝" in text
        await db.dispose()
    run(go())


def test_love_letter_costs_a_little_has_a_cooldown_and_a_length_limit():
    async def go():
        from services import gifting
        from services.errors import GameError
        from game.rules import R
        db = await _setup()
        await _wed(db)
        b = await _bal(1)
        r = await gifting.send_letter(1, 2, "  You are my <favourite> person & more  ")
        assert r["text"] == "You are my <favourite> person & more" and r["points"] == R.letter_points
        assert await _bal(1) == b - R.letter_cost and await _love(db) == R.letter_points
        with pytest.raises(GameError):
            await gifting.send_letter(1, 2, "again")                      # 30 min cooldown
        assert await _bal(1) == b - R.letter_cost                         # the refused letter cost nothing
        await gifting.send_letter(2, 1, "x" * R.letter_max_chars)         # exactly the limit is fine (own cooldown)
        with pytest.raises(GameError):
            await gifting.send_letter(2, 1, "y" * (R.letter_max_chars + 1))
        with pytest.raises(GameError):
            await gifting.send_letter(2, 1, "   ")
        with pytest.raises(GameError):
            await gifting.send_letter(1, 4, "to a stranger")
        await db.dispose()
    run(go())


def test_love_letter_card_is_escaped_and_defused():
    from services import gifting
    t = gifting.clean_letter("hi\x00 there\u202e\r\n\n\n\n@bob see http://x.y")
    assert "\x00" not in t and "\u202e" not in t and "\n\n\n" not in t
    card = gifting.letter_card(1, "<i>Me</i>", 2, "A&B", "<b>hello</b> @bob http://x.y")
    assert "&lt;b&gt;hello&lt;/b&gt;" in card and "&lt;i&gt;Me&lt;/i&gt;" in card and "A&amp;B" in card
    assert "<b>hello</b>" not in card and "@bob" not in card and "http://" not in card     # no pings, no auto-links
    assert "Love Letter" in card


# ================================================================== Part C: honeymoon, couple quest, love streak
async def _end_trip(db):
    from datetime import timedelta
    from sqlalchemy import update
    from database.models import Honeymoon, utcnow
    async with db.tx() as s:
        await s.execute(update(Honeymoon).values(ends_at=utcnow() - timedelta(seconds=1)))


def test_honeymoon_needs_a_wedding_and_both_accept_money_is_a_sink():
    async def go():
        from services import couplelife, extras
        from services.errors import GameError
        db = await _setup()
        await _fund(db, 1, 5000)
        with pytest.raises(GameError):
            await couplelife.honeymoon_status(1)                          # single
        await extras.marry(1, 2, ref="marry:h0")
        with pytest.raises(GameError):
            await couplelife.honeymoon_offer(1)                           # married with /marry but no wedding ceremony yet
        await db.dispose()
    run(go())


def test_honeymoon_full_trip_timer_story_bonus_once():
    async def go():
        from services import couplelife
        from services.errors import GameError
        from game.rules import R
        db = await _setup()
        await _wed(db)
        pid, pname, half = await couplelife.honeymoon_offer(1)
        assert (pid, pname, half) == (2, "User2", R.honeymoon_cost // 2)
        b1, b2 = await _bal(1), await _bal(2)
        started = await couplelife.start_honeymoon(1, 2, ref="honeymoon:t1")
        assert await _bal(1) == b1 - half and await _bal(2) == b2 - half
        v = await couplelife.honeymoon_status(2)
        assert v["phase"] == "away" and len(v["chapters"]) == 1 and v["finale"] == "" and 0 < v["left"] <= R.honeymoon_duration_s
        assert started["destination"] in __import__("utils.content.romance", fromlist=["x"]).HONEYMOON_DESTINATIONS
        with pytest.raises(GameError):
            await couplelife.collect_honeymoon(1)                         # still away
        with pytest.raises(GameError):
            await couplelife.honeymoon_offer(1)                           # once per couple
        with pytest.raises(GameError):
            await couplelife.start_honeymoon(1, 2, ref="honeymoon:t2")
        assert await couplelife.due_honeymoon_notices() == []
        await _end_trip(db)
        v = await couplelife.honeymoon_status(1)
        assert v["phase"] == "back" and len(v["chapters"]) == 3 and v["finale"]
        notices = await couplelife.due_honeymoon_notices()
        assert len(notices) == 1 and await couplelife.due_honeymoon_notices() == []   # announced once
        love0 = await _love(db)
        r = await couplelife.collect_honeymoon(2)                         # either partner can collect; both are paid
        assert r["bonus"] == R.honeymoon_bonus
        assert await _bal(1) == b1 - half + R.honeymoon_bonus and await _bal(2) == b2 - half + R.honeymoon_bonus
        assert await _love(db) == love0 + R.honeymoon_love
        with pytest.raises(GameError):
            await couplelife.collect_honeymoon(1)
        assert (await couplelife.honeymoon_status(1))["phase"] == "done"
        await db.dispose()
    run(go())


def test_honeymoon_collect_race_pays_once_and_the_trip_is_a_net_sink():
    async def go():
        from services import couplelife
        from game.rules import R
        db = await _setup()
        await _wed(db)
        await couplelife.start_honeymoon(1, 2, ref="honeymoon:race")
        await _end_trip(db)
        b1 = await _bal(1)
        res = await asyncio.gather(couplelife.collect_honeymoon(1), couplelife.collect_honeymoon(2), return_exceptions=True)
        assert sum(isinstance(x, dict) for x in res) == 1
        assert await _bal(1) == b1 + R.honeymoon_bonus
        await db.dispose()
    run(go())
    from game.rules import R
    assert 2 * R.honeymoon_bonus < R.honeymoon_cost and R.honeymoon_cost % 2 == 0 and R.honeymoon_duration_s == 6 * 3600


def test_honeymoon_needs_both_wallets_and_is_atomic():
    async def go():
        from services import couplelife, economy
        from services.errors import GameError
        db = await _setup()
        await _wed(db)
        everything = await _bal(2)
        async with db.tx() as s:
            await economy.apply(s, 2, wallet=-everything, kind="test")
        b1 = await _bal(1)
        with pytest.raises(GameError):
            await couplelife.honeymoon_offer(1)
        with pytest.raises(GameError):
            await couplelife.start_honeymoon(1, 2, ref="honeymoon:poor")
        assert await _bal(1) == b1
        await _fund(db, 2, 5000)
        await couplelife.honeymoon_offer(1)                               # the failed attempt did not burn the once-per-couple flag
        await db.dispose()
    run(go())


async def _do_part(db, key: str, uid: int):
    """Do one partner's share of a couple-quest task, the way a player really would (letters/gifts) or via the cooldown /daily /date /work set."""
    from database.session import db as sdb
    from services import cooldowns, gifting, life
    from game.rules import R
    other = 2 if uid == 1 else 1
    if key == "letter":
        await gifting.send_letter(uid, other, "hi love")
    elif key == "gift":
        await gifting.send_gift(uid, other, "flowers", ref=f"q:{key}:{uid}")
    else:
        cd = {"daily": R.daily_cooldown_s, "date": life.DATE_CD_S, "work": R.work_cooldown_s}[key]
        async with sdb.tx() as s:
            await cooldowns.set_(s, uid, key, cd)


def test_couple_quest_pays_both_partners_once_per_finished_task():
    async def go():
        from datetime import date
        from services import couplelife
        from game.rules import R
        db = await _setup()
        await _wed(db)
        q = await couplelife.couple_quest(1)
        assert len(q["tasks"]) == 2 and q["paid_now"] == [] and not any(t["done"] for t in q["tasks"])
        for t in q["tasks"]:
            await _do_part(db, t["key"], 1)                               # 'both' tasks are NOT done with one partner only
        q = await couplelife.couple_quest(1)                              # this call already pays the 'either' tasks (one partner is enough)
        for t in q["tasks"]:
            assert t["done"] == (t["mode"] == "either")
        assert sorted(q["paid_now"]) == sorted(t["key"] for t in q["tasks"] if t["mode"] == "either")
        remaining = [t["key"] for t in q["tasks"] if t["mode"] == "both"]
        for key in remaining:
            await _do_part(db, key, 2)
        b1, b2, love0 = await _bal(1), await _bal(2), await _love(db)
        q = await couplelife.couple_quest(2)
        assert sorted(q["paid_now"]) == sorted(remaining)
        pay = R.couple_quest_reward * len(remaining)
        assert await _bal(1) == b1 + pay and await _bal(2) == b2 + pay
        assert await _love(db) == love0 + len(remaining) * R.couple_quest_love
        q = await couplelife.couple_quest(1)
        assert q["paid_now"] == [] and all(t["paid"] for t in q["tasks"])  # never twice
        assert await _bal(1) == b1 + pay
        await db.dispose()
    run(go())


def test_couple_quest_race_between_partners_pays_once():
    async def go():
        from services import couplelife
        from game.rules import R
        db = await _setup()
        await _wed(db)
        q = await couplelife.couple_quest(1)
        for t in q["tasks"]:
            await _do_part(db, t["key"], 1)
            await _do_part(db, t["key"], 2)
        b1 = await _bal(1)
        await asyncio.gather(couplelife.couple_quest(1), couplelife.couple_quest(2))
        assert await _bal(1) == b1 + 2 * R.couple_quest_reward
        await db.dispose()
    run(go())


def test_couple_quest_tasks_are_stable_per_couple_and_day_and_need_a_marriage():
    from datetime import date
    from services import couplelife
    d = date(2026, 11, 13)
    a = couplelife.tasks_for(1, 2, d)
    assert a == couplelife.tasks_for(1, 2, d) and len({t[0] for t in a}) == 2
    assert {t[0] for day in range(1, 29) for t in couplelife.tasks_for(1, 2, date(2026, 11, day))} == \
        {t[0] for t in __import__("utils.content.romance", fromlist=["x"]).COUPLE_TASKS}   # every task shows up over time

    async def go():
        from services.errors import GameError
        db = await _setup()
        with pytest.raises(GameError):
            await couplelife.couple_quest(1)
        await db.dispose()
    run(go())


async def _shift_streak(db, days: int):
    from datetime import timedelta
    from sqlalchemy import select
    from database.models import LoveStreak
    d = timedelta(days=days)
    async with db.tx() as s:
        r = (await s.execute(select(LoveStreak))).scalars().one()
        r.last_day = r.last_day and r.last_day - d
        r.a_day = r.a_day and r.a_day - d
        r.b_day = r.b_day and r.b_day - d


def test_love_streak_needs_both_partners_and_counts_consecutive_days():
    async def go():
        from services import couplelife
        from game.rules import R
        db = await _setup()
        await _wed(db)
        r = await couplelife.love_checkin(1)
        assert not r["already"] and not r["both"] and r["streak"] == 0
        r = await couplelife.love_checkin(1)
        assert r["already"] and not r["both"]
        love0 = await _love(db)
        r = await couplelife.love_checkin(2)
        assert r["both"] and r["streak"] == 1 and r["best"] == 1 and r["next"] == (7, 200)
        assert await _love(db) == love0 + R.streak_love_per_day
        assert (await couplelife.love_checkin(1))["already"]              # a second check-in the same day adds nothing
        await _shift_streak(db, 1)                                        # pretend it is the next day
        await couplelife.love_checkin(1)
        r = await couplelife.love_checkin(2)
        assert r["streak"] == 2 and r["best"] == 2
        await _shift_streak(db, 2)                                        # a missed day breaks the streak
        await couplelife.love_checkin(2)
        r = await couplelife.love_checkin(1)
        assert r["both"] and r["streak"] == 1 and r["best"] == 2
        await db.dispose()
    run(go())


def test_love_streak_milestone_rewards_are_one_time_per_couple():
    async def go():
        from datetime import timedelta
        from sqlalchemy import update
        from database.models import LoveStreak, utcnow
        from services import couplelife
        db = await _setup()
        await _wed(db)
        await couplelife.love_checkin(1)
        await couplelife.love_checkin(2)
        y = utcnow().date() - timedelta(days=1)

        async def set_streak(n):
            async with db.tx() as s:
                await s.execute(update(LoveStreak).values(streak=n, best=n, last_day=y, a_day=y, b_day=y))

        await set_streak(6)
        b1, b2 = await _bal(1), await _bal(2)
        await couplelife.love_checkin(1)
        r = await couplelife.love_checkin(2)
        assert r["streak"] == 7 and r["rewards"] == [(7, 200)]
        assert await _bal(1) == b1 + 200 and await _bal(2) == b2 + 200
        await _shift_streak(db, 5)
        await set_streak(0)                                               # streak lost ...
        await couplelife.love_checkin(1)
        await couplelife.love_checkin(2)
        await set_streak(6)
        await couplelife.love_checkin(1)
        r = await couplelife.love_checkin(2)
        assert r["streak"] == 7 and r["rewards"] == [] and await _bal(1) == b1 + 200   # ... reaching 7 again pays nothing more
        await db.dispose()
    run(go())


def test_love_streak_race_and_divorce():
    async def go():
        from services import couplelife, extras
        from services.errors import GameError
        db = await _setup()
        await _wed(db)
        res = await asyncio.gather(couplelife.love_checkin(1), couplelife.love_checkin(2))
        assert max(r["streak"] for r in res) == 1
        assert (await couplelife.love_checkin(1))["streak"] == 1
        await extras.divorce(1)
        with pytest.raises(GameError):
            await couplelife.love_checkin(1)
        await db.dispose()
    run(go())


def test_part_b_and_c_texts_wiring_and_callbacks():
    import inspect
    from game.rules import R
    from utils import helpcatalog as H
    from utils.content import romance as t
    bad = re.compile(r"[<>&]")
    strings = [x for v in t.GIFT_REACTIONS.values() for x in v] + t.LETTER_OPENERS + t.STREAK_LINES
    for _k, (e, n, chapters, finale) in t.HONEYMOON_DESTINATIONS.items():
        assert len(chapters) == 3
        strings += [n, finale, *chapters]
    strings += [x[2] for x in t.COUPLE_TASKS]
    assert not any(bad.search(x) for x in strings)
    assert set(t.GIFT_REACTIONS) == {g[0] for g in R.gift_shop} == {"flowers", "chocolate", "teddy", "cake"}
    assert {x[3] for x in t.COUPLE_TASKS} <= {"both", "either"}
    assert all(p > 0 and pts > 0 for _, _, _, p, pts in R.gift_shop) and R.letter_max_chars == 300
    assert [title for _need, title, _e in R.bond_titles][::-1] == ["Friendly", "Close", "Inseparable"] and R.streak_milestones[0][0] == 7
    assert [d for d, _ in R.streak_milestones] == [7, 30, 100]
    big = 9_999_999_999
    for k, *_ in R.gift_shop:
        assert len(f"sg:{big}:{big}:{k}".encode()) < 64
    assert len(f"hn:c:{big}".encode()) < 64 and len(("hn:ok:" + "x" * 8).encode()) < 64
    for c in ("sendgift", "loveletter", "honeymoon", "couplequest", "lovestreak"):
        assert c in H.DESC and any(c in cmds for _, cmds in H.CATEGORIES) and c not in H.MENU
    import handlers.romance as hr
    src = inspect.getsource(hr)
    assert "due_honeymoon_notices" in src and "pay_yearly_bonuses" in src
