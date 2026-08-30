# Phase 8 – Overlap / Same-Person Warn on Create Proposal

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [Phase 7 Calendar Reader](phase-7-calendar-reader.md) · [Phase 4 confirmation](phase-4-confirmation.md) · [Phase 6 Writer](phase-6-calendar-writer.md)  

Warns the family when a **create** proposal overlaps an existing Google Calendar event (and, optionally, the same person). **Does not** silent-hard-block. **Does not** write without yes. **Does not** change the parser or the Writer.

## Goal

When `#family-plans` produces a `create_event` proposal, reuse `list_calendar_events` for the proposed `[start, end)` window (default **+1 hour** if `parse_result.end` is missing; all-day → next calendar day exclusive). If any listed interval intersects in `Asia/Hong_Kong`, append a **warning** on the proposal. If `parse_result.participants` intersects a listed event’s participants, add a **same-person** warning. The family still must reply **yes** to write. Default pytest uses `FakeCalendarClient`. No network, no tokens, no LLM.

## Why this phase

Phase 6 can create. Phase 7 can list. Double-booking is the next real-use friction. Ground rule 6 still requires explicit confirmation; a warn is not a write. Ground rule 7: Google Calendar is SoT — list live events, do not mirror them locally.

This is a **thin overlap slice**, not a freebusy Availability product.

## Time box

1–2 hours. Green offline tests + fake client. Live Slack smoke is stretch.

## In Scope

### 1. Overlap checker (testable core)

New module `overlap.py`. Reader stays read-only. Writer stays create-gated.

```text
detect_create_overlaps(parse_result, *, client=, calendar_id=, correlation_id=) -> OverlapCheckResult
```

- **`client` is required** (keyword-only). Tests inject `FakeCalendarClient`.
- Proposed window: `parse_result.start` … `parse_result.end` if present; else **start + 1 hour** (timed) or **start + 1 calendar day** (all-day). Exclusive end. Matches Writer default duration.
- Call `list_calendar_events` for that `[start, end)`.
- A listed event **overlaps** iff intervals intersect in `Asia/Hong_Kong` with half-open `[start, end)`: `max(starts) < min(ends)`. Adjacent (`15:00–16:00` then `16:00–17:00`) is **not** an overlap. Missing listed `end` uses the same +1h / +1 day default.
- **Same-person**: casefold exact string intersect of `parse_result.participants` with listed `participants` (Google attendees and/or the `Participants:` description line already mapped by the Reader). **Do not invent emails.** **No aliases:** `梓梵` ≠ `Cedric`.
- Missing `parse_result.start` → `outcome=skipped`, no list call.
- Google / list errors → `outcome=failed`, no crash, **still allow** the proposal (degraded warning, not a hard block).
- **No local event store** (class **G**).

### 2. Proposal text

Extend `build_proposal(parse_result, *, overlap_check=None)` (and `create_confirmation` to pass it through).

When overlaps exist, append English warning lines, for example:

```text
Warning: this proposal overlaps 1 existing event:
• 15:00–16:00 牙醫 (Cedric)
Warning: same person Cedric is also on overlapping event(s).
```

When the check **failed**, append:

```text
Warning: could not check the calendar for overlaps. You can still reply yes or no.
```

When omitted / skipped / empty: existing Phase 4 text unchanged.

The proposal **never** claims a calendar write. Yes/no vocabulary unchanged.

### 3. Listener (thin)

`#family-plans` + `create_event` + injected `calendar_client` → run `detect_create_overlaps` **before** `create_confirmation`, store the warning in `proposal_text`.

- Omitting `calendar_client` preserves Phase 4b/6 behaviour (Y1–Y8 stay green; no overlap claim).
- First **yes** still writes (Writer unchanged). Overlap is a warning, not a refuse.
- List queries, life-notes, and yes/no resolve paths unchanged.

### 4. Logs

`component=overlap`

