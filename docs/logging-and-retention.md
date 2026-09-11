# Logging, Troubleshooting & Data Retention

Binding operational standard for **every swarm component and every future phase**.  
Part of the permanent system spine: [ground-rules.md](ground-rules.md) · [resilience.md](resilience.md) · [architecture.md](architecture.md).  
Complements [unit-testing.md](unit-testing.md). Retention rationale: [ADR 0002](decisions/0002-logging-and-retention.md).

**Phases may narrow feature scope; they may not waive this document.**

## 1. Goals

1. **Troubleshoot in one weekend session** — enough signal to answer “what happened?” without replaying Slack or Calendar by hand.
2. **Bounded growth** — logs and operational data never grow without a purge path.
3. **Family privacy** — retain only what helps ops; do not keep message bodies forever.
4. **Component-local clarity** — each component logs its own boundary; no hidden central log orchestrator required (files/streams may still share a sink).

## 2. Logging requirements (all components)

### 2.1 Always log (structured fields)

Every component must emit structured logs (via `cec_vivisystem.logging`) with:

| Field | Required | Purpose |
|-------|----------|---------|
| `timestamp` | yes (ISO via structlog) | When |
| `level` | yes | Severity |
| `component` | yes | e.g. `parser`, `listener`, `calendar_writer` |
| `event` | yes | Stable verb name, e.g. `parse_started`, `write_succeeded` |
| `outcome` | on completion | `success` \| `failure` \| `partial` \| `skipped` |
| `correlation_id` | when a request/flow exists | Ties Listener → Parser → Confirmation → Writer |
| `duration_ms` | on completion of work units | Performance / hang detection |
| `error_type` / `error_message` | on failure | Visible failure mode (no silent swallow) |

Optional but recommended: `phase` (dev), `intent_type`, external ids (`slack_event_id`, `calendar_event_id`, `confirmation_id`).

### 2.2 Boundary events (minimum per call/message)

Each public entry point must log **at least**:

1. **Start** — received work (ids + safe summary, not secrets).
2. **End** — outcome + duration (+ error fields if failed).

External I/O (Slack, Google Calendar, disk stores) must also log attempt + result (status code / API error class, not full OAuth tokens).

### 2.3 Secrets and sensitive data

**Never log:**

- OAuth refresh/access tokens, API keys, `.env` values
- Full Slack signing secrets
- Passwords

**Log carefully (short retention class — see §3):**

- Raw user message text (`raw_text`) — allowed at DEBUG/INFO for troubleshooting; subject to **app log** retention, not audit forever
- Family names in participants — OK; this is a private family system

Prefer: message **length**, **hash or truncated preview** (e.g. first 80 chars) at INFO if full body is noisy; full body only when needed for parse debugging.

### 2.4 Levels

| Level | Use |
|-------|-----|
| `DEBUG` | Verbose parse internals, full payloads in dev |
| `INFO` | Boundary start/end, successful external calls, confirmations resolved |
| `WARNING` | Retries, timeouts approaching, clarification needed, degraded mode |
| `ERROR` | Failed unit of work after retries; operator should notice |
| `CRITICAL` | Component cannot run (auth broken, store unwritable) |

Default runtime: `LOG_LEVEL=INFO` (see `.env.example`).

---

## 3. Data classes and retention

Retention is measured from **event time** (or last update for mutable operational rows). After the period, data must be **purged** (deleted) or **compacted** (detail dropped, counters kept) by an explicit mechanism—manual script is acceptable until automation exists; unbounded “keep forever” is not.

| Data class | What it is | Retention | Purge action |
|------------|------------|-----------|--------------|
| **A. Application logs** | stdout/file structured logs from all components | **14 days** | Delete or rotate away files older than 14 days |
| **B. Audit trail** | Immutable-ish record of confirmation decisions + calendar create/update/delete attempts and results | **90 days** | Delete audit rows/files older than 90 days |
| **C. Operational state** | Pending confirmations, in-flight jobs, locks | **Until terminal state + 7 days** (max **30 days** absolute even if stuck) | Delete resolved/expired rows |
| **D. Dead letters** | Failed events kept for replay/debug | **30 days** | Delete or archive-then-delete |
| **E. Health snapshots** | Observer heartbeats, last-success timestamps | **14 days** detail; keep last-known status indefinitely (overwrite) | Drop old samples |
| **F. Life notes / user content** | Notes Keeper content the family intended to keep | **Until family deletes** (not auto-purged as “logs”) | Manual / product delete only |
| **G. Calendar events** | Live events in Google Calendar | **Owned by Google Calendar** (source of truth); we do not mirror full history locally in Phase 1+ | N/A — do not build a second calendar DB |

