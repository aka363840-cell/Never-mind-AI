"""Academy adventures, text rendering (Part 5). PURE: takes the Screen / ListView of services/academy_adventure_logic.py and returns
HTML for Telegram. Every story text is escaped here (the content files hold plain text); the <b> tags are ours."""
from __future__ import annotations

from game import academy_rules as AR
from services import academy_adventure_logic as L
from utils.content import academy_adventure_texts as T
from utils.fmt import esc

_MAX = 3900          # Telegram's limit is 4096; keep a margin


def _head(scr: L.Screen) -> str:
    a = L.get(scr.adventure)
    if a is None:
        return ""
    fest = "  🎉 <i>Fest Day bonus</i>" if scr.fest else ""
    return f"{a.emoji} <b>{esc(a.name)}</b>{fest}"


def node_text(scr: L.Screen) -> str:
    parts = [_head(scr)]
    if scr.line:
        parts.append(f"<i>{esc(scr.line)}</i>")
    parts.append(esc(scr.text))
    parts.append(T.NODE_HINT.format(mins=max(1, -(-scr.seconds_left // 60))))
    return "\n\n".join(p for p in parts if p)[:_MAX]


def ending_text(scr: L.Screen) -> str:
    rw = scr.rewards
    parts = [_head(scr)]
    if scr.line:
        parts.append(f"<i>{esc(scr.line)}</i>")
    parts.append(esc(scr.text))
    parts.append(T.ENDING_HEAD.format(title=esc(scr.title), mood=T.MOOD_LABEL.get(scr.mood, "")))
    lines = [T.REWARD_HEAD]
    if scr.paid > 0:
        if rw is not None and rw.fest and rw.fest_diamonds > 0:
            lines.append(T.REWARD_DIAMONDS_FEST.format(paid=scr.paid, extra=rw.fest_diamonds))
        else:
            lines.append(T.REWARD_DIAMONDS.format(paid=scr.paid))
    else:
        lines.append(T.REWARD_NONE)
    for stat, n in scr.gains.items():
        if n > 0:
            lines.append(T.REWARD_STAT.format(label=AR.STAT_LABELS.get(stat, stat), n=n))
    if rw is not None and rw.trophy is not None:
        if scr.trophy_new:
            tpl = T.REWARD_TITLE_NEW if rw.trophy.kind == "title" else T.REWARD_ITEM_NEW
            lines.append(tpl.format(name=esc(rw.trophy.name)))
        else:
            lines.append(T.REWARD_TROPHY_HAD.format(name=esc(rw.trophy.name)))
    if rw is not None and rw.fest:
        lines.append(T.REWARD_FEST)
    lines.append(T.FINISHED_COUNT.format(n=scr.done_today, limit=AR.ADV_MAX_PER_DAY))
    parts.append("\n".join(lines))
    return "\n\n".join(p for p in parts if p)[:_MAX]


def message_text(scr: L.Screen) -> str:
    """expired / stale / quit: the message is one of our own HTML-ready texts."""
    return scr.message


def screen_text(scr: L.Screen) -> str:
    if scr.kind == "node":
        return node_text(scr)
    if scr.kind == "ending":
        return ending_text(scr)
    return message_text(scr)


def list_text(v: L.ListView) -> str:
    out = [T.LIST_HEAD.format(day=v.day, emoji=v.phase_emoji, phase=esc(v.phase_name))]
    if v.fest:
        out.append(T.LIST_FEST.format(pct=AR.ADV_FEST_DIAMOND_PCT, stat=AR.ADV_FEST_STAT_BONUS))
    out.append(T.LIST_INTRO)
    opened = [e for e in v.entries if e.open]
    closed = [e for e in v.entries if not e.open]
    if opened:
        out.append("\n".join(T.LIST_ENTRY_OPEN.format(emoji=e.emoji, name=esc(e.name), energy=e.energy, blurb=esc(e.blurb))
                             for e in opened))
    else:
        out.append(T.LIST_NONE)
    if closed:
        out.append("\n".join(T.LIST_ENTRY_CLOSED.format(emoji=e.emoji, name=esc(e.name), reason=esc(e.reason)) for e in closed))
    if v.live is not None:
        out.append(T.LIST_LIVE.format(emoji=v.live.emoji, name=esc(v.live.name), mins=max(1, -(-v.live.seconds_left // 60))))
    out.append(T.LIST_COUNT.format(n=v.done_today, limit=v.limit, energy=v.energy))
    if v.trophies:
        out.append(T.LIST_TROPHIES.format(items=", ".join(esc(n) for _, n in v.trophies)))
    return "\n\n".join(out)[:_MAX]
