# Moderation framework

A self-contained group-moderation module (`moderation/`) that runs inside the bot but shares **no game, economy, AI or social code**.
Everything automatic is **opt-in per group** (all protections default to OFF), so installing it changes nothing until an admin enables something.

> **Status: written and statically verified, but NOT yet run against a live aiogram / database.**
> The build sandbox had no network, so aiogram, SQLAlchemy and pytest could not be installed. What *was* run: syntax compilation of every file,
> an undefined-name scan, a cross-module reference check, 34 pure-logic / architecture tests, and the existing `test_help_catalog` (which the
> integration touches). See "Verification" below for the exact list and for what you must run on your machine before going live.

## Layout

```
moderation/
  util.py defaults.py matching.py      pure logic (no Telegram / DB): durations, templates, locks, filters, flood/duplicate trackers
  models.py store.py                   own mod_* tables, settings cache, warnings, logs, scheduler rows, reports, confirmations, backup
  perms.py                             authorisation (by numeric id only), protected accounts, Telegram error translation, target resolution
  actions.py                           ban/kick/mute/restrict/warn/delete primitives: Telegram first, record only after Telegram confirmed
  confirm.py                           YES/NO confirmation (DB-backed, atomic claim) + safe bulk runner (throttle, progress, stop, summary)
  base.py                              routers, @command helper, error middleware, anonymous-admin verification, request preparation
  commands_*.py dashboard.py           the commands and the inline dashboard
  enforce.py                           automatic protection (outer middleware), captcha, raid, welcome/goodbye, chat_member logging
  scheduler.py lockdown.py greet.py    temp-punishment expiry + housekeeping, emergency lockdown, greeting renderer
  guard.py api.py install.py           AI-isolation guard, read-only hook for the old welcome, wiring
```

## Changes to existing files (5, all small and exception-safe)

| File | Change | Why |
|---|---|---|
| `bot.py` | install moderation (inside try/except), create `mod_*` tables, start its scheduler task | wiring; if moderation fails to load the bot runs exactly as before |
| `middlewares/core.py` | 5 lines: handlers flagged as moderation skip `CoreMiddleware` | otherwise the game's 5-commands/min throttle, game-ban/mute, "games disabled" gating and "ignore un-addressed chatter" would break moderation |
| `handlers/welcome.py` | `welcome_on(chat_id, kind)` asks `moderation.legacy_welcome_suppressed` (try/except → old behaviour) | a group with its own `/setwelcome` must not get two greetings |
| `handlers/admin.py` | owner's `/ban /unban /mute /unmute` also answer to `/botban /botunban /botmute /botunmute` (and the older-named `/gameban …` aliases) | in groups `/ban` now means *group* ban; the owner keeps game-level ban from groups via the new names (DM behaviour unchanged) |
| `utils/helpcatalog.py` | the eight `bot*` / `game*` names added to `STAFF` | keeps `test_help_catalog` green (admin commands must be hidden) |

### Greetings & GIFs

* The bot's original welcome / goodbye messages stay **by default**. They step aside only after an admin writes and enables their own `/setwelcome` / `/setgoodbye` (`/resetwelcome` brings the default back). Captcha alone does not silence them.
* `/kick` (and `/ban`, `/tban`, `/dban`, `/mute`, `/tmute`) first send the game's reaction GIF when one exists (kick→kick, ban→yeet, mute→bonk), then perform the real action, then post “X is kicked / banned / muted”. The GIF is injected from `bot.py`; moderation never imports game code.

## Command overlaps with the game (deliberate)

