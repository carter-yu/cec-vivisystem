# Phase 4b – Slack Confirmation (Proposal + Yes/No)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  
- [Phase 4 core](phase-4-confirmation.md) — reuse `create_confirmation` / `resolve_confirmation` / `build_proposal` / class C store  

This phase wires the existing **Confirmation Guardian** into the **Listener** for `#family-plans`. It does **not** waive system standards. It does **not** write to Google Calendar.

## Goal

When a family message in `#family-plans` parses as `create_event`, the Listener creates a **pending confirmation** and replies with the existing proposal (yes/no ask). A **short** thread reply from the locked vocabulary accepts or rejects that pending row.

`#family-life-notes` stays the Phase 5B path. **No calendar write.**

## Why this phase (decision)

Phase 4 shipped the offline gate. Live `#family-plans` still only posts a parse summary (“No calendar change was made”). Ground rule 6 still blocks Calendar Writer until a human can accept in Slack. This is the same kind of slice as 5B: dispatch into an existing core.

Calendar Writer, freebusy, LLM, and life-note enrichment stay out (one weekend, one gate).

## Time box

1–2 hours. Prefer green offline tests + thin thread yes/no over polished bilingual copy. Do not start Google OAuth.

## In Scope

### Listener (plans channel only)

After normalize + bot/channel filters, for `#family-plans` with an injectable `confirmation_store`:

1. **Expire due** pendings (`expire_due_confirmations`) so an overdue row cannot be accepted.
2. **Yes/no in a thread** (`thread_ts` set and text is locked vocabulary):  
   look up pending by `(channel_id, thread_ts)`.  
   - Found → `resolve_confirmation` (`accept` / `reject`), `actor=user id`. Reply a short ack. **Do not parse.**  
   - Not found → `ignored` / `no_pending_confirmation` (do not parse the word as a calendar event).
3. **Otherwise parse** as today.  
   - `create_event` → `create_confirmation` with `channel_id` and `thread_ts = inbound.thread_ts or inbound.ts`; reply **`proposal_text`** (not the Phase 2 parse-only summary).  
   - `needs_clarification` / `unknown` → existing `format_reply`; **no** pending row.

When `confirmation_store` is omitted (existing Phase 2/5B tests): behaviour unchanged — parse-reply only; no confirmation I/O.

### Thread anchor

Store `Confirmation.thread_ts` as the Slack thread id the family will reply in: `message.thread_ts or message.ts`.  
Lookup uses the inbound `thread_ts` (yes/no is always a thread reply).

### Locked yes/no vocabulary

Match the **entire** trimmed text after Unicode casefold (optional trailing `! . ? 。！？` stripped). No substring match in a long sentence.

| Decision | Tokens |
|----------|--------|
| Accept | `yes`, `y`, `ok`, `好`, `係`, `確認` |
| Reject | `no`, `n`, `不要`, `唔好`, `否` |

Top-level yes/no (**no** `thread_ts`) is **not** a resolve; it goes through parse (usually `unknown`).

### Reply copy

- New pending: existing `build_proposal` text (already asks yes/no; never claims a calendar write).
- Accepted: `Accepted. No calendar write yet (Writer is a later phase).`
- Rejected: `Rejected. No calendar change was made.`

English is enough (Phase 4 proposal is English). Family-facing 已確認 is stretch, not required.

### Socket Mode

- Construct `JsonDirConfirmationStore` (`data/confirmations/`, already gitignored).
- `maintain_confirmation_storage` once on start (expire + class C purge).
- Pass store into `process_slack_message_event`.
- Continue posting `reply_text` in the thread (`thread_ts or ts`).

No new env vars. Same Slack tokens / channel ids as 5B.

### Implementation constraints

- Extend Listener dispatch; **reuse** Phase 4 APIs and stores. Small helpers allowed (`classify_confirmation_reply`, `find_pending_for_thread`) in `confirmation.py`.
- **No** Calendar Writer, freebusy, Google OAuth, LLM, Parser contract change, life-note enrichment, extra channels.
- Structured logs: Listener `dispatch_succeeded` / `dispatch_failed` with `next_component=confirmation`; existing `confirmation_created` / `confirmation_resolved` / `confirmation_timeout`.
- Secrets unchanged (ground rule 13).

### Docs / process

- This file locked **before** code.
- Update architecture §3/§5: Confirmation Slack yes/no done; Writer still not started.
- `PROGRESS.md`, README, Phase 4 stretch checkbox.

## Out of Scope

| Item | Why later |
|------|-----------|
| Calendar Writer / any CUD | Requires accepted confirmation id; separate phase |
| Freebusy / Availability Checker | Explicitly deferred |
| Google OAuth | Only when Writer starts |
| LLM / parser expansion | No new live parse friction in this decision |
| Life-note enrichment | Parallel path; not this gate |
| Fancy proposal UI / buttons | Text yes/no is enough |
| Multi-pending disambiguation UI | Lookup: latest pending in that thread if more than one |

## Unit test plan (locked for implementation)

Authoritative Phase 4b test decision. Implements [unit-testing.md](../docs/unit-testing.md).

### Layout

