"""Family tree & relationships: /family, /addfamily (or /father /brother /friend ...), /unfamily, /gender, /roles."""
from __future__ import annotations

import asyncio
import json
import secrets

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

from database.models import User
from database.session import db
from keyboards.menus import kb
from services import family, gifting, life
from services.errors import GameError
from services.kv import kv
from utils import avatars, brand, gifs, paging
from utils.fmt import esc, mention
from utils.targets import resolve_target
from utils.telegram import safe_answer, safe_edit, strip_html

_TREE_SLOT = asyncio.Semaphore(1)


def _release_memory() -> None:
    """Give the memory used by the big drawing back to the OS (matters on small hosts)."""
    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
router = Router()


# ------------------------------------------------------------------ tree
def _branch(lines: list[str], items: list[str]) -> None:
    for i, it in enumerate(items):
        lines.append(f"  {'└' if i == len(items) - 1 else '├'} {it}")


PAGE_CHARS = 3000          # a tree page stays under this many visible characters (Telegram's hard limit is 4096)


def _vis(t: str) -> int:
    return len(strip_html(t))


async def tree_sections(uid: int, name: str) -> tuple[str, list[tuple[str, list[str]]], str]:
    """-> (title line, [(section header, item lines)], tail: property + command hints). Items are plain lines; branches are drawn per page."""
    v = await life.family_view(uid)
    rel = await family.relatives_of(uid)
    bonds = await gifting.bond_points_map(uid)                # bond titles earned with /sendgift and /loveletter
    head = f"✦ ━━ <b>Family of {esc(name)}</b> ━━ ✦\n"
    sections: list[tuple[str, list[str]]] = []
    if v["partner"]:
        pid, pname = v["partner"]
        sections.append(("💍 <b>Partner</b>", [f"❤️ {mention(pid, pname)} · {life.love_title(v['love'])} · love <b>{v['love']}</b> · {v['days']}d together"]))
    else:
        sections.append(("💍 <b>Partner</b>", ["<i>not married — /marry</i>"]))
    by_group: dict[str, list[str]] = {}
    for r in rel:
        by_group.setdefault(r["group"], []).append(f"{r['emoji']} <b>{esc(r['title'])}</b> — {mention(r['other'], r['name'])}"
                                                 + (f" · {t}" if (t := gifting.bond_title(bonds.get(r['other'], 0))) else ""))
    kids = [f"{'👦' if g == 'b' else '👧'} {esc(n)} · {age}d old{' · adopted' if ad else ''}" for n, g, ad, age in v["kids"]]
    by_group["children"] = kids + by_group.get("children", [])
    shown = 0
    for gkey, (gemoji, gtitle) in family.GROUPS.items():
        items = by_group.get(gkey)
        if items:
            shown += len(items)
            sections.append((f"{gemoji} <b>{esc(gtitle)}</b>", items))
    tail = []
    if not shown and not v["partner"]:
        tail.append("\n<i>Your family tree is empty. Reply to someone with</i> /father <i>·</i> /brother <i>·</i> /friend <i>…</i>")
    if v["houses"]:
        tail.append("\n🏠 <b>Property</b>  " + " ".join(life.HOUSES[k][0] + life.HOUSES[k][1] for k in v["houses"] if k in life.HOUSES))
    tail.append("\n<i>/addfamily · /unfamily · /familybonus · /date · /gift · /houses · /roles</i>")
    return head, sections, "\n".join(tail)


def split_pages(head: str, sections: list[tuple[str, list[str]]], tail: str, limit: int = PAGE_CHARS) -> list[list[tuple[str, list[str], bool]]]:
    """Pack sections into pages of at most ~`limit` visible characters. A section that runs over a page border continues on
    the next page under its repeated header (flag = continued). One page is returned whenever everything fits."""
    pages: list[list[tuple[str, list[str], bool]]] = [[]]
    used = _vis(head)
    for header, items in sections:
        cur: list[str] | None = None
        started = False
        for it in items:
            cost = _vis(it) + 6
            need = cost + (0 if cur is not None else _vis(header) + 2)
            if used + need > limit and pages[-1]:
                pages.append([])
                used = _vis(head)
                cur = None
                need = cost + _vis(header) + 2
            if cur is None:
                cur = []
                pages[-1].append((header, cur, started))
                started = True
                used += _vis(header) + 2
            cur.append(it)
            used += cost
    return pages


