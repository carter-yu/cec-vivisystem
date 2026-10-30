# ADR 0018: Multi-event create from one text message

Date: 2026-10-29. Status: Accepted and implemented locally (Phase 33a). Amended
2026-10-29 (see "Amendment: the lines are the evidence") and 2026-10-30 (see
"Amendment: repeating items inside a list", Phase 33c; decisions locked by Carter
on 2026-10-30, `CEC_BATCH_SERIES` default `on`). Decisions
locked by Carter on 2026-10-29. Prompt v7 is the default per Carter's decision on
2026-10-30 (`CEC_CREATE_PROMPT_VERSION=v6` or `v5` rolls back without a code change);
v6 was the default from 2026-10-29.

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

## Amendment: repeating items inside a list (Phase 33c, 2026-10-30)

Context: a course notice posted with `加event` listed one month, one shared daily
window and one class per weekday. Decision 1 sends a **lone** series item to Phase
27 and decision 3 refuses a series inside a list (`repeating`), so five classes
refused five times and the whole message answered 「冇一項可以加入日曆」. The refusal was
deliberate, but a term timetable is the normal shape of a school notice, and the
family got nothing. Decisions locked by Carter on 2026-10-30.

Amendment to decisions 1, 3, 7 and 9:

1. **One list, two item kinds.** `EventItem` gains `series: SeriesSpec | None` and
   `occurrence_count: int = 1`. `series is None` is the Phase 33a one-off item,
   unchanged. A series item is **one numbered entry** that writes
   `occurrence_count` Google events. `start`/`end` mirror the first occurrence the
   proposal showed, as a series mirrors its first occurrence. The item's
   occurrences are always its spec expanded (pure, cap 40) and then cut to those
   starting at `start` or later (`event_batch.expand_item`), so the proposal, a
   retry and the write always agree and no clock is read at write time.
   `ParseResult.series` stays None for a list, so the series writer still refuses
   a batch and the list writer still refuses a list-level series.
   **No line evidence is read back for a series item**: the spec owns the day and
   the clock, so `parse_fallback._read_line_evidence` (clock recovery, date
   recovery, self-reported-gap reconciliation) is replaced by
   `_series_item_evidence`, which only records flags. The copied `stated_weekday`
   membership check and the item's own `missing_fields` (item 3) are therefore the
   only deterministic nets on a repeating item.
2. **Kill switch `CEC_BATCH_SERIES`, default `on`** (`series.load_batch_series_enabled`;
   unknown values warn and stay on). With `off` a list item with a series is
   `repeating` again — Phase 33a byte for byte. The switch is the rollback path, read
   once per call site and injected, so `event_batch` stays pure (`allow_series`).
   Create is cheap and delete is expensive, so the protection is the review surface,
   not the switch: the family sees every item, its weekday, window, first and last
   occurrence, 「共 N 次」, the holiday warning and the event total before one yes.
3. **New per-item reasons** (only reachable with the switch on): `series_open_ended`
   (a repeating item with no end date: ask for the range), `series_over_cap` (more
   than 40 repeats, with the real count of the written range) and
   `series_no_occurrences` (the range has none of those weekdays). An all-day claim
   stays `multi_day_all_day`, a midnight-crossing daily window stays
   `crosses_midnight`, a reversed range stays `unclear`, and a weekday written on
   the item's own line that is not in its own weekday set stays `weekday_mismatch`
   (membership, still copied and never computed). **The item's own declared gaps are
   checked before its spec is used**, exactly as on the one-off path: an
   `unconfirmed` item (ADR 0019: a crossed-out, cancelled or 待定 entry is not an
   event) stays `unconfirmed`, and any gap other than `time` / `start` / `end` /
   `series_range` is `unclear`. A shared timetable range is copied into every class
   line, so without this a cancelled weekly class would be promoted to N events.
4. **An already-started timetable is included, trimmed, and says so.** Occurrences
   that have passed are skipped, the proposal shows the remaining count, and one
   warning line names how many were skipped. At most
   `event_batch.PAST_DROPPED_MAX_LINES` (3) items are named; the rest collapse into
   one line with the item and event totals, like the 10-line overlap cap, so the
   block cannot grow with the item count. The written range is never rewritten.
   The whole item is refused (`past`) only when every occurrence has passed. This is
   the one asymmetry with a one-off item, which is simply not included.
5. **New cap: 60 Google events per confirmation** (`event_batch.MAX_BATCH_EVENTS`),
   beside 15 items and 40 occurrences per series item. Over the cap the whole
   message is refused with a bilingual "send it in parts" clarification and no
   proposal. Nothing is ever truncated. The writer re-checks the cap after
   narrowing.
6. **Child keys and `series_id`.** A one-off list child keeps `<cid>#item-NN`
   with no `series_id`. A series item's children are
   **`<cid>#item-NN#YYYY-MM-DD`** and carry `occurrence_date` plus
   `series_id = <cid>#item-NN`. The Google id derivation is unchanged
   (`sha256("cec-confirmation:" + key)`) and the new key has a prefix no existing
   key can produce (Phase 27 children are `<cid>#YYYY-MM-DD`; one-off list children
   end at `item-NN`), so **no historical Google id changes and no success audit is
   invalidated**. Stamping `series_id` per **item** is a narrow, deliberate
   exception to decision 7's "no `series_id`": it is the handle a future
   "remove this repeating item" needs, and it cannot collide with Phase 27 values
   (plain `<cid>`).
