"""Academy Life - every constant of the feature in one place (pure Python, no imports from the bot).

One season = 7 real days shared by all players, then a short break, then the next season.
Env switches (read at call time, so tests can change them):
  ACADEMY_ENABLED      kill switch, default on  (0/false/off/no = commands say 'closed', the loop does nothing)
  ACADEMY_TZ           time zone used to DISPLAY times, default Asia/Kolkata
  ACADEMY_BREAK_HOURS  break between two seasons, default 6
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from functools import lru_cache

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ env
DEFAULT_TZ = "Asia/Kolkata"
DEFAULT_BREAK_HOURS = 6.0
_OFF = ("0", "false", "off", "no", "")


def enabled() -> bool:
    return os.getenv("ACADEMY_ENABLED", "1").strip().lower() not in _OFF


def break_hours() -> float:
    try:
        v = float(os.getenv("ACADEMY_BREAK_HOURS", str(DEFAULT_BREAK_HOURS)))
    except ValueError:
        return DEFAULT_BREAK_HOURS
    return min(max(v, 0.0), 168.0)


def break_delta() -> timedelta:
    return timedelta(hours=break_hours())


@lru_cache(maxsize=8)
def _tz(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:                         # no tzdata on this host: keep working
        if name == DEFAULT_TZ:
            return timezone(timedelta(hours=5, minutes=30), "IST")
        log.warning("ACADEMY_TZ %r is unknown here - using UTC", name)
        return timezone.utc


def tz() -> tzinfo:
    return _tz(os.getenv("ACADEMY_TZ", DEFAULT_TZ).strip() or DEFAULT_TZ)


# ------------------------------------------------------------------ calendar
SEASON_DAYS = 7
DAY = timedelta(days=1)
LATE_JOIN_LAST_DAY = 4          # enrolment closes at the END of Day 4; Day 1 = on time, Days 2-4 = late joiner
TICK_SECONDS = 60


@dataclass(frozen=True)
class Phase:
    day: int
    key: str
    name: str
    emoji: str


PHASES: tuple[Phase, ...] = (
    Phase(1, "admission", "Admission", "📝"),
    Phase(2, "classes", "Classes Begin", "📚"),
    Phase(3, "campus", "Club & Campus Day", "🎪"),
    Phase(4, "midterms", "Midterms", "🧠"),
    Phase(5, "fest", "Fest Day", "🎉"),
    Phase(6, "finals", "Finals", "🏁"),
    Phase(7, "results", "Results & Graduation", "🎓"),
)
PHASE_BY_DAY: dict[int, Phase] = {p.day: p for p in PHASES}
BREAK_KEY = "break"
BREAK_PHASE = Phase(0, BREAK_KEY, "Break", "🌴")
UPCOMING_KEY = "upcoming"
UPCOMING_PHASE = Phase(0, UPCOMING_KEY, "Opening soon", "⏳")

THEMES: tuple[str, ...] = (
    "Autumn Intake", "Winter Warmup", "Spring Bloom", "Summer Sprint", "Monsoon Majors", "Golden Jubilee",
    "Starlight Semester", "Harvest Term",
)

# ------------------------------------------------------------------ streams & clubs
STAT_KEYS: tuple[str, ...] = ("knowledge", "charisma", "fitness", "creativity")     # Energy is separate
STAT_LABELS: dict[str, str] = {"knowledge": "🧠 Knowledge", "charisma": "💬 Charisma",
                               "fitness": "💪 Fitness", "creativity": "🎨 Creativity"}
START_STATS: dict[str, int] = {"knowledge": 10, "charisma": 10, "fitness": 10, "creativity": 10}
STAT_MIN, STAT_MAX = 0, 100


@dataclass(frozen=True)
class Stream:
    key: str
    name: str
    emoji: str
    blurb: str
    bonus: dict[str, int]


STREAMS: dict[str, Stream] = {s.key: s for s in (
    Stream("science", "Science", "🔬", "Labs, gadgets and big ideas.", {"knowledge": 5, "creativity": 2}),
    Stream("arts", "Arts", "🎨", "Stories, paint and imagination.", {"creativity": 5, "charisma": 2}),
    Stream("commerce", "Commerce", "💼", "Deals, plans and people skills.", {"charisma": 4, "knowledge": 3}),
    Stream("sports", "Sports", "🏅", "Training, teamwork and stamina.", {"fitness": 5, "charisma": 2}),
    Stream("music", "Music", "🎵", "Rhythm, stage and style.", {"creativity": 4, "charisma": 3}),
)}


@dataclass(frozen=True)
class Club:
    key: str
    name: str
    emoji: str
    blurb: str
    focus: str                  # the stat this club is about (later parts use it in the club battle)


CLUBS: dict[str, Club] = {c.key: c for c in (
    Club("sports", "Sports Club", "⚽", "Friendly matches and morning runs.", "fitness"),
    Club("music", "Music Club", "🎸", "Jam sessions and the campus band.", "creativity"),
    Club("drama", "Drama Club", "🎭", "Plays, costumes and spotlight.", "charisma"),
    Club("coding", "Coding Club", "💻", "Hack nights and clever puzzles.", "knowledge"),
)}

# ------------------------------------------------------------------ energy
ENERGY_MAX = 100
ENERGY_START = 100
ENERGY_REGEN_PER_HOUR = 10      # full bar in 10 hours; Energy limits how many actions a player can do per day
ENERGY_BAR_CELLS = 10


def starting_stats(stream: str) -> dict[str, int]:
    """Base stats + the stream's bonus (each stat capped to STAT_MAX)."""
    out = dict(START_STATS)
    for k, v in STREAMS[stream].bonus.items():
        out[k] = min(STAT_MAX, out[k] + v)
    return out


