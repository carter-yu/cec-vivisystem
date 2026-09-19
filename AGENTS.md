# cec-vivisystem maintenance context

This is an existing family scheduling/notes system. Continue from its implemented
components; do not re-scaffold it.

Read `docs/ground-rules.md`, `docs/architecture.md`, and the latest `PROGRESS.md`
entry before changes. `docs/maintenance.md` is the takeover handover;
`docs/takeover-review-2026-09-19.md` contains the signed findings and backlog.

Preserve the explicit human confirmation gate for Calendar writes, rules-first
parsing, separate life-note/important-date stores, HKT time policy, and offline
fake-client tests. Engineering prose is English; runtime Chinese is Traditional.
Use synthetic fixtures and keep credentials, live stores, and incident logs local.

Lock a phase regression plan before materially extending a component. Run pytest
and Ruff, review the diff, and update PROGRESS at session end. Prefer focused fixes
and existing contracts over speculative architecture changes.

Respect existing working-tree changes. Do not read or expose `.env` secrets when
ordinary code/contract review is sufficient. Live CLI entry points post to Slack
or access Google; unit tests do not require them.
