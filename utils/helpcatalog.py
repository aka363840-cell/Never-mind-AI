"""Single source of truth for the bot's command descriptions.

Pure Python (no aiogram / database imports) so it is easy to test:
  * MENU      - the short, curated list shown in Telegram's "/" menu (DMs and groups, identical for everybody).
  * entries()  - EVERY public command (flat list, A-Z) - the raw material for /help.
  * CATEGORIES / sections() - /help groups those commands by topic (Economy, Combat, Family ...), one screen per category.
Owner / moderator commands (STAFF) are deliberately left out of both: they still work for the owner,
they are just never advertised to players.
"""
from __future__ import annotations

import html

from game.rules import R

# Commands for the owner / moderators. Never listed in the menu or in /help.
STAFF = frozenset({
    "admin", "stats", "backup", "backupdb", "restoredb", "give", "take", "setbalance", "ban", "unban", "mute",
    "unmute", "userinfo", "logs", "maintenance", "broadcast", "emojiid", "seticon", "delicon", "aitest", "drop",
    "boss", "purgeadopted",
})

# The "/" menu: only the commands players use all the time (everything else is in /help).
MENU = (
    "start", "help", "rules", "profile", "bal", "daily", "weekly", "work", "crime", "rob", "pay", "deposit", "withdraw",
    "kill", "revenge", "revive", "duel", "protect", "bounty", "shop", "inventory", "hunt", "dungeon", "pet",
    "clan", "top", "missions", "arcade", "spin", "slots", "marry", "family", "houses", "collect", "cd",
)
# AI chat only works in groups, so /ask (and /settings) are listed in the group menu only
GROUP_MENU_EXTRA = ("ask", "settings")

