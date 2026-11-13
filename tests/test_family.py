"""Family / relations tests. Run: pytest tests/test_family.py   (needs the packages from requirements*.txt)"""
import asyncio
import os
import tempfile

import pytest

DB = os.path.join(tempfile.gettempdir(), "family_test.db")
URL = f"sqlite+aiosqlite:///{DB}"          # module-local: test files must not share one database


def run(c):
    return asyncio.run(c)


async def _setup():
    from database.session import db
    from services import users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in (1, 2, 3):
        await users.touch(uid, f"u{uid}", f"User{uid}")
    return db


def test_roles_are_consistent():
    from services import family
    assert set(family.ROLES) == set(family.INVERSE)
    for tr in family.INVERSE.values():
        assert all(k in family.ALL_ROLES for k in tr)


def test_relation_gender_labels_and_limits():
    async def go():
        from services import family
        from services.errors import GameError
        db = await _setup()
        await family.set_gender(1, "b")
        await family.can_request(1, 2, "father")
        await family.create(1, 2, "father")
        mine = (await family.relatives_of(1))[0]
        theirs = (await family.relatives_of(2))[0]
        assert mine["title"] == "Father" and theirs["title"] == "Son"           # user 1 is a boy -> user 2 sees him as Son
        with pytest.raises(GameError):
            await family.can_request(2, 1, "brother")                           # one relation per pair
        title, _ = await family.remove(2, 1)
        assert title == "Son" and not await family.relatives_of(1)
        await db.dispose()
    run(go())


def test_relatives_cannot_marry_but_friends_can():
    async def go():
        from services import extras, family
        from services.errors import GameError
        db = await _setup()
        await family.create(1, 2, "brother")
        with pytest.raises(GameError):
            await extras.can_propose(1, 2)
        await family.create(1, 3, "friend")
        from services import economy
        async with db.tx() as s:                     # the proposer pays the ring, so they need the money
            await economy.apply(s, 1, wallet=extras.RING_PRICE, kind="test")
        await extras.can_propose(1, 3)
        await db.dispose()
    run(go())


def test_backup_contains_relations():
    async def go():
        from services import backup, family
        db = await _setup()
        await family.create(1, 2, "mother")
        os.environ["BACKUP_DIR"] = tempfile.mkdtemp()
        backup.BACKUP_DIR = __import__("pathlib").Path(os.environ["BACKUP_DIR"])
        p = await backup.make_backup(URL)
        assert "1 relatives" in await backup.summary(p)
        await db.dispose()
    run(go())



def test_familytree_excludes_friends_and_renders():
    async def go():
        from services import family
        from utils import treeimg
        db = await _setup()
        await family.set_gender(1, "b")
        await family.create(1, 2, "father")
        await family.create(1, 3, "bestie")
        groups, v = await family.tree_clusters(2)        # user 2 sees user 1 as "Son"
        assert [x["title"] for x in groups["children"]] == ["Son"]
        groups, _ = await family.tree_clusters(1)
        assert list(groups) == ["parents"]               # the bestie is not in the tree
        P = treeimg.Person
        png = treeimg.render("User1", {"parents": [P("User2", "Father")], "couple": [P("User1", "You", "you")]})
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        await db.dispose()
    run(go())


def test_treeimg_renders_every_cluster_and_odd_names():
    from utils import treeimg
    P = treeimg.Person
    full = {k: [P("Név 😀 " + k, "Role")] for k in treeimg.LANE_OF if k != "couple"}
    full["couple"] = [P("Me", "You", "you"), P("Mate", "Partner", "partner")]
    assert treeimg.render("Me", full, "Love 5")[:4] == b"\x89PNG"
    assert treeimg.render("x" * 80, {"couple": [P("", "You", "you")], "cousins": [P("c", "Cousin")]})[:4] == b"\x89PNG"


def _jpeg(color=(200, 60, 60), size=(320, 200)):
    import io
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


