---
date: 2026-10-30 (Asia/Hong_Kong)
branch: feat/phase-33c-batch-series
model: claude-opus-5 via Claude Code
goal: Allow a repeating (weekday series) item inside a multi-event list, so one term timetable becomes one proposal and one yes creates every occurrence
constraints:
  - Implement phases/phase-33c-batch-series.md; no re-scaffolding, no new ADR number
  - Carter's locked decisions (2026-10-30) are the design, not options to revisit
  - CEC_BATCH_SERIES defaults on; off must be Phase 33a behaviour exactly (REASON_REPEATING)
  - Caps: 15 items, 40 occurrences per item, 60 Google events per confirmation; refuse, never truncate
  - One number is one item; deselect removes the whole item with its occurrences
  - Child keys <cid>#item-NN#YYYY-MM-DD; no existing key, Google id or success audit may change
  - Pre-33c stored confirmations must still load and confirm
  - No new model, vendor, provider, Slack scope, dependency or store; no RRULE
  - Runtime Chinese is Traditional (Cantonese) with ASD-STE100 English; synthetic fixtures only
  - Logging carries counts and codes only, no titles, dates or message content
  - No live xAI/Slack/Google call, no Mac Mini, no push, no merge; do not edit PROGRESS.md
acceptance:
  - uv run pytest -q and uv run ruff check . green, with the exact pass count reported
  - Every design item verified PASS or FIXED, each named with the test that covers it
  - ADR 0018 Phase 33c amendment plus cross-reference notes in ADR 0011 and ADR 0019
  - .env.example, docs/maintenance.md (switch + concrete ordered rollback),
    docs/architecture.md, docs/logging-and-retention.md and AGENTS.md updated
  - This archive committed; local commits only; final SHA and deviations reported
---

# Prompt (summary)

## Background

A course notice posted in `#family-plans` with `加event` was refused in full. It had
the shape of a term timetable: one date range, one shared daily time window, and one
class per weekday. Phase 33a decided that a list item carrying a repeating schedule is
not supported, so every line came back under 「未包括」 as 「重複活動，請另外傳送」 and no
proposal was made. The refusal was deliberate, but a term timetable is the normal shape
of a school notice, and the family expected five repeating items.

Phase 33c was designed first (`phases/phase-33c-batch-series.md`), including the option
comparison that chose "series inside the batch" over one Phase 27 proposal per item, a
new confirmation kind, or expanding each class into separate numbered occurrences.
Carter then locked seven decisions. An earlier implementation session committed code and
tests as WIP and stopped at a usage limit; this request covers verification, the
remaining gaps, and all documentation.

## Locked decisions to honour (Carter, 2026-10-30)

1. `CEC_BATCH_SERIES` defaults **on**; `off` is the kill switch and rollback path.
2. Cap of **60** Google events per confirmation, beside 15 items and 40 per series item.
3. School and public holidays are a **warning only** — no holiday calendar, now or as a
   dependency.
4. An already-started timetable **is included**: past dates are skipped, and the copy
   states both the remaining count and how many were skipped.
5. Whole-series delete is a **later phase**; stamp the per-item `series_id` now so that
   phase has a handle.
6. A repeating item with **no end date** is not included, with an actionable
   「請寫明日期範圍」 reason.
7. Mixed one-off and repeating items in one proposal are **allowed**, with 「項」 header
   wording and the event total.

## Work requested

**1. Verify the implemented slice against the design and fix every gap with tests.**
State PASS or FIXED per item, naming the covering test: per-occurrence idempotency keys
and partial retry (a second yes after a mid-write failure writes only the missing
occurrences and no duplicates); the written-count-versus-proposed-count check; all three
caps; the weekday cross-check against the item's own line; the already-started timetable;
a repeating item with no end date; school-holiday wording; a mixed proposal; deselecting
a whole repeating item; the switch default and `off` behaviour; pre-33c stored
confirmations; the synthetic flyer fixture (five classes Monday to Friday over one month
at one shared window, 21 events, the shared header line not an item); and the logging,
script and bilingual-copy rules.

**2. Documentation.** A Phase 33c amendment section in ADR 0018 (the list contract it
changes lives there; no new ADR number), short cross-reference notes in ADR 0011 (the
expander and cap now bound a list item; `series_id` is the item key) and ADR 0019 (image
prompt v2, no schema field added). `.env.example` for the switch. `docs/maintenance.md`
for the switch, the new refusal reasons, the log fields and a **concrete, ordered**
rollback: to revert the code, first reject or expire pending list confirmations, because
pre-33c readers would load a stored repeating item as a single event at its first
occurrence and one yes would then create one event instead of N. `docs/architecture.md`,
`docs/logging-and-retention.md` and `AGENTS.md` where they describe the batch flow.
Do not edit `PROGRESS.md`.

**3. This archive**, in the existing front-matter format, in engineering English and
sanitized.

## Redactions in this archive

The private notice that motivated the phase, its real activity names and the family's
message text are not reproduced. The design document and the tests use a synthetic
flyer that mirrors only the shape (one month, one shared daily window, one class per
weekday) with invented titles. Workshop scratch paths for the original brief are
omitted.

## Result (recorded here as context, not as part of the request)

Implementation matched the design on every checked item. Two gaps were fixed: the
over-cap refusal now states the real number of repeats the written range has
(`NotIncludedItem.detail_count`, serialized with a `.get` default so older records still
load), and the logging test was widened from extraction only to the overlap, narrowing,
write and confirmation boundary lines. No behaviour was changed to make a test pass.
