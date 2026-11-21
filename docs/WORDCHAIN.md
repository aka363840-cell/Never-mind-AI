# ⛓ Word Chain

Turn-based elimination word game for groups, modelled on the popular Telegram Word Chain bots.
Say a word that **starts with the last letter** of the previous word. Run out of time (or play a bad word) and you lose;
the last player standing wins. Words must be real English words and can't be used twice in a game.

While it's your turn just **type the word** - no command. An accepted word gets a 👍 reaction
(if the bot can't react in that chat, it falls back to a `✅ WORD` line).

## Game modes

| Mode | Start command | Rules |
|---|---|---|
| ⛓ Classic | `/startclassic` · `/wordchain` · `/wc` | 3+ letters, 45 s. Every 2 rounds: +1 letter, −5 s (cap 7 letters, floor 10 s). |
| 🔥 Hard | `/starthard` · `/wchard` | Starts at 5+ letters, 30 s. A new level **every** round (cap 9 letters, floor 8 s). |
| 🔤 Chosen first letter | `/startchosenfirstletter [letter]` · `/startcfl` | **Every** word must start with one letter (random, or pick it: `/startcfl k`). Any letter with enough long words is allowed. |
| 🚫 Banned letters | `/startbannedletters` · `/startbl` | 2-4 random letters are banned for the whole game - no word may contain them. |
| 📌 Required letter | `/startrequiredletter` · `/startrl` | Every turn has a required letter the word must contain (always satisfiable). |
| 💀 Elimination | `/startelimination` · `/startelim` | Everyone plays each round; a word scores its length; the **lowest score of the round is out** (ties are all out, unless everybody tied). A timeout = 0 points, not instant elimination. |

`/wcmodes` shows this list in the chat.

## Commands

**Lobby (60 s, auto-starts with 2+ players)** - the lobby message also has Join / Leave / Start now / +30s buttons.

| Command | Who | What |
|---|---|---|
| `/join` | anyone | join the lobby |
| `/flee` | players | leave the lobby - or give up the running game |
| `/extend [seconds]` | lobby starter / admins | more lobby time (default +30 s, 5 min in total at most) |
| `/forcestart` | lobby starter / admins | start now (min 2 players) |
| `/forcejoin` | lobby starter / admins | reply to someone to add them |

**During the game**

| Command | Who | What |
|---|---|---|
| `/wcstatus` (`/wcinfo`) | anyone | whose turn, who is alive, last words |
| `/forceskip` | game starter / admins | knock out the player whose turn it is |
| `/end` (`/wend`) | game starter / admins | stop the game (not counted in stats) |

**Stats** (kept in the database, table `wordchain_results`)

| Command | What |
|---|---|
| `/wcstats [global\|group] [today\|week\|month\|all]` (`/wcscore`) | games, wins + win rate, words, letters, longest word, rank. Reply to someone to see theirs. |
| `/wclb [global\|group] [today\|week\|month\|all]` (`/wctop`, `/wcleaderboard`) | leaderboard by wins |

**Help**: `/wchelp` (how to play + every command), `/wcmodes`.

> **None of these commands are in Telegram's "/" menu.** The menu is the curated `MENU` tuple in `utils/helpcatalog.py`
> and this addon never touches it (a test enforces it). Every command *is* listed in `/help` (Games) and in `/wchelp`.

## Files

| File | Role |
|---|---|
| `game/wordchain.py` | pure rules: modes, levels, word check, banned/required letters, dictionary |
| `services/wordchain.py` | pure game flow: lobby, turns, timers, eliminations, flee, skip (talks through a `send()` callback, testable) |
| `services/wordchain_stats.py` | `wordchain_results` table (created on first use), `/wcstats` + `/wclb` queries |
| `handlers/wordchain.py` | Telegram commands, lobby buttons, 👍 reaction, plain-word middleware |
| `utils/helpcatalog.py` | descriptions / aliases / Games category for `/help` (patched by `scripts/apply_wordchain_addon.py`) |
| `tests/test_wordchain.py`, `tests/test_wordchain_stats.py` | tests |

## Design notes

* One game per chat. A running WordSeek / Wordly / Hack game blocks `/startX` (they all read plain chat messages).
* Games live in memory (minutes long). A redeploy ends them; finished games are saved, aborted ones (`/end`) are not.
* A failing stats save can never break a game (`services.wordchain.recorder` is wrapped in try/except).
* Dead ends are handled: when no unused word is left for a letter the chain restarts with a new letter
  (chosen-first-letter mode, where the letter can't change, ends the game instead).
* Tuning knobs live at the top of `game/wordchain.py` (`LOBBY_S`, `LOBBY_EXTEND_S`, `LOBBY_MAX_S`, the `Mode(...)` table).
