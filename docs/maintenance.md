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
| `cec_vivisystem.morning_recap.main` | 07:00 HKT today recap + month PNG; schedule externally |
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

After an ambiguous Calendar response, retry yes in the same thread. Sending the activity again as a new message creates a new confirmation and is intentionally a separate request. Scheduled Slack posts use pending markers to block uncertain retries; follow the Phase 25 reconciliation policy below before any manual rerun.

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


## Phase 25: Scheduled-post reconciliation

Scheduled recap, important-date reviews and token reminders now reserve their
existing class-C markers before Slack. Success stores `status=posted` and the
returned `slack_channel_id` / `slack_ts`. Old successful markers still suppress
posts. A `pending` marker means delivery is unresolved, including a crash before
posting. Posting/final-save errors return FAILED; subsequent matching runs fail
without reposting. CLI failures remain nonzero. WebClient retries are disabled
for these scheduled posts only; listener Socket Mode recovery is unchanged.

Do not add scheduler retries or clear markers merely because a run failed.
Follow [ADR 0009](decisions/0009-scheduled-post-reconciliation.md) for manual
reconciliation, including whole-batch handling for important dates. Preserve the
original markers and privately inspect Slack before deciding whether delivery
occurred. No automated Slack lookup or reconciliation command is provided.

Run only one scheduled invocation at a time. The existing 30-day marker retention
still applies to unresolved attempts; historical reruns after purge/state loss
are not protected. The important-date catalog and life notes are never purged by
this operational-marker policy. Local fake-provider tests are not rollout or
live delivery evidence.


## Phase 26: Month-board delivery

The same morning CLI posts each artifact independently and maintains both
text and board class-C markers. Board maintenance follows delivery so a board
directory failure cannot prevent the text attempt. Board markers are under `data/morning_recap/monthly_board/`
with daily filenames. A file ID in `slack_ts` identifies the uploaded file, not a
message timestamp. Preserve and reconcile pending uploads under ADRs 0009/0010;
pre-upload list/render failures have no reservation and can retry the board alone.
Keep the OFL font assets with the checkout and enable Slack `files:write` before
an authorized rollout. No Mini deployment or live upload was performed for Phase 26.


## Phase 29: Mini autostart and heartbeat

Decision (Carter, 2026-10-10; [ADR 0013](decisions/0013-mini-autostart-and-heartbeat.md)):
FileVault stays on, no automatic login, no LaunchDaemon. Agents run in
`gui/$(id -u)` and start only after Carter logs in by hand.

| Label | Runs | Notes |
| --- | --- | --- |
| `com.cec.vivisystem.listener` | at load, kept alive | heartbeat `data/health/listener.json` |
| `com.cec.vivisystem.morning-recap` | 07:00 | no `RunAtLoad`; missed slots are not replayed |
| `com.cec.vivisystem.important-dates` | 10:00 | no `RunAtLoad`; includes token reminder |
| `com.cec.vivisystem.health-check` | every 600 s after load | alert-only; never restarts anything |

### Install or update (authorized Mini action)

From the Mini's checkout, after the normal reviewed Git rollout and `uv sync --locked`:

```sh
scripts/install_launchagents.sh --dry-run   # render, lint, print launchctl commands
scripts/install_launchagents.sh             # back up, bootout, bootstrap, kickstart listener
```

The script takes `uv` from `command -v uv` (override with `--uv /abs/path/uv`),
backs up existing plists to `~/Library/LaunchAgents/backup-cec-vivisystem-<time>/`,
creates `~/Library/Logs/cec-vivisystem/`, and kickstarts only the listener.
It never kickstarts the 07:00/10:00 jobs, because that would post family
messages. Rollback: `launchctl bootout` the new label, copy the backup plist
back, then `launchctl bootstrap "gui/$(id -u)" <plist>`.

Optional `.env` entry: `CEC_HEALTH_ALERT_CHANNEL_ID` (Carter's DM or an ops
channel). If unset, alerts go to the plans channel.

Also set, by hand: System Settings → Energy → start up automatically after a
power failure, and prevent automatic sleeping on power (display sleep is fine).
Truncate any large old launchd out-logs from previous plists after preserving
needed evidence; new plists send stdout to `/dev/null`.

### After a reboot

1. **Log in by hand** at the Mini (or via Screen Sharing once FileVault is
   unlocked). Until this login, nothing of ours runs and no alert can be sent.
   For a planned restart, use `sudo fdesetup authrestart` so the disk unlocks
   unattended; the user login is still manual.
2. Wait 1–2 minutes, then check agents (from Terminal on the Mini):

   ```sh
   launchctl print "gui/$(id -u)/com.cec.vivisystem.listener" | grep -E 'state|pid|last exit'
   launchctl print "gui/$(id -u)/com.cec.vivisystem.health-check" | grep -E 'state|last exit'
   launchctl print "gui/$(id -u)/com.cec.vivisystem.morning-recap" | grep -E 'state|last exit'
   launchctl print "gui/$(id -u)/com.cec.vivisystem.important-dates" | grep -E 'state|last exit'
   ```

   The listener should be `running` with a pid; scheduled jobs are loaded and
   `not running` between runs. "Could not find service" means it is not
   bootstrapped: rerun the installer.
3. Check the heartbeat (does not post to Slack without `--alert`):

   ```sh
   uv run python -m cec_vivisystem.health; echo "exit=$?"
   ```

   `0 ok`, `1 stale`, `2 missing`. Options: `--max-age-s` (default 300) and
   `--disconnect-max-age-s` (default 600).
4. Logs: application logs in `logs/listener-YYYY-MM-DD.log` (look for
   `listener_starting`, `socket_mode_connected`) and `logs/health-*.log`;
   startup crashes in `~/Library/Logs/cec-vivisystem/<label>.err.log`.
5. Smoke: send `help` in the plans channel and expect a reply.
6. A missed 07:00/10:00 slot is not replayed. Before any manual run, follow
   [ADR 0009](decisions/0009-scheduled-post-reconciliation.md).

The first check runs 10 minutes after login (no `RunAtLoad`), so a reboot does
not send a false stale alert from the old heartbeat. If the listener is down
then, one stale alert is posted; a single recovery notice follows when the
heartbeat is fresh again. The checker never restarts the listener; recovery is
launchd `KeepAlive` and the Slack SDK. Use `launchctl kickstart -k` by hand.

Limitation: the checker runs on the Mini inside the same login session. At the
FileVault or login screen, or with the Mini off, no alert is sent, and Grok Bot
or a human cannot detect this from the Mini side. An off-box check is out of
scope for Phase 29. If Slack has been silent unexpectedly, check that the Mini
is logged in.
