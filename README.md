# Telegram AI Chatbot + Diamond Game + Family Life

> The bot's display name is **not hard-coded**: it is read live from Telegram (BotFather name). Rename the bot there and all screens, the '/' menu, the AI persona and the group name-trigger follow within ~5 minutes.

Aiogram 3 · PostgreSQL (SQLAlchemy 2 async) · Redis · Alembic · Google Gemini **free tier**.

## ⚠️ Secrets
Never commit `.env`. If a bot token or API key was ever pasted into a chat, email or repo, treat it as leaked:
revoke the bot token in **@BotFather → /revoke** and delete/recreate the Gemini key in Google AI Studio, then put the new values in `.env`.

## Deploy on bot-hosting.net (Pterodactyl panel, no Docker, no shell)
This project runs there as-is (SQLite, no Redis, in-memory fallback). Typical RAM use is about 90-150 MB; the biggest spike is the
`/familytree` picture, which is drawn one at a time and its memory is released right after (full quality; set `TREE_SUPERSAMPLE=2` only if RAM is still too tight).

1. **Upload**: zip this folder, upload it in the server's **Files** tab, click the three dots → **Unarchive**, then move the files
   out of the extracted folder (select all → **Move** → `..`) so `bot.py`, `main.py`, `requirements.txt` and `.env` sit in the main directory.
2. **Startup tab**: set the Python file to **`bot.py`** (or leave the default `main.py`, which starts the same thing). Pick Python **3.11 or 3.12**.
   The panel installs `requirements.txt` by itself on every start - there is no shell, so don't try `pip install`.
3. **`.env`** is read from the main directory (it is included; edit it in the Files tab). Needed: `BOT_TOKEN`, `OWNER_ID`.
   Keep `DATABASE_URL=sqlite+aiosqlite:///bot.db` and leave `REDIS_URL` empty. Everything else is optional (AI keys etc.).
4. **Start** the server and send `/start` to the bot. The database (`bot.db`) and `backups/` live in the same folder;
   the bot also DMs you a daily backup - keep those, a deleted server loses its files.

Optional PostgreSQL / Redis: add the lines from `requirements-extra.txt` to `requirements.txt` and set `DATABASE_URL` / `REDIS_URL`.
Memory knobs in `.env`: `TREE_SUPERSAMPLE` (3 = full quality, 2 = lighter), `AVATAR_CACHE` (profile pictures kept in RAM), `MALLOC_ARENA_MAX`.

## Deploy on a VPS (Ubuntu 22.04/24.04 or Debian 12, 1 vCPU / 1 GB RAM is enough)
Uses the `.env` you already have (SQLite database, Redis optional). Nothing here overwrites or prints `.env`.

**Option A - Docker (recommended)**
1. Upload and unzip on the server: `scp bot.zip root@YOUR_IP:/root/ && ssh root@YOUR_IP`, then `apt-get install -y unzip && unzip bot.zip -d bot && cd bot`
2. **`sudo ./deploy.sh`** - installs Docker, opens only SSH in the firewall (the bot uses outgoing long-polling, no ports needed),
   checks `.env`, builds and starts the bot. An existing `bot.db` next to the script is copied into `./data` once.
3. Check the log (`docker compose logs -f bot`), then send `/start` to your bot.

**Option B - no Docker (systemd)**: `sudo ./deploy/install-systemd.sh` (needs Python 3.11+; sets up a venv and a `telegram-bot` service).
Logs: `journalctl -u telegram-bot -f` - restart: `sudo systemctl restart telegram-bot`.

| Task (Docker) | Command |
|---|---|
| Live logs | `docker compose logs -f bot` |
| Status / health | `docker compose ps` (shows `healthy`; a frozen bot is auto-restarted) |
| Update code | copy the new files over the folder, then `./scripts/update.sh` (backs up first) |
| Backup now | `./scripts/backup.sh` (keeps 14 in `backups/`; copy them off the server too) |
| Restore | `sudo ./scripts/restore.sh backups/bot_YYYY-MM-DD_HHMM.db` |
| Stop / start | `docker compose stop` / `docker compose up -d` |
| Change a secret | edit `.env`, then `docker compose up -d` |

