# Architecture – cec-vivisystem

## 1. Architectural Philosophy

cec-vivisystem is designed as a **vivisystem** (Kevin Kelly, *Out of Control*).

Key characteristics:
- Bottom-up growth
- High decentralization
- Many simple, focused components instead of one large intelligent agent
- No central long-running orchestrator / “brain”
- Intelligence and reliability emerge from the interaction of components + the external environment
- Human confirmation is a deliberate and permanent control point

We optimize for a busy parent who can only work 1–2 hours per weekend.  
Therefore the architecture must remain understandable, testable, and evolvable under severe time constraints.

## 2. High-Level Shape

```
                    ┌─────────────────────────────┐
                    │     External Environment     │
                    │  (Google Calendar + Slack)   │
                    └──────────────┬──────────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           │                       │                       │
           ▼                       ▼                       ▼
    ┌─────────────┐       ┌──────────────┐       ┌─────────────────┐
    │  Listener   │       │   Parser     │       │ Confirmation    │
    │  (Slack)    │──────▶│              │──────▶│ Guardian        │
    └──────┬──────┘       └──────────────┘       └────────┬────────┘
           │                                              │
           │ #family-life-notes                           ▼
           │ (parallel; not calendar)            ┌─────────────────┐
           ▼                                     │ Calendar Writer │
    ┌─────────────┐                              └────────┬────────┘
    │ Life Notes  │                                       │
    │ Keeper      │                    ┌──────────────────┴──────────┐
    └─────────────┘                    │                             │
                                       ▼                             ▼
                              ┌──────────────┐              ┌─────────────┐
                              │   Reminder   │              │   Observer  │
                              │   Agent      │              │  (Health)   │
                              └──────────────┘              └─────────────┘
```

Life Notes Keeper is a **parallel** ability: it does not sit on the Parser → Confirmation → Calendar path. Dedicated channel: `#family-life-notes`. Listener dispatch (Phase 5B) stores raw notes; it does not parse them as calendar events.

Components communicate primarily through clear events and well-defined contracts rather than direct synchronous calls to a central controller.

## 3. Core Components (Target State)

| Component              | Responsibility                                      | Status      |
|------------------------|-----------------------------------------------------|-------------|
| Listener               | Receives messages from Slack (event-driven); plans vs life-notes vs confirmation dispatch; accepted confirmations may write; create proposals may warn on overlap; ``list_events`` always replies (day list or period recap); important dates add/view | **Done (Phase 2 + 5B + 4b + 6 + 8 + 12 list must-reply + 14 recap + 15 dates)** |
| Parser                 | Turns natural language (Cantonese/English) into structured intent | **Done (Phase 1 + 3 + 9–12 + 14–15 + 17 今晚/Coco + 18 hybrid fallback + 19 號/有咩 + 20 返學 + LLM failover)** |
| Availability Checker   | Overlap / same-person **warn** on create proposal (list window, not freebusy); bilingual 撞期; series checked per occurrence window; a multi-event list checks every item occurrence in one union list call | **Partial (Phase 8 + 13 + 19 stale HTTP reconnect + 27 per-occurrence + 33a/33c per list occurrence)** — freebusy later |
| Calendar Reader        | Lists events for a time range (read-only); ``format_recap`` groups multi-day lists | **Done (Phase 7 + 14)** |
| Morning recap          | Scheduled today text plus full-month PNG at 07:00 HKT; independent delivery markers | **Done (Phases 12 + 26)** |
| Important dates        | Yearly/one-off family markers; Slack add/view; 10:00 next-7-days note (not Calendar Writer) | **Done (Phase 15)** |
| Google token reminder  | 10:00 HKT status-channel notice 3/2/1 days before Testing refresh-token expiry, then daily "expired" (not Reminder Agent) | **Done (Phases 16 + 30)** |
| Proposal Agent         | Generates human-readable confirmation messages      | **Done (Phase 4)** — minimal `build_proposal` (folded) |
| Confirmation Guardian  | Tracks pending confirmations + timeouts             | **Done (Phase 4 + 4b)** — Slack thread yes/no wired |
| Calendar Writer        | The only component allowed to write to Google Calendar | **Done (Phase 6 + 13 + 27 + 28 + 33a/33c)** — create, discrete weekday-series create, list (batch) create, and delete-one; one Google create per ``confirmation_id`` (per occurrence for a series, per item for a list, per item occurrence for a repeating list item); update/patch, series-delete and list-delete not started |
| Life Notes Keeper      | Stores exact original family notes (`raw_text` + metadata); independent of calendar | **Done (Phase 5A + 5B)** — Option A raw capture; Slack `#family-life-notes` wired |
| Reminder Agent         | Posts the two standard reminders                    | Not started — morning recap is **not** this component |
| Observer / Health      | Independent health and anomaly reporting            | Partial: Phase 29 heartbeat + alert-only check (`health.py`); Phase 30 status channel, alert policy v2 (`health_policy.py`), 09:00 daily status (`daily_status.py`); Observer planned |