# name -> description ("{name}" / "{emoji}" are replaced with the bot's name / emoji)
DESC_LIST: list[tuple[str, str]] = [
    ('start', '{emoji} Open the {name} menu'),
    ('help', '📖 Command guide'),
    ('rules', '📜 Rules: kill, rob, protection, money… with the exact numbers'),
    ('about', 'ℹ️ About this bot'),
    ('profile', '👤 Your profile & stats'),
    ('bal', '💎 Wallet & bank'),
    ('daily', '🎁 Claim daily reward'),
    ('weekly', '📅 Claim weekly reward'),
    ('work', '💼 Work for diamonds'),
    ('crime', '🕶 Risky crime for diamonds'),
    ('rob', '🥷 Rob a player: /rob <amount> (reply/@user)'),
    ('pay', '💸 Send diamonds (@user amount)'),
    ('deposit', f'🏦 Bank diamonds (max {R.bank_max_pct:.0%} of your total)'),
    ('withdraw', '🏧 Take diamonds from the bank'),
    ('kill', f'⚔️ Kill a player & take {R.kill_reward_pct:.0%} of their wallet'),
    ('revenge', '😈 Hit back at your attacker'),
    ('revive', '✨ Pay diamonds to come back to life'),
    ('duel', '🤺 Duel for diamonds (@user wager)'),
    ('protect', '🛡 Buy protection (e.g. /protect 2h)'),
    ('bounty', '🎯 Put a bounty / see most wanted'),
    ('shop', '🛒 Open the shop'),
    ('powers', '⚡ Powers'),
    ('activate', '🔥 Activate your equipped power'),
    ('inventory', '🎒 Your inventory'),
    ('buy', '🛍 Buy an item'),
    ('sell', '💱 Sell an item'),
    ('equip', '🧿 Equip a power'),
    ('upgrade', '⬆️ Upgrade a power'),
    ('use', '🧪 Use an item'),
    ('hunt', '🏹 Hunt for loot, eggs & XP'),
    ('pet', '🐾 Your pet (adopt/feed/name)'),
    ('clan', '🏰 Clans (create/join/bank)'),
    ('spin', '🎡 Free lucky wheel'),
    ('slots', '🎰 Play slots (bet)'),
    ('flip', '🪙 Heads or tails (bet diamonds)'),
    ('top', '🏆 Leaderboards'),
    ('missions', '📜 Missions & achievements'),
    ('ask', '{emoji} Ask {name} anything (groups only)'),
    ('hug', '🤗 Hug someone (reply/@user)'),
    ('slap', '👋 Slap someone with a fish'),
    ('fish', '🎣 Go fishing for diamonds'),
    ('mine', '⛏ Mine for diamonds'),
    ('trivia', '🧠 Answer a quiz, win diamonds'),
    ('marry', '💍 Propose to someone (reply)'),
    ('couple', '💞 Your marriage'),
    ('divorce', '💔 End your marriage'),
    ('engage', '💍 Get engaged (reply/@user) · /engage break'),
    ('wedding', '💒 Plan your wedding: venue, card & gift'),
    ('anniversary', '🎂 Days together, milestones & yearly bonus'),
    ('sendgift', '🎁 Gift shop: flowers, chocolate, teddy, cake (partner/family)'),
    ('loveletter', '💌 Send a styled love letter (max 300 chars)'),
    ('honeymoon', '🏝 6-hour honeymoon trip after your wedding'),
    ('couplequest', '💞 Two shared daily tasks for you and your partner'),
    ('lovestreak', '🔥 Daily check-in streak with rewards at 7/30/100 days'),
    ('date', '💑 Go on a date (grow love)'),
    ('gift', '🎁 Gift your partner'),
    ('baby', '👶 Have a baby'),
    ('adopt', '🧸 Adopt someone (reply)'),
    ('family', '👨\u200d👩\u200d👧\u200d👦 Your family tree'),
    ('familytree', '🌳 Family tree picture (family only)'),
    ('addfamily', '🌳 Add a relative (reply + role)'),
    ('roles', '📜 All family roles'),
    ('unfamily', '💔 Remove a relative'),
    ('gender', '⚧ Set your gender (family labels)'),
    ('father', '👨 Make them your Father (reply)'),
    ('mother', '👩 Make them your Mother (reply)'),
    ('brother', '🧑 Make them your Brother (reply)'),
    ('sister', '👧 Make them your Sister (reply)'),
    ('familybonus', '💰 Daily family bonus'),
    ('houses', '🏠 Buy or sell property (earns daily rent)'),
    ('collect', '💰 Collect your house rent (every 24h)'),
    ('kiss', '😘 Kiss someone'),
    ('actions', '🎭 All fun actions'),
    ('compliment', '💖 Compliment someone'),
    ('choose', '🎯 Let me choose (a | b | c)'),
    ('ship', '💘 Love calculator'),
    ('roll', '🎲 Roll dice (e.g. 2d6)'),
    ('8ball', '🎱 Ask the magic 8-ball'),
    ('truth', '🫢 Random truth question'),
    ('dare', '😈 Random dare'),
    ('joke', '😂 Tell me a joke'),
    ('dungeon', '🗝 Dive into the 5-floor dungeon'),
    ('expedition', '🧭 Send your pet exploring'),
    ('heist', '🎭 Pull a group heist (groups)'),
    ('cd', '⏱ All your cooldowns at a glance'),
    ('arcade', '🕹 Arcade & social hub (all new games)'),
    ('scratch', '🎟 Scratch card (win up to 3,000)'),
    ('ttt', '⭕ Tic-tac-toe with a friend'),
    ('achievements', '🏅 Badges & progress'),
    ('clear', '🧹 Make {name} forget our chat'),
    ('rps', '✊ Rock-paper-scissors'),
    ('hangman', '🔤 Play hangman'),
    ('wyr', '🤔 Would you rather…?'),
    ('riddle', '🧩 Solve a riddle'),
    ('fact', '💡 Random fun fact'),
    ('horoscope', '🔮 Your horoscope'),
    ('fortune', '🥠 Fortune cookie'),
    ('rep', '🌟 Give someone +1 reputation (reply)'),
    ('toprep', '🏅 Reputation leaderboard'),
    ('interest', '📈 Collect daily bank interest'),
    ('roast', '🔥 Playfully roast someone'),
    ('iq', "🧠 Today's (fun) IQ"),
    ('lucky', "🍀 Today's luck"),
    ('rate', '⭐ Rate anything out of 10'),
    ('gifts', '🎁 List gifts for your partner'),
    ('disown', '💔 Remove a child from your family'),
    ('settings', '⚙️ Group settings (group admins)'),
]
DESC: dict[str, str] = dict(DESC_LIST)

