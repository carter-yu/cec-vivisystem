# Phase 33a: Several events from one text message (list → one proposal → one yes)

Date: 2026-10-28; decisions locked by Carter 2026-10-29. Status: **implemented offline
(branch `feat/phase-33a-multi-event-text`, not deployed); prompt v6 is opt-in until the
authorized live eval**. Scope: one
offline design→implementation slice, one PR, no Mini deploy. Time box: about two
weekend sessions. Inherits ground rules, unit-testing standard, logging/retention,
resilience and architecture. Builds on Phase 4/4b confirmation, Phase 6/13 Writer,
Phase 8 overlap, Phase 24 LLM-first create, Phase 27 weekday series (one confirmation,
N Google events) and Phase 28 numbered pick (narrow, then yes).
Design note (not committed): `cec-vivisystem-scratch/design-multi-event-and-image-extract.md`.
Decision record: [ADR 0018](../docs/decisions/0018-multi-event-create.md). Images are
[Phase 33b](phase-33b-image-event-extract.md), which depends on this phase.

## Why

Carter pasted five dated lines into `#family-plans`. The bot proposed one event and
said nothing about the other four. The create path is singular from end to end:

- Prompt v5 extracts "one proposed calendar event or one bounded weekday series" and
  sends "unrelated multiple schedules" to `unknown`
  (`src/cec_vivisystem/prompts/create_event.v5.txt:1-2`, `:9`). The strict schema has
  one `title/start/end` and no list field (`parse_fallback.py:79-98`, `:103-108`).
- One confirmation per Slack message (`confirmation.py:409-470`); the proposal is built
  from one `ParseResult` only (`confirmation.py:206-246`). Nothing reports dropped lines.
- One Google event per confirmation unless it is a weekday series
  (`calendar_writer.py:562-758`). Series child keys are per date
  (`calendar_writer.py:761-763`), so two list items on the same day would collide.

A probe of today's deterministic routing with synthetic pastes found three more hazards:

| Paste (synthetic) | Today | Cause |
| --- | --- | --- |
| Header `培訓行程：` or `What's on…`, then dated lines | `list_events` (a Calendar read, no create) | `_LIST_SIGNAL` matches anywhere (`parser.py:261-265`) |
| `11月6日 中文考試` / `11月13日 英文考試` (no clock) | Stored at once as **one** important date titled `中文考試 11月13日 英文考試`, no confirmation | `parser.py:730-785` |
| Header `課程（每日 9:00-17:00）：`, then dated lines | A model create becomes `series_open_ended` | Whole-text series cue (`series.py:366`, `:388-396`) |

`SeriesSpec` cannot carry a list: one title, one daily window, generated days
(`models.py:31-45`). A heterogeneous list needs its own item type.

## Locked decisions (Carter, 2026-10-29)

1. **Deselect before writing.** The family can remove extracted items before yes:
   `唔要 3` / `剔走 2,4` / `drop 3`, or keep only some: `只要 1,4` / `keep 1,4`. The bot
   re-posts the list (still pending, original numbers kept). One yes creates every item
   shown last; there is no `yes 1,3`.
2. **Unusable lines are skipped**, never silently: they are listed under
   「未包括 / Not included」 with a reason (past date, weekday mismatch, crosses
   midnight, repeating schedule, no title, no date, Simplified script, unclear). If no
   line is usable, the bot replies with the reasons and stores no confirmation.
3. **A date with no time gets a clarify question asking for the time.** It is never
   created as an all-day event (unless 全日 / all day is written) and never skipped
   silently. Combination rule (simplest safe design, consistent with the existing
   clarify flow, which stores no state): when any line has a date but no time, the
   whole message gets one time question listing those lines (and any other
   not-included lines) and **no proposal is posted**. The family adds the times and
   sends the whole message again. Usable lines are not proposed separately, so one
   source message never yields two partial requests.
4. **Images (Phase 33b) are read only when attached together with the message text
   `加event`.**
