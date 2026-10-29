# ADR 0018: Multi-event create from one text message

Date: 2026-10-29. Status: Accepted and implemented locally (Phase 33a). Amended
2026-10-29 (see "Amendment: the lines are the evidence"). Decisions
locked by Carter on 2026-10-29. Prompt v6 is the default per Carter's decision on
2026-10-29 (merged and deployed without a separate live eval; `CEC_CREATE_PROMPT_VERSION=v5` rolls back).

## Context

A pasted list of five dated lines in `#family-plans` gave one proposal; the other four
lines were dropped without notice. The create path was singular end to end: prompt v5
and its schema return one event or one weekday series, one confirmation holds one
`ParseResult`, and the Writer creates one Google event unless the confirmation is a
series. A probe of the deterministic routes also showed three hazards for pasted lists:
a `行程` / "What's on" header routed the paste to a Calendar list, clock-less exam
lines were stored at once as one merged important date without confirmation, and a
`每日 9:00-17:00` header turned a model create into an open-ended series question.
`SeriesSpec` cannot hold heterogeneous items (one title, one window).

## Decision

1. **One confirmation, N items.** A list is one confirmation whose
   `ParseResult.batch` holds up to 15 `EventItem` values (original 1-based `index`,
   title, effective start/end with the one-hour default already applied, all-day flag,
   place, people). `title/start/end` mirror item 1, as a series mirrors its first
   occurrence. One item stays the single path; one series item stays Phase 27.
2. **Extraction contract.** Prompt `create_event.v6.txt` with a strict schema:
   `intent_type`, `events[]` (`maxItems` 15), `over_cap`, `confidence`; each item keeps
   the v5 fields plus copied (never computed) `date`, `stated_weekday` and
   `source_text`. Each item is mapped and sanitized with the v5 mapper. A v5 payload
   is still accepted. v6 is the default (Carter, 2026-10-29); `CEC_CREATE_PROMPT_VERSION=v5`
   rolls back without a code change. No new model, vendor or provider.
3. **Per-item checks (pure, `event_batch.py`).** Lines that cannot be used are
   skipped and listed under 「未包括 / Not included」 with a reason: `past`,
   `weekday_mismatch`, `crosses_midnight`, `multi_day_all_day`, `repeating`,
   `missing_title`, `missing_date`, `unsupported_script`, `unclear`. Nothing is
   dropped silently. Past dates are never moved to next year.
4. **Date without time.** A line with a date but no clock is never an all-day event
   (unless 全日 / all day is written) and never skipped. Any such line makes the whole
   message one time question listing those lines; no proposal is stored. The family
   resends the message with times. This keeps the existing stateless clarify flow and
   avoids two partial requests from one source message.
5. **Cap.** More than 15 dated lines (checked before any model call) or a model
   `over_cap` → clarify. Never truncate. A provider stop at the token limit → "split
   the list", not "unavailable".
6. **Deselect, then one yes.** Before yes, whole-message thread replies `唔要 3` /
   `剔走 2,4` / `drop 3` remove items and `只要 1,4` / `keep 1,4` keep only those. The
   list is re-posted, still pending, with the original numbers; expiry is not
   extended; removing every item is refused. Bare `不要` stays a reject. One yes
   creates every item shown last. There is no `yes 1,3` and no per-item confirmation.
7. **Fourth Writer entrypoint.** `write_calendar_batch_create` keeps the accepted
   confirmation gate, re-checks every stored item (end after start, timed inside one
   HKT day, all-day one day) and refuses the whole list on any bad item with no Google
   call. Child key `<confirmation_id>#item-NN`; the Google id derivation is unchanged.
   Private properties: child `confirmation_id`, `parent_confirmation_id`,
   `batch_index`; **no `series_id`** (ADR 0011 reserves it for series-delete). One
   class-B audit row per child; a prior success is `already_created` without a Google
   call; a failed child does not stop the loop; counts are honest; a second yes in the
   same thread retries only missing items. The single and series writers refuse list
   confirmations.