## 4. Key Architectural Decisions

### 4.1 Source of Truth
- **Time-based events** → Shared Google Calendar (primary)
- **Rich notes & life fragments** → JSON files under gitignored `data/life_notes/` (class **F**; `raw_text` is the source of truth). Structured enrichment, LLM, and DB/vector search are later additive upgrades only.

### 4.2 Coordination Style
- Prefer **event-driven** and **message-based** interaction
- Avoid long-running central processes that become single points of failure
- Each component should be as replaceable and independently testable as possible

### 4.3 Human-in-the-Loop
Any action that creates, updates, or deletes a calendar event **must** go through an explicit confirmation step. This is a permanent design constraint, not a temporary limitation.

### 4.4 Growth Strategy
We only implement the next component when the previous vertical slice is stable in real use.  
We do not build the full diagram above in advance.

**Grow from need, not from a pre-drawn roadmap of cleverness.**  
Next work is chosen from friction in real family use (or the next thin vertical slice that unblocks use)—not from speculative upgrades (e.g. we do **not** pre-schedule an LLM parser phase).

### 4.4.1 Parser strategies (contract vs implementation)

**Current listener policy — Phase 24 / [ADR 0008](decisions/0008-llm-first-event-creation.md):**
configured models interpret event descriptions first, after deterministic
help/list/important-date and input-rejection routing. Creation does not consult
rule vocabulary/date/time extraction. No key retains offline rules. Model failures
produce explicit unavailability after bounded failover. `parse()` and the default
legacy fallback API remain compatible. The active prompt is v7 (v6 and v5 stay as
`CEC_CREATE_PROMPT_VERSION` rollbacks) with a provider schema whose `events[]`
items each carry an optional bounded weekday `series` object, plus local
validation; confirmation still gates every Calendar write. Phase 27 runs a
deterministic span guard / series policy after every parse; Phase 28 delete
requests are a deterministic control route the model never sees.
The phase history below describes how this system reached that policy.

- **Stable seam**: `parse(...) -> ParseResult` (and the contract fields). Downstream components depend on this, not on how intent was produced.
- **Offline implementation (Phase 1)**: offline rule/heuristic parser — deterministic, no network, no LLM.
- **Real-use note**: Phase 1 fixtures pass. Phase 3 expanded rules for common live Cantonese (聽日, 上晝/下晝, class/place titles). Phase 9 aliases: 游水→游泳, MS Wong→Miss Wong 堂; child nickname → Cedric (canonical in `ParseResult`). Phase 10 titles: 公園, playgroup, 游水班→游泳, 體能班, 手作, 商場, 生日會, 打針. Phase 11: **聽朝** → tomorrow morning. Phase 12: **聽日有乜嘢活動** / **聽日有乜嘢** / **聽日有什麼活動** → `list_events` (tomorrow), not create/unknown. Phase 14: **今日** / **今個星期** / **今個禮拜** / **下個星期** / **今個月** / ``9月1日至9月7日`` → `list_events` windows (Monday-start week; multi-day recap grouped by day). Phase 15: **3月5日 Cedric 生日** → `add_important_date`; **重要日子** / **有咩生日** → `list_important_dates` (JSON store, not Calendar; [ADR 0004](decisions/0004-important-dates-store.md)). Whole-message **help** / **指令** / **點用** → `help` (common allowed inputs including important dates; no write). Phase 19: **號** = **日**; **今日有咩嘢做？** lists today; **加重要日子** stores; year-less `9月18號` + 下晝 colon / 夜晚 / 兩點 parse as create. Phase 20: **返學** is a create title (not a signal alone). Further gaps → more fixtures/rules first; LLM only if pain is sustained + ADR.
- **Historical hybrid fallback (Phase 18 + 20, [ADR 0005](decisions/0005-hybrid-parse-fallback.md))**: `parse()` stays rules-only. Listener `parse_with_fallback` may call SpaceXAI when a create-looking line is `unknown` / `needs_clarification`. One 15s attempt per model (`max_retries=0`). Non-reasoning SKU first; grok-4.5 second (`reasoning_effort=low`) because flagship thinking returns empty JSON inside 15s. Result is still `ParseResult`; Confirmation **yes** still gates Google. Miss rows in `data/parse_misses/` feed later **rule** phases. Fake LLM in pytest.
- **Public fixtures**: README, help, tests, and phase tables use synthetic names/dates. Live nicknames and real family dates stay in parser alias tables only — do not paste Slack lines into public docs. Written Chinese is Traditional only (ground rule 9); Simplified is rejected.
- An LLM is **not** a separate swarm component and **not** used on every message.

