# Phase 6 – Calendar Writer (Create Only, Accepted Confirmations)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [Phase 4 core](phase-4-confirmation.md) · [Phase 4b Slack confirmation](phase-4b-slack-confirmation.md)  

This phase introduces the **Calendar Writer**: the only component allowed to write to Google Calendar. It does **not** waive system standards. It does **not** update or delete events. It does **not** change the parser.

## Goal

Create **one** Google Calendar event from an **accepted** confirmation that already has a `confirmation_id`. Refuse every other status. Refuse a missing confirmation id loudly. Default `pytest` never talks to Google, never needs tokens, never calls an LLM.

## Why this phase (decision)

Phase 4b lets the family accept a plan in Slack `#family-plans`. Nothing reaches Google Calendar yet. Ground rule 6: no CUD without a confirmation id. Ground rule 7: Google Calendar is the source of truth for time-based events.

This is the first (and only) writer. Update / delete stay later. Freebusy stays later.

Live Desktop OAuth (project `cec-vivisystem`, scope `calendar.events`, Testing) is already in local `.env` as of 2026-08-29. Pytest stays offline. Live OAuth smoke is stretch; do not block the phase on it.

## Time box

1–2 hours. Prefer green offline tests + fake Google client over finishing live OAuth in the same session. Live smoke is stretch.

## In Scope

### 1. `write_calendar_create` (testable core)

```text
write_calendar_create(confirmation, *, client=, calendar_id=, audit_store=, now=) -> CalendarWriteResult
```

- **`client` is required** (keyword-only). No hidden live Google default. Tests inject a fake. Live Socket Mode / CLI constructs a real client from env.
- **Refuses** (no Google call, `outcome=refused`) unless:
  - `confirmation.status == accepted`
  - `confirmation.confirmation_id` is a non-empty string
- **Refuses** if `parse_result.start` is missing (cannot build a calendar event).
- **Does not crash** on Google API errors: catch, log `write_failed`, return `outcome=failed`.
- Never logs OAuth tokens or `.env` values (ground rule 13).

### 2. Injectable Calendar client

Protocol (names may vary; semantics are the bar):

```text
CalendarClient.create_event(draft) -> CalendarEventCreated
```

- **Tests:** `FakeCalendarClient` records calls; can raise a fake API error.
- **Live:** a thin Google Calendar API wrapper (scope `calendar.events` only). Built from `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN`. Calendar id from `GOOGLE_CALENDAR_ID` or `"primary"`. Lazy-import Google libraries so the default suite does not need a live token.
- Missing / empty Google env when constructing the **live** client → typed config error (no network). Pytest never constructs the live client.

### 3. Event mapping (`ParseResult` → Calendar event)

| Source | Google field |
|--------|----------------|
| `parse_result.title` (fallback `"Family event"` if empty) | `summary` |
| `parse_result.start` in `Asia/Hong_Kong` | `start` (`dateTime` + `timeZone`, or `date` if `all_day`) |
| `parse_result.end` if present; else **start + 1 hour** (all-day: next calendar day, Google exclusive end) | `end` |
| `parse_result.location` if present | `location` |
| `parse_result.participants` | `description` line; **attendees** only if a value looks like an email (`@`). Do not invent emails for names like Cedric. |
| `confirmation_id` | private extended property (and/or description line) so the event is auditable |

Calendar id: argument, else env `GOOGLE_CALENDAR_ID`, else `"primary"`.

### 4. Structured logs (Calendar Writer matrix)

`component=calendar_writer`

| Event | Level | When |
|-------|-------|------|
| `write_attempt` | INFO | About to call Google (gate passed) |
| `write_succeeded` | INFO | Google returned an event id |
| `write_failed` | ERROR | Google / mapping failure after the gate |
| `write_refused` | WARNING | Not accepted, missing start, etc. |
| Write **without** confirmation id | **ERROR/CRITICAL** (`write_without_confirmation_id`) | Must not succeed; no Google call |

Fields: `op=create`, `confirmation_id`, title, start, calendar id, `correlation_id`, `outcome`, `duration_ms`; on failure `error_type` / `error_message`; on success `calendar_event_id`. Never tokens.

### 5. Class B audit (90 days) + purge

Every attempt **and** result (success / refused / failed) is a class **B** audit row. **No** full local calendar mirror (class **G**).

- Production: JSON files under gitignored `data/calendar_audit/`
- Tests: in-memory store
- Fields (minimum): `audit_id`, `attempted_at`, `op`, `confirmation_id`, `correlation_id`, `outcome`, `calendar_id`, `calendar_event_id`, title, start, `error_type`, `error_message`
- **Purge:** delete rows older than **90 days**; soft cap **50 MB** (delete oldest first). `purge_calendar_audit` + `maintain_calendar_audit_storage` (call on Socket Mode start).
- Do not store OAuth tokens or full Google event JSON dumps.

### 6. Listener wiring (thin)

