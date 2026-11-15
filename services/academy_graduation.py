"""Academy graduation (Part 6), DATABASE half: gather the season's results, award titles, fill academy_hall_of_fame, send the Day 7
ceremony to the groups and a report card to every graduate, and serve /reportcard and /halloffame.

Texts and the CEREMONY_SECTIONS registry live in services/academy_ceremony.py. Sends use the same claim-first pattern as the
reminders (services/academy_notify.py), so the ceremony and every report card go out exactly once, even across restarts.

IMPORTANT (SQLite): never call a services.academy_notify function that opens its own transaction while a `db.tx()` of this module is
open. Gather inside one transaction, close it, then claim and send.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from database.models import (AcademyAdventureTrophy, AcademyBadge, AcademyDaily, AcademyHallOfFame, AcademyNotifyLog, AcademySeason,
                             AcademyStudent, AcademyUserPrefs, User, aware)
from database.session import db, insert_ignore
from game import academy_rules as AR
from services import (academy_calendar as cal, academy_events as ev, academy_exam_logic as EL, academy_exams as ex, academy_notify as N,
                      academy_remind_logic as RL, academy_social as soc, academy_social_logic as SL)
from services import academy_ceremony as C
from services.academy_ticks import tick

log = logging.getLogger(__name__)
CARD_BATCH = 200                       # report cards built per tick run (the rest follows on the next run)

GRADUATED, CEREMONY_DONE = ev.GRADUATED, ev.CEREMONY_DONE


# ------------------------------------------------------------------ gather (one transaction)
async def gather(s, season: AcademySeason, now: datetime, *, user_id: int | None = None, titles_from_db: bool = False) -> C.CeremonyData:
    """Everything the ceremony and the cards need. `user_id` limits the student rows to one player (for /reportcard);
    `titles_from_db` reads the awarded titles from academy_hall_of_fame instead of computing them."""
    start = aware(season.start_at)
    data = C.CeremonyData(season_id=season.id, number=season.number, session=s,
                          next_start=start + cal.cycle())
    data.results = await ex.season_results(s, season.id, now)

    q = (select(AcademyStudent, User.first_name).join(User, User.id == AcademyStudent.user_id)
         .where(AcademyStudent.season_id == season.id, User.is_flagged.is_(False), User.is_banned.is_(False)))
    if user_id is not None:
        q = q.where(AcademyStudent.user_id == user_id)
    rows = (await s.execute(q)).all()
    by_sid: dict[int, int] = {}
    for st, name in rows:
        eday = min(max(RL.season_day(start, aware(st.enrolled_at)), 1), AR.SEASON_DAYS)
        data.students[st.user_id] = C.StudentInfo(
            user_id=st.user_id, student_id=st.id, name=name or "Player", stream=st.stream, club=st.club,
            stats={k: getattr(st, k) for k in AR.STAT_KEYS}, possible=EL.possible_class_days(eday, AR.SEASON_DAYS))
        by_sid[st.id] = st.user_id
    if by_sid:
        for sid, attended in (await s.execute(select(AcademyDaily.student_id, AcademyDaily.attended)
                                              .where(AcademyDaily.student_id.in_(list(by_sid))))).all():
            if attended:
                data.students[by_sid[int(sid)]].attended += 1

    badges: dict[int, dict[str, int]] = {}
    bq = select(AcademyBadge.user_id, AcademyBadge.kind, AcademyBadge.points).where(AcademyBadge.season_id == season.id)
    if user_id is not None:
        bq = bq.where(AcademyBadge.user_id == user_id)
    for uid, kind, pts in (await s.execute(bq)).all():
        badges.setdefault(int(uid), {})[kind] = int(pts or 0)
    trophies: dict[int, list[tuple[str, str]]] = {}
    tq = select(AcademyAdventureTrophy.user_id, AcademyAdventureTrophy.kind, AcademyAdventureTrophy.name).where(
        AcademyAdventureTrophy.season_id == season.id)
    if user_id is not None:
        tq = tq.where(AcademyAdventureTrophy.user_id == user_id)
    for uid, kind, name in (await s.execute(tq.order_by(AcademyAdventureTrophy.earned_at))).all():
        trophies.setdefault(int(uid), []).append((kind, name))
    data.extra["badges"], data.extra["trophies"] = badges, trophies

    data.club_rows = await soc.standings(s, season.id)
    battle = await soc.get_battle(s, season.id)
    data.club_winner = (battle.winner if battle is not None and getattr(battle, "winner", None) else None) or SL.winning_club(data.club_rows)

    if titles_from_db:
        hq = select(AcademyHallOfFame).where(AcademyHallOfFame.season_id == season.id)
        if user_id is not None:
            hq = hq.where(AcademyHallOfFame.user_id == user_id)
        data.titles = {k: [] for k in AR.TITLE_ORDER}
        for h in (await s.execute(hq)).scalars():
            if h.title in data.titles:
                data.titles[h.title].append((h.user_id, h.name, h.detail))
    else:
        club_pts = {uid: b.get("club", 0) for uid, b in badges.items()}
        data.titles = C.award_titles(data.results, data.students, club_pts)

    prev = (await s.execute(select(AcademyHallOfFame).where(AcademyHallOfFame.title == "valedictorian",
                                                              AcademyHallOfFame.season_number < season.number)
                            .order_by(AcademyHallOfFame.season_number.desc()).limit(3))).scalars().all()
    data.hof_prev = [(h.season_number, h.name, h.gpa) for h in prev]
    return data


def card_for(data: C.CeremonyData, user_id: int, *, provisional: bool = False) -> C.CardData:
    st = data.students.get(user_id)
    res = next((r for r in data.results if r.user_id == user_id), None)
    titles = C.titles_of(user_id, data.titles) if not provisional else []
    prize = sum(AR.GRADUATION_DIAMONDS.get(k, 0) for k in titles) if AR.graduation_diamonds_on() else 0
    return C.CardData(ceremony=data, user_id=user_id, extra={"diamonds": prize} if prize else {}, name=(st.name if st else (res.name if res else "Player")), student=st, result=res,
                      ranked_total=len(data.results), titles=titles,
                      badges=data.extra.get("badges", {}).get(user_id, {}), trophies=data.extra.get("trophies", {}).get(user_id, []),
                      provisional=provisional)


async def save_hall(s, data: C.CeremonyData) -> int:
    """Write every awarded title to academy_hall_of_fame (idempotent: UNIQUE season+user+title). Returns rows newly inserted."""
    by_user = {r.user_id: r for r in data.results}
    n = 0
    for title, items in data.titles.items():
        for uid, name, detail in items:
            r = by_user.get(uid)
            n += bool(await insert_ignore(s, AcademyHallOfFame, dict(
                season_id=data.season_id, season_number=data.number, user_id=uid, name=(name or "Player")[:128], title=title,
                rank=r.rank if r else None, gpa=round(r.gpa, 2) if r else None, detail=detail)))
    return n


# ------------------------------------------------------------------ the ceremony tick
async def _seasons_to_check(now: datetime) -> list[AcademySeason]:
    async with db.tx() as s:
        cur = await cal.ensure_season(s, now)
        prev = (await s.execute(select(AcademySeason).where(AcademySeason.number == cur.number - 1))).scalar_one_or_none()
    return [x for x in (cur, prev) if x is not None]


@tick(every=60, name="academy.ceremony")
async def ceremony_tick(bot, now: datetime) -> None:
    for season in await _seasons_to_check(now):
        try:
            await _ceremony_for(bot, season, now)
        except Exception:
            log.exception("academy ceremony for season %s failed", season.number)


def _card_pending(now: datetime, season_id: int):
    """SQL: this season's students who may get a report-card DM and have not had one."""
    done = select(AcademyNotifyLog.id).where(AcademyNotifyLog.kind == "card_dm", AcademyNotifyLog.key == f"s{season_id}",
                                             AcademyNotifyLog.target_id == User.id).correlate(User).exists()
    return (select(AcademyStudent.user_id).join(User, User.id == AcademyStudent.user_id)
            .outerjoin(AcademyUserPrefs, AcademyUserPrefs.user_id == User.id)
            .where(AcademyStudent.season_id == season_id, User.is_flagged.is_(False), N.dm_eligible(now, require_recent=False), ~done)
            .order_by(AcademyStudent.id))