def test_treeimg_draws_profile_pictures_and_survives_bad_ones():
    from PIL import Image
    import io
    from utils import treeimg
    P = treeimg.Person
    base = {"couple": [P("Me", "You", "you")], "parents": [P("Dad", "Father")]}
    plain = treeimg.render("Me", base)
    with_pics = treeimg.render("Me", {"couple": [P("Me", "You", "you", _jpeg())], "parents": [P("Dad", "Father", "blood", _jpeg((20, 90, 200), (90, 400)))]})
    assert with_pics[:4] == b"\x89PNG" and with_pics != plain
    bad = treeimg.render("Me", {"couple": [P("Me", "You", "you", b"not an image")], "parents": [P("Dad", "Father", "blood", b"")]})
    assert bad == plain                                  # unreadable / empty photo -> identical to the initial-letter tree
    Image.open(io.BytesIO(with_pics)).verify()


def test_avatars_fetch_is_best_effort_and_cached():
    from types import SimpleNamespace as NS
    from utils import avatars
    avatars._cache.clear()
    calls = {"photos": 0}

    class FakeBot:
        async def get_user_profile_photos(self, uid, offset=0, limit=1):
            calls["photos"] += 1
            if uid == 2:
                return NS(photos=[])                                       # no profile picture
            if uid == 3:
                raise RuntimeError("Forbidden: privacy")                    # hidden / blocked
            return NS(photos=[[NS(file_id=f"small{uid}", width=50, height=50), NS(file_id=f"mid{uid}", width=160, height=160),
                               NS(file_id=f"big{uid}", width=640, height=640)]])

        async def download(self, file_id, destination=None):
            assert file_id == "mid1"                                       # smallest size that is >= 160px
            destination.write(b"IMG")

    out = run(avatars.fetch(FakeBot(), [1, 2, 3, None, 1]))
    assert out == {1: b"IMG"}
    n = calls["photos"]
    assert n == 3                                                          # duplicates and None skipped
    assert run(avatars.fetch(FakeBot(), [1, 2, 3])) == {1: b"IMG"}
    assert calls["photos"] == n                                            # second call served from cache


def test_tree_clusters_carry_user_ids():
    async def go():
        from services import family
        db = await _setup()
        await family.create(1, 2, "father")
        groups, _ = await family.tree_clusters(1)
        assert groups["parents"][0]["uid"] == 2
        await db.dispose()
    run(go())


def test_family_text_splits_into_pages_only_when_needed(monkeypatch):
    import asyncio
    import re
    from types import SimpleNamespace as NS
    from handlers import family as hf
    groups = {"parents": ("P", "Parents"), "siblings": ("S", "Siblings"), "children": ("C", "Children"), "cousins": ("X", "Cousins")}

    def world(n, name_len, kids):
        rel = [dict(other=100 + i, name=f"R{i}".ljust(name_len, "x"), emoji="🧑", title="Granddaughter", group=list(groups)[i % 4], key="x") for i in range(n)]
        v = dict(partner=(7, "P" * name_len), love=9, days=9, houses=["tent"], kids=[(f"K{i}" + "k" * 29, "g", True, 999) for i in range(kids)])

        async def family_view(uid):
            return v

        async def relatives_of(uid):
            return rel
        monkeypatch.setattr(hf, "life", NS(family_view=family_view, HOUSES={"tent": ("⛺", "Tent", 0, 0)}, love_title=lambda x: "Soulmates"))
        monkeypatch.setattr(hf, "family", NS(relatives_of=relatives_of, GROUPS=groups))

    vis = lambda t: len(re.sub(r"<[^>]+>", "", t))
    world(6, 10, 1)                                                      # a normal tree: one message, no arrows
    text, mk = asyncio.run(hf.render_tree_page(1, "Aman", 0))
    assert mk is None and "Page" not in text
    world(25, 100, 6)                                                    # the biggest tree with 100-character names: several pages
    text, mk = asyncio.run(hf.render_tree_page(1, "N" * 100, 0))
    pages = int(re.search(r"Page 1/(\d+)", text).group(1))
    assert pages >= 2 and [b.callback_data for r in mk.inline_keyboard for b in r] == ["ft:1:1"]
    seen = ""
    for p in range(pages):
        t, _ = asyncio.run(hf.render_tree_page(1, "N" * 100, p))
        assert vis(t) < 4096
        seen += t
    assert all(seen.count(f'tg://user?id={100 + i}"') == 1 for i in range(25))        # nobody dropped or repeated
    assert "/addfamily" in t and "/addfamily" not in text                              # command hints only on the last page
