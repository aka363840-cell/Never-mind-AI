"""Random group events: diamond drops and boss raids. State is in memory (a restart just cancels open events —
no money is ever minted before a player actually claims / defeats something)."""
from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.types import InlineKeyboardButton as B

from database.models import GroupSetting
from database.session import db
from game.rules import R
from keyboards.menus import kb
from services import economy
from services.errors import GameError
from services.kv import kv
from utils import content
from utils.fmt import esc, num

log = logging.getLogger(__name__)
_rng = random.SystemRandom()

ACTIVE: dict[int, float] = {}          # chat_id -> last time someone talked to the bot there
LAST_EVENT: dict[int, float] = {}
DROPS: dict[str, dict] = {}
BOSSES: dict[str, "Boss"] = {}
BOSS_NAMES = [("🐲", "Ember Drake"), ("👹", "Cave Ogre"), ("🧌", "Bridge Troll"), ("🐍", "Hydra Spawn"),
              ("💀", "Skeleton King"), ("🦂", "Giant Scorpion"), ("🧟", "Crypt Horror")]
BOSS_NAMES += content.BOSS_MORE


def note_activity(chat_id: int) -> None:
    ACTIVE[chat_id] = time.time()


@dataclass
class Boss:
    token: str
    chat_id: int
    mid: int
    emoji: str
    name: str
    hp: int
    max_hp: int
    ends: float
    damage: dict[int, int] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    last_edit: float = 0.0
    done: bool = False


def boss_text(b: Boss, note: str = "") -> str:
    filled = max(0, min(10, round(10 * b.hp / b.max_hp)))
    top = sorted(b.damage.items(), key=lambda kv_: -kv_[1])[:3]
    medals = ["🥇", "🥈", "🥉"]
    board = "\n".join(f"{medals[i]} {esc(b.names[u])} — {num(d)}" for i, (u, d) in enumerate(top)) or "<i>No hits yet — be the first!</i>"
    left = max(0, int(b.ends - time.time()))
    return (f"{b.emoji} <b>BOSS RAID: {esc(b.name)}</b>\n\n❤️ {'█' * filled}{'░' * (10 - filled)}  {num(b.hp)}/{num(b.max_hp)}\n"
            f"⏳ {left // 60}m {left % 60}s left · prize pool {num(R.boss_pool)} 💎\n\n{board}" + (f"\n\n{note}" if note else ""))


def _boss_kb(token: str):
    return kb([[B(text="⚔️ ATTACK!", callback_data=f"boss:{token}")]])


async def spawn_drop(bot: Bot, chat_id: int) -> bool:
    token, reward = secrets.token_urlsafe(5), _rng.randint(R.drop_min, R.drop_max)
    msg = await bot.send_message(chat_id, "🎁 <b>A diamond crate just dropped!</b>\nFirst one to tap grabs it 👇",
                                 reply_markup=kb([[B(text="💎 Grab it!", callback_data=f"drop:{token}")]]))
    DROPS[token] = dict(chat=chat_id, mid=msg.message_id, reward=reward, expires=time.time() + R.drop_ttl_s)
    return True


async def claim_drop(token: str, uid: int) -> int:
    d = DROPS.pop(token, None)          # single-threaded event loop => exactly one winner
    if d is None:
        raise GameError("💨 Too late — someone already grabbed it (or it vanished).")
    try:
        async with db.tx() as s:
            await economy.apply(s, uid, wallet=d["reward"], kind="drop", ref=f"drop:{token}")
    except Exception:
        DROPS[token] = d
        raise
    return d["reward"]


async def spawn_boss(bot: Bot, chat_id: int) -> bool:
    token = secrets.token_urlsafe(5)
    emoji, name = _rng.choice(BOSS_NAMES)
    b = Boss(token, chat_id, 0, emoji, name, R.boss_hp, R.boss_hp, time.time() + R.boss_duration_s)
    msg = await bot.send_message(chat_id, boss_text(b), reply_markup=_boss_kb(token))
    b.mid = msg.message_id
    BOSSES[token] = b
    return True