# ================================================================== Part 2: daily life
# Six daily actions. Every number lives here so balance changes never touch the logic.
# "day" below is the SEASON day 1..7 (the daily counters, limits and the diamond cap all reset when the day changes).
@dataclass(frozen=True)
class Action:
    key: str
    name: str
    emoji: str
    energy: int                  # Energy cost (0 = free)
    cooldown_s: int              # per player and action (services.cooldowns, key "academy:<key>")
    daily_limit: int             # max uses per season day
    days: tuple[int, ...]        # season days the action is open on


ALL_DAYS: tuple[int, ...] = tuple(range(1, SEASON_DAYS + 1))
CLASS_DAYS: tuple[int, ...] = (2, 3, 4)          # /attend: Classes Begin, Club & Campus Day, Midterms
FINALS_DAY = 6                                   # /parttime is closed on Finals day

ACTIONS: dict[str, Action] = {a.key: a for a in (
    Action("study", "Study", "📚", 15, 10 * 60, 6, ALL_DAYS),
    Action("attend", "Attend class", "🏫", 10, 30 * 60, 1, CLASS_DAYS),
    Action("hangout", "Hang out", "🎈", 10, 15 * 60, 4, ALL_DAYS),
    Action("rest", "Rest", "😴", 0, 90 * 60, 3, ALL_DAYS),
    Action("club", "Club practice", "🎯", 15, 20 * 60, 4, ALL_DAYS),
    Action("parttime", "Part-time job", "💼", 20, 30 * 60, 3, tuple(d for d in ALL_DAYS if d != FINALS_DAY)),
)}


def cooldown_key(action: str) -> str:
    return f"academy:{action}"


# ---- effects (inclusive random ranges unless stated)
STUDY_GAIN = (1, 3)                  # Knowledge per /study
ATTEND_GAIN = 1                      # Knowledge per /attend (flat)
HANGOUT_CHARISMA = (1, 2)            # Charisma per /hangout
HANGOUT_CREATIVITY_CHANCE = 0.30     # chance of +1 Creativity on top
CLUB_POINTS = (3, 5)                 # points added to the player's club per /club
CLUB_STAT_GAIN = (1, 2)              # the stat below, per /club
CLUB_STAT: dict[str, str] = {"sports": "fitness", "music": "creativity", "drama": "creativity", "coding": "creativity"}
REST_ENERGY = 20                     # Energy restored by /rest (before stream perks)
STREAK_MILESTONES: dict[int, int] = {3: 1, 5: 2, 7: 3}     # study streak (days in a row) -> bonus Knowledge, once per day

# ---- diamonds: ONLY /parttime pays. The cap below is the most a player can earn from Academy actions per season day.
PARTTIME_PAY = (20, 40)              # diamonds per shift (before the commerce perk)
DAILY_DIAMOND_CAP = 100              # max diamonds per player per season day (3 shifts at the cap; a partial shift pays the rest)

