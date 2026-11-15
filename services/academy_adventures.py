"""Academy adventures, DATABASE half (Part 5): the data-driven engine behind /adventure.

    async with db.tx() as s:
        scr = await adv.start(s, user_id, "haunted_library")        # costs Energy, creates the run
    async with db.tx() as s:
        scr = await adv.choose(s, user_id, run_id, step, idx)       # one button press; the last press pays the ending
    await adv.announce(user_id, scr)                                 # AFTER the commit: emits "adventure_done"

The stories are plain data (utils/content/academy_adventures.py); the pure rules (checks, transitions, rewards, validator) are in
services/academy_adventure_logic.py; every number is in game/academy_rules.py (Part 5 section).

Safety:
  * One action = one transaction (the caller's session). A refusal (AdventureBlocked, no Energy ...) raises and rolls everything back.
  * Every press carries the run's `step`; the move is an atomic UPDATE ... WHERE step = :step, so a double tap or an old button
    loses ("stale") and changes nothing. The same UPDATE claims the ending, so an ending is settled exactly once.
  * Diamonds: stu.pay_diamonds with the unique ref `academy:adv:<user>:<enrolled µs>:<run id>`: a replay pays nothing.
  * Expiry: no press for AR.ADV_EXPIRE_MINUTES closes the run (ending 'expired', no reward, no refund). Done lazily on every touch
    (the result is RETURNED, not raised, so the close is committed) and by the `academy.adventure_expire` tick.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime

from sqlalchemy import delete, select, update

from database.models import AcademyAdventureRun, AcademyAdventureTrophy, aware, utcnow
from database.session import db, insert_ignore
from game import academy_rules as AR
from services import academy_adventure_logic as L, academy_calendar as cal, academy_events as ev, academy_students as stu
from services.academy_ticks import tick
from services.errors import GameError
from utils.content import academy_adventure_texts as T
from utils.fmt import dur

log = logging.getLogger(__name__)


class AdventureBlocked(GameError):
    """An expected 'not now / not allowed' answer (HTML-ready `text`). enroll=True lets the handler offer an Enroll button.
    Raised BEFORE anything is changed, so the transaction rolls back harmlessly."""
    def __init__(self, text: str, *, enroll: bool = False):
        super().__init__(text)
        self.text, self.enroll = text, enroll


# ------------------------------------------------------------------ context / gates
def _us(dt: datetime) -> int:
    return int(aware(dt).timestamp() * 1_000_000)


async def _ctx(s, user_id: int, now: datetime):
    season = await cal.ensure_season(s, now)
    ph = cal.phase_at(aware(season.start_at), now)
    st = await stu.get_student(s, user_id, season.id, lock=True)
    return season, ph, st


def _gate(season, ph, st, now: datetime):
    """The enrolled student, or AdventureBlocked (break / not started / not enrolled)."""
    if ph.key == AR.BREAK_KEY:
        raise AdventureBlocked(T.BREAK)
    if ph.day == 0:
        raise AdventureBlocked(T.NOT_STARTED)
    if st is None:
        win = cal.enroll_window(aware(season.start_at), now)
        raise AdventureBlocked(T.NEED_ENROLL if win.open else T.NEED_ENROLL_CLOSED, enroll=win.open)
    return st


# ------------------------------------------------------------------ runs
async def _unfinished(s, student_id: int) -> list[AcademyAdventureRun]:
    q = (select(AcademyAdventureRun).where(AcademyAdventureRun.student_id == student_id, AcademyAdventureRun.finished.is_(False))
         .order_by(AcademyAdventureRun.id.desc()))
    return list((await s.execute(q)).scalars())


async def _live_run(s, student_id: int, now: datetime) -> AcademyAdventureRun | None:
    """The newest run that is still alive. Every unfinished run that timed out is closed on the way (ending 'expired').
    Older unfinished runs behind a live one are closed too: a student has at most one live run."""
    live = None
    for run in await _unfinished(s, student_id):
        if L.expired(aware(run.updated_at), now):
            run.finished, run.ending = True, "expired"
        elif live is None:
            live = run
        else:
            run.finished, run.ending = True, "expired"
    return live


async def finished_today(s, student_id: int, season_start: datetime, day: int) -> int:
    """How many runs the student FINISHED (a real ending, not expired / quit) on season day `day`. The finish time is the day."""
    rows = (await s.execute(select(AcademyAdventureRun.ending, AcademyAdventureRun.updated_at)
                            .where(AcademyAdventureRun.student_id == student_id, AcademyAdventureRun.finished.is_(True)))).all()
    start = aware(season_start)
    return sum(1 for ending, at in rows
               if ending and ending not in L.ENDED_NO_REWARD and cal.phase_at(start, aware(at)).day == day)


async def _claim(s, run_id: int, step: int, node: str, state: dict, now: datetime, *, finished: bool, ending: str | None) -> bool:
    """Atomic move: only succeeds if the run is still unfinished AND still at `step`. False = a stale or double press."""
    res = await s.execute(
        update(AcademyAdventureRun)
        .where(AcademyAdventureRun.id == run_id, AcademyAdventureRun.finished.is_(False), AcademyAdventureRun.step == step)
        .values(node=node, state_json=L.dump_state(state), step=step + 1, updated_at=now, finished=finished, ending=ending))
    return res.rowcount == 1


def _node_screen(adv, node_key: str, state: dict, st, run_id: int, step: int, updated_at: datetime, now: datetime,
                 line: str = "") -> L.Screen:
    node = adv.nodes[node_key]
    return L.Screen(
        kind="node", adventure=adv.key, run_id=run_id, step=step, node=node_key, text=node.text, line=line,
        choices=L.node_choices(adv, node, state, stu.stats_of(st), st.stream), fest=bool(state.get("fest")),
        seconds_left=L.seconds_left(updated_at, now))


# ------------------------------------------------------------------ the list screen
async def list_view(s, user_id: int, now: datetime | None = None) -> L.ListView:
    """What /adventure shows. AdventureBlocked when the player cannot use adventures at all (break, not enrolled)."""
    now = aware(now) if now else utcnow()
    season, ph, st = await _ctx(s, user_id, now)
    st = _gate(season, ph, st, now)
    day, hour = ph.day, cal.local(now).hour
    live = await _live_run(s, st.id, now)
    entries = []
    for key in L.CONTENT.ORDER:
        a = L.CONTENT.ADVENTURES[key]
        reason = L.closed_reason(a, day, hour)
        entries.append(L.ListEntry(a.key, a.name, a.emoji, a.blurb, a.energy, not reason, reason))
    live_run = None
    if live is not None:
        a = L.get(live.adventure_key)
        live_run = L.LiveRun(live.adventure_key, a.name if a else live.adventure_key, a.emoji if a else "🗺", live.id, live.step,
                             L.seconds_left(aware(live.updated_at), now))
    trophies = (await s.execute(select(AcademyAdventureTrophy.kind, AcademyAdventureTrophy.name)
                                .where(AcademyAdventureTrophy.season_id == season.id, AcademyAdventureTrophy.user_id == user_id)
                                .order_by(AcademyAdventureTrophy.earned_at))).all()
    return L.ListView(day=day, phase_name=ph.name, phase_emoji=ph.emoji, fest=day == AR.ADV_FEST_DAY, entries=entries, live=live_run,
                      done_today=await finished_today(s, st.id, season.start_at, day), energy=stu.current_energy(st, now),
                      trophies=[(k, n) for k, n in trophies])


# ------------------------------------------------------------------ start
async def start(s, user_id: int, key: str, now: datetime | None = None, rng: random.Random | None = None) -> L.Screen:
    """Start (or resume) adventure `key`. Costs the adventure's Energy; refuses (nothing changed) when it is closed, the player
    already finished AR.ADV_MAX_PER_DAY today, is in another adventure, or lacks Energy. Pressing Start on the adventure that is
    already running just resumes it for free (a double tap never charges twice)."""
    now = aware(now) if now else utcnow()
    adv = L.get(key)
    if adv is None:
        raise AdventureBlocked(T.UNKNOWN)
    season, ph, st = await _ctx(s, user_id, now)
    st = _gate(season, ph, st, now)
    day = ph.day
    if day < AR.ADV_FIRST_DAY:
        raise AdventureBlocked(T.TOO_EARLY)
    reason = L.closed_reason(adv, day, cal.local(now).hour)
    if reason:
        raise AdventureBlocked(T.CLOSED.format(name=adv.name, reason=reason))
    live = await _live_run(s, st.id, now)
    if live is not None:
        if live.adventure_key == key:
            state = L.load_state(live.state_json)
            return _node_screen(adv, live.node, state, st, live.id, live.step, aware(live.updated_at), now)
        other = L.get(live.adventure_key)
        raise AdventureBlocked(T.ACTIVE_OTHER.format(name=other.name if other else live.adventure_key))
    done = await finished_today(s, st.id, season.start_at, day)
    if done >= AR.ADV_MAX_PER_DAY:
        resets = dur(int((aware(ph.ends_at) - now).total_seconds()))
        raise AdventureBlocked(T.DAILY_LIMIT.format(n=done, limit=AR.ADV_MAX_PER_DAY, resets=resets))
    await stu.spend_energy(s, user_id, season.id, adv.energy, now)           # GameError if short: nothing is spent
    state = L.new_state(day)
    state["trail"] = [adv.start]
    run = AcademyAdventureRun(student_id=st.id, adventure_key=key, node=adv.start, state_json=L.dump_state(state),
                              started_at=now, updated_at=now, finished=False, step=0)
    s.add(run)
    await s.flush()
    return _node_screen(adv, adv.start, state, st, run.id, 0, now, now)


async def current(s, user_id: int, now: datetime | None = None) -> L.Screen | None:
    """The player's live run as a screen (to continue it), or None."""
    now = aware(now) if now else utcnow()
    season, ph, st = await _ctx(s, user_id, now)
    st = _gate(season, ph, st, now)
    live = await _live_run(s, st.id, now)
    if live is None:
        return None
    adv = L.get(live.adventure_key)
    if adv is None or live.node not in adv.nodes:
        live.finished, live.ending = True, "expired"
        return None
    return _node_screen(adv, live.node, L.load_state(live.state_json), st, live.id, live.step, aware(live.updated_at), now)


