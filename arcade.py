"""Arcade & social extras: scratch cards, rock-paper-scissors, tic-tac-toe, hangman, reputation,
horoscope / fortune, would-you-rather, riddles, facts and bank interest.

Rules of the house (same as the rest of the bot):
* every diamond change goes through economy.apply()  (atomic, ledgered, never negative)
* every cooldown goes through cooldowns.claim()       (atomic, two concurrent taps -> one winner)
* short-lived game state lives in the KV layer (Redis or in-memory), never in the game tables
* lifetime counters reuse the existing `user_stats` table -> no new tables, no migration, backups just work
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, timedelta

from sqlalchemy import func, select

from database.models import Transaction, User, UserStat, utcnow
from database.session import db
from game.rules import R
from services import cooldowns, economy, users
from services.errors import GameError, OnCooldown
from utils import content

_rng = random.SystemRandom()


async def _bump(s, uid: int, key: str, n: int = 1) -> None:
    from services import realm
    await realm.bump(s, uid, key, n)


async def stats_of(uid: int) -> dict[str, int]:
    from services import realm
    return await realm.stats_of(uid)


# =============================================================== scratch card
SCRATCH_COST = 100
SCRATCH_CD_S = 15
FILLERS = ["🍒", "🍋", "🍇", "💎", "👑", "🔔", "⭐", "🍀"]
# (weight in 1000ths, prize, matching symbol) -> return to player 97%
SCRATCH_TABLE = [(620, 0, ""), (200, 100, "🍒"), (100, 200, "🍋"), (50, 300, "🍇"), (24, 1000, "💎"), (6, 3000, "👑")]


def scratch_grid(symbol: str, rng: random.Random | None = None) -> list[str]:
    """6 cells. A winning card has EXACTLY three of the prize symbol; a losing card never has three of anything."""
    rng = rng or _rng
    cells = [symbol] * 3 if symbol else []
    pool = [f for f in FILLERS if f != symbol]
    while len(cells) < 6:
        c = rng.choice(pool)
        if cells.count(c) < 2:
            cells.append(c)
    rng.shuffle(cells)
    return cells


async def scratch_buy(uid: int, rng: random.Random | None = None) -> tuple[list[str], int]:
    """Pays the prize immediately (the reveal in the chat is only cosmetic), so an abandoned card never costs anything extra."""
    rng = rng or _rng
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "scratch", SCRATCH_CD_S)
        if rem:
            raise OnCooldown(rem, "Scratch cards")
        _, prize, sym = rng.choices(SCRATCH_TABLE, [t[0] for t in SCRATCH_TABLE], k=1)[0]
        await economy.apply(s, uid, wallet=-SCRATCH_COST, kind="scratch_buy")
        if prize:
            await economy.apply(s, uid, wallet=prize, kind="scratch_win")
        await _bump(s, uid, "scratch")
        if prize:
            await _bump(s, uid, "scratch_win")
        return scratch_grid(sym, rng), prize


# =============================================================== rock paper scissors
RPS = {"r": "🪨 Rock", "p": "📄 Paper", "s": "✂️ Scissors"}
_BEATS = {"r": "s", "p": "r", "s": "p"}
RPS_WIN_MULT = 1.9          # win pays 1.9x the bet, tie refunds, loss forfeits -> small house edge, a net sink over time


def rps_outcome(me: str, bot: str) -> str:
    return "tie" if me == bot else ("win" if _BEATS[me] == bot else "lose")


async def rps_play(uid: int, pick: str, bet: int, rng: random.Random | None = None) -> tuple[str, str, int]:
    """Returns (bot pick, outcome, net diamonds). bet == 0 is a free practice round (no cooldown, no diamonds)."""
    if pick not in RPS:
        raise GameError("❌ Pick rock, paper or scissors.")
    rng = rng or _rng
    bot = rng.choice(list(RPS))
    res = rps_outcome(pick, bot)
    if bet <= 0:
        return bot, res, 0
    from services import casino
    casino._check_bet(bet)
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "casino", R.casino_cooldown_s)
        if rem:
            raise OnCooldown(rem, "The casino")
        if (await economy.wallet_of(s, uid)).balance < bet:
            raise GameError("❌ You don't have enough 💎 for that bet.")
        if res == "tie":
            return bot, res, 0
        await economy.apply(s, uid, wallet=-bet, kind="rps_bet")
        if res == "win":
            pay = int(bet * RPS_WIN_MULT)
            await economy.apply(s, uid, wallet=pay, kind="rps_win")
            return bot, res, pay - bet
        return bot, res, -bet


# =============================================================== tic-tac-toe (pure logic; state is kept by the handler in KV)
TTT_LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def ttt_winner(board: str) -> str | None:
    for a, b, c in TTT_LINES:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    return None


def ttt_over(board: str) -> str | None:
    """'x' / 'o' winner, 'draw', or None while the game goes on."""
    w = ttt_winner(board)
    if w:
        return w
    return "draw" if " " not in board else None


TTT_STAKE = 25            # the winner takes this many diamonds from the loser
TTT_PAIR_DAILY = 5        # per pair of players and day; after that games still count but no diamonds move (anti alt-farming)


async def ttt_check_funds(uid: int, name: str) -> None:
    """Both players must be able to pay the stake before a game starts."""
    if (await economy.balance(uid)).balance < TTT_STAKE:
        raise GameError(f"❌ <b>{name}</b> needs at least <b>{TTT_STAKE}</b> 💎 to play tic-tac-toe (the winner takes {TTT_STAKE}).")


async def ttt_record(winner: int | None, a: int, b: int) -> int:
    """Records the game; the winner wins TTT_STAKE 💎 from the loser. Returns the diamonds that moved."""
    moved = 0
    async with db.tx() as s:
        for uid in (a, b):
            await _bump(s, uid, "ttt_play")
        if winner:
            await _bump(s, winner, "ttt_win")
            loser = b if winner == a else a
            day = utcnow() - timedelta(days=1)
            played = (await s.execute(select(func.count()).select_from(Transaction).where(
                Transaction.kind == "ttt_in", Transaction.created_at >= day,
                ((Transaction.user_id == winner) & (Transaction.counterparty_id == loser)) |
                ((Transaction.user_id == loser) & (Transaction.counterparty_id == winner))))).scalar()
            if played < TTT_PAIR_DAILY:
                try:
                    moved = await economy.transfer_upto(s, loser, winner, TTT_STAKE, "ttt")
                except GameError:
                    moved = 0
    return moved


# =============================================================== hangman
HANGMAN_CD_S = 120
HANGMAN_LIVES = 6
WORDS: dict[str, list[str]] = {
    "🐾 Animal": ["ELEPHANT", "GIRAFFE", "PENGUIN", "DOLPHIN", "KANGAROO", "CHEETAH", "PEACOCK", "TORTOISE", "BUTTERFLY", "CROCODILE", "OCTOPUS", "HEDGEHOG"],
    "🍕 Food": ["PANCAKE", "BIRYANI", "CHOCOLATE", "SANDWICH", "NOODLES", "MANGO", "WATERMELON", "SAMOSA", "PIZZA", "ICECREAM", "POPCORN", "STRAWBERRY"],
    "🌍 Place": ["MOUNTAIN", "VOLCANO", "ISLAND", "DESERT", "WATERFALL", "BHUBANESWAR", "AUSTRALIA", "PYRAMID", "HIMALAYA", "RAINFOREST", "LIGHTHOUSE", "CANYON"],
    "💻 Tech": ["KEYBOARD", "TELEGRAM", "INTERNET", "SMARTPHONE", "ALGORITHM", "BROWSER", "PASSWORD", "ROBOT", "SATELLITE", "BLUETOOTH", "CAMERA", "PROGRAM"],
    "⚽ Sport": ["CRICKET", "FOOTBALL", "BADMINTON", "HOCKEY", "TENNIS", "BASKETBALL", "SWIMMING", "WRESTLING", "ARCHERY", "MARATHON", "KABADDI", "VOLLEYBALL"],
}
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def hangman_pick(rng: random.Random | None = None) -> tuple[str, str]:
    rng = rng or _rng
    cat = rng.choice(list(WORDS))
    return cat, rng.choice(WORDS[cat])


def hangman_mask(word: str, guessed: str) -> str:
    return " ".join(ch if ch in guessed else "＿" for ch in word)


def hangman_reward(lives_left: int) -> int:
    return 30 + 20 * max(0, min(HANGMAN_LIVES, lives_left))


async def hangman_start(uid: int) -> None:
    async with db.tx() as s:
        rem = await cooldowns.claim(s, uid, "hangman", HANGMAN_CD_S)
        if rem:
            raise OnCooldown(rem, "Hangman")


async def hangman_finish(uid: int, gid: str, won: bool, lives_left: int) -> int:
    """Pays the winner (idempotent per game id) and records the result. Returns diamonds won."""
    amount = hangman_reward(lives_left) if won else 0
    async with db.tx() as s:
        await _bump(s, uid, "hangman_play")
        if won:
            await _bump(s, uid, "hangman_win")
            await economy.apply(s, uid, wallet=amount, kind="hangman", ref=f"hang:{gid}")
    return amount


# =============================================================== reputation
REP_CD_S = 12 * 3600


async def give_rep(giver: int, target: int) -> int:
    """One rep point per giver every 12 h, never to yourself. Returns the receiver's new total."""
    if giver == target:
        raise GameError("😅 You can't give reputation to yourself.")
    async with db.tx() as s:
        await users.require(s, target)
        rem = await cooldowns.claim(s, giver, "rep_give", REP_CD_S)
        if rem:
            raise OnCooldown(rem, "Giving reputation")
        await _bump(s, target, "rep")
        from services import realm
        return await realm.stat(s, target, "rep")


