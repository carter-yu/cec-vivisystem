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
| `python -m cec_vivisystem.medic_librarian` | Phase 32 offline `build` / `search` / `install-seed` for the `#jeff-aia-medic` knowledge tree |

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

For future work, the highest-value next slices are scheduled-post reconciliation, durable-data backup/corruption reporting, and independent maintenance/health checks. Planned Reminder Agent, Calendar update/patch, series-delete, and note search remain future features (delete-one shipped in Phase 28).

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

Phase 30 ([ADR 0014](decisions/0014-status-channel-alert-hysteresis-and-stuck-exit.md))
adds one bounded exception: after about 15 minutes stuck, the listener replaces its
whole Socket Mode client a few times and then exits with code 75. It never uses
the forced endpoint path above. See "Phase 30" below.


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


## Phases 27–28: weekday series create and delete-one (2026-10-02)

Offline only; no Mini deployment is claimed. Deploy `create_event.v5.txt` with
the parser/series/confirmation/writer/listener source. No new environment
variable, dependency or Google scope (`calendar.events` already authorizes
delete). Confirmation JSON gains optional `parse_result.series` and
`delete_candidates`; older files load unchanged. Series children are audited per
occurrence under the existing class-B audit store (keys `<confirmation_id>#<date>`),
and a timed single create crossing HKT days is refused even for a pending
pre-upgrade confirmation. A partial series failure is visible in Slack counts and
in `series_write_completed` logs; a later yes in the same thread retries missing
days only. Delete attempts are audited as `op=delete`. Live series/delete smoke
needs explicit authorization and should use a synthetic event.

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
| `com.cec.vivisystem.health-check` | every 300 s after load (Phase 30) | alert-only; never restarts anything |

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

`.env` entry: `CEC_HEALTH_ALERT_CHANNEL_ID`, the system-status channel
(`#vivi-status`, Phase 30). If unset, alerts fall back to the plans channel
and every run logs `system_channel_fallback`.

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

The first check runs 5 minutes after login (no `RunAtLoad`), so a reboot does
not count the old heartbeat. Phase 30 alert policy v2 then needs 2 unhealthy
checks and 15 minutes before one outage post, and 3 healthy checks and 15
minutes before one recovery post (see "Phase 30" below). The checker never
restarts the listener; recovery is launchd `KeepAlive`, the Slack SDK and the
listener's own stuck-connection exit. Use `launchctl kickstart -k` by hand.

Limitation: the checker runs on the Mini inside the same login session. At the
FileVault or login screen, or with the Mini off, no alert is sent, and Grok Bot
or a human cannot detect this from the Mini side. An off-box check is out of
scope for Phase 29. If Slack has been silent unexpectedly, check that the Mini
is logged in.


## Phase 30: system-status channel, alert hysteresis, stuck exit

Decisions (Carter, 2026-10-15) and rationale:
[ADR 0014](decisions/0014-status-channel-alert-hysteresis-and-stuck-exit.md);
plan and tests: [Phase 30](../phases/phase-30-status-channel.md).

### Status channel `#vivi-status`

1. In Slack, create the private channel and invite the bot (`chat:write` is enough).
2. Put its ID in the Mini `.env` as `CEC_HEALTH_ALERT_CHANNEL_ID`. The checker
   and the 10:00 job read `.env` on every run; no restart is needed.

These go to the status channel: outage / unstable / still-down / recovered
alerts, the 09:00 daily status, and Google token notices. `#family-plans` keeps
the 07:00 recap, the 10:00 dates review and thread replies. If the variable is
unset, system messages go to `#family-plans`, each run logs WARNING
`system_channel_fallback`, and the daily status says so.

### Alert policy v2 (health checker every 300 s)

| Rule | Default |
| --- | --- |
| Outage post | 2 unhealthy checks in a row and outage ≥ 15 min |
| Unstable post | instead of an outage post when another outage opened in the last 6 h |
| Recovered post | 3 healthy checks and ≥ 15 min healthy (60 min after "unstable"); only after an outage post |
| Still down | every 6 h while the outage stays open |
| Gap | ≥ 30 min between these posts (the daily status is exempt) |
| Brief disconnects | no post; counted in the daily status |

The outage start comes from the heartbeat's `outage_since`, which a connection
shorter than 60 s does not reset. Each post shows the start (HKT), duration,
reason and listener PID, in English and then Traditional Chinese. State is in
`data/health/alert_state.json` (v2; a Phase 29 file migrates on the next run).
A failed post leaves the state, so the next run retries it.