| Event | Level | When |
|-------|-------|------|
| `overlap_check_started` | INFO | About to list the proposed window |
| `overlap_check_completed` | INFO | List + interval filter done |
| `overlap_check_failed` | ERROR | Reader/client error (proposal still created) |
| `overlap_check_skipped` | INFO | No start / not a create window |

Fields: calendar id, proposed `time_min` / `time_max`, `overlap_count`, `same_person_count`, `correlation_id`, `outcome`, `duration_ms`; on failure `error_type` / `error_message`. Never tokens.

### 5. Docs / process

- This file locked **before** code.
- Architecture §3/§5: overlap warn done; freebusy still not started.
- `PROGRESS.md`, README, logging standard §4/§8.

## Out of Scope

| Item | Why later |
|------|-----------|
| Silent hard-block / refuse write on overlap | Family still confirms; warn only |
| Freebusy API / full Availability Checker product | Explicitly deferred |
| Update / delete | Writer still create-only |
| Parser aliases (`梓梵` → Cedric) / `游水` / MS Wong titles | Separate parser phase; **do not combine** |
| Writer behaviour changes | Yes still creates; no overlap gate on write |
| Local calendar mirror | Class G |
| Inventing attendee emails | Names stay names |
| LLM | Rules first |
| Family `GOOGLE_CALENDAR_ID` in `.env` | Operator; listing/conflict use `primary` until set |

## Unit test plan (locked for implementation)

Authoritative Phase 8 test decision. Implements [unit-testing.md](../docs/unit-testing.md).

### Layout

| Path | Role |
|------|------|
| `tests/test_overlap.py` | O1–O8 (window, intersect, same-person, fail, contract, logs) |
| `tests/test_confirmation.py` | Existing C1–C10 stay green; proposal with overlap_check is covered via overlap + listener |
| `tests/test_listener.py` | L-overlap; existing create/yes-no/list stay green |
| Fake `CalendarClient` | Injected; default pytest never hits Google or `.env` tokens |

Keep Phase 0–7 suites green (W5 = full `pytest`).

### Determinism

1. Injectable `now=` (`FIXED_NOW = 2026-08-08 12:00 Asia/Hong_Kong`).
2. Real offline `parse` of F1 (`星期六下午3點帶 Cedric 去游泳`) → start Saturday 15:00 HKT, `end=None` → window **15:00–16:00**.
3. `FakeCalendarClient.listed_events` seeds calendar rows; overlap code **must** still filter by interval (fake list may return extra).
4. No tokens, no LLM, no live Slack.

### Fixtures / cases

| ID | Scenario | Expect |
|----|----------|--------|
| **O1** | F1 window vs listed `15:30–16:30` 牙醫 (Cedric) | `outcome=success`; 1 overlap; same-person includes `Cedric` |
| **O2** | F1 window vs adjacent `16:00–17:00` | no overlap |
| **O3** | F1 window vs overlap whose participants are only `梓梵` | overlap **yes**; same-person **empty** (`梓梵` ≠ `Cedric`) |
| **O4** | Empty / far-away listed events | success, `hits=[]` |
| **O5** | Fake Google list error | `outcome=failed`; does **not** raise |
| **O6** | `OverlapCheckResult` contract | `outcome`, `time_min`, `time_max`, `hits`, `error_type`, `error_message`, `duration_ms` |
| **O7** | Log boundary | `overlap_check_started` / `overlap_check_completed` (or `_failed`) |
| **O8** | Missing start | `skipped`; **no** list call |
| **L-overlap** | plans + F1 + fake overlapping event | proposal reply contains `Warning:` + overlapping title; pending created; **no** write yet |
| **L-yes** | Existing first-accept write still happens after an overlap warning | `create_event` called once on `yes` |
| **W5** | Full `pytest` green | previous suites unchanged |

Named tests:

- `test_overlapping_interval_and_same_person` → O1  
- `test_adjacent_interval_is_not_overlap` → O2  
- `test_zifan_is_not_cedric` → O3  
- `test_no_listed_events_is_empty` → O4  
- `test_list_error_returns_failed_without_crash` → O5  
- `test_overlap_result_contract_fields` → O6  
- `test_overlap_logs_boundary` → O7  
- `test_missing_start_skips_without_list` → O8  
- `test_create_proposal_includes_overlap_warning` → L-overlap  
- `test_overlap_warning_still_writes_on_yes` → L-yes  

