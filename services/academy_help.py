"""/academyhelp pages (Part 7). PURE: no database, no Telegram. Every number, time and name is read from game/academy_rules.py, the
adventure data and the pure calendar functions AT CALL TIME, so the help can never go stale (tests/test_academy_help.py changes
rules and checks the text follows).

    render(page, season_start=None, now=None) -> HTML text (always < 4000 characters)
    resolve(arg) -> page key | None          ('week', 'exam', 'privacy' ... aliases included)
    nav(page) -> (back, next)                the Back / Next page keys (None = no such button)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from database.models import aware
from game import academy_rules as AR
from services import academy_adventure_logic as AL, academy_calendar as cal, academy_exam_logic as EL
from services import academy_remind_logic as RL, academy_social_logic as SL
from utils.content import academy_help_texts as T
from utils.fmt import esc

PAGE_KEYS: tuple[str, ...] = tuple(T.PAGES)
MAX_LEN = 4000


# ------------------------------------------------------------------ small formatters
def _num(x: float) -> str:
    return f"{x:g}"


def _mins(seconds: int) -> str:
    m = round(seconds / 60)
    if m >= 120 and m % 60 == 0:
        return f"{m // 60} h"
    return f"{m} min"


def _days_txt(days: Iterable[int]) -> str:
    ds = sorted(set(days))
    if not ds:
        return "never"
    if len(ds) == 1:
        return f"Day {ds[0]}"
    if ds == list(range(ds[0], ds[-1] + 1)):
        return f"Days {ds[0]}–{ds[-1]}"
    return "Days " + ", ".join(str(d) for d in ds)


def _signed(points: float) -> str:
    n = round(points * 100)
    return "0" if n == 0 else f"{n:+d}"


def _stat(key: str) -> str:
    return AR.STAT_LABELS.get(key, key.capitalize())


def _tzname() -> str:
    tz = AR.tz()
    return getattr(tz, "key", None) or str(tz)


def _hh(hour: int) -> str:
    return f"{hour:02d}:00"


def _bullets(lines: Iterable[str]) -> str:
    return "\n".join(f"• {x}" for x in lines)


def _stream_bonus(b: dict[str, int]) -> str:
    return ", ".join(f"+{v} {k.capitalize()}" for k, v in b.items())


def _adventures():
    from utils.content import academy_adventures as C
    return [C.ADVENTURES[k] for k in C.ORDER]


def _head(page: str) -> str:
    emoji, _label, title = T.PAGES[page]
    return f"{emoji} <b>{title}</b>"


# ------------------------------------------------------------------ pages
def page_start() -> str:
    streams = [f"{s.emoji} <b>{esc(s.name)}</b>: {esc(s.blurb)} ({_stream_bonus(s.bonus)})" for s in AR.STREAMS.values()]
    clubs = [f"{c.emoji} <b>{esc(c.name)}</b>: {esc(c.blurb)} (focus: {_stat(c.focus)})" for c in AR.CLUBS.values()]
    base = ", ".join(f"{_stat(k)} {v}" for k, v in AR.START_STATS.items())
    return "\n".join([
        _head("start"), "", T.START_WHAT, T.START_FREE, "",
        f"<b>A season</b> lasts {AR.SEASON_DAYS} real days and is shared by every player. Then comes a short break "
        f"({_num(AR.break_hours())} h) and the next season opens.", "",
        "<b>First steps</b>", _bullets(T.START_STEPS), "",
        f"<b>Enrolling:</b> open on Day 1 and still open until the end of Day {AR.LATE_JOIN_LAST_DAY}. Joining after Day 1 makes you a "
        f"late joiner with a lighter workload (class attendance is measured only against the days you could attend).", "",
        f"<b>Streams</b> (each gives a head start):", *streams, "",
        f"<b>Clubs</b> (a team for the club battle):", *clubs, "",
        f"<b>Your stats:</b> every stat starts at {_num(min(AR.START_STATS.values()))} to {_num(max(AR.START_STATS.values()))} "
        f"({base}) plus your stream bonus, up to {AR.STAT_MAX}. <b>Energy</b> (0 to {AR.ENERGY_MAX}) limits what you can do each day.",
        "Stats and Energy start fresh every season.",
    ])


def page_daily() -> str:
    rows = []
    for a in AR.ACTIONS.values():
        cost = f"{a.energy} Energy" if a.energy else "free"
        rows.append(f"{a.emoji} <b>/{a.key}</b>: {cost}, every {_mins(a.cooldown_s)}, up to {a.daily_limit} a day, {_days_txt(a.days)}")
    perks = []
    for k, s in AR.STREAMS.items():
        extra = AR.perk_actions(k)
        if extra:
            perks.append(f"{s.emoji} {esc(s.name)}: " + ", ".join(extra))
    streak = ", ".join(f"{d} days = +{v}" for d, v in sorted(AR.STREAK_MILESTONES.items()))
    return "\n".join([
        _head("daily"), "",
        f"Each action costs <b>Energy</b>. You get about {AR.ENERGY_REGEN_PER_HOUR} back every hour (a full bar of {AR.ENERGY_MAX} in "
        f"{_num(AR.ENERGY_MAX / AR.ENERGY_REGEN_PER_HOUR)} hours), so a little play every few hours beats one long burst.", "", *rows, "",
        "<b>What they give</b>",
        _bullets([
            f"Study: {AR.STUDY_GAIN[0]}–{AR.STUDY_GAIN[1]} Knowledge. Study streak bonus on your first study of a day: {streak} Knowledge.",
            f"Attend class: +{AR.ATTEND_GAIN} Knowledge (class days only).",
            f"Hang out: +{AR.HANGOUT_CHARISMA[0]}–{AR.HANGOUT_CHARISMA[1]} Charisma, sometimes +1 Creativity.",
            f"Club practice: {AR.CLUB_POINTS[0]}–{AR.CLUB_POINTS[1]} points for your club and +{AR.CLUB_STAT_GAIN[0]}–{AR.CLUB_STAT_GAIN[1]} "
            f"Fitness (Sports club) or Creativity.",
            f"Rest: +{AR.REST_ENERGY} Energy.",
            f"Part-time job: {AR.PARTTIME_PAY[0]}–{AR.PARTTIME_PAY[1]} 💎 a shift, at most {AR.DAILY_DIAMOND_CAP} 💎 a day.",
        ]), "",
        "<b>Stream perks</b> (small extras on these actions):", _bullets(perks),
    ])


def page_exams() -> str:
    kinds = []
    for k in AR.EXAM_KINDS:
        own = AR.EXAM_STREAM_CARDS[k]
        d_lo, d_hi = min(AR.EXAM_DIFFICULTIES[k]), max(AR.EXAM_DIFFICULTIES[k])
        kinds.append(f"{AR.EXAM_EMOJI[k]} <b>{AR.EXAM_NAMES[k]}</b>: Day {AR.EXAM_DAY[k]}, open all day, {AR.EXAM_ROUNDS[k]} rounds "
                     f"({own} from your own stream), difficulty {d_lo}–{d_hi}, worth {_num(AR.EXAM_WEIGHT[k] * 100)}% of your season result")
    risks = ", ".join(f"{r.capitalize()} {_signed(v)}" for r, v in AR.RISK_CHANCE.items())
    pts = ", ".join(f"{r.capitalize()} {ok}/{bad}" for r, (ok, bad) in AR.ROUND_POINTS.items())
    targets = ", ".join(f"{d}: {t}" for d, t in sorted(AR.STAT_TARGET.items()))
    grades = " · ".join(f"{g} {_num(lo)}+" if lo else f"{g} below {_num(AR.GRADES[-2][1])}" for g, lo in AR.GRADES)
    rewards = ", ".join(f"{g} {v}" for g, v in AR.EXAM_REWARD.items() if v)
    return "\n".join([
        _head("exams"), "", T.EXAM_NO_KNOWLEDGE, "",
        *kinds, "",
        f"<b>A round:</b> you get {AR.ROUND_SECONDS} seconds to pick one of the choices. Each choice uses a stat and a risk level.",
        _bullets([
            f"Your <b>chance</b> starts at {_num(AR.CHANCE_BASE * 100)}% and moves one point for every skill point above (or below) the target "
            f"of the card (target by difficulty: {targets}). Risk changes it: {risks} points.",
            f"Your <b>skill</b> is the choice's stat + {AR.STREAM_CARD_BONUS} on a card of your own stream + {AR.STREAM_STAT_BONUS} if the stat "
            f"is one your stream is good at + {_num(AR.STREAK_BONUS_PER_DAY)} per study-streak day (up to {_num(AR.STREAK_BONUS_MAX)}) + "
            f"up to {_num(AR.ATTENDANCE_BONUS_MAX)} for attendance.",
            f"Then <b>one dice roll</b> (luck) nudges the chance by at most {_num(AR.LUCK_CAP * 100)} points. Preparation decides most of it, "
            f"luck decides the close calls.",
            f"Points per round (success/fail): {pts}. Riskier choices pay more and can cost points.",
        ]), "",
        f"<b>Score</b> = your points ÷ (rounds × {AR.SCORE_REF}) × 100, kept between 0 and 100. <b>Grade:</b> {grades}.",
        f"<b>GPA</b> = score ÷ 10 (a 10-point scale). Reward per exam (💎, once): {rewards}.",
        f"<b>Focus:</b> re-roll one failed round (up to {AR.FOCUS_MAX_USES} per exam, {AR.FOCUS_ENERGY} Energy each). "
        f"A GPA under {_num(AR.PROBATION_GPA)} puts you on probation (no Focus).", "",
        "<b>Prepare:</b>", _bullets(T.EXAM_PREP_TIPS), "",
        T.GRADING_NOTE, "Commands: /exam, /grades, /classrank.",
    ])


def page_adv() -> str:
    lines = []
    for a in _adventures():
        when = _days_txt(a.days)
        if a.night:
            when += f", at night ({_hh(AR.ADV_NIGHT_FROM_HOUR)}–{_hh(AR.ADV_NIGHT_TO_HOUR)} {_tzname()})"
        bonus = ""
        if a.stream_bonus:
            bonus = " Stream bonus: " + ", ".join(f"{AR.STREAMS[k].name} +{v}" for k, v in a.stream_bonus.items() if k in AR.STREAMS) + "."
        lines.append(f"{a.emoji} <b>{esc(a.name)}</b>: {a.energy} Energy, {when}.{bonus}")
    targets = ", ".join(f"{d}: {t}" for d, t in sorted(AR.ADV_TARGET.items()))
    return "\n".join([
        _head("adv"), "", T.ADV_WHAT, T.ADV_PRESS, "",
        *lines, "",
        _bullets([
            f"Adventures open from Day {AR.ADV_FIRST_DAY}. The Energy is charged once, when you start.",
            f"You can finish up to {AR.ADV_MAX_PER_DAY} a day. One at a time; starting the same one again just resumes it.",
            f"A story you leave idle for {AR.ADV_EXPIRE_MINUTES} minutes ends with no reward and no refund.",
            f"A stat check: chance = {_num(AR.ADV_CHANCE_BASE * 100)}% + (your stat + clue bonuses - target), target by difficulty {targets}; "
            f"kept between {_num(AR.ADV_CHANCE_MIN * 100)}% and {_num(AR.ADV_CHANCE_MAX * 100)}%. One dice roll decides it.",
            f"Rewards depend on the ending: up to {AR.ADV_MAX_DIAMONDS} 💎 and a few stat points.",
            f"🎉 <b>Fest Day (Day {AR.ADV_FEST_DAY}):</b> +{AR.ADV_FEST_DIAMOND_PCT}% diamonds and +{AR.ADV_FEST_STAT_BONUS} on every stat "
            f"the ending gives.",
        ]), "", T.ADV_COSMETIC, "Command: /adventure.",
    ])


def page_social() -> str:
    return "\n".join([
        _head("social"), "", T.SOCIAL_WHOLESOME, "",
        f"🤝 <b>Rivals</b> (/rival, /rivalduel): paired once a season from Day {AR.RIVAL_PAIR_FROM_DAY}, by similar GPA and Knowledge "
        f"(or a bot rival if there is an odd one out). A duel is {AR.RIVAL_DUEL_ROUNDS} strategy rounds each on the same cards, "
        f"until the end of Day {AR.RIVAL_DUEL_LAST_DAY}, {AR.RIVAL_DUEL_ENERGY} Energy. Win: {AR.RIVAL_WIN_DIAMONDS} 💎 and a Rival badge point; "
        f"draw: {AR.RIVAL_TIE_DIAMONDS} 💎 each; if your rival never plays you still get {AR.RIVAL_WALKOVER_PCT}% of the win prize.", "",
        f"🎪 <b>Club battle</b> (/clubs, /clubs play): on Days {AR.BATTLE_FROM_DAY}–{AR.BATTLE_LAST_DAY} everyone plays ONE round "
        f"({AR.BATTLE_ENERGY} Energy) for their club; your club's <i>focus</i> stat gets +{AR.BATTLE_FOCUS_BONUS}. Club score = practice "
        f"points + round points. Every contributing member of the winning club gets {AR.CLUB_WIN_DIAMONDS} 💎 and a Club badge point.", "",
        f"💘 <b>Crushes</b> (/crush, /crushoff, /campuscouple): open from Day {AR.CRUSH_FROM_DAY} to the end of the season, opt-in, one at a time. "
        f"{T.CRUSH_PRIVACY}", "",
        f"🌹 <b>Campus date</b> (/campusdate): a couple can go on one date a day. The one who starts it pays {AR.DATE_ENERGY} Energy and "
        f"{AR.DATE_DIAMONDS} 💎; both get stat boosts and the couple earns {AR.DATE_POINTS} points.", "",
        f"📖 <b>Study buddy</b> (/buddy @name): once your friend accepts, you both get +{AR.BUDDY_KNOWLEDGE_BONUS} Knowledge on your first "
        f"/study each day. /buddy off ends it.", "",
        "Group tip: the season announcements and the graduation ceremony appear in active groups; use /classrank in a group for its own board.",
    ])


# ---- the week (real times)
def _when_factory(start: datetime | None):
    if start is None:
        def rel(dt: datetime, base=datetime(2000, 1, 1, tzinfo=timezone.utc)) -> str:
            h = int((dt - base).total_seconds() // 3600)
            return "when the season opens" if h == 0 else f"{h} h after the season opens"
        return rel
    return cal.fmt_when


def _day_notes(day: int, start: datetime, when) -> list[str]:
    notes: list[str] = []
    if day == 1:
        notes.append("enrolment opens: /enroll")
    elif day <= AR.LATE_JOIN_LAST_DAY:
        notes.append("late enrolment (lighter workload)")
    if day == AR.LATE_JOIN_LAST_DAY:
        notes.append(f"enrolment closes {when(RL.enroll_closes(start))}")
    if day in AR.CLASS_DAYS:
        notes.append("class day (/attend)")
    for k in AR.EXAM_KINDS:
        if AR.EXAM_DAY[k] == day:
            lo, hi = EL.exam_window(start, k)
            notes.append(f"{AR.EXAM_NAMES[k]} exam open until {when(hi)}")
    if day == AR.ADV_FIRST_DAY:
        notes.append("adventures open")
    if day == AR.RIVAL_PAIR_FROM_DAY:
        notes.append("rivals paired")
    if day == AR.BATTLE_FROM_DAY:
        notes.append("club battle opens")
    if day == AR.CRUSH_FROM_DAY:
        notes.append("crushes and dates open")
    if day == AR.BATTLE_LAST_DAY:
        notes.append(f"club battle closes {when(SL.battle_window(start)[1])}")
    if day == AR.RIVAL_DUEL_LAST_DAY:
        notes.append(f"last rival duels (until {when(SL.duel_deadline(start))})")
    if day == AR.ADV_FEST_DAY:
        only = [a.name for a in _adventures() if tuple(a.days) == (AR.ADV_FEST_DAY,)]
        notes.append(f"adventure bonus +{AR.ADV_FEST_DIAMOND_PCT}%" + (f", {' and '.join(esc(n) for n in only)} only today" if only else ""))
    if day not in AR.ACTIONS["parttime"].days:
        notes.append("part-time job closed")
    if day == AR.SEASON_DAYS:
        notes.append(f"graduation ceremony about {when(RL.ceremony_window(start)[0])}")
    return notes


def page_week(season_start: datetime | None = None, label: str | None = None) -> str:
    # With no real season yet, show the week on a sample clock (Day n = start + n-1 days) and word every time as an offset.
    sample = aware(season_start) if season_start is not None else datetime(2000, 1, 1, tzinfo=timezone.utc)
    when = _when_factory(season_start)
    out = [_head("week")]
    if season_start is not None:
        out += [label or "", f"Times are in {_tzname()}."]
    else:
        out += ["No season has a date yet, so times are shown as offsets from the opening.", f"(Real clock times use {_tzname()}.)"]
    out.append("")
    for p in cal.week(sample):
        out.append(f"{p.emoji} <b>Day {p.day} · {esc(p.name)}</b>: {when(p.starts_at)}")
        notes = _day_notes(p.day, sample, when)
        if notes:
            out.append("   " + "; ".join(notes))
    end = cal.season_end(sample)
    out += ["", f"🌴 <b>Break</b>: from {when(end)} for {_num(AR.break_hours())} h",
            f"🎓 <b>Next season</b>: {when(end + AR.break_delta())}"]
    if season_start is not None:                      # the recap time depends on the local clock: show the real one per day
        recaps = [RL.recap_time(sample, d) for d in range(1, AR.RECAP_LAST_DAY + 1)]
        times = sorted({cal.local(r).strftime("%H:%M") for r in recaps if r is not None})
        out.append(f"🗒 Daily recap (Days 1–{AR.RECAP_LAST_DAY}): about {' / '.join(times)}.")
    else:
        out.append(f"🗒 Daily recap (Days 1–{AR.RECAP_LAST_DAY}): about {_hh(AR.daily_hour())} {_tzname()}.")
    return "\n".join(x for x in out if x is not None)


def page_how() -> str:
    mid, fin = AR.EXAM_KINDS[0], AR.EXAM_KINDS[-1]
    return "\n".join([
        _head("how"), "",
        f"<b>Seasons.</b> One season = {AR.SEASON_DAYS} real days, the same clock for everybody, then a {_num(AR.break_hours())} h break "
        f"and the next season. Seasons sit on a fixed calendar grid, so a restart of the bot never moves or repeats them. "
        f"Times are shown in {_tzname()}.", "",
        f"<b>Energy.</b> {AR.ENERGY_MAX} at most, you start with {AR.ENERGY_START}, and you regain {AR.ENERGY_REGEN_PER_HOUR} an hour. "
        f"{T.HOW_LAZY} Energy is what limits how much you can do in a day.", "",
        f"<b>Stats</b> ({', '.join(_stat(k) for k in AR.STAT_KEYS)}) run from {AR.STAT_MIN} to {AR.STAT_MAX} and reset every season.", "",
        "<b>The grading formula</b>",
        _bullets([
            f"Each exam round gives points (see Exams): your exam <b>score</b> = points ÷ (rounds × {AR.SCORE_REF}) × 100, from 0 to 100.",
            f"Season score = {AR.EXAM_NAMES[mid]} × {_num(AR.EXAM_WEIGHT[mid] * 100)}% + {AR.EXAM_NAMES[fin]} × {_num(AR.EXAM_WEIGHT[fin] * 100)}%.",
            "Grades: " + ", ".join(f"{g} from {_num(lo)}" for g, lo in AR.GRADES if lo) + f", otherwise {AR.GRADES[-1][0]}.",
            f"<b>GPA</b> = season score ÷ 10, on a 10-point scale with two decimals.",
            f"GPA below {_num(AR.PROBATION_GPA)} = probation. {T.HOW_PROBATION}",
        ]), "",
        f"<b>Ranks.</b> Only students who finished at least one exam are ranked. Equal scores share a place.", "",
        f"<b>Graduation.</b> On Day {AR.SEASON_DAYS} the ceremony shows the toppers and awards titles: "
        + ", ".join(f"{AR.TITLES[k][0]} {AR.TITLES[k][1]}" for k in AR.TITLE_ORDER) + ". Past ceremonies live in /halloffame; your own marks in /reportcard.",
    ])


def page_how2() -> str:
    recap = _hh(AR.daily_hour())
    return "\n".join([
        _head("how2"), "",
        "<b>Reminders</b> (by DM, only about the Academy):",
        _bullets([
            f"At most <b>one reminder a day</b> (exam soon, an adventure is open, Energy is full), never between "
            f"{_hh(AR.QUIET_FROM_HOUR)} and {_hh(AR.QUIET_TO_HOUR)} {_tzname()}.",
            f"A short <b>recap</b> on Days 1–{AR.RECAP_LAST_DAY} at about {recap}, and your report card after the ceremony on Day {AR.SEASON_DAYS}.",
            f"Groups where the bot saw activity in the last {AR.ACTIVE_GROUP_DAYS} days get the season announcement, enrolment reminders "
            f"(Days 1–{AR.LATE_JOIN_LAST_DAY}), the recap and the ceremony.",
            f"Enrolment reminders by DM only go to people who used the bot in the last {AR.DM_ACTIVE_DAYS} days. The same message is never sent twice.",
        ]), "",
        "<b>Privacy: exactly what is stored</b> (linked to your Telegram id, which the bot already has):",
        _bullets([
            "your stream, club, stats, Energy and join time; counters per day (how many times you did each action, no message text)",
            "exam attempts with every round (card, choice, dice roll, points), adventure runs and trophies",
            "rival, duel, club-battle, badge, buddy, couple and date records",
            "a <b>pending crush</b> (who you chose): kept private, deleted by /crushoff, and turned into a couple only if it is mutual",
            "Hall of Fame lines (your display name at the ceremony, rank, GPA, titles)",
            "your reminder setting, whether the bot could reach you, and a send-once log (which kind of message went to which chat)",
            f"per group: its id, when the bot last saw activity (written at most once every {_num(AR.SEEN_WRITE_EVERY_S / 3600)} h, never per message) "
            f"and your id if you used /exam, /grades or /classrank there (for the group board)",
        ]), T.PRIVACY_NOT_STORED, T.PRIVACY_RETENTION, "",
        "<b>How to opt out</b>", _bullets(T.OPT_OUT_STEPS),
    ])


def home() -> str:
    lines = [f"{T.PAGES[k][0]} {esc(T.PAGES[k][1])}" for k in PAGE_KEYS]          # button labels are plain text: escape for HTML
    return "\n".join([T.HOME_TITLE, "", T.HOME_INTRO, "", *lines, "", T.HOME_FOOT])


# ------------------------------------------------------------------ public API
def resolve(arg: str | None) -> str | None:
    """'week' / 'Exams' / 'privacy' ... -> a page key (or 'home' for an empty argument); None when unknown."""
    a = (arg or "").strip().lower().lstrip("/")
    if not a or a == T.HOME_KEY:
        return T.HOME_KEY
    if a in T.PAGES:
        return a
    return T.ALIASES.get(a)


def nav(page: str) -> tuple[str | None, str | None]:
    """(back, next) page keys for the Back / Next buttons. Home has none; the first page's Back is None (use Home)."""
    if page not in T.PAGES:
        return None, None
    i = PAGE_KEYS.index(page)
    return (PAGE_KEYS[i - 1] if i > 0 else None), (PAGE_KEYS[i + 1] if i + 1 < len(PAGE_KEYS) else None)


def week_label(season_number: int | None, theme: str | None, upcoming: bool = False) -> str:
    if season_number is None:
        return ""
    base = f"Season {season_number}" + (f" · {esc(theme)}" if theme else "")
    return f"{base} (the next one)" if upcoming else base


def render(page: str, season_start: datetime | None = None, label: str | None = None) -> str:
    """The HTML text of a page. `season_start` / `label` only matter for 'week' (real times of a real season)."""
    if page == T.HOME_KEY:
        return home()
    builders = {"start": page_start, "daily": page_daily, "exams": page_exams, "adv": page_adv, "social": page_social,
                "how": page_how, "how2": page_how2}
    if page == "week":
        text = page_week(season_start, label)
    elif page in builders:
        text = builders[page]()
    else:
        text = home()
    return text if len(text) <= MAX_LEN else text[:MAX_LEN - 1].rsplit("\n", 1)[0]
