"""Extended family & friends on top of marriage / children: papa, mummy, bhai, behen, dada, chacha, bestie ...

Rules
* Both sides must agree (accept button) - nobody can be 'adopted' into a family without consent.
* One relation per pair, max MAX_RELATIVES per player, no self / no bots.
* Relatives of the blood/in-law kind can't marry each other (friends can).
* Gender (/gender) only decides how the OTHER side is labelled (Papa vs Mummy, Brother vs Sister ...).
Everything lives in the database (tables `relations`, `app_state`), so /backupdb and the daily backup contain it."""
from __future__ import annotations

from sqlalchemy import delete, func, or_, select

from database.models import AppState, Relation, User, aware, utcnow
from database.session import db
from services import users
from services.errors import GameError

MAX_RELATIVES = 25
BONUS_PER_RELATIVE, BONUS_CAP_RELATIVES = 15, 8        # tiny daily /familybonus top-up (keeps multi-account farming pointless)

# group key -> (emoji, title)  (display order)
GROUPS = {
    "parents": ("👨‍👩‍👦", "Parents"), "siblings": ("🫂", "Siblings"), "children": ("👶", "Children"),
    "grandparents": ("👴", "Grandparents"), "grandchildren": ("🧒", "Grandchildren"),
    "piblings": ("🏡", "Uncles & Aunts"), "niblings": ("🎈", "Nephews & Nieces"), "cousins": ("🤝", "Cousins"),
    "inlaws": ("💒", "In-laws"), "friends": ("💖", "Friends"),
}
# role key (= the command name) -> (emoji, title, group)
ROLES: dict[str, tuple[str, str, str]] = {
    "father": ("👨", "Father", "parents"), "mother": ("👩", "Mother", "parents"),
    "son": ("👦", "Son", "children"), "daughter": ("👧", "Daughter", "children"),
    "brother": ("🧑", "Brother", "siblings"), "sister": ("👧", "Sister", "siblings"),
    "grandfather": ("👴", "Grandfather", "grandparents"), "grandmother": ("👵", "Grandmother", "grandparents"),
    "grandson": ("👦", "Grandson", "grandchildren"), "granddaughter": ("👧", "Granddaughter", "grandchildren"),
    "uncle": ("🧔", "Uncle", "piblings"), "aunt": ("👩‍🦰", "Aunt", "piblings"),
    "nephew": ("👦", "Nephew", "niblings"), "niece": ("👧", "Niece", "niblings"),
    "cousin": ("🧑‍🤝‍🧑", "Cousin", "cousins"),
    "brother_in_law": ("🧑", "Brother-in-law", "inlaws"), "sister_in_law": ("👩", "Sister-in-law", "inlaws"),
    "father_in_law": ("👴", "Father-in-law", "inlaws"), "mother_in_law": ("👵", "Mother-in-law", "inlaws"),
    "son_in_law": ("🤵", "Son-in-law", "inlaws"), "daughter_in_law": ("👰", "Daughter-in-law", "inlaws"),
    "friend": ("🤝", "Friend", "friends"), "bestie": ("💖", "Best friend", "friends"),
}
# neutral labels used when the viewer's gender is unknown (display only, not requestable)
NEUTRAL: dict[str, tuple[str, str, str]] = {
    "child": ("🧒", "Child", "children"), "parent": ("🧑", "Parent", "parents"), "sibling": ("🫂", "Sibling", "siblings"),
    "grandchild": ("🧒", "Grandchild", "grandchildren"), "grandparent": ("🧓", "Grandparent", "grandparents"),
    "nibling": ("🎈", "Nephew/Niece", "niblings"), "pibling": ("🏡", "Uncle/Aunt", "piblings"),
    "inlaw": ("💒", "In-law", "inlaws"),
}
ALL_ROLES = {**ROLES, **NEUTRAL}