### 3.1 Why these numbers (summary)

- **14d logs**: covers “broke last weekend / this week” within one or two parent sessions.
- **90d audit**: enough to reconstruct “did we really create that appointment?” without storing chat forever.
- **7d post-terminal operational**: short tail for “confirmation disappeared” bugs.
- **30d dead letter**: enough to debug flaky Slack/Calendar without infinite queue growth.
- **Notes ≠ logs**: user content is not telemetry; different rules.

### 3.2 Volume caps (soft)

If a sink has no time-based rotation yet, apply soft caps so disk cannot fill unnoticed:

| Sink | Soft cap (home Mac Mini) |
|------|---------------------------|
| App log directory | **100 MB** total — rotate/delete oldest first |
| Audit store | **50 MB** or 90d, whichever hits first |
| Dead-letter store | **20 MB** or 30d |

Observer (when built) should WARNING when a cap is >80% used.

---

## 4. Per-component logging matrix

What “sufficient for troubleshooting” means for each target component. Implement when the component is born; do not stub unused components early.

### Listener (Slack)

| Event | Level | Include |
|-------|-------|---------|
| `message_received` | INFO | `correlation_id`, `slack_event_id`, `channel_id`, `user_id`, text length or truncated preview |
| `message_ignored` | INFO/DEBUG | reason (`bot_message`, `wrong_channel`, …) |
| `dispatch_succeeded` / `dispatch_failed` | INFO/ERROR | next component, `duration_ms`, error |
| Auth / webhook verify failure | ERROR | error class only |

### Parser

| Event | Level | Include |
|-------|-------|---------|
| `parse_started` | INFO | `correlation_id`, message length / preview |
| `parse_completed` | INFO | `intent_type`, `confidence`, `missing_fields`, `duration_ms`, `outcome` |
| `parse_failed` | ERROR | `error_type`, `error_message` (exceptions only; bad NL → completed with `unknown`) |

Phase 1 acceptance already requires a boundary log; fields above are the bar.

### Availability / overlap checker

| Event | Level | Include |
|-------|-------|---------|
| `overlap_check_started` | INFO | proposed time range, calendar id, `correlation_id` |
| `overlap_check_completed` | INFO | `overlap_count`, `same_person_count`, `duration_ms` |
| `overlap_check_failed` | ERROR | error class |
| `overlap_check_skipped` | INFO | reason (e.g. missing start) |
| `freebusy_query_started` / `completed` | INFO | *later — freebusy API not in Phase 8* |

**Data:** no local event store (class **G**). Logs class **A**. Overlap is a warn, not a write.

### Calendar Reader

| Event | Level | Include |
|-------|-------|---------|
| `list_attempt` | INFO | time range, calendar id, `correlation_id` |
| `list_succeeded` | INFO | event count, `duration_ms` |
| `list_failed` | ERROR | error class |

**Data:** no local event store (class **G**). Logs class **A**.

### Proposal Agent

| Event | Level | Include |
|-------|-------|---------|
| `proposal_built` | INFO | `confirmation_id`, intent summary (title, start), channel |
| `proposal_send_failed` | ERROR | target, error |

### Confirmation Guardian

| Event | Level | Include |
|-------|-------|---------|
| `confirmation_created` | INFO | `confirmation_id`, expiry, correlation |
| `confirmation_resolved` | INFO | accepted / rejected / expired, who, `duration` pending |
| `confirmation_timeout` | WARNING | `confirmation_id` |
| State load/save failures | ERROR | error |

**Data:** pending rows = class **C**; resolution records copy key fields into class **B** audit.

### Calendar Writer