5. **Images go raw to xAI Grok vision** (metadata stripped, nothing stored); no OCR on
   the Mini. Phase 33b stays docs-only in this PR.

Implementation defaults (from the design, unchanged by Carter):

| Topic | Default |
| --- | --- |
| Item cap | **15** per message; more than 15 dated lines or `over_cap` → clarify, never truncate |
| Missing end | One-hour default per timed item, stored at proposal time and shown |
| Past dates in a list | Not included (`past`); never moved to next year |
| Repeating series inside a list | Not included (`repeating`); a series-only message keeps Phase 27 |
| Child key | `<confirmation_id>#item-NN` (original number, two digits) |
| Private properties | Child `confirmation_id`, `parent_confirmation_id`, `batch_index`; **no `series_id`** |
| Dated-list routing guard | ≥ 2 lines with a date → skip the list and important-date control routes (model configured); offline mode → "send one per message" |
| Prompt | v6 (`events[]`, `max_tokens` 2,048) is opt-in with `CEC_CREATE_PROMPT_VERSION=v6`; v5 stays the default until the authorized live eval; per-model timeout stays 15 s |

## Goal

1. Extract every event in one message (prompt v6, `events[]`, 1–15 items) and validate
   each item with today's per-event rules plus list rules.
2. Route by count: one item → **today's single path, unchanged**; one item that is a
   series → **Phase 27, unchanged**; otherwise one list proposal.
3. One bilingual proposal lists every item (number, date with weekday, effective window
   or 全日, place, people, default-end marker), the not-included lines with reasons,
   and overlap warnings. Never claims a write.
4. Optional narrowing replies before yes; yes creates exactly the last list shown.
5. Writer: one Google event per item with a stable per-item key, one audit row per item,
   honest partial-failure counts, retry of missing items only.
6. Close the three routing hazards above with a deterministic dated-list guard.

## Non-goals

- No write without yes; no `yes 1,3`; no per-item confirmations.
- No update/patch, list-delete, series-delete or RRULE.
- No weekday series inside a list; no multi-day all-day items.
- No bulk important dates (a dated list never reaches the important-date store).
- No redaction change for typed text (parity with today: `parse_fallback.py:287`).
- No change to offline `parse()` (rules-only, one event).
- No images (Phase 33b). No new store, scope, dependency or vendor.

## Architecture

### Flow (target)

```text
Slack text in #family-plans
  ├─ thread reply? confirmation yes/no → (existing) ; delete pick → (existing)
  │                 list narrowing (唔要 3 / keep 1,4) → re-propose, still pending   [new]
  └─ parse_with_fallback (LLM-first)
       ├─ parse_control (help / delete / list / important date)
       │     └─ dated-list guard: ≥2 dated lines → skip list + important-date routes  [new]
       ├─ model, prompt v6 → events[] (≤15) → per-item map + sanitize               [new]
       └─ per-item validation → included / not included (+ warnings)                [new]
  route: 1 item → single path (unchanged) │ 1 series → Phase 27 (unchanged)
         │ ≥2 or any not-included → list path
  list path: union overlap list → per-item + between-item warnings → one confirmation
  yes → write_calendar_batch_create → N × create_event (#item-NN) → ack with counts
```

### Data model (`models.py`)

- `EventItem` (frozen): `index` (original 1-based number), `title`, `start`, `end`
  (effective exclusive end, default applied and **stored**), `all_day`, `location`,
  `participants`, `end_defaulted`, `source_text` (verified excerpt or None),
  `image_index` (33b; None here).
- `NotIncludedItem` (frozen): `reason`, `title`, `source_text`.
- `ParseResult.batch: tuple[EventItem, ...] | None` and
  `ParseResult.batch_not_included: tuple[NotIncludedItem, ...]`. `title/start/end`
  mirror item 1, as a series mirrors its first occurrence (`models.py:72-74`).
