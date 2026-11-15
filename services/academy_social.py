"""Academy social, DATABASE half (Part 4): rivals + duels, club battles, badges, the tick handlers and /academyreset support.
(Crushes, campus couples, dates and study buddies are in services/academy_love.py.)

    async with db.tx() as s:
        scr = await soc.open_duel(s, user_id, chat_id=chat)           # /rivalduel
    await soc.deliver(bot, scr)                                        # DMs / the ONE group message / events, AFTER the commit

One action = one transaction (the caller's session). A refusal raises SocialBlocked (a GameError) and the whole transaction rolls
back, so a refused action never costs Energy and never pays. Diamonds only through stu.pay_diamonds with a unique ref.
The scenario rounds are Part 3's play_round (no new chance maths). Rules live in game/academy_rules.py (Part 4 section).
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select, update

from database.models import (AcademyBadge, AcademyBattle, AcademyBattlePlay, AcademyBuddy, AcademyBuddyBonus, AcademyCouple,
                             AcademyCoupleDate, AcademyCrush, AcademyDaily, AcademyRival, AcademyRivalDuel, AcademyRivalRound,
                             AcademySeason, AcademySocialRun, AcademyStudent, User, aware, utcnow)
from database.session import db, insert_ignore
from game import academy_rules as AR
from services import (academy_calendar as cal, academy_events as ev, academy_exam_logic as L, academy_exams as ex,
                      academy_life as life, academy_social_logic as S, academy_social_views as V, academy_students as stu)
from services.academy_ticks import tick
from services.errors import GameError
from utils.content import academy_exam_rounds as CARDS, academy_social_texts as T
from utils.fmt import esc
from utils.telegram import safe_send

log = logging.getLogger(__name__)

# kinds of events emitted here (Part 4); see services/academy_events.py
RIVAL_WIN, CRUSH_MUTUAL, DATE, CLUB_WIN = "rival_win", "crush_mutual", "date", "club_win"


class SocialBlocked(GameError):
    """An expected 'not now / not allowed' answer. `text` is the friendly message; enroll=True lets the handler offer an Enroll button."""
    def __init__(self, text: str, *, enroll: bool = False):
        super().__init__(text)
        self.text, self.enroll = text, enroll


# ------------------------------------------------------------------ shared helpers
def _us(dt: datetime) -> int:
    return int(aware(dt).timestamp() * 1_000_000)


async def ctx(s, user_id: int, now: datetime, lock: bool = True):
    """(season, phase, student|None) for `now`."""
    season = await cal.ensure_season(s, now)
    ph = cal.phase_at(aware(season.start_at), now)
    st = await stu.get_student(s, user_id, season.id, lock=lock)
    return season, ph, st


def gate(season, ph, st, now: datetime) -> AcademyStudent:
    """The enrolled student, or SocialBlocked (break / not enrolled)."""
    if ph.key == AR.BREAK_KEY:
        raise SocialBlocked(T.BREAK)
    if st is None:
        win = cal.enroll_window(aware(season.start_at), now)
        raise SocialBlocked(T.NEED_ENROLL if win.open else T.NEED_ENROLL_CLOSED, enroll=win.open)
    return st


async def names(s, ids) -> dict[int, str]:
    ids = {int(i) for i in ids if i}
    if not ids:
        return {}
    rows = (await s.execute(select(User.id, User.first_name).where(User.id.in_(ids)))).all()
    return {uid: (n or "Player") for uid, n in rows}


async def add_badge(s, season_id: int, user_id: int, kind: str, points: int) -> None:
    """Atomically add badge points (kind: rival | club). One row per season, player and kind."""
    await insert_ignore(s, AcademyBadge, dict(season_id=season_id, user_id=user_id, kind=kind, points=0))
    await s.execute(update(AcademyBadge).where(AcademyBadge.season_id == season_id, AcademyBadge.user_id == user_id,
                                               AcademyBadge.kind == kind).values(points=AcademyBadge.points + int(points)))


async def badge_points(s, user_id: int, kind: str, season_id: int | None = None) -> int:
    q = select(func.coalesce(func.sum(AcademyBadge.points), 0)).where(AcademyBadge.user_id == user_id, AcademyBadge.kind == kind)
    if season_id is not None:
        q = q.where(AcademyBadge.season_id == season_id)
    return int((await s.execute(q)).scalar_one())


# ================================================================== delivery after the commit
@dataclass
class Out:
    """What must happen AFTER the transaction committed: DMs (with an optional GIF action), ONE group message, events."""
    dm: list = field(default_factory=list)          # [(user_id, text, gif_action | None)]
    announce: tuple | None = None                   # (chat_id, text): one message per event
    events: list = field(default_factory=list)      # [(kind, user_id, data)]


async def dm(bot, user_id: int, text: str, gif: str | None = None) -> None:
    """A private message, optionally as a SFW reaction GIF (falls back to text when GIFs are off / down). Never raises."""
    try:
        if gif:
            from utils import gifs
            url = await gifs.pick(gif)
            if url:
                try:
                    await bot.send_animation(user_id, url, caption=text[:1000])
                    return
                except Exception:
                    log.info("academy: gif DM failed, sending text", exc_info=True)
        await safe_send(bot, user_id, text)
    except Exception:
        log.exception("academy: could not DM %s", user_id)


async def deliver(bot, out: Out | None, skip_chat: int | None = None) -> None:
    """Send DMs, the single group announcement and the events of a finished action. Never raises. `skip_chat`: do not
    announce in this chat (the player already sees the result there)."""
    if out is None:
        return
    for uid, text, gif in out.dm:
        await dm(bot, uid, text, gif)
    if out.announce and bot is not None and out.announce[0] != skip_chat:
        try:
            await safe_send(bot, out.announce[0], out.announce[1])
        except Exception:
            log.exception("academy: group announcement failed")
    for kind, uid, data in out.events:
        try:
            await ev.emit(kind, uid, **data)
        except Exception:
            log.exception("academy: emitting %s failed", kind)


# ================================================================== rivals: pairing
async def _prior_gpas(s, season: AcademySeason, user_ids: set[int]) -> dict[int, float]:
    """Last season's GPA of each student that graduated then (Part 3's season_results). Nobody else is listed."""
    if not user_ids:
        return {}
    prev = (await s.execute(select(AcademySeason).where(AcademySeason.number == season.number - 1))).scalar_one_or_none()
    if prev is None:
        return {}
    rows = await ex.season_results(s, prev.id, aware(prev.end_at), set(user_ids))
    return {r.user_id: float(r.gpa) for r in rows}


async def _make_bot_rival(s, season_id: int, user_id: int, now: datetime) -> None:
    seed = (season_id * 1_000_003 + user_id) % 2_000_000_000
    await insert_ignore(s, AcademyRival, dict(season_id=season_id, user_id=user_id, rival_id=None, bot_name=S.bot_name(seed),
                                              bot_seed=seed, notified=False, created_at=now))


async def pair_rivals(s, season: AcademySeason, now: datetime) -> dict | None:
    """Pair this season's students ONCE (a unique run marker makes a second call a no-op, returns None). Similar GPA and Knowledge
    become rivals; an odd one out gets a bot rival. Banned / flagged players are left out."""
    if not await insert_ignore(s, AcademySocialRun, dict(season_id=season.id, kind="rivals", created_at=now)):
        return None
    q = (select(AcademyStudent.user_id, AcademyStudent.knowledge).join(User, User.id == AcademyStudent.user_id)
         .where(AcademyStudent.season_id == season.id, User.is_banned.is_(False), User.is_flagged.is_(False)))
    rows = (await s.execute(q)).all()
    gpas = await _prior_gpas(s, season, {uid for uid, _ in rows})
    cands = [S.Candidate(uid, gpas.get(uid, AR.RIVAL_DEFAULT_GPA), int(k)) for uid, k in rows]
    pairs, odd = S.pair_students(cands)
    for a, b in pairs:
        for me, other in ((a, b), (b, a)):
            await insert_ignore(s, AcademyRival, dict(season_id=season.id, user_id=me, rival_id=other, bot_name="", bot_seed=0,
                                                      notified=False, created_at=now))
    if odd is not None:
        await _make_bot_rival(s, season.id, odd, now)
    return dict(pairs=len(pairs), bot=odd)


async def get_rival(s, user_id: int, season_id: int) -> AcademyRival | None:
    return (await s.execute(select(AcademyRival).where(AcademyRival.season_id == season_id,
                                                       AcademyRival.user_id == user_id))).scalar_one_or_none()


async def ensure_rival(s, season_id: int, user_id: int, now: datetime) -> AcademyRival | None:
    """The player's rival. A student who enrolled after the pairing ran gets a friendly bot rival on demand.
    None = the pairing has not happened yet."""
    row = await get_rival(s, user_id, season_id)
    if row is not None:
        return row
    done = (await s.execute(select(AcademySocialRun.season_id).where(AcademySocialRun.season_id == season_id,
                                                                     AcademySocialRun.kind == "rivals"))).first()
    if done is None:
        return None
    await _make_bot_rival(s, season_id, user_id, now)
    return await get_rival(s, user_id, season_id)


def rival_label(rv: AcademyRival, nm: dict[int, str]) -> tuple[str, bool]:
    """(display name, is_bot)."""
    if rv.rival_id is None:
        return rv.bot_name or "Bot", True
    return nm.get(rv.rival_id, "Player"), False


# ================================================================== rival duels
@dataclass
class DuelScreen:
    """What the handler shows. status: question | wait | done."""
    status: str
    duel_id: int
    rival_name: str
    bot: bool
    n: int = 0
    total: int = AR.RIVAL_DUEL_ROUNDS
    card_key: str = ""
    last: dict | None = None            # the card just played: card_key, choice, success, points, line
    my_total: int = 0
    result_text: str = ""
    out: Out = field(default_factory=Out)


async def _duel_row(s, season_id: int, user_a: int, lock: bool = True) -> AcademyRivalDuel | None:
    q = select(AcademyRivalDuel).where(AcademyRivalDuel.season_id == season_id, AcademyRivalDuel.user_a == user_a)
    if lock:
        q = q.with_for_update()
    return (await s.execute(q)).scalar_one_or_none()


async def _my_rounds(s, duel_id: int, user_id: int) -> list[AcademyRivalRound]:
    return list((await s.execute(select(AcademyRivalRound).where(AcademyRivalRound.duel_id == duel_id,
                                                                 AcademyRivalRound.user_id == user_id)
                                 .order_by(AcademyRivalRound.n))).scalars())


async def _totals(s, duel_id: int) -> tuple[dict[int, int], dict[int, int]]:
    """({user_id: points}, {user_id: rounds played}); the bot side is user 0."""
    rows = (await s.execute(select(AcademyRivalRound.user_id, func.sum(AcademyRivalRound.points), func.count())
                            .where(AcademyRivalRound.duel_id == duel_id).group_by(AcademyRivalRound.user_id))).all()
    return {u: int(p or 0) for u, p, _ in rows}, {u: int(c) for u, _, c in rows}


def _keys(duel: AcademyRivalDuel) -> list[str]:
    return [k for k in (duel.cards or "").split(",") if k]


async def open_duel(s, user_id: int, now: datetime | None = None, rng: random.Random | None = None,
                    chat_id: int | None = None) -> DuelScreen:
    """/rivalduel: start (first time) or resume your side of the duel with your rival. `chat_id` = the group it was typed in
    (used for the ONE result announcement). Energy is charged when you answer the first card, not here."""
    now = aware(now) if now else utcnow()
    rng = rng or random.Random()
    season, ph, st = await ctx(s, user_id, now)
    gate(season, ph, st, now)
    rv = await ensure_rival(s, season.id, user_id, now)
    if rv is None:
        raise SocialBlocked(T.RIVAL_NONE)
    human = rv.rival_id is not None
    a = min(user_id, rv.rival_id) if human else user_id
    duel = await _duel_row(s, season.id, a)
    nm = await names(s, [user_id, rv.rival_id])
    rname, bot = rival_label(rv, nm)
    if duel is not None and duel.state == "done":
        return await _done_screen(s, duel, user_id, rname, bot)
    if ph.day < AR.RIVAL_PAIR_FROM_DAY:
        raise SocialBlocked(T.DUEL_NOT_OPEN.format(day=AR.RIVAL_PAIR_FROM_DAY))
    if not S.duel_open(ph.day):
        raise SocialBlocked(T.DUEL_CLOSED.format(day=AR.RIVAL_DUEL_LAST_DAY))
    if duel is None:
        keys = S.duel_cards(rng)
        await insert_ignore(s, AcademyRivalDuel, dict(season_id=season.id, user_a=a, user_b=(max(user_id, rv.rival_id) if human else None),
                                                      cards=",".join(keys), state="open", walkover=False, chat_id=chat_id, created_at=now))
        duel = await _duel_row(s, season.id, a)
    mine = await _my_rounds(s, duel.id, user_id)
    total = AR.RIVAL_DUEL_ROUNDS
    mt = sum(r.points for r in mine)
    if len(mine) >= total:
        return DuelScreen("wait", duel.id, rname, bot, n=total, my_total=mt)
    n = len(mine) + 1
    return DuelScreen("question", duel.id, rname, bot, n=n, card_key=_keys(duel)[n - 1], my_total=mt)


async def _done_screen(s, duel: AcademyRivalDuel, user_id: int, rname: str, bot: bool, last: dict | None = None) -> DuelScreen:
    totals, _ = await _totals(s, duel.id)
    nm = await names(s, [duel.user_a, duel.user_b])
    text = _result_text(duel, totals, nm, rname if bot else "")
    return DuelScreen("done", duel.id, rname, bot, n=AR.RIVAL_DUEL_ROUNDS, last=last, my_total=totals.get(user_id, 0), result_text=text)


def _side_name(duel: AcademyRivalDuel, which: str, nm: dict[int, str], bot_name: str = "") -> str:
    uid = duel.user_a if which == "a" else duel.user_b
    return nm.get(uid, "Player") if uid else (bot_name or "Bot")


def _result_text(duel: AcademyRivalDuel, totals: dict[int, int], nm: dict[int, str], bot_name: str = "") -> str:
    ta, tb = totals.get(duel.user_a, 0), totals.get(duel.user_b or 0, 0)
    res = duel.result or "none"
    wz, tz = S.duel_prize("win", bool(duel.walkover)), S.duel_prize("tie")
    wp, tp = V.prize_text(wz.diamonds, wz.badge), V.prize_text(tz.diamonds, tz.badge)
    return V.duel_result_text(result=res, name_a=_side_name(duel, "a", nm, bot_name), name_b=_side_name(duel, "b", nm, bot_name),
                              total_a=ta, total_b=tb, walkover=bool(duel.walkover), tie_prize=tp, win_prize=wp)


async def duel_answer(s, user_id: int, duel_id: int, n: int, idx: int, now: datetime | None = None,
                      rng: random.Random | None = None) -> DuelScreen:
    """Play card `n` of your side with button `idx`. A stale / double press is refused and changes nothing."""
    now = aware(now) if now else utcnow()
    rng = rng or random.Random()
    duel = (await s.execute(select(AcademyRivalDuel).where(AcademyRivalDuel.id == duel_id).with_for_update())).scalar_one_or_none()
    if duel is None or user_id not in (duel.user_a, duel.user_b):
        raise SocialBlocked(T.NOT_YOURS)
    season = await s.get(AcademySeason, duel.season_id)
    rv = await get_rival(s, user_id, duel.season_id)
    nm = await names(s, [duel.user_a, duel.user_b])
    other = duel.user_b if user_id == duel.user_a else duel.user_a
    rname, bot = ((rv.bot_name if rv else "Bot"), True) if (other is None or other == 0) else (nm.get(other, "Player"), False)
    if duel.state == "done":
        return await _done_screen(s, duel, user_id, rname, bot)
    if now >= S.duel_deadline(aware(season.start_at)):                 # the duel closed: settle it now
        res = await _resolve(s, duel, now)
        scr = await _done_screen(s, duel, user_id, rname, bot)
        if res is not None:
            scr.out = res
        return scr
    keys = _keys(duel)
    mine = await _my_rounds(s, duel.id, user_id)
    total = AR.RIVAL_DUEL_ROUNDS
    if n != len(mine) + 1 or n > total or not 0 <= idx < 3:
        raise SocialBlocked(T.STALE)
    st = await stu.get_student(s, user_id, duel.season_id, lock=True)
    if st is None:
        raise SocialBlocked(T.NEED_ENROLL)
    if n == 1:
        await stu.spend_energy(s, user_id, duel.season_id, AR.RIVAL_DUEL_ENERGY, now)   # GameError if too tired: nothing changes
    card = CARDS.BY_KEY[keys[n - 1]]
    if idx >= len(card.buttons):
        raise SocialBlocked(T.STALE)
    r = L.play_round(st, card, idx, rng)
    if not await insert_ignore(s, AcademyRivalRound, dict(duel_id=duel.id, user_id=user_id, n=n, card_key=card.key, choice=idx,
                                                          roll=r.roll, success=r.success, points=r.points)):
        raise SocialBlocked(T.STALE)
    mt = sum(x.points for x in mine) + r.points
    last = dict(card_key=card.key, choice=idx, success=r.success, points=r.points, line=r.line)
    if n < total:
        return DuelScreen("question", duel.id, rname, bot, n=n + 1, card_key=keys[n], last=last, my_total=mt)
    # my side is complete
    if bot:
        await _play_bot_side(s, duel, st, rv)
    _, counts = await _totals(s, duel.id)
    other_key = 0 if bot else other
    if counts.get(other_key, 0) < total:
        return DuelScreen("wait", duel.id, rname, bot, n=n, last=last, my_total=mt)
    res = await _resolve(s, duel, now, presser=user_id)
    scr = await _done_screen(s, duel, user_id, rname, bot, last)
    if res is not None:
        scr.out = res
    return scr


async def _play_bot_side(s, duel: AcademyRivalDuel, st: AcademyStudent, rv: AcademyRival) -> None:
    """The bot rival plays the same cards: stats near the player's, always the move with the best expected points, its own dice."""
    rng = random.Random((rv.bot_seed or 1) * 7919 + duel.id)
    bot = S.bot_student(stu.stats_of(st), rng)
    for n, key in enumerate(_keys(duel), 1):
        card = CARDS.BY_KEY[key]
        r = L.play_round(bot, card, S.bot_choice(bot, card), rng)
        await insert_ignore(s, AcademyRivalRound, dict(duel_id=duel.id, user_id=0, n=n, card_key=key, choice=r.choice, roll=r.roll,
                                                       success=r.success, points=r.points))


