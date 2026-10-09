---
date: 2026-10-10 HK
branch: feat/phase-29-mini-autostart
model: claude-opus-5-5 (Claude Code)
goal: Implement Phase 29 — tracked LaunchAgent templates and installer, listener heartbeat, alert-only stale checker, reboot runbook.
constraints: FileVault stays on; option A+C; manual login after reboot; no auto-login, no LaunchDaemon; checker never kickstarts; installer only run with --dry-run off-Mini; offline tests with fakes; no commit, push, Mini access, or live Slack/Google.
acceptance: templates parse with plistlib and contain required keys; heartbeat, checker states and alert dedupe tested; maintenance "After a reboot" runbook; phase doc, PROGRESS and this archive updated; pytest and ruff check green.
---

# Manager request (sanitized archive)

Locked by Carter on 2026-10-10: FileVault stays ON; option A+C; after a reboot
Carter logs in by hand (no auto-login, no LaunchDaemon). Update the Phase 29
document to reflect this decision.

Read the workshop design note, the Phase 29 document, ground rules, maintenance
guide, and existing listener/launchd material first. Then build:

1. Tracked LaunchAgent plist templates (for example under `deploy/launchd/`) for
   the listener, the 07:00 morning recap and the 10:00 important-dates job,
   matching the existing CLI entry points. `RunAtLoad` true; `KeepAlive` true for
   the listener; placeholders for the absolute `uv` path, repository and home;
   `WorkingDirectory`; `StandardOutPath` `/dev/null`; `StandardErrorPath`
   `~/Library/Logs/cec-vivisystem/<name>.err.log`; `TZ=Asia/Hong_Kong`. Plus
   `scripts/install_launchagents.sh` that detects `uv` via `command -v`, supports
   `--dry-run`, renders, runs `plutil -lint`, boots out old jobs, bootstraps into
   `gui/$(id -u)` and kickstarts. Do not run it beyond `--dry-run` on Linux.
2. Listener heartbeat: periodic writer in the listener producing
   `data/health/listener.json` atomically (`written_at` ISO with timezone, `pid`,
   `socket_mode_connected`), about every 60 seconds, injectable clock/writer;
   failures logged, not fatal. A small checker CLI reporting ok/stale/missing with
   exit codes 0/1/2, configurable maximum age (default about 5 minutes), also
   stale when disconnected longer than a threshold.
3. Stale alert (C), alert-only, no automatic kickstart: a periodic Mini
   LaunchAgent (about every 10 minutes) runs the checker with `--alert`, posting
   through the existing Slack client/token configuration to Carter's DM or a
   configured channel variable, only on ok→stale (state-file dedupe) and once on
   recovery. Document that at the FileVault login screen nothing runs, so the
   outage cannot be detected from the Mini side; an external check is out of scope.
4. Docs: maintenance "After a reboot" runbook (manual login, `launchctl print`
   checks, health-check command, log locations, `fdesetup authrestart` for planned
   restarts); PROGRESS; this archive.
5. Offline tests with fakes for heartbeat writing, checker states, alert dedupe and
   template rendering (parsed with `plistlib`). Run pytest and Ruff until clean.

Do not commit, push or touch any remote machine. Finish with a summary of changed
files and the test count.

Sanitization: workshop note path reduced to its repository-relative name; Mini
user paths, account identifiers, channel IDs and credentials omitted. No family
records or incident text included. Implementation deviations (scheduled jobs and
the health check without `RunAtLoad`) are recorded in PROGRESS and the phase
document, not here.
