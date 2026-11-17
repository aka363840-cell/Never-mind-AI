"""Pure-Python text for /waifu (no aiogram / database), so it can be tested anywhere."""
HOURS = 24


def tier(bond: int) -> str:
    return ("💍 Soulmates" if bond >= 90 else "💘 Madly in love" if bond >= 70 else "💞 Strong spark" if bond >= 50
            else "🌸 Slow burn" if bond >= 30 else "🌱 Just getting started")


def bar(bond: int) -> str:
    n = max(0, min(10, round(bond / 10)))
    return "▰" * n + "▱" * (10 - n)


def left_text(seconds: int) -> str:
    h, m = divmod(max(0, seconds) // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def caption(owner: str, waifu: str, bond: int, seconds_left: int) -> str:
    """HTML caption. owner / waifu are ready-made HTML (e.g. clickable mentions)."""
    return (f"✨ {owner}'s Today's Waifu ✨\n\n"
            "╭──────────────\n"
            f"┊ 💖 {waifu}\n"
            f"┊ 💗 Bond <b>{bond}%</b>  {bar(bond)}\n"
            f"┊ {tier(bond)}\n"
            "╰──────────────\n"
            f"<i>🕒 New pick in {left_text(seconds_left)}</i>")