async def rep_cooldown(uid: int) -> int:
    async with db.tx() as s:
        return await cooldowns.remaining(s, uid, "rep_give")


async def rep_of(uid: int) -> int:
    return (await stats_of(uid)).get("rep", 0)


async def top_rep(limit: int = 10) -> list[tuple[int, str, int]]:
    async with db.tx() as s:
        rows = (await s.execute(
            select(UserStat.user_id, User.first_name, UserStat.value)
            .join(User, User.id == UserStat.user_id)
            .where(UserStat.key == "rep", UserStat.value > 0, User.is_flagged == False, User.is_banned == False)  # noqa: E712
            .order_by(UserStat.value.desc(), UserStat.user_id).limit(limit))).all()
        return [(r[0], r[1], r[2]) for r in rows]


async def top_rep_page(page: int = 0, per: int = 10) -> tuple[list[tuple[int, str, int]], int]:
    """One page of the reputation board -> (rows, total players on the board)."""
    ok = (UserStat.key == "rep", UserStat.value > 0, User.is_flagged == False, User.is_banned == False)  # noqa: E712
    async with db.tx() as s:
        total = (await s.execute(select(func.count()).select_from(UserStat).join(User, User.id == UserStat.user_id).where(*ok))).scalar() or 0
        rows = (await s.execute(select(UserStat.user_id, User.first_name, UserStat.value).join(User, User.id == UserStat.user_id)
                                .where(*ok).order_by(UserStat.value.desc(), UserStat.user_id).limit(per).offset(max(0, page) * per))).all()
        return [(r[0], r[1], r[2]) for r in rows], int(total)


