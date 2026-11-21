"""Word packs for Word Jumble (pure data).

THEMES: themed puzzle words (4, 5 and 6 letters) - every theme has at least 10 words per length.
EXTRA : everyday 7 and 8-letter words for the Extreme difficulty (the WordSeek dictionary only goes up to 6 letters).
Add words by appending to the strings; tests/test_jumble_pure.py checks lengths, spelling (letters only) and duplicates."""
from __future__ import annotations


def _w(s: str) -> tuple[str, ...]:
    return tuple(s.split())


THEMES: dict[str, dict[int, tuple[str, ...]]] = {
    "animals": {
        4: _w("bear wolf frog duck goat lion seal crab deer fish swan hawk mole lamb toad bull dove mule boar hare"),
        5: _w("horse zebra tiger mouse snake sheep camel panda otter eagle shark whale koala goose moose bison llama robin finch hyena lemur skunk squid"),
        6: _w("monkey rabbit donkey parrot turtle spider lizard falcon beaver badger salmon walrus jaguar weasel puffin python toucan iguana gibbon marmot"),
    },
    "food": {
        4: _w("rice cake milk soup corn meat bean pear plum lime kiwi tuna tart wine beer oats stew nuts figs leek"),
        5: _w("bread pasta apple grape lemon mango peach pizza salad honey sugar onion olive melon berry juice candy chili curry steak toast wheat cocoa"),
        6: _w("butter cheese banana cherry orange potato tomato carrot noodle cookie pepper coffee muffin burger garlic almond walnut yogurt pickle waffle"),
    },
    "countries": {
        4: _w("chad cuba iran iraq mali laos togo fiji peru oman"),
        5: _w("india japan spain italy china egypt ghana kenya qatar yemen syria haiti chile sudan malta nepal wales tonga samoa benin gabon libya niger"),
        6: _w("france brazil canada mexico sweden norway turkey israel jordan panama poland cyprus greece russia uganda serbia rwanda zambia bhutan angola gambia guinea latvia monaco taiwan"),
    },
    "nature": {
        4: _w("tree rain wind fire moon star sand rock lake leaf snow hill wave seed mist dust pond reef root"),
        5: _w("river cloud stone plant ocean beach storm grass flame earth coral frost shore cliff field creek flora"),
        6: _w("forest flower island desert valley meadow planet jungle canyon spring summer winter autumn breeze sunset stream season"),
    },
    "home": {
        4: _w("sofa lamp door bath desk bowl fork wall roof oven sink bulb vase iron shed tile"),
        5: _w("chair table couch house clock plate spoon knife fence floor shelf stair broom glass brush razor towel"),
        6: _w("window bottle carpet pillow mirror ladder garden pantry closet hammer bucket basket candle drawer kettle teapot"),
    },
}

EXTRA: dict[int, tuple[str, ...]] = {
    7: _w(
        "balance brother chicken country courage diamond dolphin evening factory fashion freedom journey kitchen library machine monster morning mystery network picture problem pumpkin rainbow science station stomach teacher thunder traffic trouble village weather whistle history holiday healthy hunting lantern message nothing painter perfect quality captain cabinet cottage curtain explore gallery harvest imagine jewelry mission musical penguin reading shelter silence swimmer theater tourist vampire wedding airport bedroom"),
    8: _w(
        "absolute airplane birthday blizzard bluebird building business calendar champion computer creature daylight elephant exercise firework football function generous hospital internet language magazine mountain notebook painting pancakes passport pleasure princess question research sandwich shoulder sunshine survival swimming teaspoon umbrella universe vacation backpack bathroom bracelet daughter dinosaur discover envelope favorite gardener homework keyboard learning majestic mushroom paradise treasure triangle wardrobe wireless"),
}
