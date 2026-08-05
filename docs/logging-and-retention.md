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

### Availability Checker

| Event | Level | Include |
|-------|-------|---------|
| `freebusy_query_started` / `completed` | INFO | time range, calendar id(s), `duration_ms` |
| API errors / partial calendars | WARNING/ERROR | error class, which calendar |

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
| `write_failed` | ERROR | error class, retry count |
| Write **without** confirmation id | ERROR/CRITICAL | must not happen; log loud |

**Data:** every attempt + result → class **B** audit (90d). No bulk local clone of the calendar (class **G**).

### Life Notes Keeper

| Event | Level | Include |
|-------|-------|---------|
| `note_written` / `note_read` / `note_deleted` | INFO | note id, size, op — **not** necessarily full body at INFO |
| Store errors | ERROR | error |

**Data:** note bodies = class **F** (no auto 14d purge). Access logs still class **A**.

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

## 6. Purge mechanisms (evolution)

| Stage | What we do |
|-------|------------|
| **Now (Phase 0–1)** | Stdout logging only is fine; document retention. No multi-GB stores yet. |
| **When file logs appear** | Size + time rotation (e.g. daily files, delete >14d). Prefer stdlib/`logging` handlers or a tiny rotate script under `scripts/`. |
| **When audit/dead-letter stores appear** | Each store documents retention in its module docstring; provide `scripts/purge_expired_data.py` (or equivalent) runnable by hand after a weekend session. |
| **Later** | Optional cron/launchd calling the same purge script; Observer warns if purge has not run within 7 days while stores are non-empty. |

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
| Purge | N/A until file logging exists |
| Correlation | Optional `correlation_id` on `parse()` |

Do not build audit DB or purge cron in Phase 1. Later phases (Listener, Confirmation, Calendar Writer, …) inherit the full matrix in §4.