8. **Dated-list routing guard.** In the LLM-first control step, a message with two or
   more lines naming a day skips the single-query list-events and add-important-date
   routes, so it reaches the model. Help, delete and important-date listing are
   unchanged. In offline rules mode a dated list gets "send one event per message".
   The guard decides routing only; it is not create vocabulary. `parse()` is unchanged.
9. **Overlap warns only**: one union list over the items' span, intersection per item
   window, plus warnings between items (duplicate, overlap, year check, dated-line
   count larger than understood items).

## Consequences

- Pasted schedules become one reviewable proposal; the family sees every item and
  every skipped line before one yes.
- A time question for one clock-less line blocks the whole list until it is resent.
- Confirmation JSON gains `parse_result.batch`, `parse_result.batch_not_included`,
  `batch_dropped` and stored overlap notes. Readers use defaults, so pre-33a records
  load unchanged. Code from before 33a would read a pending list as a single create of
  item 1: reject or let pending lists expire before a rollback.
- Deleting a whole list (via `parent_confirmation_id`), bulk important dates and
  editing an item before yes remain future scope. Update/patch and series-delete stay
  unimplemented.
- Phase 33b (images) feeds this list path; its provider, trigger and Slack decisions
  are recorded in its phase document and proposed ADR 0019.

See [Phase 33a](../../phases/phase-33a-multi-event-text.md).

## Amendment: the lines are the evidence (2026-10-29)

Context: the first two live lists both answered 「有日期但冇時間」 for five lines that
each wrote a 24-hour clock, and made no proposal. The provider reply is not logged,
so five v6 shapes reproduce that one outcome offline: a null `start`, an all-day
`start` (with or without midnight), and a **correct timed `start` reported with
`missing_fields ["time"]` or `["start"]`**. Decision 2 mapped each item with the v5
mapper, which treats a self-reported gap as authoritative, so the last two shapes
refused every line even though the clock was extracted
(`event_batch.classify_item`: `missing & {"time", "start"}`).

Amendment to decision 2 and decision 4, for list items only:

1. **The family's own line is the evidence for an item's day and clock.** When a
   list item has no usable start, or claims all-day without 全日 / all day written,
   the mapper reads exactly one unambiguous 24-hour clock from that item's own line
   (its verified `source_text`, the single message line naming its day, or — only
   when the dated-line count equals the item count — its line by position). A
   second clock, a range, an AM/PM or 上午/下午 cue, or a written all-day cue keeps
   the model's answer and the time question. A clock is never invented.
2. **A list item may not report a gap it fills.** `missing_fields` entries for a
   field the same item provides are dropped before the per-item checks. An unknown
   name still asks. An all-day claim keeps the time question, because an all-day
   start is midnight, not a stated clock. The single-event payload keeps the
   Phase 21 rule (a self-inconsistent create still asks), because nothing re-reads
   one line there.
3. **A missing day is read the same way** (ISO or `M月D日` only; a slash date is
   day/month in Hong Kong writing and month/day elsewhere, so it is never guessed).
   A yearless day keeps the year of `now`, so decision 3's "never move a passed
   date to next year" still holds and the family sees 「日期已過」.
4. **Diagnostics.** One `batch_item_evidence` line per list carries content-free
   per-item flags and counts (model start, model all-day, model date, self-reported
   gap, verified source line, clock or date read locally, no start) plus the
   correlation id, which now reaches `build_batch_result`. Decision 4's copy also
   names the only reply that works: the whole list again, with a time on every line.

Consequences: a dropped clock no longer blocks a whole list; the Calendar yes gate,
the per-item checks and the 「未包括」 list are unchanged; prompt v6 states the clock
rule positively and keeps a worked example, which is not verified live yet. The
unchanged risk is a model that invents a clock for a clock-less line: the proposal
shows every start and still needs one explicit yes.

## Implementation notes

- Cap `event_batch.MAX_BATCH_ITEMS = 15`; v6 `max_tokens` 2,048; per-model timeout
  stays 15 s.
- `parse_fallback` imports `series` lazily so the medic librarian (which reuses the
  model client) still loads no Calendar-path module.
- Test double `FakePayloadLlmParser` runs the live validation and mapping on canned
  v5/v6 payloads; tests are in `tests/test_multi_event_create.py`.