async def render_tree_page(uid: int, name: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup | None]:
    head, sections, tail = await tree_sections(uid, name)
    pages = split_pages(head, sections, tail)
    page = paging.clamp(page, len(pages), 1)
    lines = [head]
    for i, (header, items, cont) in enumerate(pages[page]):
        lines.append(("" if page == 0 and i == 0 else "\n") + header + (" <i>(cont.)</i>" if cont else ""))   # page 1 opens with the Partner block, as before
        _branch(lines, items)
    if page == len(pages) - 1:
        lines.append(tail)
    if len(pages) > 1:
        lines.append(paging.footer(page, len(pages)))
    row = paging.nav(lambda p: f"ft:{uid}:{p}", page, len(pages))
    return "\n".join(lines), (kb([row]) if row else None)


async def render_tree(uid: int, name: str) -> str:
    return (await render_tree_page(uid, name, 0))[0]


@router.callback_query(F.data.startswith("ft:"))
async def tree_page_cb(cb: CallbackQuery):
    _, uid, page = cb.data.split(":")
    async with db.tx() as s:
        u = await s.get(User, int(uid))
    text, markup = await render_tree_page(int(uid), u.first_name if u else "Player", int(page))
    await safe_edit(cb, text, markup)
    await safe_answer(cb)


@router.message(Command("family", "kids", "tree", "relatives"))
async def family_cmd(m: Message, command: CommandObject, db_user: User):
    target, _ = await resolve_target(m, (command.args or "").split())
    uid, name = db_user.id, db_user.first_name
    if target and target != db_user.id:
        async with db.tx() as s:
            u = await s.get(User, target)
        if not u:
            raise GameError("🔎 I haven't seen that player yet. Reply to any message of theirs with the command.")
        uid, name = u.id, u.first_name
    text, markup = await render_tree_page(uid, name, 0)
    await m.reply(text, reply_markup=markup)


@router.message(Command("familytree", "ftree", "treepic"))
async def familytree_cmd(m: Message, command: CommandObject, db_user: User):
    """Picture of the family tree - blood relatives, in-laws, partner and kids only (no friends / besties)."""
    target, _ = await resolve_target(m, (command.args or "").split())
    uid, name = db_user.id, db_user.first_name
    if target and target != db_user.id:
        async with db.tx() as s:
            u = await s.get(User, target)
        if not u:
            raise GameError("🔎 I haven't seen that player yet. Reply to any message of theirs with the command.")
        uid, name = u.id, u.first_name
    groups, v = await family.tree_clusters(uid)
    if not groups and not v["partner"]:
        raise GameError("🌳 No family to draw yet. Reply to someone with /father · /brother · /son … or /marry. (Friends are not part of the tree.)")
    partner_id = v["partner"][0] if v["partner"] else None
    from utils import treeimg                      # Pillow is only loaded when somebody asks for the picture
    pics = await avatars.fetch(m.bot, [uid, partner_id] + [x["uid"] for items in groups.values() for x in items])
    P = treeimg.Person
    clusters = {k: [P(x["name"], x["title"], "inlaw" if x["inlaw"] else "blood", pics.get(x["uid"])) for x in items] for k, items in groups.items()}
    couple = [P(name, "You", "you", pics.get(uid))]
    love = ""
    if v["partner"]:
        couple.append(P(v["partner"][1], "Partner", "partner", pics.get(partner_id)))
        love = f"Love {v['love']} · {v['days']}d"
    clusters["couple"] = couple
    async with _TREE_SLOT:                         # one picture at a time: keeps peak memory low on small hosts
        png = await asyncio.to_thread(treeimg.render, name, clusters, love, brand.name())
        _release_memory()
    total = sum(len(x) for k, x in clusters.items() if k != "couple") + len(couple) - 1
    cap = f"🌳 <b>Family of {esc(name)}</b> · {total} member{'s' if total != 1 else ''}\n<i>Friends are not shown · text version: /family</i>"
    await m.reply_photo(BufferedInputFile(png, "familytree.png"), caption=cap)


