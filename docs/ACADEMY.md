# Academy Life: documentation

A standalone school/college sim inside the Telegram bot. One **season** = 7 real days shared by every player, then a short **break**, then the
next season. All times use `ACADEMY_TZ` (default `Asia/Kolkata`). It touches only three shared things: `users`, the diamond economy
(ledger) and the GIF helper. In-chat help for players: `/academyhelp`. Build history and per-part notes: `docs/ACADEMY_PROGRESS.md`.

## 1. The week

| Day | Name | What happens |
|---|---|---|
| 1 | Admission | `/enroll` (stream + club), first actions |
| 2 | Classes begin | `/attend`, adventures open |
| 3 | Club and Campus Day | club battle opens, rivals paired, crushes/dates open |
| 4 | Midterms | midterm window; late enrolment closes at the end of the day |
| 5 | Fest Day | adventure bonus; club battle closes at the end of the day |
| 6 | Finals | finals window; last rival duels; part-time job closed |
| 7 | Results and Graduation | ceremony ~10 min after the day starts, report cards |

Streams: Science, Arts, Commerce, Sports, Music. Clubs: Sports, Music, Drama, Coding. Stats: Knowledge, Charisma, Fitness, Creativity,
Energy (Energy limits daily actions). Grades A+ to F, GPA on a 10-point scale. **Every number lives in `game/academy_rules.py`**; the help
pages, the economy report and the tests read it, nothing else repeats a number.

## 2. Architecture

```
game/academy_rules.py            every number, window, reward and the env readers (enabled(), tz(), break_hours(), daily_hour())
database/models.py               the academy_* tables (new tables only; created by db.create_all())
services/academy_calendar.py     PURE calendar: phase_at(start, now), week(start), enroll_window, cycle(); + ensure_season(session, now)
services/academy_students.py     enrolment, stats, lazy Energy, pay_diamonds(...) (the ONLY payout path)
services/academy_actions.py      daily actions (study, attend, ...)       services/academy_life.py   dashboard / views
services/academy_exam_logic.py   PURE exam maths                         services/academy_exams.py  DB + exam_close tick
services/academy_social_logic.py PURE rivals / battle / crush rules      services/academy_social.py + academy_love.py  DB + ticks
services/academy_adventure_logic.py + academy_adventures.py (+ _views)   story engine
services/academy_remind_logic.py PURE reminder/recap/ceremony timing    services/academy_notify.py  send-once log, Sender, rate limiter
services/academy_reminders.py    reminder ticks                          services/academy_graduation.py  ceremony tick, report card, Hall of Fame
services/academy_ceremony.py     ceremony text + CEREMONY_SECTIONS       services/academy_seen.py  group-activity middleware
services/academy_help.py         PURE /academyhelp pages                 services/academy_economy.py  PURE diamond budget (audit)
services/academy_events.py       emit(kind, user_id, **data) / @subscribe(kind)
services/academy_ticks.py        @tick registry + academy_loop (ONE loop)
handlers/academy_*.py            routers; handlers/academy_routers.py lists them (MODULES), bot.build_dispatcher includes them
                                 BEFORE the free-form AI chat router
utils/content/academy_*.py       texts, cards, adventures (data only)
```

Rules of the house: calendar logic takes `now` as a parameter (tests simulate a whole season); handlers and ticks never raise; messages go
through `utils.telegram.safe_send`; GIFs through `utils.gifs`; diamonds only through the ledger with a unique `ref`.

## 3. Tables (all `academy_*`, created automatically; no migration needed)

| Table | Holds |
|---|---|
| `academy_seasons` | season number, theme, start time |
| `academy_students` | one row per user and season: stream, club, stats, Energy, joined_at, late flag |
| `academy_daily` | per user/season/day counters of each action (counts only) |
| `academy_exams`, `academy_attempts`, `academy_rounds` | exam windows, attempts, every round (card, choice, roll, points) |
| `academy_adventure_runs`, `academy_adventure_trophies` | story position/flags per run; cosmetic trophies |
| `academy_rivals`, `academy_rival_duels`, `academy_rival_rounds` | pairings, duels, their rounds |
| `academy_battles`, `academy_battle_plays`, `academy_club_points` | club battle, each member's play, practice points |
| `academy_badges` | club / rival badge points |
| `academy_buddies`, `academy_buddy_bonus` | study-buddy pairs/requests and the daily bonus claim |
| `academy_crushes`, `academy_couples`, `academy_couple_dates` | opt-in crushes (private until mutual), couples, dates |
| `academy_social_runs` | send-once markers of the social ticks |
| `academy_hall_of_fame` | ceremony results per graduate (display name, rank, GPA, titles) |
| `academy_user_prefs` | `/academyremind` setting, whether the bot could reach the user |
| `academy_notify_log` | send-once log: which message kind went to which chat |
| `academy_group_seen` | group id + last activity (written at most once per 6 h per group) |
| `academy_chat_members` | group id + user id, written when `/exam`, `/grades` or `/classrank` is used in the group |

