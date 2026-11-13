"""Level-up DMs + bonus. add_xp() queues (uid, old, new); we verify against the DB afterwards, so a rolled-back
transaction can never produce a fake announcement, and the ledger ref makes the bonus pay exactly once."""
from __future__ import annotations

import asyncio
import collections
import logging

from aiogram import Bot

from database.models import AppState, User
from database.session import db
from game.rules import R
from services import economy, users
from services.errors import GameError
from utils.fmt import num
from utils.telegram import safe_send

log = logging.getLogger(__name__)
QUEUE: collections.deque[tuple[int, int, int]] = collections.deque(maxlen=2000)


async def _announce(bot: Bot, uid: int, old: int, new: int) -> bool:
    """True = the level isn't visible in the DB yet (add_xp's transaction hasn't committed) -> try again shortly."""
    async with db.tx() as s:
        u = await s.get(User, uid)
        if u is None:
            return False
        if u.level < new:
            return True
        row = await s.get(AppState, f"lvl_ann:{uid}")
        last = int(row.value) if row else old
        if last >= new:
            return False
        total = sum(min(R.level_bonus * lv, 2000) for lv in range(last + 1, new + 1))
        await economy.apply(s, uid, wallet=total, kind="level_bonus", ref=f"levelup:{uid}:{last}:{new}")
        await users.set_state(s, f"lvl_ann:{uid}", str(new))
    await safe_send(bot, uid, f"🎉 <b>LEVEL UP!</b>\nYou reached <b>level {new}</b>!\nBonus: <b>+{num(total)}</b> 💎\n"
                              f"<i>Higher level = more HP, attack and defence in every fight.</i>")
    return False


async def levelup_loop(bot: Bot) -> None:
    tries: dict[tuple[int, int], int] = {}
    while True:
        await asyncio.sleep(2)
        for _ in range(len(QUEUE)):                    # one pass over what is queued now; retries wait for the next tick
            uid, old, new = QUEUE.popleft()
            try:
                if await _announce(bot, uid, old, new):
                    n = tries[(uid, new)] = tries.get((uid, new), 0) + 1
                    if n < 5:                          # ~10 s is plenty for a transaction to commit; then drop (rolled back)
                        QUEUE.append((uid, old, new))
                        continue
            except GameError:
                pass
            except Exception:
                log.exception("level-up announce failed")
            tries.pop((uid, new), None)
