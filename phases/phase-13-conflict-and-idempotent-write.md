# Phase 13 – Conflict-before-create + idempotent Writer

**Do not implement until Phase 12 is green.**

**Goal.** (1) Same `confirmation_id` → at most one Google `create_event` (Incident B, 2026-09-05 extra events in one slot). Second yes: “already added”, no second insert. (2) Create proposals show bilingual 撞期 (times + titles). **Warn, do not silent-hard-block** different events. Reuse `overlap.py`. No freebusy. No LLM.

**Tests (min):** W1 two accepts → one create; C1–C3 overlap hits/fail/none on proposal text; existing Phase 6/8 green.

**Out of scope:** period recap, 07:00 job, important dates.