"""Extra flavour text for the game side: work, crime, dates, pet expeditions, heists, boss raids,
fishing and mining. Only text changes -- every reward / chance / cooldown stays exactly as it was."""

WORK_MORE = [
    "You herded sky-goats on the Cloud Pastures", "You carried crates at the Harbour of Lanterns", "You sold hot chai at the Crystal Market",
    "You polished armour for the royal guard", "You translated ancient scrolls for a scholar", "You rowed ferry passengers across the Moonlit River",
    "You fixed streetlamps in Ember Town", "You baked bread for the Dawn Bakery", "You trained messenger pigeons at the Sky Post",
    "You mapped unexplored caves for the Explorers' Guild", "You painted signs for the Merchant Quarter", "You sharpened swords at the blacksmith's forge",
    "You taught a dragon to fetch", "You tended the Glowing Gardens", "You sorted gemstones at the Jeweller's Guild",
    "You cleaned the fountains of the Great Plaza", "You guided pilgrims through the Mist Mountains", "You helped build a bridge over Thunder Gorge",
    "You played music for tips at the Golden Inn", "You repaired fishing nets by the Coral Docks", "You delivered letters through a snowstorm",
    "You scared off crows from the Royal Farm", "You taught kids to read at the village school", "You stacked shelves at the Alchemist's Emporium",
    "You caught runaway chickens for a farmer", "You carved statues for the Temple of Stars", "You steered a cart of lanterns through the night market",
    "You swept the dust off the old library", "You helped a wizard find his lost hat", "You guarded the festival fireworks",
]
CRIME_WIN_MORE = [
    "You swapped a merchant's scales when nobody looked", "You borrowed a gem from a sleeping dragon's hoard", "You sneaked into a noble's banquet and left with the silverware",
    "You forged a perfect trade licence", "You lifted a heavy purse from a distracted baron", "You tunnelled into the old treasury from below",
    "You talked your way past the gate guards with a fake invitation", "You rode away on a stolen carriage full of goods", "You emptied a smuggler's hidden stash",
    "You swapped a famous painting for a copy", "You tricked a gambler at the shadow table", "You cracked a safe with a hairpin and patience",
]
CRIME_LOSE_MORE = [
    "A guard dog sniffed you out", "Your disguise slipped at the worst moment", "The alarm bell rang louder than expected",
    "A child spotted you and shouted for help", "Your lock pick snapped in half", "A nosy neighbour called the watch",
    "You tripped over your own cloak", "The vault turned out to be empty and trapped", "A rival thief tipped off the patrol",
    "Your getaway horse refused to move", "A sneaky cat knocked over a vase and woke everyone",
]
DATE_MORE = [
    "You flew paper lanterns into the night sky 🏮", "You played cards at a cosy cafe ☕", "You watched the stars from a rooftop 🌌",
    "You took a boat ride on a quiet lake ⛵", "You shared a plate of pani puri 🥘", "You visited a night fair and won a plush toy 🧸",
    "You took silly photos in a photo booth 📸", "You went kite flying together 🪁", "You wrote each other little notes 💌",
    "You had a picnic under a big old tree 🧺", "You sang along to old songs on a long drive 🎶", "You planted a tiny tree together 🌱",
    "You solved a puzzle side by side 🧩", "You tried each other's favourite food 🍜", "You shared one umbrella in the rain ☔",
    "You made wishes by the fountain ⛲", "You went to a local festival and danced 🎊", "You baked cookies and burnt half of them 🍪",
    "You watched the sunrise together 🌄", "You built a blanket fort and told stories 🏕",
]
EXP_TALES_MORE = {
    "scout": [
        "{p} found a shiny pouch tucked inside a hollow log.", "{p} followed a butterfly and stumbled on a hidden cache.",
        "{p} dug up a buried chest beside a mossy stone.", "{p} helped a lost fox and received a reward.",
        "{p} chased a glowing firefly to a stash of coins.", "{p} swam a stream and returned with a handful of gems.",
        "{p} impressed a forest spirit and got a small tip.",
    ],
    "trek": [
        "{p} climbed a steep cliff and reached a forgotten shrine.", "{p} befriended a mountain goat who led it to a hidden cave.",
        "{p} found a trader's lost backpack on a snowy pass.", "{p} sniffed out a vein of crystals in the rock.",
        "{p} crossed a rope bridge and discovered an old camp.", "{p} warmed up a stranded traveller and was paid well.",
        "{p} spotted something glittering in an ice cave.",
    ],
    "voyage": [
        "{p} rode a dolphin to a secret lagoon full of pearls.", "{p} outsmarted a sea serpent and kept the treasure.",
        "{p} found a message in a bottle with a treasure map.", "{p} danced with mermaids and earned a handful of shells.",
        "{p} dived into a shipwreck and surfaced with gold.", "{p} steered through a storm and brought back a rich catch.",
        "{p} befriended a giant turtle who carried it to a glittering reef.",
    ],
}
HEIST_TARGETS_MORE = [
    "the Starlight Auction House", "the Emerald Tower", "the Phantom Opera House", "the Dragon Bank of Embergate",
    "the Floating Library of Skyrock", "the Silk Road Caravan", "the Crystal Palace treasury", "Duchess Vale's jewel vault", "the Moonshadow Museum",
]
BOSS_MORE = [
    ("🦅", "Storm Roc"), ("🕷", "Web Queen"), ("🐺", "Frost Wolf Alpha"), ("🧙", "Rogue Archmage"), ("🦑", "Deep Kraken"),
    ("🗿", "Stone Golem"), ("🐉", "Thunder Wyrm"), ("🦇", "Night Vampire Lord"),
]

