# Academy Life — progress log

Standalone school sim: one season = 7 real days shared by everyone, then a break (ACADEMY_BREAK_HOURS, default 6), then the next season.
Each part appends its own section below. Read this file first, build on the public functions, never rename or rewrite earlier work.

## Shared rules (all parts)
* Tables are `academy_*`, defined in `database/models.py`. Never add columns to an earlier part's table (create_all will not migrate it): use a NEW table.
* Routers: add the module name to `MODULES` in `handlers/academy_routers.py` (each module exposes `router`). `build_dispatcher` includes them before the AI chat router.
* Ticks: add `@tick` functions in a `services/academy_*.py` module and list that module in `TICK_MODULES` in `services/academy_ticks.py`. One loop only.
* Events: `await emit(kind, user_id, **data)` AFTER your transaction commits; `@subscribe(kind)` on `async def f(user_id, **data)`.
* Commands go to `DESC_LIST` + the "Academy 🎓" entry of `CATEGORIES` in `utils/helpcatalog.py` (+ `ALIASES` if any). NOT in `MENU` / `GROUP_MENU_EXTRA`. Hidden commands go to `STAFF`.
* Callback prefixes in use: `ac:` (player screens), `acs:` (staff).  Keep callback data < 64 bytes and carry the owner id.

---

## Part 1 — Foundation (calendar, enrolment, dashboard)  ✅ done

### Files added
* `game/academy_rules.py` — all constants + env readers
* `services/academy_calendar.py` — pure calendar + season table upkeep
* `services/academy_students.py` — enrolment, lazy Energy, stats, diamonds
* `services/academy_events.py` — event bus
* `services/academy_ticks.py` — tick registry, `academy_loop`, rollover tick
* `services/academy_views.py` — dashboard / schedule text (pure)
* `utils/content/academy_texts.py` — texts (phase blurbs, day hints, notes)
* `handlers/academy_core.py` — /enroll /academy /schedule (+ buttons)
* `handlers/academy_staff.py` — hidden /academyadvance /academyreset
* `handlers/academy_routers.py` — router registry
* `tests/test_academy_foundation.py` — 17 tests
* `docs/ACADEMY_PROGRESS.md` — this file

### Files changed (minimal)
* `database/models.py` — appended `AcademySeason`, `AcademyStudent`
* `bot.py` — `academy_routers` import + `*academy_routers.routers()` before `ai_chat.router`; `academy_loop` wrapper + task in the task list
* `utils/helpcatalog.py` — STAFF += academyadvance, academyreset; DESC_LIST += enroll/academy/schedule; ALIASES += academy→school, schedule→timetable; CATEGORIES += "Academy 🎓"

### Tables
* `academy_seasons(id, number UNIQUE, start_at, end_at, theme, status 'active'|'ended')`
* `academy_students(id, season_id→seasons, user_id→users, stream, club, knowledge, charisma, fitness, creativity, energy, energy_at, late_join, enrolled_at)`, UNIQUE(season_id, user_id)
* Also one `app_state` row (existing table, no schema change): key `academy:marker` = `"<last season announced started>:<last announced ended>"`.

### Time model
Season start `S`. Day n = `[S+(n-1)d, S+n d)`, so Day 7 ends at `S+7d`. Break = `[S+7d, S+7d+break)`. Next season starts at `S + 7d + break` exactly (calendar grid, not "when the tick noticed").
Start inclusive / end exclusive. Season 1 starts at "now" rounded down to the hour on first use. Times are only DISPLAYED in ACADEMY_TZ (default Asia/Kolkata; falls back to a fixed +05:30 if tzdata is missing). If the bot is offline for several cycles, ONE new row is created (number = last+1) on the grid; nothing is backfilled.
Phases: 1 admission, 2 classes, 3 campus, 4 midterms, 5 fest, 6 finals, 7 results; then `break`; `upcoming` = before the start.
Enrolment: open Days 1–4 (Day 1 on time, Days 2–4 `late_join=True`), closed from the end of Day 4. Late joiners get a "lighter workload" note (Part 1 only stores the flag: later parts should apply the lighter workload).

### Public API
**game/academy_rules.py** — `enabled()`, `break_hours()`, `break_delta()`, `tz()`; constants `SEASON_DAYS, LATE_JOIN_LAST_DAY, TICK_SECONDS, PHASES, PHASE_BY_DAY, BREAK_KEY, UPCOMING_KEY, THEMES, STAT_KEYS, STAT_LABELS, START_STATS, STAT_MIN/MAX (0/100), STREAMS (Stream: key,name,emoji,blurb,bonus), CLUBS (Club: key,name,emoji,blurb,focus), ENERGY_MAX/START (100), ENERGY_REGEN_PER_HOUR (10)`; `starting_stats(stream) -> dict`.
Env: `ACADEMY_ENABLED` (kill switch, default on), `ACADEMY_TZ`, `ACADEMY_BREAK_HOURS`.

**services/academy_calendar.py**
* pure: `phase_at(season_start, now) -> PhaseInfo(key, day, name, emoji, starts_at, ends_at)`, `time_left(season_start, now) -> timedelta`, `next_phase(season_start, now) -> PhaseInfo` (after Day 7 = break; in the break = Day 1 of the NEXT season), `week(season_start) -> list[PhaseInfo]`, `enroll_window(season_start, now) -> EnrollWindow(open, late, closes_at)`, `season_end(start)`, `cycle()`, `local(dt)`, `fmt_when(dt)`
* db (caller owns the transaction): `await ensure_season(session, now=None) -> AcademySeason` (creates / rolls over; in the break it returns the OLD season with status 'ended' — use `phase_at(season.start_at, now).key == "break"`), `latest_season(session)`, `advance(session, now)`, `reset_all(session)`, `current_student_count(session, season_id)`

**services/academy_students.py** (all take the session first; `now` optional)
* `await get_student(s, user_id, season_id, lock=False) -> AcademyStudent | None`
* `await enroll(s, user_id, season, stream, club, now=None) -> (student, created)` — idempotent; GameError for bad stream/club or a closed window
* `await spend_energy(s, user_id, season_id, amount, now=None) -> int left` — GameError if short (spends nothing)
* `await restore_energy(s, user_id, season_id, amount, now=None) -> int` (extra helper, capped at 100)
* `await add_stats(s, user_id, season_id, {"knowledge": +n, ...}) -> student` — clamped 0..100; ValueError on unknown key
* `await pay_diamonds(s, user_id, amount, ref, *, kind="academy", meta=None) -> bool` — ledger via `economy.apply`, ref auto-prefixed `academy:`; False = this ref was already paid (nothing paid)
* pure: `regen_energy(stored, stored_at, now)`, `current_energy(student, now)`, `energy_bar(value)`, `seconds_to_full(student, now)`, `stats_of(student)`
Energy is lazy: the row keeps `energy` as of `energy_at`; never tick it.

**services/academy_events.py** — `emit(kind, user_id=None, **data) -> int`, `subscribe(kind)`, `subscribers(kind)`; constants `ENROLLED, SEASON_STARTED, SEASON_ENDED, RESET`.
**services/academy_ticks.py** — `tick(fn | every=, name=)`, `run_ticks(bot, now=None, force=False) -> {name: status}`, `academy_loop(bot)`, `TICK_MODULES`, `clear_marker(session)`.
**services/academy_views.py** — `dashboard(season, student|None, now)`, `schedule(season, now)`, `phase_line`, `hints_for`, `season_title`.

### Commands
* `/enroll` — stream buttons → club buttons → enrolled (late joiners see the lighter-workload note); idempotent
* `/academy` (alias `/school`) — dashboard: stream, club, stats, Energy bar, phase + countdown, hints for today; buttons Schedule / Refresh / Enroll now
* `/schedule` (alias `/timetable`) — today's phase, countdown, full week, break, late-enrolment close, next season start
* STAFF (hidden): `/academyadvance` (jump to the next phase by moving the season start back; from the break it starts the next season now; then runs every tick once with force) and `/academyreset` (confirm button; emits `reset`, deletes all seasons and students, clears the marker, starts a fresh Season 1)
* None of these are in MENU / GROUP_MENU_EXTRA (the "/" menu is built only from those); all are in /help except STAFF.

### Tick handlers
* `academy.rollover` (every loop, 60 s): `ensure_season`, then emits `season_started` / `season_ended` once per season (marker in app_state), after the commit. Works even if a handler did the DB rollover first, and announces an end that was missed while the bot was off.