Synthetic test without downtime (no Slack post without `--alert`):

```sh
uv run python -m cec_vivisystem.health --path /tmp/hb.json; echo "exit=$?"
```

With `--alert`, also pass a temporary `--state-path` (and `--samples-dir`) so
the live alert state is not touched.

### 09:00 daily status

The first checker run at or after 09:00 HKT posts one daily status per HKT date,
always. **If no daily status arrives by about 09:10, the Mini is probably at the
FileVault or login screen, off, or the checker is not loaded.** Content:
listener state and PID; connected % / disconnects / longest outage / restarts
over 24 h (from `data/health/samples/*.jsonl`, about 5-minute resolution, kept
14 days); open alert; today's 07:00 recap marker (posted / not confirmed /
failed / not posted); the latest 10:00 dates-review outcome from
`logs/important_dates-*.log`; Google access days left or EXPIRED (from
`GOOGLE_REFRESH_TOKEN_ISSUED_AT`). A line is left out when its record is absent.

### Google token notices

The 10:00 job now posts token notices to the status channel: 3/2/1 days before
expiry, "expires today" on the day, then "expired" once per HKT date until
someone re-authorizes and updates `GOOGLE_REFRESH_TOKEN` and
`GOOGLE_REFRESH_TOKEN_ISSUED_AT`. Since Phase 31b, re-authorize from the
control app (see "Phase 31" below; ADR 0016).

### Stuck Slack connection: what the exit means

The listener samples the connection every 15 s. When the latest sample is
disconnected and no connection lasted 5 min during the last
`CEC_LISTENER_STUCK_AFTER_S` (default 900 s):

1. It logs `socket_mode_stuck_retry` (WARNING), closes the whole Socket Mode
   client, builds a new one and connects (`socket_mode_stuck_reconnected`, or
   `socket_mode_stuck_retry_failed`). It waits `CEC_LISTENER_STUCK_RETRY_WAIT_S`
   (default 120 s) before the next retry, up to `CEC_LISTENER_STUCK_RETRIES`
   (default 3, range 0–10).
2. A connection that stays up 5 min logs `socket_mode_stuck_recovered` and
   resets the counter.
3. Otherwise it logs `socket_mode_stuck_exit` (ERROR), writes
   `exit_reason: socket_mode_stuck` into `data/health/listener.json`, and exits
   with code 75. launchd `KeepAlive` starts a new process after
   `ThrottleInterval` (30 s). `launchctl print … | grep 'last exit'` shows 75.
   The new process has a new `instance_id` and PID; the daily status counts it
   as a restart.

Repeated stuck exits mean the Mini cannot keep a Slack connection at all
(network, DNS, Slack outage, or an SDK fault). Read `logs/slack-sdk.log`
(below); do not add a second reconnect loop. The env values are read only at
listener start; invalid values log `listener_stuck_config_invalid` and use the
default.

### Reading `logs/slack-sdk.log`

The Slack SDK and Bolt stdlib loggers (INFO and higher) write here even though
launchd sends stdout to `/dev/null`. The file rotates at 5 MB and keeps 3
backups (`slack-sdk.log.1` … `.3`); it is not part of the dated 14-day purge.
Tokens, Socket Mode tickets and message `text` values are redacted.

```sh
tail -n 50 logs/slack-sdk.log
grep -c 'on_error invoked' logs/slack-sdk.log                 # transport errors
grep -E 'A new session has been established|stale|Failed to' logs/slack-sdk.log | tail
```

- `A new session has been established (session id: …)`: a connect succeeded.
  Many new sessions per hour with errors between them is the stuck pattern.
- `on_error invoked (… error: BrokenPipeError …)`: the socket is dead; the SDK
  reconnects. Compare times with `socket_mode_*` lines in
  `logs/listener-YYYY-MM-DD.log`.
- `The connection seems to be stale. Disconnecting…`: no pong for 20 s (4 × the
  5-s ping). Frequent stale lines are the evidence for a ping change, which
  needs its own ADR.
- `Failed to check the state of sock` / `Failed to retrieve WSS URL` /
  rate-limit messages: the reconnect itself fails.

### After a reboot or deploy (Phase 30 additions)

After the reviewed merge, on the Mini: `uv sync --locked`, then
`scripts/install_launchagents.sh --dry-run`, then the installer (the
health-check interval becomes 300 s; the listener restarts). Then:

1. `launchctl print "gui/$(id -u)/com.cec.vivisystem.health-check" | grep -i interval`
   shows 300.
2. `uv run python -c 'import json;print(json.load(open("data/health/listener.json"))["instance_id"])'`
   prints an ID (heartbeat v2), and `uv run python -m cec_vivisystem.health`
   prints `ok … pid=<listener pid>`.
3. `ls -l logs/slack-sdk.log` exists and has a recent
   `A new session has been established` line.
4. `#family-plans` receives no system posts; `#vivi-status` stays quiet while
   healthy; the next 09:00 daily status arrives.


## Phase 31: Mini control app (LAN web app)

Decisions: [ADR 0015](decisions/0015-mini-control-app-lan-auth.md) (form
factor, LAN/HTTP, accounts, dependencies) and
[ADR 0016](decisions/0016-google-reauthorization-flow.md) (Google
re-authorization). Plan and tests: [Phase 31](../phases/phase-31-mini-control-app.md).
Everything below is an authorized operator action on the Mini; nothing here was
run on the Mini during implementation.

### What it is

`com.cec.vivisystem.control` serves a small web app on the home LAN for both
parents: status (listener, Slack connection, 07:00 recap, 10:00 dates, Google
access, alerts), the next 14 days of Calendar events and important dates (both
read-only), Google re-authorization and Test connection (both roles), and for
the admin only: Slack channel IDs, the stuck-retry setting and a listener
restart. Nothing depends on it; if it stops, the listener and jobs run on.

### Install (after the reviewed merge)

1. Give the Mini a DHCP reservation on the router. Do **not** port-forward.
2. Install the extra and the agents (the installer renders the new template):

   ```sh
   uv sync --locked --extra control
   scripts/install_launchagents.sh --dry-run
   scripts/install_launchagents.sh
   ```

   Always use `--extra control` with `uv sync` on the Mini. A plain
   `uv sync --locked` removes the web packages; the control agent then
   reinstalls them from the uv cache at its next start.
3. In `.env`, set the LAN address and (optionally) the allowed networks and
   host names, then restart the agent:

   ```sh
   CEC_CONTROL_BIND=192.168.1.20:8730          # the Mini's reserved LAN IP
   # CEC_CONTROL_ALLOWED_NETS=192.168.1.0/24   # default: loopback + bind /24
   # CEC_CONTROL_HOSTS=mac-mini.local          # extra Host names (Bonjour)
   ```

   ```sh
   launchctl kickstart -k "gui/$(id -u)/com.cec.vivisystem.control"
   ```

   The app refuses `0.0.0.0`, public addresses and host names in
   `CEC_CONTROL_BIND`, and public networks in `CEC_CONTROL_ALLOWED_NETS`; it
   then exits, and `~/Library/Logs/cec-vivisystem/control.err.log` shows why.
4. Create the two accounts (prompts for a passphrase of 12 or more characters;
   nothing is echoed or logged):

   ```sh
   uv run --extra control python -m cec_vivisystem.control adduser carter --role admin
   uv run --extra control python -m cec_vivisystem.control adduser <name> --role member
   uv run --extra control python -m cec_vivisystem.control users      # names and roles
   ```

   `passwd <name>` sets a new passphrase; `deluser <name>` removes an account.
   The file is `data/control/users.json` (mode 0600). No web sign-up exists.
5. On each iPhone (home Wi-Fi), open `http://<LAN IP>:8730` (or
   `http://mac-mini.local:8730` if that name is in the Host list), sign in, and
   use Share → Add to Home Screen. Confirm that the page does **not** open on
   mobile data.

### Acceptance test: first Google re-authorization

The refresh token expired on 2026-10-15 (Testing mode). Re-authorize through
the app, not the helper script (ADR 0016):

1. Overview → "Re-authorize" (enter the password again if asked).
2. "Open Google". Sign in with the family calendar account. If Google shows the
   unverified-app warning, select "Continue". Select the calendar box, then
   "Continue".
3. Safari shows "Cannot connect" on `http://127.0.0.1:53682/…`. This is the
   correct result. Copy the full address from the address bar.
4. Return to the app tab, paste the address, select "Save access".
5. Expect "Google access is saved. It expires on …" and "The test with the new
   access is OK." The status channel gets the re-authorization notice.
