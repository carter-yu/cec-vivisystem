# Phase 4 – Confirmation Path (Thin Human Gate)

**Inherits (non-waivable)**  
- [Unit Testing Standard](../docs/unit-testing.md) · [ADR 0003](../docs/decisions/0003-unit-testing-standard.md)  
- [Logging & retention](../docs/logging-and-retention.md) · [ADR 0002](../docs/decisions/0002-logging-and-retention.md)  
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)  

This phase specializes unit testing + logging/retention for the **Confirmation Guardian** (and a minimal proposal text builder). It does **not** waive system standards.

**Goal**  
Ship a thin **human confirmation** slice: given a `create_event` `ParseResult`, create a **pending confirmation**, render a clear proposal, and resolve it with **accept / reject / expire**. **No Google Calendar read or write.** No LLM.

**Why this phase (decision)**  
Phases 1–3 make intake + understanding work on Slack. Real calendar use is still blocked by ground rule 6 (explicit confirmation before any CUD). Phase 4 must be:

1. **The permanent write gate** — nothing reaches Calendar Writer later without a confirmation id.
2. **Offline-testable first** — pending store + resolve logic with injectable clock; Slack I/O at the edges.
3. **Retention-complete** — first durable operational store (class **C**) ships with a purge story.
4. **Thin** — freebusy and Calendar Writer stay out (discussed; deferred so this weekend can finish).

**Time box**  
1–2 hours. Prefer green offline suite + purge path over polished Slack UX. If Slack yes/no wiring does not fit, document it as stretch and still meet core acceptance.

## In Scope

### Components

#### 1. Proposal text (minimal — may live in `confirmation.py` or tiny `proposal.py`)

- **Input**: `ParseResult` with `intent_type == create_event`.
- **Output**: human-readable proposal string (English OK for Phase 4) that:
  - Summarizes title, start, participants, location if present
  - Asks for yes/no (or accept/reject)
  - **Never** claims the event was written to Google Calendar

#### 2. Confirmation Guardian (core)

- **Create** pending confirmation from a `create_event` parse (+ optional Slack channel/thread/user metadata for later reply).
- **Resolve**: `accept` | `reject` | `expire` (timeout).
- **Ignore / do not create** pending for `needs_clarification` or `unknown` (caller may still reply with existing Listener summary).
- **Idempotency**: resolving an already-terminal confirmation is a controlled no-op or explicit error outcome (document + test)—must not crash.
- **Clock**: injectable `now=` for expiry (family TZ `Asia/Hong_Kong`).

### Minimum data contract

Documented in code (typed models) and covered by tests. Names may vary; semantics are the bar.

**`ConfirmationStatus`**: `pending` | `accepted` | `rejected` | `expired`

**`Confirmation` / pending record (minimum fields):**

| Field | Notes |
|-------|--------|
| `confirmation_id` | Stable id (uuid string OK) |
| `status` | See above |
| `correlation_id` | Ties Listener → Parser → Confirmation |
| `parse_result` | Snapshot or enough fields to rebuild proposal (title, start, …) |
| `proposal_text` | Text shown / to show to family |
| `created_at` / `expires_at` | timezone-aware |
| `resolved_at` | set when terminal |
| `resolved_by` | optional (e.g. Slack user id or `"system"` on expire) |
| `channel_id` / `thread_ts` | optional Slack anchors for Phase 4 stretch |

### Public entry points (testable core)

Prefer pure-ish APIs so default `pytest` needs no Slack/Google:

1. **`build_proposal(parse_result: ParseResult) -> str`**
2. **`create_confirmation(parse_result, *, now=, correlation_id=, ttl=, store=, …) -> Confirmation`**  
   - Rejects / raises typed error / returns failure if not `create_event`
3. **`resolve_confirmation(confirmation_id, decision, *, now=, actor=, store=) -> Confirmation`**  
   - `decision` in `accept` | `reject`
4. **`expire_due_confirmations(*, now=, store=) -> list[Confirmation]`**  
   - Marks pending past `expires_at` as `expired`
