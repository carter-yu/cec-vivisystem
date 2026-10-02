# Phase 27: Weekday series create (discrete expand + span guard)

Date: 2026-10-02. Scope: one offline design→implementation slice, one PR, no Mini deploy.
Inherits ground rules, unit-testing standard, logging/retention, resilience, architecture.
Builds on Phase 4/4b confirmation, Phase 6 Writer (create), Phase 8 overlap warn,
Phase 13 idempotent write, Phase 24 LLM-first create.
Research: `cec-vivisystem-scratch/research-batch-delete-calendar.md` (not committed).

## Why

Phrase like **「逢星期一至五 8:30–12:00 from Oct 2–30」** collapsed into **one** timed
`ParseResult` spanning Oct 5 → Oct 30. Overlap then scanned the multi-week window and
raised false afternoon 撞期. Intended: **N discrete Mon–Fri mornings**, each
`08:30–12:00`. Root cause: single-event domain/schema/writer/overlap + no multi-day
timed span guard (see research §1).

## Locked product decisions (Carter, 2026-10-02)

| Decision | Choice |
| --- | --- |
| Batch create shape | **B1 discrete expand** — N separate Google events, not RRULE day-one |
| Confirmation | **One** Slack proposal listing occurrences; yes required (ground rule 6) |
| Overlap | **Per-occurrence** morning window only; never one multi-week `proposed_window` |
| Cap N | **~40** recommended (≈ 8 weeks Mon–Fri); over cap → clarify / shorten / split |
| Span guard | Timed create with `end.date() > start.date()` (or equivalent multi-day timed) → **reject / clarify**; do not propose spanning |
| RRULE | **Deferred** |
| Update / patch | **Deferred** |
| Silent create | **Never** |
| Deploy | Offline PR only; Mini pull/restart needs later explicit auth |

## Goal

1. Detect bounded weekday-series phrasing (Mon–Fri and general weekday sets + date
   range + daily time window) and **expand** into N discrete occurrence drafts.
2. Show **one** Slack confirmation that lists occurrences (count + sample + “and N more”
   when long); family must reply **yes** before any Google insert.
3. Run overlap **per occurrence** (e.g. each day `08:30–12:00`); aggregate 撞期
   warnings; warn only (never hard-block).
4. **Hard span guard:** a single timed `ParseResult` that crosses calendar days must
   not become a create proposal — clarify / unknown instead.
5. Cap expansions (~40); refuse open-ended “forever” without UNTIL / end date.
6. On accept: Writer loops `create_event` with stable per-occurrence ids; partial
   failure reports created/failed counts honestly.
7. Docs + prompt/evals; no Mini deploy; no RRULE; no update; no silent create.

## Architecture

### Flow (target)

```text
Slack → parse_with_fallback (LLM-first)
  → series detect / expand (or span-guard clarify)
  → per-occurrence overlap → aggregate warn
  → one confirmation listing occurrences
  → yes → Writer loop create_event (child drafts)
  → Slack ack with created / failed / already counts
```

### Components (expected touch points)

1. **Models / parse** — introduce a series plan shape (occurrences or expander inputs:
   weekday set, range start/end, daily start/end time, title, participants) without
   collapsing to one multi-day timed `start`/`end`. Keep single-event create path.
2. **Prompt + schema** (`create_event.v4` → next versioned prompt) — define weekday-series
   expansion; forbid emitting one long timed span for `逢` / weekday-set + date-range
   cues; span-looking timed → clarify / unknown until expander runs.
3. **Span guard** (sanitize / validation) — timed create with multi-day span →
   `needs_clarification` / refuse proposal. Optional clarify copy: ask whether to
   expand each weekday morning vs one contiguous activity.
4. **Expander** (pure, injectable) — given weekday set + `[range_start, range_end]` +
   daily time window → list of local dates × `{start, end}` in `Asia/Hong_Kong`.
   Cap N; over cap → clarify. No I/O inside pure expand.