# ---- GIF moments (all optional: utils.gifs falls back to plain text)
LOW_ENERGY_GIF = 20                  # after an action that leaves you at or below this: facepalm
HANGOUT_GIF_CHANCE = 0.5             # set to 1.0 for a laugh GIF on every hangout
GIF_MOMENTS: dict[str, str] = {"streak": "highfive", "first_attend": "wave", "hangout": "laugh", "low_energy": "facepalm"}


@dataclass(frozen=True)
class Perk:
    """What a stream adds to daily life. stats: action -> {stat: extra points}."""
    stats: dict[str, dict[str, int]]
    pay_pct: int = 0                 # +% on /parttime pay (still limited by DAILY_DIAMOND_CAP)
    rest_bonus: int = 0              # extra Energy from /rest


STREAM_PERKS: dict[str, Perk] = {
    "science": Perk({"study": {"knowledge": 1}, "attend": {"knowledge": 1}}),
    "arts": Perk({"hangout": {"creativity": 1}, "attend": {"creativity": 1}}),
    "commerce": Perk({"hangout": {"charisma": 1}}, pay_pct=20),
    "sports": Perk({"club": {"fitness": 1}}, rest_bonus=5),
    "music": Perk({"club": {"creativity": 1}, "hangout": {"charisma": 1}}),
}


def perk_actions(stream: str) -> tuple[str, ...]:
    """The actions a stream has a perk on (used to look up the flavour note in utils/content/academy_texts)."""
    p = STREAM_PERKS.get(stream)
    if p is None:
        return ()
    out = list(p.stats)
    if p.pay_pct:
        out.append("parttime")
    if p.rest_bonus:
        out.append("rest")
    return tuple(out)


# ================================================================== Part 3: exams (strategy rounds), grades, ranks
# No real-subject questions anywhere: every round is a stat + risk + luck decision. All numbers live here.
EXAM_KINDS: tuple[str, ...] = ("midterm", "finals")
EXAM_DAY: dict[str, int] = {"midterm": 4, "finals": 6}          # season day the exam window is open (whole day)
EXAM_NAMES: dict[str, str] = {"midterm": "Midterm", "finals": "Finals"}
EXAM_EMOJI: dict[str, str] = {"midterm": "🧠", "finals": "🏁"}
EXAM_ROUNDS: dict[str, int] = {"midterm": 5, "finals": 8}
EXAM_WEIGHT: dict[str, float] = {"midterm": 0.5, "finals": 0.5}  # 50 / 50
# how many of the cards come from the player's own stream (the rest are general), and which difficulties are drawn
EXAM_STREAM_CARDS: dict[str, int] = {"midterm": 2, "finals": 3}
EXAM_DIFFICULTIES: dict[str, tuple[int, ...]] = {"midterm": (1, 2), "finals": (2, 3)}

ROUND_SECONDS = 25                # time to answer one round, enforced by timestamps (no background timers)
ROUND_GRACE_SECONDS = 2           # network slack added to the check only (the player is told 25 s)

# ---- chance of success (a probability in 0.02..0.98), then ONE dice roll 1..100 that can move it by at most +-LUCK_CAP
STAT_TARGET: dict[int, int] = {1: 30, 2: 45, 3: 62}             # skill needed for an even chance, by card difficulty
CHANCE_BASE = 0.50
SKILL_SCALE = 100.0               # 10 skill points = 0.10 chance
RISK_CHANCE: dict[str, float] = {"safe": 0.12, "balanced": 0.0, "risky": -0.25}
CHANCE_MIN, CHANCE_MAX = 0.02, 0.98
LUCK_CAP = 0.15                   # the dice never move the chance by more than +-15 points
SUCCESS_LINE = 0.5                # success when chance + luck >= this line

STREAM_CARD_BONUS = 4             # skill, on a card of the player's own stream
STREAM_STAT_BONUS = 3             # skill, when the button's stat is one the stream is good at (Stream.bonus)
STREAK_BONUS_PER_DAY = 1.2        # skill per study-streak day ...
STREAK_BONUS_MAX = 6.0            # ... up to this
ATTENDANCE_BONUS_MAX = 8.0        # skill at 100% attendance of the class days you could attend

# ---- round points (per round), then the exam score = sum / (rounds * SCORE_REF) * 100, clamped to 0..100
ROUND_POINTS: dict[str, tuple[int, int]] = {"safe": (10, 3), "balanced": (14, 0), "risky": (22, -8)}   # (success, failure)
TIMEOUT_POINTS = 0                # the 25 seconds ran out: no points
SCORE_REF = 14