After a **first** accept in `#family-plans` (status was pending → accepted), if an injectable `calendar_client` is present, call `write_calendar_create`.

- Omitting `calendar_client` preserves Phase 4b behaviour (Y3 stays green: “Accepted. No calendar write yet…”).
- Idempotent second `yes`: do **not** write again (only write when the row was still pending before resolve).
- Socket Mode: if Google env is complete, construct the live client; if not, log and skip write (Listener still runs). Also `maintain_calendar_audit_storage` on start.
- **Slack ack that the event was created — stretch.** Only after `write_succeeded`. If time is tight, keep the existing accept ack when no client is injected.

### 7. Env

Use existing placeholders. Add `GOOGLE_CALENDAR_ID` **name** in `.env.example` only (value empty or `primary`). Never commit `.env`, client JSON, or refresh tokens.

| Name | Role |
|------|------|
| `GOOGLE_CLIENT_ID` | Desktop OAuth client id |
| `GOOGLE_CLIENT_SECRET` | Desktop OAuth client secret |
| `GOOGLE_REFRESH_TOKEN` | Offline refresh token |
| `GOOGLE_CALENDAR_ID` | Target calendar (`primary` or `…@group.calendar.google.com`) |
| `GOOGLE_CREDENTIALS_PATH` | Optional gitignored client JSON (not required if id+secret are in `.env`) |

Live operator steps stay in gitignored `my-notes/google-calendar-credentials.md`.

### 8. Docs / process

- This file locked **before** code.
- Update architecture §3/§5: Calendar Writer done (create-only); freebusy still not started.
- `PROGRESS.md`, README, `.env.example`.
- Logging standard §8: Phase 6 class B applicability.
- Resilience write gate: single attempt this phase (no retry storm); failures observable (logs + audit); no confirmation id → no write.

## Out of Scope

| Item | Why later |
|------|-----------|
| Update / delete calendar events | Create-only this phase |
| Freebusy / Availability Checker | Explicitly deferred |
| LLM / parser expansion (梓梵, 游水, new titles) | Separate parser phase if family need that; **do not combine** |
| Life-note enrichment | Parallel path |
| Publishing the Google OAuth app | Stay Testing; test users only |
| Combining Writer + parser + 4b redesign in one session | One gate |
| Retry / dead-letter store (class D) | Audit is enough; retry later |
| Duplicate-event detector beyond “first accept only” | Later if live duplicates appear |
| Inventing attendee emails | Names go in description |

**Live friction (do not “fix” by guessing calendar writes):**  
A real `#family-plans` line `聽日9點，梓梵游水` returned `needs_clarification` (missing title). Writer will not see it until parse is `create_event` and the family replies yes. Prefer `聽日上午9點帶 Cedric 去游泳` for Writer smoke. If family need that weekend is actually parser rules (梓梵/游水) because yes/no never appears, **stop**, say so, and write a parser phase doc instead.

## Unit test plan (locked for implementation)

Authoritative Phase 6 test decision. Implements [unit-testing.md](../docs/unit-testing.md).

### Layout

| Path | Role |
|------|------|
| `tests/test_calendar_writer.py` | W1–W8 (writer core, fake client, audit, logs) |
| `tests/test_listener.py` | Existing Y1–Y8 stay green; optional W-listener if wiring lands |
| Fake `CalendarClient` | Injected; default pytest never hits Google or `.env` tokens |

Keep Phase 0–5B / 4 / 4b suites green (W5 = full `pytest`).

### Determinism

1. Injectable `now=` (`FIXED_NOW = 2026-08-08 12:00 Asia/Hong_Kong`).
2. Real offline `parse` + Phase 4 `create_confirmation` / `resolve_confirmation` to build an accepted row (F1: `星期六下午3點帶 Cedric 去游泳`), **or** a constructed `Confirmation`.
3. Fake Google client records `create_event` calls; never network.
4. In-memory audit store.
5. No tokens, no LLM, no live Slack.

### Fixtures / cases

| ID | Scenario | Expect |
|----|----------|--------|
| **W1** | Accepted confirmation (F1 or equivalent) | `create_event` called with title + start (`Asia/Hong_Kong`); `confirmation_id` passed; `outcome=success`; fake event id returned |
| **W2** | `pending` / `rejected` / `expired` | Refuse; **no** Google call |
| **W3** | Missing / blank `confirmation_id` | Refuse; **ERROR/CRITICAL** log (`write_without_confirmation_id`); no success; no Google call |
| **W4** | Fake Google API error | `outcome=failed`; process does **not** crash; `write_failed` path |
| **W5** | Previous Phase 4 / 4b / 5B suites | Full `uv run pytest` green |
| **W6** | `CalendarWriteResult` contract | Attributes: `outcome`, `op`, `confirmation_id`, `calendar_id`, `calendar_event_id`, `error_type`, `error_message`, `duration_ms` |
| **W7** | Class B audit + purge | Attempt+result stored (success and failure); purge deletes rows older than 90d; fresh rows kept |
| **W8** | Log boundary | Accepted write completes with logging configured (`write_attempt` / `write_succeeded` or equivalent) |