# ------------------------------------------------------------------ roles / gender
@router.message(Command("roles", "familyroles"))
async def roles_cmd(m: Message):
    out = ["👨‍👩‍👧‍👦 <b>Who can they be to you?</b>\nReply to someone with <code>/addfamily &lt;role&gt;</code> — or use the role as a command.\n"]
    for gkey, (gemoji, gtitle) in family.GROUPS.items():
        keys = [k for k, v in family.ROLES.items() if v[2] == gkey]
        out.append(f"{gemoji} <b>{esc(gtitle)}</b>: " + " ".join(f"/{k}" for k in keys))
    out.append("\nThe role is what <b>they</b> become to <b>you</b> — /father on someone = “you are my father”.\nThey must accept. Set /gender so they are labelled correctly (Father/Mother, Brother/Sister …).")
    await m.reply("\n".join(out))


@router.message(Command("gender"))
async def gender_cmd(m: Message, command: CommandObject, db_user: User):
    arg = (command.args or "").strip().lower()
    g = "b" if arg in ("boy", "male", "m", "ladka", "b") else "g" if arg in ("girl", "female", "f", "ladki", "g") else None
    if g is None:
        async with db.tx() as s:
            cur = await family.gender_of(s, db_user.id)
        await m.reply(f"⚧ Your gender: <b>{'Boy 👦' if cur == 'b' else 'Girl 👧' if cur == 'g' else 'not set'}</b>\nChange: <code>/gender boy</code> or <code>/gender girl</code>\n"
                      "<i>Only used to label your relatives (e.g. Father vs Mother). Optional.</i>")
        return
    await family.set_gender(db_user.id, g)
    await m.reply(f"✅ Saved — you are a <b>{'boy 👦' if g == 'b' else 'girl 👧'}</b> in family trees.")


# ------------------------------------------------------------------ request / accept
async def _request(m: Message, db_user: User, role_raw: str, rest: list[str], adopt: bool = False):
    kind = family.resolve_role(role_raw)
    if kind is None:
        raise GameError("❓ Unknown role. See the full list with /roles\nExample: reply to someone with <code>/addfamily brother</code>")
    target, _ = await resolve_target(m, rest)
    if target is None:
        raise GameError(f"👨‍👩‍👧 Reply to someone (or use @user) — e.g. reply with /{kind}")
    await family.can_request(db_user.id, target, kind)
    async with db.tx() as s:
        gen = await family.gender_of(s, db_user.id)
    emoji, title, _ = family.role_info(kind)
    inv = family.role_info(family.inverse_key(kind, gen))
    nonce = secrets.token_urlsafe(6)
    await kv.put(f"fm:{nonce}", json.dumps({"a": db_user.id, "b": target, "k": kind, "an": db_user.first_name}), 300)
    ask = (f"wants to adopt <a href=\"tg://user?id={target}\">you</a> as their <b>{esc(title)}</b>!" if adopt
           else f"wants <a href=\"tg://user?id={target}\">you</a> to be their <b>{esc(title)}</b>!")
    await m.reply(f"{emoji} {mention(db_user.id, db_user.first_name)} {ask}\n"
                  f"{inv[0]} You would be their <b>{esc(inv[1])}</b>.\n<i>Expires in 5 minutes.</i>",
                  reply_markup=kb([[B(text="✅ Accept", callback_data=f"fm:ok:{nonce}"), B(text="✖️ Decline", callback_data=f"fm:no:{nonce}")]]))


@router.message(Command("addfamily", "relate", "addrelative"))
async def addfamily_cmd(m: Message, command: CommandObject, db_user: User):
    args = (command.args or "").split()
    if not args:
        raise GameError("👨‍👩‍👧 Usage: reply to someone with <code>/addfamily &lt;role&gt;</code> (e.g. brother, father, friend). Roles: /roles")
    await _request(m, db_user, args[0], args[1:])


