# Ground Rules

These rules are binding for **all phases and all components** unless changed by explicit family decision (and, for technical standards, an ADR when appropriate).

1. **Time Reality**  
   Development only happens in 1–2 hour weekend sessions. Every change must leave the system in a working state.

2. **Bottom-Up Growth**  
   We add only the next small, proven capability. We do not design the full system in advance.

3. **Resilience First**  
   Every component ships with tests, structured logging, and visible failure modes.  
   Details: [resilience.md](resilience.md).

4. **Unit Tests Are Mandatory (every phase)**  
   No component or phase is done without a locked test plan and a green offline suite that meets the [Unit Testing Standard](unit-testing.md): happy path, failure/partial path, contract shape, controlled handling of garbage input, and light boundary-log coverage.  
   Each phase document must list its fixtures/cases and non-tests **before** implementation.  
   Decision record: [ADR 0003](decisions/0003-unit-testing-standard.md).

5. **Logging, Troubleshooting & Retention (every phase)**  
   Every component emits sufficient structured logs for weekend troubleshooting (boundary start/end, `component`, `event`, `outcome`, errors, `correlation_id` when flows span parts).  
   Logs and operational data follow classified **retention and purge** rules; no persistent store without a purge story.  
   Full matrix and periods: [logging-and-retention.md](logging-and-retention.md).  
   Decision record: [ADR 0002](decisions/0002-logging-and-retention.md).

6. **Human Confirmation**  
   Any create / update / delete of a calendar event requires explicit human confirmation.

7. **Source of Truth**  
   Shared Google Calendar is the primary source of truth for time-based events.

8. **No Hidden Orchestrator**  
   We do not create a central long-running controller that other components must depend on.

9. **Language**  
   Runtime conversation: Cantonese + English.  
   All engineering artifacts: English only.

10. **Decision Records**  
    Non-obvious decisions are recorded as short Architecture Decision Records (ADRs) in `docs/decisions/`.

11. **Progress Visibility**  
    Every session ends with an update to `PROGRESS.md`.

12. **Phase Documents Inherit System Standards**  
    Phase docs (e.g. `phases/phase-N-*.md`) may narrow **scope** for a weekend. They may not waive unit testing or logging/retention. Acceptance criteria for every future phase must include compliance with rules 4 and 5.

13. **Secrets Stay Local**  
    API keys, tokens, signing secrets, and OAuth credentials live only in a local never-committed `.env` (or another gitignored path). Commit names and empty placeholders in `.env.example` only. Never log secret values. Default tests must not require real credentials.