async def _resolve(s, duel: AcademyRivalDuel, now: datetime, presser: int | None = None) -> Out | None:
    """Settle a duel exactly once (the 'open -> done' update is the claim; a second call returns None): winner, prizes (ledger refs),
    Rival badge point, the people to tell and the events. `presser` = the player who is looking at the screen (not DM'd)."""
    claim = await s.execute(update(AcademyRivalDuel).where(AcademyRivalDuel.id == duel.id, AcademyRivalDuel.state == "open")
                            .values(state="done", closed_at=now))
    if claim.rowcount != 1:
        return None
    total = AR.RIVAL_DUEL_ROUNDS
    totals, counts = await _totals(s, duel.id)
    ua, ub = duel.user_a, duel.user_b
    ta, tb = totals.get(ua, 0), totals.get(ub or 0, 0)
    done_a, done_b = counts.get(ua, 0) >= total, counts.get(ub or 0, 0) >= total
    walkover = False
    if done_a and done_b:
        res = S.duel_outcome(ta, tb)
    elif done_a or done_b:
        res, walkover = ("a" if done_a else "b"), True
    else:
        res = "none"
    duel.state, duel.result, duel.walkover, duel.closed_at = "done", res, walkover, now
    await s.flush()
    human_b = ub is not None
    sides = [(ua, "a")] + ([(ub, "b")] if human_b else [])
    ref0 = f"rival:{duel.season_id}:{duel.id}:{_us(duel.created_at)}"
    for uid, which in sides:
        outcome = "tie" if res == "tie" else ("win" if res == which else ("lose" if res in ("a", "b") else "none"))
        pz = S.duel_prize(outcome, walkover) if outcome != "none" else S.Prize(0, 0)
        if pz.diamonds > 0:
            await stu.pay_diamonds(s, uid, pz.diamonds, f"{ref0}:{uid}", kind="academy_rival",
                                   meta={"season": duel.season_id, "duel": duel.id, "result": outcome})
        if pz.badge > 0:
            await add_badge(s, duel.season_id, uid, "rival", pz.badge)
    nm = await names(s, [ua, ub])
    rv_bot = ""
    if not human_b:
        rv = await get_rival(s, ua, duel.season_id)
        rv_bot = rv.bot_name if rv else ""
    text = _result_text(duel, totals, nm, rv_bot)
    out = Out()
    for uid, _w in sides:
        if uid != presser:
            out.dm.append((uid, T.DUEL_DM.format(result=text), None))
    win_uid = (ua if res == "a" else ub) if res in ("a", "b") else None
    if win_uid:                                                   # a bot rival that wins is not a rival_win for anybody
        lose_uid = ub if res == "a" else ua
        out.events.append((RIVAL_WIN, win_uid, dict(season_id=duel.season_id, duel_id=duel.id, opponent_id=lose_uid or None,
                                                    bot=not human_b, walkover=walkover,
                                                    total=ta if res == "a" else tb, opponent_total=tb if res == "a" else ta)))
    if human_b and res != "none" and duel.chat_id:
        out.announce = (duel.chat_id, text)
    return out