- `Confirmation.batch_dropped: list[int]` (narrowing state, like `delete_candidates`).
- `CalendarEventDraft.batch_index: int | None`; new `CalendarBatchWriteResult`
  (`item_count`, `created_count`, `already_count`, `failed_count`, `failed_indices`,
  `children`, errors, `duration_ms`).
- Confirmation JSON gains `parse_result.batch`, `parse_result.batch_not_included` and
  `batch_dropped`; readers use `.get`, so pre-33a records load unchanged
  (`confirmation.py:803-832`).

### Extraction (prompt v6, `parse_fallback.py`)

- New `create_event.v6.txt` and schema: top-level `intent_type`, `events[]`
  (`maxItems` 15; xAI enforces `maxItems` up to 256), `over_cap`, `confidence`. Each item
  keeps the v5 field names plus `stated_weekday` (MO..SU or null, copied from the text)
  and `source_text` (≤ 80 characters, copied).
- Map and sanitize **per item** with the existing `validate_create_payload`,
  `llm_payload_to_parse_result` and `_sanitize_llm_result` logic
  (`parse_fallback.py:103-130`, `:477-544`, `:613-667`).
- Accept a v5-shaped payload as a one-item list, so switching the prompt back to v5 is
  a rollback without code changes.
- `finish_reason == "length"` → `list_too_long` clarification, not "temporarily
  unavailable".
- v5 stays in the repo and stays the default. v6 is selected with
  `CEC_CREATE_PROMPT_VERSION=v6` (`parse_fallback.load_prompt_version`) and becomes the
  default only after the eval gate (below). The `events[]` mapping, list proposal,
  narrowing and writer work with either prompt; v5 simply never returns a list.

### Per-item validation (new pure module, e.g. `event_batch.py`)

Not-included reason codes, each with Traditional Chinese + English copy:
`missing_title`, `missing_date`, `missing_time`, `crosses_midnight` (span guard rule,
`series.py:482-485`), `multi_day_all_day` (`series.py:488-491`), `repeating` (item has
`series`), `weekday_mismatch` (`stated_weekday` ≠ date), `past` (before today HKT),
`unsupported_script`.

Warnings (item stays included): duplicate items; items that overlap each other; "check
the year" when an item is more than 180 days after the earliest item; dated-line count
larger than understood items.

`looks_like_dated_list(text)`: at least two non-empty lines contain a date token
(`11月6日` / `號`, `2026-11-06`, `6/11`, English month name + day, or a weekday word).
It decides routing only; it is not create vocabulary.

### Routing (`listener.py`, `parser.py`)

- After parse: over cap → clarify; any date-only line (`missing_time`) → one time
  question for the whole message, no proposal (locked decision 3); 0 included → clarify
  with reasons; one item and no
  not-included lines → the existing single path (including `apply_series_policy` on the
  whole text); one series item alone → Phase 27; otherwise the list path, which does
  **not** run whole-text series cue detection.
- Dated-list guard inside the LLM-first control step: skip `_try_list_events` and
  `_try_add_important_date` when `looks_like_dated_list` is true. Help and delete are
  unchanged. Offline `parse()` is unchanged; when no model is configured and the text is
  a dated list, the listener replies "一次加多個活動需要 AI 理解服務，請逐個傳送 / Lists of
  events need the event-understanding service; send one event per message."
- `_write_for_accepted` checks `batch` before `series` (`listener.py:1163-1189`).
- Narrowing handler runs where the delete pick runs (`listener.py:978-984`), only when
  the latest pending confirmation in the thread is a list.

### Proposal and replies (`confirmation.py`)

Example (synthetic, clock 2026-10-28 HKT):

