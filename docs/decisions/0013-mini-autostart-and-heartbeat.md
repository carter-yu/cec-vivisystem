# ADR 0013: GUI LaunchAgents with FileVault on, plus an alert-only heartbeat

Date: 2026-10-10. Status: Accepted for Phase 29 (decision by Carter, 2026-10-10).

## Context

After a Mac Mini restart the listener and the 07:00 / 10:00 jobs stayed down for
several days unnoticed. Agents in `gui/<uid>` start only after the user logs in;
FileVault disables automatic login. The Mini's plists were untracked, and an
unbounded launchd stdout file had previously filled the disk.

## Decision

- **FileVault stays on.** No automatic login and no LaunchDaemon. After any
  reboot Carter logs in by hand; planned restarts use `sudo fdesetup authrestart`.
- **Option A:** tracked templates in `deploy/launchd/` rendered by
  `scripts/install_launchagents.sh` into `~/Library/LaunchAgents` and bootstrapped
  into `gui/$(id -u)`. Listener: `RunAtLoad` + unconditional `KeepAlive`,
  `ThrottleInterval` 30. Scheduled jobs: `StartCalendarInterval` only, no
  `RunAtLoad`/`KeepAlive`, so login or install never posts early and launchd never
  retries a Phase 25 post. All agents: absolute `uv`, checkout `WorkingDirectory`
  (for `.env`), `TZ=Asia/Hong_Kong`, stdout `/dev/null`, stderr in
  `~/Library/Logs/cec-vivisystem/`.
- **Option C:** the listener health loop writes `data/health/listener.json`.
  A 10-minute LaunchAgent runs `python -m cec_vivisystem.health --alert`:
  exit 0 ok, 1 stale (old, malformed, future, or disconnected longer than the
  threshold), 2 missing, 3 alert configuration missing. It posts once when the
  state becomes stale/missing and once on recovery, deduplicated by
  `data/health/alert_state.json`. It never kickstarts, kills or reconnects.
- **Missing is distinct from stale** so "never started since install or data
  reset" is visible separately, but both trigger the same alert.
- The heartbeat is driven from the existing health loop, not a separate thread:
  a hung loop then goes stale too. It writes on state change and every 60 s.

## Consequences

- A surprise power loss leaves the Mini at the FileVault screen; nothing runs
  until Carter logs in. The checker shares that fate, so neither it, Grok Bot nor
  a human can learn of the outage from the Mini side. An off-box dead-man's
  switch would close this gap and is explicitly out of scope.
- The checker cannot detect a dead Mini, a full disk that also stops the checker,
  or a stopped checker. It detects a dead/hung/disconnected listener while the
  session is up.
- An alert post failure leaves state unchanged, so the next run retries; an
  ambiguous Slack failure may repeat one operator alert. Acceptable for an ops
  notice; family scheduled posts still follow ADR 0009.
- Slots missed while logged out are not replayed. Manual runs follow ADR 0009.
- `socket_mode_connected` is a transport snapshot, not proof of delivery.