async def _ceremony_for(bot, season: AcademySeason, now: datetime) -> None:
    start, sid = aware(season.start_at), season.id
    if not RL.ceremony_due(start, now):
        return
    dkey = f"s{sid}"
    if await N.was_sent("ceremony_done", dkey, 0):
        return
    try:
        await ex.exam_close_tick(bot, now)               # finish any attempt whose window has closed, so every result counts
    except Exception:
        log.exception("academy ceremony: exam_close_tick failed")
    groups = await N.active_groups(now)
    gdone = await N.sent_targets("ceremony_g", dkey, groups)
    gpend = [g for g in groups if g not in gdone]
    hof_done = await N.was_sent("ceremony_hof", dkey, 0)
    quiet = RL.in_quiet_hours(now)
    async with db.tx() as s:
        pend_users = [int(u) for u in (await s.execute(_card_pending(now, sid).limit(CARD_BATCH))).scalars()]
    if hof_done and not gpend and (quiet or not pend_users):
        if not pend_users:
            await _finish(season, dkey, now)
        return

    texts: dict[int, tuple[str, bool]] = {}
    async with db.tx() as s:                              # ---- one read/write transaction: gather, award, build texts
        data = await gather(s, season, now)
        if not hof_done:
            await save_hall(s, data)
        group_text = await C.build_ceremony(data) if gpend else ""
        if pend_users and not quiet:
            for uid in pend_users:
                try:
                    card = card_for(data, uid)
                    ok = card.result is not None and card.result.grade != "F"
                    texts[uid] = (await C.build_card(card), ok)
                except Exception:
                    log.exception("academy ceremony: report card for %s failed", uid)
    await pay_prizes(season, data)                        # idempotent (unique ledger refs): a repeat or a retry pays nothing twice
    if not hof_done:                                      # events once per graduate, THEN the marker (a crash in between repeats nothing)
        for r in data.results:
            if await N.claim("graduated", dkey, r.user_id, sid, now):
                await ev.emit(GRADUATED, r.user_id, season_id=sid, number=season.number, rank=r.rank, gpa=r.gpa, grade=r.grade,
                              titles=C.titles_of(r.user_id, data.titles))
        await N.claim("ceremony_hof", dkey, 0, sid, now)

    sender = N.Sender(bot)
    for chat_id in gpend:                                 # ---- groups: the ceremony, once each
        if sender.exhausted:
            return
        if not await N.claim("ceremony_g", dkey, chat_id, sid, now):
            continue
        res = await N.deliver_group(sender, chat_id, group_text)
        if res == "error":
            await N.release("ceremony_g", dkey, chat_id)
    for uid, (text, dance) in texts.items():              # ---- graduates: the report card, once each
        if sender.exhausted:
            return
        if not await N.claim("card_dm", dkey, uid, sid, now):
            continue
        if await N.deliver_dm(sender, uid, text, gif=AR.CARD_GIF if dance else None, now=now) == "error":
            await N.release("card_dm", dkey, uid)