# ---- grades and GPA (10-point scale; GPA = score / 10)
GRADES: tuple[tuple[str, float], ...] = (("A+", 90.0), ("A", 80.0), ("B+", 70.0), ("B", 60.0), ("C", 50.0), ("D", 40.0), ("F", 0.0))
GRADE_ORDER: tuple[str, ...] = tuple(g for g, _ in GRADES)
PASS_GRADES_GIF: tuple[str, ...] = ("A+", "A")
FAIL_GRADES: tuple[str, ...] = ("F",)
PROBATION_GPA = 4.0               # GPA under this = probation (until the next season)

# ---- diamonds per exam grade (paid once per exam, idempotent ref). Part 2's jobs can earn up to 600/season.
EXAM_REWARD: dict[str, int] = {"A+": 120, "A": 90, "B+": 70, "B": 50, "C": 30, "D": 15, "F": 0}

# ---- Focus: re-roll ONE failed round; costs Energy, limited uses per exam, removed on probation
FOCUS_MAX_USES = 3
FOCUS_ENERGY = 15
EXAM_GIFS: dict[str, str] = {"top": "dance", "fail": "facepalm"}

RANK_TOP = 10


# ================================================================== Part 4: social (rivals, crushes, club battles, buddies)
# Wholesome and opt-in. No real-subject questions: every duel / battle round is a Part 3 scenario card played with play_round.
# All numbers live here. Days are SEASON days 1..7.

# ---- rivals
RIVAL_PAIR_FROM_DAY = 3           # the pairing tick runs once per season, on Day 3 (or the first tick after it if the bot was off)
RIVAL_PAIR_LAST_DAY = 5           # ... and never later than this day (after it, late students get a bot rival on demand)
RIVAL_DEFAULT_GPA = 5.0           # GPA used for a student with no earlier graduation (neutral middle of the 10-point scale)
RIVAL_GPA_WEIGHT = 10.0           # pairing distance = |GPA diff| * this + |Knowledge diff|  (GPA 0..10 and Knowledge 0..100 on one scale)
RIVAL_DUEL_ROUNDS = 3             # both players play the same 3 cards
RIVAL_DUEL_LAST_DAY = 6           # a duel can be played until the end of this day
RIVAL_DUEL_ENERGY = 10            # Energy to start your side of a duel
RIVAL_WIN_DIAMONDS = 60           # winner (a bot rival pays the same: the odd player out is not punished)
RIVAL_TIE_DIAMONDS = 20           # each side on a draw (no badge point)
RIVAL_WALKOVER_PCT = 50           # rival never played: the player who did gets this % of RIVAL_WIN_DIAMONDS, no badge point
RIVAL_BADGE_POINTS = 1            # Rival badge points for a win
RIVAL_BOT_STAT_SPREAD = 3         # a bot rival's stats = the player's stats +- this much
RIVAL_NOTIFY_PER_RUN = 40         # at most this many "you have a rival" DMs per tick run (a big season is spread over a few runs)

# ---- crushes / campus couples
CRUSH_FROM_DAY = 3                # crushes and dates are open from Day 3 to the end of Day 7
DATE_ENERGY = 20                  # Energy for /campusdate (paid by the player who starts it)
DATE_DIAMONDS = 15                # small diamond cost (paid by the same player, through the ledger)
DATE_POINTS = 10                  # couple points per date
DATE_STAT_GAIN = (1, 2)           # random gain of each partner: Charisma +1..2 and one more stat +1
DATE_GIF_CHANCE = 0.5

# ---- club battle
BATTLE_FROM_DAY = 3               # the tick opens the battle on Day 3 ...
BATTLE_LAST_DAY = 5               # ... it is open until the END of this day, then the tick finalises it
BATTLE_ENERGY = 10                # Energy per battle round (one round per member)
BATTLE_FOCUS_BONUS = 5            # skill bonus on a button whose stat is the club's focus stat (Club.focus)
BATTLE_DIFFICULTY = 2             # difficulty of the battle card (general cards only)
CLUB_WIN_DIAMONDS = 80            # each contributing member of the winning club (once per season)
CLUB_WIN_BADGE_POINTS = 1

# ---- study buddies
BUDDY_KNOWLEDGE_BONUS = 1         # +Knowledge on your first /study of each day while you have a buddy (max once a day)
BUDDY_PENDING_MAX = 1             # open requests a player may have at the same time