The game database lives in `./data/bot.db` (Docker) - keep that folder when moving servers. The bot also makes a daily backup
into `data/backups` and sends it to the owner on Telegram. Containers restart after reboots and crashes (`restart: unless-stopped`).
To use PostgreSQL instead, remove the two `environment:` lines in `docker-compose.yml` and set `DATABASE_URL` in `.env`.

## Commands
* The Telegram "/" menu is a short list of the useful commands, the same in DMs and groups (groups also show `/settings`).
* `/help` shows category buttons (Economy, Combat, Shop, Adventure, Games, Love & Family, Social & Fun …); tapping one lists that category's commands.
* Owner / moderator commands (`/give`, `/take`, `/broadcast`, `/stats`, `/backupdb`, `/ban` …) still work for the owner,
  but are never listed in the menu or `/help`. To hide a new staff command, add it to `STAFF` in `utils/helpcatalog.py`.
* Add a new player command: write its handler, then add `("name", "description")` to `DESC_LIST` in `utils/helpcatalog.py`
  Then put it in a category in `CATEGORIES` (same file); a command you forget still appears under "Other". `tests/test_help_catalog.py` fails if a registered command is missing from `/help`.

## Quick start (Docker, local machine)
```bash
python setup_env.py         # asks for BOT_TOKEN, OWNER_ID, GEMINI_API_KEY and writes .env (or: cp .env.example .env and edit)
python check_ai.py          # optional: tests your AI key right now, prints the exact error if it fails
docker compose up -d --build
docker compose logs -f bot
```
## Manual
```bash
python3.11+ -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # edit
alembic upgrade head        # creates the schema
python bot.py
```
Without `REDIS_URL` the bot falls back to in-memory rate limiting (fine for one process). Without
`GEMINI_API_KEY` the AI is disabled and all game features keep working.

In BotFather: `/setprivacy` → keep **Enabled** (the bot still receives @mentions, replies to it, and commands).

## Architecture
| Folder | Role |
|---|---|
| `game/` | `rules.py` (ALL balance numbers, override via `RULES_FILE` JSON), `engine.py` (pure fight resolver) |
| `services/` | All business logic: economy, combat, duel, bounty, shop/powers, progress, leaderboard, admin |
| `ai/` | Provider interface, Gemini provider, conversation service. **Imports nothing from the game** (enforced by a test) |
| `handlers/` `keyboards/` `middlewares/` | Telegram layer only |
| `database/` `migrations/` | Models, session, Alembic |

**Integrity rules**: every balance change goes through `economy.apply()` — one conditional `UPDATE … WHERE balance+Δ>=0`
plus a ledger row, inside one DB transaction (plus `CHECK` constraints). Cooldowns are claimed with a single atomic
UPDATE/INSERT. Idempotency keys (`transactions.ref` unique) block duplicate rewards/payments. Concurrent PvP on the
same player is serialised with row locks. PostgreSQL is the source of truth; Redis only holds rate-limit counters,
callback-replay guards and one-time admin confirmation nonces (and the bot degrades to memory if Redis dies).

**Economy**: 💎 diamonds are the ONLY currency (no gold, coins or gems anywhere). Diamonds are minted only by daily / weekly / work / successful crime / missions
(and the small guaranteed kill reward when a victim is broke). Rob, kill, duel, tic-tac-toe and
bounty *move* diamonds: whoever wins gains them, whoever loses pays them.

**Death**: a `/kill` that wins kills the target and the killer takes diamonds. A dead player cannot `/kill`, `/revenge` or `/duel`
until they pay `revive_cost` 💎 (`game/rules.py`, default 150) with `/revive`. The flag is stored in `user_stats` (key `dead`), so no migration is needed. Sinks: shop, power activation, protection, crime fines, bounty-cancel fee.

## AI: automatic fallback ("little busy" fix)
The AI is a chain of providers (`AI_PROVIDERS` in `.env`): Gemini (several keys) -> Groq -> Cerebras -> OpenRouter -> Mistral -> your own
OpenAI-compatible service. A failing provider/key/model is skipped for a while and the next answers at once; if one is merely slow,
the next starts in parallel after `AI_HEDGE_S` seconds and the first answer wins. "Busy" only appears when EVERY provider failed.
Add keys with `GROQ_API_KEYS=...` etc. (see `.env.example`), then check with `/aitest` or `python check_ai.py`.