# role the other side gets: (gender unknown, requester is a boy, requester is a girl)
INVERSE: dict[str, tuple[str, str, str]] = {
    "father": ("child", "son", "daughter"), "mother": ("child", "son", "daughter"),
    "son": ("parent", "father", "mother"), "daughter": ("parent", "father", "mother"),
    "brother": ("sibling", "brother", "sister"), "sister": ("sibling", "brother", "sister"),
    "grandfather": ("grandchild", "grandson", "granddaughter"), "grandmother": ("grandchild", "grandson", "granddaughter"),
    "grandson": ("grandparent", "grandfather", "grandmother"), "granddaughter": ("grandparent", "grandfather", "grandmother"),
    "uncle": ("nibling", "nephew", "niece"), "aunt": ("nibling", "nephew", "niece"),
    "nephew": ("pibling", "uncle", "aunt"), "niece": ("pibling", "uncle", "aunt"),
    "cousin": ("cousin", "cousin", "cousin"),
    "brother_in_law": ("inlaw", "brother_in_law", "sister_in_law"), "sister_in_law": ("inlaw", "brother_in_law", "sister_in_law"),
    "father_in_law": ("inlaw", "son_in_law", "daughter_in_law"), "mother_in_law": ("inlaw", "son_in_law", "daughter_in_law"),
    "son_in_law": ("inlaw", "father_in_law", "mother_in_law"), "daughter_in_law": ("inlaw", "father_in_law", "mother_in_law"),
    "friend": ("friend", "friend", "friend"), "bestie": ("bestie", "bestie", "bestie"),
}
# other spellings -> role key (the old Hindi-style names keep working, e.g. rows saved by an earlier version)
ALIASES = {"dad": "father", "papa": "father", "pitaji": "father", "baap": "father",
           "mom": "mother", "mummy": "mother", "maa": "mother",
           "beta": "son", "beti": "daughter", "bro": "brother", "bhai": "brother", "bhaiya": "brother",
           "sis": "sister", "behen": "sister", "behan": "sister", "didi": "sister",
           "grandpa": "grandfather", "dada": "grandfather", "nana": "grandfather",
           "grandma": "grandmother", "dadi": "grandmother", "nani": "grandmother",
           "pota": "grandson", "poti": "granddaughter",
           "chacha": "uncle", "mama": "uncle", "fufa": "uncle", "masa": "uncle",
           "chachi": "aunt", "mami": "aunt", "bua": "aunt", "masi": "aunt",
           "bhatija": "nephew", "bhatiji": "niece",
           "bhabhi": "sister_in_law", "bhabi": "sister_in_law", "saali": "sister_in_law",
           "devar": "brother_in_law", "jija": "brother_in_law", "saala": "brother_in_law",
           "saas": "mother_in_law", "sasur": "father_in_law", "bahu": "daughter_in_law", "damad": "son_in_law",
           "dost": "friend", "bff": "bestie", "bestfriend": "bestie", "best_friend": "bestie"}
# role key -> lane cluster of the /familytree picture (friends / bestie are deliberately absent: the tree is family only)
TREE_CLUSTER = {**{k: v[2] for k, v in ALL_ROLES.items() if v[2] not in ("friends", "inlaws", "cousins")},
                "cousin": "cousins",
                "father_in_law": "pil", "mother_in_law": "pil", "brother_in_law": "sil", "sister_in_law": "sil", "inlaw": "sil",
                "son_in_law": "cil", "daughter_in_law": "cil"}
BLOCKS_MARRIAGE = {k for k, v in ROLES.items() if v[2] != "friends"}


def norm(kind: str) -> str:
    return ALIASES.get(kind, kind)


def resolve_role(raw: str) -> str | None:
    k = norm((raw or "").strip().lower().lstrip("/").replace("-", "_"))
    return k if k in ROLES else None


def role_info(key: str) -> tuple[str, str, str]:
    key = norm(key)
    return ALL_ROLES.get(key, ("👤", key.replace("_", " ").title(), "friends"))


async def gender_of(s, uid: int) -> str | None:
    row = await s.get(AppState, f"gender:{uid}")
    return row.value if row and row.value in ("b", "g") else None


async def set_gender(uid: int, g: str) -> None:
    async with db.tx() as s:
        await users.set_state(s, f"gender:{uid}", g)


def inverse_key(kind: str, requester_gender: str | None) -> str:
    n, b, g = INVERSE[norm(kind)]
    return b if requester_gender == "b" else g if requester_gender == "g" else n


async def _pair(s, a: int, b: int) -> Relation | None:
    return (await s.execute(select(Relation).where(or_((Relation.user_a == a) & (Relation.user_b == b),
                                                       (Relation.user_a == b) & (Relation.user_b == a))))).scalar_one_or_none()


