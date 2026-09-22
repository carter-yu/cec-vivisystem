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

## Phase 22: Socket Mode incident fix (2026-09-22)

The September 21 logs show sustained socket errors and repeated application-forced
reconnects, with very little message intake. The original transport-drop trigger
is unknown. Code inspection confirmed two recovery owners: SDK auto-reconnect and
the application's 15-second force-reconnect loop. Phase 22 removes the latter;
the health loop now only observes. Do not restore forced endpoint replacement.

Local verification: 282 offline tests and Ruff pass. The SDK's automatic recovery
remains enabled. Tests prove application non-interference and fake delivery after
recovery, not recovery on the Mini's network. See the [phase plan](../phases/phase-22-socket-recovery.md).

After this patch is transferred through the normal reviewed Git rollout, on Mini
from its existing repository checkout:

```sh
uv sync --locked
launchctl kickstart -k "gui/$(id -u)/com.cec.vivisystem.listener"
```

Restart only the listener; no recap rerun, token rotation, or second MacBook
listener is needed for this code fix. Verify `listener_starting` and
`socket_mode_connected` with `recovery_owner=slack_sdk`. Fresh application logs
must no longer emit `socket_mode_reconnect_attempt` / `socket_mode_reconnected`.
`socket_mode_disconnected` can appear while the SDK recovers, followed by
`socket_mode_recovered` if a later poll observes connectivity. A brief disconnect
between polls may not produce either observation.

Operator smoke: send help, a list request, and a synthetic create, then explicitly
confirm only the intended test event. Check the actual Slack replies and repeated
yes behavior. Observe for at least an hour and through a controlled connection
interruption. If socket errors persist, retain fresh logs and inspect network/SDK
recovery; the supplied logs alone do not prove the initial failure's cause. There
is no new process-restart watchdog in this patch. Launchd stdout/stderr log rotation
remains operator-managed; do not delete the incident evidence during rollout.