async def pay_prizes(season: AcademySeason, data: C.CeremonyData) -> dict[int, int]:
    """Graduation diamonds, ONLY if ACADEMY_GRADUATION_DIAMONDS=1. One payment per user and title with a unique ledger ref
    (`academy:graduation:s<season>:u<user>:<title>`), so a retry or a restart can never pay twice. Returns {user_id: total paid now or before}.
    Each payment is its own transaction (a failure for one player never blocks the others)."""
    if not AR.graduation_diamonds_on():
        return {}
    from services import academy_students as stu
    paid: dict[int, int] = {}
    for title, amount in AR.GRADUATION_DIAMONDS.items():
        for uid, _name, _detail in data.titles.get(title, ()):
            try:
                async with db.tx() as s:
                    await stu.pay_diamonds(s, uid, amount, f"graduation:s{season.id}:u{uid}:{title}", kind="academy_graduation")
                paid[uid] = paid.get(uid, 0) + amount
            except Exception:
                log.exception("academy graduation prize %s for %s failed", title, uid)
    return paid


async def _finish(season: AcademySeason, dkey: str, now: datetime) -> None:
    if await N.claim("ceremony_done", dkey, 0, season.id, now):
        async with db.tx() as s:
            n = len(await ex.season_results(s, season.id, now))
        await ev.emit(CEREMONY_DONE, 0, season_id=season.id, number=season.number, graduates=n)


# ------------------------------------------------------------------ /reportcard and /halloffame
async def report_card(user_id: int, now: datetime, last: bool = False) -> str | None:
    """The player's report as text: the running season (provisional until the ceremony time) or the last finished one.
    None if the player has never been enrolled."""
    async with db.tx() as s:
        cur = await cal.ensure_season(s, now)
        prev = (await s.execute(select(AcademySeason).where(AcademySeason.number == cur.number - 1))).scalar_one_or_none()
        ended = aware(now) >= aware(cur.end_at)
        last_done = cur if ended else prev

        async def enrolled(season) -> bool:
            return season is not None and (await s.execute(select(AcademyStudent.id).where(
                AcademyStudent.season_id == season.id, AcademyStudent.user_id == user_id))).first() is not None
        order = [last_done, cur] if last else [cur, last_done]
        target = None
        for x in order:                                    # (a loop: `await` inside a generator expression makes it async)
            if x is not None and await enrolled(x):
                target = x
                break
        if target is None:
            return None
        final = aware(now) >= RL.ceremony_window(aware(target.start_at))[0]
        data = await gather(s, target, now, user_id=user_id, titles_from_db=final)
        card = card_for(data, user_id, provisional=not final)
        return await C.build_card(card)


async def hall_of_fame(limit: int | None = None) -> str:
    limit = limit or AR.HOF_SEASONS_SHOWN
    async with db.tx() as s:
        nums = (await s.execute(select(AcademyHallOfFame.season_number).distinct()
                                .order_by(AcademyHallOfFame.season_number.desc()).limit(limit))).scalars().all()
        rows = (await s.execute(select(AcademyHallOfFame).where(AcademyHallOfFame.season_number.in_(nums or [-1]))
                                .order_by(AcademyHallOfFame.season_number.desc(), AcademyHallOfFame.rank, AcademyHallOfFame.name))).scalars().all()
    seasons = [(n, [C.HofEntry(h.title, h.user_id, h.name, h.gpa, h.detail) for h in rows if h.season_number == n]) for n in nums]
    return C.hof_text(seasons)


# ------------------------------------------------------------------ staff reset
@ev.subscribe(ev.RESET)
async def _on_reset(user_id, **data) -> None:
    """Staff reset: forget every sent-message record and the Hall of Fame (user preferences and group activity are kept)."""
    from sqlalchemy import delete
    async with db.tx() as s:
        await s.execute(delete(AcademyNotifyLog))
        await s.execute(delete(AcademyHallOfFame))