@router.message(Command("adopt"))
async def adopt_cmd(m: Message, command: CommandObject, db_user: User):
    """Reply to a player with /adopt: they get an Accept / Decline message and become your son / daughter if they accept."""
    args = (command.args or "").split()
    target, rest = await resolve_target(m, args)
    if target is None:
        raise GameError("🧸 Reply to the person you want to adopt with <code>/adopt</code> (or <code>/adopt @user</code>).")
    pick = next((a.lower() for a in rest if a.lower() in ("son", "daughter", "boy", "girl")), None)
    async with db.tx() as s:
        tg = await family.gender_of(s, target)
    kind = "son" if pick in ("son", "boy") else "daughter" if pick in ("daughter", "girl") else "son" if tg == "b" else "daughter" if tg == "g" else None
    if kind is None:
        raise GameError("🧸 Is it a son or a daughter? Use <code>/adopt son</code> or <code>/adopt daughter</code> (reply to them).")
    await _request(m, db_user, kind, [a for a in rest if a.lower() not in ("son", "daughter", "boy", "girl")], adopt=True)


def _role_cmd(key: str):
    @router.message(Command(key))
    async def handler(m: Message, command: CommandObject, db_user: User):
        await _request(m, db_user, key, (command.args or "").split())
    return handler


for _k in family.ROLES:
    _role_cmd(_k)


def _alias_cmd(alias: str, key: str):
    @router.message(Command(alias))
    async def handler(m: Message, command: CommandObject, db_user: User):
        await _request(m, db_user, key, (command.args or "").split())
    return handler


for _al, _key in family.ALIASES.items():        # hidden shortcuts: /dad /mom /bro ... also work
    if _al not in family.ROLES and "-" not in _al:
        _alias_cmd(_al, _key)


@router.callback_query(F.data.startswith("fm:"))
async def family_cb(cb: CallbackQuery):
    _, verb, nonce = cb.data.split(":")
    raw = await kv.get(f"fm:{nonce}")
    if not raw:
        await safe_edit(cb, "⌛ This family request expired.")
        await safe_answer(cb)
        return
    d = json.loads(raw)
    if verb == "no":
        if cb.from_user.id not in (d["a"], d["b"]):
            await safe_answer(cb, "This isn't for you.", alert=True)
            return
        await kv.take(f"fm:{nonce}")
        await safe_edit(cb, "💔 The family request was declined / cancelled.")
        await safe_answer(cb)
        return
    if cb.from_user.id != d["b"]:
        await safe_answer(cb, "Only the person who was asked can accept 💞", alert=True)
        return
    if not await kv.take(f"fm:{nonce}"):
        await safe_answer(cb)
        return
    await family.create(d["a"], d["b"], d["k"])
    async with db.tx() as s:
        gen = await family.gender_of(s, d["a"])
    emoji, title, _ = family.role_info(d["k"])
    inv = family.role_info(family.inverse_key(d["k"], gen))
    await safe_edit(cb, f"🎉 <b>New family bond!</b>\n{emoji} {mention(d['b'], cb.from_user.first_name)} is now "
                        f"{mention(d['a'], d.get('an') or 'their')}'s <b>{esc(title)}</b>\n{inv[0]} and {mention(d['a'], d.get('an') or 'they')} is their <b>{esc(inv[1])}</b>.\n"
                        "<i>See it with /family</i>")
    await safe_answer(cb)
    if cb.message:
        await gifs.bonus(cb.message, "family")


@router.message(Command("unfamily", "removefamily", "cutoff"))
async def unfamily_cmd(m: Message, command: CommandObject, db_user: User):
    target, _ = await resolve_target(m, (command.args or "").split())
    if target is None:
        raise GameError("Usage: reply to the person (or @user) with /unfamily")
    title, name = await family.remove(db_user.id, target)
    await m.reply(f"💔 {esc(name)} is no longer your <b>{esc(title)}</b>.")
