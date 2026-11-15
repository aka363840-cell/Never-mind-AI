"""Academy adventures (Part 5): the stories as PLAIN DATA. No imports from the bot, no logic beyond building the tables.

An adventure is a small graph of nodes:
  * a story node has `text` and 1-4 `choices` (buttons). A choice either moves straight to another node (`to`) or rolls a
    stat `check` (success / failure targets). `sets` are flags remembered for the rest of the run, `needs` / `hides` show or
    hide a button depending on those flags, and a check can get skill from flags (`bonus`) and set flags when passed (`win_sets`).
  * an ending node has an `Ending` (title, mood, rewards, optional title / item trophy, optional GIF action) and no choices.
Every number here is checked against game/academy_rules.py by tests/test_academy_adventure_data.py (reachability, dead links,
reward caps, button lengths ...). Texts are PLAIN text: the views escape them. Fun, light school life; nothing real-subject.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Check:
    stat: str                                   # knowledge | charisma | fitness | creativity | luck
    difficulty: int                             # 1..4 (AR.ADV_TARGET)
    success: str                                # node key
    fail: str                                   # node key
    bonus: tuple[tuple[str, int], ...] = ()     # ((flag, +/- skill), ...) applied when the flag is set
    win_sets: tuple[str, ...] = ()              # flags set when the check is passed


@dataclass(frozen=True)
class Choice:
    text: str                                   # button label (kept short)
    to: str | None = None                       # plain move ... or:
    check: Check | None = None                  # ... a stat check
    sets: tuple[str, ...] = ()                  # flags set when this button is pressed
    needs: tuple[str, ...] = ()                 # shown only when ALL of these flags are set
    hides: tuple[str, ...] = ()                 # hidden when ANY of these flags is set


@dataclass(frozen=True)
class Trophy:
    key: str                                    # unique across all adventures (one per season and player)
    kind: str                                   # "title" | "item"
    name: str


@dataclass(frozen=True)
class Ending:
    title: str
    mood: str                                   # great | good | ok | bad
    diamonds: int = 0
    stats: dict = field(default_factory=dict)   # {"creativity": 2, ...}
    trophy: Trophy | None = None
    gif: str | None = None                      # one of AR.ADV_GIFS_ALLOWED


@dataclass(frozen=True)
class Node:
    key: str
    text: str
    choices: tuple[Choice, ...] = ()
    ending: Ending | None = None


@dataclass(frozen=True)
class Adventure:
    key: str
    name: str
    emoji: str
    blurb: str
    energy: int                                 # Energy to start (AR.ADV_ENERGY_MIN..MAX)
    days: tuple[int, ...]                       # season days it is open on
    start: str
    nodes: dict                                 # key -> Node
    night: bool = False                         # only at night (AR.adv_night, local time)
    stream_bonus: dict = field(default_factory=dict)    # {"music": 5}: skill added to every stat check of that stream


def _adv(key, name, emoji, blurb, energy, days, start, nodes, night=False, stream_bonus=None) -> Adventure:
    table: dict[str, Node] = {}
    for n in nodes:
        if n.key in table:
            raise ValueError(f"duplicate node {n.key!r} in {key}")
        table[n.key] = n
    return Adventure(key, name, emoji, blurb, energy, tuple(days), start, table, night, dict(stream_bonus or {}))


def _end(key, text, **ending) -> Node:
    return Node(key, text, (), Ending(**ending))


C = Choice
K = Check

# ================================================================== 1. The Haunted Library (Days 2-6, at night)
HAUNTED_LIBRARY = _adv(
    "haunted_library", "The Haunted Library", "🕯",
    "The library after dark: a floating book, a mysterious 'whooooo' and a very unbothered cat.",
    20, (2, 3, 4, 5, 6), "gates", night=True, nodes=[
        Node("gates", "It is long past dusk and you are returning one extremely overdue book. The library doors lock behind "
                      "you all by themselves. Somewhere in the Reading Room, a soft 'whooooo' floats between the shelves.",
             (C("Tiptoe toward the whooooo", to="reading"),
              C("Study the old notice board", check=K("knowledge", 1, "notice", "reading", win_sets=("map",))))),
        Node("notice", "In torchlight you read a brass plate: 'Night lamps run on a timer. If you hear a bell, that is only "
                       "Professor Biscuit.' Beside it hangs a hand-drawn floor plan with a secret shortcut behind Poetry.",
             (C("Take the Poetry shortcut", to="stacks"),
              C("Head for the Reading Room", to="reading"))),
        Node("reading", "A book hovers a foot above a table, turning its own pages. A lamp flickers. Your torch finds a "
                        "long, wobbly shadow shaped suspiciously like a bedsheet.",
             (C("Call out: 'I come in peace and snacks!'", check=K("charisma", 2, "talk", "chase")),
              C("Answer with a shadow puppet", check=K("creativity", 2, "talk", "stacks")),
              C("Sneak around the tables", to="stacks"))),
        Node("stacks", "The shelves tower over you and the air turns chilly. Something jingles, and a pale shape glides "
                       "past the Poetry aisle.",
             (C("Follow the jingling", to="cat_trail"),
              C("Use the floor plan to cut it off", to="talk", needs=("map",)),
              C("Sprint after the pale shape", check=K("fitness", 2, "talk", "end_closet")))),
        Node("cat_trail", "The jingling leads to a very fluffy cat wearing a tiny bell. She sits on a pile of overdue "
                          "notices, looking deeply unbothered. Behind her, a sheet-covered figure is hiding.",
             (C("Offer the cat your hand, gently", check=K("charisma", 1, "talk", "chase")),
              C("Pet the cat and quietly go home", to="end_cat"))),
        Node("chase", "The sheet-covered shape bolts and the chase is on! It zigzags through Reference, around the big "
                      "globe and straight toward the staircase.",
             (C("Sprint after it", check=K("fitness", 2, "talk", "end_closet")),
              C("Shout: 'Wait, I only have a book!'", check=K("charisma", 2, "talk", "end_closet")))),
        Node("talk", "The figure skids to a halt. 'Shhh!' A first-year student pops out from under the sheet. 'I am "
                     "rehearsing my ghost for the Drama Club audition. Please do not tell anyone!'",
             (C("Offer to be the audience and give notes", check=K("creativity", 3, "end_encore", "end_shush")),
              C("Suggest a spooky duet", check=K("charisma", 3, "end_encore", "end_shush")),
              C("Promise to keep the secret", to="end_shush"))),
        _end("end_encore", "Your notes are so good that the first-year drags you onto the stage. Together you perform the "
                           "most dramatic ghost duet the library has ever heard. Even Professor Biscuit meows applause.",
             title="Encore, Ghost Whisperer!", mood="great", diamonds=30, stats={"creativity": 2, "charisma": 1},
             trophy=Trophy("ghost_whisperer", "title", "Ghost Whisperer"), gif="dance"),
        _end("end_shush", "You promise to keep the secret, hand in your overdue book and tiptoe home. The next day the "
                          "audition poster reads 'Best Ghost: nobody will ever know who'. You smile.",
             title="A Secret Well Kept", mood="good", diamonds=18, stats={"knowledge": 1, "charisma": 1}, gif="wink"),
        _end("end_cat", "You pet Professor Biscuit, who accepts this as her due. As you leave, she drops her bell at your "
                        "feet. You decide that is a gift. The 'whooooo' was never solved, but the evening was lovely.",
             title="Professor Biscuit's Friend", mood="ok", diamonds=12, stats={"knowledge": 1, "creativity": 1},
             trophy=Trophy("biscuits_bell", "item", "Professor Biscuit's Bell"), gif="wave"),
        _end("end_closet", "You lose the shape and take a wrong turn into the supplies closet. The door clicks shut. You "
                           "spend a quiet night with mops and a very patient spider until the morning bell frees you.",
             title="Locked in with the Mops", mood="bad", diamonds=4, stats={"fitness": 1}, gif="facepalm"),
    ])

# ================================================================== 2. Midnight Canteen Heist (Charisma and luck)
CANTEEN_HEIST = _adv(
    "canteen_heist", "Midnight Canteen Heist", "🍩",
    "A harmless hostel mission: one legendary jalebi tray, one very serious night guard.",
    20, (3, 4, 5, 6, 7), "plan", nodes=[
        Node("plan", "Lights out in the hostel. Your gang meets under a blanket fort with one mission: the canteen's "
                     "legendary midnight jalebi tray. The only obstacle is Mr. Pillai, the night guard, who takes his "
                     "rounds very seriously.",
             (C("Sweet-talk a senior into lending the key", check=K("charisma", 2, "door", "window", win_sets=("key",))),
              C("Take the back window route", to="window"),
              C("Build a pillow decoy for your bunk", to="window", sets=("decoy",)))),
        Node("door", "The key turns without a squeak. Inside, the canteen smells of cardamom and sugar. Between you and "
                     "the kitchen lies a floor of old, very talkative tiles.",
             (C("Tiptoe along the quiet edge", check=K("fitness", 1, "kitchen", "guard")),
              C("Gamble on the middle tiles", check=K("luck", 2, "kitchen", "guard")))),
        Node("window", "You wriggle through the back window and land in a sack of flour. Whump! A small white cloud puffs "
                       "up. Somewhere, a floorboard answers.",
             (C("Shake it off and sneak on", check=K("luck", 2, "kitchen", "guard", bonus=(("decoy", 8),))),
              C("Act like a very official night cook", check=K("charisma", 3, "kitchen", "guard")))),
        Node("kitchen", "The jalebi tray glows under the emergency lamp like treasure. Keys jingle in the corridor. "
                        "Mr. Pillai is coming this way.",
             (C("Grab the whole tray and run", check=K("fitness", 2, "end_feast", "guard")),
              C("Grab a handful and slip away", to="end_snack"),
              C("Duck into the pantry", to="hide"))),
        Node("hide", "In the dark pantry you hear Mr. Pillai talking to himself: 'Every night I say just one jalebi. And "
                     "every night...' He sighs happily. He does not sound angry at all.",
             (C("Step out and offer to share", check=K("charisma", 2, "end_deal", "end_caught")),
              C("Stay very still and hope", check=K("luck", 2, "end_snack", "end_caught")))),
        Node("guard", "A torch beam finds you. 'Aha!' says Mr. Pillai, one eyebrow raised. 'Students in my canteen at "
                      "midnight. Explain.' Flour is still falling off your sleeve.",
             (C("Charm him: 'We came to say thanks!'", check=K("charisma", 3, "end_deal", "end_caught", bonus=(("key", 6),))),
              C("Confess and offer to wash dishes", check=K("luck", 1, "end_deal", "end_caught")),
              C("Make a run for the window", check=K("fitness", 2, "end_snack", "end_caught")))),
        _end("end_feast", "You sprint out with the whole tray held high and share it with the entire hostel under the "
                          "blanket fort. By morning the legend of the Midnight Jalebi Run is told on every floor.",
             title="Midnight Legend", mood="great", diamonds=30, stats={"charisma": 1, "fitness": 1, "creativity": 1},
             trophy=Trophy("midnight_legend", "title", "Midnight Legend"), gif="dance"),
        _end("end_deal", "Mr. Pillai chuckles, pushes the tray toward you and says, 'Half for you, half for me, and we "
                         "never speak of this.' He even shares his secret recipe for the syrup.",
             title="A Deal with the Guard", mood="good", diamonds=22, stats={"charisma": 2},
             trophy=Trophy("guards_recipe", "item", "Mr. Pillai's Secret Recipe"), gif="wink"),
        _end("end_snack", "You escape with a few sticky jalebis and a flour-covered hoodie. Not exactly a feast, but "
                          "your gang agrees it was worth every crumb.",
             title="A Few Sticky Jalebis", mood="ok", diamonds=12, stats={"fitness": 1, "charisma": 1}, gif="laugh"),
        _end("end_caught", "Mr. Pillai marches you back to the hostel, then hands you a sponge. Dish duty it is. It turns "
                           "out the sink is a great place to make new friends.",
             title="Dish Duty", mood="bad", diamonds=4, stats={"charisma": 1}, gif="facepalm"),
    ])

# ================================================================== 3. The Missing Question Paper (a small mystery with clues)
MISSING_PAPER = _adv(
    "missing_paper", "The Missing Question Paper", "🔎",
    "The sealed paper vanished from the staff room. Collect clues and name the culprit... carefully.",
    15, (2, 3, 4, 5, 6), "office", nodes=[
        Node("office", "Principal Mehta hurries over, waving an empty folder. 'The sealed Midterm paper has vanished from "
                       "the staff room! Nobody is in trouble yet, but I need a detective. Where do you start?'",
             (C("Search the staff room", to="staffroom"),
              C("Have a quiet word with Mr. Das", to="gardener"))),
        Node("staffroom", "The staff room is tidy except for a few odd things: a smudge on the desk, a scrap of tape in "
                          "the bin and a chair pushed a little too far back.",
             (C("Look closely at the smudge", check=K("knowledge", 1, "corridor", "corridor", win_sets=("glitter",))),
              C("Fish the tape out of the bin", check=K("luck", 2, "corridor", "corridor", win_sets=("tape",))),
              C("Skip ahead to the corridor", to="corridor"))),
        Node("gardener", "Mr. Das is watering the marigolds and humming nervously. 'I saw nothing,' he says, a bit too "
                         "quickly. A cartoon plaster covers his thumb.",
             (C("Ask kindly about his morning", check=K("charisma", 2, "corridor", "corridor", win_sets=("alibi",))),
              C("Press him hard about the plaster", to="end_wrong"))),
        Node("corridor", "Outside the staff room the floor is dusted with something that sparkles in the light. A few "
                         "tiny footprints and bits of tape head toward the far end of the corridor.",
             (C("Follow the sparkle trail", to="artroom", needs=("glitter",)),
              C("Follow the bits of tape", to="artroom", needs=("tape",)),
              C("Study the footprints", check=K("knowledge", 2, "artroom", "board", win_sets=("trail",))),
              C("Gather what you have on the case board", to="board"))),
        Node("artroom", "The Art Room is full of paint pots and Fest banners. A cheerful Art Club member points at a "
                        "drying backdrop. 'We borrowed the big paper roll from the staff room! Was there a folder in it?'",
             (C("Ask them to check the roll with you", check=K("charisma", 2, "end_solved", "board")),
              C("Search the paper roll yourself",
                check=K("luck", 2, "end_lucky", "board", bonus=(("glitter", 6), ("tape", 6), ("trail", 8)))))),
        Node("board", "You pin your clues to the case board and the whole staff room waits. It is time to name the "
                      "culprit, or to admit you need more time.",
             (C("Accuse Mr. Das, the gardener", to="end_wrong"),
              C("Lead everyone along the sparkle trail", check=K("charisma", 3, "end_solved", "end_partial",
                                                                   bonus=(("tape", 4), ("alibi", 4))), needs=("glitter",)),
              C("Say you need more time", to="end_partial"))),
        _end("end_solved", "Case closed! The sealed folder is found tucked inside the backdrop roll the Art Club borrowed "
                           "for Fest. Nobody did anything wrong: it was a mix-up. Principal Mehta shakes your hand.",
             title="Master Detective", mood="great", diamonds=30, stats={"knowledge": 2, "charisma": 1},
             trophy=Trophy("master_detective", "title", "Master Detective"), gif="highfive"),
        _end("end_lucky", "You rummage through the roll and out slides the sealed folder. You did not exactly deduce it, "
                          "but nobody needs to know that. Detectives need luck too.",
             title="A Lucky Find", mood="good", diamonds=20, stats={"knowledge": 1, "creativity": 1}, gif="laugh"),
        _end("end_partial", "You run out of clues. The next morning the paper turns up in the Art Room, all by itself. "
                            "The principal thanks you for trying and offers you a biscuit.",
             title="Case Still Mysterious", mood="ok", diamonds=12, stats={"knowledge": 1}),
        _end("end_wrong", "Oops. Mr. Das was only nervous because he is secretly making a birthday card for the "
                          "principal. You apologise, and he forgives you with a marigold.",
             title="Wrong Suspect", mood="bad", diamonds=4, stats={"charisma": 1},
             trophy=Trophy("dass_marigold", "item", "Mr. Das's Marigold"), gif="facepalm"),
    ])

# ================================================================== 4. Rooftop Concert (Fest Day, Music and Creativity)
ROOFTOP_CONCERT = _adv(
    "rooftop_concert", "Rooftop Concert", "🎸",
    "The Fest stage lost power, so the music moves to the roof. Sunset, a guitar and a growing crowd.",
    25, (5,), "roof", stream_bonus={"music": 5}, nodes=[
        Node("roof", "Fest Day sunset. The main stage lost power, so half the campus drifted up to the Science Block roof, "
                     "where you have a guitar, a speaker and about ten minutes of golden light.",
             (C("Build a stage from desks and lights", check=K("creativity", 2, "soundcheck", "soundcheck", win_sets=("stage",))),
              C("Round up the band on the group chat", check=K("charisma", 2, "soundcheck", "soundcheck", win_sets=("band",))),
              C("Keep it tiny: one guitar, no fuss", to="unplugged"))),
        Node("soundcheck", "The amp coughs, hums, then rings out one perfect chord. Students crowd the stairwell door, "
                           "phones glowing like fireflies. It is time to open.",
             (C("Open with your own song", check=K("creativity", 3, "hit", "flop", bonus=(("stage", 6), ("band", 4)))),
              C("Open with a song everyone knows", check=K("charisma", 2, "hit", "flop", bonus=(("band", 6), ("stage", 2)))))),
        Node("unplugged", "Just you, one guitar and an orange sky. A few classmates sit down on the warm concrete. "
                          "Somebody hums along from the back.",
             (C("Play a quiet original", check=K("creativity", 2, "hit", "end_duet")),
              C("Ask the roof to sing along", check=K("charisma", 2, "hit", "end_duet")))),
        Node("hit", "The roof erupts! Phones go up, a drummer appears from nowhere and somebody starts a clapping beat. "
                    "The sun melts into the horizon. Your next move decides the finale.",
             (C("Go big: a key-change finale", check=K("creativity", 3, "end_headliner", "end_crowd", bonus=(("stage", 4),))),
              C("Slow down: a sunset ballad", check=K("creativity", 2, "end_crowd", "end_duet")),
              C("Hand the mic to the crowd", check=K("charisma", 2, "end_crowd", "end_duet")))),
        Node("flop", "A cable pops loose and the speaker lets out a squeal that startles every pigeon in the district. "
                     "The crowd winces... then someone giggles kindly.",
             (C("Laugh along and make it a beat", check=K("charisma", 2, "recover", "end_flop")),
              C("Pack up quietly", to="end_flop"))),
        Node("recover", "Your squeal-beat catches on! The crowd claps the rhythm, and a friend on the stairs adds a cowbell "
                        "from somewhere. You are back in the game.",
             (C("Improvise a brand-new song", check=K("creativity", 3, "end_crowd", "end_duet")),
              C("Keep the beat simple and smile", to="end_duet"))),
        _end("end_headliner", "The key change lands perfectly. The whole roof sings, the fairy lights sway and the Fest "
                              "organisers announce you as the surprise headliner of the night.",
             title="Rooftop Headliner", mood="great", diamonds=30, stats={"creativity": 2, "charisma": 1},
             trophy=Trophy("rooftop_headliner", "title", "Rooftop Headliner"), gif="dance"),
        _end("end_crowd", "The crowd sings every word back to you. When the last chord fades, a first-year hands you a "
                          "string of fairy lights as a thank-you.",
             title="Crowd Pleaser", mood="good", diamonds=20, stats={"creativity": 1, "charisma": 1},
             trophy=Trophy("fairy_lights", "item", "String of Fairy Lights"), gif="highfive"),
        _end("end_duet", "It stays small and sweet: you, your guitar and a friend on a borrowed tambourine. Nobody "
                         "remembers the setlist, but everybody remembers the sunset.",
             title="Sunset Duet", mood="ok", diamonds=12, stats={"creativity": 1}, gif="wink"),
        _end("end_flop", "You pack up with a red face. By dinner, though, the whole campus is laughing about 'The Squeal "
                         "Heard Round Campus', and somebody asks for an encore of the squeal.",
             title="The Great Squeal", mood="bad", diamonds=4, stats={"charisma": 1}, gif="laugh"),
    ])

# ================================================================== 5. Sports Day Showdown (Fest Day, Fitness)
SPORTS_SHOWDOWN = _adv(
    "sports_showdown", "Sports Day Showdown", "🏅",
    "Blue House against Green House: a relay, a tug-of-war and one last sprint.",
    25, (5,), "warmup", stream_bonus={"sports": 5}, nodes=[
        Node("warmup", "Fest Day on the sports ground! Blue House faces Green House in the big Showdown: a relay, a "
                       "tug-of-war and one last sprint. Your captain points at you. 'You anchor the team. Ready?'",
             (C("Do a proper warm-up", check=K("fitness", 1, "relay", "relay", win_sets=("warm",))),
              C("Lead a loud team chant", check=K("charisma", 1, "relay", "relay", win_sets=("spirit",))),
              C("Eat one giant samosa first", to="relay", sets=("samosa",)))),
        Node("relay", "The relay! Your teammate sprints in with the baton and shouts your name. The Green House runner is "
                      "half a step ahead.",
             (C("Take off at full speed", check=K("fitness", 2, "tug", "drop", bonus=(("warm", 6), ("samosa", -6)))),
              C("Go for a smooth, careful handoff", check=K("charisma", 2, "tug", "drop", bonus=(("spirit", 6),))))),
        Node("drop", "The baton slips and clatters across the track. The crowd gasps, then goes 'ooooh'. Green House "
                     "sprints ahead.",
             (C("Scoop it up and chase them down", check=K("fitness", 3, "tug", "end_spirit", bonus=(("warm", 6),))),
              C("Wave to the crowd and finish with a grin", to="end_spirit"))),
        Node("tug", "Tug-of-war! Both Houses grab the rope. The ground is damp from last night's rain. Your captain "
                    "shouts, 'Anchor spot, that is you!'",
             (C("Dig in your heels and hold", check=K("fitness", 2, "final", "mud", bonus=(("spirit", 6), ("warm", 4)))),
              C("Call the rhythm: 'Heave! Heave!'", check=K("charisma", 2, "final", "mud", bonus=(("spirit", 4),))))),
        Node("mud", "The rope slips and the whole line tumbles into the mud. For a moment nobody moves. Then a Green "
                    "House kid starts laughing, and everybody joins in.",
             (C("Start a victory-in-the-mud dance", check=K("charisma", 2, "end_spirit", "end_mud")),
              C("Shake hands and call it a draw", to="end_spirit"))),
        Node("final", "It is all tied. Sudden death: one last 100-metre dash, you versus the Green House captain. The "
                      "whole field goes quiet, and somebody drops a samosa in the silence.",
             (C("Burn the track with a full sprint", check=K("fitness", 3, "end_champion", "end_silver",
                                                              bonus=(("warm", 6), ("samosa", -6)))),
              C("Suggest a shared victory lap", check=K("charisma", 2, "end_spirit", "end_silver", bonus=(("spirit", 4),))))),
        _end("end_champion", "You cross the line a heartbeat ahead! Blue House erupts, the captain lifts you onto "
                             "shoulders and even the Green House captain shakes your hand.",
             title="Sports Day Champion", mood="great", diamonds=30, stats={"fitness": 2, "charisma": 1},
             trophy=Trophy("sports_champion", "title", "Sports Day Champion"), gif="dance"),
        _end("end_silver", "A photo finish, and you are a nose behind. The silver medal looks great on you, and "
                           "the rematch is already planned for next Fest.",
             title="Silver at the Line", mood="good", diamonds=20, stats={"fitness": 1, "charisma": 1},
             trophy=Trophy("silver_medal", "item", "Silver Medal"), gif="highfive"),
        _end("end_spirit", "Nobody is quite sure who won, but everybody is smiling. The teachers give out House Spirit "
                           "ribbons and yours is the shiniest.",
             title="House Spirit", mood="ok", diamonds=12, stats={"charisma": 1, "fitness": 1},
             trophy=Trophy("spirit_ribbon", "item", "House Spirit Ribbon"), gif="wave"),
        _end("end_mud", "You are brown from head to toe, and the mud will not come out of your socks for a week. Still, "
                        "it is the best photo in the Fest album.",
             title="Mud Hero", mood="bad", diamonds=4, stats={"fitness": 1}, gif="laugh"),
    ])

ADVENTURES: dict[str, Adventure] = {a.key: a for a in (
    HAUNTED_LIBRARY, CANTEEN_HEIST, MISSING_PAPER, ROOFTOP_CONCERT, SPORTS_SHOWDOWN)}
ORDER: tuple[str, ...] = tuple(ADVENTURES)