```text
請確認以下 5 個活動（會分開建立 5 個日曆活動）/ Please confirm these 5 events (5 separate calendar events):
1. 2026-11-06（五）09:00–17:00 急救課程 Day 1
2. 2026-11-13（五）09:00–17:00 急救課程 Day 2
3. 2026-11-20（五）09:00–10:00 急救考試（冇講結束時間，預設 1 小時 / no end time: 1 h default）
4. 2026-11-27（五）14:00–17:00 複習 · 地點 Location: Room 12
5. 2026-12-04（五）全日 實習 · 參與者 Participants: Elaine
未包括 / Not included (1):
• 「12月11日 結業禮」— 冇時間 / no time given
注意：第 4 項撞期 1 個現有活動 / Warning: item 4 overlaps 1 existing event:
• 2026-11-27 14:30–15:30 Piano (Cedric)
回覆 yes 確認建立以上 5 個活動；回覆「唔要 3」剔走第 3 項；回覆 不要 取消。
Reply yes to create all 5, "drop 3" to remove item 3, or no to cancel.
No calendar change will be made until you confirm.
```

Narrowing rules: whole-message replies; verbs `唔要` / `不要` / `剔走` / `剔除` / `drop` /
`remove` / `skip` and `只要` / `淨係要` / `keep` / `only`; numbers 1–15 (full-width
accepted) separated by `,` `，` `、` or spaces, optional `第…個/項`. Numbers keep the
original numbering. Removing every item is refused. A narrowing reply on an accepted
list changes nothing and says so. Expiry is not extended. Bare `不要` stays a reject
(`confirmation.py:70-72`). A single digit stays a delete pick and does nothing in a list
thread (`listener.py:1037-1042`).

Ack after yes (counts, failed item numbers and titles; Google-login note as today):
「已確認。已建立 5 個活動。/ Accepted. 5 calendar events created.」, or 「已確認，但 1/5 個
活動未能建立：第 3 項（2026-11-20 急救考試）。可以喺呢個 thread 再回 yes，只會重試未完成嘅
項目。/ Accepted, but 1 of 5 events failed: item 3. Reply yes in this thread to retry
only the missing items.」

### Writer (`calendar_writer.py`)

- New `write_calendar_batch_create`; same gates as the series writer
  (`calendar_writer.py:792-823`) plus per-item defence: start present, end after start,
  timed item inside one HKT day, all-day item one day.
- Child key `<confirmation_id>#item-NN`; Google ID derivation unchanged
  (`sha256("cec-confirmation:" + key)`, `calendar_writer.py:1293-1295`); the 409
  ownership check covers lost responses (`calendar_writer.py:386-397`).
- Private properties: child `confirmation_id`, `parent_confirmation_id`, `batch_index`.
  No `series_id` (reserved by ADR 0011 for series-delete).
- Loop in item order, skip dropped items, continue after a failure, one class-B audit
  row per child, prior success → `already_created` without a Google call
  (`calendar_writer.py:1150-1167`).
- `write_calendar_create` and `write_calendar_series_create` refuse confirmations that
  carry a batch.
- AGENTS.md "Contracts to preserve" lists this as the fourth Writer entrypoint (ADR 0018).

### Overlap (`overlap.py`)

`detect_batch_overlaps`: one union list over the items' span, intersection per item
window (pattern of `detect_series_overlaps`, `overlap.py:141-233`), plus pure checks
between items. Warn only; ≤ 10 lines plus "and N more"; a failed list keeps today's
"could not check" warning.

## Logging and retention applicability

- Class A (14 days): `batch_extract_completed` (item, included, not-included counts by
  reason; model; prompt version/hash; tokens; latency), `batch_overlap_check_*`,
  `batch_edit_applied` (counts), `batch_write_started` / `batch_write_completed`
  (created/already/failed counts). Child writes reuse `write_*` events with the child
  key. No item lists or full message text at INFO beyond the existing 80-character
  preview.
- Class B (90 days): one audit row per child (≤ 15 per message).
- Class C (terminal + 7 days): confirmations now hold the item list; same purge.
- No new store, no class F data.

## Unit test plan (locked 2026-10-29)

Implemented in `tests/test_multi_event_create.py` (end-to-end through
`process_slack_message_event` with `FakePayloadLlmParser` and `FakeCalendarClient`).
The table below is the full plan; rows not yet covered there are tracked as follow-up
in PROGRESS.

