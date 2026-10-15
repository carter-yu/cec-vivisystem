---
date: 2026-10-15 HK
branch: feat/phase-30-status-channel
model: claude-opus-5-5 (Claude Code)
goal: Implement Phase 30 — system-status channel, alert policy v2 with hysteresis, SDK log capture and heartbeat identity, stuck-connection self-recovery with exit 75, 09:00 daily status, and token notices moved to the status channel.
constraints: decisions locked by Carter 2026-10-15; offline tests with fakes only (no Slack, Google or network; fake clock and fake socket client, no sleeps); health checker stays read-only; no Simplified Chinese; ADR 0014 (not 0011/0012); installer only with --dry-run; no commit, push or access to any other machine.
acceptance: uv run pytest -q all green and uv run ruff check . clean; ADR 0014; .env.example and maintenance runbook updated; phase doc status/acceptance, PROGRESS (new 2026-10-15 entry; 2026-10-12 wording corrected) and this archive written; final summary of files, test count, behaviour changes, new env vars and deferred items.
---

# Manager request (sanitized archive)

Implement Phase 30 on the current branch `feat/phase-30-status-channel` (based on
the Phase 30–31 design commit). Read first: the Phase 30 document (locked
decisions 1–8, slices 30a/30b/30c, alert policy v2 table, message catalog, test
plan), the workshop note `design-status-channel-and-control-app.md` (§0, §0a,
§2–§4, §7.4), ground rules, the maintenance guide, ADR 0013, the Phase 22
document, `health.py`, `listener.py`, `logging.py`, `google_token_reminder.py`,
`important_dates.py`, the LaunchAgent templates, the installer script, and the
Phase 29 prompt archive (format).

Locked by Carter on 2026-10-15:

- **Status channel `#vivi-status`** through the existing
  `CEC_HEALTH_ALERT_CHANNEL_ID`. All operator and system messages (health
  alerts, unstable, still-down reminders, daily status, Google token
  reminders) go there. Fall back to the plans channel only if it is unset, with
  a WARNING log.
- **Mini evidence:** one listener process whose slack_sdk builtin Socket Mode
  client looped on `BrokenPipeError` (about every 6 s) for 4 days; about 100
  "recovered" per day, each lasting under 30 s. Each short connect reset
  `disconnected_since`, which caused the stale/recovered flapping. A reboot
  fixed it.
- **30a alert policy v2:** measure the outage as disconnected time over a window
  (short reconnects under 60 s must not reset it). Alert after 2 consecutive
  unhealthy checks and at least 15 min of outage. Recovered only after 3
  healthy checks and 15 min stable (60 min when unstable). A second outage
  within 6 h posts "unstable" instead. Still-down reminder every 6 h. At least
  30 min between posts. Checker every 300 s (update the health-check template).
  Alert text shows outage start (HKT), duration, reason in words and writer PID.
  Persist state (v2 file; migrate or ignore v1 safely).
- **30c daily status at 09:00 HKT:** the first checker run at or after 09:00
  posts once per HKT date, always (absence is a liveness signal). Content:
  listener state; connected %, disconnect count and longest over 24 h (from
  samples kept by the checker, small retention); Google token days left or
  EXPIRED; last 07:00 recap and 10:00 job outcome if easily available from
  existing markers or logs (keep it simple; omit what is not available; do not
  invent).
- **Token reminder:** move to the status channel; when expired, post "expired"
  daily (once per HKT date) instead of skipping. Stop posting token reminders to
  `#family-plans`.
- **30b:** route the `slack_sdk` / `slack_bolt` stdlib loggers into a rotating
  file (for example `logs/slack-sdk.log` through `RotatingFileHandler`, bounded
  size and backups, INFO and higher, no tokens or payload bodies) so they are
  kept although launchd stdout is `/dev/null`. The heartbeat records the PID
  plus `instance_id` and `started_at` (keep the Phase 29 keys compatible).
- **Stuck-connection self-recovery** (reverses the Phase 22 "no self-exit" rule;
  write ADR 0014, do not use 0011/0012): in the listener, "stuck" means no
  connection lasting at least 5 min within the last `CEC_LISTENER_STUCK_AFTER_S`
  (default 900) while SDK auto-reconnect has not fixed it. Then retry the
  reconnect (close the Socket Mode client, connect again in-process) up to
  `CEC_LISTENER_STUCK_RETRIES` (default 3, range 0–10, validated in config) with
  `CEC_LISTENER_STUCK_RETRY_WAIT_S` (default 120) between retries. Success means
  the connection stays up 5 min, and the counter resets. If still stuck after N
  retries: log `socket_mode_stuck_exit` (ERROR), write the heartbeat with
  `exit_reason`, and exit with code 75 so launchd `KeepAlive` restarts it. It
  must be injectable and testable with a fake clock and fake client (no real
  sockets, no sleeps in tests). The health checker stays read-only (never
  restarts).
- **Messages:** English in ASD-STE100 style first (short sentences, active
  voice, one instruction per sentence), then Traditional Chinese (no
  Simplified). Use the message catalog in the phase document and design §7.4.
- **Docs:** document the new environment variables in `.env.example` and the
  maintenance guide (reboot/after-deploy runbook, how to read
  `logs/slack-sdk.log`, what the stuck exit means). Update the Phase 30 status
  and acceptance and `PROGRESS.md` (new top entry dated 2026-10-15; also correct
  the 2026-10-12 entry phrase "behind Tailscale Serve" to "on the home LAN (HTTP
  first)").
- **Archive:** write this file in the same format as the Phase 29 archive.

Constraints: offline tests only with fakes (no Slack, Google or network). Do not
commit, push or touch any other machine. Do not run the install script except
with `--dry-run`. Keep changes focused; no Simplified Chinese. Finish with
`uv run pytest -q` all green and `uv run ruff check .` clean, then print a
summary: files changed, test count, behaviour changes, new environment
variables, and anything deferred.

Sanitization: workshop note path reduced to its file name; Mini user paths,
process IDs, account identifiers, channel IDs and credentials omitted. No family
records or incident log text included. Implementation results and deferrals are
recorded in PROGRESS and the Phase 30 document, not here.