5. **Store protocol** (file or in-memory): `save` / `get` / `list_pending` / `purge_expired`  
   - Default production: simple JSON/JSONL under a gitignored path (e.g. `data/confirmations/`) with purge on startup or explicit `purge` call from service start
   - Tests: in-memory fake store

### Slack integration (stretch within Phase 4)

**Minimum for “live useful” if time remains:**

- After Listener gets `create_event`, create confirmation + post **proposal** (not only parse summary).
- Treat short replies in the same thread (`yes` / `no` / `y` / `n` / `ok` / `不要` — keep list small and documented) as resolve accept/reject.

**Not required for offline acceptance** if documented as stretch: full Listener wiring can land in a follow-up session as Phase 4b without changing the core contract.

### Implementation constraints

- New module(s) under `src/cec_vivisystem/` (e.g. `confirmation.py`, models in `models.py`).
- **No** Google Calendar client; **no** freebusy; **no** Calendar Writer.
- **No** LLM.
- Structured logs per Confirmation matrix in [logging-and-retention.md](../docs/logging-and-retention.md) §4:  
  `component=confirmation` (or `confirmation_guardian`),  
  `confirmation_created` / `confirmation_resolved` / `confirmation_timeout`, load/save failures.
- Default TTL: **e.g. 24 hours** from `created_at` (document exact value in code); injectable for tests.
- Secrets: unchanged (ground rule 13). No new Google env vars required this phase.

### Docs / process

- Update architecture: Confirmation Guardian → In progress / Done; Proposal Agent may be “minimal / folded into confirmation” for Phase 4.
- `PROGRESS.md` + README phase link.
- ADR only if store format choice is non-obvious (skip ADR for “JSON files under data/”).

## Out of Scope

| Item | Why later |
|------|-----------|
| Calendar Writer / any calendar CUD | Requires this gate first; separate phase |
| Calendar read-only / freebusy | Explicitly deferred (Option C); not this phase |
| Full Proposal Agent productization | Minimal `build_proposal` is enough |
| LLM | Grow from need + ADR |
| Multi-intent confirmations (cancel/update) | create_event only |
| Production DB / Postgres | File or memory store OK for family Mac Mini |
| Always-on daemon hardening | Weekend-local |

## Unit test plan (locked for implementation)

Authoritative Phase 4 test decision. Implements [unit-testing.md](../docs/unit-testing.md).

### Layout

| Path | Role |
|------|------|
| `tests/test_confirmation.py` | Phase 4 confirmation + proposal unit tests |
| In-memory store fixture | Shared in `conftest` or test module |

Keep Phase 0–3 suites green unchanged.

### Determinism

1. Fixed `now=` for create/expire (e.g. `FIXED_NOW = 2026-08-08 12:00 Asia/Hong_Kong`).
2. Fixed TTL in tests (e.g. 1 hour) so expiry is exact.
3. No network, no Slack, no Google in default suite.
4. Use real offline `parse(...)` for a known create_event fixture (e.g. Phase 1 F1) **or** a constructed `ParseResult` — either OK; prefer one real parse for integration confidence.

### Fixtures / cases

| ID | Scenario | Expect |
|----|----------|--------|
| C1 | `build_proposal` on create_event (F1 or equivalent) | Non-empty text; includes title or start signal; does **not** claim calendar write |
| C2 | `create_confirmation` from create_event | `status=pending`; id set; `expires_at == created_at + ttl`; proposal_text set; `confirmation_created` path completes |
| C3 | `create_confirmation` from needs_clarification / unknown | Controlled refusal (no pending row) — typed result or exception; document which |
| C4 | `resolve` accept | `status=accepted`; `resolved_at` set; not pending |
| C5 | `resolve` reject | `status=rejected` |
| C6 | `expire_due` after ttl | pending past expiry → `expired`; log timeout/resolve path |
| C7 | Double resolve / resolve after expired | Controlled outcome; no crash |
| C8 | Contract fields present on Confirmation | Attributes listed in data contract |
| C9 | Purge: terminal rows older than retention rule removable | `purge` deletes/eligible rows; pending within max age kept (see retention below) |
| C10 | Log boundary | create + resolve completes with logging configured |