# primary command -> its alternative spellings (taken from the handlers)
ALIASES: dict[str, tuple[str, ...]] = {
    '8ball': ('ask8',),
    'about': ('info',),
    'rules': ('rule', 'guide'),
    'achievements': ('badges', 'ach'),
    'actions': ('fun',),
    'addfamily': ('relate', 'addrelative'),
    'arcade': ('games',),
    'ask': ('chat', 'bot'),
    'baby': ('child',),
    'bal': ('balance',),
    'cd': ('timers', 'cooldowns', 'cooldown'),
    'choose': ('pick',),
    'clan': ('clans',),
    'clear': ('reset',),
    'collect': ('rent',),
    'couple': ('marriage', 'partner'),
    'deposit': ('dep',),
    'dungeon': ('dg', 'delve'),
    'expedition': ('exp', 'expeditions'),
    'fact': ('facts',),
    'family': ('kids', 'tree', 'relatives'),
    'familybonus': ('fbonus',),
    'familytree': ('ftree', 'treepic'),
    'fish': ('fishing',),
    'flip': ('coinflip', 'cf'),
    'fortune': ('cookie',),
    'hangman': ('hang',),
    'heist': ('job',),
    'horoscope': ('zodiac',),
    'houses': ('house', 'property'),
    'hunt': ('explore',),
    'inventory': ('inv',),
    'marry': ('propose',),
    'mine': ('mining',),
    'pet': ('pets',),
    'profile': ('me',),
    'rep': ('respect',),
    'roles': ('familyroles',),
    'roll': ('dice',),
    'rps': ('rockpaperscissors',),
    'scratch': ('scratchcard',),
    'slots': ('slot',),
    'spin': ('wheel',),
    'start': ('menu',),
    'top': ('leaderboard', 'lb'),
    'toprep': ('reps',),
    'trivia': ('quiz',),
    'ttt': ('tictactoe', 'xo'),
    'unfamily': ('removefamily', 'cutoff'),
    'withdraw': ('wd',),
    'wyr': ('wouldyourather',),
}


def _fill(text: str, name: str, emoji: str) -> str:
    return text.replace("{name}", name).replace("{emoji}", emoji)


def menu(name: str = "Bot", emoji: str = "", group: bool = False) -> list[tuple[str, str]]:
    names = list(MENU) + (list(GROUP_MENU_EXTRA) if group else [])
    return [(c, _fill(DESC[c], name, emoji)) for c in names]


def entries(name: str = "Bot", emoji: str = "", actions: dict | None = None, action_aliases: dict | None = None,
            roles: dict | None = None, role_aliases: dict | None = None) -> list[tuple[str, str, tuple[str, ...]]]:
    """Every public command as (command, description, aliases), sorted A-Z.
    actions = fun.ACTIONS (+ ACTION_ALIASES), roles = family.ROLES (+ ALIASES): commands generated in loops."""
    found: dict[str, tuple[str, tuple[str, ...]]] = {}
    for c, d in DESC.items():
        found[c] = (_fill(d, name, emoji), tuple(ALIASES.get(c, ())))
    for a, spec in (actions or {}).items():
        al = tuple(sorted(x for x, tgt in (action_aliases or {}).items() if tgt == a and x != a))
        if a in found:
            found[a] = (found[a][0], al or found[a][1])
        else:
            found[a] = (f"{spec[0]} {a.capitalize()} someone (reply/@user)", al)
    for r in (roles or {}):
        al = tuple(sorted(x for x, tgt in (role_aliases or {}).items() if tgt == r and x != r and x not in (roles or {}) and "-" not in x))
        if r in found:
            found[r] = (found[r][0], al or found[r][1])
        else:
            found[r] = (f"👪 Make them your {r.replace('_', ' ')} (reply)", al)
    return [(c, d, a) for c, (d, a) in sorted(found.items(), key=lambda kv: kv[0].lower()) if c not in STAFF]


def pages(items: list[tuple[str, str, tuple[str, ...]]], limit: int = 3300) -> list[str]:
    """Split the A-Z list into message-sized HTML pages (Telegram allows 4096 chars; the caller adds a header)."""
    out: list[str] = []
    cur: list[str] = []
    size = 0
    letter = ""
    for c, d, al in items:
        first = c[0].upper() if c[0].isalpha() else "0-9"
        line = f"/{c} — {html.escape(d, quote=False)}"
        if al:
            line += " <i>(also " + " ".join("/" + a for a in al) + ")</i>"
        head = f"<b>▸ {first}</b>\n" if first != letter else ""
        if cur and size + len(head) + len(line) + 1 > limit:
            out.append("\n".join(cur)); cur, size = [], 0
            head = f"<b>▸ {first}</b>\n"
        if head:
            cur.append(head.rstrip("\n")); size += len(head)
            letter = first
        cur.append(line); size += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


