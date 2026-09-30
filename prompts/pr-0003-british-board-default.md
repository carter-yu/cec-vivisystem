---
date: 2026-10-01 HK
branch: feat/british-board-default
model: gpt-6-astra
goal: Wire british month-board theme as the live 07:00 morning_recap default and open a theme-only PR.
constraints: Rebase onto origin/main (Phase 26 already merged); theme-only diff vs main; Traditional Chinese only in user-facing Chinese; no Mini, Slack, or merge.
acceptance: morning_recap renders with theme="british"; classic library default retained; docs/PROGRESS/prompt archive updated; pytest and ruff green; PR opened not merged.
---

# Manager request (sanitized archive)

Wire the British calendar board theme as the live 07:00 default and open a PR.
Repo already has Phase 26 monthly board on origin/main. Existing theme work lived on
feat/monthly-board-british-style. Cut a clean theme-only branch onto origin/main,
make morning_recap use british when rendering the month board PNG, update
docs/PROGRESS/prompts if needed, keep tests and Ruff green, open the PR with gh,
and do not merge or touch Mini/Slack.

Sanitization: local workspace paths and private context omitted. No secrets,
family records or incident text included.
