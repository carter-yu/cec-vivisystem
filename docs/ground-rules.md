# Ground Rules

These rules are binding for **all phases and all components** unless changed by explicit family decision (and, for technical standards, an ADR when appropriate).

**Sibling (LLM learning / demo projects, not this swarm):** personal GitHub repos that learn LangGraph, RAG, eval, Langfuse, and similar inherit this spine (rules 1–14) and then follow the extra LLM constitution:

`/Users/yucarter/my-ai-projects/ai-projects-ground-rules.md`

That file is **not** a waiver of anything here. cec-vivisystem stays **no LLM by default**. Do not copy a graph, retriever, or live model into this family swarm without an ADR.

---

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
   Runtime conversation: **Hong Kong Cantonese + English**. Written Chinese in this swarm is **Traditional Chinese** (HK usage) only.  
   **Simplified Chinese is rejected.** Do not add Simplified aliases, fallbacks, or “also understand Simplified” mappings in parser, help, production docs, or Slack copy. A Simplified line may parse as `unknown` / not that intent; that is correct. Tests may quote Simplified **only** as negative fixtures that must not parse.  
   All engineering artifacts: English only.

10. **Decision Records**  
    Non-obvious decisions are recorded as short Architecture Decision Records (ADRs) in `docs/decisions/`.

11. **Progress Visibility**  
    Every session ends with an update to `PROGRESS.md`.

12. **Phase Documents Inherit System Standards**  
    Phase docs (e.g. `phases/phase-N-*.md`) may narrow **scope** for a weekend. They may not waive unit testing or logging/retention. Acceptance criteria for every future phase must include compliance with rules 4 and 5.

13. **Secrets Stay Local**  
    API keys, tokens, signing secrets, and OAuth credentials live only in a local never-committed `.env` (or another gitignored path). Commit names and empty placeholders in `.env.example` only. Never log secret values. Default tests must not require real credentials.

14. **Teaching comments (AI / LLM learning)**  
    This repo is the **vivisystem spine** Carter reuses when learning LLM tooling. Comments exist so the next weekend (and the next Grok session) can re-learn a seam without rediscovering it from Slack incidents.  
    When a boundary, family alias, write gate, or “do not do X” rule is easy to miss, add a **short English comment** that teaches:
    - **Why** it exists (the family or ops constraint)
    - The **contract** (what callers may assume)
    - **What not to do** (the failure that looks tempting)

    Comments must **not** narrate implementation steps, must **not** leave placeholders for unrelated work, and must **not** substitute for tests, logs, or an ADR. Never put secrets, tokens, or `.env` values in comments. Runtime UI stays Cantonese + English (rule 9); comments stay English only.

    If a future ADR ever adopts an LLM **behind an existing contract** (parser, not a hidden agent), that slice also follows the sibling LLM constitution (Fake LLM in the default suite, tool allowlist, schema outputs, eval gates). Until then: **no LLM in this swarm.**

15. **Public artifacts use synthetic family fixtures**  
    README, `help`, tests, PROGRESS, and phase fixture tables must not quote live Slack lines, real birthdays, or medical appointment titles. Live nicknames stay in parser alias tables so the bot still works. Do not rewrite git history unless the family explicitly chooses that.
