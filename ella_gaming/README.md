# Ella Gaming

An independent Telegram Mini App with free HTML5 games from [GameZipper](https://gamezipper.com/). It has its own server, its own
database, its own player profiles and its own original UI. The Ella bot is **only the launcher**.

```
Telegram  ──/app, button──►  Ella bot (unchanged except the launcher)          ← shares NOTHING with Ella Gaming except the bot token
    │
    └── opens Mini App URL ─►  Ella Gaming server  ──►  ella_gaming.sqlite3   (its own file: players, favourites, recent, launches)
                                  │  serves web/ (the UI) + /api/*
                                  └── fetches  gamezipper.com/api/games.json  (cached, ≤ 6 h as the publisher asks)
                                      players' iframes load  gamezipper.com/<slug>/   (sandboxed)
```

| Folder | What |
|---|---|
| `ella_server/` | Backend (Starlette/ASGI): Telegram `initData` login, catalogue cache + search, ad/embed verification, profiles, rate limiting |
| `web/` | Frontend (plain ES modules, no build step): Home · Games · Profile, player, favourites, recents |
| `tests/`, `tools/` | 32 automated tests incl. headless-Chromium end-to-end on an emulated Android phone; a local fake of GameZipper |
| `deploy/` | systemd unit, Caddy config, backup script. `Dockerfile` + `docker-compose.yml` in this folder |
| `TESTING_REPORT.md`, `KNOWN_LIMITATIONS.md` | Read these before launch |

The bot side is three small edits (see "Bot launcher" below): `handlers/ella_app.py` (new), one router line in `bot.py`, one button in
the Games hub, plus the `/help` entry. The bot never imports from `ella_gaming/` and `ella_gaming/` never imports from the bot.

---

## 1. Quick start (any machine with Python 3.12+)

```bash
cd ella_gaming
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium      # for the runtime ad check (recommended, see §3)
cp .env.example .env                          # fill in ELLA_BOT_TOKEN, ELLA_SESSION_SECRET, ELLA_PUBLIC_URL
python -m ella_server scan                    # fetch the catalogue, verify every game, print the real counts (takes a while the first time)
python -m ella_server                         # run the server on :8080
```

Local try-out without Telegram: `ELLA_DEV=1 python -m ella_server` serves the app, but it will show the "open from Telegram" screen
because there is no signed `initData` outside Telegram. That is intentional (see the security notes).

## 2. How the catalogue works

* The whole list is fetched from `https://gamezipper.com/api/games.json` (a JSON array of `{slug, name, emoji, category, tags, embedUrl,
  thumbnail}` – the shape documented at gamezipper.com/publisher-partner.html). Nothing is hard-coded.
* It is cached on disk and in memory for 6 hours (the publisher asks for ≤ 6 h), refreshed in the background, `If-None-Match` is used when
  the server supplies an ETag, and the previous catalogue keeps serving if a refresh fails.
* Each entry is validated: sane slug, HTTPS `embedUrl` **on an allowed host only** (`ELLA_EMBED_HOSTS`, default `gamezipper.com`).
  Invalid or duplicate entries are counted and dropped.
* GameZipper gives one category per game. Ella maps those (puzzle, arcade, skill, idle, card, board, casual, sports, racing, word,
  simulation) onto its 12 categories, and adds Action / Adventure / Strategy / Multiplayer from tags/name keywords. Which categories end up
  with how many games depends on the live feed – see `python -m ella_server report`.
* Embedding follows the publisher terms: the documented iframe URL form with `utm_source` (your domain) / `utm_medium=embed`, games load
  only when a player taps Play, no raw game files are copied or hosted.

## 3. Ad verification (read this)

Ella is ad-free **by construction**: a game is only listed when its verdict is `clean`.

| Verdict | Meaning | Shown? |
|---|---|---|
| `clean` | Embeddable, no ad code, nothing ad-related rendered | yes |
| `ads` | Ad-network code (AdSense/GPT/IMA/Adsterra/…) in the page or its own scripts, **or** ads/popups/dialogs/visible ad labels at runtime | no |
| `review` | Ad-*like* markup or a "Sponsored · Advertisement" text in the HTML, but no ad-network code | no, until the browser check proves it is not rendered |
| `blocked` | Not embeddable: non-200, redirects off-host, `X-Frame-Options`/CSP `frame-ancestors` forbids framing | no |
| `error` | Could not be checked (timeout, 5xx); retried hourly | no |

Two layers:

1. **Static check** (always): fetches the exact embed URL a player would load and its first-party scripts, and looks for ad-network
   domains/SDKs, ad slots and ad labels. Framing restrictions are respected, never bypassed.
2. **Runtime check** (`ELLA_SCAN_BROWSER=1`, the default; needs Playwright + Chromium): opens the game in a mobile viewport, taps it, and
   records requests to ad hosts, pop-ups, `alert/confirm` dialogs, ad iframes, and ad-looking elements/labels that are actually
   **rendered** (hidden placeholders do not count; visible ones do).

Without Playwright installed the server still runs, but every `review` game stays hidden. Check `python -m ella_server report` for
the real numbers (`eligible_shown`) and `exclusion_reasons`.

> **Important, observed while building this:** the game pages that `embedUrl` points to are GameZipper's regular website pages. In the
> sample we could read, they contain a Google AdSense account meta tag and "Sponsored · Advertisement" labels, even with the embed
> parameters, although the publisher page says "zero ads injected into embedded games". Whether those labels are visible placeholders or
> hidden markup could not be determined from the authoring sandbox. **Run `python -m ella_server scan` first**: if the labels are
> rendered, those games are correctly excluded and the eligible count will be low. In that case ask GameZipper
> (partners@gamezipper.com, the contact on their publisher page) for an ad-free embed endpoint and set it via the feed/`embedUrl`.
> Do not switch to `ELLA_ADS_POLICY=trust_provider` – it lists unchecked games and breaks the ad-free promise.

Also: the iframe is sandboxed **without** `allow-popups` and `allow-top-navigation`, so even a misbehaving game cannot open pop-ups
or redirect the Mini App. A scan is a snapshot; verdicts expire (`ELLA_SCAN_TTL`, default 7 days) and are re-checked in the background.

## 4. Bot launcher

Set in the **bot's** `.env`:

```
ELLA_GAMING_URL=https://games.example.com/          # same value as ELLA_PUBLIC_URL (+ trailing slash is fine)
ELLA_GAMING_LINK=https://t.me/YourBot/play          # optional: BotFather direct link, used when /app is run in a group
```

* `/app` → message with a **🎮 Open Ella Gaming** Mini App button.
* **🕹 Games hub** (`/start` → Games, or `/arcade`) → **🎮 Ella Gaming** button → the same button, same message edited in place.
* BotFather **Main Mini App** / direct link (below) → opens the same URL.

Telegram only allows Mini App buttons in **private chats**. In groups the button is a plain link (direct link if set, else the bot's DM).
`/app` is listed in `/help → 🕹 Games` and, per your project rules, is **not** in the "/" command menu.
The bot's global menu button is deliberately **not** changed: setting it to a Mini App would replace the commands-list button in every chat.

## 5. Deployment

### 5.1 Backend (+ frontend) on a VPS – recommended

1. A small Linux VPS (1 vCPU / 1 GB is enough; the first Chromium scan benefits from 2 GB) and a domain pointing at it.
2. `git clone`/copy the project, then in `ella_gaming/` follow §1 (venv, `pip install`, `playwright install --with-deps chromium`, `.env`).
3. Keep it running: `sed -e "s#__USER__#$USER#" -e "s#__DIR__#$PWD#" deploy/ella-gaming.service | sudo tee /etc/systemd/system/ella-gaming.service`
   then `sudo systemctl enable --now ella-gaming`. Logs: `journalctl -u ella-gaming -f`.
4. **HTTPS** (Telegram requires it): install Caddy, put `deploy/Caddyfile` (your domain) in `/etc/caddy/Caddyfile`, `sudo systemctl reload caddy`.
   Certificates are issued and renewed automatically. Set `ELLA_TRUST_PROXY=true` in `.env`.
5. Docker instead: `docker compose up -d --build` in `ella_gaming/` (untested in the authoring sandbox), then proxy to `127.0.0.1:8080` as above.
6. Run **one** server process (rate-limit and scan state are in memory). It is asynchronous and handles many simultaneous players;
   the bot runs as a separate process and is never blocked by it.

### 5.2 Platforms (Railway / Render / Fly.io)

Deploy the `ella_gaming/` folder as its own service (Dockerfile provided). Mount a **persistent volume at `/data`** (SQLite lives
there), set the env vars from `.env.example`, and use the platform's HTTPS domain as `ELLA_PUBLIC_URL`. The platform's proxy sets
`X-Forwarded-For`, so keep `ELLA_TRUST_PROXY=true`.

### 5.3 Option B – frontend on a static host (Cloudflare Pages / Netlify / GitHub Pages)

Optional. Upload the `web/` folder, set `API_BASE` in `web/js/config.js` to the backend's https origin, and on the backend set
`ELLA_SERVE_FRONTEND=0`, `ELLA_ALLOWED_ORIGINS=https://your-frontend-origin`, and `ELLA_PUBLIC_URL=https://your-frontend-origin`
(this is the URL you give BotFather and the bot). The static host should also send a CSP; copy the one from `ella_server/app.py: csp()`.
Same-origin hosting (§5.1) is simpler and was the tested configuration.

### 5.4 Database

SQLite in WAL mode: `$ELLA_DATA_DIR/ella_gaming.sqlite3`. Separate file from the bot's database, no shared tables, created and migrated
automatically on start. Tables: `players`, `favorites`, `recent_games`, `launches` (with measured session duration), `game_checks`.
Back it up with `deploy/backup.sh` (online-safe, keeps 14). The data is the player's Telegram id, name/username/photo URL as sent by
Telegram, an optional nickname, and their game activity. Nothing else. SQLite is plenty for thousands of concurrent players on
this workload; if you outgrow it, the access layer is one file (`db.py`).

### 5.5 Environment variables

All variables, with defaults, are documented in `.env.example`. Required: `ELLA_BOT_TOKEN`, `ELLA_SESSION_SECRET`, `ELLA_PUBLIC_URL`.
The server refuses to start without a ≥ 32-char secret and a token (outside `ELLA_DEV=1`). Secrets are only ever read from the environment.

### 5.6 BotFather

1. `/mybots` → your bot → **Bot Settings** → **Configure Mini App** (called "Main Mini App") → enable, URL = `ELLA_PUBLIC_URL`. This adds an
   **Open app** button on the bot's profile and the link `https://t.me/YourBot?startapp`. (BotFather's menu wording changes over time; follow its prompts.)
2. Optional named direct link: `/newapp` → pick the bot → title, description, 640×360 photo, **Web App URL** = `ELLA_PUBLIC_URL`, short name `play`
   → link `https://t.me/YourBot/play` → put it in `ELLA_GAMING_LINK`.
3. Leave **Menu Button** on its default (commands). Only change it if you want the Mini App there *instead of* the command list.
4. Use the **same bot** for the launcher and `ELLA_BOT_TOKEN`; Telegram signs `initData` with that bot's token. If you ever rotate the token, update both `.env` files.

## 6. Security model

* **Login**: the frontend sends `Telegram.WebApp.initData`; the backend verifies it exactly as Telegram documents (HMAC-SHA256 with
  `WebAppData`, constant-time compare, rejects duplicates/oversize/expired/future `auth_date`, bots, malformed user). It then issues its own
  short-lived HMAC-signed bearer token. Every other endpoint trusts only that token – never anything the frontend claims.
* **Input validation** on every parameter (slug regex, category/sort whitelists, integer bounds, JSON object + 16 KB body limit,
  nickname regex). All SQL is parameterised.
* **Rate limiting**: per IP, stricter on login, per user on writes; `429` + `Retry-After`.
* **Headers**: strict CSP (no inline scripts; frames only from the allowed game host; `frame-ancestors` only Telegram), nosniff,
  referrer policy, permissions policy. Game iframes are sandboxed.
* **Admin endpoints** only for ids in `ELLA_ADMIN_IDS`.
* Isolation: separate process, port, database file and codebase from the bot.

## 7. Running the tests

```bash
pip install -r requirements-dev.txt && playwright install chromium
python -m pytest -q tests          # ~5 min; runs a local fake GameZipper, the real server and headless Chromium (Pixel 7 emulation)
```
Bot-side: from the project root `python -m pytest -q tests/test_help_catalog.py tests/test_ella_app.py` (needs the bot's own requirements).
See `TESTING_REPORT.md` for what these do and do not prove.