| Event | Level | Include |
|-------|-------|---------|
| `write_attempt` | INFO | op (`create`/`update`/`delete`), `confirmation_id`, title, start, calendar id |
| `write_succeeded` | INFO | `calendar_event_id`, `duration_ms` |
| `write_skipped_already_created` | INFO | `confirmation_id`, existing `calendar_event_id` (Phase 13) |
| `write_failed` | ERROR | error class, retry count |
| Write **without** confirmation id | ERROR/CRITICAL | must not happen; log loud |

**Data:** every attempt + result → class **B** audit (90d). No bulk local clone of the calendar (class **G**).

### Life Notes Keeper

| Event | Level | Include |
|-------|-------|---------|
| `note_written` / `note_read` / `note_deleted` | INFO | note id, size, op — **not** necessarily full body at INFO |
| Store errors | ERROR | error |

**Data:** note bodies = class **F** (no auto 14d purge). Access logs still class **A**.

### Morning recap

| Event | Level | Include |
|-------|-------|---------|
| `morning_recap_started` | INFO | recap date, channel, today window, `correlation_id` |
| `morning_recap_posted` | INFO | recap date, `event_count`, `duration_ms` |
| `morning_recap_skipped` | INFO | recap date, reason (`already_posted`) |
| `morning_recap_failed` | ERROR | error class |

**Data:** posted-date markers = class **C** (`data/morning_recap/`, 30 days). App logs class **A**. Google events class **G** (no local mirror). Not a calendar write.

### Important dates

| Event | Level | Include |
|-------|-------|---------|
| `important_date_written` | INFO | `date_id`, kind, month, day, `duration_ms` |
| `important_date_already_exists` | INFO | `date_id` |
| `important_dates_listed` | INFO | `date_count` |
| `important_dates_review_started` | INFO | review date, window end, channel, `correlation_id` |
| `important_dates_review_posted` | INFO | `hit_count`, `duration_ms` |
| `important_dates_review_skipped` | INFO | reason (`no_new_hits`) |
| `important_dates_review_failed` | ERROR | error class |
| Store save/load failures | ERROR | error class |

**Data:** date rows = class **F** (`data/important_dates/`, until family deletes). Occurrence post markers = class **C** (`data/important_dates_posts/`, 30 days). App logs class **A**. Not a calendar write.

### Reminder Agent

| Event | Level | Include |
|-------|-------|---------|
| `reminder_sent` / `reminder_skipped` | INFO | reminder type, target, reason if skipped |
| Send failure | ERROR | error |

### Observer / Health

| Event | Level | Include |
|-------|-------|---------|
| `health_check` | DEBUG/INFO | component, status, last_success_at |
| `anomaly_detected` | WARNING | rule, evidence summary |
| Retention/cap pressure | WARNING | sink, usage |

Observer should eventually **report** retention compliance (last purge time, disk usage)—implementation when Observer exists.

---

## 5. Correlation

When a flow spans components, propagate a single `correlation_id` (UUID4 or Slack event id if unique enough).

- Generated at Listener (or at Parser if invoked standalone/CLI).
- Passed in event payloads between components.
- Present on every boundary log for that flow.

Standalone Phase 1 `parse()` may generate a correlation id per call or accept an optional one.

---

## 6. Local file logs + purge mechanisms

### 6.1 Layout (class A — implemented)

Root: **`logs/`** at the repository root (override with env `CEC_LOG_DIR`).  
Gitignored; never commit log bodies.

| Path | Role |
|------|------|
| `logs/{component}-YYYY-MM-DD.log` | Active file for that component on the **local calendar date** |
| `logs/archive/{component}-YYYY-MM-DD.log` | Prior days moved here on service start |
| `component` | From structured field `component` (e.g. `listener`, `parser`, `hello`, `system`) |

Example: `logs/listener-2026-08-08.log`, `logs/parser-2026-08-08.log`.

Stdout is still used for interactive runs (Socket Mode terminal). File lines use a key=value renderer; console keeps the human ConsoleRenderer.

### 6.2 Startup archive + purge (every service start)

Whenever a process calls `setup_logging(..., enable_file_logging=True)` (default for real runs of Listener / hello / parser CLI):

1. **Archive** — move `logs/*-YYYY-MM-DD.log` with date **before today** → `logs/archive/`.
2. **Purge** — delete active + archive files older than **14 days** (class A).
3. **Soft cap** — if total size of `logs/` + `logs/archive/` exceeds **100 MB**, delete oldest files first.

