"""Academy Part 6 texts: reminders, recaps, command replies. Wholesome, short, HTML. Dynamic values are escaped by the callers
(services/academy_remind_views.py, services/academy_ceremony.py)."""
from __future__ import annotations

OPT_OUT_NOTE = "<i>Too many pings? /academyremind off</i>"
ENROLL_BTN = "📝 Enroll now"

# ---- season open (groups)
SEASON_OPEN = ("🎓 <b>Season {number} is open!</b>{theme}\n\n"
               "Doors are open: pick a <b>stream</b>, join a <b>club</b> and start your week. "
               "Admissions stay open until <b>{closes}</b> (late joiners get a lighter workload).\n\n"
               "Tap the button and I will send you the enrolment in a private message. "
               "No private chat yet? Open one with me, press Start, then tap again.")
THEME_LINE = "\n🎭 Theme: <i>{theme}</i>"

# ---- enrolment reminders (groups): one per 6 h window, chosen by window number
ENROLL_GROUP: tuple[str, ...] = (
    "📣 <b>Academy Season {number}</b> is running and the doors are still open. Join in with a stream and a club!",
    "🎒 Haven't enrolled yet? Season {number} has room for you. It takes 20 seconds.",
    "🏫 The campus is getting busy: classes, clubs and a Fest Day are coming. Your seat is waiting!",
    "📚 New here? Pick a stream, pick a club, and you are in for Season {number}.",
)
ENROLL_GROUP_TAIL = "\nAdmissions close <b>{closes}</b>."
LAST_CALL_GROUP = ("⏰ <b>Last call!</b> Admissions for Season {number} close at <b>{closes}</b> "
                   "(in about {left}). Enrol now, or wait for the next season.")

# ---- enrolment reminders (DM, at most one a day)
ENROLL_DM: dict[int, str] = {
    1: "👋 Season {number} of the Academy just started. Pick a stream and a club and join the class!",
    2: "🎒 The Academy is in full swing and you can still join. Late joiners get a lighter workload.",
    3: "🎪 Club &amp; Campus Day is on. Enrol now and your club can use you today!",
    4: "⏰ <b>Last call:</b> admissions close at <b>{closes}</b>. Enrol now or catch the next season.",
}

# ---- reminders for enrolled players (DM, max one a day)
MIDTERM_SOON = "🧠 <b>Midterm in about {mins} minutes.</b> Five quick strategy rounds, open all day with /exam. Rest up and bring your best."
MIDTERM_OPEN = "🧠 <b>The Midterm is open</b> and closes in about {left}. Five quick strategy rounds: /exam"
FINALS_SOON = "🏁 <b>Finals are coming:</b> they open {when} (in about {left}). Top up your Energy and keep your streak going. /exam"
FINALS_OPEN = "🏁 <b>The Finals are open</b> and close in about {left}. Eight strategy rounds: /exam"
ADVENTURE_VARIANTS: tuple[str, ...] = (
    "🗺 <b>An adventure is waiting.</b> You have the Energy and {n} of {max} runs left today. Try /adventure",
    "🧭 The campus has a story with your name on it. {n} of {max} adventure runs left today: /adventure",
    "🎒 Feeling brave? You have {n} of {max} adventure runs left today and the Energy for one. /adventure",
)
ADVENTURE = ADVENTURE_VARIANTS[0]
ADVENTURE_FEST = "🎉 <b>Fest Day!</b> Every adventure pays a bonus today and you still have runs left. /adventure"
ENERGY_FULL_VARIANTS: tuple[str, ...] = (
    "⚡ <b>Your Energy is full.</b> Time to /study, /club or head out on an /adventure.",
    "🔋 Fully recharged! Your Energy is at the top, so don't let it go to waste: /study, /club or /adventure.",
    "⚡ All charged up and nowhere to go? Your Energy is full. Try /hangout, /study or /adventure.",
)
ENERGY_FULL = ENERGY_FULL_VARIANTS[0]

