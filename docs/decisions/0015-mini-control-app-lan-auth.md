# ADR 0015: Mini control app — LAN web app, HTTP first, two accounts

Date: 2026-10-20. Status: Accepted for Phase 31 (decisions by Carter, 2026-10-15
and 2026-10-20). Related: ADR 0013 (LaunchAgents, FileVault), ADR 0014 (status
channel, stuck exit), ADR 0016 (Google re-authorization).

## Context

Both parents need one place on the phone to see whether the family bot is
healthy and to fix the common problems: Google access expired (a weekly event
while the OAuth consent screen stays in Testing), the listener needs a restart,
or a Slack channel ID is wrong. Until now every fix needed Terminal on the Mini.

## Decision

1. **Form factor: a local web app.** Starlette, Jinja2, uvicorn and
   python-multipart, as the optional `control` extra in `pyproject.toml`
   (locked in `uv.lock`; `httpx` joins the dev group for Starlette's
   `TestClient`). Pages are rendered on the server and work without JavaScript.
   No CDN and no web fonts: CSS and the icon are vendored; the Noto Sans TC
   file is the repository's existing `assets/fonts` copy. Rejected: SwiftUI (two
   codebases, Apple distribution, no Linux tests) and a menubar app (Mini only).
2. **Isolation.** Package `cec_vivisystem.control`; nothing else imports it.
   The listener and jobs never load Starlette (a subprocess test enforces it),
   and the package top level imports no web code. The app is not an
   orchestrator: if it is down, nothing else changes. It has no background
   tasks; daily audit purge runs lazily on the first request of an HKT day.
3. **Network: home LAN only, HTTP first.** `CEC_CONTROL_BIND` (default
   `127.0.0.1:8730`) must be an IP literal that is loopback or private; `0.0.0.0`,
   `::`, public addresses and host names are refused at start. Each request must
   pass a client-IP allowlist (`CEC_CONTROL_ALLOWED_NETS`; default loopback plus
   the bind address's /24, explicit lists accept only loopback or private
   networks of /16 or smaller) and a Host allowlist (`localhost`, `127.0.0.1`,
   `::1`, the bind address, the machine name and `<name>.local`, plus
   `CEC_CONTROL_HOSTS`). uvicorn runs with `proxy_headers=False`, so only the
   socket peer address counts. No router port forwarding.
4. **Accepted risk: HTTP on the LAN.** Session cookies are `HttpOnly` and
   `SameSite=Strict` but not `Secure`, and traffic is readable on the home
   Wi-Fi. Mitigations: the IP and Host allowlists, sign-in, a 5-failure
   lockout, password re-entry for sensitive actions, CSRF tokens plus an Origin
   check, and no secret values in any response. HTTPS is a configuration
   change later: set `CEC_CONTROL_TLS_CERT` and `CEC_CONTROL_TLS_KEY` (for
   example an mkcert certificate trusted on both iPhones); cookies then become
   `__Host-` + `Secure` and HSTS is sent. Tailscale can be added later without
   app changes (loopback bind plus `tailscale serve`).
5. **Accounts and roles.** Two accounts made by CLI on the Mini
   (`python -m cec_vivisystem.control adduser <name> --role admin|member`); no
   web sign-up. `data/control/users.json` (mode 0600, directory 0700) holds
   names, roles, language and stdlib `hashlib.scrypt` hashes (n=2^15, r=8, p=1,
   16-byte salt); passphrases have 12 or more characters. Roles are enforced on
   the server per route (403):
   - *member*: view status, events and important dates; Google
     re-authorization and Test connection.
   - *admin*: everything, including Slack channel edits, listener restart, the
     stuck-retry setting and the users page.
6. **Sessions and requests.** In-memory sessions with 256-bit IDs (idle 7 days,
   absolute 30 days; an app restart signs everyone out). 5 failures in 15 min
   lock the account and the source address for 15 min; errors are generic and
   an unknown name costs the same scrypt time. Every POST needs the session
   CSRF token (sign-in uses a pre-session double-submit cookie) and an `Origin`
   (or same-origin `Referer`) that matches the request host. GET never changes
   state. Sensitive actions (channel edit, restart, stuck-retry edit, starting
   Google re-authorization) need a password entered in the last 15 minutes.
