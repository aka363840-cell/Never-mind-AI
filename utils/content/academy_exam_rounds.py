"""Academy exam scenario cards (Part 3). Original, light school-life situations: NO real-subject questions, nothing with a
right or wrong factual answer. Each button names the stat it uses (knowledge / creativity / charisma / fitness), a risk level
(safe / balanced / risky) and a short flavour line for success and for failure. `stream` = None for a general card, or a
stream key for a stream-flavoured one. Difficulty 1 (easy) .. 3 (hard)."""
from __future__ import annotations

from dataclasses import dataclass

STATS = ("knowledge", "creativity", "charisma", "fitness")
RISKS = ("safe", "balanced", "risky")
K, X, C, F = "knowledge", "creativity", "charisma", "fitness"
S, B, R = "safe", "balanced", "risky"


@dataclass(frozen=True)
class Button:
    label: str
    stat: str
    risk: str
    win: str
    lose: str


@dataclass(frozen=True)
class Card:
    key: str
    title: str
    text: str
    difficulty: int
    stream: str | None
    buttons: tuple[Button, ...]


def _c(key, stream, diff, title, text, *btns) -> Card:
    return Card(key, title, text, diff, stream, tuple(Button(*b) for b in btns))


CARDS: tuple[Card, ...] = (
    # ------------------------------------------------------------------ general, difficulty 1
    _c("late_bus", None, 1, "The Late Bus", "Your bus is stuck behind a parade and the hall doors close in ten minutes.",
       ("Run the last stretch", F, B, "You sprint in, hair wild, seat still warm.", "You arrive panting and lose a few precious minutes."),
       ("Chat up a scooter-driving senior", C, R, "Free ride, and a cheer from the gate guards!", "He laughs and rides off without you."),
       ("Take the sensible shortcut", K, S, "You remember the alley route and arrive calm.", "The alley is blocked, but you still make it, a bit rattled.")),
    _c("broken_pencil", None, 1, "Pencil Panic", "Your only pencil snaps right as the paper is handed out.",
       ("Borrow one with a smile", C, S, "Your neighbour hands over two pencils and a tiny eraser.", "Everyone is using theirs; you lose a moment finding one."),
       ("Sharpen the stub into art", X, B, "A stubby pencil, but oddly good grip. Neat work follows.", "The tip keeps breaking. Not your best start."),
       ("Write fast before it dies", K, R, "You finish the first page in record time!", "The stub gives up halfway through a line.")),
    _c("noisy_hall", None, 1, "The Noisy Hall", "A band is rehearsing next door and the whole hall can hear the drums.",
       ("Block it out and focus", K, S, "You zone in and the drums fade to a hum.", "The beat creeps in; you reread a line three times."),
       ("Ask the band to lower it", C, B, "They grin, wave and drop to a whisper.", "They nod, then forget in two minutes."),
       ("Use the rhythm as a metronome", X, R, "You pace yourself to the beat and fly through.", "You lose your place chasing the beat.")),
    _c("lost_notes", None, 1, "The Lost Notebook", "You left your notebook at home. Your bag has a pen, a snack and a hope.",
       ("Piece it together from memory", K, B, "More survived in your head than you thought!", "The middle part is a blur."),
       ("Trade a snack for a peek at a friend's notes", C, S, "A cookie closes the deal. Friendship wins.", "Your friend only has a doodle of a cat."),
       ("Invent your own memory tricks", X, R, "A silly rhyme saves the day.", "The rhyme sounds good but means nothing.")),
    _c("group_corner", None, 1, "Corner Seat", "You get the corner desk, next to the loudest radiator in school.",
       ("Swap seats politely", C, S, "A kind classmate trades with you.", "Nobody wants to move, so you stay."),
       ("Stay and stay cool", F, B, "You ignore the heat and keep a steady pace.", "You sweat through the second page."),
       ("Turn the noise into a challenge", X, R, "You race the radiator and win!", "The radiator wins this round.")),
    _c("snack_break", None, 1, "Snack Break Choice", "The canteen has ten minutes left and a very long queue.",
       ("Power-walk to the front", F, B, "You squeeze in with a warm samosa in hand.", "You get there as the last tray empties."),
       ("Charm the canteen aunty", C, R, "Aunty slips you an extra treat!", "Aunty says 'queue, dear' and points back."),
       ("Skip it and review", K, S, "A calm review beats a snack.", "Your stomach grumbles and distracts you.")),
    _c("seat_neighbor", None, 1, "Whispering Neighbour", "The student beside you keeps whispering questions during the paper.",
       ("Ignore and focus", K, S, "You tune them out like a pro.", "Whispers slip into your thoughts."),
       ("Give a friendly shush", C, B, "They blush and go quiet.", "They giggle louder. Oops."),
       ("Swap the seat with a joke", X, R, "The invigilator smiles at your quip and moves them.", "The joke lands flat and you both get a look.")),
    _c("rainy_day", None, 1, "Rainy Exam Morning", "It is pouring and your shoes squelch with every step into the hall.",
       ("Dry off and settle in", F, S, "A towel, a deep breath, and you are good to go.", "Wet socks haunt you for a while."),
       ("Laugh it off with classmates", C, B, "A shared joke and everyone relaxes.", "The giggles fade and nerves return."),
       ("Treat it like an adventure", X, R, "You turn the squelch into a rhythm and ace it.", "You skid at the door. Dramatic.")),
    # ------------------------------------------------------------------ general, difficulty 2
    _c("group_project", None, 2, "Group Project Rescue", "Your team is arguing and the deadline is tonight.",
       ("Run a calm meeting", C, S, "Roles assigned in five minutes, tea included.", "Two people sulk but it still works out."),
       ("Do the hard parts yourself", K, B, "A long night, a solid result.", "Too much work for one pair of hands."),
       ("Pitch a bold new angle", X, R, "Your wild idea wins the room!", "The new angle confuses everyone.")),
    _c("surprise_question", None, 2, "Surprise Twist", "The paper has a twist nobody practised for. The room goes silent.",
       ("Stay calm and plan", K, S, "A few notes and the path appears.", "You overthink and stall."),
       ("Trust your gut", X, B, "Gut feeling says go left. Left is right!", "Gut says left. It was wrong."),
       ("Swing big and improvise", C, R, "You wing it with such confidence the page looks great!", "Confidence cannot cover everything.")),
    _c("energy_dip", None, 2, "Afternoon Slump", "Hour three. Eyes heavy, pen heavier.",
       ("Stretch and sip water", F, S, "Two stretches and you are back.", "The slump lingers a little."),
       ("Chat with a friend at the break", C, B, "A laugh wakes you up.", "The break ends before the chat does."),
       ("Push through on pure focus", K, R, "Tunnel vision. Magnificent.", "Your thoughts blur at the edges.")),
    _c("club_clash", None, 2, "Club Clash", "Your club rehearsal is scheduled at the same hour as the exam prep session.",
       ("Ask for a swap", C, S, "Both captains agree to shift by an hour.", "The swap falls through and you rush both."),
       ("Do prep first, club later", K, B, "Prep done, club forgiving.", "You rush prep and the club grumbles."),
       ("Stage a quick mini-session", X, R, "Ten sharp minutes of club magic and a full prep!", "Neither gets proper attention.")),
    _c("shaky_nerves", None, 2, "Shaky Nerves", "Your hands tremble as the invigilator starts the timer.",
       ("Breathe in for four, out for four", F, S, "Steady hands by the third breath.", "Breath helps a bit, not enough."),
       ("Visualise yourself finishing", X, B, "You see the finish line. You reach it.", "The picture fades as the clock ticks."),
       ("Boldly start from the hardest part", K, R, "Hardest first and it falls like dominoes!", "The hardest part bites back.")),
    _c("friend_help", None, 2, "A Friend Needs Help", "A friend is stuck and asks for a hint mid-session. Time is short.",
       ("Point them to the invigilator", C, S, "Good call. You both move on.", "They look hurt but you kept your focus."),
       ("Give a quick encouraging nod", C, B, "A smile and a thumbs up. They manage alone.", "The nod does not help, and you lose a beat."),
       ("Ignore and finish yours", K, R, "Fast and focused. Friend understands later.", "You rush and miss a detail.")),
    _c("mystery_prop", None, 2, "Mystery Prop", "A strange wooden contraption sits on your desk with a note: 'Make it useful'.",
       ("Study it slowly", K, S, "It is a clever stand. You use it well.", "You spend too long examining it."),
       ("Reinvent it your way", X, B, "A new use no one imagined!", "It tips over with a clatter."),
       ("Show it off to the room", C, R, "Applause. The examiner is impressed.", "Silence. Slightly awkward.")),
    _c("power_cut", None, 2, "Power Cut", "The lights flicker and die. The invigilator grins and hands out torches.",
       ("Carry on by torchlight", K, S, "A pool of light, a calm mind.", "Shadows dance and you squint."),
       ("Lead a calm sing-along", C, B, "The room hums along while the lights return.", "Nobody knows the words."),
       ("Run to fetch a lantern", F, R, "You return with a glowing lantern like a hero!", "You trip over a bench. Dramatic.")),
    _c("rival_stare", None, 2, "The Rival's Stare", "The class topper keeps glancing your way. They look confident.",
       ("Ignore and breathe", F, S, "A neutral face. You settle.", "The stare stays in your head."),
       ("Give them a warm smile", C, B, "They blink, blush and look away.", "They smile back, pen racing."),
       ("Challenge them in your head", X, R, "A silent contest sparks your best work!", "You rush to keep pace and slip.")),
    # ------------------------------------------------------------------ general, difficulty 3
    _c("double_booking", None, 3, "Double Booking", "You are due in two rooms at once: the exam and a prize ceremony.",
       ("Negotiate a time split", C, S, "Both teachers agree. Applause all round.", "One teacher frowns; time is lost."),
       ("Sprint between them", F, B, "Sweaty, brilliant, both done!", "You are late to both."),
       ("Pick the exam and trust your plan", K, B, "A clear head wins the day.", "Regret slows you."),
       ("Find a third way", X, R, "A video message and a quick note settle it!", "The clever plan fizzles.")),
    _c("long_case", None, 3, "The Long Case", "A page-long scenario with a dozen moving parts and one mystery token.",
       ("Map every part on paper", K, S, "A neat chart and the whole thing clicks.", "Your chart is neat but half empty."),
       ("Skim and find the pattern", X, B, "The pattern pops out like a puzzle piece.", "You miss the pattern entirely."),
       ("Pitch a bold solution first", C, R, "You commit to the answer and it fits!", "You commit to the wrong answer.")),
    _c("marathon_day", None, 3, "Marathon Day", "Two papers, one long break and a heat wave.",
       ("Pace yourself", F, S, "Short rests and a steady rhythm.", "You slow down more than planned."),
       ("Run on adrenaline", F, R, "A burst of energy carries you!", "The adrenaline fades in the middle."),
       ("Review during the break", K, B, "A quick review pays off.", "You tire before the second paper.")),
    _c("big_stage", None, 3, "Big Stage Moment", "The panel asks you to present your approach to the entire hall.",
       ("Keep it short and clear", K, S, "Crisp and easy. A neat nod.", "You rush and the point blurs."),
       ("Win the room with charm", C, B, "A joke breaks the ice and they follow along.", "The joke fizzles."),
       ("Make it a performance", X, R, "A little drama, a lot of cheers!", "Too much drama, too little point.")),
    _c("curveball_rule", None, 3, "The Curveball Rule", "A new rule appears halfway: 'No repeating your first idea.' You already used it.",
       ("Combine ideas smartly", K, B, "A mash-up that feels fresh.", "The mash-up is a mess."),
       ("Think sideways", X, R, "A sideways angle nobody expected.", "The angle goes in circles."),
       ("Ask the invigilator to clarify", C, S, "A clear answer and a fair restart.", "The clarification costs a minute.")),
    _c("tight_clock", None, 3, "The Tight Clock", "Ten minutes left and a third of the paper untouched.",
       ("Pick the easiest bits first", K, S, "Quick wins stack up.", "Easy bits are fewer than you hoped."),
       ("Sprint and sacrifice neatness", F, B, "Messy, but complete!", "Too messy to read."),
       ("Trust your instincts for all of it", X, R, "Instincts pour out like a river.", "Instincts take a nap.")),
    _c("closing_ceremony", None, 3, "Leaders' Challenge", "The head asks a volunteer to lead the last hall activity.",
       ("Offer a plan in writing", K, S, "A clear plan, applause.", "The plan is too stiff."),
       ("Take the mic with a smile", C, B, "The hall follows your lead!", "The mic squeals and the mood dips."),
       ("Turn it into a game", X, R, "A tiny game, a huge cheer!", "Rules too complicated; chaos.")),
    # ------------------------------------------------------------------ Science (lab practical)
    _c("sci_lab_mixup", "science", 1, "Lab Practical: Mixed Trays", "Two trays of lab gear got swapped and the practical starts now.",
       ("Sort them by logic", K, S, "Labels, order, done!", "You sort neatly but slowly."),
       ("Ask your lab partner to help", C, B, "Partner nods, together you fix it.", "Partner is just as confused."),
       ("Improvise a clever setup", X, R, "A smart arrangement; the examiner raises an eyebrow in respect.", "The setup wobbles and a flask rolls away.")),
    _c("sci_gadget", "science", 2, "Lab Practical: The Rattling Gadget", "A gadget on the bench rattles and nobody knows why. You have to make it behave.",
       ("Check it step by step", K, S, "Found the loose screw!", "You check everything but the screw."),
       ("Tweak and test quickly", X, B, "A few tweaks and it hums.", "A tweak makes it rattle more."),
       ("Give it a firm tap", F, R, "A tap and it purrs.", "Something pops. Hmm.")),
    _c("sci_report", "science", 2, "Lab Practical: The Messy Report", "Your notes are scribbles. The report is due in fifteen minutes.",
       ("Rewrite neatly from scratch", K, S, "Clear and tidy.", "Tidy but unfinished."),
       ("Draw it as a diagram", X, B, "A clear diagram says it all.", "The diagram leaves out the middle."),
       ("Present it as a story", C, R, "A story the examiner follows with a smile!", "The story wanders.")),
    _c("sci_showcase", "science", 3, "Lab Practical: The Big Demo", "The examiners want a live demo of your experiment. A crowd gathers.",
       ("Rehearse once, calmly", K, S, "Smooth and safe.", "Smooth, but a step skipped."),
       ("Narrate it like a TV host", C, B, "The crowd loves your narration!", "You lose track mid-sentence."),
       ("Add a flashy finale", X, R, "Sparkle! The examiner claps!", "The finale fizzles with a pop.")),
    # ------------------------------------------------------------------ Arts (essay)
    _c("art_blank_page", "arts", 1, "Essay: The Blank Page", "The essay title is 'The Day My Umbrella Spoke'. Your page is blank.",
       ("Outline first, then write", K, S, "A clean outline, a clear essay.", "The outline takes too long."),
       ("Start with a funny line", X, B, "The line hooks everybody.", "The line leads nowhere."),
       ("Write the ending first", X, R, "You land the ending and build around it!", "The ending does not match the beginning.")),
    _c("art_voice", "arts", 2, "Essay: Finding Your Voice", "You want your essay to sound like YOU, not a textbook.",
       ("Keep it simple and honest", K, S, "Plain words, strong feeling.", "Too plain; it falls flat."),
       ("Read it aloud softly", C, B, "The rhythm helps you polish.", "Reading aloud makes you self-conscious."),
       ("Go fully poetic", X, R, "Lyrical lines sing!", "Too flowery; the point is lost.")),
    _c("art_critic", "arts", 2, "Essay: The Critic in the Corner", "A classmate whispers that your idea is 'too weird'.",
       ("Trust yourself and write", X, B, "Weird works. You get an eager nod.", "Too weird. You rewrite."),
       ("Ask what they mean", C, S, "A useful chat and a sharper idea.", "Nothing useful, and a minute gone."),
       ("Double down on weird", X, R, "Bold and brilliant!", "Bold... and baffling.")),
    _c("art_deadline", "arts", 3, "Essay: Last Paragraph", "Five minutes left and the last paragraph is still a doodle.",
       ("Wrap up with a short summary", K, S, "A neat closing and you finish.", "The summary is thin."),
       ("Pick the strongest image", X, B, "One vivid picture and it works.", "The image is not quite right."),
       ("Go for a dramatic twist", C, R, "A twist ending! The examiner grins.", "The twist confuses the story.")),
    # ------------------------------------------------------------------ Commerce (case study)
    _c("com_stall", "commerce", 1, "Case Study: The Lemonade Stall", "A stall has a long queue but prices that make customers frown.",
       ("List costs and fix the price", K, S, "A fair price and happy faces.", "A fair price but a slow explanation."),
       ("Chat with customers", C, B, "Customers love the friendly offers.", "Chatting slows the queue."),
       ("Launch a bold bundle deal", X, R, "The bundle sells out!", "Nobody understands the bundle.")),
    _c("com_pitch", "commerce", 2, "Case Study: The Tiny Pitch", "You have 60 seconds to pitch a sturdy, ordinary umbrella to a shy investor.",
       ("Stick to the facts", K, S, "Clear and trustworthy.", "Accurate but dull."),
       ("Tell a story about a rainy day", X, B, "The investor leans in.", "The story is longer than the pitch."),
       ("Charm them with confidence", C, R, "A smile and a handshake: deal!", "Too much swagger, no deal.")),
    _c("com_team", "commerce", 2, "Case Study: The Quarrelling Team", "Two managers of a pretend shop disagree about opening hours.",
       ("Ask for a short trial", K, S, "A fair compromise.", "Both frown and nothing changes."),
       ("Listen to each side", C, B, "A calm talk brings peace.", "The talk runs on and on."),
       ("Propose a surprise sale", X, R, "The sale draws a crowd and solves it!", "The sale confuses everyone.")),
    _c("com_crisis", "commerce", 3, "Case Study: The Sold-Out Rush", "A pretend shop sells out of its star item with an hour left of opening.",
       ("Offer a pre-order list", K, S, "Customers happily sign up.", "The list is longer than the stock will ever be."),
       ("Calm the queue and promise more", C, B, "A heartfelt promise keeps them.", "Customers grumble and drift away."),
       ("Launch an instant mystery box", X, R, "Mystery boxes fly off the shelf!", "The mystery boxes are mostly empty.")),
    # ------------------------------------------------------------------ Sports (fitness test)
    _c("spt_warmup", "sports", 1, "Fitness Test: The Warm-up", "The coach announces a surprise warm-up lap before the test.",
       ("Jog steadily", F, S, "Smooth and warm.", "Steady, but a bit stiff."),
       ("Dash and recover", F, B, "A burst of speed and a clean recovery.", "You burn out before the finish."),
       ("Lead the whole class", C, R, "The class follows you like a flock!", "You lose count of the laps.")),
    _c("spt_relay", "sports", 2, "Fitness Test: The Relay", "Your relay team needs a plan for the baton handover.",
       ("Practise one clean handover", K, S, "A clean handover.", "The baton wobbles but you keep it."),
       ("Run your fastest leg", F, B, "A flying leg!", "A stumble on the bend."),
       ("Risk a blind pass", F, R, "A smooth blind pass! The crowd cheers.", "The baton hits the ground.")),
    _c("spt_cramp", "sports", 2, "Fitness Test: The Cramp", "A cramp bites just before the final stretch.",
       ("Slow down and stretch", F, S, "A short pause saves the race.", "You lose a few places but finish."),
       ("Talk yourself through it", C, B, "A pep talk and the cramp relents.", "The pep talk helps your mood, not your leg."),
       ("Push through the pain", F, R, "You grit your teeth and win!", "You limp over the line.")),
    _c("spt_final_lap", "sports", 3, "Fitness Test: The Last Lap", "Three runners are level on the final lap.",
       ("Hold your pace", F, S, "Smooth, stylish and safe.", "Your pace is steady but not enough."),
       ("Plan a final kick", K, B, "Your kick is perfectly timed.", "Too early; you fade."),
       ("Go for the surge", F, R, "A mighty surge and you are first!", "The surge leaves you gasping.")),
    # ------------------------------------------------------------------ Music (performance)
    _c("mus_tuning", "music", 1, "Performance: The Stubborn Instrument", "Your instrument keeps going out of tune.",
       ("Tune carefully", K, S, "Pitch perfect.", "Nearly there, a little sharp."),
       ("Play by ear", X, B, "Your ear finds the pitch.", "The ear gets it wrong."),
       ("Turn the squeak into a feature", C, R, "The audience laughs and loves it!", "The squeak is just a squeak.")),
    _c("mus_stage_fright", "music", 2, "Performance: Stage Fright", "The curtain opens and the audience looks very big.",
       ("Breathe and count the beat", F, S, "Steady beat, steady heart.", "Heart races through the intro."),
       ("Find a friendly face", C, B, "A smile in row three keeps you going.", "The friendly face has left."),
       ("Start with a flourish", X, R, "A flourish and a roar of applause!", "The flourish goes sour.")),
    _c("mus_duet", "music", 2, "Performance: The Surprise Duet", "Your duet partner forgot the words and looks panicked.",
       ("Slow down and lead gently", C, S, "Gentle lead, smooth finish.", "You both stumble but recover."),
       ("Improvise a harmony", X, B, "A harmony so sweet!", "The harmony clashes."),
       ("Switch to a bold solo", X, R, "A solo that brings the house down!", "The solo wanders off-key.")),
    _c("mus_encore", "music", 3, "Performance: The Encore", "The crowd asks for one more song. You have nothing prepared.",
       ("Repeat your best piece", K, S, "A warm reprise, loved by all.", "A reprise... slightly flat."),
       ("Play the crowd's favourite", C, B, "The crowd sings along!", "You remember half of it."),
       ("Make up a new tune right now", X, R, "A magical new tune!", "A tune with no ending.")),
)

BY_KEY: dict[str, Card] = {c.key: c for c in CARDS}
GENERAL: tuple[Card, ...] = tuple(c for c in CARDS if c.stream is None)


def stream_cards(stream: str) -> tuple[Card, ...]:
    return tuple(c for c in CARDS if c.stream == stream)