async def duel_status(s, user_id: int, now: datetime | None = None) -> dict:
    """Everything /rival shows."""
    now = aware(now) if now else utcnow()
    season, ph, st = await ctx(s, user_id, now, lock=False)
    gate(season, ph, st, now)
    rv = await ensure_rival(s, season.id, user_id, now)
    pts = await badge_points(s, user_id, "rival")
    if rv is None:
        return dict(rival=None, badge_points=pts, day=ph.day)
    nm = await names(s, [rv.rival_id])
    rname, bot = rival_label(rv, nm)
    rst = await stu.get_student(s, rv.rival_id, season.id) if rv.rival_id else None
    human = rv.rival_id is not None
    a = min(user_id, rv.rival_id) if human else user_id
    duel = await _duel_row(s, season.id, a, lock=False)
    mine = await _my_rounds(s, duel.id, user_id) if duel else []
    return dict(rival=rv, name=rname, bot=bot, stream=rst.stream if rst else None, knowledge=rst.knowledge if rst else None,
                duel=duel, my_rounds=len(mine), badge_points=pts, day=ph.day, season_id=season.id)


# ------------------------------------------------------------------ rival notices (DM once, a few per tick run)
async def notify_rivals(bot, now: datetime) -> int:
    """Tell each newly paired student who their rival is (one DM per season). Marked in the DB first, then sent, at most
    AR.RIVAL_NOTIFY_PER_RUN per run so a big season never floods Telegram. Returns how many were sent."""
    batch: list[tuple[int, str, bool]] = []
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        rows = list((await s.execute(select(AcademyRival).where(AcademyRival.season_id == season.id, AcademyRival.notified.is_(False))
                                     .order_by(AcademyRival.id).limit(AR.RIVAL_NOTIFY_PER_RUN))).scalars())
        nm = await names(s, [r.rival_id for r in rows])
        for r in rows:
            label, is_bot = rival_label(r, nm)
            r.notified = True
            batch.append((r.user_id, label, is_bot))
    sent = 0
    for uid, label, is_bot in batch:
        tpl = T.RIVAL_NOTIFY_BOT if is_bot else T.RIVAL_NOTIFY
        await dm(bot, uid, tpl.format(name=esc(label), prize=AR.RIVAL_WIN_DIAMONDS))
        sent += 1
        await asyncio.sleep(0.05)
    return sent


