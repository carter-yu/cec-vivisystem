# Phase 21 — Takeover review and reliability fixes

Date: 2026-09-19. Author: OpenAI GPT-6 Astra (Codex; Carter's AI assistant).

## Goal and scope

Review requirements, architecture, all production components, existing tests,
and operational boundaries. Fix demonstrated correctness defects while preserving
the confirmation gate, rules-first parsing, and existing JSON schemas. One review
session; deployment and new roadmap components are outside this phase.

Inherits [ground rules](../docs/ground-rules.md), [testing](../docs/unit-testing.md),
and [logging/retention](../docs/logging-and-retention.md).

## Locked regression plan (before implementation)

| Case | Required result |
| --- | --- |
| Google insert commits then loses its response | Retry uses the same event ID; verified conflict recovers the existing event |
| Google conflict belongs to another confirmation or deleted event | Controlled failure; never claim successful creation |
| Google list has multiple pages / empty intermediate page | Return all events; page failure fails the whole read |
| Expired pending confirmation resolved directly | Persist expiry and refuse acceptance |
| Proposal has explicit or default duration | Show the effective end being confirmed |
| Duplicate Slack create delivery / raw note delivery | Reuse stored record; preserve terminal confirmation state |
| Calendar unavailable or retry fails | User receives truthful status, never an unverified success |
| Explicit morning colon time | Morning beats today's afternoon heuristic |
| Invalid AM/PM clock / reversed explicit date range | Controlled clarification or unknown; no fabricated valid date |
| Yearly leap-day marker added in a non-leap year | Store February 29 as yearly |
| Incomplete list / important-date request | No create-only LLM call |
| LLM missing all-day start, invalid boolean, reversed end, missing fields | No invalid create proposal |
| Parse-miss store unavailable | Keep useful parse result and log diagnostic failure |
| JSON replacement fails | Original data intact; temporary file removed |
| Log sink cannot open / daily rollover | Console survives; old file handles close; retention runs |

Minimum bar: existing 234 tests plus at least 25 regression cases, all offline,
Ruff clean, and boundary-log checks. Use synthetic fixtures and fixed HKT clocks.
Non-tests: live Slack posting, real Calendar writes, paid model inference,
distributed failover, sudden power loss, and Mini deployment.

## Logging and retention

Keep existing component logs and data classes: A logs, B calendar audit,
C confirmations/miss rows/post markers, F notes and important dates.
Atomic writes use ephemeral sibling temporary files, removed on normal failure.
No new durable database. Provider event IDs remain in Google (class G).
Document external-side-effect ambiguity and remaining crash windows explicitly.

## Acceptance

Deliver a signed review with requirement traceability, fixes, evidence, remaining
limitations, and a maintenance handover. Update PROGRESS and current architecture.


## Additional contract regressions discovered during implementation

The baseline tests also exposed or prompted direct checks of supported vocabulary
rejection through fallback, all-day midnight consistency, expired-thread replies,
concurrent duplicate dispatch, non-create Writer refusal, visible parse/store
failures, archive collisions, and clipping multi-day events to the recap window.
These were added as focused contract regressions rather than new product features.

## Completion evidence

- Baseline: 234 passing tests; first new batch reproduced 27 failures.
- Final: 281 passing tests (47 new regression cases), Ruff clean, diff whitespace clean.
- Signed review, maintenance notes, ADR 0006, root AGENTS, README/architecture,
  logging notes, and PROGRESS updated.
- No live API smoke or deployment performed. Remaining operational risks are
  recorded in the review; they are not represented as solved by atomic files.