6. The app then restarts the listener automatically (both roles; no extra
   confirmation and no 5-minute gap, ADR 0016 amended 2026-10-21). The
   "Listener restart" box on the same page updates every 5 seconds until
   "The listener started again at … New process: …". The status channel gets
   a restart notice. If the box shows an error or "No new listener process
   after 2 minutes", an admin selects System → Listener → "Restart listener…"
   (a manual restart within 5 minutes of a successful automatic restart is
   refused; wait for the time shown).
7. Send `help` and a synthetic list request in `#family-plans`. The 07:00 recap
   uses the new token the next morning without a restart.

Weekly after that: the Overview banner warns 2 days before expiry; the status
channel posts "expired" daily if nobody re-authorizes.

### Other actions

- **Slack channels (admin):** Settings → Slack channels → Change… → Preview →
  Save. Only `SLACK_FAMILY_PLANS_CHANNEL_ID`, `SLACK_LIFE_NOTES_CHANNEL_ID` and
  `CEC_HEALTH_ALERT_CHANNEL_ID` can change (format `C…`/`G…`). Then restart the
  listener. "Send test message…" posts one STE test line to that channel.
- **Stuck retries (admin):** System → Listener → Stuck connection guard
  (`CEC_LISTENER_STUCK_RETRIES`, 0–10). Restart the listener to apply.
- **Restart (admin):** at most one manual restart per 5 minutes (the automatic
  restart after a Google re-authorization skips this gap but starts a new one); runs only
  `launchctl kickstart -k gui/<uid>/com.cec.vivisystem.listener`.
- **`.env` backups:** every app write first copies `.env` to
  `data/control/env-backups/env-<time>-<id>.bak` (mode 0600; last 3; at most 30
  days). To roll back: `cp -p data/control/env-backups/<file> .env`, then
  restart the listener.

### Troubleshooting and emergency access

- Page does not open: `launchctl print "gui/$(id -u)/com.cec.vivisystem.control" | grep -E 'state|last exit'`,
  then `tail ~/Library/Logs/cec-vivisystem/control.err.log`. "Forbidden: this
  address is not on the family network" means the phone's IP is outside
  `CEC_CONTROL_ALLOWED_NETS`; "Bad request: host not allowed" means the
  address typed is not in the Host list.
- Locked out after 5 wrong passwords: wait 15 minutes, or restart the control
  agent (sessions and lockouts live in memory). Forgotten passphrase: `passwd`.
- Emergency: Screen Sharing to the Mini, then open the same LAN address in
  Safari on the Mini, or use Terminal as before. The app is never required.
- Logs: `logs/control-YYYY-MM-DD.log` (`control_request`, `control_sign_in`,
  `control_action_*`, `control_oauth_failed`); audit in
  `data/control/audit/YYYY-MM.jsonl` (90 days).

### Local demo (screenshots, no Mini)

`uv run --extra control python -m cec_vivisystem.control demo --port 8799`
serves synthetic data on loopback only (it refuses other addresses and never
reads `.env`), with one-click sign-in at `/demo/signin/admin` and
`/demo/signin/member`. Stop it with Ctrl-C and delete the printed temporary
directory.

### Installer fix (Phase 31)

`launchctl bootout` returns before the job is gone; an immediate `bootstrap`
then failed twice on the Mini with "Bootstrap failed: 5: Input/output error",
and the script stopped half-way with the listener unloaded. The installer now
polls `launchctl print` (up to about 10 s) until the label is gone, retries
`bootstrap` up to 3 times (2 s apart), continues with the other labels if one
still fails, skips the listener kickstart if the listener did not load, and
exits 1 with "not loaded: …". Rerun the installer after fixing the cause.

## Phase 32: AIA Medic librarian (#jeff-aia-medic)

See [ADR 0017](decisions/0017-aia-medic-librarian.md) and
[Phase 32](../phases/phase-32-aia-medic-librarian.md). The listener answers
questions in `#jeff-aia-medic` only from the redacted knowledge tree, with page
sources. That channel never reaches the Calendar parser, confirmations, important
dates or life notes. Nothing is stored; logs carry counts only.

### Set up the channel (authorized Mini action)

1. In Slack, invite the existing Vivi bot to `#jeff-aia-medic`
   (`/invite @<bot name>`). No new app. Check the app's event subscriptions: a
   public channel needs `message.channels` (`channels:history`), a private
   channel needs `message.groups` (`groups:history`). The family channels'
   current settings were not verified for this phase; adding a scope means
   reinstalling the app to the workspace (the tokens may change).
