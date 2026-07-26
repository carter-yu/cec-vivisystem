# Ground Rules

These rules are binding unless changed by explicit family decision.

1. **Time Reality**  
   Development only happens in 1–2 hour weekend sessions. Every change must leave the system in a working state.

2. **Bottom-Up Growth**  
   We add only the next small, proven capability. We do not design the full system in advance.

3. **Resilience First**  
   Every component ships with tests, structured logging, and visible failure modes.

4. **Human Confirmation**  
   Any create / update / delete of a calendar event requires explicit human confirmation.

5. **Source of Truth**  
   Shared Google Calendar is the primary source of truth for time-based events.

6. **No Hidden Orchestrator**  
   We do not create a central long-running controller that other components must depend on.

7. **Language**  
   Runtime conversation: Cantonese + English.  
   All engineering artifacts: English only.

8. **Decision Records**  
   Non-obvious decisions are recorded as short Architecture Decision Records (ADRs) in `docs/decisions/`.

9. **Progress Visibility**  
   Every session ends with an update to `PROGRESS.md`.