Never alter a table made by an earlier part (`create_all` does not migrate): add a NEW table.

## 4. Commands

All are in `/help` under **Academy 🎓** and none is in the `/` menu (`utils/helpcatalog.MENU` / `GROUP_MENU_EXTRA`; `utils/commands.py` builds
the menu only from those; tested).

* Start: `/enroll`, `/academy` (alias `/school`), `/schedule` (alias `/timetable`), `/academyhelp [page]`
* Daily life: `/study`, `/attend`, `/hangout`, `/rest`, `/club`, `/parttime`
* Exams: `/exam`, `/grades`, `/classrank`
* Adventures: `/adventure`
* Social: `/rival`, `/rivalduel`, `/clubs`, `/buddy`, `/crush`, `/crushoff`, `/campuscouple`, `/campusdate`
* Reminders and records: `/academyremind`, `/reportcard`, `/halloffame`
* Owner only (not in /help): `/academyadvance`, `/academyreset`

`/academyhelp` pages: `start`, `daily`, `exams`, `adv`, `social`, `week`, `how`, `how2` (aliases such as `privacy`, `exam`, `timeline`).

Callback prefixes (all carry the owner id, all verified; each < 64 bytes): `ac:` core, `acx:` exams, `acv:` adventures, `acr:` rival duel,
`acb:` club battle, `acp:`/`acu:` crush & buddy, `acs:` staff, `ach:` help. `acn:e:` (Enroll button of group announcements) acts only for the
person pressing.

## 5. Ticks (one loop: `academy_loop`; modules are listed in `services/academy_ticks.TICK_MODULES`)

| Tick | Purpose | Why a restart is safe |
|---|---|---|
| `academy.rollover` | ensure the season, emit `season_started` / `season_ended` | marker in `app_state`, season rows sit on a fixed grid |
| `academy.exam_close` | finish attempts whose window closed, pay the reward | attempt state + ledger `ref` |
| `academy.rival_pairing` | pair rivals once, DM them | one rival row per user and season, send-once log |
| `academy.club_battle_open` | open the battle | the battle row is looked up before it is created |
| `academy.social_close` | close battles, pay winners, settle duels | `social_runs` markers + ledger `ref` |
| `academy.adventure_expire` | end idle runs | `finished` flag |
| `academy.remind_season_open` | season announcement + Enroll button in active groups | `academy_notify.claim` (send-once log) |
| `academy.remind_enroll` | enrolment reminders (Days 1-4) | send-once log, quiet hours |
| `academy.remind_enrolled` | one DM reminder per enrolled player per local day | `dm_daily` slot in the send-once log |
| `academy.recap` | daily recap (group + DM) | send-once log |
| `academy.notify_purge` | purge old log rows | plain delete |
| `academy.ceremony` | titles, Hall of Fame, ceremony, report cards | `ceremony_done` marker + send-once log |

Add a tick: `@tick(every=300, name="academy.my_tick")` in a service module and list the module in `TICK_MODULES`. It must be idempotent.
Never start another loop.

## 6. Events (`services/academy_events.py`)

`emit(kind, user_id, **data)` after every meaningful action; extras use `@subscribe(kind)` and never edit the core code. Kinds: `enrolled`,
`season_started`, `season_ended`, `reset`, `studied`, `attended`, `hangout`, `rested`, `club_practice`, `parttime`, `study_streak`,
`exam_started`, `exam_round`, `exam_finished`, `adventure_done`, `graduated`, `ceremony_done`, `remind_changed`; the social services emit their own kinds (`emit(kind, ...)` / `res.event`). A failing subscriber is logged and never breaks the player's action.

## 7. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `ACADEMY_ENABLED` | `1` | kill switch (`0/off/false/no`): commands answer "closed", ticks idle, data is kept; `/academyhelp` still works |
| `ACADEMY_TZ` | `Asia/Kolkata` | all calendar days, quiet hours, night adventures, recap time |
| `ACADEMY_BREAK_HOURS` | `6` | break between seasons (0 to 168) |
| `ACADEMY_DAILY_HOUR` | `22` | local hour of the daily recap |
| `ACADEMY_GRADUATION_DIAMONDS` | `0` | `1` = pay title prizes at graduation (off by default) |

## 8. How to extend

