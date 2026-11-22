# Testing report

Environment: Linux sandbox, Python 3.13, headless Chromium via Playwright 1.63 (emulated Pixel 7 = Android Chrome, touch input).
**The sandbox could not reach gamezipper.com from its shell**, so everything below that involves "games" ran against a local fake
provider (`tools/fake_upstream.py`) that imitates the documented feed shape. It proves our code, not GameZipper's games.

## 1. Game counts (what is known and what is not)

| | Count | How known |
|---|---|---|
| Advertised by GameZipper | 467+ | their marketing text (from the task brief) |
| **Fetched** from the live feed | **≈ 468 entries** (≈ 330 + ≈ 138 in two chunks) | read through the web-fetch tool, which returns a summary; counts are estimates ±few. Shape confirmed: JSON array of `{slug,name,emoji,category,tags,embedUrl,thumbnail}`; category values seen: puzzle (~394), arcade (~38), card (~15), board (~6), idle (~4), casual (~4), sports (2), racing (2), skill (2), simulation (1), word (1) |
| **Eligible** (passed ad + embed checks) | **not measured** | needs `python -m ella_server scan` on a machine with internet access |
| **Excluded** | **not measured** | same |
| **Tested live** (loaded and played in a browser) | **0 real games** | sandbox network restriction |
| Real pages read as text | 2 (star-loom, sukima) | both contained "Sponsored · Advertisement" labels and star-loom an AdSense account meta (see KNOWN_LIMITATIONS #2) |

Against the **local fake provider** (not real data): 62 feed entries → 58 valid, 4 dropped (off-host URL, bad slug, duplicate, non-object)
→ 53 clean, 3 ads (static ad script, first-party script calling an ad network, ad injected only at runtime), 2 blocked (X-Frame-Options, HTTP 404).

## 2. Results

Final run, Ella Gaming: **see the last line of `python -m pytest -q tests` below**.

```
$ python -m pytest -q tests          (ella_gaming/, final code)
32 passed in 253.96s
  - 16 backend/auth/catalogue/API tests, 11 browser end-to-end tests (Pixel 7 emulation), 5 scanner tests (3 new)
  - docs/screens/*.png are produced by that e2e run against the FAKE provider (placeholder thumbnails, invented game names)
```

Bot side (from project root): `tests/test_help_catalog.py` 4 passed (includes the new `/app` entry: listed in /help → Games, not in the "/" menu).
The bot's whole suite cannot run in the sandbox (aiogram/SQLAlchemy not installable: no package network). Run on the **original zip and on the
modified project with the same command, the failing sets are identical** (124 failed / 160 errors / 371 passed in both; all are
missing-module errors in the sandbox), i.e. the changes introduced no new failures. `tests/test_ella_app.py` is skipped there (needs aiogram);
the same handler logic was executed against a minimal stub of aiogram instead (see row 1–3).

## 3. Your checklist

| # | Item | Status | Evidence / caveat |
|---|---|---|---|
| 1 | `/app` launches correctly | Logic ✔ / Telegram ✘ | Handler run against a stubbed aiogram: private chat → Mini App (`web_app`) button with `ELLA_GAMING_URL`; group → link only; unset or non-https URL → "not switched on". **Not run against Telegram.** |
| 2 | Mini App button works | Logic ✔ / Telegram ✘ | Games-hub button `ella:app` → same message edited in place with the Mini App button and a "⬅️ Games" row. Not run against Telegram. |
| 3 | Both open the same application | ✔ (logic) | `/app` and the hub button produce the same URL (asserted in `tests/test_ella_app.py` and in the stub run). BotFather Main Mini App uses the same URL by configuration (§5.6 README). |
| 4 | Homepage works | ✔ | e2e: greeting with name, avatar, brand, Featured / Categories / Popular / Favourites / Continue playing, search bar |
| 5 | Catalogue loads | ✔ fake / ◐ real | e2e + backend on the fake feed incl. validation, caching, failure fallback. Real feed: shape and categories checked by reading it; not loaded by the server. |
| 6 | Search and categories work | ✔ | backend: ranking, pagination, every category chip returns only its games; e2e: lazy loading, typing in search, chip filtering |
| 7 | Games launch correctly | ✔ fake / ✘ real | e2e opens a game inside the Mini App iframe, taps it (touch) and the score changes; loading cover, timeout → error state with retry |
| 8 | Games work on Android | ◐ | Chromium emulating Pixel 7 with touch; no horizontal scroll at small widths. **No physical Android device, no Telegram Android client.** |
| 9 | No advertisements appear | ✔ fake / ✘ real | Verifier tests (below). e2e asserts no ad requests, pop-ups or external redirects while playing. For real games this depends on the scan (see §1 and KNOWN_LIMITATIONS #1–2). |
| 10 | Navigation works | ✔ | exactly three tabs Home · Games · Profile; page text contains no "Stats"/"Leaderboard"; back-to-games and Telegram BackButton |
| 11 | Authentication works | ✔ | valid initData accepted; tampered, signed with another bot's token, expired, future-dated, empty/hash-less, oversize, bot-account logins rejected (duplicate-field rejection is implemented but has no dedicated test); session token tamper/expiry; expired token → silent re-login; outside Telegram → gate screen |
| 12 | Profiles work | ✔ | create on login, nickname validation, favourites, recent, launch history with duration, popularity from own launches; two users are isolated |
| 13 | Existing bot functionality unchanged | ◐ | edits to existing files: `bot.py` (1 import + 1 router), `handlers/arcade.py` (1 button row), `utils/helpcatalog.py` (2 entries), `.env.example`, `.gitignore`, `.dockerignore`. Same failing set before/after on the full suite in the sandbox; bot not started against Telegram. |

## 4. Verifier tests (ad / embed checks) on the fake provider

* static ad script (AdSense) → `ads`; first-party script calling an ad network → `ads`; `X-Frame-Options: DENY` → `blocked` (never bypassed);
  HTTP 404 → `blocked`. (Blocking a redirect to another host is implemented in `scan_static` but has no dedicated test.)
* label/ad-like markup only → `review` (hidden from players).
* **real Chromium**: a game that injects an ad iframe at runtime → `ads`; a **visible** "Sponsored · Advertisement" label → `ads`;
  a **hidden** (`display:none`) or **collapsed** label → `clean` with warning `ad-markup-not-rendered`; if the browser cannot load the page a `review` game
  stays hidden.
* clean games are not flagged; slow game shows loading then a failure state; connection loss while playing shows "Connection lost" + reload.

## 5. Bugs found and fixed during this work

1. **Over-eager exclusion (would have emptied the catalogue):** a "Sponsored · Advertisement" text in the HTML was treated as proof of ads
   even if hidden. Now it yields `review`, decided by the rendered-page check; ad-network code remains a hard fail.
2. **Visibility-blind runtime check:** the DOM check counted hidden placeholders and missed visible labels. It now counts only rendered elements (size, `display`, `visibility`,
   opacity, collapsed `overflow:hidden` parents) and always counts ad iframes.
3. **Repeated rescans:** with the browser check on but Playwright missing, every game would have been re-fetched every 15 min; now skipped once the missing-Playwright note is set.
4. Browser check is on by default (`ELLA_SCAN_BROWSER=1`) since it is what verifies `review` games.

## 6. Not tested

Real Telegram clients (iOS/Android/Desktop/Web), a physical Android phone, real GameZipper games (loading, ads, fullscreen, orientation),
load with thousands of simultaneous players, the Docker build, the Caddy/systemd files on a real server, and the bot started against Telegram.
