# ADR 0014: Status channel, alert hysteresis, and the stuck-connection exit

Date: 2026-10-15. Status: Accepted for Phase 30 (decisions by Carter, 2026-10-15).
Supersedes: the Phase 22 non-goal "no self-exit / no process-restart watchdog" for
the single case below. Extends ADR 0013 (heartbeat and alert-only checker).

## Context

- From 2026-10-10 to 10-14 one listener process on the Mini had a stuck
  slack_sdk builtin Socket Mode client. It logged `BrokenPipeError` about every
  6 s. About 100 times a day the SDK reported a new session, but each one broke
  within 30 s. Only a reboot fixed it. Read-only Mini checks ruled out two
  listener processes and host sleep (Phase 30 step 0).
- Each brief connect reset the heartbeat's `disconnected_since`. The Phase 29
  checker posts on every ok↔stale edge, so the verdict flipped and posted at
  almost every 10-minute check. With `CEC_HEALTH_ALERT_CHANNEL_ID` unset, all
  those posts went to `#family-plans`.
- The SDK's own reconnect messages were lost: the listener LaunchAgent sends
  stdout to `/dev/null`, and stdlib loggers wrote only to stdout.
- The Google token reminder skipped silently once the token had expired.

## Decision

1. **System-status channel.** `CEC_HEALTH_ALERT_CHANNEL_ID` (no rename) names
   a private `#vivi-status` channel. Health alerts, the daily status and Google
   token notices go there through `status_channel.load_system_channel`. If it
   is unset they fall back to the plans channel with a WARNING
   (`system_channel_fallback`), so they are never silent. Family posts (07:00
   recap, 10:00 dates review, thread replies) do not move.
2. **Outage measured over time, not by one snapshot.** Heartbeat v2 adds
   `outage_since`, which clears only after the connection stayed up 60 s. The
   checker uses it when present (Phase 29 files classify as before).
3. **Alert policy v2** (`health_policy.evaluate_alert`, persisted in
   `alert_state.json` v2; v1 files migrate): open after 2 unhealthy checks and
   ≥ 15 min; close after 3 healthy checks and ≥ 15 min (60 min when unstable);
   a second outage within 6 h posts "unstable"; reminder every 6 h; ≥ 30 min
   between these posts; checker every 300 s. A failed post does not advance
   state (the ADR 0013 trade-off is unchanged). Texts are English (STE style)
   then Traditional Chinese, with start time, duration, reason and PID.
4. **Daily status at 09:00 HKT**, posted by the first checker run at or after
   09:00, once per HKT date, always. Its absence is the liveness signal for the
   FileVault/login blind spot. Content comes only from existing records
   (checker samples, recap markers, dated logs, the configured token issue
   date). The checker keeps 14 days of class-E samples.
5. **SDK logs to a file.** `slack_sdk` and `slack_bolt` stdlib records (INFO+)
   also go to `logs/slack-sdk.log` (rotating, 5 MB × 3 backups). Token-like
   strings, Socket Mode tickets and `"text"` values are redacted.
6. **Stuck-connection self-recovery in the listener (reverses the Phase 22
   non-goal).** "Stuck" means: the latest 15-s sample is disconnected and no
   connection lasted ≥ 5 min during the last `CEC_LISTENER_STUCK_AFTER_S`
   (default 900 s). The listener then closes the whole Socket Mode client and
   connects a newly built one, up to `CEC_LISTENER_STUCK_RETRIES` (default 3,
   0–10) times, `CEC_LISTENER_STUCK_RETRY_WAIT_S` (default 120 s) apart. A
   connection that stays up 5 min resets the counter. After the last failed
   retry the listener logs `socket_mode_stuck_exit`, writes the heartbeat with
   `exit_reason`, and exits with code 75; launchd `KeepAlive` starts a new
   process. Invalid values log `listener_stuck_config_invalid` and use the
   default, so a typo cannot crash-loop the listener.
7. **Expired Google token:** post an "expires today" / "expired" notice once
   per HKT date instead of skipping.

## Why this does not bring back the September storm

Phase 22 removed a 15-s forced `connect_to_new_endpoint(force=True)` loop that
competed with the SDK's own reconnect on the same client. Here the SDK keeps
sole ownership for at least 15 minutes. The guard acts only when that has
visibly failed, at most N times, minutes apart, and it replaces the whole
client: `close()` stops the old client's auto-reconnect before the new one
starts, so one owner exists at a time. The final step is a process exit, which
launchd already owns. The health checker stays read-only and never restarts,
kills or reconnects anything.

## Consequences

- An outage now posts after about 15–20 min instead of 10–20 min. Brief
  disconnects never post; they show in the daily status.
- A stuck connection is replaced in-process after about 15 min and the process
  exits after about 21 min (defaults). During a real Slack or network outage the
  listener can also exit and restart every ~21 min plus `ThrottleInterval`. That is
  harmless (one new process each time) and visible as restarts in the daily
  status.
- An in-process retry closes the old client, which waits for running message
  handlers. A message that arrives during the swap can be redelivered by Slack;
  existing source-derived confirmation IDs keep that safe.
- `logs/slack-sdk.log` is bounded by rotation (about 20 MB) and is not part of
  the dated-file purge.
- Not done here (Phase 30 backlog): the single-instance `flock`, the
  multiple-writer notice, job run records, the installer warning for unmanaged
  agents, a "pending deliveries" count, and any SDK ping or transport change
  (needs `slack-sdk.log` evidence and its own ADR).