# Extra texts for the fish / mine tables. key = the ORIGINAL row text; the row's chance is shared evenly between the
# original and its variants, and min / max rewards are unchanged -- so the economy is exactly the same as before.
FISH_EXTRA = {
    "👢 You reeled in an old boot. Better luck next time!": [
        "🥫 You hooked a rusty tin can. Better luck next time!", "🌿 You only caught a tangle of seaweed. Better luck next time!",
        "🧦 You pulled up a soggy sock. Better luck next time!",
    ],
    "🐟 You caught a nice fish": ["🐟 You caught a shiny silver fish", "🐡 You pulled up a grumpy pufferfish", "🎣 You reeled in a fat river carp", "🐟 You landed a tasty rohu"],
    "🐠 You caught a colourful tropical fish": ["🐠 You netted a glittering angelfish", "🦐 You scooped up a basket of big prawns", "🐠 You caught a rainbow reef fish"],
    "🦈 You wrestled a shark": ["🐊 You wrestled a sneaky crocodile fish", "🦈 You out-swam a hammerhead and took its catch", "🐟 You hauled in a giant swordfish"],
    "🐙 You landed a giant octopus": ["🦞 You landed a gigantic lobster", "🐙 You caught an octopus holding a pearl"],
}
MINE_EXTRA = {
    "🪨 Only plain rocks this time.": ["🪨 Just dust and gravel this time.", "🪨 You hit solid granite and found nothing.", "🕳 The tunnel was empty today."],
    "⛏ You dug up some iron ore": ["⛏ You found a lump of copper ore", "⛏ You struck a rich vein of coal", "⛏ You dug up some silver ore"],
    "💎 You found a diamond vein": ["💎 You uncovered a glittering crystal cluster", "💎 You found a vein of rough diamonds", "💚 You dug up a handful of emeralds"],
    "💠 You struck a sapphire": ["💠 You found a deep-blue sapphire", "❤️ You dug out a ruby the size of a coin"],
    "💎 You unearthed a raw diamond": ["💎 You pulled out a flawless raw diamond", "💎 You cracked open a geode full of diamonds"],
}
