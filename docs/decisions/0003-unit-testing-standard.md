# ADR 0003: System-Wide Unit Testing Standard

## Status
Accepted

## Context
Phase 1 defined a concrete unit test plan (fixtures, determinism, happy/failure/contract cases). Those patterns are not Parser-specific: without elevating them, later phases risk shipping components with only smoke tests or live-API-only checks, which breaks weekend-safe development and resilience goals.

## Decision
1. Adopt a permanent **Unit Testing Standard** in [docs/unit-testing.md](../unit-testing.md).
2. Bind it via ground rules, resilience, and architecture so **every future phase and component** must comply.
3. Require each phase doc to lock its own fixture/test table before implementation, following the standard’s structure (happy, failure, contract, no-raise, light log boundary; offline by default).
4. Phase-specific examples (e.g. Parser F1–F5) remain in phase docs; the standard owns the cross-cutting rules.

## Consequences
- Slightly more upfront test design per phase; fewer regressions and clearer contracts between swarm components.
- Default `pytest` remains runnable without secrets or network.
- Non-compliance is a phase acceptance failure, not a nice-to-have.
