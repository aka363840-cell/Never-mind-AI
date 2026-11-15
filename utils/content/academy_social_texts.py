"""Academy social texts (Part 4): rivals, crushes, campus couples, club battles, study buddies. Wholesome and SFW.
HTML-safe: user names must be passed through utils.fmt.esc by the caller."""
from __future__ import annotations

BOT_RIVAL_NAMES: tuple[str, ...] = (
    "Captain Chalk", "Prefect Pixel", "Topper Tara-Bot", "Professor Pancake", "Sir Sprint-a-Lot",
    "Lady Lunchbox", "Quiz-Bot Quincy", "Marvin the Monitor", "Doodle Dave", "Echo the Hall Pass",
)

# ------------------------------------------------------------------ shared
NEED_ENROLL = "🎓 You are not enrolled this season. Use /enroll first."
NEED_ENROLL_CLOSED = "🎓 You are not enrolled this season, and enrolment is closed. See you in the next one!"
BREAK = "🌴 The campus is on break. Social life restarts with the next season."
NOT_YET = "⏳ This opens on <b>Day {day}</b> (Club & Campus Day). Check /schedule."
NOT_YOURS = "🚫 This isn't your menu. Use the command yourself."
STALE = "⌛ That button is out of date. Open the command again."
NO_TARGET = "👤 Reply to someone's message or write their @username."
SELF = "🙃 That's you! Pick someone else."
BOT_TARGET = "🤖 Bots don't go to school. Pick a real student."
TARGET_NOT_ENROLLED = "🎓 That player is not enrolled in the Academy this season."

# ------------------------------------------------------------------ rivals
RIVAL_NONE = ("🤝 No rival yet. Rivals are paired on <b>Day 3</b> (Club & Campus Day) from students with a similar GPA and "
              "Knowledge. Check back then!")
RIVAL_LATE = "🤖 You joined after the pairing, so a friendly bot rival will do. Use /rivalduel!"
RIVAL_NOTIFY = ("🤝 <b>Rival assigned!</b> Your Academy rival this season is <b>{name}</b>. "
                "Settle it with /rivalduel: three scenario cards, the higher total wins {prize} 💎 and a Rival badge point.")
RIVAL_NOTIFY_BOT = ("🤝 <b>Rival assigned!</b> There was an odd number of students, so your rival is the bot <b>{name}</b>. "
                    "Beat them with /rivalduel for {prize} 💎 and a Rival badge point.")
DUEL_CLOSED = "🔒 The rival duel closed at the end of Day {day}. Rivals are back next season."
DUEL_NOT_OPEN = "⏳ Duels open on Day {day}. Rivals are paired on Day 3."
DUEL_ALREADY = "✅ Your side of the duel is finished. Waiting for your rival to play."
DUEL_DONE = "🏁 This duel is over."
DUEL_HEAD = "⚔️ <b>Rival duel</b> vs <b>{name}</b> — card {n}/{total}"
DUEL_NEXT = "\n\n<b>{title}</b>\n{text}\n\n<i>Pick a move. Each button uses a different stat and risk.</i>"
DUEL_ROUND_WIN = "✅ {line} <b>+{points}</b>"
DUEL_ROUND_LOSE = "❌ {line} <b>{points:+d}</b>"
DUEL_WAIT = ("🏁 <b>Your side is done!</b> You scored <b>{total}</b>.\nYour rival hasn't played yet: you will both be told "
             "the result when they do (or when the duel closes).")
DUEL_RESULT_WIN = "🏆 <b>{winner}</b> wins the rival duel! <b>{wt}</b> to <b>{lt}</b>.{prize}"
DUEL_RESULT_TIE = "🤝 A draw! <b>{a}</b> and <b>{b}</b> both scored <b>{t}</b>.{prize}"
DUEL_WALKOVER = "🚪 <b>{winner}</b> wins by walkover: the rival never turned up.{prize}"
DUEL_PRIZE = " (+{d} 💎{badge})"
DUEL_BADGE = " and a Rival badge point"
DUEL_DM = "⚔️ Your rival duel is settled!\n{result}"
BADGE_LINE = "🏅 Rival badge points: <b>{n}</b>"

# ------------------------------------------------------------------ crushes
CRUSH_SAVED = ("🤫 <b>Your secret crush is saved.</b>\nIf they crush you back this season, you will both find out. "
               "Nobody else is told anything.\n<i>Tip: for full secrecy use /crush in a private chat with me.</i>")
CRUSH_GROUP_NUDGE = "🤫 Crushes are secret! I can't confirm in the group: open a private chat with me and use /crush there."
CRUSH_ALREADY = "💌 You already have an active crush. Use /crushoff first if you changed your mind."
CRUSH_IN_COUPLE = "💞 You are already a Campus Couple this season!"
CRUSH_TARGET_TAKEN = "💞 That student is already a Campus Couple this season. Pick someone else."
CRUSH_OFF = "🗑 Your secret crush was removed. Nobody was told."
CRUSH_OFF_NONE = "💌 You have no active crush."
CRUSH_MUTUAL_DM = ("💞 <b>It's mutual!</b> You and <b>{name}</b> both had a secret crush on each other. "
                   "You are now a <b>Campus Couple</b>! Try /campusdate once a day.")
CRUSH_MUTUAL_GROUP = "💞 A new Campus Couple: <b>{a}</b> and <b>{b}</b>! Congratulations!"
CRUSH_NO_CRUSH = "💌 No active crush. Send one with /crush (reply or @user). It stays secret unless it is mutual."
CRUSH_PENDING = "🤫 You have a secret crush waiting: <b>{name}</b>. Only you can see this. /crushoff removes it."

