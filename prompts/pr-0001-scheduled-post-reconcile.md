---
date: 2026-09-28 HK (Asia/Hong_Kong)
branch: feat/scheduled-post-reconcile
model: gpt-6-astra
goal: Implement one offline P1 scheduled-post reconciliation slice and a PR prompt archive.
constraints: Existing contracts; fake providers; no secrets, live jobs, deployment, unrelated repository work, or merge; Traditional Chinese only if quoted.
acceptance: Branch from origin/main; one PR; reviewed diff; uv sync --locked; uv run pytest -q; uv run ruff check .; git diff --check; factual PROGRESS entry; this archive, or an evidenced blocker.
---

# Manager request (sanitized archive)

Task: P1 scheduled-post reconciliation + prompts/ archive.
Repository: cec-vivisystem. Model: gpt-6-astra.
Create a feature branch from origin/main, such as feat/scheduled-post-reconcile.
Do not touch or merge main. Open one PR with gh when ready. Work in this repository
only; do not share its working tree with other repositories.

Language lock: all Chinese in repository, docs, tests, and chat artifacts must be
Traditional Chinese. Engineering English for code, commits, and prompt archives.

Before any edit:

1. Inspect Git status, branch and remotes; preserve dirty work; do not touch main.
2. Read AGENTS.md, docs/ground-rules.md, docs/architecture.md, docs/maintenance.md,
   docs/takeover-review-2026-09-19.md section 4 P1 scheduled-post row, and the latest
   PROGRESS.md entry.
3. Read only the modules that post scheduled Slack messages and write posted
   markers: morning_recap, google_token_reminder, the important-date scheduled
   path, and related storage.

One-PR goals:

1. Add prompts/ with README following the requested sibling-project convention:
   one Markdown file per PR, no secrets, Traditional-only Chinese if quoted,
   sanitized content.
2. Implement the smallest offline scheduled-post reconciliation slice from P1:
   define delivery/reconciliation policy in a phase doc and/or ADR, retain Slack
   response handles where posts are recorded, add injected crash-window tests
   with fake Slack, and do not automatically rerun ambiguous posting failures.
3. Update PROGRESS.md with facts only.
4. Archive this manager prompt in prompts/pr-0001-scheduled-post-reconcile.md,
   with date 2026-09-28 HK, branch, model, goal, constraints and acceptance metadata.

Authority: parent chat orders take precedence over a skill that would pause. If a
skill stops work, name the file and quote the relevant line.

Preserve these contracts:

- Google Calendar is the source of truth for timed events; creates require human
  yes and write_calendar_create.
- parse() stays rules-only. Configured listener creation may be LLM-first under
  ADR 0008. No model writes Calendar.
- Preserve Asia/Hong_Kong, exclusive end boundaries and the one-hour default for
  timed creation. Inject clocks in tests.
- Life notes preserve exact raw_text. Do not purge notes or important dates as
  retention.
- One listener per data directory. Slack SDK owns Socket Mode recovery.
- Tests use fake Google, Slack and LLM. uv.lock stays locked.

Finish line: branch, PR, reviewed diff, required verification command notes,
PROGRESS facts and prompt archive, or an evidenced blocker.

Stop and wait only for merge to main; claims that Mini, live Slack/Google,
launchd, tokens or billing were verified; Calendar writes outside confirmation;
new LLM features beyond ADR 0008; reading or pasting .env or live data; destructive
Git operations or unrelated repository work.

Commands when Python exists:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
git diff --check
```

Session limits: max_threads <= 3, max_depth = 1. No ultra/max-parallel. No two
writers on listener, confirmation, calendar_writer or storage. Never request
Slack/Google/xAI secrets in chat. Never run live listener, recap or token jobs.
Bias to action and persist until the slice is reviewable. Treat the request as an
order to finish. Report branch, PR URL, test/Ruff results, changed-file summary
and any blocker.

Sanitization: workspace-specific absolute paths and the sibling repository name
were generalized. No credentials, family records or incident text were supplied
or added to this archive.
