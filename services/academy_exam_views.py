"""Academy exams, text rendering (Part 3). Pure: data in, HTML text out."""
from __future__ import annotations

from game import academy_rules as AR
from services import academy_calendar as cal
from utils.content import academy_exam_rounds as CARDS, academy_exam_texts as T
from utils.fmt import esc

RISK_ICON = {"safe": "🟢", "balanced": "🟡", "risky": "🔴"}
STAT_SHORT = {"knowledge": "🧠 Knowledge", "creativity": "🎨 Creativity", "charisma": "💬 Charisma", "fitness": "💪 Fitness"}


def head(kind: str, n: int, total: int, secs: int | None = None) -> str:
    s = AR.ROUND_SECONDS if secs is None else secs
    return T.ROUND_HEAD.format(emoji=AR.EXAM_EMOJI[kind], name=AR.EXAM_NAMES[kind], n=n, total=total, secs=s)


def intro(kind: str) -> str:
    return T.INTRO.format(emoji=AR.EXAM_EMOJI[kind], name=AR.EXAM_NAMES[kind], rounds=AR.EXAM_ROUNDS[kind], secs=AR.ROUND_SECONDS)


def question_text(scr) -> str:
    card = CARDS.BY_KEY[scr.card_key]
    tag = T.TAG_STREAM.format(stream=esc(AR.STREAMS[card.stream].name)) if card.stream else ""
    body = [head(scr.kind, scr.n, scr.total, scr.seconds_left), "",
            T.CARD.format(title=esc(card.title), tag=tag, text=esc(card.text)),
            f"<i>Difficulty: {T.DIFF[card.difficulty]}</i>", ""]
    body += [f"{i + 1}. {RISK_ICON[b.risk]} {esc(b.label)} — {STAT_SHORT[b.stat]}" for i, b in enumerate(card.buttons)]
    text = "\n".join(body)
    return (intro(scr.kind) + "\n\n" + text) if scr.first else text


def button_labels(card_key: str) -> list[str]:
    card = CARDS.BY_KEY[card_key]
    return [f"{i + 1} {RISK_ICON[b.risk]} {b.label}"[:60] for i, b in enumerate(card.buttons)]


def result_text(scr) -> str:
    last = scr.last
    card = CARDS.BY_KEY[last["card_key"]]
    lines = [head(scr.kind, scr.n, scr.total, 0).split("·  ⏱")[0].strip(), f"<b>{esc(card.title)}</b>", ""]
    if last["timed_out"]:
        lines.append(T.RESULT_TIMEOUT)
    else:
        lines.append((T.RESULT_WIN if last["success"] else T.RESULT_LOSE).format(line=esc(last["line"])))
        sign = "+" if last["points"] >= 0 else ""
        lines.append("<i>" + T.POINTS.format(sign=sign, pts=last["points"], stat=STAT_SHORT[last["stat"]], risk=last["risk"],
                                              chance=last["chance"]) + "</i>")
        if last.get("focused"):
            lines.append(T.FOCUS_DONE)
    return "\n".join(lines)


def final_text(scr) -> str:
    reward = T.REWARD.format(n=scr.reward) if scr.reward else ""
    tpl = T.FINAL_EXPIRED if scr.expired else T.FINAL
    return tpl.format(emoji=AR.EXAM_EMOJI[scr.kind], name=AR.EXAM_NAMES[scr.kind], score=scr.score, grade=scr.grade,
                      gpa=scr.gpa, won=scr.won, total=scr.total, reward=reward)


def grades_text(rep: dict) -> str:
    season = rep["season"]
    title = f"Season {season.number}" + (f" · {esc(season.theme)}" if season.theme else "")
    out = [T.GRADES_HEAD.format(title=title), ""]
    for e in rep["exams"]:
        k = e["kind"]
        if e["state"] == "done":
            st = f"<b>{e['grade']}</b> · {e['score']:.1f}/100" + (f" · 💎 +{e['reward']}" if e["reward"] else "")
        elif e["state"] == "in_progress":
            st = f"in progress ({e['rounds']}/{e['total']} rounds) — /exam to continue"
        elif e["state"] == "open":
            st = "open now! Use /exam"
        elif e["state"] == "upcoming":
            st = f"opens {cal.fmt_when(e['opens'])}"
        else:
            st = "missed (counts as 0)"
        out.append(T.GRADES_LINE.format(emoji=AR.EXAM_EMOJI[k], name=AR.EXAM_NAMES[k], status=st))
    if rep["score"] is None:
        out += ["", T.GRADES_NONE]
        return "\n".join(out)
    out.append(T.GPA_LINE.format(gpa=rep["gpa"], grade=rep["grade"], prov=" (provisional)" if rep["provisional"] else ""))
    if rep["probation"]:
        out.append(T.PROBATION.format(limit=AR.PROBATION_GPA))
    if rep["rank"]:
        out.append(T.RANK_LINE.format(rank=rep["rank"], total=rep["ranked"]))
    return "\n".join(out)


def rank_text(data: dict) -> str:
    scope = "this group" if data["scope"] == "group" else "everyone"
    out = [T.RANK_HEAD.format(scope=scope), ""]
    if not data["top"]:
        out.append(T.RANK_EMPTY)
        return "\n".join(out)
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}
    for r in data["top"]:
        out.append(f"{medal.get(r.rank, f'{r.rank}.')} {esc(r.name)} — GPA {r.gpa:.2f} ({r.grade})" + (" ⚠️" if r.probation else ""))
    me = data["me"]
    if me and me.rank > AR.RANK_TOP:
        out.append("…")
    out.append(T.RANK_ME.format(rank=me.rank, gpa=me.gpa, grade=me.grade) if me else T.RANK_NOT_YET)
    if data.get("provisional"):
        out.append("<i>" + T.RANK_PROV + "</i>")
    return "\n".join(out)
