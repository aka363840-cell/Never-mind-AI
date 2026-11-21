# Wordly & Hack (add-on)

Two group games. They are listed in `/help` (Games) but **not** in Telegram's "/" command menu.

## 🔠 Word scramble (Wordly)
Use the given 8 letters to make as many words as you can!

| Command | What it does |
|---|---|
| `/wordly` or `/wl` | Start a new game (groups and DMs). |
| `/end` | End the game early (the player who started it, or a group admin). |

* The bot posts 8 letters. Everybody just types words in the chat - 3 to 8 letters, each letter used at most as often as it appears.
* 3 letters = 1 pt · 4 = 2 · 5 = 4 · 6 = 7 · 7 = 11 · 8 = 16. About half of the racks are an 8-letter word shuffled, so a full-rack word exists. Every word counts once - the first player to type it gets it.
* The bot reacts 👍 (accepted) / 🔥 (6+ letters) / 👀 (already taken). Messages that are not words of the rack are ignored.
* A round lasts 2 minutes (`game/wordly.py: ROUND_S`), then the ranking and some missed words are shown.
* Words: 4-6 letters from the WordSeek dictionary (`game/wordseek_data.py`), plus 3, 7 and 8-letter everyday words in `game/wordly_data.py` (add more words there, one list per length).

## 🔐 Password hacking (Hack)
Crack the secret password! Each guess gives you **hacks** (right digit, right position) and **glitches** (right digit, wrong position). Turn-based, with betting.

| Command | What it does |
|---|---|
| `/hack3 <amount>` | 3-digit code |
| `/hack4 <amount>` | 4-digit code |
| `/hack5 <amount>` | 5-digit code |
| `/hack6 <amount>` | 6-digit code |
| `/joinhack` | Join an open lobby (same bet as the host) |
| `/end` | End the game (**host only**) - everybody is refunded |

* Groups only. The host's bet opens a lobby (2-6 players); it starts after 60 s with 2+ players, or when the host taps **Start**.
* The code has all-different digits (a leading 0 is possible). Players guess in turn by typing the code in the chat; every guess is public.
* 8 / 10 / 12 / 14 tries per player for 3 / 4 / 5 / 6 digits, 45 s per turn. Miss 2 turns in a row and you are out (your bet stays in the pot).
* First to crack it wins the whole pot (`game/hack.py: FEE_PCT` = house fee, default 0). If nobody cracks it, or the host ends the game, every bet is refunded.
* **No wasted tries:** a guess with a repeated digit (the code never repeats one) or a code that was already tried is rejected and costs no try.
* **Digit tracker:** the board lists the digits that are certainly not in the code (every digit of a guess that scored 0 hacks and 0 glitches).
* **Practice:** `/hack3`-`/hack6` in a private chat starts a free solo game against the bot (no bet, no pot, nothing paid or refunded).
* Money: bets, payout and refunds all go through `economy.apply` with idempotency refs (`hack:<gid>:b|win|r:<uid>`; a tied win is `hack:<gid>:win:<uid>`). Games live in memory, so
  `services.wordgames.recover_orphans()` runs at boot and refunds the bets of any game that was interrupted by a restart. No new tables.

## Notes
* One game per chat: Wordly, Hack and WordSeek block each other (`/end` the running one first).
* `/end` is shared with WordSeek: if a Wordly/Hack game is running it ends that one, otherwise it is WordSeek's `/end` as before.
* As with WordSeek, plain-message guesses need the bot to see normal group messages (privacy mode off, or the bot is an admin).

## Hack stats, ranks and the daily code

* **Stats** (`/hstats`, reply to see someone else's) and **leaderboard** (`/hlb`, all-time wins). Lifetime counters live in the existing `user_stats` table (`hack_play`, `hack_win`, `hack_crack`, `hack_tries`, `hack_fast`) - no new tables, no migration. Only finished **group** games count (a game the host ended, practice games and the daily code don't), so ranks can't be farmed solo.
* **Ranks** by wins (`game/hack_stats.py: RANKS`): 🐣 Script Kiddie → 📡 Packet Sniffer (3) → 🎩 Black Hat (10) → 🔓 Cipher Breaker (25) → 💥 Zero-Day (50) → 👑 Elite Hacker (100). A rank-up is announced in the game result.
* **Daily code** (`/hackdaily`, private chat only): one secret 4-digit code, the same for everybody for a UTC day, derived from a hash of the bot token + date (reading the source can't reveal it). One attempt per day, 10 tries, no timer, no bet. Solving pays `DAILY_BASE` (100) + `DAILY_PER_TRY` (25) per unused try, +5 % per day of streak (max +50 %) - once per day: ledger kind `hack_daily`, ref `hackdaily:<YYYYMMDD>:<uid>` (a replay is a no-op), meta `{"tries": n}`. That ledger row is also the record of the solve (streak, `/hlb daily`, `/hstats`).
* Progress of an open attempt is kept in the KV layer (`hd:g:<day>:<uid>`; Redis if configured, else memory), so `/hackdaily stop` and a restart (with Redis) keep the tries. A failed day is marked `hd:f:<day>:<uid>`. If a solve's payout fails, `/hackdaily` collects it. The code of a failed day is never revealed.
* `/hlb daily` shows today's fastest solvers (fewest tries, earlier solve breaks ties).

