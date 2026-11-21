#!/usr/bin/env python3
"""Dick game addon (/growdick, /pvp, /dicktop). Run ONCE from the repository root after copying the addon files over the repo:
    python scripts/apply_dick_addon.py
Edits 4 existing files (bot.py, database/models.py, handlers/arcade.py, utils/helpcatalog.py). Idempotent: safe to run twice.
New tables are created automatically on the next bot start."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

MODELS_TAIL = 'class DickPlayer(Base):\n    """🍆 /dick game: one row per player (size in cm, lifetime wins / losses in /pvp)."""\n    __tablename__ = "dick_players"\n    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)\n    size: Mapped[int] = mapped_column(Integer, default=0)\n    wins: Mapped[int] = mapped_column(Integer, default=0)\n    losses: Mapped[int] = mapped_column(Integer, default=0)\n    last_delta: Mapped[int] = mapped_column(Integer, default=0)\n    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)\n    __table_args__ = (CheckConstraint("size >= 0", name="ck_dick_size"), Index("ix_dick_size", "size"))\n\n\nclass DickChat(Base):\n    """Which group a player has played in (group leaderboard = players with a row for that chat)."""\n    __tablename__ = "dick_chats"\n    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)\n    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)\n\n\nclass DickChallenge(Base):\n    """An open /pvp <cm> challenge waiting for somebody to press ⚔️ Accept."""\n    __tablename__ = "dick_challenges"\n    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)\n    chat_id: Mapped[int] = mapped_column(BigInteger)\n    challenger_id: Mapped[int] = mapped_column(BigInteger)\n    target_id: Mapped[int | None] = mapped_column(BigInteger)          # set when the challenge was a reply to somebody\n    bet: Mapped[int] = mapped_column(Integer)\n    status: Mapped[str] = mapped_column(String(12), default="open")    # open | done | cancelled | expired\n    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)\n    __table_args__ = (Index("ix_dick_ch_open", "chat_id", "challenger_id", "status"),)\n'
DICK_INFO = 'async def _dick_info(msg: Message, user: User, reply: bool = True) -> None:\n    await (msg.reply if reply else msg.answer)(\n        "🍆 <b>Dick game</b>\\n/growdick — once every 24 h your dick randomly grows or shrinks (cm)\\n"\n        "/pvp <i>cm</i> — bet cm in a group, anyone (or the person you reply to) taps ⚔️ Accept: a random winner takes the cm\\n"\n        "/dicktop — this group\'s board, button for the overall board, names are clickable")\n\n\n'


def patch(rel: str, steps: list[tuple[str, str, str]], marker: str) -> None:
    p = ROOT / rel
    t = p.read_text(encoding="utf-8")
    if marker in t:
        print(f"= {rel}: already patched")
        return
    for old, new, _ in steps:
        if old not in t:
            sys.exit(f"! {rel}: expected text not found: {old[:60]!r} - file differs from the original, patch it by hand")
        t = t.replace(old, new, 1)
    p.write_text(t, encoding="utf-8")
    print(f"+ {rel}: patched")


def main() -> None:
    p = ROOT / "database" / "models.py"
    t = p.read_text(encoding="utf-8")
    if "class DickPlayer" in t:
        print("= database/models.py: already patched")
    else:
        p.write_text(t.rstrip("\n") + "\n\n\n" + MODELS_TAIL, encoding="utf-8")
        print("+ database/models.py: patched")

    patch("bot.py", [
        ("jumble as jumble_h, academy_routers", "jumble as jumble_h, dick as dick_h, academy_routers", ""),
        ("charades_h.router, wordseek_h.router,", "charades_h.router, dick_h.router, wordseek_h.router,", ""),
    ], "dick_h")

    patch("handlers/arcade.py", [
        ('            "🛑 /end — end the running game in the chat\\n\\n"',
         '            "🍆 /growdick · /pvp <i>cm</i> · /dicktop — grow daily, fight for cm, group &amp; overall board\\n"\n'
         '            "🛑 /end — end the running game in the chat\\n\\n"', ""),
        ('            [B(text="🎟 Solo & daily fun ➡️", callback_data="ar:solo")],',
         '            [B(text="🍆 Dick game", callback_data="ar:dk")],\n            [B(text="🎟 Solo & daily fun ➡️", callback_data="ar:solo")],', ""),
        ('    table = {"sc": do_scratch,', '    table = {"dk": _dick_info, "sc": do_scratch,', ""),
        ("# =============================================================== hub\nasync def hub(",
         DICK_INFO + "# =============================================================== hub\nasync def hub(", ""),
    ], "_dick_info")

    patch("utils/helpcatalog.py", [
        ("    ('top', '🏆 Leaderboards'),\n",
         "    ('top', '🏆 Leaderboards'),\n"
         "    ('growdick', '🍆 Once every 24 h your dick randomly grows or shrinks (cm)'),\n"
         "    ('pvp', '⚔️ Dick fight in a group: /pvp <cm> - anyone (or the person you reply to) can accept, a random winner takes the cm'),\n"
         "    ('dicktop', '🍆 Dick leaderboard: this group first, button for overall - names are clickable'),\n", ""),
        ("    'top': ('leaderboard', 'lb'),\n", "    'top': ('leaderboard', 'lb'),\n    'growdick': ('dick',),\n    'dicktop': ('dtop',),\n", ""),
        ('"charades", "end"]),', '"charades", "growdick", "pvp", "dicktop", "end"]),', ""),
    ], "growdick")
    print("Done. Restart the bot.")


if __name__ == "__main__":
    main()
