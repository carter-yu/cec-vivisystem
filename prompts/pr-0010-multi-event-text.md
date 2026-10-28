---
date: 2026-10-29 HK
branch: feat/phase-33a-multi-event-text
model: claude-opus-5-5 (Claude Code)
goal: Turn one #family-plans text message that lists several dated events into one list proposal; the family can deselect items, and one yes creates every item shown last.
constraints: decisions locked by Carter 2026-10-29; reuse the existing xAI parser (no new model, provider or vendor); Calendar yes gate stays; no update/patch, list-delete or series-delete; offline tests with fake model and fake calendar only; Traditional Chinese only in runtime copy and docs; no commit, push, Mini access, Slack app change, .env edit, or live listener/recap/token/xAI/Google calls; Phase 33b (images) docs-only.
acceptance: uv run pytest -q and uv run ruff check . green with exact counts; ADR 0018, this archive, phase 33a/33b docs and the design note updated with the locked decisions; summary of files changed, behaviour changes and risks.
---

# Manager request (sanitized archive)

Implement Phase 33a on branch feat/phase-33a-multi-event-text (from main@5a1b701).
The phase 33a/33b design drafts and a design note in the workshop scratch folder
describe the target; a scratch probe showed the current routing hazards.

Locked (Carter 2026-10-29):
1. Before writing, the user can deselect items (`唔要 3` / `剔走 2,4` / `drop 3`,
   `只要 1,4` / `keep 1,4`); the list is re-posted and still needs yes. One yes
   creates every item shown last.
2. Unusable lines are skipped and listed under 「未包括 / Not included」 with the
   reason.
3. A line with a date but no time triggers a clarify question asking for the time
   (not all-day, not silently skipped). The implementer chooses and documents how
   this combines with usable lines, consistent with the existing clarify flow.
4. Phase 33b: images are processed only when attached together with the message text
   `加event`.
5. Phase 33b: images are sent raw to xAI Grok vision (no OCR on the Mini). Carter
   approves reinstalling the Slack app for `files:read`; the Mini `.env` update and
   listener restart happen later only with his explicit authorization. 33b stays
   docs-only in this PR.

Requested implementation:
- Extend the extraction contract to `events[]` (1–15 items) with a new prompt
  version; keep v5 for rollback. One item behaves as today; one series item keeps
  Phase 27; two or more items are a list. Over the cap → clarify, never truncate.
- Per-item deterministic validation (past dates, weekday mismatch, repeating inside a
  list, crossing midnight → not included; date-only → time question; missing end →
  existing one-hour default, shown).
- One bilingual proposal listing usable items and not-included lines with reasons.
- Narrowing replies before yes re-propose (still pending); yes creates all shown; no
  rejects.
- Writer: per-item keys `<confirmation_id>#item-NN`, one class-B audit row per item,
  no `series_id`, honest partial-failure counts, a second yes retries only missing
  items (Phase 27 series writer pattern).
- Overlap warns only. Routing guard: two or more dated lines must not be taken by the
  list, important-date or series routes. Confirmation store backward compatible.
- Tests (offline): five-line paste → five items; unusable lines skipped; date-only
  clarify; deselect with `唔要 N` and `只要 1,4`; yes creates all; partial failure and
  retry; over cap; single-event and series regressions; store compatibility; the yes
  gate.
- Docs: ADR 0018, phase docs with the locked decisions (open-question wording
  removed), PROGRESS, and this archive.

Follow-up constraint (resume session, same day): the first session stopped at a WIP
state with failing tests and Ruff errors; fix code, not by deleting or skipping
tests.

Redactions in this archive: workshop scratch file paths and the private incident
message that motivated the phase (replaced by synthetic examples).
