# 🐙 Word Jumble (add-on, v2)

A multiplayer word unscramble game with rounds, difficulties, themes, rewards, streaks, leaderboards and an optional entry-fee pot.
Listed in `/help` (Games) but **not** in Telegram's "/" command menu.

| Command | What it does |
|---|---|
| `/jumble` or `/jum` | Start a new game: a panel asks for the settings, then **▶️ Start**. |
| `/jskip` | Vote to skip the current word (2 votes in a group, 1 in a private chat, a majority of the players in a paid game). |
| `/jlb [global\|group] [today\|week\|month\|all]` | Leaderboard (default: this group, this week; in a private chat: global). |
| `/jstats` | Your points, words, games, wins, day streak and diamonds earned today (reply to someone to see theirs). |
| `/endgame` | End the running game (the player who started it, or a group admin). Cancels an open setup / lobby. In a paid game everybody is refunded. |

## The setup panel (only the player who started the game can use it)
| Row | Choices |
|---|---|
| Mode | 🔢 **Rounds** (3 / 5 / 10 words) · 🏁 **Race** (first to 10 / 20 / 30 points; stops after 40 words if nobody gets there) |
| Difficulty | 🟢 Easy (4 letters, 30 s) · 🟡 Medium (5 letters, 40 s) · 🔴 Hard (6 letters, 55 s) · 🟣 **Extreme** (7-8 letters, 75 s) · 🌈 **Mixed** (easy → medium → hard as the game goes on) |
| Speed | ⚡ Fast (time × 0.6) · ⏱ Normal · 🐢 Relaxed (time × 1.6) |
| Theme | 🎲 any words · 🐾 animals · 🍕 food · 🌍 countries · 🌿 nature · 🏠 home (4-6 letter words; Extreme uses any words) |
| Entry (groups only) | 🆓 Free · 💰 100 / 500 / 1,000 💎 (only the amounts inside the bot's casino bet limits are shown) |

## Playing
* Each round the bot posts a scrambled word. Everybody types the real word in the chat - the **first correct answer wins the round** (🎉 reaction). Any real anagram counts. An **edited** message counts too, if the bot receives edited-message updates.
* **Step hints:** a letter is revealed after 50 % of the time and another after 75 % (each hint costs 1 point, never below 1).
* **Points:** base (easy 2 · medium 3 · hard 5 · extreme 8) + speed bonus (+2 in the first third of the time, +1 in the second) − hints.
* **One live message:** the bot keeps a single round message in the chat (puzzle → hint → result, edited in place) and deletes the previous one, plus the final ranking. (Deleting needs the bot to be allowed to delete messages; otherwise old rounds simply stay.)
* The game ends after the last round / when somebody reaches the race target / after two silent rounds in a row. Then: ranking with 🥇🥈🥉, rewards and a **🔁 Play again** button.

## Rewards, streaks, leaderboard (no new tables)
* After a **group** game in which **2+ players scored**, every scorer gets a diamond reward: `points × 5` 💎, **+5 % per day of streak (max +50 %)**, at most **1,500 💎 per day** from Jumble. After the cap a game still pays 1 💎 so the points stay on the leaderboard. Private chats and one-player games pay nothing and are not counted. All numbers are constants at the top of `game/jumble_stats.py`.
* The reward row in the existing `Transaction` ledger (kind `jumble_win`, ref `jumble:<chat>:<game>:<user>:<points>:<words>:<rank>`) **is** the stats record: `/jlb`, `/jstats` and the streak are computed from those rows. Nothing else is stored.
* Leaderboard names come from the names seen while games run, then from Telegram (`get_chat_member` / `get_chat`), then "Player 1234".

## Paid games (optional entry fee)
* The host picks an entry fee and presses Start → the host pays and a lobby opens (2-8 players, Join button, starts after 60 s with 2+ players or when the host taps Start). Only players who paid can score or vote.
* The best player takes the whole pot (a tie splits it; `FEE_PCT` in `game/jumble.py` = house fee, default 0). Nobody scored, `/endgame`, an error, or nobody joining → everybody is refunded.
* Money safety: every change goes through `economy.apply` with idempotency refs (`jumblebet:<game>:b|r|w:<user>`); the payout is one transaction; `services.jumble.recover_orphans()` runs at boot and refunds entry fees of a game that a restart interrupted.

## Other details
* **Puzzle picture:** each word is posted as a picture of letter tiles (`game/jumble_img.py`, Pillow + the bundled Poppins font), so it can't be copied into an anagram solver. Hints and results edit the picture's caption. If the picture can't be made or sent, the puzzle falls back to plain text and the game goes on.
* **Real scrambles:** at most `len/4` letters (min. 1) may stay in their original place, so the word never half shows (`game/jumble.py: scramble`).
* **Close-but-wrong:** a real word of the right length that isn't an answer gets one 🤔 reaction per player and round.
* Words: WordSeek dictionary (`game/wordseek_data.py`) + `game/jumble_words.py` (themes and 7-8 letter words; add words there - `tests/test_jumble_pure.py` checks them).
* Games live in memory (`services/jumble.py: GAMES`). A restart drops a free game (paid games are refunded at boot).
* One game per chat: Jumble, Wordly, Hack and WordSeek block each other - `/endgame` the running one first.
* Plain-message answers need the bot to receive normal group messages (privacy mode off, or the bot is an admin) - same as WordSeek.
* Tuning knobs: `game/jumble.py` (`ROUND_CHOICES`, `TARGET_CHOICES`, `DIFFS`, `SPEEDS`, `ENTRY_CHOICES`, `LOBBY_S`, `BREAK_S`, `IDLE_ROUNDS`) and `game/jumble_stats.py` (rewards).

## Solo mode (private chat)

* `/jumble` in a private chat is a solo game: still no reward and not on the group leaderboards (so nothing can be farmed), but the player now keeps **personal records** in `user_stats`: `jumble_solo_games`, `jumble_solo_words`, `jumble_solo_pts` and `jumble_solo_best` (best points in one game).
* The result message says "🏅 New personal best" or shows the current best; `/jstats` gets a 🎯 Solo section; `/jlb solo` is the board of best solo games.

