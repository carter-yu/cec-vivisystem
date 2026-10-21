---
date: 2026-10-21 HK
branch: feat/phase-31-reauth-auto-restart
model: claude-opus-5-5 (Claude Code)
goal: After a successful Google re-authorization in the control app, restart the listener LaunchAgent automatically so it loads the new refresh token.
constraints: decisions locked by Carter 2026-10-21; reuse the guarded restart path (one helper); listener only; manual 5-minute gap kept, bypassed only for the re-authorization restart; both roles; offline fakes only; EN (STE) + Traditional Chinese copy, no Simplified; no commit, push or access to any other machine.
acceptance: uv run pytest -q all green and uv run ruff check . clean; ADR 0016 amended; maintenance acceptance, phase-31 doc, PROGRESS and this archive updated; summary of files, test count and the rate-limit choice.
---

# Manager request (sanitized archive)

Implement automatic listener restart after successful Google re-auth on branch feat/phase-31-reauth-auto-restart (from main@46e5565). Read first: control/app.py (google_finish, restart_*, RESTART_GAP, kickstart_listener usage), launchd.py, templates/google.html, the copy catalogs, tests/test_control_actions.py, docs/maintenance.md (Acceptance test: first Google re-authorization), ADR 0016, phases/phase-31-mini-control-app.md, prompts/pr-0007-mini-control-app.md (archive format).

Requirement (Carter 2026-10-21): after google_reauth finish succeeds (token written to .env and probe ran), restart the listener LaunchAgent so it loads the new refresh token. Today the operator must restart by hand and forgets (incident: re-authorization in the afternoon, listener still from earlier that day, the recap path fixed only after a kickstart).

Locked design decisions:
1. Only the listener is restarted. The recap, important-dates and health-check agents are one-shot and load .env on every run.
2. Reuse the guarded restart path: same kickstart_listener fixed argv, audit action listener_restart, status notice notice.restart, RestartInfo / _restart_status polling. One helper (e.g. _perform_listener_restart(..., reason)) used by restart_submit and google_finish.
3. Rate limit: keep the 5-minute RESTART_GAP for manual restarts. The post-reauth restart bypasses it (Save access is itself a confirmed human action; without a restart the new token is unused). Still record last_restart so a manual restart within 5 minutes is refused. Document in an ADR note and maintenance.
4. Roles: member may re-authorize, so the auto-restart runs for member and admin. Manual restart UI stays admin-only.
5. Platform: off macOS kickstart is unavailable; show failure copy and do not undo the token write.
6. No restart when the OAuth exchange or the .env write failed. A probe failure after a successful write still restarts.
7. No extra password for the auto-restart: the attempt is session-bound and at most 10 minutes old. Document this.

UI/copy (STE English + Traditional Chinese): on the Google result page show the restart outcome (waiting, success with new PID/time, failure with short code and "admin uses System → Listener → Restart"). New keys under google.restart.*. Prefer inline status on google.html for both roles. Remove or soften the "then restart the listener" instruction in the result copy, maintenance step 6, ADR 0016 and phase-31.

Docs: maintenance acceptance (automatic restart; admin fallback), ADR 0016 dated amendment (auto-restart, gap bypass for re-auth only, listener-only scope), phase-31 behaviour note, PROGRESS top entry 2026-10-21, this archive.

Tests: success with fake runner (token written, listener_restart audited, kickstart argv once, restart copy; member covered); OAuth failure and write failure never kick; manual restart still rate-limited and refused within 5 minutes after a re-auth restart; existing tests stay green.

Constraints: offline fakes only; no commit, push or Mini; no Simplified Chinese. Finish with pytest and ruff green, then print a summary of files, test count and the rate-limit choice.
