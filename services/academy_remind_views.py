"""Academy Part 6: text of the season-open message, enrolment reminders and the daily recaps (PURE: rows in, HTML out)."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

from game import academy_rules as AR
from services import academy_remind_logic as RL
from utils.content import academy_remind_texts as T, academy_texts as AT
from utils.fmt import dur, esc


# ------------------------------------------------------------------ season open / enrolment
def season_open_text(number: int, theme: str, closes_at: datetime) -> str:
    return T.SEASON_OPEN.format(number=number, theme=T.THEME_LINE.format(theme=esc(theme)) if theme else "", closes=RL.when(closes_at))


def enroll_group_text(plan: RL.GroupPlan, number: int, closes_at: datetime, now: datetime, window: int = 1) -> str:
    if plan.last_call:
        left = dur(max(60, int((RL.aware(closes_at) - RL.aware(now)).total_seconds())))
        return T.LAST_CALL_GROUP.format(number=number, closes=RL.when(closes_at), left=left)
    line = T.ENROLL_GROUP[window % len(T.ENROLL_GROUP)].format(number=number)
    return line + T.ENROLL_GROUP_TAIL.format(closes=RL.when(closes_at))


def enroll_dm_text(day: int, number: int, closes_at: datetime) -> str:
    base = T.ENROLL_DM.get(day, T.ENROLL_DM[2]).format(number=number, closes=RL.when(closes_at))
    return base + "\n\n" + T.OPT_OUT_NOTE


def reminder_dm_text(text: str) -> str:
    return text + "\n\n" + T.OPT_OUT_NOTE


# ------------------------------------------------------------------ recap pieces
def top_student(results: Sequence, progress: Iterable[tuple[int, str, int, int]], members: set[int] | None = None) -> tuple[str, str] | None:
    """(name, 'GPA 8.20' | 'Knowledge 34') of the best student so far, or None. `results` are Part 3 Ranked rows (best first); when the
    first exam is done they decide. Before that the highest Knowledge (then total stats, then lower user id) wins. `members` limits
    the choice to one group's players; if that leaves nobody the school-wide best is used."""
    def pick(ids: set[int] | None):
        rs = [r for r in results if ids is None or r.user_id in ids]
        if rs:
            r = rs[0]
            return r.name, f"GPA {r.gpa:.2f}"
        ps = [p for p in progress if ids is None or p[0] in ids]
        if ps:
            uid, name, k, total = sorted(ps, key=lambda p: (-p[2], -p[3], p[0]))[0]
            return name, f"Knowledge {k}"
        return None
    return (pick(members) if members else None) or pick(None)


def club_lines(rows: Sequence) -> list[str]:
    """['🥇 🏀 Sports 120', ...] from rows with .club and .total (best first)."""
    medals = ("🥇", "🥈", "🥉", "▫️")
    out = []
    for i, r in enumerate(rows):
        c = AR.CLUBS[r.club]
        out.append(f"{medals[min(i, 3)]} {c.emoji} {esc(c.name)} · <b>{r.total}</b>")
    return out


def teaser(day: int) -> str:
    """One line about tomorrow (day + 1)."""
    ph = AR.PHASE_BY_DAY.get(day + 1)
    if ph is None:
        return ""
    return T.RECAP_TEASER.format(emoji=ph.emoji, day=ph.day, name=esc(ph.name), blurb=esc(AT.PHASE_BLURBS[ph.day]))


def recap_group_text(day: int, top: tuple[str, str] | None, clubs: Sequence) -> str:
    ph = AR.PHASE_BY_DAY[day]
    lines = [T.RECAP_GROUP_HEAD.format(day=day, emoji=ph.emoji, name=esc(ph.name)), ""]
    lines.append(T.RECAP_TOP.format(name=esc(top[0]), how=esc(top[1])) if top else T.RECAP_NO_TOP)
    cl = club_lines(clubs)
    if cl and any(getattr(r, "total", 0) for r in clubs):
        lines += ["", T.RECAP_CLUBS] + cl
    tz = teaser(day)
    if tz:
        lines += ["", tz]
    return "\n".join(lines)


def next_step(day: int) -> str:
    """'what's next' for the DM recap: tomorrow's headline plus a pointer to the right command."""
    ph = AR.PHASE_BY_DAY.get(day + 1)
    if ph is None:
        return "see you at the ceremony."
    hint = {4: "the Midterm opens: /exam", 5: "Fest Day, adventures pay a bonus: /adventure", 6: "the Finals open: /exam",
            7: "results and the graduation ceremony.", 3: "club battle and rivals: /clubs, /rival", 2: "classes begin: /attend, /study"}.get(day + 1, "keep going: /study")
    return f"{ph.emoji} Day {ph.day} — {esc(ph.name)}: {hint}"


def recap_dm_text(day: int, *, energy_used: int, energy_now: int, stats: dict[str, int],
                  rank: int | None, total: int, gpa: float | None, provisional: bool) -> str:
    lines = [T.RECAP_DM_HEAD.format(day=day), ""]
    lines.append(T.RECAP_DM_ENERGY.format(used=energy_used, bar="▰" * round(10 * energy_now / AR.ENERGY_MAX) + "▱" * (10 - round(10 * energy_now / AR.ENERGY_MAX)),
                                          now=energy_now, max=AR.ENERGY_MAX))
    lines.append(T.RECAP_DM_STATS.format(stats=" · ".join(f"{AR.STAT_LABELS[k].split(' ')[0]} <b>{stats.get(k, 0)}</b>" for k in AR.STAT_KEYS)))
    if rank is not None and gpa is not None:
        lines.append(T.RECAP_DM_RANK.format(rank=rank, total=total, gpa=gpa, prov=", provisional" if provisional else ""))
    else:
        lines.append(T.RECAP_DM_NO_RANK)
    lines += ["", T.RECAP_DM_NEXT.format(text=next_step(day)), "", T.OPT_OUT_NOTE]
    return "\n".join(lines)
