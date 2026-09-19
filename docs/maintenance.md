# Maintenance handover

Local takeover completed on 2026-09-19 by OpenAI GPT-6 Astra (Codex; Carter's AI assistant).
See [signed review](takeover-review-2026-09-19.md) for evidence and the prioritized backlog.

## Start here

1. Read the latest `PROGRESS.md` entry, `docs/ground-rules.md`, and `docs/architecture.md`.
2. Inspect `git status --short`; preserve unrelated/untracked work.
3. Run `.venv/bin/python -m pytest -q` and `.venv/bin/python -m ruff check .`.
4. For a feature, lock a small phase test table before implementation. Keep tests offline, inject clocks, and use synthetic family fixtures.
5. End with regression verification and an update to PROGRESS. Record non-obvious architecture decisions in an ADR.

`uv sync --locked` is the documented environment setup for a fresh checkout.
The existing local `.venv` was sufficient for this review; no dependency change was required.

## Runtime map

| Entry point | Responsibility |
| --- | --- |
| `cec_vivisystem.listener.main` | Slack Socket Mode; plans, confirmation replies, notes, important dates |
| `cec_vivisystem.morning_recap.main` | 07:00 HKT today recap; schedule externally |
| `cec_vivisystem.important_dates.main` | 10:00 HKT important dates plus token reminder |
| `cec_vivisystem.google_token_reminder.main` | Optional standalone token reminder |
| `cec_vivisystem.parse_misses.main` | Offline keyword counts for rule improvements |

Invoke entry points using the README's `uv run python -c` commands from the repository working directory. They load local `.env` and may contact external services. Running these live is separate from running tests.

## Invariants to preserve

- Calendar writes go only through Writer and an explicitly accepted create confirmation.
- Rules remain first; model fallback is create-only, optional, and never a direct write path.
- Google Calendar owns time-based events. Life notes and important dates are independent class-F data.
- Runtime language is Cantonese/English with Traditional Chinese; engineering prose is English.
- Store APIs retain existing JSON schemas. Atomic replacement prevents partial overwrite; it is not a multi-file or cross-service transaction.
- Keep the stable event-ID derivation unchanged across versions. Changing it can break retry safety.
- Slack confirmation source IDs survive redelivery; never replace a terminal confirmation with pending state.
- Run one listener process per data directory. Its intake lock covers worker threads, not multiple processes.

## Local verification and rollout

The Phase 21 patch was verified locally; Carter subsequently authorized its commit and push. Mini deployment remains pending. Before Mini rollout, preserve its current `data/` and `.env` through the operator's existing private backup process, especially Calendar success audits and class-F content. No schema migration is required for existing files. Old Google-generated event IDs remain protected by their existing audit rows; do not blindly retry historical uncertain writes after losing those rows.

Verify the intended checkout, installed dependencies, working directory, HKT timezone, launchd schedules, and log locations on Mini. After deployment, use a synthetic list request, create proposal, explicit yes, repeated yes, and raw note to verify the live wiring. A live smoke posts messages/creates an event and should be run as a deliberate operator action.

After an ambiguous Calendar response, retry yes in the same thread. Sending the activity again as a new message creates a new confirmation and is intentionally a separate request. Scheduled Slack posts still have a post/marker crash window; inspect Slack before manually rerunning an uncertain scheduled delivery.

For future work, the highest-value next slices are scheduled-post reconciliation, durable-data backup/corruption reporting, and independent maintenance/health checks. Planned Reminder Agent, update/delete, and note search remain future features.