# ------------------------------------------------------------------ campus couple / date
COUPLE_NONE = "💞 You are not a Campus Couple. Send a secret /crush; if it's mutual you will both find out."
COUPLE_HEAD = ("💞 <b>Campus Couple</b>\n"
               "You and <b>{name}</b>\n"
               "Couple points: <b>{points}</b> · Dates this season: <b>{dates}</b>\n"
               "{today}")
COUPLE_TODAY_FREE = "Today's date: <b>available</b> (⚡{energy} Energy, {cost} 💎) → /campusdate"
COUPLE_TODAY_DONE = "Today's date: <b>done</b> ✅ Come back tomorrow."
DATE_NEED_COUPLE = "💞 You need a Campus Couple partner first. Send a secret /crush and hope it's mutual!"
DATE_CLOSED_DAY = "⏳ Campus dates open on Day {day}."
DATE_ONCE = "💞 You two already had today's date. Come back tomorrow!"
DATE_NO_COST = "💎 A campus date costs <b>{cost}</b> 💎 and you don't have enough."
DATE_OK = ("🍿 <b>Campus date with {name}!</b> Cost: ⚡{energy} Energy and {cost} 💎.\n"
           "{lines}\nCouple points +<b>{points}</b> (total <b>{total}</b>).")
DATE_LINES: tuple[str, ...] = (
    "You shared a milkshake behind the library.", "You watched the sunset from the rooftop of the science block.",
    "You cheered at the school ground together.", "You doodled in the same notebook for an hour.",
    "You walked the long way round the campus.", "You and a friendly stray cat shared the canteen bench.",
)
DATE_GAIN = "{who}: {gains}"

# ------------------------------------------------------------------ club battle
BATTLE_NOT_OPEN = "⏳ The club battle opens on <b>Day {day}</b> (Club & Campus Day)."
BATTLE_CLOSED = "🔒 The club battle is over."
BATTLE_ONCE = "✅ You already played your battle round for the club. Thanks for the points!"
BATTLE_HEAD = "🏟 <b>Club battle</b> — {emoji} {club}"
BATTLE_FOOT = ("<i>One round per member. Moves that use <b>{focus}</b> get a club bonus. Costs ⚡{energy} Energy.</i>")
BATTLE_RESULT_WIN = "✅ {line}\nYou scored <b>+{points}</b> for the {club}!"
BATTLE_RESULT_LOSE = "❌ {line}\nYou scored <b>{points:+d}</b> for the {club}."
CLUBS_HEAD = "🏟 <b>Club standings</b> — {title}"
CLUBS_ROW = "{medal} {emoji} <b>{name}</b> — <b>{total}</b> pts (practice {practice} + battle {battle}, {players} played)"
CLUBS_BATTLE_OPEN = "⚔️ Battle open until <b>{when}</b>. Each member plays one round."
CLUBS_BATTLE_SOON = "⚔️ The battle opens on Day {day}."
CLUBS_BATTLE_DONE = "🏁 The battle is over."
CLUBS_WINNER = "🏆 Winner: {emoji} <b>{name}</b>! Contributing members got a season badge and {d} 💎."
CLUBS_NO_WINNER = "🏁 No winner this season: nobody scored."
CLUBS_PLAY_BTN = "⚔️ Play my battle round"
CLUBS_WIN_DM = ("🏆 <b>{club}</b> won the club battle! You earned a season badge and <b>{d}</b> 💎. "
                "Great teamwork!")

# ------------------------------------------------------------------ buddies
BUDDY_NONE = ("📚 No study buddy yet. Ask someone with <code>/buddy @user</code> (or reply). When they accept, you both get "
              "+{k} Knowledge on your first /study each day.")
BUDDY_ACTIVE = ("📚 <b>Study buddy:</b> {name}\nBonus: +{k} Knowledge on your first /study each day. Today: {today}\n"
                "<i>/buddy off ends it.</i>")
BUDDY_TODAY_DONE = "claimed ✅"
BUDDY_TODAY_FREE = "not yet (study today!)"
BUDDY_PENDING_OUT = "📨 Your request to <b>{name}</b> is waiting for an answer. <i>/buddy off cancels it.</i>"
BUDDY_ALREADY_HAS = "📚 You already have a study buddy. Use /buddy off first."
BUDDY_TARGET_HAS = "📚 That student already has a study buddy."
BUDDY_PENDING_LIMIT = "📨 You already have an open buddy request. Wait for the answer or use /buddy off."
BUDDY_REQUEST = ("📚 <b>{who}</b> asks <b>{name}</b> to be study buddies this season!\n"
                 "Both get +{k} Knowledge on the first /study each day.")
BUDDY_SENT = "📨 Request sent to <b>{name}</b>."
BUDDY_CANT_DM = "📨 I couldn't reach them in a private chat. Ask in a group where they can press the button."
BUDDY_ACCEPTED = "📚 <b>{a}</b> and <b>{b}</b> are now study buddies! 🎉"
BUDDY_DECLINED = "📚 No problem: request declined."
BUDDY_ENDED = "📚 Study buddy ended."
BUDDY_NOTHING = "📚 You have no study buddy or open request."
BUDDY_GONE = "⌛ That request is no longer open."
BUDDY_NOT_FOR_YOU = "🚫 This request is not for you."