5. **Confirmation** — `build_proposal` series variant: title, weekday summary, daily
   window, date range, **N**, sample lines, overflow cue; still never claims a write.
6. **Overlap** — call detect path **once per occurrence** (or batch list covering
   union of days but filter intersections per morning window only). Aggregate hits
   for proposal text. Never pass spanning multi-week interval as the sole proposed window.
7. **Writer** — loop `create_event` for accepted series. Child idempotency:
   stable Google id from
   `sha256("cec-confirmation:" + confirmation_id + "#" + local_date.isoformat())`
   (or equivalent extension of Phase 13 scheme). Stamp private extendedProperties:
   parent `confirmation_id`, occurrence date, optional `series_id` for later series-delete.
   Class B audit **per child** (`op=create`). Partial failure: continue remaining;
   Slack reports counts; never claim full success if any failed.
8. **Listener** — thin wiring: series intent → expand → overlap → one confirmation;
   on yes → series write path. Existing single-event create unchanged when not series.

### Cap and weekday sets

- Default cap **40** occurrences per confirmation (tunable constant; document in ADR).
- Over cap → ask to shorten range or split; do not silently truncate.
- Support general weekday sets (e.g. Mon/Wed/Fri), not only Mon–Fri; Mon–Fri is the
  primary Carter case.
- Open-ended recurrence without end date / UNTIL → clarify (out of scope for silent forever).

### Logging

- `component` names consistent with writer / overlap / parse (e.g. `series_expand_*`,
  `series_write_*`, span-guard clarify events).
- Fields: `occurrence_count`, `created_count`, `failed_count`, `already_count`,
  `confirmation_id`, `correlation_id`, `duration_ms`; never tokens or full bodies.

## In scope

- Span guard for multi-day **timed** single ParseResult.
- Weekday-series detect + discrete expand + one confirmation + per-occurrence overlap.
- Writer loop + per-occurrence idempotency + class B audit per child.
- Prompt / schema / evals for series cues vs spanning collapse.
- Optional `series_id` / occurrence date in private extendedProperties (for later
  series-delete; not consumed by delete-one in Phase 28).
- Offline unit tests (table below); docs / architecture blurb / PROGRESS / prompts archive.
- Short ADR 0011 (or finalize at implement time — see decisions/).

## Out of scope

| Item | Notes |
| --- | --- |
| Google RRULE / `recurrence` on insert | Deferred (B2 later) |
| Calendar update / patch | Separate phase |
| Delete (one or series) | Phase 28 delete-one; series-delete later |
| Silent or LLM-autonomous create | Never |
| Unlimited open-ended recurrence | Cap + end date required |
| Google batch HTTP | Nice-to-have later |
| Overlap warn → hard-block | Unchanged (warn only) |
| Freebusy / Reminder / Observer | Unchanged |
| Important-date edit/delete | Unchanged |
| Mini deploy / new OAuth scopes | `calendar.events` already enough; no Mini |

## Unit test plan (locked before code)

Fixed `now` in `Asia/Hong_Kong` unless noted. Fake calendar + fake confirmation store.
No network. Keep Phases 0–26 suites green. Ruff clean.

| ID | Scenario | Expect |
| --- | --- | --- |
| S0 | Timed create `end.date() > start.date()` | No create proposal; clarify / unknown |
| S1 | Mon–Fri Oct 2–30 2026, 08:30–12:00 | Expand to **21** weekday mornings; none spanning |
| S2 | Proposal text | One confirmation; lists N + sample + overflow cue |
| S3 | Per-occurrence overlap | Afternoon event mid-range does **not** warn every morning; only true morning hits |
| S4 | Cap exceeded (e.g. N>40) | Clarify / refuse expand; no partial silent truncate |
| S5 | Yes → Writer loop | N `create_event` calls; stable child ids; audit per child |
| S6 | Redeploy / second yes | Missing children only; `ALREADY_CREATED` / skip for done children |
| S7 | Partial Google failure mid-loop | Honest created/failed counts; no full-success claim |
| S8 | Single-event create (no series cues) | Existing path unchanged |
| S9 | Weekday subset (e.g. Mon/Wed/Fri) | Expands only those weekdays in range |
| S10 | Series cues but LLM emits long span | Guard or expander wins; no multi-week timed insert |
| S11 | All-day multi-day (if any) | Documented policy: span guard targets **timed**; all-day multi-day stays clarify or explicit later rule — do not silently invent series |