# ------------------------------------------------------------------ one button press
async def choose(s, user_id: int, run_id: int, step: int, idx: int, now: datetime | None = None,
                 rng: random.Random | None = None) -> L.Screen:
    """Press button `idx` of the node the run is on. `step` must be the step printed on the buttons. A stale / double press, an
    expired or finished run is ANSWERED (kind 'stale' / 'expired'), not raised, and changes nothing but the expiry itself.
    The press that reaches an ending claims it, pays it ONCE and returns kind 'ending'."""
    now = aware(now) if now else utcnow()
    rng = rng or random
    season, ph, st = await _ctx(s, user_id, now)
    st = _gate(season, ph, st, now)
    run = (await s.execute(select(AcademyAdventureRun).where(AcademyAdventureRun.id == run_id,
                                                              AcademyAdventureRun.student_id == st.id))).scalar_one_or_none()
    adv = L.get(run.adventure_key) if run is not None else None
    if run is None or adv is None:
        return L.Screen(kind="stale", message=T.GONE)
    if run.finished:
        return L.Screen(kind="expired" if run.ending == "expired" else "stale",
                        message=T.EXPIRED if run.ending == "expired" else T.GONE, adventure=adv.key, run_id=run.id)
    if L.expired(aware(run.updated_at), now):
        run.finished, run.ending = True, "expired"
        return L.Screen(kind="expired", message=T.EXPIRED, adventure=adv.key, run_id=run.id)
    if run.step != step:
        return L.Screen(kind="stale", message=T.STALE, adventure=adv.key, run_id=run.id)
    state = L.load_state(run.state_json)
    try:
        mv = L.advance(adv, run.node, state, idx, stu.stats_of(st), st.stream, rng)
    except L.BadChoice:
        return L.Screen(kind="stale", message=T.STALE, adventure=adv.key, run_id=run.id)
    line = L.check_line(mv.check) if mv.check else ""
    target = adv.nodes[mv.node]

    if target.ending is None:                                              # ---- the story goes on
        if not await _claim(s, run.id, step, mv.node, mv.state, now, finished=False, ending=None):
            return L.Screen(kind="stale", message=T.STALE, adventure=adv.key, run_id=run.id)
        return _node_screen(adv, mv.node, mv.state, st, run.id, step + 1, now, now, line)

    # ---- an ending: claim it (exactly once), then pay
    if not await _claim(s, run.id, step, mv.node, mv.state, now, finished=True, ending=mv.node):
        return L.Screen(kind="stale", message=T.STALE, adventure=adv.key, run_id=run.id)
    end = target.ending
    rw = L.rewards_for(end, bool(mv.state.get("fest")))
    paid = 0
    if rw.diamonds > 0:
        ref = L.reward_ref(user_id, _us(st.enrolled_at), run.id)
        if await stu.pay_diamonds(s, user_id, rw.diamonds, ref, kind="academy_adv",
                                  meta={"season": season.id, "adventure": adv.key, "ending": mv.node, "fest": rw.fest}):
            paid = rw.diamonds
    gains: dict[str, int] = {}
    if rw.stats:
        before = stu.stats_of(st)
        await stu.add_stats(s, user_id, season.id, rw.stats)
        after = stu.stats_of(st)
        gains = {k: after[k] - before[k] for k in rw.stats}
    trophy_new = False
    if rw.trophy is not None:
        trophy_new = await insert_ignore(s, AcademyAdventureTrophy, dict(
            season_id=season.id, user_id=user_id, key=rw.trophy.key, kind=rw.trophy.kind, name=rw.trophy.name,
            adventure_key=adv.key, earned_at=now))
    done = await finished_today(s, st.id, season.start_at, ph.day)
    data = dict(season_id=season.id, student_id=st.id, run_id=run.id, adventure=adv.key, ending=mv.node, mood=end.mood, day=ph.day,
                fest=rw.fest, diamonds=paid, base_diamonds=rw.base_diamonds, gains=gains,
                trophy=rw.trophy.key if rw.trophy else None, trophy_new=trophy_new,
                wins=int(mv.state.get("wins", 0)), losses=int(mv.state.get("losses", 0)))
    return L.Screen(kind="ending", adventure=adv.key, run_id=run.id, step=step + 1, node=mv.node, text=target.text, line=line,
                    fest=rw.fest, title=end.title, mood=end.mood, rewards=rw, paid=paid, gains=gains, trophy_new=trophy_new,
                    gif=end.gif, done_today=done, event_data=data)