### 4.5 Cross-Cutting Quality Bars (all phases)

These are **architectural constraints**, not optional polish. They apply to every component in the diagram above and every future phase.

| Concern | Binding standard | ADR |
|---------|------------------|-----|
| Unit tests (happy, failure, contract, offline, determinism) | [unit-testing.md](unit-testing.md) | [0003](decisions/0003-unit-testing-standard.md) |
| Troubleshooting logs + data retention/purge | [logging-and-retention.md](logging-and-retention.md) | [0002](decisions/0002-logging-and-retention.md) |
| Resilience checklist | [resilience.md](resilience.md) | — |
| Family/process rules | [ground-rules.md](ground-rules.md) (1–14; teaching comments for LLM learning) | — |

**Implications for structure:**

- Components expose **pure, testable** entry points; I/O at the edges with injectable fakes.
- Time-dependent logic accepts an injectable clock / `now`.
- Multi-step flows carry `correlation_id` for log stitching.
- Durable state is classified (app log / audit / operational / dead letter / notes / calendar-as-SoT); stores ship with a purge story.
- Phase docs **narrow scope** only; they **cannot waive** testing or logging/retention.

### 4.6 Phase document contract

Every `phases/phase-N-*.md` must include:

1. Goal, in/out of scope, time box  
2. **Locked unit test plan** (fixtures/cases + non-tests) conforming to [unit-testing.md](unit-testing.md)  
3. **Logging/retention applicability** for data the phase introduces (class A–G from logging-and-retention)  
4. Acceptance criteria that require green offline tests and boundary logs  

Phase 1 is the first example: [phases/phase-1-parser.md](../phases/phase-1-parser.md).

## 5. Current State (Phase 21 locally verified)

Phase 27 + 28 (locally verified; no deployment): a bounded weekday series
(e.g. Mon–Fri over a date range with a daily window) expands server-side into N
discrete Google events behind **one** confirmation, capped at 40; overlap runs per
occurrence window; a single timed create crossing HKT days, or an all-day
multi-day span, becomes a clarification instead of a proposal
([ADR 0011](decisions/0011-weekday-series-create.md)). Delete-one lists one HKT
day, matches deterministically, and requires a confirmation naming title + HKT
start + short event id (numbered pick when ambiguous) before
`write_calendar_delete` ([ADR 0012](decisions/0012-calendar-delete-confirmation.md)).
Series-delete, RRULE and update/patch remain future scope.

Phase 25 adds pending scheduled-delivery markers and retained Slack message
handles for recap, important-date reviews and token reminders. Unresolved attempts
block automatic reposting and require manual reconciliation; see
[ADR 0009](decisions/0009-scheduled-post-reconciliation.md). This does not provide
cross-process locking or exactly-once delivery.

Phase 24 supersedes the Phase 23 prompt selection and rules-first listener policy.

