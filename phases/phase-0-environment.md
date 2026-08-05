# Phase 0 – Environment & Foundations

**Inherits (non-waivable for later phases)**  
System spine established after Phase 0; all **future** phases must follow:

- [Unit Testing Standard](../docs/unit-testing.md)
- [Logging & retention](../docs/logging-and-retention.md)
- [Ground rules](../docs/ground-rules.md) · [Resilience](../docs/resilience.md) · [Architecture](../docs/architecture.md)

Phase 0 itself only bootstrapped pytest + structlog; full per-component bars apply starting Phase 1.

**Goal**  
Create a clean, reproducible development environment on the Mac Mini so that future sessions can focus on real capability.

**Time box**  
1–2 hours

## Acceptance Criteria

- [x] Repository structure created
- [x] Python 3.12 pinned
- [x] Dependency management with `uv` + lock file
- [x] `pytest` runs and passes (minimum 3 meaningful tests)
- [x] Structured logging works
- [x] `ruff` configured for linting and formatting
- [x] `.env.example` present
- [x] `PROGRESS.md` updated with this session
- [x] One ADR written explaining tooling choices
- [x] A simple `hello` module can be executed and produces a log line

## Out of Scope
Slack, Google Calendar, any real agent logic, database.
