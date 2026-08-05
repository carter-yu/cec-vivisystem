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
    └─────────────┘       └──────────────┘       └────────┬────────┘
                                                          │
                                                          ▼
                                                 ┌─────────────────┐
                                                 │ Calendar Writer │
                                                 └────────┬────────┘
                                                          │
           ┌──────────────────────────────────────────────┼──────────────┐
           │                                              │              │
           ▼                                              ▼              ▼
    ┌─────────────┐                              ┌──────────────┐ ┌─────────────┐
    │ Life Notes  │                              │   Reminder   │ │   Observer  │
    │ Keeper      │                              │   Agent      │ │  (Health)   │
    └─────────────┘                              └──────────────┘ └─────────────┘
```

Components communicate primarily through clear events and well-defined contracts rather than direct synchronous calls to a central controller.

## 3. Core Components (Target State)

| Component              | Responsibility                                      | Status      |
|------------------------|-----------------------------------------------------|-------------|
| Listener               | Receives messages from Slack (event-driven)         | Not started |
| Parser                 | Turns natural language (Cantonese/English) into structured intent | **Done (Phase 1)** |
| Availability Checker   | Queries free/busy time                              | Not started |
| Proposal Agent         | Generates human-readable confirmation messages      | Not started |
| Confirmation Guardian  | Tracks pending confirmations + timeouts             | Not started |
| Calendar Writer        | The only component allowed to write to Google Calendar | Not started |
| Life Notes Keeper      | Stores and retrieves unstructured / semi-structured family notes | Not started |
| Reminder Agent         | Posts the two standard reminders                    | Not started |
| Observer / Health      | Independent health and anomaly reporting            | Planned (Phase 0+) |

## 4. Key Architectural Decisions

### 4.1 Source of Truth
- **Time-based events** → Shared Google Calendar (primary)
- **Rich notes & life fragments** → Separate lightweight store (to be decided later)

### 4.2 Coordination Style
- Prefer **event-driven** and **message-based** interaction
- Avoid long-running central processes that become single points of failure
- Each component should be as replaceable and independently testable as possible

### 4.3 Human-in-the-Loop
Any action that creates, updates, or deletes a calendar event **must** go through an explicit confirmation step. This is a permanent design constraint, not a temporary limitation.

### 4.4 Growth Strategy
We only implement the next component when the previous vertical slice is stable in real use.  
We do not build the full diagram above in advance.

### 4.5 Cross-Cutting Quality Bars (all phases)

These are **architectural constraints**, not optional polish. They apply to every component in the diagram above and every future phase.

| Concern | Binding standard | ADR |
|---------|------------------|-----|
| Unit tests (happy, failure, contract, offline, determinism) | [unit-testing.md](unit-testing.md) | [0003](decisions/0003-unit-testing-standard.md) |
| Troubleshooting logs + data retention/purge | [logging-and-retention.md](logging-and-retention.md) | [0002](decisions/0002-logging-and-retention.md) |
| Resilience checklist | [resilience.md](resilience.md) | — |
| Family/process rules | [ground-rules.md](ground-rules.md) | — |

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

## 5. Current State (as of Phase 1 complete)

At present the system contains:
- Project structure, environment, logging/testing foundations, and system standards
- **Parser** (offline): `cec_vivisystem.parser.parse` → `ParseResult` (create_event / needs_clarification / unknown)
- Phase 1 unit tests (12 parser + 3 hello)

Slack, Calendar, Confirmation, and other swarm components are not started.

**Next**: decide Phase 2 scope only after real use of Parser fixtures/phrases; candidates include Listener or calendar read-only + confirmation path.

## 6. Future Evolution Rules

1. New components must ship meeting [unit-testing.md](unit-testing.md) and [logging-and-retention.md](logging-and-retention.md).
2. Persistent data must declare a retention class and purge path.
3. No component may become a hidden central orchestrator.
4. Every significant architectural decision must be recorded as an ADR in `docs/decisions/`.
5. We optimize for resilience and clarity over cleverness.
6. Future phase docs inherit ground rules 4–5 and §4.5–4.6 of this architecture; waivers require family decision + ADR.

---

*This document describes the intended direction. It will evolve as real components are born.*
