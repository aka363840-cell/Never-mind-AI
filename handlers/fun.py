"""Social / party commands. No wallets or game state involved."""
import hashlib
import random

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from utils import brand, content, deck, gifs
from utils.fmt import esc, mention

router = Router()

BALL = ["It is certain ✅", "Without a doubt ✨", "Yes, definitely 💯", "Most likely 👍", "Signs point to yes 🔮",
        "Ask again later 🌫", "Cannot predict now 🤔", "Don't count on it 🙅", "My sources say no ❌",
        "Very doubtful 😬", "Absolutely not 🚫"]
TRUTH = ["What's the most embarrassing thing you've done in public?", "Who was your first crush?",
         "What's a secret talent nobody knows about?", "What's the last lie you told?",
         "What app do you waste the most time on?", "What's your biggest irrational fear?",
         "Which chat member would you trust with your phone?", "What's the weirdest dream you remember?"]
DARE = ["Send your last photo in the gallery (keep it clean!) 📸", "Change your name to '{bot}'s Fan' for 1 hour 🦋",
        "Voice-message yourself singing the chorus of any song 🎤", "Write a 2-line poem about the person above you ✍️",
        "Say something nice about every member who replies to this 💬", "Send a sticker that describes your mood right now 😎"]
JOKES = ["Why don't skeletons fight each other? They don't have the guts. 💀",
         "I told my computer I needed a break. Now it won't stop sending me Kit-Kat ads. 🍫",
         "Why did the diamond go to school? To get a little more polished. 💎",
         "I'm reading a book about anti-gravity. It's impossible to put down. 📖",
         "Why do bees have sticky hair? Because they use honeycombs. 🐝",
         "What do you call a fake noodle? An impasta. 🍝"]
BALL += content.BALL_MORE
TRUTH += content.TRUTH_MORE
DARE += content.DARE_MORE
JOKES += content.JOKES_MORE
# name: (emoji, text when aimed at someone ({a}=you, {b}=target), text when used alone (optional))
ACTIONS = {
    "applaud": ("👏", "{a} gives {b} a standing ovation!", "{a} claps for everyone!"),
    "bite": ("😬", "{a} playfully nibbles {b}. Nom!", None),
    "blush": ("😊", "{a} blushes because of {b}.", "{a} is blushing so hard."),
    "bonk": ("🔨", "{a} bonks {b}. Go to timeout!", None),
    "boop": ("👉", "{a} boops {b} on the nose. Boop!", None),
    "brofist": ("🤜", "{a} and {b} share a brofist 🤛", None),
    "cheer": ("🎉", "{a} cheers for {b}! Woohoo!", "{a} cheers loudly! Woohoo!"),
    "comfort": ("🫂", "{a} comforts {b}. It's gonna be okay.", None),
    "cry": ("😭", "{a} is crying because of {b}.", "{a} is crying… somebody hug them."),
    "cuddle": ("🥰", "{a} cuddles {b} tightly.", None),
    "dance": ("💃", "{a} dances with {b}!", "{a} is dancing like nobody's watching!"),
    "facepalm": ("🤦", "{a} facepalms at {b}.", "{a} facepalms. Why, just why?"),
    "feed": ("🍕", "{a} feeds {b} some yummy food.", None),
    "flower": ("💐", "{a} gives {b} a bouquet of flowers.", None),
    "glare": ("😒", "{a} glares at {b}.", "{a} glares at everyone."),
    "handhold": ("🤝", "{a} holds {b}'s hand.", None),
    "happy": ("😄", "{a} is happy because of {b}!", "{a} is super happy today!"),
    "highfive": ("🙌", "{a} and {b} share an epic high-five!", None),
    "hug": ("🤗", "{a} gives {b} a big warm hug!", None),
    "kick": ("🦵", "{a} kicks {b} into the sky!", None),
    "defeat": ("☠️", "{a} dramatically pretends to defeat {b}. (Just a joke!)", None),
    "kiss": ("😘", "{a} gives {b} a sweet kiss on the cheek.", None),
    "laugh": ("😂", "{a} laughs at {b}'s joke.", "{a} is laughing out loud!"),
    "love": ("❤️", "{a} sends all the love to {b}.", "{a} spreads love to everyone!"),
    "massage": ("💆", "{a} gives {b} a relaxing massage.", None),
    "nom": ("😋", "{a} nom noms on {b}.", "{a} is eating something tasty. Nom nom!"),
    "pat": ("😊", "{a} pats {b} on the head. Good one!", None),
    "poke": ("👉", "{a} pokes {b}. Hey! Wake up!", None),
    "punch": ("👊", "{a} punches {b} (a very soft, friendly punch).", None),
    "roar": ("🦁", "{a} roars at {b}! RAWR!", "{a} roars! RAWR!"),
    "run": ("🏃", "{a} runs away from {b}!", "{a} runs away!"),
    "salute": ("🫡", "{a} salutes {b}. Respect!", "{a} salutes everyone."),
    "shy": ("🙈", "{a} gets shy around {b}.", "{a} feels shy."),
    "slap": ("👋", "{a} slaps {b} with a giant fish! 🐟", None),
    "sleep": ("😴", "{a} falls asleep on {b}'s shoulder.", "{a} is sleeping. Zzz…"),
    "smile": ("😊", "{a} smiles at {b}.", "{a} smiles. Cute!"),
    "stare": ("👀", "{a} stares at {b}…", "{a} stares at everyone…"),
    "tickle": ("🪶", "{a} tickles {b}. Hahaha stop!", None),
    "tease": ("😜", "{a} teases {b}.", None),
    "thumbsup": ("👍", "{a} gives {b} a thumbs up!", "{a} gives a thumbs up!"),
    "wave": ("👋", "{a} waves at {b}. Hi!", "{a} waves at everyone. Hello!"),
    "wink": ("😉", "{a} winks at {b}.", "{a} winks."),
    "yeet": ("🚀", "{a} yeets {b} to the moon!", None),
    "yell": ("📢", "{a} yells at {b}!", "{a} yells into the void!"),
    "zen": ("🧘", "{a} meditates with {b}. Peace.", "{a} meditates. Peace."),
}
ACTION_ALIASES = {"clap": "applaud", "hi": "wave", "hold": "handhold", "sad": "cry",
                  "thumbs": "thumbsup", "nap": "sleep", "meditate": "zen", "lol": "laugh", "hit": "punch"}


