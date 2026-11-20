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
* Money: bets, payout and refunds all go through `economy.apply` with idempotency refs (`hack:<gid>:b|win|r:<uid>`). Games live in memory, so
  `services.wordgames.recover_orphans()` runs at boot and refunds the bets of any game that was interrupted by a restart. No new tables.

## Notes
* One game per chat: Wordly, Hack and WordSeek block each other (`/end` the running one first).
* `/end` is shared with WordSeek: if a Wordly/Hack game is running it ends that one, otherwise it is WordSeek's `/end` as before.
* As with WordSeek, plain-message guesses need the bot to see normal group messages (privacy mode off, or the bot is an admin).
