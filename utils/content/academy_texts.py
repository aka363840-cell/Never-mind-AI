"""Academy Life texts (pure data). Wholesome, HTML-safe. Day hints stay generic: later parts add their own commands."""
from __future__ import annotations

PHASE_BLURBS: dict[int, str] = {
    1: "Doors open! Enrol, pick your stream and join a club.",
    2: "Classes begin. Settle in and start building your stats.",
    3: "Club and Campus Day: clubs compete and the campus buzzes.",
    4: "Midterms week. Late enrolment closes tonight.",
    5: "Fest Day: the big adventure of the season.",
    6: "Finals. Strategy, nerve and a little luck.",
    7: "Results and the Graduation ceremony. Congratulations, everyone!",
}

DAY_HINTS: dict[int, tuple[str, ...]] = {
    1: ("Choose your stream and club with /enroll.", "Your first stats come from your stream.",
        "Warm up with /study, /hangout or /club. Each costs Energy.", "New here? /academyhelp explains everything, page by page."),
    2: ("Your Energy refills by itself, about 10 points an hour.", "Check /schedule to plan the week.", "Adventures open today: try /adventure (the Haunted Library opens at night).",
        "Class day: /attend gives a mark, /study builds your streak."),
    3: ("Your club is your team today: play your battle round with /clubs play.", "Rivals are paired today: see /rival, then /rivalduel.",
        "Class day: /attend. Practise with /club to lift your club's score.", "Secret crush? /crush is private and only revealed if it is mutual."),
    4: ("Late joiners can still enrol until the end of today.", "Class day: last /attend of the week.", "The Midterm is open all day: /exam (5 quick strategy rounds).",
        "Low on Energy? /rest gives some back."),
    5: ("Fest Day is the highlight of the season: every /adventure pays a bigger bonus today!", "Rooftop Concert and Sports Day Showdown open only today.", "A /parttime shift pays a few diamonds (daily limit)."),
    6: ("Finals are open all day: /exam (8 rounds). Check /grades after.", "Rest up and keep your Energy high.", "/parttime is closed today so you can focus."),
    7: ("Results and graduation. Check /grades and /classrank. See you after the break!",),
}
BREAK_HINTS: tuple[str, ...] = ("The campus is resting. The next season starts soon.",)

LATE_JOIN_NOTE = ("🌱 <b>Late joiner:</b> you joined after Day 1, so you get a <b>lighter workload</b>. "
                  "You can still catch up and graduate with everyone!")

NOT_ENROLLED_OPEN = "You are not enrolled this season. Use /enroll to join the Academy!"
CLOSED_FOR_SEASON = "🔒 Enrolment for this season has closed. Come back when the next season opens!"
DISABLED = "🎓 The Academy is closed right now. Please check back later."


# ================================================================== Part 2: daily life
# Wholesome one-liners, plain text (the message builder escapes them). At least 10 per action.
STUDY_LINES: tuple[str, ...] = (
    "You found a quiet corner of the library and actually got things done.",
    "Colour-coded notes: your future self says thank you.",
    "Two chapters down, and the coffee is still warm.",
    "A study group, a whiteboard and one very good idea.",
    "You explained a tricky topic to a friend and finally understood it yourself.",
    "Flashcards, a timer and total focus. Beautiful.",
    "You cracked a puzzle that looked impossible an hour ago.",
    "Headphones on, world off. Pure concentration.",
    "You turned a messy page of notes into a neat summary.",
    "A rainy afternoon, a cosy desk and a very productive session.",
    "You solved the practice round on your second try. Progress!",
    "The campus clock struck four and you barely noticed. Deep focus.",
)
ATTEND_LINES: tuple[str, ...] = (
    "You slid into your seat just as the bell rang. Perfect timing.",
    "Front row, notebook open, pen ready. Teacher noticed.",
    "You answered a question in class and the room nodded.",
    "A guest lecture with a surprise demo. Everyone leaned in.",
    "You took tidy notes and shared them with the row behind you.",
    "The lesson ran long, but the story at the end was worth it.",
    "You picked up a clever trick from the teacher's example.",
    "Chalk dust, sunlight through the windows, and a good lesson.",
    "Your attendance mark is in. The register has your name on it.",
    "You asked a great question and sparked a mini-discussion.",
    "The class played a team game to review the lesson. Your side won!",
    "You walked in with a smile and left knowing more than you expected.",
)
HANGOUT_LINES: tuple[str, ...] = (
    "Samosas, laughter and a very long bench conversation.",
    "You joined a game of cards on the lawn and made three new friends.",
    "A spontaneous singalong broke out at the canteen.",
    "You and your friends swapped stories until the sun went down.",
    "Chai on the steps with the best company on campus.",
    "You organised a tiny quiz-free picnic and everybody came.",
    "A friendly frisbee match turned into a campus-wide event.",
    "You complimented a stranger's sketchbook and started a great chat.",
    "Board games in the common room: you won the first round, lost the second.",
    "You helped a first-year find their classroom and got a new buddy.",
    "Everyone shared a snack and a silly joke. The mood was perfect.",
    "A sunset walk around the campus pond with good friends.",
)
REST_LINES: tuple[str, ...] = (
    "A short nap on the library beanbags. Bliss.",
    "You stretched, sipped some water and watched the clouds.",
    "Ten quiet minutes under the big tree. Reset complete.",
    "A warm snack and a comfy bench worked wonders.",
    "You put the phone down and just breathed for a bit.",
    "Soft music, dim light, no worries. Recharged.",
    "A slow walk across the courtyard cleared your head.",
    "You curled up with a good book for a while.",
    "A cup of chai and a sunny windowsill. Heavenly.",
    "You did some light stretching and felt your shoulders drop.",
    "A quick power nap before the next adventure.",
    "You listened to the rain from the corridor. So calm.",
)
CLUB_LINES: tuple[str, ...] = (
    "Practice was loud, messy and exactly what the team needed.",
    "Your club leader clapped twice and said: again, but with feeling!",
    "You stayed late to help set up and the whole club noticed.",
    "A new teammate joined today and you showed them the ropes.",
    "The club room smelled of snacks and teamwork.",
    "You tried a new routine and it clicked on the third attempt.",
    "Everyone pitched in and the session ended with cheers.",
    "A rival club peeked through the window. Time to impress them!",
    "You helped paint the club banner. It looks great.",
    "The captain announced you did great work today.",
    "A friendly challenge inside the club made everyone sharper.",
    "You stayed to tidy up and found a forgotten lucky charm.",
)
CLUB_EXTRA: dict[str, tuple[str, ...]] = {
    "sports": ("Morning laps, then passing drills. Legs feeling it!", "Your team practised a clean relay handover.",
               "A tricky corner kick finally worked."),
    "music": ("The band nailed the chorus on the fourth take.", "You found a funky new rhythm on the drums.",
              "Harmonies lined up for the first time. Goosebumps."),
    "drama": ("You rehearsed a dramatic entrance and the stage lit up.", "Costume fitting: the cape is magnificent.",
              "You improvised a scene and the whole room laughed."),
    "coding": ("The team debugged a stubborn puzzle together.", "A midnight hack session fuelled by snacks.",
               "You shipped a tiny game and everyone played it."),
}
PARTTIME_LINES: tuple[str, ...] = (
    "You shelved books at the campus library and got paid for it.",
    "A shift at the canteen counter: you remembered every order.",
    "You helped the bookshop with its monthly stock check.",
    "You tutored younger students in the art of drawing cats.",
    "A weekend-style shift at the campus cafe. Tips were generous.",
    "You handed out flyers for the next fest and finished early.",
    "You helped the gardening crew water the campus flower beds.",
    "A shift at the stationery shop: you organised the pen wall.",
    "You guided visitors around campus and got a thank-you tip.",
    "You helped set up chairs for an assembly and got paid on the spot.",
    "A quiet shift at the lost-and-found desk. You even reunited a scarf!",
    "You helped the print shop collate notes. Your arms are tired, your wallet smiles.",
)