### Named tests (implement these)

- `test_build_proposal_create_event_no_calendar_claim` → C1  
- `test_create_confirmation_pending` → C2  
- `test_create_confirmation_rejects_non_create_event` → C3  
- `test_resolve_accept` → C4  
- `test_resolve_reject` → C5  
- `test_expire_due_confirmations` → C6  
- `test_resolve_idempotent_or_safe_when_terminal` → C7  
- `test_confirmation_contract_fields` → C8  
- `test_purge_terminal_confirmations` → C9  
- `test_confirmation_logs_boundary` → C10  

### Explicit non-tests

- Live Slack Socket Mode / real thread yes-no  
- Google freebusy / OAuth  
- Calendar Writer  
- LLM  
- Concurrent multi-writer stress  
- Perfect bilingual proposal wording  

### Minimum green bar

| Category | Min tests |
|----------|-----------|
| Proposal + create + reject non-create | 3 |
| Accept / reject / expire | 3 |
| Idempotent + contract + purge + log | 4 |
| **Total new** | **~10** |

Full suite ≈ 46 after Phase 4.

### Implementation order (TDD)

1. Models + in-memory store + red tests C1–C10.  
2. Green `build_proposal` / create / resolve / expire / purge.  
3. Optional file store + startup purge hook.  
4. Stretch: Listener creates confirmation + posts proposal; thread yes/no.  
5. Lint, pytest, docs.

## Logging & retention applicability

| Data | Class | Phase 4 action |
|------|-------|----------------|
| App logs | **A** | Existing file logging; confirmation boundary events |
| Pending / resolved confirmation records | **C** operational | **Until terminal + 7 days** (max **30 days** absolute even if stuck). Purge function + call on service start (or documented manual script). |
| Resolution summary copied to audit | **B** | **Optional in Phase 4** — if not a separate audit file, rely on class A logs + retained confirmation row until C purge; do **not** keep forever |
| Calendar / freebusy | — | Not introduced |
| Secrets | — | Unchanged |

**No store without purge story** — Phase 4 acceptance requires an explicit `purge` (or `maintain_confirmation_storage`) covered by C9.

Soft disk awareness: confirmation store should stay tiny; if a directory soft-cap is easy, align with logging soft-cap spirit (optional).

## Acceptance Criteria

- [x] Confirmation module(s) with testable create / resolve / expire / proposal APIs
- [x] Pending store with retention class **C** + purge path (tested)
- [x] Only `create_event` creates pending; non-create controlled refusal
- [x] Proposal text never claims calendar write
- [x] Unit tests match plan (~10); offline and secret-free
- [x] `uv run pytest` passes; `uv run ruff check .` clean on touched code
- [x] Boundary logs: created / resolved / timeout (as applicable)
- [x] Architecture + `PROGRESS.md` + README updated
- [x] **No** Google Calendar dependency in this phase
- [x] Stretch (optional): Slack proposal + yes/no in `#family-plans` documented if shipped — **done in [Phase 4b](phase-4b-slack-confirmation.md)**

### Implementation note (2026-08-22)

Core offline path shipped: `cec_vivisystem.confirmation` + `InMemoryConfirmationStore` /
`JsonDirConfirmationStore` under gitignored `data/confirmations/`. Slack thread yes/no
remains **stretch / Phase 4b** — not required for Phase 4 acceptance.

## Suggested session order (implementation)

1. Red tests + models + memory store.  
2. Green core guardian + purge.  
3. Wire optional file store under gitignored `data/`.  
4. Stretch Slack; otherwise stop at offline green.  
5. Docs / PROGRESS.

## Success definition

Phase 4 is **done** when a `create_event` parse can become a **pending confirmation**, be **accepted or rejected or expired** under test, with **purge** for class C data—and **zero** calendar writes. That confirmation id becomes the required ticket for a later Calendar Writer phase.

## Explicit non-goal

Do not implement freebusy or Calendar Writer “while wiring confirmation.” One gate, one store, green tests.