## Acceptance criteria

- [x] Multi-day timed single ParseResult cannot become a create proposal (span guard)
- [x] Bounded weekday series expands to N discrete events; one Slack confirmation lists them
- [x] Overlap is per-occurrence; spanning false 撞期 of the Oct incident does not reproduce
- [x] Cap ~40 enforced with clarify-over-truncate
- [x] Yes writes via confirmation_id only; per-occurrence idempotency; partial failure honest
- [x] No RRULE; no update; no delete; no silent create; no Mini deploy in this PR
- [x] Offline pytest green; architecture / README / PROGRESS / prompts archive updated
- [x] ADR 0011 accepted or explicitly deferred to implement-time note in this file

## Success definition

Family can say a bounded weekday-morning series in Slack, review one list of
occurrences, confirm once, and get N correct morning events — without a multi-week
spanning block or false afternoon overlap spam.

## Implementation notes (for Codex later)

- Prefer current guide model pick for multi-file parse / confirm / writer changes.
- Working tree: `/workspace/cec-vivisystem` on Grok Bot computer.
- Branch naming: e.g. `feat/weekday-series-create`.
- Do not push Mini changes; do not merge without Carter review.
- Ship span guard early in the PR if series expand slips (27a before 27b).

## Open points for Carter (if any remain)

- Exact Traditional Chinese proposal / clarify copy (can lock at implement UX pass).
- Confirm cap **40** vs tighter/looser number.
- Confirm stamping `series_id` in extendedProperties now (recommended yes for later delete-series).
- All-day multi-day policy if it appears in the wild (recommend clarify until explicit product rule).

## Related parked work

- Phase 28: delete-one via confirmation.
- Later: series-delete, RRULE (B2), Calendar update/patch.

## Implementation record (2026-10-02)

Implemented offline on `feat/weekday-series-and-delete` together with Phase 28.
ADR 0011 is accepted; open points resolved per Carter's lock: cap **40**,
`series_id` stamped now, all-day multi-day → clarify, Traditional Chinese copy.

- `series.py`: pure `expand_weekday_series` (cap, no truncation), deterministic
  weekday-set / date-range / daily-window cues, and `apply_series_policy` run by
  the listener after every parse (model or offline rules). Precedence: model
  `series` object → explicit text series → weekday cue + collapsed span salvage →
  clarify. A single timed create crossing HKT days → `span_guard`; all-day span
  longer than one day → `all_day_span`; open-ended / over-cap / invalid → series
  clarifications. Model clarifications stay authoritative.
- Prompt `create_event.v5.txt` + nullable `series` schema object (weekday codes,
  inclusive range, HH:MM window). A payload without `series` is still accepted.
- Proposal: one bilingual message with weekday summary, daily window, range, N,
  first five occurrences and an overflow cue naming the last date.
- Overlap: one list over the union of occurrence days, then intersection per
  occurrence window only (the phase allows union-list + per-window filtering);
  aggregated 撞期 lines are capped at ten.
- Writer: `write_calendar_series_create` loops children with key
  `<confirmation_id>#<YYYY-MM-DD>` (Google id = sha256 of
  `cec-confirmation:` + key), private `parent_confirmation_id`,
  `occurrence_date`, `series_id` (= parent confirmation id); class B audit per
  child; partial failure continues and reports counts; retry inserts missing days.
  `write_calendar_create` refuses series confirmations and timed multi-day spans.
- Tests: `tests/test_series_create.py` covers S0–S11 plus validation, logging and
  garbage-input cases. Eval corpus gained `weekday_series` and `multi_day_span`.