# =============================================================== bank interest
INTEREST_RATE = 0.01
INTEREST_CAP = 500
INTEREST_MIN_BANK = 1000
INTEREST_CD_S = 22 * 3600


def interest_for(bank: int) -> int:
    return min(INTEREST_CAP, int(bank * INTEREST_RATE))


async def claim_interest(uid: int) -> tuple[int, int]:
    """Returns (interest paid into the bank, new bank total). Capped, once per ~day: a gentle reward for saving."""
    async with db.tx() as s:
        w = await economy.wallet_of(s, uid)
        bank_before = w.bank                      # read first: the ORM row is synchronised by the UPDATE below
        if bank_before < INTEREST_MIN_BANK:
            raise GameError(f"🏦 Interest starts at <b>{INTEREST_MIN_BANK:,}</b> 💎 in your bank. Move some with /deposit.")
        rem = await cooldowns.claim(s, uid, "interest", INTEREST_CD_S)
        if rem:
            raise OnCooldown(rem, "Bank interest")
        amt = interest_for(bank_before)
        await economy.apply(s, uid, bank=amt, kind="interest", ref=f"interest:{uid}:{utcnow():%Y%m%d%H%M%S}")
        return amt, bank_before + amt


# =============================================================== horoscope & fortune (deterministic: same sign = same reading all day)
SIGNS = [("♈", "Aries"), ("♉", "Taurus"), ("♊", "Gemini"), ("♋", "Cancer"), ("♌", "Leo"), ("♍", "Virgo"),
         ("♎", "Libra"), ("♏", "Scorpio"), ("♐", "Sagittarius"), ("♑", "Capricorn"), ("♒", "Aquarius"), ("♓", "Pisces")]
_MOOD = ["Your energy is sky-high today ⚡", "A calm, thoughtful day awaits 🌿", "You'll feel unusually confident 😎", "Today suits slow, cozy plans ☕",
         "Curiosity leads you somewhere fun 🧭", "People will naturally listen to you 🎤", "A small surprise brightens your afternoon 🎁"]
_LOVE = ["Someone is thinking about you right now 💭", "Say the kind thing you've been holding back 💌", "A friendship gets stronger today 🤝",
         "Old chat, new spark ✨", "Flirty vibes — but keep it classy 😉", "Spend time with family, it will feel good 🏠", "Self-love first: treat yourself 💖"]
