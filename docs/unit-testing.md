# Unit Testing Standard

Binding quality bar for **every component and every future phase**.  
Part of the permanent system spine: [ground-rules.md](ground-rules.md) · [resilience.md](resilience.md) · [architecture.md](architecture.md).  
Complements [logging-and-retention.md](logging-and-retention.md). Decision: [ADR 0003](decisions/0003-unit-testing-standard.md).

Phase-specific fixture tables live in that phase’s doc; **this document defines the permanent rules**.  
**Phases may narrow feature scope; they may not waive this document.**

## 1. Why this is permanent

Weekend-only development means regressions are expensive. Each component must prove:

1. Happy path works with realistic family inputs
2. Failure / ambiguous paths are explicit, not crashes
3. Contract fields and boundaries stay stable for the next component
4. No network or secrets required to run the suite on a cold machine

These rules apply to Phase 1, Phase 2, and all later phases unless a superseding ADR changes them.

## 2. Minimum suite shape (every component)

When a component is born or materially extended, its tests **must** include:

| Category | Requirement |
|----------|-------------|
| **Happy path** | At least one test that exercises the primary successful outcome with realistic input |
| **Failure / partial path** | At least one test for error, timeout simulation, unknown, or needs-clarification (as appropriate) — never “only happy path” |
| **Contract / shape** | Assert the public result or event exposes the documented fields (attributes or dict keys) |
| **No uncaught raise on garbage** | Nonsense input, empty/whitespace, or malformed payload returns a controlled result or typed error — does not crash the process |
| **Boundary logging (light)** | At least one test that invoking the public entry point completes with logging configured; optional capture of a boundary event — no brittle full-message equality |

External I/O components (Slack, Google Calendar) additionally require:

| Category | Requirement |
|----------|-------------|
| **Isolated from live APIs** | Unit tests use fakes/mocks/stubs; live API tests are optional and marked so default `pytest` stays offline |
| **Auth failure path** | At least one test for missing/invalid credentials or denied API class (mocked) |

## 3. Fixtures and determinism

1. **Commit fixtures** — representative inputs live in tests or `tests/fixtures/`, not only in a developer’s head.
2. **Family realism** — for user-facing language, include mixed Cantonese + English when the component handles messages.
3. **Fixed clocks** — any relative date/time logic accepts an injectable `now` (or clock); tests pin a fixed timezone-aware instant (family default: `Asia/Hong_Kong`).
4. **No wall-clock flakiness** — tests must not depend on “today” unless `now=` is injected.
5. **No network** in default unit tests.
6. **No LLM / paid API** required for the default green suite (unless a future ADR accepts a recorded-cassette approach).

## 4. Layout conventions

| Path | Role |
|------|------|
| `tests/test_<component>.py` | Primary unit tests for that component |
| `tests/fixtures/` | Shared phrases, payloads, golden files when tables get large |
| `tests/conftest.py` | Shared fixtures (e.g. logging setup) |

Phase 0 hello/logging tests remain; new components add files rather than overloading unrelated tests.

## 5. Phase definition obligation

**Every phase document** that introduces or extends a component must, **before implementation**, lock:

1. Named test cases or a fixture table (inputs → expected outcomes)
2. Explicit **non-tests** for that phase (what is deferred)
3. Minimum green bar count (approximate is fine)
4. Pointer that suite rules follow **this** standard + [logging-and-retention.md](logging-and-retention.md)

Example: Phase 1 locks F1–F5 + clarification + unknown in [phases/phase-1-parser.md](../phases/phase-1-parser.md). Later phases do the same for their component—not by copying Parser fixtures, but by applying the same structure.

## 6. TDD session order (recommended)

1. Define public API / contract
2. Write the phase’s locked tests (red)
3. Implement until green
4. `uv run pytest` and `uv run ruff check .`
5. Stop expanding fixtures mid-session unless a test reveals a contract bug

## 7. Definition of done (tests)

A component/phase is not done until:

- [ ] All locked phase tests pass
- [ ] Full suite (`uv run pytest`) passes offline after `uv sync`
- [ ] Happy + failure paths exist for the new code
- [ ] Contract fields asserted where a structured result exists
- [ ] Logging standard satisfied (boundary fields; see logging-and-retention)
- [ ] `PROGRESS.md` notes test count / result

## 8. Explicit non-goals of this standard

- Full end-to-end live Slack/Calendar in every PR
- Property-based fuzzing as a default gate
- 100% line coverage as a vanity metric
- Duplicating every phase’s fixture table into this file (phase docs own specifics)

---

*This standard is system-wide. Phase docs specialize it; they do not replace it.*