Fixed HKT clock, `FakeLlmParser` returning canned v6 payloads, `FakeCalendarClient`,
in-memory or temporary stores. No network. Phases 0–32 suites stay green; Ruff clean.

| ID | Area | Case | Expect |
| --- | --- | --- | --- |
| E1 | extract | v6 payload with one complete item | Same `ParseResult` and the same proposal text as today's single path |
| E2 | extract | v6 payload with 5 items | `batch` of 5; `title/start/end` mirror item 1 |
| E3 | extract | `over_cap: true`, or 16 items, or 16 dated lines | Over-cap clarification; no confirmation; nothing truncated |
| E4 | extract | Extra fields, wrong types, `events` not a list, item missing fields | Validator rejects; failover; then the unavailable reply |
| E5 | extract | `finish_reason == "length"` | `list_too_long` clarification, not "unavailable" |
| E6 | extract | v5-shaped payload | Mapped as a one-item list (rollback path) |
| V1 | validate | One item per reason code (9 codes) | Item not included with that code and its copy |
| V2 | validate | `source_text` not in the message | Shown as title only |
| V3 | validate | Participant not named in the message | Dropped (existing rule), per item |
| V4 | validate | Duplicate items; two items overlapping each other | Warning lines; both included |
| V5 | validate | Item 200 days after the earliest | "Check the year" warning |
| V6 | validate | 6 dated lines, 4 items understood | Honesty warning with both numbers |
| V7 | validate | Timed item without end; all-day item | Stored end = start + 1 h with marker; next midnight exclusive |
| R1 | route | One item, nothing excluded | Existing single proposal, byte-identical |
| R2 | route | One series item alone | Existing Phase 27 proposal, byte-identical |
| R3 | route | Two or more included items | List proposal |
| R4 | route | One included + one not included | List proposal showing the not-included line |
| R5 | route | Zero included | Clarification with reasons; no confirmation stored |
| R6 | route | Header `課程（每日 9:00-17:00）：` + dated lines | List proposal, not `series_open_ended` |
| R7 | guard | Header `培訓行程：` / `What's on…` + dated lines | Reaches the model; list proposal; no list reply |
| R8 | guard | Clock-less exam lines | Never `add_important_date`; important-date store untouched |
| R9 | guard | One-line list query, one-line important date, `help`, `刪除星期四游水` | Unchanged routes |
| R10 | guard | No model configured + dated list | "Send one per message" reply; no confirmation |
| P1 | proposal | 5 items with defaults, place, people, all-day, not-included, overlap | Matches the template above |
| P2 | proposal | Rendered copy | No Simplified markers |
| P3 | proposal | 15 items + 10 not-included + 10 overlap lines | Under 4,000 characters; overflow lines present |
| P4 | proposal | Any list proposal | Never claims a write |
| N1 | narrow | `唔要 3` / `drop 3` | Item 3 removed; re-proposal keeps numbers 1, 2, 4, 5; still pending; no Calendar call |
| N2 | narrow | `只要 1,4` / `keep 1,4` | Only items 1 and 4 remain |
| N3 | narrow | `2,4` `2、4` `2 4` `第2個` full-width digits, `12` | Parsed correctly |
| N4 | narrow | Unknown, out-of-range or already removed number | Helpful reply; state unchanged |
| N5 | narrow | Remove every item | Refused; state unchanged |
| N6 | narrow | After acceptance; after expiry | "Already accepted" / "expired"; no write |
| N7 | narrow | Bare `不要`; bare `2` in a list thread | Reject; no delete, no write |
| N8 | narrow | Latest pending in thread is a single or series create | Not handled; falls through as today |
| W1 | writer | Yes on 5 items | 5 `create_event` calls in order; keys `#item-01…05`; Google ids from the keys; parent + `batch_index`; no `series_id` |
| W2 | writer | Same | One audit row per child, `op=create` |
| W3 | writer | Item 3 dropped before yes | Item 3 never written |
| W4 | writer | Fake fails call 2 | FAILED with honest counts; ack names item 2 |
| W5 | writer | Second yes after W4 | Only item 2 attempted; others `already_created` without Google calls |
| W6 | writer | Same child key across retries | Same Google id in the request body |
| W7 | writer | Not accepted, no id, no batch, all dropped, stored item crossing days | REFUSED, no Google call, audit row |
| W8 | writer | Single and series writers given a list confirmation | REFUSED |
| W9 | writer | Google auth error | Ack carries the login-expired note |
| C1 | store | JSON round trip with items, not-included, dropped | Equal after load |
| C2 | store | Pre-33a confirmation file | Loads unchanged |
| C3 | store | Same Slack message delivered twice | Same confirmation; item list unchanged |
| O1 | overlap | Morning items, afternoon existing event | One list call; no false warnings |
| O2 | overlap | List call fails | "Could not check" warning; yes still allowed |
| O3 | overlap | 12 hits | 10 lines + "and 2 more" |
| G1 | logs | Extraction, edit, write | Events with counts; no item titles beyond existing writer fields; no full message text |
| X1 | garbage | Null/str/nested junk payloads, 10,000-character title | Clarification; no crash |
| X2 | garbage | Garbage narrowing text | Not handled; no crash |

