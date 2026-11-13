"""Texts for engagement / wedding / anniversary (and the later gift / honeymoon / quest features).
Pure data - plain text only, no HTML tags and no raw < > & (handlers wrap it in their own markup)."""

# Short vows shown on the wedding card (picked at random per ceremony).
VOWS = [
    "I promise to share my last slice, my best jokes and every sunrise with you.",
    "I will cheer for your wins, hold your hand through the bad days, and never steal the blanket. Much.",
    "With you, every ordinary day feels like a festival.",
    "I choose you today, tomorrow, and in every quest still to come.",
    "You are my favourite person, my safest place and my loudest laugh.",
    "I promise to always save you the last diamond.",
    "Wherever the road leads, I want to walk it beside you.",
    "You make my world brighter than a royal treasury ever could.",
]

# venue key -> (scene line for the card, guests line)
VENUE_SCENES = {
    "simple": ("A quiet registry office, two witnesses and a very happy clerk.", "Guests: close friends and one emotional pigeon."),
    "garden": ("Under a flower arch in a lantern-lit garden, petals drifting in the breeze.", "Guests: family, friends and a string quartet of crickets."),
    "royal": ("In the grand hall of the Royal Palace, trumpets sounding as the doors swing open.", "Guests: nobles, knights, dragons in their best armour and half the kingdom."),
}

ENGAGE_PROPOSAL = [
    "is down on one knee with a sparkling ring",
    "hid a ring in a cake and you found it",
    "wrote your names in the sky with fireworks",
    "is holding out a ring with shaking hands",
]

ENGAGED_ACCEPT = [
    "said YES! The ring is on and everyone is crying happy tears.",
    "said yes before the question was even finished.",
    "said yes, and the whole chat is cheering.",
]

# Greeting DM sent by the anniversary loop. {years} {ordinal} {bonus} are filled in by the loop.
ANNIV_GREETINGS = [
    "Happy {ordinal} wedding anniversary!",
    "{years} year{plural} since the big day - congratulations!",
    "Another year, another reason to celebrate: happy {ordinal} anniversary!",
]


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


# ================================================================== Part B: gifts & love letters
# gift key -> reaction lines (plain text)
GIFT_REACTIONS = {
    "flowers": ["A bouquet that smells like spring.", "Fresh petals, fresher smiles.", "Wrapped in ribbon and a little bit of nervousness."],
    "chocolate": ["Sweet, smooth and gone in five minutes.", "A box of chocolates, half of it already missing.", "Dark, milk or both? Both."],
    "teddy": ["A huge, fluffy teddy bear with a tiny bow tie.", "It hugs back, which is more than most people do.", "The teddy has already been given a name."],
    "cake": ["A three-layer cake with candles and a very wobbly 'Congrats'.", "Cake! Nobody is sharing this one.", "Frosting everywhere, happiness everywhere."],
}

LETTER_OPENERS = ["With all my heart", "Written just for you", "A little note from me", "Sealed with a kiss"]

# ================================================================== Part C: honeymoon
# destination key -> (emoji, name, [3 story chapters unlocked over the trip], finale shown when the trip ends)
HONEYMOON_DESTINATIONS = {
    "beach": ("🏝", "Coral Island",
              ["You step off the little boat onto warm white sand. The only footprints are yours.",
               "Midday: snorkelling over a reef full of glowing fish. A turtle swims beside you like it knows a secret.",
               "Sunset bonfire on the beach. Someone is playing a guitar, badly and wonderfully."],
              "You sail home sunburnt, sandy and completely in love."),
    "mountain": ("🏔", "Frostpeak Lodge",
                 ["A tiny wooden cabin, a roaring fireplace and snow falling past the window.",
                  "You climb to the viewpoint together. The whole valley glitters below you.",
                  "Hot chocolate under a sky so full of stars it hurts."],
                 "You head down the mountain holding hands, already planning the next trip."),
    "city": ("🌆", "Lantern City",
             ["Paper lanterns light every street as you arrive. The city smells of street food and music.",
              "You get lost on purpose in a night market and come back with way too many souvenirs.",
              "A rooftop dinner above the glowing skyline. The waiter pretends not to notice you staring at each other."],
             "You catch the late train home with sleepy heads on each other's shoulders."),
    "forest": ("🌲", "Whisperwood",
               ["You walk into an enchanted forest where the trees hum quietly when you pass.",
                "A hidden waterfall, a picnic blanket and a fox who steals one sandwich.",
                "Fireflies gather around your campfire as if they were invited."],
               "You leave the forest with pockets full of pinecones and a head full of memories."),
}

# couple quest tasks: (key, emoji, text, mode). mode 'both' = each partner must do it, 'either' = one of you is enough.
COUPLE_TASKS = [
    ("letter", "💌", "Send each other a /loveletter", "both"),
    ("gift", "🎁", "Send your partner a /sendgift", "either"),
    ("daily", "🎁", "Both claim your /daily reward", "both"),
    ("date", "💑", "Go on a /date together", "either"),
    ("work", "💼", "Both do some /work", "both"),
]

STREAK_LINES = ["Another day, another heart on the board.", "Together again today!", "The flame keeps burning.", "Love is a habit - and you have it."]