Phase 23 (locally verified; live model quality and rollout unverified): the create
fallback uses prompt v3 and extracts independently from message/reference time,
without receiving the rule parser's missing-field verdict. AM/PM rule clocks allow
CJK adjacency. See [ADR 0007](decisions/0007-independent-fallback-extraction.md).

Phase 22 (locally verified, rollout unverified): Slack SDK is the sole Socket Mode
recovery owner. The listener's periodic health check observes connection status
without forcing endpoint replacement; this avoids competing with SDK recovery.
See [Phase 22](../phases/phase-22-socket-recovery.md) and the maintenance handover.

Phase 31 ([ADR 0015](decisions/0015-mini-control-app-lan-auth.md),
[ADR 0016](decisions/0016-google-reauthorization-flow.md); locally verified, no
deployment): `cec_vivisystem.control` is an optional Starlette app (the
`control` extra) on the home LAN for both parents. It reads the same records as
the 09:00 daily status, lists the next 14 days of Calendar events and the
important dates (read-only), lets either parent re-authorize Google (loopback
redirect + paste, PKCE) and test the connection, and lets the admin change the
three Slack channel IDs, change the stuck-retry setting and restart the listener
(`launchctl kickstart -k`, confirmed, rate-limited). Saving new Google access
also restarts the listener once, so Slack replies use the new token (ADR 0016,
amended 2026-10-21). Every action is audited
(class B) and announced in the system-status channel. Nothing imports the app,
it has no scheduler, and it never writes Calendar or resolves confirmations.

Phase 32 ([ADR 0017](decisions/0017-aia-medic-librarian.md); locally verified,
no deployment): the listener routes `CEC_MEDIC_CHANNEL_ID` (`#jeff-aia-medic`)
to `medic_librarian` before any Calendar path and outside the Calendar dispatch
lock. An offline build turns the plan PDF into a redacted, page-referenced
knowledge tree; runtime retrieves nodes by keyword, asks the model (same primary +
fallback policy as Phase 24) for a JSON answer limited to the retrieved context,
validates citations and figures, and renders sources from the tree. No hit means
"not in this document" without a model call. Nothing is stored; no Calendar,
confirmation, important-date or life-note access.

Phase 33a + 33c ([ADR 0018](decisions/0018-multi-event-create.md); locally
verified, no deployment): one `#family-plans` message that lists several events
becomes **one** confirmation holding up to 15 numbered items. Lines that cannot be
used are listed under 「未包括」 with a reason, a line with a date but no clock makes
the whole message one time question, and thread replies 「唔要 3」 / 「只要 1,4」 /
"drop 3" / "keep 1,4" narrow the list (still pending, original numbers) before one
yes creates every item shown last through `write_calendar_batch_create`. Phase 33c
(`CEC_BATCH_SERIES`, default `on`) lets **one item** carry its own bounded weekday
series: the item stays one numbered entry showing its weekdays, daily window, first
and last occurrence and 「共 N 次」, and one yes writes one Google event per
occurrence. Caps are 15 items, 40 occurrences per item and **60 events per
confirmation**; over any cap the message or the item is refused, never truncated.
Occurrences already past are skipped with a stated count, a repeating item with no
end date asks for the range, school and public holidays are a warning only, and
one-off and repeating items may be mixed in one proposal. Deselect removes a whole
item with all of its occurrences. The list-level `ParseResult.series` stays None, so
the Phase 27 single-series path is unchanged and each writer still refuses the
other's shape. With the switch `off`, behaviour is Phase 33a exactly.

Phase 33b ([ADR 0019](decisions/0019-image-event-extract.md); locally verified,
no deployment, `CEC_IMAGE_EXTRACT` default `off`): a top-level `#family-plans`
message whose text contains `加event` may carry up to three PNG/JPEG images. The
listener downloads them from `https://files.slack.com` only (no redirect to
another host), strips metadata and downscales with Pillow, and sends them to xAI
vision with the Phase 33a `events[]` schema (image prompt v2 also carries the
Phase 33c list-series rules). The result takes the unchanged 33a / 33c
proposal, narrowing and yes path; nothing is stored beyond the confirmation.
The line evidence for the 33a checks is the model's own `source_text` lines, one
normalised line per item (`build_image_evidence`), never the caption; the caption
stays the message for the reply, the preview and narrowing. An item that returned
no line has no evidence, and the image time question asks for the same image
again with `加event` and the times, or for the typed list.
With the switch off, or without `加event`, every message behaves as before.