# ---- end-of-day recap
RECAP_GROUP_HEAD = "🌙 <b>Academy · end of Day {day}</b> — {emoji} {name}"
RECAP_TOP = "🥇 <b>Top student so far:</b> {name} ({how})"
RECAP_NO_TOP = "🥇 <b>Top student so far:</b> nobody yet. Be the first to /study!"
RECAP_CLUBS = "🏟 <b>Clubs:</b>"
RECAP_TEASER = "👀 <b>Tomorrow:</b> {emoji} Day {day} — {name}. <i>{blurb}</i>"
RECAP_DM_HEAD = "🌙 <b>Your Day {day} recap</b>"
RECAP_DM_ENERGY = "⚡ Energy used today: <b>{used}</b> · now {bar} <b>{now}/{max}</b>"
RECAP_DM_STATS = "📈 {stats}"
RECAP_DM_RANK = "🏅 Class rank: <b>#{rank}</b> of {total} (GPA {gpa:.2f}{prov})"
RECAP_DM_NO_RANK = "🏅 Class rank: appears after your first exam."
RECAP_DM_NEXT = "➡️ <b>Next:</b> {text}"

# ---- /academyremind
REMIND_ON = ("🔔 Reminders are <b>on</b>: enrolment nudges, exam reminders, a daily recap and your report card "
             "(at most one reminder a day, never between {q_from}:00 and {q_to}:00).")
REMIND_OFF = ("🔕 Reminders are <b>off</b>. I will not send you Academy DMs any more (groups still see the season messages). "
              "You can always check /reportcard. Turn them back on with /academyremind on.")
REMIND_USAGE = "Use <code>/academyremind on</code> or <code>/academyremind off</code>."
REMIND_STATUS = "Right now: {state}"
REMIND_DM_HINT = "\n\n💡 I can only DM you after you have pressed Start in a private chat with me."

# ---- /reportcard and /halloffame
NO_REPORT = "📜 You have no Academy report yet. Join the next season with /enroll."
NO_HOF = "🏛 The Hall of Fame is empty. The first graduation ceremony will fill it."
HOF_HEAD = "🏛 <b>Academy Hall of Fame</b>"
CARD_PROVISIONAL = "<i>Season in progress: this is your report so far. The final one arrives on Day 7.</i>"
CARD_NO_EXAMS = "📊 No exam results yet."
CARD_NOT_ENROLLED_FINAL = "You were enrolled but did not sit an exam this season. A fresh start awaits in the next one."

# ---- graduation
CEREMONY_HEAD = "🎓 <b>Season {number} · Results &amp; Graduation Ceremony</b>"
CEREMONY_NO_GRADS = "Nobody sat an exam this season, so there is no valedictorian. The doors reopen soon!"
CEREMONY_VALE = "🏆 <b>Valedictorian:</b> {names}"
CEREMONY_TOP = "🏅 <b>Top {n}</b>"
CEREMONY_CLUB = "🏟 <b>Club winner:</b> {club} ({points} pts)"
CEREMONY_NO_CLUB = "🏟 <b>Club winner:</b> no club scored this season."
CEREMONY_TITLES = "🎖 <b>Titles</b>"
CEREMONY_HOF = "🏛 <b>Hall of Fame:</b> new entries added. See /halloffame"
CEREMONY_HOF_PREV = "   Earlier valedictorians: {names}"
CEREMONY_FOOT_VARIANTS: tuple[str, ...] = (
    "👏 Congratulations, graduates! Check your private message for your report card, or send /reportcard.",
    "🎉 What a season! Well done, everyone. Your report card is in your private messages, or send /reportcard.",
    "🌟 Proud of every one of you. Grab your report card with /reportcard, and rest well before the next season.",
)
CEREMONY_FOOT = CEREMONY_FOOT_VARIANTS[0]
CEREMONY_HIGHLIGHTS = "✨ <b>Season highlights:</b> {bits}"
CARD_DIAMONDS = "💎 Graduation prize: <b>+{n}</b> diamonds"
CEREMONY_NEXT = "Next season opens <b>{when}</b>."
CARD_HEAD = "📜 <b>Report card · Season {number}</b>"
