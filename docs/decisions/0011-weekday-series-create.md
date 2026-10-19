# ADR 0011: Weekday series create via discrete expand (not RRULE day-one)

Date: 2026-10-02. Status: Accepted and implemented (Phase 27, 2026-10-02). Implement-time details
may refine mechanics; product shape below is locked.

## Decision

Bounded weekday-series creates (e.g. Mon–Fri or other weekday sets over a date range
with a daily time window) expand **server-side into N discrete Google Calendar
events**. One Slack confirmation lists the occurrences; the family must reply yes
before any insert (ground rule 6).

Do **not** ship Google `recurrence` / RRULE as the day-one series mechanism. RRULE
remains a later option if Calendar-native series UX is preferred after discrete
expand is stable.

## Consequences

- Overlap runs **per occurrence** morning (or daily) window only — never one
  multi-week spanning `proposed_window` (fixes the Oct 2026 false 撞期 incident).
- Cap **~40** occurrences per confirmation; over cap → clarify / shorten / split,
  never silent truncate. Open-ended “forever” without end date → clarify.
- **Span guard:** a single timed parse with multi-day span must not become a create
  proposal; clarify instead (and/or expand when series intent is clear).
- Writer loops `create_event` with stable per-occurrence ids derived from
  `confirmation_id` + local date (Phase 13 spirit). Partial failure reports counts
  honestly. Optional `series_id` in private extendedProperties for later series-delete.
- Prompt/schema/evals must stop collapsing series cues into one long timed `end`.

See [Phase 27](../../phases/phase-27-weekday-series-create.md).

## Implementation notes

- Cap is `series.MAX_OCCURRENCES = 40`; over cap reports the full count and asks to
  shorten or split.
- Child key `<confirmation_id>#<YYYY-MM-DD>`; `series_id` equals the parent
  confirmation id so a later series-delete can query private properties.
- Overlap lists the union of occurrence days once and intersects per occurrence
  window, avoiding N Google list calls while keeping per-morning semantics.
- The deterministic series policy runs after any parse, so offline rules mode and
  a model that collapses a series both hit the same guard. Text cues are a guard,
  not a growing create vocabulary.
- All-day multi-day spans clarify; weekday series require a daily time window.