## AI (Gemini free tier)
**If the AI does not answer:** (1) send `/aitest` to the bot as the owner, or run `python check_ai.py` — both print Google's exact error;
(2) check the startup log line `AI self-test: …`; (3) every non-200 Gemini answer is logged as `Gemini model=… -> HTTP …`.
`AQ.…` keys (the new AI Studio format) work on the native endpoint used here with current Gemini 3.x models; they fail on retired 2.x models.
If every model in `GEMINI_MODELS` fails for a non-quota reason, the bot asks Google (`models.list`) for the current Flash / Flash-Lite
models and uses those automatically (Pro/image/etc. are never used).
* Key only from `GEMINI_API_KEY`. `GEMINI_MODELS` is an ordered list of **Flash / Flash-Lite** models; names containing
  `pro`, `ultra`, `image`, etc. are refused at startup so the bot can't drift onto paid-only models.
  Model names change often — check your project's free models in Google AI Studio and edit `GEMINI_MODELS`.
  A 404 on a model just moves to the next one in the list.
* **Keep billing disabled on the Google project** — that is what guarantees you are never charged. The bot never calls a paid provider.
* Quota exhausted (HTTP 429) → users see "The bot is temporarily unavailable, try later"; a global back-off stops hammering the API.
* Limits: per-user/minute, per-user/day, and a global requests/minute budget (`AI_*` env vars).
* Per-user (and per-chat) history, `AI_CONTEXT_MESSAGES` turns, expires after `AI_CONVERSATION_TTL_MIN`; `/clear` wipes it.
* Add another provider: implement `AIProvider.generate()` in `ai/` and register it in `build_provider()`.
* Free-tier note: Google may use free-tier prompts to improve its products — tell your users.

## 🗺 The Realm (skill & teamwork — no betting)
| Command | What it is |
|---|---|
| `/dungeon` | 5-floor, button-driven dungeon. Each room is a choice: pick a stance (⚔️ Aggressive / ⚖️ Balanced / 🛡 Defensive), sneak, open chests (mimics!), pray at shrines, trade with merchants, rest at camps. Your **gear, pet, level and active power** all count. Gold is paid when the run ends — **cash out between floors** or push on (death keeps half). Boss on floor 5; ☠️ **Hard** mode at level 5+. 90 min cooldown. Runs are stored in the DB, so restarts never eat a run. |
| `/expedition` | Send your pet away for 1h / 4h / 8h. Stronger / rarer pets bring home more. You get a DM when it's back. |
| `/heist` | Group co-op: players join a lobby as 💪 Muscle / 💻 Hacker / 🔭 Lookout / 🚗 Driver. 4 stages, each needs its role — mix your crew! Needs a 24h+ old account, 3h personal cooldown. |
| `/cd` | Every cooldown on one screen (ready vs. time left, expedition, dungeon run). |
| `/achievements` | 29 badges in 4 groups with live progress. `/profile` shows your count. |

New missions: *Clear the dungeon* (daily), *3 expeditions* and *a group heist* (weekly). Fishing / mining now feed the Angler,
Prospector and Whale Watcher badges.

Tuning: dungeon numbers are constants at the top of `services/realm.py` (`GOLD_SCALE`, `CLEAR_BONUS`, `DUNGEON_CD_S`,
`monster_stats()`), expedition rewards in `EXPEDITIONS`, heist numbers in `HEIST_*`. New tables (`user_stats`, `expeditions`,
`dungeon_runs`) are created automatically at boot. Run `pytest tests/test_realm.py` for the Realm tests.

