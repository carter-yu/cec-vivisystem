# Phase 33c: Repeating items inside a multi-event list (and image)

Date: 2026-10-30. Status: **implemented locally** on branch
`feat/phase-33c-batch-series` (design commit from `main@1f620ab`, then one
implementation slice). Decisions locked by Carter on 2026-10-30 (see "Locked
decisions"); `CEC_BATCH_SERIES` ships **on**. Offline tests and Ruff are green;
no deployment, no live model call and no Mini work is claimed. The live eval
below is still outstanding. Inherits ground rules, the
unit-testing standard, logging/retention, resilience and architecture. Builds on
Phase 27 weekday series (ADR 0011), [Phase 33a](phase-33a-multi-event-text.md) list
create (ADR 0018) and [Phase 33b](phase-33b-image-event-extract.md) image extract
(ADR 0019). Proposed decision record: an **amendment to ADR 0018** plus cross-reference
notes in ADR 0011 and ADR 0019 (see "Decision record" below).

## Why

A course notice posted in `#family-plans` with `加event` was answered
「呢個訊息入面冇一項可以加入日曆。/ No event in this message can be proposed.」, with six
lines listed under 「未包括」 as 「重複活動，請另外傳送 / repeating schedule: send it as its
own message」. The notice had the shape of a term timetable: one month window, one shared
daily time window, and one class per weekday. The family expected five repeating items
(21 events); it got nothing.

The refusal is deliberate, not a bug. Phase 33a decided that a list item carrying a
series is not supported, and the check is explicit:

- `event_batch.classify_item` returns `REASON_REPEATING` for any item with a series
  (`src/cec_vivisystem/event_batch.py:304`: `if item.has_series or result.series is not
  None:`). With every item refused, `build_batch_result` returns the
  `batch_none_included` clarification (`event_batch.py:189-269`, copy at `:502-509`).
- `EventItem` is one occurrence: one `start`, one `end`, one `all_day`
  (`models.py:58-80`). `SeriesSpec` carries a weekday set, an inclusive date range and a
  daily window, but no title, place or people (`models.py:31-46`), so it cannot hold a
  heterogeneous list either.
- Routing sends a **lone** item with a series to Phase 27 and two or more items to the
  list path (`parse_fallback.py:834-839`). Five classes are five items, so Phase 27 is
  never reached.
- `series.apply_series_policy` returns early for a list (`series.py:368-373`), which is
  correct and now also necessary: the shared 「逢…（星期一至五）…」 header must not turn the
  whole list into one series clarification.
- The two writers refuse each other's shape: `write_calendar_series_create` refuses a
  confirmation that carries a batch (`calendar_writer.py:836-839`) and
  `write_calendar_batch_create` refuses one that carries a list-level series
  (`calendar_writer.py:1041-1043`).
- The image prompt has **no** series rules at all
  (`prompts/create_event_image.v1.txt`), although `series` is in every item of the shared
  schema (`parse_fallback.py:84-104`, `:154-181`). The text prompt v6 does have a series
  section (`prompts/create_event.v6.txt`), and it tells the model to fill `series` for a
  repeating item — which the list path then refuses. So a typed term timetable fails the
  same way.

The sixth not-included line was most likely the shared header (the weekday set and the
daily window with no activity name of its own). Raw model replies are not logged, so this
stays unconfirmed; the design must be correct whether or not the model returns it.

Verified offline on this branch, with the flyer's window: `extract_weekdays("逢上學日（星期
一至五）…")` gives `(0,1,2,3,4)`, `extract_date_range` gives `2026-11-02 … 2026-11-30`,
`extract_time_window` gives `15:15–16:15`, and five single-weekday specs expand to
**5 + 4 + 4 + 4 + 4 = 21** occurrences. A bare 「（星期一）」 is **not** a cue
(`extract_weekdays` returns None, `series.py:190-215`), so a class line alone cannot be
recovered deterministically; the weekday must come from the model's copied field.

## Options

| Option | Shape | Verdict |
| --- | --- | --- |
| **A** | Series inside the batch: one combined proposal, items are one-off or series blocks, deselect per item, one yes writes all, child keys `#item-NN#YYYY-MM-DD` | **Chosen** |
| B | One Phase 27 series proposal per item in the same thread (N yes replies) | Rejected |
| C | Prompt-only plus clearer copy | Rejected as a solution; one part kept |
| D | Ask the family to resend one class per message (today's copy, better worded) | Rejected |
| E | Expand each series item into K ordinary `EventItem` values in the same batch | Rejected |
| F | A new `batch_series` confirmation kind beside the list | Rejected |

**B breaks two locked contracts.** A confirmation id is a uuid5 of the channel plus the
source message (`confirmation._confirmation_id_for`, `confirmation.py:598-607`), so one
Slack message has exactly one id; five proposals would need synthetic suffixes and
Slack redelivery would no longer reuse the same authorization (AGENTS: "Keep Slack
source-derived IDs ... stable. Reuse terminal confirmations on redelivery"). Worse,
`find_pending_for_thread` returns the single latest pending confirmation in a thread
(`confirmation.py:483-531`), so one `yes` would resolve one of five proposals and the
family could not tell which. Five 24-hour pending records from one message also multiply
the expiry and overlap work. B is more code **and** less safe.

**C cannot work alone.** No prompt wording clears `classify_item`'s refusal
(`event_batch.py:304`). Telling the model to emit one item per occurrence instead would
need 21 items against a cap of 15 (`event_batch.MAX_BATCH_ITEMS = 15`) and would make the
model compute weekday arithmetic, which ADR 0011 and both prompts forbid ("never compute
it"). The useful part of C — the shared-header rule and the per-item range/time copy — is
kept as the prompt work inside A.

**E (expand into ordinary items) is attractive but loses the unit of review.**
`EventItem.index` is both the number shown to the family and the number in the child key
(`models.py:58-80`, `calendar_writer.batch_child_key:976-982`). Expanding gives 21 numbered
lines, and removing one class means 「唔要 2,7,12,17,21」. Keeping group numbers while
storing 21 items would split the display number from the key number — exactly the
coupling ADR 0018 decision 6 protects. **F** duplicates the whole list path (proposal,
narrowing, serialization, writer) and has no answer for a message that mixes a one-off
and a repeating item.

**A is chosen** because it reuses every existing seam: one confirmation per source
message, one number space, one yes gate, one narrowing grammar, one audit row per Google
event, and the already-reviewed pure expander. The new surface is one optional field on
`EventItem`, one extra key level, and the copy.

## Locked decisions (Carter, 2026-10-30)

1. **One list, two item kinds.** `EventItem` gains `series: SeriesSpec | None` and
   `occurrence_count: int = 1`. `series is None` is today's one-off item, byte for byte.
   A series item is one numbered entry that writes `occurrence_count` Google events.
   Its occurrences are **its spec expanded and then cut to the occurrences starting at
   `item.start` or later**, so the stated range is never rewritten, the proposal and the
   write always agree, and no clock is read at write time (`event_batch.expand_item`).
2. **The list-level `ParseResult.series` stays None.** Only items carry specs, so the
   series writer keeps refusing a list and the list writer keeps refusing a list-level
   series (`calendar_writer.py:836-839`, `:1041-1043`).
3. **Expand once, reuse everywhere.** `expand_weekday_series` (pure, cap 40) is called
   once per proposal; the resulting windows feed the copy, the overlap check and the
   between-item warnings. The writer re-expands and refuses if the count changed.
4. **Caps:** 15 items (unchanged), 40 occurrences per series item (unchanged,
   `series.MAX_OCCURRENCES`), and a new **60 events per confirmation**
   (`event_batch.MAX_BATCH_EVENTS`). Over any cap → clarify, never truncate.
5. **Deselect removes a whole item**, with its occurrences. Per-date exclusion is future
   scope.
6. **Child keys:** a one-off item keeps `<cid>#item-NN`; a series item's children use
   `<cid>#item-NN#YYYY-MM-DD`. No existing key or Google id changes.
7. **Kill switch `CEC_BATCH_SERIES`, default `on`.** The switch is kept as the
   rollback path, but repeating list items are live at merge. With `off` a list item
   with a series stays `REASON_REPEATING`: Phase 33a behaviour exactly.
8. **Prompts v7 (text) and image v2 ship as the new defaults** and are safe with the
   switch off. `CEC_CREATE_PROMPT_VERSION=v6` (or `v5`) stays the prompt rollback.
9. **Holidays are a warning, not data.** The bot has no school or public holiday
   calendar and will not get one in this phase.

### Why the switch defaults to `on`

Creating is cheap and removing is expensive: one yes can insert up to 60 events, while
delete is still one listed target on one HKT day (Phase 28, ADR 0012); series-delete and
list-delete are unimplemented. Carter weighed that against the cost of the refusal the
family already met and chose `on` at merge (2026-10-30), so a term timetable works the
first time it is sent. The protection is the review surface, not the switch: the family
sees every item, its weekday, window, first and last occurrence and 「共 N 次」, the
holiday warning and the event total before one explicit yes, and can drop any item. The
switch stays as the one-line rollback (`CEC_BATCH_SERIES=off` plus a listener restart),
which needs no code revert.

## Goal

1. A message or image that states a shared date range and daily window plus one line per
   weekday class gives **one proposal** with one numbered entry per class, each showing
   its weekday, daily window, first and last occurrence, and occurrence count.
2. One yes creates every occurrence of every remaining item through
   `write_calendar_batch_create`, with per-occurrence idempotency and honest partial
   counts.
3. A repeating item that cannot be used is listed under 「未包括」 with an actionable
   reason; nothing is dropped silently and nothing is truncated.
4. Mixed messages (some one-off, some repeating) give one combined proposal.
5. With `CEC_BATCH_SERIES=off`, behaviour is identical to Phase 33a.

## Non-goals

- No RRULE, no Google `recurrence` (ADR 0011 stands).
- No series-delete, no list-delete, no update/patch; no per-occurrence deselect.
- No holiday calendar, no holiday dependency, no new store.
- No change to Phase 27's single-series path, to a lone series item's routing, or to the
  one-off list item's key, copy or write.
- No new model, vendor, provider, Slack scope or dependency.
- No multi-day all-day series; no series in `parse()` (rules-only, one event).
- No bulk important dates; no life-notes change.

## Architecture

### Flow (target; new steps marked)

```text
text list or image (加event) in #family-plans
  └─ model, prompt v7 / image v2 → events[] (≤15), some items with series   [new rules]
       ├─ per-item map + sanitize (unchanged)
       ├─ expand each series item once (pure, cap 40)                        [new]
       ├─ per-item checks: one-off rules, or series rules                     [new]
       ├─ total-event cap 60 → clarify, never truncate                        [new]
       └─ one list ParseResult: EventItem.series set on repeating items        [new]
  overlap: union list over every occurrence window; per-occurrence intersect   [changed]
  one proposal: series block per item (count, first/last occurrence, warning)   [changed]
  唔要 N → removes item N and its occurrences (still pending)                  [copy]
  yes → write_calendar_batch_create
          one-off item → <cid>#item-NN                 (unchanged)
          series item  → <cid>#item-NN#YYYY-MM-DD per occurrence              [new]
        ack with event counts and failed item numbers                          [changed]
```

### Data model (`models.py`)

| Change | Detail |
| --- | --- |
| `EventItem.series: SeriesSpec \| None = None` | The item's own bounded weekday series. `start`/`end` then mirror the **first occurrence**, as `ParseResult.start`/`end` mirror a series' first occurrence (`models.py:109-111`). `all_day` is always False for a series item. |
| `EventItem.occurrence_count: int = 1` | Proposal-time occurrence count. 1 for a one-off item. The writer re-expands and refuses on a mismatch, so yes writes exactly what was proposed. |
| `CalendarBatchWriteResult.event_count: int` | Google events attempted (sum over items). `item_count` keeps its meaning (list items). |
| `CalendarBatchWriteResult.failed_events_by_item: dict[int, int]` | Failed occurrence count per item number, for the ack. `failed_indices` stays a deduplicated list of item numbers. |
| `BatchOverlapHit.occurrence_date: date \| None` | The occurrence that overlaps, so the warning can name a day and collapse per item. |

Rejected alternative: a separate `SeriesItem` type with `ParseResult.batch_series`. Every
consumer (`remaining_items`, `apply_batch_edit`, `build_batch_proposal`, `batch_warnings`,
the writer loop, serialization) would need two code paths, and the item numbers would have
to be shared across two tuples. One tuple, one number space.

### Serialization and compatibility (`confirmation.py`)

- `_item_to_dict` (`confirmation.py:966-979`) adds `"series": _series_to_dict(item.series)`
  and `"occurrence_count"`. `_series_to_dict` / `_series_from_dict` already exist
  (`:1019-1041`).
- `_item_from_dict` (`:981-998`) reads both with `.get`, so **every pre-33c confirmation
  file loads unchanged** (`series` absent → None, `occurrence_count` absent → 1).
- **Rollback hazard (both ADR 0018 and here).** Pre-33c code reads named keys only, so a
  stored series item would load as a *single* event at its first occurrence and one yes
  would create 1 event instead of N. Reject or let pending lists expire **before**
  reverting the code. This is sharper than ADR 0018's version of the same warning and is
  repeated in the rollback section.

### Validation (`event_batch.py`)

`classify_item` (`event_batch.py:283-357`) gains a series branch, taken instead of the
`REASON_REPEATING` refusal at `:304` when `allow_series` is true. `allow_series` is an
injected keyword (the kill switch is read once at the call site), so the module stays
pure with no clock and no env.

Order for a series item: Simplified check, title, open-ended check, weekday cross-check,
expand, past check, cap checks. The expander owns the window and range errors.

| Case | Outcome |
| --- | --- |
| `allow_series` false | `REASON_REPEATING` (today's copy, unchanged) |
| `series.range_end` missing (model returned null) | **new** `REASON_SERIES_OPEN_ENDED` 「重複活動冇寫結束日期，請寫明日期範圍 / repeating item has no end date: give the date range」. Today this becomes a `series_range` gap and then `REASON_UNCLEAR` (`parse_fallback.py:765-767`, `event_batch.py:330`) |
| `expand_weekday_series` raises `over_cap` | **new** `REASON_SERIES_OVER_CAP` 「重複多過 40 次，請縮短日期範圍 / more than 40 repeats: shorten the range」, with the real count |
| raises `no_occurrences` | **new** `REASON_SERIES_NO_OCCURRENCES` 「日期範圍內冇講明嘅星期 / the range has none of those weekdays」 |
| raises `invalid_window` (daily window crosses midnight) | existing `REASON_CROSSES_MIDNIGHT` |
| raises `invalid_range` / `empty_weekdays` | existing `REASON_UNCLEAR` |
| `all_day` true with a series | existing `REASON_MULTI_DAY_ALL_DAY` |
| every occurrence before today HKT | existing `REASON_PAST` |
| some occurrences past, some not | **included**, trimmed to the future occurrences, with a warning line naming the dropped count (see copy). The stated range is not rewritten; `item.start` becomes the first kept occurrence and `occurrence_count` the trimmed count, so `expand_item` reproduces exactly that set later |
| `stated_weekday` set and not in `series.weekdays` | existing `REASON_WEEKDAY_MISMATCH` |
| total events over `MAX_BATCH_EVENTS` | whole-message clarification (new note `batch_over_event_cap`), no proposal, nothing truncated |

Trimming past occurrences is the one asymmetry with a one-off item (which is simply not
included when its date has passed). A term timetable sent mid-term is the normal case, and
refusing the whole item would be unhelpful; the dropped count is stated, so nothing is
silent.

New pure helper in the same module:

```text
expand_item(item)        -> list[SeriesOccurrence]  # strict; the writer's source of truth
item_occurrences(item)   -> list[SeriesOccurrence]  # copy-time, tolerant of a bad store
expand_batch_items(items)-> list[BatchOccurrence]   # (item_index, local_date, start, end)
batch_event_count(items) -> int                     # Google events these items write
```

One flat window list for the whole proposal (≤ 60 entries), used by the copy, the overlap
check and the between-item warnings. It imports `series` **lazily**, like
`parse_fallback._has_series_cue` (`parse_fallback.py:1190-1195`): `medic_librarian/answer.py`
imports `parse_fallback`, which imports `event_batch` at module level
(`parse_fallback.py:30`), and the medic path must keep loading no Calendar-path module
(ADR 0018 implementation notes).

### Weekday-vs-text cross-check

`stated_weekday` is already a copied-not-computed field per item
(`parse_fallback.py:168-174`, mapped at `:881-884`). For a series item the check becomes
membership: the weekday written in the item's own line must be **in** `series.weekdays`,
else `REASON_WEEKDAY_MISMATCH`. For the flyer shape 「音樂班（星期二）」 that is `TU ∈ {TU}`;
a model that attached the Tuesday class to Wednesday is caught.

A deterministic regex cross-check is **not** possible here: `series.extract_weekdays`
returns None for a bare 「（星期一）」 (verified offline; `series.py:190-215` needs 逢, a
range, a list, a compact run, 平日 or 每日). The prompts already forbid computing the
weekday, so the copied field is the only honest evidence.

Rejected alternative: a new `stated_weekdays` array per item. `EVENT_ITEM_SCHEMA` is
validated as an exact field set shared with the image path
(`parse_fallback._validate_v6_payload:234-255`), and ADR 0019 amendment item 5 records
that no field was added. A multi-weekday item (逢一三五) therefore leaves `stated_weekday`
null and skips the check; the array stays future scope.

### Routing (`parse_fallback.py`, `listener.py`)

- `_events_payload_to_parse_result` (`parse_fallback.py:786-895`): **unchanged shape**. A
  lone item with a series still goes to the single Phase 27 path (`:834-839`); two or more
  items still go to `build_batch_result`. Only `allow_series` is threaded through, read
  once from the environment.
- `series.apply_series_policy` keeps returning early for a list (`series.py:368-373`).
  This is now load-bearing: the shared header is a whole-text series cue, and without the
  early return the list would become one `series_open_ended` question.
- `listener.py` is unchanged except the ack. The offline-rules reply for a dated list
  (`listener.py:473-479`) is unchanged.

### Idempotency keys and partial retry

| Level | Key | Private properties |
| --- | --- | --- |
| One-off list item | `<cid>#item-NN` (unchanged, `calendar_writer.py:976-982`) | child `confirmation_id`, `parent_confirmation_id`, `batch_index`; no `series_id` |
| Series item occurrence | **new** `<cid>#item-NN#YYYY-MM-DD` | child `confirmation_id`, `parent_confirmation_id`, `batch_index`, `occurrence_date`, `series_id = <cid>#item-NN` |

The Google id derivation is unchanged (`sha256("cec-confirmation:" + key)`), and the new
key has a prefix no existing key can produce: Phase 27 children are `<cid>#YYYY-MM-DD` and
one-off list children are `<cid>#item-NN` with nothing after. **No historical Google id
changes, and no existing success audit is invalidated.**

`series_id` on these children is a deliberate, narrow extension of ADR 0018 decision 7
("no `series_id`"): the value is per **item**, not per confirmation, so a future
"remove this repeating item" can find exactly those occurrences through a private-property
query. It does not collide with Phase 27 values (plain `<cid>`). Given that one yes can
now create 40 events and delete is still one-at-a-time, the cheap handle is worth
stamping now. One-off list children still carry none.

Partial retry already works per child through `_successful_create_for(child_id,
audit_store)` (`calendar_writer.py:1141-1152`), so a second yes in the same thread retries
only missing occurrences. Three writer changes are needed:

1. `_write_batch_child` becomes a per-item loop over the item's occurrences (one
   `CalendarWriteResult` and one class-B audit row each).
2. `failed_indices` is currently built with `zip(items, children, strict=True)`
   (`calendar_writer.py:1070-1075`), which assumes one child per item. It must be derived
   from the per-child results instead, with `failed_events_by_item` beside it.
3. `_batch_item_problem` (`:1116-1130`) gains a series branch: re-expand, refuse on
   `SeriesExpansionError` (`series_<reason>`), refuse on `len(occurrences) !=
   occurrence_count` (`series_count_changed`), refuse `all_day` with a series
   (`series_all_day`), and refuse the whole list when the remaining items sum above
   `MAX_BATCH_EVENTS` (`batch_over_event_cap`). Any bad item refuses the whole list with
   no Google call, as today.
4. The writer also refuses a stored series item when `CEC_BATCH_SERIES` is off
   (`series_disabled`, injectable flag with an env default). A visible refusal beats a
   silent one-event write if the switch is flipped while a list is pending.

### Proposal copy (`confirmation.build_batch_proposal`, `event_batch.format_batch_item`)

Synthetic example; ground rule 15. The fixture mirrors only the **shape** of the live
notice (one month, one shared daily window, one class per weekday); the titles are
invented. Clock 2026-10-30 HKT.

Source (synthetic):

```text
第二期課堂
26年11月2日至11月30日（上學日）
逢上學日（星期一至五）下午3:15-4:15
美術班（星期一）
音樂班（星期二）
舞蹈班（星期三）
故事班（星期四）
科學班（星期五）
```

Proposal:

```text
請確認以下 5 個重複活動（會分開建立 21 個日曆活動）/ Please confirm these 5 repeating items (21 separate calendar events):
1. 美術班 · 逢星期一 15:15–16:15 · 2026-11-02 至 2026-11-30 · 共 5 次
2. 音樂班 · 逢星期二 15:15–16:15 · 2026-11-03 至 2026-11-24 · 共 4 次
3. 舞蹈班 · 逢星期三 15:15–16:15 · 2026-11-04 至 2026-11-25 · 共 4 次
4. 故事班 · 逢星期四 15:15–16:15 · 2026-11-05 至 2026-11-26 · 共 4 次
5. 科學班 · 逢星期五 15:15–16:15 · 2026-11-06 至 2026-11-27 · 共 4 次
未包括 / Not included (1):
• 「逢上學日（星期一至五）下午3:15-4:15」— 冇活動名稱 / no activity name
注意：學校假期同公眾假期我分唔到，呢 21 日全部會建立，請核對 / Warning: I do not know the school holidays or the public holidays. I will make all 21 events. Please check the dates.
請核對每項嘅日期同時間。/ Please check each date and time.
回覆 yes 確認建立以上 21 個活動；回覆「唔要 5」剔走第 5 項（連埋佢 4 次）；回覆 不要 取消。
Reply yes to create all 21 events. Reply "drop 5" to remove item 5 and its 4 events. Reply no to cancel.
No calendar change will be made until you confirm.
```

Copy rules:

- **The shown range is the first and last occurrence**, not the stated range. All five
  items state 11-02 至 11-30, but only the Monday class has an occurrence on 11-30. The
  shown span and 「共 N 次」 are then self-consistent, and they are what yes creates.
- **No per-occurrence sample lines inside a list.** Phase 27's single-series proposal
  shows five samples (`confirmation.SERIES_SAMPLE_LINES = 5`, `:304-309`); 15 items × 5
  would be 75 lines. One summary line per item keeps a 15-item proposal under the
  4,000-character budget that Phase 33a case P3 already tests. Phase 27's own copy is
  unchanged.
- The holiday warning is **one line per proposal**, not per item, and states the total
  count so the family knows the size of the mistake it is preventing.
- A trimmed item adds 「注意：第 N 項有 K 次已經過去，唔會建立 / Warning: K events of item N
  have passed. I will not make them.」
- `format_batch_item` keeps the existing one-off line byte for byte
  (`event_batch.py:371-387`); the series branch is additive.
- Mixed message header: 「請確認以下 4 項（會分開建立 13 個日曆活動）/ Please confirm these 4
  items (13 separate calendar events):」 — 「項」 rather than 「活動」 when the item count and
  the event count differ.
- Narrowing re-post: 「已剔走 / Removed: 第 5 項（4 次）」.
- Never claims a write; always ends with the existing no-change line.

Ack (`listener._batch_ack:1553-1587`): the success and already-created branches already
use child counts; only `n = result.item_count` must become `result.event_count`. A partial
failure names the item and its failed occurrences:

```text
已確認，但 3/21 個活動未能建立：第 2 項（音樂班，4 次中 3 次未建立）。（新建立 18 個，之前已有 0 個）可以喺呢個 thread 再回 yes，只會重試未完成嘅次數。/ Accepted, but 3 of 21 events failed: item 2 (3 of its 4 events). 18 events were created. Reply yes in this thread to retry only the missing events.
```

### Deselect semantics

Unchanged grammar (`event_batch.classify_batch_edit:534-557`,
`confirmation.apply_batch_edit:703-762`): `唔要 5` / `剔走 2,4` / `drop 5` / `只要 1,4` /
`keep 1,4`. A number names an **item**, so
removing a series item removes all of its occurrences; the re-post states how many. The
numbers stay the original item numbers, expiry is not extended, removing every item is
refused, and nothing is written. The only change is copy: the removed line and the
instruction line name the occurrence count.

Per-date exclusion (「唔要 3 嘅 11月9日」, or a public-holiday list in the reply) is **future
scope**: it needs a new reply grammar, per-item excluded-date state in the confirmation,
a re-render rule and its own ADR amendment. Until then the family can reject and resend a
narrower range, or delete one occurrence afterwards with the Phase 28 delete-one path,
which already targets one listed event on one HKT day.

### Caps

| Cap | Value | Where | Over cap |
| --- | --- | --- | --- |
| Items per message | 15 | `event_batch.MAX_BATCH_ITEMS:46` (unchanged) | existing over-cap clarification |
| Occurrences per series item | 40 | `series.MAX_OCCURRENCES:44` (unchanged) | `REASON_SERIES_OVER_CAP`, item not included, with the count |
| Events per confirmation | **60** | new `event_batch.MAX_BATCH_EVENTS` | whole-message clarification, no proposal, nothing truncated |

60 is 1.5× the single-series cap the family already accepted, and it covers the realistic
cases: this flyer is 21, and five classes over a 12-week term is 60. A full 15 × 40 = 600
inserts from one yes is not a reviewable proposal. Over-cap copy:

```text
呢個訊息會建立 85 個活動，一次最多 60 個。請分開傳送，例如一個月一次，或者一班一次。我冇建立任何提案。/ This message makes 85 events. The limit is 60 events per message. Please send it in parts; for example one month per message, or one class per message. I made no proposal.
未有改動日曆。/ No calendar change was made.
```

Operational note: 60 sequential `create_event` calls run inside the listener's
process-local dispatch lock (`listener._dispatch_lock:890`), so one yes can delay other
family replies by tens of seconds. It does **not** affect the heartbeat or the stuck
guard: `monitor_socket_health` runs on the main thread while dispatch runs on SDK worker
threads (`listener.py:1834-1866`, `:2043-2065`), and the ADR 0014 guard needs 900 s of
*disconnected* samples. Phase 27 already allows 40 in one write; the partial-retry path
covers a timeout mid-list.

### Overlap (`overlap.py`)

`detect_batch_overlaps` (`overlap.py:244-330`) currently takes one window per item. It
must iterate the flat occurrence list from `expand_batch_items`:

- `time_min` / `time_max` over **every** occurrence, so the single union Calendar list
  really covers the whole series span. Without this, only each item's first occurrence
  would be checked and later weeks would silently pass.
- Intersect per occurrence window (Phase 27 semantics, `detect_series_overlaps:149-170`).
  Still exactly **one** `list_calendar_events` call.
- Rendering collapses per item instead of one line per hit: 「第 1 項 5 次中 5 次撞期（例如
  2026-11-02 15:30–16:30 Piano）/ item 1: 5 of its 5 events overlap (example: …)」. A
  weekly existing lesson against a weekly class would otherwise produce five identical
  lines. The existing 10-line cap plus "and N more" stays.
- A failed list keeps today's "could not check" warning and yes is still allowed; overlap
  warns and never prohibits (AGENTS).

Between-item warnings (`event_batch.batch_warnings:407-453`) compare **occurrence
windows**, not item windows: a pair overlaps when two occurrences on the same day have
intersecting windows. Exact, pure and bounded (≤ 60 windows → ≤ 1,770 pairs). The flyer's
five classes are on five different weekdays, so they produce no warning; a Mon/Wed class
against a Monday class produces one. Duplicate detection adds "same title and same
series spec".

If the shared header does come back as an item with an invented title, it covers Mon–Fri
at the same window and therefore overlaps **every** class item on every day — five
collapsed warning lines, under the family's eyes, removable with 「唔要 6」. Three
independent layers cover that case: the prompt rule, `missing_title` when the model
invents nothing, and the overlap warnings plus deselect before one explicit yes.

### Prompts

`prompts/create_event_image.v2.txt` (new; v1 stays in the archive) adds a series section
the image prompt has never had:

1. A line that gives the schedule shared by the whole notice — a date range, a weekday
   set, a daily time window, or 逢/every wording — and that has **no activity name of its
   own** is **not an item**. Do not return it.
2. Copy that shared date range and daily time window into **every** class item it covers.
3. One class line is one item. When the item's own line names a weekday (or a weekday set)
   and a shared range and window apply, fill `series` with `weekdays` from the item's own
   line, `range_start` / `range_end` from the shared range, and `start_time` / `end_time`
   from the shared window. Set `start` / `end` to the first occurrence only. Never return
   one long timed span.
4. `stated_weekday` is the weekday written in that item's own line; null when the line
   names more than one.
5. `source_text` copies the item's own class line only, never the shared header line.
6. A repeating item with no end date: `series.range_end` null.
7. Unchanged: never compute a weekday, never invent a date, clock, person or place.

`prompts/create_event.v7.txt` (new; v6 stays) carries the same four list-series rules, so
a typed term timetable behaves like the image. `parse_fallback.PROMPT_PATHS` gains `v7`
and `DEFAULT_PROMPT_VERSION` becomes `v7` (`parse_fallback.py:60-69`);
`CEC_CREATE_PROMPT_VERSION=v6` and `v5` stay the rollbacks.

Both prompts are **inert with the switch off**: a returned series item is still
`REASON_REPEATING`, and the only visible difference is that the shared header line stops
becoming a bogus item — an improvement either way. No schema field is added, so the exact
field-set validation shared by both paths is unchanged.

### Text list impact

The same path, with one extra benefit: a typed term timetable 「11月2日至11月30日 逢星期一
至五 15:15-16:15 / 美術班（星期一）…」 now proposes instead of refusing. Nothing about a
one-off typed list changes: same mapping, same evidence rules (ADR 0018 amendment "the
lines are the evidence"), same keys, same copy. The dated-list routing guard
(`looks_like_dated_list:163-165`) is unchanged — the flyer's class lines already count as
dated lines through the 星期X token (`_DATE_TOKEN:132-144`).

## Logging and retention applicability

Counts and codes only; no titles, no dates, no message content beyond today's fields.

- Class A (14 days): `batch_extract_completed` (`event_batch.py:223-234`) gains
  `series_item_count`, `series_event_count` and `total_event_count`;
  `not_included_reasons` carries the new codes. `batch_write_started` /
  `batch_write_completed` gain `event_count` and `series_item_count`.
  `batch_overlap_check_*` gains `occurrence_count`. `batch_edit_applied` gains
  `removed_event_count`. One new line, `batch_series_refused`, carries the reason code and
  the counts when an item's expansion is refused.
- Per-child `write_attempt` / `write_succeeded` are unchanged and already carry the title
  and start, as Phase 27 children do. An accepted 60-event list therefore writes about 120
  child log lines; class A retention already handles that volume.
- Class B (90 days): one audit row per Google event — now up to 60 per message instead of
  15. Worth noting in `logging-and-retention.md`; the 90-day class is unchanged.
- Class C: confirmations now hold per-item specs; same terminal + 7-day purge.
- No new store, no class F data, no new retention class.

## Unit test plan (lock before implementation)

New file `tests/test_batch_series.py`, end to end through `process_slack_message_event`
with `FakePayloadLlmParser`, `FakeImageEventExtractor` and `FakeCalendarClient`; fixed HKT
clock; temporary stores; no network. Existing suites (`test_series_create.py`,
`test_multi_event_create.py`, `test_batch_clock_recovery.py`,
`test_image_event_extract.py`, `test_image_line_evidence.py`) stay green unchanged.

**The flyer fixture** (`FLYER_*`, synthetic as above): a v7/v2 payload of five items, each
with `series` weekdays `["MO"]`…`["FR"]`, `range_start` 2026-11-02, `range_end`
2026-11-30, window 15:15–16:15, plus a sixth header item with a null title. Expected: 5
items included, 21 events, one not-included line, one holiday warning.

| ID | Area | Case | Expect |
| --- | --- | --- | --- |
| K1 | switch | Flyer payload, `CEC_BATCH_SERIES` unset or `off` | Today's reply exactly (golden): 6 × `repeating`, `batch_none_included`, no confirmation |
| K2 | switch | Switch on, no series item in the payload | One-off list proposal byte-identical to Phase 33a |
| K3 | switch | Stored series item, switch flipped off before yes | `series_disabled` refusal, no Google call, audit row |
| E1 | extract | Flyer payload, switch on | 5 items, `occurrence_count` 5/4/4/4/4, `EventItem.start` = first occurrence, `ParseResult.series` None |
| E2 | extract | Lone item with a series | Phase 27 single-series proposal, byte-identical |
| E3 | extract | Mixed: 2 one-off + 2 series items | One proposal, 「項」 header, event count = 2 + N |
| E4 | extract | v5 and v6 payloads | Rollback paths unchanged |
| E5 | extract | Item with `series` and `all_day` true | `multi_day_all_day`, not included |
| V1 | validate | `range_end` null | `series_open_ended` with its copy |
| V2 | validate | Range giving 41 occurrences | `series_over_cap`, count in the copy, item not included |
| V3 | validate | Weekday set with no day in the range | `series_no_occurrences` |
| V4 | validate | Daily window 23:00–01:00 | `crosses_midnight` |
| V5 | validate | `range_end` before `range_start`; empty weekday list | `unclear` |
| V6 | validate | Every occurrence in the past | `past`, not included |
| V7 | validate | Two of five occurrences passed | Included, trimmed, `occurrence_count` 3, dropped-count warning |
| V8 | validate | `stated_weekday` TU, `series.weekdays` [WE] | `weekday_mismatch` |
| V9 | validate | `stated_weekday` null with weekdays MO,WE,FR | Included; check skipped |
| V10 | validate | 2 series items of 40 occurrences (80 total) | `batch_over_event_cap` clarification; no proposal; nothing truncated |
| V11 | validate | Exactly 60 total events | Proposed |
| V12 | validate | 16 items, one with a series | Existing over-cap clarification (item cap first) |
| V13 | validate | Simplified title on a series item | `unsupported_script` |
| P1 | proposal | Flyer | Matches the template above, including first/last occurrence and 「共 N 次」 |
| P2 | proposal | Any series item | Holiday warning present exactly once; never claims a write |
| P3 | proposal | 15 items totalling 60 events, 10 not-included, 10 overlap lines | Under 4,000 characters |
| P4 | proposal | Rendered copy | No Simplified markers |
| P5 | proposal | Image-sourced flyer | Image header line present (`IMAGE_PROPOSAL_HEADER`) |
| P6 | proposal | Phase 27 single-series proposal | Unchanged, five sample lines |
| N1 | narrow | `唔要 5` on the flyer | Item 5 and its 4 occurrences removed; re-post says 「第 5 項（4 次）」; still pending; no Calendar call |
| N2 | narrow | `只要 1` | 5 events remain; header count updates |
| N3 | narrow | Remove every item | Refused; state unchanged |
| N4 | narrow | After acceptance; after expiry | Existing replies; no write |
| W1 | writer | Yes on the flyer | 21 `create_event` calls in item then date order; keys `#item-01#2026-11-02` … `#item-05#2026-11-27`; Google ids derived from those keys |
| W2 | writer | Same | One class-B audit row per event; `series_id = <cid>#item-NN`; `batch_index`; `occurrence_date`; `parent_confirmation_id` |
| W3 | writer | One-off item in the same list | Key stays `<cid>#item-NN`; no `series_id` (Phase 33a golden) |
| W4 | writer | Fake fails occurrences 2 and 3 of item 2 | FAILED; `event_count` 21, `failed_events_by_item` {2: 2}; ack names item 2 and 2 of its 4 |
| W5 | writer | Second yes after W4 | Only the two missing occurrences attempted; all others `already_created` with no Google call |
| W6 | writer | Same keys across retries | Same Google id in each request body |
| W7 | writer | Stored `occurrence_count` 5, spec expands to 4 | `series_count_changed` refusal; no Google call; audit row |
| W8 | writer | Stored series item with `all_day`; stored spec over cap; all items dropped | REFUSED with the matching reason; no Google call |
| W9 | writer | Series writer given this confirmation; single writer given it | REFUSED (existing guards) |
| W10 | writer | Google auth error mid-list | Ack carries the login-expired note; honest counts |
| C1 | store | JSON round trip with series items | Equal after load, specs included |
| C2 | store | Pre-33c confirmation file (no `series` key) | Loads unchanged; `series` None; `occurrence_count` 1 |
| C3 | store | Same Slack message delivered twice | Same confirmation id; items unchanged |
| O1 | overlap | Existing Monday 15:30 lesson across the whole month | One `list_calendar_events` call; window covers 11-02 → 11-30; item 1 reports 5 of 5; collapsed to one line with an example |
| O2 | overlap | Existing event only in the last week | Still found (window covers the whole span) |
| O3 | overlap | Mon/Wed series item and a Monday series item | Between-item warning once; both included |
| O4 | overlap | Five different-weekday items | No between-item warning |
| O5 | overlap | List call fails | "Could not check" warning; yes still allowed |
| G1 | logs | Extraction, overlap, edit, write | New count fields present; no titles or dates in the new fields; no message content |
| I1 | isolation | `import cec_vivisystem.medic_librarian.answer` | `cec_vivisystem.series` not in `sys.modules` (lazy import held) |
| X1 | garbage | `series` as a string, a list, a dict with wrong types, 10,000-character title | Validator rejects or the item is not included; no crash |
| X2 | garbage | `occurrence_count` negative or huge in a stored file | Writer refuses; no Google call |
| X3 | prompt | v7 and image v2 files | Contain the header rule and the per-item copy rule; no Simplified text; `CEC_CREATE_PROMPT_VERSION=v6` still loads v6 |

Non-tests: live model accuracy on real flyers (the authorized eval below), Slack delivery
and threading, Google behaviour beyond the fakes and the existing 409 tests, holiday
correctness (explicitly out of scope), Mini deployment.

## Evaluation (synthetic; live calls need explicit authorization)

Extend `scripts/evaluate_create_parser.py` and `evals/create_cases.json`, and the Phase
33b image set, with: the synthetic flyer as text and as a rendered image; the same flyer
with a header that *does* carry an activity name (must become an item); one class line with
two weekdays (逢一三五); a term with no end date; a timetable crossing a year boundary; a
month grid where one cell is a repeating class; a mixed notice (two one-off dates plus two
weekly classes); a timetable of 85 events (over the total cap).

Metrics: per-item weekday set, range, window and title exact match; occurrence count;
invented items (must be zero); the shared header must never be an item; latency p50/p95
and tokens. Gate for the `.env` flip: zero invented items, correct weekday sets on every
case, and the header suppressed on the flyer cases.

## Decision record

Amend **ADR 0018** with a new section, "Amendment: repeating items inside a list": it owns
the list contract that changes (decision 1 "one series item stays Phase 27", decision 3's
`repeating` reason, decision 7's child key and "no `series_id`"), in the same way it
already absorbed "the lines are the evidence" rather than minting a new number. Then add
short cross-reference notes:

- **ADR 0011**: the 40-occurrence cap and the pure expander now also bound a list item;
  `series_id` on a list-series child is the **item** key `<cid>#item-NN`, not the
  confirmation id, and remains reserved for a future series-delete.
- **ADR 0019**: image prompt v2 adds the shared-header and per-item series rules; no
  schema field is added, so amendment item 5 still holds.

No new ADR number: one decision, one home.

## Acceptance criteria

- [x] With `CEC_BATCH_SERIES=off`, behaviour is identical to Phase 33a (tests K1, K2, K3)
- [x] The synthetic flyer gives one proposal of 5 items and 21 events, with the shared
      header listed as not included and the holiday warning shown once
- [x] One yes creates 21 events with keys `#item-NN#YYYY-MM-DD`, one audit row each,
      `series_id = <cid>#item-NN`
- [x] One-off list items, Phase 27 single-series proposals and writes, and every existing
      key and Google id are unchanged (the Phase 27 / 33a / 33b suites pass unchanged
      except the three default prompt-version assertions)
- [x] Every refusal reason has actionable bilingual copy; nothing is truncated; no cap is
      silently exceeded
- [x] Deselect removes a whole item and states its occurrence count; narrowing never writes
- [x] Partial failure reports honest event counts; a second yes retries only the missing
      occurrences
- [x] Overlap makes one Calendar list covering every occurrence; warnings collapse per item
- [x] Pre-33c confirmation files load unchanged; the rollback hazard is documented in
      maintenance
- [x] Offline pytest green (1,027 tests), Ruff clean, `git diff --check` clean, boundary
      logs present
- [x] ADR 0018 amendment plus the 0011/0019 notes, architecture, AGENTS (Writer contract
      and the new switch), maintenance, `.env.example`, logging-and-retention and the
      prompt archive updated
- [ ] Live eval run with authorization (still outstanding; no live call was made)

## Rollout and rollback (deployment authorized separately)

Rollout: `git pull`, `uv sync --locked`, restart the listener. The switch is `on`, so
repeating list items are live from that restart. Smoke in `#family-plans` with a
synthetic two-class timetable far in the future (about 6 events): reply `不要` once, send
again, reply `唔要 2`, then `yes`; check the events in Google Calendar and remove them
with delete-one.

Rollback, in this order:

1. Reject or let expire **every pending list confirmation** — pre-33c code would read a
   stored series item as one event at its first occurrence and one yes would write 1
   instead of N.
2. Set `CEC_BATCH_SERIES=off` and restart. This is enough for the behaviour; no code
   revert is needed. A pending list that already holds a repeating item is then refused
   visibly (`series_disabled`), never written as a single event.
3. Only if the prompts must go back too: `CEC_CREATE_PROMPT_VERSION=v6` (or `v5`) and
   restart.
4. Already-created events stay; remove them with delete-one.

## Answers locked by Carter (2026-10-30)

1. **Switch default: `on`.** The kill switch is kept; `CEC_BATCH_SERIES=off` is the
   rollback. No separate `.env` flip is needed for the feature.
2. **Total-event cap: 60** Google events per confirmation, with 15 items and 40
   occurrences per series item unchanged.
3. **Holidays: warning only.** No holiday calendar, no holiday store, in this phase or
   as a dependency.
4. **An already-started timetable is included**, the passed dates are skipped, and the
   copy states the remaining count and how many were skipped.
5. **Whole-series delete is a later phase**, out of scope here. The per-item
   `series_id` is stamped now so that phase has a handle.
6. **A repeating item with no end date is not included**, with an actionable
   「請寫明日期範圍 / give the date range」 reason.
7. **Mixed one-off and repeating items in one proposal: allowed**, with the 「項」 header
   wording and the event total.

## Rough size

| Area | Estimate |
| --- | --- |
| `models.py`, `parse_fallback.py`, `image_extract.py` | ~45 lines |
| `event_batch.py` (reasons, `expand_batch_items`, series branch, copy, cap) | ~130 lines |
| `calendar_writer.py` (per-occurrence children, counts, defences) | ~90 lines |
| `overlap.py` (occurrence windows, collapsed rendering) | ~70 lines |
| `confirmation.py` (serialization, proposal counts) | ~40 lines |
| `listener.py` (ack) | ~25 lines |
| `prompts/create_event.v7.txt`, `prompts/create_event_image.v2.txt` | ~105 lines |
| `tests/test_batch_series.py` (~55 cases) | ~900 lines |
| Docs (ADR amendment + notes, architecture, AGENTS, maintenance, `.env.example`, logging-and-retention, README help, PROGRESS) | ~160 lines |

About **two weekend sessions**: one for the data model, validation and writer; one for
copy, overlap, prompts and tests. The eval and the `.env` flip are separate authorized
steps.

## Implementation notes and deviations (2026-10-30)

1. **Occurrence set, not a trimmed spec.** The design said the stated range is not
   rewritten and `occurrence_count` is the trimmed count, while the writer refuses on a
   count change. Both hold only if the writer derives the same set without a clock, so
   `expand_item` expands the stored spec and keeps the occurrences from `item.start`
   onward. `item.start` is the first occurrence the proposal showed, so the stated range
   stays as written, a retry writes the same dates, and `past_dropped_count` still
   derives the skipped count for the copy. No third field was added to `EventItem`.
2. **The kill switch lives in `series.py`** (`load_batch_series_enabled`), read lazily at
   the two call sites (`parse_fallback._events_payload_to_parse_result` and
   `calendar_writer.write_calendar_batch_create`). `event_batch` stays pure: it takes
   `allow_series` as a keyword. `MAX_OCCURRENCES_HINT` mirrors `series.MAX_OCCURRENCES`
   for copy, with a test keeping the two equal, because `event_batch` may not import
   `series` at module level (medic isolation, ADR 0018 implementation notes).
3. **`BatchOverlapResult` gained `occurrence_counts` and `occurrence_count`** so the
   collapsed line can say "5 of its 5" without passing the items to the renderer.
4. **An empty `series.weekdays` never reaches the per-item checks**: the payload
   validator already rejects it (ADR 0018 decision 2), so that row of the validation
   table is unreachable through the model path and is covered by a validator test.
5. **The flyer proposal also carries the existing 33a dated-line warning** (「你個訊息有 7
   行有日期，但我只理解到 6 項」), because the shared range line and the shared schedule
   line both name a day and neither is an item. That is honest Phase 33a behaviour and
   was left alone.
6. **Three existing prompt-version assertions were updated** (`test_create_extraction`,
   `test_parse_fallback`, `test_multi_event_create`, plus the image prompt name in
   `test_image_event_extract`) because v7 and image v2 are now the defaults. No other
   existing test changed.
7. **The eval corpus was not extended** in this slice: `scripts/evaluate_create_parser.py`
   and the case files are the live-eval step, which is still unauthorized, so the
   synthetic cases are covered by `tests/test_batch_series.py` instead.

## Related / later

- [Phase 27](phase-27-weekday-series-create.md), [Phase 33a](phase-33a-multi-event-text.md),
  [Phase 33b](phase-33b-image-event-extract.md).
- Later: per-date exclusion before yes; remove a whole repeating item or a whole list
  after yes (`series_id` / `parent_confirmation_id` queries); a `stated_weekdays` array
  for multi-weekday cross-checks; RRULE if Calendar-native series are ever preferred
  (ADR 0011).

## Remaining risks

- **Model accuracy on real notices is unmeasured.** The whole phase depends on the model
  copying a shared range and window into each class item without inventing one. The
  deterministic nets are the weekday cross-check, the caps, the overlap warnings, the
  「未包括」 list and one explicit yes.
- **Create is cheap, delete is not.** One yes can write 60 events; removal is one event
  per conversation until series-delete exists. The cap, the switch and open question 5
  are the mitigation.
- **Holidays will be wrong sometimes.** 上學日 means a school day; the bot expands every
  weekday in the range. The warning is honest but the family must check.
- **One long write delays other replies** (up to 60 inserts inside the dispatch lock).
  The heartbeat and the stuck guard are unaffected.
- **Rollback with a pending list is silently wrong** unless pending lists are cleared
  first. Documented in the rollback steps and in maintenance.