# ================================================================== club battle
async def open_battle(s, season: AcademySeason, now: datetime) -> AcademyBattle | None:
    """Create the season's battle (idempotent: UNIQUE per season). Only while it can still be played."""
    start = aware(season.start_at)
    ph = cal.phase_at(start, now)
    if ph.key == AR.BREAK_KEY or not S.battle_due(ph.day):
        return await get_battle(s, season.id)
    opens, closes = S.battle_window(start)
    key = S.battle_card(f"battle:{season.number}:{start.isoformat()}")
    await insert_ignore(s, AcademyBattle, dict(season_id=season.id, card_key=key, opens_at=opens, closes_at=closes, state="open"))
    return await get_battle(s, season.id)


async def get_battle(s, season_id: int) -> AcademyBattle | None:
    return (await s.execute(select(AcademyBattle).where(AcademyBattle.season_id == season_id))).scalar_one_or_none()


async def standings(s, season_id: int) -> list[S.ClubRow]:
    """Club table: Part 2 practice points + the sum of the members' battle rounds. Best first."""
    practice = dict(await life.club_standings(s, season_id))
    battle: dict[str, int] = {}
    players: dict[str, int] = {}
    b = await get_battle(s, season_id)
    if b is not None:
        rows = (await s.execute(select(AcademyBattlePlay.club, func.sum(AcademyBattlePlay.points), func.count())
                                .where(AcademyBattlePlay.battle_id == b.id).group_by(AcademyBattlePlay.club))).all()
        for club, pts, n in rows:
            battle[club], players[club] = int(pts or 0), int(n)
    return S.club_rows(practice, battle, players)