async def hit_boss(bot: Bot, token: str, uid: int, name: str, level: int) -> tuple[int, Boss]:
    b = BOSSES.get(token)
    if b is None or b.done or time.time() > b.ends:
        raise GameError("💨 This boss is gone.")
    if not await kv.set_nx(f"bosshit:{token}:{uid}", R.boss_hit_cooldown_s):
        raise GameError("😮‍💨 Catch your breath for a second!")
    if b.done or time.time() > b.ends:       # the boss may have died while we awaited above (simultaneous final taps)
        raise GameError("💨 This boss is gone.")
    dmg = _rng.randint(10, 25) + level * 2
    crit = _rng.random() < 0.1
    dmg = dmg * 2 if crit else dmg
    dmg = min(dmg, b.hp)
    b.hp -= dmg
    b.damage[uid] = b.damage.get(uid, 0) + dmg
    b.names[uid] = name
    if b.hp <= 0:
        b.done = True
        await _finish(bot, b, uid)
    elif time.time() - b.last_edit >= 3:
        b.last_edit = time.time()
        try:
            await bot.edit_message_text(boss_text(b), chat_id=b.chat_id, message_id=b.mid, reply_markup=_boss_kb(token))
        except Exception:
            pass
    return dmg, b


async def _finish(bot: Bot, b: Boss, last_hitter: int) -> None:
    BOSSES.pop(b.token, None)
    total = sum(b.damage.values()) or 1
    lines = []
    for uid, dmg in sorted(b.damage.items(), key=lambda x: -x[1]):
        reward = max(R.boss_min_reward, R.boss_pool * dmg // total)
        if uid == last_hitter:
            reward += R.boss_last_hit_bonus
        try:
            async with db.tx() as s:
                await economy.apply(s, uid, wallet=reward, kind="boss", ref=f"boss:{b.token}:{uid}")
            lines.append(f"{'🗡 ' if uid == last_hitter else ''}{esc(b.names[uid])}: <b>+{num(reward)}</b> 💎 ({num(dmg)} dmg)")
        except Exception:
            log.exception("boss reward failed for %s", uid)
    text = f"🏆 <b>{esc(b.name)} DEFEATED!</b>\n\n" + "\n".join(lines[:15]) + f"\n\n🗡 = final blow (+{R.boss_last_hit_bonus} 💎)"
    try:
        await bot.edit_message_text(text, chat_id=b.chat_id, message_id=b.mid)
    except Exception:
        await bot.send_message(b.chat_id, text)


async def _expire(bot: Bot) -> None:
    now = time.time()
    for token, b in list(BOSSES.items()):
        if now > b.ends and not b.done:
            b.done = True
            BOSSES.pop(token, None)
            try:
                await bot.edit_message_text(f"💨 <b>{esc(b.name)}</b> escaped into the shadows… nobody was strong enough!",
                                            chat_id=b.chat_id, message_id=b.mid)
            except Exception:
                pass
    for token, d in list(DROPS.items()):
        if now > d["expires"]:
            DROPS.pop(token, None)
            try:
                await bot.edit_message_text("💨 The crate vanished — nobody grabbed it in time.", chat_id=d["chat"], message_id=d["mid"])
            except Exception:
                pass


async def events_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(R.event_check_s)
        try:
            await _expire(bot)
            now = time.time()
            for chat_id, seen in list(ACTIVE.items()):
                if now - seen > 7200:
                    ACTIVE.pop(chat_id, None)
                    continue
                if now - seen > 5400 or now - LAST_EVENT.get(chat_id, 0) < R.event_min_gap_s or _rng.random() > R.event_chance:
                    continue
                async with db.tx() as s:
                    g = await s.get(GroupSetting, chat_id)
                if g is None or not g.games_enabled:
                    continue
                LAST_EVENT[chat_id] = now
                try:
                    await (spawn_boss if _rng.random() < 0.25 else spawn_drop)(bot, chat_id)
                except Exception as e:
                    log.info("event spawn failed in %s: %s", chat_id, e)
        except Exception:
            log.exception("events loop error")
