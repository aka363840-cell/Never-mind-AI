"""All game balance in one place. Every number is configurable.

Override any scalar field with a JSON file (env RULES_FILE), e.g. {"daily_reward": 750}.
Catalogues (POWERS / ITEMS) are synced into the DB at startup.

Economy design: 💎 diamonds are the ONLY currency. Diamonds are MINTED only by daily / weekly / work / successful crime and
mission rewards. PvP (rob, kill, duel, bounty) only MOVES existing diamonds. Sinks: shop,
power activation, protection, failed crime fines, bounty-cancel fee.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields


@dataclass(frozen=True)
class PowerDef:
    key: str
    name: str
    emoji: str
    price: int
    activation_cost: int
    cooldown_s: int
    duration_s: int
    description: str
    limitation: str
    max_level: int = 5


@dataclass(frozen=True)
class ItemDef:
    key: str
    category: str  # weapons | armour | shields | consumables | cosmetics | collectibles
    name: str
    emoji: str
    price: int
    description: str
    attack: int = 0
    defence: int = 0
    health: int = 0
    max_level: int = 5
    stackable: bool = False


POWERS: dict[str, PowerDef] = {
    p.key: p
    for p in [
        PowerDef("PHANTOM", "Phantom", "👻", 3000, 150, 2400, 900,
                 "Avoid ONE incoming hit while active.",
                 "Single charge. Critical hits cannot be avoided."),
        PowerDef("REAPER", "Reaper", "💀", 4000, 200, 2400, 600,
                 "+20% damage dealt while active (+10% per upgrade).",
                 "Your own defence is reduced by 10% while active."),
        PowerDef("GUARDIAN", "Guardian", "🛡", 3500, 300, 4800, 600,
                 "Grant another player temporary protection (reply to them with /activate).",
                 "Cannot target yourself or an already-protected player. Short protection."),
        PowerDef("MIRROR", "Mirror", "🪞", 3500, 200, 2400, 600,
                 "Reflect 25% of incoming damage (+10% per upgrade).",
                 "Reflected damage is capped at 15 per hit and cannot kill."),
        PowerDef("ORACLE", "Oracle", "🔮", 2000, 100, 1200, 0,
                 "Scan another player (reply with /activate): level, combat stats, power, protection.",
                 "Never reveals wallet or bank balances."),
        PowerDef("PHOENIX", "Phoenix", "🔥", 5000, 400, 28800, 3600,
                 "Survive ONE lethal hit with 1 HP while active.",
                 "Single charge, 12h cooldown."),
        PowerDef("PARASITE", "Parasite", "🦠", 4500, 250, 4800, 600,
                 "Steal an extra 15% of the loser's wallet after a won fight (+3% per upgrade).",
                 "Extra steal capped at 150 diamonds and only taken from the loser."),
    ]
}

ITEMS: dict[str, ItemDef] = {
    i.key: i
    for i in [
        ItemDef("wooden_sword", "weapons", "Wooden Sword", "🗡", 500, "A humble start.", attack=3),
        ItemDef("iron_sword", "weapons", "Iron Sword", "⚔️", 2500, "Reliable steel.", attack=8),
        ItemDef("steel_blade", "weapons", "Steel Blade", "🔪", 8000, "Honed and deadly.", attack=15),
        ItemDef("shadow_dagger", "weapons", "Shadow Dagger", "🌑", 15000, "Strikes from nowhere.", attack=24),
        ItemDef("leather_armour", "armour", "Leather Armour", "🥋", 500, "Light protection.", defence=2, health=10),
        ItemDef("chainmail", "armour", "Chainmail", "⛓", 3000, "Interlocked rings.", defence=5, health=25),
        ItemDef("plate_armour", "armour", "Plate Armour", "🛡", 10000, "Heavy, near-impenetrable.", defence=10, health=50),
        ItemDef("wooden_shield", "shields", "Wooden Shield", "🪵", 400, "Blocks a few blows.", defence=3),
        ItemDef("tower_shield", "shields", "Tower Shield", "🏰", 6000, "A moving wall.", defence=8),
        ItemDef("health_potion", "consumables", "Health Potion", "🧪", 300,
                "/use: +40 HP in your fights for 30 minutes.", stackable=True, max_level=1),
        ItemDef("xp_scroll", "consumables", "XP Scroll", "📜", 800,
                "/use: instantly gain 100 XP.", stackable=True, max_level=1),
        ItemDef("title_hunter", "cosmetics", "Hunter", "🏹", 2000, "Profile title.", max_level=1),
        ItemDef("title_shadow", "cosmetics", "Shadow", "🌘", 5000, "Profile title.", max_level=1),
        ItemDef("title_legend", "cosmetics", "Legend", "👑", 10000, "Profile title.", max_level=1),
        ItemDef("crystal_shard", "collectibles", "Crystal Shard", "🔹", 1000, "Shiny. Tradable for 50%.", stackable=True, max_level=1),
        ItemDef("ancient_coin", "collectibles", "Ancient Relic", "🏺", 2500, "A relic.", stackable=True, max_level=1),
        ItemDef("golden_idol", "collectibles", "Diamond Idol", "🗿", 10000, "A rare treasure.", max_level=1),
    ]
}

PET_RARITY = {"common": (1.0, "⚪"), "rare": (1.4, "🔵"), "epic": (1.8, "🟣"), "legendary": (2.5, "🟡")}
PET_ROLL = [("common", 70), ("rare", 22), ("epic", 7), ("legendary", 1)]
PET_SPECIES = {
    "fox": ("🦊", "Fox", "common"), "cat": ("🐱", "Cat", "common"), "wolf": ("🐺", "Wolf", "common"),
    "owl": ("🦉", "Owl", "rare"), "panther": ("🐆", "Panther", "rare"),
    "unicorn": ("🦄", "Unicorn", "epic"), "griffin": ("🦅", "Griffin", "epic"),
    "dragon": ("🐉", "Dragon", "legendary"), "kraken": ("🐙", "Kraken", "legendary"),
}


def pet_stats(level: int, rarity: str) -> tuple[float, float, float]:
    """(attack, defence, hp) bonus a pet adds to its owner in every fight."""
    m = PET_RARITY.get(rarity, (1.0, ""))[0]
    return (1 + level * 0.6) * m, level * 0.4 * m, level * 3 * m


EQUIP_SLOTS = {"weapons": "weapon", "armour": "armour", "shields": "shield", "cosmetics": "cosmetic"}
CATEGORIES = ["powers", "weapons", "armour", "shields", "consumables", "cosmetics", "collectibles"]


@dataclass
class Rules:
    # --- accounts
    start_balance: int = 100
    # --- economy
    daily_reward: int = 500
    daily_cooldown_s: int = 18 * 3600
    weekly_reward: int = 3500
    weekly_cooldown_s: int = 5 * 24 * 3600
    work_cooldown_s: int = 1800
    work_min: int = 80
    work_max: int = 250
    crime_cooldown_s: int = 900
    crime_success_chance: float = 0.55
    crime_reward_min: int = 200
    crime_reward_max: int = 600
    crime_fine_min: int = 100
    crime_fine_max: int = 300
    crime_jail_s: int = 600
    # --- rob
    rob_cooldown_s: int = 900
    rob_victim_immunity_s: int = 1800
    rob_pair_cooldown_s: int = 3 * 3600
    rob_success_chance: float = 0.45
    rob_steal_pct_min: float = 0.10
    rob_steal_pct_max: float = 0.25
    rob_min_steal: int = 50
    rob_max_steal: int = 2000
    rob_target_min_wallet: int = 300
    rob_robber_min_wallet: int = 200
    rob_fine_pct: float = 0.10          # (legacy, unused: the fine now scales with the amount you tried to rob)
    rob_fine_ratio: float = 0.5         # caught: you pay this share of the amount you tried to rob (min/max below)
    rob_fine_min: int = 50
    rob_fine_max: int = 1000
    rob_fail_jail_s: int = 300
    # --- pay / bank
    pay_min: int = 1
    pay_max: int = 1_000_000
    pay_sender_min_age_s: int = 24 * 3600   # anti multi-account: young accounts cannot send
    pay_pair_daily_count: int = 10
    pay_sender_daily_cap: int = 100_000
    bank_max_pct: float = 0.50            # at most 50% of all your diamonds (wallet + bank) may sit in the bank
    # --- combat
    base_hp: int = 100
    hp_per_level: int = 5
    base_attack: int = 10
    attack_per_level: int = 2
    base_defence: int = 5
    defence_per_level: int = 1
    fight_rounds: int = 6
    crit_chance: float = 0.10
    kill_cooldown_s: int = 0             # no kill cooldown
    kill_victim_immunity_s: int = 900
    kill_pair_daily_limit: int = 3
    kill_reward_pct: float = 0.30        # killer takes 30% of the victim's FREE diamonds (wallet only, never the bank)
    kill_loss_pct: float = 0.04          # what the attacker pays if they lose
    kill_loss_max: int = 400
    revive_cost: int = 150               # a dead player must pay this to /revive before killing again
    xp_win: int = 25
    xp_loss: int = 8
    protect_cost_per_hour: int = 125     # /protect 1h, 2h, 3h ... costs hours x this
    protect_max_hours: int = 24
    revenge_window_s: int = 1800
    revenge_cooldown_s: int = 0
    revenge_attack_bonus: float = 0.10
    potion_hp: int = 40
    potion_duration_s: int = 1800
    # --- duel
    duel_invite_cooldown_s: int = 30
    duel_invite_ttl_s: int = 300
    duel_play_ttl_s: int = 300
    duel_min_wager: int = 10             # every duel moves diamonds: the winner wins them, the loser loses them
    duel_max_wager: int = 100_000
    duel_pair_daily_credit: int = 5     # duels per pair per day that grant XP / win-loss stats
    # --- bounty
    bounty_min: int = 500
    bounty_max: int = 500_000
    bounty_ttl_s: int = 72 * 3600
    bounty_cancel_refund_pct: float = 0.8
    # --- progression
    xp_per_level: int = 100              # xp needed for level L -> L+1 is L * xp_per_level
    # --- shop
    sell_ratio: float = 0.5
    upgrade_stat_bonus: float = 0.2      # +20% item stats per level above 1
    power_level_bonus: float = 0.1       # +10% power strength per level above 1
    power_upgrade_cost_ratio: float = 0.5
    # --- missions
    daily_missions: tuple = (
        ("claim_daily", "Claim your daily reward", "daily_claim", 1, 150),
        ("earn_500", "Earn 500 diamonds", "earn", 500, 200),
        ("win_duel", "Win a duel", "duel_win", 1, 250),
        ("rob_success", "Complete a successful robbery", "rob_success", 1, 200),
        ("use_power", "Use a power", "power_use", 1, 150),
        ("dungeon_clear", "Clear the dungeon", "dungeon_clear", 1, 300),
    )
    weekly_missions: tuple = (
        ("w_earn_5000", "Earn 5,000 diamonds", "earn", 5000, 1000),
        ("w_win_duels", "Win 5 duels", "duel_win", 5, 1200),
        ("w_kills", "Get 10 kills", "kill", 10, 1500),
        ("w_expedition", "Complete 3 pet expeditions", "expedition", 3, 900),
        ("w_heist", "Pull off a group heist", "heist_win", 1, 700),
    )
    # --- streaks / lucky wheel / casino
    streak_bonus: int = 50                # extra diamonds per streak day on /daily
    streak_max_days: int = 10             # bonus stops growing after this many days
    streak_window_s: int = 48 * 3600      # claim again within this time to keep the streak
    spin_cooldown_s: int = 8 * 3600
    casino_min_bet: int = 10
    casino_max_bet: int = 50_000
    casino_cooldown_s: int = 5
    # --- clans / pets / hunting
    clan_create_cost: int = 5000
    clan_max_members: int = 20
    clan_level_step: int = 20_000         # clan bank per clan level (max level 10)
    clan_daily_bonus: int = 25            # extra /daily diamonds per clan level
    pet_adopt_cost: int = 2500
    pet_feed_cost: int = 150
    pet_feed_xp: int = 40
    pet_feed_cooldown_s: int = 900
    pet_max_level: int = 20
    hunt_cooldown_s: int = 1200
    # --- group events
    event_check_s: int = 300
    event_min_gap_s: int = 9000           # per group
    event_chance: float = 0.3
    drop_min: int = 100
    drop_max: int = 400
    drop_ttl_s: int = 900
    boss_hp: int = 1000
    boss_pool: int = 2500
    boss_min_reward: int = 50
    boss_last_hit_bonus: int = 300
    boss_duration_s: int = 600
    boss_hit_cooldown_s: int = 6
    # --- level-ups
    level_bonus: int = 50                 # diamonds = level * this (capped at 2000)
    # --- romance: engagement / wedding / anniversary (every one-time bonus is smaller than what the couple paid for it)
    engage_ring_cost: int = 1000          # paid by the proposer when the other side accepts (a sink)
    engage_break_penalty: int = 250       # paid by whoever breaks the engagement (at most their wallet); the ring is not refunded
    engage_request_ttl_s: int = 180
    wedding_request_ttl_s: int = 300
    # (key, emoji, name, total cost split 50/50 between the partners, one-time bonus for EACH partner)
    wedding_venues: tuple = (
        ("simple", "🏛", "Simple Registry", 1500, 150),
        ("garden", "🌸", "Garden Ceremony", 4000, 500),
        ("royal", "👑", "Royal Palace", 10000, 1500),
    )
    # (days together, label, one-time reward for EACH partner)
    anniv_milestones: tuple = (
        (30, "1 month", 150),
        (100, "100 days", 400),
        (365, "1 year", 1000),
    )
    anniv_yearly_bonus: int = 250         # paid to each partner once per wedding anniversary (year 1, 2, 3 ...)
    # --- romance B: gifts & love letters (gifts are a sink: diamonds are spent, only love / bond points are gained)
    # (key, emoji, name, price in diamonds, love / bond points)
    gift_shop: tuple = (
        ("flowers", "💐", "Flowers", 50, 6),
        ("chocolate", "🍫", "Chocolate", 120, 14),
        ("teddy", "🧸", "Teddy bear", 300, 35),
        ("cake", "🎂", "Cake", 600, 75),
    )
    gift_daily_cap_points: int = 120      # love / bond points one SENDER can create per UTC day (gifts + letters together)
    letter_max_chars: int = 300
    letter_cost: int = 30
    letter_cooldown_s: int = 1800
    letter_points: int = 3
    # (bond points needed, title, emoji) - shown next to relatives in /family
    bond_titles: tuple = ((300, "Inseparable", "💞"), (100, "Close", "🤝"), (30, "Friendly", "🌼"))
    # --- romance C: honeymoon / couple quest / love streak
    honeymoon_cost: int = 1500            # total, split 50/50 (a sink: the two bonuses together are smaller)
    honeymoon_duration_s: int = 6 * 3600
    honeymoon_bonus: int = 400            # for EACH partner, paid once per couple when the trip is collected
    honeymoon_love: int = 40
    honeymoon_request_ttl_s: int = 300
    couple_quest_reward: int = 40         # diamonds for EACH partner per completed shared task (2 tasks a day)
    couple_quest_love: int = 8
    streak_love_per_day: int = 2
    # (consecutive days both checked in, one-time reward for EACH partner - once per couple, ever)
    streak_milestones: tuple = ((7, 200), (30, 600), (100, 2000))
    # --- limits
    command_rate_window_s: int = 3
    command_rate_max: int = 6


def load_rules() -> Rules:
    rules = Rules()
    path = os.getenv("RULES_FILE", "").strip()
    if path and os.path.exists(path):
        data = json.load(open(path, encoding="utf-8"))
        allowed = {f.name: f for f in fields(Rules)}
        for k, v in data.items():
            if k in allowed and not isinstance(getattr(rules, k), tuple):
                setattr(rules, k, type(getattr(rules, k))(v))
    return rules


R = load_rules()

# (key, name, event, goal, reward) – level/achievement catalogue
ACHIEVEMENTS = {
    "first_blood": ("🩸 First Blood", "Win your first kill"),
    "duelist": ("🤺 Duelist", "Win 5 duels"),
    "thief": ("🦝 Master Thief", "Complete 5 robberies"),
    "tycoon": ("💰 Tycoon", "Hold 10,000 diamonds in total"),
    "level_5": ("⭐ Rising Star", "Reach level 5"),
    "level_10": ("🌟 Veteran", "Reach level 10"),
    "powered": ("⚡ Empowered", "Buy your first power"),
    "collector": ("🎒 Collector", "Own 5 different items"),
    # --- Realm: dungeon / expeditions / heists
    "dungeon_first": ("🗝 Delver", "Clear the dungeon once"),
    "dungeon_10": ("🏰 Dungeon Master", "Clear the dungeon 10 times"),
    "dungeon_hard": ("☠️ Nightmare Walker", "Clear the dungeon on Hard"),
    "explorer": ("🧭 Explorer", "Bring your pet back from an expedition"),
    "voyager": ("⛵ Voyager", "Complete 10 expeditions"),
    "heist_first": ("🎭 Partner in Crime", "Pull off a heist with a crew"),
    "heist_5": ("🕶 Mastermind", "Pull off 5 successful heists"),
    "heist_perfect": ("💎 Flawless Heist", "Clear all 4 heist stages in one job"),
    # --- Life & collection
    "pet_owner": ("🐾 Beastmaster", "Own a pet"),
    "pet_legend": ("🐉 Legendary Bond", "Own a legendary pet"),
    "married": ("💍 Hitched", "Get married"),
    "parent": ("👶 Parent", "Have or adopt a child"),
    "landlord": ("🏠 Landlord", "Own a property"),
    "clanner": ("🏰 Clanner", "Join a clan"),
    "angler": ("🎣 Angler", "Go fishing 50 times"),
    "prospector": ("⛏ Prospector", "Go mining 50 times"),
    "whale": ("🐋 Whale Watcher", "Catch the Diamond Whale"),
    # --- Milestones
    "mogul": ("🤑 Mogul", "Hold 100,000 diamonds in total"),
    "level_20": ("🔥 Legend", "Reach level 20"),
    "slayer": ("☠️ Slayer", "Get 50 kills"),
    "completionist": ("🌈 Completionist", "Unlock every other achievement"),
}