2. In `.env`, set `CEC_MEDIC_CHANNEL_ID=C0CB179V0AE` (the default when unset; use
   `off` to disable). It must differ from the plans and life-notes IDs; the
   listener disables the route and logs `medic_channel_conflict` otherwise. The
   control app does not edit this key.
3. Put a knowledge tree in place (next section), restart the listener (control app
   System → Listener → Restart, or `launchctl kickstart -k
   gui/$(id -u)/com.cec.vivisystem.listener`), and check the start log for
   `medic_configured` (`kb_source`, `node_count`, `answer_mode`).
4. Ask one English and one Cantonese question, for example "How much
   physiotherapy can I claim?" and 「洗牙有冇得 claim？」. Each reply ends with
   `Source:` / `來源：` lines and the scope line. Ask "weather tomorrow": the reply
   must say the document does not contain it.

### Build or rebuild the knowledge tree

PDFs stay in the gitignored `agent_librarian/` folder. Never copy them into the
repository or `knowledge/`.

```sh
# Option A: start from the tracked sanitized seed
uv run python -m cec_vivisystem.medic_librarian install-seed
# Option B: build from the local PDFs (identical copies are skipped)
brew install poppler   # once; provides pdftotext
uv run python -m cec_vivisystem.medic_librarian build --pdf agent_librarian/Jeff-AIA-Medic/
# Option C: text extracted elsewhere with `pdftotext -layout`
uv run python -m cec_vivisystem.medic_librarian build --from-text <file.txt>
# Check retrieval offline (no Slack, no model)
uv run python -m cec_vivisystem.medic_librarian search "physiotherapy limit"
```

The build writes `data/agent_librarian/jeff-aia-medic/tree.json` and `tree.md`
(override with `--out` or `CEC_MEDIC_KB_DIR`). It prints node counts per
category, the redaction count and warnings (tie-break attachments, merged-cell
repairs). Read `tree.md` against the PDF after every rebuild. The listener picks
up a changed `tree.json` on the next medic message; a corrupt file logs
`medic_kb_load_failed` once and the seed answers. Exit codes: 0 built, 1 nothing
built (no text: a scanned PDF needs OCR first), 2 usage or extraction error.

When the full policy PDF arrives, put it in the same folder (or pass a second
`--pdf`). Pages without a benefit table become page-referenced
`policy` / `exclusions` / `claims` / `definitions` chunks. Review the warnings;
the table rules were tuned on the Benefit Summary layout.

### Answers, outages and logs

- No `XAI_API_KEY`, a model outage, or an answer that fails validation (unknown
  citation, figure not in the document, Simplified Chinese) → the bot replies
  "I cannot write an answer now" and quotes matching tree lines with pages.
  Logs: `medic_answer_failed`, `medic_answer_rejected` (`reason`),
  `medic_extractive_fallback`.
- No retrieval hit → "not in this document"; no model call (`medic_retrieval
  hit_count=0`).
- Events (`component=medic_librarian`): `medic_message_received`
  (`message_length`, `redaction_count`, `language`), `medic_retrieval`,
  `medic_answer_attempt` / `_succeeded`, `medic_message_handled` (`mode`). Node
  ids appear only at DEBUG. Question text, answers and redacted values are never
  logged.

## Phase 33a: list create answers 「有日期但冇時間」 (2026-10-29)

Offline fix only; no Mini deployment is claimed. Deploy `create_event.v6.txt`
with the `parse_fallback` / `event_batch` source. No new environment variable,
dependency or Google scope. `CEC_CREATE_PROMPT_VERSION=v5` is still the rollback.

Symptom: one message lists several dated lines, every line writes a clock, and
the reply says every line has a date but no time. The provider reply is not
logged, so read the two INFO lines for that `correlation_id`:

- `batch_item_evidence` (`component=parse_fallback`) carries one flag token per
  item in message order plus counts. `flags_key` in the same line explains the
  letters: `s` model start, `a` model all-day, `d` model date, `m` the model
  reported a missing time/start, `o` another reported gap, `t` the item's
  `source_text` was verified in the message, `l` the item's line was located,
  `+` the clock was read from that line, `#` the day was read from that line,
  `?` the item still has no start.
- `batch_extract_completed` (`component=event_batch`) then gives the verdict:
  `included_count`, `not_included_count` and `not_included_reasons`.

Readings:

- `+` on every item: the model dropped or mis-typed the clock and the line was
  read locally. The proposal is correct; nothing to do.
- `?` on an item: its line really has no single unambiguous 24-hour clock (no
  clock, a range such as `15:30-16:30`, or an AM/PM / 上午 / 下午 cue). The family
  must resend the whole list with a start time on each line.
- `m` with `s` and no `+`: the model reported a gap it had filled. The list now
  ignores that self-report; if this shows up on a **single** event (one item, no
  list) the reply still asks for the start, which is the Phase 21 rule.
- `self_missing_values` lists only field names from a fixed vocabulary; anything
  else is logged as `other`, so no message content reaches the log.

If every list fails with `s` only and no `+` while the lines do write clocks,
check the prompt version in `parse_fallback_succeeded` before changing code.


## Phase 33b image extract

- Kill switch: `CEC_IMAGE_EXTRACT=off` (default). Set `xai` and restart the listener to enable.
- Vision model: `LLM_VISION_MODEL` (default `grok-4.20-0309-non-reasoning`); optional `LLM_VISION_FALLBACK_MODEL`.
- Trigger: PNG/JPEG attached in `#family-plans` with message text containing `加event`.
- A download HTTP 403 usually means the Slack bot scope `files:read` is missing — reinstall the app, update the bot token in `.env` by hand, restart.
- `image_fetch_refused_redirect` (then `image_fetch_failed` with the 3xx status) means the file host redirected to another host. The token was not sent there. Check the scope and the bot token as for a 403.
- Rollback: set `CEC_IMAGE_EXTRACT=off` and restart. The Slack scope can stay.
- Logs (counts only): `image_intake_*`, `image_fetch_*`, `image_prepare_*`, `image_extract_*`.

## Phase 33c: repeating items inside a list

Offline implementation only; no Mini deployment is claimed here. See
[ADR 0018](decisions/0018-multi-event-create.md), "Amendment: repeating items inside
a list", and [Phase 33c](../phases/phase-33c-batch-series.md).

- **Kill switch: `CEC_BATCH_SERIES` (default `on`).** `off` restores Phase 33a
  behaviour exactly: a list item carrying a repeating schedule is listed under
  「未包括」 as 「重複活動，請另外傳送」. Unknown values warn
  (`batch_series_switch_unknown`) and stay on. A change needs a listener restart.
- **Prompts:** `create_event.v7.txt` and `create_event_image.v2.txt` are the
  defaults. `CEC_CREATE_PROMPT_VERSION=v6` (or `v5`) is the prompt rollback and
  needs only a restart. Both prompts are inert with the switch off.
- **Caps:** 15 items per message, 40 occurrences per repeating item, and **60
  Google events per confirmation**. Over any cap the message or the item is refused
  with a bilingual reason; nothing is truncated. An over-cap message makes **no
  proposal**, so there is nothing to narrow — the family must resend it in parts.
  The writer's own cap re-check only guards a stored list (items that were already
  under the cap when proposed).
- **Holidays are a warning.** 上學日 expands to every weekday in the range; the bot
  has no school or public holiday calendar. One warning line per proposal states the
  **repeating items'** event total (events, not days), so in a mixed list it is
  smaller than the header's total. If the family confirms a holiday week, remove
  those events one at a time with delete-one (Phase 28); there is no series-delete.
- **One yes can hold the dispatch lock for tens of seconds** (up to 60 sequential
  `create_event` calls). Other family replies queue behind it. The audit store is
  scanned **once** per write, not once per event, so the wait does not also grow with
  the size of `data/calendar_audit/`. The heartbeat and the ADR 0014 stuck guard are
  unaffected — they need disconnected samples.

### Reading the logs (counts only; no titles, no dates)

- `batch_extract_completed` (`component=event_batch`) gains `series_item_count`,
  `series_event_count` and `total_event_count`. `not_included_reasons` may now carry
  `series_open_ended`, `series_over_cap` and `series_no_occurrences`.
- `batch_series_refused` (`component=event_batch`) is one line per refused repeating
  item: `item_index`, `reason` and `occurrence_count` (the real count for
  `series_over_cap`, which the family also sees in the 「未包括」 line).
- `batch_overlap_check_started` / `_completed` gain `occurrence_count` — the number of
  windows the single union list call really covered.
- `batch_edit_applied` gains `removed_event_count` and `remaining_event_count`.
- `batch_write_started` / `_completed` gain `event_count` and `series_item_count`.
  `failed_count` now counts **events**, not items.
- Per-occurrence `write_attempt` / `write_succeeded` lines are unchanged (they carry
  the title and start, as Phase 27 children do), so an accepted 60-event list writes
  about 120 child lines. Class A retention already handles that volume. Class B gains
  up to 60 audit rows per message instead of 15; the 90-day class is unchanged.

### Writer refusals specific to a repeating item

All of these refuse the **whole list** with no Google call and one class-B audit row:

| `error_type` | Meaning | Action |
| --- | --- | --- |
| `series_disabled` | A pending list holds a repeating item and `CEC_BATCH_SERIES` is `off` | Intended. Reject the confirmation; the family resends one class per message, or set the switch back to `on` and restart |
| `series_count_changed` | The stored spec no longer expands to the proposed `occurrence_count` (also a negative or absurd stored count) | Reject and ask the family to resend. Never edit the confirmation file by hand |
| `series_over_cap`, `series_no_occurrences`, `series_invalid_range`, `series_invalid_window`, `series_empty_weekdays` | The stored spec itself is unusable | Same: reject and resend |
| `series_all_day` | A stored repeating item claims all-day | Same |
| `batch_over_event_cap` | The remaining items sum above 60 events | The family must send the timetable in parts (one month or one class per message) |

### Rollback

**Order matters.** Pre-33c code reads named JSON keys only, so a stored repeating item
would load as a **single** event at its first occurrence, and one yes would create 1
event instead of N. Clear pending lists before touching the code.

The switch alone is almost always enough and needs no code revert:

1. Edit `.env` on the Mini (or use the control app's `.env` writer):
   `CEC_BATCH_SERIES=off`.
2. Restart the listener: `launchctl kickstart -k gui/$(id -u)/com.cec.vivisystem.listener`
   (or the control app's Restart action).
3. Verify: send a synthetic two-class timetable far in the future in
   `#family-plans`; the reply must list both classes under 「未包括」 as
   「重複活動，請另外傳送」, and `batch_extract_completed` must show
   `series_item_count=0`. A list that was already pending is now refused visibly
   (`series_disabled`) rather than written as one event.

If the **code** must go back to `1f620ab` (Phase 33b) as well:

1. **First** clear every pending list confirmation, so that nothing with a `series`
   key can be confirmed by old code. The store is the plain JSON directory
   `data/confirmations/` (one file per confirmation). Pick one of:
   - **Family-visible:** reply `不要` in each pending list's thread. The record becomes
     `rejected`, which old code also treats as terminal.
   - **Wait it out (preferred):** set `CEC_BATCH_SERIES=off`, restart, and wait for the
     24-hour pending expiry before reverting. With the switch off no new repeating item
     is proposed, and any list still pending is refused visibly (`series_disabled`).
   - **Operator, listener stopped:** move the affected records out of the store
     (do not edit fields in place — the id is derived from the Slack message, so a
     redelivery simply creates a fresh confirmation):
     ```sh
     cd <repo>            # the directory holding data/
     mkdir -p /tmp/cec-pending-33c
     grep -l '"series": {' data/confirmations/*.json |
       xargs -r grep -l '"status": "pending"' |
       xargs -r -I{} mv {} /tmp/cec-pending-33c/
     ```
2. Check that nothing is left:
   ```sh
   grep -l '"series": {' data/confirmations/*.json |
     xargs -r grep -c '"status": "pending"'      # every count must be 0 (or no output)
   ```
3. Then revert the code (`git checkout 1f620ab`, or deploy the previous tag),
   `uv sync --locked`, and restart the listener.
4. The reverted tree's own `DEFAULT_PROMPT_VERSION` applies (v6). Remove or set
   `CEC_CREATE_PROMPT_VERSION` only if `.env` pins a version the reverted code does
   not know: an unknown value warns and falls back to that tree's default.
5. `CEC_BATCH_SERIES` in `.env` is then ignored by the reverted code. It is harmless
   to leave; remove it when the variable is no longer wanted.
6. Events already created stay in Google Calendar. Remove them one at a time with
   delete-one; there is no series-delete and no list-delete.

A pre-33c confirmation file is **not** a problem in the other direction: 33c reads
`series` and `occurrence_count` with defaults, so every older record loads as a
one-off list and writes the same `<cid>#item-NN` keys as before.