async def clubs_view(s, user_id: int, now: datetime | None = None) -> dict:
    """Everything /clubs shows. Works for non-students too (the table is public); `can_play` tells if the player may play a round."""
    now = aware(now) if now else utcnow()
    season = await cal.ensure_season(s, now)
    start = aware(season.start_at)
    ph = cal.phase_at(start, now)
    st = await stu.get_student(s, user_id, season.id)
    b = await get_battle(s, season.id)
    rows = await standings(s, season.id)
    played = False
    if b is not None and st is not None:
        played = (await s.get(AcademyBattlePlay, (b.id, user_id))) is not None
    is_open = b is not None and b.state == "open" and aware(b.opens_at) <= now < aware(b.closes_at)
    return dict(season=season, phase=ph, student=st, battle=b, rows=rows, played=played, open=is_open,
                finished=b is not None and b.state == "closed", winner=b.winner if b else None,
                can_play=bool(is_open and st is not None and not played), closes_at=aware(b.closes_at) if b else None)


@dataclass
class BattleResult:
    club: str
    card_key: str
    choice: int
    success: bool
    points: int
    line: str
    energy: int
    rows: list


async def battle_card_for(s, user_id: int, now: datetime | None = None) -> tuple[AcademyBattle, AcademyStudent]:
    """(battle, student) if the player may play a battle round now, else SocialBlocked."""
    now = aware(now) if now else utcnow()
    season, ph, st = await ctx(s, user_id, now, lock=False)
    gate(season, ph, st, now)
    b = await get_battle(s, season.id)
    if b is None or aware(b.opens_at) > now:
        raise SocialBlocked(T.BATTLE_NOT_OPEN.format(day=AR.BATTLE_FROM_DAY))
    if b.state != "open" or now >= aware(b.closes_at):
        raise SocialBlocked(T.BATTLE_CLOSED)
    if (await s.get(AcademyBattlePlay, (b.id, user_id))) is not None:
        raise SocialBlocked(T.BATTLE_ONCE)
    return b, st