* `/ban /unban /mute /unmute /userinfo` — **in groups** → group moderation (needs admin rights; non-admins get a clear refusal). **In DMs** → the old owner/game command, unchanged. Owner game-level actions from a group: `/botban`, `/botunban`, `/botmute`, `/botunmute` (these only flag the user inside the bot's own database; they never touch the Telegram group; there is no `/botkick` because the game never had a kick of that kind).
* `/kick` — real kick **only for group admins / the bot owner**; everyone else's `/kick` stays the game's joke action. `/kickuser` is the unconditional alias.
* `/rules` — shows the group's rules if an admin set them; otherwise falls through to the game's own `/rules`. `/grouprules` always shows the group's rules.
* The owner's plain-language game-level ban/unban through the AI (existing feature) is **kept as is**; it only flips the game's `users` flags and never touches Telegram groups.

## Commands (129 lines of tables below; 95 commands, 131 names with aliases)

Targets: reply, `@username` (members the bot has seen), numeric ID, or a text-mention. Durations: `30s 15m 2h 3d 1w 1h30m` (minimum 30 s).
Prefixes `/` and `!` both work. `/modhelp` shows all of this inside Telegram.

### Member moderation

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `/ban` | `/banuser` | `/ban <target> [duration] [reason]` | Ban a member (permanent, or temporary when you add a duration like 2h / 3d) |
| `/banme` | — | `/banme` | Ban yourself (asks YES/NO first; admins can unban you) |
| `/dban` | `/delban` | `/dban (reply) [reason]` | Delete the replied message AND ban its sender |
| `/kick` | `/kickuser` | `/kick <target> [reason]` | Remove a member (they may rejoin). Regular members' /kick stays the game's joke action |
| `/kickme` | — | `/kickme` | Leave the group (you can rejoin by link) |
| `/mute` | `/silence` | `/mute <target> [duration] [reason]` | Mute a member (permanent, or temporary with a duration) |
| `/resetwarns` | `/clearwarns` | `/resetwarns <target>` | Clear ALL of one member's warnings |
| `/restrict` | — | `/restrict <target> <types…> [duration] [reason]` | Limit ONE member: text media photos videos audios documents voice videonotes polls other previews invite pin info |
| `/tban` | `/tempban` | `/tban <target> <duration> [reason]` | Temporary ban; the ban is lifted automatically |
| `/tmute` | `/tempmute` | `/tmute <target> <duration> [reason]` | Temporary mute; permissions come back automatically |
| `/unban` | `/unbanuser` | `/unban <target> [reason]` | Lift a ban (works with a user ID too) |
| `/unmute` | `/unsilence` | `/unmute <target>` | Restore a muted / restricted member's permissions |
| `/unrestrict` | — | `/unrestrict <target> [types…]` | Lift restrictions (all, or just the listed types) |
| `/unwarn` | `/rmwarn` | `/unwarn <target>` | Remove a member's latest warning |
| `/userinfo` | `/uinfo` | `/userinfo [target]` | Everything the moderation system knows about a member (admins only) |
| `/warn` | `/warnuser` | `/warn <target> [reason]` | Warn a member; the group's warn limit triggers the automatic punishment |
| `/warnings` | `/warns` | `/warnings [target]` | Show warnings (yours, or a member's); also shows the group's limit |

### Mass moderation & clean-up

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `/banall` | — | `/banall [reason] [user IDs…]` | Ban every tracked, non-protected member (asks YES/NO, shows progress) |
| `/cleandownloads` | — | `/cleandownloads [hours]` | Delete recent file messages (documents, audio, video, voice, GIFs) that I tracked (asks YES/NO) |
| `/cleanservice` | — | `/cleanservice on\|off [join leave pin title photo other] \| now` | Auto-delete 'X joined / left / pinned…' service messages; 'now' removes the recent ones (asks YES/NO) |
| `/delall` | — | `/delall <target>` | Delete every recent message (last ~48h that I tracked) of one member (asks YES/NO) |
| `/kickall` | — | `/kickall [reason] [user IDs…]` | Remove every tracked non-protected member without banning (asks YES/NO) |
| `/kickinactive` | — | `/kickinactive <days>` | Remove tracked members silent for N days (only members I have seen; asks YES/NO) |
| `/muteall` | — | `/muteall [duration] [reason] [user IDs…]` | Mute every tracked non-protected member (for a group-wide silence /lock all or /lockdown is faster) |
| `/purge` | — | `/purge (reply to the first message to delete)  or  /purge <N>` | Delete everything from the replied message down to now (asks YES/NO above 10 messages) |
| `/restrictall` | — | `/restrictall <types…> [duration]` | Restrict tracked members from given types, e.g. media stickers (asks YES/NO) |
| `/unbanall` | — | `/unbanall [user IDs…]` | Unban every member I know was banned (asks YES/NO) |
| `/unmuteall` | — | `/unmuteall` | Unmute every tracked member who is currently restricted (asks YES/NO) |
| `/unrestrictall` | — | `/unrestrictall` | Lift restrictions of every tracked restricted member (asks YES/NO) |
| `/unwarnall` | — | `/unwarnall` | Clear ALL warnings of ALL members in this group (asks YES/NO) |
| `/warnall` | — | `/warnall [reason]` | Give every tracked non-protected member a warning (asks YES/NO) |
| `/zombies` | — | `/zombies [clean]` | Find deleted accounts among tracked members; '/zombies clean' removes them (asks YES/NO) |

### Administrator management

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `/admincache` | — | `/admincache` | Refresh my cached list of admins and re-check my own rights |
| `/admininfo` | `/ainfo` | `/admininfo [target]` | An admin's exact rights, title and whether I can edit them |
| `/adminlist` | `/admins` | `/adminlist` | List the group's admins with their titles |
| `/checkadmin` | `/isadmin` | `/checkadmin [target]` | Is this person an admin? (no target: checks you and me, incl. the rights I'm missing) |
| `/demote` | — | `/demote <target>` | Remove someone's admin rights (asks YES/NO). Telegram only allows it for admins I promoted |
| `/permissions` | `/perms` | `/permissions [reset]` | Show the group's member permissions and active locks; 'reset' lifts every lock (asks YES/NO) |
| `/promote` | — | `/promote <target> [basic\|mod\|full\|all] [+right -right …] [title]` | Make someone admin (rights presets: basic, mod, full; tune with +pin -info). Asks YES/NO first |
| `/setadmin` | — | `/setadmin <target> <basic\|mod\|full\|none\|+right -right …> [title]` | Change an admin's rights exactly (confirmed with YES/NO) |
| `/staff` | — | `/staff` | Admins plus the protected accounts the moderation system will never touch |
| `/title` | — | `/title <target> <text>` | Set an admin's custom title (max 16 chars, no emoji; only admins I promoted) |

### Messages, pins & locks

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `/del` | `/delete` | `/del (reply)` | Delete the replied message (and your command) |
| `/edit` | — | `/edit <new text> (reply to MY message)` | Edit one of my own messages (Telegram doesn't let bots edit anyone else's) |
| `/lock` | — | `/lock <types…>` | Forbid message types for everyone but admins (see /locktypes). 'all' asks YES/NO |
| `/locktypes` | `/locks` | `/locktypes` | All lock types and which are currently on |
| `/pin` | — | `/pin (reply) [loud]` | Pin the replied message (silent by default; add 'loud' to notify everyone) |
| `/pinned` | — | `/pinned` | Jump to the current pinned message |
| `/unlock` | — | `/unlock <types…>` | Allow message types again |
| `/unpin` | — | `/unpin [reply]` | Unpin the replied message, or the most recent pin |
| `/unpinall` | — | `/unpinall` | Remove EVERY pinned message (asks YES/NO) |

### Group settings

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `/goodbye` | — | `/goodbye [on\|off]` | Show or toggle the goodbye message |
| `/resetgoodbye` | — | `/resetgoodbye` | Remove the custom goodbye |
| `/resetrules` | `/clearrules` | `/resetrules` | Delete the group's rules |
| `/resetwelcome` | — | `/resetwelcome` | Remove the custom welcome (the default greeting returns) |
| `/rules` | `/grouprules` | `/rules` | Show this group's rules (if none are set, /rules still shows the game guide) |
| `/setdescription` | `/setabout` | `/setdescription <text>  (alias /setabout)` | Set the group's description / about text (max 255) |
| `/setflood` | — | `/setflood <messages\|off> [seconds] [delete\|warn\|mute\|kick\|ban]` | Anti-flood: max messages per time window, and the punishment |
| `/setgoodbye` | — | `/setgoodbye <text>` | Custom goodbye message when someone leaves |
| `/setrules` | — | `/setrules <text> (or reply to a message)` | Set this group's rules (formatting kept) |
| `/settitle` | — | `/settitle <new title>` | Rename the group (needs 'Change group info' for me and you) |
| `/setwarnexpire` | — | `/setwarnexpire <duration\|off>` | Warnings older than this stop counting (e.g. 30d) |
| `/setwarnlimit` | — | `/setwarnlimit <1-20>` | How many warnings trigger the automatic punishment |
| `/setwarnmode` | — | `/setwarnmode <ban\|kick\|mute\|tmute\|tban> [duration]` | What happens at the warn limit |
| `/setwelcome` | — | `/setwelcome <text> (placeholders, buttons)` | Custom welcome message with {first} {mention} {chatname} {count}… and buttons |
| `/slowmode` | — | `/slowmode <seconds\|off>` | Minimum delay between a member's messages (I enforce it by deleting too-fast messages) |
| `/welcome` | — | `/welcome [on\|off\|clean on\|off]` | Show or toggle the welcome message |

### Anti-spam & security

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `/antibot` | — | `/antibot on\|off [action=kick\|ban]` | Remove bots added by non-admins |
| `/antiflood` | — | `/antiflood on\|off [limit=8 window=10 action=mute duration=10m]` | Message-frequency limit (also see /setflood) |
| `/antiforward` | — | `/antiforward on\|off [action=delete duration=1h]` | Block forwarded messages |
| `/antilink` | — | `/antilink on\|off [action=delete duration=1h]` | Block links except whitelisted domains |
| `/antiraid` | — | `/antiraid on\|off [joins=8 window=30 lockdown=10m action=kick\|ban\|mute]` | Detect join floods automatically |
| `/antispam` | — | `/antispam on\|off [dup=3 window=60 repeat=15 mentions=10 links=on cross=on action=delete duration=1h]` | Duplicate / repeated-character / mention / link-spam protection |
| `/approve` | — | `/approve <target>` | Exempt a member from every automatic filter (anti-spam, locks, flood…) |
| `/approvedlist` | `/approved` | `/approvedlist` | List approved members |
| `/blocklist` | `/blacklist` | `/blocklist [list \| add [word\|phrase\|link\|domain] <text> [action=warn] \| remove <text\|#id> \| clear]` | Blocked words, phrases, links and domains (alias /blacklist) |
| `/captcha` | — | `/captcha on\|off [mode=button\|math timeout=120 fail=kick\|ban\|mute]` | Verify new members before they can talk |
| `/filter` | — | `/filter <word\|phrase\|link\|domain> <delete\|warn\|mute\|kick\|ban\|reply>[:duration] <text> [=> reply text]` | Custom filter: match text and react (delete / warn / mute / kick / ban) and/or auto-reply |
| `/filters` | — | `/filters` | List every custom filter (blocklist entries too) |
| `/lockdown` | `/emergency` | `/lockdown on [duration] \| off \| status` | Emergency mode: silence the whole group at once (asks YES/NO to start) |
| `/protection` | `/protectionlevel` | `/protection [off\|low\|medium\|high]` | One-step community protection level that configures flood / spam / links / captcha / raid / slow mode together |
| `/raidmode` | — | `/raidmode on [duration] \| off` | Manually start / stop raid mode (new joiners are removed) |
| `/stopfilter` | `/unfilter` | `/stopfilter <#id \| text>` | Delete a filter |
| `/suspicious` | — | `/suspicious` | Recent suspicious-activity alerts the monitor recorded |
| `/unapprove` | — | `/unapprove <target>` | Remove a member's exemption |
| `/whitelist` | — | `/whitelist [list \| add <domain> \| remove <domain>]` | Domains that anti-link always allows |

### Reports, logs, backup, health, help

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `/health` | `/communityhealth` | `/health` | Community health: activity, growth, warnings, reports, protection coverage |
| `/modbackup` | `/backupmod` | `/modbackup` | Export this group's moderation settings, warnings, filters, approvals and tracked members as a file |
| `/modhelp` | `/moderation` | `/modhelp [command]` | This help: every moderation command with syntax |
| `/modlog` | `/mlogs` | `/modlog [target] [count]` | Moderation history: who did what, to whom, when, result |
| `/modpanel` | `/dashboard` `/mod` | `/modpanel` | Interactive moderation dashboard (admins) |
| `/modrestore` | `/restoremod` | `/modrestore (reply to a backup file)` | Restore from a backup file - merges missing records, replaces settings only after YES/NO |
| `/report` | `/reportuser` | `/report [reason] (reply to the message)  or  /reportuser <@user\|id> [reason]` | Report a member / message to the admins (rate-limited) |
| `/reports` | — | `/reports` | Pending reports with one-tap actions (admins) |
| `/securityreport` | `/secreport` | `/securityreport [days]` | Security report: what was caught, by which protection, and the most-punished members |


## Telegram permissions

The bot must be an **admin**. Rights used: *Ban users* (ban/kick/mute/restrict/warn punishments/locks/captcha/raid/lockdown), *Delete messages* (del, purge, filters, flood/spam/link, slow mode, cleanups),
*Pin messages* (pin family), *Add new admins* (promote/demote/setadmin/title — and it can only hand out rights it has itself), *Change group info* (settitle/setdescription).
Missing rights are detected **before** acting; the reply names the exact right to enable. The person giving the command needs the matching right too (the bot owner and group owner always pass; checked live by user id, never by username).
For automatic protections to see messages in groups the bot must be admin (or have privacy mode off). `chat_member` updates (join tracking, manual-action logging) also need admin.

## Confirmation (YES / NO)

Required for: banall, unbanall, muteall, unmuteall, kickall, restrictall, unrestrictall, warnall, unwarnall, kickinactive, zombies clean, purge (> 10 messages), delall, cleanservice now, cleandownloads,
promote, demote, setadmin, unpinall, lock/unlock all, permissions reset, lockdown start, blocklist clear, modrestore, banme, dashboard "reset all settings".
Stored in `mod_pending`: only the issuer can answer, expires after 90 s, YES is an atomic claim (double taps / second admins get "already handled"), permissions are re-checked on YES, one bulk operation per group at a time,
live progress with a Stop button, final statistics (done / failed / skipped + reasons). A restart marks running confirmations *interrupted* and never re-runs them.
Low-risk commands (info, single warn/mute/ban, …) never ask.

## Database

New tables only (own `ModBase` metadata, so the game's `create_all`/Alembic never see them): `mod_meta` (schema version), `mod_settings` (JSON per chat), `mod_warns`, `mod_actions` (audit log),
`mod_punishments` (scheduled expiries), `mod_members` (tracked members), `mod_msgs` (recent message ids, ~48 h), `mod_filters`, `mod_approved`, `mod_reports`, `mod_pending`, `mod_captcha`.
Created idempotently at start-up; `store._migrate` is the hook for future additive migrations. No game table is read or written. Every row carries `chat_id`; nothing is shared between groups.
If you use Alembic for production, either keep `init_schema()` (it only creates missing `mod_*` tables) or generate a migration for them separately.

## Known Telegram Bot API limitations (and what is done instead)

* **No member list / no banned list**: bulk commands work on members the bot has *seen* (tracked) plus IDs you pass; every prompt and summary states the covered count. For an instant group-wide silence use `/lockdown` or `/lock all`.
* **No "delete all messages of a user" / no history**: `delall`, `cleandownloads`, `cleanservice now` use the ~48 h of message ids the bot tracked. `deleteMessages` does not say which ids existed, so results say "submitted", not "deleted N".
* **No slow-mode setter**: `/slowmode` is enforced by deleting too-fast messages (needs Delete rights); Telegram's native value is shown if set.
* **No per-user GIF/sticker permission**: `gifs`, `stickers`, `animations`, `links`, `forwarded`, `contact`, `location` locks are bot-side (delete); photos, videos, audios, documents, voice, videonotes, polls, `other`, previews, invite, pin, info are native.
* **Demote / title** only work for admins the bot promoted. **`/edit`** only edits the bot's own messages. **Bots can't be told apart from users by username**: username targets work for members the bot has seen.
* Captcha / raid / manual-action logging depend on `chat_member` updates, which Telegram sends only when the bot is admin.

## AI isolation

* Nothing in `ai/` or `handlers/ai_chat.py` imports or mentions the moderation module; the moderation package imports no AI/game code (enforced by `tests/test_moderation_static.py`). Only `bot.py` and `handlers/welcome.py` import it.
* Every moderation action is a dedicated command handler with its own live authorisation; there is no function an AI response can call.
* `guard.py` answers plain-language requests like "ban him" / "mute everyone" from non-owners with a fixed explanation and never calls the model. (Owner messages are untouched, per your decision to keep the owner's game-level ban/unban.)

## Verification

Run here (no network, no aiogram/SQLAlchemy): `py_compile` of all files · undefined-name scan · cross-module reference check ·
`python tests/run_pure.py` → 34 tests pass (durations, templates, buttons, link/host extraction, blocklist matching incl. unicode evasion, flood & duplicate trackers, suspicion score, settings defaults/merge/per-group isolation,
lock → permission computation, message-kind detection, protection presets, AI-guard patterns incl. false-positive cases, no hard-coded identities; AST tests for required commands, duplicate names, game-command clashes, AI↔moderation import isolation,
no game-table access, confirmation coverage, callback namespacing, minimal edits to existing files, table prefixes) · existing `tests/test_help_catalog.py` passes.
Tests found and fixed three real bugs during development (free-form `locks` settings were dropped on reload; a lock command would have overwritten hand-set permissions; the AI guard matched "remove the bug").

**Not run (needs your environment):** the existing suite (needs pytest/aiogram/SQLAlchemy), any live Telegram call, the database layer, the middleware/router wiring, confirmation flow, scheduler, bulk runs, rate-limit and network-failure handling, restart recovery, multi-group behaviour.
Please run, in this order: `pip install -r requirements-dev.txt && pytest -q` (existing + new tests), start the bot in a throw-away supergroup and walk through `/modhelp`.
Points I'd check first because they depend on aiogram internals I could not execute: (1) moderation handlers really bypass `CoreMiddleware` (try `/ban` as admin while games are disabled / right after 6 other commands);
(2) `/rules` falls back to the game guide in a group without custom rules; (3) a custom `/setwelcome` suppresses the old greeting; (4) a non-admin's `/kick` still triggers the joke action; (5) anonymous-admin verify button.
