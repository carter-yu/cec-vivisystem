---
date: 2026-10-01 HK
branch: feat/monthly-calendar-board
model: gpt-6-astra
goal: Implement Phase 26 monthly calendar PNG in the existing 07:00 recap job.
constraints: Preserve today text and independent delivery markers; read-only Calendar; fake providers; Traditional Chinese runtime; no secrets, unrelated repositories, merge, or Mini deployment.
acceptance: Phase 26 grid/render/delivery cases; prior suites green; locked sync, pytest, Ruff, diff check and wheel build; sample PNG; one feature-branch PR with documentation and licensed fonts.
---

# Manager request (sanitized archive)

Implement the locked Phase 26 specification on the existing feature branch at
92ec59f. Read repository guidance, architecture, maintenance, phase specifications,
ADR 0009, reader/recap/delivery/models, related tests and the existing prompt archive
before implementation. Preserve existing work and use the supplied OFL font assets.

Keep the 07:00 HKT today-text recap and add a full-month 1680 × 1260 PNG with a
Sunday-first grid, specified paper/accent colors, highlighted today, muted adjacent
days without events, all-day-first sorting, four event lines and visible overflow.
Respect exclusive ends, HKT multi-day continuations, untitled fallback and ellipsis.
Use pure model/render functions with injectable fonts and controlled font failure.

Extend fake and live Slack posters with file upload, retain receipt handles, and
reserve independent image delivery before upload. Image failure must not undo a
successful text post. Keep Calendar Writer, parser, confirmation and LLM unchanged.
Tests use fixed clocks, synthetic events, fake clients and temporary stores only.

Add Pillow and update uv.lock. Verify packaging, generate a sample PNG outside the
repository, update architecture/README/PROGRESS, record ADR 0010 and archive this
request. Run uv sync --locked, uv run pytest -q, uv run ruff check ., git diff --check.
Commit code, tests, docs, phase specification, licensed fonts and dependency files;
exclude environments, private content, scratch files and unrelated duplicates.
Push and open one PR against main; never merge or deploy Mini. Report branch,
commit, checks, key changes, deviations, blockers, sample location and PR.

Sanitization: local workspace paths and private context are omitted. No secrets,
family records or incident text are included.