| Path | Role |
|------|------|
| `tests/test_listener.py` | Y1–Y8 on Listener dispatch (inject in-memory confirmation store) |
| `tests/test_confirmation.py` | Unchanged Phase 4 C1–C10; optional tiny tests for classify/lookup if kept pure |
| In-memory confirmation store | Injected; default pytest never hits Slack or `data/confirmations/` |

Keep Phase 0–5B suites green (Y9 = full `pytest`).

### Determinism

1. Injectable `now=` (`FIXED_NOW = 2026-08-08 12:00 Asia/Hong_Kong`).
2. Injectable `confirmation_store` (`InMemoryConfirmationStore`).
3. Fake Slack events (reuse `_user_message`; set `thread_ts` for yes/no).
4. Real offline `parse` for F1.
5. No network, no tokens, no Google, no LLM.

### Fixtures / cases

| ID | Scenario | Expect |
|----|----------|--------|
| Y1 | F1 in plans channel **with** confirmation store | Pending created; `channel_id` + `thread_ts` set; reply is proposal; `parse_result` is `create_event`; text never claims calendar write |
| Y2 | `needs_clarification` or `unknown` in plans with store | No pending; existing summary reply |
| Y3 | `yes` (or `好`) in thread of Y1 pending | `accepted`; `resolved_by` = Slack user; ack reply; no second pending |
| Y4 | `不要` in thread of pending | `rejected`; no calendar-write claim |
| Y5 | Life-notes message with both stores | Life note stored; **no** confirmation |
| Y6 | Top-level `yes` (no `thread_ts`) | Not resolved; parse path; no crash |
| Y7 | `yes` in a thread with **no** pending | Ignored `no_pending_confirmation` (or equivalent); no new pending |
| Y8 | Wrong channel / bot | No confirmation created |
| Y9 | Phase 2 L1 without `confirmation_store` | Unchanged parse-reply (no disk write, no proposal-only requirement) |

### Named tests

- `test_create_event_creates_pending_confirmation` → Y1  
- `test_non_create_event_does_not_create_confirmation` → Y2  
- `test_thread_yes_accepts_pending` → Y3  
- `test_thread_reject_vocabulary_rejects_pending` → Y4  
- `test_life_notes_does_not_create_confirmation` → Y5  
- `test_toplevel_yes_is_not_a_resolve` → Y6  
- `test_thread_yes_without_pending_is_ignored` → Y7  
- `test_wrong_channel_does_not_create_confirmation` → Y8  

Y9 is existing `test_handle_inbound_create_event_replies` (must stay green).

### Non-tests

- Live Slack Socket Mode  
- Calendar Writer / Google  
- Expire-on-yes beyond calling existing `expire_due` (Phase 4 already tests expire)  
- Slack Block Kit buttons  
- LLM  

### Minimum green bar

| Category | Min tests |
|----------|-----------|
| Create pending from create_event | 1 |
| No pending on clarify/unknown | 1 |
| Accept / reject in thread | 2 |
| Isolation (life notes, top-level yes, no pending, wrong channel) | 3–4 |
| **Total new** | **~7–8** |

## Logging & retention applicability

| Data | Class | Phase 4b action |
|------|-------|-----------------|
| Listener logs | **A** | `next_component=confirmation` on create/resolve dispatch |
| Confirmation logs | **A** | Existing created / resolved / timeout |
| Pending/resolved rows | **C** | Unchanged: terminal+7d, pending max 30d; `maintain_confirmation_storage` on Socket Mode start |
| Calendar | — | **Not introduced** |

No new store class. No new env secrets.

## Acceptance Criteria

- [x] Phase doc (this file) locked before implementation
- [x] `create_event` in `#family-plans` creates pending + proposal reply when store is injected
- [x] Thread yes/no resolves with locked vocabulary; actor stored
- [x] Non-create, life-notes, wrong channel, top-level yes handled without mixing paths
- [x] Omitting `confirmation_store` preserves Phase 2 parse-reply
- [x] Reply text never claims a Google Calendar write
- [x] Unit tests Y1–Y8 (or equivalent) pass; full suite green
- [x] Default pytest offline, secret-free, no Google, no LLM
- [x] Socket Mode wires JSON confirmation store + startup maintain
- [x] Architecture, README, `PROGRESS.md`, Phase 4 stretch note updated
- [x] `uv run pytest` and `ruff check .` clean

### Manual smoke (opt-in; not pytest)

1. Restart Listener with local `.env`.
2. Post F1-style phrase in `#family-plans` → proposal asking yes/no (not only parse summary).
3. Reply `yes` in the thread → accepted ack; **still** no Google Calendar event.
4. Post in `#family-life-notes` → `已記低` only; no confirmation file.

## Suggested session order (TDD)

1. Lock this document.  
2. Red tests Y1–Y8.  
3. Helpers + Listener dispatch + Socket Mode store.  
4. Docs; `pytest` + `ruff`.

## Success definition

A family `create_event` in `#family-plans` becomes a pending confirmation the family can accept or reject in the Slack thread—with zero calendar writes. That accepted `confirmation_id` is the ticket for a later Calendar Writer phase.
