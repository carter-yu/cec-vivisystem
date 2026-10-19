# Phase 29 — Mini autostart + heartbeat

**Status:** decision locked by Carter on 2026-10-10 (HKT); repository slice implemented locally. Mini install, reboot test and live alert remain separately authorized operator actions.
**Depends on:** design note `cec-vivisystem-scratch/design-mini-autostart.md` (workshop, untracked); [ADR 0013](../docs/decisions/0013-mini-autostart-and-heartbeat.md).
**Non-goals:** Mini install from this agent, live deploy, auto-login, LaunchDaemons, turning FileVault off, competing Socket Mode reconnect loops, automatic kickstart/kill from a checker, launchd retries of Phase 25 scheduled posts, an off-box checker.

## Intent

Ensure the Slack listener and the 07:00 / 10:00 jobs return whenever Carter's GUI session is running on the Mac Mini, and make a dead or disconnected listener visible through an alert-only heartbeat check.

## Locked decision (Carter, 2026-10-10)

1. **FileVault stays ON.** No automatic login. No LaunchDaemon (option B rejected).
2. **Option A + C.** Hardened LaunchAgents in `gui/<uid>` (A) plus a heartbeat with an alert-only stale check (C).
3. **After any reboot Carter logs in by hand** at the Mini (or Screen Sharing after unlock). Agents start only after that login. Planned restarts use `sudo fdesetup authrestart` so the FileVault unlock is pre-authorized; the Aqua login is still manual.
4. **Stdout:** every agent's `StandardOutPath` is `/dev/null`; dated `logs/*.log` stay the application record; stderr goes to `~/Library/Logs/cec-vivisystem/<name>.err.log`.
5. **Heartbeat:** the listener's existing health loop writes `data/health/listener.json` atomically about every 60 s (and on connection-state changes). Write failures are logged, never fatal.
6. **Checker:** `python -m cec_vivisystem.health` reports `ok` / `stale` / `missing` with exit codes 0 / 1 / 2. Missing is a distinct exit code, not a crash. `--alert` posts once on transition into stale/missing and once on recovery (state file dedupe). It never kickstarts, kills or reconnects anything.
7. **Known blind spot:** the checker runs as a GUI LaunchAgent on the same Mini. At the FileVault pre-boot screen or the login window nothing of ours runs, so neither the checker nor Grok Bot nor a human can learn of the outage from the Mini side. An off-box dead-man's switch is out of scope for this phase.

## Deliverables

| Slice | Content |
|-------|---------|
| Templates | `deploy/launchd/*.plist.template`: listener (RunAtLoad, KeepAlive), morning-recap 07:00, important-dates 10:00, health-check every 600 s (no RunAtLoad, so login does not send a false stale alert from the pre-reboot heartbeat). Placeholders `__UV__`, `__UV_DIR__`, `__REPO__`, `__HOME__`. `TZ=Asia/Hong_Kong`. |
| Installer | `scripts/install_launchagents.sh`: detect `uv` via `command -v`, render, `plutil -lint`, back up old plists, `bootout` then `bootstrap gui/$(id -u)`, kickstart the listener only. `--dry-run` renders and prints commands without touching launchd. |
| Heartbeat | `cec_vivisystem.health.ListenerHeartbeat` called from the listener health loop; injectable clock, writer and pid. |
| Checker / alert | `cec_vivisystem.health.main` with `--max-age-s` (default 300), `--disconnect-max-age-s` (default 600), `--alert`. Alert destination `CEC_HEALTH_ALERT_CHANNEL_ID`, falling back to `SLACK_FAMILY_PLANS_CHANNEL_ID`. |
| Docs | `docs/maintenance.md` "After a reboot" runbook; ADR 0013; retention row for `data/health/`; PROGRESS; `prompts/pr-0005-mini-autostart.md`. |

Scheduled 07:00 / 10:00 templates keep `RunAtLoad` false: loading them (login, install) must not post a family message early or outside the schedule. A missed slot while the Mini was off or logged out is not replayed; follow ADR 0009 if a manual run is chosen. Only the listener is kickstarted by the installer for the same reason, and it is bootstrapped first.

## Test table (offline, locked before implementation)

| Case | Expect |
|------|--------|
| Heartbeat first observe with frozen clock | Writes `written_at` (HKT ISO with offset), `pid`, `socket_mode_connected=true`, `disconnected_since=null` |
| Heartbeat within interval | No second write; after interval, writes again |
| Connection drops / recovers | Immediate write; `disconnected_since` set to first false observation, kept while down, cleared on recovery |
| Writer raises `OSError` | Logged `listener_heartbeat_write_failed`; no exception; next observe retries |
| Listener runtime wiring (fake handler) | Health loop produces `listener.json` in the injected dir |
| Checker fresh / old / missing / garbage / non-object / future timestamp | `ok` (0) / `stale` (1) / `missing` (2) / `stale` invalid (1) / `stale` (1) / `stale` (1) |
| Checker disconnected short vs long | `ok` under threshold; `stale` reason `socket_disconnected` over threshold; disconnected without valid `disconnected_since` is stale |
| Alert ok→stale→stale→ok→ok | Exactly one stale post and one recovery post |
| Alert starts missing; corrupt state file | Posts stale once; corrupt state treated as not alerted |
| Alert post raises | Logged failure; state not advanced, so the next run retries |
| Alert config | Explicit channel wins, plans channel fallback, missing token/channel is a config error |
| CLI exit codes | `main([...])` returns 0 / 1 / 2 with fake poster; prints one status line |
| Plist templates via installer `--dry-run` | Render with fake uv/home; `plistlib` parses; labels, ProgramArguments, WorkingDirectory, RunAtLoad, KeepAlive (listener only), StartCalendarInterval 7:00 / 10:00, StartInterval 600, `/dev/null` stdout, stderr path, `TZ`; no leftover placeholders; no launchctl invoked |

Non-tests: real launchd, `plutil`, reboots, FileVault, live Slack posts, Socket Mode on the Mini network.

## Operator Mini checklist (manual; authorized separately)

Diagnose read-only → energy settings (restart after power failure, no system sleep) → free disk / truncate old unbounded out-logs → `scripts/install_launchagents.sh --dry-run` then install → `sudo fdesetup authrestart` → log in by hand → verify per `docs/maintenance.md` "After a reboot" → Slack `help` → simulate a stale heartbeat once and observe one alert and one recovery.

## Acceptance

- [x] Carter answered FileVault / auto-login questions (FileVault on, no auto-login, no LaunchDaemon)
- [x] Templates, installer, heartbeat, checker, alert and docs implemented locally
- [x] Offline tests and Ruff green (rules 4 and 5: tests plus structured logs for heartbeat/check/alert)
- [ ] Templates + maintenance docs merged
- [ ] Authorized install and `authrestart` + manual login on Mini brings agents up without manual `kickstart`
- [ ] Stale alert and recovery demonstrated once on Mini (authorized)

## Out of scope

Off-box checker or dead-man's switch, auto-login, LaunchDaemons, automatic restart from the checker, scheduler catch-up of missed slots, log rotation for `*.err.log` beyond the documented operator truncate, RRULE, calendar update, series-delete, Observer product, auto deploy from Grok Bot.
