# ADR 0004: Important dates live in a JSON store, not Calendar

## Status
Accepted

## Context
Phase 15 needs durable family markers (yearly birthdays, one-off exams/trips) plus Slack add/view and a 10:00 next-7-days note. Candidates: tagged all-day Google Calendar events, LifeNotes `raw_text`, or a dedicated store.

Calendar tagging would be a calendar **create** (ground rule 6 confirmation + Writer). LifeNotes is unstructured capture on `#family-life-notes`, not a queryable date catalog.

## Decision
1. Persist important dates as JSON under gitignored `data/important_dates/` (class **F** — until the family deletes).
2. Persist 10:00 occurrence post markers under `data/important_dates_posts/` (class **C**, 30 days).
3. Do **not** call Calendar Writer. Do **not** store these as LifeNotes.
4. Slack add is immediate (no yes/no). Ground rule 6 applies to calendar CUD only.

## Consequences
- Google Calendar remains SoT for timed events (class **G**); important dates are a parallel catalog.
- Add/view work offline in pytest with an in-memory store.
- Delete/edit and a later move to tagged calendar events would need a new phase (and a superseding ADR if the store changes).