Non-tests: live model accuracy, latency and cost (the authorized live eval below);
Slack threading and delivery; Google behaviour beyond the fakes and the existing 409
tests; Mini deployment.

## Evaluation (synthetic; live calls need explicit authorization)

- Extend `scripts/evaluate_create_parser.py` to score `batch` items (date, start, end,
  title per item; not-included reason codes).
- Add synthetic cases to `evals/create_cases.json`: five Chinese lines with times; three
  English lines; a one-line list (「星期一 3點游水，星期三 4點鋼琴」); the `行程` header;
  the `每日` header; clock-less exam lines; 16 items (over cap); a list with one series
  line; a weekday mismatch; a past item.
- Gate for switching to v6: the 18 existing cases give the same results; every list case
  passes; record p50/p95 latency and tokens for the 15-item case.

## Acceptance criteria

- [ ] A five-line synthetic paste gives one proposal listing five items; one yes creates
      five events with keys `#item-01…05`, one audit row each
- [ ] Single-event and weekday-series proposals and writes are unchanged (golden tests)
- [ ] Unusable lines are listed with reasons; nothing is dropped silently; over cap
      never truncates
- [ ] Narrowing re-proposes and never writes; yes writes exactly the last list shown
- [ ] Partial failure reports honest counts; retry creates missing items only
- [ ] The three routing hazards are fixed; help, delete and single-line list/date routes
      are unchanged
- [ ] No `series_id` on list items; no new scope, store or dependency
- [ ] Offline pytest green, Ruff clean, boundary logs present; ADR 0018, architecture,
      AGENTS (fourth Writer entrypoint), README help, maintenance, logging-and-retention,
      PROGRESS and the prompt archive updated
- [ ] Live eval run with authorization before v6 is the default prompt

## Rollout (authorized separately)

Mini: `git pull`, `uv sync --locked`, restart the listener (control app or
`launchctl kickstart -k`). Smoke in `#family-plans` with a synthetic two-item list far
in the future: reply `不要` once, send again and reply yes, check two events in Google
Calendar, then remove them with delete-one. Rollback: reject or let expire any pending
list proposals first (pre-33a code would read one as a single create of item 1), then
revert.

## Remaining risks

- Live model accuracy, latency and token use for 15-item lists are unmeasured until the
  authorized eval; v6 stays opt-in until then.
- A time question for one clock-less line blocks the whole list; the family must resend
  the whole message with times (chosen for simplicity; revisit after real use).
- Rollback with pending list proposals: reject or let them expire before reverting the
  code (pre-33a code would read one as a single create of item 1).

## Related / later

- [Phase 33b](phase-33b-image-event-extract.md): images feed this list path.
- Later: delete a whole list via `parent_confirmation_id`; bulk important dates;
  editing an item before yes (only removal is in scope).
