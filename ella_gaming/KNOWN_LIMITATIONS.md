# Known limitations

Plain list of what is **not** proven or not possible. Nothing here is hidden behind a feature flag.

## Ads and the game provider
1. **The real eligible game count is unknown until you run the scan.** The authoring sandbox could not reach gamezipper.com from its
   shell (outbound allow-list), so the verifier was never run against the live games. `python -m ella_server scan` on your server prints the
   true numbers (fetched / eligible / excluded / reasons).
2. **GameZipper's `embedUrl` points at full website pages, and the pages we could read contain ad markers.** Two sampled pages
   (star-loom, sukima) show a Google AdSense account meta tag and "Sponsored · Advertisement" labels, also with the documented embed
   parameters. The publisher page says embedded games carry "zero ads injected", which does not obviously match. The runtime check decides by
   what is *rendered*; if those labels are visible, the affected games are excluded and the catalogue may be small or even empty.
   That is the correct outcome for an ad-free app, and the fix is a clean embed endpoint from the provider, not a weaker filter.
3. Because those are full pages, a game may also show GameZipper's own page furniture (headings, "More … Games" links to gamezipper.com).
   Links that try to leave the game are contained by the sandbox (no top navigation, no pop-ups); they can navigate *inside* the game frame.
4. A scan is a snapshot. Ads that appear only after long play, a particular level, or A/B test buckets can be missed; providers can change a
   game after it was checked (verdicts expire after 7 days by default and are re-checked, and the unchecked-game policy is "hide").
5. The ad-domain list is a curated blocklist, not exhaustive; an unknown network from a never-seen domain would be caught only if it
   shows up as a visible label, popup, dialog or ad iframe at runtime.
6. Game licences: the publisher page says embedding is free, commercial use allowed, no approval, and forbids redistributing raw game files
   or hosting them on a competing portal. It has no formal licence text or terms-of-service link. Ella embeds the provider's own URLs and
   copies nothing. Get written confirmation from partners@gamezipper.com if you need certainty.

## Catalogue and categories
7. GameZipper provides one category per game (≈ 11 values, mostly `puzzle`). Ella's Action / Adventure / Strategy / Multiplayer are
   inferred from tags and names, so those categories can be small or empty; empty categories show a "no games" state.
8. The feed is read, not controlled: if GameZipper changes its JSON shape, entries that no longer validate are dropped and counted
   (`dropped_invalid` in the report) while the last good cache keeps serving.

## Telegram / devices
9. **Not tested inside real Telegram clients or on a physical Android phone.** Tests ran in headless Chromium emulating a Pixel 7 (touch,
   small viewport) with a mock of the official `Telegram.WebApp` object and correctly *signed* `initData`, so the server-side login is
   exercised for real but the client behaviour is simulated.
10. Fullscreen uses Telegram's `requestFullscreen` (Bot API 8.0+, newer clients) and falls back to the browser Fullscreen API, else the button is hidden.
    Some games manage their own fullscreen/orientation; those are not controllable from outside the iframe.
11. Mini App buttons only work in private chats; in groups `/app` shows a link to the bot (or the BotFather direct link).
12. Session duration is measured by heartbeat pings while a game is open and the app is visible; it is an estimate (a killed app records the
    last ping, capped at 12 h).

## Platform
13. Single server process by design (in-memory rate limits and scan state). SQLite handles this workload comfortably; scale-out
    would need a shared rate limiter and a server database.
14. The first full runtime scan of ~470 games takes a while (several minutes to ~30 min depending on CPU/network). Until a game
    is verified it is hidden, so a fresh install fills up gradually; the app shows a small "verifying games" hint meanwhile.
15. The Dockerfile and docker-compose were written but **not built** (no Docker in the sandbox). The same commands were run directly with Python.
16. The bot-side code was run only against a stubbed aiogram and the bot's pure test files; the bot itself was not started against Telegram.
    Run `python -m pytest -q tests/test_ella_app.py tests/test_help_catalog.py` on your own environment.