Phase 30 ([ADR 0014](decisions/0014-status-channel-alert-hysteresis-and-stuck-exit.md)):
the SDK stays the first recovery owner. Only after about 15 minutes with no
5-minute connection does the listener replace its whole Socket Mode client
(bounded retries, `stuck_connection.py`) and then exit with code 75 for launchd
`KeepAlive`. The health checker stays read-only. Operator messages go to the
system-status channel (`status_channel.py`), not `#family-plans`.

The [takeover review](takeover-review-2026-09-19.md) records 15 corrected findings
and remaining operational limits. [ADR 0006](decisions/0006-retry-safe-calendar-and-atomic-stores.md)
adds stable Google event IDs with verified conflict recovery, Slack-source
confirmation/note deduplication, process-local intake serialization, and atomic
JSON replacement without changing schemas. Google reads now follow all pages.
Proposals disclose the effective end; expiry is enforced during resolution.
Logs use local-date files and run retention on daily rollover as well as startup.
Period recaps clip spanning events to the requested dates.

Current communication is synchronous function dispatch behind injectable
interfaces. The event/message-based diagram is a direction, not an implemented
message broker. Operate one listener per data directory. Atomic files and stable
Calendar IDs do not make scheduled Slack post/marker writes transactional.



At present the system contains:
- Project structure, environment, logging/testing foundations, and system standards
- **Parser** (offline): `parse` → `ParseResult`; Phase 1 + Phase 3 live expansions + Phase 9 aliases (游水→游泳, MS Wong→Miss Wong 堂, child nickname→Cedric) + Phase 10 titles (公園, playgroup, 游水班→游泳, 體能班, 手作, 商場, 生日會, 打針) + Phase 11 **聽朝** (tomorrow morning) + Phase 12 list phrases (**聽日有乜嘢活動** and kin) + Phase 14 period windows (**今日** / **今個星期** / **今個月** / date range) + Phase 15 important dates (**3月5日 Cedric 生日**, **重要日子**) + Phase 17 **今晚** / **今日** create + colon `2:30` + **加活動** + pet nickname→Coco + Phase 19 **號** dates, **今日有咩嘢做？**, **加重要日子**, 兩點 / 夜晚 + Phase 20 **返學** + whole-message **help** / **指令** / **點用**; same contract (§4.4.1)
- **Listener** (Slack Socket Mode): `#family-plans` → parse; `create_event` creates a pending confirmation and thread yes/no resolves it (Phase 4b). On first accept, injectable Calendar Writer may create one event (Phase 6). Create proposals may include an overlap / same-person **warning** (Phase 8); yes is still required. `list_events` **always replies** (day list, period recap, empty, or explicit error — never silent; no confirmation; no write). Important dates add immediately and view lists the store (no confirmation; no calendar write). `#family-life-notes` → `create_life_note` (Phase 5B)
- **Confirmation Guardian**: pending accept/reject/expire + class C purge; Slack thread vocabulary wired; proposal text may include overlap warnings
- **Calendar Writer**: `write_calendar_create`, `write_calendar_series_create` (Phase 27), `write_calendar_batch_create` (Phase 33a/33c) and `write_calendar_delete` (Phase 28) for **accepted** confirmations only; fake client in pytest; live Google client from env in Socket Mode. No update/patch. Same `confirmation_id` → at most one Google create (Phase 13; second yes replies already added); series children use `<confirmation_id>#<YYYY-MM-DD>` keys, stamp `parent_confirmation_id` / `occurrence_date` / `series_id` private properties, audit per child, and retry only missing days. A list child is `<confirmation_id>#item-NN` (no `series_id`); a **repeating** list item's children are `<confirmation_id>#item-NN#YYYY-MM-DD` and stamp `occurrence_date` plus `series_id = <confirmation_id>#item-NN` (per item, not per confirmation). A list write refuses the whole list on any bad stored item with no Google call, counts **events** honestly, and a second yes retries only the missing occurrences. A timed single create crossing HKT days is refused. Delete is audited as `op=delete`; a prior delete audit or Google 404/410 is a soft `already_deleted`. Overlap does **not** refuse a write. Morning recap does **not** write
- **Calendar Reader**: `list_calendar_events` for a parsed range (`list_events`); Slack `#family-plans` replies a day list or a day-grouped `format_recap`. No local calendar mirror
- **Morning recap**: scheduled today text plus full-month PNG (`run_morning_recap`); posts to the plans channel even on an empty day; one successful post per calendar date (JSON under `data/morning_recap/`). Not an orchestrator — a clock + the reader + a Slack poster. launchd 07:00 HKT is operator stretch. `calendar_board` builds immutable month cells and renders PNG bytes with Pillow and bundled TC fonts (live 07:00 uses british theme; classic remains the library default); no provider calls. Daily image markers are separate under `data/morning_recap/monthly_board/` ([ADR 0010](decisions/0010-monthly-calendar-board.md))
- **Important dates**: JSON catalog (`data/important_dates/`, class F, [ADR 0004](decisions/0004-important-dates-store.md)); Slack add/view; 10:00 HKT next-7-days note (`run_important_dates_review`); occurrence markers class C (`data/important_dates_posts/`). Not Calendar Writer. Not an orchestrator. launchd 10:00 is operator stretch
- **Google token reminder** (Phase 16, moved in Phase 30): 10:00 HKT system-status-channel notice when Testing refresh token expires in 3/2/1 HKT days, then once per day after expiry (`GOOGLE_REFRESH_TOKEN_ISSUED_AT` in `.env`; markers `data/google_token_reminders/`). Same 10:00 CLI as important dates. Not the Reminder Agent. Not a calendar write
- **Overlap checker**: `detect_create_overlaps` reuses the reader for the proposed `[start, end)` (default +1h). Same-person is exact casefold intersect; no aliases (`梓梵` ≠ Cedric); no invented emails. Proposal warning is bilingual 撞期 (times + titles); warn only, not a hard-block. Live Google client reconnects **once** on idle `BrokenPipeError` (Phase 19); overlap still makes one list call
- **LifeNotesKeeper** (parallel): exact `raw_text` + source metadata as `status=raw`; JSON under `data/life_notes/` (class F)
- **File logs** + gitignored `data/confirmations/`, `data/life_notes/`, `data/calendar_audit/` (class B, 90d), `data/morning_recap/` (class C posted-date markers), `data/important_dates/` (class F), `data/important_dates_posts/` (class C), `data/google_token_reminders/` (class C), and `data/parse_misses/` (class C, 90d)
- **Medic librarian** (Phase 32): `#jeff-aia-medic` Q&A from a redacted knowledge tree (`knowledge/jeff-aia-medic/` seed or `data/agent_librarian/jeff-aia-medic/`); offline `build` CLI; shared `redaction.redact_sensitive`; read-only, never Calendar
- **Mini control app** (Phase 31, optional `control` extra): LAN-only web app; status, read-only events and dates, Google re-authorization, guarded admin actions; `data/control/` (audit class B 90d, `.env` backups class C, users file mode 0600). Not an orchestrator; never a Calendar write
- Default suite offline / secret-free

Freebusy API is not started. Desktop OAuth for live smoke (project `cec-vivisystem`, scope `calendar.events`, Testing) is in local `.env` only (ground rule 13). Pytest never uses those tokens.

**Next:** Follow [maintenance handover](maintenance.md) for a deliberate Mini rollout and synthetic live smoke. Phase 21 is tested locally; no deployment is claimed. Prioritize the open reliability items in the signed review.

## 6. Future Evolution Rules

1. New components must ship meeting [unit-testing.md](unit-testing.md) and [logging-and-retention.md](logging-and-retention.md).
2. Persistent data must declare a retention class and purge path.
3. No component may become a hidden central orchestrator.
4. Every significant architectural decision must be recorded as an ADR in `docs/decisions/`.
5. We optimize for resilience and clarity over cleverness.
6. Future phase docs inherit ground rules 4–5 and §4.5–4.6 of this architecture; waivers require family decision + ADR.

---

*This document describes the intended direction. It will evolve as real components are born.*