**Add an adventure.** In `utils/content/academy_adventures.py` build it with `_adv(key, name, emoji, blurb, energy, days, start, nodes,
night=False, stream_bonus=None)`; nodes are `Node(key, text, choices)` where a `Choice(text, to=..., check=Check(stat, difficulty, success, fail), sets=, needs=, hides=)`
moves or rolls one stat check, and endings are made with `_end(key, text, title=..., mood=..., diamonds=..., stats={...}, trophy=...)`. Add it to `ADVENTURES` and `ORDER`. Rules: 8-12 nodes, at least 3 endings,
diamonds <= `AR.ADV_MAX_DIAMONDS`, unique trophy keys. `tests/test_academy_adventure_data.py` validates the data; update the `ORDER`/energy
assertions there. The help page, the Week page and the economy report pick it up by themselves.

**Add scenario cards (exam rounds, duels, battle rounds).** In `utils/content/academy_exam_rounds.py` add `_c(key, stream_or_None, difficulty,
title, text, (label, stat, risk, win_text, lose_text), ...)` to `CARDS` (three buttons; risk is safe / balanced / risky; difficulty as in
`AR.STAT_TARGET`). General cards
(`stream=None`) are shared; stream cards are drawn for that stream first. **Game-style only: never a real-subject question with a right
answer.** The data test checks the shape.

**Add a ceremony / report-card section.**
```python
from services.academy_ceremony import ceremony_section

@ceremony_section(name="my_extra", order=200, scope="both")      # "ceremony" | "card" | "both"
async def my_extra(ctx):          # sync or async; ctx.scope, ctx.data (CeremonyData), ctx.card (CardData | None)
    return "🌟 ..."               # HTML text, or None to skip
```
Built-in blocks use order 10-60; extras land after them and before the footer. A raising section is logged and skipped; the same name replaces
a section; `unregister(name)` removes it. `ctx.data.session` is an open session: read-only please.

**Add a help page.** Add the key to `PAGES` in `utils/content/academy_help_texts.py`, a builder in `services/academy_help.py` that reads its
numbers from `AR` at call time, and keep it under 4000 characters (tested).

## 9. Diamond economy (from `services/academy_economy.py`, upper bounds for perfect, lucky play)

Every payout uses `academy_students.pay_diamonds` (ledger, unique `ref`, so a retry cannot pay twice). No real money anywhere.

* Highest a player can earn on a normal day (part-time + best adventures, on Fest Day): **190** 💎 <!-- economy:per_day_recurring=190 -->
* Best single day, with one-off prizes (day **4** <!-- economy:peak_day=4 -->): **280** 💎 <!-- economy:peak_day_total=280 -->
* Best whole season: **1370** 💎 <!-- economy:season_total=1370 -->  (part-time 600, adventures 390, exam rewards 240, duel win 60, winning club 80)
* Optional graduation prizes (off by default): up to **85** 💎 <!-- economy:graduation_optional=85 -->
* Sinks: only the campus date (15 💎 each, at most **75** 💎 a season) <!-- economy:sink_season=75 -->. Everything else costs Energy.
* Energy is not the binding limit for diamonds: the busiest earning day needs **100** Energy <!-- economy:energy_needed_peak=100 --> and a day
  gives about **315** <!-- economy:energy_supply_day=315 --> (refill plus rests). The real limits are the daily part-time cap, the shift
  limit, the 2 adventures a day and the once-a-season prizes.
* Real players earn far less: the figures assume the best ending of every adventure, A+ in both exams, a won duel and a winning club.

Observation for the owner: with a single small sink, the Academy is a faucet. If it ever inflates the economy, lower
`DAILY_DIAMOND_CAP`, `ADV_MAX_DIAMONDS` or the prizes in `game/academy_rules.py`; nothing else needs changing.

## 10. Privacy (what is stored, how to opt out)

Stored, linked to the Telegram user id: the tables in section 3 (stats, per-day counters, exam rounds, adventure runs, rival/club/buddy/couple
records, a pending crush, Hall of Fame display name, reminder setting, send-once log, group id + last activity). Not stored: the text of
messages, photos, location, phone number. No database write happens per message (the group-activity write is throttled in memory to once per 6 h
per group). Opt out: `/academyremind off` (no unsolicited DMs), do not `/enroll`, `/crushoff`, `/buddy off`. There is no self-service
delete in this version; the owner can delete the user's `academy_*` rows.

## 11. Audit (Part 7)

Automated in `tests/test_academy_audit.py`: no command collisions; all commands in the Academy help category and none in the `/` menu; callback
data < 64 bytes at worst-case ids; every callback checks who pressed; unguarded Telegram calls are impossible in academy code; the in-memory
dicts (`_seen_mono`, reminder `_cursor`) are bounded; the economy numbers above match the rules; every table/command/tick is documented here;
two whole seasons are walked in 30-minute steps (three time zones, three break lengths).