### Non-tests

- Live Google list / live Slack overlap smoke (manual stretch)
- Freebusy API
- Parser aliases / 游水 / missing title
- Hard-block on overlap
- Update / delete
- Slack Block Kit
- LLM
- Inventing emails for names

### Minimum green bar

| Category | Min tests |
|----------|-----------|
| Happy overlap + same-person (O1) | 1 |
| Adjacent / empty (O2, O4) | 2 |
| Alias non-match (O3) | 1 |
| Fake API error + skip (O5, O8) | 2 |
| Contract + logs (O6, O7) | 2 |
| Listener warn + still-write (L-overlap, L-yes) | 2 |
| **Total new** | **~10** |

Plus W5 = full suite still green.

## Logging & retention applicability

| Data | Class | Phase 8 action |
|------|-------|----------------|
| Overlap / listener / confirmation logs | **A** | New overlap boundary events; existing 14d file purge |
| Google Calendar events | **G** | **No** local mirror; list then discard |
| Confirmations | **C** | Unchanged (proposal_text may include warning) |
| Calendar write audit | **B** | Unchanged; Writer not modified |
| Secrets | — | Never logged |

**No new durable store** — nothing to purge beyond existing class A files.

Update [logging-and-retention.md](../docs/logging-and-retention.md) §4 (Availability / overlap) and §8 with a Phase 8 row.

### Resilience write gate (this phase)

- Overlap is **not** a write. Writer gate unchanged (accepted + confirmation_id).
- List failure is observable (`overlap_check_failed`) and **must not** prevent creating a pending confirmation.
- Single list attempt; no retry loop.
- Human still confirms before any create.

## Acceptance Criteria

- [x] This file locked before implementation
- [x] `detect_create_overlaps` reuses `list_calendar_events`; fake client in pytest
- [x] Intersect uses `[start, end)` in `Asia/Hong_Kong`; default +1h (or +1 day all-day); adjacent is not overlap
- [x] Same-person is exact casefold intersect; `梓梵` ≠ `Cedric`; no invented emails
- [x] Warning on proposal; yes still required; no silent hard-block
- [x] List error does not crash; proposal still created with degraded warning
- [x] Omitting calendar client leaves Phase 4b/6 behaviour
- [x] Writer and parser **unchanged** this session
- [x] No local calendar DB; no freebusy API; no LLM
- [x] Tests O1–O8 + L-overlap + L-yes; full pytest; ruff clean
- [x] Architecture, README, `PROGRESS.md`, logging standard updated
- [x] Live smoke **not** required for acceptance

### Manual smoke (opt-in; not pytest)

1. Restart Listener after pull.
2. Ensure a timed event already exists on the target calendar (or create one with the usual yes path).
3. Propose a new create that overlaps that window (same hour, Cedric if testing same-person).
4. Confirm the Slack proposal includes `Warning:` and still asks yes/no.
5. `yes` still creates; `no` does not.

Overlap/conflict listing uses `GOOGLE_CALENDAR_ID` or `primary`. Setting the shared family calendar id is **operator**, not this phase.

## Suggested session order (TDD)

1. Lock this document.  
2. Models + red tests O1–O8.  
3. Green `detect_create_overlaps` + proposal warning.  
4. Thin Listener inject (L-overlap, L-yes).  
5. Docs; `pytest` + `ruff`.  
6. Stop before parser aliases, Writer gates, or freebusy.

## Success definition

Phase 8 is **done** when a create proposal can warn about overlapping (and same-person) events from the injectable calendar client, the family still must say yes to write, and default pytest never touches Google.

## Explicit non-goal

Do not implement freebusy, parser title/alias rules, or a Writer-side overlap refuse “while adding the warning.” One warn on the proposal, green offline tests.
