from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database.models import Pet, User
from game.rules import PET_RARITY, PET_SPECIES, R, pet_stats
from services import pets
from services.errors import GameError
from utils.fmt import esc, num

router = Router()


def _card(p: Pet) -> str:
    emoji, species, _ = PET_SPECIES[p.species]
    a, d, h = pet_stats(p.level, p.rarity)
    bar_n = 10 if p.level >= R.pet_max_level else min(10, 10 * p.xp // pets.need_xp(p.level))
    xp = "MAX" if p.level >= R.pet_max_level else f"{p.xp}/{pets.need_xp(p.level)} XP"
    return (f"{emoji} <b>{esc(p.name)}</b> the {species} {PET_RARITY[p.rarity][1]} {p.rarity.title()}\n"
            f"⭐ Level {p.level}/{R.pet_max_level}  [{'█' * bar_n}{'░' * (10 - bar_n)}] {xp}\n"
            f"⚔️ +{a:.1f} attack · 🛡 +{d:.1f} defence · ❤️ +{h:.0f} HP in every fight")


@router.message(Command("pet", "pets"))
async def pet_cmd(m: Message, command: CommandObject, db_user: User):
    parts = (command.args or "").split(None, 1)
    sub, rest = (parts[0].lower() if parts else ""), (parts[1] if len(parts) > 1 else "")
    uid = db_user.id
    if sub == "adopt":
        p = await pets.adopt(uid)
        await m.reply(f"🐾 You adopted a pet for {num(R.pet_adopt_cost)} 💎!\n\n{_card(p)}\n\n<i>Rarer pets come from eggs found with /hunt.</i>")
    elif sub == "feed":
        p, lv = await pets.feed(uid)
        up = f"\n🎉 <b>Level up!</b> Now level {p.level}." if lv else ""
        await m.reply(f"🍖 Yum! +{R.pet_feed_xp} XP for {num(R.pet_feed_cost)} 💎.{up}\n\n{_card(p)}")
    elif sub in ("name", "rename"):
        if not rest.strip(): raise GameError("Usage: /pet name &lt;new name&gt;")
        p = await pets.rename(uid, rest)
        await m.reply(f"✏️ Renamed!\n\n{_card(p)}")
    else:
        p = await pets.get(uid)
        if p is None:
            await m.reply(f"🐾 <b>You have no pet yet.</b>\n\n• /pet adopt — {num(R.pet_adopt_cost)} 💎 (common pet)\n"
                          "• /hunt — you might find a rare egg!\n\nPets add attack, defence and HP in every fight.")
            return
        await m.reply(_card(p) + "\n\n/pet feed · /pet name &lt;name&gt;")


@router.message(Command("hunt", "explore"))
async def hunt(m: Message, db_user: User):
    r = await pets.hunt(db_user.id)
    k = r["kind"]
    if k == "nothing":
        txt = "🌫 You wandered for a while but found nothing. Better luck next time!"
    elif k == "diamonds":
        txt = f"💎 You found a hidden stash: <b>+{num(r['amount'])}</b> diamonds!"
    elif k == "potion":
        txt = "🧪 You found a <b>Health Potion</b>! (/use health_potion)"
    elif k == "scroll":
        txt = "📜 You found an <b>XP Scroll</b>! (/use xp_scroll)"
    elif k == "shard":
        txt = "🔹 You found a <b>Crystal Shard</b>! Sell it in /inventory."
    elif k == "coin":
        txt = "🏺 You found an <b>Ancient Relic</b>! Worth a nice price of 💎."
    elif k == "xp":
        txt = f"✨ You trained hard and gained <b>+{r['amount']} XP</b>!"
    elif "new_pet" in r:
        txt = f"🥚 An egg hatched into a pet!\n\n{_card(r['new_pet'])}"
    else:
        txt = f"🥚 You found an egg, but you already have a pet — it ate it and gained XP!" + (" 🎉 Level up!" if r["levels"] else "")
    await m.reply(txt + f"\n\n<i>Next hunt in {R.hunt_cooldown_s // 60} min.</i>")