### Named tests

- `test_accepted_confirmation_creates_event` → W1  
- `test_non_accepted_confirmation_refuses_without_google_call` → W2  
- `test_missing_confirmation_id_refuses_loudly` → W3  
- `test_google_api_error_returns_failed_without_crash` → W4  
- `test_calendar_write_result_contract_fields` → W6  
- `test_audit_records_attempt_and_purge_after_90d` → W7  
- `test_calendar_writer_logs_boundary` → W8  

W5 is the existing suite (no new test function required).

### Optional (if Listener wiring lands in this session)

- `test_thread_yes_with_calendar_client_writes_once` — first accept calls create; second `yes` does not.

Omitting `calendar_client` must leave Y3 unchanged.

### Non-tests

- Live Google Calendar insert (manual smoke only; mark if a live test is ever added so default pytest stays offline)
- OAuth browser consent / publishing the app
- Update / delete
- Freebusy
- Parser expansion (梓梵 / 游水 / missing title)
- Slack Block Kit
- LLM
- Retry storms / concurrent double-write stress beyond first-accept guard

### Minimum green bar

| Category | Min tests |
|----------|-----------|
| Happy create (W1) | 1 |
| Refuse pending/rejected/expired (W2) | 1 |
| Missing confirmation id (W3) | 1 |
| Fake API error (W4) | 1 |
| Contract + audit/purge + logs (W6–W8) | 3 |
| **Total new** | **~7** |

Plus W5 = full suite still green.

## Logging & retention applicability

| Data | Class | Phase 6 action |
|------|-------|----------------|
| Writer / Listener logs | **A** | `write_attempt` / `write_succeeded` / `write_failed` / `write_without_confirmation_id`; existing 14d file purge |
| Calendar write attempt + result | **B** | New store `data/calendar_audit/`; **90 days**; purge + 50 MB soft cap |
| Confirmation rows | **C** | Unchanged |
| Google Calendar events | **G** | Source of truth; **do not** mirror full event history locally |
| Secrets | — | `.env` / `my-notes/` only; never logged |

**No store without a purge story** — W7 covers purge.

Update [logging-and-retention.md](../docs/logging-and-retention.md) §8 with a Phase 6 row (class B).

### Resilience write gate (this phase)

- Timeout / retry: **single attempt**; live HTTP timeout if the client library allows (e.g. ~30s). No retry loop.
- Failures observable: `write_failed` + class B audit row.
- Human confirmation id present; writes without it log ERROR/CRITICAL and must not succeed (W3).
- Dead-letter class D: not introduced; audit is the failed-write record.

## Acceptance Criteria

- [x] Phase doc (this file) locked before implementation
- [x] `write_calendar_create` refuses unless `accepted` + non-empty `confirmation_id`
- [x] Fake client used in default pytest; no network, no tokens, no LLM
- [x] Mapping: title, start in `Asia/Hong_Kong`, location/participants as specified
- [x] Structured logs match the Calendar Writer matrix; missing confirmation id is loud and unsuccessful
- [x] Class B audit of attempt + result with 90d purge (tested)
- [x] No local full calendar mirror
- [x] `.env.example` names only (`GOOGLE_CALENDAR_ID` added); secrets stay local
- [x] Unit tests W1–W4 and W6–W8 pass; W5 full suite green
- [x] Architecture, README, `PROGRESS.md`, logging standard updated
- [x] `uv run pytest` and `ruff check .` clean
- [x] Live OAuth smoke **not** required for acceptance (stretch)
- [x] Slack “event created” ack **not** required for acceptance (stretch) — shipped when a calendar client is injected; omitted-client path keeps Phase 4b ack

### Manual smoke (opt-in; not pytest)

1. Restart Listener with local `.env` (Google vars filled; `GOOGLE_CALENDAR_ID` as intended).
2. In `#family-plans` post a **create_event** phrase, e.g. `聽日上午9點帶 Cedric 去游泳` — **not** `聽日9點，梓梵游水`.
3. Reply `yes` in the thread.
4. Confirm one event on the target calendar (title + start). Stretch: Slack ack that it was created.
5. If Google env is missing, Listener still runs; accept ack remains “no calendar write yet”.

## Suggested session order (TDD)

1. Lock this document.  
2. Models + fake client + red tests W1–W4, W6–W8.  
3. Green `write_calendar_create` + audit + purge.  
4. Thin Listener inject + Socket Mode client/audit if time.  
5. Docs; `pytest` + `ruff`.  
6. Stop before parser expansion or live OAuth debugging.

## Success definition

Phase 6 is **done** when an **accepted** confirmation with a `confirmation_id` can create **one** calendar event through an injectable client, with class B audit and loud refusal without that id — and default pytest never touches Google.

## Explicit non-goal

Do not implement update/delete, freebusy, or parser title rules “while wiring the writer.” One writer, create-only, green offline tests.