async def play_battle(s, user_id: int, battle_id: int, idx: int, now: datetime | None = None,
                      rng: random.Random | None = None) -> BattleResult:
    """Play your ONE battle round (the primary key of the play row is the once-only lock). Costs AR.BATTLE_ENERGY.
    The club's focus stat gets AR.BATTLE_FOCUS_BONUS. Everything rolls back if you are too tired."""
    now = aware(now) if now else utcnow()
    rng = rng or random.Random()
    b, st = await battle_card_for(s, user_id, now)
    if b.id != battle_id:
        raise SocialBlocked(T.STALE)
    card = CARDS.BY_KEY[b.card_key]
    if not 0 <= idx < len(card.buttons):
        raise SocialBlocked(T.STALE)
    st = await stu.get_student(s, user_id, b.season_id, lock=True)
    r = L.play_round(S.battle_student(st, st.club), card, idx, rng)
    if not await insert_ignore(s, AcademyBattlePlay, dict(battle_id=b.id, user_id=user_id, club=st.club, choice=idx, roll=r.roll,
                                                          success=r.success, points=r.points, played_at=now)):
        raise SocialBlocked(T.BATTLE_ONCE)
    left = await stu.spend_energy(s, user_id, b.season_id, AR.BATTLE_ENERGY, now)       # GameError if too tired: the play is rolled back
    return BattleResult(st.club, card.key, idx, r.success, r.points, r.line, left, await standings(s, b.season_id))