### Events emitted
| kind | user_id | data |
|---|---|---|
| `enrolled` | player | season_id, stream, club, late_join |
| `season_started` | None | season_id, number |
| `season_ended` | None | season_id, number (fires at the end of Day 7, i.e. when the break begins) |
| `reset` | staff id | — (later parts MUST subscribe and delete their own academy_* rows; subscribers run before Part 1's rows are deleted) |

### Known issues / notes for later parts
* `DAY_HINTS` in `utils/content/academy_texts.py` are generic (they only mention /enroll and /schedule). Later parts should edit them to point at their new commands.
* Students are per season: a player re-enrols every season (stats do not carry over). Part 1 does not announce season start to players (no DMs); a later part can subscribe to `season_started`.
* Part 1 never pays diamonds; `pay_diamonds` is ready for graduation rewards (use a unique ref such as `f"grad:s{season_id}:u{user_id}"`).
* Kill switch `ACADEMY_ENABLED=off`: player commands answer "closed", the loop stops running ticks; staff commands still work. Data is kept.
* Tests that build the real Dispatcher must detach module-level routers afterwards (see `_detach_routers` in the test file): aiogram allows one parent per router.
* Pre-existing, unrelated: `tests/test_arcade_flow.py::test_every_new_command_answers_and_buttons_work` is order/luck dependent (it failed once in the baseline run and when run alone, passes in other runs). Not touched.
* Not done / left for later parts: club battle, rivals, crushes, exams (midterms/finals), Fest Day, results and graduation, diamonds rewards, season-start announcements.

---

## Part 2 — Daily life (actions, Energy, rewards, GIFs)  ✅ done

> **Verification status:** the pure half (`tests/test_academy_actions.py`, 23 tests) was executed and passes (also mutation-checked).
> `tests/test_academy_life.py` (DB + dispatcher, ~25 tests) and the existing suite could NOT be run in the build sandbox (no network, so no aiogram / SQLAlchemy / pytest). Run `pytest` once before deploying and fix anything it reports; see "Known issues".

### 💎 Maximum diamonds per player per day: **100** (`AR.DAILY_DIAMOND_CAP`)
Only `/parttime` pays. 3 shifts a day, 20–40 💎 each (Commerce stream +20%), never past 100 per season day (a shift at the cap pays the remainder, then the job answers "daily maximum reached"). The part-time job is closed on Day 6, so the most a player can earn in a whole season is **6 × 100 = 600 💎**. Nothing else in Part 2 pays diamonds. For comparison `/work` pays 80–250 every 30 min, so this is deliberately small.

### Files added
* `services/academy_actions.py` — PURE rules: day gates, daily limits, diamond cap, study streak, effect rolls (`plan`), GIF choice, `ActionResult`, `render`, `ActionBlocked`
* `services/academy_life.py` — DB half: `perform`, `announce`, daily rows, club points, season totals, `reset` subscriber
* `handlers/academy_life.py` — `/study /attend /hangout /rest /club /parttime`
* `tests/test_academy_actions.py` (pure, 23 tests), `tests/test_academy_life.py` (DB + dispatcher)

### Files changed (minimal, additive)
* `game/academy_rules.py` — appended the Part 2 section (nothing existing touched)
* `database/models.py` — appended `AcademyDaily`, `AcademyClubPoints`
* `utils/content/academy_texts.py` — appended flavour lines / messages; **edited `DAY_HINTS`** to mention the new commands (as Part 1 asked)
* `services/academy_events.py` — added event-kind constants + doc lines
* `handlers/academy_routers.py` — `"handlers.academy_life"` added to `MODULES`
* `utils/helpcatalog.py` — `from game import academy_rules as AR`; DESC_LIST += 6 commands; "Academy 🎓" category += 6 commands. NOT in MENU / GROUP_MENU_EXTRA, no aliases.
* No change to `bot.py` (routers come through `academy_routers`), no tick added.

### Tables (new)
* `academy_daily(id, student_id→academy_students, day 1..7, study_count, attended 0/1, hangouts, club_count, parttime_count, energy_spent, rest_count, diamonds)` UNIQUE(student_id, day). `rest_count` and `diamonds` (earned that day, drives the cap) are extra to the brief.
* `academy_club_points(season_id, club, points)` PRIMARY KEY(season_id, club); all students of a club add to one row. Per-player contribution = `academy_daily.club_count`.
* Created by `create_all` (no migration).

### Rules (all in `game/academy_rules.py`, `AR.ACTIONS`)
| action | Energy | cooldown | per day | open on |
|---|---|---|---|---|
| study | 15 | 10 min | 6 | Days 1–7 |
| attend | 10 | 30 min | 1 | class days **2, 3, 4** (`AR.CLASS_DAYS`) |
| hangout | 10 | 15 min | 4 | Days 1–7 |
| rest | 0 (gives +20) | 90 min | 3 | Days 1–7 |
| club | 15 | 20 min | 4 | Days 1–7 |
| parttime | 20 | 30 min | 3 | Days 1–5 and 7 (closed on Finals, Day 6) |
Everything is closed in the break / before the season starts, and for non-enrolled players. Cooldown key: `academy:<action>` (`AR.cooldown_key`).
Effects: study Knowledge +1..3 (+ streak bonus); attend Knowledge +1; hangout Charisma +1..2 (30%: Creativity +1); club +3..5 club points and +1..2 Fitness (Sports club) or Creativity (Music / Drama / Coding); rest +20 Energy; parttime 20–40 💎.
Study streak = consecutive season days with ≥1 study, derived from `academy_daily` (no extra table). Milestones 3 / 5 / 7 days give +1 / +2 / +3 Knowledge **once, on the first study of that day**.
Stream perks (`AR.STREAM_PERKS`, note texts in `STREAM_NOTES`): Science +1 Knowledge on study/attend; Arts +1 Creativity on hangout/attend; Commerce +1 Charisma on hangout and +20% parttime pay; Sports +1 Fitness on club and +5 Energy on rest; Music +1 Creativity on club and +1 Charisma on hangout.
GIFs (all optional, one per message): study streak milestone → `highfive`; first attendance of the season → `wave`; after an action leaves Energy ≤ 20 (`AR.LOW_ENERGY_GIF`) → `facepalm`; hangout → `laugh` with 50% chance (`AR.HANGOUT_GIF_CHANCE`, set 1.0 for always). Priority streak > first attend > low Energy > hangout. A refused action (no Energy etc.) is plain text, never a GIF.

### Public API
**services/academy_actions.py (pure)** — `ActionBlocked(text, enroll=False)` (a GameError), `DailyCounts`, `day_block(action, day)`, `limit_block(action, counts, resets)`, `diamonds_left(counts)`, `cap_pay(roll, earned_today)`, `study_streak(days, today)`, `best_streak(days)`, `streak_bonus(n)`, `plan(action, *, stream, club, rng, streak, first_study_today) -> Plan`, `choose_gif(...)`, `ActionResult`, `render(result)`
**services/academy_life.py** — `await perform(session, user_id, key, now=None, rng=None) -> ActionResult` (one transaction; raises `ActionBlocked`, `OnCooldown` or `GameError`, nothing changed in that case), `await announce(user_id, result)` (call AFTER commit), `await get_daily(s, student_id, day)`, `daily_rows(s, student_id)`, `season_totals(s, student_id) -> SeasonTotals(study, attended_days, hangouts, club, parttime, rest, energy_spent, diamonds, best_streak)`, `add_club_points(s, season_id, club, pts) -> total`, `club_standings(s, season_id) -> [(club, points)]` (all 4 clubs, best first)
**For later parts:** exams/awards read `season_totals`; the club battle starts from `club_standings` and `academy_daily.club_count`; attendance = `attended_days` (3 class days max).

### Commands
`/study /attend /hangout /rest /club /parttime` — in /help under "Academy 🎓", not in the "/" menu. Reply buttons reuse Part 1's owner-checked callbacks: `ac:db:<uid>` (dashboard) and `ac:en:<uid>` (Enroll; shown only while enrolment is still open, otherwise the message just says it is closed).

### Tick handlers
None added.

### Events emitted (after the commit; every payload has `action`, `season_id`, `day`, `student_id`, `energy`, `gains`)
| kind | extra data |
|---|---|
| `studied` | streak, milestone |
| `attended` | first |
| `hangout` | — |
| `rested` | energy_restored |
| `club_practice` | club, points, total |
| `parttime` | paid, capped, diamonds_today |
| `study_streak` | season_id, day, streak, bonus (extra; only on a 3/5/7 milestone) |
Subscribes to `reset`: deletes all `academy_daily`, `academy_club_points` and every `academy:*` cooldown.

### Idempotency / safety
* Everything of one action (cooldown claim, Energy, stats, counters, club points, ledger) is ONE transaction; any refusal rolls all of it back.
* Cooldown claim is the atomic `services.cooldowns.claim`: a double tap or retry loses there.
* Payment ref = `academy:pt:<uid>:<enrolled_at µs>:d<day>:n<shift number>`; replay → "already paid", whole action rolled back. (Enrolment time is in the ref so refs cannot collide after `/academyreset`.)
* Milestone bonus only when `study_count` goes 0→1 that day; the diamond cap is read from the day's row.

### Known issues / notes
* **Tests not run in the build sandbox** (see top). `test_academy_life.py` mirrors Part 1's tested harness; if something fails it is more likely a test detail than the logic, but check.
* Cooldowns use the real clock (`services.cooldowns`), not the simulated `now`; tests clear them between actions.
* Late joiners: Part 2 does NOT apply the "lighter workload" (it only makes sense for exams); `student.late_join` is still there for Part 4+.
* The dashboard (`/academy`) does not yet show today's counters; use `academy_life.get_daily`.
* Old seasons' `academy_daily` rows are never deleted (≤ 7 per student per season, tiny).
* Hangout laugh GIF is 50%, not 100%, to avoid GIF spam in groups: one constant to change.
* Class days are Days 2–4 (Fest, Finals and Results days have no classes): one constant (`AR.CLASS_DAYS`).
* Not done / left for later parts: club battle (use `club_standings`), rivals, crushes, exams, Fest Day, results and graduation, season-start announcements.

---

## Part 3 — Exams (strategy rounds), grades, ranks  ✅ done

> **Verification:** full suite run in the build sandbox: **263 passed** (191 before + 72 new: `tests/test_academy_exam_logic.py` pure, `tests/test_academy_exams.py` DB + real Dispatcher). Part 2's tests were also executed this time and pass.

### Files added
* `utils/content/academy_exam_rounds.py` — 44 original scenario cards (24 general, 4 per stream), `CARDS`, `BY_KEY`, `GENERAL`, `stream_cards(stream)`, `Card`, `Button`. No real-subject questions anywhere.
* `utils/content/academy_exam_texts.py` — all exam texts.
* `services/academy_exam_logic.py` — PURE: `play_round`, chance, scoring, grades, GPA, ranking, card drawing, deadlines.
* `services/academy_exams.py` — DB half: attempts, rounds, Focus, finishing, rewards, ranks, tick, reset.
* `services/academy_exam_views.py` — pure text rendering.
* `handlers/academy_exams.py` — `/exam /grades /classrank` + `acx:` buttons.
* `tests/test_academy_exam_logic.py`, `tests/test_academy_exams.py`.

### Files changed (minimal, additive)
* `game/academy_rules.py` — appended the Part 3 section (all numbers).
* `database/models.py` — appended `AcademyExam`, `AcademyAttempt`, `AcademyRound`, `AcademyChatMember`.
* `services/academy_events.py` — constants `EXAM_STARTED`, `EXAM_ROUND`, `EXAM_FINISHED`.
* `services/academy_ticks.py` — `TICK_MODULES = ("services.academy_exams",)`.
* `handlers/academy_routers.py` — `"handlers.academy_exams"` added to `MODULES`.
* `utils/helpcatalog.py` — DESC_LIST += exam/grades/classrank; added to the "Academy 🎓" category. NOT in MENU / GROUP_MENU_EXTRA (a test checks it).
* `utils/content/academy_texts.py` — `DAY_HINTS` for Days 4, 6, 7 now mention /exam, /grades, /classrank.
* No change to `bot.py`, the AI feature, or any earlier table.

### Tables (new)
* `academy_exams(id, season_id, kind, opens_at, closes_at)` UNIQUE(season_id, kind). Created lazily on the first /exam of a kind.
* `academy_attempts(id, student_id, exam_kind, started_at, finished_at, score, grade, state, cards, round_started_at, focus_used, reward)` UNIQUE(student_id, exam_kind) = ONE attempt per exam. `state`: `active | done | expired`. `cards` = drawn card keys (comma list). `round_started_at` is set while a question is on screen (the 25 s are checked against it) and NULL while the player looks at a result. Extra columns beyond the brief: `cards, round_started_at, focus_used, reward`.
* `academy_rounds(id, attempt_id, n, card_key, choice, roll, success, points, focused)` UNIQUE(attempt_id, n). `choice` = button index as text or `"timeout"`.
* `academy_chat_members(chat_id, user_id)` — written once (insert-ignore) when someone uses /exam, /grades or /classrank in a group; powers the group scope (together with existing `group_stats`). Never per message.

### Rules (all in `game/academy_rules.py`, Part 3 section)
* Exams: midterm Day 4 (5 rounds), finals Day 6 (8 rounds), whole day each. Midterm cards: 2 own-stream + 3 general, difficulty 1-2. Finals: 3 own-stream + 5 general, difficulty 2-3, avoiding cards already seen in the midterm (stream count is soft: fewer fresh stream cards are topped up with general ones).
* 25 s per round (`ROUND_SECONDS`), checked with timestamps, plus `ROUND_GRACE_SECONDS = 2` of network slack (the player is told 25). A late press, or coming back after the time, records the round as a timeout (0 points).
* Chance = `0.50 + (skill - target[difficulty]) / 100 + risk` clamped 0.02..0.98. skill = button stat + stream (+4 on an own-stream card, +3 if the stat is one the stream is good at) + study streak (+1.2/day, max 6) + attendance (up to +8; a late joiner is measured only against the class days they could attend = the "lighter workload"). Risk: safe +0.12, balanced 0, risky -0.25. Targets 30/45/62.
* Luck: ONE dice roll 1..100 shifts the chance by at most ±0.15 (`LUCK_CAP`); success when `chance + luck >= 0.5`. So outside a ±0.15 band around the line the outcome is fixed by preparation (tested).
* Round points (success, fail): safe (10, 3), balanced (14, 0), risky (22, -8). Score = sum / (rounds × 14) × 100, clamped 0..100.
* Grades: A+ ≥90, A ≥80, B+ ≥70, B ≥60, C ≥50, D ≥40, F below. GPA = score/10 (two decimals). Season score = midterm 50% + finals 50%.
* Balance check by simulation (finals, best play): idle player ≈22, casual ≈40, active ≈78, daily grinder ≈97; random clicking is far worse. Retune only the numbers in the rules file.
* Probation: season GPA < 4.0 (= grade F). Recomputed live (a good finals can lift it), always ends with the season. **Perks removed by Part 3: Focus.** It is exposed as `probation_status(...)` so later parts can remove more; Part 2's stream perks / part-time pay are NOT changed (earlier work is not edited).
* Focus: re-rolls ONE failed round you are still looking at; up to 3 per exam (`FOCUS_MAX_USES`), 15 Energy each (`FOCUS_ENERGY`, GameError if too tired, nothing changes), once per round, off on probation. The new roll replaces the old one.
* Rewards per exam grade (paid once, ref `academy:exam:<user>:<enrolled µs>:<kind>`): A+ 120, A 90, B+ 70, B 50, C 30, D 15, F 0. Max 240/season next to Part 2's 600.
* A missed exam counts 0 once its window closed; while open the season result is `provisional`. A student with no finished exam at all is not ranked. Flagged/banned players are left out of ranks.
* GIFs: `dance` on A+/A, `facepalm` on F (final screen only; falls back to nothing if GIFs are off).

### Public API
**services/academy_exam_logic.py (pure)**
* **`play_round(student, card, choice, rng=None) -> RoundResult`** — the reusable core for Parts 4 and 8A. `student`: an `ExamStudent` or an `AcademyStudent` row (needs `stream, knowledge, charisma, fitness, creativity`; `streak` and `attendance` default to 0 through getattr). `card`: a `Card` from `utils/content/academy_exam_rounds`. `choice`: button index or `Button`. `rng`: anything with `randint(a, b)` (use `random.Random(seed)` in tests); exactly one `randint(1, 100)` call, no I/O. Returns `RoundResult(card_key, choice, stat, risk, chance, roll, luck, success, points, line, skill, parts)`. For a different tuning call `chance_of(student, card, button) -> (chance, skill, parts)` and `luck_of(roll)`.
* `ExamStudent(stream, knowledge, charisma, fitness, creativity, streak=0, attendance=0.0)`, `exam_score(points, rounds)`, `grade_of(score)`, `gpa_of(score)`, `combine(midterm, finals, midterm_closed, finals_closed) -> (score|None, provisional)`, `on_probation(score)`, `reward_for(grade)`, `rank_rows(rows)` (ties share a rank: 1, 2, 2, 4), `Ranked`, `draw_cards(stream, kind, rng, exclude=())`, `exam_window(season_start, kind)`, `exam_open_now(season_start, now)`, `next_exam(...)`, `round_expired(started_at, now)`, `seconds_left(...)`, `attendance_ratio`, `possible_class_days`.

**services/academy_exams.py (DB; the caller owns the transaction; `now`/`rng` optional)**
* **`await season_results(session, season_id, now=None, user_ids=None) -> list[Ranked]`** — for the graduation ceremony. Each `Ranked`: `rank, user_id, student_id, name, score, gpa, grade, midterm, finals, provisional, probation`. Best first, ties share a rank. Only students with at least one finished exam.
* `open_exam(s, user_id, now, rng) -> Screen` (start/resume; raises `ExamBlocked` with a friendly text), `answer(s, user_id, attempt_id, n, idx, now, rng)`, `focus(...)`, `next_round(...)` (after the last round it finishes the exam), `announce(user_id, screen)` (events AFTER the commit), `grades_report(s, user_id, now)`, `class_rank(s, user_id, chat_id|None, now)`, `probation_status(s, user_id, season_id, now)`, `prep_of(s, student, season, kind)`, `ensure_exam`, `note_chat`, `chat_user_ids`.
* Tick `academy.exam_close` (every 120 s): finishes every attempt whose window closed (state `expired`, scored on the rounds played, reward paid once) and sends the player a short note through `safe_send`.
* Subscribes to `reset`: deletes all four exam tables.

### Commands
`/exam` (start/resume the open exam), `/grades` (my exams, GPA, grade, rank; buttons Class rank / Refresh), `/classrank` (top 10 + my rank; in a group, buttons Global / This group). All in /help under "Academy 🎓", not in the "/" menu. No command collisions (checked).
Callbacks (`acx:` prefix, all < 64 bytes, all owner-checked): `acx:c:<uid>:<attempt>:<n>:<idx>` answer, `acx:f:<uid>:<attempt>:<n>` Focus, `acx:n:<uid>:<attempt>:<n>` next round / finish, `acx:g:<uid>` grades, `acx:r:<uid>:<g|w>` rank scope. A stale button (round already played) is refused and changes nothing.

### Events emitted (after the commit; the exam kind is in `exam`, because `kind` is emit's own argument)
| kind | user_id | data |
|---|---|---|
| `exam_started` | player | season_id, exam, student_id |
| `exam_round` | player | season_id, exam, n, success, points, student_id (+ `focus=True` on a re-roll) |
| `exam_finished` | player | season_id, exam, score, grade, gpa, reward, expired, student_id |

### Known issues / notes for later parts
* The exam is its own flow: after the last round the player taps "Finish exam" (so Focus can still be used on the last round). If they never do, the tick finishes it when the window closes. Until an attempt is finished it does not count in ranks.
* Between rounds (on a result screen) no clock runs; only a shown question has the 25 s.
* `/classrank` group scope = players who used an exam command in that group (or have `group_stats` there). A player who never did will not appear in the group board.
* Probation only removes Focus so far (see Rules). If more perks should go, a later part can call `probation_status`.
* DM on tick-finish goes through `safe_send` (blocked users are skipped).
* Season results are per season; there is no all-time board. Graduation (Day 7) can call `season_results` directly; ranks are final only once `provisional` is False for everyone (both windows closed).
* Not done / left for later parts: club battle, rivals, crushes, Fest Day, graduation ceremony, season-start announcements, Parts 4 and 8A (reuse `play_round`).

---

## Part 4 — Social (rivals, crushes, club battles, study buddies)  ✅ done (DB tests NOT run in the build sandbox, see below)

> **Verification status:** the sandbox had no network, so aiogram / SQLAlchemy / pytest could not be installed.
> * RUN and passing: `tests/test_academy_social_logic.py` (26 test functions = 33 cases, run with a tiny pytest stand-in), plus Part 2's pure tests (23). All new files compile; a static check found no undefined names or missing module attributes.
> * WRITTEN but NOT RUN: `tests/test_academy_social.py` (24 DB + dispatcher tests, same harness as Part 3) and the existing suite. **Run `pytest` once before deploying** and fix what it reports; a failing test is more likely a test detail than the logic, but check both.

### Files added
* `services/academy_social_logic.py` — PURE: pairing, bot rival, duel cards / outcome / prizes, battle card + focus bonus, club scoring, crush / date / buddy rules, day windows
* `services/academy_social.py` — DB: rivals, duels, club battle, badges, `Out` + `deliver`, the 3 ticks, reset
* `services/academy_love.py` — DB: crushes, couples, `/campusdate`, buddies, buddy bonus subscriber
* `services/academy_social_views.py` — pure text rendering
* `handlers/academy_social.py` — `/rival /rivalduel /crush /crushoff /campuscouple /campusdate /clubs /buddy` + buttons
* `utils/content/academy_social_texts.py`
* `tests/test_academy_social_logic.py` (pure), `tests/test_academy_social.py` (DB)

### Files changed (minimal, additive)
* `game/academy_rules.py` — appended the Part 4 section (every number)
* `database/models.py` — appended 12 tables (below)
* `handlers/academy_routers.py` — `"handlers.academy_social"` in `MODULES`
* `services/academy_ticks.py` — `TICK_MODULES` += `"services.academy_social"`
* `utils/helpcatalog.py` — DESC_LIST += 8 commands; added to the "Academy 🎓" category. NOT in MENU / GROUP_MENU_EXTRA (checked by a test). No collisions (checked against every handler and alias).
* `utils/content/academy_texts.py` — `DAY_HINTS[3]` now mentions /clubs play, /rival, /crush
* `services/academy_events.py` — docstring only (new event kinds)
* No change to `bot.py`, the AI feature, or any earlier table.

### Tables (new, created by create_all)
`academy_rivals` (one row per PLAYER and season, a pair = 2 mirrored rows; rival_id NULL = bot; bot_name, bot_seed, notified) · `academy_rival_duels` (UNIQUE season+user_a; cards, state open|done, result a|b|tie|none, walkover, chat_id) · `academy_rival_rounds` (user_id 0 = bot) · `academy_badges` (PK season+user+kind: rival|club; points) · `academy_crushes` (UNIQUE season+from_user; PRIVATE) · `academy_couples` · `academy_couple_dates` (PK couple+day = once-a-day lock) · `academy_battles` (UNIQUE season; card_key, opens/closes, state open|closed, winner) · `academy_battle_plays` (PK battle+user = once per battle) · `academy_buddies` (pending|active) · `academy_buddy_bonus` (PK season+user+day) · `academy_social_runs` (PK season+kind; marks "rivals paired").

### Rules (all in `game/academy_rules.py`, Part 4 section)
* **Rivals:** one pairing per season, by the tick, on Day 3 (or the first tick after it, never later than Day 5). Distance = `|GPA diff| * 10 + |Knowledge diff|`; GPA = the student's GPA from LAST season (Part 3 `season_results`), else 5.0 (nothing carries over otherwise, and no exam exists yet on Day 3). Sorted by that score and paired with the neighbour; the odd one out is the one whose removal keeps the total distance smallest and gets a **bot rival** (stats = theirs ±3, plays the move with the best expected points). A student who enrols after the pairing gets a bot rival on demand. Banned/flagged players are not paired. Each paired student gets ONE DM (max 40 per tick run).
* **Duel:** `/rivalduel`, Days 3–6. Both sides play the SAME 3 general cards (difficulty 1–2) through Part 3's `play_round`; no timer. 10 Energy, charged when you answer card 1. Higher total wins **60 💎 + 1 Rival badge point** (bot rival pays the same); draw = 20 💎 each, no badge; rival never plays by the end of Day 6 = the finisher gets a walkover (50% = 30 💎, no badge); nobody finished = no result. A duel is settled exactly once (the `open→done` update is the claim) and prizes use ledger refs.
* **Crush:** open Days 3–7. Both enrolled, not self, not a bot, not already a couple, one active crush each, `/crushoff` deletes it. Mutual only if both point at each other in the same season: then both rows are consumed, a couple is created, both get a DM with a `blush` GIF and ONE group message (the group of the second crush, else of the first) is sent. Players are row-locked (`users.lock`) so two simultaneous crushes cannot miss each other.
* **Campus date:** `/campusdate`, Days 3–7, once per couple and day (PK lock), costs the player **20 Energy + 15 💎** (ledger, unique ref), both partners +Charisma 1–2 and +1 on another stat, couple +10 points. Any refusal rolls EVERYTHING back (the day is not used up).
* **Club battle:** a tick opens it on Day 3 (UNIQUE per season), open until the END of Day 5. One general card (difficulty 2) for everyone, same per season. Each member plays ONE round (`/clubs play` or the button), 10 Energy; the club's focus stat gets +5 (`battle_student`). Club score = Part 2 club points + sum of the members' round points. A tick closes it exactly once: winner = best total above 0 (ties: more players, then `AR.CLUBS` order); every CONTRIBUTING member (played a round or practised with /club) gets a `club` badge point + **80 💎** (ref per player) + a DM.
* **Buddies:** `/buddy @user` (or reply) → the target gets Accept/Decline buttons (in a group in the same chat, otherwise by DM); `/buddy` status; `/buddy off` ends it or cancels your request. One open request and one active buddy per player; if they asked you first, your request accepts it. Bonus: +1 Knowledge on your FIRST `/study` each day (via the `studied` event, so Part 2 is untouched; PK `academy_buddy_bonus` = once a day). `/buddy` shows if today's bonus is claimed.
* Diamond budget added by Part 4: at most 60 (duel) + 80 (club) per season; dates COST 15 each.

### Public API
**services/academy_social_logic.py (pure)** — `Candidate`, `pair_score`, `pair_students(cands) -> (pairs, odd_id|None)`, `bot_name`, `bot_student(stats, rng)`, `bot_choice(student, card)`, `duel_cards(rng)`, `battle_card(seed)`, `boosted`, `battle_student(student, club)`, `side_total`, `duel_outcome`, `duel_prize(result, walkover) -> Prize`, `ClubRow`, `club_rows(practice, battle, players)`, `winning_club`, `crush_block(...)`, `is_mutual`, `couple_key`, `date_block`, `date_gains`, `buddy_block`, `rival_pairing_due/duel_open/battle_due(day)`, `battle_window(start)`, `duel_deadline(start)`.
**services/academy_social.py** — `SocialBlocked(text, enroll)` (a GameError), `ctx`, `gate`, `names`, `add_badge`, `badge_points(s, uid, kind, season_id=None)`, `Out`, `dm`, `deliver(bot, out, skip_chat=None)`, `pair_rivals`, `get_rival`, `ensure_rival`, `open_duel(s, uid, now, rng, chat_id) -> DuelScreen`, `duel_answer(s, uid, duel_id, n, idx, now, rng)`, `duel_status`, `notify_rivals`, `open_battle`, `get_battle`, `standings(s, season_id) -> [ClubRow]`, `clubs_view`, `battle_card_for`, `play_battle`, `finalize_battle`; ticks `rival_pairing_tick`, `club_battle_open_tick`, `social_close_tick`.
**services/academy_love.py** — `send_crush(s, uid, target, chat_id, target_is_bot, now) -> CrushResult`, `crush_off`, `my_crush_name`, `couple_of`, `couple_status(s, uid, now, private)`, `campus_date(s, uid, now, rng) -> DateResult`, `charge_diamonds`, `request_buddy`, `answer_buddy`, `end_buddy`, `buddy_status`, `active_buddy`.
**Reusable for Part 5+:** `soc.badge_points` / `add_badge` (badges), `love.couple_of`, `soc.standings` (club table incl. battle), `soc.get_rival`.

### Commands (in /help, not in the "/" menu)
`/rival`, `/rivalduel`, `/crush`, `/crushoff`, `/campuscouple`, `/campusdate`, `/clubs` (`/clubs play`), `/buddy`. Callback prefixes (all < 64 bytes, all carry the owner id and are checked): `acr:` rival/duel, `acb:` club battle, `acp:` couple date, `acu:` buddy answer (uid = the player who must answer).

### Tick handlers (registry in `services/academy_ticks.py`; still ONE loop)
`academy.rival_pairing` (every 300 s: pair once on Days 3–5, then DM the next batch) · `academy.club_battle_open` (every 300 s) · `academy.social_close` (every 120 s: close battles whose time is over and pay the winners once; settle duels past the end of Day 6).

### Events emitted (after the commit)
| kind | user_id | data |
|---|---|---|
| `rival_win` | winner | season_id, duel_id, opponent_id (None = bot), bot, walkover, total, opponent_total (not emitted when the bot wins or on a draw) |
| `crush_mutual` | the player who completed it (ONCE per couple) | season_id, partner_id, couple_id |
| `date` | initiator | season_id, day, partner_id, couple_id, points, total |
| `club_win` | each rewarded member | season_id, club, battle_id, diamonds |
Nothing is emitted for a crush that is not mutual. Subscribes to `studied` (buddy bonus) and `reset` (deletes every Part 4 row).

### Privacy of crushes (the design rule)
A crush row is only read by the sender (`/campuscouple` in a private chat, `/crushoff`) and by the mutual check. Refusals never say anything about how the target feels. In a group the command message is deleted when the bot is allowed to, the answer goes by DM only (if the DM is impossible the group sees one neutral line), and the only thing the group ever sees is the announcement of a MUTUAL couple. `/campuscouple` in a group never shows a pending crush.

### Known issues / notes
* **DB tests not run here** (see the top). Also not run: the existing suite.
* If a player types `/crush` as a reply in a group and the bot is not an admin, Telegram keeps that command message visible, so it is public that THEY used /crush on THAT person (the bot cannot prevent that). The help text and the confirmation recommend a private chat.
* Pairing happens once (first tick of Day 3). Students enrolled earlier that day but after the tick, and late joiners, get a bot rival on demand rather than a human one.
* GPA for pairing is last season's (default 5.0 for newcomers); Knowledge is the live value.
* Big clubs have an edge (scores are sums, as specified). Club winners must have contributed (played a round or practised) to be paid.
* One date per couple per day (either partner may start it); the starter pays.
* `/clubs` standings include the battle only once it exists (Day 3+).
* Season-start announcements and graduation ceremony are still not done (Part 1/3 notes); `soc.badge_points` is ready for the ceremony.
* Not done / left for later parts: Fest Day, results and graduation ceremony, season-start announcements.

---

## Part 5 — Adventures (story engine, 5 adventures, Fest Day bonus)  ✅ done (DB + Dispatcher tests NOT run in the build sandbox, see below)

> **Verification status:** the sandbox had no network, so aiogram / SQLAlchemy / pytest could not be installed (same as Parts 2 and 4).
> * RUN and passing (with a tiny pytest stand-in): `tests/test_academy_adventure_data.py` + `tests/test_academy_adventure_logic.py` = **75 cases** (data validation, stat checks, transitions, rewards, expiry, rendering, help catalog). Mutation-checked (roll comparison, expiry boundary, flag visibility, Fest stat bonus, reward caps each make tests fail). Also re-run with the stand-in: `tests/test_help_catalog.py`, Part 2/3/4 pure tests (all pass).
> * WRITTEN but NOT RUN: `tests/test_academy_adventures.py` (32 DB + Dispatcher tests, same harness as Part 3) and the rest of the existing suite. All new files compile; a static check found no undefined names or missing module attributes. **Run `pytest` once before deploying**; a failing test there is more likely a test detail than the logic, but check both.

### Files added
* `utils/content/academy_adventures.py` — PLAIN DATA: dataclasses `Check, Choice, Trophy, Ending, Node, Adventure` + the 5 adventures (`ADVENTURES`, `ORDER`)
* `utils/content/academy_adventure_texts.py` — UI texts
* `services/academy_adventure_logic.py` — PURE: availability, chance / dice, transitions, rewards, expiry, run state, `Screen`/`ListView`, and the data validator (`problems`, `all_problems`, `explore`)
* `services/academy_adventures.py` — DB engine (the file named in the brief): `start`, `choose`, `quit_run`, `current`, `list_view`, `announce`, expiry tick, reset
* `services/academy_adventure_views.py` — pure HTML rendering
* `handlers/academy_adventures.py` — `/adventure` + `acv:` buttons
* `tests/test_academy_adventure_data.py`, `tests/test_academy_adventure_logic.py` (pure), `tests/test_academy_adventures.py` (DB + Dispatcher)

### Files changed (minimal, additive)
* `game/academy_rules.py` — appended the Part 5 section (every number, caps, Fest bonus helper)
* `database/models.py` — appended `AcademyAdventureRun`, `AcademyAdventureTrophy`
* `services/academy_events.py` — constant `ADVENTURE_DONE` + docstring line
* `services/academy_ticks.py` — `TICK_MODULES` += `"services.academy_adventures"`
* `handlers/academy_routers.py` — `"handlers.academy_adventures"` in `MODULES` (before the AI chat router, as always)
* `utils/helpcatalog.py` — DESC_LIST += `adventure`; added to the "Academy 🎓" category. NOT in MENU / GROUP_MENU_EXTRA (tested). No collision (checked against every handler: nothing else uses /adventure).
* `utils/content/academy_texts.py` — `DAY_HINTS` for Days 2 and 5 now mention /adventure
* No change to `bot.py`, the AI feature, or any earlier table.

### Tables (new, created by create_all, no migration)
* `academy_adventure_runs(id, student_id→academy_students, adventure_key, node, state_json, started_at, updated_at, finished, ending, step)`. `step` is an extra column (this is a new table): the optimistic-lock token. `ending` = an ending key, or `expired` / `quit` (no reward). `state_json` = `{"flags": [...], "fest": bool, "day": n, "wins": n, "losses": n, "trail": [node keys]}`.
* `academy_adventure_trophies(season_id, user_id, key, kind title|item, name, adventure_key, earned_at)` PK(season_id, user_id, key) = once per season. Cosmetic only (no effect on any number).

### Engine rules (all numbers in `game/academy_rules.py`, `ADV_*`)
* **Story format:** a node has text + 1-4 choices; a choice is a plain move (`to`) or a stat `check` (stat, difficulty 1-4, success / fail node, `bonus` flags that add / remove skill, `win_sets` flags set on success); `sets` flags are remembered for the run; `needs` / `hides` show or hide buttons by flags. An ending node has an `Ending` (title, mood great|good|ok|bad, diamonds, stat gains, optional Trophy, optional GIF action) and no choices. Stats of a check are the player's live stats.
* **Check:** chance = 0.55 + (stat + flag bonuses + stream bonus − target) / 100, targets 10 / 25 / 40 / 55 for difficulty 1-4, clamped 5%-95%. A `luck` check ignores stats (fixed 70 / 55 / 40 / 25% + flags). ONE dice roll 1-100, success when roll ≤ chance. Buttons show the chance (`💬 45%`), the result line shows the roll.
* **Availability:** from Day 2 (Day 1 is for enrolling). The Haunted Library: Days 2-6 and only at night = local ACADEMY_TZ hour ≥ 20 or < 6 (`AR.adv_night`). Canteen Heist: Days 3-7. Missing Question Paper: Days 2-6. Rooftop Concert and Sports Day Showdown: Day 5 only.
* **Cost / limits:** Energy per adventure (20 / 20 / 15 / 25 / 25, always 10-30), charged at start only. At most **2 finished adventures per season day** (the FINISH time decides the day; expired and quit runs do not count). One live run at a time: starting another is refused, starting the SAME adventure again just resumes it for free (a double tap never charges twice).
* **Expiry:** 30 minutes without a button press → the run ends as `expired` (no reward, no Energy refund). Done lazily on every touch (the result is RETURNED so the close is committed) and by the tick.
* **Fest Day (Day 5):** every adventure played on it gets +50% diamonds (rounded up) and +1 on every stat the ending already gives. Decided when the run STARTS (a run crossing midnight keeps it). Nothing is invented for an ending that gives 0.
* **Caps (checked by tests for every ending):** diamonds ≤ 30 (45 on Fest Day), each stat gain ≤ 3 (4 on Fest Day), total stat gain ≤ 5. So at most 2 × 30 = 60 💎 a day (90 on Fest Day), always below Part 2's 100 💎/day job cap.
* **Stream bonus:** Rooftop Concert +5 skill for the Music stream, Sports Day Showdown +5 for Sports (stat checks only).
* **Diamonds** only through `academy_students.pay_diamonds` with ref `academy:adv:<user>:<enrolled µs>:<run id>`; paid once per run. The ending is also claimed by an atomic `UPDATE ... WHERE finished = 0 AND step = :step`, so a double tap / old button / retry changes nothing ("stale"). One transaction per press: a refusal rolls everything back.
* **Balance (simulation, best play):** expected 💎 per run rises with stats in every adventure (e.g. Haunted Library ≈ 20 at stat 12 → 27 at stat 60); tests assert it is monotonic and that a stream bonus helps only its own adventure.

### The 5 adventures (all 10-11 nodes, 4 endings each, 7-11 real stat checks)
| key | when | Energy | main stats | trophies |
|---|---|---|---|---|
| `haunted_library` 🕯 | Days 2-6, night | 20 | Knowledge, Charisma, Creativity, Fitness | title Ghost Whisperer · item Professor Biscuit's Bell |
| `canteen_heist` 🍩 | Days 3-7 | 20 | Charisma + luck (+ Fitness) | title Midnight Legend · item Mr. Pillai's Secret Recipe |
| `missing_paper` 🔎 | Days 2-6 | 15 | clues (Knowledge, luck, Charisma): flags `glitter`, `tape`, `alibi`, `trail` unlock buttons and add skill | title Master Detective · item Mr. Das's Marigold |
| `rooftop_concert` 🎸 | Day 5 | 25 | Creativity, Charisma (Music +5) | title Rooftop Headliner · item String of Fairy Lights |
| `sports_showdown` 🏅 | Day 5 | 25 | Fitness, Charisma (Sports +5) | title Sports Day Champion · items Silver Medal, House Spirit Ribbon |

### Public API
**services/academy_adventure_logic.py (pure)** — `get(key)`, `is_open(adv, day, local_hour)`, `closed_reason`, `available(day, hour)`, `expired(updated_at, now)`, `seconds_left`, `new_state(day)`, `dump_state/load_state`, `visible(node, flags)`, `chance_of(adv, check, stats, stream, flags) -> CheckResult`, `resolve_check(..., rng)`, `hint`, `check_line`, **`advance(adv, node_key, state, idx, stats, stream, rng) -> Move(node, check, state, choice_text)`** (raises `BadChoice`), `rewards_for(ending, fest) -> Rewards`, `reward_ref(user_id, enrolled_us, run_id)`, `Screen`, `ChoiceView`, `ListView`, `explore(adv)`, `problems(adv)`, `all_problems()`.
**services/academy_adventures.py (DB; caller owns the transaction; `now` / `rng` optional)** — `AdventureBlocked(text, enroll)` (a GameError), `await start(s, uid, key, now, rng) -> Screen`, `await choose(s, uid, run_id, step, idx, now, rng) -> Screen` (kinds `node | ending | expired | stale`), `await quit_run(s, uid, run_id, now)`, `await current(s, uid, now) -> Screen | None`, `await list_view(s, uid, now) -> ListView`, `await finished_today(s, student_id, season_start, day)`, `await announce(uid, screen)` (AFTER the commit), `await expire_stale(s, now) -> int`.
**To add an adventure:** add an `_adv(...)` to `utils/content/academy_adventures.py` and list it in `ADVENTURES`; `tests/test_academy_adventure_data.py` validates it automatically (a new one must satisfy 8-12 nodes, ≥ 3 endings, caps, unique trophy keys; update the `ORDER` / energy assertions there).

### Commands
`/adventure` — the list of adventures open now (buttons), the live run (Continue / Leave), finished today `n/2`, Energy, trophies. In /help under "Academy 🎓", not in the "/" menu. Callbacks (`acv:` prefix, all < 64 bytes, all carry the owner id and are checked): `acv:l:<uid>` list · `acv:s:<uid>:<key>` start / resume · `acv:r:<uid>` continue · `acv:c:<uid>:<run>:<step>:<idx>` choice · `acv:q:<uid>:<run>` leave.

### Tick handlers
`academy.adventure_expire` (every 600 s): closes unfinished runs idle for more than 30 minutes (reads them, compares times in Python, updates by id). Still ONE loop.

### Events emitted (after the commit)
| kind | user_id | data |
|---|---|---|
| `adventure_done` | player | season_id, student_id, run_id, adventure, ending, mood, day, fest, diamonds (paid), base_diamonds, gains, trophy (key or None), trophy_new, wins, losses |
Only for a real ending (not for expired / quit). Subscribes to `reset`: deletes all runs and trophies.

### Known issues / notes
* **DB + Dispatcher tests not run here** (see the top). Also not run: the existing suite as a whole.
* "Night" is by the clock of ACADEMY_TZ, not by season-day boundaries (a season starts at an arbitrary hour), so on any season day there is a night window; the Library therefore opens every evening of Days 2-6.
* The Fest bonus is decided by the day the run STARTED; the daily limit counts the day it FINISHED.
* A button for the Haunted Library that is already running keeps working after the night window closes (resume uses `current`), but a NEW Library run cannot start then.
* Adventure rewards are small and fixed per ending; there is no extra per-season cap (the daily limit of 2 and Energy bound it).
* The dashboard `/academy` does not show adventure progress yet; use `academy_adventures.list_view` / `finished_today`. Trophies are only shown in `/adventure` (a later part such as graduation can read `academy_adventure_trophies`).
* Not done / left for later parts: Fest Day itself beyond the adventure bonus (other Fest content), results and graduation ceremony, season-start announcements.


---

## Part 6 — Reminders, daily recaps, graduation  ✅ done (pure tests run; DB + Dispatcher tests written but NOT run in the build sandbox, see below)

**Build-sandbox limit (same as Parts 2-5):** no SQLAlchemy / aiogram / aiosqlite and no network, so `tests/test_academy_notify.py` (28 DB + Dispatcher tests) could not run here, and neither could the old DB tests. What ran: all pure tests, `tests/test_academy_notify_logic.py` (53 passed), the help-catalog test, `py_compile` of every file, and an AST scan for undefined names in all new DB modules. The existing suite gives the same 102 failures / 193 passes before and after (every failure is "No module named sqlalchemy"). **Please run `pytest` once on a real install.**

### Files added
`game/academy_rules.py` (Part 6 section appended) · `services/academy_remind_logic.py` (pure: calendar, quiet hours, schedule, Limiter, who may be messaged, reminder choice) · `services/academy_remind_views.py` (pure: season-open / enrol / recap texts) · `services/academy_ceremony.py` (**CEREMONY_SECTIONS registry**, titles, ceremony / report-card / Hall-of-Fame text; pure at import) · `services/academy_notify.py` (DB: send-once log, prefs, active groups, `Sender`) · `services/academy_reminders.py` (ticks) · `services/academy_graduation.py` (gather, Hall of Fame, ceremony tick, /reportcard, /halloffame data) · `services/academy_seen.py` (group-activity middleware) · `handlers/academy_remind.py` · `utils/content/academy_remind_texts.py` · `tests/test_academy_notify_logic.py` · `tests/test_academy_notify.py`.

### Files changed (minimal, additive)
`database/models.py` (4 new tables appended) · `handlers/academy_routers.py` (+1 module) · `services/academy_ticks.py` (TICK_MODULES +2) · `services/academy_events.py` (+3 constants) · `utils/helpcatalog.py` (3 DESC_LIST lines + the 3 names in the "Academy 🎓" category; NOT in MENU / GROUP_MENU_EXTRA) · `bot.py` (2 lines in `build_dispatcher`: `dp.update.outer_middleware(GroupSeenMiddleware())`). No existing table, command or balance was touched; no AI file was touched.

### Tables (new, created by create_all, no migration)
* `academy_notify_log` (id, kind, key, season_id, target_id, sent_at; **UNIQUE kind+key+target_id**). The "send once" lock. `target_id` = chat id or user id.
* `academy_user_prefs` (user_id PK, remind_on, unreachable, unreachable_at, updated_at).
* `academy_group_seen` (chat_id PK, last_seen, fails). Why: the existing group tables have no activity time, so the middleware notes it, **at most once per 6 h per group** (in-memory throttle, never per message). A group counts as active from its first message after deploy.
* `academy_hall_of_fame` (id, season_id, season_number, user_id, name, title, rank, gpa, detail, created_at; UNIQUE season+user+title). Holds EVERY awarded title (the lighter ones too: a report card reads them from here).

### How "exactly once" works
Claim first, send second: `N.claim(kind, key, target)` INSERTs the log row; only the winner sends. Restarts, retries and overlapping ticks cannot double-send. If the send raises (network error, nothing delivered) the claim is released so the next tick retries; if Telegram refuses (blocked / kicked) the claim stays (never retried). Trade-off: a crash in the few ms between claim and send loses that one message (it is never duplicated). Several rows at once (daily DM slot + the reminder) use `claim_all` (all or nothing). **SQLite rule:** never call an `academy_notify` function (they open their own transaction) inside another `db.tx()`.

### Sending (everything goes through `utils.telegram.safe_send`)
`N.Sender(bot)`: one global `Limiter` (15 msgs per rolling second), a 0.25 s sleep every 20 sends, a 35 s time budget and 400-message cap per tick run (the rest goes out next tick; ticks are killed at 50 s). `deliver_dm` marks a user **unreachable** when `safe_send` returned None and `users.blocked_bot` is set; `deliver_group` counts refusals in `academy_group_seen.fails` (3 in a row = skipped until someone writes in the group again). Texts over 3900 chars are split. GIF: report cards of graduates with a passing grade use `AR.CARD_GIF` ("dance") via `utils.gifs.pick` + `send_animation` (caption ≤ 1000 chars, otherwise plain text; GIFS=off -> text).

### Ticks (all in the ONE academy loop)
| name | every | what |
|---|---|---|
| `academy.remind_season_open` | 120 s | Day 1, not quiet: announcement + Enroll button in every active group (key `s<id>:open`) |
| `academy.remind_enroll` | 300 s | Days 1-4, not quiet. Groups: one per 6-hour window (window 0 = the season-open message), plus ONE "last call" in the final 2 h before admissions close. DMs: once a local day to non-enrolled users seen in the last 14 days; the Day 4 text is the last call |
| `academy.remind_enrolled` | 300 s | Days 1-6, not quiet: ONE DM reminder per user per local day (`dm_daily` slot). Priority: midterm (from 1 h before it opens until 2 h before it closes) > finals (from 18 h before) > adventure available > Energy full (refilled in the last 24 h). Scans 200 students per run round-robin |
| `academy.recap` | 300 s | At ACADEMY_DAILY_HOUR (default 22) local, Days 1-6: group recap (top student so far, club standings, tomorrow's teaser) + DM to each enrolled player (Energy used, stats, rank, next). If the daily hour comes < 4 h after the day began, the recap goes 1 h before the day ends. DM recaps wait for 07:00 if due at night (while the day still runs). Not a "reminder": not counted in the one-a-day limit |
| `academy.ceremony` | 60 s | Day 7 + 10 min (and up to 30 h after the season ends): awards titles, writes the Hall of Fame, emits `graduated`, sends the ceremony to active groups and a report card DM to every enrolled player (200 per run), then marks `ceremony_done`. Calls `academy_exams.exam_close_tick` first so every closed attempt counts. Group ceremony ignores quiet hours; DMs wait for 07:00 |
| `academy.notify_purge` | 6 h | deletes `dm_daily` rows > 3 days and any row > 90 days |
"Active group" = in `group_settings` with `games_enabled`, `academy_group_seen.last_seen` within 7 days, `fails < 3`. DM eligibility = not banned, not `blocked_bot`, `remind_on`, and not `unreachable` (retried after 7 days if the user was seen since; `/academyremind` or `/reportcard` in a private chat clears it).

### Titles (rules in `academy_ceremony.award_titles`, numbers in `AR`)
* **Valedictorian** rank 1 (a tie shares it), grade not F. * **Topper** best score of each stream (ties share), score ≥ 60. * **Club Champion** members who hold the season's club badge points (they played for the winning club). * **Perfect Attendance** attended every class day they could (a late joiner has fewer). * **Backbencher** attended no class, yet passed (grade not F) - written as a cheeky compliment.

### CEREMONY_SECTIONS (plug-ins)
```python
from services.academy_ceremony import ceremony_section
@ceremony_section(name="my_extra", order=200, scope="both")   # "ceremony" | "card" | "both"
async def my_extra(ctx):          # sync or async; ctx.scope, ctx.data (CeremonyData), ctx.card (CardData | None); return HTML text or None
    return "🌟 ..."
```
Built-in blocks use order 10-60; extras (default 100) land after them and before the footer. A raising section is logged and skipped. Same name again replaces it; `unregister(name)` removes it. `ctx.data.session` is an open DB session while the text is built (read-only use please).

### Public API (signatures)
* `academy_remind_logic`: `season_day(start, now)`, `in_quiet_hours(now)`, `local_day_key(now)`, `enroll_group_plan(start, now) -> GroupPlan|None`, `recap_time(start, day)`, `recap_due(start, now)`, `ceremony_window(start)`, `ceremony_due(start, now)`, `Limiter(rate, clock, sleep).acquire()`, `dm_allowed(...)`, `group_allowed(...)`, `pick_reminder(StudentState, now, sent) -> Reminder|None`, `reminder_candidates`, `log_kind(kind)`.
* `academy_notify` (DB): `claim(kind, key, target, season_id, now) -> bool`, `claim_all(entries, now)`, `release`, `was_sent`, `sent_targets`, `purge_old`, `set_remind(uid, on, now, private=False)`, `reminders_on(uid)`, `mark_unreachable`, `clear_unreachable`, `is_unreachable`, `active_groups(now)`, `note_group_seen(chat_id, now)`, `Sender(bot, ...).send(chat_id, text, kb, gif) -> "ok"|"refused"|"error"`, `deliver_dm`, `deliver_group`.
* `academy_graduation` (DB): `gather(s, season, now, user_id=None, titles_from_db=False) -> CeremonyData`, `card_for(data, uid, provisional)`, `save_hall(s, data)`, `report_card(uid, now, last=False) -> str|None`, `hall_of_fame(limit) -> str`.

### Commands (in /help under "Academy 🎓", NOT in the "/" menu; no name collisions: checked against all handlers)
`/academyremind on|off` (no argument = status; **off = no unsolicited Academy DMs at all**, reminders, recaps and the report card DM; groups still get the season messages) · `/reportcard [last]` (running season so far, or the last finished one; Day 7 onward = final, with titles) · `/halloffame` (last 5 seasons: valedictorian, toppers, club champions, counts of the lighter titles).
Callback `acn:e:<season_id>` (the Enroll button of group messages): **it has no owner id on purpose** (one shared message): it acts only for the person who presses it (their id comes from Telegram) and only sends THEM their enrolment menu in a private message (`_enroll_intro` of Part 1; its own buttons carry the id and are owner-checked). If the bot cannot DM them it says to press Start first.

### Events
New constants in `academy_events`: `graduated` (user_id; season_id, number, rank, gpa, grade, titles), once per ranked graduate (claimed in the log, so exactly once); `ceremony_done` (user_id=0; season_id, number, graduates), once per season; `remind_changed` (user_id; on). Subscribes to `reset`: deletes `academy_notify_log` and `academy_hall_of_fame` (prefs and group-activity rows are kept).

### Known issues / notes
* DB + Dispatcher tests not run in the build sandbox (see top).
* No diamonds are paid for graduation (not in the brief); `academy_students.pay_diamonds` is ready if a later part wants it (use ref `graduation:s<id>:u<id>`).
* "Active group" starts at the first message seen after deploying Part 6 (no history exists in the old tables).
* Quiet hours: reminders (season-open, enrol, enrolled) and every DM wait; group recaps and the group ceremony are sent on time.
* Students who never sat an exam still get a short report card DM (no GIF) and no rank; they are not listed in the ceremony.
* Titles for a season are final once the ceremony tick ran; `/reportcard` in the first 10 minutes of Day 7 shows the final GPA but may not list titles yet.
* The recap's "top student so far" before the first exam is by Knowledge; in a group it is limited to players the bot knows in that group (falls back to the school).
* `/reportcard` and `/halloffame` have no extra cooldown beyond the existing middleware throttle; `/reportcard` computes the season ranking (one query set).
* Not done / left for later parts: extras that plug into CEREMONY_SECTIONS (e.g. memory-book, awards), moderator tools.


### Part 6 extras (added after the first delivery)
* **Message variety:** `RL.variant(options, key)` picks one of several wordings deterministically (same key = same text). Used for the adventure and Energy-full reminders and the ceremony footer (`ADVENTURE_VARIANTS`, `ENERGY_FULL_VARIANTS`, `CEREMONY_FOOT_VARIANTS` in `utils/content/academy_remind_texts.py`).
* **Season highlights** line in the group ceremony (graduates, class average GPA, A-or-better count, perfect attendance): `academy_ceremony.highlights(data)`, block order 55.
* **Graduation diamonds, OFF by default.** `ACADEMY_GRADUATION_DIAMONDS=1` switches on `AR.GRADUATION_DIAMONDS` (valedictorian 50, topper 25, perfect attendance 10 per title). `academy_graduation.pay_prizes(season, data)` pays through `academy_students.pay_diamonds` with ref `graduation:s<season>:u<user>:<title>` (unique, so a retry pays nothing twice); the report card shows "Graduation prize +N". Tune the numbers in the rules file.
* Not done on purpose: staff / moderation controls (the brief forbids moderation features). A fake-database test layer was not built (it would not prove the real SQLAlchemy queries work); run `tests/test_academy_notify.py` on a real install instead.
* Tests: `tests/test_academy_notify_logic.py` has 57 tests, all passing here; 30 DB tests in `tests/test_academy_notify.py` (not run in the build sandbox).


## Part 7 — Help menus, docs, final audit  ✅ done

### Files
Added: `services/academy_help.py` (PURE pages) · `services/academy_economy.py` (PURE diamond budget) · `handlers/academy_help.py` · `utils/content/academy_help_texts.py` · `docs/ACADEMY.md` · `tests/test_academy_help.py` · `tests/test_academy_audit.py`.
Changed (minimal): `handlers/academy_routers.py` (+`handlers.academy_help`) · `utils/helpcatalog.py` (DESC_LIST + Academy category: `academyhelp`) · `utils/content/academy_texts.py` (Day 1 hint mentions /academyhelp) · `services/academy_notify.py` (`seen_due` dict bounded: `SEEN_MAX`) · `services/academy_reminders.py` (`_set_cursor`, `_CURSOR_KEEP`) · `services/academy_graduation.py` (bug fix, see below) · `README.md` · `.env.example`.

### Public API
* `academy_help`: `render(page, season_start=None, label=None) -> HTML`, `resolve(arg) -> key|None`, `nav(page) -> (back, next)`, `week_label(number, theme, upcoming)`, `PAGE_KEYS`.
* `academy_economy`: `summary()`, `days()`, `day_budget(day)`, `peak_day()`, `max_per_season()`, `once_sources()`, `sinks()`, `energy_needed()`, `energy_supply_per_day()`.
* Command `/academyhelp [start|daily|exams|adv|social|week|how|privacy|...]`; callbacks `ach:<page>:<owner id>`. No tables, no ticks, no events added. Works even when ACADEMY_ENABLED=0 (adds a note).

### Fixed while auditing (earlier parts)
* `academy_graduation.report_card` used `await` inside a generator expression (async generator, `/reportcard` would crash): now a loop.
* Four earlier tests were wrong (not the code): notify rate-limit measurement, reminder cursor leaking between tests, campus-date event (fires in `deliver`), adventure dispatcher loop. Bounded `_seen_mono` and `_cursor`.

### Audit results
Command collisions: none. Callback data: all < 64 bytes at 16-digit ids. Owner checks: every callback handler (acn:e acts only for the presser). Telegram calls: all through safe helpers or inside try blocks. Per-message DB writes: none (group-seen write throttled in memory, once per 6 h per group). Restarts: every tick idempotent (markers / send-once log / ledger refs).
Diamonds (upper bounds, see `docs/ACADEMY.md` section 9): 190 per normal day, 280 best day, 1370 per season (+85 optional graduation prizes); only sink: campus date, 75 per season max. Energy is not the limit.

### Known issues / left for later
* No self-service deletion of Academy data (the help says to ask the owner).
* The economy is a faucet with one small sink; tune `game/academy_rules.py` if it inflates.
* Replaying the same adventure on a day is allowed (2 finishes a day cap).
