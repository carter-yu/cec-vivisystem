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
| Parser                 | Turns natural language (Cantonese/English) into structured intent | Not started |
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

## 5. Current State (as of Phase 0)

At present the system only contains:
- Project structure
- Development environment
- Basic logging and testing foundations
- Documentation and ground rules

No runtime components of the swarm exist yet.

## 6. Future Evolution Rules

1. New components must ship with tests and structured logging.
2. No component may become a hidden central orchestrator.
3. Every significant architectural decision must be recorded as an ADR in `docs/decisions/`.
4. We optimize for resilience and clarity over cleverness.

---

*This document describes the intended direction. It will evolve as real components are born.*