async def quit_run(s, user_id: int, run_id: int, now: datetime | None = None) -> L.Screen:
    """Leave the adventure (no reward, Energy is not refunded). Idempotent: leaving a finished run just says so."""
    now = aware(now) if now else utcnow()
    season, ph, st = await _ctx(s, user_id, now)
    st = _gate(season, ph, st, now)
    run = (await s.execute(select(AcademyAdventureRun).where(AcademyAdventureRun.id == run_id,
                                                              AcademyAdventureRun.student_id == st.id))).scalar_one_or_none()
    if run is None or run.finished:
        return L.Screen(kind="stale", message=T.GONE)
    res = await s.execute(update(AcademyAdventureRun).where(AcademyAdventureRun.id == run.id, AcademyAdventureRun.finished.is_(False))
                          .values(finished=True, ending="quit", updated_at=now, step=AcademyAdventureRun.step + 1))
    if res.rowcount != 1:
        return L.Screen(kind="stale", message=T.GONE)
    return L.Screen(kind="quit", adventure=run.adventure_key, run_id=run.id, message=T.QUIT)


async def announce(user_id: int, scr: L.Screen) -> None:
    """Emit `adventure_done` for a finished adventure. Call it AFTER the transaction committed. Never raises."""
    if scr.kind != "ending":
        return
    try:
        await ev.emit(ev.ADVENTURE_DONE, user_id, **scr.event_data)
    except Exception:
        log.exception("academy: announcing adventure_done failed")


