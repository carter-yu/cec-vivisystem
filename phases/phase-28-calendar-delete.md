# Phase 28: Calendar delete-one (list/match → confirmation → delete)

Date: 2026-10-02. Scope: one offline design→implementation slice, one PR, no Mini deploy.
Inherits ground rules, unit-testing standard, logging/retention, resilience, architecture.
Builds on Phase 4/4b confirmation, Phase 6 Writer (extend beyond create-only),
Phase 7 Reader / list, Phase 13 idempotency patterns.
Depends on / follows Phase 27 for weekend ordering preference; not blocked on series
metadata for **delete-one**. Research: `cec-vivisystem-scratch/research-batch-delete-calendar.md`.

## Why

Creates can be wrong (including the spanning-series incident). Family needs a safe
way to **remove one** Google Calendar event from Slack. Today Writer is create-only;
`CalendarClient` has no `delete`; architecture and Phase 26 park amend as a separate
phase. Ground rule 6: **never** silent CUD — delete must use the same confirmation gate.

## Locked product decisions (Carter, 2026-10-02)

| Decision | Choice |
| --- | --- |
| Slice | **Delete-one** (D1): list/match → confirmation → `CalendarClient.delete` |
| Silent delete | **Never** — explicit Slack yes required |
| Series-delete | **Later** (after discrete `series_id` and/or RRULE) |
| Update / patch | **Deferred** (own phase) |
| OAuth scope | Existing **`calendar.events` already enough** — no new Google scope |
| Deploy | Offline PR only; Mini pull/restart needs later explicit auth |

## Goal

1. Parse delete intent (e.g. “刪除星期四游水” / “delete Cedric swim Thu”) without
   writing yet.
2. Search via `list_events` in a sensible window; show **1–K** matches (title + start
   + short event id); if ambiguous → clarify; if none → clear empty reply.
3. On explicit **yes**: call new Writer delete op → Google `events().delete`; class B
   audit; Slack ack.
4. Idempotent second yes / already-gone (404) → soft success (`already_deleted` /
   clear Slack text); never crash the listener.
5. No silent delete from LLM alone; no auto-delete when a create is rejected.
6. Docs + tests; no series-delete; no update; no Mini deploy; no scope change.

## Architecture

### Flow (target)

```text
Slack → parse delete intent
  → list_events in match window → rank / filter matches
  → if 0: empty reply; if >1 ambiguous: clarify / pick list
  → if 1 (or user picks): confirmation proposal (title + start + short id)
  → yes → write_calendar_delete → events().delete
  → Slack ack; class B audit
```

### Components (expected touch points)

1. **Intent / parse** — add delete intent (or structured delete fields) without granting
   write power to the model. Model may propose a delete candidate; **Writer + confirmation**
   remain the only gate. Unsupported / vague → clarify / unknown.
2. **Match** — reuse Reader `list_calendar_events` over a bounded window (e.g. around
   mentioned day / “this week” / default near-term). Match on title substring + date/time
   hints + participants when present. Deterministic ranking; do not invent events.
3. **Confirmation** — proposal lists exact target(s): title, start (HKT), short
   `calendar_event_id`. Vocabulary yes/no unchanged. Proposal never claims delete done.
4. **CalendarClient** — extend Protocol + live client + `FakeCalendarClient`:
   ```text
   delete_event(calendar_id, event_id) -> ...
   ```
   Map Google 404 to soft already-gone. No batch API required.
5. **Writer** — `write_calendar_delete(confirmation, *, client=, ...)`:
   - Refuse unless `status == accepted` and non-empty `confirmation_id`.
   - Refuse missing target event id.
   - Audit every attempt (`op=delete`) class B.
   - Idempotency: second accept for same confirmation / already-deleted audit →
     `already_deleted` without claiming a fresh delete.
6. **Listener** — thin: delete intent → match → confirmation; on first yes → delete
   write. Create / list / important-dates / series-create paths unchanged.
7. **OAuth** — keep `GOOGLE_SCOPES = calendar.events` only. Document that delete is
   already authorized; product gate was missing, not scope.

### Safety gates (required)

- Explicit confirm with **title + start + short event id**.
- No delete from LLM alone; no delete on create-reject.
- Soft-fail Google 404 with clear Slack text.
- Never log tokens; never dump full Google event JSON into logs.

## In scope

- Delete-one vertical slice: intent → match → confirm → `events().delete`.
- Protocol / Fake / live client delete method.
- Writer delete entrypoint + class B audit (`op=delete`).
- Idempotent / already-deleted behaviour.
- Offline unit tests; docs / architecture (Writer no longer create-only for delete);
  PROGRESS / README as needed.