7. **Writer defences and honest counts.** `write_calendar_batch_create` re-expands
   every stored series item and refuses the **whole list** with no Google call when
   the count no longer matches `occurrence_count` (`series_count_changed`), the spec
   no longer expands (`series_<reason>`), the item claims all-day (`series_all_day`),
   the remaining items exceed the event cap (`batch_over_event_cap`), or the switch
   is off (`series_disabled` — a visible refusal beats a silent one-event write).
   `CalendarBatchWriteResult` gains `event_count` and `failed_events_by_item`;
   `item_count` keeps its meaning. A second yes in the same thread retries only the
   missing **occurrences**, because the per-child success audit already works per key.
   The prior-success lookup is **one** audit-store scan per write
   (`_successful_creates_by_id`), not one per child: `list_all` reads the whole audit
   directory, and 60 scans of a growing store ran inside the dispatch lock. Every
   child key in one write is distinct, so one scan before the loop is the same
   answer. An `already_created` audit row records **its own** occurrence start, so
   the 90-day trail does not claim every retried occurrence is the first one.
8. **Deselect still removes a whole item**, with all of its occurrences; the re-post
   names the count (「已剔走 / Removed: 第 5 項（4 次）」). Per-date exclusion before yes
   is future scope; until then the family can resend a narrower range or remove one
   occurrence afterwards with the Phase 28 delete-one path.
9. **Overlap (decision 9) is per occurrence.** Still exactly one `list_calendar_events`
   call, but the window covers **every** occurrence of every item, the intersection is
   per occurrence window (Phase 27 semantics inside the list), and a repeating item's
   hits collapse into one line with an example day. Between-item warnings compare
   occurrence windows, and a duplicate now also needs the same series spec. Warn only.
10. **Mixed one-off and repeating items in one proposal are allowed**, with 「項」
    wording in the header because the item count and the event count differ.
11. **Holidays are a warning, not data.** The bot has no school or public holiday
    calendar. One line per proposal states the **repeating items' event total** and
    asks the family to check the dates. It counts events, not days (two items can
    share a day), and it is scoped to the repeating items so a mixed list does not
    print a second total that disagrees with the header. 上學日 expands to every
    weekday in the range.
12. **Prompts v7 (text) and image v2** are the new defaults and add the list-series
    rules: a line that only gives the schedule shared by the whole message and has no
    activity name of its own is **not an item**; the shared range and window are
    copied into every class item; one class line is one item; `source_text` copies the
    class line, never the shared line. No schema field was added, so the exact field-set
    validation shared with the image path is unchanged. Both prompts are inert with the
    switch off. `CEC_CREATE_PROMPT_VERSION=v6` (or `v5`) stays the prompt rollback.

Consequences:

- One yes can now create up to 60 Google events, inside the listener's dispatch lock
  (tens of seconds). The heartbeat and the ADR 0014 stuck guard are unaffected: they
  need disconnected samples, not an idle dispatcher.
- Class B audit rows per message rise from at most 15 to at most 60; the 90-day class
  is unchanged. Class A boundary lines gain counts only (`series_item_count`,
  `series_event_count`, `total_event_count`, `event_count`, `occurrence_count`,
  `removed_event_count`) plus one new `batch_series_refused` line with a reason code.
- Confirmation JSON gains `parse_result.batch[].series`,
  `parse_result.batch[].occurrence_count` and
  `parse_result.batch_not_included[].detail_count`. Readers use `.get`, so **every
  pre-33c record loads unchanged** (`series` → None, `occurrence_count` → 1).
- **Rollback hazard, sharper than the Consequences note above.** Pre-33c code reads
  named keys only, so a stored series item would load as a **single** event at its
  first occurrence and one yes would create 1 event instead of N. Reject or let every
  pending list confirmation expire **before** reverting the code. Setting
  `CEC_BATCH_SERIES=off` needs no code revert and refuses such a pending list visibly;
  see `docs/maintenance.md`.
- Whole-series delete, per-date exclusion before yes, a `stated_weekdays` array for
  multi-weekday cross-checks, RRULE and a holiday calendar all remain future scope.

See [Phase 33c](../../phases/phase-33c-batch-series.md), and the cross-reference notes
in [ADR 0011](0011-weekday-series-create.md) and [ADR 0019](0019-image-event-extract.md).

## Implementation notes

- Cap `event_batch.MAX_BATCH_ITEMS = 15`; v6 `max_tokens` 2,048; per-model timeout
  stays 15 s.
- `parse_fallback` imports `series` lazily so the medic librarian (which reuses the
  model client) still loads no Calendar-path module.
- Test double `FakePayloadLlmParser` runs the live validation and mapping on canned
  v5/v6 payloads; tests are in `tests/test_multi_event_create.py`.
