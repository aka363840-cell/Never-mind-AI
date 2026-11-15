"""Academy screens as HTML text (pure: they take already-loaded rows and `now`, no DB, no Telegram)."""
from __future__ import annotations

from datetime import datetime

from database.models import AcademySeason, AcademyStudent, aware
from game import academy_rules as AR
from services import academy_calendar as cal, academy_students as stu
from utils.content import academy_texts as T
from utils.fmt import dur, esc


def season_title(season: AcademySeason) -> str:
    return f"Season {season.number}" + (f" · <i>{esc(season.theme)}</i>" if season.theme else "")


def phase_line(season: AcademySeason, now: datetime) -> str:
    ph = cal.phase_at(season.start_at, now)
    left = dur(int(cal.time_left(season.start_at, now).total_seconds()))
    if ph.key == AR.BREAK_KEY:
        nxt = cal.next_phase(season.start_at, now)
        return f"{ph.emoji} <b>Break</b> · next season opens in <b>{dur(int((nxt.starts_at - aware(now)).total_seconds()))}</b>"
    if ph.key == AR.UPCOMING_KEY:
        return f"{ph.emoji} <b>Opening soon</b>"
    return f"📍 <b>Day {ph.day} — {ph.emoji} {esc(ph.name)}</b> · ends in <b>{left}</b>"


def hints_for(season: AcademySeason, now: datetime) -> list[str]:
    ph = cal.phase_at(season.start_at, now)
    return list(T.BREAK_HINTS if ph.day == 0 else T.DAY_HINTS.get(ph.day, ()))


def dashboard(season: AcademySeason, st: AcademyStudent | None, now: datetime) -> str:
    head = f"🎓 <b>Academy</b> — {season_title(season)}"
    lines = [head, "", phase_line(season, now)]
    ph = cal.phase_at(season.start_at, now)
    if ph.day:
        lines.append(f"<i>{esc(T.PHASE_BLURBS[ph.day])}</i>")
    if st is None:
        win = cal.enroll_window(season.start_at, now)
        lines += ["", T.NOT_ENROLLED_OPEN if win.open else T.CLOSED_FOR_SEASON]
        if win.open and win.late:
            lines.append(T.LATE_JOIN_NOTE)
        return "\n".join(lines)
    stream, club = AR.STREAMS[st.stream], AR.CLUBS[st.club]
    e = stu.current_energy(st, now)
    full = stu.seconds_to_full(st, now)
    lines += ["", f"{stream.emoji} <b>{esc(stream.name)}</b> · {club.emoji} {esc(club.name)}"]
    if st.late_join:
        lines.append(T.LATE_JOIN_NOTE)
    lines.append("")
    lines += [f"{AR.STAT_LABELS[k]}: <b>{getattr(st, k)}</b>" for k in AR.STAT_KEYS]
    lines.append(f"⚡ Energy {stu.energy_bar(e)} <b>{e}/{AR.ENERGY_MAX}</b>" + (f" · full in {dur(full)}" if full > 0 else ""))
    hints = hints_for(season, now)
    if hints:
        lines += ["", "💡 <b>Today</b>"] + [f"• {esc(h)}" for h in hints]
    return "\n".join(lines)


def schedule(season: AcademySeason, now: datetime) -> str:
    now = aware(now)
    start = aware(season.start_at)
    cur = cal.phase_at(start, now)
    nxt = cal.next_phase(start, now)
    lines = [f"📅 <b>Academy schedule</b> — {season_title(season)}", "", phase_line(season, now)]
    if cur.key == AR.BREAK_KEY:
        lines.append(f"⏭ Next: {nxt.emoji} {esc(nxt.name)} (new season) at {cal.fmt_when(nxt.starts_at)}")
    else:
        lines.append(f"⏭ Next in <b>{dur(int(cal.time_left(start, now).total_seconds()))}</b>: {nxt.emoji} {esc(nxt.name)}"
                     if nxt.day else f"⏭ Next: 🌴 Break at {cal.fmt_when(nxt.starts_at)}")
    lines += ["", "<b>The week</b>"]
    for p in cal.week(start):
        mark = "✅" if p.ends_at <= now else ("▶️" if p.key == cur.key else "▫️")
        lines.append(f"{mark} Day {p.day} · {p.emoji} {esc(p.name)} — {cal.fmt_when(p.starts_at)}")
    brk = cal.phase_at(start, cal.season_end(start))
    lines.append(f"{'✅' if brk.ends_at <= now else ('▶️' if cur.key == AR.BREAK_KEY else '▫️')} 🌴 Break — {cal.fmt_when(brk.starts_at)}")
    lines.append("")
    lines.append(f"🕓 Late enrolment closes: {cal.fmt_when(cal.enroll_window(start, start).closes_at)}")
    lines.append(f"🌅 Next season opens: {cal.fmt_when(start + cal.cycle())}")
    return "\n".join(lines)