# one short note per (stream, action) perk defined in game/academy_rules.STREAM_PERKS
STREAM_NOTES: dict[tuple[str, str], str] = {
    ("science", "study"): "🔬 Science perk: lab habits make it stick (+1 Knowledge).",
    ("science", "attend"): "🔬 Science perk: you spotted the key idea early (+1 Knowledge).",
    ("arts", "hangout"): "🎨 Arts perk: you sketched the scene on a napkin (+1 Creativity).",
    ("arts", "attend"): "🎨 Arts perk: doodles in the margin sparked an idea (+1 Creativity).",
    ("commerce", "hangout"): "💼 Commerce perk: you made a useful new contact (+1 Charisma).",
    ("commerce", "parttime"): "💼 Commerce perk: you negotiated a better rate (+20% pay).",
    ("sports", "club"): "🏅 Sports perk: extra warm-up drills (+1 Fitness).",
    ("sports", "rest"): "🏅 Sports perk: a good stretch speeds up recovery (+5 Energy).",
    ("music", "club"): "🎵 Music perk: you added a flourish to the arrangement (+1 Creativity).",
    ("music", "hangout"): "🎵 Music perk: you led a singalong (+1 Charisma).",
}

STREAK_LINES: dict[int, str] = {
    3: "🔥 <b>3-day study streak!</b> Bonus Knowledge unlocked.",
    5: "🔥🔥 <b>5-day study streak!</b> Your teachers are impressed.",
    7: "🔥🔥🔥 <b>7-day study streak!</b> A perfect week of studying!",
}

# ---- gate / status messages (HTML)
BREAK_CLOSED = "🌴 The campus is on break and everything is closed. The next season opens <b>{when}</b>."
NOT_STARTED = "⏳ The Academy has not opened yet. Check /schedule."
NEED_ENROLL_OPEN = "🎓 Join the Academy first, then you can {verb}! Tap the button to enrol."
NEED_ENROLL_CLOSED = "🔒 You are not enrolled this season and enrolment has closed. See you in the next season!"
NO_CLASS_TODAY = "🏫 No classes today. You can attend on class days (Days {days})."
FINALS_NO_JOB = "💼 It is Finals day: the part-time job is closed so everyone can focus. Back tomorrow!"
CLOSED_TODAY = "🔒 {name} is not open on Day {day}."
ATTENDED_TODAY = "✅ You already attended class today. See you on the next class day!"
LIMIT_REACHED = "{emoji} You have already done <b>{name}</b> {n}/{n} times today. A new Academy day starts in <b>{resets}</b>."
CAP_REACHED = ("💎 You have earned today's maximum of <b>{cap}</b> diamonds from part-time work. "
               "Come back when a new Academy day starts in <b>{resets}</b>.")
ALREADY_RESTED = "⚡ Your Energy is already full. Keep /rest for when you need it!"
NO_ENERGY = ("😴 Not enough Energy: you have <b>{have}</b>, need <b>{need}</b>. "
             "Enough regenerates in about <b>{wait}</b>. A /rest helps too.")
ALREADY_PAID = "⚠️ That shift was already paid, nothing changed."

VERBS: dict[str, str] = {"study": "study", "attend": "attend class", "hangout": "hang out", "rest": "rest",
                         "club": "practise with a club", "parttime": "take a part-time job"}