# ------------------------------------------------------------------ /help categories
# (emoji + title, [commands]).  "@actions" / "@roles" expand to the commands that are generated in loops
# (fun.ACTIONS / family.ROLES) and are shown as one compact line.  Any public command that is not listed
# here still shows up under "Other", so a new command can never be missing from /help.
CATEGORIES: list[tuple[str, list[str]]] = [
    ("🚀 Basics", ["start", "help", "rules", "about", "profile", "cd", "settings", "clear"]),
    ("💰 Economy", ["bal", "daily", "weekly", "work", "crime", "rob", "pay", "deposit", "withdraw", "interest"]),
    ("🏠 Property & Rent", ["houses", "collect"]),
    ("⚔️ Combat & PvP", ["kill", "revenge", "revive", "duel", "protect", "bounty"]),
    ("🛒 Shop & Items", ["shop", "buy", "sell", "inventory", "use", "powers", "equip", "upgrade", "activate"]),
    ("🗺 Adventure & Pets", ["hunt", "dungeon", "expedition", "heist", "fish", "mine", "pet"]),
    ("🏰 Clans", ["clan"]),
    ("🎰 Casino & Luck", ["spin", "slots", "flip", "scratch"]),
    ("🕹 Games", ["arcade", "ttt", "rps", "hangman", "trivia", "wyr", "riddle", "roll", "8ball", "choose"]),
    ("🏆 Progress & Ranking", ["top", "missions", "achievements", "rep", "toprep"]),
    ("💍 Love & Family", ["marry", "engage", "wedding", "anniversary", "sendgift", "loveletter", "honeymoon", "couplequest", "lovestreak", "couple", "date", "gift", "gifts", "baby", "divorce", "adopt", "disown", "family",
                          "familytree", "addfamily", "unfamily", "roles", "gender", "familybonus", "ship",
                          "father", "mother", "brother", "sister", "@roles"]),
    ("🎭 Social & Fun", ["actions", "hug", "slap", "kiss", "compliment", "roast", "truth", "dare", "joke", "fact", "horoscope",
                         "fortune", "iq", "lucky", "rate", "@actions"]),
    ("🤖 AI Chat", ["ask"]),
]
OTHER = "📦 Other"


def _line(c: str, d: str, al: tuple[str, ...]) -> str:
    line = f"/{c} — {html.escape(d, quote=False)}"
    if al:
        line += " <i>(also " + " ".join("/" + a for a in al) + ")</i>"
    return line


def sections(items: list[tuple[str, str, tuple[str, ...]]], actions: dict | None = None,
             roles: dict | None = None, limit: int = 3300) -> list[tuple[str, list[str]]]:
    """Group the public commands into [(category title, [page html, ...]), ...] in the order of CATEGORIES.
    Generated commands (fun actions, family roles) become one compact line instead of one line each;
    anything not listed in CATEGORIES lands in 'Other'. A category only gets more than one page if it is huge."""
    by_name = {c: (d, a) for c, d, a in items}
    gen = {
        "@actions": sorted(a for a in (actions or {}) if a in by_name and a not in DESC),
        "@roles": sorted(r for r in (roles or {}) if r in by_name and r not in DESC),
    }
    used: set[str] = set()
    for names in gen.values():
        used.update(names)
    out: list[tuple[str, list[str]]] = []

    def build(title: str, tokens: list[str]) -> None:
        lines: list[str] = []
        for t in tokens:
            if t in gen:
                if gen[t]:
                    label = "More actions (reply/@user)" if t == "@actions" else "More relatives (reply to someone)"
                    lines.append(f"\n<b>{label}</b>\n" + " ".join("/" + n for n in gen[t]))
            elif t in by_name and t not in used:
                d, a = by_name[t]
                lines.append(_line(t, d, a))
                used.add(t)
        if not lines:
            return
        pages, cur, size = [], [], 0
        for ln in lines:
            if cur and size + len(ln) + 1 > limit:
                pages.append("\n".join(cur)); cur, size = [], 0
            cur.append(ln); size += len(ln) + 1
        pages.append("\n".join(cur))
        out.append((title, pages))

    for title, tokens in CATEGORIES:
        build(title, tokens)
    build(OTHER, sorted((c for c in by_name if c not in used), key=str.lower))
    return out