7. **Headers.** CSP `default-src 'self'; frame-ancestors 'none'; form-action
   'self'; base-uri 'none'; object-src 'none'`, `X-Content-Type-Options:
   nosniff`, `X-Frame-Options: DENY`, `Cache-Control: no-store`,
   `Cross-Origin-Opener-Policy: same-origin`. **Deviation from the Phase 31
   draft:** `Referrer-Policy: same-origin` instead of `no-referrer`. With
   `no-referrer`, browsers send `Origin: null` on same-origin form posts, which
   would make the Origin check reject every legitimate form. `same-origin`
   still sends no referrer to Google or any other origin.
8. **Secrets.** Templates receive presence flags only ("Set" / "Not set") for
   `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`,
   `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`, `XAI_API_KEY`, `LLM_API_KEY`.
   Logs and the audit never contain these values, passphrases, OAuth codes or
   session IDs; the audit refuses unknown fields. A test crawls every GET route
   with sentinel secrets and searches bodies, headers, logs and the audit.
9. **Calendar gate.** The app never writes Google Calendar and never resolves
   a confirmation; a static test fails if the package references
   `write_calendar_create`, `create_event`, `resolve_confirmation` or the
   other write/delete entry points. A future write (31c) must create a normal
   pending confirmation and wait for an explicit "yes" in Slack.
10. **Admin actions** are confirmed, audited (class B, 90 days) and announced in
    the system-status channel (English then Traditional Chinese, no IDs or
    tokens). Restart: confirmation screen, at most one per 5 minutes, fixed argv
    `launchctl kickstart -k gui/<uid>/com.cec.vivisystem.listener`, no shell.
    `.env` edits: allowlisted keys only, line-preserving atomic write, backups
    in `data/control/env-backups/` (mode 0600, last 3, at most 30 days).
11. **Language and visuals.** English (ASD-STE100 style) by default; Traditional
    Chinese (zh-Hant-HK) per account from a copy catalog with key parity and a
    Simplified-character test. Tokens, type and components from the design note
    §8; light and dark through `prefers-color-scheme`; a test checks WCAG AA
    contrast of the shipped CSS tokens.
12. **Process.** LaunchAgent `com.cec.vivisystem.control` (`RunAtLoad`,
    `KeepAlive`, `ThrottleInterval` 30, stdout `/dev/null`, stderr in
    `~/Library/Logs/cec-vivisystem/control.err.log`, `TZ=Asia/Hong_Kong`):
    `uv run --frozen --extra control python -m cec_vivisystem.control serve`.
    The installer renders it with the others and never kickstarts it.

## Dependencies and licenses

| Package | Version locked | License |
| --- | --- | --- |
| starlette | 1.9.0 | BSD-3-Clause |
| jinja2 (+ markupsafe) | 3.1.6 (3.0.4) | BSD-3-Clause |
| uvicorn (+ click) | 0.54.0 (8.5.0) | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| httpx (+ httpcore), dev group only | 0.28.1 (1.0.9) | BSD-3-Clause |

## Consequences

- One more LaunchAgent and one listening socket on the Mini, LAN only.
- `uv sync --locked` without `--extra control` removes the web packages from
  `.venv`; the control agent's `uv run --frozen --extra control` puts them back
  at its next start. Use `uv sync --locked --extra control` on the Mini and in
  development (the control tests skip, with a reason, when the extra is absent).
- The listener reads `.env` only at start. A channel or Google change takes
  effect in Slack replies after a listener restart (admin only). The checker
  and the 07:00/10:00 jobs pick it up at their next run.
- Not done: HTTPS certificates, bot channel-membership checks (needs extra
  Slack scopes), web user management, password change in the web UI, Calendar
  writes, job reruns.
