# ADR 0012: Calendar delete-one only via Slack confirmation

Date: 2026-10-02. Status: Accepted and implemented (Phase 28, 2026-10-02). Implement-time details
may refine match UX; safety gates below are locked.

## Decision

Ship **delete-one** first: list/match → Slack confirmation (title + start + short
event id) → Writer `delete` → Google `events().delete`. Never silent delete. Never
delete from the LLM alone. Never auto-delete when a create proposal is rejected.

Series-delete and Calendar **update/patch** stay later phases. Existing OAuth scope
`https://www.googleapis.com/auth/calendar.events` already authorizes delete; **no
new Google scope**.

## Consequences

- Extend `CalendarClient` / Fake / live wrapper with `delete_event`; Writer gains
  `write_calendar_delete` gated on accepted `confirmation_id` (same gate as create).
- Class B audit records `op=delete` attempts and outcomes. Idempotent second yes and
  Google 404 map to soft `already_deleted` / clear Slack text — no listener crash.
- Ambiguous matches require clarify or an explicit pick; zero matches never call delete.
- Architecture / Phase 6 “create-only” wording updates when Phase 28 lands; update
  remains explicitly not started.

See [Phase 28](../../phases/phase-28-calendar-delete.md).

## Implementation notes

- Delete parsing is a deterministic control route; the model never proposes or
  selects a delete target.
- The match window is the single HKT day named in the request. A missing day is
  asked back rather than defaulting to a near-term window.
- Up to nine candidates appear as a numbered pick list; a pick re-proposes one
  target and still needs yes. More matches ask for a time or fuller title.
- `CalendarClient.delete_event` returns False for Google 404/410 (already gone).