- Short ADR 0012 (or finalize at implement time — see decisions/).

## Out of scope

| Item | Notes |
| --- | --- |
| Series-delete / this-and-following | Later (needs series identity or RRULE) |
| Calendar update / patch title/time | Separate phase |
| Silent or auto delete | Never |
| New Google OAuth scopes | Not required |
| Freebusy / Reminder / Observer | Unchanged |
| Important-date store delete UI | Separate |
| Google batch HTTP | Not required |
| Mini deploy | Later explicit auth |
| Changing overlap to hard-block | Unrelated |

## Unit test plan (locked before code)

Fake calendar + fake confirmation store. No network. Keep prior suites green. Ruff clean.

| ID | Scenario | Expect |
| --- | --- | --- |
| D1 | Unique match → proposal | Shows title + start + short id; no Google delete yet |
| D2 | Yes on accepted confirmation | Exactly one `delete_event` call; audit success |
| D3 | No match | Clear empty / not-found reply; no delete |
| D4 | Ambiguous K matches | Clarify / list; no delete until one target confirmed |
| D5 | Second yes / redelivery | `already_deleted` or skip; no duplicate destructive claim |
| D6 | Google 404 | Soft already-gone Slack text; audit records outcome |
| D7 | Missing confirmation_id / not accepted | Refuse; no Google call |
| D8 | Create-reject path | Does **not** trigger delete |
| D9 | LLM/parser alone | Cannot delete without confirmation accept |
| D10 | Existing create / list / series paths | Unchanged behaviour when not delete |

## Acceptance criteria

- [x] Delete-one requires Slack confirmation with title + start + short event id
- [x] `CalendarClient.delete_event` + Writer delete + Fake coverage
- [x] Ambiguous / empty match never silent-deletes
- [x] Idempotent second yes and Google 404 soft-handled
- [x] Class B audit for delete attempts; no new OAuth scope
- [x] No series-delete; no update; no silent delete; no Mini deploy in this PR
- [x] Offline pytest green; architecture / PROGRESS updated (Writer delete noted)
- [x] ADR 0012 accepted or explicitly deferred to implement-time note in this file

## Success definition

Family can ask to remove one mistaken or finished event, see exactly what will be
deleted, confirm once, and get a clear Slack result — including when the event was
already gone — without any silent Calendar mutation.

## Implementation notes (for Codex later)

- Prefer current guide model pick; keep the slice thin (delete-one only).
- Working tree: `/workspace/cec-vivisystem` on Grok Bot computer.
- Branch naming: e.g. `feat/calendar-delete-one`.
- Do not push Mini changes; do not merge without Carter review.
- Prefer after Phase 27 lands, or same weekend as a thin follow-on if 27 is stable.

## Open points for Carter (if any remain)

- Default list window when the user omits a date (“delete swim”) — e.g. next 14 days
  vs ask for day.
- Traditional Chinese match/clarify/ack copy (lock at implement UX pass).
- Whether delete proposals may show up to K candidates as a numbered pick list in v1
  (recommended yes for ambiguity).

## Related parked work

- Series-delete (discrete `series_id` or RRULE).
- Calendar update / patch.
- Important-date edit/delete.

## Implementation record (2026-10-02)

Implemented offline after Phase 27 on the same branch. ADR 0012 is accepted;
open points resolved per Carter's lock: a missing day is **asked for** (no default
window), ambiguity uses a numbered pick list (up to 9), Traditional Chinese copy.

- Parser control route: leading 刪除 / 刪走 / 取消 / delete / remove / cancel →
  `delete_event` with title/day/clock/participant hints; never reaches the model.
  A model-emitted `delete_event` is sanitized to unknown.
- `calendar_delete.py`: list one HKT day, match by title alias/substring, optional
  clock, participant preference; deterministic order.
- Confirmation: `create_delete_confirmation` stores `delete_candidates`; a thread
  number (`2`, `第2個`) narrows to one and re-proposes; yes with several candidates
  replies "pick a number first" and deletes nothing.
- Writer: `write_calendar_delete` (accepted + id + `delete_event` + exactly one
  target) → `CalendarClient.delete_event`; Google 404/410 → `already_deleted`;
  prior delete audit → `already_deleted` without a call; audit `op=delete`.
- No new OAuth scope; no series-delete; no update.
- Tests: `tests/test_calendar_delete.py` covers D1–D10 plus live-client 404/410
  mapping, store round-trip, pick classifier and logging.
