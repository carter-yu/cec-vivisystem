# Phase 14 – Period recap (today / week / month / date range)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

Read-only `LIST_EVENTS` windows beyond a single named day. Does **not** depend on Writer. Does **not** waive system standards.

## Goal

1. Review 2026-09-11 incident logs (`incident-logs/2026-09-11/`).
2. Live `#family-plans` line **`今日有乜？`** is `LIST_EVENTS` for **today** HKT (not create-clarification missing `start`).
3. Week / next-week / month / explicit date-range phrases list that window.
4. Multi-day replies use `format_recap` (group by day). Empty period → `呢段時間日曆冇活動。`
5. Listener recap = no confirmation, no calendar write. List path still always replies.

No LLM. No life-notes in recap. No important-dates. No freebusy. No Writer change.

## Why this phase (decision)

Incident 2026-09-08 (`listener-2026-09-08.log` / `parser-2026-09-08.log`): `今日有乜？` was accepted (`dispatch_succeeded`) but parsed `needs_clarification` `missing_fields=['start']` (`notes` path: list signal `有乜` with no date word `_extract_date` understands). Same miss appears 2026-09-06 in Mini stdout. Phase 7/12 cover `聽日有乜` and `聽日有乜嘢活動`, not **今日**. Phase 14 is that list-window expansion.

Socket Mode `Errno 49` reconnect storm in the same bundle is Mini network/sleep (Listener did reconnect). Not this phase. Token `invalid_grant` remains operator.

## Time box

1–2 hours. P1–P5 parse + `format_recap` + listener recap tests. No launchd change.

## In Scope

### 1. Parser — period list windows

`_try_list_events` stays before create. List signal still required (`有乜` / `有什麼` / `tell me the events` / kin). Week = **Monday-start** Asia/Hong_Kong. End exclusive.

`FIXED_NOW = 2026-09-08 12:00 Asia/Hong_Kong` (Tuesday).

| ID | Message | Window (HKT, end exclusive) |
|----|---------|-----------------------------|
| P1 | `今日有乜？` / `今日有乜` | 2026-09-08 00:00 → 2026-09-09 00:00 |
| P2 | `今個星期有乜` / `今個禮拜有乜` | 2026-09-07 00:00 → 2026-09-14 00:00 |
| P3 | `下個星期有乜` | 2026-09-14 00:00 → 2026-09-21 00:00 |
| P4 | `今個月有乜` | 2026-09-01 00:00 → 2026-10-01 00:00 |
| P5 | `9月1日至9月7日有乜` | 2026-09-01 00:00 → 2026-09-08 00:00 (year from `now`) |
| Q3 | existing `聽日有乜` | still tomorrow |
| Q5 | `今日天氣點呀` | still `unknown` |
| Q6 | `有乜` with no date/period | still `needs_clarification` missing `start` |

These must **not** become `create_event`. `下星期三有乜` stays a **day** list (existing weekday rule), not next-week.

### 2. `format_recap`

In `calendar_reader.py` next to `format_event_list`.

- Group listed events by HKT calendar day; days sorted; items by start.
- Empty success → `呢段時間日曆冇活動。`
- Failed list → same error line as `format_event_list` (never claim a write).
- Single-day `LIST_EVENTS` (today, 聽日, Q1) keep `format_event_list`. Multi-day windows (`end - start > 1 day`) use `format_recap`.

### 3. Listener

`LIST_EVENTS` still always replies (list / recap / empty / explicit error). **No** confirmation. **No** calendar write. `No calendar change was made` on the reply.

### 4. Help text

`help` / `指令` allowed-input list includes `今日有乜`, `今個星期有乜`, `今個月有乜`, `9月1日至9月7日有乜`.

### Logging

Class **A** only. Existing `parse_*` / `list_*` / `dispatch_*`. No new store.

## Out of Scope

| Item | Why later / elsewhere |
|------|------------------------|
| `今日有咩做？` / `明天活動？` / `what are the events for today?` | Not locked; Phase 12 left 有咩 / bare 活動 as clarification |
| Socket reconnect backoff / Errno 49 | Mini network; Listener already reconnects |
| Google `invalid_grant` | Operator token refresh |
| Important dates + 10:00 review | Phase 15 |
| Dedup two confirmations, update/delete, freebusy, LLM | later |

## Unit test plan (locked)

`FIXED_NOW = 2026-09-08 12:00 Asia/Hong_Kong` for P1–P5. Fake calendar. No network.

| Path | Role |
|------|------|
| `tests/test_parser.py` | P1–P5, Q3/Q5/Q6 still green, weekday-not-week |
| `tests/test_calendar_reader.py` | R6–R8 `format_recap` |
| `tests/test_listener.py` | L3 today list; L4 week recap; L2 still replies on error |

| ID | Scenario | Expect |
|----|----------|--------|
| P1 | `今日有乜？` | `list_events`, today window, not create/unknown/clarification |
| P2 | `今個星期有乜` / `今個禮拜有乜` | this Monday→next Monday |
| P3 | `下個星期有乜` | next Monday→Monday after |
| P4 | `今個月有乜` | month 00:00 → next month 00:00 |
| P5 | `9月1日至9月7日有乜` | 2026-09-01 → 2026-09-08 |
| R6 | two events on two days | grouped by day; titles present |
| R7 | empty period | `呢段時間日曆冇活動。` |
| R8 | failed list | error wording; no write claim |
| L3 | listener P1 + fake client | Slack reply of today’s events; no confirmation |
| L4 | listener P2 + two days | recap grouped; no confirmation |

Named tests:

- `test_parse_list_events_today` → P1  
- `test_parse_list_events_this_week` → P2  
- `test_parse_list_events_next_week` → P3  
- `test_parse_list_events_this_month` → P4  
- `test_parse_list_events_date_range` → P5  
- `test_parse_list_events_next_weekday_is_not_next_week`  
- `test_format_recap_groups_by_day` → R6  
- `test_format_recap_empty` → R7  
- `test_format_recap_failed` → R8  
- `test_list_events_today_replies_without_confirmation` → L3  
- `test_list_events_this_week_recap_without_confirmation` → L4  

### Non-tests

Live Slack/Google, 有咩 variants, reconnect backoff, Phase 15, LLM.

### Minimum green bar

~11 new tests + full suite green + ruff.

## Acceptance Criteria

- [x] 2026-09-11 logs reviewed; PROGRESS names the `今日有乜？` miss
- [x] P1–P5 parse windows
- [x] `format_recap` R6–R8
- [x] Listener L3/L4; no confirmation; no write
- [x] Q5 weather / Q6 bare 有乜 / create fixtures unchanged
- [x] No Writer change, no LLM, no important-dates
- [x] pytest + ruff clean
- [x] architecture, README, PROGRESS updated

## Success definition

Asking `今日有乜？` in `#family-plans` lists today’s events (or empty/error). `今個星期` / `今個月` / a date range get a day-grouped recap.
