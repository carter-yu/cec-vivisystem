# Phase 7 – Calendar Reader (list / summary for a period)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

Read-only list of Google Calendar events for a parsed date range. **No CUD.** **No confirmation.** Conflict / same-person overlap is **Phase 8**.

## Goal

A family message in `#family-plans` such as `tell me the events on 1 Sept 2026` or `2026年9月1日有乜` becomes `list_events` and returns the events on that calendar day (Asia/Hong_Kong). Default pytest uses a fake Google client.

## Why this phase

Phase 6 can create events. The family next needs to **see** what is already on the calendar. Overlap / same-person conflict (Availability) must list a window first. Reader is the shared primitive; conflict waits.

Ground rule 6: listing is not create/update/delete — no confirmation id.

## Time box

1–2 hours. Green offline tests + fake client. Live Slack smoke is stretch.

## In Scope

### 1. Parser — `IntentType.LIST_EVENTS`

Same `parse(...)` → `ParseResult`. New intent only.

Day query: `all_day=True`, `start` = that day 00:00 HKT, `end` = **next calendar day 00:00 HKT** (exclusive). Title unused.

Locked phrases: Q1–Q5 below. List-like with no date → `needs_clarification` (`missing_fields` includes `start`). No LLM. No 梓梵/游水 titles.

### 2. `list_calendar_events(*, time_min, time_max, client=, calendar_id=, correlation_id=)`

New module `calendar_reader.py`. Writer stays create-gated.

- `client` required. Tests: `FakeCalendarClient.list_events`.
- Calendar id: argument, else `GOOGLE_CALENDAR_ID`, else `primary`.
- Result items: `event_id`, `summary`, `start`, `end`, `all_day`, `location`, `participants` (from attendees and/or `Participants:` description line).
- Google errors → `outcome=failed`, no crash. Empty range → success, `events=[]`.
- **No local event store** (class G).

Logs: `component=calendar_reader`, `list_attempt` / `list_succeeded` / `list_failed`.

Extend `CalendarClient` + live `GoogleCalendarClient` with `list_events`. Existing `calendar.events` scope is enough.

### 3. Listener

`#family-plans` + `list_events` + injected client → list reply, **no** confirmation. Omit client → do not pretend to have listed. Create / yes-no unchanged.

### 4. Docs

This file locked before code. Architecture, README, PROGRESS, logging §4/§8.

## Out of Scope

| Item | Why later |
|------|-----------|
| Overlap / same-person conflict | Phase 8; reuses this reader |
| Freebusy API | Availability product later |
| Update / delete | Writer still create-only |
| Parser 梓梵 / 游水 / MS Wong | Separate parser phase |
| Family calendar id in `.env` | Operator; listing uses `primary` until set |
| LLM | Rules first |

## Unit test plan (locked)

| Path | Role |
|------|------|
| `tests/test_parser.py` | Q1–Q5 |
| `tests/test_calendar_reader.py` | R1–R5 |
| `tests/test_listener.py` | L-list + existing create/yes-no stay green |

`FIXED_NOW = 2026-08-08 12:00 Asia/Hong_Kong`. No network, no tokens, no LLM.

| ID | Scenario | Expect |
|----|----------|--------|
| Q1 | `tell me the events on 1 Sept 2026` | `list_events`; start 2026-09-01 00:00 HKT; end 2026-09-02 00:00 HKT |
| Q2 | `2026年9月1日有乜` | same day range |
| Q3 | `聽日有乜` at FIXED_NOW | list for 2026-08-09 |
| Q4 | F1 create phrase | still `create_event` |
| Q5 | `今日天氣點呀` / empty | `unknown` |
| Q6 | `有乜` with no date | `needs_clarification`; missing `start` |
| R1 | Fake list two events in range | titles/starts present; `outcome=success` |
| R2 | Empty list | success, `events=[]` |
| R3 | Fake Google error | `failed`; no crash |
| R4 | Contract fields on list result |
| R5 | Log boundary `list_attempt` / `list_succeeded` |
| L-list | plans + list intent + fake client | replied list; **no** confirmation |
| W5 | Full `pytest` green |

Named tests:

- `test_parse_list_events_english_sept` → Q1  
- `test_parse_list_events_cantonese_ymd` → Q2  
- `test_parse_list_events_ting_yat` → Q3  
- `test_parse_create_event_not_list` → Q4 (existing F1 may suffice if still green)  
- `test_parse_list_without_date_needs_clarification` → Q6  
- `test_list_calendar_events_returns_items` → R1  
- `test_list_calendar_events_empty` → R2  
- `test_list_calendar_events_google_error` → R3  
- `test_calendar_list_result_contract_fields` → R4  
- `test_list_calendar_events_logs_boundary` → R5  
- `test_list_events_replies_without_confirmation` → L-list  

### Non-tests

Live Google list, conflict logic, parser aliases, Slack Block Kit, LLM.

### Minimum green bar

~10 new tests + full suite green.

## Logging & retention

| Data | Class | Action |
|------|-------|--------|
| Reader / parser / listener logs | **A** | 14d file purge |
| Google events | **G** | No local mirror |
| Confirmations | **C** | Unchanged |

## Acceptance Criteria

- [x] This file locked before implementation
- [x] `list_events` parse for Q1–Q3; create/unknown unchanged
- [x] `list_calendar_events` fake client; empty and error paths
- [x] Slack list path creates no confirmation
- [x] No calendar CUD from list
- [x] Tests Q/R/L-list; full pytest; ruff clean
- [x] Architecture, README, PROGRESS, logging standard updated
- [x] Live smoke not required

## Success definition

Family can ask what is on a given day and get a list from Google Calendar (or a fake in tests) without writing anything and without a yes/no confirmation.

## Next (not this phase)

Phase 8: overlap + optional same-person warn on the create **proposal**. Parser 梓梵 still separate.