async def finalize_battle(s, battle: AcademyBattle, now: datetime) -> Out | None:
    """Close a battle whose time is over EXACTLY ONCE (the 'open -> closed' update is the claim). Winner = best club total (above 0).
    Every CONTRIBUTING member (played a battle round or practised with /club) gets a season badge and AR.CLUB_WIN_DIAMONDS
    (ledger ref per player). Returns what to deliver after the commit, or None if someone else closed it first."""
    claim = await s.execute(update(AcademyBattle).where(AcademyBattle.id == battle.id, AcademyBattle.state == "open")
                            .values(state="closed"))
    if claim.rowcount != 1:
        return None
    rows = await standings(s, battle.season_id)
    winner = S.winning_club(rows)
    battle.state, battle.winner = "closed", winner
    await s.flush()
    out = Out()
    if winner is None:
        return out
    members = (await s.execute(select(AcademyStudent.id, AcademyStudent.user_id, AcademyStudent.enrolled_at)
                               .join(User, User.id == AcademyStudent.user_id)
                               .where(AcademyStudent.season_id == battle.season_id, AcademyStudent.club == winner,
                                      User.is_banned.is_(False)))).all()
    played = set((await s.execute(select(AcademyBattlePlay.user_id).where(AcademyBattlePlay.battle_id == battle.id,
                                                                          AcademyBattlePlay.club == winner))).scalars())
    practised = set((await s.execute(select(AcademyDaily.student_id).where(AcademyDaily.club_count > 0)
                                     .where(AcademyDaily.student_id.in_([m[0] for m in members] or [0])))).scalars())
    club = AR.CLUBS[winner]
    for sid, uid, enrolled in members:
        if uid not in played and sid not in practised:
            continue
        paid = await stu.pay_diamonds(s, uid, AR.CLUB_WIN_DIAMONDS, f"clubwin:{battle.season_id}:{uid}:{_us(enrolled)}",
                                      kind="academy_club", meta={"season": battle.season_id, "club": winner})
        if not paid:
            continue
        await add_badge(s, battle.season_id, uid, "club", AR.CLUB_WIN_BADGE_POINTS)
        out.dm.append((uid, T.CLUBS_WIN_DM.format(club=f"{club.emoji} {esc(club.name)}", d=AR.CLUB_WIN_DIAMONDS), AR.SOCIAL_GIFS["club"]))
        out.events.append((CLUB_WIN, uid, dict(season_id=battle.season_id, club=winner, battle_id=battle.id, diamonds=AR.CLUB_WIN_DIAMONDS)))
    return out