_MONEY = ["A tiny saving habit pays off 💰", "Avoid impulse buys after 6 pm 🛒", "Good day to ask for what you deserve 📈",
          "Someone owes you a favour 🔄", "Luck favours a small risk — keep it small 🎲", "Sell what you no longer need 📦", "Patience beats speed with money today 🐢"]
_TIPS = ["Drink water 💧", "Text an old friend 📱", "Take a 10-minute walk 🚶", "Back up your phone 💾", "Sleep early tonight 😴", "Learn one new word 📖", "Tidy your desk 🧹"]
_COLORS = ["Red", "Blue", "Green", "Yellow", "Purple", "Orange", "Pink", "White", "Black", "Teal", "Gold", "Silver"]
# Bigger pools (originals stay first) so readings stop repeating; appended, never reordered.
_MOOD += content.MOOD_MORE
_LOVE += content.LOVE_MORE
_MONEY += content.MONEY_MORE
_TIPS += content.TIPS_MORE
_COLORS += content.COLORS_MORE
_CAREER = content.CAREER
_SIGN_VIBE = content.SIGN_VIBE
_LUCKY_TIMES = content.LUCKY_TIMES


def _seeded(*parts: object) -> random.Random:
    h = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def horoscope(sign: str, day: date | None = None) -> str:
    day = day or utcnow().date()
    emoji = next(e for e, n in SIGNS if n.lower() == sign.lower())
    name = next(n for e, n in SIGNS if n.lower() == sign.lower())
    r = _seeded("horo", name, day.isoformat())
    stars = lambda: "⭐" * r.randint(2, 5)  # noqa: E731
    mood, vibe = r.choice(_MOOD), r.choice(_SIGN_VIBE[name])
    love, love_s = r.choice(_LOVE), stars()
    money, money_s = r.choice(_MONEY), stars()
    career, career_s = r.choice(_CAREER), stars()
    tip, colour, number = r.choice(_TIPS), r.choice(_COLORS), r.randint(1, 99)
    when = r.choice(_LUCKY_TIMES)
    m_emoji, m_name = r.choice([x for x in SIGNS if x[1] != name])
    return (f"{emoji} <b>{name}</b> · {day:%d %b %Y}\n\n🌤 {mood}\n✨ {vibe}\n\n❤️ {love}  {love_s}\n"
            f"💰 {money}  {money_s}\n💼 {career}  {career_s}\n\n💡 Tip: {tip}\n\n"
            f"🎨 Lucky colour: <b>{colour}</b>   🔢 Lucky number: <b>{number}</b>\n"
            f"⏰ Lucky time: <b>{when}</b>   {m_emoji} Best match today: <b>{m_name}</b>")


def find_sign(text: str) -> str | None:
    t = (text or "").strip().lower()
    for e, n in SIGNS:
        if t in (n.lower(), n.lower()[:3]) or t == e:
            return n
    return None


_FORTUNES = ["A pleasant surprise is on its way 🎁", "Your next idea will be a good one 💡", "Someone admires you more than you know 💖",
             "Today's small effort becomes tomorrow's win 🏆", "Say yes to the invitation you were about to refuse 🎟", "Good news arrives by message 📩",
             "A closed door was protecting you 🚪", "Your patience is about to be rewarded ⏳", "Laugh at yourself today; it will make friends 😄",
             "The answer you want is simpler than you think 🔑", "A new friend is closer than you expect 🤗", "You are one step ahead — keep going 🚀"]
_FORTUNES += content.FORTUNES_MORE


def fortune(uid: int, day: date | None = None) -> str:
    day = day or utcnow().date()
    r = _seeded("fortune", uid, day.isoformat())
    nums = ", ".join(str(n) for n in sorted(r.sample(range(1, 50), 5)))
    return f"🥠 <b>Your fortune for {day:%d %b}</b>\n\n<i>{r.choice(_FORTUNES)}</i>\n\n🍀 Lucky numbers: <b>{nums}</b>\n<i>A new cookie every day.</i>"