Implementation: `cec_vivisystem.logging.maintain_log_storage` + `setup_logging`.  
Disable files in tests automatically; force off with `CEC_LOG_TO_FILE=0`.

| Stage | What we do |
|-------|------------|
| **Now** | Dated per-component files + startup archive/purge (above). |
| **When audit/dead-letter stores appear** | Each store documents retention in its module docstring; call `maintain_*` on service start (Phase 6: `maintain_calendar_audit_storage`). A combined `scripts/purge_expired_data.py` is still optional. |
| **Later** | Optional cron/launchd calling the same purge; Observer warns if purge has not run within 7 days while stores are non-empty. |

**Rule:** any new persistent store PR must state its **data class (A–G)** and retention in the module docs or an ADR update. No store without a purge story.

---

## 7. Checklist for new components

Before marking a component “done”:

- [ ] `component` + `event` + start/end boundary logs
- [ ] Failures log `error_type` / message; nothing swallowed
- [ ] No secrets in logs
- [ ] `correlation_id` if multi-step
- [ ] Persistent data classified A–G with retention honored or purge stub listed in PROGRESS/resilience backlog
- [ ] Tests still cover happy + failure path (resilience.md)

---

## 8. Applying this standard in each phase

When a phase introduces a component:

1. Map required events from §4 (or add rows if a new component type appears—update this doc in the same phase).
2. State which retention classes (A–G) the phase creates or touches.
3. If a new durable store appears, document purge before merge.
4. Acceptance criteria must require boundary logs + classified data; copy specifics into the phase doc.

### 8.1 Phase 1 (Parser) — first application

| Requirement | Phase 1 bar |
|-------------|-------------|
| Boundary logs | `parse_started` / `parse_completed` (or single completed with outcome) |
| Fields | `component=parser`, `intent_type`, `outcome`, `duration_ms`; preview or length of input |
| Retention | Class **A** only (no durable parse store required) |
| Purge | Class A files via startup `maintain_log_storage` (see §6) |
| Correlation | Optional `correlation_id` on `parse()` |

Do not build audit DB or purge cron in Phase 1. Later phases (Listener, Confirmation, Calendar Writer, …) inherit the full matrix in §4.

### 8.2 Phase 5A (LifeNotesKeeper) — class F store

| Requirement | Phase 5A bar |
|-------------|--------------|
| Boundary logs | `note_create_started` / `note_written` (success) or `note_create_failed` |
| Fields | `component=life_notes`, `outcome`, `duration_ms`, `note_id`, size / preview (not full body at INFO) |
| Retention | Note bodies = class **F** (until family deletes). Access logs = class **A**. |
| Purge | **No auto-purge** of notes (user content ≠ logs). Product/manual delete later. Store errors logged. |
| Correlation | Optional `correlation_id` on `create_life_note()` |

### 8.3 Phase 5B (Listener → LifeNotesKeeper)

| Requirement | Phase 5B bar |
|-------------|--------------|
| Boundary logs | Listener `message_received` + `dispatch_succeeded` / `dispatch_failed` with `next_component=life_notes`; keeper logs unchanged |
| Fields | `component=listener`, `correlation_id`, channel/user ids, preview/length; never tokens |
| Retention | Note bodies still class **F**. Listener logs class **A**. No new store. |
| Purge | Unchanged from 5A (no auto-purge of notes) |
| Correlation | Generated at Listener; passed into `create_life_note` |

### 8.4 Phase 4b (Listener → Confirmation)

| Requirement | Phase 4b bar |
|-------------|--------------|
| Boundary logs | Listener `dispatch_succeeded` / `dispatch_failed` with `next_component=confirmation`; keeper events unchanged |
| Fields | `confirmation_id` / `status` on resolve dispatch; never tokens |
| Retention | Class **C** store unchanged; `maintain_confirmation_storage` on Socket Mode start |
| Purge | Existing terminal+7d / pending max 30d |
| Correlation | Listener corr passed into `create_confirmation` |

### 8.5 Phase 6 (Calendar Writer)