## Latest update — family, welcome, GIFs
* **`/familytree`** draws your family as a real tree **picture** (grandparents → parents → you ♥ partner → children …, in-laws, uncles/aunts, cousins, nephews/nieces). Friends and besties are never shown. Reply to someone with it to see their tree. Each card shows the person's **Telegram profile picture** (cached for a few hours); anyone without a picture, with a hidden one, or a bot-made child falls back to their initial. Rendered with Pillow using the bundled Inter font (`assets/fonts`).
* **Family tree** (`/family`): beyond marriage/kids there are now 23 relations — father, mother, son, daughter, brother, sister, grandparents/grandchildren, uncle, aunt, nephew, niece, cousin, in-laws, friend, bestie (`/roles`).
  Reply to someone with `/addfamily brother` (or just `/father`, `/mother`, `/friend` …); they must tap **Accept**. `/unfamily` removes, `/gender boy|girl` sets how *you* are labelled to them. Relatives can't marry each other. `/familybonus` gets +15 💎 per relative (max 8).
* **/start in DM**: first-time users get a welcome + about card with buttons; later `/start` shows the menu. `/about`, `/help` (command guide), group intro when the bot is added to a group, nicer join messages.
* **GIFs** on important actions: hug, kiss, slap, pat, cuddle, punch, kick, bite, cry, dance, wave, highfive, poke, blush, wink, handhold, bonk, yeet, tickle, laugh, sleep, facepalm, plus marriage & new-family moments. They load from free APIs at runtime; if unreachable the action is sent as text. `GIFS=off` disables.
* **Backups**: everything new (relations, genders, welcome flags, last bot name) is in the same SQLite file, so `/backupdb`, the daily DM backup and `/restoredb` carry it; the backup caption shows row counts, and restoring an older backup auto-creates missing tables.
* Fixed: `/gift <item>` (partner gift) was shadowed by the fun action `/gift`.

## New in this version
Clans (`/clan`) · pets & `/hunt` · random group events (💎 drops, 🐲 boss raids) · level-up bonus DMs · owner `/stats` `/backup` + daily SQLite backups · English-only AI · shorter cooldowns · daily 🔥 streak bonus · `/spin` `/slots` `/flip` · fun commands (`/hug /slap /pat /ship /roll /8ball /truth /dare /joke`) · `/ask` · answers to her name in groups · per-role `/` command menus.

## Commands
Economy `/bal /daily /weekly /work /crime /rob <amount> /pay /deposit /withdraw` · Combat `/kill /duel /revive /protect /revenge /bounty /activate`
Shop `/shop /powers /inventory /buy /sell /equip /upgrade /use` · `/top /missions /profile /clear /settings`

`/top` — in a DM: one leaderboard at a time with 💎 ⚔️ ⭐ 🏆 tab buttons and ◀️ ▶️ pages. In a group: **all four leaderboards** (richest, kills, level, wins) in one message, 10 players per board per page, with ◀️ ▶️ to reach everybody. Pages are shared — whoever taps an arrow turns the page for the whole chat.

**Arrow pages** (◀️ ▶️, anyone can turn them) are also used by: `/toprep` (10 per page), `/clan top` (10 per page), `/clan info` member list (15 per page), `/bounty` Most Wanted (10 per page), `/inventory`, and the `/family` text tree (it stays on one message unless it would get too long, then it continues on page 2 …). Shared helper: `utils/paging.py` — to page another list, add a `(rows, total)` query and a `xx:{page}` callback.
Admin (owner; `ADMIN_IDS` get moderation only) `/admin /give /take /setbalance /ban /unban /mute /unmute /userinfo /broadcast /maintenance /logs`
Balance changes, bans, broadcast and maintenance need a **confirm tap** (one-time, 90 s, same admin) and are written to `admin_logs`.
Targets: reply, `@username`, or numeric ID. The full inline menu lives in the private chat.

## 💍 Engagement, wedding & anniversary
`/marry` and `/divorce` work exactly as before. Three new commands add a longer love story on top (logic: `services/romance.py`, numbers: `game/rules.py`, texts: `utils/content/romance.py`, tests: `tests/test_romance.py`):

