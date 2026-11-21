#!/usr/bin/env python3
"""Word Chain addon - the ONLY edit to an existing file: utils/helpcatalog.py (so /help lists every new command).

Run it once from the repository root, after copying the addon files over the repo:
    python scripts/apply_wordchain_addon.py
It is idempotent (safe to run twice) and it NEVER touches MENU - the Telegram '/' menu stays exactly as it was,
so none of the Word Chain commands show up there (they are only in /help and inside the game).
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILE = ROOT / "utils" / "helpcatalog.py"

DESC_WORDCHAIN = """    ('wordchain', '⛓ Word Chain (groups): classic mode - say a word starting with the last letter of the previous word, slow or stuck = eliminated, last one standing wins. Words get longer and the clock shorter! /wc opens the lobby'),
    ('starthard', '🔥 Word Chain HARD mode: starts at 5+ letters, a new level every round and a much shorter clock'),
    ('startchosenfirstletter', '🔤 Word Chain: every word of the game must start with ONE letter - random, or pick it yourself: /startcfl k'),
    ('startbannedletters', '🚫 Word Chain: a few letters are banned - no word may contain them'),
    ('startrequiredletter', '📌 Word Chain: every turn has a required letter that the word must contain'),
    ('startelimination', '💀 Word Chain ELIMINATION: everybody plays each round, a word scores its length, the lowest score of the round is out'),
    ('wcstatus', '📋 Word Chain: whose turn it is, who is still alive, the last words'),
    ('wcmodes', '🎛 Word Chain: every game mode and the command that starts it'),
    ('wchelp', '❓ Word Chain: how to play + every Word Chain command'),
    ('wcstats', '📊 Your Word Chain stats (games, wins, words, longest word): /wcstats [global|group] [today|week|month|all] - reply to see someone else\\'s'),
    ('wclb', '🏆 Word Chain leaderboard: /wclb [global|group] [today|week|month|all]'),"""

DESC_JOIN = """    ('join', '➕ Join the open Word Chain lobby (or tap the Join button)'),
    ('flee', '🚪 Leave the Word Chain lobby - or give up the running game'),
    ('forcejoin', '➕ Word Chain: add the person you reply to into the lobby (lobby starter or group admins)'),"""

DESC_FORCESTART = """    ('forcestart', '▶️ Start the Word Chain game now (lobby starter or group admins, min 2 players)'),
    ('extend', '⏱ Word Chain: give the lobby more time - /extend [seconds] (lobby starter or group admins, 5 minutes in total at most)'),
    ('forceskip', '⏭ Word Chain: knock out the player whose turn it is (game starter or group admins)'),"""

ALIASES_NEW = """    'wordchain': ('wc', 'startclassic'),
    'starthard': ('wchard',),
    'startchosenfirstletter': ('startcfl',),
    'startbannedletters': ('startbl',),
    'startrequiredletter': ('startrl',),
    'startelimination': ('startelim',),
    'wcstatus': ('wcinfo',),
    'wcstats': ('wcscore',),
    'wclb': ('wctop', 'wcleaderboard'),"""

CATEGORY_OLD = '"wordchain", "join", "forcestart", "charades", "end"]'
CATEGORY_NEW = ('"wordchain", "starthard", "startchosenfirstletter", "startbannedletters", "startrequiredletter", "startelimination",\n'
                '              "join", "flee", "extend", "forcestart", "forcejoin", "forceskip", "wcstatus", "wcmodes", "wchelp", "wcstats", "wclb",\n'
                '              "charades", "end"]')


def sub_once(text: str, pattern: str, repl: str, what: str) -> str:
    new, n = re.subn(pattern, lambda _m: repl, text, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"✗ could not find {what} in {FILE} - the file changed upstream. Do this edit by hand, see WORDCHAIN_ADDON_INSTALL.md (step 3).")
    return new


def main() -> None:
    src = FILE.read_text(encoding="utf-8")
    if "('starthard'," in src:
        print("✓ helpcatalog.py already patched - nothing to do")
        return
    src = sub_once(src, r"^    \('wordchain', .*\),$", DESC_WORDCHAIN, "the ('wordchain', ...) description line")
    src = sub_once(src, r"^    \('join', .*\),$", DESC_JOIN, "the ('join', ...) description line")
    src = sub_once(src, r"^    \('forcestart', .*\),$", DESC_FORCESTART, "the ('forcestart', ...) description line")
    src = sub_once(src, r"^    'wordchain': \('wc',\),$", ALIASES_NEW, "the 'wordchain': ('wc',) alias line")
    if CATEGORY_OLD not in src:
        sys.exit(f"✗ could not find the Games category list in {FILE}. Do this edit by hand, see WORDCHAIN_ADDON_INSTALL.md (step 3).")
    src = src.replace(CATEGORY_OLD, CATEGORY_NEW, 1)
    FILE.write_text(src, encoding="utf-8")
    print("✓ helpcatalog.py patched (/help now lists every Word Chain command; the '/' menu is untouched)")


if __name__ == "__main__":
    main()