async def count_of(s, uid: int) -> int:
    return (await s.execute(select(func.count()).select_from(Relation).where(or_(Relation.user_a == uid, Relation.user_b == uid)))).scalar() or 0


async def can_request(a: int, b: int, kind: str) -> None:
    if a == b:
        raise GameError("❌ You can't be your own relative 😅")
    async with db.tx() as s:
        await users.require(s, a)
        await users.require(s, b)
        old = await _pair(s, a, b)
        if old:
            raise GameError("👨‍👩‍👧 You two are already connected. Use /unfamily first to change the relation.")
        if await count_of(s, a) >= MAX_RELATIVES:
            raise GameError(f"👨‍👩‍👧‍👦 Your family list is full ({MAX_RELATIVES} max). Remove someone with /unfamily.")
        if await count_of(s, b) >= MAX_RELATIVES:
            raise GameError("👨‍👩‍👧‍👦 That person's family list is full.")
        from services import extras
        m = await extras.partner_of(s, a)
        if m and b in (m.user_a, m.user_b):
            raise GameError("💍 That's your partner! Choose someone else.")


async def create(a: int, b: int, kind: str) -> None:
    async with db.tx() as s:
        await users.lock(s, a, b)
        await users.require(s, a)
        await users.require(s, b)
        if await _pair(s, a, b):
            raise GameError("👨‍👩‍👧 You two are already connected.")
        if await count_of(s, a) >= MAX_RELATIVES or await count_of(s, b) >= MAX_RELATIVES:
            raise GameError("👨‍👩‍👧‍👦 A family list is full.")
        s.add(Relation(user_a=a, user_b=b, kind=kind, created_at=utcnow()))


async def remove(uid: int, other: int) -> tuple[str, str]:
    """-> (title of other's role for uid, other's name)"""
    async with db.tx() as s:
        r = await _pair(s, uid, other)
        if not r:
            raise GameError("❌ You have no relation with that person. See /family")
        key = r.kind if r.user_a == uid else inverse_key(r.kind, await gender_of(s, r.user_a))
        name = (await s.execute(select(User.first_name).where(User.id == other))).scalar() or "them"
        await s.delete(r)
        return role_info(key)[1], name


async def blocked_for_marriage(s, a: int, b: int) -> bool:
    r = await _pair(s, a, b)
    return bool(r and norm(r.kind) in BLOCKS_MARRIAGE)


async def relatives_of(uid: int) -> list[dict]:
    """Every relation from uid's point of view: role key, group, other user, since."""
    async with db.tx() as s:
        rows = (await s.execute(select(Relation).where(or_(Relation.user_a == uid, Relation.user_b == uid)).order_by(Relation.created_at))).scalars().all()
        out = []
        for r in rows:
            if r.user_a == uid:
                other, key = r.user_b, r.kind
            else:
                other, key = r.user_a, inverse_key(r.kind, await gender_of(s, r.user_a))
            name = (await s.execute(select(User.first_name).where(User.id == other))).scalar() or "Player"
            emoji, title, group = role_info(key)
            out.append(dict(other=other, name=name, emoji=emoji, title=title, group=group, key=norm(key),
                            days=(utcnow() - aware(r.created_at)).days))
        return out


async def bonus_part(s, uid: int) -> int:
    n = await count_of(s, uid)
    return min(n, BONUS_CAP_RELATIVES) * BONUS_PER_RELATIVE


async def tree_clusters(uid: int) -> tuple[dict[str, list[dict]], dict]:
    """Family only (no friends / besties) grouped for the picture: cluster -> [{uid, name, title, inlaw}], plus the life view.
    uid is the relative's Telegram id (None for children, who are bot characters without an account)."""
    from services import life
    v = await life.family_view(uid)
    out: dict[str, list[dict]] = {}
    for r in await relatives_of(uid):
        c = TREE_CLUSTER.get(r["key"])
        if c:
            out.setdefault(c, []).append(dict(uid=r["other"], name=r["name"], title=r["title"], inlaw=c in ("pil", "sil", "cil")))
    for n, g, adopted, _age in v["kids"]:
        out.setdefault("children", []).insert(0, dict(uid=None, name=n, title=("Adopted " if adopted else "") + ("Son" if g == "b" else "Daughter"), inlaw=False))
    return out, v