def _name(u) -> str:
    return mention(u.id, u.full_name or u.first_name)


def _has_target(m: Message, command: CommandObject) -> bool:
    rep = m.reply_to_message
    if rep and rep.from_user:
        return True
    if any(e.type == "text_mention" and e.user for e in (m.entities or [])):
        return True
    return bool((command.args or "").strip())


def _target(m: Message, command: CommandObject) -> str:
    rep = m.reply_to_message
    if rep and rep.from_user:
        return _name(rep.from_user)
    for e in m.entities or []:
        if e.type == "text_mention" and e.user:
            return _name(e.user)
    arg = (command.args or "").strip()
    return f"<b>{esc(arg[:40])}</b>" if arg else "<b>everyone</b>"


def _make_action(name: str):
    emoji, tpl, solo = ACTIONS[name]
    cmds = [name] + [al for al, tgt in ACTION_ALIASES.items() if tgt == name and al != name]

    @router.message(Command(*cmds))
    async def handler(m: Message, command: CommandObject):
        if solo and not _has_target(m, command):
            text = f"{emoji} " + solo.format(a=_name(m.from_user))
        else:
            text = f"{emoji} " + tpl.format(a=_name(m.from_user), b=_target(m, command))
        if name in gifs.GIF_ACTIONS:           # only the 'important' actions get a reaction GIF
            await gifs.send(m, name, text)
        else:
            await m.answer(text)
    return handler


for _n in ACTIONS:
    _make_action(_n)


@router.message(Command("roll", "dice"))
async def roll(m: Message, command: CommandObject):
    spec = (command.args or "1d6").lower().strip()
    try:
        n, sides = (int(x) for x in spec.split("d")) if "d" in spec else (1, int(spec))
    except ValueError:
        n, sides = 1, 6
    n, sides = max(1, min(n, 10)), max(2, min(sides, 1000))
    rolls = [random.randint(1, sides) for _ in range(n)]
    await m.reply(f"🎲 {n}d{sides}: <b>{' + '.join(map(str, rolls))}</b>" + (f" = <b>{sum(rolls)}</b>" if n > 1 else ""))


@router.message(Command("8ball", "ask8"))
async def eight_ball(m: Message, command: CommandObject):
    if not (command.args or "").strip():
        await m.reply("🎱 Ask a yes/no question: /8ball Will I win the next duel?")
        return
    await m.reply(f"🎱 <b>{deck.pick('8ball', BALL, m.chat.id)}</b>")


