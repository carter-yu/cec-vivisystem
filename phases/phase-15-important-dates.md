# Phase 15 – Important dates (add / view / 10:00 next-7-days)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

Family **important dates** (birthdays, exams, trips): add and view in `#family-plans`, plus a **10:00 Asia/Hong_Kong** scan of the next 7 days. Does **not** waive system standards.

## Goal

1. Slack `#family-plans` can **add** and **view** important dates with locked phrases.
2. `help` / `指令` lists how to add and view them.
3. Daily **10:00 HKT** job: if any stored important date occurs in the next 7 days and that occurrence is not yet posted, post once to `#family-plans`.
4. Offline-testable with injectable `now=` and fake store + fake Slack poster.

No Calendar Writer. No LifeNotes `raw_text`. No LLM. No freebusy. No delete/update UI this phase.

## Why this phase (decision)

Phase 14 lists calendar periods. Birthdays and one-off family markers are **not** timed calendar events (ground rule 7 SoT is Google for time-based events). A separate store keeps them off the Writer gate. Add is immediate (like life notes), not a calendar create, so ground rule 6 confirmation does **not** apply. Recorded as [ADR 0004](../docs/decisions/0004-important-dates-store.md).

## Time box

1–2 hours. Parser add/view + store + listener + help + `run_important_dates_review`. launchd 10:00 plist on Mini is **operator stretch** (document the command).

## In Scope

### 1. Parser — new intents (before create)

`IntentType.ADD_IMPORTANT_DATE` and `IntentType.LIST_IMPORTANT_DATES`.

List path (`有乜`) and help stay first. Important-date add requires a month-day (optional year) **and** a keyword (`生日` / `birthday` / `考試` / `exam` / `旅行` / `trip`), and must **not** have a clock or `聽日` / `tomorrow` (those stay create/list).

`FIXED_NOW = 2026-09-08 12:00 Asia/Hong_Kong` (Tuesday).

| ID | Message | Expect |
|----|---------|--------|
| I1 | `4月12日 梓梵生日` | `add_important_date`; yearly; title contains 生日; participant **Cedric**; `all_day`; month 4 day 12; no year |
| I2 | `10月22日 老婆生日` | yearly; title 老婆生日 |
| I3 | `12月4日 Carter 生日` | yearly; participant Carter |
| I4 | `2026年9月15日 考試` | one-off; start 2026-09-15 00:00 HKT |
| I5 | `重要日子` / `有咩生日` | `list_important_dates` |
| I6 | F1 create phrase | still `create_event` |
| I7 | `今日有乜` | still `list_events` |
| I8 | `4月12日` (no keyword) | not add (unknown or create-clarification as today) |

Year omitted → **recurring yearly**. Year present → **one-off**. `梓梵` still canonical **Cedric** in `participants`.

### 2. Store (ADR 0004)

Module `src/cec_vivisystem/important_dates.py`.

- Rows: `data/important_dates/` JSON (class **F**, until family deletes; no auto-purge).
- 10:00 occurrence markers: `data/important_dates_posts/` (class **C**, 30d purge on CLI start).
- `create_important_date(parse_result, *, store=, now=)` — persists; same month/day/year/title (casefold) → `already_exists`, no second row.
- `list_important_dates(store)` — all rows, sorted by month then day.
- Tests: `InMemoryImportantDatesStore`. Live: `JsonDirImportantDatesStore`.
- Not LifeNotes. Not Calendar Writer. Not a local calendar mirror (class **G** stays Google).

### 3. Listener

`#family-plans`:

- `ADD_IMPORTANT_DATE` + store → persist + ack (e.g. `已記低重要日子：4月12日 梓梵生日（每年）`). **No** confirmation. **No** calendar write.
- `LIST_IMPORTANT_DATES` → list or empty `未記低重要日子。` **No** confirmation. **No** write.
- Omit store → explicit “not stored / not listed” line; still a reply.
- Keep `No calendar change was made` on these replies.

### 4. Help

`ALLOWED_INPUTS_HELP` gains a **重要日子 / important dates** section with add examples (I1–I4) and view examples (I5). States: 即時記低，唔使 yes；唔寫入日曆.

### 5. 10:00 review

`run_important_dates_review(*, now=, poster=, channel_id=, dates_store=, post_store=) -> ImportantDatesReviewResult`

