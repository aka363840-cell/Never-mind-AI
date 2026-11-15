"""Academy students: enrolment, lazy Energy, stats, diamond payouts. Every function takes the caller's session
(one transaction per action) and an optional `now`; the pure helpers (regen_energy, current_energy) need no database.

Energy is NEVER ticked by a loop: the row stores `energy` as of `energy_at`, and the real value is computed on read.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from database.models import AcademyStudent, Transaction, aware, utcnow
from database.session import insert_ignore
from game import academy_rules as AR
from services import academy_calendar as cal
from services.errors import GameError, InsufficientFunds


# ------------------------------------------------------------------ pure energy maths
def regen_energy(stored: int, stored_at: datetime, now: datetime, per_hour: int = AR.ENERGY_REGEN_PER_HOUR,
                 maximum: int = AR.ENERGY_MAX) -> tuple[int, datetime]:
    """(energy now, new reference time). Whole points only; the unfinished fraction of the next point is kept
    by moving the reference time forward only by the time that was actually converted into points."""
    stored_at, now = aware(stored_at), aware(now)
    if stored >= maximum:
        return maximum, now
    if per_hour <= 0 or now <= stored_at:
        return max(0, stored), stored_at
    secs_per_point = 3600.0 / per_hour
    gained = int((now - stored_at).total_seconds() // secs_per_point)
    energy = min(maximum, stored + gained)
    if energy >= maximum:
        return maximum, now
    return energy, stored_at + timedelta(seconds=gained * secs_per_point)


def current_energy(st: AcademyStudent, now: datetime | None = None) -> int:
    """What the student has RIGHT NOW (read-only, nothing is written)."""
    return regen_energy(st.energy, st.energy_at, aware(now) if now else utcnow())[0]


def energy_bar(value: int, maximum: int = AR.ENERGY_MAX, cells: int = AR.ENERGY_BAR_CELLS) -> str:
    filled = 0 if maximum <= 0 else round(cells * max(0, min(value, maximum)) / maximum)
    return "▰" * filled + "▱" * (cells - filled)


def seconds_to_full(st: AcademyStudent, now: datetime | None = None) -> int:
    now = aware(now) if now else utcnow()
    e, at = regen_energy(st.energy, st.energy_at, now)
    if e >= AR.ENERGY_MAX:
        return 0
    return int((AR.ENERGY_MAX - e) * 3600.0 / AR.ENERGY_REGEN_PER_HOUR - (now - at).total_seconds())


# ------------------------------------------------------------------ reads
async def get_student(s, user_id: int, season_id: int, lock: bool = False) -> AcademyStudent | None:
    q = select(AcademyStudent).where(AcademyStudent.season_id == season_id, AcademyStudent.user_id == user_id)
    if lock:
        q = q.with_for_update()            # no-op on SQLite (writers are serialised), row lock on PostgreSQL
    return (await s.execute(q)).scalar_one_or_none()


async def _need(s, user_id: int, season_id: int) -> AcademyStudent:
    st = await get_student(s, user_id, season_id, lock=True)
    if st is None:
        raise GameError("🎓 You are not enrolled this season. Use /enroll first.")
    return st


# ------------------------------------------------------------------ enrolment
async def enroll(s, user_id: int, season, stream: str, club: str, now: datetime | None = None) -> tuple[AcademyStudent, bool]:
    """Enrol into `season`. Returns (student, created). Idempotent: a second call (double tap, retry) changes nothing
    and returns the existing student with created=False. Raises GameError for bad choices or a closed window."""
    now = aware(now) if now else utcnow()
    if stream not in AR.STREAMS or club not in AR.CLUBS:
        raise GameError("❌ Unknown stream or club.")
    existing = await get_student(s, user_id, season.id)
    if existing:
        return existing, False
    win = cal.enroll_window(season.start_at, now)
    if not win.open:
        raise GameError("🔒 Enrolment is closed for this season. See you in the next one!")
    vals = AR.starting_stats(stream)
    inserted = await insert_ignore(s, AcademyStudent, dict(
        season_id=season.id, user_id=user_id, stream=stream, club=club, energy=AR.ENERGY_START, energy_at=now,
        late_join=win.late, enrolled_at=now, **vals))
    st = await get_student(s, user_id, season.id)
    return st, bool(inserted)


# ------------------------------------------------------------------ energy / stats
async def spend_energy(s, user_id: int, season_id: int, amount: int, now: datetime | None = None) -> int:
    """Pay `amount` Energy (after regen). Returns what is left. GameError if there is not enough (nothing is spent)."""
    if amount < 0:
        raise ValueError("amount must be >= 0")
    now = aware(now) if now else utcnow()
    st = await _need(s, user_id, season_id)
    energy, at = regen_energy(st.energy, st.energy_at, now)
    if energy < amount:
        wait = int((amount - energy) * 3600.0 / AR.ENERGY_REGEN_PER_HOUR - (now - at).total_seconds())
        from utils.fmt import dur
        raise GameError(f"😴 Not enough Energy: you have <b>{energy}</b>, need <b>{amount}</b>. "
                        f"Enough regenerates in about <b>{dur(max(60, wait))}</b>.")
    st.energy, st.energy_at = energy - amount, at if energy - amount < AR.ENERGY_MAX else now
    return st.energy


async def restore_energy(s, user_id: int, season_id: int, amount: int, now: datetime | None = None) -> int:
    """Give Energy back (snacks, rest...). Capped at ENERGY_MAX. Returns the new value."""
    now = aware(now) if now else utcnow()
    st = await _need(s, user_id, season_id)
    energy, at = regen_energy(st.energy, st.energy_at, now)
    energy = min(AR.ENERGY_MAX, energy + max(0, amount))
    st.energy, st.energy_at = energy, (now if energy >= AR.ENERGY_MAX else at)
    return st.energy


async def add_stats(s, user_id: int, season_id: int, deltas: dict[str, int]) -> AcademyStudent:
    """Add (or subtract) stat points, clamped to [STAT_MIN, STAT_MAX]. Keys: knowledge, charisma, fitness, creativity."""
    bad = [k for k in deltas if k not in AR.STAT_KEYS]
    if bad:
        raise ValueError(f"unknown stat(s): {bad}")
    st = await _need(s, user_id, season_id)
    for k, d in deltas.items():
        setattr(st, k, min(AR.STAT_MAX, max(AR.STAT_MIN, getattr(st, k) + int(d))))
    return st


def stats_of(st: AcademyStudent) -> dict[str, int]:
    return {k: getattr(st, k) for k in AR.STAT_KEYS}


# ------------------------------------------------------------------ diamonds
async def pay_diamonds(s, user_id: int, amount: int, ref: str, *, kind: str = "academy", meta: dict | None = None) -> bool:
    """Pay `amount` 💎 through the economy ledger. `ref` must be unique per payment (e.g. 'graduation:s3:u42'):
    a retry with the same ref pays NOTHING and returns False. True = paid now. No real money, ever."""
    from services import economy
    if amount <= 0:
        raise ValueError("amount must be positive")
    ref = ref if ref.startswith("academy:") else f"academy:{ref}"
    if len(ref) > 128:
        raise ValueError("ref too long")
    try:
        await economy.apply(s, user_id, wallet=amount, kind=kind, ref=ref, meta=meta)
    except InsufficientFunds:
        raise
    except GameError:                          # 'already processed' - the ledger's unique ref blocked a second payment
        return False
    return True
