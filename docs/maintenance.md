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


## Phase 23: Create extraction incident (2026-09-23)

Deploy `create_fallback.v3.txt` together with the updated parser/fallback source;
v2 is retained for comparison but is no longer selected. Restart the existing
listener through the normal reviewed rollout. No credential change or data
migration is required. Prompt hashes in fallback completion logs identify the
actual prompt used. A successful API response can still be a partial extraction;
inspect `intent_type` and `missing_fields`, not just the completion event name.

Local verification is 300 offline tests, not a live model evaluation. For operator
verification, use synthetic unfamiliar and compound activities with a weekday and
CJK-adjacent AM/PM clock, plus missing-date/time/title counterexamples. Check the
proposal's full title, participants and HKT start/end before confirming. Genuine
missing details must still clarify. No Mini rollout or live smoke was performed
in this session.


## Phase 24: LLM-first creation (2026-09-24)

This supersedes Phase 23's active prompt and routing instructions. Deploy
`create_event.v4.txt` with the parser/fallback/listener source. With the existing
model credentials, the listener selects LLM-first creation automatically; no new
environment variable, dependency or data migration is needed. Startup logs
`event_parser_configured` with `parse_mode=llm_first` or `offline_rules`.

First run the synthetic evaluation described in README using exported provider
credentials and explicit `--live`; it makes paid model calls but no Slack/Google
calls. Inspect field mismatches, model latency and token usage. The offline rules
baseline is 7/16 on this deliberately gap-focused corpus; no live model result was
measured during implementation. Prompt/schema tokens both count toward input.

After the normal reviewed rollout, restart only the existing listener. Check a
synthetic compound create's full title, HKT start/end and actor before explicit
confirmation. Verify help/list/important-date routing and raw notes still bypass
the model. A configured-model outage must reply temporarily unavailable; absence
of a key deliberately keeps the existing vocabulary-limited offline parser.

Current offline verification: 332 tests, Ruff and diff checks passed. No Mini
rollout, live model extraction, Slack delivery or Calendar write was performed.