# ------------------------------------------------------------------ expiry tick + reset
async def expire_stale(s, now: datetime) -> int:
    """Close every unfinished run with no press for AR.ADV_EXPIRE_MINUTES (ending 'expired'). Returns how many. Idempotent."""
    now = aware(now)
    rows = (await s.execute(select(AcademyAdventureRun.id, AcademyAdventureRun.updated_at)
                            .where(AcademyAdventureRun.finished.is_(False)))).all()
    ids = [rid for rid, at in rows if L.expired(aware(at), now)]
    if not ids:
        return 0
    res = await s.execute(update(AcademyAdventureRun).where(AcademyAdventureRun.id.in_(ids), AcademyAdventureRun.finished.is_(False))
                          .values(finished=True, ending="expired"))
    return int(res.rowcount or 0)


@tick(every=600, name="academy.adventure_expire")
async def adventure_expire_tick(bot, now: datetime) -> None:
    async with db.tx() as s:
        n = await expire_stale(s, now)
    if n:
        log.info("academy: %d adventure run(s) expired", n)


@ev.subscribe(ev.RESET)
async def _on_reset(user_id, **data) -> None:
    """Staff reset: forget every adventure run and trophy (Part 1 then deletes students and seasons)."""
    async with db.tx() as s:
        await s.execute(delete(AcademyAdventureRun))
        await s.execute(delete(AcademyAdventureTrophy))