# ================================================================== ticks (registered with services.academy_ticks; ONE loop)
@tick(every=300, name="academy.rival_pairing")
async def rival_pairing_tick(bot, now) -> None:
    """Day 3: pair the students once (a run marker keeps it idempotent), then DM each their rival in small batches."""
    now = aware(now) if now else utcnow()
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        ph = cal.phase_at(aware(season.start_at), now)
        if ph.key == AR.BREAK_KEY or not S.rival_pairing_due(ph.day):
            return
        done = await pair_rivals(s, season, now)
    if done:
        log.info("academy: rivals paired (%s pairs, bot rival: %s)", done["pairs"], done["bot"])
    await notify_rivals(bot, now)


@tick(every=300, name="academy.club_battle_open")
async def club_battle_open_tick(bot, now) -> None:
    """Day 3: open the club battle (UNIQUE per season, so a repeat is harmless)."""
    now = aware(now) if now else utcnow()
    async with db.tx() as s:
        season = await cal.ensure_season(s, now)
        await open_battle(s, season, now)


@tick(every=120, name="academy.social_close")
async def social_close_tick(bot, now) -> None:
    """Close battles whose time is over (winners paid once) and settle duels whose deadline passed (walkover / no result)."""
    now = aware(now) if now else utcnow()
    outs: list[Out] = []
    async with db.tx() as s:
        due = list((await s.execute(select(AcademyBattle).where(AcademyBattle.state == "open", AcademyBattle.closes_at <= now)
                                     .limit(20))).scalars())
        for b in due:
            o = await finalize_battle(s, b, now)
            if o is not None:
                outs.append(o)
        q = (select(AcademyRivalDuel, AcademySeason.start_at).join(AcademySeason, AcademySeason.id == AcademyRivalDuel.season_id)
             .where(AcademyRivalDuel.state == "open").limit(100))
        for duel, start in list((await s.execute(q)).all()):
            if now >= S.duel_deadline(aware(start)):
                o = await _resolve(s, duel, now)
                if o is not None:
                    outs.append(o)
    for o in outs:                                                       # messages and events after the commit
        await deliver(bot, o)
        await asyncio.sleep(0)


# ================================================================== /academyreset
@ev.subscribe(ev.RESET)
async def _on_reset(user_id, **data) -> None:
    """Staff reset: forget every Part 4 row (children first). Part 1 then deletes students and seasons."""
    async with db.tx() as s:
        for model in (AcademyBattlePlay, AcademyBattle, AcademyRivalRound, AcademyRivalDuel, AcademyRival, AcademyBadge, AcademyCrush,
                      AcademyCoupleDate, AcademyCouple, AcademyBuddyBonus, AcademyBuddy, AcademySocialRun):
            await s.execute(delete(model))