| Requirement | Phase 6 bar |
|-------------|-------------|
| Boundary logs | `write_attempt` / `write_succeeded` / `write_failed` / `write_skipped_already_created`; `write_without_confirmation_id` at ERROR/CRITICAL |
| Fields | `component=calendar_writer`, `op=create`, `confirmation_id`, title, start, calendar id, `calendar_event_id`, `outcome`, `duration_ms`; never tokens |
| Retention | Write attempt + result = class **B** (`data/calendar_audit/`, 90 days). App logs class **A**. Google Calendar events class **G** (no local mirror). |
| Purge | `purge_calendar_audit` / `maintain_calendar_audit_storage` on Socket Mode start; 50 MB soft cap |
| Correlation | From confirmation `correlation_id` (Listener → Parser → Confirmation → Writer) |

### 8.6 Phase 7 (Calendar Reader)

| Requirement | Phase 7 bar |
|-------------|-------------|
| Boundary logs | `list_attempt` / `list_succeeded` / `list_failed` |
| Fields | `component=calendar_reader`, calendar id, time range, `event_count`, `duration_ms`; never tokens |
| Retention | Class **A** logs. Google events class **G** (no local mirror). |
| Purge | Existing class A file purge |
| Correlation | Listener corr passed into `list_calendar_events` |

### 8.7 Phase 8 (Overlap / same-person warn)

| Requirement | Phase 8 bar |
|-------------|-------------|
| Boundary logs | `overlap_check_started` / `overlap_check_completed` / `overlap_check_failed` / `overlap_check_skipped` |
| Fields | `component=overlap`, calendar id, proposed time range, `overlap_count`, `same_person_count`, `outcome`, `duration_ms`; never tokens |
| Retention | Class **A** logs only. Google events class **G** (no local mirror). Confirmations (class **C**) may store warning text in `proposal_text`. No new store. |
| Purge | Existing class A file purge |
| Correlation | Listener corr passed into `detect_create_overlaps` → `list_calendar_events` |

### 8.8 Phase 9 (Parser family aliases)

| Requirement | Phase 9 bar |
|-------------|-------------|
| Boundary logs | Existing `parse_started` / `parse_completed` (no new events) |
| Fields | Unchanged `component=parser` |
| Retention | Class **A** only. No new store |
| Purge | Existing class A file purge |
| Correlation | Unchanged optional `correlation_id` on `parse()` |

### 8.9 Phase 12 (Morning recap)

| Requirement | Phase 12 bar |
|-------------|--------------|
| Boundary logs | `morning_recap_started` / `morning_recap_posted` / `morning_recap_skipped` / `morning_recap_failed`; Listener list path still `dispatch_succeeded` (or `dispatch_failed` + still a reply) |
| Fields | `component=morning_recap`, recap date, channel, `event_count`, `outcome`, `duration_ms`; never tokens |
| Retention | Posted-date JSON = class **C** (`data/morning_recap/`, 30 days). App logs class **A**. Google events class **G**. |
| Purge | `maintain_morning_recap_storage` on CLI start |
| Correlation | Generated at `run_morning_recap`; passed into `list_calendar_events` |

### 8.10 Phase 13 (Idempotent Writer + bilingual 撞期)

| Requirement | Phase 13 bar |
|-------------|--------------|
| Boundary logs | `write_skipped_already_created` when the same `confirmation_id` already has a successful create; overlap warning still uses Phase 8 events |
| Fields | `component=calendar_writer`, `confirmation_id`, existing `calendar_event_id`, `outcome=skipped`; never tokens |
| Retention | Class **B** audit includes `already_created` rows (90 days). No new store |
| Purge | Existing `maintain_calendar_audit_storage` |
| Correlation | From confirmation `correlation_id` |

### 8.11 Phase 15 (Important dates)

| Requirement | Phase 15 bar |
|-------------|--------------|
| Boundary logs | `important_date_written` / `important_date_already_exists` / `important_dates_listed` / `important_dates_review_started` / `important_dates_review_posted` / `important_dates_review_skipped` / `important_dates_review_failed` |
| Fields | `component=important_dates`, `date_id`, kind, month/day, `hit_count`, `outcome`, `duration_ms`; never tokens |
| Retention | Rows class **F** (`data/important_dates/`). Post markers class **C** (`data/important_dates_posts/`, 30 days). App logs class **A** |
| Purge | `maintain_important_dates_post_storage` on CLI start; rows until family deletes |
| Correlation | Listener corr on add; generated at `run_important_dates_review` |
