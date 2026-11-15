"""/academyhelp (Part 7): labels and the fixed prose. Every NUMBER, time, name and rule in the help pages is generated from
game/academy_rules.py (and the adventure data) in services/academy_help.py, so nothing here may contain a game number.
HTML-safe: a literal & is written as &amp;."""
from __future__ import annotations

# page key -> (emoji, button label, HTML title). Order = the order of the Back / Next buttons.
PAGES: dict[str, tuple[str, str, str]] = {
    "start": ("🚀", "Getting Started", "Getting Started"),
    "daily": ("📚", "Daily Life", "Daily Life"),
    "exams": ("🧠", "Exams & Grades", "Exams &amp; Grades"),
    "adv": ("🗺", "Adventures", "Adventures"),
    "social": ("🤝", "Social", "Social"),
    "week": ("📅", "The Week", "The Week"),
    "how": ("⚙️", "How It Works", "How It Works (1/2)"),
    "how2": ("🔒", "Privacy & Reminders", "How It Works (2/2): reminders and privacy"),
}
HOME_KEY = "home"
ALIASES: dict[str, str] = {
    "begin": "start", "getting": "start", "gettingstarted": "start", "enroll": "start", "new": "start",
    "life": "daily", "actions": "daily", "energy": "daily",
    "exam": "exams", "grades": "exams", "grading": "exams", "gpa": "exams",
    "adventure": "adv", "adventures": "adv", "story": "adv",
    "friends": "social", "rival": "social", "crush": "social", "clubs": "social",
    "timeline": "week", "schedule": "week", "times": "week",
    "works": "how", "seasons": "how",
    "privacy": "how2", "data": "how2", "reminders": "how2", "optout": "how2", "opt-out": "how2",
}

HOME_TITLE = "🎓 <b>Academy help</b>"
HOME_INTRO = "Pick a page. Everything here is generated from the game's real rules, so the numbers are always current."
HOME_FOOT = "Tip: <code>/academyhelp week</code> jumps straight to a page. Start with 🚀 Getting Started."
CLOSED_NOTE = "⏸ The Academy is closed right now (switched off by the owner). Your data is kept."
NOT_YOURS = "🚫 This help menu belongs to someone else. Send /academyhelp for your own."
ERROR = "⚠️ Something went wrong. Please try /academyhelp again."

BTN_HOME = "🏠 Home"
BTN_BACK = "⬅️ Back"
BTN_NEXT = "Next ➡️"

# ---- fixed prose pieces (no numbers)
START_WHAT = "A school-life game inside the chat: enrol, pick a stream and a club, build your stats, sit exams, go on adventures and graduate."
START_FREE = "It is all optional and free of real money. The exams never ask school questions: they are strategy rounds about your stats and a little luck."
START_STEPS = (
    "<code>/enroll</code> to join: pick a stream, then a club.",
    "<code>/academy</code> shows your dashboard (stats, Energy, what is on today).",
    "<code>/schedule</code> shows the whole week with a countdown.",
    "Use daily actions like <code>/study</code> and <code>/hangout</code> to grow, and keep an eye on your Energy.",
    "<code>/academyhelp</code> any time to come back here.",
)
EXAM_NO_KNOWLEDGE = ("<b>You do not need to know any subject.</b> There are no maths, science or general-knowledge questions. "
                     "A round is a short school-life scenario with three choices; what matters is your stats, your preparation and how much risk you take.")
EXAM_PREP_TIPS = (
    "Raise the stats the choices use (the button shows which).",
    "Study a little every day: the streak adds skill.",
    "Attend class on the class days (/attend).",
    "Pick Safe when you are under-prepared and Risky when you are strong.",
)
GRADING_NOTE = "A missed exam counts as 0 once its window has closed. Until both windows close your result is marked provisional."
ADV_WHAT = "Short story adventures: you read a scene, choose what to do, and some choices are stat checks decided by one dice roll."
ADV_PRESS = "Every button shows your chance of success before you press it."
ADV_COSMETIC = "Trophies and items are for show only: they never change a number."
SOCIAL_WHOLESOME = "Everything social is friendly and opt-in."
CRUSH_PRIVACY = ("A crush is <b>private</b>: nobody is told unless you both pick each other in the same season. "
                 "Use /crush in a private chat with the bot if you want to keep it fully quiet.")
HOW_LAZY = "Energy is worked out when you look at it, so nothing runs in the background for it."
HOW_PROBATION = "Probation only removes the Focus power-up and ends with the season."
PRIVACY_NOT_STORED = "<b>Not stored by the Academy:</b> the text of your messages, photos, location or phone number."
PRIVACY_RETENTION = ("Season records are kept so report cards and the Hall of Fame keep working. There is no self-service delete in "
                     "this version: ask the bot's owner if you want your Academy data removed.")
OPT_OUT_STEPS = (
    "<code>/academyremind off</code>: no unsolicited Academy DMs at all (reminders, recaps, the report card DM). Group announcements still appear for the group.",
    "Do not <code>/enroll</code>: you will not be a student, have no stats and appear in no ranking.",
    "<code>/crushoff</code> removes your pending crush. <code>/buddy off</code> ends a buddy pair or cancels your request.",
)