- Window: `[today 00:00, today+7 days 00:00)` HKT (today + next 6 days).
- Yearly: next occurrence on or after today (same month-day; next year if already passed).
- One-off: the stored date if it falls in the window.
- Hits whose occurrence is not yet marked → **one** Slack post listing them; then mark each occurrence.
- No hits, or all already posted → `skipped` (no Slack post; unlike 07:00 recap, do **not** post empty).
- `poster` injectable; tests never hit Slack. Failures log `component=important_dates`; do not write calendar.

**CLI:** `uv run python -c "from cec_vivisystem.important_dates import main; main()"`  
Operator Mini: launchd at 10:00 HKT (document in README / PROGRESS; plist not required for pytest).

### Logging

- `component=important_dates`: `important_date_written` / `important_date_already_exists` / `important_dates_listed` / `important_dates_review_started` / `important_dates_review_posted` / `important_dates_review_skipped` / `important_dates_review_failed`
- Store save/load failures ERROR
- Class **A** logs; class **F** rows; class **C** post markers

## Out of Scope

| Item | Why later |
|------|-----------|
| Slack delete / edit important dates | later |
| Tagged Google all-day events | ADR 0004 chose JSON store |
| Calendar Writer / confirmation yes-no | not a calendar create |
| Reminder Agent / 07:00 recap rewrite | separate components |
| LLM | rules first |

## Unit test plan (locked)

No network. Fake store + fake poster. `FIXED_NOW = 2026-09-08 12:00 Asia/Hong_Kong`. Review tests may use `10:00`.

| Path | Role |
|------|------|
| `tests/test_parser.py` | I1–I8 |
| `tests/test_important_dates.py` | D1–D10 |
| `tests/test_listener.py` | L-add, L-view, L-help includes phrases |

| ID | Scenario | Expect |
|----|----------|--------|
| I1–I5 | locked phrases | intents/windows as table |
| I6–I8 | no steal / no bare MD | create, list, not-add |
| D1 | create I1 | stored yearly 4/12; Cedric |
| D2 | list empty | empty list |
| D3 | duplicate I1 | `already_exists`; one row |
| D4 | contract fields on stored row | id, title, month, day, kind, … |
| D5 | log boundary on create | `important_date_written` |
| D6 | review two hits in window | one poster call; titles present |
| D7 | empty / none in window | skip; no poster call |
| D8 | second review same occurrence | skip; no second post |
| D9 | poster error | failed; occurrence not marked |
| D10 | garbage / missing store path | no crash (load skip or typed error) |
| L-add | I1 + store | ack; stored; no confirmation; no Google create |
| L-view | stored I1 | reply lists it; no write |
| L-help | `指令` | contains 重要日子 and `4月12日` |

Named tests:

- `test_parse_add_important_date_zifan_birthday` → I1  
- `test_parse_add_important_date_wife_birthday` → I2  
- `test_parse_add_important_date_carter_birthday` → I3  
- `test_parse_add_important_date_exam_one_off` → I4  
- `test_parse_list_important_dates` → I5  
- `test_parse_create_not_important_date` → I6  
- `test_parse_today_list_not_important_date` → I7  
- `test_parse_month_day_without_keyword_not_add` → I8  
- `test_create_important_date_stores_yearly` → D1  
- `test_list_important_dates_empty` → D2  
- `test_create_important_date_duplicate` → D3  
- `test_important_date_contract_fields` → D4  
- `test_create_important_date_logs_boundary` → D5  
- `test_run_important_dates_review_posts_hits` → D6  
- `test_run_important_dates_review_skips_when_none` → D7  
- `test_run_important_dates_review_skips_already_posted` → D8  
- `test_run_important_dates_review_poster_error` → D9  
- `test_json_store_skips_corrupt_file` → D10  
- `test_add_important_date_replies_without_confirmation` → L-add  
- `test_list_important_dates_replies_without_confirmation` → L-view  
- `test_help_lists_important_date_phrases` → L-help (may extend existing help test)

### Non-tests

Live Slack/Google, launchd plist, delete/edit, tagged calendar events, LLM.

### Minimum green bar

~18 new tests + full suite green + ruff.

## Acceptance Criteria

- [x] Phase doc + ADR 0004 locked
- [x] I1–I8 parse
- [x] Store D1–D10
- [x] Listener add/view; help lists phrases
- [x] 10:00 review D6–D9; CLI documented
- [x] No Writer change; no calendar write on add
- [x] pytest + ruff clean
- [x] architecture, README, PROGRESS, logging matrix updated

## Success definition

In `#family-plans`, `4月12日 梓梵生日` is stored and `重要日子` / `有咩生日` lists it. `help` shows how. At 10:00, if that birthday falls in the next 7 days, the family gets one Slack note per occurrence.
