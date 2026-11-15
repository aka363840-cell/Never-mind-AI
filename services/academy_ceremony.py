"""Graduation ceremony and report card (Part 6): the section REGISTRY, title rules and all text. PURE at import time (no database,
no Telegram), so it is easy to test; the database half (gathering results, the Hall of Fame table, the tick) is in
services/academy_graduation.py.

Extras plug in WITHOUT editing this file:

    from services.academy_ceremony import ceremony_section

    @ceremony_section(name="my_extra", order=200, scope="both")       # scope: "ceremony" | "card" | "both"
    async def my_extra(ctx):                                          # sync or async; return HTML text, or None to add nothing
        if ctx.scope == "card":
            return f"🌟 Your extra line for {ctx.card.name}"
        return "🌟 A line for the group message"

`ctx` is a SectionContext (scope, data = CeremonyData, card = CardData | None). A section that raises is logged and skipped; the
ceremony still goes out. Sections run in `order` (built-in blocks use 10..90, so 100+ lands after them and before the footer).
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Sequence

from game import academy_rules as AR
from services import academy_remind_logic as RL
from utils.content import academy_remind_texts as T
from utils.fmt import esc

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ data the builders read
@dataclass
class StudentInfo:
    user_id: int
    student_id: int
    name: str
    stream: str
    club: str
    stats: dict[str, int] = field(default_factory=dict)
    attended: int = 0                 # class days attended
    possible: int = 1                 # class days the student could attend (a late joiner has fewer)


@dataclass
class CeremonyData:
    season_id: int
    number: int
    results: list = field(default_factory=list)                 # Part 3 Ranked rows, best first
    students: dict[int, StudentInfo] = field(default_factory=dict)   # by user id: every enrolled, non-flagged student
    titles: dict[str, list[tuple[int, str, str | None]]] = field(default_factory=dict)   # title -> [(user_id, name, detail)]
    club_rows: list = field(default_factory=list)               # rows with .club and .total, best first
    club_winner: str | None = None
    hof_prev: list[tuple[int, str, float | None]] = field(default_factory=list)          # (season number, name, gpa) earlier valedictorians
    next_start: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)         # free space for plug-in sections
    session: Any = None                                         # an open DB session while the message is built (plug-ins may query)


@dataclass
class CardData:
    ceremony: CeremonyData
    user_id: int
    name: str
    student: StudentInfo | None = None
    result: Any = None                                          # the student's Ranked row, or None
    ranked_total: int = 0
    titles: list[str] = field(default_factory=list)             # title keys
    badges: dict[str, int] = field(default_factory=dict)        # rival / club badge points of the season
    trophies: list[tuple[str, str]] = field(default_factory=list)    # (kind, name) from adventures
    provisional: bool = False                                   # a report of a season that is still running
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SectionContext:
    scope: str                          # "ceremony" | "card"
    data: CeremonyData
    card: CardData | None = None


# ------------------------------------------------------------------ the registry
@dataclass(frozen=True)
class Section:
    name: str
    fn: Callable[[SectionContext], Any]
    order: int = 100
    scope: str = "both"


CEREMONY_SECTIONS: list[Section] = []


def ceremony_section(fn: Callable | None = None, *, name: str | None = None, order: int = 100, scope: str = "both"):
    """Register a section function (decorator). Registering the same name again replaces it, so re-importing a module is harmless."""
    if scope not in ("ceremony", "card", "both"):
        raise ValueError(f"scope must be ceremony, card or both, not {scope!r}")

    def deco(f: Callable) -> Callable:
        n = name or f"{f.__module__}.{f.__name__}"
        CEREMONY_SECTIONS[:] = [s for s in CEREMONY_SECTIONS if s.name != n] + [Section(n, f, order, scope)]
        return f
    return deco(fn) if fn is not None else deco


def unregister(name: str) -> None:
    CEREMONY_SECTIONS[:] = [s for s in CEREMONY_SECTIONS if s.name != name]


async def run_sections(scope: str, data: CeremonyData, card: CardData | None = None) -> list[str]:
    """The text blocks of every registered section for this scope, in `order` (ties: registration order). Never raises."""
    ctx = SectionContext(scope, data, card)
    out: list[str] = []
    for sec in sorted(CEREMONY_SECTIONS, key=lambda s: s.order):
        if sec.scope not in ("both", scope):
            continue
        try:
            res = sec.fn(ctx)
            if inspect.isawaitable(res):
                res = await res
        except Exception:
            log.exception("ceremony section %r failed", sec.name)
            continue
        if res:
            out.append(str(res))
    return out


# ------------------------------------------------------------------ titles (pure rules)
def award_titles(results: Sequence, students: dict[int, StudentInfo], club_points: dict[int, int] | None = None
                 ) -> dict[str, list[tuple[int, str, str | None]]]:
    """{title: [(user_id, name, detail)]} for one season.
    * valedictorian: every student on rank 1 (a tie shares it) with a passing grade (not F)
    * topper: the best score of each STREAM (ties share it), at least AR.TOPPER_MIN_SCORE
    * club_champion: members of the winning club who contributed (they hold the season's club badge: `club_points` > 0); detail = club
    * perfect_attendance: attended every class day they could attend (a late joiner is measured against fewer days)
    * backbencher: attended no class at all, yet passed (grade not F)
    Flagged / banned players are not in `results` or `students` at all."""
    club_points = club_points or {}
    out: dict[str, list[tuple[int, str, str | None]]] = {k: [] for k in AR.TITLE_ORDER}
    by_user = {r.user_id: r for r in results}

    for r in results:
        if r.rank == 1 and r.grade != "F":
            out["valedictorian"].append((r.user_id, r.name, None))

    best: dict[str, float] = {}
    for r in results:
        st = students.get(r.user_id)
        if st is not None and r.score >= AR.TOPPER_MIN_SCORE:
            best[st.stream] = max(best.get(st.stream, 0.0), r.score)
    for r in results:
        st = students.get(r.user_id)
        if st is not None and best.get(st.stream) == r.score and r.score >= AR.TOPPER_MIN_SCORE:
            out["topper"].append((r.user_id, r.name, st.stream))

    for uid in sorted(students, key=lambda u: (-club_points.get(u, 0), u)):
        st = students[uid]
        if club_points.get(uid, 0) > 0:
            out["club_champion"].append((uid, st.name, st.club))
        if st.possible > 0 and st.attended >= st.possible:
            out["perfect_attendance"].append((uid, st.name, None))
        r = by_user.get(uid)
        if st.attended == 0 and st.possible > 0 and r is not None and r.grade != "F":
            out["backbencher"].append((uid, st.name, None))
    return out


def titles_of(user_id: int, titles: dict[str, list[tuple[int, str, str | None]]]) -> list[str]:
    return [k for k in AR.TITLE_ORDER if any(u == user_id for u, _, _ in titles.get(k, ()))]


def title_label(key: str) -> str:
    emoji, name = AR.TITLES[key]
    return f"{emoji} {name}"


# ------------------------------------------------------------------ small text helpers
def _names(items: Sequence[tuple[int, str, str | None]], limit: int | None = None) -> str:
    limit = AR.NAMES_PER_TITLE if limit is None else limit
    shown = [esc(n) for _, n, _ in items[:limit]]
    more = len(items) - limit
    return ", ".join(shown) + (f" +{more} more" if more > 0 else "")


def _medal(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")


# ------------------------------------------------------------------ ceremony text
def highlights(data: CeremonyData) -> str:
    """One 'Season highlights' line from numbers we already have (empty if there is nothing to say)."""
    bits = []
    if data.results:
        bits.append(f"{len(data.results)} graduate{'s' if len(data.results) != 1 else ''}")
        bits.append(f"class average GPA {sum(r.gpa for r in data.results) / len(data.results):.2f}")
        a = sum(1 for r in data.results if r.grade in ("A+", "A"))
        if a:
            bits.append(f"{a} with an A or better")
    pa = len(data.titles.get("perfect_attendance", ()))
    if pa:
        bits.append(f"{pa} perfect attendance")
    return T.CEREMONY_HIGHLIGHTS.format(bits=" · ".join(bits)) if bits else ""


# ------------------------------------------------------------------ ceremony text
def ceremony_blocks(data: CeremonyData) -> list[tuple[int, str]]:
    """The built-in blocks as (order, text): header 10, valedictorian 20, top 5 30, club 40, titles 50, hall of fame 60."""
    blocks: list[tuple[int, str]] = [(10, T.CEREMONY_HEAD.format(number=data.number))]
    if not data.results:
        blocks.append((20, T.CEREMONY_NO_GRADS))
    else:
        vale = data.titles.get("valedictorian", [])
        if vale:
            line = T.CEREMONY_VALE.format(names=_names(vale))
            if len(vale) == 1:
                line += "".join(f" · GPA {r.gpa:.2f}" for r in data.results if r.user_id == vale[0][0])
            blocks.append((20, line))
        top = data.results[:AR.CEREMONY_TOP]
        blocks.append((30, "\n".join([T.CEREMONY_TOP.format(n=len(top))] +
                                     [f"{_medal(r.rank)} {esc(r.name)} · {r.grade} · GPA {r.gpa:.2f}" for r in top])))
    if data.club_winner and data.club_winner in AR.CLUBS:
        c = AR.CLUBS[data.club_winner]
        pts = next((r.total for r in data.club_rows if r.club == data.club_winner), 0)
        blocks.append((40, T.CEREMONY_CLUB.format(club=f"{c.emoji} {esc(c.name)}", points=pts)))
    else:
        blocks.append((40, T.CEREMONY_NO_CLUB))
    tl = [f"{AR.TITLES[k][0]} <b>{AR.TITLES[k][1]}:</b> {_names(data.titles[k])}"
          for k in AR.TITLE_ORDER if k != "valedictorian" and data.titles.get(k)]
    if tl:
        blocks.append((50, "\n".join([T.CEREMONY_TITLES] + tl)))
    hl = highlights(data)
    if hl:
        blocks.append((55, hl))
    hof = T.CEREMONY_HOF
    if data.hof_prev:
        hof += "\n" + T.CEREMONY_HOF_PREV.format(names=", ".join(f"{esc(n)} (S{s})" for s, n, _ in data.hof_prev))
    blocks.append((60, hof))
    return blocks


async def build_ceremony(data: CeremonyData) -> str:
    """The group message: built-in blocks, then every registered 'ceremony' / 'both' section, then the footer."""
    blocks = ceremony_blocks(data)
    for sec_text in await run_sections("ceremony", data):
        blocks.append((100, sec_text))
    blocks.sort(key=lambda b: b[0])                         # stable: extras keep their registry order
    foot = [RL.variant(T.CEREMONY_FOOT_VARIANTS, f"s{data.season_id}")]
    if data.next_start is not None:
        foot.append(T.CEREMONY_NEXT.format(when=RL.when(data.next_start)))
    return "\n\n".join([t for _, t in blocks] + ["\n".join(foot)])


# ------------------------------------------------------------------ report card
def _score(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}"


def card_blocks(card: CardData) -> list[str]:
    st, r = card.student, card.result
    lines = [T.CARD_HEAD.format(number=card.ceremony.number), ""]
    who = f"<b>{esc(card.name)}</b>"
    if st is not None:
        s, c = AR.STREAMS.get(st.stream), AR.CLUBS.get(st.club)
        if s:
            who += f" · {s.emoji} {esc(s.name)}"
        if c:
            who += f" · {c.emoji} {esc(c.name)} club"
    lines.append(who)
    if card.provisional:
        lines += [T.CARD_PROVISIONAL]
    lines.append("")
    if r is not None:
        lines.append(f"📊 Midterm <b>{_score(r.midterm)}</b> · Finals <b>{_score(r.finals)}</b>")
        lines.append(f"🎓 {'GPA so far' if card.provisional or r.provisional else 'Final GPA'} <b>{r.gpa:.2f}</b> · Grade <b>{r.grade}</b>"
                     f" · Rank <b>#{r.rank}</b> of {card.ranked_total}")
    else:
        lines.append(T.CARD_NO_EXAMS if card.provisional else T.CARD_NOT_ENROLLED_FINAL)
    prize = card.extra.get("diamonds")
    if prize:
        lines.append(T.CARD_DIAMONDS.format(n=prize))
    if card.titles:
        lines.append("🎖 " + " · ".join(title_label(k) for k in card.titles))
    elif not card.provisional:
        lines.append("🎖 No titles this time. Try for one next season!")
    badge_bits = [f"Rival {card.badges.get('rival', 0)}", f"Club {card.badges.get('club', 0)}"]
    if card.trophies:
        badge_bits.append("Trophies: " + ", ".join(esc(n) for _, n in card.trophies[:6]) + (f" +{len(card.trophies) - 6}" if len(card.trophies) > 6 else ""))
    lines.append("🏅 Badges: " + " · ".join(badge_bits))
    if st is not None:
        lines.append(f"📅 Attendance: {st.attended}/{st.possible} class days")
        if st.stats:
            lines.append("📈 " + " · ".join(f"{AR.STAT_LABELS[k].split(' ')[0]} {st.stats.get(k, 0)}" for k in AR.STAT_KEYS))
    return lines


async def build_card(card: CardData) -> str:
    """The report card text (DM or /reportcard): built-in lines, registered 'card' / 'both' sections, then the next-season line."""
    lines = card_blocks(card)
    for sec_text in await run_sections("card", card.ceremony, card):
        lines += ["", sec_text]
    if not card.provisional and card.ceremony.next_start is not None:
        lines += ["", T.CEREMONY_NEXT.format(when=RL.when(card.ceremony.next_start))]
    return "\n".join(lines)


# ------------------------------------------------------------------ Hall of Fame text
@dataclass
class HofEntry:
    title: str
    user_id: int
    name: str
    gpa: float | None = None
    detail: str | None = None


def hof_text(seasons: Sequence[tuple[int, Sequence[HofEntry]]]) -> str:
    """seasons = [(season number, entries)] newest first. Valedictorians, toppers and club champions are listed; the lighter titles are counted."""
    if not seasons:
        return T.NO_HOF
    out = [T.HOF_HEAD]
    for number, entries in seasons:
        def pick(k):
            return [e for e in entries if e.title == k]
        out += ["", f"<b>Season {number}</b>"]
        vale = pick("valedictorian")
        out.append("🎓 Valedictorian: " + (", ".join(esc(e.name) + (f" (GPA {e.gpa:.2f})" if e.gpa is not None else "") for e in vale) or "—"))
        top = pick("topper")
        if top:
            out.append("⭐ Toppers: " + ", ".join(
                f"{esc(e.name)} ({AR.STREAMS[e.detail].emoji} {esc(AR.STREAMS[e.detail].name)})" if e.detail in AR.STREAMS else esc(e.name)
                for e in top[:AR.NAMES_PER_TITLE]) + (f" +{len(top) - AR.NAMES_PER_TITLE} more" if len(top) > AR.NAMES_PER_TITLE else ""))
        cc = pick("club_champion")
        if cc:
            club = AR.CLUBS.get(cc[0].detail or "")
            label = f"{club.emoji} {esc(club.name)}" if club else "the winning club"
            names = ", ".join(esc(e.name) for e in cc[:AR.NAMES_PER_TITLE]) + (f" +{len(cc) - AR.NAMES_PER_TITLE} more" if len(cc) > AR.NAMES_PER_TITLE else "")
            out.append(f"🏆 Club Champions ({label}): {names}")
        extra = []
        if pick("perfect_attendance"):
            extra.append(f"📅 {len(pick('perfect_attendance'))} perfect attendance")
        if pick("backbencher"):
            extra.append(f"🪑 {len(pick('backbencher'))} backbencher{'s' if len(pick('backbencher')) != 1 else ''}")
        if extra:
            out.append(" · ".join(extra))
    return "\n".join(out)