# ---- badges (table academy_badges, one row per season + player + kind)
BADGE_KINDS: tuple[str, ...] = ("rival", "club")
SOCIAL_GIFS: dict[str, str] = {"mutual": "blush", "date": "dance", "rival": "highfive", "club": "dance"}   # all SFW


def crush_open(day: int) -> bool:
    return CRUSH_FROM_DAY <= day <= SEASON_DAYS


# ================================================================== Part 5: adventures
# A data-driven story engine. The stories live in utils/content/academy_adventures.py; every NUMBER lives here.
# Wholesome and light: no real-subject questions, only stat + luck checks. Days are SEASON days 1..7.
ADV_MAX_PER_DAY = 2                 # a player can FINISH at most this many adventures per season day
ADV_EXPIRE_MINUTES = 30             # a run with no button press for this long is closed ("expired"), no reward, no refund
ADV_ENERGY_MIN, ADV_ENERGY_MAX = 10, 30      # every adventure's Energy cost must be inside this range (checked by tests)
ADV_NIGHT_FROM_HOUR, ADV_NIGHT_TO_HOUR = 20, 6   # "at night" = local ACADEMY_TZ hour >= 20 or < 6
ADV_FIRST_DAY = 2                   # Day 1 (Admission) is for enrolling: adventures open from Day 2
ADV_FEST_DAY = 5                    # Fest Day: the bigger bonus below applies to EVERY adventure played on it

# ---- stat checks: chance = ADV_CHANCE_BASE + (skill - target) / 100, clamped; ONE dice roll 1..100, success when roll <= chance
ADV_TARGET: dict[int, int] = {1: 10, 2: 25, 3: 40, 4: 55}      # skill needed for the base chance, by difficulty 1..4
ADV_CHANCE_BASE = 0.55
ADV_CHANCE_MIN, ADV_CHANCE_MAX = 0.05, 0.95
ADV_LUCK_CHANCE: dict[int, float] = {1: 0.70, 2: 0.55, 3: 0.40, 4: 0.25}   # a "luck" check ignores stats (flags still add/subtract)
ADV_CHECK_STATS: tuple[str, ...] = STAT_KEYS + ("luck",)
ADV_FLAG_BONUS_LIMIT = 10           # one flag adds or removes at most this much skill (checked by tests)
ADV_STREAM_BONUS_LIMIT = 6          # a stream bonus on an adventure is at most this much skill (checked by tests)

# ---- reward caps (per ENDING, before the Fest Day bonus). Tests check every ending of every adventure against these.
ADV_MAX_DIAMONDS = 30
ADV_MAX_STAT_GAIN = 3               # per stat
ADV_MAX_STAT_TOTAL = 5              # over all stats
ADV_FEST_DIAMOND_PCT = 50           # Fest Day: +50% diamonds (rounded up) ...
ADV_FEST_STAT_BONUS = 1             # ... and +1 on every stat the ending already gives


