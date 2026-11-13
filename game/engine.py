"""Pure, DB-free fight resolver. Deterministic for a given RNG seed (tests rely on this)."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from game.rules import R

STRATEGIES = {  # (attack mult, defence mult)
    "a": ("⚔️ Aggressive", 1.25, 0.80),
    "b": ("⚖️ Balanced", 1.0, 1.0),
    "d": ("🛡 Defensive", 0.85, 1.30),
}


def stance_help() -> str:
    """One line explaining what each stance does, built from STRATEGIES so it can't go stale."""
    def eff(a: float, d: float) -> str:
        parts = [f"{a - 1:+.0%} attack" if a != 1 else "", f"{d - 1:+.0%} defence" if d != 1 else ""]
        return ", ".join(p for p in parts if p) or "no change"
    return " · ".join(f"{lbl}: {eff(a, d)}" for lbl, a, d in STRATEGIES.values())


@dataclass
class Fighter:
    uid: int
    name: str
    hp: int
    atk: float
    dfn: float
    power: str | None = None        # ACTIVE power key (already validated as active), else None
    power_mult: float = 1.0         # 1 + level bonus
    charges: int = 0                # for PHANTOM / PHOENIX
    start_hp: int = 0
    consumed: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.start_hp = self.hp
        if self.power == "REAPER":
            self.dfn *= 0.9


@dataclass
class FightResult:
    winner: int | None
    hp: dict[int, int]
    dealt: dict[int, int]
    log: list[str]


def _hit(src: Fighter, dst: Fighter, rng: random.Random, log: list[str]) -> int:
    dmg = src.atk * rng.uniform(0.85, 1.15)
    crit = rng.random() < R.crit_chance
    if crit:
        dmg *= 1.5
    dmg = max(1, round(dmg - dst.dfn * 0.6))
    if src.power == "REAPER":
        dmg = round(dmg * (1 + 0.2 * src.power_mult))  # +20% at level 1, strength scales +10%/level
    if dst.power == "PHANTOM" and dst.charges > 0 and not crit:
        dst.charges -= 1
        dst.consumed.add("PHANTOM")
        log.append(f"👻 {dst.name} phases out — hit avoided!")
        return 0
    if dst.power == "PHOENIX" and dst.charges > 0 and dmg >= dst.hp:
        dst.charges -= 1
        dst.consumed.add("PHOENIX")
        dmg = dst.hp - 1
        log.append(f"🔥 {dst.name} rises from the ashes with 1 HP!")
    dst.hp = max(0, dst.hp - dmg)
    log.append(f"{'💥' if crit else '🗡'} {src.name} hits {dst.name} for {dmg}" + (" (CRIT)" if crit else ""))
    if dst.power == "MIRROR" and dmg > 0 and dst.hp > 0:
        refl = min(15, int(dmg * 0.25 * dst.power_mult))
        refl = min(refl, max(0, src.hp - 1))
        if refl:
            src.hp -= refl
            log.append(f"🪞 {dst.name} reflects {refl} back")
    return dmg


def resolve(a: Fighter, d: Fighter, rng: random.Random | None = None, a_first: bool = True) -> FightResult:
    rng = rng or random.Random()
    log: list[str] = []
    dealt = {a.uid: 0, d.uid: 0}
    order = (a, d) if a_first else (d, a)
    for rnd in range(1, R.fight_rounds + 1):
        for src in order:
            dst = d if src is a else a
            if src.hp <= 0 or dst.hp <= 0:
                continue
            dealt[src.uid] += _hit(src, dst, rng, log)
        if a.hp <= 0 or d.hp <= 0:
            break
    if a.hp <= 0 and d.hp <= 0:
        winner = None
    elif a.hp <= 0:
        winner = d.uid
    elif d.hp <= 0:
        winner = a.uid
    else:
        ra, rd = a.hp / a.start_hp, d.hp / d.start_hp
        winner = a.uid if ra > rd else d.uid if rd > ra else None
    return FightResult(winner, {a.uid: a.hp, d.uid: d.hp}, dealt, log)