@router.message(Command("ship"))
async def ship(m: Message, command: CommandObject):
    a = m.from_user
    rep = m.reply_to_message
    if rep and rep.from_user:
        b_id, b_name = rep.from_user.id, _name(rep.from_user)
    else:
        arg = (command.args or "").strip()
        if not arg:
            await m.reply("💘 Reply to someone or use /ship &lt;name&gt;")
            return
        b_id, b_name = int(hashlib.sha256(arg.lower().encode()).hexdigest()[:12], 16), f"<b>{esc(arg[:30])}</b>"
    seed = int(hashlib.sha256(f"{min(a.id, b_id)}:{max(a.id, b_id)}".encode()).hexdigest()[:8], 16)
    pct = seed % 101
    bar = "❤️" * (pct // 10) + "🖤" * (10 - pct // 10)
    verdict = "Soulmates! 💍" if pct >= 85 else "Great match! 💞" if pct >= 60 else "Could work… 🌱" if pct >= 35 else "Just friends 🤝"
    await m.reply(f"💘 {_name(a)} + {b_name}\n{bar}\n<b>{pct}%</b> — {verdict}")


@router.message(Command("truth"))
async def truth(m: Message):
    await m.reply(f"🫢 <b>Truth:</b> {deck.pick('truth', TRUTH, m.chat.id)}")


@router.message(Command("dare"))
async def dare(m: Message):
    await m.reply(f"😈 <b>Dare:</b> {deck.pick('dare', DARE, m.chat.id).format(bot=esc(brand.name()))}")


@router.message(Command("joke"))
async def joke(m: Message):
    await m.reply(deck.pick('joke', JOKES, m.chat.id))


@router.message(Command("actions", "fun"))
async def actions_list(m: Message):
    names = sorted(set(ACTIONS) | set(ACTION_ALIASES))
    await m.reply("🎭 <b>Fun actions</b> — reply to someone or add @user\n" +
                  "  ".join(f"/{n}" for n in names) +
                  "\n\nMore fun: /ship /rate /choose /compliment /roast /iq /lucky /truth /dare /joke /8ball /roll")


COMPLIMENTS = ["You light up every chat you enter ✨", "Your vibe is unmatched 😎", "You're smarter than you think 🧠",
               "You make people smile without even trying 😊", "You're a legend, never forget it 👑",
               "Your energy is contagious in the best way ⚡", "You're a diamond 💎 in a world of pebbles"]
ROASTS = ["You're proof that even autocorrect gives up sometimes 😂", "You have the speed of a slow Wi-Fi at midnight 🐌",
          "Your jokes arrive late, just like your replies 😜", "You're like a cloud: when you disappear, it's a beautiful day… kidding, stay! ☁️",
          "If laziness were a sport, you'd still skip practice 🛋", "You're not lost, you just have a creative sense of direction 🧭"]
MOODS = ["Happy 😄", "Chill 😎", "Sleepy 😴", "Hyper 🤪", "Romantic 😍", "Hungry 🍔", "Savage 😈", "Lazy 🦥", "Confident 💪", "Dramatic 🎭"]
COMPLIMENTS += content.COMPLIMENTS_MORE
ROASTS += content.ROASTS_MORE
MOODS += content.MOODS_MORE


def _victim(m: Message, command: CommandObject):
    rep = m.reply_to_message
    if rep and rep.from_user:
        return _name(rep.from_user), rep.from_user.id
    return _name(m.from_user), m.from_user.id


@router.message(Command("compliment"))
async def compliment(m: Message, command: CommandObject):
    who, _ = _victim(m, command)
    await m.reply(f"💖 {who}: {deck.pick('compliment', COMPLIMENTS, m.chat.id)}")


@router.message(Command("roast"))
async def roast(m: Message, command: CommandObject):
    who, _ = _victim(m, command)
    await m.reply(f"🔥 {who}: {deck.pick('roast', ROASTS, m.chat.id)}")


def _daily_seed(uid: int, salt: str) -> int:
    import datetime
    return int(hashlib.sha256(f"{uid}:{salt}:{datetime.date.today()}".encode()).hexdigest()[:8], 16)


@router.message(Command("iq"))
async def iq(m: Message, command: CommandObject):
    who, uid = _victim(m, command)
    await m.reply(f"🧠 {who}'s IQ today: <b>{70 + _daily_seed(uid, 'iq') % 81}</b>  (just for fun!)")


@router.message(Command("lucky"))
async def lucky(m: Message, command: CommandObject):
    who, uid = _victim(m, command)
    await m.reply(f"🍀 {who}'s luck today: <b>{_daily_seed(uid, 'lucky') % 101}%</b>\nMood: {MOODS[_daily_seed(uid, 'mood') % len(MOODS)]}")


@router.message(Command("rate"))
async def rate(m: Message, command: CommandObject):
    what = (command.args or "").strip()
    if not what and not m.reply_to_message:
        await m.reply("⭐ Use: /rate &lt;anything&gt; (or reply to someone)")
        return
    label = esc(what[:60]) if what else _victim(m, command)[0]
    pct = int(hashlib.sha256((what or str(m.reply_to_message.from_user.id)).lower().encode()).hexdigest()[:6], 16) % 11
    await m.reply(f"⭐ I rate {label}: <b>{pct}/10</b> " + "★" * pct + "☆" * (10 - pct))


@router.message(Command("choose", "pick"))
async def choose(m: Message, command: CommandObject):
    raw = (command.args or "").strip()
    opts = [o.strip() for o in (raw.split("|") if "|" in raw else raw.split(" or ") if " or " in raw.lower() else raw.split(",")) if o.strip()]
    if len(opts) < 2:
        await m.reply("🤔 Use: /choose pizza | burger | pasta   (or: /choose tea or coffee)")
        return
    await m.reply(f"🎯 I choose: <b>{esc(random.choice(opts)[:80])}</b>")