def adv_fest_diamonds(base: int) -> int:
    """Diamonds of an ending on Fest Day (0 stays 0)."""
    return 0 if base <= 0 else base + -(-base * ADV_FEST_DIAMOND_PCT // 100)      # ceil, integers only


ADV_FEST_MAX_DIAMONDS = adv_fest_diamonds(ADV_MAX_DIAMONDS)          # 45
ADV_FEST_MAX_STAT_GAIN = ADV_MAX_STAT_GAIN + ADV_FEST_STAT_BONUS     # 4

# ---- GIFs: only SFW reaction actions (all exist in utils/gifs.GIF_ACTIONS)
ADV_GIFS_ALLOWED: tuple[str, ...] = ("wave", "highfive", "dance", "blush", "cry", "facepalm", "laugh", "wink")
ADV_TROPHY_KINDS: tuple[str, ...] = ("title", "item")


def adv_night(local_hour: int) -> bool:
    return local_hour >= ADV_NIGHT_FROM_HOUR or local_hour < ADV_NIGHT_TO_HOUR


# ================================================================== Part 6: reminders, daily recaps, graduation
# Env: ACADEMY_DAILY_HOUR = local hour (ACADEMY_TZ) of the end-of-day recap, default 22.
DEFAULT_DAILY_HOUR = 22


def daily_hour() -> int:
    try:
        v = int(float(os.getenv("ACADEMY_DAILY_HOUR", str(DEFAULT_DAILY_HOUR))))
    except ValueError:
        return DEFAULT_DAILY_HOUR
    return min(max(v, 0), 23)


# ---- sending: one global limiter, small chunks, a time budget per tick (the loop kills a tick after 50 s)
NOTIFY_RATE_PER_SEC = 15           # global: never more than this many messages in any rolling second
NOTIFY_CHUNK = 20                  # after this many sends the sender sleeps a little (lets the event loop breathe)
NOTIFY_CHUNK_SLEEP = 0.25          # seconds
NOTIFY_TICK_BUDGET_S = 35.0        # stop STARTING new sends after this long; the rest goes out on the next tick
NOTIFY_MAX_PER_TICK = 400          # hard cap of messages per tick run
GROUP_FAILS_SKIP = 3               # a group that refused this many messages in a row is skipped until someone writes there again
UNREACHABLE_RETRY_DAYS = 7         # a user marked unreachable is tried again after this long, but only if we saw them since

# ---- who counts as "active"
ACTIVE_GROUP_DAYS = 7              # a group is active if the bot saw a message there in the last 7 days
SEEN_WRITE_EVERY_S = 6 * 3600      # the group activity row is written at most this often per group (never per message)
DM_ACTIVE_DAYS = 14                # DM reminders only go to users seen in the last 14 days

# ---- reminders (Days 1-6; Day 7 belongs to the ceremony)
QUIET_FROM_HOUR, QUIET_TO_HOUR = 23, 7      # local ACADEMY_TZ: no reminder DMs from 23:00 to 07:00
ENROLL_GROUP_EVERY_H = 6                    # group enrolment reminder: one per 6-hour window of Days 1-4
ENROLL_LAST_CALL_H = 2                      # "last call": the final N hours before admissions close
MIDTERM_SOON_MIN = 60                       # "midterm in 1 hour"
FINALS_SOON_H = 18                          # "finals tomorrow": from 18 h before the finals open
ENERGY_FULL_MAX_AGE_H = 24                  # "Energy full" only if it refilled within the last 24 h (never nag about an old bar)
REMINDER_LAST_DAY = 6
DM_REMINDER_SLOT = "dm_daily"               # notify-log kind that enforces ONE reminder DM per user per local day
REMINDER_KINDS: tuple[str, ...] = ("midterm", "finals", "adventure", "energy_full")      # priority order

# ---- recap / ceremony
RECAP_LAST_DAY = 6                          # Days 1-6 get a recap; Day 7 gets the ceremony instead
RECAP_MIN_AGE_H = 4                         # if the daily hour comes sooner than this after the day began, the recap is sent 1 h before the day ends instead
CEREMONY_DELAY_MIN = 10                     # the ceremony starts this long after Day 7 begins (the exam tick has closed every finals by then)
CEREMONY_GRACE_H = 30                       # ... and can still be sent until this long after the season ends (bot was off)
CEREMONY_TOP = 5
HOF_SEASONS_SHOWN = 5
TOPPER_MIN_SCORE = 60.0                     # a stream topper needs at least a B (score 60)
NAMES_PER_TITLE = 5
TITLES: dict[str, tuple[str, str]] = {      # key -> (emoji, name)
    "topper": ("⭐", "Topper"),
    "valedictorian": ("🎓", "Valedictorian"),
    "backbencher": ("🪑", "Backbencher"),
    "perfect_attendance": ("📅", "Perfect Attendance"),
    "club_champion": ("🏆", "Club Champion"),
}
TITLE_ORDER: tuple[str, ...] = ("valedictorian", "topper", "club_champion", "perfect_attendance", "backbencher")
TITLE_BLURB: dict[str, str] = {
    "valedictorian": "best final GPA of the whole school",
    "topper": "best GPA of their stream (needs a B or better)",
    "club_champion": "played for the club that won the season",
    "perfect_attendance": "attended every class day they could",
    "backbencher": "never sat in class, still passed",
}
CARD_GIF = "dance"                          # SFW reaction GIF on a graduate's report card (utils.gifs falls back to text)


# ---- Part 6 extras: graduation diamonds (OFF by default: set ACADEMY_GRADUATION_DIAMONDS=1 to switch on)
def graduation_diamonds_on() -> bool:
    return os.getenv("ACADEMY_GRADUATION_DIAMONDS", "0").strip().lower() in ("1", "true", "yes", "on")


GRADUATION_DIAMONDS: dict[str, int] = {"valedictorian": 50, "topper": 25, "perfect_attendance": 10}   # per title, paid once (unique ref)