* **`/engage`** (reply / `@user`) — the other side must tap **Accept**; the **ring costs 1,000 💎**, paid by the proposer when it is accepted. `/engage` alone shows your engagement; **`/engage break`** ends it for a **250 💎 penalty** (paid by whoever breaks it, at most their wallet; the ring is not refunded). Brand-new accounts (< 24 h, the same rule as `/pay`), relatives, married or already-engaged players can't take part.
* **`/wedding`** — for an engaged couple (or a couple that married with `/marry` and never had a ceremony). Pick a venue: 🏛 **Simple** 1,500 · 🌸 **Garden** 4,000 · 👑 **Royal** 10,000 💎, **split 50/50**. You choose the venue, your partner taps **I do!**; then a **wedding card** is posted and a **one-time wedding gift** (150 / 500 / 1,500 💎 each) is paid. The gift is always smaller than what the couple paid and is given **once per couple, ever** (a divorce + re-wedding doesn't earn it again).
* **`/anniversary`** — days together plus one-time milestone rewards at **30 days (150 💎), 100 days (400 💎) and 1 year (1,000 💎)** for each partner. A background loop greets every couple on their wedding anniversary in a DM and pays **250 💎 each, once per year**.

Every diamond change goes through `economy.apply()` with a unique ref; one-time rewards are guarded by a unique `couple_flags` row that survives divorce. New tables (created automatically at start-up): `engagements`, `weddings`, `couple_flags`.

## 🎁 Gifts, love letters & couple life
Logic: `services/gifting.py` (gifts, bonds, letters) and `services/couplelife.py` (honeymoon, quests, streak); texts in `utils/content/romance.py`; numbers in `game/rules.py`; tests in `tests/test_romance.py`.

* **`/sendgift`** (reply / `@user`, or no target = your partner) — a button shop: 💐 Flowers 50 💎 (+6) · 🍫 Chocolate 120 💎 (+14) · 🧸 Teddy 300 💎 (+35) · 🎂 Cake 600 💎 (+75). For your **partner** or a **relative / friend from `/family`** only. Partner gifts raise your couple **love**; relative gifts raise a **bond** (titles **Friendly 🌼 → Close 🤝 → Inseparable 💞** are shown next to the person in `/family`). Each sender can create at most **120 points per day** (a gift that would be cut off is refused before it charges you). Gifts are a pure money sink.
* **`/loveletter <text>`** (max **300 characters**; reply to a relative, otherwise it goes to your partner) — posts a styled card. Costs 30 💎, 30 min cooldown, +3 love / bond (counts toward the daily cap). `@mentions` and links in the text are defused.
* **`/honeymoon`** — only after a `/wedding`, **once per couple**. You book, your partner taps **Let's go!**; the trip costs 1,500 💎 split 50/50, lasts **6 hours**, shows story chapters as time passes, and a DM tells you when it is over. Then either of you taps **Collect bonus**: **400 💎 + 40 love for each** partner (smaller than the trip price, so it stays a sink).
* **`/couplequest`** — two shared tasks per day, the same for both partners (e.g. send each other a `/loveletter`, send a `/sendgift`, both claim `/daily`, go on a `/date`, both `/work`). When you run the command and a task is done, **both** partners get 40 💎 + 8 love, once per task.
* **`/lovestreak`** — both partners check in on the same UTC day to build a streak. One-time rewards for each partner at **7 days (200 💎), 30 days (600 💎) and 100 days (2,000 💎)**, once per couple ever; every completed day adds 2 love.

Everything needs a marriage or an accepted `/family` relation (nobody can target themselves). New tables (created automatically at start-up): `bonds`, `bond_daily`, `honeymoons`, `love_streaks`.

## 🕹 Arcade & Social (new)
Plain commands + inline buttons (no web app). Open the hub with `/arcade` (also a button on the main menu).

| Command | What it does |
|---|---|
| `/scratch` | 🎟 100 💎 scratch card, tap the squares, match 3 to win up to **3,000** 💎 (97% return, 15 s cooldown). The prize is paid instantly, the reveal is cosmetic, so an abandoned card never loses anything. |
| `/rps [bet]` | ✊ Rock-paper-scissors vs the bot. No bet = free practice. Win pays x1.9, tie refunds. Shares the casino cooldown. |
| `/ttt [reply/@user]` | ⭕ Tic-tac-toe with buttons. Challenge someone, or leave the name out for an open lobby anyone can join. Wins are tracked. |
| `/hangman` | 🔤 Guess the word (5 categories, 6 lives). Win 50-150 💎, fewer mistakes = more. 2 min cooldown. |
| `/wyr` | 🤔 Would-you-rather poll with live percentages; everyone in the group can vote (and change their vote). |
| `/riddle` · `/fact` | 🧩 Riddles with a reveal button · 💡 fun facts. |
| `/horoscope [sign]` · `/fortune` | 🔮 Daily horoscope (same for everyone per sign) · 🥠 your personal daily fortune + lucky numbers. |
| `/rep` (reply) · `/toprep` | 🌟 Give a friend +1 reputation (once every 12 h), leaderboard of the most respected players. |
| `/interest` | 🏦 Daily interest on your **bank**: 1% (max 500 💎), needs 1,000+ 💎 banked. A reason to use `/deposit`. |

Design notes: diamonds only move through `economy.apply()`, cooldowns through `cooldowns.claim()`, rewards are idempotent (`ref`),
game state lives in the KV layer, and counters reuse the existing `user_stats` table, so there are **no new tables / migrations** and `/backupdb` covers everything.
Tuning constants are at the top of `services/arcade.py` (`SCRATCH_TABLE`, `RPS_WIN_MULT`, `HANGMAN_*`, `REP_CD_S`, `INTEREST_*`).
Tests: `pytest tests/test_arcade.py tests/test_arcade_flow.py` (the second one drives the real Dispatcher with a fake Telegram transport).
The `/` menu shows `/arcade`, `/scratch` and `/ttt` (Telegram limits a menu to 100 commands); every other command works the same and is listed in `/arcade` and `/help`.

## Tests
```bash
pytest                                   # SQLite
TEST_DATABASE_URL=postgresql+asyncpg://user:pw@localhost/bot_test pytest    # PostgreSQL (recommended; WIPES that DB)
```
Covers: simultaneous dailies, duplicate/concurrent payments, insufficient funds, protected/cooldown/immune targets,
simultaneous attacks & robberies, shop races, max upgrade, expired protection/bounty/duel refunds (restart recovery),
invalid IDs, AI outage & quota back-off, Redis outage fallback, AI/game isolation, admin confirm + replay, full
Dispatcher round-trips with a fake Telegram transport.

## Backup & recovery
```bash
pg_dump -Fc -h HOST -U bot bot > bot_$(date +%F).dump      # daily via cron
pg_restore -c -d bot bot_YYYY-MM-DD.dump                      # restore (stop the bot first)
docker compose exec db pg_dump -U bot -Fc bot > backup.dump   # docker variant
```
After a restart the bot needs no manual recovery: open duels/bounties are expired and **refunded** on boot and every 60 s
(idempotent via ledger refs).

## Operations
* Schema changes: edit `database/models.py` → `alembic revision --autogenerate -m "msg"` → `alembic upgrade head`.
* Rebalance: edit `game/rules.py` (or `RULES_FILE`); catalogue prices sync into the DB on boot.
* Scale-out: one polling process only (or switch to webhooks); the DB/Redis design is multi-process safe, the in-process AI lock is not.

## Known limitations
* Not yet run against the live Telegram / Gemini APIs in development (sandbox had no access) — do a smoke test with your own token.
* Alembic `0001` is a baseline built from the models; later migrations should be autogenerated.
* Anti multi-account protection is heuristic (24 h send lock, pair/daily caps, pair-fight limits), not bulletproof; use `/ban` and `is_flagged`.
* Menus are private-chat only; groups use commands plus duel/settings buttons.


## Combat & bank rules (current)
- `/kill`: the winner takes **30%** of the victim's **free (wallet) diamonds**. Bank diamonds are never touched. The victim is dead until `/revive` (150 💎, paid from the wallet). A player with no free diamonds left is reported as "already dead" and cannot be attacked. There is **no kill cooldown** (`kill_cooldown_s`, `revenge_cooldown_s` = 0).
- `/protect 1h` · `/protect 2h` … up to 24h: costs `protect_cost_per_hour` (125) × hours, paid from the wallet. A protected player cannot be killed or robbed. Attacking drops your own protection.
- `/deposit`: the bank may hold at most **50%** of wallet + bank (`bank_max_pct`). `/deposit all` deposits as much as is allowed.
- The kill result message only shows the winner/loser (clickable) and the revive line.
