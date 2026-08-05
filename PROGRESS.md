# Progress Log – cec-vivisystem

## How to use
Add a new entry at the top after every session (below this section, above older entries).

---

## 2026-08-05 (implementation)

- **Phase**: 1 – Natural Language Parser (implemented)
- **Completed**:
  - `src/cec_vivisystem/models.py` — `IntentType`, `Confidence`, `ParseResult` (Phase 1 contract)
  - `src/cec_vivisystem/parser.py` — offline rule/heuristic `parse(message, *, now=, correlation_id=)`; weekday policy documented in module
  - Boundary logs: `parse_started` / `parse_completed` with `component`, `outcome`, `intent_type`, `duration_ms`, preview (structlog event name = boundary event)
  - `tests/test_parser.py` — F1–F5 create-event, 2 clarification, 2 unknown, contract + garbage + log boundary
  - CLI smoke: `uv run python -c "from cec_vivisystem.parser import main; main()"`
  - Acceptance criteria checked in `phases/phase-1-parser.md`; architecture Parser → Done
- **Tests**: 15 passed (12 parser + 3 hello); ruff clean
- **Issues / Friction**: structlog reserves keyword `event` — use event name as the log message positional arg
- **Resilience notes**: No network/LLM; parse never raises on garbage; class A logs only (no durable parse store)
- **Next session plan**: Decide Phase 2 scope only (do not implement Listener/Calendar until scope is explicit)
- **Session status**: Phase 1 implementation complete

---

## 2026-08-05

- **Phase**: 1 – scope + unit test plan + logging/retention standard (no swarm implementation)
- **Completed**:
  - Decided Phase 1: first swarm component = offline **Natural Language Parser** (create-event intent)
  - Wrote [phases/phase-1-parser.md](phases/phase-1-parser.md) with goal, contract fields, acceptance criteria, and explicit out-of-scope
  - **Locked unit test plan** in the same phase doc: ~11–12 tests in `tests/test_parser.py`
    - F1–F5 create-event fixtures (mixed Canto/English; fixed `now=2026-08-08 12:00 Asia/Hong_Kong`)
    - 2 needs_clarification, 2 unknown/empty, contract + no-raise + log boundary
    - Explicit non-tests: Slack, Calendar, LLM mocks, fuzzing
  - **Logging & retention standard** ([docs/logging-and-retention.md](docs/logging-and-retention.md), [ADR 0002](docs/decisions/0002-logging-and-retention.md)):
    - Per-component boundary log matrix (troubleshoot without replaying Slack/Calendar)
    - Retention: app logs 14d; audit 90d; pending state terminal+7d (max 30d); dead letters 30d; notes until family deletes
    - No store without purge story; soft disk caps; secrets never logged
  - **Elevated to system spine (all future phases)**:
    - [docs/unit-testing.md](docs/unit-testing.md) + [ADR 0003](docs/decisions/0003-unit-testing-standard.md)
    - Ground rules 4–5 + 12: unit tests + logging/retention mandatory; phases cannot waive
    - Resilience rewritten as permanent checklist (tests + logs + retention + calendar write gate)
    - Architecture §4.5–4.6: cross-cutting quality bars + phase document contract
    - Phase 0/1 headers + README: inherit non-waivable standards
  - Rationale: highest leverage before Listener/Calendar; fully offline so OAuth/Slack cannot burn the 1–2h session; contract-first for later components
- **Tests**: unchanged in code (plan only; Phase 0 still 3 passed)
- **Issues / Friction**: none
- **Resilience notes**: Unit testing + log/retention are now permanent system rules, not Phase 1 one-offs. Phase 1 still forbids calendar writers; parser ships with happy + failure paths and boundary logs (class A only).
- **Next session plan**: TDD implement Phase 1 — red tests from unit test plan, then green parser with logging fields from the standard. Do not start Slack or Google Calendar until Parser acceptance criteria are met.
- **Session status**: Scope + tests + log/retention policy locked as **system-wide** standards; ready for implementation weekend

---

## 2026-07-26

- **Phase**: 0 – Environment & Foundations
- **Completed**:
  - Project structure and core docs (philosophy, ground rules, architecture, resilience, ADR 0001)
  - Python 3.12 pinned; `uv` + lock file; `ruff` and `pytest` configured
  - Hatchling package install for `src/cec_vivisystem`
  - `src/cec_vivisystem/logging.py` (structlog) and `hello.py` proof-of-life module
  - `.env.example` present
  - Three unit tests passing; structured logging verified
- **Tests**: 3 passed
- **Issues / Friction**: Package import / editable install friction early on (resolved with hatchling build config)
- **Resilience notes**: Logging and tests in place from day one; no calendar writers yet
- **Repo**: Pushed to `carter-yu/cec-vivisystem` on GitHub
- **Next session plan**: Decide Phase 1 scope only; do not implement swarm components until that decision is explicit
- **Session status**: Closed for this weekend

---

## Template
### YYYY-MM-DD
- **Phase**:
- **Completed**:
- **Tests**:
- **Issues / Friction**:
- **Resilience notes**:
- **Next session plan**:
