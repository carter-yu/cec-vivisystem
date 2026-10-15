# Phase 30 — System-status channel and alert hysteresis

**Status:** decisions locked 2026-10-15 (HKT). Implemented locally on `feat/phase-30-status-channel` for PR #6 (30a, 30c and the 30b items in the 2026-10-15 brief; see "Implementation (2026-10-15)"). Not committed, merged or deployed. Mini actions are separately authorized operator steps.
**Depends on:** Phase 29 heartbeat and checker ([ADR 0013](../docs/decisions/0013-mini-autostart-and-heartbeat.md)); [Phase 22](phase-22-socket-recovery.md) single recovery owner; workshop note `cec-vivisystem-scratch/design-status-channel-and-control-app.md` (untracked).
**ADR:** [0014](../docs/decisions/0014-status-channel-alert-hysteresis-and-stuck-exit.md), status channel, alert hysteresis and the stuck-connection exit. Numbers 0011/0012 are reserved by open PR #4.
**Non-goals:**
- Kickstart or kill triggered by the health checker. The only self-recovery is decision 7, inside the listener.
- Changes to SDK auto-reconnect or ping settings. This is an evidence-gated follow-up that needs its own ADR.
- Moving family posts (07:00 recap, 10:00 dates review, in-thread replies).
- An off-box checker.
- The control app (Phase 31).

Inherits the ground rules, the unit-testing standard, and the logging/retention and resilience standards.

## Intent

Stop operator messages from flooding `#family-plans`, alert only on sustained outages, and make the cause of repeated `socket_disconnected` verdicts visible without adding a second recovery owner.

## Evidence (code, 2026-10-12)

| Fact | Citation |
| --- | --- |
| The listener samples `is_connected()` every 15 s. The heartbeat writes on state change, otherwise every 60 s. | `listener.py:118`, `listener.py:1162-1176`; `health.py:43`, `health.py:108-149` |
| `socket_disconnected` means the latest sample is disconnected and continuous disconnected samples go back more than 600 s. The printed `age` is the heartbeat-file age, not the outage length. | `health.py:221`, `health.py:231-236`, `health.py:271-281` |
| Posts fire on every ok↔not-ok edge. Recovery posts on the first ok verdict. There is no hysteresis and no minimum interval. | `health.py:324-336` |
| An unset alert channel falls back to `#family-plans`. | `health.py:248-258`; `docs/maintenance.md:192-193` |
| The token reminder posts to `#family-plans`. | `important_dates.py:622-630`; `google_token_reminder.py:375-391` |
| The checker runs every 600 s. | `deploy/launchd/com.cec.vivisystem.health-check.plist.template:39-40` |
| SDK/Bolt stdlib logs reach stdout only, and the LaunchAgent's stdout is `/dev/null`, so the SDK's reconnect reasons are lost on the Mini. | `logging.py:322-328`; `deploy/launchd/com.cec.vivisystem.listener.plist.template:29-31` |
| "One listener per data directory" is documentation only. The installer boots out only the labels it renders. | `docs/maintenance.md:38`; `scripts/install_launchagents.sh:116-133` |

**Conclusion:**
- **Certain:** the flood is caused by the alert policy plus the plans-channel fallback.
- **To confirm in step 0:** the trigger is either two listener processes overwriting one heartbeat file, or real outages of 10 minutes or more in which the SDK cannot reconnect.
- **Unlikely:** host sleep. It would mostly report `too_old` with large ages, not 12–59 s.
- **Not the problem:** the 15-s sampler and the 300 s / 600 s thresholds.

## Step 0 evidence (Mini, read-only, 2026-10-15 19:52–19:56 HKT)

Carter's answers (2026-10-15): run the checks; no family messages were sent during the alert windows, so reply evidence does not exist. Channel name: **`#vivi-status`** (locked 2026-10-15).

| Check | Result |
| --- | --- |
| Listener processes | 1 launchd job (`com.cec.vivisystem.listener`, uv pid 1699) → 1 Python child (pid 1735), both started 2026-10-14 16:02 HKT. No other `cec_vivisystem` process. |
| Heartbeat `pid` (3 reads, about 1 min apart) | 1735, 1735, 1735; `socket_mode_connected=true`, `disconnected_since=null` |
| LaunchAgents | Only the four Phase 29 labels (plus two old `backup-…` folders in `~/Library/LaunchAgents`, which are not loaded). Listener: `runs = 1`, never exited. |
| `listener_starting` | Once on 2026-10-10 02:49 HKT (Phase 29 install); then 2026-10-14 09:52 and 16:02 HKT, one after each reboot. No second writer at any time. |
| Disconnected samples (`listener-2026-10-12.log`, one HKT day) | 5,601 `socket_mode_disconnected` at a strict 15.0–15.1 s cadence (about 23.3 h of the day). 0 pairs less than 5 s apart, so only one sampler wrote. |
| Errors | 14,035 `socket_mode_error` (`BrokenPipeError: [Errno 32] Broken pipe`) on 10-12, about every 6 s. The same pattern ran from 2026-10-10 14:11 HKT until 2026-10-14 09:49 HKT. 0 errors since. |
| "Recovered" | 104 `socket_mode_recovered` on 10-12. Each one was followed by `BrokenPipeError` within 0.1 s and `socket_mode_disconnected` within 15–30 s. |
| Sleep (H3) | `pmset -g`: `sleep 0`. No Sleep/Wake/DarkWake entries in `pmset -g log`. |
| `listener.err.log` | 0 bytes |

**Verdict: H2, one process with an SDK connection stuck for four days. H1 (two writers) and H3 (sleep) are ruled out.** The single listener's Socket Mode client looped on a broken pipe (a ping on a dead socket, about every 6 s). The SDK reported a "recovered" connection about 100 times a day, but no recovery lasted more than about 30 s. Each brief recovery reset `disconnected_since`. The 10-minute checker therefore saw an outage of more than 600 s, then a few `ok` reads, which caused the stale/recovered flapping. Only the reboot on 2026-10-14 cleared the state. The listener has been connected and silent since. Replies were very probably down from 10-10 to 10-14, but no family message tested this.

**Consequences for this phase:**
- 30a stays as designed. Hysteresis would have reduced the about 18 posts per 3 hours to one "unstable" post and one reminder every 6 hours.
- 30a: the outage counter must track **total disconnected time over a window** (for example, more than 80 % of the last 30 min), not only `disconnected_since`. One connect lasting less than 60 s must not reset the outage.
- 30b, the single-instance lock, drops from evidence-gathering to **hygiene** (keep it, low priority).
- 30b, routing SDK logs, becomes **the main diagnostic**: it must capture why the SDK's reconnect leaves a dead socket.
- **New decision 7 (Carter):** a "wedged connection" exit. If the SDK has had no connection lasting 5 min or more for 60 min, the listener logs `socket_mode_wedged` and exits with a non-zero code, and launchd `KeepAlive` restarts it. This would reverse the Phase 22 "no self-exit" non-goal, so it needs ADR 0014 to supersede that rule explicitly. The other options are an alternative SDK transport (for example, `aiohttp` or `websockets`) or only alerting. A reboot fixed the fault, so a process restart very probably would have fixed it too.

**Side findings (not Phase 30 scope; report to Carter):**
- The 07:00 recap failed today (2026-10-15) with `RefreshError`. The Google token reminder logged `days_left=-10` (`expired_or_today`, skipped), so the Google refresh token has expired. Carter decided (2026-10-15) **not** to run the helper script now. Re-authorization will be done through the Phase 31b control app, as its acceptance test. Until then the 07:00 recap fails, and #vivi-status shows "expired" every day.
- The reminder **skips** when the token has expired, so the family did not see a warning. Phase 30a should post an "expired" line to the status channel instead of skipping.

## Decisions (locked by Carter 2026-10-15)

1. **Status channel `#vivi-status`.** Reuse `CEC_HEALTH_ALERT_CHANNEL_ID` (no rename) with a private channel and the bot invited. The fallback to the plans channel stays so alerts are never silent, and it logs a WARNING.
2. **Alert policy v2** with the defaults in the table below.
3. **Daily status** at 09:00 HKT, always posted. Its absence is a liveness signal for the FileVault/login blind spot in ADR 0013.
4. **Bilingual status posts:** an English (STE-style) block, then a Traditional Chinese block. No Simplified characters.
5. **One-listener lock: deferred.** The step 0 evidence showed one process only. The heartbeat records `pid` and `instance_id` so a second writer becomes visible; the `flock` is a later hygiene item.
6. **SDK ping interval stays at 5 s in this phase.** A change needs the 30b evidence and an ADR.
7. **Stuck-connection self-recovery (locked; reverses the Phase 22 "no self-exit" non-goal, recorded in ADR 0014).**
   - **Stuck** means that the listener's 15-s samples show no connection that lasted 5 minutes or more during the last `CEC_LISTENER_STUCK_AFTER_S` seconds (default 900). Also, the SDK's own auto-reconnect has not fixed it.
   - When the listener is stuck, it makes a **reconnect retry**: it closes the Socket Mode client and connects again in the same process. It does this up to `CEC_LISTENER_STUCK_RETRIES` times (default **3**, range 0–10, set in config/env; it will be editable in the Phase 31b control app). It waits `CEC_LISTENER_STUCK_RETRY_WAIT_S` (default 120) between retries. A retry succeeds when the connection stays up for 5 minutes, and the retry counter then goes back to 0.
   - If the listener is still stuck after N retries, it logs `socket_mode_stuck_exit` (ERROR), writes the heartbeat with `exit_reason`, and exits with code 75. The LaunchAgent `KeepAlive` and `ThrottleInterval` then start a new process. The heartbeat records `pid` and `instance_id`, so the restart is visible in #vivi-status.
   - This is the only self-exit. Health alerts never restart or kill the listener, and the checker stays read-only.
8. **Expired Google token (locked):** post an "expired" notice to #vivi-status every day until someone re-authorizes, instead of skipping it. All token reminders go to the status channel, not #family-plans.

## Step 0 — operator, before code (authorized separately)

1. **Relief now.** Create the status channel, invite the bot, and set `CEC_HEALTH_ALERT_CHANNEL_ID` in the Mini `.env`. The checker reads it on its next run; no restart is needed.
2. **Read-only evidence:**
   - `pgrep -fl cec_vivisystem.listener`
   - `launchctl list | grep -i -E 'cec|vivisystem'` and `ls ~/Library/LaunchAgents`
   - three reads of `pid` from `data/health/listener.json`, 40 s apart
   - counts of `listener_starting`, `socket_mode_disconnected`, `socket_mode_recovered`, `socket_mode_error` and `socket_mode_closed` in today's `logs/listener-*.log`
   - `pmset -g` and `pmset -g log | grep -E 'Sleep|DarkWake' | tail`

   Record counts and yes/no answers only (no message bodies or Slack identifiers). Also record whether family messages got replies during the alert windows.
3. **Re-verify the slack-sdk 3.43.0 facts** used by the design against the installed `slack_sdk/socket_mode/builtin/{client,connection}.py`: what `is_connected()` checks, the stale-pong rule, the auto-reconnect path, and the logger names.

## Slices

### 30a — stop the flood (one session)

| Item | Content |
| --- | --- |
| Policy engine | Pure `evaluate_alert(state, check, now, policy) -> (state, actions)`. Versioned state file `alert_state.json` v2; v1 `{"alerted": bool}` migrates in place. |
| Check data | `HealthCheck` gains `outage_started_at` and `disconnected_for_s`. The existing `pid` is carried into the text. |
| Messages | EN + TC catalog: outage (socket, stopped, missing), recovered, unstable, still down. Each states the start `HH:MM` HKT, the duration in minutes, the PID, a reason-specific next step, and the sentence that the check does not restart the listener. |
| Cadence | Health-check `StartInterval` 300; `RunAtLoad` stays false. |
| Channel | Shared `load_system_channel(env)` for health and the token reminder. `important_dates.main` and the standalone token CLI pass the system channel to `run_google_token_reminder`. Fallback logs WARNING `system_channel_fallback`. The 10:00 dates review keeps the plans channel. |

### 30b — make the trigger visible (one session)

| Item | Content |
| --- | --- |
| Heartbeat v2 | Adds `instance_id` (per process start), `started_at`, `last_connected_at`, `disconnect_episodes`, `disconnected_s` and `sdk_session_changes` (from `client.session_id()` when the client offers it). Existing keys are unchanged. Readers ignore missing fields. |
| Single instance | `fcntl.flock(LOCK_EX \| LOCK_NB)` on `data/health/listener.lock` before the handler connects. If the lock is held, log `listener_already_running` once (with the holder pid), overwrite `data/health/standby.json` every 60 s and retry. After acquiring the lock, start normally. Never a second Slack connection. |
| Multiple writers | The checker keeps the recent (pid, instance) pairs. Pattern A, B, A within 1 h, or a fresh standby record, triggers one notice per 24 h. |
| SDK logs | A forwarding handler on the `slack_sdk` and `slack_bolt` loggers sends records to structlog with `component=slack_sdk` (dated class-A file). Propagation is off to avoid duplicate stdout lines. Token-like strings (`xox?-`, `xapp-`) are redacted. `LOG_LEVEL` is honored; DEBUG payloads stay off by default. |
| Installer | `--dry-run` and install print a warning for agents matching `cec\|vivisystem` that the templates do not render. They are never booted out automatically. |

### 30c — daily digest (one session)

| Item | Content |
| --- | --- |
| Samples | Each checker run appends one JSON line to `data/health/samples/YYYY-MM-DD.jsonl` (status, reason, ages, pid, instance, counters, alert action). Files older than 14 days are purged on each run. |
| Job run records | A `record_job_run(label)` context manager in `morning_recap.main`, `important_dates.main` and `google_token_reminder.main` overwrites `data/health/jobs/<label>.json` (`started_at`, `finished_at`, `outcome`, `exit_code`, `pid`). A record failure is logged and never changes the job's outcome. |
| Snapshot | `status_snapshot.py`: pure readers for the heartbeat, alert state, samples, job records, delivery markers (posted, pending, missing) and token days (`days_until_expiry`). Phase 31 shares it. |
| Digest | The first checker run at or after 09:00 HKT posts once per HKT date (`digest_posted_for` in state). Content: connected %, disconnects (count, total, longest), alerts sent, restarts (instance changes), 07:00 recap state, 10:00 job state, Google access days left, pending deliveries. |

## Alert policy v2 (locked defaults)

| Rule | Default |
| --- | --- |
| Check cadence | 300 s |
| Unhealthy verdicts | `missing`, `invalid`, `future_timestamp`, `too_old` (> 300 s), `socket_disconnected` (> 600 s continuous); unchanged from Phase 29 |
| Open an outage | 2 consecutive unhealthy checks and an outage age of at least 15 min. Outage start = `disconnected_since`, else `written_at`, else the first unhealthy check. |
| Close an outage | 3 consecutive healthy checks and at least 15 min healthy. Posts only if an outage post was sent. |
| Unstable | A second outage within 6 h posts "unstable" instead of a plain outage. Recovery then needs 60 min healthy. Clears after 6 h with no outage. |
| Still down | Reminder every 6 h while an outage is open |
| Minimum gap | 30 min between outage, recovery, unstable and still-down posts. The digest and the multiple-listener notice are exempt. |
| Brief disconnects | No post; counted in the digest |
| Post failure | State does not advance; the next run retries (Phase 29 trade-off) |
| Restarts | None. The checker observes and reports only. |

States: `ok` → `suspect` → `alerted` ⇄ `recovering` → `ok`. A relapse during `recovering` returns to `alerted` without a post.

## Message catalog (status channel)

| Event | English | 繁體中文 |
| --- | --- | --- |
| Outage, socket | ⚠ Listener: no Slack connection. The connection stopped at 14:05 (25 minutes). The bot cannot reply to family messages. This check does not restart the listener. PID 4182. | ⚠ 監聽程式：沒有 Slack 連線。連線於 14:05 中斷（25 分鐘）。機械人未能回覆家人的訊息。此檢查不會重新啟動監聽程式。PID 4182。 |
| Outage, stopped | ⚠ Listener: stopped. The last heartbeat was at 14:05 (20 minutes). launchd starts the listener again if the process stopped. | ⚠ 監聽程式：已停止。最後心跳訊號在 14:05（20 分鐘前）。如程式停止，launchd 會再次啟動監聽程式。 |
| Recovered | ✓ Listener: connection restored at 14:40. Outage: 14:05–14:40 (35 minutes). | ✓ 監聽程式：連線已於 14:40 恢復。中斷時間：14:05–14:40（35 分鐘）。 |
| Unstable | ⚠ Listener: the connection is unstable. It stopped 2 times in 6 hours. The next recovery message comes after 60 minutes of stable connection. | ⚠ 監聽程式：連線不穩定。6 小時內中斷了 2 次。連線穩定 60 分鐘後才會發出恢復訊息。 |
| Still down | ⚠ Listener: still no Slack connection. The outage started at 08:10 (6 hours 5 minutes). | ⚠ 監聽程式：仍然沒有 Slack 連線。中斷由 08:10 開始（6 小時 5 分鐘）。 |
| Two listeners | ⚠ Two listener processes are running: PID 812 and PID 1430. Replies can go missing. Stop the process that launchd did not start. | ⚠ 有兩個監聽程式同時運行：PID 812 及 PID 1430。回覆可能會遺失。請停止並非由 launchd 啟動的程式。 |
| Token reminder | Google Calendar access expires in 2 days. Re-authorize Google access before Wed 14 Oct. | Google 日曆授權將於 2 日後到期。請在 10月14日（星期三）前重新授權。 |

The daily status layout follows design note §7.4. Times are HKT 24-hour; examples are synthetic.

## Test plan (draft; the implemented and locked subset is in "Implementation (2026-10-15)")

Fixed HKT clocks, temporary directories, a fake poster and fake socket client/handler. No network, real sleeps or real launchd.

| ID | Case | Expect |
| --- | --- | --- |
| P1 | Classify a v2 heartbeat; classify a Phase 29 heartbeat without new fields | `outage_started_at` / `disconnected_for_s` filled; the old file classifies exactly as today |
| P2 | Unhealthy once, outage 12 min | No post; state `suspect` |
| P3 | Unhealthy twice, outage ≥ 15 min | One outage post with start `HH:MM`, minutes and PID |
| P4 | Unhealthy, then healthy before alerting | No post |
| P5 | `alerted`, then healthy twice (10 min) | No post; state `recovering` |
| P6 | Healthy three times and ≥ 15 min | One recovery post with outage window and duration |
| P7 | Relapse during `recovering` | Back to `alerted`; no post |
| P8 | Second outage within 6 h | "Unstable" post instead of outage; recovery needs 60 min |
| P9 | Replay: verdict alternates at every 10-min check for 3 h; then every 20 min | At most 1 and at most 3 posts; the v1 count (18 / 9) is recorded in the test as the baseline |
| P10 | Outage open 5 h, 6 h, 12 h | Reminders at 6 h and 12 h only |
| P11 | Two eligible posts within 30 min | Second post deferred until the gap passes |
| P12 | Poster raises for each action | State not advanced; retried next run; `health_alert_failed` logged |
| P13 | State v1 `alerted` true / false; corrupt state | `alerted` / `ok` / `ok` plus `health_alert_state_unreadable` |
| P14 | Missing heartbeat at install | Outage after 15 min and 2 checks with "no heartbeat data" wording |
| P15 | Text contract | EN and TC blocks; no Simplified markers (`parser.contains_simplified_markers`); no token-like strings; "does not restart" sentence present |
| C1 | Channel resolution | Alert channel wins; fallback to the plans channel logs `system_channel_fallback` |
| C2 | Token reminder routing | System channel when set, plans channel when unset; dates review always plans |
| T1 | Templates via installer `--dry-run` | Health-check `StartInterval` 300, `RunAtLoad` false; listener keys unchanged |
| H1 | Heartbeat v2 shape | New fields plus Phase 29 keys; injected `instance_id` and clock |
| H2 | Samples true, false, false, true at 15 s | `disconnect_episodes` 1, `disconnected_s` 30 |
| H3 | SDK session id changes with no false sample | `sdk_session_changes` increments |
| L1 | Second lock attempt on a held lock | Non-blocking failure; `listener_already_running` with holder pid; `standby.json` written |
| L2 | Holder releases | Standby acquires; `listener_lock_acquired` logged |
| L3 | Runtime wiring with fake handler and held lock | Handler never connects; only the holder writes the heartbeat |
| L4 | Garbage lock or standby file | Controlled result; no crash |
| M1 | Writers alternate A, B, A within 1 h | One notice; none again within 24 h |
| M2 | Restart A → B with no return | No notice |
| S1 | INFO record on a `slack_sdk.socket_mode` logger | One line in the `slack_sdk` dated file (temporary log dir); no duplicate; DEBUG dropped at INFO |
| S2 | Record containing `xoxb-…` / `xapp-…` | Redacted in file and console |
| I1 | Installer `--dry-run` with an unmanaged `cec` plist in a fake HOME | Warning printed; no `launchctl` call |
| D1 | Checker runs | One sample line per run; files older than 14 days purged |
| D2 | Runs at 08:55, 09:00, 09:05 on one date; poster fails once | One digest per date, none before 09:00; failure retried |
| D3 | Digest from fixtures | Connected %, disconnects, longest, restarts, recap posted/pending/missing, 10:00 job record, token days, pending deliveries |
| D4 | No samples (fresh install) | "No data for the last 24 hours" line; digest still posts |
| J1 | Job run record on success, failure and exception; record write fails | Record shape; job outcome and exit codes unchanged; failure logged |
| G1 | Garbage samples, job records or markers | Skipped with a warning; digest still posts |

Non-tests: Mini network, real SDK reconnect timing, Slack delivery and channel membership, launchd timing, `pmset`, two real launchd agents, Socket Mode on the Mini, Chinese wording quality beyond the lint.

Target about 40 new cases plus the full suite, Ruff and `git diff --check`.

## Logging and retention

- **Class A** (`component=health`, 14 days):
  - `health_outage_opened`, `health_outage_closed`
  - `health_alert_suppressed` (with `reason`: `hysteresis`, `min_gap`, `unstable`)
  - `health_multiple_listeners` (WARNING)
  - `health_digest_sent` / `health_digest_failed`
  - `system_channel_fallback` (WARNING)

  In the listener: `listener_lock_acquired`, `listener_already_running` (ERROR), `listener_standby_waiting` (hourly). SDK and Bolt records go to `component=slack_sdk` under the existing 14-day retention and 100 MB cap.
- **Class C** (single overwritten records, no purge needed): `data/health/listener.json` (v2), `alert_state.json` (v2), `listener.lock`, `standby.json`.
- **Class E:** `data/health/samples/*.jsonl` (14 days, purged on each checker run); `data/health/jobs/*.json` (last-known status, overwritten).
- None of these hold message bodies, tokens or family content. The `.gitignore` `data/*` rule already covers them.

## Operator rollout (authorized separately)

1. Step 0 above, before or during review.
2. After the reviewed merge: `uv sync --locked`, then `scripts/install_launchagents.sh --dry-run`, then install. The checker becomes 300 s, and the listener kickstart takes the lock.
3. Verify:
   - one `listener_lock_acquired`
   - the heartbeat has `instance_id`
   - `logs/slack_sdk-*.log` exists
   - `#family-plans` receives no system posts
   - the status channel is quiet while healthy
   - the next 09:00 digest arrives
4. Optional, at night: a controlled outage (`launchctl bootout` of the listener for 25 min, then `bootstrap`). Expect one outage post and one recovery post. A synthetic heartbeat file with `--path` and a temporary `--state-path` is the no-downtime alternative.

## Implementation (2026-10-15)

Scope follows Carter's 2026-10-15 brief, which narrows 30b and 30c:

| Slice | Implemented |
| --- | --- |
| 30a | `status_channel.load_system_channel` (WARNING `system_channel_fallback`); heartbeat v2 `outage_since` (a connection < 60 s does not close an outage) used by the checker; pure `health_policy.evaluate_alert` with v2 state and v1 migration; EN + TC catalog in `status_messages.py`; checker `StartInterval` 300; token notices to the status channel, and "expires today" / "expired" once per HKT date instead of a skip. |
| 30b | Heartbeat adds `instance_id`, `started_at`, `last_connected_at`, `disconnect_episodes`, `disconnected_s`, `last_outage_s`, `last_outage_ended_at`, `stuck_retries`, `exit_reason` (Phase 29 keys unchanged). `slack_sdk` / `slack_bolt` stdlib loggers → `logs/slack-sdk.log` (`RotatingFileHandler`, INFO+, 5 MB × 3) with redaction, instead of the dated structlog file in the draft. Decision 7 stuck guard (`stuck_connection.py`, `listener.SocketSession`, `monitor_socket_health`). |
| 30c | One sample line per `--alert` run (`data/health/samples/`, 14 days); `daily_status.build_daily_status` posted by the first run at or after 09:00 HKT, once per date, retried on failure. Job outcomes come from existing markers and dated logs only. |

**Deferred (not in the brief; backlog):** `fcntl` single-instance lock and standby record (L1–L4), multiple-writer notice (M1–M2), `record_job_run` job records (J1), installer warning for unmanaged agents (I1), `sdk_session_changes` (H3), "pending deliveries" and "alerts sent" digest lines, and the `status_snapshot.py` module shared with Phase 31 (the readers live in `daily_status.py` for now).

**Behaviour notes:**
- Samples and the daily status run only with `--alert` (the scheduled job). A plain `python -m cec_vivisystem.health` stays read-only.
- "Restored at" in a recovery post is the first healthy check, and the outage window ends there.
- The 10:00 dates-review line comes from `logs/important_dates-*.log` (today, else yesterday). The 07:00 recap line comes from today's marker, else `morning_recap_failed` in today's log, else "not posted today".
- Invalid `CEC_LISTENER_STUCK_*` values log `listener_stuck_config_invalid` and use the default, so a typo cannot crash-loop the listener under `KeepAlive`.
- A retry replaces the whole client (close, build, connect). It never calls `connect_to_new_endpoint(force=True)`.

**Locked and implemented tests** (fixed HKT clocks, temporary directories, fake poster, fake socket handlers; no network, sleeps or launchd):

| IDs | File | Cases |
| --- | --- | --- |
| H1, H2, P1, outage debounce, exit heartbeat | `tests/test_health.py` | v2 shape with injected `instance_id`/clock; T,F,F,T → 1 episode / 30 s; < 60 s reconnects keep `outage_since`; Phase 29 files classify as before; v2 garbage is `invalid`; `too_old` starts at the last write |
| P12, P13, C1, CLI | `tests/test_health.py` | failed post keeps state and retries; v1 true/false migrate; corrupt/unknown state → ok + warning; status channel wins, fallback logged; plain check writes no samples; `--alert` CLI flow |
| P2–P11, P14, P15 | `tests/test_status_channel.py` | open/close hysteresis, relapse, unstable + 60 min, replay (v1 18 / 9 → v2 1 / 3), 6-h reminders, 30-min gap, missing at install, catalog is EN then TC with no Simplified markers or tokens and the "does not restart" sentence |
| D1–D4, G1 | `tests/test_status_channel.py` | one sample per run + 14-day purge; 08:55/09:00 (fail)/09:05/09:10 → one status per date; fixture summary (98.8 %, 2 disconnects, longest 10 min, 1 restart), recap posted/pending/failed/missing, dates review, token days/EXPIRED, fallback line; fresh install; garbage sources skipped with warnings |
| C2, G5, G11 | `tests/test_google_token_reminder.py` | token notice → status channel (fallback plans), dates review stays in plans; expiry-day notice; expired once per HKT date |
| K1–K7, L-stuck, runtime | `tests/test_stuck_connection.py` | stable and ordinary reconnects never act; < 30 s connects do not reset; retries at 900/1020/1140 s, exit at 1260 s; 5 min up resets; retries 0 → exit; config range/garbage → default + warning; monitor loop closes old clients, never force-reconnects, writes `exit_reason`, raises `SystemExit(75)`; real `run_socket_mode` wiring with fake Bolt |
| S1, S2, retention | `tests/test_slack_sdk_logging.py` | SDK/Bolt INFO lines in `slack-sdk.log` once, DEBUG dropped, no duplicate handlers; tokens/tickets/text redacted in file and console; dated purge and soft cap skip the rotating file |
| T1 | `tests/test_launchd_templates.py` | health-check `StartInterval` 300, `RunAtLoad` false; listener keys unchanged |

Verification: `uv run pytest -q` 516 passed (439 before; 77 net new, plus Phase 29 alert tests rewritten for policy v2); `uv run ruff check .` and `git diff --check` clean. Not verified: anything on the Mini, Slack delivery and channel membership, launchd timing, real SDK reconnect behaviour, and STE wording against the official dictionary.

## Acceptance

- [x] Step 0 evidence recorded (2026-10-15, above): H2, single wedged process
- [x] Carter confirmed decisions 1–8 and the channel name (2026-10-15)
- [x] 30a, 30c and the briefed 30b items: tests locked, implemented and green; Ruff and diff check pass (deferred 30b/30c items listed above)
- [x] ADR 0014; maintenance (status channel, alert policy, daily status, stuck exit, `slack-sdk.log`, after-deploy steps); logging-and-retention rows; `.env.example`; architecture Observer row; PROGRESS
- [ ] Mini (authorized): status channel receives alerts; `#family-plans` receives none; SDK log file present; heartbeat has `instance_id`
- [ ] One controlled or synthetic outage shows exactly one outage and one recovery post
- [ ] First 09:00 daily status received

## Out of scope

- Restarts or kills by the health checker, and any reconnect loop other than the bounded decision 7 guard.
- Ping or auto-reconnect changes without evidence and an ADR.
- Scheduled-post reruns or automated reconciliation (ADR 0009).
- An off-box dead-man's switch.
- The control app.
- Calendar, parser, confirmation or LLM behavior.
- Moving family posts.
