"""Academy social screens as HTML text (Part 4). Pure: data in, text out (no database, no Telegram, no calendar).
Every name that comes from a player goes through utils.fmt.esc."""
from __future__ import annotations

from game import academy_rules as AR
from utils.content import academy_exam_rounds as CARDS, academy_social_texts as T
from utils.fmt import esc

RISK_ICON = {"safe": "🟢", "balanced": "🟡", "risky": "🔴"}
STAT_SHORT = {"knowledge": "🧠 Knowledge", "creativity": "🎨 Creativity", "charisma": "💬 Charisma", "fitness": "💪 Fitness"}
MEDALS = ("🥇", "🥈", "🥉", "▫️")


def card_block(card_key: str) -> str:
    """The card text with its three moves (shared by duels and club battles)."""
    card = CARDS.BY_KEY[card_key]
    lines = [f"<b>{esc(card.title)}</b>", esc(card.text), ""]
    lines += [f"{i + 1}. {RISK_ICON[b.risk]} {esc(b.label)} — {STAT_SHORT[b.stat]}" for i, b in enumerate(card.buttons)]
    return "\n".join(lines)


def button_labels(card_key: str) -> list[str]:
    card = CARDS.BY_KEY[card_key]
    return [f"{i + 1} {RISK_ICON[b.risk]} {b.label}"[:60] for i, b in enumerate(card.buttons)]


def round_line(success: bool, points: int, line: str) -> str:
    return (T.DUEL_ROUND_WIN if success else T.DUEL_ROUND_LOSE).format(line=esc(line), points=points)


# ------------------------------------------------------------------ rivals
def prize_text(diamonds: int, badge: int) -> str:
    if diamonds <= 0 and badge <= 0:
        return ""
    return T.DUEL_PRIZE.format(d=diamonds, badge=T.DUEL_BADGE if badge else "")


def duel_result_text(*, result: str, name_a: str, name_b: str, total_a: int, total_b: int, walkover: bool, tie_prize: str = "",
                     win_prize: str = "") -> str:
    """result: a | b | tie | none (a / b = which side won)."""
    if result == "tie":
        return T.DUEL_RESULT_TIE.format(a=esc(name_a), b=esc(name_b), t=total_a, prize=tie_prize)
    if result in ("a", "b"):
        winner = name_a if result == "a" else name_b
        if walkover:
            return T.DUEL_WALKOVER.format(winner=esc(winner), prize=win_prize)
        wt, lt = (total_a, total_b) if result == "a" else (total_b, total_a)
        return T.DUEL_RESULT_WIN.format(winner=esc(winner), wt=wt, lt=lt, prize=win_prize)
    return T.DUEL_DONE + " Nobody finished, so there is no winner."


def rival_text(*, name: str, bot: bool, stream: str | None, knowledge: int | None, duel: str, badge_points: int) -> str:
    """`duel` = a short status line (built by the caller)."""
    head = f"🤝 <b>Your rival:</b> {'🤖 ' if bot else ''}<b>{esc(name)}</b>"
    lines = [head]
    if stream and stream in AR.STREAMS:
        s = AR.STREAMS[stream]
        lines.append(f"{s.emoji} {esc(s.name)}" + (f" · 🧠 Knowledge {knowledge}" if knowledge is not None else ""))
    lines += ["", duel, "", T.BADGE_LINE.format(n=badge_points)]
    return "\n".join(lines)


# ------------------------------------------------------------------ club standings
def clubs_text(*, title: str, rows, winner: str | None, battle_line: str, finished: bool) -> str:
    lines = [T.CLUBS_HEAD.format(title=title), ""]
    for i, r in enumerate(rows):
        c = AR.CLUBS[r.club]
        lines.append(T.CLUBS_ROW.format(medal=MEDALS[min(i, 3)], emoji=c.emoji, name=esc(c.name), total=r.total,
                                        practice=r.practice, battle=r.battle, players=r.players))
    lines += ["", battle_line]
    if finished:
        if winner:
            c = AR.CLUBS[winner]
            lines.append(T.CLUBS_WINNER.format(emoji=c.emoji, name=esc(c.name), d=AR.CLUB_WIN_DIAMONDS))
        else:
            lines.append(T.CLUBS_NO_WINNER)
    return "\n".join(lines)


def battle_card_text(club: str, card_key: str) -> str:
    c = AR.CLUBS[club]
    head = T.BATTLE_HEAD.format(emoji=c.emoji, club=esc(c.name))
    foot = T.BATTLE_FOOT.format(focus=STAT_SHORT[c.focus], energy=AR.BATTLE_ENERGY)
    return head + "\n\n" + card_block(card_key) + "\n\n" + foot


def battle_result_text(club: str, line: str, success: bool, points: int) -> str:
    c = AR.CLUBS[club]
    tpl = T.BATTLE_RESULT_WIN if success else T.BATTLE_RESULT_LOSE
    return tpl.format(line=esc(line), points=points, club=f"{c.emoji} {esc(c.name)}")


# ------------------------------------------------------------------ couple
def couple_text(*, partner: str, points: int, dates: int, done_today: bool, open_today: bool) -> str:
    if not open_today:
        today = T.DATE_CLOSED_DAY.format(day=AR.CRUSH_FROM_DAY)
    elif done_today:
        today = T.COUPLE_TODAY_DONE
    else:
        today = T.COUPLE_TODAY_FREE.format(energy=AR.DATE_ENERGY, cost=AR.DATE_DIAMONDS)
    return T.COUPLE_HEAD.format(name=esc(partner), points=points, dates=dates, today=today)


def date_text(*, partner: str, line: str, points: int, total: int, mine: dict, theirs: dict, me: str) -> str:
    def fmt(g: dict) -> str:
        return ", ".join(f"+{v} {STAT_SHORT[k]}" for k, v in g.items() if v)
    lines = f"<i>{esc(line)}</i>\n" + T.DATE_GAIN.format(who=esc(me), gains=fmt(mine)) + "\n" + T.DATE_GAIN.format(who=esc(partner), gains=fmt(theirs))
    return T.DATE_OK.format(name=esc(partner), energy=AR.DATE_ENERGY, cost=AR.DATE_DIAMONDS, lines=lines, points=points, total=total)


# ------------------------------------------------------------------ buddy
def buddy_text(*, partner: str, claimed_today: bool) -> str:
    return T.BUDDY_ACTIVE.format(name=esc(partner), k=AR.BUDDY_KNOWLEDGE_BONUS,
                                 today=T.BUDDY_TODAY_DONE if claimed_today else T.BUDDY_TODAY_FREE)