# =============================================================== content: would-you-rather, riddles, facts
WYR: list[tuple[str, str]] = [
    ("Have unlimited pizza for life", "Have unlimited ice cream for life"), ("Be able to fly", "Be able to turn invisible"),
    ("Always be 10 minutes late", "Always be 20 minutes early"), ("Lose your phone for a month", "Lose your wallet for a month"),
    ("Live in the mountains", "Live by the beach"), ("Know how you die", "Know when you die"),
    ("Speak every language", "Talk to animals"), ("Have a rewind button for your life", "Have a pause button for your life"),
    ("Never use social media again", "Never watch a movie again"), ("Be a famous singer", "Be a famous cricketer"),
    ("Eat only sweet food forever", "Eat only spicy food forever"), ("Have a pet dragon", "Have a pet robot"),
    ("Get unlimited free data", "Get unlimited free coffee"), ("Always win arguments", "Always win games"),
    ("Travel to the past", "Travel to the future"), ("Be the funniest person in the room", "Be the smartest person in the room"),
    ("Have summer all year", "Have winter all year"), ("Read minds", "See the future"),
    ("Only text, never call", "Only call, never text"), ("Have a personal chef", "Have a personal driver"),
    ("Live without music", "Live without movies"), ("Be able to teleport", "Have super speed"),
    ("Wake up at 5 am every day", "Sleep at 5 am every day"), ("Have 1 true best friend", "Have 100 good friends"),
    ("Live in a treehouse", "Live in a houseboat"), ("Fight 1 horse-sized duck", "Fight 100 duck-sized horses"),
]
RIDDLES: list[tuple[str, str]] = [
    ("What has keys but can't open locks?", "A keyboard ⌨️"), ("What gets wetter the more it dries?", "A towel 🧻"),
    ("I speak without a mouth and hear without ears. What am I?", "An echo 🏔"), ("What has hands but can't clap?", "A clock 🕰"),
    ("The more you take, the more you leave behind. What are they?", "Footsteps 👣"), ("What can travel around the world while staying in a corner?", "A stamp 📮"),
    ("What has a head and a tail but no body?", "A coin 🪙"), ("What goes up but never comes down?", "Your age 🎂"),
    ("What has many teeth but can't bite?", "A comb 💇"), ("What can you break without touching it?", "A promise 🤞"),
    ("I'm tall when I'm young and short when I'm old. What am I?", "A candle 🕯"), ("What has one eye but can't see?", "A needle 🪡"),
    ("What runs but never walks?", "Water 💧"), ("What has a neck but no head?", "A bottle 🍾"),
    ("What is full of holes but still holds water?", "A sponge 🧽"), ("Which month has 28 days?", "All of them 📅"),
    ("What comes once in a minute, twice in a moment, but never in a thousand years?", "The letter M 🔤"),
    ("What can fill a room but takes up no space?", "Light 💡"), ("What has words but never speaks?", "A book 📖"),
    ("What invention lets you look right through a wall?", "A window 🪟"),
]
FACTS: list[str] = [
    "🐙 Octopuses have three hearts and blue blood.", "🍯 Honey never spoils — edible honey was found in ancient tombs.",
    "🌋 There are more volcanoes under the sea than on land.", "🐘 Elephants can recognise themselves in a mirror.",
    "⚡ Lightning is about five times hotter than the surface of the Sun.", "🧠 Your brain uses about 20% of your body's energy.",
    "🌙 The Moon moves about 3.8 cm farther from Earth every year.", "🦈 Sharks are older than trees.",
    "🌍 India has the world's largest postal network.", "🕷 A spider's silk is stronger than steel of the same thickness.",
    "🍌 Bananas are slightly radioactive (it's harmless!).", "🐝 A bee visits about 2 million flowers to make 1 kg of honey.",
    "🪐 A day on Venus is longer than its year.", "🦒 A giraffe has the same number of neck bones as you: seven.",
    "🧊 Hot water can freeze faster than cold water (the Mpemba effect).", "🛰 The first email was sent in 1971.",
    "🐜 Ants never sleep and have no lungs.", "🎵 Music can make plants grow faster — some studies say so!",
    "🌈 No two rainbows are exactly the same — each person sees their own.", "🐧 Penguins propose with a pebble.",
    "🏔 Mount Everest grows about 4 mm every year.", "🧬 Humans share about 60% of their DNA with bananas.",
    "☕ Coffee beans are actually seeds of a fruit.", "🌊 The ocean makes more than half of Earth's oxygen.",
    "🐌 A snail can sleep for three years.", "🔥 Fire has no shadow.", "🦋 Butterflies taste with their feet.",
    "📱 There are more phones than people on Earth.", "🐨 Koala fingerprints look almost like human ones.", "🌅 Sunlight takes about 8 minutes to reach Earth.",
]

# Appended (never reordered): old riddle buttons / polls keep pointing at the same entries.
WYR += content.WYR_MORE
RIDDLES += content.RIDDLES_MORE
FACTS += content.FACTS_MORE
